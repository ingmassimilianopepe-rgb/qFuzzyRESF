"""Auditable, editable Mamdani inference for RESF semantic reconstruction.

All inputs are measured or externally supplied evidence in [0, 1]. ``N`` is
normal agreement. Missing evidence is represented by an absent key or ``None``;
every rule depending on it is skipped. In particular, lack of occupied cells
is *not* evidence of occlusion or free space.

The defaults are explicit engineering hypotheses, not learned parameters or
the reproduction of a calibrated classifier from the draft paper. Each class
has an independent output membership function. Rule conjunction is minimum,
implication clips the consequent by the rule activation, aggregation is maximum,
and defuzzification is numerical centroid integration. Decision support is the
minimum of that centroid and the strongest supporting rule activation, so a
weakly activated rule cannot become a strong decision through defuzzification.
These supports and ``confidence`` are NOT calibrated probabilities and need not
sum to one. Their calibration requires separately annotated validation data.

Configuration (all optional, under ``config['fuzzy']`` or at the top level):
    decision_threshold: 0.55; ambiguity_margin: 0.12; universe_samples: 501
    geometry_confidence_cap: 0.85; uncertain_confidence_cap: 0.49
    geometric_feedback_threshold: 0.55
    wall_rules / opening_rules: validated replacements of the complete rule list
    memberships: {evidence_key: {term: [a,b,c,d]}} trapezoid overrides
    output_memberships: {moderate|strong|very_strong: [a,b,c,d]}

A rule is ``{'id': str, 'if': [[key, term], ...], 'then': state,
             'strength': 'strong', 'weight': 1.0}``. The optional ``weight`` is
an explicit, audited engineering rule weight. Geometry/visibility guards still
apply after a user replaces the rules: a rule cannot turn missing observations
into an observed object.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np


WALL_STATES = ("Permanent", "OccludedPermanent", "Clutter", "Uncertain")
OPENING_STATES = ("OpenVoid", "Door", "Window", "Uncertain")
WALL_KEYS = (
    "A_star", "E_H", "E_V", "C", "N", "floor_contact", "ceiling_contact",
    "junction", "room_closure", "continuation", "semantic_wall",
    "semantic_furniture", "occlusion", "geometric_support",
)
OPENING_KEYS = (
    "sill_ratio", "frame_support", "free_space", "semantic_door",
    "semantic_window", "catalog_fit", "vacancy", "rectangularity",
    "host_confidence",
)
_CORE_WALL = ("A_star", "E_H", "E_V", "C", "N")
_TERMS = {
    "low": [0.0, 0.0, 0.20, 0.45],
    "medium": [0.20, 0.40, 0.60, 0.80],
    "high": [0.45, 0.70, 1.0, 1.0],
    "present": [0.05, 0.25, 1.0, 1.0],
    "at_floor": [0.0, 0.0, 0.03, 0.12],
    "raised": [0.08, 0.20, 1.0, 1.0],
}
_OUTPUT_TERMS = {
    "moderate": [0.25, 0.45, 0.55, 0.75],
    "strong": [0.55, 0.80, 1.0, 1.0],
    "very_strong": [0.70, 0.90, 1.0, 1.0],
}


def _rule(name: str, state: str, *conditions: tuple[str, str],
          strength: str = "strong") -> dict:
    return {"id": name, "if": [list(c) for c in conditions], "then": state,
            "strength": strength, "weight": 1.0}


_GEOMETRY = (("A_star", "high"), ("E_H", "high"), ("E_V", "high"),
             ("C", "high"), ("N", "high"))
DEFAULT_WALL_RULES = [
    _rule("wall_full_height_floor", "Permanent", *_GEOMETRY, ("floor_contact", "high")),
    _rule("wall_full_height_ceiling", "Permanent", *_GEOMETRY, ("ceiling_contact", "high")),
    _rule("wall_full_height_junction", "Permanent", *_GEOMETRY, ("junction", "high")),
    _rule("wall_full_height_closure", "Permanent", *_GEOMETRY, ("room_closure", "high")),
    _rule("wall_semantic_and_geometry", "Permanent", ("semantic_wall", "high"),
          ("A_star", "present"), ("E_H", "high"), ("E_V", "high"),
          ("C", "high"), ("N", "high")),
    _rule("wall_semantic_confirmed", "Permanent", *_GEOMETRY,
          ("semantic_wall", "high"), ("floor_contact", "high"), strength="very_strong"),
    _rule("occluded_wall_continuation", "OccludedPermanent", ("occlusion", "high"),
          ("continuation", "high"), ("N", "high"), ("E_V", "high"),
          ("A_star", "present"), ("floor_contact", "high")),
    _rule("occluded_wall_room", "OccludedPermanent", ("occlusion", "high"),
          ("continuation", "high"), ("N", "high"), ("E_V", "high"),
          ("A_star", "present"), ("junction", "high"), ("room_closure", "high")),
    _rule("furniture_semantics", "Clutter", ("semantic_furniture", "high")),
    _rule("short_unanchored_surface", "Clutter", ("E_V", "low"),
          ("floor_contact", "low"), ("ceiling_contact", "low")),
    _rule("isolated_fragment", "Clutter", ("A_star", "low"), ("C", "low"),
          ("continuation", "low"), ("junction", "low"), strength="moderate"),
    _rule("contradictory_semantics", "Uncertain", ("semantic_wall", "high"),
          ("semantic_furniture", "high"), strength="very_strong"),
    _rule("wall_label_fragmented", "Uncertain", ("semantic_wall", "high"), ("C", "low")),
    _rule("unanchored_large_surface", "Uncertain", ("E_H", "high"), ("E_V", "high"),
          ("floor_contact", "low"), ("ceiling_contact", "low"),
          ("junction", "low"), ("room_closure", "low")),
]

DEFAULT_OPENING_RULES = [
    _rule("observed_passage", "OpenVoid", ("free_space", "high"), ("frame_support", "high")),
    _rule("door_observed_semantics", "Door", ("semantic_door", "high"),
          ("frame_support", "high"), ("sill_ratio", "at_floor")),
    _rule("window_observed_semantics", "Window", ("semantic_window", "high"),
          ("frame_support", "high"), ("sill_ratio", "raised")),
    _rule("door_catalog_agreement", "Door", ("semantic_door", "high"),
          ("frame_support", "high"), ("sill_ratio", "at_floor"),
          ("catalog_fit", "high"), strength="very_strong"),
    _rule("window_catalog_agreement", "Window", ("semantic_window", "high"),
          ("frame_support", "high"), ("sill_ratio", "raised"),
          ("catalog_fit", "high"), strength="very_strong"),
    _rule("conflicting_opening_labels", "Uncertain", ("semantic_door", "high"),
          ("semantic_window", "high"), strength="very_strong"),
    _rule("door_label_raised_sill", "Uncertain", ("semantic_door", "high"),
          ("sill_ratio", "raised")),
    _rule("window_label_floor_sill", "Uncertain", ("semantic_window", "high"),
          ("sill_ratio", "at_floor")),
]


def _validate_trapezoid(value: Sequence[float], label: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 4:
        raise ValueError(f"{label} must contain four trapezoid coordinates")
    try:
        points = tuple(float(x) for x in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not all(math.isfinite(x) and 0 <= x <= 1 for x in points):
        raise ValueError(f"{label} coordinates must be finite and within [0,1]")
    if list(points) != sorted(points) or points[0] == points[-1]:
        raise ValueError(f"{label} coordinates must be ordered and span a nonzero interval")
    return points


def trapezoid(x: Any, a: float, b: float, c: float, d: float) -> Any:
    """Trapezoidal membership with inclusive, correct left/right shoulders.

    Coordinates are assumed validated by the inference engine. A scalar gives
    a float and an array gives an ndarray; outside [a,d] membership is zero.
    """
    values = np.asarray(x, dtype=np.float64)
    left = np.ones_like(values) if a == b else (values - a) / (b - a)
    right = np.ones_like(values) if c == d else (d - values) / (d - c)
    result = np.where((values >= a) & (values <= d),
                      np.clip(np.minimum(left, right), 0.0, 1.0), 0.0)
    return float(result) if result.ndim == 0 else result


def _settings(config: Mapping | None) -> dict:
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        raise ValueError("config must be a mapping")
    nested = config.get("fuzzy", {})
    if isinstance(nested, bool):
        # Older pipeline configurations use fuzzy=True. This does not disable
        # the auditable inference implementation used here.
        nested = {}
    if not isinstance(nested, Mapping):
        raise ValueError("config.fuzzy must be a mapping")
    out = {**config, **nested}
    for key, default in (("decision_threshold", 0.55), ("ambiguity_margin", 0.12),
                         ("geometric_feedback_threshold", 0.55),
                         ("geometry_confidence_cap", 0.85), ("uncertain_confidence_cap", 0.49)):
        value = out.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{key} must be finite and within [0,1]")
        out[key] = float(value)
    samples = out.get("universe_samples", 501)
    if isinstance(samples, bool) or not isinstance(samples, int) or not 51 <= samples <= 10001:
        raise ValueError("universe_samples must be an integer in [51,10001]")
    out["universe_samples"] = samples
    return out


def _evidence(evidence: Mapping, keys: Sequence[str]) -> dict:
    if not isinstance(evidence, Mapping):
        raise ValueError("evidence must be a mapping")
    out = {}
    for key in keys:
        value = evidence.get(key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"evidence {key} must be numeric or None") from exc
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"evidence {key} must be finite and within [0,1]")
        out[key] = value
    return out


def _validated_model(settings: dict, kind: str, keys: Sequence[str], states: Sequence[str]) -> tuple:
    defaults = DEFAULT_WALL_RULES if kind == "wall" else DEFAULT_OPENING_RULES
    rules = settings.get(f"{kind}_rules", settings.get(f"fuzzy_{kind}_rules", defaults))
    if not isinstance(rules, list) or not rules or len(rules) > 1000:
        raise ValueError(f"{kind}_rules must contain between 1 and 1000 rules")
    memberships = {key: dict(_TERMS) for key in keys}
    if "A_star" in memberships:
        # Raster occupancy depends on scan density and cell size. Coverage of
        # 0.30 can support a wall when its extent, normals and contacts agree.
        memberships["A_star"]["high"] = [0.12, 0.42, 1.0, 1.0]
    overrides = settings.get("memberships", {})
    if not isinstance(overrides, Mapping):
        raise ValueError("memberships must be a mapping")
    for key, terms in overrides.items():
        if key not in (*WALL_KEYS, *OPENING_KEYS):
            raise ValueError(f"unknown evidence membership key {key!r}")
        if not isinstance(terms, Mapping):
            raise ValueError(f"memberships.{key} must be a mapping")
        for name, coordinates in terms.items():
            if not isinstance(name, str) or not name:
                raise ValueError("membership term names must be nonempty strings")
            points = _validate_trapezoid(coordinates, f"memberships.{key}.{name}")
            if key in memberships:
                memberships[key][name] = points
    outputs = dict(_OUTPUT_TERMS)
    output_overrides = settings.get("output_memberships", {})
    if not isinstance(output_overrides, Mapping):
        raise ValueError("output_memberships must be a mapping")
    for name, coordinates in output_overrides.items():
        if not isinstance(name, str) or not name:
            raise ValueError("output membership names must be nonempty strings")
        outputs[name] = _validate_trapezoid(coordinates, f"output_memberships.{name}")
    checked, names = [], set()
    for rule in rules:
        if not isinstance(rule, Mapping) or set(rule) - {"id", "if", "then", "strength", "weight"}:
            raise ValueError("each rule must use only id, if, then, strength and weight")
        name = rule.get("id")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("rule IDs must be unique nonempty strings")
        names.add(name)
        conditions = rule.get("if")
        if not isinstance(conditions, (list, tuple)) or not conditions:
            raise ValueError(f"rule {name}: if must be a nonempty list of [key,term]")
        for item in conditions:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError(f"rule {name}: malformed antecedent")
            key, term = item
            if not isinstance(key, str) or key not in memberships or not isinstance(term, str) or term not in memberships[key]:
                raise ValueError(f"rule {name}: unknown evidence or membership {item!r}")
        state, strength = rule.get("then"), rule.get("strength", "strong")
        if state not in states or strength not in outputs:
            raise ValueError(f"rule {name}: unknown output state or strength")
        weight = rule.get("weight", 1.0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or not 0 < weight <= 1:
            raise ValueError(f"rule {name}: weight must be within (0,1]")
        checked.append({"id": name, "if": conditions, "then": state,
                        "strength": strength, "weight": float(weight)})
    return checked, memberships, outputs


def _infer(evidence: Mapping, config: Mapping | None, kind: str) -> tuple[dict, dict, dict]:
    settings = _settings(config)
    keys, states = (WALL_KEYS, WALL_STATES) if kind == "wall" else (OPENING_KEYS, OPENING_STATES)
    values = _evidence(evidence, keys)
    rules, memberships, output_terms = _validated_model(settings, kind, keys, states)
    universe = np.linspace(0.0, 1.0, settings["universe_samples"])
    aggregates = {state: np.zeros_like(universe) for state in states}
    activations = {state: 0.0 for state in states}
    fired, skipped = [], []
    for rule in rules:
        missing = sorted({key for key, _ in rule["if"] if key not in values})
        if missing:
            skipped.append({"id": rule["id"], "reason": "missing_evidence", "missing": missing})
            continue
        antecedents = [{"key": key, "term": term, "value": values[key],
                        "membership": trapezoid(values[key], *memberships[key][term])}
                       for key, term in rule["if"]]
        activation = min(a["membership"] for a in antecedents) * rule["weight"]
        if activation <= 0:
            continue
        state = rule["then"]
        clipped = np.minimum(activation, trapezoid(universe, *output_terms[rule["strength"]]))
        aggregates[state] = np.maximum(aggregates[state], clipped)
        activations[state] = max(activations[state], activation)
        fired.append({"id": rule["id"], "state": state, "strength": rule["strength"],
                      "activation": float(activation), "weight": rule["weight"], "antecedents": antecedents})
    # np.trapezoid was introduced in NumPy 2; keep Windows NumPy 1 compatible.
    integrate = getattr(np, "trapezoid", None) or np.trapz
    centroids, scores = {}, {}
    for state, output in aggregates.items():
        area = float(integrate(output, universe))
        centroid = float(integrate(universe * output, universe) / area) if area > 0 else 0.0
        centroids[state] = centroid
        scores[state] = min(centroid, activations[state])
    result = {
        "state": "Uncertain", "confidence": 0.0, "scores": scores,
        "mamdani_centroids": centroids, "max_rule_activation": activations,
        "fired_rules": fired, "skipped_rules": skipped,
        "missing_evidence": [key for key in keys if key not in values],
        "observed_evidence": values, "decision_reasons": [],
        "confidence_kind": "uncalibrated_fuzzy_support_not_probability",
        "inference": "Mamdani_min_implication_max_aggregation_centroid",
    }
    return result, values, settings


def infer_wall(evidence: dict, config: dict | None = None) -> dict:
    """Infer Permanent/OccludedPermanent/Clutter/Uncertain, retaining audit.

    Geometric-only walls require observed full-height support and at least one
    architectural contact. Occlusion requires an explicit measured occlusion
    score *and* continuation; no occupancy-derived substitute is inserted.
    Strong furniture evidence blocks wall acceptance even with replacement rules.
    """
    result, values, settings = _infer(evidence, config, "wall")
    threshold = settings["decision_threshold"]
    scores = result["scores"]
    candidates = dict(scores)
    reasons = result["decision_reasons"]
    if not all(key in values for key in _CORE_WALL):
        candidates["Permanent"] = candidates["OccludedPermanent"] = 0.0
        reasons.append("incomplete_core_geometry")
    geometry_viable = all(values.get(key, 0.0) >= minimum for key, minimum in
                          (("A_star", 0.12), ("E_H", 0.40), ("E_V", 0.55), ("C", 0.30), ("N", 0.40)))
    anchored = any(values.get(key, 0.0) >= 0.45 for key in
                   ("floor_contact", "ceiling_contact", "junction", "room_closure"))
    semantic_supported = values.get("semantic_wall", 0.0) >= 0.55
    if not geometry_viable or not (anchored or semantic_supported):
        candidates["Permanent"] = 0.0
        reasons.append("insufficient_observed_wall_geometry_or_contacts")
    # Sparse occupancy and interrupted continuity may be explained by measured
    # occlusion, but a wall still needs a real oriented, extended surface.
    # Keep this guard independent of editable rules, as for observed walls.
    occluded_geometry_viable = all(values.get(key, 0.0) >= minimum for key, minimum in
                                   (("A_star", 0.12), ("E_H", 0.40), ("E_V", 0.55), ("N", 0.40)))
    if not occluded_geometry_viable or not (anchored or semantic_supported):
        candidates["OccludedPermanent"] = 0.0
        reasons.append("insufficient_observed_support_for_occluded_wall")
    if values.get("occlusion", 0.0) < 0.55 or values.get("continuation", 0.0) < 0.55:
        candidates["OccludedPermanent"] = 0.0
        if "occlusion" not in values:
            reasons.append("visibility_missing_occlusion_not_inferred")
    if values.get("semantic_furniture", 0.0) >= 0.70:
        if max(scores["Permanent"], scores["OccludedPermanent"]) >= threshold:
            candidates["Uncertain"] = max(candidates["Uncertain"], candidates["Clutter"],
                                          candidates["Permanent"], candidates["OccludedPermanent"])
            # Explicit conflict must not be resolved by arbitrary class ordering.
            candidates["Clutter"] = 0.0
            reasons.append("wall_geometry_conflicts_with_furniture_semantics")
        candidates["Permanent"] = candidates["OccludedPermanent"] = 0.0
    if not semantic_supported:
        for state in ("Permanent", "OccludedPermanent"):
            candidates[state] = min(candidates[state], settings["geometry_confidence_cap"])
    # Feedback measures the reconstructed geometry, not the original candidate.
    # Once a fitted instance fails that check, strong pre-fit rules cannot undo
    # the contradiction. Missing feedback is not treated as either success or
    # failure, so the initial inference pass remains possible.
    if "geometric_support" in values and values["geometric_support"] < settings["geometric_feedback_threshold"]:
        candidates["Uncertain"] = max(candidates["Uncertain"], candidates["Permanent"], candidates["OccludedPermanent"])
        candidates["Permanent"] = candidates["OccludedPermanent"] = 0.0
        reasons.append("reconstructed_geometry_failed_feedback")
    ordered = sorted(candidates, key=lambda key: candidates[key], reverse=True)
    winner = ordered[0]
    confidence = candidates[winner]
    # Permanent and OccludedPermanent are compatible wall conclusions; prefer
    # the observed wall when full geometry supports it as strongly.
    if winner in ("Permanent", "OccludedPermanent"):
        competitor = max(candidates["Clutter"], candidates["Uncertain"])
    else:
        competitor = max(candidates[state] for state in candidates if state != winner)
    if confidence <= 0.0 or confidence < threshold:
        winner = "Uncertain"
        reasons.append("insufficient_fuzzy_support")
    elif winner != "Uncertain" and confidence - competitor < settings["ambiguity_margin"]:
        winner = "Uncertain"
        reasons.append("competing_class_support")
    if winner == "Uncertain":
        confidence = min(confidence, settings["uncertain_confidence_cap"])
    result.update(state=winner, confidence=float(confidence), decision_scores=candidates,
                  accepted=winner in ("Permanent", "OccludedPermanent"),
                  geometry_status="inferred_completion" if winner == "OccludedPermanent" else "observed_candidate",
                  semantic_status="externally_observed" if semantic_supported else "inferred_from_geometry_and_topology")
    return result


def infer_opening(evidence: dict, config: dict | None = None) -> dict:
    """Infer an opening without promoting a raster hole to a semantic fill.

    OpenVoid needs measured free-space support plus an observed frame. Door or
    Window needs externally supplied semantic support plus a compatible frame
    and sill; a catalog match alone is never semantic evidence. The rectangular
    geometry remains a fitted hypothesis even when the semantic class is observed.
    """
    result, values, settings = _infer(evidence, config, "opening")
    threshold = settings["decision_threshold"]
    candidates = dict(result["scores"])
    reasons = result["decision_reasons"]
    frame_ok = values.get("frame_support", 0.0) >= 0.45
    door_ok = frame_ok and values.get("semantic_door", 0.0) >= 0.55 and values.get("sill_ratio", 1.0) <= 0.12
    window_ok = frame_ok and values.get("semantic_window", 0.0) >= 0.55 and values.get("sill_ratio", 0.0) >= 0.08
    void_ok = frame_ok and values.get("free_space", 0.0) >= 0.55
    for state, valid in (("Door", door_ok), ("Window", window_ok), ("OpenVoid", void_ok)):
        if not valid:
            candidates[state] = 0.0
    if "free_space" not in values:
        reasons.append("visibility_missing_hole_not_confirmed_void")
    if "semantic_door" not in values and "semantic_window" not in values:
        reasons.append("semantic_fill_unobserved")
    # A void supports a Door/Window hypothesis, it is not a competing fill class.
    fills = sorted(("Door", "Window"), key=lambda state: candidates[state], reverse=True)
    if candidates[fills[0]] > 0.0 and candidates[fills[0]] >= threshold:
        winner = fills[0]
        confidence = candidates[winner]
        competitor = max(candidates[fills[1]], candidates["Uncertain"])
        if confidence - competitor < settings["ambiguity_margin"]:
            winner = "Uncertain"
            reasons.append("competing_semantic_or_geometric_evidence")
    elif candidates["OpenVoid"] > 0.0 and candidates["OpenVoid"] >= threshold and candidates["Uncertain"] < candidates["OpenVoid"] - settings["ambiguity_margin"]:
        winner, confidence = "OpenVoid", candidates["OpenVoid"]
    else:
        winner, confidence = "Uncertain", max(candidates.values())
        reasons.append("insufficient_semantic_or_visibility_evidence")
    if winner == "Uncertain":
        confidence = min(confidence, settings["uncertain_confidence_cap"])
    fill = winner in ("Door", "Window")
    result.update(state=winner, confidence=float(confidence), decision_scores=candidates,
                  accepted=winner != "Uncertain", can_export_void=winner == "OpenVoid" or fill,
                  can_export_fill=fill,
                  geometry_status="inferred_rectangle_from_observed_frame" if winner != "Uncertain" else "unconfirmed_occupancy_hole",
                  semantic_status="externally_observed" if fill else "unclassified",
                  catalog_status="supporting_prior" if "catalog_fit" in values else "not_provided")
    return result
