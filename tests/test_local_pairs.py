"""Identification, abstention and real IFC property/geometry consistency checks."""
import copy
import json

import numpy as np
import pytest
from shapely.geometry import box, mapping, shape
from shapely.affinity import affine_transform

from fuzzyresf.local_pairs import refine, utility


def seed():
    return {"walls": [{"id": "W001", "axis": 0, "position": .06,
                       "thickness": .12, "t0": 0., "t1": 4.,
                       "polygon": mapping(box(0, 0, .12, 4)), "base": 0., "top": 3.,
                       "score": .9, "thickness_status": "ASSUMED",
                       "proposal_ids": ["P001"], "endpoint_extension_m": [0., 0.],
                       "extent_status": "TEST"}],
            "rotation_world_to_uv": [[1., 0.], [0., 1.]], "origin_xy": [0., 0.],
            "coordinate_origin_m": [0., 0., 0.],
            "main_level": {"height_m": 3., "floor_z": 0., "ceiling_z": 3.},
            "spaces": [{"id": "SP001", "polygon": mapping(box(.145, .1, 3., 3.9)),
                        "area_m2": (3-.145)*3.8, "base": 0., "top": 3., "status": "PROVISIONAL"}],
            "source_sha256": "0"*64, "source_filename": "synthetic.xyz64",
            "envelope": mapping(box(-.3, -.3, 4.3, 4.3)), "envelope_status": "TEST",
            "scope": "SYNTHETIC REGRESSION TEST", "surfaces": []}


def paired(second_height=.85, single=False):
    yy, zz = np.meshgrid(np.arange(.02, 3.99, .04), np.arange(1., 1.85, .04))
    p = [np.c_[np.zeros(yy.size), yy.ravel(), zz.ravel()]]
    if not single:
        yy, zz = np.meshgrid(np.arange(.02, 3.99, .04), np.arange(1., 1.+second_height, .04))
        p.append(np.c_[np.full(yy.size, .2), yy.ravel(), zz.ravel()])
    P = np.vstack(p)
    return P, np.tile([1., 0., 0.], (len(P), 1)), np.ones(len(P))


def test_local_partial_height_pair_updates_actual_profile():
    out = refine(seed(), *paired())
    assert out["local_refinement"]["changed_walls"] == 1
    w = out["walls"][0]
    assert shape(w["polygon"]).bounds == pytest.approx((0, 0, .2, 4))
    assert w["thickness"] == pytest.approx(.2)
    assert w["physical_thickness_status"] == "UNKNOWN_UNVERIFIED"
    assert w["observed_opposing_pair"] is True
    assert shape(out["spaces"][0]["polygon"]).bounds[0] == pytest.approx(.225)


@pytest.mark.parametrize("args", [{"single": True}, {"second_height": .35}])
def test_abstain_single_face_or_short_furniture(args):
    out = refine(seed(), *paired(**args))
    assert out["local_refinement"]["changed_walls"] == 0
    assert shape(out["walls"][0]["polygon"]).equals(shape(seed()["walls"][0]["polygon"]))
    assert out["walls"][0]["physical_thickness_status"] == "UNKNOWN_UNVERIFIED"


def test_alternating_disjoint_vertical_patches_are_not_an_opposing_pair():
    pieces = []
    for face in [0, 1]:
        for strip in range(8):
            y, z = np.meshgrid(np.arange(strip*.5+.02, (strip+1)*.5, .04),
                               np.arange(1. if (strip+face)%2 == 0 else 2.,
                                         1.7 if (strip+face)%2 == 0 else 2.7, .04))
            pieces.append(np.c_[np.full(y.size, .2*face), y.ravel(), z.ravel()])
    P = np.vstack(pieces)
    out = refine(seed(), P, np.tile([1., 0., 0.], (len(P), 1)), np.ones(len(P)))
    assert out["local_refinement"]["changed_walls"] == 0
    rejection = out["local_refinement"]["decisions"][0]["rejected_pairs"]
    assert any("SHARED_STRIPS" in r["reasons"] for r in rejection)


def test_controls_share_candidates_and_only_aggregation_changes():
    C, F = [refine(seed(), *paired(), aggregation=a) for a in ["mean", "minimum"]]
    assert C["local_refinement"]["candidate_pool_sha256"] == F["local_refinement"]["candidate_pool_sha256"]
    assert utility(.00030320, [1., .812, .9607, .5257], .01195732, "mean") > 0
    assert utility(.00030320, [1., .812, .9607, .5257], .01195732, "minimum") < 0


def test_same_admissible_pool_produces_distinct_mean_and_minimum_decisions():
    P, N, PL = paired()
    angle = np.radians(2.99)
    N[:] = [np.cos(angle), np.sin(angle), 0.]
    C = refine(seed(), P, N, PL, aggregation="mean")
    F = refine(seed(), P, N, PL, aggregation="minimum")
    assert C["local_refinement"]["candidate_pool_sha256"] == F["local_refinement"]["candidate_pool_sha256"]
    assert C["local_refinement"]["changed_walls"] == 1
    assert F["local_refinement"]["changed_walls"] == 0


