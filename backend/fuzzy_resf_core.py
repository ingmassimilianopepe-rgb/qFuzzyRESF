"""Reference Python backend for qFuzzyRESF v0.1.

This module intentionally favors transparency over peak performance.  It implements the
same conceptual stages used by the paper: multi-orientation support, multi-peak extraction,
room-boundary/topology cues and fuzzy-style confidence fusion.  Future releases can replace
individual stages with the frozen research implementation while preserving the JSON/CSV
contract consumed by the CloudCompare plugin.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import csv
import json
import math
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


def _load_xyz(path: str | Path, max_points: int = 1_500_000) -> np.ndarray:
    pts = np.loadtxt(path, dtype=np.float64, usecols=(0, 1, 2))
    if pts.ndim == 1:
        pts = pts.reshape(1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) > max_points:
        idx = np.linspace(0, len(pts) - 1, max_points, dtype=np.int64)
        pts = pts[idx]
    return pts


def _circular_distance_deg(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _orientation_score(xy: np.ndarray, theta_deg: float, bin_width: float) -> float:
    th = math.radians(theta_deg)
    n = np.array([math.cos(th), math.sin(th)])
    d = xy @ n
    lo, hi = float(d.min()), float(d.max())
    if hi <= lo:
        return 0.0
    bins = max(16, int(math.ceil((hi - lo) / bin_width)))
    hist, _ = np.histogram(d, bins=bins, range=(lo, hi))
    if hist.size == 0:
        return 0.0
    # Persistent planes create a small number of unusually populated offset bins.
    k = max(1, min(8, hist.size))
    top = np.partition(hist, -k)[-k:]
    return float(np.mean(top) / max(1.0, np.mean(hist)))


def detect_orientations(xy: np.ndarray, angle_step_deg: float, max_families: int) -> list[float]:
    angles = np.arange(0.0, 180.0, angle_step_deg, dtype=float)
    span = max(float(np.ptp(xy[:, 0])), float(np.ptp(xy[:, 1])), 1.0)
    score_bin = max(0.03, min(0.12, span / 500.0))
    scored = [(float(a), _orientation_score(xy, float(a), score_bin)) for a in angles]
    scored.sort(key=lambda x: x[1], reverse=True)
    selected: list[float] = []
    for angle, _ in scored:
        if all(_circular_distance_deg(angle, s) >= max(5.0, 2.0 * angle_step_deg) for s in selected):
            selected.append(angle)
        if len(selected) >= max_families:
            break
    return sorted(selected)


def _continuity_score(t: np.ndarray, z: np.ndarray, t0: float, t1: float, z0: float, z1: float, cell: float) -> float:
    if t1 <= t0 or z1 <= z0:
        return 0.0
    nt = max(1, int(math.ceil((t1 - t0) / max(cell, 1e-3))))
    nz = max(1, int(math.ceil((z1 - z0) / max(cell, 1e-3))))
    nt = min(nt, 600)
    nz = min(nz, 300)
    ti = np.clip(((t - t0) / (t1 - t0) * nt).astype(int), 0, nt - 1)
    zi = np.clip(((z - z0) / (z1 - z0) * nz).astype(int), 0, nz - 1)
    occ = np.zeros((nt, nz), dtype=bool)
    occ[ti, zi] = True
    return float(occ.mean())


def _classify(area: float, eh: float, ev: float, continuity: float, boundary: float, confidence_threshold: float,
              fuzzy: bool, topology: bool, occlusion: bool) -> tuple[float, float, str]:
    # Interpretable fuzzy-style fusion. Inputs are already normalized to [0,1].
    score = 0.34 * area + 0.18 * eh + 0.24 * ev + 0.10 * continuity
    if topology:
        score += 0.14 * boundary
    else:
        score += 0.07

    occ = float(np.clip((1.0 - continuity) * boundary * ev, 0.0, 1.0)) if occlusion else 0.0

    if not fuzzy:
        confidence = float(np.clip(score, 0.0, 1.0))
    else:
        # Smooth transition around the paper's frozen working threshold.
        confidence = float(1.0 / (1.0 + math.exp(-8.0 * (score - 0.50))))

    if ev < 0.35 or (boundary < 0.20 and eh < 0.30):
        state = "Clutter"
    elif confidence >= confidence_threshold:
        state = "Permanent"
    elif occ >= 0.35 and boundary >= 0.45:
        state = "OccludedPermanent"
    else:
        state = "Uncertain"
    return confidence, occ, state


def reconstruct(request: dict) -> tuple[list[WallHypothesis], dict]:
    pts = _load_xyz(request["input"], int(request.get("max_points", 1_500_000)))
    if len(pts) < 50:
        raise ValueError("Point cloud contains too few valid points for RESF reconstruction")

    xy = pts[:, :2]
    z = pts[:, 2]
    z_floor = float(np.quantile(z, 0.02))
    z_ceiling = float(np.quantile(z, 0.98))
    room_height = max(0.1, z_ceiling - z_floor)

    plane_spacing = float(request.get("plane_spacing_m", 0.01))
    tolerance = float(request.get("plane_tolerance_m", 0.03))
    raster = float(request.get("raster_cell_m", 0.02))
    angle_step = float(request.get("angle_step_deg", 2.0))
    max_families = int(request.get("max_orientation_families", 12))
    confidence_threshold = float(request.get("confidence_threshold", 0.45))
    multi_peak = bool(request.get("multi_peak", True))
    topology = bool(request.get("topology", True))
    occlusion = bool(request.get("occlusion", True))
    fuzzy = bool(request.get("fuzzy", True))

    orientations = detect_orientations(xy, angle_step, max_families)
    candidates: list[WallHypothesis] = []
    wall_id = 1

    for theta_deg in orientations:
        th = math.radians(theta_deg)
        n = np.array([math.cos(th), math.sin(th)])
        tvec = np.array([-math.sin(th), math.cos(th)])
        proj_n = xy @ n
        proj_t = xy @ tvec
        dmin, dmax = float(proj_n.min()), float(proj_n.max())
        t_room_min, t_room_max = float(proj_t.min()), float(proj_t.max())
        room_t_extent = max(0.1, t_room_max - t_room_min)

        bin_w = max(plane_spacing, tolerance)
        nbins = max(8, int(math.ceil((dmax - dmin) / bin_w)))
        hist, edges = np.histogram(proj_n, bins=nbins, range=(dmin, dmax))
        if hist.max(initial=0) <= 0:
            continue

        order = np.argsort(hist)[::-1]
        chosen: list[int] = []
        for idx in order:
            if hist[idx] < max(20, 0.12 * hist[order[0]]):
                break
            center = 0.5 * (edges[idx] + edges[idx + 1])
            if any(abs(center - 0.5 * (edges[j] + edges[j + 1])) < 0.30 for j in chosen):
                continue
            chosen.append(int(idx))
            if not multi_peak or len(chosen) >= 12:
                break

        for idx in chosen:
            d = float(0.5 * (edges[idx] + edges[idx + 1]))
            mask = np.abs(proj_n - d) <= max(tolerance, bin_w)
            if mask.sum() < 30:
                continue
            tt = proj_t[mask]
            zz = z[mask]
            t0, t1 = map(float, np.quantile(tt, [0.02, 0.98]))
            zz0, zz1 = map(float, np.quantile(zz, [0.02, 0.98]))
            z0 = max(z_floor, zz0)
            z1 = min(z_ceiling, zz1)
            if t1 - t0 < 0.30 or z1 - z0 < 0.40:
                continue

            area = float(hist[idx] / max(1, hist[chosen[0]]))
            eh = float(np.clip((t1 - t0) / room_t_extent, 0.0, 1.0))
            ev = float(np.clip((z1 - z0) / room_height, 0.0, 1.0))
            continuity = _continuity_score(tt, zz, t0, t1, z0, z1, max(raster, 0.04))
            # A room boundary candidate lies near either extreme of this orientation's offset range.
            edge_dist = min(abs(d - dmin), abs(dmax - d))
            boundary = float(np.clip(1.0 - edge_dist / max(0.5, 0.18 * (dmax - dmin)), 0.0, 1.0))
            confidence, occ, state = _classify(area, eh, ev, continuity, boundary, confidence_threshold,
                                               fuzzy=fuzzy, topology=topology, occlusion=occlusion)

            p1 = n * d + tvec * t0
            p2 = n * d + tvec * t1
            candidates.append(WallHypothesis(
                id=wall_id,
                theta_deg=theta_deg,
                d=d,
                x1=float(p1[0]), y1=float(p1[1]), x2=float(p2[0]), y2=float(p2[1]),
                z0=z0, z1=z1, thickness=0.20,
                area_support=float(np.clip(area, 0.0, 1.0)),
                horizontal_extent=eh, vertical_extent=ev,
                continuity=float(np.clip(continuity, 0.0, 1.0)),
                boundary_support=boundary, occlusion_support=occ,
                confidence=confidence, state=state,
            ))
            wall_id += 1

    # Confidence-ordered output makes the DB tree easier to inspect.
    candidates.sort(key=lambda w: w.confidence, reverse=True)
    for i, wall in enumerate(candidates, 1):
        wall.id = i

    summary = {
        "status": "success",
        "method": "Fuzzy-RESF",
        "version": "1.0",
        "input_points_used": int(len(pts)),
        "floor_z": z_floor,
        "ceiling_z": z_ceiling,
        "orientation_families_deg": orientations,
        "hypotheses": len(candidates),
        "permanent": sum(w.state == "Permanent" for w in candidates),
        "occluded_permanent": sum(w.state == "OccludedPermanent" for w in candidates),
        "clutter": sum(w.state == "Clutter" for w in candidates),
        "uncertain": sum(w.state == "Uncertain" for w in candidates),
    }
    return candidates, summary


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


def write_result_json(summary: dict, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
