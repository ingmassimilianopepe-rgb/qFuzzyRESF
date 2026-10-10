"""Fuzzy-RESF-BIM v3.1 topology and object-completion layer.

This module keeps the paper's vector-valued RESF candidate generation but replaces the
fragile "plane fragment -> BIM wall" conversion with an architectural instance stage:

* coplanar wall-face fragments are completed across door/window/occlusion-scale gaps;
* physical wall instances are merged on a common centre line instead of exported as sheets;
* nearby wall ends are extended/snapped to common junctions;
* missing outer-envelope edges may be inferred only when floor AND ceiling boundaries agree;
* object-level Cloud<->wall feedback measures distributed surface coverage, not only distance;
* short isolated mid-height planar clutter is rejected;
* doors are separated from foreground occlusions before semantic IFC export.

No reference BIM is used by this module.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
from shapely.geometry import LineString, Point, Polygon

import bim_reconstruction as base


def _interval_union_length(intervals: list[tuple[float, float]]) -> float:
    ints=sorted((min(a,b),max(a,b)) for a,b in intervals if abs(b-a)>1e-9)
    if not ints:
        return 0.0
    total=0.0; a0,a1=ints[0]
    for b0,b1 in ints[1:]:
        if b0<=a1:
            a1=max(a1,b1)
        else:
            total+=a1-a0; a0,a1=b0,b1
    return total+(a1-a0)


def consolidate_candidates(cands: list[base.RESFCandidate], enabled: bool=True) -> list[base.RESFCandidate]:
    """Complete one wall face across architectural gaps without inventing observed support."""
    if not enabled:
        return list(cands)
    groups: list[list[base.RESFCandidate]]=[]
    for c in sorted(cands,key=lambda x:(x.storey_id,x.theta_deg,x.d,x.t0)):
        best=None; best_cost=1e9
        for gi,g in enumerate(groups):
            r=max(g,key=lambda x:x.confidence)
            if c.storey_id!=r.storey_id or base._circular_distance(c.theta_deg,r.theta_deg)>3.0:
                continue
            dd=abs(c.d-r.d)
            if dd>0.12:
                continue
            g0=min(x.t0 for x in g); g1=max(x.t1 for x in g)
            gap=max(0.0,max(c.t0,g0)-min(c.t1,g1))
            strong=(c.E_V>=0.52 and c.N>=0.40 and c.confidence>=0.30 and
                    max(x.E_V for x in g)>=0.52 and max(x.N for x in g)>=0.40)
            allowed=0.45 if not strong else 2.20
            if gap>allowed:
                continue
            cost=4.0*dd+gap+0.1*base._circular_distance(c.theta_deg,r.theta_deg)
            if cost<best_cost:
                best=gi; best_cost=cost
        if best is None:
            groups.append([c])
        else:
            groups[best].append(c)

    rank={"Permanent":3,"OccludedPermanent":2,"Uncertain":1,"Clutter":0}
    out=[]
    for g in groups:
        r=max(g,key=lambda x:x.confidence)
        lengths=np.array([max(x.t1-x.t0,0.05) for x in g])
        weights=lengths*np.array([max(x.confidence,0.08) for x in g])
        d=float(np.average([x.d for x in g],weights=weights))
        t0=min(x.t0 for x in g); t1=max(x.t1 for x in g)
        full=max(t1-t0,1e-9)
        observed=_interval_union_length([(x.t0,x.t1) for x in g])
        miss=float(np.clip(1-observed/full,0,1))
        def wavg(name):
            return float(np.average([getattr(x,name) for x in g],weights=weights))
        cp=max(max(x.T_CP for x in g),math.exp(-(miss*miss)/(2*0.35**2)))
        occ=max(max(x.O for x in g),miss*max(max(x.T_F for x in g),max(x.T_C for x in g)))
        state=max((x.state for x in g),key=lambda v:rank[v])
        if miss>=0.12 and state=="Permanent":
            state="OccludedPermanent"
        out.append(base.RESFCandidate(
            0,r.storey_id,r.theta_deg,d,t0,t1,min(x.z0 for x in g),max(x.z1 for x in g),
            wavg("A_star"),max(wavg("E_H"),min(1.0,observed/full)),wavg("E_V"),
            max(x.C for x in g),wavg("N"),float(np.clip(occ,0,1)),max(x.rho for x in g),wavg("semantic_wall"),
            max(x.T_F for x in g),max(x.T_C for x in g),max(x.T_W for x in g),
            max(x.T_B for x in g),max(x.T_R for x in g),float(np.clip(cp,0,1)),
            min(x.G for x in g),max(x.confidence for x in g),state
        ))
    for i,c in enumerate(out,1): c.id=i
    return out


def _wall_length(w: base.Wall) -> float:
    return float(math.hypot(w.x2-w.x1,w.y2-w.y1))


def _wall_axis(w: base.Wall, theta: float|None=None):
    a=float(w.theta_deg if theta is None else theta)
    th=math.radians(a)
    n=np.array([math.cos(th),math.sin(th)])
    t=np.array([-math.sin(th),math.cos(th)])
    p1=np.array([w.x1,w.y1]); p2=np.array([w.x2,w.y2])
    d=float(0.5*(p1@n+p2@n))
    u0,u1=sorted((float(p1@t),float(p2@t)))
    return n,t,d,u0,u1


def _merge_wall_group(group: list[base.Wall], theta: float|None=None) -> base.Wall:
    ref=max(group,key=lambda w:(_wall_length(w),w.confidence))
    theta=float(ref.theta_deg if theta is None else theta)
    n,t,_,_,_=_wall_axis(ref,theta)
    lengths=np.array([max(_wall_length(w),0.05) for w in group])
    weights=lengths*np.array([max(w.confidence,0.08) for w in group])
    ds=[]; intervals=[]
    for w in group:
        _,_,d,a,b=_wall_axis(w,theta); ds.append(d); intervals.append((a,b))
    d=float(np.average(ds,weights=weights))
    u0=min(a for a,b in intervals); u1=max(b for a,b in intervals)
    observed=_interval_union_length(intervals); full=max(u1-u0,1e-9)
    miss=float(np.clip(1-observed/full,0,1))
    p1=n*d+t*u0; p2=n*d+t*u1
    def wavg(name):
        return float(np.average([getattr(w,name) for w in group],weights=weights))
    state="OccludedPermanent" if miss>=0.10 or any(w.state=="OccludedPermanent" for w in group) else "Permanent"
    source="coplanar_gap_completion" if miss>=0.06 else ("paired_faces" if any(w.source=="paired_faces" for w in group) else "instance_merge")
    return base.Wall(
        0,ref.storey_id,float(p1[0]),float(p1[1]),float(p2[0]),float(p2[1]),
        min(w.z0 for w in group),max(w.z1 for w in group),
        float(np.clip(np.average([w.thickness for w in group],weights=weights),0.09,0.45)),theta,
        float(np.clip(wavg("confidence")-0.08*miss+0.05*max(w.confidence for w in group),0,1)),
        wavg("support"),max(w.continuity for w in group),source,state,
        wavg("resf_A"),wavg("resf_EH"),wavg("resf_EV"),max(w.resf_C for w in group),
        wavg("resf_N"),max(max(w.resf_O for w in group),miss),min(w.geometric_feedback for w in group)
    )


def merge_wall_instances(walls: list[base.Wall], max_gap: float=2.20, offset_tol: float=0.18) -> list[base.Wall]:
    if not walls:
        return []
    groups=[]
    for w in sorted(walls,key=lambda q:(q.storey_id,q.theta_deg,-q.confidence)):
        best=None; best_cost=1e9
        for gi,g in enumerate(groups):
            ref=max(g,key=_wall_length)
            if w.storey_id!=ref.storey_id or base._circular_distance(w.theta_deg,ref.theta_deg)>4.0:
                continue
            theta=ref.theta_deg
            _,_,dw,a0,a1=_wall_axis(w,theta)
            ds=[]; ints=[]
            for q in g:
                _,_,dq,b0,b1=_wall_axis(q,theta); ds.append(dq); ints.append((b0,b1))
            dg=float(np.average(ds,weights=[max(_wall_length(q),0.05) for q in g]))
            if abs(dw-dg)>max(offset_tol,0.55*max([q.thickness for q in g]+[w.thickness])):
                continue
            g0=min(a for a,b in ints); g1=max(b for a,b in ints)
            gap=max(0.0,max(a0,g0)-min(a1,g1))
            if gap>max_gap:
                continue
            if gap>0.55 and min(w.confidence,max(q.confidence for q in g))<0.38:
                continue
            cost=gap+4.0*abs(dw-dg)+0.1*base._circular_distance(w.theta_deg,ref.theta_deg)
            if cost<best_cost:
                best=gi; best_cost=cost
        if best is None: groups.append([w])
        else: groups[best].append(w)
    out=[_merge_wall_group(g) for g in groups]
    for i,w in enumerate(out,1): w.id=i
    return out


def deduplicate_walls(walls: list[base.Wall]) -> list[base.Wall]:
    if not walls: return []
    merged=merge_wall_instances(walls,0.35,0.16)
    kept=[]
    for w in sorted(merged,key=lambda x:(x.confidence,_wall_length(x)),reverse=True):
        line=LineString([(w.x1,w.y1),(w.x2,w.y2)])
        dup=False
        for k in kept:
            if w.storey_id!=k.storey_id or base._circular_distance(w.theta_deg,k.theta_deg)>3.0: continue
            kl=LineString([(k.x1,k.y1),(k.x2,k.y2)])
            if line.distance(kl)>max(0.10,0.40*max(w.thickness,k.thickness)): continue
            if line.hausdorff_distance(kl)<=0.30 and abs(line.length-kl.length)<=max(0.8,0.35*max(line.length,kl.length)):
                dup=True; break
        if not dup: kept.append(w)
    for i,w in enumerate(kept,1): w.id=i
    return kept


def _cluster_endpoints(walls: list[base.Wall], tol: float=0.22) -> None:
    refs=[]
    for wi,w in enumerate(walls):
        refs += [(wi,0,np.array([w.x1,w.y1])),(wi,1,np.array([w.x2,w.y2]))]
    used=set()
    for i,(wi,ei,p) in enumerate(refs):
        if i in used: continue
        group=[i]; used.add(i); changed=True
        while changed:
            changed=False; centre=np.mean([refs[j][2] for j in group],axis=0)
            for j,(_,_,q) in enumerate(refs):
                if j in used: continue
                if np.linalg.norm(q-centre)<=tol:
                    group.append(j); used.add(j); changed=True
        if len(group)<2: continue
        weights=[]; pts=[]
        for j in group:
            wj,_,q=refs[j]
            weights.append(max(walls[wj].confidence,0.1)*max(_wall_length(walls[wj]),0.3)); pts.append(q)
        node=np.average(np.asarray(pts),axis=0,weights=np.asarray(weights))
        for j in group:
            wj,ej,_=refs[j]
            if ej==0: walls[wj].x1,walls[wj].y1=map(float,node)
            else: walls[wj].x2,walls[wj].y2=map(float,node)


def snap_wall_graph(walls: list[base.Wall], snap: float=0.65) -> list[base.Wall]:
    if len(walls)<2: return walls
    walls=deduplicate_walls(walls); snap=max(0.20,float(snap))
    for _ in range(2):
        ext=[]
        for w in walls:
            p1=np.array([w.x1,w.y1]); p2=np.array([w.x2,w.y2]); L=np.linalg.norm(p2-p1)
            if L<1e-9: ext.append(LineString([p1,p2])); continue
            u=(p2-p1)/L; ext.append(LineString([p1-u*snap,p2+u*snap]))
        inters=[[] for _ in walls]
        for i in range(len(walls)):
            for j in range(i+1,len(walls)):
                if walls[i].storey_id!=walls[j].storey_id or base._circular_distance(walls[i].theta_deg,walls[j].theta_deg)<12: continue
                g=ext[i].intersection(ext[j])
                if g.geom_type!="Point": continue
                q=np.array([g.x,g.y])
                di=min(np.linalg.norm(q-np.array([walls[i].x1,walls[i].y1])),np.linalg.norm(q-np.array([walls[i].x2,walls[i].y2])))
                dj=min(np.linalg.norm(q-np.array([walls[j].x1,walls[j].y1])),np.linalg.norm(q-np.array([walls[j].x2,walls[j].y2])))
                if di<=snap and dj<=snap:
                    inters[i].append(q); inters[j].append(q)
        for i,w in enumerate(walls):
            for end,p in [(0,np.array([w.x1,w.y1])),(1,np.array([w.x2,w.y2]))]:
                if not inters[i]: continue
                ds=np.array([np.linalg.norm(q-p) for q in inters[i]]); k=int(np.argmin(ds))
                if ds[k]<=snap:
                    if end==0: w.x1,w.y1=map(float,inters[i][k])
                    else: w.x2,w.y2=map(float,inters[i][k])
        _cluster_endpoints(walls,min(0.25,0.45*snap))
    for i,w in enumerate(walls,1): w.id=i
    return walls


def _endpoint_support(walls: list[base.Wall], p: np.ndarray, radius: float=0.75) -> float:
    best=0.0; pp=Point(float(p[0]),float(p[1]))
    for w in walls:
        d=LineString([(w.x1,w.y1),(w.x2,w.y2)]).distance(pp)
        if d<=radius:
            best=max(best,float(1-d/radius)*max(w.confidence,0.2))
    return best


def infer_horizontal_envelope_walls(pts: np.ndarray, walls: list[base.Wall], storey: base.Storey) -> list[base.Wall]:
    floor=base._horizontal_footprint(pts,storey.z0,0.12)
    ceil=base._horizontal_footprint(pts,storey.z1,0.14)
    if floor is None or ceil is None: return walls
    f=floor.simplify(0.18,preserve_topology=True); c=ceil.simplify(0.18,preserve_topology=True)
    if f.geom_type!="Polygon" or c.geom_type!="Polygon": return walls
    coords=list(f.exterior.coords); cb=c.boundary
    thick=float(np.median([w.thickness for w in walls])) if walls else 0.20
    add=[]
    for a,b in zip(coords[:-1],coords[1:]):
        p0=np.array(a); p1=np.array(b); v=p1-p0; L=float(np.linalg.norm(v))
        if not 0.80<=L<=25: continue
        u=v/L; angle=math.degrees(math.atan2(-u[0],u[1]))%180.0
        line=LineString([tuple(p0),tuple(p1)]); mid=Point(float((p0[0]+p1[0])/2),float((p0[1]+p1[1])/2))
        cd=float(cb.distance(mid))
        if cd>0.45: continue
        explained=False
        for w in walls:
            if w.storey_id!=storey.id or base._circular_distance(angle,w.theta_deg)>12: continue
            wl=LineString([(w.x1,w.y1),(w.x2,w.y2)])
            if line.distance(wl)<=0.35 and line.intersection(wl.buffer(0.35)).length>=0.45*L:
                explained=True; break
        if explained: continue
        s0=_endpoint_support(walls,p0); s1=_endpoint_support(walls,p1)
        if min(s0,s1)<0.12 and max(s0,s1)<0.35: continue
        conf=float(np.clip(0.44+0.18*min(1,s0+s1)+0.14*(1-cd/0.45),0,0.72))
        add.append(base.Wall(0,storey.id,float(p0[0]),float(p0[1]),float(p1[0]),float(p1[1]),storey.z0,storey.z1,
                             float(np.clip(thick,0.12,0.35)),angle,conf,0.12,0.55,
                             "horizontal_envelope_completion","OccludedPermanent",0.12,min(1,L/max(f.length,1)),1,0.55,0.55,0.75,0.65))
    return merge_wall_instances(walls+add,2.20,0.20)


def geometric_feedback(pts: np.ndarray, walls: list[base.Wall], sigma: float=0.08) -> None:
    xy=pts[:,:2]; z=pts[:,2]
    for w in walls:
        p1=np.array([w.x1,w.y1]); p2=np.array([w.x2,w.y2]); v=p2-p1
        L=float(np.linalg.norm(v)); H=max(float(w.z1-w.z0),1e-6)
        if L<1e-6: continue
        t=v/L; n=np.array([-t[1],t[0]]); rel=xy-p1
        along=rel@t; normal=np.abs(rel@n); band=max(0.12,0.55*w.thickness+0.08)
        near=(along>=-0.10)&(along<=L+0.10)&(z>=w.z0-0.05)&(z<=w.z1+0.05)&(normal<=band)
        if near.sum()<20:
            w.geometric_feedback=0; w.confidence*=0.68; continue
        d50=float(np.median(np.maximum(0,normal[near]-0.5*w.thickness)))
        gd=math.exp(-(d50*d50)/(2*sigma*sigma))
        du=max(0.10,min(0.20,L/80)); dz=max(0.10,min(0.18,H/30))
        nx=int(np.clip(math.ceil(L/du),4,300)); nz=int(np.clip(math.ceil(H/dz),6,80))
        occ=np.zeros((nx,nz),dtype=bool)
        ai=np.clip((along[near]/L*nx).astype(int),0,nx-1); zi=np.clip(((z[near]-w.z0)/H*nz).astype(int),0,nz-1)
        np.add.at(occ,(ai,zi),True)
        occ=base._binary_close2d(occ,1) if min(nx,nz)>=4 else occ
        horiz=float(np.mean(occ.any(axis=1))); vert=float(np.mean(occ.any(axis=0))); area=float(occ.mean())
        gh=float(np.clip(horiz/0.62,0,1)); gv=float(np.clip(vert/0.70,0,1)); ga=float(np.clip(area/0.18,0,1))
        g=float(np.clip(gd*(0.50*math.sqrt(gh)+0.30*math.sqrt(gv)+0.20*math.sqrt(ga)),0,1))
        w.geometric_feedback=g
        allowance=0.10 if w.source in {"coplanar_gap_completion","horizontal_envelope_completion"} else 0
        w.confidence=float(np.clip(0.70*w.confidence+0.25*g+allowance,0,1)); w.continuity=max(w.continuity,horiz)


def prune_wall_network(walls: list[base.Wall]) -> list[base.Wall]:
    if not walls: return []
    lines=[LineString([(w.x1,w.y1),(w.x2,w.y2)]) for w in walls]; kept=[]
    for i,w in enumerate(walls):
        L=_wall_length(w); junctions=0
        for j,o in enumerate(walls):
            if i==j or w.storey_id!=o.storey_id or base._circular_distance(w.theta_deg,o.theta_deg)<12: continue
            if lines[i].distance(lines[j])<=0.28: junctions+=1
        network=min(1.0,junctions/2); score=0.42*w.confidence+0.23*w.geometric_feedback+0.20*w.continuity+0.15*network
        protected=w.source in {"coplanar_gap_completion","horizontal_envelope_completion"} and (network>0 or L>=1.5)
        if L<0.55: continue
        if not protected and L<1.0 and score<0.52: continue
        if not protected and network<=0 and L<2.20 and w.resf_EV<0.62: continue
        if not protected and w.resf_EV<0.48 and network<0.5: continue
        if not protected and w.geometric_feedback<0.10 and w.confidence<0.50: continue
        if L>=1.8 or protected or score>=0.42: kept.append(w)
    kept=merge_wall_instances(kept,2.20,0.18)
    for i,w in enumerate(kept,1): w.id=i
    return kept


def detect_openings(pts: np.ndarray, walls: list[base.Wall], raster: float) -> list[base.Opening]:
    xy,z=pts[:,:2],pts[:,2]; out=[]; oid=1
    cell=float(np.clip(max(0.055,raster*2.5),0.055,0.095))
    for w in walls:
        p1=np.array([w.x1,w.y1]); p2=np.array([w.x2,w.y2]); v=p2-p1
        L=float(np.linalg.norm(v)); H=float(w.z1-w.z0)
        if L<1 or H<1.8: continue
        t=v/L; n=np.array([-t[1],t[0]]); rel=xy-p1; u=rel@t; dn=np.abs(rel@n)
        face_band=max(0.13,0.55*w.thickness+0.07)
        near=(u>=0)&(u<=L)&(dn<=face_band)&(z>=w.z0-0.03)&(z<=w.z1+0.03)
        if near.sum()<100: continue
        uu=u[near]; zz=z[near]-w.z0
        nx=int(np.clip(math.ceil(L/cell),12,650)); nz=int(np.clip(math.ceil(H/cell),20,260))
        counts=np.zeros((nx,nz),dtype=np.uint16)
        xi=np.clip((uu/L*nx).astype(int),0,nx-1); zi=np.clip((zz/H*nz).astype(int),0,nz-1)
        np.add.at(counts,(xi,zi),1)
        if counts.max(initial=0)==0: continue
        occ=base._binary_close2d(counts>0,1); du=L/nx; dz=H/nz

        zlow=max(1,int(0.22/dz)); zhigh=min(nz-2,int(min(2.15,H-0.18)/dz))
        if zhigh>zlow+3:
            profile=occ[:,zlow:zhigh].mean(axis=1); pos=profile[profile>0]; ref=float(np.quantile(pos,0.60)) if pos.size else 0
            low=profile<=max(0.10,0.30*ref) if ref>0 else np.zeros(nx,dtype=bool)
            margin=max(2,int(0.25/du)); low[:margin]=False; low[-margin:]=False
            low=base._close_1d(low,max(1,int(0.10/du)))
            for i0,i1 in base._segments(low):
                width=(i1-i0)*du
                if not 0.60<=width<=1.80: continue
                flank=max(1,int(0.18/du))
                left=occ[max(0,i0-flank):i0,zlow:zhigh].mean() if i0>0 else 0
                right=occ[i1:min(nx,i1+flank),zlow:zhigh].mean() if i1<nx else 0
                head=None
                for k in range(max(int(1.60/dz),1),min(nz-2,int(min(2.50,H-0.05)/dz))):
                    slab=occ[i0:i1,k:min(nz,k+max(1,int(0.15/dz)))]
                    if slab.size and slab.mean()>=0.22: head=k*dz; break
                if head is None or head>min(2.45,0.88*H): continue
                jamb=min(left,right)
                if jamb<0.16: continue
                vacancy=float(1-profile[i0:i1].mean()/max(ref,1e-6)); centre=0.5*(i0+i1)*du
                ur=(u>=max(0,centre-0.5*width))&(u<=min(L,centre+0.5*width)); zr=(z>=w.z0+0.15)&(z<=w.z0+head)
                front=ur&zr&(dn>face_band)&(dn<=min(1.20,face_band+0.90))
                foreground=min(1.0,float(front.sum())/max(35.0,0.30*float(near.sum())))
                conf=float(np.clip(0.38*vacancy+0.34*min(1,jamb/0.35)+0.18*w.confidence+0.10*w.resf_C-0.28*foreground,0,1))
                if conf<0.48: continue
                out.append(base.Opening(oid,w.id,w.storey_id,"Door",float(centre),float(width),0.0,float(np.clip(head,1.65,min(2.45,H*0.88))),conf,"jamb_lintel_occlusion_checked")); oid+=1

        empty=~occ; valid=np.zeros_like(empty,dtype=bool); k0=max(1,int(0.35/dz)); k1=min(nz-1,int(min(2.45,H-0.15)/dz)); valid[1:-1,k0:k1]=True
        holes=empty&valid; seen=np.zeros_like(holes,dtype=bool)
        for i in range(1,nx-1):
            for k in range(k0,k1):
                if not holes[i,k] or seen[i,k]: continue
                q=deque([(i,k)]); seen[i,k]=True; comp=[]
                while q:
                    x,y=q.popleft(); comp.append((x,y))
                    for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                        xx,yy=x+dx,y+dy
                        if 0<=xx<nx and 0<=yy<nz and holes[xx,yy] and not seen[xx,yy]: seen[xx,yy]=True; q.append((xx,yy))
                xs=np.array([a for a,b in comp]); zs=np.array([b for a,b in comp]); i0,i1=int(xs.min()),int(xs.max())+1; j0,j1=int(zs.min()),int(zs.max())+1
                width=(i1-i0)*du; height=(j1-j0)*dz; sill=j0*dz
                if not (0.45<=width<=3.2 and 0.45<=height<=1.9 and 0.45<=sill<=1.65): continue
                pu=max(1,int(0.15/du)); pz=max(1,int(0.12/dz))
                left=occ[max(0,i0-pu):i0,j0:j1].mean() if i0>0 else 0; right=occ[i1:min(nx,i1+pu),j0:j1].mean() if i1<nx else 0
                bottom=occ[i0:i1,max(0,j0-pz):j0].mean() if j0>0 else 0; top=occ[i0:i1,j1:min(nz,j1+pz)].mean() if j1<nz else 0
                frame=float((left+right+bottom+top)/4); fill=float(occ[i0:i1,j0:j1].mean()); rect=len(comp)/max(1,(i1-i0)*(j1-j0))
                if frame<0.16 or rect<0.55: continue
                conf=float(np.clip(0.40*frame+0.28*(1-fill)+0.20*w.confidence+0.12*rect,0,1)); centre=0.5*(i0+i1)*du
                if conf<0.43 or any(o.wall_id==w.id and o.kind=="Door" and abs(o.offset-centre)<0.5*(o.width+width) for o in out): continue
                if any(o.wall_id==w.id and o.kind=="Window" and abs(o.offset-centre)<0.35 for o in out): continue
                out.append(base.Opening(oid,w.id,w.storey_id,"Window",float(centre),float(width),float(sill),float(height),conf,"four_sided_frame")); oid+=1
    return out


def reconstruct_bim(request: dict) -> base.BIMModel:
    raw=base.load_xyz(request["input"],int(request.get("max_points",2_000_000)))
    if len(raw)<100: raise ValueError("Point cloud contains too few valid points for BIM reconstruction")
    raster=float(request.get("raster_cell_m",0.05)); pts=base.voxel_subsample(raw,max(0.025,min(0.05,max(raster,0.02))))
    storeys=base.detect_storeys(pts); all_walls=[]; all_spaces=[]; all_slabs=[]; all_candidates=[]
    threshold=float(request.get("confidence_threshold",0.50)); multi=bool(request.get("multi_peak",True)); topo=bool(request.get("topology",True))
    occ=bool(request.get("occlusion",True)); fuzzy=bool(request.get("fuzzy",True)); instance=bool(request.get("instance_consolidation",True)); feedback=bool(request.get("geometric_feedback",True))

    for s in storeys:
        band=pts[(pts[:,2]>=s.z0-0.10)&(pts[:,2]<=s.z1+0.10)]
        if len(band)<100: continue
        orientations=base.detect_orientations(band,float(request.get("angle_step_deg",2.0)),int(request.get("max_orientation_families",12)))
        cands=base.build_resf_candidates(band,s,orientations,max(0.008,float(request.get("plane_spacing_m",0.01))),max(0.02,float(request.get("plane_tolerance_m",0.03))),max(0.04,raster),multi)
        base.enrich_topology(cands)
        for c in cands: c.state,c.confidence=base.fuzzy_decision(c,threshold,fuzzy,topo,occ)
        cands=consolidate_candidates(cands,instance); base.enrich_topology(cands)
        for c in cands: c.state,c.confidence=base.fuzzy_decision(c,threshold,fuzzy,topo,occ)
        if feedback: base._apply_candidate_feedback(band,cands)
        walls=base.candidates_to_walls(cands,s)
        walls=merge_wall_instances(walls,2.20 if topo else 0.55,0.18); walls=snap_wall_graph(walls,0.68 if topo else 0.22)
        if topo:
            walls=infer_horizontal_envelope_walls(band,walls,s); walls=snap_wall_graph(walls,0.72); walls=merge_wall_instances(walls,2.20,0.18)
        if feedback: geometric_feedback(band,walls)
        walls=prune_wall_network(walls); walls=snap_wall_graph(walls,0.55 if topo else 0.20)
        walls=[w for w in walls if _wall_length(w)>=0.55 and (w.confidence>=0.30 or w.source=="horizontal_envelope_completion")]
        baseid=len(all_walls)
        for i,w in enumerate(walls,1): w.id=baseid+i
        spaces=base.spaces_from_walls(walls,s) if topo else []; slabs=base.slabs_from_evidence(band,spaces,s)
        all_walls.extend(walls); all_spaces.extend(spaces); all_slabs.extend(slabs); all_candidates.extend(cands)

    for i,x in enumerate(all_spaces,1): x.id=i
    for i,x in enumerate(all_slabs,1): x.id=i
    openings=detect_openings(pts,all_walls,max(0.04,raster))
    for i,o in enumerate(openings,1): o.id=i
    warnings=[]
    if not all_walls: warnings.append("No accepted BIM wall instances were reconstructed")
    if not all_spaces: warnings.append("No closed IfcSpace polygons reconstructed; wall graph may be incomplete")
    if not openings: warnings.append("No host-wall door/window objects passed opening/occlusion checks")
    if not any(x.kind=="FLOOR" for x in all_slabs): warnings.append("No floor slab footprint reconstructed")
    summary={
        "status":"success" if all_walls else "warning","method":"Fuzzy-RESF-BIM","version":"3.1-topology-completion",
        "input_points":int(len(raw)),"regularized_points":int(len(pts)),"storeys":len(storeys),"resf_candidates":len(all_candidates),
        "accepted_candidates":sum(c.state in {"Permanent","OccludedPermanent"} for c in all_candidates),"walls":len(all_walls),
        "doors":sum(o.kind=="Door" for o in openings),"windows":sum(o.kind=="Window" for o in openings),"openings":len(openings),
        "spaces":len(all_spaces),"slabs":len(all_slabs),"wall_completion":"coplanar_gap_plus_junction_plus_floor_ceiling_envelope",
        "occlusion_evidence":"geometry_topology_proxy_without_scanner_poses","semantic_evidence":"geometry_prior_without_pointwise_class_probabilities","warnings":warnings,
    }
    return base.BIMModel(storeys,all_walls,openings,all_spaces,all_slabs,all_candidates,summary)
