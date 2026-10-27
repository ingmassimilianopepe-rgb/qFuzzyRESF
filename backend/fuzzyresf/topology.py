"""Bounded geometric regularisation. All inferred edits are recorded, not evidence.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import copy
import numpy as np
from shapely.geometry import Polygon, LineString, Point, MultiPoint, mapping, shape
from shapely.ops import unary_union, polygonize, split
from shapely.affinity import affine_transform
from shapely import set_precision, make_valid


def stable(g):
    """Millimetre precision for overlays; collapse zero-width numerical slivers."""
    return unary_union(polygons(set_precision(make_valid(g), .001)))


def polygons(g):
    if g.is_empty:
        return []
    return [g] if g.geom_type == 'Polygon' else [p for p in getattr(g, 'geoms', []) if p.geom_type == 'Polygon']


def clean_polygon(g, tolerance=.20):
    """Remove grid teeth at the declared scale, preserving large concavities/holes."""
    results = []
    for p in polygons(g):
        smooth = p.buffer(-tolerance / 2, join_style=2).buffer(tolerance / 2, join_style=2)
        if smooth.is_empty or smooth.area < .8 * p.area:
            smooth = p
        smooth = smooth.buffer(tolerance / 2, join_style=2).buffer(-tolerance / 2, join_style=2)
        smooth = smooth.simplify(tolerance, preserve_topology=True)
        hull = smooth.convex_hull
        if hull.area <= 1.08 * smooth.area and hull.hausdorff_distance(smooth) <= tolerance:
            smooth = hull
        for q in polygons(smooth):
            if q.is_valid and q.area > .05:
                results.append(q)
    return stable(unary_union(results))


def plane_xy(s):
    p = shape(s['polygon'])
    o, u, v = [np.asarray(s[k]) for k in ('plane_origin', 'basis_u', 'basis_v')]
    return affine_transform(p, [u[0], v[0], u[1], v[1], o[0], o[1]])


def roof_z(s, xy):
    n = np.asarray(s['normal']); o = np.asarray(s['plane_origin'])
    xy = np.asarray(xy)
    return (n @ o - xy @ n[:2]) / n[2]


def roof_profile(s, footprint):
    o, u, v = [np.asarray(s[k]) for k in ('plane_origin', 'basis_u', 'basis_v')]
    def ring(coords):
        xy = np.array(coords)
        q = np.c_[xy, roof_z(s, xy)] - o
        return np.c_[q @ u, q @ v]
    return Polygon(ring(footprint.exterior.coords), [ring(h.coords) for h in footprint.interiors])


def _line_join(walls, tolerance=.50):
    """Intersect nearby line endpoints; no extension beyond the tolerance."""
    joins = []
    original = [(np.array(w['a']), np.array(w['b'])) for w in walls]
    for i, w in enumerate(walls):
        a, b = original[i]; d = (b-a) / np.linalg.norm(b-a)
        for key, end in [('a', a), ('b', b)]:
            candidates = []
            for j, q in enumerate(walls):
                if i == j or min(w['top'], q['top']) - max(w['base'], q['base']) < .50:
                    continue
                c, e = original[j]; f = (e-c) / np.linalg.norm(e-c)
                M = np.column_stack([d, -f])
                if abs(np.linalg.det(M)) < .10:
                    continue
                t, u = np.linalg.solve(M, c-a)
                hit = a+t*d
                if -.50 <= u <= np.linalg.norm(e-c)+.50 and np.linalg.norm(hit-end) <= tolerance:
                    candidates.append((np.linalg.norm(hit-end), j, hit))
            if candidates:
                distance, j, hit = min(candidates, key=lambda item: (item[0], item[1]))
                w[key] = hit.tolist()
                joins.append({'wall': w['id'], 'end': key, 'to': walls[j]['id'], 'movement_m': float(distance)})
    return joins


def regularize_structure(I, P, N, PL):
    """Wall graph, paired faces, shared roof ridges and bounded roof clipping."""
    I = copy.deepcopy(I)
    roof_sources = sorted([s for s in I['surfaces'] if s['kind'] == 'roof'], key=lambda s: -s['area_m2'])
    roofs = []
    rejected = []
    # Roof candidates need nearby wall-top support, preventing sloping terrain
    # outside the building from being classified as a roof simply because it is
    # the highest point in its XY column.
    vertical = P[(abs(N[:, 2]) < .20) & (PL > .30)]
    keys, inverse = np.unique(np.floor(vertical[:, :2] / .30).astype(int), axis=0, return_inverse=True)
    wall_tops = np.full(len(keys), -np.inf)
    np.maximum.at(wall_tops, inverse, vertical[:, 2])
    wall_xy = (keys + .5) * .30
    for s in roof_sources:
        fp = clean_polygon(plane_xy(s), .22)
        if fp.is_empty:
            continue
        from shapely import contains_xy
        nearby = contains_xy(fp.buffer(.50), wall_xy[:, 0], wall_xy[:, 1])
        agreement = nearby & (abs(roof_z(s, wall_xy) - wall_tops) < .75)
        if agreement.sum() < 8:
            rejected.append({'id': s['id'], 'reason': 'Roof lacks nearby wall-top support; possible ground or furniture'})
            continue
        # Opposite observations of one roof must not become two overlapping roofs.
        paired = False
        for r in roofs:
            n = np.array(r['normal']); offset = abs(n @ (np.array(s['plane_origin'])-r['plane_origin']))
            overlap = fp.intersection(shape(r['footprint'])).area / max(.001, min(fp.area, shape(r['footprint']).area))
            if abs(n @ s['normal']) > np.cos(np.radians(4)) and offset < .30 and overlap > .65:
                r.setdefault('merged_observations', []).append(s['id'])
                if offset > .05:
                    r['thickness_m'] = offset
                    r['thickness_status'] = 'PAIRED PARALLEL ROOF FACES; requires review'
                if n @ s['plane_origin'] > n @ r['plane_origin']:
                    r['plane_origin'] = s['plane_origin']
                paired = True
                break
        if paired:
            continue
        for part in polygons(fp):
            r = copy.deepcopy(s)
            r['footprint'] = mapping(part)
            r['extrusion'] = 'VERTICAL_DOWN_FROM_OBSERVED_TOP'
            roofs.append(r)
    # Shared ridge/valley line where two observed boundaries already approach it.
    ridge_joins = []
    for i, r in enumerate(roofs):
        for j, q in enumerate(roofs[i+1:], i+1):
            a, b = shape(r['footprint']), shape(q['footprint'])
            n, m = np.array(r['normal']), np.array(q['normal'])
            direction = n[:2]/n[2] - m[:2]/m[2]
            if np.linalg.norm(direction) < .08 or a.distance(b) > .35:
                continue
            c = np.dot(n,r['plane_origin'])/n[2] - np.dot(m,q['plane_origin'])/m[2]
            v = direction/np.linalg.norm(direction); c /= np.linalg.norm(direction)
            pa, pb = np.array(a.centroid.coords[0]), np.array(b.centroid.coords[0])
            if (pa@v-c)*(pb@v-c) >= 0:
                continue
            tangent = np.array([-v[1],v[0]])
            mid = (pa+pb)/2; mid += (c-mid@v)*v
            line = LineString([mid-10000*tangent,mid+10000*tangent])
            if a.distance(line) > .35 or b.distance(line) > .35:
                continue
            for s, poly in [(r,a),(q,b)]:
                xy=np.array(poly.exterior.coords)
                near=abs(xy@v-c)<=.35
                if near.sum()<2:
                    continue
                xy[near] += (c-xy[near]@v)[:,None]*v
                snapped=Polygon(xy,[h.coords for h in poly.interiors])
                if snapped.is_valid and snapped.area > .8*poly.area:
                    s['footprint']=mapping(snapped)
            ridge_joins.append([r['id'],q['id']])
    # A footprint belongs to at most one roof surface. Preserve actual overhangs.
    occupied = Polygon()
    unique_roofs = []
    for r in roofs:
        for fp in polygons(shape(r['footprint']).difference(occupied)):
            if fp.area < 2:
                continue
            q = copy.deepcopy(r);q['footprint']=mapping(fp)
            q['polygon']=mapping(roof_profile(q,fp));q['area_m2']=shape(q['polygon']).area
            q['boundary_status']='Regularized observed outline (0.22 m); shared ridges <=0.35 m; overhang retained'
            unique_roofs.append(q)
        occupied=occupied.union(shape(r['footprint']))
    roofs=unique_roofs
    for r in roofs:
        domain=stable(shape(r['footprint']).buffer(.50, join_style=2))
        for q in roofs:
            if [r['id'],q['id']] not in ridge_joins and [q['id'],r['id']] not in ridge_joins:
                continue
            n,m=np.array(r['normal']),np.array(q['normal'])
            v=n[:2]/n[2]-m[:2]/m[2]
            c=n@r['plane_origin']/n[2]-m@q['plane_origin']/m[2]
            length=np.linalg.norm(v);v/=length;c/=length
            centre=np.array(shape(r['footprint']).centroid.coords[0])
            on_line=centre+(c-centre@v)*v
            tangent=np.array([-v[1],v[0]])
            divider=LineString([on_line-10000*tangent,on_line+10000*tangent])
            side=np.sign(centre@v-c)
            domain=stable(unary_union([p for p in polygons(split(domain,divider))
                                      if (np.array(p.representative_point().coords[0])@v-c)*side>=0]))
        r['clipping_domain']=mapping(domain)

    walls = []
    for s in I['surfaces']:
        if s['kind'] != 'wall':
            continue
        n=np.array(s['normal']);o=np.array(s['plane_origin']);u=np.array(s['basis_u'])
        p=shape(s['polygon']);xy=np.array(p.exterior.coords)
        lo,hi=xy.min(0),xy.max(0)
        candidates=(abs(P@n-n@o)<.065)&(abs(N@n)>np.cos(np.radians(15)))&(PL>.25)
        local=(P-o)@u
        candidates &= (local>=lo[0]-.05)&(local<=hi[0]+.05)
        q=P[candidates]
        if len(q)<150:
            rejected.append({'id':s['id'],'reason':'Insufficient connected wall support'});continue
        along=(q-o)@u
        t0,t1=np.quantile(along,[.005,.995]);base,top=np.quantile(q[:,2],[.01,.99])
        length=t1-t0
        # Short low planar furniture/roof fascia must not become full-height walls.
        if length<.60 or top-base<1.4 or s['area_m2']/(length*(top-base))<.35:
            rejected.append({'id':s['id'],'reason':'Short/low/fragmentary wall candidate'});continue
        w=copy.deepcopy(s)
        w.update(a=(o+t0*u)[:2].tolist(),b=(o+t1*u)[:2].tolist(),base=float(base),top=float(top),
                 thickness_m=.20,thickness_status='ASSUMED 0.20 m',source_ids=[s['id']])
        walls.append(w)
    # Pair parallel observed faces before making the connection graph.
    walls.sort(key=lambda w:-w['area_m2'])
    used=set();paired=[]
    for i,w in enumerate(walls):
        if i in used:continue
        a,b=np.array(w['a']),np.array(w['b']);d=(b-a)/np.linalg.norm(b-a);n=np.array([-d[1],d[0]])
        options=[]
        for j,q in enumerate(walls[i+1:],i+1):
            if j in used:continue
            c,e=np.array(q['a']),np.array(q['b']);qd=(e-c)/np.linalg.norm(e-c)
            if abs(d@qd)<np.cos(np.radians(5)):continue
            gap=abs(((c+e)/2-a)@n)
            t=sorted([(c-a)@d,(e-a)@d]);length=np.linalg.norm(b-a)
            overlap=max(0,min(length,t[1])-max(0,t[0]))
            z_overlap=max(0,min(w['top'],q['top'])-max(w['base'],q['base']))
            if gap<=.85 and overlap>.65*min(length,t[1]-t[0]) and z_overlap>.4*min(w['top']-w['base'],q['top']-q['base']):
                options.append((gap,j,t,c,e))
        if options:
            gap,j,t,c,e=min(options,key=lambda item:item[0]);q=walls[j];used.add(j)
            delta=((c+e)/2-a)@n
            # Near-coplanar fragments merge; separate faces estimate thickness.
            centre=delta/2 if gap>=.075 else 0.
            w['a']=(a+min(0,t[0])*d+centre*n).tolist();w['b']=(a+max(np.linalg.norm(b-a),t[1])*d+centre*n).tolist()
            if gap>=.075:
                w['thickness_m']=float(gap);w['thickness_status']='PAIRED OBSERVED FACES'
            w['base']=min(w['base'],q['base']);w['top']=max(w['top'],q['top']);w['source_ids']+=q['source_ids']
        used.add(i);paired.append(w)
    walls=paired
    joins=_line_join(walls)
    # Stabilize nearly coincident bases in connected neighbours only.
    for j in joins:
        w=next(w for w in walls if w['id']==j['wall']);q=next(w for w in walls if w['id']==j['to'])
        if abs(w['base']-q['base'])<.30:
            w['base']=q['base']=min(w['base'],q['base'])
    occupied=Polygon();result=[]
    for w in walls:
        a,b=np.array(w['a']),np.array(w['b'])
        if np.linalg.norm(b-a)<.2:continue
        # Square caps reach the shared centreline; subtraction resolves corner overlap.
        footprint=stable(LineString([a,b]).buffer(w['thickness_m']/2,cap_style=3,join_style=2))
        footprint=stable(footprint.difference(occupied))
        footprint=unary_union([p for p in polygons(footprint) if p.area >= .02])
        if footprint.is_empty:continue
        occupied=occupied.union(footprint)
        pieces=[];remaining=footprint
        for r in sorted(roofs,key=lambda r:-shape(r['footprint']).intersection(footprint).area):
            # The clipping domain may extend 0.50 m beyond a sampled roof edge;
            # this removes unsupported wall tips without altering the roof overhang.
            covered=remaining.intersection(stable(shape(r['clipping_domain'])))
            for part in polygons(covered):
                if part.area<.001:continue
                heights=roof_z(r,np.array(part.exterior.coords))-r['thickness_m']/abs(r['normal'][2])
                if heights.min()<=w['base']+.5 or heights.min()>w['top']+.75:
                    continue
                pieces.append({'polygon':mapping(part),'base':w['base'],
                               'top':float(max(w['top'],heights.max())+.01),
                               'clip':{'origin':(np.array(r['plane_origin'])-np.array([0,0,r['thickness_m']/abs(r['normal'][2])])).tolist(),
                                       'normal':r['normal']},'roof_id':r['id']})
                remaining=remaining.difference(part)
        for part in polygons(remaining):
            if part.area>=.001:pieces.append({'polygon':mapping(part),'base':w['base'],'top':w['top']})
        w['solid_parts']=pieces;w['footprint']=mapping(footprint)
        w['boundary_status']='Regularized wall strip; paired faces where observed; endpoint joins <=0.50 m; clipped under supported roofs'
        w['inferred_completion']=True
        result.append(w)
    seen={}
    for s in result+roofs:
        raw_id=s['id'];s['raw_candidate_id']=raw_id
        seen[raw_id]=seen.get(raw_id,0)+1
        if seen[raw_id]>1:s['id']=raw_id+'_'+str(seen[raw_id])
    I['surfaces']=result+roofs
    I['method_version']='0.5'
    I['topology']={'regularization_m':.22,'max_wall_join_m':.50,'max_roof_join_m':.35,
                   'wall_joins':joins,'roof_joins':ridge_joins,'rejected_fragments':rejected,
                   'raw_surface_count':len(I.get('proposals',[])),
                   'clipped_wall_parts':sum('clip' in p for s in result for p in s['solid_parts']),
                   'wall_plan_overlap_m2':max(0.,sum(shape(s['footprint']).area for s in result)-unary_union([shape(s['footprint']) for s in result]).area),
                   'global_envelope_watertight':False,
                   'scope':'Bounded joins and intersections; unobserved facades and unresolved connections remain explicit'}
    I['scope']='Regularized observed walls and roofs; bounded inferred connections; no unsupported rooms'
    return I


def structural_envelope(env, walls):
    """Use closed wall loops instead of the noisy ceiling raster when supported."""
    env=stable(env)
    lines=[];wall_polys=[]
    for w in walls:
        p=stable(shape(w['polygon']));wall_polys.append(p)
        lines.append(LineString([(w['position'],w['t0']),(w['position'],w['t1'])]) if w['axis']==0 else
                     LineString([(w['t0'],w['position']),(w['t1'],w['position'])]))
    loops=[p for p in polygonize(unary_union(lines)) if p.area>2 and p.intersection(env).area>.6*p.area]
    candidate=unary_union(loops+wall_polys)
    status='Regularized ceiling support; wall-face snapping and bounded closure'
    if loops and candidate.intersection(env).area>.70*env.area and candidate.area<1.35*env.area:
        env=stable(candidate.buffer(.01,join_style=2).buffer(-.01,join_style=2))
        status='Shared closed wall loops and wall outer faces; ceiling support confirms footprint'
    else:
        env=clean_polygon(env,.30)
        # Snap vertices only to nearby observed wall faces, in the fitted UV frame.
        snapped=[]
        for part in polygons(env):
            xy=np.array(part.exterior.coords)
            for point in xy:
                for axis in (0,1):
                    values=[w['position']+sign*w['thickness']/2 for w in walls for sign in (-1,1)
                            if w['axis']==axis and w['t0']-.35<=point[1-axis]<=w['t1']+.35]
                    if values:
                        nearest=min(values,key=lambda v:abs(v-point[axis]))
                        if abs(nearest-point[axis])<.35:point[axis]=nearest
            poly=Polygon(xy,[h.coords for h in part.interiors])
            snapped.append(poly if poly.is_valid and poly.area>.8*part.area else part)
        env=stable(unary_union(snapped))
    # Clip wall tips to the shared footprint and assign overlaps to one wall only.
    taken=Polygon();kept=[]
    for w in walls:
        p=stable(shape(w['polygon'])).intersection(env).difference(taken)
        if p.area<.01:continue
        w['polygon']=mapping(p);kept.append(w);taken=taken.union(p)
    return env,kept,status
