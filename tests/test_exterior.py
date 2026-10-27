import json
from pathlib import Path
import numpy as np
import pytest
import ifcopenshell
import ifcopenshell.util.placement
from fuzzyresf.cli import run, validate_request
from fuzzyresf.levels import select_level
from fuzzyresf.exterior import _patches
from shapely.geometry import LinearRing


def horizontal_scene(specs):
    chunks = []
    for z, xoffset in specs:
        x, y = np.meshgrid(np.arange(.05, 6, .10), np.arange(.05, 5, .10))
        chunks.append(np.c_[x.ravel() + xoffset, y.ravel(), np.full(x.size, z)])
    P = np.vstack(chunks)
    N = np.tile([0., 0., 1.], (len(P), 1))
    A = {"horizontal_peaks": [{"z": z} for z, _ in specs], "coordinate_origin_m": [0, 0, 100]}
    return P, N, np.ones(len(P)), A


def test_level_pair_ignores_more_populous_disjoint_surface():
    P, N, PL, A = horizontal_scene([(0, 0), (1.037, 20), (3, 0)])
    result = select_level(P, N, PL, A)
    assert result["floor_z"] == 0
    assert result["ceiling_z"] == 3


def test_invalid_height_reports_actual_distance():
    with pytest.raises(ValueError, match="1.037"):
        select_level(*horizontal_scene([(0, 0), (1.037, 0)]))


def test_multi_storey_ambiguity_requires_crop():
    with pytest.raises(ValueError, match="Multiple plausible"):
        select_level(*horizontal_scene([(0, 0), (3, 0), (6, 0)]))


def test_raster_kissing_holes_are_separated():
    # A hole touching the exterior at one grid vertex is a valid 2D polygon,
    # but extrusion creates a non-manifold vertical edge unless regularized.
    x, y = np.meshgrid(np.arange(.075, 3, .15), np.arange(.075, 3, .15))
    xy = np.c_[x.ravel(), y.ravel()]
    mask = ~(((xy[:, 0] > 1.2) & (xy[:, 0] < 1.8) & (xy[:, 1] > 1.2) & (xy[:, 1] < 1.8))
             | ((xy[:, 0] >= 1.8) & (xy[:, 1] >= 1.8)))
    for p, _ in _patches(xy[mask], 1, 10000):
        rings = [LinearRing(p.exterior), *[LinearRing(r) for r in p.interiors]]
        assert all(a.distance(b) > 1e-6 for i, a in enumerate(rings) for b in rings[i+1:])


def house():
    step = .06
    x, y, z = [np.arange(0, stop + step/2, step) for stop in (8, 5, 3)]
    pieces = []
    xx, yy = np.meshgrid(x, y)
    roof = 3 + .375 * (4 - abs(xx - 4))
    pieces.append(np.c_[xx.ravel(), yy.ravel(), roof.ravel()])
    for ywall in (0, 5):
        xx, zz = np.meshgrid(x, np.arange(0, 4.51, step))
        use = zz <= 3 + .375 * (4 - abs(xx - 4))
        pieces.append(np.c_[xx[use], np.full(use.sum(), ywall), zz[use]])
    yy, zz = np.meshgrid(y, z)
    for xwall in (0, 8):
        pieces.append(np.c_[np.full(yy.size, xwall), yy.ravel(), zz.ravel()])
    P = np.vstack(pieces)
    angle = np.radians(27)
    R = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    P[:, :2] = P[:, :2] @ R.T
    P += np.random.default_rng(5).normal(0, .001, P.shape)
    return P


def test_sloped_roof_exterior_ifc_with_survey_coordinates(tmp_path):
    origin = np.array([400000., 4600000., 500.])
    source = tmp_path / "Facciate città.xyz64"
    P = house() + origin
    np.asarray(P * 1000, dtype="<f8").tofile(source)
    output = tmp_path / "Esterno.ifc"
    assert run({"schema_version": 1, "input": {"path": str(source)}, "output": str(output),
                "meters_per_unit": .001, "mode": "exterior"}) == 0
    m = ifcopenshell.open(output)
    assert len(m.by_type("IfcWall")) == 4
    assert len(m.by_type("IfcRoof")) == 2
    placement = ifcopenshell.util.placement.get_local_placement(m.by_type("IfcRoof")[0].ObjectPlacement)
    assert np.linalg.norm(placement[:3, 3] - origin) < 5
    for roof in m.by_type("IfcRoof"):
        solid = roof.Representation.Representations[0].Items[0]
        normal = np.array(solid.Position.Axis.DirectionRatios)
        assert abs(np.degrees(np.arccos(abs(normal[2]))) - np.degrees(np.arctan(.375))) < 1
    assert not m.by_type("IfcSpace")
    assert not m.by_type("IfcBuildingStorey")
    assert not m.by_type("IfcOpeningElement")
    report = json.loads((tmp_path / "Esterno.fuzzyresf/validation.json").read_text())
    assert not report["schema_messages"]
    assert all(s["watertight"] and s["volume_m3"] > 0 for s in report["meshes"])
    I = json.loads((tmp_path / "Esterno.fuzzyresf/inference.json").read_text())
    roofs = [s for s in I["surfaces"] if s["kind"] == "roof"]
    assert all(abs(s["inclination_from_horizontal_deg"] - np.degrees(np.arctan(.375))) < 1 for s in roofs)
    audit = json.loads((tmp_path / "Esterno.fuzzyresf/audit.json").read_text())
    assert np.allclose(audit["actual_min_m"], P.min(0))
    assert all(e.Representation.Representations[0].RepresentationType in {"SweptSolid", "CSG"}
               for e in m.by_type("IfcElement"))


@pytest.mark.parametrize("mode,profile", [("automatic", "monotone"), ("exterior", "legacy")])
def test_unsupported_modes_fail_before_processing(tmp_path, mode, profile):
    source = tmp_path / "input.xyz64"
    source.write_bytes(b"")
    with pytest.raises(ValueError):
        validate_request({"schema_version": 1, "input": {"path": str(source)},
                          "output": str(tmp_path / "output.ifc"), "mode": mode, "profile": profile})
