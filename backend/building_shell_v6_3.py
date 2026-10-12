"""Fuzzy-RESF-BIM v6.3: room-aware regularized historic-building reconstruction.

This revision targets three residual failure modes observed on real historic-building scans:
(1) facade walls that inherit small hull oscillations and become visibly wavy,
(2) a gross interior shell without reliable individual IfcSpace reconstruction, and
(3) fragmented/over-extended sloped roof facets.

The RESF/fuzzy evidence layer is unchanged.  v6.3 regularizes the *physical object graph*
after evidence extraction: facade axes are fitted to locally dominant scan-supported line
families, rooms are polygonized from a noded partition graph inside the physical shell, and
roof facets are reconstructed from connected upper-envelope support with per-component
plane fitting.  No reference BIM is read at runtime.
"""
from __future__ import annotations

import math
import numpy as np
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.ops import polygonize, unary_union

import bim_reconstruction as base
import building_shell_v6 as v6
import building_shell_v6_1 as v61
import building_shell_v6_2 as v62
from topology_completion import detect_openings, geometric_feedback

RoofFacet = v6.RoofFacet


def _adiff(a: float, b: float) -> float:
    d = abs(float(a) - float(b)) % 180.0
    return min(d, 180.0 - d)


def _mean_angle180(angles, weights) -> float:
    a = np.radians(2.0 * np.asarray(angles, dtype=float))
    w = np.asarray(weights, dtype=float)
    z = np.sum(w * (np.cos(a) + 1j * np.sin(a)))
    if abs(z) < 1e-12:
        return float(np.asarray(angles, dtype=float)[0] % 180.0)
    return float((0.5 * math.degrees(math.atan2(z.imag, z.real))) % 180.0)


def _orientation_families(coords: np.ndarray) -> list[tuple[float, float]]:
    """Return locally dominant tangent-angle families without imposing Manhattan geometry."""
    seg = coords[1:] - coords[:-1]
    lengths = np.linalg.norm(seg, axis=1)
    angles = (np.degrees(np.arctan2(seg[:, 1], seg[:, 0])) % 180.0)
    order = np.argsort(-lengths)
    groups: list[list[int]] = []
    centres: list[float] = []
    for i in order:
        if lengths[i] < 0.35:
            continue
        best = None
        bestd = 999.0
        for gi, c in enumerate(centres):
            d = _adiff(angles[i], c)
            if d <= 8.0 and d < bestd:
                best, bestd = gi, d
        if best is None:
            groups.append([int(i)])
            centres.append(float(angles[i]))
        else:
            groups[best].append(int(i))
            centres[best] = _mean_angle180(angles[groups[best]], lengths[groups[best]])
    out = []
    perimeter = float(lengths.sum())
    for g, c in zip(groups, centres):
        weight = float(lengths[g].sum())
        # A family must represent an architectural run, not a small corner artefact.
        if weight >= max(1.20, 0.055 * perimeter):
            out.append((float(c), weight))
    return out


def _line_intersection(p: np.ndarray, u: np.ndarray, q: np.ndarray, v: np.ndarray):
    cross = float(u[0] * v[1] - u[1] * v[0])
    if abs(cross) < 1e-5:
        return None
    w = q - p
    t = float((w[0] * v[1] - w[1] * v[0]) / cross)
    return p + t * u


