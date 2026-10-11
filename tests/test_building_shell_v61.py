from pathlib import Path
import json
import math
import subprocess
import sys

import numpy as np
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import bim_reconstruction as base
import building_shell_v6_1 as v61
from ifc_bim_export_v6 import write_bim_ifc


def test_regularized_facade_footprint_rejects_post_spikes():
    # Dense wall evidence around an 8 x 5 m building, an interior partition and three
    # isolated porch posts.  A concave hull can jump to those posts and create long fins;
    # the v6.1 morphological envelope must not.
    cell = 0.10
    xy = []
    for x in np.arange(0.0, 8.01, cell):
        xy += [[x, 0.0], [x, 5.0]]
    for y in np.arange(0.0, 5.01, cell):
        xy += [[0.0, y], [8.0, y]]
    for y in np.arange(0.4, 4.7, cell):
        xy.append([4.0, y])
    for cx, cy in ((9.1, 0.8), (9.1, 2.5), (9.1, 4.2)):
        for dx in (-0.08, 0.0, 0.08):
            for dy in (-0.08, 0.0, 0.08):
                xy.append([cx + dx, cy + dy])
    fp = v61._regularized_footprint(np.asarray(xy, dtype=float), cell)
    assert fp is not None
    assert 34.0 <= fp.area <= 47.0, fp.area
    assert fp.bounds[2] < 8.55, fp.bounds
    assert len(fp.exterior.coords) - 1 <= 24
    for p in ((9.1, 0.8), (9.1, 2.5), (9.1, 4.2)):
        assert fp.distance(Point(*p)) > 0.35


def test_v61_ifc_exports_compact_support_as_ifccolumn(tmp_path: Path):
    storey = base.Storey(1, 0.0, 3.0, 0.9)
    walls = [
        base.Wall(1, 1, 0, 0, 5, 0, 0, 3, .32, 90, .9, .9, 1.0, "closed_facade_envelope"),
        base.Wall(2, 1, 5, 0, 5, 4, 0, 3, .32, 0, .9, .9, 1.0, "closed_facade_envelope"),
        base.Wall(3, 1, 5, 4, 0, 4, 0, 3, .32, 90, .9, .9, 1.0, "closed_facade_envelope"),
        base.Wall(4, 1, 0, 4, 0, 0, 0, 3, .32, 0, .9, .9, 1.0, "closed_facade_envelope"),
    ]
    model = base.BIMModel([storey], walls, [], [], [], [], {"status": "success"})
    model.roofs = []
    model.columns = [v61.Column(1, 1, 6.1, 1.0, 0.0, 2.8, .28, .24, 90.0, .86)]
    path = tmp_path / "column.ifc"
    write_bim_ifc(model, path)
    text = path.read_text(encoding="utf-8").upper()
    assert "IFCCOLUMN" in text
    assert "PSET_FUZZYRESF_COLUMN" in text


