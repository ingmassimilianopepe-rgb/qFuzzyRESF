"""Geometry regressions for measured, stepwise RESF reconstruction."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from resf_geometry import detect_levels, estimate_normals, extract_candidates, prepare_points


def room(angle=27.0, translation=(0., 0., 0.), levels=(0., 2.8), partition=False):
    rng = np.random.default_rng(104)
    chunks = []
    for z0, z1 in zip(levels, levels[1:]):
        for y in ([0., 4., 2.] if partition else [0., 4.]):
            n = 5000
            chunks.append(np.column_stack([rng.uniform(0, 6, n), y + rng.normal(0, .003, n), rng.uniform(z0, z1, n)]))
        for x in (0., 6.):
            n = 3800
            chunks.append(np.column_stack([x + rng.normal(0, .003, n), rng.uniform(0, 4, n), rng.uniform(z0, z1, n)]))
    for z in levels:
        n = 6000
        chunks.append(np.column_stack([rng.uniform(0, 6, n), rng.uniform(0, 4, n), z + rng.normal(0, .002, n)]))
    points = np.vstack(chunks)
    theta = np.radians(angle)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    points[:, :2] = points[:, :2] @ rotation.T
    return points + np.asarray(translation)


def reconstruct(points, config=None):
    cfg = {"voxel_m": .04, "raster_cell_m": .10, "normal_neighbors": 20}
    cfg.update(config or {})
    processed, ids, _ = prepare_points(points, cfg)
    normals, planarity = estimate_normals(processed, cfg)
    levels = detect_levels(processed, normals, cfg)
    candidates, evidence, diagnostics = extract_candidates(processed, normals, planarity, levels, cfg)
    return processed, ids, levels, candidates, evidence, diagnostics


def angle_distance(first, second):
    return abs((first - second + 90) % 180 - 90)


def test_preprocessing_is_deterministic_and_keeps_exact_source_rows():
    points = room()
    points[3, 0] = np.nan
    first, indices, diag = prepare_points(points, {"voxel_m": .05, "max_points": 2000})
    second, again, _ = prepare_points(points, {"voxel_m": .05, "max_points": 2000})
    np.testing.assert_array_equal(first, second)
    np.testing.assert_array_equal(indices, again)
    np.testing.assert_array_equal(first, points[indices])
    assert len(first) == 2000 and len(np.unique(indices)) == 2000
    assert diag["removed_nonfinite"] == 1
    assert np.ptp(first[:, 2]) > 2.7
    with pytest.raises(ValueError, match="finite"):
        prepare_points(np.full((5, 3), np.nan), {})


def test_rotated_room_keeps_opposite_and_parallel_surfaces():
    processed, _, storeys, candidates, evidence, diag = reconstruct(room(partition=True))
    assert len(storeys) == 1
    assert len(candidates) == 5, diag
    assert sum(angle_distance(c.theta_deg, 27) < 2 for c in candidates) == 2
    assert sum(angle_distance(c.theta_deg, 117) < 2 for c in candidates) == 3
    assert min(c.t1 - c.t0 for c in candidates) > 3.7
    assert all(c.A_star > .65 and c.E_V > .95 and c.N > .8 for c in candidates)
    for candidate in candidates:
        measurements = evidence[candidate.id]
        assert measurements["support_count"] == len(measurements["support_indices"])
        assert max(measurements["support_indices"]) < len(processed)
        assert not measurements["semantic_evidence_available"]
        assert not measurements["visibility_evidence_available"]
        assert candidate.O == candidate.semantic_wall == 0


def test_translated_coordinates_produce_same_physical_geometry():
    original = room()
    shift = np.array([643000., 4872000., 170.])
    a = reconstruct(original)
    b = reconstruct(original + shift)
    assert len(a[3]) == len(b[3]) == 4
    a_lengths = sorted(c.t1 - c.t0 for c in a[3])
    b_lengths = sorted(c.t1 - c.t0 for c in b[3])
    np.testing.assert_allclose(a_lengths, b_lengths, atol=.05)
    for first in a[3]:
        second = min(b[3], key=lambda c: angle_distance(c.theta_deg, first.theta_deg)
                     + abs(c.d - np.dot(shift[:2], [np.cos(np.radians(c.theta_deg)), np.sin(np.radians(c.theta_deg))]) - first.d))
        assert angle_distance(first.theta_deg, second.theta_deg) < .2
    assert b[2][0].z0 == pytest.approx(a[2][0].z0 + shift[2], abs=.02)


def test_two_storeys_have_unique_candidate_ids_and_observed_levels():
    _, _, levels, candidates, evidence, diag = reconstruct(room(levels=(0., 2.8, 5.6)))
    assert len(levels) == 2, levels
    np.testing.assert_allclose([(s.z0, s.z1) for s in levels], [[0, 2.8], [2.8, 5.6]], atol=.03)
    assert len(candidates) == 8, diag
    assert len({c.id for c in candidates}) == 8
    assert {c.storey_id for c in candidates} == {1, 2}
    assert set(evidence) == {c.id for c in candidates}


def test_explicit_storeys_validate_and_preserve_heights():
    points = np.array([[0, 0, 0], [1, 1, 8.]])
    levels = detect_levels(points, np.zeros_like(points), {"storeys": [[4, 7], [0, 3]]})
    assert [(s.id, s.z0, s.z1) for s in levels] == [(1, 0., 3.), (2, 4., 7.)]
    with pytest.raises(ValueError, match="overlap"):
        detect_levels(points, np.zeros_like(points), {"storeys": [[0, 4], [3, 6]]})


def test_disjoint_coplanar_support_is_not_bridged_to_one_wall():
    rng = np.random.default_rng(52)
    blocks = []
    for low, high in [(0, 2), (4, 6)]:
        n = 4000
        blocks.append(np.column_stack([rng.uniform(low, high, n), rng.normal(0, .002, n), rng.uniform(0, 3, n)]))
    _, _, _, candidates, _, diag = reconstruct(np.vstack(blocks), {"storeys": [[0, 3]]})
    assert len(candidates) == 2, diag
    assert all(1.8 < c.t1 - c.t0 < 2.1 for c in candidates)


def test_line_like_scan_stripes_do_not_become_walls():
    rng = np.random.default_rng(33)
    points = np.column_stack([np.repeat([0., 1., 2.], 2000), rng.normal(0, .0001, 6000), np.tile(np.linspace(0, 3, 2000), 3)])
    _, _, _, candidates, _, _ = reconstruct(points, {"voxel_m": .01, "storeys": [[0, 3]], "normal_neighbors": 16})
    assert candidates == []


def test_nearby_parallel_faces_remain_distinct_measured_hypotheses():
    rng = np.random.default_rng(12)
    points = np.vstack([
        np.column_stack([rng.uniform(0, 5, 7000), rng.normal(y, .002, 7000),
                         rng.uniform(0, 3, 7000)])
        for y in (0., .12, 3.)
    ])
    _, _, _, candidates, evidence, _ = reconstruct(
        points, {"voxel_m": .03, "storeys": [[0, 3]]})
    assert len(candidates) == 3
    np.testing.assert_allclose(sorted(c.d for c in candidates), [0., .12, 3.], atol=.01)
    assert all(evidence[c.id]["plane_residual_m"] < .004 for c in candidates)
    # A physical-wall pairing/assumed thickness must not erase either face
    # in the geometric evidence stage.
    assert all(c.state == "Uncertain" for c in candidates)


def test_ablation_switches_disable_extra_peaks_and_leave_topology_missing():
    _, _, _, candidates, evidence, diagnostics = reconstruct(
        room(partition=True), {"multi_peak": False, "topology": False})
    assert len(candidates) == 2  # Strongest plane for each measured orientation.
    assert not diagnostics["multi_peak_enabled"]
    assert not diagnostics["topology_enabled"]
    for item in evidence.values():
        assert all(key not in item for key in ("junction", "room_closure", "continuation"))
        assert "floor_contact" in item


@pytest.mark.parametrize("config,key", [
    ({"plane_tolerance_m": 0}, "plane_tolerance_m"),
    ({"raster_cell_m": -1}, "raster_cell_m"),
    ({"max_support_gap_m": -1}, "max_support_gap_m"),
    ({"max_peaks_per_orientation": 0}, "max_peaks_per_orientation"),
    ({"min_planarity": 1.1}, "min_planarity"),
])
def test_invalid_geometry_configuration_is_reported(config, key):
    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 0., 1.]])
    normals = np.tile([0., 1., 0.], (len(points), 1))
    with pytest.raises(ValueError, match=key):
        extract_candidates(points, normals, np.ones(len(points)), [], config)


def test_normal_estimation_rejects_nonfinite_unprocessed_coordinates():
    with pytest.raises(ValueError, match="finite"):
        estimate_normals(np.array([[np.nan, 0., 0.]]), {})
