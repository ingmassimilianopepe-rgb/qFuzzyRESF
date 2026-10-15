"""Measured geometry for the stepwise RESF–fuzzy pipeline.

RESF retains multiple occupied-plane hypotheses per normal direction. It does
not treat a projection peak as a BIM wall. Memberships exported here are
geometric evidence; semantic and visibility evidence must come from later
stages. Coordinates and tolerances are metres, with gravity along +Z.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter1d, label
from scipy.signal import find_peaks
from scipy.spatial import cKDTree

try:
    from .bim_reconstruction import RESFCandidate, Storey
except ImportError:
    from bim_reconstruction import RESFCandidate, Storey


def _positive(config: dict, key: str, default: float) -> float:
    value = float(config.get(key, default))
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be a positive finite number")
    return value


def _unit_interval(config: dict, key: str, default: float) -> float:
    value = float(config.get(key, default))
    if not np.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{key} must be a finite number in [0,1]")
    return value


def _finite_xyz(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3 or not np.isfinite(pts).all():
        raise ValueError("points must be finite N x 3 coordinates; run prepare_points first")
    return pts


def prepare_points(points: np.ndarray, config: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    """Filter/regularise points and retain an exact source-row mapping.

    Each voxel retains the input sample nearest its centre, rather than an
    invented centroid. Sampling is spatial, deterministic, and independent of
    input order except for exact distance ties. No statistical outlier filter
    removes isolated architectural observations without the caller's consent.
    """
    source = np.asarray(points, dtype=np.float64)
    if source.ndim != 2 or source.shape[1] < 3:
        raise ValueError("points must be an N x 3 (or wider) array")
    source = source[:, :3]
    ids = np.flatnonzero(np.isfinite(source).all(axis=1))
    clean = source[ids]
    if not len(clean):
        raise ValueError("point cloud contains no finite XYZ samples")
    voxel = float(config.get("voxel_m", config.get("voxel_size", 0.04)))
    if not np.isfinite(voxel) or voxel < 0:
        raise ValueError("voxel_m must be finite and nonnegative")
    before_voxel = len(clean)
    origin = clean.min(axis=0)
    if voxel > 0:
        scaled = (clean - origin) / voxel
        if scaled.max(initial=0) > np.iinfo(np.int64).max / 8:
            raise ValueError("coordinate extent is too large for voxel_m")
        q = np.floor(scaled + 1e-9).astype(np.int64)
        distance = np.sum((scaled - q - 0.5) ** 2, axis=1)
        order = np.lexsort((ids, distance, q[:, 2], q[:, 1], q[:, 0]))
        first = np.r_[True, np.any(q[order][1:] != q[order][:-1], axis=1)]
        keep = order[first]
        clean, ids = clean[keep], ids[keep]
    after_voxel = len(clean)
    maximum = int(config.get("max_points", 500_000))
    if maximum < 3:
        raise ValueError("max_points must be at least 3")
    if len(clean) > maximum:
        # Sorting by a quantised Morton code distributes a budget over 3D
        # support. Stable source ids resolve collisions reproducibly.
        scaled = (clean - clean.min(axis=0)) / np.maximum(np.ptp(clean, axis=0), 1e-12)
        quantised = np.floor(scaled * ((1 << 20) - 1)).astype(np.uint64)
        morton = np.zeros(len(clean), dtype=np.uint64)
        for bit in range(20):
            for axis in range(3):
                morton |= ((quantised[:, axis] >> np.uint64(bit)) & np.uint64(1)) << np.uint64(3 * bit + axis)
        order = np.lexsort((ids, morton))
        keep = order[np.linspace(0, len(order) - 1, maximum, dtype=np.int64)]
        clean, ids = clean[keep], ids[keep]
    return clean.copy(), ids.copy(), {
        "input_points": int(len(source)), "finite_points": before_voxel,
        "removed_nonfinite": int(len(source) - before_voxel),
        "voxel_points": after_voxel, "processed_points": int(len(clean)),
        "voxel_m": voxel, "input_index_mapping": "exact retained input rows",
        "sample_budget_applied": bool(after_voxel > maximum),
    }


def estimate_normals(points: np.ndarray, config: dict) -> tuple[np.ndarray, np.ndarray]:
    """Local PCA with bounded batches; signs are intentionally unoriented."""
    pts = _finite_xyz(points)
    count = len(pts)
    if count < 3:
        return np.zeros((count, 3)), np.zeros(count)
    neighbors = min(count, max(3, int(config.get("normal_neighbors", 24))))
    chunk = max(64, min(8192, int(config.get("normal_chunk_size", 2048))))
    tree = cKDTree(pts)
    normals = np.zeros((count, 3))
    planarity = np.zeros(count)
    radius = float(config.get("normal_radius_m", 0.0))
    if radius < 0 or not np.isfinite(radius):
        raise ValueError("normal_radius_m must be finite and nonnegative")
    for begin in range(0, count, chunk):
        end = min(count, begin + chunk)
        distances, indices = tree.query(pts[begin:end], k=neighbors, workers=1)
        local = pts[indices] - pts[begin:end, None, :]
        if radius > 0:
            mask = distances <= radius
            weights = mask[..., None]
            sample_count = np.maximum(mask.sum(axis=1), 1)
            centre = np.sum(local * weights, axis=1) / sample_count[:, None]
            delta = (local - centre[:, None, :]) * weights
            covariance = np.einsum("nki,nkj->nij", delta, delta) / sample_count[:, None, None]
        else:
            sample_count = np.full(end - begin, neighbors)
            delta = local - local.mean(axis=1, keepdims=True)
            covariance = np.einsum("nki,nkj->nij", delta, delta) / neighbors
        values, vectors = np.linalg.eigh(covariance)
        values = np.maximum(values, 0)
        valid = (sample_count >= 3) & (values[:, 2] > 1e-16)
        quality = (values[:, 1] - values[:, 0]) / np.maximum(values[:, 2], 1e-16)
        normals[begin:end][valid] = vectors[valid, :, 0]
        planarity[begin:end] = np.where(valid, np.clip(quality, 0, 1), 0)
    return normals, planarity


def detect_levels(points: np.ndarray, normals: np.ndarray, config: dict) -> list[Storey]:
    """Infer levels from observed horizontal occupied area, or use given pairs.

    An unsegmented vertical extent is a fallback storey with low confidence;
    it is never periodically sliced into assumed floor heights.
    """
    pts = _finite_xyz(points)
    normals = np.asarray(normals, dtype=float)
    if normals.shape != pts.shape or not np.isfinite(normals).all():
        raise ValueError("normals must be finite and match points")
    if config.get("storeys") is not None:
        bounds = config["storeys"]
        if not bounds:
            raise ValueError("storeys must contain at least one [z0,z1] pair")
        result = []
        for i, pair in enumerate(bounds):
            if len(pair) != 2 or not np.isfinite(pair).all() or float(pair[1]) <= float(pair[0]):
                raise ValueError("storeys must contain finite increasing [z0,z1] pairs")
            result.append(Storey(i + 1, float(pair[0]), float(pair[1]), 1.0))
        result.sort(key=lambda item: item.z0)
        if any(a.z1 > b.z0 + 1e-6 for a, b in zip(result, result[1:])):
            raise ValueError("explicit storeys must not overlap")
        for i, item in enumerate(result):
            item.id = i + 1
        return result
    if not len(pts):
        return []
    low, high = map(float, np.quantile(pts[:, 2], [0.002, 0.998]))
    if high <= low:
        return []
    horizontal = np.abs(np.asarray(normals)[:, 2]) >= math.cos(math.radians(20))
    horizontal &= np.linalg.norm(normals, axis=1) > 0.5
    selected = pts[horizontal]
    minimum_height = _positive(config, "min_storey_height_m", 1.8)
    levels: list[float] = []
    if len(selected) >= 20:
        zcell = _positive(config, "level_bin_m", 0.06)
        xycell = _positive(config, "level_xy_cell_m", 0.20)
        local = selected - selected.min(axis=0)
        qxy = np.floor(local[:, :2] / xycell).astype(np.int64)
        qz = np.floor(local[:, 2] / zcell).astype(np.int64)
        unique = np.unique(np.column_stack((qz, qxy)), axis=0)
        if int(qz.max()) <= 100_000:
            score = np.bincount(unique[:, 0])
            smooth = gaussian_filter1d(score.astype(float), 0.8, mode="constant")
            peaks, _ = find_peaks(np.r_[0, smooth, 0], height=max(8, smooth.max() * 0.12))
            peaks = peaks - 1
            # Nearby horizontal bands can be opposite faces of one slab.
            accepted = []
            for peak in sorted(peaks, key=lambda i: (-smooth[i], i)):
                height = selected[:, 2].min() + (peak + 0.5) * zcell
                if all(abs(height - old) >= 0.40 for old in accepted):
                    band = np.abs(selected[:, 2] - height) <= zcell * 1.6
                    accepted.append(float(np.median(selected[band, 2])))
            levels = sorted(accepted)
    valid = []
    maximum_height = _positive(config, "max_storey_height_m", 8.0)
    for lower, upper in zip(levels, levels[1:]):
        if minimum_height <= upper - lower <= maximum_height:
            valid.append(Storey(len(valid) + 1, lower, upper, 0.8))
    if valid:
        # Include an incompletely scanned highest or lowest storey only where
        # the cloud itself supplies sufficient remaining vertical extent.
        if valid[0].z0 - low >= minimum_height:
            valid.insert(0, Storey(0, low, valid[0].z0, 0.35))
        if high - valid[-1].z1 >= minimum_height:
            valid.append(Storey(0, valid[-1].z1, high, 0.35))
        for i, item in enumerate(valid):
            item.id = i + 1
        return valid
    return [Storey(1, low, high, 0.35)]


def _angle_distance(a: float | np.ndarray, b: float) -> float | np.ndarray:
    return np.abs((a - b + 90) % 180 - 90)


def _orientations(normals: np.ndarray, planarity: np.ndarray, config: dict) -> list[float]:
    threshold = _unit_interval(config, "min_planarity", 0.35)
    vertical = (np.abs(normals[:, 2]) <= math.sin(math.radians(18))) & (planarity >= threshold)
    if not vertical.any():
        return []
    angles = np.degrees(np.arctan2(normals[vertical, 1], normals[vertical, 0])) % 180
    weights = planarity[vertical]
    bin_width = _positive(config, "angle_step_deg", 2.0)
    maximum = int(config.get("max_orientation_families", 24))
    if maximum < 1:
        raise ValueError("max_orientation_families must be at least 1")
    bins = max(18, int(math.ceil(180 / min(bin_width, 10))))
    edges = np.linspace(0, 180, bins + 1)
    histogram = np.histogram(angles, bins=edges, weights=weights)[0]
    smooth = gaussian_filter1d(histogram, 1.0, mode="wrap")
    local_max = smooth >= np.maximum(np.roll(smooth, 1), np.roll(smooth, -1))
    selected = np.flatnonzero(local_max & (smooth >= max(4, smooth.max() * 0.025)))
    order = sorted(selected, key=lambda i: (-smooth[i], i))
    result = []
    for index in order:
        angle = float((edges[index] + edges[index + 1]) / 2)
        nearby = _angle_distance(angles, angle) <= max(4, 2 * 180 / bins)
        doubled = np.radians(angles[nearby] * 2)
        angle = float(np.degrees(np.arctan2(np.sum(weights[nearby] * np.sin(doubled)),
                                             np.sum(weights[nearby] * np.cos(doubled)))) / 2 % 180)
        if all(_angle_distance(angle, old) >= 5 for old in result):
            result.append(angle)
        if len(result) >= maximum:
            break
    return sorted(result)


def _runs(active: np.ndarray) -> list[tuple[int, int]]:
    changes = np.diff(np.r_[False, active, False].astype(np.int8))
    return list(zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)))


def _bridge_small_gaps(active: np.ndarray, maximum: int) -> np.ndarray:
    result = active.copy()
    for begin, end in _runs(~active):
        if begin > 0 and end < len(active) and end - begin <= maximum:
            result[begin:end] = True
    return result


def _occupancy(t: np.ndarray, z: np.ndarray, t0: float, t1: float,
               z0: float, z1: float, cell: float) -> tuple[np.ndarray, float]:
    nt = max(1, int(math.ceil((t1 - t0) / cell)))
    nz = max(1, int(math.ceil((z1 - z0) / cell)))
    # Bound every raster; the returned effective cell is recorded, so large
    # objects cannot silently claim the requested fine resolution.
    factor = max(1.0, math.sqrt(nt * nz / 2_000_000))
    if factor > 1:
        cell *= factor
        nt, nz = max(1, int(math.ceil((t1 - t0) / cell))), max(1, int(math.ceil((z1 - z0) / cell)))
    occupied = np.zeros((nt, nz), dtype=bool)
    ti = np.clip(np.floor((t - t0) / cell).astype(int), 0, nt - 1)
    zi = np.clip(np.floor((z - z0) / cell).astype(int), 0, nz - 1)
    occupied[ti, zi] = True
    return occupied, cell


def _add_topology(candidates: list[RESFCandidate], evidence: dict[int, dict], tolerance: float) -> None:
    """Endpoint contacts and measured cycles; no assumed rectangular rooms."""
    size = 2 * len(candidates)
    parent = list(range(size))
    contacts = np.zeros(size, dtype=bool)
    continuation = np.zeros(len(candidates))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        parent[find(first)] = find(second)

    lines = []
    for candidate in candidates:
        th = math.radians(candidate.theta_deg)
        normal = np.array([math.cos(th), math.sin(th)])
        tangent = np.array([-normal[1], normal[0]])
        lines.append((normal, tangent, np.array([normal * candidate.d + tangent * candidate.t0,
                                               normal * candidate.d + tangent * candidate.t1])))
    for i, first in enumerate(candidates):
        n1, t1, ends1 = lines[i]
        for j in range(i + 1, len(candidates)):
            second = candidates[j]
            if first.storey_id != second.storey_id:
                continue
            n2, t2, ends2 = lines[j]
            angle = _angle_distance(first.theta_deg, second.theta_deg)
            if angle < 6:
                offset = abs(float((ends2.mean(axis=0) - ends1.mean(axis=0)) @ n1))
                gap = float(np.linalg.norm(ends1[:, None, :] - ends2[None, :, :], axis=2).min())
                if offset <= tolerance and gap <= tolerance * 3:
                    value = float(math.exp(-gap / max(tolerance, 1e-9)))
                    continuation[i] = max(continuation[i], value)
                    continuation[j] = max(continuation[j], value)
                continue
            if angle < 15:
                continue
            meet = np.linalg.solve(np.stack((n1, n2)), np.array([first.d, second.d]))
            distances1, distances2 = np.linalg.norm(ends1 - meet, axis=1), np.linalg.norm(ends2 - meet, axis=1)
            near1, near2 = int(np.argmin(distances1)), int(np.argmin(distances2))
            in1 = first.t0 - tolerance <= float(meet @ t1) <= first.t1 + tolerance
            in2 = second.t0 - tolerance <= float(meet @ t2) <= second.t1 + tolerance
            if in1 and in2:
                if distances1[near1] <= tolerance:
                    contacts[2 * i + near1] = True
                if distances2[near2] <= tolerance:
                    contacts[2 * j + near2] = True
                if distances1[near1] <= tolerance and distances2[near2] <= tolerance:
                    union(2 * i + near1, 2 * j + near2)
    adjacency: dict[int, list[tuple[int, int]]] = {}
    for i in range(len(candidates)):
        a, b = find(2 * i), find(2 * i + 1)
        adjacency.setdefault(a, []).append((b, i))
        adjacency.setdefault(b, []).append((a, i))
    for i, candidate in enumerate(candidates):
        start, target = find(2 * i), find(2 * i + 1)
        queue, seen = deque([start]), {start}
        while queue:
            node = queue.popleft()
            for other, edge_id in adjacency.get(node, []):
                if edge_id != i and other not in seen:
                    seen.add(other)
                    queue.append(other)
        closure = float(target in seen and start != target)
        junction = float(contacts[2 * i:2 * i + 2].mean())
        evidence[candidate.id].update(junction=junction, room_closure=closure, continuation=float(continuation[i]))
        candidate.T_W, candidate.T_R, candidate.T_CP = junction, closure, float(continuation[i])


def extract_candidates(points: np.ndarray, normals: np.ndarray, planarity: np.ndarray,
                       storeys: list[Storey], config: dict) -> tuple[list[RESFCandidate], dict[int, dict], dict]:
    """Multi-orientation, multi-peak RESF with local plane refinement.

    Only observed support contributes to A*, EH, EV, C and N. Missing samples
    do not imply occlusion or an opening. Both wall faces remain hypotheses;
    physical-wall pairing belongs to instance construction, not peak finding.
    """
    points = _finite_xyz(points)
    normals, planarity = np.asarray(normals, dtype=float), np.asarray(planarity, dtype=float)
    if (normals.shape != points.shape or planarity.shape != (len(points),)
            or not np.isfinite(normals).all() or not np.isfinite(planarity).all()
            or np.any((planarity < 0) | (planarity > 1))):
        raise ValueError("normal/planarity arrays must be finite, match points, and planarity must be in [0,1]")
    tolerance = _positive(config, "plane_tolerance_m", 0.04)
    step = _positive(config, "plane_spacing_m", 0.02)
    cell = _positive(config, "raster_cell_m", 0.10)
    minimum_length = _positive(config, "min_wall_length_m", 0.50)
    alignment = _positive(config, "normal_alignment_deg", 12.0)
    peak_fraction = _unit_interval(config, "resf_peak_fraction", 0.025)
    min_area = _unit_interval(config, "min_occupied_area_fraction", 0.08)
    gap = float(config.get("max_support_gap_m", 0.30))
    if not np.isfinite(gap) or gap < 0:
        raise ValueError("max_support_gap_m must be finite and nonnegative")
    max_peaks = int(config.get("max_peaks_per_orientation", 128))
    if max_peaks < 1:
        raise ValueError("max_peaks_per_orientation must be at least 1")
    if not config.get("multi_peak", True):
        max_peaks = 1
    min_support = max(12, int(config.get("min_plane_points", 35)))
    angles = _orientations(normals, planarity, config)
    minimum_planarity = _unit_interval(config, "min_planarity", 0.35)
    vertical = (np.abs(normals[:, 2]) <= math.sin(math.radians(20))) & (planarity >= minimum_planarity)
    point_angles = np.degrees(np.arctan2(normals[:, 1], normals[:, 0])) % 180
    candidates: list[RESFCandidate] = []
    evidence: dict[int, dict[str, Any]] = {}
    diagnostics: dict[str, Any] = {"orientations_deg": angles, "storeys": [],
        "semantic_evidence_available": False, "visibility_evidence_available": False,
        "normal_support_points": int(vertical.sum()), "peak_measure": "occupied tangent-height cells per distance bin",
        "multi_peak_enabled": bool(config.get("multi_peak", True)),
        "topology_enabled": bool(config.get("topology", True))}
    centre = points[:, :2].mean(axis=0) if len(points) else np.zeros(2)
    xy = points[:, :2] - centre
    for storey in storeys:
        height = storey.z1 - storey.z0
        zmargin = min(0.08, height * 0.025)
        level_mask = (points[:, 2] >= storey.z0 - zmargin) & (points[:, 2] <= storey.z1 + zmargin)
        storey_diag = {"storey_id": storey.id, "tested_peaks": 0, "retained_candidates": 0}
        for angle in angles:
            oriented = level_mask & vertical & (_angle_distance(point_angles, angle) <= alignment)
            oriented_ids = np.flatnonzero(oriented)
            if len(oriented_ids) < min_support:
                continue
            rad = math.radians(angle)
            normal = np.array([math.cos(rad), math.sin(rad)])
            tangent = np.array([-normal[1], normal[0]])
            dvalues, tvalues = xy[oriented_ids] @ normal, xy[oriented_ids] @ tangent
            dbase, tbase = float(dvalues.min() - 2 * tolerance), float(tvalues.min())
            distance_bins = np.floor((dvalues - dbase) / step).astype(np.int64)
            if distance_bins.max(initial=0) > 2_000_000:
                raise ValueError("distance extent/plane_spacing_m creates too many RESF bins")
            tangent_bins = np.floor((tvalues - tbase) / cell).astype(np.int64)
            height_bins = np.floor((points[oriented_ids, 2] - storey.z0) / cell).astype(np.int64)
            occupied = np.unique(np.column_stack((distance_bins, tangent_bins, height_bins)), axis=0)
            field = np.bincount(occupied[:, 0], minlength=int(distance_bins.max()) + 3)
            smooth = gaussian_filter1d(field.astype(float), max(0.65, tolerance / step * 0.55), mode="constant")
            peaks, _ = find_peaks(smooth, height=max(3, smooth.max() * peak_fraction))
            ranked = sorted(peaks, key=lambda p: (-smooth[p], p))
            selected: list[int] = []
            separation = max(0.07, 1.8 * tolerance)
            for peak in ranked:
                if all(abs(int(peak) - old) * step >= separation for old in selected):
                    selected.append(int(peak))
                if len(selected) >= max_peaks:
                    break
            for peak in selected:
                storey_diag["tested_peaks"] += 1
                guess = dbase + (peak + 0.5) * step
                support = np.abs(dvalues - guess) <= max(tolerance * 1.5, step * 1.5)
                ids = oriented_ids[support]
                if len(ids) < min_support:
                    continue
                # Fit in centred XY, then robustly reselect support.
                local = xy[ids]
                midpoint = np.median(local, axis=0)
                eigval, eigvec = np.linalg.eigh(np.cov((local - midpoint).T))
                fitted = eigvec[:, 0]
                if fitted @ normal < 0:
                    fitted = -fitted
                fitted_angle = float(np.degrees(np.arctan2(fitted[1], fitted[0])) % 180)
                if _angle_distance(fitted_angle, angle) > 5:
                    fitted = normal.copy()
                    fitted_angle = angle
                else:
                    fitted = np.array([math.cos(math.radians(fitted_angle)), math.sin(math.radians(fitted_angle))])
                fitted_tangent = np.array([-fitted[1], fitted[0]])
                offset = float(np.median(xy[ids] @ fitted))
                residual = np.abs(xy[oriented_ids] @ fitted - offset)
                ids = oriented_ids[residual <= tolerance]
                if len(ids) < min_support:
                    continue
                local_t = xy[ids] @ fitted_tangent
                low_t, high_t = map(float, np.quantile(local_t, [0.002, 0.998]))
                nsegments = max(1, int(math.ceil((high_t - low_t) / cell)))
                horizontal_bins = np.clip(np.floor((local_t - low_t) / cell).astype(int), 0, nsegments - 1)
                vertical_bins = np.floor((points[ids, 2] - storey.z0) / cell).astype(int)
                unique = np.unique(np.column_stack((horizontal_bins, vertical_bins)), axis=0)
                per_column = np.bincount(unique[:, 0], minlength=nsegments)
                required_height_cells = max(2, int(math.ceil(0.12 * height / cell)))
                active = per_column >= required_height_cells
                active = _bridge_small_gaps(active, int(round(gap / cell)))
                for start, finish in _runs(active):
                    chosen = (horizontal_bins >= start) & (horizontal_bins < finish)
                    segment_ids = ids[chosen]
                    if len(segment_ids) < min_support:
                        continue
                    segment_t = local_t[chosen]
                    t0, t1 = map(float, np.quantile(segment_t, [0.002, 0.998]))
                    z0, z1 = map(float, np.quantile(points[segment_ids, 2], [0.002, 0.998]))
                    if t1 - t0 < minimum_length or z1 - z0 < min(1.0, 0.40 * height):
                        continue
                    raster, effective_cell = _occupancy(segment_t, points[segment_ids, 2], t0, t1, storey.z0, storey.z1, cell)
                    area = float(raster.mean())
                    if area < min_area:
                        continue
                    labels, count = label(raster, structure=np.ones((3, 3)))
                    sizes = np.bincount(labels.ravel())[1:]
                    continuity = float(sizes.max(initial=0) / max(1, raster.sum()))
                    eh = float(raster.any(axis=1).mean())
                    ev = float(np.clip((z1 - z0) / height, 0, 1))
                    local_offset = float(np.median(xy[segment_ids] @ fitted))
                    residuals = xy[segment_ids] @ fitted - local_offset
                    rmse = float(np.sqrt(np.mean(residuals ** 2)))
                    normal_consistency = float(np.mean(np.abs(normals[segment_ids, :2] @ fitted)))
                    normal_quality = float(np.clip(math.exp(-0.5 * (rmse / max(tolerance * 0.5, 1e-6)) ** 2) * normal_consistency, 0, 1))
                    floor = float(math.exp(-0.5 * (max(0.0, z0 - storey.z0) / max(0.12, 2 * cell)) ** 2))
                    ceiling = float(math.exp(-0.5 * (max(0.0, storey.z1 - z1) / max(0.12, 2 * cell)) ** 2))
                    global_d = local_offset + float(centre @ fitted)
                    global_t0, global_t1 = t0 + float(centre @ fitted_tangent), t1 + float(centre @ fitted_tangent)
                    # Different angular modes may converge onto the same plane.
                    duplicate = False
                    for old in candidates:
                        if old.storey_id != storey.id or _angle_distance(old.theta_deg, fitted_angle) >= 3:
                            continue
                        old_n = np.array([math.cos(math.radians(old.theta_deg)), math.sin(math.radians(old.theta_deg))])
                        old_t = np.array([-old_n[1], old_n[0]])
                        endpoints = np.array([old_n * old.d + old_t * old.t0, old_n * old.d + old_t * old.t1])
                        separation_m = np.max(np.abs(endpoints @ fitted - global_d))
                        old_interval = np.sort(endpoints @ fitted_tangent)
                        overlap = max(0.0, min(old_interval[1], global_t1) - max(old_interval[0], global_t0))
                        if separation_m < tolerance and overlap / max(1e-9, min(old.t1 - old.t0, t1 - t0)) > 0.70:
                            duplicate = True
                            break
                    if duplicate:
                        continue
                    candidate_id = len(candidates) + 1
                    rho = float((area * eh * ev * normal_quality) ** 0.25)
                    candidate = RESFCandidate(candidate_id, storey.id, fitted_angle, global_d, global_t0, global_t1,
                                              storey.z0, storey.z1, area, eh, ev, continuity, normal_quality,
                                              0.0, rho, 0.0, T_F=floor, T_C=ceiling)
                    candidates.append(candidate)
                    evidence[candidate_id] = {
                        "A_star": area, "E_H": eh, "E_V": ev, "C": continuity, "N": normal_quality,
                        "floor_contact": floor, "ceiling_contact": ceiling,
                        "junction": 0.0, "room_closure": 0.0, "continuation": 0.0,
                        "support_indices": segment_ids.tolist(), "support_count": int(len(segment_ids)),
                        "plane_residual_m": rmse, "observed_z0": z0, "observed_z1": z1,
                        "observed_t0": global_t0, "observed_t1": global_t1,
                        "occupied_cell_count": int(raster.sum()), "occupancy_shape": list(raster.shape),
                        "raster_cell_m": effective_cell, "orientation_source": "local_PCA_normal_modes",
                        "semantic_evidence_available": False, "visibility_evidence_available": False,
                        "height_extent_source": "storey_envelope; observed extents recorded separately",
                    }
                    storey_diag["retained_candidates"] += 1
        diagnostics["storeys"].append(storey_diag)
    if config.get("topology", True):
        _add_topology(candidates, evidence, _positive(config, "junction_tolerance_m", 0.25))
    else:
        # Absence of a graph computation is missing evidence, not evidence
        # that a wall has no junctions or that an enclosure is open.
        for item in evidence.values():
            for key in ("junction", "room_closure", "continuation"):
                item.pop(key)
    diagnostics["candidate_count"] = len(candidates)
    return candidates, evidence, diagnostics