def regularize_facade_footprint(fp: Polygon, support_xy: np.ndarray, cell: float) -> Polygon:
    """Straighten physical facade axes while preserving non-Manhattan historic directions."""
    if fp is None or fp.geom_type != "Polygon":
        return fp
    tol = float(np.clip(2.3 * cell, 0.15, 0.26))
    g = fp.simplify(tol, preserve_topology=True)
    if g.geom_type != "Polygon":
        g = fp
    coords = np.asarray(g.exterior.coords, dtype=float)
    if len(coords) < 4:
        return fp
    families = _orientation_families(coords)
    if not families:
        return g

    lines = []
    originals = coords[:-1]
    nseg = len(originals)
    for i in range(nseg):
        a = coords[i]
        b = coords[i + 1]
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1e-8:
            lines.append((a.copy(), np.array([1.0, 0.0]), a, b))
            continue
        ang = float(math.degrees(math.atan2(d[1], d[0])) % 180.0)
        fam = min(families, key=lambda x: _adiff(ang, x[0]))
        use = fam[0] if _adiff(ang, fam[0]) <= 8.0 else ang
        r = math.radians(use)
        u = np.array([math.cos(r), math.sin(r)], dtype=float)
        n = np.array([-u[1], u[0]], dtype=float)
        mid = 0.5 * (a + b)

        # Robustly fit line offset to actual persistent facade support near this run.
        if support_xy is not None and len(support_xy) >= 10:
            rel = support_xy - mid
            along = rel @ u
            off = rel @ n
            band = max(0.28, 3.0 * cell)
            m = (along >= -0.25 * L) & (along <= 1.25 * L) & (np.abs(off) <= band)
            if int(m.sum()) >= 8:
                mid = mid + n * float(np.median(off[m]))
        lines.append((mid, u, a, b))

    verts = []
    for i in range(nseg):
        prev = lines[(i - 1) % nseg]
        cur = lines[i]
        q = _line_intersection(prev[0], prev[1], cur[0], cur[1])
        old = coords[i]
        # Reject unstable intersections at nearly parallel micro-corners.
        if q is None or not np.isfinite(q).all() or np.linalg.norm(q - old) > max(0.48, 4.0 * cell):
            q = old
        verts.append([float(q[0]), float(q[1])])

    out = Polygon(verts)
    if not out.is_valid:
        out = out.buffer(0)
    if out.geom_type != "Polygon" or out.area <= 2.0:
        return g
    ratio = out.area / max(fp.area, 1e-9)
    if not 0.86 <= ratio <= 1.14:
        return g
    # Do not let regularisation move the physical shell too far from observed support.
    if out.boundary.hausdorff_distance(fp.boundary) > max(0.55, 5.0 * cell):
        return g
    return out


def _extend_line(line: LineString, amount: float) -> LineString:
    c = np.asarray(line.coords, dtype=float)
    if len(c) < 2:
        return line
    a, b = c[0], c[-1]
    d = b - a
    L = float(np.linalg.norm(d))
    if L < 1e-8:
        return line
    u = d / L
    return LineString([a - amount * u, b + amount * u])


