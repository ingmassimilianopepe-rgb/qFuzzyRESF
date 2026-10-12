import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, Point

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import bim_reconstruction as base
import building_shell_v6_3 as v63


def _wall(i, x1, y1, x2, y2, source, confidence=.90, gf=.90, th=.30):
    dx, dy = x2-x1, y2-y1
    theta = math.degrees(math.atan2(-dx, dy)) % 180.0
    return base.Wall(i, 1, x1, y1, x2, y2, 0.0, 3.0, th, theta,
                     confidence, .9, .9, source, "Permanent",
                     .8, .8, .9, .7, .8, .1, gf)


def test_v63_regularizes_wavy_facade_without_global_manhattan():
    # Saw-tooth perturbations along an otherwise clean 8 x 5 m shell.
    ring = [
        (0,0),(1.3,.04),(2.6,-.03),(4.0,.05),(5.4,-.02),(6.8,.03),(8,0),
        (8.04,1.2),(7.97,2.5),(8.03,3.8),(8,5),
        (6.5,5.03),(5.0,4.96),(3.5,5.04),(2.0,4.97),(0,5),
        (-.03,3.7),(.04,2.4),(-.02,1.1),(0,0)
    ]
    fp = Polygon(ring)
    support = []
    for x in np.linspace(0, 8, 161):
        support += [(x,0.0),(x,5.0)]
    for y in np.linspace(0, 5, 101):
        support += [(0.0,y),(8.0,y)]
    out = v63.regularize_facade_footprint(fp, np.asarray(support, dtype=float), .08)
    assert out.is_valid
    assert 38.0 <= out.area <= 42.0
    assert len(out.exterior.coords)-1 <= 10
    c = np.asarray(out.exterior.coords)
    seg = c[1:] - c[:-1]
    L = np.linalg.norm(seg, axis=1)
    ang = np.degrees(np.arctan2(seg[:,1], seg[:,0])) % 180.0
    for a, l in zip(ang, L):
        if l >= 1.0:
            assert min(abs(a-0), abs(a-90), abs(a-180)) <= 7.0


def test_v63_polygonizes_four_rooms_from_validated_partition_graph():
    fp = Polygon([(0,0),(8,0),(8,6),(0,6)])
    s = base.Storey(1, 0.0, 3.0, .9)
    walls = [
        _wall(1,0,0,8,0,"closed_facade_envelope"),
        _wall(2,8,0,8,6,"closed_facade_envelope"),
        _wall(3,8,6,0,6,"closed_facade_envelope"),
        _wall(4,0,6,0,0,"closed_facade_envelope"),
        _wall(5,4,0.12,4,5.88,"paired_height_persistent_faces"),
        _wall(6,0.12,3,7.88,3,"paired_height_persistent_faces"),
    ]
    rooms = v63.spaces_from_partition_graph(fp, walls, s)
    assert len(rooms) == 4, [(r.name, r.area) for r in rooms]
    assert all(r.area > 8.0 for r in rooms)
    assert sum(r.area for r in rooms) > 42.0
    assert all(r.name.startswith("Room_") for r in rooms)


def test_v63_roof_planes_remain_connected_and_scan_supported():
    rng = np.random.default_rng(63)
    pts = []
    # Dense gable roof over an 8 x 5 m shell; ridge runs along X.
    for _ in range(22000):
        x = rng.uniform(0.0, 8.0)
        y = rng.uniform(0.0, 5.0)
        z = 3.0 + 0.34 * (2.5 - abs(y-2.5)) + rng.normal(0, .012)
        pts.append((x,y,z))
    # Detached lower porch roof: separate component, not a bridge between main facets.
    for _ in range(5500):
        x = rng.uniform(8.4, 10.0)
        y = rng.uniform(.4, 4.6)
        z = 2.72 + .08*(x-8.4) + rng.normal(0,.01)
        pts.append((x,y,z))
    pts = np.asarray(pts, dtype=float)
    fp = Polygon([(0,0),(8,0),(8,5),(0,5)])
    s = base.Storey(1, 0.0, 3.0, .9)
    roofs = v63.fit_roof_facets(pts, fp, s, .05)
    assert len(roofs) >= 2
    assert len(roofs) <= 8
    assert all(r.source == "component_supported_upper_envelope_plane" for r in roofs)
    # No facet is allowed to grow into an unsupported far-away strip.
    for r in roofs:
        poly = Polygon([(v[0],v[1]) for v in r.vertices])
        assert poly.is_valid and poly.area >= .8
        assert poly.bounds[0] >= -.35
        assert poly.bounds[2] <= 10.35
        assert poly.bounds[1] >= -.35
        assert poly.bounds[3] <= 5.35
