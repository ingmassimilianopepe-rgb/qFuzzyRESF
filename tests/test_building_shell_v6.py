from pathlib import Path
import json
import subprocess
import sys

import numpy as np


def make_exterior_building(path: Path) -> None:
    rng = np.random.default_rng(31)
    # Irregular non-Manhattan footprint, deliberately unlike the indoor rectangular tests.
    ring = np.asarray([[0.0,0.0],[7.2,0.25],[8.0,3.2],[6.2,5.0],[2.3,5.35],[-0.5,3.4]])
    pts=[]
    # Full-height masonry facades with a door and a window-like vacancy on two edges.
    for ei,(a,b) in enumerate(zip(ring,np.vstack((ring[1:],ring[:1])))):
        v=b-a; L=float(np.linalg.norm(v)); t=v/L; n=np.asarray([-t[1],t[0]])
        for _ in range(6500):
            u=rng.uniform(0,L); z=rng.uniform(0,3.05)
            if ei==0 and 2.7<u<3.6 and z<2.15: continue
            if ei==1 and 0.9<u<2.2 and 1.0<z<2.1: continue
            q=a+t*u+n*rng.normal(0,0.006)
            pts.append((q[0],q[1],z+rng.normal(0,0.004)))
    # One genuine interior partition.
    a=np.asarray([2.2,0.1]); b=np.asarray([2.45,5.2]); v=b-a; L=np.linalg.norm(v); t=v/L; n=np.asarray([-t[1],t[0]])
    for _ in range(6000):
        u=rng.uniform(0,L); z=rng.uniform(0,3.0); q=a+t*u+n*rng.normal(0,0.005); pts.append((q[0],q[1],z))
    # Ground evidence.
    minx,miny=ring.min(axis=0); maxx,maxy=ring.max(axis=0)
    for _ in range(9000):
        pts.append((rng.uniform(minx,maxx),rng.uniform(miny,maxy),rng.normal(0,0.004)))
    # Gabled/sloped roof represented by two broad planes with ~1 m vertical relief.
    for _ in range(24000):
        x=rng.uniform(minx-0.15,maxx+0.15); y=rng.uniform(miny-0.15,maxy+0.15)
        ridge=2.55
        z=3.02 + 0.34*(ridge-abs(y-ridge)) + rng.normal(0,0.012)
        if z>=2.95: pts.append((x,y,z))
    # Low clutter panels must not become facade walls.
    for x0 in (1.2,1.45,1.7):
        for _ in range(700): pts.append((x0+rng.normal(0,0.006),rng.uniform(1.0,2.4),rng.uniform(0,1.45)))
    np.savetxt(path,np.asarray(pts),fmt="%.6f")


def test_exterior_shell_closes_facades_and_exports_roof(tmp_path: Path):
    root=Path(__file__).resolve().parents[1]; backend=root/"backend"; cloud=tmp_path/"historic_building.xyz"; out=tmp_path/"out"; out.mkdir(); make_exterior_building(cloud)
    request={"method":"Fuzzy-RESF-BIM","version":"6.0-building-envelope-graph","preset":"Fuzzy-RESF-BIM 6.0 (Adaptive building envelope)","input":str(cloud),"output":str(out),"plane_spacing_m":0.01,"plane_tolerance_m":0.03,"raster_cell_m":0.05,"angle_step_deg":2.0,"confidence_threshold":0.50,"max_orientation_families":12,"max_points":300000,"multi_peak":True,"topology":True,"occlusion":True,"fuzzy":True,"instance_consolidation":True,"geometric_feedback":True,"export_ifc":True,"export_diagnostics":True}
    req=tmp_path/"request.json"; req.write_text(json.dumps(request),encoding="utf-8")
    proc=subprocess.run([sys.executable,str(backend/"fuzzy_resf_cli.py"),"--request",str(req)],cwd=backend,capture_output=True,text=True,check=False)
    assert proc.returncode==0,proc.stderr
    result=json.loads((out/"result.json").read_text(encoding="utf-8"))
    assert result["status"]=="success",result
    assert result["version"]=="6.0-building-envelope-graph"
    assert result["scene_mode"]=="exterior_building_shell",result
    assert 5<=result["facade_walls"]<=24,result
    assert result["walls"]<=30,result
    assert result["wall_duplicate_pairs"]==0,result
    assert result["roof_facets"]>=1,result
    assert result["footprint_area"]>15.0,result
    roofs=(out/"roofs.csv").read_text(encoding="utf-8")
    assert "upper_envelope_ransac" in roofs
    ifc=(out/"fuzzy_resf.ifc").read_text(encoding="utf-8").upper()
    assert "IFCWALLSTANDARDCASE" in ifc
    assert "IFCSLAB" in ifc and ".ROOF." in ifc and "IFCFACETEDBREP" in ifc
