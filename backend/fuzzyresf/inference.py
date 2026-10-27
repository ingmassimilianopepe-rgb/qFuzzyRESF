"""Fuzzy-RESF v0.5 pilot, ported from the six-case research workflow.
Numerical parameters are fixed. No reference model is read by this module.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import time
import numpy as np
from scipy import ndimage as ndi
from scipy.signal import find_peaks
from skimage.segmentation import watershed
from shapely.geometry import box, mapping, shape as geometry_shape
from shapely.ops import unary_union
from shapely import contains_xy


def infer(
    P,
    N,
    PL,
    A,
    profile="monotone",
    max_raster_cells=4_000_000,
    progress=lambda percent, stage: None,
):
    if profile not in {"monotone", "legacy", "geometry_only"}:
        raise ValueError("Unknown profile")
    start = time.perf_counter()
    from .levels import select_level
    selected_level = select_level(P, N, PL, A)
    zf = selected_level["floor_z"]
    zc = selected_level["ceiling_z"]
    H = selected_level["height_m"]
    horizontal = (abs(N[:, 2]) > 0.95) & (PL > 0.3)
    near = (
        (P[:, 2] > zf + 0.15)
        & (P[:, 2] < zc - 0.10)
        & (abs(N[:, 2]) < 0.2)
        & (PL > 0.3)
    )
    angles = (np.arctan2(N[near, 1], N[near, 0]) + np.pi / 4) % (np.pi / 2) - np.pi / 4
    dominant = np.angle(np.mean(np.exp(4j * angles))) / 4 if len(angles) else 0.
    residual = (angles - dominant + np.pi / 4) % (np.pi / 2) - np.pi / 4
    aligned = dominant + residual[abs(residual) < np.radians(10)]
    if len(aligned) < 120:
        raise ValueError(
            "Insufficient support for two dominant orthogonal wall directions."
        )
    angle = float(np.median(aligned))
    c, s = np.cos(angle), np.sin(angle)
    R = np.array([[c, s], [-s, c]])
    origin = P[:, :2].min(0)
    UV = (P[:, :2] - origin) @ R.T
    NN = N[:, :2] @ R.T
    Q = np.c_[UV, P[:, 2]]

    def up(x, a, b):
        return float(np.clip((x - a) / (b - a), 0, 1))

    def fuzzy(feat):
        v = up(feat["verticality"], 0.75, 0.95)
        p = up(feat["planarity"], 0.25, 0.65)
        c = up(feat["height_persistence"], 0.30, 0.75)
        g = 1 - up(feat["floor_gap"], 0.15, 0.70)
        l = up(feat["continuity"], 0.50, 0.90)
        w = np.array(
            [
                min(v, p, c, g),
                min(v, l, c),
                min(v, l, 1 - g, 1 - c),
                1 - v,
                1 - p,
                min(v, 1 - c, g),
                0.05,
            ]
        )
        legacy = float(
            w @ np.array([0.95, 0.85, 0.08, 0.05, 0.25, 0.45, 0.50]) / w.sum()
        )
        # Complete product-partition Sugeno rule base, singleton consequents equal to
        # weighted binary antecedents. This is exactly its multilinear simplification.
        revised = float(
            np.array([v, p, c, g, l]) @ np.array([0.20, 0.20, 0.25, 0.15, 0.20])
        )
        return revised, legacy, [v, p, c, g, l]

    def runs(mask):
        idx = np.flatnonzero(np.diff(np.r_[False, mask, False]))
        return list(zip(idx[::2], idx[1::2]))

    def raster_poly(mask, xy0, step):
        rectangles = []
        for i, row in enumerate(mask):
            for a, b in runs(row):
                rectangles.append(
                    box(
                        xy0[0] + i * step,
                        xy0[1] + a * step,
                        xy0[0] + (i + 1) * step,
                        xy0[1] + b * step,
                    )
                )
        return unary_union(rectangles).buffer(0)

    def polygons(g):
        return (
            [g]
            if g.geom_type == "Polygon"
            else [x for x in g.geoms if x.geom_type == "Polygon"]
        )

    # Main-level outer support uses horizontal ceiling samples, with a 0.4 m
    # raster closing and interior-hole fill. Infill is explicitly inferred.
    step = 0.10
    lo = UV.min(0) - 1
    shape = np.ceil((UV.max(0) - lo + 1) / step).astype(int)
    if int(shape[0]) * int(shape[1]) > max_raster_cells:
        raise ValueError(
            "Raster too large: crop the cloud or check units."
        )
    ceilpts = Q[horizontal & (abs(Q[:, 2] - zc) < 0.07), :2]
    # Ceiling finishes can have several offsets within one level. Combine their
    # support with the measured floor so omitted ceiling patches do not truncate
    # whole wings of the building or leave walls projecting beyond the slab.
    supportpts = Q[horizontal & ((abs(Q[:, 2] - zc) < .18) | (abs(Q[:, 2] - zf) < .10)), :2]
    ij = np.floor((supportpts - lo) / step).astype(int)
    mask = np.zeros(shape, bool)
    mask[ij[:, 0], ij[:, 1]] = True
    mask = ndi.binary_closing(mask, structure=np.ones((5, 5)), border_value=0)
    mask = ndi.binary_fill_holes(mask)
    labs, n = ndi.label(mask)
    sizes = np.bincount(labs.ravel())
    mask = np.isin(labs, np.flatnonzero(sizes >= 100))
    mask[labs == 0] = False
    env = raster_poly(mask, lo, step).simplify(0.12, preserve_topology=True)
    env = unary_union([p for p in polygons(env) if p.area > 5]).buffer(
        0.08, join_style=2
    )

    if env.is_empty:
        raise ValueError(
            "Insufficient ceiling footprint for room reconstruction."
        )
    progress(55, "Wall proposals and fuzzy support")
    proposals = []
    for axis in [0, 1]:
        other = 1 - axis
        good = near & (abs(NN[:, axis]) > np.cos(np.radians(12)))
        vals = Q[good, axis]
        if len(vals) < 120:
            continue
        edges = np.arange(vals.min() - 0.05, vals.max() + 0.075, 0.025)
        hist = np.histogram(vals, edges)[0]
        smooth = ndi.gaussian_filter1d(hist.astype(float), 0.8)
        pk, _ = find_peaks(
            smooth, distance=3, prominence=max(35, float(smooth.max() * 0.006))
        )
        for pi in pk:
            center = (edges[pi] + edges[pi + 1]) / 2
            ids = np.flatnonzero(good & (abs(Q[:, axis] - center) < 0.055))
            if len(ids) < 120:
                continue
            center = float(np.median(Q[ids, axis]))
            ids = np.flatnonzero(good & (abs(Q[:, axis] - center) < 0.045))
            if len(ids) < 120:
                continue
            along = Q[ids, other]
            bins = np.arange(along.min() - 0.05, along.max() + 0.15001, 0.10)
            cnt = np.histogram(along, bins)[0]
            support = cnt >= 3
            # Fill internal unsupported spans <= 1.2m for a potential doorway/occlusion.
            for aa, bb in runs(~support):
                if aa > 0 and bb < len(support) and (bb - aa) * 0.1 <= 1.2:
                    support[aa:bb] = True
            for aa, bb in runs(support):
                t0, t1 = bins[aa], bins[bb]
                sel = ids[(along >= t0) & (along <= t1)]
                if t1 - t0 < 0.8 or len(sel) < 120:
                    continue
                q = Q[sel]
                pos = float(np.median(q[:, axis]))
                res = abs(q[:, axis] - pos)
                zlo, zhi = np.quantile(q[:, 2], [0.02, 0.98])
                hc = np.histogram(
                    q[:, 2], np.linspace(zf + 0.05 * H, zc - 0.05 * H, 11)
                )[0]
                lc = np.histogram(q[:, other], np.arange(t0, t1 + 0.10001, 0.10))[0]
                feat = {
                    "verticality": float(np.median(1 - abs(N[sel, 2]))),
                    "planarity": float(np.median(PL[sel])),
                    "height_persistence": float(np.mean(hc >= 5)),
                    "floor_gap": float(max(0, zlo - zf)),
                    "continuity": float(np.mean(lc >= 3)),
                }
                score, legacy, m = fuzzy(feat)
                geometry = bool(np.quantile(res, 0.95) <= 0.05 and zhi - zlo >= 0.5 * H)
                accept = bool(
                    geometry
                    and (
                        profile == "geometry_only"
                        or (legacy if profile == "legacy" else score) >= 0.70
                    )
                )
                proposals.append(
                    {
                        "id": f"P{len(proposals) + 1:03d}",
                        "axis": axis,
                        "face": pos,
                        "t0": float(t0),
                        "t1": float(t1),
                        "n": len(q),
                        "zlo": float(zlo),
                        "zhi": float(zhi),
                        "P95_m": float(np.quantile(res, 0.95)),
                        "features": feat,
                        "membership_V_P_C_F_L": m,
                        "score": score,
                        "legacy_score": legacy,
                        "geometry_pass": geometry,
                        "accepted": accept,
                        "legacy_accepted": bool(geometry and legacy >= 0.70),
                    }
                )

    # Remove near-identical parallel peaks; then pair faces with compatible extents.
    accepted = sorted(
        [p.copy() for p in proposals if p["accepted"]], key=lambda p: -p["n"]
    )
    faces = []
    for p in accepted:
        if any(
            p["axis"] == w["axis"]
            and abs(p["face"] - w["face"]) < 0.065
            and max(0, min(p["t1"], w["t1"]) - max(p["t0"], w["t0"]))
            > 0.7 * min(p["t1"] - p["t0"], w["t1"] - w["t0"])
            for w in faces
        ):
            continue
        faces.append(p)
    if not faces:
        raise ValueError("No wall passes the geometric and fuzzy checks.")
    used = set()
    walls = []
    for i, p in enumerate(faces):
        if i in used:
            continue
        options = []
        for j, q in enumerate(faces):
            if j <= i or j in used or q["axis"] != p["axis"]:
                continue
            gap = abs(p["face"] - q["face"])
            ov = max(0, min(p["t1"], q["t1"]) - max(p["t0"], q["t0"]))
            if 0.075 <= gap <= 0.45 and ov > 0.65 * max(
                p["t1"] - p["t0"], q["t1"] - q["t0"]
            ):
                options.append((gap, j))
        used.add(i)
        if options:
            _, j = min(options)
            used.add(j)
            q = faces[j]
            position = (p["face"] + q["face"]) / 2
            thickness = abs(p["face"] - q["face"])
            t0 = min(p["t0"], q["t0"])
            t1 = max(p["t1"], q["t1"])
            sources = [p["id"], q["id"]]
            status = "PAIRED OBSERVED FACES"
        else:
            # Locate observed ceiling support on the two sides of the face; put inferred
            # thickness into the less observed side. If balanced, centre on the face.
            axis = p["axis"]
            other = 1 - axis
            sel = (ceilpts[:, other] > p["t0"]) & (ceilpts[:, other] < p["t1"])
            dd = ceilpts[sel, axis] - p["face"]
            left = np.sum((dd < -0.05) & (dd > -0.45))
            right = np.sum((dd > 0.05) & (dd < 0.45))
            sign = (
                -1 if right > 2 * max(left, 1) else 1 if left > 2 * max(right, 1) else 0
            )
            thickness = 0.12
            position = p["face"] + sign * thickness / 2
            t0 = p["t0"]
            t1 = p["t1"]
            sources = [p["id"]]
            status = "SINGLE FACE; 0.12m THICKNESS ASSUMED"
        walls.append(
            {
                "id": f"W{len(walls) + 1:03d}",
                "axis": p["axis"],
                "position": position,
                "t0": t0,
                "t1": t1,
                "thickness": thickness,
                "thickness_status": status,
                "score": p["score"],
                "proposal_ids": sources,
                "base": zf,
                "top": zc,
                "extent_status": "OBSERVED SUPPORT WITH GAPS <=1.2m BRIDGED",
            }
        )

    # Extend ends to the nearest perpendicular wall within 0.45m, using cloud
    # proposals only. Extensions remain tagged as inferred topology completion.
    for w in walls:
        old = [w["t0"], w["t1"]]
        for side in ["t0", "t1"]:
            candidates = [
                q["position"]
                for q in walls
                if q["axis"] != w["axis"]
                and q["t0"] - 0.45 <= w["position"] <= q["t1"] + 0.45
                and abs(q["position"] - w[side]) < 0.45
            ]
            if candidates:
                w[side] = min(candidates, key=lambda v: abs(v - w[side]))
        w["endpoint_extension_m"] = [w["t0"] - old[0], w["t1"] - old[1]]
    walls = [w for w in walls if w["t1"] - w["t0"] >= 0.2]
    if not walls:
        raise ValueError("Degenerate walls after topology completion.")
    progress(65, "Segmenting provisional spaces")

    def wallpoly(w):
        a, b = w["position"] - w["thickness"] / 2, w["position"] + w["thickness"] / 2
        return (
            box(a, w["t0"], b, w["t1"])
            if w["axis"] == 0
            else box(w["t0"], a, w["t1"], b)
        )

    wallpolys = [wallpoly(w) for w in walls]
    solid = unary_union(wallpolys)
    for w, p in zip(walls, wallpolys):
        w["polygon"] = mapping(p)

    from .topology import structural_envelope
    env, walls, envelope_status = structural_envelope(env, walls)
    wallpolys = [geometry_shape(w["polygon"]) for w in walls]
    solid = unary_union(wallpolys)

    # Candidate spaces are components of supported envelope minus closed wall solids.
    # Minor numerical closure only; all are labelled provisional in IFC.
    free = env.difference(solid.buffer(0.025, join_style=2))
    initial_regions = [
        p for p in polygons(free) if p.area >= 2 and not p.buffer(-0.35).is_empty
    ]
    # Marker-controlled watershed of wall-distance clearance, not a room-count target.
    # This partitions connected large regions; unsupported interfaces remain virtual.
    gx = lo[0] + (np.arange(shape[0]) + 0.5) * step
    gy = lo[1] + (np.arange(shape[1]) + 0.5) * step
    xx, yy = np.meshgrid(gx, gy, indexing="ij")
    free_mask = contains_xy(free, xx, yy)
    dist = ndi.distance_transform_edt(free_mask) * step
    maxima = (dist == ndi.maximum_filter(dist, size=41)) & (dist >= 1.0)
    ml, nm = ndi.label(maxima)
    centers = []
    for lab in range(1, nm + 1):
        ii = np.argwhere(ml == lab)
        ij = ii[np.argmax(dist[ml == lab])]
        centers.append(tuple(ij))
    # Greedy suppression of nearby equivalent clearance maxima, 3m physical spacing.
    seeds = []
    for ij in sorted(centers, key=lambda ij: -dist[ij]):
        if all(np.linalg.norm(np.array(ij) - np.array(jj)) * step >= 3 for jj in seeds):
            seeds.append(ij)
    # Guarantee at least one marker per connected free component >=2m2.
    fl, nfl = ndi.label(free_mask)
    for lab in range(1, nfl + 1):
        mm = fl == lab
        if mm.sum() * step * step < 2:
            continue
        if not any(fl[ij] == lab for ij in seeds):
            indices = np.argwhere(mm)
            seeds.append(tuple(indices[np.argmax(dist[mm])]))
    markers = np.zeros(shape, np.int32)
    for i, ij in enumerate(seeds):
        markers[ij] = i + 1
    ws = watershed(-dist, markers, mask=free_mask, connectivity=1)
    regions = []
    for lab in range(1, len(seeds) + 1):
        parts = polygons(raster_poly(ws == lab, lo, step).intersection(free))
        regions.extend(p for p in parts if p.area >= 2 and not p.buffer(-0.35).is_empty)
    regions = sorted(regions, key=lambda p: (p.centroid.y, p.centroid.x))
    spaces = []
    for i, p in enumerate(regions):
        # Simplification limits staircase artifacts of the support raster.
        # Keep exact raster partition to avoid simplification overlap across interfaces.
        p = p.simplify(0.000001, preserve_topology=True)
        spaces.append(
            {
                "id": f"SP{i + 1:03d}",
                "polygon": mapping(p),
                "area_m2": p.area,
                "base": zf,
                "top": zc,
                "status": "PROVISIONAL CLOUD-DERIVED SPACE; TOPOLOGY UNVALIDATED",
            }
        )

    if not spaces:
        raise ValueError("No closed space could be reconstructed.")
    result = {
        "version": "0.5",
        "profile": profile,
        "status": "PILOT CLOUD-ONLY RECONSTRUCTION; REVIEW REQUIRED",
        "source_sha256": A["sha256"],
        "source_filename": A["source"],
        "reference_used": False,
        "origin_xy": origin.tolist(),
        "rotation_world_to_uv": R.tolist(),
        "rotation_deg": float(np.degrees(angle)),
        "units": "METRES; input scale explicitly supplied",
        "meters_per_input_unit": A["meters_per_unit"],
        "coordinate_origin_m": A.get("coordinate_origin_m", [0, 0, 0]),
        "main_level": {
            "floor_z": zf,
            "ceiling_z": zc,
            "height_m": H,
            "pair_selection": selected_level,
            "floor_fit_residual_P95_m": float(
                np.quantile(
                    abs(P[horizontal & (abs(P[:, 2] - zf) < 0.08), 2] - zf), 0.95
                )
            ),
            "ceiling_fit_residual_P95_m": float(
                np.quantile(
                    abs(P[horizontal & (abs(P[:, 2] - zc) < 0.08), 2] - zc), 0.95
                )
            ),
        },
        "scope": "Main densely observed level; lower partial observations retained in diagnostics, not completed as rooms",
        "envelope": mapping(env),
        "envelope_status": envelope_status,
        "fuzzy": {
            "version": "complete product-partition zero-order Sugeno with monotone singleton consequents",
            "equivalent_score_weights_V_P_C_F_L": [0.20, 0.20, 0.25, 0.15, 0.20],
            "threshold": 0.70,
            "weights_status": "expert-defined pilot values, not trained or calibrated",
            "legacy_score_computed": True,
        },
        "parameters": {
            "voxel_m": 0.05,
            "axis_tolerance_deg": 12,
            "face_band_m": 0.045,
            "face_histogram_bin_m": 0.025,
            "longitudinal_bin_m": 0.10,
            "max_inferred_gap_m": 1.2,
            "min_segment_m": 0.8,
            "min_height_fraction": 0.5,
            "max_plane_P95_m": 0.05,
            "assumed_single_face_thickness_m": 0.12,
            "endpoint_snap_m": 0.45,
            "room_min_area_m2": 2,
            "room_erosion_test_m": 0.35,
            "space_wall_clearance_m": 0.025,
        },
        "proposals": proposals,
        "walls": walls,
        "spaces": spaces,
        "openings": [],
        "runtime_s": time.perf_counter() - start,
    }
    result["parameters"].update(
        {
            "watershed": "masked distance-transform marker-controlled watershed",
            "raster_m": step,
            "local_maximum_window_cells": 41,
            "seed_spacing_m": 3.0,
            "seed_min_clearance_m": 1.0,
            "initial_connected_regions": len(initial_regions),
            "watershed_seeds": len(seeds),
        }
    )

    return result
