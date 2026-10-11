"""Fuzzy-RESF-BIM v4: paper-aligned architectural reconstruction.

This module deliberately separates RESF *surface hypotheses* from BIM *physical objects*.
The previous portable backend still allowed too many accepted translated planes to survive
as wall solids.  That failure mode is visible as comb-like stacks of parallel sheets and is
not the object-consolidation stage described in Sec. 3.4 of the manuscript.

v4 keeps the manuscript RESF evidence and fuzzy decision layer, then performs a global
architectural reconstruction stage:

1. build multi-orientation / multi-peak RESF candidates with the frozen 1 cm sweep and
   5 cm projected occupancy grid;
2. consolidate coplanar candidate fragments into persistent *wall-face tracks*;
3. rank orientation families by room-scale support and suppress weak side-lobes;
4. pair compatible opposite faces into one physical wall centreline and estimate thickness;
5. non-maximum-suppress duplicate parallel wall instances;
6. bridge door/window/occlusion-sized gaps on the same physical wall;
7. snap wall endpoints to architectural junctions and prune isolated clutter planes;
8. complete only missing outer-envelope edges supported jointly by floor and ceiling;
9. polygonize the wall graph into spaces and reconstruct host-wall openings;
10. run Cloud<->BIM geometric feedback before IFC export.

No reference BIM is accessed during reconstruction.  Observed and inferred geometry remain
explicit through Wall.source / Wall.state and through RESF provenance values.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

import bim_reconstruction as base
from topology_completion import detect_openings, geometric_feedback


@dataclass
class FaceTrack:
    storey_id: int
    theta_deg: float
    d: float
    t0: float
    t1: float
    z0: float
    z1: float
    confidence: float
    score: float
    support: float
    continuity: float
    state: str
    A: float
    EH: float
    EV: float
    C: float
    N: float
    O: float
    G: float
    members: int

    @property
    def length(self) -> float:
        return max(0.0, self.t1 - self.t0)


_STATE_RANK = {"Clutter": 0, "Uncertain": 1, "OccludedPermanent": 2, "Permanent": 3}


def _interval_union_length(intervals: Iterable[tuple[float, float]]) -> float:
    vals = sorted((min(a, b), max(a, b)) for a, b in intervals if abs(b-a) > 1e-9)
    if not vals:
        return 0.0
    a0, a1 = vals[0]
    total = 0.0
    for b0, b1 in vals[1:]:
        if b0 <= a1:
            a1 = max(a1, b1)
        else:
            total += a1-a0
            a0, a1 = b0, b1
    return total + a1-a0


def _interval_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    return inter / max(1e-9, min(a1-a0, b1-b0))


def _gap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, max(a0, b0) - min(a1, b1))


def _candidate_score(c: base.RESFCandidate) -> float:
    # Vector-valued evidence is kept separate during extraction; this score is used only
    # by the object-consolidation stage to choose between competing physical explanations.
    geom = (
        0.18*c.A_star + 0.10*c.E_H + 0.20*c.E_V + 0.12*c.C +
        0.14*c.N + 0.08*c.T_F + 0.06*c.T_C + 0.05*c.T_W +
        0.04*c.T_B + 0.03*c.T_CP
    )
    state_bonus = 0.08 if c.state == "Permanent" else (0.04 if c.state == "OccludedPermanent" else 0.0)
    return float(np.clip(0.55*c.confidence + 0.45*geom + state_bonus, 0.0, 1.0))


def _merge_candidate_group(group: list[base.RESFCandidate]) -> FaceTrack:
    lengths = np.asarray([max(c.t1-c.t0, 0.05) for c in group], dtype=float)
    strengths = np.asarray([max(_candidate_score(c), 0.05) for c in group], dtype=float)
    weights = lengths * strengths
    d = float(np.average([c.d for c in group], weights=weights))
    t0 = min(c.t0 for c in group)
    t1 = max(c.t1 for c in group)
    observed = _interval_union_length((c.t0, c.t1) for c in group)
    full = max(t1-t0, 1e-9)
    missing = float(np.clip(1.0-observed/full, 0.0, 1.0))

    def avg(name: str) -> float:
        return float(np.average([getattr(c, name) for c in group], weights=weights))

    ref = max(group, key=_candidate_score)
    state = max((c.state for c in group), key=lambda s: _STATE_RANK.get(s, 0))
    if missing >= 0.10 and state == "Permanent":
        state = "OccludedPermanent"
    support = float(np.clip(0.55*avg("A_star") + 0.25*avg("E_V") + 0.20*avg("N"), 0.0, 1.0))
    continuity = float(np.clip(max(c.C for c in group) * (1.0-0.30*missing), 0.0, 1.0))
    score = float(np.clip(np.average([_candidate_score(c) for c in group], weights=weights) - 0.06*missing, 0.0, 1.0))
    confidence = float(np.clip(max(c.confidence for c in group) - 0.05*missing, 0.0, 1.0))
    return FaceTrack(
        ref.storey_id, ref.theta_deg, d, t0, t1,
        min(c.z0 for c in group), max(c.z1 for c in group), confidence, score,
        support, continuity, state, avg("A_star"), avg("E_H"), avg("E_V"),
        max(c.C for c in group), avg("N"), max(max(c.O for c in group), missing),
        min(c.G for c in group), len(group)
    )


def consolidate_face_tracks(cands: list[base.RESFCandidate], plane_tol: float) -> list[FaceTrack]:
    """Convert many translated RESF peaks into persistent physical wall-face tracks."""
    accepted = [
        c for c in cands
        if c.state in {"Permanent", "OccludedPermanent"}
        and c.confidence >= 0.32 and c.E_V >= 0.38 and (c.t1-c.t0) >= 0.35
    ]
    if not accepted:
        return []

    offset_tol = max(0.07, min(0.14, 3.0*plane_tol))
    groups: list[list[base.RESFCandidate]] = []
    for c in sorted(accepted, key=lambda q: (q.storey_id, q.theta_deg, q.d, q.t0)):
        best = None
        best_cost = float("inf")
        for gi, group in enumerate(groups):
            ref = max(group, key=_candidate_score)
            if c.storey_id != ref.storey_id or base._circular_distance(c.theta_deg, ref.theta_deg) > 2.5:
                continue
            dd = abs(c.d - float(np.average([x.d for x in group])))
            if dd > offset_tol:
                continue
            g0 = min(x.t0 for x in group)
            g1 = max(x.t1 for x in group)
            gap = _gap(c.t0, c.t1, g0, g1)
            overlap = _interval_overlap(c.t0, c.t1, g0, g1)
            continuation = max(c.T_CP, c.O, max(x.T_CP for x in group), max(x.O for x in group))
            # Small gaps are normal sampling fragmentation; architectural-size gaps are
            # bridged only when continuation / occlusion evidence supports one wall face.
            allowed_gap = 0.35
            if continuation >= 0.42 and min(c.E_V, max(x.E_V for x in group)) >= 0.48:
                allowed_gap = 2.40
            if gap > allowed_gap and overlap < 0.10:
                continue
            cost = 5.0*dd + gap + 0.08*base._circular_distance(c.theta_deg, ref.theta_deg) - 0.25*overlap
            if cost < best_cost:
                best = gi
                best_cost = cost
        if best is None:
            groups.append([c])
        else:
            groups[best].append(c)

    tracks = [_merge_candidate_group(g) for g in groups]
    # Remove clearly local planar clutter before orientation-family ranking.
    return [t for t in tracks if t.length >= 0.55 and (t.score >= 0.36 or t.state == "Permanent")]


def _orientation_key(theta: float, step: float = 4.0) -> float:
    return round((theta % 180.0) / step) * step % 180.0


def select_orientation_families(tracks: list[FaceTrack], max_families: int = 8) -> list[FaceTrack]:
    if not tracks:
        return []
    family_strength: dict[float, float] = {}
    for t in tracks:
        k = _orientation_key(t.theta_deg)
        family_strength[k] = family_strength.get(k, 0.0) + t.length * max(t.score, 0.05) * max(t.EV, 0.15)
    ranked = sorted(family_strength.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[0][1]
    keep: list[float] = []
    cumulative = 0.0
    total = sum(v for _, v in ranked)
    for k, strength in ranked:
        if keep and strength < 0.12*top and cumulative >= 0.90*total:
            continue
        keep.append(k)
        cumulative += strength
        if len(keep) >= max(2, min(max_families, 8)):
            break
    return [t for t in tracks if _orientation_key(t.theta_deg) in keep]


def _track_to_line(track: FaceTrack, d: float | None = None, t0: float | None = None, t1: float | None = None):
    th = math.radians(track.theta_deg)
    n = np.asarray([math.cos(th), math.sin(th)])
    tv = np.asarray([-math.sin(th), math.cos(th)])
    dd = track.d if d is None else d
    a = track.t0 if t0 is None else t0
    b = track.t1 if t1 is None else t1
    return n*dd + tv*a, n*dd + tv*b


def _wall_from_tracks(a: FaceTrack, b: FaceTrack | None, storey: base.Storey, default_thickness: float) -> base.Wall:
    if b is None:
        d = a.d
        t0, t1 = a.t0, a.t1
        thickness = default_thickness
        conf = a.confidence
        score = a.score
        support = a.support
        cont = a.continuity
        state = a.state
        source = "single_observed_face"
        A, EH, EV, C, N, O, G = a.A, a.EH, a.EV, a.C, a.N, a.O, a.G
    else:
        d = 0.5*(a.d+b.d)
        t0, t1 = min(a.t0,b.t0), max(a.t1,b.t1)
        thickness = abs(a.d-b.d)
        overlap = _interval_overlap(a.t0,a.t1,b.t0,b.t1)
        conf = float(np.clip(0.5*(a.confidence+b.confidence) + 0.10*overlap, 0.0, 1.0))
        score = 0.5*(a.score+b.score)
        support = 0.5*(a.support+b.support)
        cont = max(a.continuity,b.continuity)
        state = "OccludedPermanent" if "OccludedPermanent" in {a.state,b.state} else "Permanent"
        source = "paired_observed_faces"
        A, EH, EV = 0.5*(a.A+b.A), max(a.EH,b.EH), 0.5*(a.EV+b.EV)
        C, N, O, G = max(a.C,b.C), 0.5*(a.N+b.N), max(a.O,b.O), min(a.G,b.G)
    p1, p2 = _track_to_line(a, d=d, t0=t0, t1=t1)
    return base.Wall(
        0, storey.id, float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]),
        float(storey.z0), float(storey.z1), float(np.clip(thickness,0.09,0.48)), float(a.theta_deg),
        float(np.clip(0.55*conf+0.45*score,0,1)), float(np.clip(support,0,1)), float(np.clip(cont,0,1)),
        source, state, float(A), float(EH), float(EV), float(C), float(N), float(O), float(G)
    )


def pair_faces_to_walls(tracks: list[FaceTrack], storey: base.Storey) -> list[base.Wall]:
    """Pair opposite observed faces; leave unmatched strong tracks as one physical wall."""
    if not tracks:
        return []
    # Estimate a scene-specific typical thickness from plausible parallel-face separations.
    sep_samples = []
    for i,a in enumerate(tracks):
        for b in tracks[i+1:]:
            if base._circular_distance(a.theta_deg,b.theta_deg)>2.5:
                continue
            sep=abs(a.d-b.d)
            if 0.09<=sep<=0.48 and _interval_overlap(a.t0,a.t1,b.t0,b.t1)>=0.55:
                sep_samples.append(sep)
    default_thickness = float(np.clip(np.median(sep_samples),0.11,0.32)) if sep_samples else 0.18

    used:set[int]=set(); walls=[]
    order=sorted(range(len(tracks)),key=lambda i:(tracks[i].score*tracks[i].length),reverse=True)
    for i in order:
        if i in used:
            continue
        a=tracks[i]
        best=None; best_cost=float("inf")
        for j,b in enumerate(tracks):
            if j==i or j in used or a.storey_id!=b.storey_id:
                continue
            if base._circular_distance(a.theta_deg,b.theta_deg)>2.5:
                continue
            sep=abs(a.d-b.d)
            if not (0.08<=sep<=0.48):
                continue
            ov=_interval_overlap(a.t0,a.t1,b.t0,b.t1)
            if ov<0.55:
                continue
            # Prefer plausible masonry thickness, long overlap and similarly persistent evidence.
            cost=abs(sep-default_thickness)/max(default_thickness,0.1) + 0.8*(1.0-ov) + 0.25*abs(a.EV-b.EV)
            if cost<best_cost:
                best=j; best_cost=cost
        if best is not None and best_cost<=1.65:
            walls.append(_wall_from_tracks(a,tracks[best],storey,default_thickness))
            used.add(i); used.add(best)
        else:
            walls.append(_wall_from_tracks(a,None,storey,default_thickness))
            used.add(i)
    return walls


def _wall_axis(w: base.Wall, theta: float | None = None):
    th=math.radians(w.theta_deg if theta is None else theta)
    n=np.asarray([math.cos(th),math.sin(th)])
    tv=np.asarray([-math.sin(th),math.cos(th)])
    p1=np.asarray([w.x1,w.y1]); p2=np.asarray([w.x2,w.y2])
    d=0.5*(float(p1@n)+float(p2@n))
    a,b=sorted((float(p1@tv),float(p2@tv)))
    return n,tv,d,a,b


def _wall_length(w: base.Wall) -> float:
    return float(math.hypot(w.x2-w.x1,w.y2-w.y1))


def _merge_wall_group(group: list[base.Wall]) -> base.Wall:
    ref=max(group,key=lambda w:_wall_length(w)*max(w.confidence,0.1))
    theta=ref.theta_deg
    n,tv,_,_,_=_wall_axis(ref,theta)
    weights=np.asarray([max(_wall_length(w),0.1)*max(w.confidence,0.1) for w in group])
    ds=[]; ints=[]
    for w in group:
        _,_,d,a,b=_wall_axis(w,theta); ds.append(d); ints.append((a,b))
    d=float(np.average(ds,weights=weights)); a=min(x for x,_ in ints); b=max(y for _,y in ints)
    observed=_interval_union_length(ints); full=max(b-a,1e-9); missing=float(np.clip(1-observed/full,0,1))
    p1=n*d+tv*a; p2=n*d+tv*b
    def avg(name): return float(np.average([getattr(w,name) for w in group],weights=weights))
    source="coplanar_gap_completion" if missing>0.04 or len(group)>1 else ref.source
    state="OccludedPermanent" if missing>0.10 or any(w.state=="OccludedPermanent" for w in group) else "Permanent"
    return base.Wall(
        0,ref.storey_id,float(p1[0]),float(p1[1]),float(p2[0]),float(p2[1]),
        min(w.z0 for w in group),max(w.z1 for w in group),float(np.clip(avg("thickness"),0.09,0.48)),theta,
        float(np.clip(max(w.confidence for w in group)-0.06*missing,0,1)),avg("support"),max(w.continuity for w in group),
        source,state,avg("resf_A"),avg("resf_EH"),avg("resf_EV"),max(w.resf_C for w in group),avg("resf_N"),
        max(max(w.resf_O for w in group),missing),min(w.geometric_feedback for w in group)
    )


def merge_collinear_walls(walls: list[base.Wall], max_gap: float=2.35) -> list[base.Wall]:
    groups=[]
    for w in sorted(walls,key=lambda q:(q.storey_id,q.theta_deg,-q.confidence)):
        best=None; best_cost=float("inf")
        for gi,g in enumerate(groups):
            ref=max(g,key=_wall_length)
            if w.storey_id!=ref.storey_id or base._circular_distance(w.theta_deg,ref.theta_deg)>3.0:
                continue
            _,_,dw,a0,a1=_wall_axis(w,ref.theta_deg)
            ds=[]; ints=[]
            for q in g:
                _,_,dq,b0,b1=_wall_axis(q,ref.theta_deg); ds.append(dq); ints.append((b0,b1))
            dg=float(np.average(ds,weights=[max(_wall_length(q),0.1) for q in g]))
            dd=abs(dw-dg)
            if dd>max(0.12,0.45*max([w.thickness]+[q.thickness for q in g])):
                continue
            g0=min(a for a,_ in ints); g1=max(b for _,b in ints)
            gap=_gap(a0,a1,g0,g1)
            if gap>max_gap:
                continue
            # Long completion gaps require strong architectural evidence on both sides.
            if gap>0.55 and min(w.confidence,max(q.confidence for q in g))<0.48:
                continue
            cost=gap+5.0*dd+0.1*base._circular_distance(w.theta_deg,ref.theta_deg)
            if cost<best_cost: best=gi; best_cost=cost
        if best is None: groups.append([w])
        else: groups[best].append(w)
    out=[_merge_wall_group(g) for g in groups]
    for i,w in enumerate(out,1): w.id=i
    return out


def suppress_parallel_duplicates(walls: list[base.Wall]) -> list[base.Wall]:
    """Global NMS that removes the comb-like parallel-sheet failure mode."""
    kept=[]
    for w in sorted(walls,key=lambda q:(q.confidence*_wall_length(q),q.support),reverse=True):
        _,_,dw,a0,a1=_wall_axis(w)
        duplicate=False
        for k in kept:
            if w.storey_id!=k.storey_id or base._circular_distance(w.theta_deg,k.theta_deg)>3.0:
                continue
            _,_,dk,b0,b1=_wall_axis(k,w.theta_deg)
            ov=_interval_overlap(a0,a1,b0,b1)
            dd=abs(dw-dk)
            # After face pairing, two physical walls should not occupy almost the same band.
            if ov>=0.55 and dd<=max(0.16,0.55*max(w.thickness,k.thickness)):
                duplicate=True; break
        if not duplicate:
            kept.append(w)
    for i,w in enumerate(kept,1): w.id=i
    return kept


def snap_junctions(walls: list[base.Wall], reach: float=0.55) -> list[base.Wall]:
    if len(walls)<2:
        return walls
    reach=max(0.20,float(reach))
    for _ in range(2):
        endpoints=[]
        ext=[]
        for w in walls:
            p1=np.asarray([w.x1,w.y1]); p2=np.asarray([w.x2,w.y2]); L=float(np.linalg.norm(p2-p1))
            u=(p2-p1)/max(L,1e-9)
            endpoints.append((p1,p2)); ext.append(LineString([p1-u*reach,p2+u*reach]))
        proposals=[[] for _ in walls]
        for i in range(len(walls)):
            for j in range(i+1,len(walls)):
                if walls[i].storey_id!=walls[j].storey_id or base._circular_distance(walls[i].theta_deg,walls[j].theta_deg)<12.0:
                    continue
                inter=ext[i].intersection(ext[j])
                if inter.geom_type!="Point": continue
                q=np.asarray([inter.x,inter.y])
                if min(np.linalg.norm(q-endpoints[i][0]),np.linalg.norm(q-endpoints[i][1]))<=reach and min(np.linalg.norm(q-endpoints[j][0]),np.linalg.norm(q-endpoints[j][1]))<=reach:
                    proposals[i].append(q); proposals[j].append(q)
        for i,w in enumerate(walls):
            p1,p2=endpoints[i]
            for end,p in ((0,p1),(1,p2)):
                if not proposals[i]: continue
                ds=np.asarray([np.linalg.norm(q-p) for q in proposals[i]])
                k=int(np.argmin(ds))
                if ds[k]<=reach:
                    q=proposals[i][k]
                    if end==0: w.x1,w.y1=map(float,q)
                    else: w.x2,w.y2=map(float,q)
    for i,w in enumerate(walls,1): w.id=i
    return walls


def _endpoint_degree(walls: list[base.Wall], idx: int, radius: float=0.30) -> tuple[int,int]:
    w=walls[idx]; out=[]
    for p in (Point(w.x1,w.y1),Point(w.x2,w.y2)):
        n=0
        for j,q in enumerate(walls):
            if j==idx or q.storey_id!=w.storey_id: continue
            if LineString([(q.x1,q.y1),(q.x2,q.y2)]).distance(p)<=radius: n+=1
        out.append(n)
    return out[0],out[1]


def prune_wall_graph(walls: list[base.Wall]) -> list[base.Wall]:
    if not walls: return []
    out=[]
    for i,w in enumerate(walls):
        L=_wall_length(w); d0,d1=_endpoint_degree(walls,i)
        structural=(d0>0 or d1>0)
        # Clutter commonly appears as short isolated planes or high-confidence planar furniture.
        if L<0.65: continue
        if not structural and L<1.15 and w.confidence<0.70: continue
        if not structural and w.resf_EV<0.58 and w.confidence<0.72: continue
        if w.confidence<0.36 and not structural: continue
        out.append(w)
    for i,w in enumerate(out,1): w.id=i
    return out


def _polygon_edges(poly: Polygon, min_len: float=0.65):
    coords=list(poly.exterior.coords)
    for a,b in zip(coords[:-1],coords[1:]):
        if math.hypot(b[0]-a[0],b[1]-a[1])>=min_len:
            yield np.asarray(a,dtype=float),np.asarray(b,dtype=float)


def envelope_rescue(pts: np.ndarray, walls: list[base.Wall], storey: base.Storey) -> list[base.Wall]:
    """Infer only missing outer edges jointly supported by floor AND ceiling footprints."""
    try:
        floor=base._horizontal_footprint(pts,storey.z0,0.12)
        ceil=base._horizontal_footprint(pts,storey.z1,0.14)
    except Exception:
        return walls
    if floor is None or ceil is None or floor.geom_type!="Polygon" or ceil.geom_type!="Polygon":
        return walls
    f=floor.simplify(0.22,preserve_topology=True); c=ceil.simplify(0.22,preserve_topology=True)
    if f.geom_type!="Polygon" or c.geom_type!="Polygon": return walls
    existing=list(walls); new=[]
    c_boundary=c.boundary
    for a,b in _polygon_edges(f,0.80):
        line=LineString([a,b])
        if line.length<0.8: continue
        # Joint floor-ceiling boundary requirement prevents furniture footprints from creating walls.
        if line.hausdorff_distance(c_boundary)>0.45 and line.distance(c_boundary)>0.28:
            continue
        mid=line.interpolate(0.5,normalized=True)
        if any(LineString([(w.x1,w.y1),(w.x2,w.y2)]).distance(mid)<=0.28 for w in existing):
            continue
        dx,dy=b-a; theta=(math.degrees(math.atan2(-dx,dy))%180.0)
        conf=0.48
        new.append(base.Wall(
            0,storey.id,float(a[0]),float(a[1]),float(b[0]),float(b[1]),storey.z0,storey.z1,0.18,theta,
            conf,0.35,0.45,"floor_ceiling_envelope_inference","OccludedPermanent",0.0,0.0,1.0,0.0,0.0,0.65,0.45
        ))
    return existing+new


def _network_duplicate_count(walls: list[base.Wall]) -> int:
    n=0
    for i,w in enumerate(walls):
        _,_,dw,a0,a1=_wall_axis(w)
        for k in walls[i+1:]:
            if w.storey_id!=k.storey_id or base._circular_distance(w.theta_deg,k.theta_deg)>3: continue
            _,_,dk,b0,b1=_wall_axis(k,w.theta_deg)
            if abs(dw-dk)<=max(0.16,0.55*max(w.thickness,k.thickness)) and _interval_overlap(a0,a1,b0,b1)>=0.55:
                n+=1
    return n


def _junction_ratio(walls: list[base.Wall]) -> float:
    if not walls: return 0.0
    connected=0; total=2*len(walls)
    for i in range(len(walls)):
        d0,d1=_endpoint_degree(walls,i,0.32); connected+=(d0>0)+(d1>0)
    return float(connected/max(total,1))


def reconstruct_bim(request: dict) -> base.BIMModel:
    raw=base.load_xyz(request["input"],int(request.get("max_points",2_000_000)))
    if len(raw)<100:
        raise ValueError("Point cloud contains too few valid points for BIM reconstruction")

    raster=max(0.04,float(request.get("raster_cell_m",0.05)))
    pts=base.voxel_subsample(raw,max(0.025,min(0.05,raster)))
    storeys=base.detect_storeys(pts)
    threshold=float(request.get("confidence_threshold",0.50))
    multi=bool(request.get("multi_peak",True)); topo=bool(request.get("topology",True))
    occ=bool(request.get("occlusion",True)); fuzzy=bool(request.get("fuzzy",True))
    feedback=bool(request.get("geometric_feedback",True))
    plane_spacing=max(0.008,float(request.get("plane_spacing_m",0.01)))
    plane_tol=max(0.02,float(request.get("plane_tolerance_m",0.03)))
    max_orient=int(request.get("max_orientation_families",12))

    all_walls=[]; all_spaces=[]; all_slabs=[]; all_candidates=[]
    diagnostics=[]

    for s in storeys:
        band=pts[(pts[:,2]>=s.z0-0.10)&(pts[:,2]<=s.z1+0.10)]
        if len(band)<100: continue
        orientations=base.detect_orientations(band,float(request.get("angle_step_deg",2.0)),max_orient)
        cands=base.build_resf_candidates(band,s,orientations,plane_spacing,plane_tol,raster,multi)
        base.enrich_topology(cands)
        for c in cands:
            c.state,c.confidence=base.fuzzy_decision(c,threshold,fuzzy,topo,occ)
        if feedback:
            base._apply_candidate_feedback(band,cands)
            for c in cands:
                c.state,c.confidence=base.fuzzy_decision(c,threshold,fuzzy,topo,occ)

        tracks=consolidate_face_tracks(cands,plane_tol)
        tracks=select_orientation_families(tracks,max_orient)
        walls=pair_faces_to_walls(tracks,s)
        walls=merge_collinear_walls(walls,2.35 if topo else 0.45)
        walls=suppress_parallel_duplicates(walls)
        walls=snap_junctions(walls,0.60 if topo else 0.22)
        walls=prune_wall_graph(walls)

        # Only use floor+ceiling envelope completion when the wall graph does not close rooms.
        provisional=list(polygonize(unary_union([LineString([(w.x1,w.y1),(w.x2,w.y2)]) for w in walls]))) if len(walls)>=3 else []
        if topo and not provisional:
            walls=envelope_rescue(band,walls,s)
            walls=merge_collinear_walls(walls,1.25)
            walls=suppress_parallel_duplicates(walls)
            walls=snap_junctions(walls,0.62)
            walls=prune_wall_graph(walls)

        if feedback and walls:
            geometric_feedback(band,walls)
            walls=[w for w in walls if w.confidence>=0.34 or w.source=="floor_ceiling_envelope_inference"]
            walls=suppress_parallel_duplicates(walls)

        baseid=len(all_walls)
        for i,w in enumerate(walls,1): w.id=baseid+i
        spaces=base.spaces_from_walls(walls,s) if topo else []
        slabs=base.slabs_from_evidence(band,spaces,s)
        all_walls.extend(walls); all_spaces.extend(spaces); all_slabs.extend(slabs); all_candidates.extend(cands)
        diagnostics.append({
            "storey":s.id,"orientations":len(orientations),"tracks":len(tracks),"walls":len(walls),
            "duplicates":_network_duplicate_count(walls),"junction_ratio":_junction_ratio(walls),"spaces":len(spaces)
        })

    for i,x in enumerate(all_spaces,1): x.id=i
    for i,x in enumerate(all_slabs,1): x.id=i
    openings=detect_openings(pts,all_walls,raster)
    for i,o in enumerate(openings,1): o.id=i

    warnings=[]
    duplicates=_network_duplicate_count(all_walls)
    junction_ratio=_junction_ratio(all_walls)
    if duplicates:
        warnings.append(f"{duplicates} near-duplicate parallel wall pairs remain after global NMS")
    if junction_ratio<0.45 and len(all_walls)>=4:
        warnings.append(f"Wall graph endpoint connectivity is low ({junction_ratio:.2f}); review inferred room continuity")
    if not all_spaces:
        warnings.append("No closed IfcSpace polygons reconstructed; wall graph remains incomplete")
    if not openings:
        warnings.append("No host-wall door/window objects passed the opening evidence checks")
    if len(all_walls)>120:
        warnings.append("Excessive wall-instance count detected; IFC should be reviewed before downstream BIM use")

    summary={
        "status":"success" if all_walls else "warning",
        "method":"Fuzzy-RESF-BIM","version":"4.0-paper-aligned-object-graph",
        "input_points":int(len(raw)),"regularized_points":int(len(pts)),"storeys":len(storeys),
        "resf_candidates":len(all_candidates),"accepted_candidates":sum(c.state in {"Permanent","OccludedPermanent"} for c in all_candidates),
        "walls":len(all_walls),"doors":sum(o.kind=="Door" for o in openings),"windows":sum(o.kind=="Window" for o in openings),
        "openings":len(openings),"spaces":len(all_spaces),"slabs":len(all_slabs),
        "wall_duplicate_pairs":duplicates,"wall_junction_ratio":junction_ratio,
        "wall_reconstruction":"RESF_face_tracks_to_paired_physical_walls_to_global_room_graph",
        "observed_inferred_separation":True,
        "occlusion_evidence":"geometry_topology_proxy_without_scanner_poses",
        "semantic_evidence":"geometry_prior_without_independent_pointwise_semantic_backbone",
        "storey_diagnostics":diagnostics,"warnings":warnings,
    }
    return base.BIMModel(storeys,all_walls,openings,all_spaces,all_slabs,all_candidates,summary)
