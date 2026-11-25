"""Catalogue hypotheses must be source-supported and preserve export geometry."""
import copy

import numpy as np
import pytest
from shapely.geometry import box, mapping, shape

import fuzzyresf.catalogue_semantics as engine
from test_local_pairs import seed, paired


def catalogue():
    return {"schema_version": 1, "catalogue_id": "synthetic-reference", "units": "m",
            "families": [{"id": "W241", "ifc_class": "IfcWall",
                          "dimension_role": "finished_wall_thickness", "nominal_m": .241,
                          "source": {"reference": "Synthetic dimensional fixture; not a survey"},
                          "applicability": "GENERAL_REFERENCE", "layers_m": [.10, .141],
                          "physical_measurement": False}]}


def observed(single=False):
    P, N, PL = paired(single=single)
    P[P[:, 0] == .2, 0] = .241
    return P, N, PL


def test_catalogue_probes_counterface_without_histogram_peak(monkeypatch):
    # Suppress exactly the old search's second maximum; source samples remain.
    # This isolates the search limitation independently of SciPy peak tie rules.
    old_find = engine.find_peaks
    def anchor_only(values, **kwargs):
        peaks, properties = old_find(values, **kwargs)
        return peaks[:1], properties
    monkeypatch.setattr(engine, "find_peaks", anchor_only)
    original = seed()
    out = engine.infer_catalogue(original, *observed(), catalogue())
    audit = out["catalogue_inference"]
    assert audit["decisions"][0]["face_count"] == 1
    assert not any(c["proposal"] == "OBSERVED_FREE_PAIR" for c in audit["candidates"])
    positive = [c for c in audit["candidates"] if c["proposal"] == "CATALOGUE_RAW_BAND" and c["side"] == 1]
    assert len(positive) == 1 and positive[0]["exportable"]
    assert positive[0]["observed_face_count"] == 2
    assert audit["changed_walls"] == 1
    w = out["walls"][0]
    assert w["thickness"] == pytest.approx(.241)
    assert w["family_hypothesis"] == "W241"
    assert w["physical_thickness_status"] == "UNKNOWN_UNVERIFIED"
    assert not w["physical_measurement"]
    assert shape(out["spaces"][0]["polygon"]).bounds[0] == pytest.approx(.266)
    assert original == seed()  # detached result, including nested geometry


def test_blind_catalogue_width_abstains_with_all_alternatives_audited():
    out = engine.infer_catalogue(seed(), *observed(single=True), catalogue())
    a = out["catalogue_inference"]
    assert a["changed_walls"] == 0
    attempts = [c for c in a["candidates"] if c["proposal"] == "CATALOGUE_RAW_BAND"]
    assert {c["side"] for c in attempts} == {-1, 1}
    assert all(not c["exportable"] and "TWO_OBSERVED_FACES_REQUIRED" in c["reasons"] for c in attempts)
    assert {c["proposal"] for c in a["candidates"]} >= {"IDENTITY", "FREE_GEOMETRY_IDENTITY"}
    assert out["walls"][0]["family_hypothesis"] == "UNKNOWN"


def test_same_pool_different_geometry_aggregation_decisions():
    P, N, PL = observed()
    angle = np.radians(2.99)
    N[:] = [np.cos(angle), np.sin(angle), 0.]
    results = [engine.infer_catalogue(seed(), P, N, PL, catalogue(), aggregation=a)
               for a in ["mean", "minimum", "crisp"]]
    audits = [r["catalogue_inference"] for r in results]
    assert len({a["candidate_pool_sha256"] for a in audits}) == 1
    assert [a["changed_walls"] for a in audits] == [1, 0, 0]
    for c in audits[0]["candidates"]:
        assert len(c["memberships"]) == 5
        assert all(f"utility_{a}_m" in c for a in ["mean", "minimum", "crisp"])
    # Catalogue membership is diagnostic: every utility uses only the five q.
    c = next(c for c in audits[0]["candidates"] if c["proposal"] == "CATALOGUE_RAW_BAND" and c["exportable"])
    assert c["utility_mean_m"] == pytest.approx(engine.utility(c["gain_m"], c["memberships"], c["max_motion_m"], "mean"))


def test_observed_free_pair_retains_unknown_when_nominal_is_wrong():
    out = engine.infer_catalogue(seed(), *paired(), catalogue())
    assert out["catalogue_inference"]["changed_walls"] == 1
    assert out["walls"][0]["family_hypothesis"] == "UNKNOWN"
    assert out["walls"][0]["thickness"] == pytest.approx(.2)
    assert not out["catalogue_inference"]["hidden_layers_inferred"]


