"""Dependency-free IFC2X3 semantic exporter for qFuzzyRESF.

Exports wall solids, floor/ceiling slabs, IfcOpeningElement voids and their
IfcDoor/IfcWindow fills. The portable Windows build therefore does not depend
on IfcOpenShell.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import time
import uuid

from fuzzy_resf_core import ReconstructionModel

_IFC64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def _guid():
    n = uuid.uuid4().int
    chars = []
    for _ in range(22):
        chars.append(_IFC64[n & 63])
        n >>= 6
    return "".join(reversed(chars))


def _q(value):
    return "'" + str(value).replace("'", "''") + "'"


class _Writer:
    def __init__(self):
        self.entities = []
        self.eid = 1

    def add(self, expr):
        i = self.eid
        self.entities.append(f"#{i}={expr};")
        self.eid += 1
        return i


def _frame(theta_deg):
    th = math.radians(theta_deg)
    return (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))


def _placement(w, parent, x, y, z, tx, ty, zdir):
    p = w.add(f"IFCCARTESIANPOINT(({x:.9f},{y:.9f},{z:.9f}))")
    ref = w.add(f"IFCDIRECTION(({tx:.12f},{ty:.12f},0.))")
    axis = w.add(f"IFCAXIS2PLACEMENT3D(#{p},#{zdir},#{ref})")
    return w.add(f"IFCLOCALPLACEMENT(#{parent},#{axis})")


def _rect_shape(w, context, length, depth, height, zdir, xdir, origin3):
    p2 = w.add(f"IFCCARTESIANPOINT(({length/2.0:.9f},0.))")
    axis2 = w.add(f"IFCAXIS2PLACEMENT2D(#{p2},$)")
    profile = w.add(f"IFCRECTANGLEPROFILEDEF(.AREA.,$,#{axis2},{max(length,1e-3):.9f},{max(depth,1e-3):.9f})")
    solid_axis = w.add(f"IFCAXIS2PLACEMENT3D(#{origin3},#{zdir},#{xdir})")
    solid = w.add(f"IFCEXTRUDEDAREASOLID(#{profile},#{solid_axis},#{zdir},{max(height,1e-3):.9f})")
    rep = w.add(f"IFCSHAPEREPRESENTATION(#{context},'Body','SweptSolid',(#{solid}))")
    return w.add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")


def _polygon_shape(w, context, footprint, height, zdir, xdir, origin3):
    if len(footprint) < 3:
        return None
    pts = [w.add(f"IFCCARTESIANPOINT(({float(x):.9f},{float(y):.9f}))") for x, y in footprint]
    pts.append(pts[0])
    poly = w.add("IFCPOLYLINE((" + ",".join(f"#{p}" for p in pts) + "))")
    profile = w.add(f"IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,$,#{poly})")
    solid_axis = w.add(f"IFCAXIS2PLACEMENT3D(#{origin3},#{zdir},#{xdir})")
    solid = w.add(f"IFCEXTRUDEDAREASOLID(#{profile},#{solid_axis},#{zdir},{max(height,1e-3):.9f})")
    rep = w.add(f"IFCSHAPEREPRESENTATION(#{context},'Body','SweptSolid',(#{solid}))")
    return w.add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")


def _pset(w, owner, element, name, confidence, state, extra=None):
    props = [
        w.add(f"IFCPROPERTYSINGLEVALUE('FinalConfidence',$,IFCREAL({float(confidence):.6f}),$)"),
        w.add(f"IFCPROPERTYSINGLEVALUE('ClassificationState',$,IFCLABEL({_q(state)}),$)"),
        w.add("IFCPROPERTYSINGLEVALUE('AlgorithmVersion',$,IFCLABEL('Fuzzy-RESF 1.1 semantic BIM'),$)"),
    ]
    for prop_name, ifc_value in extra or []:
        props.append(w.add(f"IFCPROPERTYSINGLEVALUE({_q(prop_name)},$,{ifc_value},$)"))
    pset = w.add(f"IFCPROPERTYSET({_q(_guid())},#{owner},{_q(name)},$,({','.join('#'+str(p) for p in props)}))")
    w.add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{element}),#{pset})")


def write_ifc(model_or_walls, path, project_name="Fuzzy-RESF Scan-to-BIM"):
    path = Path(path)
    if isinstance(model_or_walls, ReconstructionModel):
        walls = list(model_or_walls.modeled_walls)
        openings = list(model_or_walls.openings)
        slabs = list(model_or_walls.slabs)
    else:
        walls = list(model_or_walls)
        openings, slabs = [], []

    w = _Writer()
    person = w.add("IFCPERSON($,'Pepe','Massimiliano',$,$,$,$,$)")
    org = w.add("IFCORGANIZATION($,'qFuzzyRESF',$,$,$)")
    person_org = w.add(f"IFCPERSONANDORGANIZATION(#{person},#{org},$)")
    app = w.add(f"IFCAPPLICATION(#{org},'1.1','qFuzzyRESF semantic Scan-to-BIM','qFuzzyRESF')")
    owner = w.add(f"IFCOWNERHISTORY(#{person_org},#{app},$,.ADDED.,$,$,$,{int(time.time())})")
    unit_len = w.add("IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)")
    units = w.add(f"IFCUNITASSIGNMENT((#{unit_len}))")
    origin3 = w.add("IFCCARTESIANPOINT((0.,0.,0.))")
    zdir = w.add("IFCDIRECTION((0.,0.,1.))")
    xdir = w.add("IFCDIRECTION((1.,0.,0.))")
    world_axis = w.add(f"IFCAXIS2PLACEMENT3D(#{origin3},#{zdir},#{xdir})")
    context = w.add(f"IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#{world_axis},$)")
    project = w.add(f"IFCPROJECT({_q(_guid())},#{owner},{_q(project_name)},$,$,$,$,(#{context}),#{units})")
    site_place = w.add(f"IFCLOCALPLACEMENT($,#{world_axis})")
    site = w.add(f"IFCSITE({_q(_guid())},#{owner},'Site',$,$,#{site_place},$,$,.ELEMENT.,$,$,$,$,$)")
    building_place = w.add(f"IFCLOCALPLACEMENT(#{site_place},#{world_axis})")
    building = w.add(f"IFCBUILDING({_q(_guid())},#{owner},'Building',$,$,#{building_place},$,$,.ELEMENT.,$,$,$)")
    storey_place = w.add(f"IFCLOCALPLACEMENT(#{building_place},#{world_axis})")
    storey = w.add(f"IFCBUILDINGSTOREY({_q(_guid())},#{owner},'Storey 01',$,$,#{storey_place},$,$,.ELEMENT.,0.)")
    w.add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{project},(#{site}))")
    w.add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{site},(#{building}))")
    w.add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{building},(#{storey}))")

    contained, wall_entities, wall_data = [], {}, {}
    for wall in walls:
        dx, dy = float(wall.x2-wall.x1), float(wall.y2-wall.y1)
        length = max(1e-3, math.hypot(dx, dy))
        height = max(1e-3, float(wall.z1-wall.z0))
        tx, ty = dx/length, dy/length
        place = _placement(w, storey_place, wall.x1, wall.y1, wall.z0, tx, ty, zdir)
        shape = _rect_shape(w, context, length, max(.08, float(wall.thickness)), height, zdir, xdir, origin3)
        entity = w.add(f"IFCWALLSTANDARDCASE({_q(_guid())},#{owner},{_q('Wall_'+str(wall.id))},$,$,#{place},#{shape},$)")
        contained.append(entity)
        wall_entities[wall.id] = entity
        wall_data[wall.id] = wall
        _pset(w, owner, entity, "Pset_FuzzyRESF", wall.confidence, wall.state, [
            ("RESFSupport", f"IFCREAL({float(wall.area_support):.6f})"),
            ("Continuity", f"IFCREAL({float(wall.continuity):.6f})"),
            ("WallThickness", f"IFCLENGTHMEASURE({float(wall.thickness):.6f})"),
        ])

    for slab in slabs:
        zbase = float(slab.z) - (float(slab.thickness) if slab.kind.lower() == "ceiling" else 0.0)
        place = _placement(w, storey_place, 0.0, 0.0, zbase, 1.0, 0.0, zdir)
        shape = _polygon_shape(w, context, slab.footprint, slab.thickness, zdir, xdir, origin3)
        if shape is None:
            continue
        predefined = ".FLOOR." if slab.kind.lower() == "floor" else ".ROOF."
        entity = w.add(f"IFCSLAB({_q(_guid())},#{owner},{_q(slab.kind)},$,$,#{place},#{shape},$,{predefined})")
        contained.append(entity)
        _pset(w, owner, entity, "Pset_FuzzyRESF_Slab", slab.confidence, slab.kind)

    for opening in openings:
        wall = wall_data.get(opening.wall_id)
        wall_entity = wall_entities.get(opening.wall_id)
        if wall is None or wall_entity is None:
            continue
        (nx, ny), (tvx, tvy) = _frame(wall.theta_deg)
        p1t = wall.x1*tvx + wall.y1*tvy
        p2t = wall.x2*tvx + wall.y2*tvy
        t_at = min(p1t, p2t) + opening.s0
        ox, oy = nx*wall.d + tvx*t_at, ny*wall.d + tvy*t_at
        place = _placement(w, storey_place, ox, oy, opening.z0, tvx, tvy, zdir)
        opening_shape = _rect_shape(w, context, opening.width, wall.thickness+.06, opening.height, zdir, xdir, origin3)
        opening_entity = w.add(f"IFCOPENINGELEMENT({_q(_guid())},#{owner},{_q(opening.kind+' Opening '+str(opening.id))},$,$,#{place},#{opening_shape},$)")
        w.add(f"IFCRELVOIDSELEMENT({_q(_guid())},#{owner},$,$,#{wall_entity},#{opening_entity})")
        fill_shape = _rect_shape(w, context, opening.width, max(.04, min(float(wall.thickness), .12)), opening.height, zdir, xdir, origin3)
        if opening.kind.lower() == "door":
            fill = w.add(f"IFCDOOR({_q(_guid())},#{owner},{_q('Door_'+str(opening.id))},$,$,#{place},#{fill_shape},$,{float(opening.height):.9f},{float(opening.width):.9f})")
        else:
            fill = w.add(f"IFCWINDOW({_q(_guid())},#{owner},{_q('Window_'+str(opening.id))},$,$,#{place},#{fill_shape},$,{float(opening.height):.9f},{float(opening.width):.9f})")
        contained.append(fill)
        w.add(f"IFCRELFILLSELEMENT({_q(_guid())},#{owner},$,$,#{opening_entity},#{fill})")
        _pset(w, owner, fill, "Pset_FuzzyRESF_Opening", opening.confidence, opening.kind, [
            ("Width", f"IFCLENGTHMEASURE({float(opening.width):.6f})"),
            ("Height", f"IFCLENGTHMEASURE({float(opening.height):.6f})"),
            ("HostWallId", f"IFCLABEL({_q(str(opening.wall_id))})"),
        ])

    if contained:
        w.add(f"IFCRELCONTAINEDINSPATIALSTRUCTURE({_q(_guid())},#{owner},$,$,({','.join('#'+str(i) for i in contained)}),#{storey})")

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text = [
        "ISO-10303-21;", "HEADER;",
        "FILE_DESCRIPTION(('ViewDefinition [CoordinationView_V2.0]'),'2;1');",
        f"FILE_NAME({_q(path.name)},{_q(timestamp)},('Massimiliano Pepe','Donato Palumbo'),('G. dAnnunzio University of Chieti-Pescara'),'qFuzzyRESF 1.1','qFuzzyRESF','');",
        "FILE_SCHEMA(('IFC2X3'));", "ENDSEC;", "DATA;", *w.entities, "ENDSEC;", "END-ISO-10303-21;"
    ]
    path.write_text("\n".join(text), encoding="utf-8")
