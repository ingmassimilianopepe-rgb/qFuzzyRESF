"""Fuzzy-RESF-BIM v6.2: component-aware robust historic-building envelope.

This revision fixes a remaining v6.1 failure: morphological closing could still bridge a
masonry facade to nearby porch posts, and a hollow buffered ring could be mistaken for the
building area.  v6.2 filters disconnected vertical-support components before envelope
closing, fills the masonry shell interior explicitly, and then reuses v6.1 roof/partition/
opening/column reconstruction.  Runtime remains reference-BIM free.
"""
from __future__ import annotations

import numpy as np
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union

import building_shell_v6_1 as v61


def _support_component_filter(xy: np.ndarray, cell: float) -> tuple[np.ndarray, dict]:
    """Keep facade-scale support while rejecting compact detached posts before closing."""
    if len(xy) == 0:
        return xy, {"components": 0, "kept_components": 0, "removed_support_cells": 0}
    radius = float(np.clip(3.8 * cell, 0.34, 0.52))
    comps = v61._components(xy, radius)
    if not comps:
        return xy, {"components": 0, "kept_components": 0, "removed_support_cells": 0}
    comps = sorted(comps, key=len, reverse=True)
    main = comps[0]
    main_geom = MultiPoint(xy[main].tolist()).buffer(max(0.12, 1.5 * cell))
    min_large = max(14, int(round(0.10 * len(main))))
    keep = [main]
    for c in comps[1:]:
        # Detached porch posts are compact and small.  Large facade pieces separated by a
        # doorway/occlusion are retained if they are close enough to the dominant envelope.
        if len(c) < min_large:
            continue
        cg = MultiPoint(xy[c].tolist()).buffer(max(0.12, 1.5 * cell))
        if main_geom.distance(cg) <= max(1.10, 8.0 * cell):
            keep.append(c)
            main_geom = unary_union([main_geom, cg])
    idx = np.unique(np.concatenate(keep))
    return xy[idx], {
        "components": len(comps),
        "kept_components": len(keep),
        "removed_support_cells": int(len(xy) - len(idx)),
        "dominant_component_cells": int(len(main)),
    }


def _regularized_footprint(xy: np.ndarray, cell: float) -> Polygon | None:
    if len(xy) < 20:
        return None
    support, _ = _support_component_filter(xy, cell)
    if len(support) < 20:
        return None
    buf = float(np.clip(1.75 * cell, 0.12, 0.17))
    close = float(np.clip(7.0 * cell, 0.50, 0.68))
    g = MultiPoint(support.tolist()).buffer(buf, quad_segs=1)
    g = g.buffer(close, join_style=2).buffer(-close, join_style=2)
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda q: q.area)
    if g.geom_type != "Polygon":
        return None
    # A facade ring is a boundary observation; its holes are not courtyards unless there is
    # independent topology to prove that.  Filling here produces the physical shell footprint
    # instead of the area of the buffered strips.
    g = Polygon(g.exterior)
    if g.area <= 2.0:
        return None
    q = g.buffer(-0.045, join_style=2).buffer(0.045, join_style=2)
    if q.geom_type == "MultiPolygon":
        q = max(q.geoms, key=lambda p: p.area)
    if q.geom_type == "Polygon" and q.area >= 0.90 * g.area:
        g = Polygon(q.exterior)
    area0 = g.area
    best = g
    tol = max(0.13, 1.8 * cell)
    for _ in range(8):
        q = g.simplify(tol, preserve_topology=True)
        if q.geom_type != "Polygon" or q.area < 0.92 * area0:
            break
        q = Polygon(q.exterior)
        best = q
        if len(q.exterior.coords) - 1 <= 22:
            break
        tol *= 1.17
    return best


def reconstruct_bim(request: dict):
    saved = v61._regularized_footprint
    v61._regularized_footprint = _regularized_footprint
    try:
        model = v61.reconstruct_bim(request)
    finally:
        v61._regularized_footprint = saved
    # Preserve the v6.1 object model but make the active regularisation auditable.
    model.summary["version"] = "6.2-component-aware-building-envelope"
    model.summary["component_aware_shell"] = True
    model.summary["wall_reconstruction"] = "component_filtered_morphological_facade_graph_plus_topology_pruned_partitions"
    model.summary["reference_bim_used_at_runtime"] = False
    return model