def spaces_from_partition_graph(footprint: Polygon, walls: list[base.Wall], storey: base.Storey) -> list[base.Space]:
    """Polygonize room cells from the regularized facade + validated internal wall graph."""
    if footprint is None or footprint.geom_type != "Polygon":
        return []
    ext_th = [w.thickness for w in walls if str(w.source).startswith("closed_facade_envelope")]
    t_ext = float(np.median(ext_th)) if ext_th else 0.34
    inner = footprint.buffer(-max(0.10, 0.52 * t_ext), join_style=2)
    if inner.geom_type == "MultiPolygon":
        inner = max(inner.geoms, key=lambda p: p.area)
    if inner.geom_type != "Polygon" or inner.area < 2.0:
        return []

    partitions = []
    for w in walls:
        if str(w.source).startswith("closed_facade_envelope"):
            continue
        L = math.hypot(w.x2 - w.x1, w.y2 - w.y1)
        # Rooms must be defined by strongly supported physical partitions, not every RESF trace.
        if L < 1.0 or w.confidence < 0.58:
            continue
        if getattr(w, "geometric_feedback", 0.0) < 0.14 and w.confidence < 0.76:
            continue
        ln = _extend_line(LineString([(w.x1, w.y1), (w.x2, w.y2)]), 0.48)
        q = ln.intersection(inner)
        if q.geom_type == "LineString" and q.length >= 0.90:
            partitions.append(q)
        elif q.geom_type == "MultiLineString":
            partitions.extend([x for x in q.geoms if x.length >= 0.90])

    if not partitions:
        return [base.Space(1, storey.id, "GrossInterior", [[float(x), float(y)] for x, y in list(inner.exterior.coords)[:-1]],
                           storey.z0, storey.z1, float(inner.area), 0.72)]

    # unary_union nodes all crossings; polygonize then recovers closed room cells.
    graph = unary_union([inner.boundary] + partitions)
    polys = []
    for p in polygonize(graph):
        if p.area < 2.2:
            continue
        rp = p.representative_point()
        if not inner.covers(rp):
            continue
        p = p.intersection(inner)
        if p.geom_type != "Polygon" or p.area < 2.2:
            continue
        rect = p.minimum_rotated_rectangle
        rc = np.asarray(rect.exterior.coords, dtype=float)
        sides = [float(np.linalg.norm(rc[i + 1] - rc[i])) for i in range(4)] if len(rc) >= 5 else [0.0]
        if sides and min(sides) < 0.72:
            continue
        compact = float(4.0 * math.pi * p.area / max(p.length * p.length, 1e-9))
        if compact < 0.035:
            continue
        polys.append(p)

    if not polys:
        return [base.Space(1, storey.id, "GrossInterior", [[float(x), float(y)] for x, y in list(inner.exterior.coords)[:-1]],
                           storey.z0, storey.z1, float(inner.area), 0.66)]

    # Avoid pathological over-segmentation: small sliver cells are not rooms.
    polys = sorted(polys, key=lambda p: p.area, reverse=True)
    cap = int(np.clip(round(inner.area / 6.0), 2, 16))
    polys = polys[:cap]
    rooms = []
    for i, p in enumerate(polys, 1):
        closure = float(np.clip(p.area / max(p.convex_hull.area, 1e-9), 0.0, 1.0))
        conf = float(np.clip(0.58 + 0.24 * closure + 0.18 * min(1.0, p.area / 10.0), 0.0, 0.96))
        rooms.append(base.Space(i, storey.id, f"Room_{i:02d}",
                                [[float(x), float(y)] for x, y in list(p.exterior.coords)[:-1]],
                                storey.z0, storey.z1, float(p.area), conf))
    return rooms


