"""Fuzzy-RESF-BIM v6.5: evidence-graph preserving reconstruction.

This revision was introduced after a real historic-building regression where v6.4
over-regularised the object layer. The 2-D Fuzzy-RESF wall skeleton is treated as
the authoritative topological evidence and architectural primitives are fitted to it
only when the cloud supports the change.

Main changes:
* preserve piecewise-linear wall evidence instead of rebuilding walls from a coarse hull;
* reject implausibly large false openings before IFC export;
* regularise roof facets against connected upper-envelope support, never across unsupported gaps;
* recover rooms from a noded wall-evidence graph;
* search stair flights across multiple vertical bands, including stairs below the storey datum.

No reference BIM is read at runtime.
"""
from __future__ import annotations

from dataclasses import replace
import math
import numpy as np
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.ops import polygonize, unary_union

import bim_reconstruction as base
import architectural_graph_v5 as core
import building_shell_v6 as v6
import building_shell_v6_1 as v61
import building_shell_v6_3 as v63
import building_shell_v6_4 as v64
from topology_completion import geometric_feedback, snap_wall_graph

RoofFacet = v6.RoofFacet
StairFlight = v64.StairFlight


def _line_angle(a, b) -> float:
    d = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    return float(math.degrees(math.atan2(d[1], d[0])) % 180.0)


def _candidate_endpoints(c: base.RESFCandidate):
    th = math.radians(float(c.theta_deg))
    n = np.array([math.cos(th), math.sin(th)], dtype=float)
    t = np.array([-math.sin(th), math.cos(th)], dtype=float)
    p0 = n * float(c.d) + t * float(c.t0)
    p1 = n * float(c.d) + t * float(c.t1)
    return p0, p1


def _candidate_score(c: base.RESFCandidate) -> float:
    state_bonus = 1.0 if c.state == "Permanent" else (0.86 if c.state == "OccludedPermanent" else 0.45)
    return float(state_bonus * (
        0.34 * np.clip(c.confidence, 0, 1)
        + 0.22 * np.clip(c.E_V, 0, 1)
        + 0.16 * np.clip(c.N, 0, 1)
        + 0.14 * np.clip(c.A_star, 0, 1)
        + 0.14 * np.clip(c.semantic_wall, 0, 1)
    ))


def _wall_from_candidate(c: base.RESFCandidate, storey: base.Storey, wid: int) -> base.Wall:
    p0, p1 = _candidate_endpoints(c)
    return base.Wall(
        wid, c.storey_id, float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1]),
        max(float(c.z0), float(storey.z0)), min(float(c.z1), float(storey.z1)),
        0.18, float(c.theta_deg), float(c.confidence),
        float(c.A_star), float(c.E_H), "resf_evidence_graph", c.state,
        float(c.A_star), float(c.E_H), float(c.E_V), float(c.C), float(c.N), float(c.O),
        0.0,
    )


def _microcorner_clean(poly: Polygon, cell: float) -> Polygon:
    """Remove only small oscillations; preserve genuine curved/polygonal historical facades."""
    if poly is None or poly.geom_type != "Polygon":
        return poly
    g = poly.simplify(max(0.07, 0.75 * cell), preserve_topology=True)
    if g.geom_type != "Polygon":
        g = poly
    pts = [np.asarray(p, float) for p in list(g.exterior.coords)[:-1]]
    if len(pts) < 4:
        return g
    changed = True
    while changed and len(pts) > 4:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            l0, l1 = float(np.linalg.norm(b - a)), float(np.linalg.norm(c - b))
            if min(l0, l1) < 1e-6:
                del pts[i]
                changed = True
                break
            turn = v63._adiff(_line_angle(a, b), _line_angle(b, c))
            dev = LineString([a, c]).distance(Point(float(b[0]), float(b[1])))
            if turn <= 5.0 and dev <= max(0.10, 1.15 * cell):
                del pts[i]
                changed = True
                break
            if min(l0, l1) <= max(0.24, 2.2 * cell) and turn <= 18.0 and dev <= 0.14:
                del pts[i]
                changed = True
                break
    out = Polygon([[float(p[0]), float(p[1])] for p in pts])
    if not out.is_valid:
        out = out.buffer(0)
    if out.geom_type != "Polygon" or out.area < 2.0:
        return g
    ratio = out.area / max(poly.area, 1e-9)
    if not 0.90 <= ratio <= 1.10:
        return g
    return out


