"""Refinement layer for Fuzzy-RESF-BIM v4.1.

The v4 object graph was intentionally conservative, but stress regression showed two
remaining failure modes typical of indoor scans: (1) a short cabinet face could be paired
with a much longer wall face if overlap was normalized by the shorter interval, and (2)
unmatched furniture faces parallel to a real wall could survive as full-height BIM sheets.
This layer fixes both at the physical-object stage without changing the manuscript RESF
candidate/evidence stage.
"""

from __future__ import annotations

import numpy as np

import bim_reconstruction as base
import paper_aligned_bim_v4 as core


def _coverage_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Intersection divided by the longer interval: appropriate for physical face pairing."""
    inter=max(0.0,min(a1,b1)-max(a0,b0))
    return inter/max(1e-9,max(a1-a0,b1-b0))


def _short_coverage(a0: float, a1: float, b0: float, b1: float) -> float:
    """How much of the shorter interval is covered by the longer one."""
    inter=max(0.0,min(a1,b1)-max(a0,b0))
    return inter/max(1e-9,min(a1-a0,b1-b0))


def select_orientation_families(tracks: list[core.FaceTrack], max_families: int=8) -> list[core.FaceTrack]:
    if not tracks:
        return []
    strength={}
    for t in tracks:
        k=core._orientation_key(t.theta_deg)
        # Room-scale persistence, not local point density, determines orientation support.
        strength[k]=strength.get(k,0.0)+t.length*max(t.score,0.05)*max(t.EV,0.15)*max(t.continuity,0.20)
    ranked=sorted(strength.items(),key=lambda kv:kv[1],reverse=True)
    top=ranked[0][1]
    total=sum(v for _,v in ranked)

    # When two approximately orthogonal families explain most of a room, suppress
    # weak angular side-lobes. Non-Manhattan scenes fall through to the general branch.
    if len(ranked)>=2:
        k0,s0=ranked[0]
        for k1,s1 in ranked[1:4]:
            angular=base._circular_distance(k0,k1)
            if 72.0<=angular<=108.0 and (s0+s1)>=0.72*total and s1>=0.28*s0:
                keep={k0,k1}
                return [t for t in tracks if core._orientation_key(t.theta_deg) in keep]

    keep=[]; cumulative=0.0
    for k,s in ranked:
        if keep and s<0.22*top and cumulative>=0.82*total:
            continue
        if s<0.12*top and len(keep)>=2:
            continue
        keep.append(k); cumulative+=s
        if len(keep)>=max(2,min(max_families,6)):
            break
    chosen=set(keep)
    return [t for t in tracks if core._orientation_key(t.theta_deg) in chosen]


def pair_faces_to_walls(tracks: list[core.FaceTrack], storey: base.Storey) -> list[base.Wall]:
    if not tracks:
        return []

    # Thickness can only be inferred from faces that cover most of one another.
    # A short cabinet in front of a long wall cannot define masonry thickness.
    sep_samples=[]
    for i,a in enumerate(tracks):
        for b in tracks[i+1:]:
            if base._circular_distance(a.theta_deg,b.theta_deg)>2.5:
                continue
            sep=abs(a.d-b.d)
            cov=_coverage_overlap(a.t0,a.t1,b.t0,b.t1)
            length_ratio=min(a.length,b.length)/max(a.length,b.length,1e-9)
            if 0.09<=sep<=0.45 and cov>=0.62 and length_ratio>=0.62:
                sep_samples.append(sep)
    default_thickness=float(np.clip(np.median(sep_samples),0.11,0.30)) if sep_samples else 0.18

    used=set(); walls=[]
    order=sorted(range(len(tracks)),key=lambda i:tracks[i].score*tracks[i].length,reverse=True)
    for i in order:
        if i in used: continue
        a=tracks[i]; best=None; best_cost=float("inf")
        for j,b in enumerate(tracks):
            if j==i or j in used or a.storey_id!=b.storey_id: continue
            if base._circular_distance(a.theta_deg,b.theta_deg)>2.5: continue
            sep=abs(a.d-b.d)
            if not (0.08<=sep<=0.45): continue
            cov=_coverage_overlap(a.t0,a.t1,b.t0,b.t1)
            ratio=min(a.length,b.length)/max(a.length,b.length,1e-9)
            if cov<0.62 or ratio<0.62: continue
            cost=(abs(sep-default_thickness)/max(default_thickness,0.1)+
                  1.1*(1.0-cov)+0.7*(1.0-ratio)+0.20*abs(a.EV-b.EV))
            if cost<best_cost:
                best=j; best_cost=cost
        if best is not None and best_cost<=1.45:
            walls.append(core._wall_from_tracks(a,tracks[best],storey,default_thickness)); used.add(i); used.add(best)
        else:
            walls.append(core._wall_from_tracks(a,None,storey,default_thickness)); used.add(i)
    return walls


def suppress_parallel_duplicates(walls: list[base.Wall]) -> list[base.Wall]:
    """Remove residual same-band physical duplicates after face pairing."""
    kept=[]
    for w in sorted(walls,key=lambda q:(q.confidence*core._wall_length(q),q.support),reverse=True):
        _,_,dw,a0,a1=core._wall_axis(w)
        duplicate=False
        for k in kept:
            if w.storey_id!=k.storey_id or base._circular_distance(w.theta_deg,k.theta_deg)>3.0: continue
            _,_,dk,b0,b1=core._wall_axis(k,w.theta_deg)
            cov=_coverage_overlap(a0,a1,b0,b1)
            dd=abs(dw-dk)
            if cov>=0.58 and dd<=max(0.20,0.65*max(w.thickness,k.thickness)):
                duplicate=True; break
        if not duplicate: kept.append(w)
    for i,w in enumerate(kept,1): w.id=i
    return kept


_original_prune=core.prune_wall_graph


def prune_wall_graph(walls: list[base.Wall]) -> list[base.Wall]:
    """Topology pruning plus explicit rejection of parallel foreground furniture planes.

    The manuscript notes that planar furniture can be aligned with permanent walls. A short,
    vertically incomplete single face lying a few decimetres in front of a much longer,
    stronger parallel wall is therefore negative architectural evidence, not a second wall.
    """
    walls=_original_prune(walls)
    if len(walls)<2:
        return walls

    strength=lambda w: core._wall_length(w)*max(w.confidence,0.05)*max(w.resf_EV,0.15)
    ordered=sorted(walls,key=strength,reverse=True)
    kept=[]
    for w in ordered:
        Lw=core._wall_length(w)
        _,_,dw,a0,a1=core._wall_axis(w)
        shadow=False
        for k in kept:
            if w.storey_id!=k.storey_id or base._circular_distance(w.theta_deg,k.theta_deg)>3.0:
                continue
            Lk=core._wall_length(k)
            if Lk<=Lw: continue
            _,_,dk,b0,b1=core._wall_axis(k,w.theta_deg)
            dd=abs(dw-dk)
            covered=_short_coverage(a0,a1,b0,b1)
            # Furniture/cabinet signature: short and vertically incomplete, almost
            # contained along a stronger wall, within a typical furnishing depth.
            if (0.08<=dd<=0.70 and covered>=0.72 and Lw<=0.58*Lk and
                w.resf_EV<0.78 and w.source=="single_observed_face"):
                shadow=True; break
        if not shadow:
            kept.append(w)

    # A second conservative pass removes isolated sub-1.5 m single faces with weak
    # vertical persistence. This threshold lies inside the manuscript sensitivity range.
    final=[]
    for i,w in enumerate(kept):
        L=core._wall_length(w)
        d0,d1=core._endpoint_degree(kept,i,0.32)
        if (w.source=="single_observed_face" and L<1.50 and d0==0 and d1==0 and
            w.resf_EV<0.72):
            continue
        final.append(w)
    for i,w in enumerate(final,1): w.id=i
    return final


# Patch the v4 reconstruction graph at module scope. Candidate extraction, fuzzy inference,
# opening reconstruction, IFC generation and diagnostics remain unchanged.
core.select_orientation_families=select_orientation_families
core.pair_faces_to_walls=pair_faces_to_walls
core.suppress_parallel_duplicates=suppress_parallel_duplicates
core.prune_wall_graph=prune_wall_graph


def reconstruct_bim(request: dict) -> base.BIMModel:
    model=core.reconstruct_bim(request)
    model.summary["version"]="4.1-paper-aligned-object-graph"
    model.summary["face_pairing"]="long-coverage-aware"
    model.summary["orientation_selection"]="room-scale-support-with-orthogonal-dominance"
    model.summary["foreground_parallel_clutter_rejection"]=True
    return model
