"""Observed facade/roof plane patches. No room or storey invention.

Fixed expert parameters, deterministic normal-guided RANSAC and monotone
Sugeno support. Output is a partial exterior model, not a watertight building.
"""
import time
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import box, mapping
from shapely.ops import unary_union


def _parts(g):
    if g.geom_type == "Polygon":
        return [g]
    return [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]


def _patches(xy, min_area, max_cells):
    step = 0.15
    low = xy.min(0) - 2 * step
    ij = np.floor((xy - low) / step).astype(int)
    shape = ij.max(0) + 3
    if int(shape[0]) * int(shape[1]) > max_cells:
        raise ValueError("Surface raster too large: crop to one building.")
    mask = np.zeros(shape, bool)
    mask[ij[:, 0], ij[:, 1]] = True
    # Only bridge gaps at the 15 cm raster scale. Large holes remain holes;
    # their semantics (window/occlusion) are deliberately not assigned.
    closed = ndi.binary_closing(mask, structure=np.ones((3, 3)))
    labels, count = ndi.label(closed)
    sizes = np.bincount(labels.ravel())
    for label in range(1, count + 1):
        if sizes[label] * step**2 < min_area:
            continue
        component = labels == label
        rectangles = []
        for i in np.flatnonzero(component.any(1)):
            edges = np.flatnonzero(np.diff(np.r_[False, component[i], False]))
            for a, b in zip(edges[::2], edges[1::2]):
                rectangles.append(box(low[0] + i * step, low[1] + a * step,
                                      low[0] + (i + 1) * step, low[1] + b * step))
        for polygon in _parts(unary_union(rectangles).simplify(0.07, preserve_topology=True).buffer(0.002, join_style=2)):
            if polygon.area >= min_area and polygon.is_valid:
                yield polygon, float((component & mask).sum() / component.sum())