def _candidate_support_for_wall(w: base.Wall, candidates: list[base.RESFCandidate]) -> float:
    line = LineString([(w.x1, w.y1), (w.x2, w.y2)])
    L = max(line.length, 1e-9)
    best = 0.0
    for c in candidates:
        if c.storey_id != w.storey_id:
            continue
        if base._circular_distance(float(c.theta_deg), float(w.theta_deg)) > 6.0:
            continue
        p0, p1 = _candidate_endpoints(c)
        cl = LineString([p0, p1])
        if line.distance(cl) > max(0.24, 0.75 * w.thickness):
            continue
        overlap = line.buffer(0.12, cap_style=2).intersection(cl.buffer(0.12, cap_style=2)).area
        frac = float(np.clip(overlap / max(0.24 * L, 1e-9), 0, 1))
        best = max(best, frac * _candidate_score(c))
    return float(best)


def reconstruct_wall_evidence_graph(model, pts: np.ndarray, fp: Polygon, raster: float):
    """Build a conservative physical graph from the intermediate wall evidence."""
    storey = model.storeys[0]
    support_xy, _, _, cell = v6._facade_columns(pts, raster)
    reg = _microcorner_clean(v63.regularize_facade_footprint(fp, support_xy, cell), cell)

    old_ext = [w for w in model.walls if str(w.source).startswith("closed_facade_envelope")]
    thickness = float(np.median([w.thickness for w in old_ext])) if old_ext else 0.34
    ext = v6.walls_from_footprint(pts, reg, storey, model.candidates, thickness)

    interiors = []
    for w in model.walls:
        if str(w.source).startswith("closed_facade_envelope"):
            continue
        L = math.hypot(w.x2 - w.x1, w.y2 - w.y1)
        cand_support = _candidate_support_for_wall(w, model.candidates)
        if L < 0.65:
            continue
        if w.confidence < 0.36 and cand_support < 0.20:
            continue
        if getattr(w, "geometric_feedback", 0.0) < 0.06 and w.confidence < 0.68 and cand_support < 0.24:
            continue
        interiors.append(replace(w))

    shell = reg.buffer(0.35)
    for c in model.candidates:
        if c.storey_id != storey.id or c.state not in ("Permanent", "OccludedPermanent"):
            continue
        p0, p1 = _candidate_endpoints(c)
        L = float(np.linalg.norm(p1 - p0))
        sc = _candidate_score(c)
        if L < 0.85 or sc < 0.40 or c.E_V < 0.42 or c.N < 0.28:
            continue
        mid = 0.5 * (p0 + p1)
        if not shell.covers(Point(float(mid[0]), float(mid[1]))):
            continue
        line = LineString([p0, p1])
        duplicate = False
        for w in interiors:
            wl = LineString([(w.x1, w.y1), (w.x2, w.y2)])
            if base._circular_distance(c.theta_deg, w.theta_deg) <= 5.0 and line.distance(wl) <= 0.20:
                if line.buffer(0.10).intersection(wl.buffer(0.10)).area > 0.05:
                    duplicate = True
                    break
        if duplicate:
            continue
        interiors.append(_wall_from_candidate(c, storey, len(interiors) + 1))

    interiors = core.merge_collinear(interiors, 0.42) if interiors else []
    interiors = core.deduplicate_physical_walls(interiors) if interiors else []
    interiors = snap_wall_graph(interiors, 0.38) if interiors else []

    walls = ext + interiors
    geometric_feedback(pts, walls)
    final = []
    for w in walls:
        if str(w.source).startswith("closed_facade_envelope"):
            final.append(w)
            continue
        cs = _candidate_support_for_wall(w, model.candidates)
        if getattr(w, "geometric_feedback", 0.0) >= 0.08 or w.confidence >= 0.70 or cs >= 0.22:
            final.append(w)
    for i, w in enumerate(final, 1):
        w.id = i
    return reg, final


