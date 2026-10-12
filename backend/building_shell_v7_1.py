"""Fuzzy-RESF-BIM v7.1: evidence-preserving physical-object hypotheses.

v7.1 is an extension of the manuscript workflow, not the frozen v1 implementation.
It keeps RESF/fuzzy evidence primary and makes the object layer more conservative:
- wall fragments are merged across a real gap only when the candidate graph supports
  coplanar continuation/occlusion; otherwise the gap is preserved;
- Cloud<->BIM support is a decision gate, so unsupported inferred walls are retained only
  as Uncertain diagnostics rather than silently exported as strong BIM objects;
- IfcSpace polygons are derived from the exact final wall graph, with only small endpoint
  completions to nearby graph nodes/boundaries;
- roof support is evaluated per output polygon (not per whole component), preventing a
  dense neighbouring patch from validating an unsupported facet;
- opening filters are host/evidence driven and standards remain soft priors;
- column hypotheses intersecting exported wall axes are rejected.

No reference BIM is read at runtime and no object is created from a standard alone.
"""
from __future__ import annotations

from dataclasses import replace
import math
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, polygonize, unary_union

import bim_reconstruction as base
import architectural_graph_v5 as core
import building_shell_v6 as v6
import building_shell_v6_1 as v61
import building_shell_v6_3 as v63
import building_shell_v6_5 as v65
from topology_completion import geometric_feedback, snap_wall_graph
from object_knowledge_v7 import (
    load_registry,
    refine_openings_with_abacus,
    standards_alignment_payload,
    object_abacus_payload,
)

RoofFacet = v6.RoofFacet
StairFlight = v65.StairFlight


def _wall_len(w: base.Wall) -> float:
    return float(math.hypot(w.x2 - w.x1, w.y2 - w.y1))


def _axis(theta_deg: float):
    th = math.radians(float(theta_deg))
    n = np.array([math.cos(th), math.sin(th)], dtype=float)
    t = np.array([-math.sin(th), math.cos(th)], dtype=float)
    return n, t


def _wall_interval(w: base.Wall, theta_deg: float | None = None):
    theta = float(w.theta_deg if theta_deg is None else theta_deg)
    n, t = _axis(theta)
    p0 = np.array([w.x1, w.y1], dtype=float)
    p1 = np.array([w.x2, w.y2], dtype=float)
    d = float(0.5 * (p0 @ n + p1 @ n))
    a, b = sorted((float(p0 @ t), float(p1 @ t)))
    return n, t, d, a, b


def _candidate_interval(c: base.RESFCandidate, theta_deg: float):
    p0, p1 = v65._candidate_endpoints(c)
    n, t = _axis(theta_deg)
    d = float(0.5 * (p0 @ n + p1 @ n))
    a, b = sorted((float(p0 @ t), float(p1 @ t)))
    return d, a, b