def _upper_grid(pts: np.ndarray, zmin: float, cell: float) -> np.ndarray:
    p = pts[pts[:, 2] >= zmin]
    if len(p) < 80:
        return np.empty((0, 3))
    o = p[:, :2].min(axis=0)
    ix = np.floor((p[:, 0] - o[0]) / cell).astype(np.int64)
    iy = np.floor((p[:, 1] - o[1]) / cell).astype(np.int64)
    ny = int(iy.max()) + 1
    key = ix * max(ny, 1) + iy
    order = np.argsort(key, kind="mergesort")
    ks = key[order]
    zz = p[order, 2]
    st = np.r_[0, np.flatnonzero(ks[1:] != ks[:-1]) + 1]
    en = np.r_[st[1:], len(ks)]
    keys = ks[st]
    zq = np.array([np.quantile(zz[a:b], 0.86) for a, b in zip(st, en)], dtype=float)
    xy = o + np.column_stack((keys // max(ny, 1) + 0.5, keys % max(ny, 1) + 0.5)) * cell
    return np.column_stack((xy, zq))


def _support_polygon(xy: np.ndarray, cell: float):
    if len(xy) < 8:
        return None
    # Union of scan-supported cells prevents plane polygons from jumping across unsupported gaps.
    g = MultiPoint(xy.tolist()).buffer(0.72 * cell, resolution=1)
    close = max(0.18, 1.45 * cell)
    g = g.buffer(close, join_style=2).buffer(-close, join_style=2)
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda p: p.area)
    if g.geom_type != "Polygon" or g.area < 0.7:
        return None
    g = g.simplify(max(0.07, 0.55 * cell), preserve_topology=True)
    if g.geom_type != "Polygon":
        return None
    # Rectangular roof patches are regularized when the data strongly supports that topology.
    r = g.minimum_rotated_rectangle
    if r.geom_type == "Polygon" and g.area / max(r.area, 1e-9) >= 0.84:
        g = r
    return g


def fit_roof_facets(pts: np.ndarray, footprint: Polygon, storey: base.Storey, raster: float,
                    max_planes: int = 8) -> list[RoofFacet]:
    """Connected-component, scan-supported roof plane reconstruction."""
    cell = float(np.clip(max(0.12, 2.4 * raster), 0.12, 0.20))
    top = float(np.quantile(pts[:, 2], 0.985))
    zstart = storey.z0 + max(1.75, 0.34 * (top - storey.z0))
    xyz = _upper_grid(pts, zstart, cell)
    if len(xyz) < 70:
        return []
    envelope = footprint.buffer(3.2)
    m = np.array([envelope.covers(Point(float(x), float(y))) for x, y in xyz[:, :2]], dtype=bool)
    xyz = xyz[m]
    if len(xyz) < 60:
        return []

    comps = v61._components(xyz[:, :2], max(1.75 * cell, 0.24))
    comps = [c for c in comps if len(c) >= max(28, int(0.018 * len(xyz)))]
    comps = sorted(comps, key=len, reverse=True)[:6]
    rng = np.random.default_rng(63)
    facets: list[RoofFacet] = []
    facet_polys = []

    for comp in comps:
        cloud = xyz[comp]
        remaining = np.arange(len(cloud))
        component_poly = _support_polygon(cloud[:, :2], cell)
        if component_poly is None:
            continue
        for _ in range(max_planes):
            if len(remaining) < 30:
                break
            pr = cloud[remaining]
            best_abc = None
            best_in = None
            tol = max(0.075, 0.50 * cell)
            for _trial in range(180):
                if len(pr) < 3:
                    break
                ii = rng.choice(len(pr), 3, replace=False)
                A = np.column_stack((pr[ii, 0], pr[ii, 1], np.ones(3)))
                try:
                    abc = np.linalg.solve(A, pr[ii, 2])
                except np.linalg.LinAlgError:
                    continue
                slope = math.hypot(float(abc[0]), float(abc[1]))
                if not 0.018 <= slope <= 1.65:
                    continue
                ins = np.abs(pr[:, 2] - (abc[0] * pr[:, 0] + abc[1] * pr[:, 1] + abc[2])) <= tol
                if best_in is None or int(ins.sum()) > int(best_in.sum()):
                    best_abc, best_in = abc, ins
            min_in = max(24, int(0.075 * len(cloud)))
            if best_abc is None or best_in is None or int(best_in.sum()) < min_in:
                break

            pin_all = pr[best_in]
            # A single mathematical plane can occur in disconnected places; export only connected support.
            in_comps = v61._components(pin_all[:, :2], max(1.85 * cell, 0.25))
            for ci in sorted(in_comps, key=len, reverse=True):
                if len(ci) < max(18, int(0.035 * len(cloud))):
                    continue
                pin = pin_all[ci]
                A = np.column_stack((pin[:, 0], pin[:, 1], np.ones(len(pin))))
                abc, *_ = np.linalg.lstsq(A, pin[:, 2], rcond=None)
                pred = A @ abc
                rms = float(np.sqrt(np.mean((pin[:, 2] - pred) ** 2)))
                slope = math.hypot(float(abc[0]), float(abc[1]))
                if not 0.018 <= slope <= 1.65 or rms > max(0.10, 0.62 * cell):
                    continue
                poly = _support_polygon(pin[:, :2], cell)
                if poly is None:
                    continue
                poly = poly.intersection(component_poly).intersection(envelope)
                if poly.geom_type == "MultiPolygon":
                    poly = max(poly.geoms, key=lambda p: p.area)
                if poly.geom_type != "Polygon" or poly.area < 0.9:
                    continue
                # Strong overlap means duplicate RANSAC plane; keep the earlier stronger facet.
                if any(poly.intersection(q).area / max(min(poly.area, q.area), 1e-9) > 0.35 for q in facet_polys):
                    continue
                zlo, zhi = np.quantile(pin[:, 2], [0.01, 0.99])
                verts = []
                for x, y in list(poly.exterior.coords)[:-1]:
                    z = float(abc[0] * x + abc[1] * y + abc[2])
                    verts.append([float(x), float(y), float(np.clip(z, zlo - 0.18, zhi + 0.18))])
                density = float(np.clip(len(pin) * cell * cell / max(poly.area, 1e-9), 0.0, 1.0))
                conf = float(np.clip(0.48 + 0.28 * density + 0.24 * (1.0 - rms / max(tol, 1e-9)), 0.40, 0.97))
                facets.append(RoofFacet(len(facets) + 1, storey.id, verts, 0.14, conf,
                                        "component_supported_upper_envelope_plane"))
                facet_polys.append(poly)
            remaining = remaining[~best_in]
            if len(facets) >= 10:
                break
        if len(facets) >= 10:
            break
    return facets


def _rebuild_facades(model, pts: np.ndarray, fp: Polygon, raster: float):
    storey = model.storeys[0]
    support_xy, _, _, cell = v6._facade_columns(pts, raster)
    reg = regularize_facade_footprint(fp, support_xy, cell)
    old_facades = [w for w in model.walls if str(w.source).startswith("closed_facade_envelope")]
    thickness = float(np.median([w.thickness for w in old_facades])) if old_facades else 0.34
    facades = v6.walls_from_footprint(pts, reg, storey, model.candidates, thickness)
    interiors = [w for w in model.walls if not str(w.source).startswith("closed_facade_envelope")]
    walls = facades + interiors
    geometric_feedback(pts, walls)
    for i, w in enumerate(walls, 1):
        w.id = i
    return reg, walls


def reconstruct_bim(request: dict):
    model = v62.reconstruct_bim(request)
    if model.summary.get("scene_mode") == "indoor_room":
        model.summary.update({"version": "6.3-room-roof-wall-regularized",
                              "v63_path": "delegated_indoor_graph",
                              "reference_bim_used_at_runtime": False})
        return model

    raw = base.load_xyz(request["input"], int(request.get("max_points", 2_000_000)))
    raster = float(np.clip(float(request.get("raster_cell_m", 0.05)), 0.04, 0.10))
    pts = base.voxel_subsample(raw, max(0.03, min(0.055, raster)))
    fp = v61._LAST_FOOTPRINT
    if fp is None or fp.geom_type != "Polygon":
        model.summary.setdefault("warnings", []).append("v6.3 could not access a closed facade footprint; v6.2 geometry retained")
        model.summary["version"] = "6.3-room-roof-wall-regularized"
        return model

    reg, model.walls = _rebuild_facades(model, pts, fp, raster)
    model.spaces = spaces_from_partition_graph(reg, model.walls, model.storeys[0])
    model.roofs = fit_roof_facets(pts, reg, model.storeys[0], raster)

    # Wall IDs changed after facade straightening, so openings must be re-hosted geometrically.
    model.openings = detect_openings(pts, model.walls, raster)
    v61._dedupe_openings(model)
    for i, o in enumerate(model.openings, 1):
        o.id = i

    s = model.summary
    s.update({
        "version": "6.3-room-roof-wall-regularized",
        "scene_mode": "exterior_building_shell",
        "wall_reconstruction": "scan_supported_orientation_family_facade_axes_plus_pruned_partitions",
        "space_reconstruction": "noded_partition_graph_polygonization_inside_regularized_shell",
        "roof_reconstruction": "connected_component_supported_upper_envelope_planes",
        "facade_regularization": "local_orientation_families_not_global_manhattan",
        "footprint_vertices": len(reg.exterior.coords) - 1,
        "walls": len(model.walls),
        "spaces": len(model.spaces),
        "roof_facets": len(model.roofs),
        "doors": sum(o.kind == "Door" for o in model.openings),
        "windows": sum(o.kind == "Window" for o in model.openings),
        "openings": len(model.openings),
        "reference_bim_used_at_runtime": False,
    })
    warnings = s.setdefault("warnings", [])
    if len(model.spaces) <= 1:
        warnings.append("Only one gross/closed interior space was reconstructed; internal wall evidence may still be incomplete")
    if not model.roofs:
        warnings.append("No sufficiently supported sloped roof facet passed v6.3 connected-support checks")
    return model
