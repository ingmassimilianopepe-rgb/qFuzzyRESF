"""Fuzzy-RESF-BIM v7.0: paper-core consensus + scan-derived object knowledge.

The v7 path intentionally returns to the manuscript logic: multi-peak RESF hypotheses,
topology/occlusion/fuzzy evidence and Cloud<->BIM feedback remain the primary decision
system. Object-specific modules are added *after* that stage. Standards and object abaci
are soft priors/metadata only and never act as a hidden reference BIM.

Key protections against regressions seen in v6.4-v6.5:
- no coarse-hull replacement of a useful wall graph;
- no automatic promotion of every RESF segment to a physical wall;
- rooms use only consensus partitions;
- roof polygons are unions of actually occupied support cells, not convex hulls spanning gaps;
- door/window types are learned from repeated dimensions in the current scan.
"""
from __future__ import annotations

from dataclasses import replace
import math
import numpy as np
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.ops import unary_union

import bim_reconstruction as base
import architectural_graph_v5 as core
import building_shell_v6 as v6
import building_shell_v6_1 as v61
import building_shell_v6_3 as v63
import building_shell_v6_5 as v65
from topology_completion import geometric_feedback, snap_wall_graph
from object_knowledge_v7 import load_registry, refine_openings_with_abacus, standards_alignment_payload, object_abacus_payload

RoofFacet = v6.RoofFacet
StairFlight = v65.StairFlight


def _wall_len(w: base.Wall) -> float:
    return float(math.hypot(w.x2 - w.x1, w.y2 - w.y1))


def _wall_consensus_score(w: base.Wall, candidates: list[base.RESFCandidate]) -> float:
    cs = v65._candidate_support_for_wall(w, candidates)
    gf = float(np.clip(getattr(w, "geometric_feedback", 0.0), 0, 1))
    return float(np.clip(
        0.36 * np.clip(w.confidence, 0, 1)
        + 0.22 * gf
        + 0.20 * cs
        + 0.12 * np.clip(getattr(w, "resf_EV", 0.0), 0, 1)
        + 0.10 * np.clip(getattr(w, "resf_N", 0.0), 0, 1), 0, 1))


def _candidate_topology_score(c: base.RESFCandidate) -> float:
    vals = [float(getattr(c, k, 0.0)) for k in ("T_F", "T_C", "T_W", "T_B", "T_R", "T_CP")]
    return float(np.clip(0.22 * vals[0] + 0.18 * vals[1] + 0.18 * vals[2] + 0.16 * vals[3] + 0.14 * vals[4] + 0.12 * vals[5], 0, 1))


def consensus_wall_graph(model, pts: np.ndarray, footprint: Polygon | None):
    """Conservative wall graph: physical instances first, exceptional candidate recovery second."""
    if not model.storeys:
        return model.walls
    storey = model.storeys[0]
    exterior = []
    interior = []
    for w in model.walls:
        if str(w.source).startswith("closed_facade_envelope"):
            exterior.append(replace(w))
            continue
        if _wall_len(w) < 0.65:
            continue
        score = _wall_consensus_score(w, model.candidates)
        if score >= 0.43 or (w.confidence >= 0.68 and getattr(w, "resf_EV", 0.0) >= 0.45):
            q = replace(w)
            q.confidence = float(np.clip(0.72 * w.confidence + 0.28 * score, 0, 0.98))
            q.source = str(w.source) + "+paper_consensus"
            interior.append(q)

    # Recover only exceptionally well-supported missed partitions. This is deliberately
    # much stricter than the v6.5 candidate promotion that caused noisy wall graphs.
    shell = footprint.buffer(0.40) if footprint is not None and footprint.geom_type == "Polygon" else None
    for c in model.candidates:
        if c.storey_id != storey.id or c.state not in ("Permanent", "OccludedPermanent"):
            continue
        p0, p1 = v65._candidate_endpoints(c)
        L = float(np.linalg.norm(p1 - p0))
        top = _candidate_topology_score(c)
        sc = v65._candidate_score(c)
        if L < 1.05 or sc < 0.59 or top < 0.46 or c.E_V < 0.58 or c.N < 0.48:
            continue
        mid = 0.5 * (p0 + p1)
        if shell is not None and not shell.covers(Point(float(mid[0]), float(mid[1]))):
            continue
        line = LineString([p0, p1])
        if any(base._circular_distance(c.theta_deg, w.theta_deg) <= 5.0 and
               line.distance(LineString([(w.x1, w.y1), (w.x2, w.y2)])) <= 0.18
               for w in interior):
            continue
        w = v65._wall_from_candidate(c, storey, len(interior) + 1)
        w.confidence = float(np.clip(0.68 * c.confidence + 0.20 * sc + 0.12 * top, 0, 0.95))
        w.source = "high_consensus_resf_recovery"
        interior.append(w)

    if interior:
        interior = core.merge_collinear(interior, 0.30)
        interior = core.deduplicate_physical_walls(interior)
        interior = snap_wall_graph(interior, 0.30)
    walls = exterior + interior
    geometric_feedback(pts, walls)

    final = []
    for w in walls:
        if str(w.source).startswith("closed_facade_envelope"):
            final.append(w)
            continue
        score = _wall_consensus_score(w, model.candidates)
        if score >= 0.40 and (getattr(w, "geometric_feedback", 0.0) >= 0.06 or w.confidence >= 0.72):
            final.append(w)
    for i, w in enumerate(final, 1):
        w.id = i
    return final