def _interval_overlap_length(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _interval_gap(a0: float, a1: float, b0: float, b1: float) -> float:
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return 0.0


def _candidate_topology_score(c: base.RESFCandidate) -> float:
    vals = [float(getattr(c, k, 0.0)) for k in ("T_F", "T_C", "T_W", "T_B", "T_R", "T_CP")]
    return float(np.clip(
        0.20 * vals[0] + 0.16 * vals[1] + 0.20 * vals[2]
        + 0.14 * vals[3] + 0.14 * vals[4] + 0.16 * vals[5], 0, 1))


def _wall_candidate_evidence(w: base.Wall, candidates: list[base.RESFCandidate]) -> dict:
    _, _, dw, a0, a1 = _wall_interval(w)
    length = max(a1 - a0, 1e-9)
    best_support = 0.0
    best_occ = 0.0
    best_top = 0.0
    best_conf = 0.0
    for c in candidates:
        if int(c.storey_id) != int(w.storey_id):
            continue
        if base._circular_distance(float(c.theta_deg), float(w.theta_deg)) > 5.0:
            continue
        dc, b0, b1 = _candidate_interval(c, float(w.theta_deg))
        if abs(dc - dw) > max(0.24, 0.75 * float(w.thickness)):
            continue
        ov = _interval_overlap_length(a0, a1, b0, b1) / length
        if ov < 0.12:
            continue
        cs = float(np.clip(ov * v65._candidate_score(c), 0, 1))
        best_support = max(best_support, cs)
        best_occ = max(best_occ, float(np.clip(c.O, 0, 1)) * max(0.35, ov))
        best_top = max(best_top, _candidate_topology_score(c) * max(0.45, ov))
        best_conf = max(best_conf, float(np.clip(c.confidence, 0, 1)) * max(0.45, ov))
    return {"candidate_support": best_support, "occlusion": best_occ,
            "topology": best_top, "candidate_confidence": best_conf}


def _gap_bridge_evidence(a: base.Wall, b: base.Wall,
                         candidates: list[base.RESFCandidate], gap: float) -> float:
    if gap <= 0.14:
        return 1.0
    if int(a.storey_id) != int(b.storey_id):
        return 0.0
    if base._circular_distance(float(a.theta_deg), float(b.theta_deg)) > 3.0:
        return 0.0
    theta = float(a.theta_deg)
    _, _, da, a0, a1 = _wall_interval(a, theta)
    _, _, db, b0, b1 = _wall_interval(b, theta)
    if abs(da - db) > max(0.14, 0.55 * max(float(a.thickness), float(b.thickness))):
        return 0.0
    lo0, lo1 = (a0, a1) if a1 <= b0 else (b0, b1)
    hi0, hi1 = (b0, b1) if a1 <= b0 else (a0, a1)
    g0, g1 = lo1, hi0
    if g1 <= g0:
        return 1.0
    dref = 0.5 * (da + db)
    best = 0.0
    for c in candidates:
        if int(c.storey_id) != int(a.storey_id):
            continue
        if c.state not in ("Permanent", "OccludedPermanent"):
            continue
        if base._circular_distance(float(c.theta_deg), theta) > 4.0:
            continue
        dc, c0, c1 = _candidate_interval(c, theta)
        if abs(dc - dref) > max(0.18, 0.65 * max(float(a.thickness), float(b.thickness))):
            continue
        bridge = _interval_overlap_length(g0, g1, c0, c1) / max(g1 - g0, 1e-9)
        if bridge < 0.55:
            continue
        top = _candidate_topology_score(c)
        score = float(np.clip(
            0.30 * np.clip(c.confidence, 0, 1)
            + 0.25 * np.clip(c.T_CP, 0, 1)
            + 0.20 * np.clip(c.O, 0, 1)
            + 0.15 * top
            + 0.10 * bridge, 0, 1))
        best = max(best, score)
    return best


def _merge_two_walls(a: base.Wall, b: base.Wall, gap: float, bridge: float) -> base.Wall:
    theta = float(a.theta_deg if a.confidence >= b.confidence else b.theta_deg)
    n, t = _axis(theta)
    _, _, da, a0, a1 = _wall_interval(a, theta)
    _, _, db, b0, b1 = _wall_interval(b, theta)
    la, lb = max(_wall_len(a), 0.05), max(_wall_len(b), 0.05)
    wa, wb = la * max(a.confidence, 0.08), lb * max(b.confidence, 0.08)
    d = float((wa * da + wb * db) / max(wa + wb, 1e-9))
    u0, u1 = min(a0, b0), max(a1, b1)
    p0, p1 = n * d + t * u0, n * d + t * u1

    def avg(name: str) -> float:
        return float((wa * float(getattr(a, name)) + wb * float(getattr(b, name))) / max(wa + wb, 1e-9))

    q = replace(a)
    q.x1, q.y1, q.x2, q.y2 = float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1])
    q.z0, q.z1 = min(a.z0, b.z0), max(a.z1, b.z1)
    q.theta_deg = theta
    q.thickness = float(np.clip(avg("thickness"), 0.09, 0.45))
    q.confidence = float(np.clip(avg("confidence") - 0.05 * min(1.0, gap / 1.5) + 0.05 * bridge, 0, 0.99))
    q.support = avg("support")
    q.continuity = max(float(a.continuity), float(b.continuity), float(1.0 - min(1.0, gap / max(u1-u0, 1e-9))))
    q.source = "supported_gap_completion" if gap > 0.14 else "overlap_or_sampling_gap_merge"
    q.state = "OccludedPermanent" if gap > 0.14 else (a.state if a.confidence >= b.confidence else b.state)
    for name in ("resf_A", "resf_EH", "resf_EV", "resf_N"):
        setattr(q, name, avg(name))
    q.resf_C = max(float(a.resf_C), float(b.resf_C))
    q.resf_O = max(float(a.resf_O), float(b.resf_O), float(bridge if gap > 0.14 else 0.0))
    q.geometric_feedback = min(float(a.geometric_feedback), float(b.geometric_feedback))
    return q


