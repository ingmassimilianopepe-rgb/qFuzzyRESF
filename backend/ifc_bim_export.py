"""Semantic IFC2X3 exporter for Fuzzy-RESF-BIM v2.

Exports an actual object model: IfcBuildingStorey, IfcWallStandardCase,
IfcOpeningElement, IfcDoor, IfcWindow, IfcSlab/IfcCovering and IfcSpace.
Openings are related to host walls with IfcRelVoidsElement and filled by doors/windows
with IfcRelFillsElement.  Geometry uses simple swept solids for broad viewer support.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import math
import uuid


_IFC64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def _guid() -> str:
    # 128-bit UUID encoded into 22 IFC-safe characters.  This is deterministic in
    # length/charset and avoids the invalid hexadecimal GlobalIds of the v0.1 writer.
    n = uuid.uuid4().int
    chars = []
    for _ in range(22):
        chars.append(_IFC64[n & 63])
        n >>= 6
    return "".join(reversed(chars))


def _q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def write_bim_ifc(model, path: str | Path, project_name: str = "Fuzzy-RESF-BIM") -> None:
    path = Path(path)
    if not model.walls:
        raise ValueError("Refusing IFC export: the BIM contains no wall objects")

    ent: list[str] = []
    eid = 1

    def add(expr: str) -> int:
        nonlocal eid
        ent.append(f"#{eid}={expr};")
        eid += 1
        return eid - 1

    owner = add("IFCOWNERHISTORY($,$,$,.ADDED.,$,$,$,0)")
    unit_len = add("IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)")
    units = add(f"IFCUNITASSIGNMENT((#{unit_len}))")
    origin3 = add("IFCCARTESIANPOINT((0.,0.,0.))")
    zdir = add("IFCDIRECTION((0.,0.,1.))")
    xdir = add("IFCDIRECTION((1.,0.,0.))")
    world_axis = add(f"IFCAXIS2PLACEMENT3D(#{origin3},#{zdir},#{xdir})")
    context = add(f"IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#{world_axis},$)")

    project = add(f"IFCPROJECT({_q(_guid())},#{owner},{_q(project_name)},$,$,$,$,(#{context}),#{units})")
    site_place = add(f"IFCLOCALPLACEMENT($,#{world_axis})")
    site = add(f"IFCSITE({_q(_guid())},#{owner},'Site',$,$,#{site_place},$,$,.ELEMENT.,$,$,$,$,$)")
    building_place = add(f"IFCLOCALPLACEMENT(#{site_place},#{world_axis})")
    building = add(f"IFCBUILDING({_q(_guid())},#{owner},'Building',$,$,#{building_place},$,$,.ELEMENT.,$,$,$)")
    add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{project},(#{site}))")
    add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{site},(#{building}))")

    storey_ifc: dict[int, int] = {}
    storey_place: dict[int, int] = {}
    for s in model.storeys:
        # Geometry is kept in the source cloud coordinate system. The Elevation field
        # records the storey level while the placement remains at the building origin.
        place = add(f"IFCLOCALPLACEMENT(#{building_place},#{world_axis})")
        st = add(f"IFCBUILDINGSTOREY({_q(_guid())},#{owner},{_q('Storey_'+str(s.id))},$,$,#{place},$,$,.ELEMENT.,{float(s.z0):.9f})")
        storey_ifc[int(s.id)] = st
        storey_place[int(s.id)] = place
        add(f"IFCRELAGGREGATES({_q(_guid())},#{owner},$,$,#{building},(#{st}))")

    def placement_xyz(x: float, y: float, z: float, tx: float = 1.0, ty: float = 0.0,
                      parent: int | None = None) -> int:
        p = add(f"IFCCARTESIANPOINT(({x:.9f},{y:.9f},{z:.9f}))")
        ref = add(f"IFCDIRECTION(({tx:.12f},{ty:.12f},0.))")
        axis = add(f"IFCAXIS2PLACEMENT3D(#{p},#{zdir},#{ref})")
        if parent is None:
            parent = building_place
        return add(f"IFCLOCALPLACEMENT(#{parent},#{axis})")

    def rectangle_shape(length: float, depth: float, height: float) -> int:
        length = max(float(length), 1e-3)
        depth = max(float(depth), 1e-3)
        height = max(float(height), 1e-3)
        p2 = add(f"IFCCARTESIANPOINT(({length/2.0:.9f},0.))")
        ax2 = add(f"IFCAXIS2PLACEMENT2D(#{p2},$)")
        profile = add(f"IFCRECTANGLEPROFILEDEF(.AREA.,$,#{ax2},{length:.9f},{depth:.9f})")
        solid_axis = add(f"IFCAXIS2PLACEMENT3D(#{origin3},#{zdir},#{xdir})")
        solid = add(f"IFCEXTRUDEDAREASOLID(#{profile},#{solid_axis},#{zdir},{height:.9f})")
        rep = add(f"IFCSHAPEREPRESENTATION(#{context},'Body','SweptSolid',(#{solid}))")
        return add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")

    def polygon_shape(poly: list[list[float]], height: float) -> tuple[int, float, float]:
        if len(poly) < 3:
            raise ValueError("Polygon needs at least three vertices")
        ox, oy = float(poly[0][0]), float(poly[0][1])
        refs = []
        for x, y in poly:
            refs.append(add(f"IFCCARTESIANPOINT(({float(x)-ox:.9f},{float(y)-oy:.9f}))"))
        refs.append(refs[0])
        pline = add(f"IFCPOLYLINE(({','.join('#'+str(r) for r in refs)}))")
        profile = add(f"IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,$,#{pline})")
        solid_axis = add(f"IFCAXIS2PLACEMENT3D(#{origin3},#{zdir},#{xdir})")
        solid = add(f"IFCEXTRUDEDAREASOLID(#{profile},#{solid_axis},#{zdir},{max(float(height),1e-3):.9f})")
        rep = add(f"IFCSHAPEREPRESENTATION(#{context},'Body','SweptSolid',(#{solid}))")
        shape = add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")
        return shape, ox, oy

    contained: dict[int, list[int]] = {int(s.id): [] for s in model.storeys}
    wall_ifc: dict[int, int] = {}
    wall_by_id = {int(w.id): w for w in model.walls}

    for w in model.walls:
        dx, dy = float(w.x2-w.x1), float(w.y2-w.y1)
        length = max(math.hypot(dx, dy), 1e-6)
        tx, ty = dx/length, dy/length
        place = placement_xyz(w.x1, w.y1, w.z0, tx, ty, storey_place.get(int(w.storey_id)))
        shape = rectangle_shape(length, w.thickness, w.z1-w.z0)
        wall = add(f"IFCWALLSTANDARDCASE({_q(_guid())},#{owner},{_q('Wall_'+str(w.id))},$,$,#{place},#{shape},$)")
        wall_ifc[int(w.id)] = wall
        contained.setdefault(int(w.storey_id), []).append(wall)

        props = [
            add(f"IFCPROPERTYSINGLEVALUE('ObjectConfidence',$,IFCREAL({float(w.confidence):.6f}),$)"),
            add(f"IFCPROPERTYSINGLEVALUE('ObservedSupport',$,IFCREAL({float(w.support):.6f}),$)"),
            add(f"IFCPROPERTYSINGLEVALUE('Continuity',$,IFCREAL({float(w.continuity):.6f}),$)"),
            add(f"IFCPROPERTYSINGLEVALUE('ReconstructionSource',$,IFCLABEL({_q(w.source)}),$)"),
            add("IFCPROPERTYSINGLEVALUE('AlgorithmVersion',$,IFCLABEL('Fuzzy-RESF-BIM 2.0'),$)"),
        ]
        pset = add(f"IFCPROPERTYSET({_q(_guid())},#{owner},'Pset_FuzzyRESF_BIM',$,({','.join('#'+str(p) for p in props)}))")
        add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{wall}),#{pset})")

    for o in model.openings:
        w = wall_by_id.get(int(o.wall_id))
        host = wall_ifc.get(int(o.wall_id))
        if w is None or host is None:
            continue
        dx, dy = float(w.x2-w.x1), float(w.y2-w.y1)
        length = max(math.hypot(dx, dy), 1e-6)
        tx, ty = dx/length, dy/length
        start = max(0.0, float(o.offset) - float(o.width)/2.0)
        x = float(w.x1) + tx*start
        y = float(w.y1) + ty*start
        z = float(w.z0) + float(o.sill)
        parent = storey_place.get(int(o.storey_id))
        op_place = placement_xyz(x, y, z, tx, ty, parent)
        op_shape = rectangle_shape(o.width, max(0.12, 1.35*w.thickness), o.height)
        op = add(f"IFCOPENINGELEMENT({_q(_guid())},#{owner},{_q('Opening_'+str(o.id))},$,$,#{op_place},#{op_shape},$)")
        add(f"IFCRELVOIDSELEMENT({_q(_guid())},#{owner},$,$,#{host},#{op})")

        fill_place = placement_xyz(x, y, z, tx, ty, parent)
        fill_shape = rectangle_shape(o.width, max(0.035, 0.30*w.thickness), o.height)
        if str(o.kind).lower() == "door":
            obj = add(f"IFCDOOR({_q(_guid())},#{owner},{_q('Door_'+str(o.id))},$,$,#{fill_place},#{fill_shape},$,{float(o.height):.9f},{float(o.width):.9f})")
        else:
            obj = add(f"IFCWINDOW({_q(_guid())},#{owner},{_q('Window_'+str(o.id))},$,$,#{fill_place},#{fill_shape},$,{float(o.height):.9f},{float(o.width):.9f})")
        contained.setdefault(int(o.storey_id), []).append(obj)
        add(f"IFCRELFILLSELEMENT({_q(_guid())},#{owner},$,$,#{op},#{obj})")
        p_conf = add(f"IFCPROPERTYSINGLEVALUE('ObjectConfidence',$,IFCREAL({float(o.confidence):.6f}),$)")
        p_host = add(f"IFCPROPERTYSINGLEVALUE('HostWallId',$,IFCINTEGER({int(o.wall_id)}),$)")
        pset = add(f"IFCPROPERTYSET({_q(_guid())},#{owner},'Pset_FuzzyRESF_Opening',$,(#{p_conf},#{p_host}))")
        add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{obj}),#{pset})")

    for slab in model.slabs:
        if len(slab.polygon) < 3:
            continue
        shape, ox, oy = polygon_shape(slab.polygon, slab.thickness)
        z = slab.z - slab.thickness if slab.kind.upper() == "CEILING" else slab.z
        place = placement_xyz(ox, oy, z, 1.0, 0.0, storey_place.get(int(slab.storey_id)))
        if slab.kind.upper() == "CEILING":
            obj = add(f"IFCCOVERING({_q(_guid())},#{owner},{_q('Ceiling_'+str(slab.id))},$,$,#{place},#{shape},$,.CEILING.)")
        else:
            obj = add(f"IFCSLAB({_q(_guid())},#{owner},{_q('Floor_'+str(slab.id))},$,$,#{place},#{shape},$,.FLOOR.)")
        contained.setdefault(int(slab.storey_id), []).append(obj)

    for space in model.spaces:
        if len(space.polygon) < 3:
            continue
        shape, ox, oy = polygon_shape(space.polygon, max(0.10, space.z1-space.z0))
        place = placement_xyz(ox, oy, space.z0, 1.0, 0.0, storey_place.get(int(space.storey_id)))
        obj = add(f"IFCSPACE({_q(_guid())},#{owner},{_q(space.name)},$,$,#{place},#{shape},$,.ELEMENT.,.INTERNAL.,$)")
        contained.setdefault(int(space.storey_id), []).append(obj)
        p_area = add(f"IFCPROPERTYSINGLEVALUE('ReconstructedArea',$,IFCAREAMEASURE({float(space.area):.6f}),$)")
        p_conf = add(f"IFCPROPERTYSINGLEVALUE('ObjectConfidence',$,IFCREAL({float(space.confidence):.6f}),$)")
        pset = add(f"IFCPROPERTYSET({_q(_guid())},#{owner},'Pset_FuzzyRESF_Space',$,(#{p_area},#{p_conf}))")
        add(f"IFCRELDEFINESBYPROPERTIES({_q(_guid())},#{owner},$,$,(#{obj}),#{pset})")

    for sid, products in contained.items():
        st = storey_ifc.get(sid)
        if st is None or not products:
            continue
        refs = ",".join("#"+str(p) for p in products)
        add(f"IFCRELCONTAINEDINSPATIALSTRUCTURE({_q(_guid())},#{owner},$,$,({refs}),#{st})")

    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    body = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');",
        f"FILE_NAME({_q(path.name)},{_q(timestamp)},('Massimiliano Pepe','Donato Palumbo'),('G. dAnnunzio University of Chieti-Pescara'),'qFuzzyRESF','qFuzzyRESF','');",
        "FILE_SCHEMA(('IFC2X3'));",
        "ENDSEC;",
        "DATA;",
        *ent,
        "ENDSEC;",
        "END-ISO-10303-21;",
    ]
    text = "\n".join(body)
    required = ["IFCWALLSTANDARDCASE", "IFCBUILDINGSTOREY"]
    for token in required:
        if token not in text:
            raise RuntimeError(f"IFC validation failed: {token} missing")
    path.write_text(text, encoding="utf-8")
    if path.stat().st_size < 1024:
        path.unlink(missing_ok=True)
        raise RuntimeError("IFC validation failed: file unexpectedly small")
