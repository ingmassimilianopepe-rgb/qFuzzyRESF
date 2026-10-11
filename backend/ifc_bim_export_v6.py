"""IFC2X3 exporter for Fuzzy-RESF-BIM v6.1.

Exports semantic walls/openings/spaces, explicit sloped roof facets and compact structural
supports as IfcColumn. Runtime geometry comes only from the current scan model.
"""
from __future__ import annotations

from pathlib import Path
import math
import uuid

_IFC64="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"

def _guid():
    n=uuid.uuid4().int; chars=[]
    for _ in range(22): chars.append(_IFC64[n&63]); n >>= 6
    return "".join(reversed(chars))

def _q(s): return "'"+str(s).replace("'","''")+"'"

def write_bim_ifc(model, path: str|Path, project_name: str="Fuzzy-RESF-BIM v6.1") -> None:
    path=Path(path)
    if not model.walls: raise ValueError("Refusing IFC export: BIM contains no walls")
    ent=[]; eid=1
    def add(expr):
        nonlocal eid
        ent.append(f"#{eid}={expr};"); eid+=1; return eid-1

    owner=add("IFCOWNERHISTORY($,$,$,.ADDED.,$,$,$,0)")
    unit=add("IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)"); units=add(f"IFCUNITASSIGNMENT((#{unit}))")
    origin=add("IFCCARTESIANPOINT((0.,0.,0.))"); zdir=add("IFCDIRECTION((0.,0.,1.))"); xdir=add("IFCDIRECTION((1.,0.,0.))")
    world=add(f"IFCAXIS2PLACEMENT3D(#{origin},#{zdir},#{xdir})")
    context=add(f"IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#{world},$)")
    project=add(f"IFCPROJECT({_q(_guid())},#{owner},{_q(project_name)},$,$,$,$,(#{context}),#{units})")
    site_place=add(f"IFCLOCALPLACEMENT($,#{world})"); site=add(f"IFCSITE({_q(_guid())},#{owner},'Site',$,$,#{site_place},$,$,.ELEMENT.,$,$,$,$,$)")
    building_place=add(f"IFCLOCALPLACEMENT(#{site_place},#{world})"); building=add(f"IFCBUILDING({_q(_guid())},#{owner},'Building',$,$,#{building_place},$,$,.ELEMENT.,$,$,$)")
    add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{project},(#{site}))"); add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{site},(#{building}))")

    storey_ifc={}; storey_place={}
    for s in model.storeys:
        pl=add(f"IFCLOCALPLACEMENT(#{building_place},#{world})")
        st=add(f"IFCBUILDINGSTOREY({_q(_guid())},#{owner},{_q('Storey_'+str(s.id))},$,$,#{pl},$,$,.ELEMENT.,{float(s.z0):.9f})")
        storey_ifc[int(s.id)]=st; storey_place[int(s.id)]=pl
        add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{building},(#{st}))")

    def placement(x,y,z,tx=1.,ty=0.,parent=None):
        p=add(f"IFCCARTESIANPOINT(({x:.9f},{y:.9f},{z:.9f}))"); ref=add(f"IFCDIRECTION(({tx:.12f},{ty:.12f},0.))"); ax=add(f"IFCAXIS2PLACEMENT3D(#{p},#{zdir},#{ref})")
        return add(f"IFCLOCALPLACEMENT(#{building_place if parent is None else parent},#{ax})")

    def rect_shape(L,D,H):
        L=max(float(L),1e-3); D=max(float(D),1e-3); H=max(float(H),1e-3)
        p2=add(f"IFCCARTESIANPOINT(({L/2:.9f},0.))"); ax2=add(f"IFCAXIS2PLACEMENT2D(#{p2},$)")
        prof=add(f"IFCRECTANGLEPROFILEDEF(.AREA.,$,#{ax2},{L:.9f},{D:.9f})"); sax=add(f"IFCAXIS2PLACEMENT3D(#{origin},#{zdir},#{xdir})")
        solid=add(f"IFCEXTRUDEDAREASOLID(#{prof},#{sax},#{zdir},{H:.9f})"); rep=add(f"IFCSHAPEREPRESENTATION(#{context},'Body','SweptSolid',(#{solid}))")
        return add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")

    def poly_shape(poly,height):
        ox,oy=float(poly[0][0]),float(poly[0][1]); refs=[]
        for x,y in poly: refs.append(add(f"IFCCARTESIANPOINT(({float(x)-ox:.9f},{float(y)-oy:.9f}))"))
        refs.append(refs[0]); pline=add(f"IFCPOLYLINE(({','.join('#'+str(r) for r in refs)}))"); prof=add(f"IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,$,#{pline})")
        sax=add(f"IFCAXIS2PLACEMENT3D(#{origin},#{zdir},#{xdir})"); solid=add(f"IFCEXTRUDEDAREASOLID(#{prof},#{sax},#{zdir},{max(float(height),1e-3):.9f})")
        rep=add(f"IFCSHAPEREPRESENTATION(#{context},'Body','SweptSolid',(#{solid}))"); return add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))"),ox,oy

    def brep_shape(vertices,thickness):
        if len(vertices)<3: raise ValueError("Roof facet needs at least three vertices")
        top=[]; bot=[]; th=max(float(thickness),0.02)
        for x,y,z in vertices: top.append(add(f"IFCCARTESIANPOINT(({float(x):.9f},{float(y):.9f},{float(z):.9f}))"))
        for x,y,z in vertices: bot.append(add(f"IFCCARTESIANPOINT(({float(x):.9f},{float(y):.9f},{float(z)-th:.9f}))"))
        faces=[]
        def face(refs):
            loop=add(f"IFCPOLYLOOP(({','.join('#'+str(r) for r in refs)}))"); bound=add(f"IFCFACEOUTERBOUND(#{loop},.T.)"); return add(f"IFCFACE((#{bound}))")
        faces.append(face(top)); faces.append(face(list(reversed(bot))))
        n=len(top)
        for i in range(n): faces.append(face([top[i],top[(i+1)%n],bot[(i+1)%n],bot[i]]))
        shell=add(f"IFCCLOSEDSHELL(({','.join('#'+str(f) for f in faces)}))"); brep=add(f"IFCFACETEDBREP(#{shell})")
        rep=add(f"IFCSHAPEREPRESENTATION(#{context},'Body','Brep',(#{brep}))"); return add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")

    contained={int(s.id):[] for s in model.storeys}; wall_ifc={}; wall_by={int(w.id):w for w in model.walls}
    for w in model.walls:
        dx,dy=float(w.x2-w.x1),float(w.y2-w.y1); L=max(math.hypot(dx,dy),1e-6); tx,ty=dx/L,dy/L
        pl=placement(w.x1,w.y1,w.z0,tx,ty,storey_place.get(int(w.storey_id))); shape=rect_shape(L,w.thickness,w.z1-w.z0)
        obj=add(f"IFCWALLSTANDARDCASE({_q(_guid())},#{owner},{_q('Wall_'+str(w.id))},$,$,#{pl},#{shape},$)"); wall_ifc[int(w.id)]=obj; contained.setdefault(int(w.storey_id),[]).append(obj)
        p1=add(f"IFCPROPERTYSINGLEVALUE('ObjectConfidence',$,IFCREAL({float(w.confidence):.6f}),$)"); p2=add(f"IFCPROPERTYSINGLEVALUE('ReconstructionSource',$,IFCLABEL({_q(w.source)}),$)"); p3=add(f"IFCPROPERTYSINGLEVALUE('ClassificationState',$,IFCLABEL({_q(getattr(w,'state','Permanent'))}),$)")
        ps=add(f"IFCPROPERTYSET({_q(_guid())},#{owner},'Pset_FuzzyRESF_BIM',$,(#{p1},#{p2},#{p3}))"); add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{obj}),#{ps})")

    for c in getattr(model,"columns",[]):
        ang=math.radians(float(c.theta_deg)); tx,ty=math.cos(ang),math.sin(ang)
        x=float(c.x)-0.5*float(c.width)*tx; y=float(c.y)-0.5*float(c.width)*ty
        pl=placement(x,y,float(c.z0),tx,ty,storey_place.get(int(c.storey_id))); shape=rect_shape(float(c.width),float(c.depth),max(0.05,float(c.z1-c.z0)))
        obj=add(f"IFCCOLUMN({_q(_guid())},#{owner},{_q('Column_'+str(c.id))},$,$,#{pl},#{shape},$)"); contained.setdefault(int(c.storey_id),[]).append(obj)
        pc=add(f"IFCPROPERTYSINGLEVALUE('ObjectConfidence',$,IFCREAL({float(c.confidence):.6f}),$)"); ps=add(f"IFCPROPERTYSET({_q(_guid())},#{owner},'Pset_FuzzyRESF_Column',$,(#{pc}))"); add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{obj}),#{ps})")

    for o in model.openings:
        w=wall_by.get(int(o.wall_id)); host=wall_ifc.get(int(o.wall_id))
        if w is None or host is None: continue
        dx,dy=w.x2-w.x1,w.y2-w.y1; L=max(math.hypot(dx,dy),1e-6); tx,ty=dx/L,dy/L; start=max(0.,float(o.offset)-float(o.width)/2)
        x=w.x1+tx*start; y=w.y1+ty*start; z=w.z0+o.sill; parent=storey_place.get(int(o.storey_id))
        opl=placement(x,y,z,tx,ty,parent); ops=rect_shape(o.width,max(.12,1.35*w.thickness),o.height); op=add(f"IFCOPENINGELEMENT({_q(_guid())},#{owner},{_q('Opening_'+str(o.id))},$,$,#{opl},#{ops},$)"); add(f"IFCRELVOIDSELEMENT({_q(_guid())},#{owner},$,$,#{host},#{op})")
        fpl=placement(x,y,z,tx,ty,parent); fs=rect_shape(o.width,max(.035,.30*w.thickness),o.height)
        if str(o.kind).lower()=="door": fill=add(f"IFCDOOR({_q(_guid())},#{owner},{_q('Door_'+str(o.id))},$,$,#{fpl},#{fs},$,{float(o.height):.9f},{float(o.width):.9f})")
        else: fill=add(f"IFCWINDOW({_q(_guid())},#{owner},{_q('Window_'+str(o.id))},$,$,#{fpl},#{fs},$,{float(o.height):.9f},{float(o.width):.9f})")
        contained.setdefault(int(o.storey_id),[]).append(fill); add(f"IFCRELFILLSELEMENT({_q(_guid())},#{owner},$,$,#{op},#{fill})")

    for slab in model.slabs:
        if len(slab.polygon)<3: continue
        shape,ox,oy=poly_shape(slab.polygon,slab.thickness); kind=str(slab.kind).upper(); z=slab.z-slab.thickness if kind=="CEILING" else slab.z; pl=placement(ox,oy,z,1.,0.,storey_place.get(int(slab.storey_id)))
        if kind=="CEILING": obj=add(f"IFCCOVERING({_q(_guid())},#{owner},{_q('Ceiling_'+str(slab.id))},$,$,#{pl},#{shape},$,.CEILING.)")
        else:
            typ=".ROOF." if kind=="ROOF" else ".FLOOR."; name="Roof" if kind=="ROOF" else "Floor"; obj=add(f"IFCSLAB({_q(_guid())},#{owner},{_q(name+'_'+str(slab.id))},$,$,#{pl},#{shape},$,{typ})")
        contained.setdefault(int(slab.storey_id),[]).append(obj)

    for roof in getattr(model,"roofs",[]):
        if len(roof.vertices)<3: continue
        shape=brep_shape(roof.vertices,roof.thickness); pl=add(f"IFCLOCALPLACEMENT(#{building_place},#{world})")
        obj=add(f"IFCSLAB({_q(_guid())},#{owner},{_q('RoofFacet_'+str(roof.id))},$,$,#{pl},#{shape},$,.ROOF.)"); contained.setdefault(int(roof.storey_id),[]).append(obj)
        pc=add(f"IFCPROPERTYSINGLEVALUE('ObjectConfidence',$,IFCREAL({float(roof.confidence):.6f}),$)"); ps=add(f"IFCPROPERTYSET({_q(_guid())},#{owner},'Pset_FuzzyRESF_Roof',$,(#{pc}))"); add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{obj}),#{ps})")

    for s in model.spaces:
        if len(s.polygon)<3: continue
        shape,ox,oy=poly_shape(s.polygon,max(.10,s.z1-s.z0)); pl=placement(ox,oy,s.z0,1.,0.,storey_place.get(int(s.storey_id))); obj=add(f"IFCSPACE({_q(_guid())},#{owner},{_q(s.name)},$,$,#{pl},#{shape},$,.ELEMENT.,.INTERNAL.,$)"); contained.setdefault(int(s.storey_id),[]).append(obj)

    for sid,objs in contained.items():
        st=storey_ifc.get(sid)
        if st and objs: add(f"IFCRELCONTAINEDINSPATIALSTRUCTURE({_q(_guid())},#{owner},$,$,({','.join('#'+str(o) for o in objs)}),#{st})")

    text="ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');\nFILE_NAME('fuzzy_resf.ifc','',$,$,'qFuzzyRESF','Fuzzy-RESF-BIM v6.1','');\nFILE_SCHEMA(('IFC2X3'));\nENDSEC;\nDATA;\n"+"\n".join(ent)+"\nENDSEC;\nEND-ISO-10303-21;\n"
    path.write_text(text,encoding="utf-8")