def conservative_merge_collinear(walls: list[base.Wall], candidates: list[base.RESFCandidate]) -> list[base.Wall]:
    """Merge only overlaps/sampling cracks or gaps explicitly supported by the evidence graph."""
    pending = [replace(w) for w in walls]
    changed = True
    while changed:
        changed = False
        best_pair = None
        best_cost = float("inf")
        for i, a in enumerate(pending):
            _, _, da, a0, a1 = _wall_interval(a)
            for j in range(i + 1, len(pending)):
                b = pending[j]
                if int(a.storey_id) != int(b.storey_id):
                    continue
                if base._circular_distance(float(a.theta_deg), float(b.theta_deg)) > 3.0:
                    continue
                _, _, db, b0, b1 = _wall_interval(b, float(a.theta_deg))
                dd = abs(da - db)
                if dd > max(0.14, 0.55 * max(float(a.thickness), float(b.thickness))):
                    continue
                gap = _interval_gap(a0, a1, b0, b1)
                if gap > 2.20:
                    continue
                bridge = _gap_bridge_evidence(a, b, candidates, gap)
                if gap > 0.14 and bridge < 0.58:
                    continue
                cost = 5.0 * dd + gap - 0.35 * bridge
                if cost < best_cost:
                    best_pair = (i, j, gap, bridge)
                    best_cost = cost
        if best_pair is not None:
            i, j, gap, bridge = best_pair
            merged = _merge_two_walls(pending[i], pending[j], gap, bridge)
            pending = [w for k, w in enumerate(pending) if k not in (i, j)] + [merged]
            changed = True
    pending = core.deduplicate_physical_walls(pending)
    for i, w in enumerate(pending, 1):
        w.id = i
    return pending


def _object_wall_decision(w: base.Wall, candidates: list[base.RESFCandidate]) -> tuple[bool, base.Wall, dict]:
    ev = _wall_candidate_evidence(w, candidates)
    gf = float(np.clip(getattr(w, "geometric_feedback", 0.0), 0, 1))
    cs = ev["candidate_support"]
    top = ev["topology"]
    occ = max(ev["occlusion"], float(np.clip(getattr(w, "resf_O", 0.0), 0, 1)))
    conf0 = float(np.clip(w.confidence, 0, 1))
    evv = float(np.clip(getattr(w, "resf_EV", 0.0), 0, 1))
    nn = float(np.clip(getattr(w, "resf_N", 0.0), 0, 1))
    score = float(np.clip(0.30*conf0 + 0.27*gf + 0.18*cs + 0.10*top + 0.08*evv + 0.07*nn, 0, 1))
    inferred = str(w.source).startswith(("high_consensus_resf_recovery", "supported_gap_completion", "horizontal_envelope_completion", "coplanar_gap_completion"))
    exterior = str(w.source).startswith("closed_facade_envelope")
    occluded_supported = occ >= 0.55 and top >= 0.42 and max(cs, ev["candidate_confidence"]) >= 0.30 and evv >= 0.48

    if exterior:
        accepted = gf >= 0.055 or (conf0 >= 0.72 and cs >= 0.18)
    elif inferred:
        accepted = score >= 0.43 and (gf >= 0.12 or occluded_supported)
    else:
        accepted = score >= 0.40 and (gf >= 0.09 or occluded_supported or (conf0 >= 0.82 and evv >= 0.70))

    q = replace(w)
    q.confidence = float(np.clip(0.52*conf0 + 0.25*gf + 0.13*cs + 0.10*top + (0.04 if occluded_supported and gf < 0.12 else 0.0), 0, 0.985))
    q.source = str(w.source) + "+v71_object_feedback"
    q.state = ("OccludedPermanent" if accepted and occluded_supported and gf < 0.12
               else ("Permanent" if accepted else "Uncertain"))
    reason = "accepted_scan_supported"
    if accepted and q.state == "OccludedPermanent":
        reason = "accepted_occlusion_continuation_supported"
    elif not accepted:
        reason = "uncertain_insufficient_cloud_or_topology_support"
    decision = {
        "wall_id_before": int(w.id), "storey_id": int(w.storey_id), "source": str(w.source),
        "accepted": bool(accepted), "state": q.state, "reason": reason,
        "object_score": score, "cloud_feedback": gf, "candidate_support": cs,
        "topology_support": top, "occlusion_support": occ, "confidence_before": conf0,
        "confidence_after": q.confidence,
    }
    return accepted, q, decision


