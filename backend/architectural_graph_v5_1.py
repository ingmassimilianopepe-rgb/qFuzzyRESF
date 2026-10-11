"""Fuzzy-RESF-BIM v5.1 orientation regularisation.

The v5 persistent-footprint stage removed furniture-height sheets, but the first regression
showed that one physical Manhattan family was still represented by several nearby angular
hypotheses (e.g. 80, 88 and 96 deg).  Those are raster/endpoint side-lobes, not distinct BIM
wall families.  This layer clusters room-scale orientation evidence before line extraction
and, only when a clearly dominant orthogonal pair exists, estimates one common axis and its
90-degree mate.  Non-Manhattan scenes keep multiple independently supported families.
"""

from __future__ import annotations

import math
import numpy as np

import bim_reconstruction as base
import architectural_graph_v5 as core


def _axis_mean(angles: list[float], weights: list[float]) -> float:
    """Weighted circular mean for unoriented axes (period 180 degrees)."""
    a=np.deg2rad(np.asarray(angles,dtype=float)*2.0)
    w=np.asarray(weights,dtype=float)
    x=float(np.sum(w*np.cos(a))); y=float(np.sum(w*np.sin(a)))
    if abs(x)+abs(y)<1e-12:
        return float(angles[0]%180.0)
    return float((0.5*math.degrees(math.atan2(y,x)))%180.0)


def _orthogonal_base(a0: float, a1: float) -> float:
    candidates=[(a1-90.0)%180.0,(a1+90.0)%180.0]
    return min(candidates,key=lambda a:base._circular_distance(a,a0))


def select_room_orientations(cells: core.StructuralCells, angle_step: float,
                             max_families: int) -> tuple[list[float],str]:
    if len(cells.xy)<20:
        return [],"insufficient_vertical_persistence"
    step=max(1.0,float(angle_step))
    scored=[(float(a),core._orientation_score(cells,float(a))) for a in np.arange(0.0,180.0,step)]
    scored.sort(key=lambda x:x[1],reverse=True)
    if not scored or scored[0][1]<=0:
        return [],"no_orientation_support"

    # A 20-degree NMS is deliberate here.  A physical wall axis should not appear as
    # several 8-12 degree variants merely because a rectangular footprint is rasterised.
    selected=[]
    sep=max(18.0,7.0*step)
    for angle,strength in scored:
        if strength<0.10*scored[0][1]:
            break
        if all(base._circular_distance(angle,a)>=sep for a,_ in selected):
            selected.append((angle,strength))
        if len(selected)>=max(2,min(int(max_families),8)):
            break
    if not selected:
        return [],"no_separated_orientation_support"

    # Choose the strongest near-orthogonal pair globally, not simply the first two peaks.
    best=None
    for i,(a0,s0) in enumerate(selected):
        for a1,s1 in selected[i+1:]:
            ad=base._circular_distance(a0,a1)
            if 74.0<=ad<=106.0:
                q=s0+s1
                if best is None or q>best[0]:
                    best=(q,a0,s0,a1,s1)
    total=sum(s for _,s in selected)
    if best is not None:
        q,a0,s0,a1,s1=best
        if q>=0.55*total and min(s0,s1)>=0.22*max(s0,s1):
            b1=_orthogonal_base(a0,a1)
            alpha=_axis_mean([a0,b1],[s0,s1])
            beta=(alpha+90.0)%180.0
            return sorted([alpha,beta]),"regularized_dominant_orthogonal_room_graph"

    top=selected[0][1]
    keep=[a for a,s in selected if s>=0.22*top]
    return sorted(keep[:max(2,min(int(max_families),6))]),"clustered_multi_orientation_room_graph"


core.select_room_orientations=select_room_orientations


def reconstruct_bim(request: dict):
    model=core.reconstruct_bim(request)
    model.summary["version"]="5.1-persistent-architectural-graph"
    model.summary["orientation_regularisation"]="clustered_axis_evidence_with_conditional_orthogonal_constraint"
    return model
