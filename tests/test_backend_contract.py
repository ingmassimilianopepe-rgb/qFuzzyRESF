from pathlib import Path
import csv
import json
import math
import subprocess
import sys

import numpy as np


def make_room_cloud(path: Path) -> None:
    rng=np.random.default_rng(7); pts=[]
    for _ in range(12000):
        x=rng.uniform(0,6); z=rng.uniform(0,2.8)
        if not (2.55<=x<=3.45 and z<=2.1): pts.append((x,rng.normal(0,0.004),z))
    for _ in range(12000):
        x=rng.uniform(0,6); z=rng.uniform(0,2.8)
        if not (1.8<=x<=3.2 and 0.9<=z<=2.0): pts.append((x,4+rng.normal(0,0.004),z))
    for _ in range(8000):
        y=rng.uniform(0,4); z=rng.uniform(0,2.8)
        pts.append((rng.normal(0,0.004),y,z)); pts.append((6+rng.normal(0,0.004),y,z))
    for _ in range(5000):
        pts.append((rng.uniform(0,6),rng.uniform(0,4),rng.normal(0,0.003)))
        pts.append((rng.uniform(0,6),rng.uniform(0,4),2.8+rng.normal(0,0.003)))
    for _ in range(1200): pts.append((rng.uniform(2,3),2+rng.normal(0,0.01),rng.uniform(0,1.2)))
    for y0 in (0.12,0.18,0.24,0.31,0.39,0.47):
        for _ in range(900): pts.append((rng.uniform(0.45,1.95),y0+rng.normal(0,0.004),rng.uniform(0,1.65)))
    for angle in (28.0,42.0,57.0):
        th=np.deg2rad(angle); u=np.array([np.cos(th),np.sin(th)]); n=np.array([-u[1],u[0]])
        for _ in range(450):
            s=rng.uniform(-0.55,0.55); z=rng.uniform(0,1.45); xy=np.array([1.25,0.65])+u*s+n*rng.normal(0,0.004)
            pts.append((xy[0],xy[1],z))
    np.savetxt(path,np.asarray(pts),fmt="%.6f")


def _wall_debug(rows):
    out=[]
    for r in rows:
        out.append({"id":r["id"],"xy":[round(float(r["x1"]),2),round(float(r["y1"]),2),round(float(r["x2"]),2),round(float(r["y2"]),2)],"L":round(math.hypot(float(r["x2"])-float(r["x1"]),float(r["y2"])-float(r["y1"])),2),"theta":round(float(r["theta_deg"]),1),"conf":round(float(r["confidence"]),2),"EV":round(float(r["resf_EV"]),2),"source":r["source"],"state":r["state"]})
    return out


def test_cli_contract(tmp_path: Path):
    root=Path(__file__).resolve().parents[1]; backend=root/"backend"; cloud=tmp_path/"room.xyz"; out=tmp_path/"out"; out.mkdir(); make_room_cloud(cloud)
    request={"method":"Fuzzy-RESF-BIM","version":"5.1-persistent-architectural-graph","preset":"Fuzzy-RESF-BIM 5.1 (Persistent architectural graph)","input":str(cloud),"output":str(out),"plane_spacing_m":0.01,"plane_tolerance_m":0.03,"raster_cell_m":0.05,"angle_step_deg":2.0,"confidence_threshold":0.50,"max_orientation_families":12,"max_points":250000,"multi_peak":True,"topology":True,"occlusion":True,"fuzzy":True,"instance_consolidation":True,"geometric_feedback":True,"export_ifc":True,"export_diagnostics":True}
    request_path=tmp_path/"request.json"; request_path.write_text(json.dumps(request),encoding="utf-8")
    proc=subprocess.run([sys.executable,str(backend/"fuzzy_resf_cli.py"),"--request",str(request_path)],cwd=backend,capture_output=True,text=True,check=False)
    assert proc.returncode==0,proc.stderr
    for name in ("result.json","bim_model.json","walls.csv","openings.csv","spaces.csv","slabs.csv","resf_candidates.csv","fuzzy_resf.ifc"): assert (out/name).exists(),name
    assert (out/"fuzzy_resf.ifc").stat().st_size>1024
    result=json.loads((out/"result.json").read_text(encoding="utf-8"))
    with (out/"walls.csv").open(newline="",encoding="utf-8") as f: rows=list(csv.DictReader(f))
    debug=_wall_debug(rows)
    if not (4<=result["walls"]<=6):
        print("BIM WALL REGRESSION SUMMARY",json.dumps(result,sort_keys=True))
        for wall in debug: print("BIM WALL",json.dumps(wall,sort_keys=True))
    assert result["status"]=="success"
    assert result["version"]=="5.1-persistent-architectural-graph"
    assert result["resf_role"]=="evidence_and_confidence_not_direct_wall_geometry"
    assert result["exact_frozen_v1_reproduction"] is False
    assert 4<=result["walls"]<=6,result
    assert result["wall_duplicate_pairs"]==0,result
    assert result["wall_junction_ratio"]>=0.50,result
    assert result["spaces"]>=1 and result["slabs"]>=2
    assert result["doors"]>=1 and result["windows"]>=1
    assert rows and "state" in rows[0]
    lengths=[math.hypot(float(r["x2"])-float(r["x1"]),float(r["y2"])-float(r["y1"])) for r in rows]
    assert min(lengths)>=0.55 and max(lengths)<=7.0
    assert all(float(r["z1"])-float(r["z0"])>=2.2 for r in rows)
    model=json.loads((out/"bim_model.json").read_text(encoding="utf-8"))
    assert len(model["walls"])==result["walls"]
    assert any(o["kind"]=="Door" for o in model["openings"])
    assert any(o["kind"]=="Window" for o in model["openings"])
    assert model["spaces"]
    assert any(s["kind"]=="FLOOR" for s in model["slabs"])
    assert any(s["kind"]=="CEILING" for s in model["slabs"])
    ifc=(out/"fuzzy_resf.ifc").read_text(encoding="utf-8").upper()
    for token in ("IFCBUILDINGSTOREY","IFCWALLSTANDARDCASE","IFCOPENINGELEMENT","IFCRELVOIDSELEMENT","IFCRELFILLSELEMENT","IFCDOOR","IFCWINDOW","IFCSLAB","IFCCOVERING","IFCSPACE"): assert token in ifc,token
