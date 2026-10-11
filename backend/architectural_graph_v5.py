"""Fuzzy-RESF-BIM v5: persistent architectural graph reconstruction.

Why this module exists
----------------------
The RESF field is a *surface-hypothesis* generator.  A translated candidate plane is not a
BIM wall.  Earlier portable backends let too many accepted planes survive downstream and
therefore produced the comb-like stacks of slabs visible on real scans.  This module keeps
RESF/fuzzy evidence for confidence and provenance, but reconstructs physical wall geometry
from height-persistent planimetric support before IFC generation.

The object stage is deliberately global:
  1. decompose the scan into storeys;
  2. build a vertical-persistence footprint from occupied (x,y,z-slice) cells;
  3. discover room-scale orientation families from that footprint;
  4. extract persistent wall-face tracks, not every translated RESF peak;
  5. pair compatible opposite faces into one physical wall centreline;
  6. merge collinear fragments across opening/occlusion-sized gaps;
  7. suppress parallel duplicates and snap architectural junctions;
  8. retain the room-cycle network plus strong connected partitions;
  9. map the original RESF/fuzzy evidence back onto each physical wall;
 10. reconstruct spaces/slabs/openings and run Cloud<->BIM feedback.

This is an extension of the manuscript object-consolidation stage, not a claim that the
frozen v1 code already implemented every step below.  No reference BIM is read.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import polygonize, unary_union

import bim_reconstruction as base
import paper_aligned_bim_v4 as v4
from topology_completion import detect_openings, geometric_feedback


@dataclass
class StructuralCells:
    xy: np.ndarray
    score: np.ndarray
    persistence: np.ndarray
    bottom: np.ndarray
    top: np.ndarray
    origin: np.ndarray
    cell: float


@dataclass
class FaceTrack:
    theta_deg: float
    d: float
    t0: float
    t1: float
    score: float
    persistence: float
    bottom_contact: float
    top_contact: float
    continuity: float

    @property
    def length(self) -> float:
        return max(0.0, self.t1 - self.t0)


def _smooth(v: np.ndarray, radius: int = 1) -> np.ndarray:
    if radius <= 0 or v.size == 0:
        return v.astype(float, copy=True)
    k = np.ones(2 * radius + 1, dtype=float)
    k /= k.sum()
    return np.convolve(v.astype(float), k, mode="same")


def _close_1d(mask: np.ndarray, max_gap: int) -> np.ndarray:
    out = mask.copy()
    i = 0
    while i < len(out):
        if out[i]:
            i += 1
            continue
        j = i
        while j < len(out) and not out[j]:
            j += 1
        if i > 0 and j < len(out) and j - i <= max_gap:
            out[i:j] = True
        i = j
    return out


def _segments(mask: np.ndarray) -> list[tuple[int, int]]:
    out = []
    i = 0
    while i < len(mask):
        if not mask[i]:
            i += 1
            continue
        j = i + 1
        while j < len(mask) and mask[j]:
            j += 1
        out.append((i, j))
        i = j
    return out


def _interval_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    return inter / max(1e-9, min(a1 - a0, b1 - b0))


def _interval_gap(a0: float, a1: float, b0: float, b1: float) -> float:
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return 0.0


def build_structural_cells(pts: np.ndarray, storey: base.Storey, cell: float) -> StructuralCells:
    """Rasterize vertical persistence instead of raw point density.

    A true wall tends to occupy the same XY footprint through many height slices and usually
    reaches the upper storey band.  A desk/cabinet may be planar and dense but normally
    disappears in the upper slices.  This representation therefore removes the principal
    cause of repeated furniture-driven RESF sheets before line fitting.
    """
    cell = float(np.clip(cell, 0.045, 0.10))
    h = max(float(storey.z1 - storey.z0), 1.0)
    zcell = float(np.clip(h / 24.0, 0.09, 0.16))
    nz = max(10, int(math.ceil(h / zcell)))

    p = pts[(pts[:, 2] >= storey.z0 + 0.03) & (pts[:, 2] <= storey.z1 - 0.03)]
    if len(p) < 100:
        return StructuralCells(np.empty((0, 2)), np.empty(0), np.empty(0),
                               np.empty(0), np.empty(0), np.zeros(2), cell)

    origin = p[:, :2].min(axis=0)
    ix = np.floor((p[:, 0] - origin[0]) / cell).astype(np.int64)
    iy = np.floor((p[:, 1] - origin[1]) / cell).astype(np.int64)
    iz = np.clip(np.floor((p[:, 2] - storey.z0) / h * nz).astype(np.int64), 0, nz - 1)

    triples = np.unique(np.column_stack((ix, iy, iz)), axis=0)
    if len(triples) == 0:
        return StructuralCells(np.empty((0, 2)), np.empty(0), np.empty(0),
                               np.empty(0), np.empty(0), origin, cell)

    max_y = int(triples[:, 1].max()) + 1
    xy_key = triples[:, 0] * max_y + triples[:, 1]
    keys, inv = np.unique(xy_key, return_inverse=True)
    counts = np.bincount(inv, minlength=len(keys)).astype(float)
    persistence = counts / float(nz)

    bottom = np.zeros(len(keys), dtype=float)
    top = np.zeros(len(keys), dtype=float)
    middle = np.zeros(len(keys), dtype=float)
    bottom_lim = max(1, int(math.ceil(0.16 * nz)))
    top_lim = min(nz - 1, int(math.floor(0.82 * nz)))
    mid0, mid1 = int(0.35 * nz), int(0.70 * nz)
    np.maximum.at(bottom, inv, (triples[:, 2] <= bottom_lim).astype(float))
    np.maximum.at(top, inv, (triples[:, 2] >= top_lim).astype(float))
    np.maximum.at(middle, inv, ((triples[:, 2] >= mid0) & (triples[:, 2] <= mid1)).astype(float))

    xs = keys // max_y
    ys = keys % max_y
    xy = origin + np.column_stack((xs + 0.5, ys + 0.5)) * cell

    # Ceiling contact has intentionally high weight: tall furniture that reaches only
    # 50-65% of room height must not become a BIM wall merely because it is planar.
    score = np.clip(0.45 * persistence + 0.16 * bottom + 0.29 * top + 0.10 * middle, 0.0, 1.0)
    structural = ((top > 0) & (persistence >= 0.20)) | (persistence >= 0.72)
    structural &= score >= 0.40

    return StructuralCells(xy[structural], score[structural], persistence[structural],
                           bottom[structural], top[structural], origin, cell)


def _orientation_score(cells: StructuralCells, theta: float) -> float:
    if len(cells.xy) < 20:
        return 0.0
    th = math.radians(theta)
    n = np.asarray([math.cos(th), math.sin(th)])
    t = np.asarray([-math.sin(th), math.cos(th)])
    d = cells.xy @ n
    u = cells.xy @ t
    db = max(cells.cell, 0.05)
    ub = max(cells.cell, 0.05)
    di = np.floor((d - d.min()) / db).astype(np.int64)
    ui = np.floor((u - u.min()) / ub).astype(np.int64)

    # One occupied tangent cell contributes once regardless of local scan density.
    umax = int(ui.max()) + 1
    pair = di.astype(np.int64) * umax + ui
    order = np.argsort(pair, kind="mergesort")
    ps = pair[order]
    ws = cells.score[order]
    first = np.r_[True, ps[1:] != ps[:-1]]
    uniq_pair = ps[first]
    uniq_w = np.maximum.reduceat(ws, np.flatnonzero(first))
    duniq = uniq_pair // umax
    prof = np.bincount(duniq, weights=uniq_w, minlength=int(di.max()) + 1)
    prof = _smooth(prof, 1)
    if prof.size == 0 or prof.max(initial=0.0) <= 0:
        return 0.0
    top = np.sort(prof)[-min(8, len(prof)):]
    # Room-scale score: several long offsets matter more than one very dense local plane.
    return float(np.sum(top) + 0.35 * np.mean(top))


def select_room_orientations(cells: StructuralCells, angle_step: float, max_families: int) -> tuple[list[float], str]:
    if len(cells.xy) < 20:
        return [], "insufficient_vertical_persistence"
    step = max(1.0, float(angle_step))
    scored = [(float(a), _orientation_score(cells, float(a))) for a in np.arange(0.0, 180.0, step)]
    scored.sort(key=lambda x: x[1], reverse=True)
    if not scored or scored[0][1] <= 0:
        return [], "no_orientation_support"

    selected: list[tuple[float, float]] = []
    sep = max(7.0, 3.0 * step)
    for a, s in scored:
        if s < 0.10 * scored[0][1]:
            break
        if all(base._circular_distance(a, b) >= sep for b, _ in selected):
            selected.append((a, s))
        if len(selected) >= max(2, min(int(max_families), 8)):
            break

    if len(selected) >= 2:
        total = sum(s for _, s in selected)
        for i, (a0, s0) in enumerate(selected[:4]):
            for a1, s1 in selected[i + 1:5]:
                ad = base._circular_distance(a0, a1)
                if 78.0 <= ad <= 102.0 and (s0 + s1) >= 0.68 * total and min(s0, s1) >= 0.25 * max(s0, s1):
                    return sorted([a0, a1]), "dominant_orthogonal_room_graph"

    top = selected[0][1]
    keep = [a for a, s in selected if s >= 0.18 * top]
    return sorted(keep[:max(2, min(int(max_families), 6))]), "multi_orientation_room_graph"


def extract_face_tracks(cells: StructuralCells, theta: float) -> list[FaceTrack]:
    if len(cells.xy) < 20:
        return []
    th = math.radians(theta)
    n = np.asarray([math.cos(th), math.sin(th)])
    tv = np.asarray([-math.sin(th), math.cos(th)])
    d = cells.xy @ n
    u = cells.xy @ tv
    db = max(0.05, cells.cell)
    ub = max(0.05, cells.cell)
    d0 = float(d.min())
    di = np.floor((d - d0) / db).astype(np.int64)
    profile = np.bincount(di, weights=cells.score, minlength=int(di.max()) + 1).astype(float)
    profile = _smooth(profile, 1)
    positive = profile[profile > 0]
    if positive.size == 0:
        return []
    threshold = max(0.16 * float(profile.max()), float(np.quantile(positive, 0.60)))
    peaks = [i for i in range(1, len(profile) - 1)
             if profile[i] >= threshold and profile[i] >= profile[i - 1] and profile[i] >= profile[i + 1]]
    if not peaks:
        peaks = [int(np.argmax(profile))]

    # NMS only within a single face ridge.  Opposite wall faces 10-45 cm apart remain
    # available for the physical-wall pairing stage.
    chosen: list[int] = []
    for i in sorted(peaks, key=lambda k: profile[k], reverse=True):
        if all(abs(i - j) >= 2 for j in chosen):
            chosen.append(i)
    chosen.sort()

    out: list[FaceTrack] = []
    for pi in chosen:
        dc = d0 + (pi + 0.5) * db
        near = np.abs(d - dc) <= max(0.075, 1.55 * db)
        if near.sum() < 8:
            continue
        uu = u[near]
        ww = cells.score[near]
        pp = cells.persistence[near]
        bb = cells.bottom[near]
        tt = cells.top[near]
        u0 = float(uu.min())
        ui = np.floor((uu - u0) / ub).astype(np.int64)
        occ = np.bincount(ui, minlength=int(ui.max()) + 1) > 0
        # Close only sampling cracks here; door/window-sized gaps are handled later when
        # fragments already share the same physical wall explanation.
        occ = _close_1d(occ, max(1, int(round(0.25 / ub))))
        for a, b in _segments(occ):
            seg0 = u0 + a * ub
            seg1 = u0 + b * ub
            if seg1 - seg0 < 0.60:
                continue
            m = near & (u >= seg0 - 0.5 * ub) & (u <= seg1 + 0.5 * ub)
            if m.sum() < 6:
                continue
            length = seg1 - seg0
            continuity = float(np.clip((b - a) / max(1, int(math.ceil(length / ub))), 0.0, 1.0))
            score = float(np.clip(np.mean(cells.score[m]) * (0.75 + 0.25 * min(1.0, length / 2.0)), 0.0, 1.0))
            out.append(FaceTrack(theta, dc, seg0, seg1, score,
                                 float(np.mean(cells.persistence[m])), float(np.mean(cells.bottom[m])),
                                 float(np.mean(cells.top[m])), continuity))
    return out


def suppress_face_duplicates(tracks: list[FaceTrack]) -> list[FaceTrack]:
    kept: list[FaceTrack] = []
    for t in sorted(tracks, key=lambda q: (q.score * q.length, q.top_contact), reverse=True):
        duplicate = False
        for k in kept:
            if base._circular_distance(t.theta_deg, k.theta_deg) > 2.5:
                continue
            if abs(t.d - k.d) > 0.075:
                continue
            if _interval_overlap(t.t0, t.t1, k.t0, k.t1) < 0.55:
                continue
            duplicate = True
            break
        if not duplicate:
            kept.append(t)
    return kept


def _resf_for_wall(cands: list[base.RESFCandidate], theta: float, d: float,
                   t0: float, t1: float) -> dict:
    selected = []
    weights = []
    for c in cands:
        if c.state not in {"Permanent", "OccludedPermanent", "Uncertain"}:
            continue
        if base._circular_distance(c.theta_deg, theta) > 5.0 or abs(c.d - d) > 0.38:
            continue
        ov = _interval_overlap(t0, t1, c.t0, c.t1)
        if ov < 0.18:
            continue
        w = max(0.05, c.confidence) * max(0.10, ov) * max(0.30, c.t1 - c.t0)
        selected.append(c)
        weights.append(w)
    if not selected:
        return {"confidence": 0.0, "A": 0.0, "EH": 0.0, "EV": 0.0, "C": 0.0,
                "N": 0.0, "O": 0.0, "G": 1.0, "state": "Uncertain"}
    w = np.asarray(weights, dtype=float)
    def avg(name: str) -> float:
        return float(np.average([getattr(c, name) for c in selected], weights=w))
    rank = {"Clutter": 0, "Uncertain": 1, "OccludedPermanent": 2, "Permanent": 3}
    state = max((c.state for c in selected), key=lambda s: rank.get(s, 0))
    return {"confidence": float(max(c.confidence for c in selected)), "A": avg("A_star"),
            "EH": avg("E_H"), "EV": avg("E_V"), "C": max(c.C for c in selected),
            "N": avg("N"), "O": max(c.O for c in selected), "G": min(c.G for c in selected),
            "state": state}


def pair_tracks_to_walls(tracks: list[FaceTrack], storey: base.Storey,
                         cands: list[base.RESFCandidate]) -> list[base.Wall]:
    if not tracks:
        return []
    tracks = suppress_face_duplicates(tracks)
    sep_samples = []
    for i, a in enumerate(tracks):
        for b in tracks[i + 1:]:
            if base._circular_distance(a.theta_deg, b.theta_deg) > 2.5:
                continue
            sep = abs(a.d - b.d)
            overlap = _interval_overlap(a.t0, a.t1, b.t0, b.t1)
            ratio = min(a.length, b.length) / max(a.length, b.length, 1e-9)
            if 0.08 <= sep <= 0.45 and overlap >= 0.65 and ratio >= 0.60:
                sep_samples.append(sep)
    typical = float(np.clip(np.median(sep_samples), 0.11, 0.32)) if sep_samples else 0.18

    order = sorted(range(len(tracks)), key=lambda i: tracks[i].score * tracks[i].length, reverse=True)
    used: set[int] = set()
    walls: list[base.Wall] = []
    for i in order:
        if i in used:
            continue
        a = tracks[i]
        best = None
        best_cost = float("inf")
        for j, b in enumerate(tracks):
            if j == i or j in used or base._circular_distance(a.theta_deg, b.theta_deg) > 2.5:
                continue
            sep = abs(a.d - b.d)
            ov = _interval_overlap(a.t0, a.t1, b.t0, b.t1)
            ratio = min(a.length, b.length) / max(a.length, b.length, 1e-9)
            if not (0.08 <= sep <= 0.45 and ov >= 0.62 and ratio >= 0.58):
                continue
            cost = abs(sep - typical) / max(typical, 0.10) + 0.9 * (1.0 - ov) + 0.65 * (1.0 - ratio)
            if cost < best_cost:
                best, best_cost = j, cost

        if best is not None and best_cost <= 1.55:
            b = tracks[best]
            d = 0.5 * (a.d + b.d)
            t0, t1 = min(a.t0, b.t0), max(a.t1, b.t1)
            thickness = abs(a.d - b.d)
            structural = float(np.clip(0.5 * (a.score + b.score) + 0.08 * _interval_overlap(a.t0, a.t1, b.t0, b.t1), 0, 1))
            persistence = 0.5 * (a.persistence + b.persistence)
            top_contact = max(a.top_contact, b.top_contact)
            source = "paired_height_persistent_faces"
            used.add(i); used.add(best)
        else:
            d, t0, t1 = a.d, a.t0, a.t1
            thickness = typical
            structural = a.score
            persistence = a.persistence
            top_contact = a.top_contact
            source = "single_height_persistent_face"
            used.add(i)

        evidence = _resf_for_wall(cands, a.theta_deg, d, t0, t1)
        th = math.radians(a.theta_deg)
        n = np.asarray([math.cos(th), math.sin(th)])
        tv = np.asarray([-math.sin(th), math.cos(th)])
        p0 = n * d + tv * t0
        p1 = n * d + tv * t1
        conf = float(np.clip(0.52 * structural + 0.30 * evidence["confidence"] + 0.10 * persistence + 0.08 * top_contact, 0.0, 1.0))
        state = evidence["state"] if evidence["state"] in {"Permanent", "OccludedPermanent"} else ("Permanent" if top_contact >= 0.45 else "Uncertain")
        walls.append(base.Wall(
            0, storey.id, float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1]),
            float(storey.z0), float(storey.z1), float(np.clip(thickness, 0.09, 0.45)), float(a.theta_deg),
            conf, structural, max(a.continuity, 0.45), source, state,
            evidence["A"], evidence["EH"], max(evidence["EV"], persistence), evidence["C"],
            evidence["N"], evidence["O"], evidence["G"]
        ))
    return walls


def merge_collinear(walls: list[base.Wall], max_gap: float = 2.35) -> list[base.Wall]:
    if not walls:
        return []
    groups: list[list[base.Wall]] = []
    for w in sorted(walls, key=lambda q: (q.storey_id, q.theta_deg, -q.confidence)):
        n, tv, dw, a0, a1 = v4._wall_axis(w)
        best = None
        best_cost = float("inf")
        for gi, group in enumerate(groups):
            ref = max(group, key=v4._wall_length)
            if w.storey_id != ref.storey_id or base._circular_distance(w.theta_deg, ref.theta_deg) > 3.0:
                continue
            _, _, dr, b0, b1 = v4._wall_axis(ref, w.theta_deg)
            dd = abs(dw - dr)
            gap = _interval_gap(a0, a1, b0, b1)
            if dd > max(0.13, 0.55 * max(w.thickness, ref.thickness)) or gap > max_gap:
                continue
            if gap > 0.55 and min(w.confidence, ref.confidence) < 0.46:
                continue
            cost = 5.0 * dd + gap
            if cost < best_cost:
                best, best_cost = gi, cost
        if best is None:
            groups.append([w])
        else:
            groups[best].append(w)
    out = [v4._merge_wall_group(g) for g in groups]
    for i, w in enumerate(out, 1):
        w.id = i
    return out


def deduplicate_physical_walls(walls: list[base.Wall]) -> list[base.Wall]:
    kept: list[base.Wall] = []
    for w in sorted(walls, key=lambda q: (q.confidence * v4._wall_length(q), q.support), reverse=True):
        _, _, dw, a0, a1 = v4._wall_axis(w)
        duplicate = False
        for k in kept:
            if w.storey_id != k.storey_id or base._circular_distance(w.theta_deg, k.theta_deg) > 3.0:
                continue
            _, _, dk, b0, b1 = v4._wall_axis(k, w.theta_deg)
            if _interval_overlap(a0, a1, b0, b1) >= 0.52 and abs(dw - dk) <= max(0.18, 0.70 * max(w.thickness, k.thickness)):
                duplicate = True
                break
        if not duplicate:
            kept.append(w)
    for i, w in enumerate(kept, 1):
        w.id = i
    return kept


def _endpoint_degree(walls: list[base.Wall], idx: int, radius: float = 0.34) -> tuple[int, int]:
    return v4._endpoint_degree(walls, idx, radius)


def prune_to_architectural_graph(walls: list[base.Wall]) -> list[base.Wall]:
    if not walls:
        return []
    # First remove obvious local remnants.  A full-height isolated segment may still be a
    # valid wall in an incomplete scan, so long/high-confidence elements are protected.
    preliminary = []
    for i, w in enumerate(walls):
        L = v4._wall_length(w)
        d0, d1 = _endpoint_degree(walls, i, 0.36)
        connected = d0 > 0 or d1 > 0
        if L < 0.65:
            continue
        if not connected and L < 1.25 and w.confidence < 0.72:
            continue
        if not connected and w.resf_EV < 0.62 and w.support < 0.62 and L < 2.0:
            continue
        preliminary.append(w)
    if len(preliminary) < 4:
        return preliminary

    lines = [LineString([(w.x1, w.y1), (w.x2, w.y2)]) for w in preliminary]
    try:
        polys = [p for p in polygonize(unary_union(lines)) if p.area >= 0.8]
    except Exception:
        polys = []
    if not polys:
        return preliminary

    # Room cycles are strong global evidence.  Connected partitions may be kept even when
    # they do not bound the exterior polygon, but they must join the graph at both ends.
    boundary = unary_union([p.boundary for p in polys])
    kept = []
    for i, (w, line) in enumerate(zip(preliminary, lines)):
        coverage = line.intersection(boundary.buffer(0.09)).length / max(line.length, 1e-9)
        d0, d1 = _endpoint_degree(preliminary, i, 0.36)
        partition = d0 > 0 and d1 > 0 and line.length >= 1.0 and w.confidence >= 0.45
        if coverage >= 0.48 or partition:
            kept.append(w)
    return kept if len(kept) >= 4 else preliminary


def _network_duplicate_count(walls: list[base.Wall]) -> int:
    n = 0
    for i, w in enumerate(walls):
        _, _, dw, a0, a1 = v4._wall_axis(w)
        for k in walls[i + 1:]:
            if w.storey_id != k.storey_id or base._circular_distance(w.theta_deg, k.theta_deg) > 3.0:
                continue
            _, _, dk, b0, b1 = v4._wall_axis(k, w.theta_deg)
            if abs(dw - dk) <= max(0.18, 0.70 * max(w.thickness, k.thickness)) and _interval_overlap(a0, a1, b0, b1) >= 0.52:
                n += 1
    return n


def _junction_ratio(walls: list[base.Wall]) -> float:
    if not walls:
        return 0.0
    connected = 0
    for i in range(len(walls)):
        a, b = _endpoint_degree(walls, i, 0.36)
        connected += int(a > 0) + int(b > 0)
    return float(connected / max(1, 2 * len(walls)))


def reconstruct_bim(request: dict) -> base.BIMModel:
    raw = base.load_xyz(request["input"], int(request.get("max_points", 2_000_000)))
    if len(raw) < 100:
        raise ValueError("Point cloud contains too few valid points for BIM reconstruction")

    raster = float(np.clip(float(request.get("raster_cell_m", 0.05)), 0.04, 0.10))
    pts = base.voxel_subsample(raw, max(0.025, min(0.05, raster)))
    storeys = base.detect_storeys(pts)
    threshold = float(request.get("confidence_threshold", 0.50))
    multi = bool(request.get("multi_peak", True))
    topo = bool(request.get("topology", True))
    occ = bool(request.get("occlusion", True))
    fuzzy = bool(request.get("fuzzy", True))
    feedback = bool(request.get("geometric_feedback", True))
    plane_spacing = max(0.008, float(request.get("plane_spacing_m", 0.01)))
    plane_tol = max(0.02, float(request.get("plane_tolerance_m", 0.03)))
    max_orient = int(request.get("max_orientation_families", 12))
    angle_step = float(request.get("angle_step_deg", 2.0))

    all_walls: list[base.Wall] = []
    all_spaces: list[base.Space] = []
    all_slabs: list[base.Slab] = []
    all_candidates: list[base.RESFCandidate] = []
    diagnostics = []

    for storey in storeys:
        band = pts[(pts[:, 2] >= storey.z0 - 0.10) & (pts[:, 2] <= storey.z1 + 0.10)]
        if len(band) < 100:
            continue

        cells = build_structural_cells(band, storey, raster)
        orientations, orientation_mode = select_room_orientations(cells, angle_step, max_orient)
        if not orientations:
            orientations = base.detect_orientations(band, angle_step, max_orient)
            orientation_mode = "fallback_resf_orientation_search"

        # Keep the manuscript RESF/fuzzy path for evidence, confidence and diagnostics.
        cands = base.build_resf_candidates(band, storey, orientations, plane_spacing, plane_tol, raster, multi)
        base.enrich_topology(cands)
        for c in cands:
            c.state, c.confidence = base.fuzzy_decision(c, threshold, fuzzy, topo, occ)
        if feedback:
            base._apply_candidate_feedback(band, cands)
            for c in cands:
                c.state, c.confidence = base.fuzzy_decision(c, threshold, fuzzy, topo, occ)

        tracks = []
        for theta in orientations:
            tracks.extend(extract_face_tracks(cells, theta))
        tracks = suppress_face_duplicates(tracks)
        walls = pair_tracks_to_walls(tracks, storey, cands)
        walls = merge_collinear(walls, 2.40 if topo else 0.40)
        walls = deduplicate_physical_walls(walls)
        walls = v4.snap_junctions(walls, 0.65 if topo else 0.22)
        walls = prune_to_architectural_graph(walls)

        # If the persistent footprint remains open, the manuscript allows inferred envelope
        # geometry to be kept distinct from observed geometry.  Use the conservative joint
        # floor+ceiling completion already implemented in v4, then deduplicate again.
        provisional = []
        if len(walls) >= 3:
            try:
                provisional = list(polygonize(unary_union([LineString([(w.x1, w.y1), (w.x2, w.y2)]) for w in walls])))
            except Exception:
                provisional = []
        if topo and not provisional:
            walls = v4.envelope_rescue(band, walls, storey)
            walls = merge_collinear(walls, 1.30)
            walls = deduplicate_physical_walls(walls)
            walls = v4.snap_junctions(walls, 0.65)
            walls = prune_to_architectural_graph(walls)

        if feedback and walls:
            geometric_feedback(band, walls)
            walls = [w for w in walls if w.confidence >= 0.34 or "envelope_inference" in w.source]
            walls = deduplicate_physical_walls(walls)

        base_id = len(all_walls)
        for i, w in enumerate(walls, 1):
            w.id = base_id + i
        spaces = base.spaces_from_walls(walls, storey) if topo else []
        slabs = base.slabs_from_evidence(band, spaces, storey)
        all_walls.extend(walls)
        all_spaces.extend(spaces)
        all_slabs.extend(slabs)
        all_candidates.extend(cands)
        diagnostics.append({
            "storey": storey.id,
            "structural_cells": int(len(cells.xy)),
            "orientations": [round(float(x), 2) for x in orientations],
            "orientation_mode": orientation_mode,
            "face_tracks": len(tracks),
            "walls": len(walls),
            "spaces": len(spaces),
        })

    for i, x in enumerate(all_spaces, 1):
        x.id = i
    for i, x in enumerate(all_slabs, 1):
        x.id = i

    openings = detect_openings(pts, all_walls, raster)
    for i, o in enumerate(openings, 1):
        o.id = i

    duplicates = _network_duplicate_count(all_walls)
    junction_ratio = _junction_ratio(all_walls)
    warnings = []
    if duplicates:
        warnings.append(f"{duplicates} near-duplicate physical wall pairs remain")
    if junction_ratio < 0.45 and len(all_walls) >= 4:
        warnings.append(f"Wall graph endpoint connectivity is low ({junction_ratio:.2f})")
    if not all_spaces:
        warnings.append("No closed IfcSpace polygons reconstructed")
    if not openings:
        warnings.append("No host-wall door/window objects passed the opening checks")

    summary = {
        "status": "success" if all_walls else "warning",
        "method": "Fuzzy-RESF-BIM",
        "version": "5.0-persistent-architectural-graph",
        "input_points": int(len(raw)),
        "regularized_points": int(len(pts)),
        "storeys": len(storeys),
        "resf_candidates": len(all_candidates),
        "accepted_candidates": sum(c.state in {"Permanent", "OccludedPermanent"} for c in all_candidates),
        "walls": len(all_walls),
        "doors": sum(o.kind == "Door" for o in openings),
        "windows": sum(o.kind == "Window" for o in openings),
        "openings": len(openings),
        "spaces": len(all_spaces),
        "slabs": len(all_slabs),
        "wall_duplicate_pairs": duplicates,
        "wall_junction_ratio": junction_ratio,
        "wall_reconstruction": "height_persistent_footprint_to_face_tracks_to_physical_wall_graph",
        "resf_role": "evidence_and_confidence_not_direct_wall_geometry",
        "observed_inferred_separation": True,
        "occlusion_evidence": "geometry_topology_proxy_without_scanner_poses",
        "semantic_evidence": "geometry_prior_without_independent_pointwise_semantic_backbone",
        "exact_frozen_v1_reproduction": False,
        "storey_diagnostics": diagnostics,
        "warnings": warnings,
    }
    return base.BIMModel(storeys, all_walls, openings, all_spaces, all_slabs, all_candidates, summary)
