import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import bim_reconstruction as base
import building_shell_v7 as v7
import object_knowledge_v7 as ok


def _wall(i, source="closed_facade_envelope"):
    return base.Wall(i, 1, 0.0, float(i), 8.0, float(i), 0.0, 3.0, .32, 90.0,
                     .90, .9, .9, source, "Permanent", .8, .8, .9, .7, .8, .1, .9)


def test_v7_scan_abacus_learns_repeated_door_type():
    walls = [_wall(1), _wall(2), _wall(3), _wall(4)]
    openings = [
        base.Opening(1,1,1,"Door",2.0,.90,0.0,2.05,.86),
        base.Opening(2,2,1,"Door",2.0,.92,0.0,2.04,.84),
        base.Opening(3,3,1,"Door",2.0,.89,0.0,2.07,.88),
        base.Opening(4,4,1,"Door",2.0,1.48,0.0,2.35,.82),
    ]
    refined, abacus = ok.refine_openings_with_abacus(openings, walls)
    assert len(refined) == 4
    repeated = [a for a in abacus if a["object_class"] == "Door" and a["count"] >= 3]
    assert repeated, abacus
    a = repeated[0]
    assert .88 <= a["width_median_m"] <= .93
    assert 2.00 <= a["height_median_m"] <= 2.10
    assert sum(getattr(o, "type_id", "") == a["type_id"] for o in refined) >= 3


def test_v7_dm236_is_soft_prior_not_geometry_override():
    w = _wall(1)
    o = base.Opening(1,1,1,"Door",2.0,.68,0.0,1.96,.92)
    refined, _ = ok.refine_openings_with_abacus([o], [w])
    assert len(refined) == 1
    # Historic/non-compliant measured geometry must not be fabricated into an 0.80 m door.
    assert abs(refined[0].width - .68) < 1e-9
    assert "below_DM236" in getattr(refined[0], "standards_status", "")


def test_v7_roof_support_cells_do_not_bridge_large_gap():
    cell = .15
    left = np.array([(x, y) for x in np.arange(0, 1.21, cell) for y in np.arange(0, 1.21, cell)], float)
    right = np.array([(x, y) for x in np.arange(3.0, 4.21, cell) for y in np.arange(0, 1.21, cell)], float)
    polys = v7._support_cell_polygons(np.vstack([left, right]), cell)
    assert len(polys) == 2, [p.area for p in polys]
    assert all(p.area < 2.5 for p in polys)


def test_v7_registry_does_not_embed_proprietary_component_dimensions():
    reg = ok.load_registry()
    assert reg["policy"]["certification"] is False
    assert reg["accessibility"]["door_access_min_clear_width_m"] == .80
    assert reg["accessibility"]["other_door_min_clear_width_m"] == .75
    assert all(x.get("numeric_geometry_priors_embedded") is False for x in reg["product_and_component_standards"])


def test_v7_candidate_topology_requires_relational_support():
    c = base.RESFCandidate(1,1,90.0,0.0,0.0,4.0,0.0,3.0,
                           .8,.8,.8,.8,.8,.1,.8,.7,
                           .1,.1,.1,.1,.1,.1,1.0,.8,"Permanent")
    low = v7._candidate_topology_score(c)
    c.T_W = .9; c.T_R = .8; c.T_CP = .8; c.T_F = .8; c.T_C = .8
    high = v7._candidate_topology_score(c)
    assert high > low + .35