def consensus_wall_graph(model, pts: np.ndarray, footprint: Polygon | None):
    """Physical wall hypotheses with evidence-supported gap completion and an explicit Uncertain sink."""
    if not model.storeys:
        return model.walls, [], []
    exterior = [replace(w) for w in model.walls if str(w.source).startswith("closed_facade_envelope")]
    interior = []
    for w in model.walls:
        if str(w.source).startswith("closed_facade_envelope") or _wall_len(w) < 0.65:
            continue
        ev = _wall_candidate_evidence(w, model.candidates)
        if (w.confidence >= 0.48 and getattr(w, "resf_EV", 0.0) >= 0.45) or ev["candidate_support"] >= 0.24:
            interior.append(replace(w))

    # Exceptional recovery from the multi-hypothesis set. A candidate must have strong
    # relational evidence; a RESF peak alone never becomes a wall.
    for c in model.candidates:
        if c.state not in ("Permanent", "OccludedPermanent"):
            continue
        storey = next((s for s in model.storeys if int(s.id) == int(c.storey_id)), None)
        if storey is None:
            continue
        p0, p1 = v65._candidate_endpoints(c)
        length = float(np.linalg.norm(p1 - p0))
        top = _candidate_topology_score(c)
        sc = v65._candidate_score(c)
        if length < 1.05 or sc < 0.60 or top < 0.48 or c.E_V < 0.60 or c.N < 0.50:
            continue
        if footprint is not None and footprint.geom_type == "Polygon" and int(c.storey_id) == int(model.storeys[0].id):
            mid = 0.5 * (p0 + p1)
            if not footprint.buffer(0.38).covers(Point(float(mid[0]), float(mid[1]))):
                continue
        line = LineString([p0, p1])
        if any(int(w.storey_id) == int(c.storey_id)
               and base._circular_distance(c.theta_deg, w.theta_deg) <= 5.0
               and line.distance(LineString([(w.x1, w.y1), (w.x2, w.y2)])) <= 0.18
               for w in interior):
            continue
        w = v65._wall_from_candidate(c, storey, len(interior) + 1)
        w.confidence = float(np.clip(0.68*c.confidence + 0.18*sc + 0.14*top, 0, 0.95))
        w.source = "high_consensus_resf_recovery"
        interior.append(w)

    interior = conservative_merge_collinear(interior, model.candidates)
    # Small junction completion only. This is intentionally much smaller than historical
    # 0.55-0.72 m snap distances that could visibly reshape real walls.
    if interior:
        interior = snap_wall_graph(interior, 0.20)
        interior = core.deduplicate_physical_walls(interior)
    walls = exterior + interior
    geometric_feedback(pts, walls)

    accepted, uncertain, decisions = [], [], []
    for w in walls:
        ok, q, d = _object_wall_decision(w, model.candidates)
        decisions.append(d)
        (accepted if ok else uncertain).append(q)
    for i, w in enumerate(accepted, 1):
        w.id = i
    for i, w in enumerate(uncertain, 1):
        w.id = i
    return accepted, uncertain, decisions


