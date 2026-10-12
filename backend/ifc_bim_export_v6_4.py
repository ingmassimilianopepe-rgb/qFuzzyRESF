"""IFC2X3 exporter wrapper for Fuzzy-RESF-BIM v6.4.

Reuses the validated v6 wall/opening/roof/column/space exporter and injects semantic
IfcStair/IfcStairFlight geometry for scan-derived stair flights.
"""
from __future__ import annotations

from pathlib import Path
import math
import re
import uuid

from ifc_bim_export_v6 import write_bim_ifc as _write_v6

_IFC64="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"

def _guid():
    n=uuid.uuid4().int; chars=[]
    for _ in range(22): chars.append(_IFC64[n&63]); n >>= 6
    return "".join(reversed(chars))

def _q(s): return "'"+str(s).replace("'","''")+"'"


def _inject_stairs(path: Path, model) -> None:
    stairs=list(getattr(model,"stairs",[]))
    if not stairs:
        return
    text=path.read_text(encoding="utf-8",errors="strict")
    ids=[int(x) for x in re.findall(r"#(\d+)=",text)]
    eid=max(ids,default=0)+1
    def add(expr):
        nonlocal eid
        s=f"#{eid}={expr};"; eid+=1; return eid-1,s

    owner_m=re.search(r"#(\d+)=IFCOWNERHISTORY\(",text,re.I)
    context_m=re.search(r"#(\d+)=IFCGEOMETRICREPRESENTATIONCONTEXT\(",text,re.I)
    if not owner_m or not context_m:
        raise RuntimeError("Cannot inject stairs: IFC owner/context entities not found")
    owner=int(owner_m.group(1)); context=int(context_m.group(1))
    zdir_id=None
    m=re.search(r"#(\d+)=IFCDIRECTION\(\(0(?:\.0*)?,0(?:\.0*)?,1(?:\.0*)?\)\)",text,re.I)
    if m: zdir_id=int(m.group(1))
    injected=[]
    if zdir_id is None:
        zdir_id,s=add("IFCDIRECTION((0.,0.,1.))"); injected.append(s)

    storey_ids={}
    for m in re.finditer(r"#(\d+)=IFCBUILDINGSTOREY\([^;]*?'Storey_(\d+)'[^;]*?\);",text,re.I):
        storey_ids[int(m.group(2))]=int(m.group(1))

    for st in stairs:
        theta=math.radians(float(st.theta_deg)); tx,ty=math.cos(theta),math.sin(theta)
        p,s=add(f"IFCCARTESIANPOINT(({float(st.x):.9f},{float(st.y):.9f},{float(st.z):.9f}))"); injected.append(s)
        r,s=add(f"IFCDIRECTION(({tx:.12f},{ty:.12f},0.))"); injected.append(s)
        ax,s=add(f"IFCAXIS2PLACEMENT3D(#{p},#{zdir_id},#{r})"); injected.append(s)
        pl,s=add(f"IFCLOCALPLACEMENT($,#{ax})"); injected.append(s)

        solids=[]
        n=max(1,int(st.step_count)); tread=max(.08,float(st.tread_depth)); width=max(.20,float(st.width)); rise=max(.05,float(st.riser_height))
        for i in range(n):
            cp,s=add(f"IFCCARTESIANPOINT(({(i+.5)*tread:.9f},0.))"); injected.append(s)
            a2,s=add(f"IFCAXIS2PLACEMENT2D(#{cp},$)"); injected.append(s)
            prof,s=add(f"IFCRECTANGLEPROFILEDEF(.AREA.,$,#{a2},{tread:.9f},{width:.9f})"); injected.append(s)
            o3,s=add("IFCCARTESIANPOINT((0.,0.,0.))"); injected.append(s)
            a3,s=add(f"IFCAXIS2PLACEMENT3D(#{o3},#{zdir_id},$)"); injected.append(s)
            solid,s=add(f"IFCEXTRUDEDAREASOLID(#{prof},#{a3},#{zdir_id},{(i+1)*rise:.9f})"); injected.append(s)
            solids.append(solid)
        rep,s=add(f"IFCSHAPEREPRESENTATION(#{context},'Body','SweptSolid',({','.join('#'+str(x) for x in solids)}))"); injected.append(s)
        shape,s=add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))"); injected.append(s)
        stair,s=add(f"IFCSTAIR({_q(_guid())},#{owner},{_q('Stair_'+str(st.id))},$,$,#{pl},$, $,.STRAIGHT_RUN_STAIR.)"); injected.append(s)
        flight,s=add(f"IFCSTAIRFLIGHT({_q(_guid())},#{owner},{_q('StairFlight_'+str(st.id))},$,$,#{pl},#{shape},$, {n},{n},{rise:.9f},{tread:.9f})"); injected.append(s)
        rel,s=add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{stair},(#{flight}))"); injected.append(s)
        sid=storey_ids.get(int(st.storey_id))
        if sid:
            rel,s=add(f"IFCRELCONTAINEDINSPATIALSTRUCTURE({_q(_guid())},#{owner},$,$,(#{stair}),#{sid})"); injected.append(s)
        pc,s=add(f"IFCPROPERTYSINGLEVALUE('ObjectConfidence',$,IFCREAL({float(st.confidence):.6f}),$)"); injected.append(s)
        ps,s=add(f"IFCPROPERTYSET({_q(_guid())},#{owner},'Pset_FuzzyRESF_Stair',$,(#{pc}))"); injected.append(s)
        rr,s=add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{stair},#{flight}),#{ps})"); injected.append(s)

    marker="\nENDSEC;\nEND-ISO-10303-21;"
    if marker not in text:
        raise RuntimeError("Cannot inject stairs: IFC DATA terminator not found")
    text=text.replace(marker,"\n"+"\n".join(injected)+marker,1)
    path.write_text(text,encoding="utf-8")


def write_bim_ifc(model, path: str|Path, project_name: str="Fuzzy-RESF-BIM v6.4") -> None:
    path=Path(path)
    _write_v6(model,path,project_name=project_name)
    _inject_stairs(path,model)
