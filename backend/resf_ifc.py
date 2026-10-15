"""IFC4 export for the auditable, stepwise RESF-FUZZY object model.

All model dimensions are metres in one local Cartesian frame. Opening.offset is
the centre distance from the host wall's first endpoint; sill is relative to the
wall base. Floor.z is its observed top surface, Ceiling.z its observed underside.
Door/window assembly geometry is deliberately unspecified: opening evidence does
not measure leaf, glazing or frame thickness. The host void *is* modelled and its
subtraction is checked using IfcOpenShell's geometry engine.

The result is reopened, checked against IFC4 schema/EXPRESS rules, and every
solid is tessellated before the requested output path is replaced atomically.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile

import numpy as np
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union


def _number(value, label: str, positive: bool = False) -> float:
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{label} must be {'positive and ' if positive else ''}finite")
    return value


def _accepted(item) -> bool:
    return str(getattr(item, "state", "Permanent")).lower() in {
        "permanent", "occludedpermanent", "accepted", "confirmed"
    }


def write_semantic_ifc(model, path: str | Path,
                       project_name: str = "Fuzzy RESF Semantic") -> dict:
    """Write and validate IFC4; return JSON-serializable validation diagnostics.

    Raises ValueError for malformed accepted geometry and RuntimeError for an IFC
    schema/geometry failure. Uncertain/rejected objects and unrecognised opening
    kinds are skipped with explicit reasons. An existing file is kept on failure.
    Optional ``model.provenance`` maps ``Wall:1``, ``Opening:2``, ``Slab:3``, etc.
    to dictionaries; these are preserved verbatim as JSON property values.
    Optional ``model.coordinate_frame`` records origin/scale and can request the
    global origin in site placement using ``restore_global_coordinates=True``.
    It never declares a CRS that was not supplied by the surveyor.
    """
    try:
        import ifcopenshell
        import ifcopenshell.geom
        import ifcopenshell.util.shape
        import ifcopenshell.validate
    except ImportError as exc:
        raise RuntimeError("IFC4 export requires ifcopenshell>=0.8,<0.9") from exc

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = ifcopenshell.file(schema="IFC4")
    guid = ifcopenshell.guid.new
    skipped = []
    provenance = getattr(model, "provenance", {}) or {}
    frame = dict(getattr(model, "coordinate_frame", {}) or {})
    origin = frame.get("origin", [0.0, 0.0, 0.0])
    if len(origin) != 3:
        raise ValueError("coordinate_frame.origin must contain three coordinates")
    origin = tuple(_number(v, "coordinate origin") for v in origin)
    restore_origin = bool(frame.get("restore_global_coordinates", False))

    def point(values):
        return f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(x) for x in values))

    def direction(values):
        return f.create_entity("IfcDirection", DirectionRatios=tuple(float(x) for x in values))

    def axis(xyz=(0.0, 0.0, 0.0), tangent=(1.0, 0.0, 0.0)):
        return f.create_entity("IfcAxis2Placement3D", Location=point(xyz),
                               Axis=direction((0, 0, 1)), RefDirection=direction(tangent))

    def placement(parent=None, xyz=(0.0, 0.0, 0.0), tangent=(1.0, 0.0, 0.0)):
        return f.create_entity("IfcLocalPlacement", PlacementRelTo=parent,
                               RelativePlacement=axis(xyz, tangent))

    context = f.create_entity("IfcGeometricRepresentationContext", ContextIdentifier="Model",
                              ContextType="Model", CoordinateSpaceDimension=3,
                              Precision=1e-6, WorldCoordinateSystem=axis())
    body = f.create_entity("IfcGeometricRepresentationSubContext", ContextIdentifier="Body",
                           ContextType="Model", ParentContext=context, TargetView="MODEL_VIEW")
    units = f.create_entity("IfcUnitAssignment", Units=[
        f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE"),
        f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"),
        f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE"),
        f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN"),
    ])
    project = f.create_entity("IfcProject", GlobalId=guid(), Name=str(project_name),
                              RepresentationContexts=[context], UnitsInContext=units)
    site = f.create_entity("IfcSite", GlobalId=guid(), Name="Survey site",
                           ObjectPlacement=placement(xyz=origin if restore_origin else (0, 0, 0)),
                           CompositionType="ELEMENT")
    building = f.create_entity("IfcBuilding", GlobalId=guid(), Name="Reconstructed building",
                               ObjectPlacement=placement(site.ObjectPlacement), CompositionType="ELEMENT")

    def aggregate(parent, children):
        if children:
            f.create_entity("IfcRelAggregates", GlobalId=guid(), RelatingObject=parent,
                            RelatedObjects=list(children))

    def pset(obj, name, props):
        values = []
        for key, value in props.items():
            if value is None:
                continue
            if isinstance(value, (bool, np.bool_)):
                nominal = f.create_entity("IfcBoolean", bool(value))
            elif isinstance(value, (int, np.integer)):
                nominal = f.create_entity("IfcInteger", int(value))
            elif isinstance(value, (float, np.floating)):
                nominal = f.create_entity("IfcReal", _number(value, str(key)))
            else:
                if isinstance(value, (dict, list, tuple)):
                    value = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                       default=lambda x: x.item() if isinstance(x, np.generic) else str(x))
                nominal = f.create_entity("IfcText", str(value))
            values.append(f.create_entity("IfcPropertySingleValue", Name=str(key), NominalValue=nominal))
        if values:
            prop_set = f.create_entity("IfcPropertySet", GlobalId=guid(), Name=name, HasProperties=values)
            f.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[obj],
                            RelatingPropertyDefinition=prop_set)

    def object_properties(obj, item, kind, extra=None):
        evidence = dict(getattr(item, "provenance", {}) or {})
        evidence.update(provenance.get(f"{kind}:{item.id}", {}) or {})
        props = {
            "SourceObjectId": int(item.id), "ObjectConfidence": float(item.confidence),
            "ConfidenceMeaning": "Uncalibrated fuzzy degree; not a probability of correctness",
            "ClassificationState": getattr(item, "state", "Accepted"),
            "ReconstructionSource": getattr(item, "source", "RESF-FUZZY geometric reconstruction"),
            "EvidenceProvenance": evidence,
        }
        if hasattr(item, "thickness"):
            props["ThicknessMetres"] = float(item.thickness)
            # Missing provenance is explicitly unknown; numerical dimensions do
            # not establish that opposing physical surfaces were observed.
            props["ThicknessEvidence"] = evidence.get("thickness_status", evidence.get("thickness_source", "unspecified"))
        if extra:
            props.update(extra)
        pset(obj, "Pset_FuzzyRESF_Reconstruction", props)

    aggregate(project, [site])
    aggregate(site, [building])
    pset(project, "Pset_FuzzyRESF_Coordinates", {
        "CoordinateFrame": frame,
        "GeometryUnits": "metre",
        "OriginAppliedToSite": restore_origin,
        "CRS": frame.get("crs", "unspecified"),
    })
    storeys = {}
    elevations = {}
    contained = {}
    spaces_by_storey = {}
    for storey in model.storeys:
        sid = int(storey.id)
        if sid in storeys:
            raise ValueError(f"Duplicate storey id {sid}")
        z0 = _number(storey.z0, "storey base")
        _number(storey.z1 - storey.z0, "storey height", positive=True)
        storeys[sid] = f.create_entity("IfcBuildingStorey", GlobalId=guid(), Name=f"Storey_{sid}",
                                       ObjectPlacement=placement(building.ObjectPlacement, (0, 0, z0)),
                                       CompositionType="ELEMENT", Elevation=z0)
        elevations[sid] = z0
        contained[sid] = []
        spaces_by_storey[sid] = []
        object_properties(storeys[sid], storey, "Storey")
    aggregate(building, list(storeys.values()))

    def object_placement(item, xyz, tangent=(1.0, 0.0, 0.0)):
        sid = int(item.storey_id)
        if sid not in storeys:
            raise ValueError(f"Object {item.id} refers to absent storey {sid}")
        x, y, z = (_number(v, "object coordinate") for v in xyz)
        return placement(storeys[sid].ObjectPlacement, (x, y, z - elevations[sid]), tangent)

    def solid_shape(profile, height):
        solid = f.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=axis(),
                                ExtrudedDirection=direction((0, 0, 1)),
                                Depth=_number(height, "extrusion height", positive=True))
        rep = f.create_entity("IfcShapeRepresentation", ContextOfItems=body,
                              RepresentationIdentifier="Body", RepresentationType="SweptSolid", Items=[solid])
        return f.create_entity("IfcProductDefinitionShape", Representations=[rep])

    def rectangle(length, thickness, height):
        length = _number(length, "rectangle length", positive=True)
        thickness = _number(thickness, "rectangle thickness", positive=True)
        profile = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                                  Position=f.create_entity("IfcAxis2Placement2D", Location=point((length / 2, 0))),
                                  XDim=length, YDim=thickness)
        return solid_shape(profile, height)

    def polygon_shape(item, height):
        poly = Polygon(item.polygon, holes=getattr(item, "holes", []) or [])
        if not poly.is_valid or poly.is_empty or poly.area <= 1e-9:
            raise ValueError(f"Invalid polygon on object {item.id}")
        poly = orient(poly, sign=1.0)
        ox, oy = poly.exterior.coords[0]

        def curve(ring):
            return f.create_entity("IfcPolyline", Points=[point((x - ox, y - oy)) for x, y in ring.coords])

        kwargs = {"ProfileType": "AREA", "OuterCurve": curve(poly.exterior)}
        if poly.interiors:
            profile = f.create_entity("IfcArbitraryProfileDefWithVoids", **kwargs,
                                      InnerCurves=[curve(ring) for ring in poly.interiors])
        else:
            profile = f.create_entity("IfcArbitraryClosedProfileDef", **kwargs)
        return solid_shape(profile, height), ox, oy, poly.area

    wall_objects = {}
    wall_data = {}
    expected_volumes = {}
    host_voids = {}
    solid_guids = []
    for wall in model.walls:
        if not _accepted(wall):
            skipped.append({"object": f"Wall:{wall.id}", "reason": "classification requires review"})
            continue
        wid = int(wall.id)
        if wid in wall_objects:
            raise ValueError(f"Duplicate wall id {wid}")
        dx, dy = float(wall.x2 - wall.x1), float(wall.y2 - wall.y1)
        length = _number(math.hypot(dx, dy), "wall length", positive=True)
        height = _number(wall.z1 - wall.z0, "wall height", positive=True)
        thickness = _number(wall.thickness, "wall thickness", positive=True)
        obj = f.create_entity("IfcWall", GlobalId=guid(), Name=f"Wall_{wid}",
                               ObjectPlacement=object_placement(wall, (wall.x1, wall.y1, wall.z0),
                                                                (dx / length, dy / length, 0)),
                               Representation=rectangle(length, thickness, height), PredefinedType="NOTDEFINED")
        wall_objects[wid] = obj
        wall_data[wid] = wall, length, height, thickness
        expected_volumes[obj.GlobalId] = length * thickness * height
        host_voids[wid] = []
        solid_guids.append(obj.GlobalId)
        contained[int(wall.storey_id)].append(obj)
        object_properties(obj, wall, "Wall", {
            "ObservedSupport": float(wall.support), "Continuity": float(wall.continuity),
            "RESF_A": float(wall.resf_A), "RESF_EH": float(wall.resf_EH),
            "RESF_EV": float(wall.resf_EV), "RESF_C": float(wall.resf_C),
            "RESF_N": float(wall.resf_N), "RESF_O": float(wall.resf_O),
        })

    opening_ids = set()
    for opening in model.openings:
        kind = str(opening.kind).lower()
        if not _accepted(opening) or kind not in {"door", "window", "opening", "openvoid"}:
            skipped.append({"object": f"Opening:{opening.id}", "reason": "unknown or review classification"})
            continue
        wid = int(opening.wall_id)
        if wid not in wall_objects:
            skipped.append({"object": f"Opening:{opening.id}", "reason": "host wall not accepted"})
            continue
        if int(opening.id) in opening_ids:
            raise ValueError(f"Duplicate opening id {opening.id}")
        opening_ids.add(int(opening.id))
        wall, length, wall_height, thickness = wall_data[wid]
        if int(opening.storey_id) != int(wall.storey_id):
            raise ValueError(f"Opening {opening.id} and host wall have different storeys")
        width = _number(opening.width, "opening width", positive=True)
        height = _number(opening.height, "opening height", positive=True)
        start = _number(opening.offset, "opening offset") - width / 2
        sill = _number(opening.sill, "opening sill")
        if start < -1e-6 or start + width > length + 1e-6 or sill < -1e-6 or sill + height > wall_height + 1e-6:
            raise ValueError(f"Opening {opening.id} extends outside host wall {wid}")
        host = wall_objects[wid]
        op = f.create_entity("IfcOpeningElement", GlobalId=guid(), Name=f"Opening_{opening.id}",
                              ObjectPlacement=placement(host.ObjectPlacement, (start, 0, sill)),
                              Representation=rectangle(width, thickness + 0.02, height), PredefinedType="OPENING")
        f.create_entity("IfcRelVoidsElement", GlobalId=guid(), RelatingBuildingElement=host,
                        RelatedOpeningElement=op)
        object_properties(op, opening, "Opening", {"HostWallId": wid, "OpeningKind": opening.kind})
        solid_guids.append(op.GlobalId)
        expected_volumes[op.GlobalId] = width * (thickness + 0.02) * height
        host_voids[wid].append(box(start, sill, start + width, sill + height))
        if kind in {"door", "window"}:
            cls = "IfcDoor" if kind == "door" else "IfcWindow"
            fill = f.create_entity(cls, GlobalId=guid(), Name=f"{kind.capitalize()}_{opening.id}",
                                    Description="Semantic opening fill; assembly geometry has not been reconstructed",
                                    ObjectPlacement=placement(op.ObjectPlacement),
                                    OverallWidth=width, OverallHeight=height, PredefinedType="NOTDEFINED")
            f.create_entity("IfcRelFillsElement", GlobalId=guid(), RelatingOpeningElement=op,
                            RelatedBuildingElement=fill)
            contained[int(wall.storey_id)].append(fill)
            object_properties(fill, opening, "Opening", {
                "HostWallId": wid, "AssemblyGeometry": "not_reconstructed",
                "DimensionsMeaning": "Host aperture dimensions; not measured leaf/frame dimensions",
            })

    for wid, voids in host_voids.items():
        if voids:
            expected_volumes[wall_objects[wid].GlobalId] -= unary_union(voids).area * wall_data[wid][3]

    for slab in model.slabs:
        kind = str(slab.kind).upper()
        if not _accepted(slab) or kind not in {"FLOOR", "CEILING"}:
            skipped.append({"object": f"Slab:{slab.id}", "reason": "unknown or review classification"})
            continue
        thickness = _number(slab.thickness, "slab thickness", positive=True)
        shape, ox, oy, area = polygon_shape(slab, thickness)
        is_ceiling = kind == "CEILING"
        obj = f.create_entity("IfcCovering" if is_ceiling else "IfcSlab", GlobalId=guid(),
                               Name=f"{kind.capitalize()}_{slab.id}",
                               ObjectPlacement=object_placement(slab, (ox, oy, slab.z if is_ceiling else slab.z - thickness)),
                               Representation=shape, PredefinedType=kind)
        expected_volumes[obj.GlobalId] = area * thickness
        solid_guids.append(obj.GlobalId)
        contained[int(slab.storey_id)].append(obj)
        object_properties(obj, slab, "Slab", {"SurfaceMeaning": "underside" if is_ceiling else "top"})

    for space in model.spaces:
        if not _accepted(space):
            skipped.append({"object": f"Space:{space.id}", "reason": "classification requires review"})
            continue
        height = _number(space.z1 - space.z0, "space height", positive=True)
        shape, ox, oy, area = polygon_shape(space, height)
        obj = f.create_entity("IfcSpace", GlobalId=guid(), Name=str(space.name),
                               ObjectPlacement=object_placement(space, (ox, oy, space.z0)),
                               Representation=shape, CompositionType="ELEMENT", PredefinedType="INTERNAL")
        expected_volumes[obj.GlobalId] = area * height
        solid_guids.append(obj.GlobalId)
        spaces_by_storey[int(space.storey_id)].append(obj)
        object_properties(obj, space, "Space", {"ReconstructedArea": float(area)})

    if not solid_guids:
        raise ValueError("No accepted solid BIM objects available for export")
    for sid, elements in contained.items():
        if elements:
            f.create_entity("IfcRelContainedInSpatialStructure", GlobalId=guid(),
                            RelatedElements=elements, RelatingStructure=storeys[sid])
        # IfcSpace is spatial structure, so it is decomposed under a storey;
        # it is not an ordinary contained IfcElement.
        aggregate(storeys[sid], spaces_by_storey[sid])

    diagnostics = {"schema": "IFC4", "schema_valid": False, "geometry_valid": False,
                   "ifcopenshell_version": ifcopenshell.version, "skipped": skipped,
                   "schema_errors": [], "geometry_checks": [], "origin_applied": restore_origin,
                   "door_window_geometry": "semantic fills with aperture dimensions; assembly solids unspecified"}
    with tempfile.NamedTemporaryFile(prefix=f".{path.stem}-", suffix=".ifc", dir=path.parent, delete=False) as temp:
        temporary_path = Path(temp.name)
    try:
        f.write(str(temporary_path))
        reopened = ifcopenshell.open(str(temporary_path))
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(reopened, logger, express_rules=True)
        diagnostics["schema_errors"] = [
            {"level": str(entry.get("level")), "message": str(entry.get("message")),
             "instance": str(entry.get("instance", ""))}
            for entry in logger.statements if entry.get("level") in {"error", "critical"}
        ]
        if diagnostics["schema_errors"]:
            raise RuntimeError(f"IFC4 schema validation failed: {diagnostics['schema_errors'][0]['message']}")
        diagnostics["schema_valid"] = True
        settings = ifcopenshell.geom.settings()
        # Local-space tessellation keeps the volume check stable even if a
        # survey origin is millions of metres from zero.
        for gid in solid_guids:
            obj = reopened.by_guid(gid)
            shape = ifcopenshell.geom.create_shape(settings, obj)
            actual = abs(float(ifcopenshell.util.shape.get_volume(shape.geometry)))
            expected = expected_volumes[gid]
            tolerance = max(1e-6, abs(expected) * 1e-5)
            valid = math.isfinite(actual) and actual > 0 and abs(actual - expected) <= tolerance
            diagnostics["geometry_checks"].append({"guid": gid, "name": obj.Name, "ifc_class": obj.is_a(),
                                                   "expected_volume_m3": expected, "actual_volume_m3": actual,
                                                   "valid": valid})
            if not valid:
                raise RuntimeError(f"IFC geometry validation failed for {obj.Name}: volume {actual}, expected {expected}")
        diagnostics["geometry_valid"] = True
        diagnostics["counts"] = {name: len(reopened.by_type(name)) for name in [
            "IfcWall", "IfcOpeningElement", "IfcDoor", "IfcWindow", "IfcSlab", "IfcCovering", "IfcSpace"
        ]}
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return diagnostics
