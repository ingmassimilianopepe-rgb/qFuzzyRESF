"""Evidence-driven semantic objects following accepted RESF wall inference.

No catalogue can turn a missing surface into a door. Vacancy is a *hypothesis*:
independent semantic probabilities or measured rays must resolve its meaning.
All coordinates are in the same local metric frame as the accepted wall graph.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
import math
from typing import Any

import numpy as np
from shapely import intersects_xy
from shapely.geometry import LineString, Polygon, box
from shapely.ops import polygonize, unary_union

try:
    from . import bim_reconstruction as base
    from .resf_fuzzy import infer_opening
except ImportError:
    import bim_reconstruction as base
    from resf_fuzzy import infer_opening


def _setting(config: Any, name: str, default: Any) -> Any:
    return config.get(name, default) if isinstance(config, dict) else getattr(config, name, default)


def _components(mask: np.ndarray):
    """Four-connected raster components; no morphological bridging of gaps."""
    visited = np.zeros(mask.shape, dtype=bool)
    nx, nz = mask.shape
    for x, z in np.argwhere(mask):
        if visited[x, z]:
            continue
        q = deque([(int(x), int(z))])
        visited[x, z] = True
        cells = []
        while q:
            i, j = q.popleft()
            cells.append((i, j))
            for a, b in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if 0 <= a < nx and 0 <= b < nz and mask[a, b] and not visited[a, b]:
                    visited[a, b] = True
                    q.append((a, b))
        yield np.asarray(cells, dtype=np.int64)


def _rings(poly: Polygon):
    outer = [[float(x), float(y)] for x, y in list(poly.exterior.coords)[:-1]]
    holes = [[[float(x), float(y)] for x, y in list(r.coords)[:-1]] for r in poly.interiors]
    return outer, holes


def _probabilities(semantic_probs, n):
    out = {}
    for key, values in (semantic_probs or {}).items():
        arr = np.asarray(values, dtype=float)
        if arr.shape != (n,) or not np.isfinite(arr).all() or np.any((arr < 0) | (arr > 1)):
            raise ValueError(f"Semantic probabilities for {key!r} must be N finite values in [0,1]")
        out[str(key).lower()] = arr
    return out


def _catalog_matches(config, width, height, sill):
    """Return ranked family compatibility, with no authority over class labels.

    Each family may define width_m, height_m, sill_m as [min,max], a scalar,
    or a {min,max} mapping. Unknown family types remain eligible suggestions.
    Missing dimensions add no compatibility evidence.
    """
    catalog = _setting(config, "family_catalog", {}) or {}
    if not isinstance(catalog, dict):
        raise ValueError("family_catalog must be a parsed JSON object")
    families = catalog.get("families", [])
    if isinstance(families, dict):
        families = [dict(value, id=key) for key, value in families.items() if isinstance(value, dict)]
    if not isinstance(families, list):
        raise ValueError("family_catalog.families must be a list or mapping")
    matches = []
    for index, family in enumerate(families):
        if not isinstance(family, dict):
            continue
        terms = []
        for key, value in (("width_m", width), ("height_m", height), ("sill_m", sill)):
            spec = family.get(key, family.get("dimensions", {}).get(key))
            if spec is None:
                continue
            if isinstance(spec, dict):
                low, high = float(spec["min"]), float(spec["max"])
            elif isinstance(spec, (list, tuple)) and len(spec) == 2:
                low, high = map(float, spec)
            else:
                low = high = float(spec)
            if not (math.isfinite(low) and math.isfinite(high) and 0 <= low <= high):
                raise ValueError(f"Invalid catalogue dimension {key}")
            distance = max(low - value, value - high, 0.0)
            scale = max(0.05, 0.1 * max(high, low), (high - low) / 2)
            terms.append(math.exp(-0.5 * (distance / scale) ** 2))
        if terms:
            matches.append({"family_id": str(family.get("id", family.get("name", index))),
                            "class": str(family.get("class", family.get("kind", "Unknown"))),
                            "fit": float(np.prod(terms) ** (1 / len(terms)))})
    return sorted(matches, key=lambda item: item["fit"], reverse=True)[:5]


def _measured_free_grid(points, origins, p1, tangent, normal, wall, nx, nz, length, height):
    """Count measured sight lines traversing the full physical wall envelope.

    A point simply behind a wall provides no visibility evidence without its
    scanner origin. Tangential rays and returns within the wall are excluded.
    """
    grid = np.zeros((nx, nz), dtype=np.int64)
    if origins is None or len(points) == 0:
        return grid
    d_start = (origins[:, :2] - p1) @ normal
    d_end = (points[:, :2] - p1) @ normal
    margin = max(float(wall.thickness) / 2 + 0.025, 0.04)
    crossing = ((d_start > margin) & (d_end < -margin)) | ((d_start < -margin) & (d_end > margin))
    if not np.any(crossing):
        return grid
    a, b = origins[crossing], points[crossing]
    fraction = d_start[crossing] / (d_start[crossing] - d_end[crossing])
    hit = a + fraction[:, None] * (b - a)
    u = (hit[:, :2] - p1) @ tangent
    z = hit[:, 2] - wall.z0
    valid = (u >= 0) & (u < length) & (z >= 0) & (z < height)
    ix = np.floor(u[valid] / length * nx).astype(int)
    iz = np.floor(z[valid] / height * nz).astype(int)
    np.add.at(grid, (ix, iz), 1)
    return grid


def _openings(points, normals, walls, config, probabilities, origins):
    openings, decisions = [], []
    cell = max(0.02, float(_setting(config, "opening_cell_m", 0.10)))
    tolerance = max(0.005, float(_setting(config, "opening_plane_tolerance_m", 0.06)))
    for wall in walls:
        p1 = np.array([wall.x1, wall.y1], dtype=float)
        delta = np.array([wall.x2, wall.y2]) - p1
        length, height = float(np.linalg.norm(delta)), float(wall.z1 - wall.z0)
        if length < 0.6 or height < 0.8:
            continue
        tangent = delta / length
        normal = np.array([-tangent[1], tangent[0]])
        u = (points[:, :2] - p1) @ tangent
        distance = np.abs((points[:, :2] - p1) @ normal)
        z = points[:, 2] - wall.z0
        valid = (u >= 0) & (u <= length) & (z >= 0) & (z <= height)
        valid &= distance <= wall.thickness / 2 + tolerance
        # Floor/ceiling returns do not count as wall support. Unknown normals
        # cannot establish an opening's bounding frame.
        alignment = np.abs(normals[:, :2] @ normal)
        near = valid & (alignment >= 0.60)
        if np.count_nonzero(near) < 20:
            continue
        nx, nz = max(3, int(math.ceil(length / cell))), max(3, int(math.ceil(height / cell)))
        if nx * nz > int(_setting(config, "max_opening_raster_cells", 2_000_000)):
            decisions.append({"object": "Opening", "wall_id": wall.id, "state": "Uncertain",
                              "missing_evidence": ["opening raster exceeds configured resource limit"]})
            continue
        du, dz = length / nx, height / nz
        ix = np.minimum((u[near] / du).astype(int), nx - 1)
        iz = np.minimum((z[near] / dz).astype(int), nz - 1)
        counts = np.zeros((nx, nz), dtype=np.int64)
        np.add.at(counts, (ix, iz), 1)
        occupied = counts > 0
        # Independently classified closed leaves/panes can occupy the aperture;
        # they are not wall-face support, unlike unlabeled points.
        semantic_region = np.zeros_like(occupied)
        for name in ("door", "window"):
            if name in probabilities:
                selected = probabilities[name][near] >= 0.65
                semantic_region[ix[selected], iz[selected]] = True
        face_support = occupied & ~semantic_region
        free_grid = _measured_free_grid(points, origins, p1, tangent, normal, wall, nx, nz, length, height)
        for component in _components(~face_support):
            lower = component.min(axis=0)
            upper = component.max(axis=0) + 1
            x0, z0 = map(int, lower)
            x1, z1 = map(int, upper)
            # At least two jambs and a lintel must be observed. Floor-connected
            # vacancies are allowed; wall-edge/top vacancies are unbounded.
            if x0 == 0 or x1 == nx or z1 == nz:
                continue
            width, opening_height, sill = (x1 - x0) * du, (z1 - z0) * dz, z0 * dz
            if width < float(_setting(config, "min_opening_width_m", 0.40)):
                continue
            if opening_height < float(_setting(config, "min_opening_height_m", 0.45)):
                continue
            if width * opening_height > float(_setting(config, "opening_max_area_m2", 12.0)):
                continue
            rectangularity = len(component) / ((x1 - x0) * (z1 - z0))
            if rectangularity < 0.70:
                continue
            pu, pz = max(1, int(math.ceil(0.15 / du))), max(1, int(math.ceil(0.15 / dz)))
            strips = [face_support[max(0, x0 - pu):x0, z0:z1],
                      face_support[x1:min(nx, x1 + pu), z0:z1],
                      face_support[x0:x1, z1:min(nz, z1 + pz)]]
            if z0 > max(1, int(0.15 / dz)):
                strips.append(face_support[x0:x1, max(0, z0 - pz):z0])
            support = [float(strip.mean()) if strip.size else 0.0 for strip in strips]
            frame = min(support)
            if frame < float(_setting(config, "opening_min_frame_support", 0.25)):
                continue
            evidence = {"frame_support": frame, "sill_ratio": sill / height,
                        "rectangularity": rectangularity, "host_confidence": float(wall.confidence),
                        "vacancy": float((~occupied[x0:x1, z0:z1]).mean())}
            interior = valid & (u >= x0 * du) & (u < x1 * du) & (z >= z0 * dz) & (z < z1 * dz)
            local = valid & (u >= (x0 - pu) * du) & (u <= (x1 + pu) * du)
            local &= (z >= (z0 - pz) * dz) & (z <= (z1 + pz) * dz)
            # Leaf/pane labels directly observed inside the aperture have
            # priority over wall labels on its surrounding frame.
            semantic_mask = interior if np.count_nonzero(interior) >= 5 else local
            for name in ("door", "window"):
                if name in probabilities:
                    evidence["semantic_" + name] = float(probabilities[name][semantic_mask].mean()) if np.any(semantic_mask) else 0.0
            # An origin supplied elsewhere in the scan says nothing about the
            # visibility of this aperture. Only crossing rays establish free
            # space; no local crossing evidence remains unknown, not zero.
            crossed = free_grid[component[:, 0], component[:, 1]] > 0
            ray_cells = int(crossed.sum())
            if ray_cells:
                coverage = ray_cells / max(len(component), 1)
                target = max(0.01, float(_setting(config, "opening_ray_coverage_target", 0.15)))
                evidence["free_space"] = min(1.0, coverage / target, ray_cells / 3)
            matches = _catalog_matches(config, width, opening_height, sill)
            if matches:
                evidence["catalog_fit"] = matches[0]["fit"]
            result = infer_opening(evidence, config)
            if not isinstance(result, dict):
                result = asdict(result)
            decision = dict(result)
            decision.update({"object": "Opening", "wall_id": int(wall.id), "storey_id": int(wall.storey_id),
                             "evidence": evidence, "width_m": width, "height_m": opening_height,
                             "sill_m": sill, "offset_m": (x0 + x1) * du / 2,
                             "measured_crossing_ray_cells": ray_cells, "catalog_suggestions": matches,
                             "exported": False})
            state = str(result.get("state", "Uncertain"))
            # Defense in depth: neither dimensional prior nor vacancy can
            # manufacture a semantic label, even under a changed rule base.
            if state in ("Door", "Window") and evidence.get("semantic_" + state.lower(), 0.0) <= 0:
                state = "Uncertain"
                decision["state"] = state
            if state in ("OpenVoid", "Door", "Window"):
                opening = base.Opening(len(openings) + 1, wall.id, wall.storey_id,
                                       "Opening" if state == "OpenVoid" else state,
                                       (x0 + x1) * du / 2, width, sill, opening_height,
                                       float(result.get("confidence", 0.0)),
                                       "RESF_wall_local_occupancy_and_independent_evidence")
                opening.provenance = {"evidence": evidence, "inference": result,
                                      "geometry_source": "wall_local_raster_bounds",
                                      "geometry_resolution_m": [du, dz],
                                      "catalog_suggestions": matches}
                openings.append(opening)
                decision["exported"] = True
                decision["opening_id"] = opening.id
            decisions.append(decision)
    return openings, decisions


def _spaces(walls, storeys, config):
    spaces = []
    for storey in storeys:
        group = [w for w in walls if w.storey_id == storey.id]
        pairs = [(w, LineString([(w.x1, w.y1), (w.x2, w.y2)])) for w in group
                 if math.hypot(w.x2 - w.x1, w.y2 - w.y1) > 1e-6]
        lines = [line for _, line in pairs]
        if len(lines) < 3:
            continue
        # unary_union nodes real intersections before polygonization. No
        # artificial closing segment or convex envelope is introduced here.
        for poly in polygonize(unary_union(lines)):
            if poly.area < float(_setting(config, "min_space_area_m2", 1.0)):
                continue
            outer, holes = _rings(poly)
            boundary_walls = [w for w, line in pairs if line.intersection(poly.boundary).length > 0.05]
            confidence = min((w.confidence for w in boundary_walls), default=0.0)
            obj = base.Space(len(spaces) + 1, storey.id, f"Space_{storey.id}_{len(spaces)+1}",
                             outer, storey.z0, storey.z1, float(poly.area), float(confidence))
            obj.holes = holes
            obj.provenance = {"source": "polygonized_noded_accepted_wall_graph",
                              "boundary_wall_ids": [int(w.id) for w in boundary_walls],
                              "geometry_status": "inferred_centerline_space_boundary",
                              "semantic_status": "space_use_unknown"}
            spaces.append(obj)
    return spaces


def _occupied_polygons(xy, cell):
    """Exact union of occupied raster cells, retaining all components/holes."""
    cells = np.unique(np.floor(xy / cell).astype(np.int64), axis=0)
    rectangles = []
    # Merge contiguous row cells before polygon union: far fewer polygons on
    # dense floors, while disconnected patches and interior holes survive.
    for y in np.unique(cells[:, 1]):
        row = np.sort(cells[cells[:, 1] == y, 0])
        starts = np.r_[0, np.flatnonzero(np.diff(row) > 1) + 1]
        ends = np.r_[starts[1:], len(row)]
        for a, b in zip(starts, ends):
            rectangles.append(box(row[a] * cell, y * cell, (row[b - 1] + 1) * cell, (y + 1) * cell))
    merged = unary_union(rectangles)
    if merged.geom_type == "Polygon":
        return [merged]
    return [p for p in getattr(merged, "geoms", []) if p.geom_type == "Polygon"]


def _slabs(points, normals, storeys, config):
    out = []
    cell = max(0.02, float(_setting(config, "floor_cell_m", 0.15)))
    tolerance = max(0.005, float(_setting(config, "horizontal_tolerance_m", 0.10)))
    horizontal = np.abs(normals[:, 2]) >= float(_setting(config, "horizontal_normal_min", 0.90))
    for storey in storeys:
        for kind, level, thickness in (("Floor", storey.z0, float(_setting(config, "assumed_floor_thickness_m", 0.18))),
                                        ("Ceiling", storey.z1, float(_setting(config, "assumed_ceiling_thickness_m", 0.12)))):
            mask = horizontal & (np.abs(points[:, 2] - level) <= tolerance)
            band = points[mask]
            if len(band) < int(_setting(config, "min_horizontal_points", 20)):
                continue
            for poly in _occupied_polygons(band[:, :2], cell):
                if poly.area < float(_setting(config, "min_slab_area_m2", 0.50)):
                    continue
                outer, holes = _rings(poly)
                # A component's bounding box can contain another component
                # within a hole. Select its actual occupied cells, preserving
                # the independent surface elevation of each component.
                centers = (np.floor(band[:, :2] / cell) + 0.5) * cell
                on_component = intersects_xy(poly, centers[:, 0], centers[:, 1])
                local_z = band[on_component, 2]
                surface = float(np.median(local_z))
                residual = float(np.median(np.abs(local_z - surface)))
                confidence = float(np.clip(math.exp(-(residual / tolerance) ** 2) * storey.confidence, 0, 1))
                obj = base.Slab(len(out) + 1, storey.id, kind, outer, surface, thickness, confidence)
                obj.holes = holes
                obj.source = "horizontal_occupancy"
                obj.provenance = {"source": "observed_horizontal_occupancy", "grid_resolution_m": cell,
                                  "geometry_status": "observed_surface_inferred_thickness",
                                  "thickness_source": "assumed_config", "thickness_m": thickness,
                                  "surface_z_m": surface, "median_absolute_z_residual_m": residual,
                                  "unsupported_holes_preserved": len(holes),
                                  "observed_area_m2": float(poly.area)}
                out.append(obj)
    return out


def reconstruct_objects(points, normals, model, config, semantic_probs=None, scanner_origins=None):
    """Build supported openings, spaces and slabs with inspectable decisions.

    ``semantic_probs`` is a mapping of class names to N aligned probabilities;
    ``scanner_origins`` contains one measured ray origin per point. Neither is
    fabricated when unavailable. Door/window geometry is limited to rectangular
    apertures; return unknown hypotheses in ``decisions`` for review.
    """
    points = np.asarray(points, dtype=float)
    normals = np.asarray(normals, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or normals.shape != points.shape:
        raise ValueError("points and normals must be aligned N x 3 arrays")
    if not np.isfinite(points).all() or not np.isfinite(normals).all():
        raise ValueError("points and normals must contain finite values")
    norms = np.linalg.norm(normals, axis=1)
    normals = normals / np.maximum(norms[:, None], 1e-12)
    probabilities = _probabilities(semantic_probs, len(points))
    origins = None if scanner_origins is None else np.asarray(scanner_origins, dtype=float)
    if origins is not None and (origins.shape != points.shape or not np.isfinite(origins).all()):
        raise ValueError("scanner_origins must be finite N x 3 aligned measured origins")
    walls = [wall for wall in model.walls if str(wall.state).lower() in
             {"permanent", "occludedpermanent", "accepted", "confirmed"}]
    openings, decisions = _openings(points, normals, walls, config, probabilities, origins)
    spaces = _spaces(walls, model.storeys, config)
    slabs = _slabs(points, normals, model.storeys, config)
    return {"openings": openings, "spaces": spaces, "slabs": slabs, "decisions": decisions,
            "provenance": {"algorithm": "RESF semantic reconstruction with independent aperture evidence",
                           "semantic_input_classes": sorted(probabilities),
                           "scanner_origins_available": origins is not None,
                           "catalog_role": "soft dimensional prior; does not establish semantic class",
                           "unsupported_classes": ["Column", "Stair", "Roof", "CurtainWall"],
                           "slab_thickness": "assumed, not inferred from one observed surface",
                           "opening_confidence": "uncalibrated fuzzy score, not probability"}}
