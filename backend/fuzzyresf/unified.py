"""One evidence-driven job: interior spaces and observed building surfaces together."""
import numpy as np
from shapely.geometry import shape
from shapely.affinity import affine_transform
from .levels import select_level
from .inference import infer
from .exterior import infer_exterior
from .topology import regularize_structure, plane_xy


def infer_unified(P, N, PL, audit, profile='monotone', max_raster_cells=4_000_000,
                  progress=lambda percent, stage: None):
    decisions=[]
    interior=None
    try:
        level=select_level(P,N,PL,audit)
    except ValueError as exc:
        decisions.append({'component':'spaces','status':'NOT_SUPPORTED','reason':str(exc)})
    else:
        # Successful pairing is necessary, not a guarantee of usable wall/room topology.
        try:
            interior=infer(P,N,PL,audit,profile,max_raster_cells,progress)
        except ValueError as exc:
            decisions.append({'component':'spaces','status':'NOT_RECONSTRUCTED','reason':str(exc)})
        else:
            decisions.append({'component':'spaces','status':'PROVISIONAL','count':len(interior['spaces'])})
    progress(58,'Fitting building surfaces and shared connections')
    raw=infer_exterior(P,N,PL,audit,profile if profile in {'geometry_only','evidence','relational'} else 'monotone',max_raster_cells,progress)
    envelope=regularize_structure(raw,P,N,PL)
    if interior is None:
        result=envelope
    else:
        # Reuse the interior wall/floor graph. Add only exterior surfaces not already
        # explained by it, retaining a single building and coordinate system.
        R=np.array(interior['rotation_world_to_uv']);o=np.array(interior['origin_xy'])
        env=affine_transform(shape(interior['envelope']),[R[0,0],R[1,0],R[0,1],R[1,1],o[0],o[1]])
        zf=interior['main_level']['floor_z'];zc=interior['main_level']['ceiling_z']
        extra=[]
        for s in envelope['surfaces']:
            if s['kind']=='roof':
                fp=shape(s['footprint'])
                from .topology import roof_z
                height=float(roof_z(s,[fp.centroid.coords[0]])[0])
                # A scanned ceiling is not also a separate roof in the same job.
                if height>zc+.35:
                    extra.append(s)
            else:
                fp=shape(s['footprint'])
                if fp.intersection(env.buffer(.50)).area<.30*fp.area and (s['top']>zc+.60 or s['base']<zf-.60):
                    extra.append(s)
        interior['surfaces']=extra
        # Keep pre-fusion diagnostics separate: many fitted exterior candidates
        # are not exported when a supported interior model exists.
        interior['additional_surface_topology_pre_fusion']=envelope['topology']
        interior['topology']={
            'scope':'Supported interior wall footprint and retained additional surfaces',
            'interior_wall_count':len(interior['walls']),
            'envelope_status':interior.get('envelope_status',''),
            'retained_additional_surface_ids':[s['id'] for s in extra],
            'building_envelope_watertight':False,
        }
        interior['scope']='Supported main interior level plus additional observed building surfaces; provisional room semantics'
        result=interior
    result['mode']='unified';result['method_version']='0.6';result['decisions']=decisions
    return result
