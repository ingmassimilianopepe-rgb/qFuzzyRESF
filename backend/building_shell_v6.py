"""Fuzzy-RESF-BIM v6: exterior-aware building-envelope reconstruction.

The v5 room graph is appropriate for indoor scans with a persistent ceiling.  A complete
historic-building scan is different: sloped roofs, terrain, eaves, porches and exterior
facades break storey/ceiling assumptions and can split one physical envelope into unrelated
upper/lower wall sets.  This module auto-detects that case and reconstructs the *building
shell* from vertically persistent facade columns before adding internal partitions.

Runtime reconstruction remains reference-free.  Previously generated IFCs may be used only
for offline evaluation/calibration; they are never read by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import math

import numpy as np
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.ops import polygonize, unary_union

import bim_reconstruction as base
import architectural_graph_v5 as core
import architectural_graph_v5_1 as indoor
from topology_completion import detect_openings, geometric_feedback


@dataclass
class RoofFacet:
    id: int
    storey_id: int
    vertices: list[list[float]]
    thickness: float
    confidence: float
    source: str = "upper_envelope_ransac"


def _grid_columns(pts: np.ndarray, cell: float, zcell: float):
    """Return sparse XY columns with unique occupied Z-bin statistics."""
    if len(pts) == 0:
        return None
    xy0 = pts[:, :2].min(axis=0)
    z0 = float(pts[:, 2].min())
    ix = np.floor((pts[:, 0] - xy0[0]) / cell).astype(np.int64)
    iy = np.floor((pts[:, 1] - xy0[1]) / cell).astype(np.int64)
    iz = np.floor((pts[:, 2] - z0) / zcell).astype(np.int64)
    ny = int(iy.max()) + 1
    key_xy = ix * max(ny, 1) + iy
    triples = np.unique(np.column_stack((key_xy, iz)), axis=0)
    if len(triples) == 0:
        return None
    keys, inv = np.unique(triples[:, 0], return_inverse=True)
    count = np.bincount(inv, minlength=len(keys)).astype(float)
    zmin_i = np.full(len(keys), np.iinfo(np.int64).max, dtype=np.int64)
    zmax_i = np.full(len(keys), -1, dtype=np.int64)
    np.minimum.at(zmin_i, inv, triples[:, 1])
    np.maximum.at(zmax_i, inv, triples[:, 1])
    xs = keys // max(ny, 1)
    ys = keys % max(ny, 1)
    xy = xy0 + np.column_stack((xs + 0.5, ys + 0.5)) * cell
    zmin = z0 + zmin_i * zcell
    zmax = z0 + (zmax_i + 1) * zcell
    return xy, count, zmin, zmax, zmax - zmin


def classify_scene(pts: np.ndarray, raster: float) -> tuple[str, dict]:
    """Distinguish a sloped-roof whole-building scan from an indoor room cloud."""
    z = pts[:, 2]
    q02, q98 = map(float, np.quantile(z, [0.02, 0.98]))
    height = max(q98 - q02, 1e-6)
    cell = float(np.clip(max(0.12, 2.5 * raster), 0.12, 0.22))
    cols = _grid_columns(pts, cell, max(0.12, height / 45.0))
    if cols is None:
        return "indoor_room", {"height": height, "roof_spread": 0.0}
    _, count, zmin, zmax, span = cols
    upper = zmax[zmax >= q02 + 0.58 * height]
    roof_spread = float(np.quantile(upper, 0.90) - np.quantile(upper, 0.10)) if len(upper) >= 20 else 0.0
    tall_ratio = float(np.mean(span >= max(1.5, 0.32 * height))) if len(span) else 0.0
    # A flat indoor ceiling has little variation in the upper envelope.  Historic sloped
    # roofs produce broad, spatially distributed upper-envelope variation.
    exterior = height >= 3.0 and roof_spread >= 0.45 and len(upper) >= 40 and tall_ratio >= 0.01
    return ("exterior_building_shell" if exterior else "indoor_room"), {
        "height": height, "roof_spread": roof_spread, "tall_column_ratio": tall_ratio,
        "q02": q02, "q98": q98,
    }


def _facade_columns(pts: np.ndarray, raster: float):
    z = pts[:, 2]
    q01, q99 = map(float, np.quantile(z, [0.01, 0.99]))
    h = max(q99 - q01, 1.0)
    cell = float(np.clip(max(0.07, 1.6 * raster), 0.07, 0.12))
    zcell = float(np.clip(h / 38.0, 0.10, 0.18))
    cols = _grid_columns(pts[(z >= q01 - 0.05) & (z <= q99 + 0.05)], cell, zcell)
    if cols is None:
        return np.empty((0, 2)), np.empty(0), np.empty(0), cell
    xy, count, zmin, zmax, span = cols
    expected_bins = np.maximum(span / zcell, 1.0)
    continuity = count / expected_bins
    tall = span >= max(1.55, 0.28 * h)
    tall &= count >= np.maximum(6.0, 0.48 * expected_bins)
    tall &= continuity >= 0.42
    return xy[tall], zmin[tall], zmax[tall], cell


def _outer_footprint(xy: np.ndarray, cell: float) -> Polygon | None:
    if len(xy) < 20:
        return None
    mp = MultiPoint(xy.tolist())
    poly = None
    if getattr(base, "concave_hull", None) is not None:
        for ratio in (0.10, 0.16, 0.24, 0.34):
            try:
                g = base.concave_hull(mp, ratio=ratio, allow_holes=False)
                if g.geom_type == "Polygon" and g.area > 2.0:
                    poly = g
                    break
            except Exception:
                pass
    if poly is None:
        g = mp.convex_hull
        poly = g if g.geom_type == "Polygon" else None
    if poly is None:
        return None
    # Simplify progressively but preserve local non-Manhattan/historic geometry.  The target
    # is a manageable number of physical wall segments, not a global orthogonal template.
    tol = max(0.09, 1.35 * cell)
    out = poly.simplify(tol, preserve_topology=True)
    for _ in range(4):
        if out.geom_type != "Polygon":
            break
        n = max(0, len(list(out.exterior.coords)) - 1)
        if n <= 36:
            break
        tol *= 1.35
        out = poly.simplify(tol, preserve_topology=True)
    return out if out.geom_type == "Polygon" and out.area > 2.0 else None


def _segment_angle(a: np.ndarray, b: np.ndarray) -> float:
    v = b - a
    # Wall normal angle convention used by RESF.
    return float(math.degrees(math.atan2(-v[0], v[1])) % 180.0)


def _local_wall_limits(pts: np.ndarray, a: np.ndarray, b: np.ndarray,
                       global_z0: float, global_eave: float, thickness: float) -> tuple[float, float, float]:
    v = b - a
    L = float(np.linalg.norm(v))
    if L < 1e-6:
        return global_z0, global_eave, 0.0
    t = v / L
    n = np.asarray([-t[1], t[0]])
    rel = pts[:, :2] - a
    along = rel @ t
    normal = np.abs(rel @ n)
    m = (along >= -0.15) & (along <= L + 0.15) & (normal <= max(0.28, 0.8 * thickness + 0.12))
    zz = pts[m, 2]
    if len(zz) < 40:
        return global_z0, global_eave, 0.35
    lo = float(np.quantile(zz, 0.03))
    hi = float(np.quantile(zz, 0.90))
    lo = float(np.clip(lo, global_z0 - 0.25, global_z0 + 0.35))
    hi = float(np.clip(hi, global_eave - 0.55, global_eave + 0.75))
    if hi - lo < 1.6:
        hi = global_eave
    support = float(np.clip(len(zz) / max(250.0, 55.0 * L), 0.20, 1.0))
    return lo, hi, support


def _wall_evidence(cands: list[base.RESFCandidate], theta: float,
                   a: np.ndarray, b: np.ndarray) -> dict:
    th = math.radians(theta)
    n = np.asarray([math.cos(th), math.sin(th)])
    tv = np.asarray([-math.sin(th), math.cos(th)])
    d = 0.5 * float(a @ n + b @ n)
    t0, t1 = sorted((float(a @ tv), float(b @ tv)))
    return core._resf_for_wall(cands, theta, d, t0, t1)


def walls_from_footprint(pts: np.ndarray, footprint: Polygon, storey: base.Storey,
                         cands: list[base.RESFCandidate], thickness: float) -> list[base.Wall]:
    coords = np.asarray(list(footprint.exterior.coords), dtype=float)
    walls: list[base.Wall] = []
    for a, b in zip(coords[:-1], coords[1:]):
        L = float(np.linalg.norm(b - a))
        if L < 0.45:
            continue
        theta = _segment_angle(a, b)
        z0, z1, observed = _local_wall_limits(pts, a, b, storey.z0, storey.z1, thickness)
        ev = _wall_evidence(cands, theta, a, b)
        conf = float(np.clip(0.52 * observed + 0.30 * ev["confidence"] + 0.18 * min(1.0, L / 2.0), 0.0, 1.0))
        state = "Permanent" if observed >= 0.38 else "OccludedPermanent"
        walls.append(base.Wall(
            0, storey.id, float(a[0]), float(a[1]), float(b[0]), float(b[1]), z0, z1,
            float(np.clip(thickness, 0.16, 0.55)), theta, conf, observed, 1.0,
            "closed_facade_envelope", state, ev["A"], ev["EH"], max(ev["EV"], observed),
            ev["C"], ev["N"], ev["O"], ev["G"]
        ))
    for i, w in enumerate(walls, 1):
        w.id = i
    return walls


def _interior_partitions(pts: np.ndarray, footprint: Polygon, storey: base.Storey,
                         cands: list[base.RESFCandidate], raster: float, max_orient: int) -> list[base.Wall]:
    cells = core.build_structural_cells(pts, storey, raster)
    orientations = base.detect_orientations(pts, 2.0, min(max_orient, 10))
    tracks = []
    for theta in orientations:
        tracks.extend(core.extract_face_tracks(cells, theta))
    tracks = core.suppress_face_duplicates(tracks)
    walls = core.pair_tracks_to_walls(tracks, storey, cands)
    walls = core.merge_collinear(walls, 1.15)
    walls = core.deduplicate_physical_walls(walls)
    inner = footprint.buffer(-0.22)
    boundary = footprint.boundary
    out = []
    for w in walls:
        line = LineString([(w.x1, w.y1), (w.x2, w.y2)])
        mid = line.interpolate(0.5, normalized=True)
        if inner.is_empty or not inner.contains(mid):
            continue
        if boundary.distance(mid) <= 0.28:
            continue
        # For interior partitions require either two observed faces or very strong full-height evidence.
        paired = w.source == "paired_height_persistent_faces"
        strong = w.confidence >= 0.76 and w.resf_EV >= 0.72 and line.length >= 1.4
        if not (paired or strong):
            continue
        out.append(w)
    out = core.merge_collinear(out, 1.35)
    out = core.deduplicate_physical_walls(out)
    return out


def _roof_grid(pts: np.ndarray, eave: float, cell: float):
    p = pts[pts[:, 2] >= eave - 0.25]
    if len(p) < 100:
        return np.empty((0, 3))
    origin = p[:, :2].min(axis=0)
    ix = np.floor((p[:, 0] - origin[0]) / cell).astype(np.int64)
    iy = np.floor((p[:, 1] - origin[1]) / cell).astype(np.int64)
    ny = int(iy.max()) + 1
    key = ix * max(ny, 1) + iy
    order = np.argsort(key, kind="mergesort")
    ks = key[order]
    zz = p[order, 2]
    starts = np.r_[0, np.flatnonzero(ks[1:] != ks[:-1]) + 1]
    keys = ks[starts]
    zmax = np.maximum.reduceat(zz, starts)
    xs = keys // max(ny, 1)
    ys = keys % max(ny, 1)
    xy = origin + np.column_stack((xs + 0.5, ys + 0.5)) * cell
    return np.column_stack((xy, zmax))


def fit_roof_facets(pts: np.ndarray, footprint: Polygon, storey: base.Storey,
                    raster: float, max_planes: int = 6) -> list[RoofFacet]:
    cell = float(np.clip(max(0.14, 2.8 * raster), 0.14, 0.24))
    xyz = _roof_grid(pts, storey.z1, cell)
    if len(xyz) < 80:
        return []
    # Restrict to the footprint plus eaves.  A Python loop is acceptable here because the
    # raster has already reduced millions of scan points to a few thousand cells.
    shell = footprint.buffer(0.65)
    keep = np.asarray([shell.contains(Point(float(x), float(y))) or shell.touches(Point(float(x), float(y)))
                       for x, y in xyz[:, :2]], dtype=bool)
    xyz = xyz[keep]
    if len(xyz) < 60:
        return []
    if len(xyz) > 12000:
        idx = np.linspace(0, len(xyz) - 1, 12000, dtype=int)
        xyz = xyz[idx]
    rng = np.random.default_rng(23)
    remaining = np.arange(len(xyz))
    facets: list[RoofFacet] = []
    tol = max(0.10, 0.65 * cell)
    for _ in range(max_planes):
        if len(remaining) < 50:
            break
        best = None
        best_in = None
        ptsr = xyz[remaining]
        for _trial in range(120):
            if len(ptsr) < 3:
                break
            ii = rng.choice(len(ptsr), 3, replace=False)
            A = np.column_stack((ptsr[ii, 0], ptsr[ii, 1], np.ones(3)))
            try:
                abc = np.linalg.solve(A, ptsr[ii, 2])
            except np.linalg.LinAlgError:
                continue
            # Reject near-vertical planes: roof facets are z=f(x,y).
            slope = float(math.hypot(abc[0], abc[1]))
            if slope > 1.8:
                continue
            residual = np.abs(ptsr[:, 2] - (abc[0] * ptsr[:, 0] + abc[1] * ptsr[:, 1] + abc[2]))
            ins = residual <= tol
            score = int(ins.sum())
            if best_in is None or score > int(best_in.sum()):
                best, best_in = abc, ins
        if best is None or best_in is None:
            break
        nin = int(best_in.sum())
        if nin < max(45, int(0.08 * len(xyz))):
            break
        pin = ptsr[best_in]
        # Refit least-squares on all inliers.
        A = np.column_stack((pin[:, 0], pin[:, 1], np.ones(len(pin))))
        abc, *_ = np.linalg.lstsq(A, pin[:, 2], rcond=None)
        mp = MultiPoint(pin[:, :2].tolist())
        poly = None
        if getattr(base, "concave_hull", None) is not None:
            try:
                poly = base.concave_hull(mp, ratio=0.22, allow_holes=False)
            except Exception:
                poly = None
        if poly is None or poly.geom_type != "Polygon":
            poly = mp.convex_hull
        poly = poly.intersection(shell)
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly.geom_type == "Polygon" and poly.area >= 1.0:
            poly = poly.simplify(max(0.10, cell), preserve_topology=True)
            verts = []
            for x, y in list(poly.exterior.coords)[:-1]:
                z = float(abc[0] * x + abc[1] * y + abc[2])
                verts.append([float(x), float(y), z])
            if len(verts) >= 3:
                conf = float(np.clip(nin / max(1.0, len(ptsr)), 0.35, 0.98))
                facets.append(RoofFacet(len(facets) + 1, storey.id, verts, 0.16, conf))
        remaining = remaining[~best_in]
    return facets


def _estimate_shell(pts: np.ndarray, raster: float):
    xy, zmin, zmax, cell = _facade_columns(pts, raster)
    if len(xy) < 20:
        raise RuntimeError("Exterior mode selected but insufficient vertically persistent facade columns were found")
    footprint = _outer_footprint(xy, cell)
    if footprint is None:
        raise RuntimeError("Could not reconstruct a closed facade footprint from vertical persistence")
    base_z = float(np.median(zmin))
    eave = float(np.median(zmax))
    if eave - base_z < 1.8:
        q05, q72 = np.quantile(pts[:, 2], [0.05, 0.72])
        base_z, eave = float(q05), float(q72)
    return footprint, base_z, eave, cell, len(xy)


def reconstruct_exterior(request: dict) -> base.BIMModel:
    raw = base.load_xyz(request["input"], int(request.get("max_points", 2_000_000)))
    raster = float(np.clip(float(request.get("raster_cell_m", 0.05)), 0.04, 0.10))
    pts = base.voxel_subsample(raw, max(0.03, min(0.055, raster)))
    footprint, z0, eave, cell, ncols = _estimate_shell(pts, raster)
    storey = base.Storey(1, z0, eave, 0.88)

    # RESF remains the evidence layer, but its translated peaks no longer become wall geometry.
    shell_coords = np.asarray(list(footprint.exterior.coords), dtype=float)
    shell_angles = [_segment_angle(a, b) for a, b in zip(shell_coords[:-1], shell_coords[1:])
                    if np.linalg.norm(b - a) >= 0.45]
    # Cluster only very close local directions; do not force a Manhattan model.
    orientations: list[float] = []
    for a in sorted(shell_angles):
        if all(base._circular_distance(a, b) >= 5.0 for b in orientations):
            orientations.append(float(a))
    if len(orientations) < 2:
        orientations = base.detect_orientations(pts, float(request.get("angle_step_deg", 2.0)),
                                                int(request.get("max_orientation_families", 12)))
    band = pts[(pts[:, 2] >= z0 - 0.15) & (pts[:, 2] <= eave + 0.25)]
    cands = base.build_resf_candidates(
        band, storey, orientations[:18],
        max(0.008, float(request.get("plane_spacing_m", 0.01))),
        max(0.02, float(request.get("plane_tolerance_m", 0.03))), raster,
        bool(request.get("multi_peak", True))
    )
    base.enrich_topology(cands)
    threshold = float(request.get("confidence_threshold", 0.50))
    for c in cands:
        c.state, c.confidence = base.fuzzy_decision(c, threshold, True, True, True)
    base._apply_candidate_feedback(band, cands)

    # Estimate masonry thickness from paired persistent interior/exterior faces where possible.
    cells = core.build_structural_cells(band, storey, raster)
    tracks = []
    for theta in orientations[:12]:
        tracks.extend(core.extract_face_tracks(cells, theta))
    paired = core.pair_tracks_to_walls(core.suppress_face_duplicates(tracks), storey, cands)
    thicknesses = [w.thickness for w in paired if w.source == "paired_height_persistent_faces"]
    thickness = float(np.clip(np.median(thicknesses), 0.20, 0.48)) if thicknesses else 0.34

    shell_walls = walls_from_footprint(band, footprint, storey, cands, thickness)
    partitions = _interior_partitions(band, footprint, storey, cands, raster,
                                      int(request.get("max_orientation_families", 12)))
    walls = shell_walls + partitions
    walls = core.deduplicate_physical_walls(walls)
    if bool(request.get("geometric_feedback", True)):
        geometric_feedback(band, walls)
    # Never prune the closed facade ring for weak local evidence; it is an inferred/observed
    # architectural envelope.  Only prune weak interior partitions.
    shell_ids = {id(w) for w in shell_walls}
    kept = []
    for w in walls:
        if w.source == "closed_facade_envelope":
            kept.append(w)
        elif w.confidence >= 0.48 and w.geometric_feedback >= 0.08:
            kept.append(w)
    walls = kept
    for i, w in enumerate(walls, 1):
        w.id = i

    spaces = base.spaces_from_walls(walls, storey)
    # Always provide a physical floor footprint for a complete building shell.
    slabs = [base.Slab(1, 1, "FLOOR", [[float(x), float(y)] for x, y in list(footprint.exterior.coords)[:-1]],
                       z0, 0.18, 0.82)]
    roofs = fit_roof_facets(pts, footprint, storey, raster)
    openings = detect_openings(band, walls, raster)
    for i, o in enumerate(openings, 1):
        o.id = i

    duplicates = core._network_duplicate_count(walls)
    junction = core._junction_ratio(walls)
    warnings = []
    if not roofs:
        warnings.append("Sloped/exterior scene detected but no stable roof facets passed RANSAC; roof omitted")
    if duplicates:
        warnings.append(f"{duplicates} near-duplicate wall pairs remain")
    if len(shell_walls) > 40:
        warnings.append("Facade footprint remains over-segmented; review local wall simplification")
    if not openings:
        warnings.append("No facade door/window objects passed host-wall vacancy checks")

    summary = {
        "status": "success" if walls else "warning",
        "method": "Fuzzy-RESF-BIM",
        "version": "6.0-building-envelope-graph",
        "scene_mode": "exterior_building_shell",
        "input_points": int(len(raw)),
        "regularized_points": int(len(pts)),
        "facade_columns": int(ncols),
        "footprint_area": float(footprint.area),
        "storeys": 1,
        "resf_candidates": len(cands),
        "accepted_candidates": sum(c.state in {"Permanent", "OccludedPermanent"} for c in cands),
        "walls": len(walls),
        "facade_walls": sum(w.source == "closed_facade_envelope" for w in walls),
        "interior_partitions": sum(w.source != "closed_facade_envelope" for w in walls),
        "doors": sum(o.kind == "Door" for o in openings),
        "windows": sum(o.kind == "Window" for o in openings),
        "openings": len(openings),
        "spaces": len(spaces),
        "slabs": len(slabs),
        "roof_facets": len(roofs),
        "wall_duplicate_pairs": duplicates,
        "wall_junction_ratio": junction,
        "wall_reconstruction": "closed_vertical_persistence_facade_graph_plus_strong_partitions",
        "roof_reconstruction": "upper_envelope_multi_plane_ransac",
        "resf_role": "evidence_and_confidence_not_direct_wall_geometry",
        "observed_inferred_separation": True,
        "reference_bim_used_at_runtime": False,
        "exact_frozen_v1_reproduction": False,
        "warnings": warnings,
    }
    model = base.BIMModel([storey], walls, openings, spaces, slabs, cands, summary)
    model.roofs = roofs
    return model


def reconstruct_bim(request: dict) -> base.BIMModel:
    raw = base.load_xyz(request["input"], min(int(request.get("max_points", 2_000_000)), 450_000))
    mode, diag = classify_scene(raw, float(request.get("raster_cell_m", 0.05)))
    if mode == "indoor_room":
        model = indoor.reconstruct_bim(request)
        model.summary["scene_mode"] = "indoor_room"
        model.summary["scene_classifier"] = diag
        model.summary["version"] = "6.0-building-envelope-graph"
        model.summary["v6_path"] = "delegated_to_v5_1_indoor_graph"
        model.summary["reference_bim_used_at_runtime"] = False
        model.roofs = []
        return model
    model = reconstruct_exterior(request)
    model.summary["scene_classifier"] = diag
    return model
