"""Fuzzy-RESF-BIM v3 semantic reconstruction backend.

Implements the paper's evidence-driven RESF pipeline and extends the frozen v1 wall-envelope
scope with object-specific BIM reconstruction for openings and spaces.

The core principles are:
- density regularisation and storey decomposition before planimetric analysis;
- vector-valued RESF evidence A*, EH, EV, C, N, O;
- multi-orientation, multi-peak candidate retention;
- topology/occlusion/fuzzy evidence fusion;
- separate surface-hypothesis and physical-wall instance stages;
- host-wall-aware door/window reconstruction;
- geometric feedback before IFC export.

No reference BIM is used during reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from collections import deque
import csv
import json
import math
from typing import Iterable

import numpy as np
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.ops import polygonize, unary_union

try:
    from shapely import concave_hull
except Exception:  # Shapely < 2 fallback (package requirement is >=2)
    concave_hull = None


@dataclass
class Storey:
    id: int
    z0: float
    z1: float
    confidence: float


@dataclass
class RESFCandidate:
    id: int
    storey_id: int
    theta_deg: float
    d: float
    t0: float
    t1: float
    z0: float
    z1: float
    A_star: float
    E_H: float
    E_V: float
    C: float
    N: float
    O: float
    rho: float
    semantic_wall: float
    T_F: float = 0.0
    T_C: float = 0.0
    T_W: float = 0.0
    T_B: float = 0.0
    T_R: float = 0.0
    T_CP: float = 0.0
    G: float = 1.0
    confidence: float = 0.0
    state: str = "Uncertain"


@dataclass
class Wall:
    id: int
    storey_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    z0: float
    z1: float
    thickness: float
    theta_deg: float
    confidence: float
    support: float
    continuity: float
    source: str
    state: str = "Permanent"
    resf_A: float = 0.0
    resf_EH: float = 0.0
    resf_EV: float = 0.0
    resf_C: float = 0.0
    resf_N: float = 0.0
    resf_O: float = 0.0
    geometric_feedback: float = 1.0


@dataclass
class Opening:
    id: int
    wall_id: int
    storey_id: int
    kind: str
    offset: float
    width: float
    sill: float
    height: float
    confidence: float
    source: str = "wall_local_vacancy"


@dataclass
class Space:
    id: int
    storey_id: int
    name: str
    polygon: list[list[float]]
    z0: float
    z1: float
    area: float
    confidence: float


@dataclass
class Slab:
    id: int
    storey_id: int
    kind: str
    polygon: list[list[float]]
    z: float
    thickness: float
    confidence: float


@dataclass
class BIMModel:
    storeys: list[Storey]
    walls: list[Wall]
    openings: list[Opening]
    spaces: list[Space]
    slabs: list[Slab]
    candidates: list[RESFCandidate]
    summary: dict


def load_xyz(path: str | Path, max_points: int = 2_000_000) -> np.ndarray:
    pts = np.loadtxt(path, dtype=np.float64, usecols=(0, 1, 2))
    if pts.ndim == 1:
        pts = pts.reshape(1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) > max_points:
        # deterministic spatial sampling: retain points across the complete spatial support
        lo = pts.min(axis=0)
        span = np.maximum(pts.max(axis=0) - lo, 1e-9)
        q = np.floor((pts - lo) / span * 2047).astype(np.int64)
        key = q[:, 0] + 2048 * q[:, 1] + (2048**2) * q[:, 2]
        order = np.argsort(key, kind="mergesort")
        idx = order[np.linspace(0, len(order) - 1, max_points, dtype=np.int64)]
        pts = pts[idx]
    return pts


def voxel_subsample(pts: np.ndarray, voxel: float) -> np.ndarray:
    if len(pts) == 0:
        return pts
    voxel = max(float(voxel), 0.005)
    origin = pts.min(axis=0)
    q = np.floor((pts - origin) / voxel).astype(np.int64)
    _, idx = np.unique(q, axis=0, return_index=True)
    return pts[np.sort(idx)]


def _smooth(v: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0 or v.size == 0:
        return v.astype(float, copy=True)
    k = np.ones(2 * radius + 1, dtype=float)
    k /= k.sum()
    return np.convolve(v.astype(float), k, mode="same")


def _tri(x: float, a: float, b: float, c: float) -> float:
    if b <= a or c <= b:
        return 0.0
    return float(max(min((x-a)/(b-a), (c-x)/(c-b)), 0.0))


def _trap(x: float, a: float, b: float, c: float, d: float) -> float:
    if not (a <= b <= c <= d):
        return 0.0
    if b <= x <= c:
        return 1.0
    if a < x < b:
        return float((x-a) / max(b-a, 1e-9))
    if c < x < d:
        return float((d-x) / max(d-c, 1e-9))
    return 0.0


def _circular_distance(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _horizontal_level_score(pts: np.ndarray, z_edges: np.ndarray, xy_cell: float = 0.12) -> np.ndarray:
    """XY occupied area per elevation bin, less sensitive than raw point counts."""
    if len(pts) == 0:
        return np.zeros(len(z_edges)-1, dtype=float)
    xy0 = pts[:, :2].min(axis=0)
    qx = np.floor((pts[:, 0] - xy0[0]) / xy_cell).astype(np.int64)
    qy = np.floor((pts[:, 1] - xy0[1]) / xy_cell).astype(np.int64)
    zi = np.searchsorted(z_edges, pts[:, 2], side="right") - 1
    valid = (zi >= 0) & (zi < len(z_edges)-1)
    if not np.any(valid):
        return np.zeros(len(z_edges)-1, dtype=float)
    qx, qy, zi = qx[valid], qy[valid], zi[valid]
    minx, miny = int(qx.min()), int(qy.min())
    qx, qy = qx-minx, qy-miny
    nx = int(qx.max()) + 1
    ny = int(qy.max()) + 1
    key = zi.astype(np.int64) * max(1, nx*ny) + qy * max(1, nx) + qx
    uniq = np.unique(key)
    zuniq = uniq // max(1, nx*ny)
    return np.bincount(zuniq, minlength=len(z_edges)-1).astype(float)


def detect_storeys(pts: np.ndarray) -> list[Storey]:
    z = pts[:, 2]
    if len(z) < 100:
        return [Storey(1, float(z.min()), float(z.max()), 0.25)]
    lo, hi = map(float, np.quantile(z, [0.002, 0.998]))
    if hi - lo < 1.0:
        return [Storey(1, lo, hi, 0.25)]
    dz = max(0.035, min(0.06, (hi-lo)/350.0))
    n = max(32, int(math.ceil((hi-lo)/dz)))
    edges = np.linspace(lo, hi, n+1)
    area = _horizontal_level_score(pts, edges, xy_cell=0.12)
    score = _smooth(area, 2)
    if not np.any(score > 0):
        q02, q98 = map(float, np.quantile(z, [0.02, 0.98]))
        return [Storey(1, q02, q98, 0.45)]

    base = max(float(np.quantile(score[score > 0], 0.72)), 0.18 * float(score.max()))
    peaks: list[tuple[float, float]] = []
    for i in range(2, len(score)-2):
        if score[i] >= base and score[i] >= score[i-1] and score[i] >= score[i+1]:
            j0, j1 = max(0, i-2), min(len(area), i+3)
            j = j0 + int(np.argmax(area[j0:j1]))
            zc = 0.5 * (edges[j] + edges[j+1])
            local_z = z[np.abs(z-zc) <= max(0.06, 1.5*(edges[j+1]-edges[j]))]
            if local_z.size:
                zc = float(np.median(local_z))
            if peaks and abs(zc-peaks[-1][0]) < 0.18:
                if score[i] > peaks[-1][1]:
                    peaks[-1] = (float(zc), float(score[i]))
            else:
                peaks.append((float(zc), float(score[i])))

    q02, q98 = map(float, np.quantile(z, [0.02, 0.98]))
    levels = [p[0] for p in peaks]
    if not levels or min(levels) > q02 + 0.35:
        levels.insert(0, q02)
    if max(levels) < q98 - 0.35:
        levels.append(q98)
    levels = sorted(levels)

    pairs: list[tuple[float, float, float]] = []
    i = 0
    while i < len(levels)-1:
        f = levels[i]
        candidates = [(j, levels[j]) for j in range(i+1, len(levels)) if 2.05 <= levels[j]-f <= 5.2]
        if not candidates:
            i += 1
            continue
        j, c = min(candidates, key=lambda p: (abs((p[1]-f)-2.85), p[0]))
        h = c-f
        conf = float(np.clip(1.0 - abs(h-2.85)/2.5, 0.45, 0.96))
        pairs.append((f, c, conf))
        i = j

    if not pairs:
        pairs = [(q02, q98, 0.45)]

    clean: list[Storey] = []
    for f, c, conf in pairs:
        if c-f < 1.8:
            continue
        if clean and f < clean[-1].z1 - 0.35:
            prev = clean[-1]
            prev_h = prev.z1-prev.z0
            cur_h = c-f
            if abs(cur_h-2.85) >= abs(prev_h-2.85):
                continue
            clean.pop()
        clean.append(Storey(len(clean)+1, float(f), float(c), conf))
    return clean or [Storey(1, q02, q98, 0.45)]


def _projected_peak_score(xy: np.ndarray, z: np.ndarray, theta: float,
                          d_bin: float, t_cell: float, z_cell: float = 0.10) -> float:
    """Orientation discovery from occupied (t,z) cells per offset bin."""
    th = math.radians(theta)
    n = np.array([math.cos(th), math.sin(th)])
    tvec = np.array([-math.sin(th), math.cos(th)])
    d = xy @ n
    t = xy @ tvec
    di = np.floor((d - float(d.min())) / d_bin).astype(np.int64)
    ti = np.floor((t - float(t.min())) / t_cell).astype(np.int64)
    zi = np.floor((z - float(z.min())) / z_cell).astype(np.int64)
    nt = max(int(ti.max()) + 1, 1)
    nz = max(int(zi.max()) + 1, 1)
    key = (di.astype(np.int64) * nt + ti) * nz + zi
    uniq = np.unique(key)
    db = uniq // (nt * nz)
    h = np.bincount(db, minlength=max(int(di.max()) + 1, 1)).astype(float)
    hs = _smooth(h, max(1, int(round(0.04 / d_bin))))
    if hs.size == 0:
        return 0.0
    top = np.sort(hs)[-min(6, len(hs)):]
    q75 = float(np.quantile(hs, 0.75))
    mean = float(np.mean(hs))
    prominence = max(0.0, float(np.mean(top)) - q75) / max(mean, 1.0)
    sharpness = float(np.max(top)) / max(mean, 1.0)
    return prominence * math.sqrt(max(sharpness, 0.0))


def detect_orientations(pts: np.ndarray, angle_step: float = 2.0, max_families: int = 12) -> list[float]:
    z = pts[:, 2]
    q10, q90 = np.quantile(z, [0.10, 0.90])
    mid = pts[(z >= q10) & (z <= q90)]
    if len(mid) < 100:
        mid = pts
    xy = mid[:, :2]
    span = max(float(np.ptp(xy[:, 0])), float(np.ptp(xy[:, 1])), 1.0)
    d_bin = max(0.035, min(0.07, span / 1200.0))
    t_cell = max(0.07, min(0.12, span / 450.0))
    scored = []
    for a in np.arange(0.0, 180.0, max(float(angle_step), 0.5)):
        scored.append((float(a), _projected_peak_score(xy, mid[:, 2], float(a), d_bin, t_cell)))
    scored.sort(key=lambda x: x[1], reverse=True)
    selected: list[float] = []
    if not scored or scored[0][1] <= 0:
        return selected
    min_score = 0.08 * float(scored[0][1])
    sep = max(8.0, 4.0 * angle_step)
    for a, score in scored:
        if score < min_score:
            continue
        if all(_circular_distance(a, b) > sep for b in selected):
            selected.append(a)
        if len(selected) >= max_families:
            break
    return sorted(selected)


def _close_1d(mask: np.ndarray, max_gap: int) -> np.ndarray:
    out = mask.copy()
    i = 0
    while i < len(out):
        if out[i]:
            i += 1
            continue
        j = i
        while j < len(out) and not out[j]:
            j += 1
        if i > 0 and j < len(out) and (j-i) <= max_gap:
            out[i:j] = True
        i = j
    return out


def _segments(mask: np.ndarray) -> list[tuple[int,int]]:
    out=[]
    i=0
    while i<len(mask):
        if not mask[i]:
            i+=1; continue
        j=i+1
        while j<len(mask) and mask[j]:
            j+=1
        out.append((i,j))
        i=j
    return out


def _binary_close2d(a: np.ndarray, iterations: int = 1) -> np.ndarray:
    out=a.copy()
    for _ in range(iterations):
        p=np.pad(out,1)
        dil=np.zeros_like(out,dtype=bool)
        for di in range(3):
            for dj in range(3):
                dil |= p[di:di+out.shape[0], dj:dj+out.shape[1]]
        p=np.pad(dil,1,constant_values=False)
        ero=np.ones_like(out,dtype=bool)
        for di in range(3):
            for dj in range(3):
                ero &= p[di:di+out.shape[0], dj:dj+out.shape[1]]
        out=ero
    return out


def _largest_component_fraction(mask: np.ndarray) -> float:
    total=int(mask.sum())
    if total==0:
        return 0.0
    seen=np.zeros_like(mask,dtype=bool)
    best=0
    nx,nz=mask.shape
    for i in range(nx):
        for k in range(nz):
            if not mask[i,k] or seen[i,k]:
                continue
            q=deque([(i,k)]); seen[i,k]=True; count=0
            while q:
                x,y=q.popleft(); count+=1
                for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                    xx,yy=x+dx,y+dy
                    if 0<=xx<nx and 0<=yy<nz and mask[xx,yy] and not seen[xx,yy]:
                        seen[xx,yy]=True; q.append((xx,yy))
            best=max(best,count)
    return float(best/total)


def _plane_occupancy(tt: np.ndarray, zz: np.ndarray, t0: float, t1: float,
                     z0: float, z1: float, cell: float) -> tuple[float,float,float,np.ndarray]:
    if t1<=t0 or z1<=z0 or len(tt)==0:
        return 0.0,0.0,0.0,np.zeros((1,1),dtype=bool)
    cell=max(0.04,float(cell))
    nt=int(np.clip(math.ceil((t1-t0)/cell),3,900))
    nz=int(np.clip(math.ceil((z1-z0)/cell),3,300))
    occ=np.zeros((nt,nz),dtype=bool)
    ti=np.clip(((tt-t0)/(t1-t0)*nt).astype(int),0,nt-1)
    zi=np.clip(((zz-z0)/(z1-z0)*nz).astype(int),0,nz-1)
    occ[ti,zi]=True
    occ2=_binary_close2d(occ,1) if min(nt,nz)>=4 else occ
    area=float(occ2.mean())
    continuity=_largest_component_fraction(occ2)
    cols=occ2.any(axis=1)
    horiz=float(cols.mean())
    return area,continuity,horiz,occ2


def build_resf_candidates(pts: np.ndarray, storey: Storey, orientations: list[float],
                          spacing: float, tolerance: float, raster: float,
                          multi_peak: bool=True) -> list[RESFCandidate]:
    h=storey.z1-storey.z0
    band=pts[(pts[:,2]>=storey.z0+min(0.16,0.06*h)) &
             (pts[:,2]<=storey.z1-min(0.16,0.06*h))]
    if len(band)<100:
        band=pts[(pts[:,2]>=storey.z0)&(pts[:,2]<=storey.z1)]
    if len(band)<100:
        return []
    xy,z=band[:,:2],band[:,2]
    candidates=[]
    cid=1
    for theta in orientations:
        th=math.radians(theta)
        n=np.array([math.cos(th),math.sin(th)])
        tvec=np.array([-math.sin(th),math.cos(th)])
        dn=xy@n
        tt=xy@tvec
        dmin,dmax=float(dn.min()),float(dn.max())
        room_t=float(np.ptp(tt))
        step=max(float(spacing),0.008)
        nb=max(8,int(math.ceil((dmax-dmin)/step)))
        edges=np.linspace(dmin,dmax,nb+1)
        di=np.clip(np.searchsorted(edges,dn,side="right")-1,0,nb-1)
        tcell=max(0.06,min(0.12,raster*2.5))
        tmin=float(tt.min())
        ti=np.floor((tt-tmin)/tcell).astype(np.int64)
        nt=max(int(ti.max())+1,1)
        uniq=np.unique(di.astype(np.int64)*nt+ti)
        db=uniq//nt
        field=np.bincount(db,minlength=nb).astype(float)
        radius=max(1,int(round(tolerance/max(step,1e-6))))
        smooth=_smooth(field,radius)
        if smooth.max(initial=0)<=0:
            continue
        thr=max(0.08*float(smooth.max()), float(np.quantile(smooth,0.70)))
        peaks=[i for i in range(1,nb-1)
               if smooth[i]>=thr and smooth[i]>=smooth[i-1] and smooth[i]>=smooth[i+1]]
        peaks.sort(key=lambda i:smooth[i],reverse=True)
        selected=[]
        min_sep=max(2,int(round(max(0.07,1.7*tolerance)/step)))
        for p in peaks:
            if all(abs(p-q)>=min_sep for q in selected):
                selected.append(p)
            if not multi_peak or len(selected)>=32:
                break
        if not multi_peak and selected:
            selected=selected[:1]

        for p in selected:
            d=0.5*(edges[p]+edges[p+1])
            dist=np.abs(dn-d)
            weight=np.clip(1.0-dist/max(tolerance,1e-6),0.0,1.0)
            m=weight>0
            if m.sum()<35:
                continue
            tloc=tt[m]; zloc=z[m]; wloc=weight[m]
            seg_cell=max(0.08,min(0.16,raster*4.0))
            tlo,thi=map(float,np.quantile(tloc,[0.005,0.995]))
            nseg=max(3,int(math.ceil((thi-tlo)/seg_cell)))
            hh,_=np.histogram(tloc,bins=nseg,range=(tlo,thi),weights=wloc)
            rawh,_=np.histogram(tloc,bins=nseg,range=(tlo,thi))
            positive=hh[hh>0]
            active=hh>=max(0.5,0.20*float(np.quantile(positive,0.45)) if positive.size else 0.5)
            active &= rawh>=1
            active=_close_1d(active,max(1,int(round(0.24/max((thi-tlo)/nseg,1e-6)))))
            sedges=np.linspace(tlo,thi,nseg+1)
            for a,b in _segments(active):
                s0,s1=float(sedges[a]),float(sedges[b])
                if s1-s0<0.45:
                    continue
                sm=m & (tt>=s0) & (tt<=s1)
                if sm.sum()<35:
                    continue
                zz=z[sm]; st=tt[sm]
                obs_z0,obs_z1=map(float,np.quantile(zz,[0.01,0.99]))
                obs_z0=max(obs_z0,storey.z0)
                obs_z1=min(obs_z1,storey.z1)
                if obs_z1-obs_z0<min(1.2,0.48*h):
                    continue
                A,C,hcont,occ=_plane_occupancy(st,zz,s0,s1,storey.z0,storey.z1,max(0.05,raster))
                EH=float(np.clip((s1-s0)/max(room_t,0.2),0,1))
                EV=float(np.clip((obs_z1-obs_z0)/max(h,0.2),0,1))
                residual=dn[sm]-d
                sig=float(np.sqrt(np.average(residual**2,weights=np.maximum(weight[sm],1e-6))))
                N=float(np.exp(-(sig**2)/(2*max(tolerance*0.70,0.012)**2)))
                rho=float(np.clip((max(A,1e-8)*max(EH,1e-8)*max(EV,1e-8)*max(N,1e-8))**0.25,0,1))
                TF=float(math.exp(-((obs_z0-storey.z0)**2)/(2*0.18**2)))
                TC=float(math.exp(-((obs_z1-storey.z1)**2)/(2*0.22**2)))
                sem=float(np.clip(0.36*EV+0.24*N+0.22*C+0.18*max(TF,TC),0,1))
                missing=1.0-A
                O=float(np.clip(missing*C*max(TF,TC),0,1))
                candidates.append(RESFCandidate(
                    cid,storey.id,float(theta),float(d),s0,s1,storey.z0,storey.z1,
                    float(A),EH,EV,float(C),N,O,rho,sem,TF,TC
                ))
                cid+=1
    return candidates


def _cand_line(c: RESFCandidate) -> LineString:
    th=math.radians(c.theta_deg)
    n=np.array([math.cos(th),math.sin(th)])
    t=np.array([-math.sin(th),math.cos(th)])
    p1=n*c.d+t*c.t0
    p2=n*c.d+t*c.t1
    return LineString([tuple(p1),tuple(p2)])


def enrich_topology(cands: list[RESFCandidate]) -> None:
    lines=[_cand_line(c) for c in cands]
    for i,c in enumerate(cands):
        cp=0.0
        junctions=0
        for j,o in enumerate(cands):
            if i==j or o.storey_id!=c.storey_id:
                continue
            ad=_circular_distance(c.theta_deg,o.theta_deg)
            if ad<=3.0:
                dd=abs(c.d-o.d)
                compat=math.exp(-(dd**2)/(2*0.10**2))*math.cos(math.radians(ad))
                dist=lines[i].distance(lines[j])
                if dist<=1.2:
                    cp=max(cp,compat*math.exp(-(dist**2)/(2*0.55**2)))
            elif ad>=18.0:
                if lines[i].distance(lines[j])<=0.28:
                    junctions+=1
        c.T_CP=float(np.clip(cp,0,1))
        c.T_W=float(np.clip(junctions/2.0,0,1))
        c.T_B=float(np.clip(0.35*c.T_F+0.25*c.T_C+0.20*min(1,c.E_H/0.18)+0.20*c.T_W,0,1))
        c.T_R=float(np.clip(0.55*c.T_W+0.45*c.T_CP,0,1))


def fuzzy_decision(c: RESFCandidate, threshold: float, fuzzy: bool=True,
                   topology: bool=True, occlusion: bool=True) -> tuple[str,float]:
    resf=float(np.clip((c.A_star*c.E_H*c.E_V*c.N+1e-12)**0.25,0,1))
    top=float(np.clip(max(c.T_B,c.T_R,c.T_W,c.T_CP),0,1)) if topology else 0.5
    occ=c.O if occlusion else 0.0
    if c.E_V < 0.52 and c.T_C < 0.20 and c.T_CP < 0.55 and c.T_R < 0.55:
        return "Clutter", float(np.clip(0.62 + 0.25*(0.52-c.E_V) + 0.13*(1.0-c.T_B), 0, 1))
    if not fuzzy:
        score=float(np.clip(0.26*c.semantic_wall+0.28*resf+0.18*c.E_V+0.16*top+0.12*c.C,0,1))
        if score>=threshold and c.E_V>=0.45:
            return "Permanent",score
        if occ>=0.45 and (c.T_CP>=0.45 or c.T_R>=0.45):
            return "OccludedPermanent",max(score,0.50)
        if score>=0.32:
            return "Uncertain",score
        return "Clutter",1.0-score

    low=lambda x:_trap(x,0.0,0.0,0.22,0.45)
    med=lambda x:_tri(x,0.20,0.50,0.78)
    high=lambda x:_trap(x,0.48,0.68,1.0,1.0)
    vhigh=lambda x:_trap(x,0.66,0.82,1.0,1.0)

    wall=c.semantic_wall
    floorceil=max(c.T_F,c.T_C)
    boundary=c.T_B if topology else 0.5
    junction=c.T_W if topology else 0.5
    cp=c.T_CP if topology else 0.5
    closure=c.T_R if topology else 0.5

    perm=[
        min(high(wall),high(resf),high(boundary)),
        min(med(wall),vhigh(resf),high(floorceil)),
        min(high(wall),high(resf),high(junction)),
    ]
    occperm=[
        min(high(wall),med(resf),high(cp),high(occ)),
        min(high(closure),vhigh(cp),high(occ)),
        min(low(resf),vhigh(occ),high(boundary),high(junction)),
    ]
    clutter=[
        min(low(boundary),max(low(resf),med(resf))),
        min(high(resf),low(c.T_C),low(boundary)),
        min(vhigh(resf),low(wall),low(boundary)),
    ]
    uncertain=[
        min(med(wall),med(resf),med(top)),
        min(low(wall),low(resf),low(top)),
    ]
    p=max(perm+[0.0])
    op=max(occperm+[0.0])
    cl=max(clutter+[0.0])
    un=max(uncertain+[0.0])
    p=max(p,0.42*resf+0.24*wall+0.18*floorceil+0.16*boundary)
    op=max(op,0.40*occ+0.30*cp+0.20*closure+0.10*wall)
    cl=max(cl,0.55*(1.0-wall)+0.25*(1.0-floorceil)+0.20*(1.0-boundary))
    un=max(un,0.55*(1.0-max(p,op,cl)))
    vals={"Permanent":p,"OccludedPermanent":op,"Clutter":cl,"Uncertain":un}
    state=max(vals,key=vals.get)
    conf=float(np.clip(vals[state],0,1))
    if state=="Permanent" and conf<threshold:
        return "Uncertain",conf
    if state=="OccludedPermanent" and conf<max(0.38,threshold-0.10):
        return "Uncertain",conf
    return state,conf


def _candidate_overlap(a: RESFCandidate,b: RESFCandidate) -> float:
    if _circular_distance(a.theta_deg,b.theta_deg)>3.0:
        return 0.0
    inter=max(0.0,min(a.t1,b.t1)-max(a.t0,b.t0))
    return inter/max(1e-6,min(a.t1-a.t0,b.t1-b.t0))


def consolidate_candidates(cands: list[RESFCandidate], enabled: bool=True) -> list[RESFCandidate]:
    if not enabled:
        return list(cands)
    groups: list[list[RESFCandidate]]=[]
    for c in sorted(cands,key=lambda x:x.confidence,reverse=True):
        placed=False
        for g in groups:
            r=g[0]
            if c.storey_id!=r.storey_id or _circular_distance(c.theta_deg,r.theta_deg)>2.5:
                continue
            if abs(c.d-r.d)>0.10:
                continue
            gap=max(0.0,max(c.t0,r.t0)-min(c.t1,r.t1))
            if _candidate_overlap(c,r)>0.20 or gap<=0.35:
                g.append(c); placed=True; break
        if not placed:
            groups.append([c])
    merged=[]
    for g in groups:
        r=max(g,key=lambda x:x.confidence)
        weights=np.array([max(x.confidence,0.05) for x in g],dtype=float)
        d=float(np.average([x.d for x in g],weights=weights))
        t0=min(x.t0 for x in g); t1=max(x.t1 for x in g)
        def wavg(attr):
            return float(np.average([getattr(x,attr) for x in g],weights=weights))
        m=RESFCandidate(
            0,r.storey_id,r.theta_deg,d,t0,t1,min(x.z0 for x in g),max(x.z1 for x in g),
            wavg("A_star"),wavg("E_H"),wavg("E_V"),max(x.C for x in g),wavg("N"),
            max(x.O for x in g),max(x.rho for x in g),wavg("semantic_wall"),
            max(x.T_F for x in g),max(x.T_C for x in g),max(x.T_W for x in g),
            max(x.T_B for x in g),max(x.T_R for x in g),max(x.T_CP for x in g),
            min(x.G for x in g),max(x.confidence for x in g),
            max((x.state for x in g),key=lambda s:{"Permanent":3,"OccludedPermanent":2,"Uncertain":1,"Clutter":0}[s])
        )
        merged.append(m)
    for i,c in enumerate(merged,1): c.id=i
    return merged


def _overlap_interval(a0,a1,b0,b1):
    inter=max(0.0,min(a1,b1)-max(a0,b0))
    return inter/max(1e-9,min(a1-a0,b1-b0))


def candidates_to_walls(cands: list[RESFCandidate], storey: Storey) -> list[Wall]:
    accepted=[c for c in cands if c.state in {"Permanent","OccludedPermanent"} and c.confidence>=0.32]
    used=set(); walls=[]
    order=sorted(range(len(accepted)),key=lambda i:accepted[i].confidence,reverse=True)
    for i in order:
        if i in used: continue
        a=accepted[i]
        best=None; bestscore=-1
        for j,b in enumerate(accepted):
            if j==i or j in used: continue
            if _circular_distance(a.theta_deg,b.theta_deg)>2.5: continue
            sep=abs(a.d-b.d)
            if not 0.07<=sep<=0.48: continue
            ov=_overlap_interval(a.t0,a.t1,b.t0,b.t1)
            if ov<0.55: continue
            score=0.55*ov+0.25*min(a.N,b.N)+0.20*min(a.confidence,b.confidence)
            if score>bestscore:
                best=j; bestscore=score
        if best is not None:
            b=accepted[best]; used.update((i,best))
            d=0.5*(a.d+b.d); thick=abs(a.d-b.d)
            t0=min(a.t0,b.t0) if abs(a.t0-b.t0)<=0.55 else max(a.t0,b.t0)
            t1=max(a.t1,b.t1) if abs(a.t1-b.t1)<=0.55 else min(a.t1,b.t1)
            if t1-t0<0.45:
                t0,t1=max(a.t0,b.t0),min(a.t1,b.t1)
            z0,z1=storey.z0,storey.z1
            conf=float(np.clip(0.55*max(a.confidence,b.confidence)+0.45*bestscore,0,1))
            src="paired_faces"
            state="Permanent" if "Permanent" in (a.state,b.state) else "OccludedPermanent"
            metrics=[0.5*(getattr(a,k)+getattr(b,k)) for k in ("A_star","E_H","E_V","C","N","O")]
        else:
            used.add(i)
            d=a.d; thick=0.20; t0,t1=a.t0,a.t1; z0,z1=storey.z0,storey.z1
            conf=0.82*a.confidence; src="single_face_inferred_thickness"; state=a.state
            metrics=[getattr(a,k) for k in ("A_star","E_H","E_V","C","N","O")]
        if t1-t0<0.45: continue
        th=math.radians(a.theta_deg)
        n=np.array([math.cos(th),math.sin(th)])
        tv=np.array([-math.sin(th),math.cos(th)])
        p1=n*d+tv*t0; p2=n*d+tv*t1
        walls.append(Wall(0,storey.id,float(p1[0]),float(p1[1]),float(p2[0]),float(p2[1]),
                          float(z0),float(z1),float(np.clip(thick,0.09,0.45)),a.theta_deg,
                          conf,float(metrics[0]),float(metrics[3]),src,state,
                          float(metrics[0]),float(metrics[1]),float(metrics[2]),float(metrics[3]),
                          float(metrics[4]),float(metrics[5]),1.0))
    return deduplicate_walls(walls)


def deduplicate_walls(walls: list[Wall]) -> list[Wall]:
    kept=[]
    for w in sorted(walls,key=lambda x:(x.confidence,math.hypot(x.x2-x.x1,x.y2-x.y1)),reverse=True):
        line=LineString([(w.x1,w.y1),(w.x2,w.y2)])
        duplicate=False
        for k in kept:
            if w.storey_id!=k.storey_id or _circular_distance(w.theta_deg,k.theta_deg)>3.0:
                continue
            kl=LineString([(k.x1,k.y1),(k.x2,k.y2)])
            if line.distance(kl)>max(0.12,0.45*max(w.thickness,k.thickness)):
                continue
            dx,dy=w.x2-w.x1,w.y2-w.y1
            L=max(math.hypot(dx,dy),1e-6)
            u=np.array([dx/L,dy/L]); origin=np.array([w.x1,w.y1])
            a=[0.0,L]
            b=sorted([float(np.dot(np.array([k.x1,k.y1])-origin,u)),
                      float(np.dot(np.array([k.x2,k.y2])-origin,u))])
            ov=max(0.0,min(a[1],b[1])-max(a[0],b[0]))/max(1e-6,min(L,kl.length))
            if ov>0.55:
                duplicate=True; break
        if not duplicate:
            kept.append(w)
    for i,w in enumerate(kept,1): w.id=i
    return kept


def snap_wall_graph(walls: list[Wall], snap: float=0.28) -> list[Wall]:
    if len(walls)<2: return walls
    lines=[]
    for w in walls:
        p1=np.array([w.x1,w.y1]); p2=np.array([w.x2,w.y2]); L=np.linalg.norm(p2-p1)
        if L<1e-9: lines.append(LineString([p1,p2])); continue
        u=(p2-p1)/L
        lines.append(LineString([p1-u*snap,p2+u*snap]))
    inters=[[] for _ in walls]
    for i in range(len(walls)):
        for j in range(i+1,len(walls)):
            if _circular_distance(walls[i].theta_deg,walls[j].theta_deg)<12: continue
            g=lines[i].intersection(lines[j])
            if g.geom_type=="Point":
                q=np.array([g.x,g.y])
                inters[i].append(q); inters[j].append(q)
    for i,w in enumerate(walls):
        for which,p in [(0,np.array([w.x1,w.y1])),(1,np.array([w.x2,w.y2]))]:
            if not inters[i]: continue
            ds=np.array([np.linalg.norm(q-p) for q in inters[i]])
            k=int(np.argmin(ds))
            if ds[k]<=snap:
                if which==0: w.x1,w.y1=map(float,inters[i][k])
                else: w.x2,w.y2=map(float,inters[i][k])
    return deduplicate_walls(walls)


def geometric_feedback(pts: np.ndarray, walls: list[Wall], sigma: float=0.08) -> None:
    if not walls: return
    xy=pts[:,:2]; z=pts[:,2]
    for w in walls:
        p1=np.array([w.x1,w.y1]); p2=np.array([w.x2,w.y2]); v=p2-p1
        L=float(np.linalg.norm(v))
        if L<1e-6: continue
        t=v/L; n=np.array([-t[1],t[0]])
        rel=xy-p1
        along=rel@t; normal=np.abs(rel@n)
        near=(along>=-0.10)&(along<=L+0.10)&(z>=w.z0-0.08)&(z<=w.z1+0.08)&(normal<=max(0.30,w.thickness))
        if near.sum()<20:
            w.geometric_feedback=0.0
            w.confidence*=0.75
            continue
        d50=float(np.median(np.maximum(0.0,normal[near]-0.5*w.thickness)))
        G=float(math.exp(-(d50*d50)/(2*sigma*sigma)))
        w.geometric_feedback=G
        w.confidence=float(np.clip(0.78*w.confidence+0.22*G,0,1))


def spaces_from_walls(walls: list[Wall], storey: Storey) -> list[Space]:
    lines=[LineString([(w.x1,w.y1),(w.x2,w.y2)]) for w in walls if w.storey_id==storey.id]
    if len(lines)<3: return []
    noded=unary_union(lines)
    polys=list(polygonize(noded))
    spaces=[]
    for p in polys:
        if not isinstance(p,Polygon): continue
        area=float(p.area)
        if not (1.2<=area<=5000): continue
        compact=4*math.pi*area/max(p.length*p.length,1e-9)
        if compact<0.015: continue
        coords=[[float(x),float(y)] for x,y in list(p.exterior.coords)[:-1]]
        if len(coords)<3: continue
        conf=float(np.clip(0.55+0.35*min(1.0,compact/0.35)+0.10*min(1.0,area/10),0,0.96))
        spaces.append(Space(0,storey.id,"",coords,storey.z0,storey.z1,area,conf))
    spaces.sort(key=lambda s:s.area,reverse=True)
    for i,s in enumerate(spaces,1):
        s.id=i; s.name=f"Space_{storey.id}_{i}"
    return spaces


def _horizontal_footprint(pts: np.ndarray, zlevel: float, tol: float=0.10) -> Polygon|None:
    band=pts[np.abs(pts[:,2]-zlevel)<=tol]
    if len(band)<30: return None
    xy=band[:,:2]
    if len(xy)>20000:
        idx=np.linspace(0,len(xy)-1,20000,dtype=int); xy=xy[idx]
    mp=MultiPoint([tuple(p) for p in xy])
    if concave_hull is not None:
        try:
            g=concave_hull(mp,ratio=0.12,allow_holes=True)
            if g.geom_type=="MultiPolygon":
                g=max(g.geoms,key=lambda q:q.area)
            if g.geom_type=="Polygon" and g.area>1.0:
                return g
        except Exception:
            pass
    g=mp.convex_hull
    return g if g.geom_type=="Polygon" and g.area>1.0 else None


def slabs_from_evidence(pts: np.ndarray, spaces: list[Space], storey: Storey) -> list[Slab]:
    sp=[Polygon(s.polygon) for s in spaces if s.storey_id==storey.id]
    room_union=unary_union(sp) if sp else None
    floor=_horizontal_footprint(pts,storey.z0,0.12)
    ceil=_horizontal_footprint(pts,storey.z1,0.14)
    def choose(obs):
        g=obs
        if room_union is not None and not room_union.is_empty:
            room=room_union.buffer(0.15)
            if g is None:
                g=room
            else:
                inter=g.intersection(room.buffer(0.40))
                g=inter if not inter.is_empty else room
        if g is None or g.is_empty: return None
        if g.geom_type=="MultiPolygon":
            g=max(g.geoms,key=lambda q:q.area)
        return g if g.geom_type=="Polygon" and g.area>=1.0 else None
    fg,cg=choose(floor),choose(ceil)
    out=[]
    if fg is not None:
        out.append(Slab(0,storey.id,"FLOOR",[[float(x),float(y)] for x,y in list(fg.exterior.coords)[:-1]],
                        storey.z0,0.18,0.82))
    if cg is not None:
        out.append(Slab(0,storey.id,"CEILING",[[float(x),float(y)] for x,y in list(cg.exterior.coords)[:-1]],
                        storey.z1,0.12,0.76))
    return out


def _runs(mask: np.ndarray) -> list[tuple[int,int]]:
    return _segments(mask)


def detect_openings(pts: np.ndarray, walls: list[Wall], raster: float) -> list[Opening]:
    """Object-specific door/window reconstruction with host-wall frame evidence."""
    xy,z=pts[:,:2],pts[:,2]
    out=[]; oid=1
    cell=float(np.clip(max(0.055,raster*2.5),0.055,0.095))
    for w in walls:
        p1=np.array([w.x1,w.y1]); p2=np.array([w.x2,w.y2]); v=p2-p1
        L=float(np.linalg.norm(v)); H=float(w.z1-w.z0)
        if L<1.0 or H<1.8: continue
        t=v/L; n=np.array([-t[1],t[0]])
        rel=xy-p1
        u=rel@t; dn=np.abs(rel@n)
        near=(u>=0)&(u<=L)&(dn<=max(0.13,0.55*w.thickness+0.07))&(z>=w.z0-0.03)&(z<=w.z1+0.03)
        if near.sum()<100: continue
        uu=u[near]; zz=z[near]-w.z0
        nx=int(np.clip(math.ceil(L/cell),12,650))
        nz=int(np.clip(math.ceil(H/cell),20,260))
        counts=np.zeros((nx,nz),dtype=np.uint16)
        xi=np.clip((uu/L*nx).astype(int),0,nx-1)
        zi=np.clip((zz/H*nz).astype(int),0,nz-1)
        np.add.at(counts,(xi,zi),1)
        if counts.max(initial=0)==0: continue
        occ=counts>0
        occ=_binary_close2d(occ,1)

        dz=H/nz; du=L/nx
        zlow=max(1,int(0.22/dz))
        zhigh=min(nz-2,int(min(2.15,H-0.18)/dz))
        if zhigh>zlow+3:
            profile=occ[:,zlow:zhigh].mean(axis=1)
            positive=profile[profile>0]
            ref=float(np.quantile(positive,0.60)) if positive.size else 0.0
            low=profile<=max(0.10,0.30*ref) if ref>0 else np.zeros(nx,dtype=bool)
            margin=max(2,int(0.25/du))
            low[:margin]=False; low[-margin:]=False
            low=_close_1d(low,max(1,int(0.10/du)))
            for i0,i1 in _runs(low):
                width=(i1-i0)*du
                if not 0.60<=width<=2.20: continue
                flank=max(1,int(0.18/du))
                left=occ[max(0,i0-flank):i0,zlow:zhigh].mean() if i0>0 else 0
                right=occ[i1:min(nx,i1+flank),zlow:zhigh].mean() if i1<nx else 0
                head=None
                min_head=int(1.60/dz); max_head=min(nz-2,int(min(2.65,H-0.05)/dz))
                for k in range(max(min_head,1),max_head):
                    slab=occ[i0:i1,k:min(nz,k+max(1,int(0.15/dz)))]
                    if slab.size and slab.mean()>=0.22:
                        head=k*dz; break
                if head is None: continue
                jamb=min(left,right)
                if jamb<0.16: continue
                vacancy=float(1.0-profile[i0:i1].mean()/max(ref,1e-6))
                conf=float(np.clip(0.38*vacancy+0.34*min(1.0,jamb/0.35)+0.18*w.confidence+0.10*w.resf_C,0,1))
                if conf<0.44: continue
                center=0.5*(i0+i1)*du
                out.append(Opening(oid,w.id,w.storey_id,"Door",float(center),float(width),0.0,
                                   float(np.clip(head,1.65,min(2.60,H*0.96))),conf,"jamb_lintel"))
                oid+=1

        empty=~occ
        valid=np.zeros_like(empty,dtype=bool)
        k0=max(1,int(0.35/dz)); k1=min(nz-1,int(min(2.45,H-0.15)/dz))
        valid[1:-1,k0:k1]=True
        holes=empty&valid
        seen=np.zeros_like(holes,dtype=bool)
        for i in range(1,nx-1):
            for k in range(k0,k1):
                if not holes[i,k] or seen[i,k]: continue
                q=deque([(i,k)]); seen[i,k]=True; comp=[]
                while q:
                    x,y=q.popleft(); comp.append((x,y))
                    for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                        xx,yy=x+dx,y+dy
                        if 0<=xx<nx and 0<=yy<nz and holes[xx,yy] and not seen[xx,yy]:
                            seen[xx,yy]=True; q.append((xx,yy))
                xs=np.array([c[0] for c in comp]); zs=np.array([c[1] for c in comp])
                i0,i1=int(xs.min()),int(xs.max())+1
                j0,j1=int(zs.min()),int(zs.max())+1
                width=(i1-i0)*du; height=(j1-j0)*dz; sill=j0*dz
                if not (0.45<=width<=3.2 and 0.45<=height<=1.9 and 0.45<=sill<=1.65):
                    continue
                pad_u=max(1,int(0.15/du)); pad_z=max(1,int(0.12/dz))
                left=occ[max(0,i0-pad_u):i0,j0:j1].mean() if i0>0 else 0
                right=occ[i1:min(nx,i1+pad_u),j0:j1].mean() if i1<nx else 0
                bottom=occ[i0:i1,max(0,j0-pad_z):j0].mean() if j0>0 else 0
                top=occ[i0:i1,j1:min(nz,j1+pad_z)].mean() if j1<nz else 0
                frame=float((left+right+bottom+top)/4)
                fill=float(occ[i0:i1,j0:j1].mean())
                rectangularity=len(comp)/max(1,(i1-i0)*(j1-j0))
                if frame<0.16 or rectangularity<0.55: continue
                conf=float(np.clip(0.40*frame+0.28*(1-fill)+0.20*w.confidence+0.12*rectangularity,0,1))
                if conf<0.43: continue
                center=0.5*(i0+i1)*du
                if any(o.wall_id==w.id and abs(o.offset-center)<0.5*(o.width+width) for o in out if o.kind=="Door"):
                    continue
                if any(o.wall_id==w.id and o.kind=="Window" and abs(o.offset-center)<0.35 for o in out):
                    continue
                out.append(Opening(oid,w.id,w.storey_id,"Window",float(center),float(width),
                                   float(sill),float(height),conf,"four_sided_frame"))
                oid+=1
    return out


def _apply_candidate_feedback(pts: np.ndarray, candidates: list[RESFCandidate], sigma: float=0.08) -> None:
    xy=pts[:,:2]; z=pts[:,2]
    for c in candidates:
        th=math.radians(c.theta_deg)
        n=np.array([math.cos(th),math.sin(th)])
        tv=np.array([-math.sin(th),math.cos(th)])
        dn=xy@n; tt=xy@tv
        m=(tt>=c.t0)&(tt<=c.t1)&(z>=c.z0)&(z<=c.z1)&(np.abs(dn-c.d)<=0.35)
        if m.sum()<20:
            c.G=0.0; continue
        d50=float(np.median(np.abs(dn[m]-c.d)))
        c.G=float(math.exp(-(d50*d50)/(2*sigma*sigma)))
        c.confidence=float(np.clip(0.82*c.confidence+0.18*c.G,0,1))


def reconstruct_bim(request: dict) -> BIMModel:
    raw=load_xyz(request["input"],int(request.get("max_points",2_000_000)))
    if len(raw)<100:
        raise ValueError("Point cloud contains too few valid points for BIM reconstruction")

    raster=float(request.get("raster_cell_m",0.05))
    voxel=max(0.025,min(0.05,max(raster,0.02)))
    pts=voxel_subsample(raw,voxel)
    storeys=detect_storeys(pts)
    all_walls=[]; all_spaces=[]; all_slabs=[]; all_candidates=[]
    threshold=float(request.get("confidence_threshold",0.50))
    multi_peak=bool(request.get("multi_peak",True))
    topology=bool(request.get("topology",True))
    occlusion=bool(request.get("occlusion",True))
    fuzzy=bool(request.get("fuzzy",True))
    instance=bool(request.get("instance_consolidation",True))
    feedback=bool(request.get("geometric_feedback",True))

    for s in storeys:
        band=pts[(pts[:,2]>=s.z0-0.10)&(pts[:,2]<=s.z1+0.10)]
        if len(band)<100: continue
        orientations=detect_orientations(
            band,
            float(request.get("angle_step_deg",2.0)),
            int(request.get("max_orientation_families",12))
        )
        cands=build_resf_candidates(
            band,s,orientations,
            max(0.008,float(request.get("plane_spacing_m",0.01))),
            max(0.02,float(request.get("plane_tolerance_m",0.03))),
            max(0.04,raster),
            multi_peak
        )
        enrich_topology(cands)
        for c in cands:
            c.state,c.confidence=fuzzy_decision(c,threshold,fuzzy,topology,occlusion)
        cands=consolidate_candidates(cands,instance)
        enrich_topology(cands)
        for c in cands:
            c.state,c.confidence=fuzzy_decision(c,threshold,fuzzy,topology,occlusion)
        if feedback:
            _apply_candidate_feedback(band,cands)
        walls=candidates_to_walls(cands,s)
        walls=snap_wall_graph(walls,0.30 if topology else 0.18)
        if feedback:
            geometric_feedback(band,walls)
        filtered=[]
        for w in walls:
            L=math.hypot(w.x2-w.x1,w.y2-w.y1)
            if L<0.60: continue
            if w.confidence<0.32: continue
            filtered.append(w)
        walls=filtered
        base=len(all_walls)
        for i,w in enumerate(walls,1): w.id=base+i
        spaces=spaces_from_walls(walls,s) if topology else []
        slabs=slabs_from_evidence(band,spaces,s)
        all_walls.extend(walls)
        all_spaces.extend(spaces)
        all_slabs.extend(slabs)
        all_candidates.extend(cands)

    for i,x in enumerate(all_spaces,1): x.id=i
    for i,x in enumerate(all_slabs,1): x.id=i

    openings=detect_openings(pts,all_walls,max(0.04,raster))
    for i,o in enumerate(openings,1): o.id=i

    warnings=[]
    if not all_walls:
        warnings.append("No accepted BIM wall instances were reconstructed")
    if not all_spaces:
        warnings.append("No closed IfcSpace polygons reconstructed; wall graph may be incomplete")
    if not openings:
        warnings.append("No host-wall door/window objects passed frame-evidence checks")
    if not any(x.kind=="FLOOR" for x in all_slabs):
        warnings.append("No floor slab footprint reconstructed")

    summary={
        "status":"success" if all_walls else "warning",
        "method":"Fuzzy-RESF-BIM",
        "version":"3.0-semantic-topological",
        "input_points":int(len(raw)),
        "regularized_points":int(len(pts)),
        "storeys":len(storeys),
        "resf_candidates":len(all_candidates),
        "accepted_candidates":sum(c.state in {"Permanent","OccludedPermanent"} for c in all_candidates),
        "walls":len(all_walls),
        "doors":sum(o.kind=="Door" for o in openings),
        "windows":sum(o.kind=="Window" for o in openings),
        "openings":len(openings),
        "spaces":len(all_spaces),
        "slabs":len(all_slabs),
        "occlusion_evidence":"proxy_without_scanner_poses",
        "semantic_evidence":"geometry_prior_without_pointwise_class_probabilities",
        "warnings":warnings,
    }
    return BIMModel(storeys,all_walls,openings,all_spaces,all_slabs,all_candidates,summary)


def write_csv(items: Iterable, path: str | Path, empty_fields: list[str]) -> None:
    items=list(items)
    fields=list(asdict(items[0]).keys()) if items else empty_fields
    with open(path,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        for item in items:
            row=asdict(item)
            for k,v in list(row.items()):
                if isinstance(v,(list,dict)):
                    row[k]=json.dumps(v,separators=(",",":"))
            w.writerow(row)


def write_model_json(model: BIMModel, path: str | Path) -> None:
    payload={
        "summary":model.summary,
        "storeys":[asdict(x) for x in model.storeys],
        "walls":[asdict(x) for x in model.walls],
        "openings":[asdict(x) for x in model.openings],
        "spaces":[asdict(x) for x in model.spaces],
        "slabs":[asdict(x) for x in model.slabs],
        "resf_candidates":[asdict(x) for x in model.candidates],
    }
    Path(path).write_text(json.dumps(payload,indent=2),encoding="utf-8")
