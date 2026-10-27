"""IFC4 parametric solids with recorded wall/roof intersections."""
import uuid
import numpy as np
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.geom
import ifcopenshell.validate
import trimesh
from shapely.geometry import shape
from shapely.geometry.polygon import orient
from .topology import polygons


def add_surfaces(f, project, building, body, I):
    api=ifcopenshell.api.run
    ns=uuid.UUID('68629214-370a-4429-a25d-66837031ea06')
    def point(p):return f.create_entity('IfcCartesianPoint',Coordinates=tuple(map(float,p)))
    def direction(v):return f.create_entity('IfcDirection',DirectionRatios=tuple(map(float,v)))
    def axis(o,n=(0,0,1),u=(1,0,0)):
        return f.create_entity('IfcAxis2Placement3D',Location=point(o),Axis=direction(n),RefDirection=direction(u))
    def curve(coords):return f.create_entity('IfcPolyline',Points=[point(p) for p in coords])
    def profile(p):
        p=orient(p,sign=1.)
        attrs={'ProfileType':'AREA','OuterCurve':curve(p.exterior.coords)}
        if p.interiors:attrs['InnerCurves']=[curve(h.coords) for h in p.interiors]
        return f.create_entity('IfcArbitraryProfileDefWithVoids' if p.interiors else 'IfcArbitraryClosedProfileDef',**attrs)
    def extrusion(p,o,n,u,d,depth):
        return f.create_entity('IfcExtrudedAreaSolid',SweptArea=profile(p),Position=axis(o,n,u),ExtrudedDirection=direction(d),Depth=float(depth))
    T=np.eye(4);T[:3,3]=I['coordinate_origin_m']
    for s in I.get('surfaces',[]):
        cls=s['ifc_class'];name=s['id']+(' Regularized wall' if cls=='IfcWall' else 'Roof surface')
        e=api('root.create_entity',f,ifc_class=cls,name=name)
        e.GlobalId=ifcopenshell.guid.compress(uuid.uuid5(ns,I['source_sha256']+name).hex)
        e.PredefinedType='NOTDEFINED'
        api('spatial.assign_container',f,products=[e],relating_structure=building)
        api('geometry.edit_object_placement',f,product=e,matrix=T)
        items=[];clipped=False
        if 'solid_parts' in s:
            from ifcopenshell.util.data import Clipping
            from .topology import stable
            # Cut one wall body with bounded half-spaces. Unioning pieces which
            # only touch along faces can leave duplicate internal faces in OCC.
            height=max(part['top'] for part in s['solid_parts'])
            for footprint in polygons(shape(s['footprint'])):
                solid=extrusion(footprint,[0,0,s['base']],[0,0,1],[1,0,0],[0,0,1],height-s['base'])
                for part in s['solid_parts']:
                    region=stable(footprint).intersection(stable(shape(part['polygon'])))
                    if region.area < .0001:continue
                    clip=part.get('clip',{'origin':[0,0,part['top']],'normal':[0,0,1]})
                    n=np.array(clip['normal'],dtype=float);u=np.cross(n,[0,1,0]);u/=np.linalg.norm(u)
                    if region.area >= footprint.area - .0001:
                        solid=Clipping(location=tuple(clip['origin']),normal=tuple(clip['normal'])).apply(f,solid,1.)
                        continue
                    for cut in polygons(region):
                        # Finite parametric cutters avoid OCC's fragile bounded
                        # half-space handling at coincident wall side faces.
                        from .topology import roof_profile
                        v=np.cross(n,u)
                        plane={'normal':n,'plane_origin':clip['origin'],'basis_u':u,'basis_v':v}
                        cutter_profile=roof_profile(plane,cut)
                        up=np.array([0.,0.,1.])
                        cutter=extrusion(cutter_profile,clip['origin'],n,u,[up@u,up@v,up@n],1000.)
                        solid=f.create_entity('IfcBooleanResult',Operator='DIFFERENCE',FirstOperand=solid,SecondOperand=cutter)
                items.append(solid);clipped=True
        else:
            n,u,v=[np.array(s[k]) for k in ('normal','basis_u','basis_v')]
            if s.get('extrusion')=='VERTICAL_DOWN_FROM_OBSERVED_TOP':
                origin=np.array(s['plane_origin']);world_down=np.array([0.,0.,-1.])
                direction_local=[world_down@u,world_down@v,world_down@n]
                depth=s['thickness_m']/abs(n[2])
            else:
                origin=np.array(s['plane_origin'])-n*s['thickness_m']/2
                direction_local=[0,0,1];depth=s['thickness_m']
            items=[extrusion(p,origin,n,u,direction_local,depth) for p in polygons(shape(s['polygon']))]
        # Union partitioned pieces so their internal seams are not exported.
        if len(items) > 1:
            solid = items[0]
            for other in items[1:]:
                solid = f.create_entity('IfcBooleanResult', Operator='UNION', FirstOperand=solid, SecondOperand=other)
            items = [solid]; clipped = True
        rep=f.create_entity('IfcShapeRepresentation',ContextOfItems=body,RepresentationIdentifier='Body',
                            RepresentationType='CSG' if clipped else 'SweptSolid',Items=items)
        api('geometry.assign_representation',f,product=e,representation=rep)
        style=api('style.add_style',f,name=name)
        color=(.68,.66,.57) if cls=='IfcWall' else (.65,.28,.20)
        api('style.add_surface_style',f,style=style,attributes={'SurfaceColour':dict(zip(['Red','Green','Blue'],color)),'Transparency':0.})
        api('style.assign_representation_styles',f,shape_representation=rep,styles=[style])
        pset=api('pset.add_pset',f,product=e,name='ScanToBIM_Evidence')
        api('pset.edit_pset',f,pset=pset,properties={
            'MethodVersion':'0.5','ReviewRequired':True,'SourceSHA256':I['source_sha256'],
            'SupportScore':s['score'],'ResidualP95_m':s['P95_m'],'SupportPoints':s['support_points'],
            'Thickness_m':s['thickness_m'],'ThicknessEvidence':s['thickness_status'],
            'BoundaryEvidence':s['boundary_status'],'ClassificationEvidence':s['classification_status'],
            'EnvelopeConnection':'Bounded joins and roof clipping; unresolved connections require review',
            'CompletionStatus':'Geometric regularization includes inferred extents and gap infill',
            'RoofOverhang':'Observed roof outline preserved; not trimmed to wall footprint' if cls=='IfcRoof' else ''})


