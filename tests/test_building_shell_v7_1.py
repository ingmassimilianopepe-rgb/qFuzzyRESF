import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import bim_reconstruction as base
import building_shell_v7_1 as v71


def _wall(i, x0, x1, y=0.0, source="paired_height_persistent_faces", gf=.75, conf=.82):
    return base.Wall(i, 1, x0, y, x1, y, 0.0, 3.0, .20, 90.0,
                     conf, .78, .80, source, "Permanent", .72, .75, .82, .72, .78, .10, gf)


def _candidate(i, t0, t1, *, cp=.1, occ=.1, conf=.80, state="Permanent"):
    # theta=90 deg => tangent direction is -X; d controls Y. Negative t values map to +X.
    return base.RESFCandidate(i, 1, 90.0, 0.0, t0, t1, 0.0, 3.0,
                              .82, .80, .82, .78, .80, occ, .82, .75,
                              .80, .80, .80, .75, .70, cp, 1.0, conf, state)


def test_v71_unsupported_opening_sized_gap_is_preserved():
    a = _wall(1, 0.0, 2.0)
    b = _wall(2, 3.0, 5.0)
    merged = v71.conservative_merge_collinear([a, b], [])
    assert len(merged) == 2


def test_v71_gap_merges_only_with_coplanar_occlusion_continuation_evidence():
    a = _wall(1, 0.0, 2.0)
    b = _wall(2, 3.0, 5.0)
    # Candidate tangent interval spans x=0..5 (t=-5..0), including the 1 m gap.
    c = _candidate(1, -5.0, 0.0, cp=.90, occ=.86, conf=.91, state="OccludedPermanent")
    merged = v71.conservative_merge_collinear([a, b], [c])
    assert len(merged) == 1
    assert merged[0].source == "supported_gap_completion"
    assert merged[0].state == "OccludedPermanent"


def test_v71_feedback_gate_withholds_unsupported_inferred_wall():
    w = _wall(1, 0.0, 3.0, source="high_consensus_resf_recovery", gf=.01, conf=.74)
    ok, q, d = v71._object_wall_decision(w, [])
    assert not ok
    assert q.state == "Uncertain"
    assert d["reason"].startswith("uncertain_")


def test_v71_feedback_gate_can_keep_occluded_wall_with_relational_evidence():
    w = _wall(1, 0.0, 3.0, source="high_consensus_resf_recovery", gf=.03, conf=.78)
    w.resf_EV = .82
    w.resf_O = .72
    c = _candidate(1, -3.0, 0.0, cp=.90, occ=.88, conf=.92, state="OccludedPermanent")
    ok, q, d = v71._object_wall_decision(w, [c])
    assert ok
    assert q.state == "OccludedPermanent"
    assert d["occlusion_support"] >= .55


def test_v71_roof_coverage_is_local_to_each_polygon():
    cell = .15
    dense = np.array([(x, y) for x in np.arange(0, 1.21, cell) for y in np.arange(0, 1.21, cell)], float)
    sparse_other = np.array([[3.0, 0.0], [3.15, 0.0], [3.0, .15]], float)
    xy = np.vstack([dense, sparse_other])
    poly_other = Polygon([(2.9, -.1), (3.4, -.1), (3.4, .4), (2.9, .4)])
    coverage, local = v71._polygon_support_coverage(xy, poly_other, cell)
    assert int(local.sum()) <= 3
    assert coverage < .40


def test_v71_columns_embedded_in_wall_are_not_exported():
    class C:
        pass
    c1 = C(); c1.id=1; c1.storey_id=1; c1.x=1.0; c1.y=.02; c1.width=.30; c1.depth=.30
    c2 = C(); c2.id=2; c2.storey_id=1; c2.x=1.0; c2.y=1.0; c2.width=.30; c2.depth=.30
    kept = v71.filter_columns_against_walls([c1, c2], [_wall(1, 0.0, 4.0)])
    assert len(kept) == 1
    assert abs(kept[0].y - 1.0) < 1e-9
