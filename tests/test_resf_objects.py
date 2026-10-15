"""Regression tests for independent semantics and occupancy-preserving geometry."""
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import bim_reconstruction as base
from resf_objects import reconstruct_objects


def wall(ident=7, a=(0, 0), b=(4, 0)):
    return base.Wall(ident, 1, *a, *b, 0, 3, .20,
                     math.degrees(math.atan2(b[0] - a[0], a[1] - b[1])) % 180,
                     .9, .95, .95, "observed_faces")


def model(walls=None):
    return base.BIMModel([base.Storey(1, 0, 3, .9)], walls or [], [], [], [], [], {})


def wall_cloud(sill=0, height=2.1, filled=False, angle=0):
    x, z = np.meshgrid(np.arange(.025, 4, .05), np.arange(.025, 3, .05), indexing="ij")
    pts = np.column_stack((x.ravel(), np.zeros(x.size), z.ravel()))
    aperture = (pts[:, 0] >= 1) & (pts[:, 0] < 2) & (pts[:, 2] >= sill) & (pts[:, 2] < sill + height)
    labels = np.where(aperture, .98, .01)
    if not filled:
        pts, labels = pts[~aperture], labels[~aperture]
    c, s = math.cos(angle), math.sin(angle)
    rotation = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    pts = pts @ rotation.T
    normals = np.repeat([[0., 1., 0.]], len(pts), axis=0) @ rotation.T
    return pts, normals, labels


def test_missing_wall_returns_are_ambiguous_even_with_catalog():
    pts, normals, _ = wall_cloud()
    config = {"family_catalog": {"families": [{"id": "D01", "class": "Door", "width_m": [0.9, 1.1], "height_m": [2, 2.2]}]}}
    result = reconstruct_objects(pts, normals, model([wall()]), config)
    assert not result["openings"]
    assert len(result["decisions"]) == 1
    decision = result["decisions"][0]
    assert decision["state"] == "Uncertain"
    assert "free_space" not in decision["evidence"]
    assert decision["catalog_suggestions"][0]["family_id"] == "D01"
    assert decision["catalog_suggestions"][0]["fit"] > .95


def test_measured_crossing_rays_establish_unclassified_void():
    pts, normals, _ = wall_cloud()
    origins = pts.copy()
    origins[:, 1] = -2
    x, z = np.meshgrid(np.arange(1.05, 2, .1), np.arange(.05, 2.1, .1), indexing="ij")
    back = np.column_stack((x.ravel(), np.full(x.size, 2.), z.ravel()))
    starts = back.copy()
    starts[:, 1] = -2
    pts = np.vstack((pts, back))
    normals = np.vstack((normals, np.tile([0., 1., 0.], (len(back), 1))))
    origins = np.vstack((origins, starts))
    result = reconstruct_objects(pts, normals, model([wall()]), {}, scanner_origins=origins)
    assert len(result["openings"]) == 1
    obj = result["openings"][0]
    assert obj.kind == "Opening" and obj.wall_id == 7
    assert obj.offset == pytest.approx(1.5, abs=.11)
    assert result["decisions"][0]["measured_crossing_ray_cells"] > 10


def test_origins_elsewhere_do_not_establish_aperture_visibility():
    pts, normals, _ = wall_cloud()
    origins = pts.copy()
    origins[:, 1] = -2
    result = reconstruct_objects(pts, normals, model([wall()]), {}, scanner_origins=origins)
    assert not result["openings"]
    assert result["decisions"][0]["state"] == "Uncertain"
    assert "free_space" not in result["decisions"][0]["evidence"]


@pytest.mark.parametrize("kind,sill,height", [("Door", 0, 2.1), ("Window", 1, 1.2)])
def test_observed_semantic_leaf_or_pane_creates_correct_host_fill(kind, sill, height):
    pts, normals, labels = wall_cloud(sill, height, filled=True)
    result = reconstruct_objects(pts, normals, model([wall()]), {}, semantic_probs={kind: labels})
    assert len(result["openings"]) == 1, result["decisions"]
    obj = result["openings"][0]
    assert obj.kind == kind and obj.wall_id == 7
    assert obj.sill == pytest.approx(sill, abs=.11)
    assert obj.width == pytest.approx(1, abs=.11)