def validate_geometry(m):
    logger=ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(m,logger,express_rules=True)
    settings=ifcopenshell.geom.settings();settings.set(settings.USE_WORLD_COORDS,True)
    records=[];failures=[]
    for e in m.by_type('IfcElement'):
        if not e.Representation:continue
        try:
            sh=ifcopenshell.geom.create_shape(settings,e);g=sh.geometry
            mesh=trimesh.Trimesh(vertices=np.array(g.verts).reshape(-1,3),faces=np.array(g.faces).reshape(-1,3),process=False)
            records.append({'guid':e.GlobalId,'name':e.Name,'class':e.is_a(),
                            'watertight':bool(mesh.is_watertight),'volume_m3':float(mesh.volume),'area_m2':float(mesh.area)})
        except Exception as exc:failures.append({'guid':e.GlobalId,'error':str(exc)})
    errors=[r for r in logger.statements if r.get('level') in {'error','critical'}]
    invalid=[r for r in records if not r['watertight'] or r['volume_m3']<=0]
    if errors or failures or invalid:
        raise ValueError('IFC validation failed: '+str(errors[:1] or failures[:1] or invalid[:1]))
    return logger.statements,records,failures


def build_exterior_ifc(I,output):
    f=ifcopenshell.file(schema='IFC4');api=ifcopenshell.api.run
    def entity(cls,name):return api('root.create_entity',f,ifc_class=cls,name=name)
    project=entity('IfcProject','qFuzzyRESF 0.3.0 | Unified scan to BIM')
    api('unit.assign_unit',f,units=[api('unit.add_si_unit',f,unit_type=t) for t in ['LENGTHUNIT','AREAUNIT','VOLUMEUNIT']])
    context=api('context.add_context',f,context_type='Model')
    body=api('context.add_context',f,context_type='Model',context_identifier='Body',target_view='MODEL_VIEW',parent=context)
    site=entity('IfcSite','Survey site; CRS unspecified');building=entity('IfcBuilding','Reconstructed building')
    for parent,child in [(project,site),(site,building)]:
        api('aggregate.assign_object',f,products=[child],relating_object=parent)
        api('geometry.edit_object_placement',f,product=child,matrix=np.eye(4))
    pset=api('pset.add_pset',f,product=project,name='ScanToBIM_Evidence')
    api('pset.edit_pset',f,pset=pset,properties={'MethodVersion':'0.5 pilot','ToolVersion':'0.3.0','Mode':I.get('mode','unified'),
        'SourceSHA256':I['source_sha256'],'ReferenceUsedForReconstruction':False,'ReviewRequired':True,
        'Scope':I['scope'],'RoomsStatus':'Insufficient interior evidence','BuildingEnvelopeClosed':False,
        'FuzzyScoreMeaning':'Expert support index; not calibrated probability',
        'OpeningsStatus':'Not semantically classified; inferred wall completion can close gaps'})
    add_surfaces(f,project,building,body,I)
    f.write(str(output));m=ifcopenshell.open(str(output))
    messages,meshes,failures=validate_geometry(m)
    return {'schema':'IFC4','mode':I.get('mode','unified'),
            'counts':{c:len(m.by_type(c)) for c in ['IfcWall','IfcRoof','IfcSlab','IfcCovering','IfcSpace','IfcBuildingStorey','IfcOpeningElement','IfcDoor','IfcWindow']},
            'schema_messages':messages,'geometry_failures':failures,'meshes':meshes,
            'space_area_m2':0.,'space_side_boundary_length_m':{},'building_envelope_closed':False,
            'topology':I.get('topology',{}),
            'validation_scope':'EXPRESS, closed element meshes, plan overlaps and recorded joins; no global envelope certification'}
