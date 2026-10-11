"""Fuzzy-RESF-BIM v6.1: robust historic-building envelope.

v6.1 keeps the v6 RESF/fuzzy evidence layer but fixes four real-scan failure modes:
(1) facade footprints that jump to posts/internal walls, (2) roof facets extrapolated across
unsupported gaps, (3) too many interior partitions, and (4) porch posts misclassified as walls.
No reference BIM is read at runtime.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from shapely.geometry import LineString, MultiPoint, Point, Polygon

import bim_reconstruction as base
import architectural_graph_v5 as core
import architectural_graph_v5_1 as indoor
import building_shell_v6 as v6
from topology_completion import geometric_feedback

RoofFacet=v6.RoofFacet
_LAST_FOOTPRINT=None
_LAST_COMPONENT_DIAG={}
_ORIG_PARTITIONS=v6._interior_partitions

@dataclass
class Column:
    id:int; storey_id:int; x:float; y:float; z0:float; z1:float
    width:float; depth:float; theta_deg:float; confidence:float
    source:str="isolated_vertical_persistence"


def _components(xy:np.ndarray,radius:float)->list[np.ndarray]:
    n=len(xy)
    if not n:return []
    r=max(float(radius),1e-3); o=xy.min(axis=0); ij=np.floor((xy-o)/r).astype(np.int64)
    buckets={}
    for i,q in enumerate(ij): buckets.setdefault((int(q[0]),int(q[1])),[]).append(i)
    parent=np.arange(n); size=np.ones(n,dtype=int)
    def find(a):
        a=int(a)
        while parent[a]!=a: parent[a]=parent[parent[a]]; a=int(parent[a])
        return a
    def union(a,b):
        a,b=find(a),find(b)
        if a==b:return
        if size[a]<size[b]:a,b=b,a
        parent[b]=a; size[a]+=size[b]
    rr=r*r
    for (gx,gy),ids in buckets.items():
        neigh=[]
        for dx in (-1,0,1):
            for dy in (-1,0,1): neigh+=buckets.get((gx+dx,gy+dy),[])
        ni=np.asarray(neigh,dtype=int)
        for i in ids:
            d=xy[ni]-xy[i]
            for j in ni[np.einsum('ij,ij->i',d,d)<=rr]:
                if j>i:union(i,int(j))
    roots=np.asarray([find(i) for i in range(n)])
    return [np.flatnonzero(roots==u) for u in np.unique(roots)]


def _regularized_footprint(xy:np.ndarray,cell:float)->Polygon|None:
    if len(xy)<20:return None
    # Buffered vertical-support cells + closing is intentionally used instead of a concave
    # hull: internal walls cannot pull the exterior boundary inward and isolated posts remain
    # disconnected. This was the main source of the long fins visible in v6 screenshots.
    buf=float(np.clip(1.75*cell,.12,.17)); close=float(np.clip(8.0*cell,.58,.78))
    g=MultiPoint(xy.tolist()).buffer(buf,resolution=1).buffer(close,join_style=2).buffer(-close,join_style=2)
    if g.geom_type=="MultiPolygon":g=max(g.geoms,key=lambda q:q.area)
    if g.geom_type!="Polygon" or g.area<=2:return None
    q=g.buffer(-.05,join_style=2).buffer(.05,join_style=2)
    if q.geom_type=="MultiPolygon":q=max(q.geoms,key=lambda p:p.area)
    if q.geom_type=="Polygon" and q.area>=.88*g.area:g=q
    a=g.area; best=g; tol=max(.16,2.2*cell)
    for _ in range(7):
        q=g.simplify(tol,preserve_topology=True)
        if q.geom_type!="Polygon" or q.area<.90*a:break
        best=q
        if len(q.exterior.coords)-1<=22:break
        tol*=1.18
    return best


def _estimate_shell(pts:np.ndarray,raster:float):
    global _LAST_FOOTPRINT,_LAST_COMPONENT_DIAG
    xy,zmin,zmax,cell=v6._facade_columns(pts,raster)
    if len(xy)<20:raise RuntimeError("Insufficient vertically persistent facade support")
    comps=_components(xy,max(.34,3.2*cell)); sizes=sorted((len(c) for c in comps),reverse=True)
    fp=_regularized_footprint(xy,cell)
    if fp is None:raise RuntimeError("Could not reconstruct robust closed facade footprint")
    z0=float(np.quantile(zmin,.45)); eave=float(np.quantile(zmax,.52))
    if eave-z0<1.8:z0,eave=map(float,np.quantile(pts[:,2],[.05,.70]))
    _LAST_FOOTPRINT=fp; _LAST_COMPONENT_DIAG={"components":len(comps),"largest":sizes[0] if sizes else 0,"columns":len(xy)}
    return fp,z0,eave,cell,len(xy)


def _local_limits(pts,a,b,global_z0,global_eave,thickness):
    v=b-a; L=float(np.linalg.norm(v))
    if L<1e-6:return global_z0,global_eave,0.
    t=v/L;n=np.array([-t[1],t[0]]);rel=pts[:,:2]-a;u=rel@t;dn=np.abs(rel@n)
    zz=pts[(u>=-.12)&(u<=L+.12)&(dn<=max(.24,.72*thickness+.10)),2]
    if len(zz)<35:return global_z0,global_eave,.30
    lo=float(np.clip(np.quantile(zz,.04),global_z0-.20,global_z0+.25))
    dz=.12; a0=max(float(np.quantile(zz,.02)),global_z0-.25); a1=min(float(np.quantile(zz,.98)),global_eave+.55)
    edges=np.arange(a0,a1+dz,dz); hi=global_eave
    if len(edges)>=2:
        h,_=np.histogram(zz,bins=edges); sm=np.convolve(h.astype(float),np.ones(3)/3,mode="same"); active=sm>=max(2.,.16*float(sm.max(initial=0)))
        runs=base._segments(active); runs=[r for r in runs if edges[r[0]]<=global_z0+.55*max(1.8,global_eave-global_z0)]
        if runs:
            r=max(runs,key=lambda x:x[1]-x[0]); hi=float(edges[min(r[1],len(edges)-1)])
    hi=float(np.clip(hi,global_eave-.35,global_eave+.28))
    if hi-lo<1.7:hi=global_eave
    support=float(np.clip(len(zz)/max(220.,50.*L),.18,1.0))
    return lo,hi,support


def _prune_partitions(pts,parts,footprint):
    if not parts:return []
    geometric_feedback(pts,parts); inner=footprint.buffer(-.18); bd=footprint.boundary
    prelim=[]
    for w in parts:
        line=LineString([(w.x1,w.y1),(w.x2,w.y2)]);L=line.length
        if L<1.0 or (not inner.is_empty and line.intersection(inner).length<.84*L):continue
        if not (w.source=="paired_height_persistent_faces" or (w.confidence>=.78 and w.resf_EV>=.70)):continue
        if w.geometric_feedback<.24 and w.confidence<.82:continue
        prelim.append(w)
    prelim=core.deduplicate_physical_walls(prelim); lines=[LineString([(w.x1,w.y1),(w.x2,w.y2)]) for w in prelim]; scored=[]
    for i,(w,line) in enumerate(zip(prelim,lines)):
        att=[]
        for p in (Point(line.coords[0]),Point(line.coords[-1])):
            att.append(bd.distance(p)<=.34 or any(j!=i and other.distance(p)<=.34 for j,other in enumerate(lines)))
        n=sum(att)
        if n==0 and not (line.length>=2.8 and w.confidence>=.84):continue
        if n==1 and not (line.length>=1.7 and w.confidence>=.70):continue
        score=.46*w.confidence+.24*w.geometric_feedback+.18*min(1.,w.resf_EV)+.12*min(1.,line.length/2.5)+.06*n
        scored.append((score,w))
    cap=int(np.clip(round(footprint.area/5.),6,18)); kept=[w for _,w in sorted(scored,key=lambda x:x[0],reverse=True)[:cap]]
    return core.deduplicate_physical_walls(core.merge_collinear(kept,.85))


def _partitions(pts,footprint,storey,cands,raster,max_orient):
    return _prune_partitions(pts,_ORIG_PARTITIONS(pts,footprint,storey,cands,raster,max_orient),footprint)


def _roof_grid(pts,minz,cell):
    p=pts[pts[:,2]>=minz-.22]
    if len(p)<100:return np.empty((0,3))
    o=p[:,:2].min(axis=0);ix=np.floor((p[:,0]-o[0])/cell).astype(int);iy=np.floor((p[:,1]-o[1])/cell).astype(int);ny=int(iy.max())+1;k=ix*max(ny,1)+iy;order=np.argsort(k,kind="mergesort");ks=k[order];zz=p[order,2]
    st=np.r_[0,np.flatnonzero(ks[1:]!=ks[:-1])+1];en=np.r_[st[1:],len(ks)];keys=ks[st];zq=np.array([np.quantile(zz[a:b],.88) for a,b in zip(st,en)])
    return np.column_stack((o+np.column_stack((keys//max(ny,1)+.5,keys%max(ny,1)+.5))*cell,zq))


def _facet_poly(pin,shell,cell):
    mp=MultiPoint(pin[:,:2].tolist());g=None
    if getattr(base,"concave_hull",None):
        for r in (.12,.20,.30):
            try:
                q=base.concave_hull(mp,ratio=r,allow_holes=False)
                if q.geom_type=="Polygon" and q.area>=.8:g=q;break
            except Exception:pass
    if g is None:g=mp.convex_hull
    g=g.intersection(shell)
    if g.geom_type=="MultiPolygon":g=max(g.geoms,key=lambda q:q.area)
    if g.geom_type!="Polygon" or g.area<.9:return None
    g=g.simplify(max(.08,.65*cell),preserve_topology=True);r=g.minimum_rotated_rectangle;rc=np.asarray(r.exterior.coords);s=[np.linalg.norm(rc[i+1]-rc[i]) for i in range(4)];aspect=max(s)/max(min(s),1e-6);density=len(pin)/max(1.,g.area/(cell*cell));compact=4*math.pi*g.area/max(g.length*g.length,1e-6)
    return g if aspect<=7.5 and density>=.14 and compact>=.08 else None


def _fit_roofs(pts,footprint,storey,raster,max_planes=8):
    cell=float(np.clip(max(.13,2.6*raster),.13,.22));top=float(np.quantile(pts[:,2],.98));xyz=_roof_grid(pts,storey.z0+max(1.75,.30*(top-storey.z0)),cell)
    if len(xyz)<70:return []
    shell=MultiPoint(xyz[:,:2].tolist()).buffer(max(.10,.85*cell),resolution=1).buffer(max(.38,3.2*cell),join_style=2).buffer(-max(.38,3.2*cell),join_style=2).intersection(footprint.buffer(3.0))
    if shell.is_empty:return []
    keep=np.array([shell.covers(Point(float(x),float(y))) for x,y in xyz[:,:2]]);xyz=xyz[keep]
    rng=np.random.default_rng(61);remaining=np.arange(len(xyz));facets=[];polys=[];tol=max(.085,.55*cell)
    for _ in range(max_planes):
        if len(remaining)<45:break
        pr=xyz[remaining];best=None;insbest=None
        for _t in range(160):
            ii=rng.choice(len(pr),3,replace=False);A=np.column_stack((pr[ii,0],pr[ii,1],np.ones(3)))
            try:abc=np.linalg.solve(A,pr[ii,2])
            except np.linalg.LinAlgError:continue
            if not .03<=math.hypot(abc[0],abc[1])<=1.45:continue
            ins=np.abs(pr[:,2]-(abc[0]*pr[:,0]+abc[1]*pr[:,1]+abc[2]))<=tol
            if insbest is None or ins.sum()>insbest.sum():best,insbest=abc,ins
        if best is None or insbest is None or insbest.sum()<max(35,int(.055*len(xyz))):break
        pinall=pr[insbest]
        for ci in _components(pinall[:,:2],max(1.8*cell,.28)):
            if len(ci)<max(18,int(.025*len(xyz))):continue
            pin=pinall[ci];A=np.column_stack((pin[:,0],pin[:,1],np.ones(len(pin))));abc,*_=np.linalg.lstsq(A,pin[:,2],rcond=None);pred=A@abc;rms=float(np.sqrt(np.mean((pin[:,2]-pred)**2)))
            if not .03<=math.hypot(abc[0],abc[1])<=1.45 or rms>max(.12,.70*cell):continue
            poly=_facet_poly(pin,shell,cell)
            if poly is None or any(poly.intersection(o).area/max(min(poly.area,o.area),1e-6)>.28 for o in polys):continue
            q0,q1=np.quantile(pin[:,2],[.02,.98]);verts=[[float(x),float(y),float(np.clip(abc[0]*x+abc[1]*y+abc[2],q0-.20,q1+.20))] for x,y in list(poly.exterior.coords)[:-1]]
            conf=float(np.clip(.45+.35*len(pin)/max(1,len(pinall))+.20*(1-rms/max(tol,1e-6)),.40,.97));facets.append(RoofFacet(len(facets)+1,storey.id,verts,.14,conf,"connected_upper_envelope_ransac"));polys.append(poly)
        remaining=remaining[~insbest]
    return facets


def _columns(pts,footprint,storey,raster):
    xy,z0,z1,cell=v6._facade_columns(pts,raster);bd=footprint.boundary;mask=np.array([.24<bd.distance(Point(float(x),float(y)))<=2.6 for x,y in xy]);xy2=xy[mask];a=z0[mask];b=z1[mask];out=[]
    for ci in _components(xy2,max(.19,2.3*cell)):
        if not 4<=len(ci)<=85:continue
        p=xy2[ci];h=MultiPoint(p.tolist()).convex_hull;r=h.minimum_rotated_rectangle
        if r.geom_type!="Polygon":continue
        rc=np.asarray(r.exterior.coords);s=[float(np.linalg.norm(rc[i+1]-rc[i])) for i in range(4)];dims=sorted([max(s[0],cell),max(s[1],cell)]);w,d=dims[1]+cell,dims[0]+cell;aspect=w/max(d,1e-6);zz0=float(np.quantile(a[ci],.45));zz1=float(np.quantile(b[ci],.55));H=zz1-zz0
        if w>.72 or d>.58 or aspect>2.8 or H<1.65:continue
        if zz0>storey.z0+.55 or zz1>storey.z1+.45:continue
        c=p.mean(axis=0)
        if footprint.distance(Point(float(c[0]),float(c[1])))>2.2:continue
        edge=rc[1]-rc[0];ang=float(math.degrees(math.atan2(edge[1],edge[0]))%180);conf=float(np.clip(.46+.22*min(1,H/2.5)+.18/aspect+.14*min(1,len(ci)/14),0,.96));out.append(Column(len(out)+1,storey.id,float(c[0]),float(c[1]),zz0,zz1,float(np.clip(w,.12,.70)),float(np.clip(d,.12,.55)),ang,conf))
    kept=[]
    for c in sorted(out,key=lambda q:q.confidence,reverse=True):
        if all(math.hypot(c.x-k.x,c.y-k.y)>.45 for k in kept):kept.append(c)
    for i,c in enumerate(kept,1):c.id=i
    return kept


def _dedupe_openings(model):
    by={w.id:w for w in model.walls};kept=[]
    for o in sorted(model.openings,key=lambda q:q.confidence,reverse=True):
        w=by.get(o.wall_id)
        if w is None or o.confidence<.46 or (o.kind=="Window" and not w.source.startswith("closed_facade_envelope")):continue
        dx,dy=w.x2-w.x1,w.y2-w.y1;L=max(math.hypot(dx,dy),1e-6);cx=w.x1+dx/L*o.offset;cy=w.y1+dy/L*o.offset;cz=w.z0+o.sill+.5*o.height
        if any(k.kind==o.kind and math.dist((cx,cy,cz),p)<.45 for k,p in kept):continue
        kept.append((o,(cx,cy,cz)))
    model.openings=[x[0] for x in kept]
    for i,o in enumerate(model.openings,1):o.id=i


def reconstruct_exterior(request):
    global _LAST_FOOTPRINT
    saved=(v6._estimate_shell,v6._local_wall_limits,v6._interior_partitions,v6.fit_roof_facets)
    v6._estimate_shell=_estimate_shell;v6._local_wall_limits=_local_limits;v6._interior_partitions=_partitions;v6.fit_roof_facets=_fit_roofs
    try:model=v6.reconstruct_exterior(request)
    finally:v6._estimate_shell,v6._local_wall_limits,v6._interior_partitions,v6.fit_roof_facets=saved
    fp=_LAST_FOOTPRINT
    raw=base.load_xyz(request["input"],int(request.get("max_points",2_000_000)));r=float(np.clip(float(request.get("raster_cell_m",.05)),.04,.10));pts=base.voxel_subsample(raw,max(.03,min(.055,r)))
    model.columns=_columns(pts,fp,model.storeys[0],r) if fp is not None else []
    _dedupe_openings(model)
    if not model.spaces and fp is not None:
        inner=fp.buffer(-.5*np.median([w.thickness for w in model.walls if w.source.startswith("closed_facade_envelope")] or [.34]))
        if inner.geom_type=="MultiPolygon":inner=max(inner.geoms,key=lambda q:q.area)
        if inner.geom_type=="Polygon" and inner.area>=2:model.spaces=[base.Space(1,1,"GrossInterior",[[float(x),float(y)] for x,y in list(inner.exterior.coords)[:-1]],model.storeys[0].z0,model.storeys[0].z1,float(inner.area),.72)]
    s=model.summary;s.update({"version":"6.1-robust-building-envelope","facade_component_filter":_LAST_COMPONENT_DIAG,"footprint_vertices":len(fp.exterior.coords)-1 if fp is not None else 0,"walls":len(model.walls),"doors":sum(o.kind=="Door" for o in model.openings),"windows":sum(o.kind=="Window" for o in model.openings),"openings":len(model.openings),"spaces":len(model.spaces),"roof_facets":len(getattr(model,"roofs",[])),"columns":len(model.columns),"wall_reconstruction":"morphological_closed_facade_graph_plus_topology_pruned_partitions","roof_reconstruction":"connected_support_quantile_grid_multi_plane_ransac"})
    return model


def reconstruct_bim(request):
    raw=base.load_xyz(request["input"],min(int(request.get("max_points",2_000_000)),450_000));mode,diag=v6.classify_scene(raw,float(request.get("raster_cell_m",.05)))
    if mode=="indoor_room":
        m=indoor.reconstruct_bim(request);m.summary.update({"scene_mode":"indoor_room","scene_classifier":diag,"version":"6.1-robust-building-envelope","v61_path":"delegated_to_v5_1_indoor_graph","reference_bim_used_at_runtime":False});m.roofs=[];m.columns=[];return m
    m=reconstruct_exterior(request);m.summary["scene_classifier"]=diag;return m
