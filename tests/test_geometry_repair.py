"""Fixed-precision overlays preserve area and separate zero-area contacts."""
import numpy as np
from shapely.geometry import Polygon, box, GeometryCollection, LineString, MultiPolygon, mapping, shape
from fuzzyresf.topology import stable, overlay, structural_envelope


def test_nested_mixed_collection_preserves_all_polygon_area():
    original = GeometryCollection([
        GeometryCollection([box(0, 0, 2, 2), LineString([(2, 0), (3, 0)])]),
        MultiPolygon([box(4, 0, 5, 1), box(6, 0, 7, 1)])])
    normalized = stable(original)
    assert normalized.is_valid
    assert abs(normalized.area - 6) < 1e-9
    assert normalized.geom_type == 'MultiPolygon'


def test_overlay_normalizes_line_contact_before_next_difference():
    # A touching wing contributes a line but no observed wall area. Only the
    # polygon may enter the next areal overlay, the real area is retained.
    footprint = MultiPolygon([box(0, 0, 2, 2), box(3, 0, 4, 2)])
    wall = box(1, 0, 3, 1)
    clipped = overlay(wall, footprint, 'intersection')
    assert abs(clipped.area - 1) < 1e-9
    result = overlay(clipped, box(1, 0, 1.5, 1), 'difference')
    assert result.is_valid
    assert abs(result.area - .5) < 1e-9
    assert result.geom_type == 'Polygon'


def test_structural_envelope_partition_is_nonoverlapping_and_bounded():
    env = box(0, 0, 5, 4)
    walls = []
    for axis, pos, start, end in [(0, 0, 0, 4), (0, 5, 0, 4), (1, 0, 0, 5), (1, 4, 0, 5), (0, 2.5, 0, 4)]:
        poly = box(pos-.1,start,pos+.1,end) if axis == 0 else box(start,pos-.1,end,pos+.1)
        walls.append(dict(axis=axis, position=pos, t0=start, t1=end, thickness=.2, polygon=mapping(poly)))
    env, walls, status = structural_envelope(env, walls)
    polys = [shape(w['polygon']) for w in walls]
    assert all(p.is_valid and p.difference(env).area < 1e-9 for p in polys)
    assert all(p.intersection(q).area < 1e-9 for i,p in enumerate(polys) for q in polys[i+1:])
    assert '0.001 m' in status
    assert len(walls) == 5


def test_partitioned_gable_wall_is_closed_and_keeps_parametric_roof_cut(tmp_path):
    import ifcopenshell
    import ifcopenshell.geom
    from fuzzyresf.exterior_ifc import build_exterior_ifc
    footprint = box(0, 0, 6, .2)
    wall = dict(id='W001',kind='wall',ifc_class='IfcWall',footprint=mapping(footprint),
                base=0.,top=5.,score=.9,P95_m=.01,support_points=500,
                thickness_m=.2,thickness_status='synthetic paired faces',
                boundary_status='synthetic test',classification_status='wall',
                solid_parts=[
                    dict(polygon=mapping(box(0,0,3,.2)),top=5.,
                         clip=dict(origin=[0,0,3],normal=[-.196116135,0,.980580676])),
                    dict(polygon=mapping(box(3,0,6,.2)),top=5.,
                         clip=dict(origin=[3,0,3.6],normal=[.196116135,0,.980580676]))])
    I=dict(coordinate_origin_m=[0,0,0],source_sha256='synthetic-gable',
           surfaces=[wall],scope='Synthetic finite wall partition')
    output=tmp_path/'gable.ifc'
    validation=build_exterior_ifc(I,output)
    assert all(m['watertight'] for m in validation['meshes'])
    model=ifcopenshell.open(output)
    settings=ifcopenshell.geom.settings();settings.set(settings.USE_WORLD_COORDS,True)
    item=ifcopenshell.geom.create_shape(settings,model.by_type('IfcWall')[0])
    vertices=np.array(item.geometry.verts).reshape(-1,3)
    target=3+.2*(3-abs(vertices[:,0]-3))
    assert np.max(vertices[:,2]-target)<.002
    assert abs(validation['meshes'][0]['volume_m3']-3.96)<.005
    assert vertices[:,0].min()>=-1e-7 and vertices[:,0].max()<=6+1e-7
    assert vertices[:,1].min()>=-1e-7 and vertices[:,1].max()<=.2+1e-7
    assert model.by_type('IfcExtrudedAreaSolid')
    assert model.by_type('IfcBooleanClippingResult')
