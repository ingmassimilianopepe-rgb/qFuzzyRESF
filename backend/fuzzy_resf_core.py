"""qFuzzyRESF semantic Scan-to-BIM backend.

Fuzzy-RESF v1.1 keeps the multi-orientation plane sweep but adds the BIM stages
that were missing from the v0.1 reference backend: wall segment extraction,
wall-face consolidation, corner snapping, door/window opening detection and
floor/ceiling slab hypotheses.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import csv, json, math
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
    s0: float
    s1: float
    z0: float
    z1: float
    width: float
    height: float
    confidence: float


@dataclass
class SlabHypothesis:
    id: int
    kind: str
    z: float
    thickness: float
    footprint: list[list[float]]
    confidence: float


@dataclass
class ReconstructionModel:
    walls: list[WallHypothesis]
    modeled_walls: list[WallHypothesis]
    openings: list[OpeningHypothesis]
    slabs: list[SlabHypothesis]


def _load_xyz(path, max_points=1_500_000):
    pts = np.loadtxt(path, dtype=np.float64, usecols=(0, 1, 2))
    if pts.ndim == 1:
        pts = pts.reshape(1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) > max_points:
        rng = np.random.default_rng(1337)
        pts = pts[np.sort(rng.choice(len(pts), max_points, replace=False))]
    return pts


def _cdist(a, b):
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _frame(theta_deg):
    th = math.radians(theta_deg)
    return np.array([math.cos(th), math.sin(th)]), np.array([-math.sin(th), math.cos(th)])


def _orientation_score(xy, theta_deg, bin_width):
    n, _ = _frame(theta_deg)
    d = xy @ n
    lo, hi = float(d.min()), float(d.max())
    if hi <= lo:
        return 0.0
    hist, _ = np.histogram(d, bins=max(16, int(math.ceil((hi - lo) / bin_width))), range=(lo, hi))
    if hist.size == 0:
        return 0.0
    k = min(8, hist.size)
    return float(np.mean(np.partition(hist, -k)[-k:]) / max(1.0, np.mean(hist)))


def detect_orientations(xy, z, z_floor, z_ceiling, angle_step_deg, max_families):
    h = max(0.1, z_ceiling - z_floor)
    band = (z > z_floor + 0.12 * h) & (z < z_ceiling - 0.06 * h)
    work = xy[band] if band.sum() >= 100 else xy
    step = max(0.5, float(angle_step_deg))
    span = max(float(np.ptp(work[:, 0])), float(np.ptp(work[:, 1])), 1.0)
    bw = max(0.025, min(0.10, span / 700.0))
    scored = sorted(
        ((float(a), _orientation_score(work, float(a), bw)) for a in np.arange(0.0, 180.0, step)),
        key=lambda p: p[1],
        reverse=True,
    )
    if not scored:
        return []
    threshold = max(1.6, 0.30 * scored[0][1])
    sep = max(8.0, 3.0 * step)
    out = []
    for a, score in scored:
        if score < threshold:
            break
        if all(_cdist(a, b) >= sep for b in out):
            out.append(a)
        if len(out) >= max_families:
            break
    return sorted(out)


def _smooth(hist):
    k = np.array([1, 2, 3, 2, 1], dtype=float)
    return np.convolve(hist.astype(float), k / k.sum(), mode="same")


def _bridge(mask, max_gap):
    out = np.asarray(mask, bool).copy()
    i = 0
    while i < len(out):
        if out[i]:
            i += 1
            continue
        j = i
        while j < len(out) and not out[j]:
            j += 1
        if i and j < len(out) and j - i <= max_gap:
            out[i:j] = True
        i = j
    return out


def _runs(mask, min_bins):
    out, i = [], 0
    while i < len(mask):
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < len(mask) and mask[j]:
            j += 1
        if j - i >= min_bins:
            out.append((i, j))
        i = j
    return out


def _continuity(t, z, t0, t1, z0, z1, cell):
    if t1 <= t0 or z1 <= z0 or len(t) == 0:
        return 0.0
    cell = max(0.04, float(cell))
    nt = max(4, min(600, int(math.ceil((t1 - t0) / cell))))
    nz = max(4, min(300, int(math.ceil((z1 - z0) / cell))))
    th, _ = np.histogram(t, bins=nt, range=(t0, t1))
    zh, _ = np.histogram(z, bins=nz, range=(z0, z1))
    return float(np.clip(0.58 * np.count_nonzero(th) / nt + 0.42 * np.count_nonzero(zh) / nz, 0, 1))


def _classify(area, eh, ev, continuity, boundary, threshold, fuzzy, occlusion, length):
    raw = 0.28 * area + 0.16 * eh + 0.28 * ev + 0.23 * continuity + 0.05 * boundary
    conf = 1.0 / (1.0 + math.exp(-8.0 * (raw - 0.46))) if fuzzy else float(np.clip(raw, 0, 1))
    occ = float(np.clip((1.0 - continuity) * ev * max(area, 0.25), 0, 1)) if occlusion else 0.0
    if ev < 0.24 or length < 0.40:
        state = "Clutter"
    elif conf >= threshold and ev >= 0.38:
        state = "Permanent"
    elif occ >= 0.20 and ev >= 0.45 and length >= 0.60:
        state = "OccludedPermanent"
    elif conf >= max(0.30, threshold - 0.12) and ev >= 0.35:
        state = "Uncertain"
    else:
        state = "Clutter"
    return float(conf), occ, state


def _surface_segments(pts, theta, z_floor, z_ceiling, spacing, tol, raster, threshold, fuzzy, occlusion):
    xy, z = pts[:, :2], pts[:, 2]
    h = max(0.1, z_ceiling - z_floor)
    n, tv = _frame(theta)
    vertical = (z > z_floor + 0.12 * h) & (z < z_ceiling - 0.06 * h)
    work = xy[vertical] if vertical.sum() >= 100 else xy
    pn_work = work @ n
    dmin, dmax = map(float, np.quantile(pn_work, [0.002, 0.998]))
    if dmax <= dmin:
        return []

    bw = max(0.025, float(spacing), 0.75 * float(tol))
    nb = max(16, int(math.ceil((dmax - dmin) / bw)))
    hist, edges = np.histogram(pn_work, bins=nb, range=(dmin, dmax))
    if hist.max(initial=0) <= 0:
        return []
    sm = _smooth(hist)
    med, mad = float(np.median(sm)), float(np.median(np.abs(sm - np.median(sm)))) + 1e-9
    peak_threshold = max(20.0, 0.55 * float(np.percentile(sm, 90)), med + 4.0 * mad)
    chosen = []
    for idx in np.argsort(sm)[::-1]:
        if sm[idx] < peak_threshold:
            break
        c = float(0.5 * (edges[idx] + edges[idx + 1]))
        if all(abs(c - float(0.5 * (edges[j] + edges[j + 1]))) >= max(0.08, 2 * bw) for j in chosen):
            chosen.append(int(idx))
        if len(chosen) >= 32:
            break

    pn, pt = xy @ n, xy @ tv
    rt0, rt1 = map(float, np.quantile(pt, [0.002, 0.998]))
    room_extent = max(0.1, rt1 - rt0)
    top = max((float(sm[i]) for i in chosen), default=1.0)
    tcell = max(0.06, min(0.14, 2.0 * max(raster, 0.03)))
    out = []

    for idx in chosen:
        d = float(0.5 * (edges[idx] + edges[idx + 1]))
        mask = (np.abs(pn - d) <= max(tol, bw)) & vertical
        if mask.sum() < 40:
            continue
        tt = pt[mask]
        q0, q1 = map(float, np.quantile(tt, [0.005, 0.995]))
        if q1 <= q0:
            continue
        nt = max(4, int(math.ceil((q1 - q0) / tcell)))
        th, te = np.histogram(tt, bins=nt, range=(q0, q1))
        pos = th[th > 0]
        if pos.size == 0:
            continue
        occ = _bridge(th >= max(2.0, 0.25 * float(np.percentile(pos, 20))), max(1, int(math.ceil(0.30 / tcell))))

        for a, b in _runs(occ, max(3, int(math.ceil(0.40 / tcell)))):
            t0, t1 = float(te[a]), float(te[b])
            seg = mask & (pt >= t0) & (pt <= t1)
            if seg.sum() < 40:
                continue
            zz = z[seg]
            zz0, zz1 = map(float, np.quantile(zz, [0.01, 0.99]))
            sz0 = z_floor if zz0 <= z_floor + 0.22 * h else max(z_floor, zz0)
            sz1 = z_ceiling if zz1 >= z_ceiling - 0.16 * h else min(z_ceiling, zz1)
            length, height = t1 - t0, sz1 - sz0
            if length < 0.40 or height < 0.50:
                continue

            area = float(np.clip(sm[idx] / max(1.0, top), 0, 1))
            eh, ev = float(np.clip(length / room_extent, 0, 1)), float(np.clip(height / h, 0, 1))
            cont = _continuity(pt[seg], zz, t0, t1, sz0, sz1, raster)
            edge_dist = min(abs(d - dmin), abs(dmax - d))
            boundary = float(np.clip(1.0 - edge_dist / max(0.5, 0.18 * (dmax - dmin)), 0, 1))
            conf, occ_score, state = _classify(area, eh, ev, cont, boundary, threshold, fuzzy, occlusion, length)
            p1, p2 = n * d + tv * t0, n * d + tv * t1
            out.append(WallHypothesis(
                0, float(theta), d, float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]),
                float(sz0), float(sz1), 0.18, area, eh, ev, cont, boundary, occ_score, conf, state
            ))
    return out


def _interval(w):
    _, tv = _frame(w.theta_deg)
    a = w.x1 * tv[0] + w.y1 * tv[1]
    b = w.x2 * tv[0] + w.y2 * tv[1]
    return min(a, b), max(a, b)


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0)) / max(1e-6, min(a1 - a0, b1 - b0))


def _consolidate(surfaces):
    used = [False] * len(surfaces)
    result = []
    order = sorted(range(len(surfaces)), key=lambda i: surfaces[i].confidence, reverse=True)
    for i in order:
        if used[i]:
            continue
        base = surfaces[i]
        a0, a1 = _interval(base)
        group = [i]
        used[i] = True
        for j in order:
            if used[j] or _cdist(base.theta_deg, surfaces[j].theta_deg) > 3.5:
                continue
            b0, b1 = _interval(surfaces[j])
            dd, ov = abs(base.d - surfaces[j].d), _overlap(a0, a1, b0, b1)
            if (dd <= 0.07 and ov >= 0.55) or (0.08 <= dd <= 0.45 and ov >= 0.62):
                group.append(j)
                used[j] = True

        ms = [surfaces[k] for k in group]
        ds = np.array([m.d for m in ms])
        span = float(ds.max() - ds.min()) if len(ds) > 1 else 0.0
        d = float(0.5 * (ds.min() + ds.max())) if 0.08 <= span <= 0.45 else float(np.average(ds, weights=[max(.05, m.confidence) for m in ms]))
        thickness = float(np.clip(span, 0.08, 0.45)) if 0.08 <= span <= 0.45 else 0.18
        t0, t1 = min(_interval(m)[0] for m in ms), max(_interval(m)[1] for m in ms)
        n, tv = _frame(base.theta_deg)
        p1, p2 = n * d + tv * t0, n * d + tv * t1
        states = [m.state for m in ms]
        state = "Permanent" if "Permanent" in states else "OccludedPermanent" if "OccludedPermanent" in states else "Uncertain" if "Uncertain" in states else "Clutter"
        result.append(WallHypothesis(
            0, base.theta_deg, d, float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]),
            float(np.median([m.z0 for m in ms])), float(np.median([m.z1 for m in ms])), thickness,
            max(m.area_support for m in ms), max(m.horizontal_extent for m in ms), max(m.vertical_extent for m in ms),
            max(m.continuity for m in ms), max(m.boundary_support for m in ms), max(m.occlusion_support for m in ms),
            max(m.confidence for m in ms), state
        ))

    final = []
    for w in sorted(result, key=lambda x: x.confidence, reverse=True):
        a0, a1 = _interval(w)
        if any(_cdist(w.theta_deg, e.theta_deg) <= 3.5 and abs(w.d - e.d) <= 0.10 and _overlap(a0, a1, *_interval(e)) >= 0.70 for e in final):
            continue
        final.append(w)
    return final


def _intersection(a, b):
    p, r = np.array([a.x1, a.y1]), np.array([a.x2 - a.x1, a.y2 - a.y1])
    q, s = np.array([b.x1, b.y1]), np.array([b.x2 - b.x1, b.y2 - b.y1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-9:
        return None
    qp = q - p
    return p + ((qp[0] * s[1] - qp[1] * s[0]) / den) * r


def _snap(walls, distance=0.35):
    for i, w in enumerate(walls):
        points = [np.array([w.x1, w.y1]), np.array([w.x2, w.y2])]
        for end in (0, 1):
            best, best_d = None, distance
            for j, other in enumerate(walls):
                if i == j or _cdist(w.theta_deg, other.theta_deg) < 15:
                    continue
                p = _intersection(w, other)
                if p is None:
                    continue
                d = float(np.linalg.norm(p - points[end]))
                if d >= best_d:
                    continue
                a, b = np.array([other.x1, other.y1]), np.array([other.x2, other.y2])
                v = b - a
                if float(v @ v) <= 1e-12:
                    continue
                u = float((p - a) @ v / (v @ v))
                if np.linalg.norm(p - (a + np.clip(u, 0, 1) * v)) <= distance:
                    best, best_d = p, d
            if best is not None:
                if end == 0:
                    w.x1, w.y1 = map(float, best)
                else:
                    w.x2, w.y2 = map(float, best)
                points[end] = best


def _openings_for_wall(pts, wall, z_floor, z_ceiling, raster, tol, first_id):
    xy, z = pts[:, :2], pts[:, 2]
    n, tv = _frame(wall.theta_deg)
    pt, pn = xy @ tv, xy @ n
    p1t, p2t = wall.x1 * tv[0] + wall.y1 * tv[1], wall.x2 * tv[0] + wall.y2 * tv[1]
    t0, t1 = min(p1t, p2t), max(p1t, p2t)
    length = t1 - t0
    base_z, top_z = max(wall.z0, z_floor), min(wall.z1, z_ceiling)
    height = top_z - base_z
    if length < 0.70 or height < 1.0:
        return []

    mask = (np.abs(pn - wall.d) <= max(tol, wall.thickness / 2 + tol)) & (pt >= t0) & (pt <= t1) & (z >= base_z) & (z <= top_z)
    if mask.sum() < 80:
        return []
    cell = max(0.06, min(0.12, max(float(raster), 0.06)))
    ns, nz = max(4, int(math.ceil(length / cell))), max(8, int(math.ceil(height / cell)))
    counts = np.zeros((ns, nz), np.int32)
    si = np.clip(((pt[mask] - t0) / length * ns).astype(int), 0, ns - 1)
    zi = np.clip(((z[mask] - base_z) / height * nz).astype(int), 0, nz - 1)
    np.add.at(counts, (si, zi), 1)
    occupied, dz, ds = counts > 0, height / nz, length / ns

    columns = [None] * ns
    for i in range(ns):
        col = _bridge(occupied[i], max(1, int(math.ceil(0.16 / dz))))
        choices, j = [], 0
        while j < nz:
            if col[j]:
                j += 1
                continue
            k = j
            while k < nz and not col[k]:
                k += 1
            if k - j >= max(3, int(math.ceil(0.30 / dz))):
                bottom, top = j * dz, k * dz
                below = float(col[:j].mean()) if j else 0.0
                above = float(col[k:].mean()) if k < nz else 0.0
                if bottom <= 0.30 and 1.55 <= top <= min(2.60, height - 0.05) and above >= 0.22:
                    choices.append(("Door", bottom, top, above))
                elif bottom >= 0.25 and top <= height - 0.12 and 0.35 <= top - bottom <= 2.20 and below >= 0.18 and above >= 0.18:
                    choices.append(("Window", bottom, top, min(below, above)))
            j = k
        if choices:
            columns[i] = max(choices, key=lambda q: (q[2] - q[1]) * q[3])

    out, i, oid = [], 0, first_id
    while i < ns:
        if columns[i] is None:
            i += 1
            continue
        kind, vals, j, bridge = columns[i][0], [columns[i]], i + 1, 0
        while j < ns:
            q = columns[j]
            mb, mt = float(np.median([v[1] for v in vals])), float(np.median([v[2] for v in vals]))
            if q and q[0] == kind and abs(q[1] - mb) <= .28 and abs(q[2] - mt) <= .32:
                vals.append(q); bridge = 0; j += 1
            elif bridge < 1:
                bridge += 1; j += 1
            else:
                j -= bridge; break
        width = (j - i) * ds
        bottom, top = float(np.median([v[1] for v in vals])), float(np.median([v[2] for v in vals]))
        support = len(vals) / max(1, j - i)
        valid = (kind == "Door" and .55 <= width <= 2.20 and 1.55 <= top - bottom <= 2.60) or (kind == "Window" and .35 <= width <= 4.0 and .35 <= top - bottom <= 2.30)
        if valid and width <= .65 * length:
            out.append(OpeningHypothesis(oid, wall.id, kind, i * ds, j * ds, base_z + bottom, base_z + top, width, top - bottom, float(np.clip(.55 + .35 * support, 0, .95))))
            oid += 1
        i = max(j, i + 1)
    return out


def _hull(points):
    pts = np.unique(np.asarray(points, float), axis=0)
    if len(pts) < 3:
        return pts.tolist()
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]
    cross = lambda o, a, b: (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lo = []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0: lo.pop()
        lo.append(tuple(p))
    up = []
    for p in pts[::-1]:
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0: up.pop()
        up.append(tuple(p))
    return [[float(x), float(y)] for x, y in lo[:-1] + up[:-1]]


def _slabs(walls, pts, z_floor, z_ceiling):
    ends = [[w.x1, w.y1] for w in walls] + [[w.x2, w.y2] for w in walls]
    if len(ends) >= 3:
        fp = _hull(ends)
    else:
        xy = pts[:, :2]
        fp = _hull(xy[np.linspace(0, len(xy)-1, min(10000, len(xy)), dtype=int)])
    return [] if len(fp) < 3 else [
        SlabHypothesis(1, "Floor", z_floor, .15, fp, .80),
        SlabHypothesis(2, "Ceiling", z_ceiling, .12, fp, .70),
    ]


def reconstruct_model(request):
    pts = _load_xyz(request["input"], int(request.get("max_points", 1_500_000)))
    if len(pts) < 50:
        raise ValueError("Point cloud contains too few valid points for Fuzzy-RESF reconstruction")
    xy, z = pts[:, :2], pts[:, 2]
    z_floor, z_ceiling = map(float, np.quantile(z, [0.01, 0.99]))
    spacing = float(request.get("plane_spacing_m", .01))
    tol = float(request.get("plane_tolerance_m", .03))
    raster = float(request.get("raster_cell_m", .02))
    angle_step = float(request.get("angle_step_deg", 2.0))
    threshold = float(request.get("confidence_threshold", .45))
    orientations = detect_orientations(xy, z, z_floor, z_ceiling, angle_step, int(request.get("max_orientation_families", 12)))

    surfaces = []
    for a in orientations:
        surfaces += _surface_segments(pts, a, z_floor, z_ceiling, spacing, tol, raster, threshold, bool(request.get("fuzzy", True)), bool(request.get("occlusion", True)))
    walls = _consolidate(surfaces) if bool(request.get("instance_consolidation", True)) else surfaces
    walls.sort(key=lambda w: (w.state not in ("Permanent", "OccludedPermanent"), -w.confidence))
    for i, w in enumerate(walls, 1):
        w.id = i

    modeled = [w for w in walls if w.state in ("Permanent", "OccludedPermanent")]
    if len(modeled) < 3:
        for w in [x for x in walls if x.vertical_extent >= .35 and x.confidence >= max(.28, threshold-.15)]:
            if w not in modeled:
                modeled.append(w)
            if len(modeled) >= min(8, len(walls)):
                break
    if bool(request.get("topology", True)):
        _snap(modeled)

    openings, next_id = [], 1
    for w in modeled:
        ops = _openings_for_wall(pts, w, z_floor, z_ceiling, raster, tol, next_id)
        openings += ops
        next_id += len(ops)
    slabs = _slabs(modeled, pts, z_floor, z_ceiling)

    summary = {
        "status": "success", "method": "Fuzzy-RESF", "version": "1.1-semantic-bim",
        "input_points_used": int(len(pts)), "floor_z": z_floor, "ceiling_z": z_ceiling,
        "room_height": max(.1, z_ceiling-z_floor), "orientation_families_deg": orientations,
        "hypotheses": len(walls), "modeled_walls": len(modeled),
        "permanent": sum(w.state == "Permanent" for w in walls),
        "occluded_permanent": sum(w.state == "OccludedPermanent" for w in walls),
        "clutter": sum(w.state == "Clutter" for w in walls),
        "uncertain": sum(w.state == "Uncertain" for w in walls),
        "doors": sum(o.kind == "Door" for o in openings),
        "windows": sum(o.kind == "Window" for o in openings), "slabs": len(slabs),
    }
    return ReconstructionModel(walls, modeled, openings, slabs), summary


def reconstruct(request):
    model, summary = reconstruct_model(request)
    return model.walls, summary


def write_walls_csv(walls: Iterable[WallHypothesis], path):
    walls = list(walls)
    fields = list(asdict(walls[0]).keys()) if walls else [
        "id","theta_deg","d","x1","y1","x2","y2","z0","z1","thickness","area_support",
        "horizontal_extent","vertical_extent","continuity","boundary_support","occlusion_support","confidence","state"
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=fields); wr.writeheader()
        for x in walls: wr.writerow(asdict(x))


def write_openings_csv(openings: Iterable[OpeningHypothesis], path):
    fields = ["id","wall_id","kind","s0","s1","z0","z1","width","height","confidence"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=fields); wr.writeheader()
        for x in openings: wr.writerow(asdict(x))


def write_slabs_csv(slabs: Iterable[SlabHypothesis], path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        fields = ["id","kind","z","thickness","footprint_json","confidence"]
        wr = csv.DictWriter(f, fieldnames=fields); wr.writeheader()
        for s in slabs:
            wr.writerow({"id":s.id,"kind":s.kind,"z":s.z,"thickness":s.thickness,"footprint_json":json.dumps(s.footprint,separators=(",",":")),"confidence":s.confidence})


def write_result_json(summary, path):
    Path(path).write_text(json.dumps(summary, indent=2), encoding="utf-8")