def _snap_line_endpoints_to_graph(line: LineString, targets, max_gap: float) -> LineString:
    coords = [np.asarray(line.coords[0], dtype=float), np.asarray(line.coords[-1], dtype=float)]
    if targets is None or getattr(targets, "is_empty", True):
        return line
    out = []
    for p in coords:
        pp = Point(float(p[0]), float(p[1]))
        dist = float(targets.distance(pp))
        if 1e-9 < dist <= max_gap:
            q = nearest_points(pp, targets)[1]
            out.append((float(q.x), float(q.y)))
        else:
            out.append((float(p[0]), float(p[1])))
    return LineString(out)


def spaces_from_final_wall_graph(footprint: Polygon, walls: list[base.Wall], storey: base.Storey,
                                 raster: float) -> list[base.Space]:
    """Polygonize the exact exported wall graph; close only small endpoint gaps."""
    if footprint is None or footprint.geom_type != "Polygon":
        return []
    ext = [w for w in walls if int(w.storey_id) == int(storey.id) and str(w.source).startswith("closed_facade_envelope")]
    t_ext = float(np.median([w.thickness for w in ext])) if ext else 0.34
    inner = footprint.buffer(-max(0.10, 0.52*t_ext), join_style=2)
    if inner.geom_type == "MultiPolygon":
        inner = max(inner.geoms, key=lambda p: p.area)
    if inner.geom_type != "Polygon" or inner.area < 2.0:
        return []

    raw_lines = []
    for w in walls:
        if int(w.storey_id) != int(storey.id) or str(w.source).startswith("closed_facade_envelope"):
            continue
        if _wall_len(w) < 0.80 or w.confidence < 0.48 or w.state == "Uncertain":
            continue
        q = LineString([(w.x1, w.y1), (w.x2, w.y2)]).intersection(inner)
        if q.geom_type == "LineString" and q.length >= 0.72:
            raw_lines.append(q)
        elif q.geom_type == "MultiLineString":
            raw_lines.extend([x for x in q.geoms if x.length >= 0.72])

    if not raw_lines:
        return [base.Space(1, storey.id, "GrossInterior",
                           [[float(x), float(y)] for x, y in list(inner.exterior.coords)[:-1]],
                           storey.z0, storey.z1, float(inner.area), 0.62)]

    max_gap = float(np.clip(max(0.12, 2.8*raster), 0.12, 0.22))
    completed = []
    for i, ln in enumerate(raw_lines):
        others = [inner.boundary] + [q for j, q in enumerate(raw_lines) if j != i]
        targets = unary_union(others)
        completed.append(_snap_line_endpoints_to_graph(ln, targets, max_gap))

    graph = unary_union([inner.boundary] + completed)
    polys = []
    for p in polygonize(graph):
        if p.area < 2.0 or not inner.covers(p.representative_point()):
            continue
        p = p.intersection(inner)
        if p.geom_type != "Polygon" or p.area < 2.0:
            continue
        rect = p.minimum_rotated_rectangle
        rc = np.asarray(rect.exterior.coords, dtype=float)
        sides = [float(np.linalg.norm(rc[i+1]-rc[i])) for i in range(4)] if len(rc) >= 5 else [0.0]
        compact = float(4.0*math.pi*p.area / max(p.length*p.length, 1e-9))
        if min(sides, default=0.0) < 0.62 or compact < 0.030:
            continue
        polys.append(p)
    if not polys:
        return [base.Space(1, storey.id, "GrossInterior",
                           [[float(x), float(y)] for x, y in list(inner.exterior.coords)[:-1]],
                           storey.z0, storey.z1, float(inner.area), 0.58)]
    polys = sorted(polys, key=lambda p: p.area, reverse=True)
    cap = int(np.clip(round(inner.area / 4.0), 2, 24))
    out = []
    for i, p in enumerate(polys[:cap], 1):
        closure = float(np.clip(p.area / max(p.convex_hull.area, 1e-9), 0, 1))
        conf = float(np.clip(0.55 + 0.25*closure + 0.20*min(1.0, p.area/8.0), 0, 0.96))
        out.append(base.Space(i, storey.id, f"Room_{i:02d}",
                              [[float(x), float(y)] for x, y in list(p.exterior.coords)[:-1]],
                              storey.z0, storey.z1, float(p.area), conf))
    return out