def make_porch_building(path: Path) -> None:
    rng = np.random.default_rng(61)
    ring = np.asarray([[0.0, 0.0], [7.5, 0.2], [7.9, 4.7], [4.7, 5.5], [0.2, 4.8]])
    pts = []
    # Masonry shell with one facade door and one facade window.
    nxt = np.vstack((ring[1:], ring[:1]))
    for ei, (a, b) in enumerate(zip(ring, nxt)):
        v = b-a; L = float(np.linalg.norm(v)); t = v/L; n = np.asarray([-t[1], t[0]])
        for _ in range(4200):
            u = rng.uniform(0, L); z = rng.uniform(0, 3.0)
            if ei == 0 and 2.4 < u < 3.4 and z < 2.15:
                continue
            if ei == 1 and 1.0 < u < 2.1 and 0.9 < z < 2.0:
                continue
            q = a + t*u + n*rng.normal(0, .006)
            pts.append((q[0], q[1], z+rng.normal(0, .004)))
    # One real internal partition.
    a = np.asarray([3.7, .1]); b = np.asarray([3.8, 5.0]); v = b-a; L = np.linalg.norm(v); t = v/L; n = np.asarray([-t[1], t[0]])
    for _ in range(3800):
        u = rng.uniform(0, L); z = rng.uniform(0, 2.95); q = a+t*u+n*rng.normal(0, .005); pts.append((q[0], q[1], z))
    # Porch posts lie outside the masonry shell and must become columns, not wall spikes.
    for cx, cy in ((8.55, .9), (8.55, 2.5), (8.55, 4.1)):
        for _ in range(1800):
            ang = rng.uniform(0, 2*math.pi); rad = rng.uniform(0, .11); z = rng.uniform(0, 2.65)
            pts.append((cx+rad*math.cos(ang), cy+rad*math.sin(ang), z))
    # Main two-slope roof.
    minx, miny = ring.min(axis=0); maxx, maxy = ring.max(axis=0)
    ridge = 2.65
    for _ in range(15000):
        x = rng.uniform(minx-.25, maxx+.25); y = rng.uniform(miny-.3, maxy+.3)
        z = 3.0 + .30*(ridge-abs(y-ridge)) + rng.normal(0, .012)
        if z > 2.9: pts.append((x, y, z))
    # Lower porch roof supported by the posts, extending beyond the masonry footprint.
    for _ in range(6500):
        x = rng.uniform(7.2, 9.0); y = rng.uniform(.25, 4.75)
        z = 2.70 + .07*(x-7.2) + rng.normal(0, .01)
        pts.append((x, y, z))
    # Floor/ground evidence and short clutter panels.
    for _ in range(5500):
        pts.append((rng.uniform(minx, maxx), rng.uniform(miny, maxy), rng.normal(0, .004)))
    for x0 in (1.1, 1.35, 1.6):
        for _ in range(450): pts.append((x0+rng.normal(0, .006), rng.uniform(1.0, 2.3), rng.uniform(0, 1.35)))
    np.savetxt(path, np.asarray(pts), fmt="%.6f")


def test_v61_porch_scene_rejects_wall_fins_and_keeps_roof_support(tmp_path: Path):
    cloud = tmp_path / "porch_building.xyz"; out = tmp_path / "out"; out.mkdir(); make_porch_building(cloud)
    request = {"method":"Fuzzy-RESF-BIM","version":"6.1-robust-building-envelope","preset":"Fuzzy-RESF-BIM 6.1 (Robust historic building envelope)","input":str(cloud),"output":str(out),"plane_spacing_m":0.01,"plane_tolerance_m":0.03,"raster_cell_m":0.05,"angle_step_deg":2.0,"confidence_threshold":0.50,"max_orientation_families":12,"max_points":300000,"multi_peak":True,"topology":True,"occlusion":True,"fuzzy":True,"instance_consolidation":True,"geometric_feedback":True,"export_ifc":True,"export_diagnostics":True}
    req = tmp_path / "request.json"; req.write_text(json.dumps(request), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(BACKEND/"fuzzy_resf_cli.py"), "--request", str(req)], cwd=BACKEND, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    result = json.loads((out/"result.json").read_text(encoding="utf-8"))
    assert result["version"] == "6.1-robust-building-envelope", result
    assert result["scene_mode"] == "exterior_building_shell", result
    assert result["walls"] <= 28, result
    assert result["interior_partitions"] <= 6, result
    assert result["spaces"] >= 1, result
    assert result["roof_facets"] >= 1, result
    assert result["columns"] >= 1, result
    assert result["footprint_vertices"] <= 24, result
    ifc = (out/"fuzzy_resf.ifc").read_text(encoding="utf-8").upper()
    assert "IFCCOLUMN" in ifc
    assert "IFCSLAB" in ifc and ".ROOF." in ifc
    roofs = (out/"roofs.csv").read_text(encoding="utf-8")
    assert "connected_upper_envelope_ransac" in roofs
