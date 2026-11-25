"""Auditable, G-seeded catalogue hypotheses; expert supports are not probabilities.

The catalogue proposes dimensions, never hidden construction, object recognition,
or physical measurements. Inputs are source voxel representatives, not GT.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import copy
import hashlib
import json
import math

import numpy as np
from scipy.signal import find_peaks
from shapely.affinity import affine_transform
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from .local_pairs import _bounds, _constant_width, _face, _guard, PARAMETERS as LOCAL_PARAMETERS
from .topology import polygons

PARAMETERS = {**LOCAL_PARAMETERS, "catalogue_sigma_m": .03, "crisp_membership_threshold": .5}
ROLE = "finished_wall_thickness"
AGGREGATIONS = {"mean", "minimum", "crisp"}
UNKNOWN = {"id": "UNKNOWN", "kind": "free_geometry", "ifc_class": "IfcWall",
           "dimension_role": ROLE, "nominal_m": None,
           "source": {"kind": "ENGINE_UNKNOWN", "description": "No family identification"},
           "applicability": "GENERAL_REFERENCE", "layers_m": [], "physical_measurement": False}


def _finite_json(value):
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_finite_json(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _finite_json(v) for k, v in value.items())
    return False


def validate_catalogue(catalogue):
    """Return a detached schema-1 catalogue with an explicit UNKNOWN family."""
    if not isinstance(catalogue, dict) or not _finite_json(catalogue):
        raise ValueError("Catalogue must contain finite JSON values")
    if type(catalogue.get("schema_version")) is not int or catalogue["schema_version"] != 1:
        raise ValueError("Catalogue schema_version must be 1")
    if not isinstance(catalogue.get("catalogue_id"), str) or not catalogue["catalogue_id"].strip():
        raise ValueError("Catalogue catalogue_id must be a nonempty string")
    if catalogue.get("units") != "m":
        raise ValueError("Catalogue units must be m")
    if not isinstance(catalogue.get("families"), list):
        raise ValueError("Catalogue families must be a list")
    result, ids = copy.deepcopy(catalogue), set()
    for family in result["families"]:
        if not isinstance(family, dict):
            raise ValueError("Each family must be an object")
        fid = family.get("id")
        if not isinstance(fid, str) or not fid.strip() or fid in ids:
            raise ValueError("Family ids must be nonempty and unique")
        ids.add(fid)
        if family.get("ifc_class") != "IfcWall" or family.get("dimension_role") != ROLE:
            raise ValueError("Only IfcWall finished_wall_thickness dimensions are supported")
        if family.get("applicability") != "GENERAL_REFERENCE":
            raise ValueError("Family applicability must be GENERAL_REFERENCE")
        if family.get("physical_measurement") is not False:
            raise ValueError("Catalogue dimensions cannot be physical measurements")
        source = family.get("source")
        if not isinstance(source, dict) or not source or not any(
                isinstance(v, str) and v.strip() for v in source.values()):
            raise ValueError("Family source must be a nonempty documented object")
        layers = family.get("layers_m")
        if not isinstance(layers, list) or any(type(v) not in (int, float) or v <= 0 for v in layers):
            raise ValueError("layers_m must be a list of positive finite metre dimensions")
        nominal = family.get("nominal_m")
        if fid == "UNKNOWN":
            if nominal is not None or layers or family.get("kind", "free_geometry") != "free_geometry":
                raise ValueError("UNKNOWN must be free_geometry with null nominal and no layers")
            family["kind"] = "free_geometry"
        elif type(nominal) not in (int, float) or not .06 <= nominal <= .70:
            raise ValueError("Wall nominal_m must be a finite dimension in [.06,.70]")
        elif layers and abs(sum(layers)-nominal) > 1e-6:
            raise ValueError("Catalogue layer dimensions must sum to nominal_m")
    if "UNKNOWN" not in ids:
        result["families"].append(copy.deepcopy(UNKNOWN))
    return result


def utility(gain_m, memberships, motion_m, aggregation="minimum"):
    """Geometry-only controls on one shared pool; catalogue similarity excluded."""
    if aggregation not in AGGREGATIONS:
        raise ValueError("Aggregation must be mean, minimum, or crisp")
    q = np.asarray(memberships, dtype=float)
    if q.shape != (5,) or not np.isfinite(q).all() or np.any((q < 0) | (q > 1)):
        raise ValueError("Five finite geometry memberships in [0,1] are required")
    support = float(q.mean() if aggregation == "mean" else q.min() if aggregation == "minimum"
                    else np.all(q >= PARAMETERS["crisp_membership_threshold"]))
    return float(gain_m * support - PARAMETERS["motion_penalty"] * motion_m)


def _utilities(candidate):
    for aggregation in sorted(AGGREGATIONS):
        candidate[f"utility_{aggregation}_m"] = utility(
            candidate["gain_m"], candidate["memberships"], candidate["max_motion_m"], aggregation)
    return candidate


def _loss(v, f0, f1):
    return float(np.minimum(np.minimum(abs(v-f0), abs(v-f1)), .10).mean())


def _observations(w, Q, N, planarity):
    a = w["axis"]
    lo, hi, t0, t1 = _bounds(w)
    ok = ((abs(N[:, 2]) < .2) & (planarity > .30)
          & (abs(N[:, a]) >= np.cos(np.radians(3)))
          & (Q[:, 1-a] >= t0) & (Q[:, 1-a] <= t1)
          & (Q[:, 2] >= w["base"]) & (Q[:, 2] <= w["top"]))
    X, normals = Q[ok], N[ok]
    bins = np.arange(lo-.21, hi+.22, .01)
    h, edges = np.histogram(X[:, a], bins)
    peaks, _ = find_peaks(np.convolve(h, [1, 2, 1], mode="same"), distance=5, prominence=12)
    faces = [_face(X, normals, a, float((edges[j]+edges[j+1])/2), t0) for j in peaks]
    faces = [f for f in faces if f is not None]
    return X, normals, sorted(faces, key=lambda f: f["coordinate"])


def _candidate(w, cid, family, proposal, target, face0, face1, original, index):
    lo, hi, t0, t1 = _bounds(w)
    a = w["axis"]
    f0, f1 = sorted(map(float, target))
    sep = f1-f0
    scale = sep/(hi-lo)
    motion = max(abs(f0-lo), abs(f1-hi))
    matrix = [scale, 0, 0, 1, f0-scale*lo, 0] if a == 0 else [1, 0, 0, scale, 0, f0-scale*lo]
    geom = affine_transform(shape(w["polygon"]), matrix)
    c = {"id": cid, "wall_id": w["id"], "family_id": family["id"], "proposal": proposal,
         "nominal_m": family["nominal_m"], "faces_m": [f0, f1],
         "source": copy.deepcopy(family["source"]), "applicability": family["applicability"],
         "matrix": matrix, "polygon": mapping(geom), "max_motion_m": motion,
         "geometry_width_m": sep, "physical_thickness_status": "UNKNOWN_UNVERIFIED",
         "physical_measurement": False, "family_recognition_verified": False,
         "observed_face_count": int(face0 is not None)+int(face1 is not None),
         "memberships": [0., 0., 0., 0., 0.], "gain_m": 0.,
         "internal_validation_gain_m": None, "catalogue_membership_diagnostic": None,
         "exportable": False, "reasons": []}
    if not .06 <= sep <= .70: c["reasons"].append("SEPARATION")
    if not .5 <= scale <= 3: c["reasons"].append("AFFINE_SCALE")
    if motion > .20+1e-9: c["reasons"].append("FACE_ASSOCIATION_MOTION")
    if face0 is None or face1 is None:
        c["reasons"].append("TWO_OBSERVED_FACES_REQUIRED")
        return _utilities(c)
    p, q = sorted([face0, face1], key=lambda f: f["coordinate"])
    observed_sep = q["coordinate"]-p["coordinate"]
    c["observed_faces_m"] = [p["coordinate"], q["coordinate"]]
    c["observed_face_separation_m"] = observed_sep
    if family["nominal_m"] is not None:
        c["catalogue_membership_diagnostic"] = float(math.exp(-.5*((observed_sep-family["nominal_m"])/.03)**2))
    domains = []
    for strip in sorted(set(p["strip_support"]) & set(q["strip_support"])):
        ps, qs = p["strip_support"][strip], q["strip_support"][strip]
        z = [max(ps["z"][0], qs["z"][0]), min(ps["z"][1], qs["z"][1])]
        t = [max(ps["t"][0], qs["t"][0]), min(ps["t"][1], qs["t"][1])]
        if z[1]-z[0] >= .30 and t[1] > t[0]:
            domains.append({"strip": strip, "z_m": z, "t_m": t})
    overlap = min((d["z_m"][1]-d["z_m"][0] for d in domains), default=0.)
    shared_length = sum(d["t_m"][1]-d["t_m"][0] for d in domains)
    height = min(p["zhi"]-p["zlo"], q["zhi"]-q["zlo"])
    angle = max(p["angle"], q["angle"])
    residual = max(float(np.quantile(abs(p["train"]-f0), .95)),
                   float(np.quantile(abs(q["train"]-f1), .95)))
    c.update(shared_domains=domains, shared_strips=[d["strip"] for d in domains],
             shared_length_m=shared_length, shared_z_m=overlap,
             observed_face_heights_m=[p["zhi"]-p["zlo"], q["zhi"]-q["zlo"]],
             normal_angle_deg=angle, residual_p95_m=residual)
    if height < .60: c["reasons"].append("LOCAL_HEIGHT")
    if overlap < .30: c["reasons"].append("SHARED_Z")
    if len(domains) < 3 or shared_length < .25*(t1-t0): c["reasons"].append("SHARED_STRIPS")
    if angle > 3: c["reasons"].append("NORMAL")
    if residual > .03: c["reasons"].append("RESIDUAL")
    train, held = np.r_[p["train"], q["train"]], np.r_[p["held"], q["held"]]
    gain = _loss(train, lo, hi)-_loss(train, f0, f1)
    held_gain = _loss(held, lo, hi)-_loss(held, f0, f1)
    if held_gain < -1e-9: c["reasons"].append("INTERNAL_STRIP_VALIDATION")
    static_rejection = _guard(original, original, index, geom)
    if static_rejection: c["reasons"].append(static_rejection)
    contact_gap = max((geom.distance(g) for j, g in enumerate(original)
                       if j != index and original[index].distance(g) <= .03), default=0.)
    c.update(gain_m=gain, internal_validation_gain_m=held_gain,
             memberships=[max(0., 1-angle/3), max(0., 1-residual/.03),
                          min(1., shared_length/max(.5*(t1-t0), .5)),
                          min(1., overlap/.8), max(0., 1-contact_gap/.03)],
             junction_proxy="2D preservation of incident G contacts; no observed junction classifier",
             exportable=not c["reasons"])
    return _utilities(c)


def _wall_pool(w, Q, N, PL, catalogue, original, index):
    lo, hi, t0, t1 = _bounds(w)
    identity = _utilities({"id": f"{w['id']}_IDENTITY", "wall_id": w["id"],
                          "family_id": "UNKNOWN", "proposal": "IDENTITY", "nominal_m": None,
                          "faces_m": [lo, hi], "polygon": copy.deepcopy(w["polygon"]),
                          "gain_m": 0., "memberships": [1.]*5, "max_motion_m": 0.,
                          "exportable": True, "reasons": [], "observed_face_count": 0,
                          "physical_thickness_status": "UNKNOWN_UNVERIFIED"})
    # Explicit free-geometry identity remains available even if no pair is observed.
    unknown = copy.deepcopy(identity)
    unknown.update(id=f"{w['id']}_UNKNOWN", proposal="FREE_GEOMETRY_IDENTITY")
    pool = [identity, unknown]
    if w.get("axis") not in (0, 1) or not _constant_width(w):
        return pool, {"reason": "G_PROFILE_NOT_CONSTANT_WIDTH_OR_TOO_SHORT", "face_count": 0}
    X, normals, faces = _observations(w, Q, N, PL)
    free = next(f for f in catalogue["families"] if f["id"] == "UNKNOWN")
    for i, p in enumerate(faces):
        for j, q in enumerate(faces[i+1:], i+1):
            pool.append(_candidate(w, f"{w['id']}_FREE_{i:03d}_{j:03d}", free,
                                   "OBSERVED_FREE_PAIR", [p["coordinate"], q["coordinate"]],
                                   p, q, original, index))
    for i, anchor in enumerate(faces):
        for fi, family in enumerate(catalogue["families"]):
            if family["nominal_m"] is None:
                continue
            for side in (-1, 1):
                target = anchor["coordinate"]+side*family["nominal_m"]
                # Crucially this probes source representatives, not the histogram face list.
                counter = _face(X, normals, w["axis"], target, t0)
                ordered = (anchor, counter) if side > 0 else (counter, anchor)
                c = _candidate(w, f"{w['id']}_CAT_{i:03d}_{fi:03d}_{'P' if side > 0 else 'M'}",
                               family, "CATALOGUE_RAW_BAND", [anchor["coordinate"], target],
                               *ordered, original, index)
                c.update(anchor_m=anchor["coordinate"], probe_m=target, side=side,
                         counterface_histogram_peak_required=False)
                pool.append(c)
    return pool, {"reason": "CANDIDATES_AVAILABLE" if len(pool) > 2 else "NO_OBSERVED_ANCHOR",
                  "face_count": len(faces), "observed_anchor_coordinates_m": [f["coordinate"] for f in faces]}


def infer_catalogue(I, P, N, PL, catalogue, aggregation="minimum"):
    """Deep-copy source inference, generate a common pool, then select guarded edits."""
    if aggregation not in AGGREGATIONS:
        raise ValueError("Aggregation must be mean, minimum, or crisp")
    catalogue = validate_catalogue(catalogue)
    result = copy.deepcopy(I)
    P, normal, planarity = np.asarray(P, float), np.asarray(N, float), np.asarray(PL, float)
    if P.ndim != 2 or P.shape[1] != 3 or normal.shape != P.shape or planarity.shape != (len(P),):
        raise ValueError("P and N must be Nx3 and PL must be length N")
    if not all(np.isfinite(v).all() for v in (P, normal, planarity)):
        raise ValueError("Source evidence must be finite")
    Q, normal = P.copy(), normal.copy()
    R = np.asarray(I["rotation_world_to_uv"], float)
    Q[:, :2] = (Q[:, :2]-I["origin_xy"]) @ R.T
    normal[:, :2] = normal[:, :2] @ R.T
    walls = result.get("walls", [])
    original = [shape(w["polygon"]) for w in walls]
    selected, candidates, decisions = list(original), [], []
    for index, w in enumerate(walls):
        pool, audit = _wall_pool(w, Q, normal, planarity, catalogue, original, index)
        if "main_level" not in I:
            for c in pool:
                if c["proposal"] not in {"IDENTITY", "FREE_GEOMETRY_IDENTITY"}:
                    c["exportable"] = False
                    c["reasons"].append("NO_SUPPORTED_MAIN_LEVEL")
        candidates.extend(pool)
        decisions.append({"wall_id": w["id"], "selection": f"{w['id']}_IDENTITY", **audit})
        lo, hi, t0, t1 = _bounds(w)
        w.update(position=(lo+hi)/2, thickness=hi-lo, geometric_profile_width_m=hi-lo, t0=t0, t1=t1,
                 physical_thickness_status="UNKNOWN_UNVERIFIED", physical_measurement=False,
                 thickness_status="PHYSICAL_THICKNESS_UNKNOWN; geometric profile width only",
                 family_hypothesis="UNKNOWN", catalogue_family_id="UNKNOWN",
                 catalogue_id=catalogue["catalogue_id"], catalogue_applicability="GENERAL_REFERENCE",
                 catalogue_membership_diagnostic=None, observed_opposing_pair=False)
    key = f"utility_{aggregation}_m"
    by_id, used = {w["id"]: j for j, w in enumerate(walls)}, set()
    for c in sorted(candidates, key=lambda v: (-v[key], v["id"])):
        i = by_id[c["wall_id"]]
        if i in used or not c["exportable"] or c[key] <= 0:
            continue
        geom = shape(c["polygon"])
        rejection = _guard(original, selected, i, geom)
        if rejection:
            decisions[i].setdefault("selection_rejections", []).append({"candidate": c["id"], "reason": rejection})
            continue
        selected[i] = geom
        w = walls[i]
        w["polygon"] = mapping(geom)
        lo, hi, t0, t1 = _bounds(w)
        w.update(position=(lo+hi)/2, thickness=hi-lo, geometric_profile_width_m=hi-lo, t0=t0, t1=t1,
                 thickness_status="CATALOGUE_OR_FREE_OPPOSING_FACE_HYPOTHESIS; full-height extrapolation",
                 observed_opposing_pair=True, observed_face_separation_m=c["observed_face_separation_m"],
                 family_hypothesis=c["family_id"], catalogue_family_id=c["family_id"],
                 catalogue_candidate_id=c["id"], local_refinement_candidate=c["id"],
                 catalogue_membership_diagnostic=c["catalogue_membership_diagnostic"])
        decisions[i].update(selection=c["id"], utility_m=c[key], family_id=c["family_id"])
        used.add(i)
    if used:
        material = unary_union(selected).buffer(.025, join_style=2)
        spaces = []
        for s in result.get("spaces", []):
            parts = sorted(polygons(shape(s["polygon"]).difference(material)), key=lambda p: (-p.area, p.centroid.x, p.centroid.y))
            for j, p in enumerate(parts):
                if p.area <= 1e-6: continue
                q = copy.deepcopy(s)
                q.update(id=s["id"] if len(parts) == 1 else f"{s['id']}_{j+1}", polygon=mapping(p),
                         area_m2=p.area, status="PROVISIONAL G SPACE RECLIPPED; NOT RESEGMENTED OR VALIDATED")
                spaces.append(q)
        result["spaces"] = spaces
    canonical = json.dumps(candidates, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    audit = {"schema_version": 1, "status": "EXPERIMENTAL_SOURCE_ONLY" if "main_level" in I else "ABSTAIN_NO_SUPPORTED_MAIN_LEVEL",
             "catalogue": catalogue, "catalogue_id": catalogue["catalogue_id"], "aggregation": aggregation,
             "parameters": copy.deepcopy(PARAMETERS), "candidates": candidates, "decisions": decisions,
             "candidate_pool_sha256": hashlib.sha256(canonical).hexdigest(), "changed_walls": len(used),
             "membership_names": ["normal", "residual", "shared_length", "shared_z", "junction_G_proxy"],
             "utility": "capped source loss improvement * geometry support - .02 * maximum face motion",
             "catalogue_membership": "exp(-.5*((observed separation-nominal)/.03)^2); diagnostic only; expert, not trained, not probability",
             "reference_used": False, "physical_thickness_verified": False, "hidden_layers_inferred": False,
             "evidence": "5 cm voxel representatives and PCA normals; not original raw scan records",
             "validation": "Internal interleaved longitudinal strips; shared preprocessing; not independent testing",
             "selection": "Deterministic greedy positive utility; static and sequential 2D G contact/overlap guards",
             "identifiability": "Family id is a dimension hypothesis, never recognition proof; physical thickness UNKNOWN",
             "rooms": "Original provisional G spaces re-clipped only"}
    result["catalogue_inference"] = audit
    result["local_refinement"] = {"status": audit["status"], "aggregation": aggregation,
                                  "changed_walls": len(used), "reference_used": False,
                                  "candidate_pool_sha256": audit["candidate_pool_sha256"],
                                  "physical_thickness_verified": False, "catalogue_compatibility": True}
    return result