def spaces_from_evidence_graph(footprint: Polygon, walls: list[base.Wall], storey: base.Storey):
    """Polygonise the same wall skeleton that is exported to IFC."""
    ext = [w for w in walls if str(w.source).startswith("closed_facade_envelope")]
    th = float(np.median([w.thickness for w in ext])) if ext else 0.34
    inner = footprint.buffer(-max(0.10, 0.52 * th), join_style=2)
    if inner.geom_type == "MultiPolygon":
        inner = max(inner.geoms, key=lambda p: p.area)
    if inner.geom_type != "Polygon" or inner.area < 2.0:
        return []

    parts = []
    for w in walls:
        if str(w.source).startswith("closed_facade_envelope"):
            continue
        L = math.hypot(w.x2 - w.x1, w.y2 - w.y1)
        if L < 0.80 or w.confidence < 0.36:
            continue
        ln = LineString([(w.x1, w.y1), (w.x2, w.y2)])
        ex = v63._extend_line(ln, 0.36).intersection(inner)
        if ex.geom_type == "LineString" and ex.length >= 0.72:
            parts.append(ex)
        elif ex.geom_type == "MultiLineString":
            parts.extend([q for q in ex.geoms if q.length >= 0.72])

    if not parts:
        return [base.Space(1, storey.id, "GrossInterior",
                           [[float(x), float(y)] for x, y in list(inner.exterior.coords)[:-1]],
                           storey.z0, storey.z1, float(inner.area), 0.64)]

    graph = unary_union([inner.boundary] + parts)
    polys = []
    for p in polygonize(graph):
        if p.area < 1.8 or not inner.covers(p.representative_point()):
            continue
        p = p.intersection(inner)
        if p.geom_type != "Polygon" or p.area < 1.8:
            continue
        r = p.minimum_rotated_rectangle
        rc = np.asarray(r.exterior.coords, float)
        sides = [float(np.linalg.norm(rc[i + 1] - rc[i])) for i in range(4)] if len(rc) >= 5 else [0]
        compact = float(4 * math.pi * p.area / max(p.length * p.length, 1e-9))
        if min(sides, default=0) < 0.62 or compact < 0.030:
            continue
        polys.append(p)

    if not polys:
        return [base.Space(1, storey.id, "GrossInterior",
                           [[float(x), float(y)] for x, y in list(inner.exterior.coords)[:-1]],
                           storey.z0, storey.z1, float(inner.area), 0.60)]

    polys = sorted(polys, key=lambda p: p.area, reverse=True)
    cap = int(np.clip(round(inner.area / 3.5), 2, 24))
    polys = polys[:cap]
    out = []
    for i, p in enumerate(polys, 1):
        closure = float(np.clip(p.area / max(p.convex_hull.area, 1e-9), 0, 1))
        conf = float(np.clip(0.58 + 0.25 * closure + 0.17 * min(1.0, p.area / 8.0), 0, 0.97))
        out.append(base.Space(i, storey.id, f"Room_{i:02d}",
                              [[float(x), float(y)] for x, y in list(p.exterior.coords)[:-1]],
                              storey.z0, storey.z1, float(p.area), conf))
    return out


def _opening_cloud_evidence(pts: np.ndarray, wall: base.Wall, opening: base.Opening, raster: float):
    p0 = np.array([wall.x1, wall.y1], float)
    p1 = np.array([wall.x2, wall.y2], float)
    d = p1 - p0
    L = float(np.linalg.norm(d))
    if L < 1e-6:
        return 0.0, 1.0
    u = d / L
    n = np.array([-u[1], u[0]])
    rel = pts[:, :2] - p0
    t = rel @ u
    off = np.abs(rel @ n)
    z = pts[:, 2] - wall.z0
    band = max(0.14, 0.75 * wall.thickness, 2.5 * raster)
    m = (off <= band) & (t >= 0) & (t <= L) & (z >= -0.10) & (z <= wall.z1 - wall.z0 + 0.10)
    if int(m.sum()) < 40:
        return 0.0, 0.5
    t, z = t[m], z[m]
    a = max(0.0, opening.offset - 0.5 * opening.width)
    b = min(L, opening.offset + 0.5 * opening.width)
    c = max(0.0, opening.sill)
    e = min(wall.z1 - wall.z0, opening.sill + opening.height)
    if b <= a or e <= c:
        return 0.0, 1.0
    edge = max(0.08, 1.7 * raster)
    interior = (t >= a + edge) & (t <= b - edge) & (z >= c + edge) & (z <= e - edge)
    frame = (((np.abs(t - a) <= edge) | (np.abs(t - b) <= edge)) & (z >= c - edge) & (z <= e + edge)) | ((np.abs(z - e) <= edge) & (t >= a - edge) & (t <= b + edge))
    if opening.kind == "Window":
        frame |= (np.abs(z - c) <= edge) & (t >= a - edge) & (t <= b + edge)
    ai = max((b - a - 2 * edge) * (e - c - 2 * edge), 0.02)
    af = max(2 * edge * (2 * (e - c) + (b - a)), 0.02)
    din = float(interior.sum()) / ai
    dfr = float(frame.sum()) / af
    vacancy = float(np.clip(1.0 - din / max(dfr, 1e-9), 0, 1))
    frame_support = float(np.clip(frame.sum() / 35.0, 0, 1))
    return frame_support, vacancy