def _robust_plane(points: np.ndarray):
    return v7._robust_plane(points)


def _support_cell_polygons(xy: np.ndarray, cell: float):
    return v7._support_cell_polygons(xy, cell)


def _polygon_support_coverage(xy: np.ndarray, poly: Polygon, cell: float) -> tuple[float, np.ndarray]:
    if len(xy) == 0 or poly is None or poly.geom_type != "Polygon" or poly.area <= 0:
        return 0.0, np.zeros(len(xy), dtype=bool)
    region = poly.buffer(0.58*cell, join_style=2)
    local = np.array([region.covers(Point(float(x), float(y))) for x, y in xy], dtype=bool)
    # _upper_grid has one sample per occupied XY cell, so count*cell^2 is a conservative
    # per-polygon support-area proxy. Crucially, support from another disconnected polygon
    # can no longer validate this one.
    coverage = float(np.clip(int(local.sum()) * cell * cell / max(poly.area, 1e-9), 0, 1))
    return coverage, local


def fit_roofs_from_supported_cells(pts: np.ndarray, raw_facets: list[RoofFacet], footprint: Polygon,
                                   storey: base.Storey, raster: float):
    if not raw_facets:
        return []
    cell = float(np.clip(max(0.11, 2.2*raster), 0.11, 0.18))
    top = float(np.quantile(pts[:, 2], 0.99))
    zstart = storey.z0 + max(1.55, 0.28*(top-storey.z0))
    upper = v63._upper_grid(pts, zstart, cell)
    if len(upper) < 35:
        return []
    env = footprint.buffer(max(0.65, 4.0*cell))
    upper = upper[np.array([env.covers(Point(float(x), float(y))) for x, y in upper[:, :2]], bool)]
    out, used = [], []
    for f in sorted(raw_facets, key=lambda q: q.confidence, reverse=True):
        old = Polygon([(v[0], v[1]) for v in f.vertices])
        if not old.is_valid:
            old = old.buffer(0)
        if old.geom_type != "Polygon" or old.area < 0.6:
            continue
        abc0, _ = _robust_plane(np.asarray(f.vertices, dtype=float))
        if abc0 is None:
            continue
        pred = abc0[0]*upper[:, 0] + abc0[1]*upper[:, 1] + abc0[2]
        near = np.abs(upper[:, 2]-pred) <= max(0.09, 0.58*cell)
        near &= np.array([old.buffer(0.38).covers(Point(float(x), float(y))) for x, y in upper[:, :2]], bool)
        support = upper[near]
        if len(support) < 14:
            continue
        comps = v61._components(support[:, :2], max(1.62*cell, 0.20))
        for ci in sorted(comps, key=len, reverse=True):
            sup = support[ci]
            if len(sup) < 12:
                continue
            for poly in _support_cell_polygons(sup[:, :2], cell):
                poly = poly.intersection(env)
                if poly.geom_type != "Polygon" or poly.area < 0.65:
                    continue
                coverage, local = _polygon_support_coverage(sup[:, :2], poly, cell)
                local_sup = sup[local]
                if coverage < 0.52 or len(local_sup) < 10:
                    continue
                abc, rms = _robust_plane(local_sup)
                if abc is None or rms > max(0.095, 0.58*cell):
                    continue
                if any(poly.intersection(q).area / max(min(poly.area, q.area), 1e-9) > 0.78 for q in used):
                    continue
                verts = [[float(x), float(y), float(abc[0]*x + abc[1]*y + abc[2])]
                         for x, y in list(poly.exterior.coords)[:-1]]
                conf = float(np.clip(0.52*f.confidence + 0.30*coverage
                                     + 0.18*(1.0-rms/max(0.095, 0.58*cell)), 0, 0.985))
                out.append(RoofFacet(len(out)+1, f.storey_id, verts, f.thickness, conf,
                                     "local_occupied_upper_cells_robust_plane"))
                used.append(poly)
                if len(out) >= 12:
                    return out
    return out


