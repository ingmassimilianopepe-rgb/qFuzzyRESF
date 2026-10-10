"""Object-level Fuzzy-RESF BIM reconstruction (research v2).

This module deliberately *unfreezes* the v1 paper implementation.  The old backend
returned surface hypotheses and then extruded them.  This implementation reconstructs
BIM objects explicitly:

1. density regularisation and storey detection;
2. multi-orientation/multi-peak wall-face evidence;
3. pairing of opposite faces into physical wall centre-lines and thicknesses;
4. topological snapping and room polygonisation;
5. wall-local opening detection and door/window classification;
6. floor/ceiling slab and IfcSpace footprints;
7. confidence/provenance attached to every object.

The design is intentionally geometry-first and conservative: an absence of points is not
automatically called a door/window.  Openings are accepted only when they have a plausible
size, surrounding wall support and a valid host-wall relationship.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from collections import deque
import csv
import json
import math
from typing import Iterable

import numpy as np
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.ops import polygonize, unary_union


@dataclass
class Storey:
    id: int
    z0: float
    z1: float
    confidence: float


@dataclass
class Wall:
    id: int
    storey_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    z0: float
    z1: float
    thickness: float
    theta_deg: float
    confidence: float
    support: float
    continuity: float
    source: str = "observed"


@dataclass
class Opening:
    id: int
    wall_id: int
    storey_id: int
    kind: str
    offset: float
    width: float
    sill: float
    height: float
    confidence: float


@dataclass
class Space:
    id: int
    storey_id: int
    name: str
    polygon: list[list[float]]
    z0: float
    z1: float
    area: float
    confidence: float


@dataclass
class Slab:
    id: int
    storey_id: int
    kind: str
    polygon: list[list[float]]
    z: float
    thickness: float
    confidence: float


@dataclass
class BIMModel:
    storeys: list[Storey]
    walls: list[Wall]
    openings: list[Opening]
    spaces: list[Space]
    slabs: list[Slab]
    summary: dict


def load_xyz(path: str | Path, max_points: int = 2_000_000) -> np.ndarray:
    pts = np.loadtxt(path, dtype=np.float64, usecols=(0, 1, 2))
    if pts.ndim == 1:
        pts = pts.reshape(1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) > max_points:
        # Deterministic spatially distributed sample after sorting by Morton-like key.
        lo = pts.min(axis=0)
        span = np.maximum(pts.max(axis=0) - lo, 1e-9)
        q = np.floor((pts - lo) / span * 1023).astype(np.int64)
        key = q[:, 0] + 1024 * q[:, 1] + (1024**2) * q[:, 2]
        order = np.argsort(key, kind="mergesort")
        idx = order[np.linspace(0, len(order) - 1, max_points, dtype=np.int64)]
        pts = pts[idx]
    return pts


def voxel_subsample(pts: np.ndarray, voxel: float) -> np.ndarray:
    if len(pts) == 0:
        return pts
    voxel = max(float(voxel), 0.005)
    origin = pts.min(axis=0)
    q = np.floor((pts - origin) / voxel).astype(np.int64)
    _, idx = np.unique(q, axis=0, return_index=True)
    return pts[np.sort(idx)]


def _smooth(v: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return v.astype(float, copy=True)
    k = np.ones(2 * radius + 1, dtype=float)
    k /= k.sum()
    return np.convolve(v.astype(float), k, mode="same")


def detect_storeys(z: np.ndarray) -> list[Storey]:
    if len(z) < 100:
        return [Storey(1, float(z.min()), float(z.max()), 0.3)]
    lo, hi = map(float, np.quantile(z, [0.002, 0.998]))
    dz = max(0.035, min(0.07, (hi - lo) / 300.0))
    n = max(32, int(math.ceil((hi - lo) / dz)))
    hist, edges = np.histogram(z, bins=n, range=(lo, hi))
    s = _smooth(hist, 2)
    peaks = []
    thr = max(float(np.quantile(s, 0.70)), 1.8 * float(np.median(s)))
    for i in range(2, len(s) - 2):
        if s[i] >= thr and s[i] >= s[i - 1] and s[i] >= s[i + 1]:
            zc = 0.5 * (edges[i] + edges[i + 1])
            if not peaks or abs(zc - peaks[-1][0]) > 0.16:
                peaks.append((float(zc), float(s[i])))
            elif s[i] > peaks[-1][1]:
                peaks[-1] = (float(zc), float(s[i]))

    q02, q98 = map(float, np.quantile(z, [0.02, 0.98]))
    levels = [p[0] for p in peaks]
    if not levels:
        return [Storey(1, q02, q98, 0.5)]

    # Candidate floor/ceiling pairs. Prefer architectural heights 2.1..4.8 m and
    # reuse a slab level as the next floor when appropriate.
    pairs: list[tuple[float, float, float]] = []
    used_until = -1e9
    i = 0
    while i < len(levels):
        f = levels[i]
        if f < used_until - 0.25:
            i += 1
            continue
        candidates = [(j, levels[j]) for j in range(i + 1, len(levels)) if 2.05 <= levels[j] - f <= 4.80]
        if not candidates:
            i += 1
            continue
        j, c = min(candidates, key=lambda x: abs((x[1] - f) - 2.8))
        conf = float(np.clip(1.0 - abs((c - f) - 2.8) / 2.0, 0.35, 0.95))
        pairs.append((f, c, conf))
        used_until = c
        i = j

    if not pairs:
        pairs = [(q02, q98, 0.45)]

    # Include points slightly outside persistent levels; prevents shaving wall tops/bases.
    out = []
    for sid, (f, c, conf) in enumerate(pairs, 1):
        z0 = min(f, float(np.quantile(z[z <= f + 0.20], 0.25)) if np.any(z <= f + 0.20) else f)
        z1 = max(c, float(np.quantile(z[z >= c - 0.20], 0.75)) if np.any(z >= c - 0.20) else c)
        out.append(Storey(sid, z0, z1, conf))
    return out


def _circular_distance(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def detect_orientations(xy: np.ndarray, angle_step: float = 2.0, max_families: int = 12) -> list[float]:
    span = max(float(np.ptp(xy[:, 0])), float(np.ptp(xy[:, 1])), 1.0)
    binw = max(0.04, min(0.12, span / 600.0))
    scored = []
    for a in np.arange(0.0, 180.0, max(0.5, angle_step)):
        th = math.radians(float(a))
        n = np.array([math.cos(th), math.sin(th)])
        tvec = np.array([-math.sin(th), math.cos(th)])
        d = xy @ n
        t = xy @ tvec
        nb = max(16, int(math.ceil((float(d.max()) - float(d.min())) / binw)))
        h, e = np.histogram(d, bins=nb)
        top = np.argsort(h)[::-1][: min(10, len(h))]
        vals = []
        for k in top:
            center = 0.5 * (e[k] + e[k + 1])
            m = np.abs(d - center) <= 1.5 * binw
            if m.sum() < 30:
                continue
            tt = t[m]
            extent = float(np.quantile(tt, 0.98) - np.quantile(tt, 0.02))
            vals.append(math.log1p(m.sum()) * max(extent, 0.0))
        scored.append((float(a), float(np.mean(vals[:4])) if vals else 0.0))
    scored.sort(key=lambda x: x[1], reverse=True)
    selected: list[float] = []
    for a, score in scored:
        if score <= 0:
            continue
        if all(_circular_distance(a, b) >= max(4.0, 2 * angle_step) for b in selected):
            selected.append(a)
        if len(selected) >= max_families:
            break
    return sorted(selected)


@dataclass
class _Face:
    theta: float
    d: float
    t0: float
    t1: float
    z0: float
    z1: float
    support: float
    continuity: float
    confidence: float


def _occupancy(tt: np.ndarray, zz: np.ndarray, t0: float, t1: float,
               z0: float, z1: float, cell: float = 0.08) -> tuple[float, float]:
    if t1 <= t0 or z1 <= z0:
        return 0.0, 0.0
    nt = int(np.clip(math.ceil((t1 - t0) / cell), 3, 700))
    nz = int(np.clip(math.ceil((z1 - z0) / cell), 3, 250))
    occ = np.zeros((nt, nz), dtype=bool)
    ti = np.clip(((tt - t0) / (t1 - t0) * nt).astype(int), 0, nt - 1)
    zi = np.clip(((zz - z0) / (z1 - z0) * nz).astype(int), 0, nz - 1)
    occ[ti, zi] = True
    area = float(occ.mean())
    columns = occ.mean(axis=1)
    continuity = float(np.mean(columns > max(0.05, 0.25 * np.quantile(columns[columns > 0], 0.7)))) if np.any(columns > 0) else 0.0
    return area, continuity


def detect_wall_faces(pts: np.ndarray, storey: Storey, orientations: list[float],
                      tolerance: float, spacing: float, raster: float) -> list[_Face]:
    band = pts[(pts[:, 2] >= storey.z0 - 0.10) & (pts[:, 2] <= storey.z1 + 0.10)]
    if len(band) < 100:
        return []
    xy, z = band[:, :2], band[:, 2]
    height = max(storey.z1 - storey.z0, 0.2)
    faces: list[_Face] = []
    for angle in orientations:
        th = math.radians(angle)
        n = np.array([math.cos(th), math.sin(th)])
        tvec = np.array([-math.sin(th), math.cos(th)])
        dn = xy @ n
        tt = xy @ tvec
        dmin, dmax = float(dn.min()), float(dn.max())
        step = max(spacing, min(0.05, tolerance))
        nb = max(10, int(math.ceil((dmax - dmin) / step)))
        hist, edges = np.histogram(dn, bins=nb, range=(dmin, dmax))
        smooth = _smooth(hist, max(1, int(round(tolerance / step))))
        if smooth.max(initial=0) <= 0:
            continue
        peak_thr = max(float(smooth.max()) * 0.055, float(np.quantile(smooth, 0.80)))
        cand = [i for i in range(1, len(smooth) - 1)
                if smooth[i] >= peak_thr and smooth[i] >= smooth[i - 1] and smooth[i] >= smooth[i + 1]]
        cand.sort(key=lambda i: smooth[i], reverse=True)
        accepted_bins: list[int] = []
        min_sep = max(2, int(round(0.07 / step)))
        for i in cand:
            if any(abs(i - j) < min_sep for j in accepted_bins):
                continue
            accepted_bins.append(i)
            if len(accepted_bins) >= 28:
                break
        for i in accepted_bins:
            d = float(0.5 * (edges[i] + edges[i + 1]))
            m = np.abs(dn - d) <= tolerance
            if m.sum() < 45:
                continue
            tloc, zloc = tt[m], z[m]
            t0, t1 = map(float, np.quantile(tloc, [0.01, 0.99]))
            z0, z1 = map(float, np.quantile(zloc, [0.01, 0.99]))
            z0, z1 = max(z0, storey.z0), min(z1, storey.z1)
            if t1 - t0 < 0.45 or z1 - z0 < min(1.20, 0.55 * height):
                continue
            support, continuity = _occupancy(tloc, zloc, t0, t1, z0, z1, max(0.06, raster * 2.5))
            vext = np.clip((z1 - z0) / height, 0, 1)
            hext = np.clip((t1 - t0) / max(float(np.ptp(tt)), 0.2), 0, 1)
            rho = float(np.clip((max(support, 1e-6) * max(vext, 1e-6) * max(continuity, 1e-6)) ** (1/3), 0, 1))
            confidence = float(np.clip(0.48 * rho + 0.30 * vext + 0.22 * min(1.0, hext / 0.18), 0, 1))
            if confidence < 0.22:
                continue
            faces.append(_Face(angle, d, t0, t1, z0, z1, support, continuity, confidence))
    return faces


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    den = max(1e-6, min(a1 - a0, b1 - b0))
    return inter / den


def faces_to_walls(faces: list[_Face], storey: Storey) -> list[Wall]:
    used: set[int] = set()
    walls: list[Wall] = []
    wid = 1
    order = sorted(range(len(faces)), key=lambda i: faces[i].confidence, reverse=True)
    for i in order:
        if i in used:
            continue
        a = faces[i]
        best = None
        best_score = -1.0
        for j, b in enumerate(faces):
            if j == i or j in used:
                continue
            if _circular_distance(a.theta, b.theta) > 2.5:
                continue
            sep = abs(a.d - b.d)
            if not 0.07 <= sep <= 0.55:
                continue
            ov = _overlap(a.t0, a.t1, b.t0, b.t1)
            zv = _overlap(a.z0, a.z1, b.z0, b.z1)
            if ov < 0.45 or zv < 0.50:
                continue
            score = 0.55 * ov + 0.25 * zv + 0.20 * min(a.confidence, b.confidence)
            if score > best_score:
                best = j
                best_score = score
        if best is not None:
            b = faces[best]
            used.update((i, best))
            d = 0.5 * (a.d + b.d)
            thickness = abs(a.d - b.d)
            t0, t1 = min(a.t0, b.t0), max(a.t1, b.t1)
            z0, z1 = min(a.z0, b.z0), max(a.z1, b.z1)
            support = max(a.support, b.support)
            continuity = max(a.continuity, b.continuity)
            conf = float(np.clip(0.60 * max(a.confidence, b.confidence) + 0.40 * best_score, 0, 1))
            source = "paired_faces"
        else:
            used.add(i)
            d = a.d
            thickness = 0.20
            t0, t1, z0, z1 = a.t0, a.t1, a.z0, a.z1
            support, continuity = a.support, a.continuity
            conf = a.confidence * 0.82
            source = "single_face_inferred_thickness"

        th = math.radians(a.theta)
        n = np.array([math.cos(th), math.sin(th)])
        tvec = np.array([-math.sin(th), math.cos(th)])
        p1 = n * d + tvec * t0
        p2 = n * d + tvec * t1
        if np.linalg.norm(p2 - p1) < 0.45:
            continue
        walls.append(Wall(wid, storey.id, float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]),
                          float(z0), float(z1), float(np.clip(thickness, 0.09, 0.45)), a.theta,
                          float(conf), float(support), float(continuity), source))
        wid += 1
    return _deduplicate_walls(walls)


def _line_params(w: Wall) -> tuple[np.ndarray, np.ndarray, float]:
    p1 = np.array([w.x1, w.y1], dtype=float)
    p2 = np.array([w.x2, w.y2], dtype=float)
    v = p2 - p1
    L = float(np.linalg.norm(v))
    return p1, p2, L


def _deduplicate_walls(walls: list[Wall]) -> list[Wall]:
    kept: list[Wall] = []
    for w in sorted(walls, key=lambda x: x.confidence, reverse=True):
        line = LineString([(w.x1, w.y1), (w.x2, w.y2)])
        duplicate = False
        for k in kept:
            if _circular_distance(w.theta_deg, k.theta_deg) > 3.0:
                continue
            kline = LineString([(k.x1, k.y1), (k.x2, k.y2)])
            if line.distance(kline) <= 0.12:
                inter = line.buffer(0.08).intersection(kline.buffer(0.08)).area
                den = max(1e-6, min(line.buffer(0.08).area, kline.buffer(0.08).area))
                if inter / den > 0.55:
                    duplicate = True
                    break
        if not duplicate:
            kept.append(w)
    for i, w in enumerate(kept, 1):
        w.id = i
    return kept


def snap_wall_graph(walls: list[Wall], snap: float = 0.35) -> list[Wall]:
    if len(walls) < 2:
        return walls
    endpoints = []
    lines_ext = []
    for w in walls:
        p1, p2, L = _line_params(w)
        if L <= 1e-9:
            lines_ext.append(LineString([p1, p2]))
            continue
        u = (p2 - p1) / L
        lines_ext.append(LineString([p1 - u * snap, p2 + u * snap]))
        endpoints.append((w, 0, p1))
        endpoints.append((w, 1, p2))

    inters: list[np.ndarray] = []
    for i in range(len(lines_ext)):
        for j in range(i + 1, len(lines_ext)):
            if _circular_distance(walls[i].theta_deg, walls[j].theta_deg) < 8.0:
                continue
            g = lines_ext[i].intersection(lines_ext[j])
            if g.is_empty:
                continue
            if g.geom_type == "Point":
                inters.append(np.array([g.x, g.y]))

    for w, which, p in endpoints:
        if not inters:
            continue
        d = np.array([np.linalg.norm(q - p) for q in inters])
        k = int(np.argmin(d))
        if d[k] <= snap:
            if which == 0:
                w.x1, w.y1 = map(float, inters[k])
            else:
                w.x2, w.y2 = map(float, inters[k])
    return _deduplicate_walls(walls)


def spaces_from_walls(walls: list[Wall], storey: Storey) -> list[Space]:
    lines = [LineString([(w.x1, w.y1), (w.x2, w.y2)]) for w in walls if w.storey_id == storey.id]
    if len(lines) < 3:
        return []
    merged = unary_union(lines)
    polys = list(polygonize(merged))
    spaces = []
    sid = 1
    for p in polys:
        if not isinstance(p, Polygon):
            continue
        area = float(p.area)
        if area < 1.2 or area > 5000.0:
            continue
        coords = [[float(x), float(y)] for x, y in list(p.exterior.coords)[:-1]]
        if len(coords) < 3:
            continue
        perimeter = float(p.length)
        conf = float(np.clip(1.0 - 1.0 / max(perimeter, 1.0), 0.45, 0.95))
        spaces.append(Space(sid, storey.id, f"Space_{storey.id}_{sid}", coords,
                            storey.z0, storey.z1, area, conf))
        sid += 1
    return spaces


def slabs_from_model(walls: list[Wall], spaces: list[Space], storey: Storey) -> list[Slab]:
    polys = [Polygon(s.polygon) for s in spaces if s.storey_id == storey.id]
    if polys:
        footprint = unary_union(polys).buffer(0.15)
        if footprint.geom_type == "MultiPolygon":
            footprint = max(footprint.geoms, key=lambda g: g.area)
    else:
        pts = [(w.x1, w.y1) for w in walls if w.storey_id == storey.id] + [(w.x2, w.y2) for w in walls if w.storey_id == storey.id]
        if len(pts) < 3:
            return []
        footprint = MultiPoint(pts).convex_hull.buffer(0.10)
    if footprint.geom_type != "Polygon" or footprint.area < 1.0:
        return []
    poly = [[float(x), float(y)] for x, y in list(footprint.exterior.coords)[:-1]]
    return [
        Slab(1, storey.id, "FLOOR", poly, storey.z0, 0.18, 0.80),
        Slab(2, storey.id, "CEILING", poly, storey.z1, 0.12, 0.70),
    ]


def _binary_dilate(a: np.ndarray, iterations: int = 1) -> np.ndarray:
    out = a.copy()
    for _ in range(iterations):
        p = np.pad(out, 1)
        acc = np.zeros_like(out, dtype=bool)
        for di in range(3):
            for dj in range(3):
                acc |= p[di:di + out.shape[0], dj:dj + out.shape[1]]
        out = acc
    return out


def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    nx, nz = mask.shape
    for i in range(nx):
        for k in range(nz):
            if not mask[i, k] or seen[i, k]:
                continue
            q = deque([(i, k)])
            seen[i, k] = True
            comp = []
            while q:
                x, y = q.popleft()
                comp.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < nx and 0 <= yy < nz and mask[xx, yy] and not seen[xx, yy]:
                        seen[xx, yy] = True
                        q.append((xx, yy))
            out.append(comp)
    return out


def detect_openings(pts: np.ndarray, walls: list[Wall], raster: float) -> list[Opening]:
    xy, z = pts[:, :2], pts[:, 2]
    cell = float(np.clip(max(0.055, raster * 2.5), 0.055, 0.10))
    result: list[Opening] = []
    oid = 1
    for w in walls:
        p1 = np.array([w.x1, w.y1])
        p2 = np.array([w.x2, w.y2])
        vec = p2 - p1
        L = float(np.linalg.norm(vec))
        H = float(w.z1 - w.z0)
        if L < 1.0 or H < 1.8:
            continue
        tvec = vec / L
        nvec = np.array([-tvec[1], tvec[0]])
        rel = xy - p1
        along = rel @ tvec
        normal = rel @ nvec
        m = ((along >= 0) & (along <= L) &
             (np.abs(normal) <= max(0.12, w.thickness * 0.80)) &
             (z >= w.z0) & (z <= w.z1))
        if m.sum() < 120:
            continue
        nx = int(np.clip(math.ceil(L / cell), 10, 600))
        nz = int(np.clip(math.ceil(H / cell), 18, 220))
        counts = np.zeros((nx, nz), dtype=np.uint16)
        xi = np.clip((along[m] / L * nx).astype(int), 0, nx - 1)
        zi = np.clip(((z[m] - w.z0) / H * nz).astype(int), 0, nz - 1)
        np.add.at(counts, (xi, zi), 1)
        nonzero = counts[counts > 0]
        if nonzero.size < 20:
            continue
        dense_thr = max(1, int(np.quantile(nonzero, 0.22)))
        occ = counts >= dense_thr
        # Closing tiny acquisition holes while preserving door/window-sized holes.
        occ = _binary_dilate(occ, 1)
        empty = ~occ
        # Ignore exterior void touching the top/left/right frame. Bottom is kept for doors.
        interior = np.ones_like(empty, dtype=bool)
        interior[0, :] = False
        interior[-1, :] = False
        interior[:, -1] = False
        empty &= interior
        for comp in _components(empty):
            xs = np.fromiter((c[0] for c in comp), dtype=int)
            zs = np.fromiter((c[1] for c in comp), dtype=int)
            x0, x1 = int(xs.min()), int(xs.max()) + 1
            z0i, z1i = int(zs.min()), int(zs.max()) + 1
            width = (x1 - x0) * L / nx
            height = (z1i - z0i) * H / nz
            sill = z0i * H / nz
            if not (0.45 <= width <= 3.20 and 0.45 <= height <= 2.80):
                continue
            # Surrounding wall evidence. A real opening has jambs and a lintel; a window
            # additionally has wall below it. This rejects most occlusion shadows.
            pad = max(1, int(round(0.15 / cell)))
            left = occ[max(0, x0-pad):x0, z0i:z1i].mean() if x0 > 0 else 0.0
            right = occ[x1:min(nx, x1+pad), z0i:z1i].mean() if x1 < nx else 0.0
            top = occ[x0:x1, z1i:min(nz, z1i+pad)].mean() if z1i < nz else 0.0
            bottom = occ[x0:x1, max(0, z0i-pad):z0i].mean() if z0i > 0 else 0.0
            touches_floor = sill <= 0.18
            if touches_floor:
                frame = float((left + right + top) / 3.0)
                plausible = 0.60 <= width <= 2.20 and 1.65 <= height <= min(2.60, 0.97 * H)
                kind = "Door"
                sill = 0.0
            else:
                frame = float((left + right + top + bottom) / 4.0)
                plausible = 0.45 <= sill <= 1.65 and 0.50 <= height <= 1.90 and width <= 3.00
                kind = "Window"
            if not plausible or frame < 0.22:
                continue
            fill_ratio = float(occ[x0:x1, z0i:z1i].mean())
            vacancy = 1.0 - fill_ratio
            conf = float(np.clip(0.45 * frame + 0.35 * vacancy + 0.20 * w.confidence, 0, 1))
            if conf < 0.42:
                continue
            offset = 0.5 * (x0 + x1) / nx * L
            if any(o.wall_id == w.id and abs(o.offset - offset) < 0.35 and o.kind == kind for o in result):
                continue
            result.append(Opening(oid, w.id, w.storey_id, kind, float(offset), float(width),
                                  float(sill), float(height), conf))
            oid += 1
    return result


def reconstruct_bim(request: dict) -> BIMModel:
    raw = load_xyz(request["input"], int(request.get("max_points", 2_000_000)))
    if len(raw) < 100:
        raise ValueError("Point cloud contains too few valid points for BIM reconstruction")
    raster = float(request.get("raster_cell_m", 0.02))
    pts = voxel_subsample(raw, max(0.025, min(0.06, raster * 1.5)))
    storeys = detect_storeys(pts[:, 2])
    all_walls: list[Wall] = []
    all_spaces: list[Space] = []
    all_slabs: list[Slab] = []

    for storey in storeys:
        band = pts[(pts[:, 2] >= storey.z0 - 0.10) & (pts[:, 2] <= storey.z1 + 0.10)]
        if len(band) < 100:
            continue
        orientations = detect_orientations(band[:, :2], float(request.get("angle_step_deg", 2.0)),
                                           int(request.get("max_orientation_families", 12)))
        faces = detect_wall_faces(band, storey, orientations,
                                  max(0.02, float(request.get("plane_tolerance_m", 0.03))),
                                  max(0.01, float(request.get("plane_spacing_m", 0.01))), raster)
        walls = faces_to_walls(faces, storey)
        walls = snap_wall_graph(walls, snap=0.38)
        # Keep wall objects with substantial vertical extent and confidence; this is an
        # object-level gate, not a surface-hypothesis gate.
        walls = [w for w in walls if (w.z1 - w.z0) >= 1.40 and w.confidence >= 0.24]
        # Renumber globally.
        base = len(all_walls)
        for k, w in enumerate(walls, 1):
            w.id = base + k
        spaces = spaces_from_walls(walls, storey)
        slabs = slabs_from_model(walls, spaces, storey)
        all_walls.extend(walls)
        all_spaces.extend(spaces)
        all_slabs.extend(slabs)

    openings = detect_openings(pts, all_walls, raster)
    # Space/slab ids globally unique.
    for i, s in enumerate(all_spaces, 1):
        s.id = i
    for i, s in enumerate(all_slabs, 1):
        s.id = i
    for i, o in enumerate(openings, 1):
        o.id = i

    warnings = []
    if not all_walls:
        warnings.append("No wall objects reconstructed")
    if not all_spaces:
        warnings.append("No closed room/space polygons reconstructed; floor plan graph is incomplete")
    if not openings:
        warnings.append("No door/window openings passed the conservative object classifier")

    summary = {
        "status": "success" if all_walls else "warning",
        "method": "Fuzzy-RESF-BIM",
        "version": "2.0-object-bim",
        "input_points": int(len(raw)),
        "regularized_points": int(len(pts)),
        "storeys": len(storeys),
        "walls": len(all_walls),
        "doors": sum(o.kind == "Door" for o in openings),
        "windows": sum(o.kind == "Window" for o in openings),
        "spaces": len(all_spaces),
        "slabs": len(all_slabs),
        "warnings": warnings,
    }
    return BIMModel(storeys, all_walls, openings, all_spaces, all_slabs, summary)


def write_csv(items: Iterable, path: str | Path, empty_fields: list[str]) -> None:
    items = list(items)
    fields = list(asdict(items[0]).keys()) if items else empty_fields
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for item in items:
            writer.writerow(asdict(item))


def write_model_json(model: BIMModel, path: str | Path) -> None:
    payload = {
        "summary": model.summary,
        "storeys": [asdict(x) for x in model.storeys],
        "walls": [asdict(x) for x in model.walls],
        "openings": [asdict(x) for x in model.openings],
        "spaces": [asdict(x) for x in model.spaces],
        "slabs": [asdict(x) for x in model.slabs],
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
