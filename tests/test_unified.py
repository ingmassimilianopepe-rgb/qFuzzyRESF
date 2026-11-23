"""Regression checks for the reported gaps, tips and duplicated geometry."""
import json
import numpy as np
import ifcopenshell
import ifcopenshell.geom
from shapely.geometry import shape, Polygon
from fuzzyresf.cli import run
from fuzzyresf.topology import clean_polygon
from test_exterior import house


def test_unified_shell_keeps_overhang_and_cuts_wall_tips(tmp_path):
    # Four observed walls plus two sloping roofs; enlarge ONLY the measured roof.
    P=house()
    angle=np.radians(27)
    R=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
    x,y=np.meshgrid(np.arange(-.45,8.46,.06),np.arange(-.45,5.46,.06))
    roof=np.c_[x.ravel(),y.ravel(),(3+.375*(4-abs(x-4))).ravel()]
    roof[:,:2]=roof[:,:2]@R.T
    P=np.vstack([P,roof])
    source=tmp_path/'overhang.xyz64';P.astype('<f8').tofile(source)
    output=tmp_path/'unified.ifc'
    run({'schema_version':1,'input':{'path':str(source)},'output':str(output)})
    I=json.loads(output.with_suffix('.fuzzyresf').joinpath('inference.json').read_text())
    V=json.loads(output.with_suffix('.fuzzyresf').joinpath('validation.json').read_text())
    assert I['mode']=='unified'
    assert not I['spaces']
    assert V['counts']['IfcRoof']==2
    assert V['counts']['IfcWall']==4
    assert I['topology']['clipped_wall_parts']>=4
    assert all(m['watertight'] and m['volume_m3']>0 for m in V['meshes'])
    model=ifcopenshell.open(output)
    settings=ifcopenshell.geom.settings();settings.set(settings.USE_WORLD_COORDS,True)
    for wall in model.by_type('IfcWall'):
        g=ifcopenshell.geom.create_shape(settings,wall).geometry
        vertices=np.array(g.verts).reshape(-1,3)
        xy=vertices[:,:2]@R
        roof_height=3+.375*(4-abs(xy[:,0]-4))
        assert np.max(vertices[:,2]-roof_height)<.025
    roof_vertices=[]
    for roof_entity in model.by_type('IfcRoof'):
        g=ifcopenshell.geom.create_shape(settings,roof_entity).geometry
        roof_vertices.extend(np.array(g.verts).reshape(-1,3))
    xy=np.array(roof_vertices)[:,:2]@R
    assert xy[:,0].min()<-.25 and xy[:,0].max()>8.25
    assert xy[:,1].min()<-.25 and xy[:,1].max()>5.25


def test_one_job_contains_rooms_and_separate_supported_roofs(tmp_path,two_rooms):
    x,y=np.meshgrid(np.arange(-.3,8.31,.06),np.arange(-.3,5.31,.06))
    roof=np.c_[x.ravel(),y.ravel(),(3.2+.375*(4-abs(x-4))).ravel()]
    P=np.vstack([two_rooms,roof])
    source=tmp_path/'mixed.xyz64';P.astype('<f8').tofile(source)
    output=tmp_path/'mixed.ifc'
    run({'schema_version':1,'input':{'path':str(source)},'output':str(output)})
    model=ifcopenshell.open(output)
    assert len(model.by_type('IfcBuilding'))==1
    assert len(model.by_type('IfcSpace'))==2
    assert len(model.by_type('IfcRoof'))==2
    I=json.loads(output.with_suffix('.fuzzyresf').joinpath('inference.json').read_text())
    assert not I['reference_used']
    # Floor/ceiling share the wall footprint; no independent raster teeth.
    envelope=shape(I['envelope'])
    assert all(shape(w['polygon']).difference(envelope).area<1e-6 for w in I['walls'])


def test_large_real_recess_is_not_filled_by_contour_regularization():
    p=Polygon([(0,0),(8,0),(8,3),(5,3),(5,6),(0,6)])
    regular=clean_polygon(p,.22)
    assert regular.is_valid
    assert regular.symmetric_difference(p).area<.02
    assert not regular.contains(__import__('shapely.geometry',fromlist=['Point']).Point(7,5))