def test_rotated_wall_uses_own_frame_not_world_xy():
    angle = math.radians(33)
    pts, normals, labels = wall_cloud(1, 1.2, True, angle)
    accepted = wall(a=(0, 0), b=(4 * math.cos(angle), 4 * math.sin(angle)))
    result = reconstruct_objects(pts, normals, model([accepted]), {}, semantic_probs={"Window": labels})
    assert len(result["openings"]) == 1
    assert result["openings"][0].offset == pytest.approx(1.5, abs=.11)


def test_disconnected_horizontal_patches_and_holes_are_preserved():
    x, y = np.meshgrid(np.arange(.025, 5, .05), np.arange(.025, 2, .05), indexing="ij")
    points = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
    keep = ((points[:, 0] < 2) | (points[:, 0] > 3))
    hole = (points[:, 0] > .6) & (points[:, 0] < 1.4) & (points[:, 1] > .6) & (points[:, 1] < 1.4)
    points = points[keep & ~hole]
    normals = np.tile([0., 0., 1.], (len(points), 1))
    result = reconstruct_objects(points, normals, model(), {"floor_cell_m": .1})
    slabs = result["slabs"]
    assert len(slabs) == 2
    polygons = [Polygon(s.polygon, s.holes) for s in slabs]
    assert all(not p.contains(Point(2.5, 1)) for p in polygons)
    assert all(not p.contains(Point(1, 1)) for p in polygons)
    assert sum(len(s.holes) for s in slabs) == 1
    assert all(s.provenance["thickness_source"] == "assumed_config" for s in slabs)
    assert all(s.kind == "Floor" for s in slabs)


def test_wall_graph_cannot_fabricate_unobserved_floor_or_ceiling():
    walls = [wall(1, (0, 0), (4, 0)), wall(2, (4, 0), (4, 3)),
             wall(3, (4, 3), (0, 3)), wall(4, (0, 3), (0, 0)),
             wall(5, (2, 0), (2, 3))]
    result = reconstruct_objects(np.empty((0, 3)), np.empty((0, 3)), model(walls), {})
    assert len(result["spaces"]) == 2
    assert sum(s.area for s in result["spaces"]) == pytest.approx(12)
    assert result["slabs"] == []


def test_clutter_and_review_walls_cannot_host_bim_objects():
    pts, normals, labels = wall_cloud(filled=True)
    for state in ("Clutter", "Review", "Uncertain"):
        rejected = wall()
        rejected.state = state
        result = reconstruct_objects(pts, normals, model([rejected]), {}, semantic_probs={"Door": labels})
        assert not result["openings"]
        assert not result["spaces"]


def test_nested_horizontal_components_keep_separate_surface_elevations():
    x, y = np.meshgrid(np.arange(.05, 4, .1), np.arange(.05, 4, .1), indexing="ij")
    xy = np.column_stack((x.ravel(), y.ravel()))
    ring = (xy[:, 0] < .2) | (xy[:, 0] > 3.8) | (xy[:, 1] < .2) | (xy[:, 1] > 3.8)
    island = (xy[:, 0] > 1) & (xy[:, 0] < 3) & (xy[:, 1] > 1) & (xy[:, 1] < 3)
    points = np.vstack((np.column_stack((xy[ring], np.full(ring.sum(), .01))),
                        np.column_stack((xy[island], np.full(island.sum(), .08)))))
    normals = np.tile([0., 0., 1.], (len(points), 1))
    result = reconstruct_objects(points, normals, model(), {"floor_cell_m": .1})
    slabs = result["slabs"]
    assert len(slabs) == 2
    ring_slab = next(s for s in slabs if len(s.holes) == 1)
    island_slab = next(s for s in slabs if len(s.holes) == 0)
    assert ring_slab.z == pytest.approx(.01)
    assert island_slab.z == pytest.approx(.08)


def test_empty_input_is_empty_and_bad_auxiliary_alignment_rejected():
    result = reconstruct_objects(np.empty((0, 3)), np.empty((0, 3)), model(), {})
    assert all(result[key] == [] for key in ("openings", "spaces", "slabs", "decisions"))
    with pytest.raises(ValueError, match="N finite"):
        reconstruct_objects(np.zeros((2, 3)), np.zeros((2, 3)), model(), {}, semantic_probs={"Door": [1.]})