def filter_openings_evidence_first(pts: np.ndarray, walls: list[base.Wall],
                                   openings: list[base.Opening], raster: float):
    """Use host-wall vacancy/frame evidence; broad geometry limits are anti-degeneracy only."""
    by_id = {int(w.id): w for w in walls}
    out = []
    for o in openings:
        w = by_id.get(int(o.wall_id))
        if w is None:
            continue
        L = _wall_len(w)
        H = max(float(w.z1-w.z0), 1e-6)
        if L < 0.65 or o.width <= 0 or o.height <= 0:
            continue
        ratio_w = float(o.width/L)
        ratio_a = float((o.width*o.height)/max(L*H, 1e-9))
        margin = min(float(o.offset-0.5*o.width), float(L-(o.offset+0.5*o.width)))
        if ratio_w > 0.70 or ratio_a > 0.58 or margin < -0.12 or o.height > 0.97*H:
            continue
        fs, vacancy = v65._opening_cloud_evidence(pts, w, o, raster)
        if o.kind == "Door":
            physical = o.width >= 0.35 and o.height >= 1.20 and -0.10 <= o.sill <= 0.38
            evidence = (o.confidence >= 0.46 and vacancy >= 0.11 and fs >= 0.10) or (o.confidence >= 0.74 and vacancy >= 0.07)
        else:
            physical = o.width >= 0.25 and o.height >= 0.25 and o.sill >= 0.05
            evidence = (o.confidence >= 0.46 and vacancy >= 0.14 and fs >= 0.14) or (o.confidence >= 0.76 and vacancy >= 0.08)
        if not physical or not evidence:
            continue
        q = replace(o)
        q.confidence = float(np.clip(0.66*o.confidence + 0.20*vacancy + 0.14*fs, 0, 0.98))
        q.source = "host_wall_vacancy_frame_evidence_v71"
        out.append(q)
    for i, o in enumerate(out, 1):
        o.id = i
    return out


def filter_columns_against_walls(columns, walls: list[base.Wall]):
    out = []
    for c in columns:
        p = Point(float(c.x), float(c.y))
        half = 0.45 * min(float(c.width), float(c.depth))
        embedded = False
        for w in walls:
            if int(w.storey_id) != int(c.storey_id):
                continue
            line = LineString([(w.x1, w.y1), (w.x2, w.y2)])
            if line.distance(p) <= 0.5*float(w.thickness) + half:
                embedded = True
                break
        if not embedded:
            out.append(c)
    for i, c in enumerate(out, 1):
        c.id = i
    return out


