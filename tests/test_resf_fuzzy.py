"""Behavioral tests of measured evidence, ambiguity and Mamdani inference."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from resf_fuzzy import infer_opening, infer_wall, trapezoid


def wall(**changes):
    evidence = dict(A_star=0.8, E_H=0.95, E_V=0.97, C=0.9, N=0.95,
                    floor_contact=0.95, ceiling_contact=0.8, junction=0.9,
                    room_closure=0.8, continuation=0.85)
    evidence.update(changes)
    return evidence


def test_trapezoid_shoulders_are_inclusive_and_bounded():
    assert trapezoid(0.0, 0, 0, 0.2, 0.4) == 1.0
    assert trapezoid(1.0, 0.4, 0.8, 1, 1) == 1.0
    assert trapezoid(0.2, 0.2, 0.4, 0.7, 0.9) == 0.0
    np.testing.assert_allclose(trapezoid(np.array([-0.1, 0, 0.1, 0.3, 0.5]), 0, 0, 0.2, 0.4),
                               [0, 1, 1, 0.5, 0])


def test_geometry_wall_accepts_without_fabricated_semantics():
    result = infer_wall(wall(), {})
    assert result["state"] == "Permanent"
    assert result["accepted"]
    assert 0.55 <= result["confidence"] <= 0.85
    assert "semantic_wall" in result["missing_evidence"]
    assert "semantic_wall" not in result["observed_evidence"]
    assert result["confidence_kind"] == "uncalibrated_fuzzy_support_not_probability"
    assert all("semantic_wall" not in [a["key"] for a in rule["antecedents"]]
               for rule in result["fired_rules"])


def test_sparse_raster_coverage_can_be_supported_by_full_height_contacts():
    result = infer_wall(wall(A_star=0.30))
    assert result["state"] == "Permanent"
    assert 0.55 <= result["confidence"] <= 0.65


def test_missing_visibility_never_creates_occluded_permanent():
    result = infer_wall(wall(A_star=0.3, C=0.2))
    assert result["state"] == "Uncertain"
    assert result["scores"]["OccludedPermanent"] == 0
    assert not any(rule["state"] == "OccludedPermanent" for rule in result["fired_rules"])
    assert any(rule["id"] == "occluded_wall_continuation" and "occlusion" in rule["missing"]
               for rule in result["skipped_rules"])


def test_measured_occlusion_and_continuation_support_inferred_completion():
    result = infer_wall(wall(A_star=0.3, C=0.2, occlusion=0.95))
    assert result["state"] == "OccludedPermanent"
    assert result["geometry_status"] == "inferred_completion"
    assert 0.55 < result["confidence"] <= 0.85


def test_occlusion_alone_is_not_a_wall():
    result = infer_wall({"occlusion": 1.0, "continuation": 1.0})
    assert result["state"] == "Uncertain"
    assert not result["accepted"]


def test_strong_furniture_conflicts_with_geometric_wall():
    result = infer_wall(wall(semantic_furniture=0.95))
    assert result["state"] == "Uncertain"
    assert not result["accepted"]
    assert result["confidence"] <= 0.49
    assert "wall_geometry_conflicts_with_furniture_semantics" in result["decision_reasons"]


def test_short_unanchored_furniture_is_clutter():
    result = infer_wall(wall(E_V=0.10, floor_contact=0.0, ceiling_contact=0.0,
                             semantic_furniture=0.95))
    assert result["state"] == "Clutter"
    assert not result["accepted"]


def test_competing_semantic_observations_remain_uncertain():
    result = infer_wall(wall(semantic_wall=0.98, semantic_furniture=0.98))
    assert result["state"] == "Uncertain"
    assert {r["state"] for r in result["fired_rules"]} >= {"Permanent", "Clutter", "Uncertain"}


def test_empty_and_explicitly_missing_evidence_never_invent_support():
    empty, missing = infer_wall({}), infer_wall({"A_star": None, "occlusion": None})
    assert empty == missing
    assert empty["state"] == "Uncertain"
    assert empty["confidence"] == 0
    assert not empty["fired_rules"]
    assert all(score == 0 for score in empty["scores"].values())


def test_deterministic_serializable_audit():
    first = infer_wall(wall(occlusion=0.75))
    second = infer_wall(wall(occlusion=0.75))
    assert first == second
    json.dumps(first, allow_nan=False)
    for rule in first["fired_rules"]:
        assert rule["activation"] == min(a["membership"] for a in rule["antecedents"]) * rule["weight"]


def test_centroid_uses_clipped_output_and_activation_limits_decision():
    override = [{"id": "custom_floor_wall", "if": [["floor_contact", "high"]],
                 "then": "Permanent", "strength": "strong"}]
    result = infer_wall(wall(floor_contact=0.50, ceiling_contact=0, junction=0, room_closure=0),
                        {"wall_rules": override})
    assert result["max_rule_activation"]["Permanent"] == pytest.approx(0.2)
    # The clipped consequent centroid remains high; decision support must not.
    assert result["mamdani_centroids"]["Permanent"] > 0.7
    assert result["scores"]["Permanent"] == pytest.approx(0.2)
    assert result["state"] == "Uncertain"


def test_valid_rule_and_membership_override_is_audited():
    config = {"fuzzy": {
        "wall_rules": [{"id": "custom", "if": [["A_star", "dense"]], "then": "Permanent"}],
        "memberships": {"A_star": {"dense": [0.1, 0.2, 1.0, 1.0]}}}}
    result = infer_wall(wall(), config)
    assert result["state"] == "Permanent"
    assert [r["id"] for r in result["fired_rules"]] == ["custom"]


def test_override_cannot_claim_occlusion_without_measurement():
    rules = [{"id": "invalid_scientific_claim", "if": [["A_star", "high"]],
              "then": "OccludedPermanent"}]
    result = infer_wall(wall(), {"wall_rules": rules})
    assert result["scores"]["OccludedPermanent"] > 0
    assert result["decision_scores"]["OccludedPermanent"] == 0
    assert result["state"] == "Uncertain"


def test_failed_reconstructed_geometry_overrides_pre_fit_wall_support():
    original = infer_wall(wall())
    unsupported = infer_wall(wall(geometric_support=0.20))
    confirmed = infer_wall(wall(geometric_support=0.90))
    assert original["accepted"] and confirmed["accepted"]
    assert unsupported["scores"]["Permanent"] == original["scores"]["Permanent"]
    assert unsupported["state"] == "Uncertain"
    assert not unsupported["accepted"]
    assert "reconstructed_geometry_failed_feedback" in unsupported["decision_reasons"]


def test_measured_occlusion_does_not_override_failed_geometry_feedback():
    result = infer_wall(wall(A_star=0.3, C=0.2, occlusion=0.95, geometric_support=0.2))
    assert result["scores"]["OccludedPermanent"] > 0.55
    assert result["state"] == "Uncertain"
    assert not result["accepted"]


def test_custom_occlusion_rule_cannot_invent_an_oriented_wall_surface():
    rule = {"id": "occlusion_only", "if": [["occlusion", "high"]],
            "then": "OccludedPermanent"}
    result = infer_wall(wall(occlusion=1.0, N=0.0), {"wall_rules": [rule]})
    assert result["scores"]["OccludedPermanent"] > 0.55
    assert result["state"] == "Uncertain"
    assert not result["accepted"]


def test_low_supplied_semantic_score_does_not_confirm_semantic_wall():
    rule = {"id": "strong_geometry", "if": [["A_star", "high"]],
            "then": "Permanent", "strength": "very_strong"}
    result = infer_wall(wall(semantic_wall=0.0), {"wall_rules": [rule]})
    assert result["state"] == "Permanent"
    assert result["confidence"] <= 0.85
    assert result["semantic_status"] == "inferred_from_geometry_and_topology"


@pytest.mark.parametrize("infer", [infer_wall, infer_opening])
def test_zero_decision_threshold_cannot_create_an_unobserved_object(infer):
    result = infer({}, {"decision_threshold": 0.0, "ambiguity_margin": 0.0})
    assert result["state"] == "Uncertain"
    assert not result["accepted"]
    assert result["confidence"] == 0.0


@pytest.mark.parametrize("config", [
    {"wall_rules": []},
    {"wall_rules": [{"id": "bad", "if": [["invented", "high"]], "then": "Permanent"}]},
    {"wall_rules": [{"id": "bad", "if": [["A_star", "unknown"]], "then": "Permanent"}]},
    {"wall_rules": [{"id": "bad", "if": [], "then": "Permanent"}]},
    {"wall_rules": [{"id": "bad", "if": [["A_star", "high"]], "then": "Permanent", "weight": 2}]},
    {"memberships": {"A_star": {"high": [0.8, 0.2, 1, 1]}}},
    {"universe_samples": 10},
    {"decision_threshold": float("nan")},
])
def test_invalid_configuration_fails_explicitly(config):
    with pytest.raises(ValueError):
        infer_wall(wall(), config)


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf"), "not a measurement"])
def test_invalid_evidence_fails_explicitly(value):
    with pytest.raises(ValueError):
        infer_wall(wall(A_star=value))


def test_hole_shape_and_catalog_never_generate_a_fill_or_void():
    result = infer_opening({"sill_ratio": 0.0, "frame_support": 0.95,
                            "catalog_fit": 0.99, "vacancy": 1, "rectangularity": 1})
    assert result["state"] == "Uncertain"
    assert not result["can_export_fill"]
    assert not result["can_export_void"]
    assert "free_space" in result["missing_evidence"]


def test_measured_crossing_rays_allow_void_but_no_semantic_fill():
    result = infer_opening({"sill_ratio": 0.0, "frame_support": 0.95, "free_space": 0.9})
    assert result["state"] == "OpenVoid"
    assert result["can_export_void"]
    assert not result["can_export_fill"]
    assert result["semantic_status"] == "unclassified"


@pytest.mark.parametrize("semantic,sill,state", [("semantic_door", 0.0, "Door"),
                                                 ("semantic_window", 0.35, "Window")])
def test_observed_labels_and_compatible_frame_allow_semantic_object(semantic, sill, state):
    result = infer_opening({"sill_ratio": sill, "frame_support": 0.95, semantic: 0.95})
    assert result["state"] == state
    assert result["can_export_fill"]
    assert result["can_export_void"]
    assert result["semantic_status"] == "externally_observed"
    assert result["geometry_status"] == "inferred_rectangle_from_observed_frame"


def test_visibility_and_door_semantics_are_compatible_conclusions():
    result = infer_opening({"sill_ratio": 0.0, "frame_support": 0.95,
                            "semantic_door": 0.95, "free_space": 0.95})
    assert result["state"] == "Door"


def test_contradictory_opening_labels_remain_uncertain():
    result = infer_opening({"sill_ratio": 0.0, "frame_support": 0.95,
                            "semantic_door": 0.95, "semantic_window": 0.95})
    assert result["state"] == "Uncertain"
    assert not result["can_export_fill"]


def test_opening_rule_override_cannot_invent_semantics():
    rule = {"id": "only_a_hole", "if": [["frame_support", "high"]], "then": "Door"}
    result = infer_opening({"sill_ratio": 0, "frame_support": 1}, {"opening_rules": [rule]})
    assert result["scores"]["Door"] > 0
    assert result["state"] == "Uncertain"
    assert not result["can_export_fill"]
