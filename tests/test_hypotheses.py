"""Behavioral tests for experimental uncalibrated evidence/hypothesis selection."""
from types import SimpleNamespace
import numpy as np
import pytest
from fuzzyresf.hypotheses import evidence, select_candidates


def candidate(score, start, end, quality=.9):
    return {"geometry_pass": True,
            "evidence": {"score": score, "observability": quality,
                         "support_interval": [max(0, score-.1), min(1, score+.1)]},
            "relation_geometry": {"kind": "wall", "start": start,
                                  "end": end, "base": 0., "top": 3.}}


def test_missing_support_remains_unknown_not_negative():
    e = evidence(.85, .99, .015, .01, coverage=0.)
    assert e["support_interval"] == [0., 1.]
    assert e["score"] == .5
    assert e["status"] == "DEFERRED_UNKNOWN"
    chosen, audit = select_candidates([{"geometry_pass": True, "evidence": e}], "evidence")
    assert chosen == []
    assert "not labels of absent" in audit["unknown_semantics"]


def test_joint_corner_can_retain_ambiguous_supported_wall():
    # Local fit alone defers the weaker wall. An observed compatible junction
    # changes the finite joint decision; no wall or junction is manufactured.
    rows = [candidate(.90, [0, 0], [4, 0]), candidate(.56, [4, 0], [4, 3])]
    local, _ = select_candidates(rows, "evidence")
    joint, audit = select_candidates(rows, "relational")
    assert local == [0]
    assert joint == [0, 1]
    assert audit["changed_candidate_indices"] == [1]
    assert audit["relations"][0]["type"] == "corner_or_t_junction"
    # A distant ambiguous candidate gets no such rescue.
    rows[1]["relation_geometry"]["start"] = [20, 20]
    rows[1]["relation_geometry"]["end"] = [20, 23]
    assert select_candidates(rows, "relational")[0] == [0]


def test_duplicate_conflict_does_not_remove_opposite_observed_faces():
    rows = [candidate(.80, [0, 0], [4, 0]),
            candidate(.91, [0, .02], [4, .02]),
            candidate(.85, [0, .20], [4, .20])]
    selected, audit = select_candidates(rows, "relational")
    assert selected == [1, 2]
    assert audit["duplicate_conflicts"] == [[0, 1]]
    assert any(e["type"] == "paired_faces" and e["active"] for e in audit["relations"])


def test_geometry_veto_cannot_be_compensated_by_context():
    rows = [candidate(.95, [0, 0], [4, 0]), candidate(.95, [4, 0], [4, 3])]
    rows[1]["geometry_pass"] = False
    assert select_candidates(rows, "relational")[0] == [0]


def test_solver_timeout_uses_deterministic_feasible_fallback(monkeypatch):
    import scipy.optimize
    monkeypatch.setattr(scipy.optimize, "milp", lambda *a, **k: SimpleNamespace(status=1, x=np.array([1., 1., 1., 1.])))
    rows = [candidate(.80, [0, 0], [4, 0]),
            candidate(.91, [0, .02], [4, .02]),
            candidate(.56, [4, .02], [4, 3])]
    first, audit = select_candidates(rows, "relational")
    assert first == [1, 2]
    assert select_candidates(rows, "relational")[0] == first
    assert audit["solver"] == "DETERMINISTIC_FALLBACK_NONOPTIMAL_SOLVER"
    assert audit["optimal"] is False


def test_insufficiently_observed_candidate_is_not_rescued_by_many_connections():
    weak = candidate(.8, [0, 0], [4, 0], quality=.1)
    rows = [weak, candidate(.9, [4, 0], [4, 3])]
    assert select_candidates(rows, "relational")[0] == [1]


def test_relational_resource_limit_is_explicit():
    rows = [candidate(.9, [0, 0], [4, 0]), candidate(.8, [4, 0], [4, 3])]
    chosen, audit = select_candidates(rows, "relational", max_candidates=1)
    assert chosen == [0, 1]
    assert audit["relation_resource_limit"] is True
    assert audit["solver"] == "INDEPENDENT_RESOURCE_LIMIT"


@pytest.mark.parametrize("planarity", [.25, .4, .7, .95])
def test_support_bounds_are_ordered_and_finite(planarity):
    e = evidence(planarity, .97, .035, .012, .6)
    lo, hi = e["support_interval"]
    assert 0 <= lo <= e["score"] <= hi <= 1
    assert e["calibration_status"] == "EXPERT_UNCALIBRATED"