def _robust_plane(points: np.ndarray):
    if len(points) < 3:
        return None, 1e9
    p = points.copy()
    abc = None
    for _ in range(3):
        A = np.column_stack((p[:, 0], p[:, 1], np.ones(len(p))))
        abc, *_ = np.linalg.lstsq(A, p[:, 2], rcond=None)
        r = np.abs(A @ abc - p[:, 2])
        if len(r) < 12:
            break
        limit = max(0.035, float(np.quantile(r, 0.82)))
        keep = r <= limit
        if keep.sum() < max(10, int(0.60 * len(p))):
            break
        p = p[keep]
    A = np.column_stack((p[:, 0], p[:, 1], np.ones(len(p))))
    rms = float(np.sqrt(np.mean((A @ abc - p[:, 2]) ** 2))) if abc is not None else 1e9
    return abc, rms


def _support_cell_polygons(xy: np.ndarray, cell: float):
    if len(xy) < 8:
        return []
    # Each polygon starts from occupied scan cells. Small one-cell gaps may close, but a
    # roof can never jump across a large unsupported courtyard/void as convex_hull did.
    squares = [box(float(x - 0.52 * cell), float(y - 0.52 * cell),
                   float(x + 0.52 * cell), float(y + 0.52 * cell)) for x, y in xy]
    g = unary_union(squares)
    close = 0.34 * cell
    g = g.buffer(close, join_style=2).buffer(-close, join_style=2)
    polys = [g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))
    out = []
    for p in polys:
        if p.geom_type != "Polygon" or p.area < max(0.55, 10 * cell * cell):
            continue
        q = p.simplify(max(0.06, 0.55 * cell), preserve_topology=True)
        if q.geom_type != "Polygon":
            q = p
        rect = q.minimum_rotated_rectangle
        if rect.geom_type == "Polygon":
            fill = q.area / max(rect.area, 1e-9)
            # Rectangularize only when the scan itself is already nearly rectangular.
            if fill >= 0.94 and rect.area <= 1.08 * q.area:
                q = rect
        out.append(q)
    return out


def fit_roofs_from_supported_cells(pts: np.ndarray, raw_facets: list[RoofFacet], footprint: Polygon,
                                   storey: base.Storey, raster: float):
    if not raw_facets:
        return []
    cell = float(np.clip(max(0.11, 2.2 * raster), 0.11, 0.18))
    top = float(np.quantile(pts[:, 2], 0.99))
    zstart = storey.z0 + max(1.55, 0.28 * (top - storey.z0))
    upper = v63._upper_grid(pts, zstart, cell)
    if len(upper) < 35:
        return []
    env = footprint.buffer(1.35)
    inside = np.array([env.covers(Point(float(x), float(y))) for x, y in upper[:, :2]], bool)
    upper = upper[inside]
    out = []
    used = []
    for f in sorted(raw_facets, key=lambda q: q.confidence, reverse=True):
        old = Polygon([(v[0], v[1]) for v in f.vertices])
        if not old.is_valid:
            old = old.buffer(0)
        if old.geom_type != "Polygon" or old.area < 0.6:
            continue
        abc0, _ = _robust_plane(np.asarray(f.vertices, float))
        if abc0 is None:
            continue
        pred = abc0[0] * upper[:, 0] + abc0[1] * upper[:, 1] + abc0[2]
        near = np.abs(upper[:, 2] - pred) <= max(0.09, 0.58 * cell)
        near &= np.array([old.buffer(0.38).covers(Point(float(x), float(y))) for x, y in upper[:, :2]], bool)
        support = upper[near]
        if len(support) < 14:
            continue
        comps = v61._components(support[:, :2], max(1.62 * cell, 0.20))
        for ci in sorted(comps, key=len, reverse=True):
            sup = support[ci]
            if len(sup) < 12:
                continue
            abc, rms = _robust_plane(sup)
            if abc is None or rms > max(0.095, 0.58 * cell):
                continue
            for poly in _support_cell_polygons(sup[:, :2], cell):
                poly = poly.intersection(env)
                if poly.geom_type != "Polygon" or poly.area < 0.65:
                    continue
                # Occupied-cell area relative to the final facet is a direct anti-bridge test.
                occupied = len(sup) * cell * cell
                coverage = float(np.clip(occupied / max(poly.area, 1e-9), 0, 1))
                if coverage < 0.52:
                    continue
                if any(poly.intersection(q).area / max(min(poly.area, q.area), 1e-9) > 0.78 for q in used):
                    continue
                verts = [[float(x), float(y), float(abc[0] * x + abc[1] * y + abc[2])]
                         for x, y in list(poly.exterior.coords)[:-1]]
                conf = float(np.clip(0.56 * f.confidence + 0.27 * coverage +
                                     0.17 * (1.0 - rms / max(0.095, 0.58 * cell)), 0, 0.985))
                out.append(RoofFacet(len(out) + 1, f.storey_id, verts, f.thickness, conf,
                                     "occupied_upper_cells_robust_plane"))
                used.append(poly)
                if len(out) >= 12:
                    return out
    return out


