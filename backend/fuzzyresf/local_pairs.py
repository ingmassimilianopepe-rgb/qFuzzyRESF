"""Experimental G-seeded, locally observed opposing-face refinement.

This is a source-only research profile, separate from the frozen legacy
benchmark selector. Memberships are expert support indices, not probabilities.
It uses the engine's 5 cm representative points, not all raw scan records.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import copy
import hashlib
import json

import numpy as np
from scipy.signal import find_peaks
from shapely.affinity import affine_transform
from shapely.geometry import LineString, mapping, shape
from shapely.ops import unary_union

from .topology import polygons


PARAMETERS = {
    "face_band_m": .025, "histogram_bin_m": .01,
    "min_face_height_m": .60, "min_shared_z_m": .30,
    "strip_m": .50, "minimum_shared_strips": 3,
    "minimum_shared_length_fraction": .25,
    "max_normal_angle_deg": 3., "max_face_residual_m": .03,
    "min_separation_m": .06, "max_separation_m": .70,
    "min_affine_scale": .5, "max_affine_scale": 3.,
    "max_face_motion_m": .20, "max_contact_gap_m": .03,
    "max_new_overlap_m2": .001, "motion_penalty": .02,
    "internal_validation_every_nth_strip": 3,
}


def utility(gain_m, memberships, motion_m, aggregation):
    """Only this aggregation differs between the continuous and fuzzy control."""
    if aggregation not in {"mean", "minimum"}:
        raise ValueError("Unknown local support aggregation")
    q = np.asarray(memberships, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or np.any((q < 0) | (q > 1)):
        raise ValueError("Four finite memberships in [0,1] are required")
    support = float(q.mean() if aggregation == "mean" else q.min())
    return float(gain_m * support - PARAMETERS["motion_penalty"] * motion_m)


def _bounds(w):
    b = shape(w["polygon"]).bounds
    a = w["axis"]
    return b[a], b[a + 2], b[1 - a], b[3 - a]


def _constant_width(w):
    p = shape(w["polygon"])
    lo, hi, t0, t1 = _bounds(w)
    if hi - lo < .02 or t1 - t0 < 1.5:
        return False
    # Exclude tapered/partitioned support domains; never replace a clipped
    # profile by a bounding rectangle to manufacture an admissible candidate.
    for t in np.linspace(t0 + .1 * (t1 - t0), t1 - .1 * (t1 - t0), 5):
        c = [[lo - 1, t], [hi + 1, t]]
        if w["axis"] == 1:
            c = [v[::-1] for v in c]
        s = p.intersection(LineString(c))
        if s.geom_type != "LineString" or abs(s.length - (hi - lo)) > .002:
            return False
    return True


def _face(points, normals, axis, center, t0):
    mask = abs(points[:, axis] - center) <= PARAMETERS["face_band_m"]
    x, n = points[mask], normals[mask]
    if len(x) < 24:
        return None
    strips = np.floor((x[:, 1 - axis] - t0) / PARAMETERS["strip_m"]).astype(int)
    train = strips % 3 != 0
    held = ~train
    if train.sum() < 12 or held.sum() < 6:
        return None
    # Axis-constrained robust plane; fitted normal disagreement is still gated.
    coordinate = float(np.median(x[train, axis]))
    residual = float(np.quantile(abs(x[train, axis] - coordinate), .95))
    angle = float(np.degrees(np.arccos(np.clip(np.median(abs(n[:, axis])), 0, 1))))
    zlo, zhi = np.quantile(x[:, 2], [.05, .95])
    support = {}
    for strip in np.unique(strips):
        pts = x[strips == strip]
        if len(pts) < 6:
            continue
        support[int(strip)] = {
            "z": np.quantile(pts[:, 2], [.05, .95]).tolist(),
            "t": np.quantile(pts[:, 1-axis], [.05, .95]).tolist(),
        }
    return {"coordinate": coordinate, "residual": residual, "angle": angle,
            "zlo": float(zlo), "zhi": float(zhi),
            "strips": sorted(map(int, np.unique(strips))),
            "strip_support": support,
            "train": x[train, axis], "held": x[held, axis], "count": len(x)}


def candidates_for_wall(w, Q, N, planarity):
    """Generate a single shared candidate pool, independent of aggregation."""
    if not _constant_width(w):
        return [], {"reason": "G_PROFILE_NOT_CONSTANT_WIDTH_OR_TOO_SHORT"}
    a = w["axis"]
    lo, hi, t0, t1 = _bounds(w)
    ok = ((abs(N[:, 2]) < .2) & (planarity > .30)
          & (abs(N[:, a]) >= np.cos(np.radians(3)))
          & (Q[:, 1 - a] >= t0) & (Q[:, 1 - a] <= t1)
          & (Q[:, a] >= lo - .20) & (Q[:, a] <= hi + .20)
          & (Q[:, 2] >= w["base"]) & (Q[:, 2] <= w["top"]))
    X, normal = Q[ok], N[ok]
    if len(X) < 48:
        return [], {"reason": "INSUFFICIENT_LOCAL_VERTICAL_SUPPORT"}
    bins = np.arange(lo - .21, hi + .22, .01)
    h, edges = np.histogram(X[:, a], bins)
    # A compact kernel avoids two adjacent bin maxima for one noisy face.
    smooth = np.convolve(h, [1, 2, 1], mode="same")
    peaks, _ = find_peaks(smooth, distance=5, prominence=12)
    faces = [_face(X, normal, a, float((edges[j] + edges[j+1]) / 2), t0)
             for j in peaks]
    faces = [f for f in faces if f is not None]
    out, rejection = [], []
    for i, p in enumerate(faces):
        for q in faces[i + 1:]:
            p, q = sorted([p, q], key=lambda f: f["coordinate"])
            sep = q["coordinate"] - p["coordinate"]
            common, domains = [], []
            for strip in sorted(set(p["strip_support"]) & set(q["strip_support"])):
                ps, qs = p["strip_support"][strip], q["strip_support"][strip]
                z = [max(ps["z"][0], qs["z"][0]), min(ps["z"][1], qs["z"][1])]
                t = [max(ps["t"][0], qs["t"][0]), min(ps["t"][1], qs["t"][1])]
                # Shared strip identifiers alone do not imply local support:
                # low/high patches may alternate between the two faces.
                if z[1]-z[0] >= .30 and t[1] > t[0]:
                    common.append(strip)
                    domains.append({"strip": strip, "z_m": z, "t_m": t})
            overlap = min((d["z_m"][1]-d["z_m"][0] for d in domains), default=0.)
            shared_length = sum(d["t_m"][1]-d["t_m"][0] for d in domains)
            scale = sep / (hi - lo)
            motion = max(abs(p["coordinate"] - lo), abs(q["coordinate"] - hi))
            reasons = []
            if not .06 <= sep <= .70: reasons.append("SEPARATION")
            if min(p["zhi"] - p["zlo"], q["zhi"] - q["zlo"]) < .60: reasons.append("LOCAL_HEIGHT")
            if overlap < .30: reasons.append("SHARED_Z")
            if len(common) < 3 or shared_length < .25 * (t1 - t0): reasons.append("SHARED_STRIPS")
            if max(p["angle"], q["angle"]) > 3: reasons.append("NORMAL")
            if max(p["residual"], q["residual"]) > .03: reasons.append("RESIDUAL")
            if not .5 <= scale <= 3: reasons.append("AFFINE_SCALE")
            if motion > .20: reasons.append("FACE_ASSOCIATION_MOTION")
            if reasons:
                rejection.append({"faces_m": [p["coordinate"], q["coordinate"]], "reasons": reasons})
                continue
            train = np.r_[p["train"], q["train"]]
            held = np.r_[p["held"], q["held"]]
            def loss(v, f0, f1):
                return float(np.minimum(np.minimum(abs(v-f0), abs(v-f1)), .10).mean())
            gain = loss(train, lo, hi) - loss(train, p["coordinate"], q["coordinate"])
            validation_gain = loss(held, lo, hi) - loss(held, p["coordinate"], q["coordinate"])
            if validation_gain < -1e-9:
                rejection.append({"faces_m": [p["coordinate"], q["coordinate"]], "reasons": ["INTERNAL_STRIP_VALIDATION"]})
                continue
            memberships = [max(0., 1-max(p["angle"], q["angle"])/3),
                           max(0., 1-max(p["residual"], q["residual"])/.03),
                           min(1., shared_length/max(.5*(t1-t0), .5)),
                           min(1., overlap/.8)]
            offset = p["coordinate"] - scale*lo
            matrix = [scale, 0, 0, 1, offset, 0] if a == 0 else [1, 0, 0, scale, 0, offset]
            geom = affine_transform(shape(w["polygon"]), matrix)
            public = {"id": f"{w['id']}_P{len(out):03d}", "wall_id": w["id"],
                      "faces_m": [p["coordinate"], q["coordinate"]],
                      "observed_face_heights_m": [p["zhi"]-p["zlo"], q["zhi"]-q["zlo"]],
                      "shared_z_m": overlap, "shared_strips": common,
                      "shared_domains": domains, "shared_length_m": shared_length,
                      "memberships": memberships, "gain_m": gain,
                      "internal_validation_gain_m": validation_gain,
                      "max_motion_m": motion, "matrix": matrix, "polygon": mapping(geom)}
            public["utility_mean_m"] = utility(gain, memberships, motion, "mean")
            public["utility_minimum_m"] = utility(gain, memberships, motion, "minimum")
            out.append(public)
    return out, {"reason": "CANDIDATES_AVAILABLE" if out else "NO_ADMISSIBLE_LOCAL_OPPOSING_PAIR",
                 "face_count": len(faces), "rejected_pairs": rejection}


def _guard(original, selected, index, new):
    if not new.is_valid or new.is_empty or new.area <= 0:
        return "INVALID_PROFILE"
    for j, (old, current) in enumerate(zip(original, selected)):
        if index == j:
            continue
        if original[index].distance(old) <= .03:
            if new.distance(current) > .03:
                return "CONTACT_GAP"
        elif original[index].intersection(old).area <= .001:
            if new.intersection(current).area > .001:
                return "NEW_NONINCIDENT_OVERLAP"
    return None


def refine(I, P, N, PL, aggregation="minimum"):
    """Fit source candidates once; select with deterministic greedy graph guards.

    Spatial strip validation shares preprocessing with fitting and is not an
    independent test set. The guards are 2D and do not certify a closed BIM.
    """
    result = copy.deepcopy(I)
    if "main_level" not in I:
        result["local_refinement"] = {"status": "ABSTAIN_NO_SUPPORTED_MAIN_LEVEL", "reference_used": False}
        return result
    R = np.asarray(I["rotation_world_to_uv"])
    Q = np.array(P, dtype=float, copy=True)
    Q[:, :2] = (Q[:, :2] - I["origin_xy"]) @ R.T
    normal = np.array(N, dtype=float, copy=True)
    normal[:, :2] = normal[:, :2] @ R.T
    walls = result["walls"]
    original = [shape(w["polygon"]) for w in walls]
    selected = list(original)
    candidates, decisions = [], []
    for w in walls:
        pool, audit = candidates_for_wall(w, Q, normal, PL)
        candidates.extend(pool)
        decisions.append({"wall_id": w["id"], "selection": "IDENTITY", **audit})
        lo, hi, t0, t1 = _bounds(w)
        w.update(position=(lo+hi)/2, thickness=hi-lo, t0=t0, t1=t1,
                 thickness_status="PHYSICAL_THICKNESS_UNKNOWN; geometric profile width only",
                 physical_thickness_status="UNKNOWN_UNVERIFIED",
                 observed_opposing_pair=False)
    key = "utility_mean_m" if aggregation == "mean" else "utility_minimum_m"
    by_id = {w["id"]: j for j, w in enumerate(walls)}
    used = set()
    for c in sorted(candidates, key=lambda v: (-v[key], v["id"])):
        i = by_id[c["wall_id"]]
        if i in used or c[key] <= 0:
            continue
        geom = shape(c["polygon"])
        rejection = _guard(original, selected, i, geom)
        if rejection:
            decisions[i].setdefault("selection_rejections", []).append({"candidate": c["id"], "reason": rejection})
            continue
        selected[i] = geom
        walls[i]["polygon"] = mapping(geom)
        lo, hi, t0, t1 = _bounds(walls[i])
        walls[i].update(position=(lo+hi)/2, thickness=hi-lo, t0=t0, t1=t1,
                        thickness_status="LOCAL_OPPOSING_FACE_HYPOTHESIS; full-height width extrapolated",
                        observed_opposing_pair=True, observed_face_separation_m=c["faces_m"][1]-c["faces_m"][0],
                        local_refinement_candidate=c["id"])
        decisions[i].update(selection=c["id"], utility_m=c[key])
        used.add(i)
    # Retain original provisional room domains, subtract modified material and
    # update exported areas/boundaries. This does not discover missing rooms.
    if used:
        material = unary_union(selected).buffer(.025, join_style=2)
        spaces = []
        for s in result.get("spaces", []):
            parts = sorted(polygons(shape(s["polygon"]).difference(material)), key=lambda p: (-p.area, p.centroid.x, p.centroid.y))
            for j, p in enumerate(parts):
                if p.area <= 1e-6:
                    continue
                q = copy.deepcopy(s)
                q.update(id=s["id"] if len(parts) == 1 else f"{s['id']}_{j+1}",
                         polygon=mapping(p), area_m2=p.area,
                         status="PROVISIONAL G SPACE RECLIPPED; NOT RESEGMENTED OR VALIDATED")
                spaces.append(q)
        result["spaces"] = spaces
    canonical = json.dumps(candidates, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    result["local_refinement"] = {
        "status": "EXPERIMENTAL_SOURCE_ONLY", "aggregation": aggregation,
        "parameters": PARAMETERS, "candidates": candidates,
        "candidate_pool_sha256": hashlib.sha256(canonical).hexdigest(),
        "decisions": decisions, "changed_walls": len(used), "reference_used": False,
        "evidence": "5 cm voxel representatives with PCA normals; not original raw records",
        "physical_thickness_verified": False,
        "identifiability": "Two associated faces constrain separation; material identity remains a hypothesis",
        "validation": "Internal interleaved longitudinal strips; shared preprocessing; not independent testing",
        "selection": "Deterministic greedy utility; identity available; 2D contact and overlap guards",
        "rooms": "Original provisional G spaces re-clipped only",
    }
    return result
