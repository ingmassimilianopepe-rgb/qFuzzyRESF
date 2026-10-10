"""Fuzzy-RESF reference backend used by the CloudCompare plugin.

This implementation follows the manuscript contract more closely than the original v0.1
scaffold: acquisition density is regularised, candidate planes are scored from projected
occupancy (not raw point count), several local RESF peaks are retained per orientation,
coplanar fragments are consolidated into BIM wall instances, and wall-local occupancy is
used to infer door/window openings.  The backend remains deliberately dependency-light
(NumPy only) so the Windows portable package is easy to reproduce.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import csv
import json
import math
from collections import deque
from typing import Iterable

import numpy as np


@dataclass
class WallHypothesis:
    id: int
    theta_deg: float
    d: float
    x1: float
    y1: float
    x2: float
    y2: float
    z0: float
    z1: float
    thickness: float
    area_support: float
    horizontal_extent: float
    vertical_extent: float
    continuity: float
    boundary_support: float
    occlusion_support: float
    confidence: float
    state: str


@dataclass
class OpeningHypothesis:
    id: int
    wall_id: int
    kind: str
    offset: float
    width: float
    sill: float
    height: float
    confidence: float


def _load_xyz(path: str | Path, max_points: int = 1_500_000) -> np.ndarray:
    pts = np.loadtxt(path, dtype=np.float64, usecols=(0, 1, 2))
    if pts.ndim == 1:
        pts = pts.reshape(1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) > max_points:
        idx = np.linspace(0, len(pts) - 1, max_points, dtype=np.int64)
        pts = pts[idx]
    return pts


def _voxel_subsample(pts: np.ndarray, voxel: float) -> np.ndarray:
    if len(pts) == 0 or voxel <= 0:
        return pts
    origin = pts.min(axis=0)
    q = np.floor((pts - origin) / voxel).astype(np.int64)
    _, idx = np.unique(q, axis=0, return_index=True)
    return pts[np.sort(idx)]


def _circular_distance_deg(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _smooth1d(values: np.ndarray, radius: int = 2) -> np.ndarray:
    if radius <= 0 or values.size == 0:
        return values.astype(float, copy=True)
    kernel = np.ones(2 * radius + 1, dtype=float)
    kernel /= kernel.sum()
    return np.convolve(values.astype(float), kernel, mode="same")


def _detect_vertical_band(z: np.ndarray) -> tuple[float, float]:
    """Robust floor/ceiling estimate using persistent horizontal levels plus quantiles."""
    if len(z) < 50:
        return float(z.min()), float(z.max())
    lo, hi = map(float, np.quantile(z, [0.005, 0.995]))
    if hi <= lo:
        return lo, hi
    dz = max(0.02, min(0.06, (hi - lo) / 250.0))
    n = max(16, int(math.ceil((hi - lo) / dz)))
    hist, edges = np.histogram(z, bins=n, range=(lo, hi))
    smooth = _smooth1d(hist, 2)
    lower = np.arange(len(smooth)) < max(2, int(0.30 * len(smooth)))
    upper = np.arange(len(smooth)) > min(len(smooth) - 3, int(0.70 * len(smooth)))
    floor_idx = int(np.argmax(np.where(lower, smooth, -1)))
    ceil_idx = int(np.argmax(np.where(upper, smooth, -1)))
    floor_z = float(0.5 * (edges[floor_idx] + edges[floor_idx + 1]))
    ceil_z = float(0.5 * (edges[ceil_idx] + edges[ceil_idx + 1]))
    # Guard against furniture-dominated horizontal modes.
    q02, q98 = map(float, np.quantile(z, [0.02, 0.98]))
    if ceil_z - floor_z < 1.6:
        floor_z, ceil_z = q02, q98
    else:
        floor_z = min(max(floor_z, q02 - 0.15), q02 + 0.20)
        ceil_z = max(min(ceil_z, q98 + 0.15), q98 - 0.20)
    return floor_z, ceil_z


def _orientation_score(xy: np.ndarray, theta_deg: float, bin_width: float) -> float:
    th = math.radians(theta_deg)
    n = np.array([math.cos(th), math.sin(th)])
    tvec = np.array([-math.sin(th), math.cos(th)])
    d = xy @ n
    t = xy @ tvec
    lo, hi = float(d.min()), float(d.max())
    if hi <= lo:
        return 0.0
    nb = max(16, int(math.ceil((hi - lo) / bin_width)))
    hist, edges = np.histogram(d, bins=nb, range=(lo, hi))
    # Weight each offset bin by horizontal coverage, reducing scanner-density bias.
    order = np.argsort(hist)[::-1][: min(12, len(hist))]
    scores = []
    for idx in order:
        center = 0.5 * (edges[idx] + edges[idx + 1])
        mask = np.abs(d - center) <= 1.5 * bin_width
        if mask.sum() < 20:
            continue
        tt = t[mask]
        span = float(np.quantile(tt, 0.98) - np.quantile(tt, 0.02))
        scores.append(math.log1p(mask.sum()) * max(span, 0.0))
    return float(np.mean(scores[:4])) if scores else 0.0


def detect_orientations(xy: np.ndarray, angle_step_deg: float, max_families: int) -> list[float]:
    angles = np.arange(0.0, 180.0, max(angle_step_deg, 0.5), dtype=float)
    span = max(float(np.ptp(xy[:, 0])), float(np.ptp(xy[:, 1])), 1.0)
    score_bin = max(0.04, min(0.12, span / 600.0))
    scored = [(float(a), _orientation_score(xy, float(a), score_bin)) for a in angles]
    scored.sort(key=lambda x: x[1], reverse=True)
    selected: list[float] = []
    separation = max(4.0, 2.0 * angle_step_deg)
    for angle, score in scored:
        if score <= 0:
            continue
        if all(_circular_distance_deg(angle, s) >= separation for s in selected):
            selected.append(angle)
        if len(selected) >= max_families:
            break
    return sorted(selected)


def _occupancy_metrics(tt: np.ndarray, zz: np.ndarray, t0: float, t1: float,
                       z0: float, z1: float, cell: float) -> tuple[float, float]:
    if t1 <= t0 or z1 <= z0 or len(tt) == 0:
        return 0.0, 0.0
    cell = max(cell, 0.04)
    nt = int(np.clip(math.ceil((t1 - t0) / cell), 1, 900))
    nz = int(np.clip(math.ceil((z1 - z0) / cell), 1, 450))
    ti = np.clip(((tt - t0) / max(t1 - t0, 1e-9) * nt).astype(int), 0, nt - 1)
    zi = np.clip(((zz - z0) / max(z1 - z0, 1e-9) * nz).astype(int), 0, nz - 1)
    occ = np.zeros((nt, nz), dtype=bool)
    occ[ti, zi] = True
    visible = float(occ.mean())
    # Continuity = fraction of horizontal columns with observations in at least 20% of height cells.
    column = occ.mean(axis=1)
    continuity = float(np.mean(column >= max(0.05, 0.20 * np.quantile(column[column > 0], 0.75)))) if np.any(column > 0) else 0.0
    return visible, continuity


def _contiguous_intervals(values: np.ndarray, min_count: int, max_gap_bins: int = 2) -> list[tuple[int, int]]:
    active = values >= min_count
    if max_gap_bins > 0 and active.any():
        # close short internal gaps
        i = 0
        while i < len(active):
            if active[i]:
                i += 1
                continue
            j = i
            while j < len(active) and not active[j]:
                j += 1
            if i > 0 and j < len(active) and j - i <= max_gap_bins:
                active[i:j] = True
            i = j
    out = []
    i = 0
    while i < len(active):
        if not active[i]:
            i += 1
            continue
        j = i + 1
        while j < len(active) and active[j]:
            j += 1
        out.append((i, j))
        i = j
    return out


def _estimate_thickness(proj_n: np.ndarray, d: float, tolerance: float) -> float:
    local = proj_n[np.abs(proj_n - d) <= max(0.35, 4.0 * tolerance)]
    if len(local) < 30:
        return 0.20
    delta = local - d
    # Wall faces often produce two nearby modes.  Use robust spread but avoid furniture-scale depths.
    q10, q90 = np.quantile(delta, [0.10, 0.90])
    spread = float(q90 - q10)
    return float(np.clip(spread, 0.10, 0.40))


def _peak_indices(field: np.ndarray, min_relative: float, min_separation_bins: int) -> list[int]:
    if field.size < 3 or field.max(initial=0.0) <= 0:
        return []
    threshold = float(field.max()) * min_relative
    candidates = [i for i in range(1, len(field) - 1)
                  if field[i] >= threshold and field[i] >= field[i - 1] and field[i] >= field[i + 1]]
    candidates.sort(key=lambda i: field[i], reverse=True)
    selected = []
    for i in candidates:
        if all(abs(i - j) >= min_separation_bins for j in selected):
            selected.append(i)
    return sorted(selected)


def _classify(area: float, eh: float, ev: float, continuity: float, topology: float,
              confidence_threshold: float, fuzzy: bool, occlusion: bool) -> tuple[float, float, str]:
    # Approximate manuscript fuzzy evidence fusion with overlapping continuous evidence.
    resf = float(np.clip((max(area, 1e-6) * max(eh, 1e-6) * max(ev, 1e-6) * max(continuity, 1e-6)) ** 0.25, 0, 1))
    floor_ceiling_contact = ev
    occlusion_support = float(np.clip((1.0 - continuity) * topology * max(ev, 0.4), 0, 1)) if occlusion else 0.0
    score = 0.44 * resf + 0.22 * floor_ceiling_contact + 0.20 * topology + 0.14 * area
    confidence = float(1.0 / (1.0 + math.exp(-8.0 * (score - 0.46)))) if fuzzy else float(np.clip(score, 0, 1))
    if ev < 0.28 or eh < 0.06:
        state = "Clutter"
    elif confidence >= confidence_threshold and (ev >= 0.45 or topology >= 0.55):
        state = "Permanent"
    elif occlusion_support >= 0.22 and topology >= 0.40:
        state = "OccludedPermanent"
    elif confidence >= 0.30:
        state = "Uncertain"
    else:
        state = "Clutter"
    return confidence, occlusion_support, state


def _merge_coplanar(walls: list[WallHypothesis]) -> list[WallHypothesis]:
    """Consolidate fragments belonging to one physical wall, including gaps caused by openings."""
    if not walls:
        return []
    groups: list[list[WallHypothesis]] = []
    for w in sorted(walls, key=lambda x: (x.theta_deg, x.d)):
        placed = False
        for g in groups:
            r = g[0]
            if _circular_distance_deg(w.theta_deg, r.theta_deg) <= 3.0 and abs(w.d - r.d) <= max(0.16, 0.65 * max(w.thickness, r.thickness)):
                g.append(w)
                placed = True
                break
        if not placed:
            groups.append([w])

    merged: list[WallHypothesis] = []
    for group in groups:
        # Split only when projected fragments are separated by very large gaps (> 1.8 m).
        ref = max(group, key=lambda x: x.confidence)
        th = math.radians(ref.theta_deg)
        tvec = np.array([-math.sin(th), math.cos(th)])
        fragments = []
        for w in group:
            a = float(np.dot(np.array([w.x1, w.y1]), tvec))
            b = float(np.dot(np.array([w.x2, w.y2]), tvec))
            fragments.append((min(a, b), max(a, b), w))
        fragments.sort(key=lambda x: x[0])
        chunks: list[list[tuple[float, float, WallHypothesis]]] = []
        for frag in fragments:
            if not chunks or frag[0] - max(x[1] for x in chunks[-1]) > 1.8:
                chunks.append([frag])
            else:
                chunks[-1].append(frag)
        for chunk in chunks:
            t0 = min(x[0] for x in chunk)
            t1 = max(x[1] for x in chunk)
            ww = [x[2] for x in chunk]
            d = float(np.average([x.d for x in ww], weights=[max(x.confidence, 0.05) for x in ww]))
            n = np.array([math.cos(th), math.sin(th)])
            p1 = n * d + tvec * t0
            p2 = n * d + tvec * t1
            state_rank = {"Permanent": 3, "OccludedPermanent": 2, "Uncertain": 1, "Clutter": 0}
            best_state = max((x.state for x in ww), key=lambda s: state_rank[s])
            merged.append(WallHypothesis(
                id=0, theta_deg=ref.theta_deg, d=d,
                x1=float(p1[0]), y1=float(p1[1]), x2=float(p2[0]), y2=float(p2[1]),
                z0=min(x.z0 for x in ww), z1=max(x.z1 for x in ww),
                thickness=float(np.clip(np.median([x.thickness for x in ww]), 0.10, 0.40)),
                area_support=float(max(x.area_support for x in ww)),
                horizontal_extent=float(max(x.horizontal_extent for x in ww)),
                vertical_extent=float(max(x.vertical_extent for x in ww)),
                continuity=float(max(x.continuity for x in ww)),
                boundary_support=float(max(x.boundary_support for x in ww)),
                occlusion_support=float(max(x.occlusion_support for x in ww)),
                confidence=float(max(x.confidence for x in ww)), state=best_state,
            ))
    merged.sort(key=lambda w: w.confidence, reverse=True)
    for i, w in enumerate(merged, 1):
        w.id = i
    return merged


def _junction_support(wall: WallHypothesis, walls: list[WallHypothesis]) -> float:
    endpoints = [np.array([wall.x1, wall.y1]), np.array([wall.x2, wall.y2])]
    hits = 0
    for p in endpoints:
        for other in walls:
            if other is wall or _circular_distance_deg(wall.theta_deg, other.theta_deg) < 12.0:
                continue
            a = np.array([other.x1, other.y1])
            b = np.array([other.x2, other.y2])
            ab = b - a
            u = float(np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-9), 0, 1))
            q = a + u * ab
            if np.linalg.norm(p - q) <= 0.35:
                hits += 1
                break
    return hits / 2.0


def _binary_close(occ: np.ndarray, iterations: int = 1) -> np.ndarray:
    out = occ.copy()
    for _ in range(iterations):
        p = np.pad(out.astype(np.uint8), 1)
        neigh = sum(p[1+i:1+i+out.shape[0], 1+j:1+j+out.shape[1]]
                    for i in (-1, 0, 1) for j in (-1, 0, 1))
        out = neigh >= 3
    return out


def _empty_components(empty: np.ndarray) -> list[list[tuple[int, int]]]:
    seen = np.zeros_like(empty, dtype=bool)
    comps = []
    nx, nz = empty.shape
    for x in range(nx):
        for z in range(nz):
            if not empty[x, z] or seen[x, z]:
                continue
            q = deque([(x, z)])
            seen[x, z] = True
            comp = []
            while q:
                i, k = q.popleft()
                comp.append((i, k))
                for di, dk in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ni, nk = i + di, k + dk
                    if 0 <= ni < nx and 0 <= nk < nz and empty[ni, nk] and not seen[ni, nk]:
                        seen[ni, nk] = True
                        q.append((ni, nk))
            comps.append(comp)
    return comps


def infer_openings(pts: np.ndarray, walls: list[WallHypothesis], cell: float = 0.08) -> list[OpeningHypothesis]:
    openings: list[OpeningHypothesis] = []
    oid = 1
    xy, z = pts[:, :2], pts[:, 2]
    for wall in walls:
        if wall.state not in {"Permanent", "OccludedPermanent"}:
            continue
        th = math.radians(wall.theta_deg)
        n = np.array([math.cos(th), math.sin(th)])
        tvec = np.array([-math.sin(th), math.cos(th)])
        pn = xy @ n
        pt = xy @ tvec
        wa = float(np.dot(np.array([wall.x1, wall.y1]), tvec))
        wb = float(np.dot(np.array([wall.x2, wall.y2]), tvec))
        t0, t1 = min(wa, wb), max(wa, wb)
        length = t1 - t0
        height = wall.z1 - wall.z0
        if length < 1.0 or height < 1.5:
            continue
        mask = ((np.abs(pn - wall.d) <= max(0.10, 0.65 * wall.thickness)) &
                (pt >= t0) & (pt <= t1) & (z >= wall.z0) & (z <= wall.z1))
        if mask.sum() < 80:
            continue
        tt, zz = pt[mask], z[mask]
        nx = int(np.clip(math.ceil(length / cell), 8, 500))
        nz = int(np.clip(math.ceil(height / cell), 12, 160))
        counts = np.zeros((nx, nz), dtype=np.uint16)
        xi = np.clip(((tt - t0) / length * nx).astype(int), 0, nx - 1)
        zi = np.clip(((zz - wall.z0) / height * nz).astype(int), 0, nz - 1)
        np.add.at(counts, (xi, zi), 1)
        nonzero = counts[counts > 0]
        threshold = max(1, int(np.quantile(nonzero, 0.30))) if nonzero.size else 1
        occupied = counts >= threshold
        occupied = _binary_close(occupied, 1)
        # Ignore a one-cell frame: exterior emptiness is not an architectural opening.
        interior = np.ones_like(occupied, dtype=bool)
        interior[[0, -1], :] = False
        interior[:, -1] = False
        empty = (~occupied) & interior
        for comp in _empty_components(empty):
            xs = np.array([p[0] for p in comp])
            zs = np.array([p[1] for p in comp])
            x0, x1 = int(xs.min()), int(xs.max()) + 1
            z0i, z1i = int(zs.min()), int(zs.max()) + 1
            width = (x1 - x0) * length / nx
            op_h = (z1i - z0i) * height / nz
            sill = z0i * height / nz
            touches_bottom = z0i <= max(1, int(0.12 / max(height / nz, 1e-6)))
            # Reject long occluded strips and tiny scan holes.
            if width < 0.45 or width > 2.60 or op_h < 0.45 or op_h > 2.60:
                continue
            # Opening must be surrounded by observed wall on left/right and (for windows) below/above.
            pad = 2
            lx = occupied[max(0, x0-pad):x0, z0i:z1i].mean() if x0 > 0 else 0.0
            rx = occupied[x1:min(nx, x1+pad), z0i:z1i].mean() if x1 < nx else 0.0
            above = occupied[x0:x1, z1i:min(nz, z1i+pad)].mean() if z1i < nz else 0.0
            below = occupied[x0:x1, max(0, z0i-pad):z0i].mean() if z0i > 0 else 0.0
            surround = float((lx + rx + above + (0.0 if touches_bottom else below)) / (3.0 if touches_bottom else 4.0))
            if surround < 0.18:
                continue
            if touches_bottom and 0.60 <= width <= 1.80 and 1.55 <= op_h <= min(2.50, height * 0.92):
                kind = "Door"
                sill = 0.0
            elif (not touches_bottom and 0.45 <= sill <= 1.65 and
                  0.50 <= op_h <= 1.80 and width <= 2.60):
                kind = "Window"
            else:
                continue
            offset = ((x0 + x1) * 0.5 / nx) * length
            confidence = float(np.clip(0.45 + 0.55 * surround, 0, 1))
            # Avoid duplicate overlapping openings on the same wall.
            if any(o.wall_id == wall.id and abs(o.offset - offset) < 0.35 and o.kind == kind for o in openings):
                continue
            openings.append(OpeningHypothesis(oid, wall.id, kind, offset, width, sill, op_h, confidence))
            oid += 1
    return openings


def reconstruct(request: dict) -> tuple[list[WallHypothesis], list[OpeningHypothesis], dict]:
    raw = _load_xyz(request["input"], int(request.get("max_points", 1_500_000)))
    if len(raw) < 50:
        raise ValueError("Point cloud contains too few valid points for RESF reconstruction")

    raster = float(request.get("raster_cell_m", 0.02))
    pts = _voxel_subsample(raw, max(0.025, min(0.06, raster * 1.5)))
    xy, z = pts[:, :2], pts[:, 2]
    z_floor, z_ceiling = _detect_vertical_band(z)
    room_height = max(0.1, z_ceiling - z_floor)

    plane_spacing = max(0.01, float(request.get("plane_spacing_m", 0.01)))
    tolerance = max(0.015, float(request.get("plane_tolerance_m", 0.03)))
    angle_step = float(request.get("angle_step_deg", 2.0))
    max_families = int(request.get("max_orientation_families", 12))
    confidence_threshold = float(request.get("confidence_threshold", 0.45))
    multi_peak = bool(request.get("multi_peak", True))
    topology_enabled = bool(request.get("topology", True))
    occlusion = bool(request.get("occlusion", True))
    fuzzy = bool(request.get("fuzzy", True))

    orientations = detect_orientations(xy, angle_step, max_families)
    raw_candidates: list[WallHypothesis] = []

    for theta_deg in orientations:
        th = math.radians(theta_deg)
        n = np.array([math.cos(th), math.sin(th)])
        tvec = np.array([-math.sin(th), math.cos(th)])
        proj_n = xy @ n
        proj_t = xy @ tvec
        dmin, dmax = float(proj_n.min()), float(proj_n.max())
        t_room_min, t_room_max = float(proj_t.min()), float(proj_t.max())
        room_t_extent = max(0.1, t_room_max - t_room_min)

        step = max(plane_spacing, min(0.05, tolerance))
        nbins = max(8, int(math.ceil((dmax - dmin) / step)))
        hist, edges = np.histogram(proj_n, bins=nbins, range=(dmin, dmax))
        smooth = _smooth1d(hist, max(1, int(round(tolerance / max(step, 1e-6)))))
        separation = max(2, int(round(0.12 / max(step, 1e-6))))
        peaks = _peak_indices(smooth, 0.08 if multi_peak else 0.35, separation)
        if not multi_peak and peaks:
            peaks = [max(peaks, key=lambda i: smooth[i])]
        peaks = sorted(peaks, key=lambda i: smooth[i], reverse=True)[:20]

        for idx in peaks:
            d = float(0.5 * (edges[idx] + edges[idx + 1]))
            dist = np.abs(proj_n - d)
            mask = dist <= tolerance
            if mask.sum() < 40:
                continue
            tt_all = proj_t[mask]
            zz_all = z[mask]
            # Split support into horizontal fragments; short gaps are closed, door-sized gaps remain available for later consolidation/opening inference.
            tlo, thi = map(float, np.quantile(tt_all, [0.01, 0.99]))
            tbin = max(0.08, min(0.18, raster * 4.0))
            nt = max(4, int(math.ceil((thi - tlo) / tbin)))
            thist, tedges = np.histogram(tt_all, bins=nt, range=(tlo, thi))
            nz = thist[thist > 0]
            min_count = max(2, int(np.quantile(nz, 0.20) * 0.30)) if nz.size else 2
            intervals = _contiguous_intervals(thist, min_count, max_gap_bins=2)
            for a, b in intervals:
                t0, t1 = float(tedges[a]), float(tedges[b])
                seg = mask & (proj_t >= t0) & (proj_t <= t1)
                if seg.sum() < 35 or t1 - t0 < 0.30:
                    continue
                tt, zz = proj_t[seg], z[seg]
                zz0, zz1 = map(float, np.quantile(zz, [0.02, 0.98]))
                wz0 = max(z_floor, zz0)
                wz1 = min(z_ceiling, zz1)
                if wz1 - wz0 < 0.50:
                    continue
                area, continuity = _occupancy_metrics(tt, zz, t0, t1, wz0, wz1, max(raster, 0.05))
                eh = float(np.clip((t1 - t0) / room_t_extent, 0, 1))
                ev = float(np.clip((wz1 - wz0) / room_height, 0, 1))
                # Topological prior: floor/ceiling contact + long architectural extent. Junction support is added after consolidation.
                floor_contact = math.exp(-((wz0 - z_floor) ** 2) / (2 * 0.18 ** 2))
                ceil_contact = math.exp(-((wz1 - z_ceiling) ** 2) / (2 * 0.22 ** 2))
                topo = float(np.clip(0.40 * floor_contact + 0.30 * ceil_contact + 0.30 * min(1.0, eh / 0.25), 0, 1)) if topology_enabled else 0.5
                confidence, occ, state = _classify(area, eh, ev, continuity, topo, confidence_threshold, fuzzy, occlusion)
                p1 = n * d + tvec * t0
                p2 = n * d + tvec * t1
                raw_candidates.append(WallHypothesis(
                    id=0, theta_deg=theta_deg, d=d,
                    x1=float(p1[0]), y1=float(p1[1]), x2=float(p2[0]), y2=float(p2[1]),
                    z0=wz0, z1=wz1, thickness=_estimate_thickness(proj_n, d, tolerance),
                    area_support=float(np.clip(area, 0, 1)), horizontal_extent=eh,
                    vertical_extent=ev, continuity=float(np.clip(continuity, 0, 1)),
                    boundary_support=topo, occlusion_support=occ,
                    confidence=confidence, state=state,
                ))

    walls = _merge_coplanar(raw_candidates)
    # Re-evaluate topology after consolidation so connected partitions are rewarded.
    for wall in walls:
        junction = _junction_support(wall, walls) if topology_enabled else 0.5
        wall.boundary_support = float(np.clip(0.70 * wall.boundary_support + 0.30 * junction, 0, 1))
        conf, occ, state = _classify(wall.area_support, wall.horizontal_extent, wall.vertical_extent,
                                     wall.continuity, wall.boundary_support, confidence_threshold,
                                     fuzzy, occlusion)
        wall.confidence = max(wall.confidence, conf)
        wall.occlusion_support = max(wall.occlusion_support, occ)
        if state_rank(state) > state_rank(wall.state):
            wall.state = state

    # Do not export clutter as BIM walls; retain uncertain hypotheses only for diagnostics.
    bim_walls = [w for w in walls if w.state in {"Permanent", "OccludedPermanent"}]
    if not bim_walls:
        # Fall back to strongest plausible hypotheses instead of silently returning an empty IFC.
        plausible = [w for w in walls if w.vertical_extent >= 0.45 and w.horizontal_extent >= 0.05]
        plausible.sort(key=lambda w: w.confidence, reverse=True)
        bim_walls = plausible[: max(1, min(12, len(plausible)))]
        for w in bim_walls:
            w.state = "Uncertain"
    for i, w in enumerate(bim_walls, 1):
        w.id = i

    openings = infer_openings(pts, bim_walls, cell=max(0.06, min(0.10, raster * 3.0)))
    summary = {
        "status": "success",
        "method": "Fuzzy-RESF",
        "version": "1.1-resf-bim",
        "input_points": int(len(raw)),
        "input_points_regularized": int(len(pts)),
        "floor_z": z_floor,
        "ceiling_z": z_ceiling,
        "orientation_families_deg": orientations,
        "raw_hypotheses": len(raw_candidates),
        "consolidated_hypotheses": len(walls),
        "bim_walls": len(bim_walls),
        "doors": sum(o.kind == "Door" for o in openings),
        "windows": sum(o.kind == "Window" for o in openings),
        "openings": len(openings),
        "warning": None if bim_walls else "No BIM wall instances reconstructed",
    }
    return bim_walls, openings, summary


def state_rank(state: str) -> int:
    return {"Permanent": 3, "OccludedPermanent": 2, "Uncertain": 1, "Clutter": 0}.get(state, 0)


def write_walls_csv(walls: Iterable[WallHypothesis], path: str | Path) -> None:
    walls = list(walls)
    fieldnames = list(asdict(walls[0]).keys()) if walls else [
        "id", "theta_deg", "d", "x1", "y1", "x2", "y2", "z0", "z1", "thickness",
        "area_support", "horizontal_extent", "vertical_extent", "continuity", "boundary_support",
        "occlusion_support", "confidence", "state"
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for wall in walls:
            writer.writerow(asdict(wall))


def write_openings_csv(openings: Iterable[OpeningHypothesis], path: str | Path) -> None:
    openings = list(openings)
    fieldnames = list(asdict(openings[0]).keys()) if openings else [
        "id", "wall_id", "kind", "offset", "width", "sill", "height", "confidence"
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for opening in openings:
            writer.writerow(asdict(opening))


def write_result_json(summary: dict, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
