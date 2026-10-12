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
import building_shell_v6_4 as v64


def _wall(i, x1, y1, x2, y2, source, confidence=.90, gf=.90, th=.30):
    dx, dy = x2-x1, y2-y1
    theta = math.degrees(math.atan2(-dx, dy)) % 180.0
    return base.Wall(i, 1, x1, y1, x2, y2, 0.0, 3.0, th, theta,
                     confidence, .9, .9, source, "Permanent",
                     .8, .8, .9, .7, .8, .1, gf)


def test_v64_removes_facade_microcorners():
    fp = Polygon([(0,0),(2,.02),(2.18,.08),(4,-.02),(6,.03),(8,0),(8.02,5),(6,5.02),(4,4.98),(2,5.01),(0,5),(0,0)])
    support=[]
    for x in np.linspace(0,8,161): support += [(x,0),(x,5)]
    for y in np.linspace(0,5,101): support += [(0,y),(8,y)]
    out=v64.regularize_facade_footprint(fp,np.asarray(support,float),.08)
    assert out.is_valid
    assert 38 <= out.area <= 42
    assert len(out.exterior.coords)-1 <= 8


def test_v64_rooms_from_wall_solids_close_small_endpoint_gaps():
    fp=Polygon([(0,0),(8,0),(8,6),(0,6)])
    s=base.Storey(1,0,3,.9)
    walls=[
        _wall(1,0,0,8,0,"closed_facade_envelope"),_wall(2,8,0,8,6,"closed_facade_envelope"),
        _wall(3,8,6,0,6,"closed_facade_envelope"),_wall(4,0,6,0,0,"closed_facade_envelope"),
        _wall(5,4,.38,4,5.62,"paired_height_persistent_faces"),
        _wall(6,.35,3,7.65,3,"paired_height_persistent_faces")]
    rooms=v64.spaces_from_wall_solids(fp,walls,s)
    assert len(rooms)==4,[(r.name,r.area) for r in rooms]
    assert all(r.area>7.0 for r in rooms)


def test_v64_detects_regular_porch_stair_flight():
    rng=np.random.default_rng(64)
    pts=[]
    width=1.35; tread=.30; rise=.17; n=6
    # Six horizontal tread strips, plus a little acquisition noise.
    for i in range(n):
        z=i*rise
        for _ in range(850):
            x=rng.uniform(i*tread,(i+1)*tread)
            y=rng.uniform(-width/2,width/2)
            pts.append((x,y,z+rng.normal(0,.007)))
    # Sparse ground/background should not itself form a stair sequence.
    for _ in range(1200):
        pts.append((rng.uniform(-1,3),rng.uniform(-2,2),rng.normal(0,.01)))
    pts=np.asarray(pts,float)
    fp=Polygon([(1.5,-2),(5,-2),(5,2),(1.5,2)])
    s=base.Storey(1,0,3,.9)
    stairs=v64.detect_stair_flights(pts,fp,s,.05)
    assert stairs, "regular repeated treads were not detected"
    st=max(stairs,key=lambda x:x.step_count)
    assert st.step_count>=5
    assert .12<=st.riser_height<=.21
    assert .20<=st.tread_depth<=.40
    assert 1.0<=st.width<=1.7


def test_v64_regularizes_zigzag_roof_boundary():
    f=v64.RoofFacet(1,1,[
        [0,0,3.0],[2,.04,3.2],[4,-.03,3.4],[6,.02,3.6],[8,0,3.8],
        [8,2,4.0],[6,2.03,3.8],[4,1.98,3.6],[2,2.02,3.4],[0,2,3.2]],.14,.9,"test")
    fp=Polygon([(0,0),(8,0),(8,5),(0,5)])
    out=v64.regularize_roof_facets([f],fp,.05)
    assert len(out)==1
    poly=Polygon([(v[0],v[1]) for v in out[0].vertices])
    assert poly.is_valid
    assert len(poly.exterior.coords)-1<=6
    assert poly.area>=14.0