def reconstruct_bim(request: dict):
    # v6.3 is the stable physical-object starting point. v7 then adds a conservative
    # consensus layer instead of inheriting the aggressive v6.5 candidate promotion.
    model = v63.reconstruct_bim(request)
    raw = base.load_xyz(request["input"], int(request.get("max_points", 2_000_000)))
    raster = float(np.clip(float(request.get("raster_cell_m", 0.05)), 0.04, 0.10))
    pts = base.voxel_subsample(raw, max(0.03, min(0.055, raster)))
    registry = load_registry()

    fp = v61._LAST_FOOTPRINT
    if fp is not None and fp.geom_type == "Polygon" and model.storeys:
        support_xy, _, _, cell = v6._facade_columns(pts, raster)
        reg = v65._microcorner_clean(v63.regularize_facade_footprint(fp, support_xy, cell), cell)
        model.walls = consensus_wall_graph(model, pts, reg)
        model.spaces = v63.spaces_from_partition_graph(reg, model.walls, model.storeys[0])
        raw_roofs = v63.fit_roof_facets(pts, reg, model.storeys[0], raster)
        model.roofs = fit_roofs_from_supported_cells(pts, raw_roofs, reg, model.storeys[0], raster)
        raw_openings = v63.detect_openings(pts, model.walls, raster)
        filtered = v65.filter_openings(pts, model.walls, raw_openings, raster)
        model.openings, abacus = refine_openings_with_abacus(filtered, model.walls, registry)
        model.stairs = v65.detect_stairs_multilevel(pts, reg, model.storeys[0], raster)
    else:
        # Indoor benchmark scenes still receive the object-knowledge refinement without
        # forcing an exterior-building shell interpretation.
        raw_openings = list(getattr(model, "openings", []))
        filtered = v65.filter_openings(pts, model.walls, raw_openings, raster) if raw_openings else []
        model.openings, abacus = refine_openings_with_abacus(filtered, model.walls, registry)
        model.stairs = list(getattr(model, "stairs", []))

    model.object_abacus = object_abacus_payload(abacus)
    model.standards_alignment = standards_alignment_payload(registry)
    s = model.summary
    s.update({
        "version": "7.0-paper-core-object-knowledge",
        "wall_reconstruction": "paper_resf_fuzzy_physical_instances_plus_strict_consensus_recovery",
        "space_reconstruction": "consensus_partition_graph_polygonization",
        "roof_reconstruction": "occupied_upper_cell_components_no_convex_gap_bridging",
        "opening_reconstruction": "host_wall_scan_evidence_plus_scan_derived_object_abacus_soft_standards_prior",
        "object_knowledge": "ISO_data_dictionary_context_plus_DM236_soft_accessibility_flags",
        "standards_geometry_policy": "soft_prior_only_never_invent_geometry",
        "paper_frozen_candidate_plane_spacing_m": 0.01,
        "paper_frozen_projected_support_grid_m": 0.05,
        "paper_frozen_fuzzy_threshold": 0.50,
        "paper_frozen_topology_threshold": 0.48,
        "walls": len(model.walls), "spaces": len(model.spaces),
        "roof_facets": len(getattr(model, "roofs", [])), "stairs": len(getattr(model, "stairs", [])),
        "doors": sum(o.kind == "Door" for o in model.openings),
        "windows": sum(o.kind == "Window" for o in model.openings),
        "opening_types": len(abacus),
        "reference_bim_used_at_runtime": False,
    })
    warnings = s.setdefault("warnings", [])
    if not abacus and model.openings:
        warnings.append("Openings were detected, but no repeated scan-derived door/window type cluster was found; unique measured dimensions were preserved")
    if not getattr(model, "roofs", []) and fp is not None:
        warnings.append("No roof facet passed v7 occupied-cell support checks; no unsupported roof geometry was invented")
    if len(model.spaces) <= 1:
        warnings.append("Room topology remains incomplete: only one closed consensus space was recovered")
    return model
