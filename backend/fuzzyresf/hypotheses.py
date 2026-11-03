"""Experimental, uncalibrated evidence and finite wall-hypothesis selection.

No reference model or scanner origins are inputs. Missing spatial support is
ignorance, not measured free space. Intervals below are support bounds under
an explicit ignorance mixture, not statistical confidence intervals.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import numpy as np

PROFILES = {"evidence", "relational"}
THRESHOLD = .60
MIN_OBSERVABILITY = .25


def evidence(planarity, agreement, residual95, residual_scale, coverage,
             span_fraction=1.):
    """Continuous fit support plus observed spatial extent, all expert-defined.

    A geometric mean limits additive compensation. Robust residual scale is
    estimated from signed point-plane residuals, with a 25 mm denominator floor
    matching the finite point/voxel representation rather than a sensor claim.
    """
    ramp = lambda x, a, b: float(np.clip((x-a)/(b-a), 0, 1))
    p = ramp(planarity, .25, .95)
    a = ramp(agreement, .80, .995)
    scale = max(.025, 3*max(0., float(residual_scale)))
    r = float(1/(1+(float(residual95)/scale)**2))
    fit = float(p**.30 * r**.40 * a**.30)
    q = float(np.clip(coverage, 0, 1)*np.clip(span_fraction, 0, 1))
    low, high = q*fit, q*fit+1-q
    score = .5*(low+high)
    status = ("OBSERVED_SUPPORT" if score >= THRESHOLD and q >= MIN_OBSERVABILITY
              else "DEFERRED_UNKNOWN" if high >= THRESHOLD
              else "LOW_GEOMETRIC_SUPPORT")
    return {"score": score, "fit_memberships_P_R_A": [p, r, a],
            "fit_support": fit, "observability": q,
            "support_interval": [float(low), float(high)], "status": status,
            "residual_scale_m": float(residual_scale),
            "residual_denominator_m": scale,
            "calibration_status": "EXPERT_UNCALIBRATED",
            "missing_support_semantics": "Unknown; no free-space or absence inference",
            "interval_semantics": "Ignorance-mixture support bounds; not a probability or statistical confidence interval"}


def _distance_segment(p, a, b):
    v = b-a
    t = float(np.clip((p-a)@v/max(v@v, 1e-12), 0, 1))
    return float(np.linalg.norm(p-(a+t*v)))


def _relation(a, b):
    ga, gb = a.get("relation_geometry"), b.get("relation_geometry")
    if not ga or not gb or ga.get("kind") != "wall" or gb.get("kind") != "wall":
        return None
    pa, qa = np.asarray(ga["start"], float), np.asarray(ga["end"], float)
    pb, qb = np.asarray(gb["start"], float), np.asarray(gb["end"], float)
    la, lb = np.linalg.norm(qa-pa), np.linalg.norm(qb-pb)
    if min(la, lb) < .1:
        return None
    ua, ub = (qa-pa)/la, (qb-pb)/lb
    zov = max(0., min(ga["top"], gb["top"])-max(ga["base"], gb["base"]))
    zh = min(ga["top"]-ga["base"], gb["top"]-gb["base"])
    if zh <= 0 or zov/zh < .50:
        return None
    cosine = abs(float(ua@ub))
    if cosine >= np.cos(np.radians(5)):
        n = np.array([-ua[1], ua[0]])
        distance = abs(float((pb-pa)@n))
        aa = sorted([float(pa@ua), float(qa@ua)])
        bb = sorted([float(pb@ua), float(qb@ua)])
        overlap = max(0., min(aa[1], bb[1])-max(aa[0], bb[0]))
        if distance < .065 and overlap > .70*min(la, lb):
            return ("duplicate_conflict", 1.)
        # Opposite observed faces must remain compatible to estimate thickness.
        if .075 <= distance <= .45 and overlap > .65*max(la, lb):
            return ("paired_faces", float(overlap/max(la, lb)))
        gap = max(aa[0]-bb[1], bb[0]-aa[1], 0.)
        if distance < .075 and 0 < gap <= .45:
            return ("continuation", float((1-gap/.60)*(1-distance/.10)))
    elif cosine <= np.sin(np.radians(12)):
        distance = min(_distance_segment(x, pb, qb) for x in (pa, qa))
        distance = min(distance, *(_distance_segment(x, pa, qa) for x in (pb, qb)))
        if distance <= .35:
            return ("corner_or_t_junction", float(1-distance/.35))
    return None


def select_candidates(records, profile, kind="interior", time_limit_s=2.,
                      max_candidates=512, max_edges=2048):
    """Select binary wall/surface hypotheses; geometry fitting is unchanged.

    Relational objective maximizes sum((score-.60)*x) plus bounded compatibility
    rewards*y, subject to duplicate conflicts and y=x_i*x_j. Connections are
    candidate relations, not a guarantee of global building closure. With no
    relations the decision equals the independent evidence selector.
    """
    if profile not in PROFILES:
        raise ValueError("Experimental selector expects evidence or relational")
    ids = [i for i, p in enumerate(records) if p.get("geometry_pass", True)]
    score = np.array([records[i]["evidence"]["score"] for i in ids])
    quality = np.array([records[i]["evidence"]["observability"] for i in ids])
    upper = np.array([records[i]["evidence"]["support_interval"][1] for i in ids])
    allowed = (quality >= MIN_OBSERVABILITY) & (upper >= THRESHOLD)
    local = allowed & (score >= THRESHOLD)
    edges, conflicts = [], []
    truncated = False
    if profile == "relational" and len(ids) <= max_candidates:
        for i in range(len(ids)):
            for j in range(i+1, len(ids)):
                if not (allowed[i] and allowed[j]):
                    continue
                rel = _relation(records[ids[i]], records[ids[j]])
                if rel is None:
                    continue
                name, compatibility = rel
                if name == "duplicate_conflict":
                    conflicts.append((i, j))
                elif compatibility > 0:
                    if len(edges) < max_edges:
                        edges.append([i, j, name, compatibility])
                    else:
                        truncated = True
    elif profile == "relational":
        truncated = True
    degree = np.zeros(len(ids), int)
    for i, j, _, _ in edges:
        degree[i] += 1; degree[j] += 1
    for e in edges:
        i, j = e[:2]
        e.append(float(.18*e[3]*np.sqrt(quality[i]*quality[j])/max(1, degree[i], degree[j])))
    selected = local.copy()
    solver = "INDEPENDENT_EVIDENCE"
    optimal = None
    if profile == "relational" and (edges or conflicts):
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import lil_matrix
        n, m = len(ids), len(edges)
        c = np.r_[THRESHOLD-score, [-e[4] for e in edges]]
        matrix = lil_matrix((len(conflicts)+3*m, n+m), dtype=float)
        row = 0
        for i, j in conflicts:
            matrix[row, i] = matrix[row, j] = 1; row += 1
        lower = [-np.inf]*len(conflicts); higher = [1.]*len(conflicts)
        for k, (i, j, *_) in enumerate(edges):
            matrix[row, n+k] = 1; matrix[row, i] = -1
            lower.append(-np.inf); higher.append(0.); row += 1
            matrix[row, n+k] = 1; matrix[row, j] = -1
            lower.append(-np.inf); higher.append(0.); row += 1
            matrix[row, n+k] = -1; matrix[row, i] = matrix[row, j] = 1
            lower.append(-np.inf); higher.append(1.); row += 1
        ub = np.r_[allowed.astype(float), np.ones(m)]
        try:
            result = milp(c, integrality=np.ones(n+m), bounds=Bounds(np.zeros(n+m), ub),
                          constraints=LinearConstraint(matrix.tocsr(), lower, higher),
                          options={"time_limit": float(time_limit_s), "node_limit": 10000,
                                   "mip_rel_gap": 0., "presolve": True})
            if result.status == 0 and result.x is not None:
                selected = result.x[:n] > .5
                solver, optimal = "SCIPY_HIGHS_MILP", True
            else:
                solver = "DETERMINISTIC_FALLBACK_NONOPTIMAL_SOLVER"
                optimal = False
        except (ValueError, RuntimeError):
            solver = "DETERMINISTIC_FALLBACK_SOLVER_ERROR"
            optimal = False
        if optimal is False:
            # A time-limit incumbent can vary across hardware; ignore it and use a
            # documented deterministic feasible local search instead.
            selected = np.zeros(n, bool)
            conflict = {i: set() for i in range(n)}
            for i, j in conflicts:
                conflict[i].add(j); conflict[j].add(i)
            for i in sorted(range(n), key=lambda i: (-score[i], ids[i])):
                if local[i] and not any(selected[j] for j in conflict[i]):
                    selected[i] = True
            for _ in range(3):
                changed = False
                for i in sorted(range(n), key=lambda i: (-score[i], ids[i])):
                    delta = float(score[i]-THRESHOLD)
                    delta += sum(e[4] for e in edges if i in e[:2] and selected[e[1] if e[0] == i else e[0]])
                    if not selected[i] and allowed[i] and delta > 1e-10 and not any(selected[j] for j in conflict[i]):
                        selected[i] = True; changed = True
                    elif selected[i] and delta < -1e-10:
                        selected[i] = False; changed = True
                if not changed:
                    break
    elif profile == "relational":
        solver = "INDEPENDENT_NO_RELATIONS" if not truncated else "INDEPENDENT_RESOURCE_LIMIT"
    chosen = [ids[i] for i in np.flatnonzero(selected)]
    audit = {"profile": profile, "scope": "Finite candidate existence and compatibility selection; geometry fits unchanged",
             "calibration_status": "EXPERT_UNCALIBRATED", "threshold": THRESHOLD,
             "min_observability": MIN_OBSERVABILITY, "local_selected": int(local.sum()),
             "joint_selected": len(chosen), "solver": solver, "optimal": optimal,
             "relation_resource_limit": truncated,
             "solver_tie_policy": "Fixed input order; multiple optimal solutions may differ across solver versions. Fallback is deterministic.",
             "observability_semantics": "Heuristic observed-support fraction, not calibrated sensor reliability",
             "limits": {"time_limit_s": time_limit_s, "max_candidates": max_candidates, "max_edges": max_edges},
             "changed_candidate_indices": [ids[i] for i in range(len(ids)) if selected[i] != local[i]],
             "relations": [{"a": ids[i], "b": ids[j], "type": name, "compatibility": comp,
                             "reward": reward, "active": bool(selected[i] and selected[j])}
                            for i, j, name, comp, reward in edges],
             "duplicate_conflicts": [[ids[i], ids[j]] for i, j in conflicts],
             "objective": "sum((score-.60)*x)+sum(reward*y), y=x_i*x_j; duplicate x_i+x_j<=1",
             "unknown_semantics": "Deferred candidates are not labels of absent walls; no inferred free space"}
    return chosen, audit
