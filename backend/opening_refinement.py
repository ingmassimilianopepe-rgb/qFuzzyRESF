"""Secondary object-level opening refinement for Fuzzy-RESF-BIM.

The main raster classifier is intentionally conservative.  This pass specifically handles
floor-connected doors that can be hidden in occupancy rasters by floor returns.  It uses
wall-local point-count deficits above the floor and requires dense jambs plus a detected
lintel before creating an IfcDoor candidate.
"""

from __future__ import annotations

import math
from pathlib import Path
import numpy as np

from bim_reconstruction import Opening, load_xyz


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    out = []
    i = 0
    while i < len(mask):
        if not mask[i]:
            i += 1
            continue
        j = i + 1
        while j < len(mask) and mask[j]:
            j += 1
        out.append((i, j))
        i = j
    return out


def refine_floor_connected_doors(input_path: str | Path, model, raster: float = 0.06) -> list[Opening]:
    pts = load_xyz(input_path, 1_200_000)
    xy, z = pts[:, :2], pts[:, 2]
    additions: list[Opening] = []
    next_id = max([o.id for o in model.openings], default=0) + 1

    for w in model.walls:
        p1 = np.array([w.x1, w.y1], dtype=float)
        p2 = np.array([w.x2, w.y2], dtype=float)
        v = p2 - p1
        length = float(np.linalg.norm(v))
        height = float(w.z1 - w.z0)
        if length < 1.5 or height < 1.9:
            continue
        t = v / length
        n = np.array([-t[1], t[0]])
        rel = xy - p1
        along = rel @ t
        normal = rel @ n
        near = ((along >= 0) & (along <= length) &
                (np.abs(normal) <= max(0.14, 0.80 * float(w.thickness))) &
                (z >= w.z0 - 0.04) & (z <= w.z1 + 0.04))
        if near.sum() < 150:
            continue

        aa = along[near]
        zz = z[near] - w.z0
        cell = float(np.clip(max(0.065, raster * 2.5), 0.065, 0.11))
        nx = int(np.clip(math.ceil(length / cell), 16, 650))
        edges = np.linspace(0.0, length, nx + 1)

        # Ignore floor returns and the lintel zone: a door is a sustained deficit from
        # ~25 cm above floor to typical door-head height.
        structural = (zz >= 0.25) & (zz <= min(2.15, height - 0.20))
        counts, _ = np.histogram(aa[structural], bins=edges)
        if not np.any(counts > 0):
            continue
        kernel = np.array([0.25, 0.50, 0.25])
        smooth = np.convolve(counts.astype(float), kernel, mode="same")
        reference = float(np.quantile(smooth[smooth > 0], 0.60))
        if reference <= 0:
            continue
        low = smooth <= max(1.5, 0.22 * reference)
        # Do not interpret missing end caps as doors.
        margin = max(2, int(round(0.25 / max(length / nx, 1e-6))))
        low[:margin] = False
        low[-margin:] = False

        # Close one-bin breaks caused by random returns inside an otherwise empty doorway.
        for i in range(1, len(low) - 1):
            if not low[i] and low[i - 1] and low[i + 1]:
                low[i] = True

        for i0, i1 in _runs(low):
            width = (i1 - i0) * length / nx
            if not 0.60 <= width <= 2.20:
                continue
            flank = max(2, int(round(0.20 / max(length / nx, 1e-6))))
            left = smooth[max(0, i0-flank):i0]
            right = smooth[i1:min(nx, i1+flank)]
            if left.size == 0 or right.size == 0:
                continue
            side_support = min(float(left.mean()), float(right.mean())) / max(reference, 1e-6)
            if side_support < 0.42:
                continue

            x0, x1 = edges[i0], edges[i1]
            gap = (aa >= x0) & (aa <= x1)
            # Floor points are ignored. Returns appearing above 1.45 m inside the gap
            # represent the lintel/wall over the doorway and provide a door-head estimate.
            high = zz[gap & (zz >= 1.45)]
            if high.size < 8:
                continue
            door_h = float(np.quantile(high, 0.08))
            if not 1.65 <= door_h <= min(2.60, height * 0.96):
                continue

            center = 0.5 * (x0 + x1)
            # Skip a candidate already represented by the primary classifier.
            if any(o.wall_id == w.id and o.kind == "Door" and abs(o.offset - center) < 0.35 for o in model.openings):
                continue
            vacancy = float(np.clip(1.0 - smooth[i0:i1].mean() / max(reference, 1e-6), 0, 1))
            confidence = float(np.clip(0.42 * vacancy + 0.33 * min(side_support, 1.0) + 0.25 * w.confidence, 0, 1))
            if confidence < 0.45:
                continue
            additions.append(Opening(next_id, w.id, w.storey_id, "Door", center, width, 0.0, door_h, confidence))
            next_id += 1

    return additions