def test_uv_frame_equivariance_with_global_coordinate_offset():
    I = seed()
    theta = .57
    R = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
    I["rotation_world_to_uv"] = R.tolist()
    I["origin_xy"] = [13., -7.]
    I["coordinate_origin_m"] = [500000., 6000000., 100.]
    P, N, PL = paired()
    P[:, :2] = P[:, :2] @ R + I["origin_xy"]
    N[:, :2] = N[:, :2] @ R
    out = refine(I, P, N, PL)
    assert out["walls"][0]["thickness"] == pytest.approx(.2)
    assert out["coordinate_origin_m"] == I["coordinate_origin_m"]


def test_profile_hole_preserved_by_transverse_affine():
    # A small end hole leaves the five constant-width cross-sections unchanged.
    I = seed()
    hole = box(.03, .02, .08, .12)
    original = shape(I["walls"][0]["polygon"]).difference(hole)
    I["walls"][0]["polygon"] = mapping(original)
    out = refine(I, *paired())
    new = shape(out["walls"][0]["polygon"])
    assert len(new.interiors) == len(original.interiors) == 1
    assert new.area == pytest.approx(original.area*.2/.12)


def test_overlap_guard_retains_identity():
    I = seed()
    q = copy.deepcopy(I["walls"][0])
    q.update(id="W002", polygon=mapping(box(.17, 0., .27, 4.)), position=.22, thickness=.1)
    I["walls"].append(q)
    out = refine(I, *paired())
    d = out["local_refinement"]["decisions"][0]
    assert d["selection"] == "IDENTITY"
    assert d["selection_rejections"][0]["reason"] == "NEW_NONINCIDENT_OVERLAP"


def test_contact_guard_rejects_an_open_joint():
    I = seed()
    q = copy.deepcopy(I["walls"][0])
    q.update(id="W002", axis=1, polygon=mapping(box(-.5, 1., 0., 1.12)),
             position=1.06, thickness=.12, t0=-.5, t1=0.)
    I["walls"].append(q)
    P, N, PL = paired()
    P[P[:, 0] == 0., 0] = .08
    out = refine(I, P, N, PL)
    d = out["local_refinement"]["decisions"][0]
    assert d["selection"] == "IDENTITY"
    assert d["selection_rejections"][0]["reason"] == "CONTACT_GAP"


def test_real_ifc_solid_and_width_metadata_agree(tmp_path):
    import ifcopenshell
    import ifcopenshell.geom
    from ifcopenshell.util.element import get_psets
    from fuzzyresf.ifc import build_ifc
    out = refine(seed(), *paired())
    path = tmp_path/"local.ifc"
    validation = build_ifc(out, path)
    assert not validation["geometry_failures"]
    assert all(m["watertight"] and m["volume_m3"] > 0 for m in validation["meshes"])
    model = ifcopenshell.open(str(path))
    wall = model.by_type("IfcWall")[0]
    psets = get_psets(wall)
    assert psets["ScanToBIM_Evidence"]["GeometricProfileWidth_m"] == pytest.approx(.2)
    assert psets["ScanToBIM_Evidence"]["PhysicalThicknessStatus"] == "UNKNOWN_UNVERIFIED"
    assert psets["ScanToBIM_Evidence"]["ObservedFaceSeparation_m"] == pytest.approx(.2)
    assert psets["Qto_WallBaseQuantities"]["Width"] == pytest.approx(.2)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    mesh = ifcopenshell.geom.create_shape(settings, wall)
    v = np.asarray(mesh.geometry.verts).reshape(-1, 3)
    assert np.ptp(v[:, 0]) == pytest.approx(.2)
    assert np.ptp(v[:, 2]) == pytest.approx(3.)
    assert wall.GlobalId == ifcopenshell.open(str(path)).by_type("IfcWall")[0].GlobalId


@pytest.mark.parametrize("profile", ["experimental_local_continuous", "experimental_local_fuzzy"])
def test_actual_cli_publication_and_abstention(tmp_path, two_rooms, profile):
    from fuzzyresf.cli import run
    source = tmp_path/"scene.xyz64"
    np.asarray(two_rooms, dtype="<f8").tofile(source)
    output = tmp_path/"scene.ifc"
    assert run({"schema_version": 1, "input": {"path": str(source)},
                "output": str(output), "profile": profile}) == 0
    inference = json.loads(output.with_suffix(".fuzzyresf").joinpath("inference.json").read_text())
    assert inference["profile"] == profile
    assert inference["local_refinement"]["reference_used"] is False
    assert inference["local_refinement"]["changed_walls"] == 0