def test_actual_strip_z_intersections_guard_alternating_patches():
    points = []
    for face in [0, 1]:
        for strip in range(8):
            low = 1. if (strip+face)%2 == 0 else 2.
            y, z = np.meshgrid(np.arange(strip*.5+.02, (strip+1)*.5, .04), np.arange(low, low+.7, .04))
            points.append(np.c_[np.full(y.size, .241*face), y.ravel(), z.ravel()])
    P = np.vstack(points)
    out = engine.infer_catalogue(seed(), P, np.tile([1., 0., 0.], (len(P), 1)), np.ones(len(P)), catalogue())
    assert out["catalogue_inference"]["changed_walls"] == 0
    pairs = [c for c in out["catalogue_inference"]["candidates"] if c["observed_face_count"] == 2]
    assert pairs and all("SHARED_STRIPS" in c["reasons"] for c in pairs)


def test_world_uv_frame_holes_and_metadata_are_preserved():
    I = seed()
    original = shape(I["walls"][0]["polygon"]).difference(box(.03, .02, .08, .12))
    I["walls"][0]["polygon"] = mapping(original)
    theta = .57
    R = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
    I["rotation_world_to_uv"], I["origin_xy"] = R.tolist(), [13., -7.]
    I["coordinate_origin_m"] = [500000., 6000000., 100.]
    P, N, PL = observed()
    P[:, :2] = P[:, :2] @ R + I["origin_xy"]
    N[:, :2] = N[:, :2] @ R
    out = engine.infer_catalogue(I, P, N, PL, catalogue())
    w = out["walls"][0]
    p = shape(w["polygon"])
    assert len(p.interiors) == 1
    assert p.area == pytest.approx(original.area*.241/.12)
    assert w["thickness"] == pytest.approx(p.bounds[2]-p.bounds[0])
    assert w["position"] == pytest.approx((p.bounds[0]+p.bounds[2])/2)
    assert [w["t0"], w["t1"]] == pytest.approx([p.bounds[1], p.bounds[3]])
    assert out["coordinate_origin_m"] == I["coordinate_origin_m"]


def test_static_guard_rejects_new_overlap():
    I = seed()
    other = copy.deepcopy(I["walls"][0])
    other.update(id="W002", polygon=mapping(box(.19, 0, .29, 4)), position=.24, thickness=.1)
    I["walls"].append(other)
    out = engine.infer_catalogue(I, *observed(), catalogue())
    first = [c for c in out["catalogue_inference"]["candidates"] if c["wall_id"] == "W001" and c["observed_face_count"] == 2]
    assert first and all("NEW_NONINCIDENT_OVERLAP" in c["reasons"] for c in first)
    assert out["catalogue_inference"]["decisions"][0]["selection"] == "W001_IDENTITY"


def test_ifc_solid_and_geometric_width_agree(tmp_path):
    import ifcopenshell
    import ifcopenshell.geom
    from ifcopenshell.util.element import get_psets
    from fuzzyresf.ifc import build_ifc
    out = engine.infer_catalogue(seed(), *observed(), catalogue())
    path = tmp_path/"catalogue.ifc"
    validation = build_ifc(out, path)
    assert not validation["geometry_failures"]
    assert all(m["watertight"] and m["volume_m3"] > 0 for m in validation["meshes"])
    wall = ifcopenshell.open(str(path)).by_type("IfcWall")[0]
    psets = get_psets(wall)
    width = out["walls"][0]["thickness"]
    assert psets["ScanToBIM_Evidence"]["GeometricProfileWidth_m"] == pytest.approx(width)
    assert psets["ScanToBIM_Evidence"]["PhysicalThicknessStatus"] == "UNKNOWN_UNVERIFIED"
    assert psets["Qto_WallBaseQuantities"]["Width"] == pytest.approx(width)
    mesh = ifcopenshell.geom.create_shape(ifcopenshell.geom.settings(), wall)
    assert np.ptp(np.asarray(mesh.geometry.verts).reshape(-1, 3)[:, 0]) == pytest.approx(width)


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(units="mm"),
    lambda c: c.update(schema_version=True),
    lambda c: c["families"][0].update(dimension_role="structural_core_thickness"),
    lambda c: c["families"][0].update(source={}),
    lambda c: c["families"][0].update(source={"reference": float("nan")}),
    lambda c: c["families"][0].update(nominal_m=float("inf")),
    lambda c: c["families"][0].update(physical_measurement=True),
    lambda c: c["families"][0].update(layers_m=[.10]),
])
def test_reject_invalid_catalogue(mutate):
    c = catalogue()
    mutate(c)
    with pytest.raises(ValueError):
        engine.validate_catalogue(c)


def test_unknown_added_without_mutating_catalogue():
    c = catalogue()
    valid = engine.validate_catalogue(c)
    assert [f["id"] for f in c["families"]] == ["W241"]
    assert valid["families"][-1]["id"] == "UNKNOWN"
    assert valid["families"][-1]["nominal_m"] is None