def filter_openings(pts: np.ndarray, walls: list[base.Wall], openings: list[base.Opening], raster: float):
    """Reject facade-scale vacancies that were incorrectly promoted to Door/Window."""
    by_id = {int(w.id): w for w in walls}
    out = []
    for o in openings:
        w = by_id.get(int(o.wall_id))
        if w is None:
            continue
        L = math.hypot(w.x2 - w.x1, w.y2 - w.y1)
        H = max(w.z1 - w.z0, 1e-6)
        if L < 0.65 or o.width <= 0 or o.height <= 0:
            continue
        ratio_w = o.width / L
        ratio_a = (o.width * o.height) / max(L * H, 1e-9)
        margin = min(o.offset - 0.5 * o.width, L - (o.offset + 0.5 * o.width))
        fs, vacancy = _opening_cloud_evidence(pts, w, o, raster)

        if o.kind == "Window":
            plausible = (0.32 <= o.width <= 2.60 and 0.40 <= o.height <= min(2.25, 0.82 * H) and 0.22 <= o.sill <= max(1.90, 0.70 * H) and ratio_w <= 0.52 and ratio_a <= 0.38 and margin >= -0.08)
            evidence = (o.confidence >= 0.50 and vacancy >= 0.18 and fs >= 0.18) or (o.confidence >= 0.72 and vacancy >= 0.08)
        else:
            plausible = (0.48 <= o.width <= 2.90 and 1.45 <= o.height <= min(2.85, 0.96 * H) and -0.08 <= o.sill <= 0.32 and ratio_w <= 0.62 and ratio_a <= 0.52 and margin >= -0.10)
            evidence = (o.confidence >= 0.48 and vacancy >= 0.12 and fs >= 0.12) or (o.confidence >= 0.70 and vacancy >= 0.05)
        if not plausible or not evidence:
            continue
        o.confidence = float(np.clip(0.65 * o.confidence + 0.20 * vacancy + 0.15 * fs, 0, 0.98))
        o.source = "host_wall_vacancy_with_frame_support"
        out.append(o)
    for i, o in enumerate(out, 1):
        o.id = i
    return out


def _roof_plane(vertices):
    a = np.asarray(vertices, float)
    A = np.column_stack((a[:, 0], a[:, 1], np.ones(len(a))))
    abc, *_ = np.linalg.lstsq(A, a[:, 2], rcond=None)
    return abc


def _roof_polygon_clean(poly: Polygon, cell: float):
    if poly is None or poly.geom_type != "Polygon" or poly.area < 0.7:
        return None
    g = poly.convex_hull if poly.area / max(poly.convex_hull.area, 1e-9) >= 0.88 else poly
    g = g.simplify(max(0.12, 1.05 * cell), preserve_topology=True)
    if g.geom_type != "Polygon":
        return None
    pts = [np.asarray(p, float) for p in list(g.exterior.coords)[:-1]]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            l0, l1 = np.linalg.norm(b - a), np.linalg.norm(c - b)
            turn = v63._adiff(_line_angle(a, b), _line_angle(b, c))
            if turn <= 7.0 or (min(l0, l1) < max(0.30, 2.0 * cell) and turn <= 20.0):
                del pts[i]
                changed = True
                break
    q = Polygon([[float(p[0]), float(p[1])] for p in pts])
    if not q.is_valid:
        q = q.buffer(0)
    if q.geom_type != "Polygon" or q.area < 0.7:
        return g
    rect = q.minimum_rotated_rectangle
    if rect.geom_type == "Polygon":
        fill = q.area / max(rect.area, 1e-9)
        if fill >= 0.84 and rect.area <= 1.17 * q.area:
            q = rect
    return q


