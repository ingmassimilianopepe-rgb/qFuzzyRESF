"""Compact IFC2X3 exporter for qFuzzyRESF BIM instances.

The exporter writes volumetric IfcWallStandardCase objects and, when inferred by the
backend, explicit IfcOpeningElement + IfcDoor/IfcWindow objects with the correct void/fill
relationships.  It intentionally remains dependency-free; geometry is represented with
simple swept solids so common IFC viewers can display a real BIM model instead of linework.
"""

from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import uuid


def _ifc_guid() -> str:
    # Compact 22-character token retained for compatibility with the v0.1 writer.
    return uuid.uuid4().hex[:22]


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def write_ifc(walls, openings, path: str | Path,
              project_name: str = "Fuzzy-RESF Scan-to-BIM") -> None:
    path = Path(path)
    walls = list(walls)
    openings = list(openings)
    if not walls:
        raise ValueError("Refusing to write IFC without wall instances")

    entities: list[str] = []
    eid = 1

    def add(expr: str) -> int:
        nonlocal eid
        entities.append(f"#{eid}={expr};")
        eid += 1
        return eid - 1

    owner = add("IFCOWNERHISTORY($,$,$,.ADDED.,$,$,$,0)")
    unit_len = add("IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)")
    units = add(f"IFCUNITASSIGNMENT((#{unit_len}))")
    world_point = add("IFCCARTESIANPOINT((0.,0.,0.))")
    zdir = add("IFCDIRECTION((0.,0.,1.))")
    xdir = add("IFCDIRECTION((1.,0.,0.))")
    world_axis = add(f"IFCAXIS2PLACEMENT3D(#{world_point},#{zdir},#{xdir})")
    geom_context = add(f"IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#{world_axis},$)")

    project = add(f"IFCPROJECT({_q(_ifc_guid())},#{owner},{_q(project_name)},$,$,$,$,(#{geom_context}),#{units})")
    site_place = add(f"IFCLOCALPLACEMENT($,#{world_axis})")
    site = add(f"IFCSITE({_q(_ifc_guid())},#{owner},'Site',$,$,#{site_place},$,$,.ELEMENT.,$,$,$,$,$)")
    building_place = add(f"IFCLOCALPLACEMENT(#{site_place},#{world_axis})")
    building = add(f"IFCBUILDING({_q(_ifc_guid())},#{owner},'Building',$,$,#{building_place},$,$,.ELEMENT.,$,$,$)")
    storey_place = add(f"IFCLOCALPLACEMENT(#{building_place},#{world_axis})")
    storey = add(f"IFCBUILDINGSTOREY({_q(_ifc_guid())},#{owner},'Storey 01',$,$,#{storey_place},$,$,.ELEMENT.,0.)")
    add(f"IFCRELAGGREGATES({_q(_ifc_guid())},#{owner},$,$,#{project},(#{site}))")
    add(f"IFCRELAGGREGATES({_q(_ifc_guid())},#{owner},$,$,#{site},(#{building}))")
    add(f"IFCRELAGGREGATES({_q(_ifc_guid())},#{owner},$,$,#{building},(#{storey}))")

    def placement_at(x: float, y: float, z: float, tx: float, ty: float) -> int:
        p = add(f"IFCCARTESIANPOINT(({x:.9f},{y:.9f},{z:.9f}))")
        refdir = add(f"IFCDIRECTION(({tx:.12f},{ty:.12f},0.))")
        axis = add(f"IFCAXIS2PLACEMENT3D(#{p},#{zdir},#{refdir})")
        return add(f"IFCLOCALPLACEMENT(#{storey_place},#{axis})")

    def box_shape(length: float, depth: float, height: float) -> int:
        length = max(1e-3, float(length))
        depth = max(1e-3, float(depth))
        height = max(1e-3, float(height))
        p2 = add(f"IFCCARTESIANPOINT(({length / 2.0:.9f},0.))")
        axis2 = add(f"IFCAXIS2PLACEMENT2D(#{p2},$)")
        profile = add(f"IFCRECTANGLEPROFILEDEF(.AREA.,$,#{axis2},{length:.9f},{depth:.9f})")
        solid_axis = add(f"IFCAXIS2PLACEMENT3D(#{world_point},#{zdir},#{xdir})")
        solid = add(f"IFCEXTRUDEDAREASOLID(#{profile},#{solid_axis},#{zdir},{height:.9f})")
        rep = add(f"IFCSHAPEREPRESENTATION(#{geom_context},'Body','SweptSolid',(#{solid}))")
        return add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")

    wall_ifc_by_id: dict[int, int] = {}
    spatial_products: list[int] = []

    for w in walls:
        dx = float(w.x2 - w.x1)
        dy = float(w.y2 - w.y1)
        length = max(1e-3, math.hypot(dx, dy))
        height = max(1e-3, float(w.z1 - w.z0))
        tx, ty = dx / length, dy / length
        placement = placement_at(float(w.x1), float(w.y1), float(w.z0), tx, ty)
        shape = box_shape(length, float(w.thickness), height)
        wall = add(
            f"IFCWALLSTANDARDCASE({_q(_ifc_guid())},#{owner},{_q('Wall_' + str(w.id))},"
            f"$,$,#{placement},#{shape},$)"
        )
        wall_ifc_by_id[int(w.id)] = wall
        spatial_products.append(wall)

        p_conf = add(f"IFCPROPERTYSINGLEVALUE('FinalConfidence',$,IFCREAL({float(w.confidence):.6f}),$)")
        p_resf = add(f"IFCPROPERTYSINGLEVALUE('RESFSupport',$,IFCREAL({float(w.area_support):.6f}),$)")
        p_occ = add(f"IFCPROPERTYSINGLEVALUE('OcclusionEvidence',$,IFCREAL({float(w.occlusion_support):.6f}),$)")
        p_cont = add(f"IFCPROPERTYSINGLEVALUE('Continuity',$,IFCREAL({float(w.continuity):.6f}),$)")
        p_state = add(f"IFCPROPERTYSINGLEVALUE('ClassificationState',$,IFCLABEL({_q(w.state)}),$)")
        p_version = add("IFCPROPERTYSINGLEVALUE('AlgorithmVersion',$,IFCLABEL('Fuzzy-RESF 1.1-resf-bim'),$)")
        pset = add(
            f"IFCPROPERTYSET({_q(_ifc_guid())},#{owner},'Pset_FuzzyRESF',$,"
            f"(#{p_conf},#{p_resf},#{p_occ},#{p_cont},#{p_state},#{p_version}))"
        )
        add(f"IFCRELDEFINESBYPROPERTIES({_q(_ifc_guid())},#{owner},$,$,(#{wall}),#{pset})")

    wall_obj_by_id = {int(w.id): w for w in walls}
    for o in openings:
        wall_obj = wall_obj_by_id.get(int(o.wall_id))
        wall_ifc = wall_ifc_by_id.get(int(o.wall_id))
        if wall_obj is None or wall_ifc is None:
            continue

        dx = float(wall_obj.x2 - wall_obj.x1)
        dy = float(wall_obj.y2 - wall_obj.y1)
        length = max(1e-6, math.hypot(dx, dy))
        tx, ty = dx / length, dy / length
        # offset is measured from the first wall endpoint along the wall axis.
        center_offset = float(o.offset)
        start_offset = max(0.0, center_offset - float(o.width) / 2.0)
        ox = float(wall_obj.x1) + tx * start_offset
        oy = float(wall_obj.y1) + ty * start_offset
        oz = float(wall_obj.z0) + float(o.sill)

        opening_place = placement_at(ox, oy, oz, tx, ty)
        opening_shape = box_shape(float(o.width), max(0.12, float(wall_obj.thickness) * 1.35), float(o.height))
        opening = add(
            f"IFCOPENINGELEMENT({_q(_ifc_guid())},#{owner},{_q('Opening_' + str(o.id))},"
            f"$,$,#{opening_place},#{opening_shape},$)"
        )
        add(f"IFCRELVOIDSELEMENT({_q(_ifc_guid())},#{owner},$,$,#{wall_ifc},#{opening})")

        element_place = placement_at(ox, oy, oz, tx, ty)
        element_shape = box_shape(float(o.width), max(0.04, float(wall_obj.thickness) * 0.35), float(o.height))
        if str(o.kind).lower() == "door":
            element = add(
                f"IFCDOOR({_q(_ifc_guid())},#{owner},{_q('Door_' + str(o.id))},$,$,"
                f"#{element_place},#{element_shape},$,{float(o.height):.9f},{float(o.width):.9f})"
            )
        else:
            element = add(
                f"IFCWINDOW({_q(_ifc_guid())},#{owner},{_q('Window_' + str(o.id))},$,$,"
                f"#{element_place},#{element_shape},$,{float(o.height):.9f},{float(o.width):.9f})"
            )
        spatial_products.append(element)
        add(f"IFCRELFILLSELEMENT({_q(_ifc_guid())},#{owner},$,$,#{opening},#{element})")

        p_conf = add(f"IFCPROPERTYSINGLEVALUE('DetectionConfidence',$,IFCREAL({float(o.confidence):.6f}),$)")
        p_host = add(f"IFCPROPERTYSINGLEVALUE('HostWallId',$,IFCINTEGER({int(o.wall_id)}),$)")
        p_kind = add(f"IFCPROPERTYSINGLEVALUE('OpeningKind',$,IFCLABEL({_q(o.kind)}),$)")
        pset = add(
            f"IFCPROPERTYSET({_q(_ifc_guid())},#{owner},'Pset_FuzzyRESF_Opening',$,"
            f"(#{p_conf},#{p_host},#{p_kind}))"
        )
        add(f"IFCRELDEFINESBYPROPERTIES({_q(_ifc_guid())},#{owner},$,$,(#{element}),#{pset})")

    if spatial_products:
        refs = ",".join("#" + str(i) for i in spatial_products)
        add(f"IFCRELCONTAINEDINSPATIALSTRUCTURE({_q(_ifc_guid())},#{owner},$,$,({refs}),#{storey})")

    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    content = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');",
        f"FILE_NAME({_q(path.name)},{_q(timestamp)},('Massimiliano Pepe','Donato Palumbo'),"
        "('G. dAnnunzio University of Chieti-Pescara'),'qFuzzyRESF','qFuzzyRESF','');",
        "FILE_SCHEMA(('IFC2X3'));",
        "ENDSEC;",
        "DATA;",
        *entities,
        "ENDSEC;",
        "END-ISO-10303-21;",
    ]
    text = "\n".join(content)
    if "IFCWALLSTANDARDCASE" not in text:
        raise RuntimeError("IFC validation failed: no wall entities")
    path.write_text(text, encoding="utf-8")
    if path.stat().st_size <= 0:
        path.unlink(missing_ok=True)
        raise RuntimeError("IFC validation failed: empty file")