def infer_exterior(P, N, PL, A, profile="monotone", max_raster_cells=4_000_000,
                   progress=lambda percent, stage: None):
    if profile not in {"monotone", "geometry_only"}:
        raise ValueError("The legacy profile is only supported by the interior research pipeline.")
    started = time.perf_counter()
    rng = np.random.default_rng(48021)
    surfaces = []
    proposals = []
    # Local column top evidence, not a claimed semantic roof classifier.
    xy_origin = P[:, :2].min(0)
    columns = np.floor((P[:, :2] - xy_origin) / 0.30).astype(np.int64)
    width = int(columns[:, 1].max()) + 1
    keys = columns[:, 0] * width + columns[:, 1]
    _, inverse = np.unique(keys, return_inverse=True)
    top = np.full(int(inverse.max()) + 1, -np.inf)
    np.maximum.at(top, inverse, P[:, 2])
    near_top = P[:, 2] >= top[inverse] - 0.30
    upper_cut = float(np.quantile(P[:, 2], 0.20))
    for kind, max_planes in [("wall", 28), ("roof", 18)]:
        plane_cos = np.cos(np.radians(12 if kind == "wall" else 5))
        fit_distance = .06 if kind == "wall" else .035
        if kind == "wall":
            available = (abs(N[:, 2]) < 0.20) & (PL > 0.25)
        else:
            available = (abs(N[:, 2]) >= 0.60) & (PL > 0.25) & near_top
        for iteration in range(max_planes):
            ids = np.flatnonzero(available)
            if len(ids) < 300:
                break
            sample = rng.choice(ids, min(len(ids), 16000), replace=False)
            seeds = rng.choice(sample, min(len(sample), 96), replace=False)
            sn = N[seeds]
            if kind == "wall":
                sn = sn.copy()
                sn[:, 2] = 0
                sn /= np.linalg.norm(sn, axis=1)[:, None]
            offsets = np.einsum("ij,ij->i", P[seeds], sn)
            distances = abs(P[sample] @ sn.T - offsets)
            agreement = abs(N[sample] @ sn.T)
            counts = ((distances <= (.05 if kind == "wall" else .025)) & (agreement >= plane_cos)).sum(0)
            best = int(np.argmax(counts))
            if counts[best] < max(40, 300 * len(sample) / len(ids)):
                break
            normal = sn[best].copy()
            offset = offsets[best]
            for _ in range(2):
                residual = abs(P[ids] @ normal - offset)
                good = (residual <= fit_distance) & (abs(N[ids] @ normal) >= plane_cos)
                selected = ids[good]
                if len(selected) < 300:
                    break
                center = P[selected].mean(0)
                q = P[selected] - center
                if kind == "wall":
                    _, vectors = np.linalg.eigh(q[:, :2].T @ q[:, :2] / len(q))
                    normal = np.r_[vectors[:, 0], 0.]
                else:
                    weights = 1 / np.maximum(abs(P[selected] @ normal - offset), .008)**2
                    center = np.average(P[selected], axis=0, weights=weights)
                    q = P[selected] - center
                    _, vectors = np.linalg.eigh((q * weights[:, None]).T @ q / weights.sum())
                    normal = vectors[:, 0]
                offset = float(center @ normal)
            if len(selected) < 300:
                available[seeds[best]] = False
                continue
            # Recompute inliers after the final fit, not for its preceding plane.
            selected = ids[(abs(P[ids] @ normal - offset) <= fit_distance)
                           & (abs(N[ids] @ normal) >= plane_cos)]
            if len(selected) < 300:
                available[seeds[best]] = False
                continue
            available[selected] = False
            residual = abs(P[selected] @ normal - offset)
            if kind == "wall" and abs(normal[2]) > 0.20:
                continue
            if kind == "roof" and (abs(normal[2]) < 0.60
                                  or np.median(P[selected, 2]) < upper_cut):
                continue
            # Right-handed plane coordinates; signs do not imply measured
            # inner/outer faces because thickness is centred and assumed.
            if normal[2] < 0:
                normal = -normal
            if abs(normal[2]) < 0.99:
                u = np.cross([0., 0., 1.], normal)
                u /= np.linalg.norm(u)
            else:
                u = np.cross([0., 1., 0.], normal)
                u /= np.linalg.norm(u)
            v = np.cross(normal, u)
            center = P[selected].mean(0)
            xy = np.column_stack(((P[selected] - center) @ u,
                                  (P[selected] - center) @ v))
            min_area = 1.5 if kind == "wall" else 4.0
            for poly, coverage in _patches(xy, min_area, max_raster_cells):
                # Evidence must be evaluated on this connected patch, not on
                # another distant component of the fitted plane.
                from shapely import contains_xy
                inside = contains_xy(poly.buffer(0.08), xy[:, 0], xy[:, 1])
                patch_ids = selected[inside]
                if len(patch_ids) < 150:
                    continue
                p95 = float(np.quantile(residual[inside], 0.95))
                planarity = float(np.median(PL[patch_ids]))
                alignment = float(np.median(abs(N[patch_ids] @ normal)))
                ramp = lambda x, a, b: float(np.clip((x-a)/(b-a), 0, 1))
                memberships = [
                    ramp(planarity, .25, .65), 1-ramp(p95, .025, .07),
                    ramp(alignment, .90, .99), ramp(poly.area, 1.5, 12),
                    ramp(coverage, .40, .90)]
                score = float(np.dot(memberships, [.25, .30, .20, .10, .15]))
                accepted = p95 <= .06 and (profile == "geometry_only" or score >= .65)
                record = {"kind": kind, "area_m2": float(poly.area),
                          "support_points": len(patch_ids), "P95_m": p95,
                          "score": score, "accepted": bool(accepted)}
                proposals.append(record)
                if not accepted:
                    continue
                surfaces.append({
                    **record, "id": ("W" if kind == "wall" else "R") + f"{len(surfaces)+1:03d}",
                    "ifc_class": "IfcWall" if kind == "wall" else "IfcRoof",
                    "plane_origin": center.tolist(), "normal": normal.tolist(),
                    "basis_u": u.tolist(), "basis_v": v.tolist(),
                    "polygon": mapping(poly), "thickness_m": .20 if kind == "wall" else .15,
                    "thickness_status": "ASSUMED; centred on observed plane",
                    "inclination_from_horizontal_deg": float(np.degrees(np.arccos(np.clip(abs(normal[2]), 0, 1)))),
                    "coverage": coverage, "memberships_P_R_N_A_C": memberships,
                    "boundary_status": "Observed raster support; 15 cm grid, 2 mm outward regularization; gaps up to 30 cm may be bridged",
                    "classification_status": "Geometric candidate; manual semantic review required",
                })
            progress(min(73, (52 + iteration // 2) if kind == "wall" else (66 + iteration // 3)),
                     "Fitting wall planes and roof slopes")
    if not any(s["kind"] == "wall" for s in surfaces):
        raise ValueError("No supported planar wall. Check units, vertical Z and point density.")
    if not any(s["kind"] == "roof" for s in surfaces):
        progress(73, "No supported roof: partial wall model")
    return {
        "mode": "exterior", "method_version": "0.5", "profile": profile,
        "source_sha256": A["sha256"], "source_filename": A["source"],
        "coordinate_origin_m": A["coordinate_origin_m"],
        "reference_used": False, "spaces": [],
        "scope": "Partial observed exterior plane patches; no inferred rooms or storeys",
        "surfaces": surfaces, "proposals": proposals,
        "parameters": {"plane_distance_m": .06, "normal_angle_deg": 12,
                       "raster_m": .15, "contour_regularization_m": .002, "fuzzy_threshold": .65,
                       "seed": 48021, "max_wall_planes": 28, "max_roof_planes": 18,
                       "roof_height_quantile": .20, "upper_column_m": .30},
        "runtime_s": time.perf_counter() - started,
    }