def regularize_roof_supported(pts: np.ndarray, facets: list[RoofFacet], footprint: Polygon, storey: base.Storey, raster: float):
    """Regularise each roof plane against real connected upper-envelope support."""
    if not facets:
        return []
    cell = float(np.clip(max(0.12, 2.4 * raster), 0.12, 0.20))
    top = float(np.quantile(pts[:, 2], 0.985))
    zstart = storey.z0 + max(1.55, 0.30 * (top - storey.z0))
    upper = v63._upper_grid(pts, zstart, cell)
    if len(upper) < 40:
        return facets
    envelope = footprint.buffer(1.60)
    out = []
    used_polys = []
    for f in sorted(facets, key=lambda x: x.confidence, reverse=True):
        abc = _roof_plane(f.vertices)
        old = Polygon([(v[0], v[1]) for v in f.vertices])
        if not old.is_valid:
            old = old.buffer(0)
        if old.geom_type != "Polygon":
            continue
        pred = abc[0] * upper[:, 0] + abc[1] * upper[:, 1] + abc[2]
        residual = np.abs(upper[:, 2] - pred)
        near = residual <= max(0.10, 0.62 * cell)
        near &= np.array([envelope.covers(Point(float(x), float(y))) for x, y in upper[:, :2]], bool)
        near &= np.array([old.buffer(0.55).covers(Point(float(x), float(y))) for x, y in upper[:, :2]], bool)
        pin = upper[near]
        if len(pin) < 18:
            continue
        comps = v61._components(pin[:, :2], max(1.85 * cell, 0.25))
        if not comps:
            continue
        best = max(comps, key=lambda ci: (sum(old.buffer(0.20).covers(Point(float(x), float(y))) for x, y in pin[ci, :2]), len(ci)))
        sup = pin[best]
        if len(sup) < 14:
            continue
        hull = MultiPoint(sup[:, :2].tolist()).convex_hull
        if hull.geom_type != "Polygon":
            continue
        poly = _roof_polygon_clean(hull.intersection(envelope), cell)
        if poly is None:
            continue
        support_geom = MultiPoint(sup[:, :2].tolist()).buffer(0.82 * cell, resolution=1)
        coverage = float(support_geom.intersection(poly).area / max(poly.area, 1e-9))
        if coverage < 0.42:
            continue
        if any(poly.intersection(q).area / max(min(poly.area, q.area), 1e-9) > 0.70 for q in used_polys):
            continue
        A = np.column_stack((sup[:, 0], sup[:, 1], np.ones(len(sup))))
        abc2, *_ = np.linalg.lstsq(A, sup[:, 2], rcond=None)
        rms = float(np.sqrt(np.mean((A @ abc2 - sup[:, 2]) ** 2)))
        if rms > max(0.11, 0.65 * cell):
            continue
        verts = [[float(x), float(y), float(abc2[0] * x + abc2[1] * y + abc2[2])] for x, y in list(poly.exterior.coords)[:-1]]
        conf = float(np.clip(0.55 * f.confidence + 0.25 * coverage + 0.20 * (1.0 - rms / max(0.11, 0.65 * cell)), 0, 0.98))
        out.append(RoofFacet(len(out) + 1, f.storey_id, verts, f.thickness, conf, "connected_scan_supported_regular_roof_plane"))
        used_polys.append(poly)
    return out


