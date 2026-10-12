import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import bim_reconstruction as base
import building_shell_v6_5 as v65


def _wall(i, x1, y1, x2, y2, source="paired_height_persistent_faces", confidence=.90, gf=.90, th=.30):
    dx, dy = x2-x1, y2-y1
    theta = math.degrees(math.atan2(-dx, dy)) % 180.0
    return base.Wall(i, 1, x1, y1, x2, y2, 0.0, 3.0, th, theta,
                     confidence, .9, .9, source, "Permanent",
                     .8, .8, .9, .7, .8, .1, gf)


def test_v65_rejects_facade_scale_false_window():
    w = _wall(1, 0, 0, 8, 0, "closed_facade_envelope")
    # Deliberately enormous vacancy similar to the real regression screenshot.
    o = base.Opening(1, 1, 1, "Window", 4.0, 6.2, 0.20, 2.6, .91)
    rng = np.random.default_rng(650)
    pts = np.column_stack((rng.uniform(0, 8, 1500), rng.normal(0, .03, 1500), rng.uniform(0, 3, 1500)))
    out = v65.filter_openings(pts, [w], [o], .05)
    assert out == []


def test_v65_room_graph_follows_exported_partitions():
    fp = Polygon([(0,0),(8,0),(8,6),(0,6)])
    s = base.Storey(1,0,3,.9)
    walls = [
        _wall(1,0,0,8,0,"closed_facade_envelope"),
        _wall(2,8,0,8,6,"closed_facade_envelope"),
        _wall(3,8,6,0,6,"closed_facade_envelope"),
        _wall(4,0,6,0,0,"closed_facade_envelope"),
        _wall(5,4,.20,4,5.80),
        _wall(6,.20,3,7.80,3),
    ]
    rooms = v65.spaces_from_evidence_graph(fp, walls, s)
    assert len(rooms) == 4, [(r.name, r.area) for r in rooms]
    assert all(r.area > 7.0 for r in rooms)


def test_v65_supported_roof_does_not_keep_serrated_boundary():
    rng = np.random.default_rng(651)
    pts = []
    # one regular sloped plane, 8 x 2 m
    for _ in range(16000):
        x = rng.uniform(0,8)
        y = rng.uniform(0,2)
        z = 3.0 + .12*x + rng.normal(0,.008)
        pts.append((x,y,z))
    pts = np.asarray(pts, float)
    facet = v65.RoofFacet(1,1,[
        [0,0,3.0],[1.5,.04,3.18],[3,-.03,3.36],[4.5,.05,3.54],[6,-.02,3.72],[8,0,3.96],
        [8,2,3.96],[6,2.04,3.72],[4.5,1.97,3.54],[3,2.03,3.36],[1.5,1.96,3.18],[0,2,3.0]
    ],.14,.9,"test")
    fp = Polygon([(0,0),(8,0),(8,5),(0,5)])
    s = base.Storey(1,0,3,.9)
    out = v65.regularize_roof_supported(pts,[facet],fp,s,.05)
    assert len(out) == 1
    poly = Polygon([(v[0],v[1]) for v in out[0].vertices])
    assert poly.is_valid
    assert len(poly.exterior.coords)-1 <= 6
    assert 13.0 <= poly.area <= 18.0


def test_v65_detects_stairs_below_storey_datum():
    rng = np.random.default_rng(652)
    pts=[]
    width=1.25; tread=.30; rise=.17; n=6; zbase=-1.0
    for i in range(n):
        z=zbase+i*rise
        for _ in range(900):
            x=rng.uniform(i*tread,(i+1)*tread)
            y=rng.uniform(-width/2,width/2)
            pts.append((x,y,z+rng.normal(0,.006)))
    for _ in range(800):
        pts.append((rng.uniform(-1,3),rng.uniform(-2,2),rng.normal(-1.05,.01)))
    pts=np.asarray(pts,float)
    fp=Polygon([(1.2,-2),(5,-2),(5,2),(1.2,2)])
    # storey datum is well above the lower stair treads
    s=base.Storey(1,.45,3.2,.9)
    stairs=v65.detect_stairs_multilevel(pts,fp,s,.05)
    assert stairs, "below-datum stair flight was not detected"
    st=max(stairs,key=lambda x:x.step_count)
    assert st.step_count >= 5
    assert .12 <= st.riser_height <= .21
    assert .20 <= st.tread_depth <= .40


def test_v65_preserves_real_piecewise_bend_while_removing_micro_jitter():
    # A real multi-segment historic facade bend must survive; only tiny oscillations vanish.
    fp=Polygon([(0,0),(2,.02),(4,.55),(6,1.65),(7.5,3.2),(7.52,5.0),(0,5),(0,0)])
    out=v65._microcorner_clean(fp,.08)
    assert out.is_valid
    assert len(out.exterior.coords)-1 >= 5
    assert out.area > 25
