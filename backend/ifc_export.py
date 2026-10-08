"""Minimal IFC2X3 exporter for qFuzzyRESF wall hypotheses.

The exporter intentionally uses a compact STEP writer so that v0.1 has no hard dependency
on IfcOpenShell. Future releases may switch to IfcOpenShell while preserving the same output
semantics and Pset_FuzzyRESF provenance fields.
"""

from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import uuid


def _ifc_guid() -> str:
    # v0.1 uses a deterministic-length UUID token rather than the compressed IFC GUID.
    # Most viewers accept quoted GlobalIds, but the production exporter will use proper compression.
    return uuid.uuid4().hex[:22]


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def write_ifc(walls, path: str | Path, project_name: str = "Fuzzy-RESF Scan-to-BIM") -> None:
    path = Path(path)
    entities = []
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

    wall_ids = []
    for w in walls:
        dx = float(w.x2 - w.x1)
        dy = float(w.y2 - w.y1)
        length = max(1e-3, math.hypot(dx, dy))
        height = max(1e-3, float(w.z1 - w.z0))
        tx, ty = dx / length, dy / length

        origin = add(f"IFCCARTESIANPOINT(({w.x1:.9f},{w.y1:.9f},{w.z0:.9f}))")
        refdir = add(f"IFCDIRECTION(({tx:.12f},{ty:.12f},0.))")
        local_axis = add(f"IFCAXIS2PLACEMENT3D(#{origin},#{zdir},#{refdir})")
        placement = add(f"IFCLOCALPLACEMENT(#{storey_place},#{local_axis})")
        p2 = add(f"IFCCARTESIANPOINT(({length/2:.9f},0.))")
        axis2 = add(f"IFCAXIS2PLACEMENT2D(#{p2},$)")
        profile = add(f"IFCRECTANGLEPROFILEDEF(.AREA.,$ ,#{axis2},{length:.9f},{float(w.thickness):.9f})")
        solid_axis = add(f"IFCAXIS2PLACEMENT3D(#{world_point},#{zdir},#{xdir})")
        solid = add(f"IFCEXTRUDEDAREASOLID(#{profile},#{solid_axis},#{zdir},{height:.9f})")
        shape_rep = add(f"IFCSHAPEREPRESENTATION(#{geom_context},'Body','SweptSolid',(#{solid}))")
        shape = add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{shape_rep}))")
        wall = add(f"IFCWALLSTANDARDCASE({_q(_ifc_guid())},#{owner},{_q('Wall_'+str(w.id))},$,$,#{placement},#{shape},$)")
        wall_ids.append(wall)

        # Provenance property set
        p_conf = add(f"IFCPROPERTYSINGLEVALUE('FinalConfidence',$,IFCREAL({float(w.confidence):.6f}),$)")
        p_resf = add(f"IFCPROPERTYSINGLEVALUE('RESFSupport',$,IFCREAL({float(w.area_support):.6f}),$)")
        p_occ = add(f"IFCPROPERTYSINGLEVALUE('OcclusionEvidence',$,IFCREAL({float(w.occlusion_support):.6f}),$)")
        p_state = add(f"IFCPROPERTYSINGLEVALUE('ClassificationState',$,IFCLABEL({_q(w.state)}),$)")
        p_version = add("IFCPROPERTYSINGLEVALUE('AlgorithmVersion',$,IFCLABEL('Fuzzy-RESF 1.0'),$)")
        pset = add(f"IFCPROPERTYSET({_q(_ifc_guid())},#{owner},'Pset_FuzzyRESF',$,(#{p_conf},#{p_resf},#{p_occ},#{p_state},#{p_version}))")
        add(f"IFCRELDEFINESBYPROPERTIES({_q(_ifc_guid())},#{owner},$,$,(#{wall}),#{pset})")

    if wall_ids:
        add(f"IFCRELCONTAINEDINSPATIALSTRUCTURE({_q(_ifc_guid())},#{owner},$,$,({','.join('#'+str(i) for i in wall_ids)}),#{storey})")

    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    content = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');",
        f"FILE_NAME({_q(path.name)},{_q(timestamp)},('Massimiliano Pepe','Donato Palumbo'),('G. dAnnunzio University of Chieti-Pescara'),'qFuzzyRESF','qFuzzyRESF','');",
        "FILE_SCHEMA(('IFC2X3'));",
        "ENDSEC;",
        "DATA;",
        *entities,
        "ENDSEC;",
        "END-ISO-10303-21;",
    ]
    path.write_text("\n".join(content), encoding="utf-8")