def detect_stairs_multilevel(pts: np.ndarray, footprint: Polygon, storey: base.Storey, raster: float):
    """Run the tread detector through overlapping vertical windows, including below z0."""
    zq = np.quantile(pts[:, 2], [0.01, 0.99])
    low = float(min(storey.z0, zq[0]))
    bases = [float(storey.z0)]
    b = float(storey.z0 - 0.65)
    while b >= low - 0.15 and len(bases) < 5:
        bases.append(b)
        b -= 0.65
    found = []
    for basez in bases:
        s = base.Storey(storey.id, basez, max(storey.z1, basez + 2.4), storey.confidence)
        found.extend(v64.detect_stair_flights(pts, footprint, s, raster))
    kept = []
    for st in sorted(found, key=lambda q: (q.step_count, q.confidence), reverse=True):
        dup = False
        for k in kept:
            d = math.hypot(st.x - k.x, st.y - k.y)
            da = abs(((st.theta_deg - k.theta_deg + 180) % 360) - 180)
            if d < 0.75 and min(da, abs(da - 180)) < 15:
                dup = True
                break
        if not dup:
            st.storey_id = storey.id
            kept.append(st)
        if len(kept) >= 8:
            break
    for i, st in enumerate(kept, 1):
        st.id = i
    return kept


def reconstruct_bim(request: dict):
    # Start from v6.3 rather than v6.4: the real scan showed that v6.4 could
    # over-correct the useful intermediate wall graph.
    model = v63.reconstruct_bim(request)
    if model.summary.get("scene_mode") == "indoor_room":
        model.stairs = []
        model.summary.update({"version": "6.5-evidence-graph-preserving", "v65_path": "delegated_indoor_graph", "reference_bim_used_at_runtime": False})
        return model

    raw = base.load_xyz(request["input"], int(request.get("max_points", 2_000_000)))
    raster = float(np.clip(float(request.get("raster_cell_m", 0.05)), 0.04, 0.10))
    pts = base.voxel_subsample(raw, max(0.03, min(0.055, raster)))
    fp = v61._LAST_FOOTPRINT
    if fp is None or fp.geom_type != "Polygon":
        model.stairs = []
        model.summary["version"] = "6.5-evidence-graph-preserving"
        model.summary.setdefault("warnings", []).append("v6.5 could not access the facade footprint; v6.3 object graph retained")
        return model

    reg, model.walls = reconstruct_wall_evidence_graph(model, pts, fp, raster)
    model.spaces = spaces_from_evidence_graph(reg, model.walls, model.storeys[0])
    raw_roofs = v63.fit_roof_facets(pts, reg, model.storeys[0], raster)
    model.roofs = regularize_roof_supported(pts, raw_roofs, reg, model.storeys[0], raster)
    raw_openings = v63.detect_openings(pts, model.walls, raster)
    model.openings = filter_openings(pts, model.walls, raw_openings, raster)
    v61._dedupe_openings(model)
    for i, o in enumerate(model.openings, 1):
        o.id = i
    model.stairs = detect_stairs_multilevel(pts, reg, model.storeys[0], raster)

    s = model.summary
    s.update({
        "version": "6.5-evidence-graph-preserving",
        "scene_mode": "exterior_building_shell",
        "wall_reconstruction": "intermediate_resf_wall_graph_preserved_then_conservatively_regularized",
        "space_reconstruction": "noded_exported_wall_graph_polygonization",
        "roof_reconstruction": "connected_upper_envelope_support_plus_regular_plane_boundaries",
        "opening_reconstruction": "architectural_size_constraints_plus_host_wall_frame_vacancy_evidence",
        "stair_reconstruction": "multi_vertical_band_repeated_tread_sequences",
        "footprint_vertices": len(reg.exterior.coords) - 1,
        "walls": len(model.walls), "spaces": len(model.spaces), "roof_facets": len(model.roofs), "stairs": len(model.stairs),
        "doors": sum(o.kind == "Door" for o in model.openings), "windows": sum(o.kind == "Window" for o in model.openings),
        "openings": len(model.openings), "reference_bim_used_at_runtime": False,
    })
    warnings = s.setdefault("warnings", [])
    if not model.roofs:
        warnings.append("No roof facet passed v6.5 connected-support regularisation")
    if len(model.spaces) <= 1:
        warnings.append("Only one closed interior cell was recovered from the evidence wall graph")
    if not model.stairs:
        warnings.append("No staircase flight passed v6.5 multi-level repeated-tread checks")
    return model