def reconstruct_bim(request: dict):
    # v6.3 remains the physical-object starting point because it predates the aggressive
    # v6.5 candidate promotion seen to regress the real scan. v7.1 then applies explicit
    # object-hypothesis decisions rather than another threshold-only patch.
    model = v63.reconstruct_bim(request)
    raw = base.load_xyz(request["input"], int(request.get("max_points", 2_000_000)))
    raster = float(np.clip(float(request.get("raster_cell_m", 0.05)), 0.04, 0.10))
    pts = base.voxel_subsample(raw, max(0.03, min(0.055, raster)))
    registry = load_registry()

    fp = v61._LAST_FOOTPRINT
    if fp is not None and fp.geom_type == "Polygon" and model.storeys:
        support_xy, _, _, cell = v6._facade_columns(pts, raster)
        reg = v65._microcorner_clean(v63.regularize_facade_footprint(fp, support_xy, cell), cell)
        model.walls, uncertain_walls, decisions = consensus_wall_graph(model, pts, reg)
        model.spaces = spaces_from_final_wall_graph(reg, model.walls, model.storeys[0], raster)
        raw_roofs = v63.fit_roof_facets(pts, reg, model.storeys[0], raster)
        model.roofs = fit_roofs_from_supported_cells(pts, raw_roofs, reg, model.storeys[0], raster)
        raw_openings = v63.detect_openings(pts, model.walls, raster)
        filtered = filter_openings_evidence_first(pts, model.walls, raw_openings, raster)
        model.openings, abacus = refine_openings_with_abacus(filtered, model.walls, registry)
        model.columns = filter_columns_against_walls(list(getattr(model, "columns", [])), model.walls)
        model.stairs = v65.detect_stairs_multilevel(pts, reg, model.storeys[0], raster)
    else:
        # Indoor benchmark path: keep the multi-storey architectural graph produced by the
        # paper-aligned indoor backend. Do not impose an exterior shell interpretation.
        uncertain_walls, decisions = [], []
        geometric_feedback(pts, model.walls)
        accepted = []
        for w in model.walls:
            ok, q, d = _object_wall_decision(w, model.candidates)
            decisions.append(d)
            (accepted if ok else uncertain_walls).append(q)
        # If the stricter object gate would empty an indoor model, retain the already fuzzy-
        # accepted graph but flag it for review rather than silently returning no BIM.
        if accepted:
            model.walls = accepted
        else:
            model.summary.setdefault("warnings", []).append(
                "v7.1 object-feedback gate accepted no indoor walls; paper-aligned fuzzy wall graph retained as Uncertain-review fallback")
            for w in model.walls:
                w.state = "Uncertain"
        raw_openings = list(getattr(model, "openings", []))
        filtered = filter_openings_evidence_first(pts, model.walls, raw_openings, raster) if raw_openings else []
        model.openings, abacus = refine_openings_with_abacus(filtered, model.walls, registry)
        model.columns = filter_columns_against_walls(list(getattr(model, "columns", [])), model.walls)
        model.stairs = list(getattr(model, "stairs", []))

    model.uncertain_wall_hypotheses = uncertain_walls
    model.object_decisions = decisions
    model.object_abacus = object_abacus_payload(abacus)
    model.standards_alignment = standards_alignment_payload(registry)

    s = model.summary
    s.update({
        "version": "7.1-evidence-object-hypotheses",
        "wall_reconstruction": "multi_hypothesis_resf_plus_supported_gap_completion_plus_object_feedback_gate",
        "space_reconstruction": "final_exported_wall_graph_polygonization_small_endpoint_completion_only",
        "roof_reconstruction": "per_polygon_local_occupied_upper_cell_support_no_cross_component_validation",
        "opening_reconstruction": "host_wall_vacancy_frame_evidence_then_scan_abacus_soft_prior",
        "column_reconstruction": "scan_supported_columns_rejected_when_embedded_in_exported_wall_axes",
        "stair_reconstruction": "multilevel_tread_sequence_search_including_below_storey_datum",
        "architectural_optimization": "conservative_graph_consistency_gates_not_full_global_probabilistic_optimizer",
        "standards_geometry_policy": "soft_prior_only_never_invent_geometry",
        "paper_frozen_candidate_plane_spacing_m": 0.01,
        "paper_frozen_projected_support_grid_m": 0.05,
        "paper_frozen_fuzzy_threshold": 0.50,
        "paper_frozen_topology_threshold": 0.48,
        "walls": len(model.walls), "uncertain_wall_hypotheses": len(uncertain_walls),
        "spaces": len(model.spaces), "roof_facets": len(getattr(model, "roofs", [])),
        "columns": len(getattr(model, "columns", [])), "stairs": len(getattr(model, "stairs", [])),
        "doors": sum(o.kind == "Door" for o in model.openings),
        "windows": sum(o.kind == "Window" for o in model.openings),
        "opening_types": len(abacus),
        "reference_bim_used_at_runtime": False,
    })
    warnings = s.setdefault("warnings", [])
    if uncertain_walls:
        warnings.append(f"{len(uncertain_walls)} wall hypotheses were withheld from strong IFC export and retained as Uncertain diagnostics")
    if not abacus and model.openings:
        warnings.append("Openings were detected, but no repeated scan-derived type cluster was found; measured dimensions were preserved")
    if not getattr(model, "roofs", []) and fp is not None:
        warnings.append("No roof facet passed v7.1 local occupied-cell support checks; unsupported roof geometry was not invented")
    if len(model.spaces) <= 1:
        warnings.append("Room topology remains incomplete: only one gross/closed space was recovered")
    return model
