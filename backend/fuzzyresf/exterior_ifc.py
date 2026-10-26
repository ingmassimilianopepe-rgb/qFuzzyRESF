"""IFC4 swept solids in each measured facade/roof plane."""
import uuid
import numpy as np
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.geom
import ifcopenshell.validate
import trimesh
from shapely.geometry import shape
from shapely.geometry.polygon import orient


def build_exterior_ifc(I, output):
    f = ifcopenshell.file(schema="IFC4")
    api = ifcopenshell.api.run
    ns = uuid.UUID("68629214-370a-4429-a25d-66837031ea06")

    def entity(cls, name):
        e = api("root.create_entity", f, ifc_class=cls, name=name)
        e.GlobalId = ifcopenshell.guid.compress(uuid.uuid5(ns, I["source_sha256"] + name).hex)
        return e

    def props(e, values):
        p = api("pset.add_pset", f, product=e, name="ScanToBIM_Evidence")
        api("pset.edit_pset", f, pset=p, properties=values)

    def point(p):
        return f.create_entity("IfcCartesianPoint", Coordinates=tuple(map(float, p)))

    def direction(v):
        return f.create_entity("IfcDirection", DirectionRatios=tuple(map(float, v)))

    def curve(coords):
        return f.create_entity("IfcPolyline", Points=[point(p) for p in coords])

    project = entity("IfcProject", "qFuzzyRESF 0.2.0 | Involucro esterno")
    api("unit.assign_unit", f, units=[api("unit.add_si_unit", f, unit_type=t)
                                     for t in ["LENGTHUNIT", "AREAUNIT", "VOLUMEUNIT"]])
    context = api("context.add_context", f, context_type="Model")
    body = api("context.add_context", f, context_type="Model", context_identifier="Body",
               target_view="MODEL_VIEW", parent=context)
    site = entity("IfcSite", "Sito; CRS non specificato")
    building = entity("IfcBuilding", "Involucro parziale rilevato")
    for parent, child in [(project, site), (site, building)]:
        api("aggregate.assign_object", f, products=[child], relating_object=parent)
        api("geometry.edit_object_placement", f, product=child, matrix=np.eye(4))
    props(project, {
        "MethodVersion": "0.4 pilot", "ToolVersion": "0.2.0", "Mode": "EXTERIOR",
        "SourceSHA256": I["source_sha256"], "ReferenceUsedForReconstruction": False,
        "ReviewRequired": True, "Scope": I["scope"], "RoomsStatus": "NOT RECONSTRUCTED",
        "StoreysStatus": "NOT INFERRED", "BuildingEnvelopeClosed": False,
        "FuzzyScoreMeaning": "Expert support index; not calibrated probability",
        "ThicknessEvidence": "ASSUMED for all elements",
        "OpeningsStatus": "Not classified; observed large holes retained where supported",
    })
    T = np.eye(4)
    T[:3, 3] = I["coordinate_origin_m"]
    for s in I["surfaces"]:
        cls = s["ifc_class"]
        name = s["id"] + (" Parete esterna candidata" if cls == "IfcWall" else "Falda candidata")
        e = entity(cls, name)
        e.PredefinedType = "NOTDEFINED"
        api("spatial.assign_container", f, products=[e], relating_structure=building)
        api("geometry.edit_object_placement", f, product=e, matrix=T)
        polygon = orient(shape(s["polygon"]), sign=1.0)
        attributes = {"ProfileType": "AREA", "OuterCurve": curve(polygon.exterior.coords)}
        if polygon.interiors:
            attributes["InnerCurves"] = [curve(r.coords) for r in polygon.interiors]
        profile = f.create_entity("IfcArbitraryProfileDefWithVoids" if polygon.interiors
                                  else "IfcArbitraryClosedProfileDef", **attributes)
        origin = np.array(s["plane_origin"]) - np.array(s["normal"]) * s["thickness_m"] / 2
        solid = f.create_entity("IfcExtrudedAreaSolid", SweptArea=profile,
            Position=f.create_entity("IfcAxis2Placement3D", Location=point(origin),
                                     Axis=direction(s["normal"]), RefDirection=direction(s["basis_u"])),
            ExtrudedDirection=direction([0, 0, 1]), Depth=float(s["thickness_m"]))
        rep = f.create_entity("IfcShapeRepresentation", ContextOfItems=body,
                              RepresentationIdentifier="Body", RepresentationType="SweptSolid", Items=[solid])
        api("geometry.assign_representation", f, product=e, representation=rep)
        style = api("style.add_style", f, name=name)
        color = (.68, .66, .57) if cls == "IfcWall" else (.65, .28, .20)
        api("style.add_surface_style", f, style=style,
            attributes={"SurfaceColour": dict(zip(["Red", "Green", "Blue"], color)), "Transparency": 0.0})
        api("style.assign_representation_styles", f, shape_representation=rep, styles=[style])
        props(e, {"MethodVersion": "0.4", "ReviewRequired": True,
                  "SourceSHA256": I["source_sha256"], "SupportScore": s["score"],
                  "ResidualP95_m": s["P95_m"], "SupportPoints": s["support_points"],
                  "Thickness_m": s["thickness_m"], "ThicknessEvidence": s["thickness_status"],
                  "InclinationFromHorizontal_deg": s["inclination_from_horizontal_deg"],
                  "Area_m2": polygon.area, "BoundaryEvidence": s["boundary_status"],
                  "ClassificationEvidence": s["classification_status"],
                  "Holes": "Unclassified gaps: openings or missing observations",
                  "EnvelopeConnection": "Not joined; intersections and gaps require review"})
    f.write(str(output))
    m = ifcopenshell.open(str(output))
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(m, logger, express_rules=True)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    meshes, failures = [], []
    for e in m.by_type("IfcElement"):
        try:
            sh = ifcopenshell.geom.create_shape(settings, e)
            g = sh.geometry
            mesh = trimesh.Trimesh(vertices=np.array(g.verts).reshape(-1, 3),
                                   faces=np.array(g.faces).reshape(-1, 3), process=False)
            rec = {"guid": e.GlobalId, "name": e.Name, "class": e.is_a(),
                   "watertight": bool(mesh.is_watertight),
                   "volume_m3": float(mesh.volume), "area_m2": float(mesh.area)}
            meshes.append(rec)
        except Exception as exc:
            failures.append({"guid": e.GlobalId, "error": str(exc)})
    errors = [r for r in logger.statements if r.get("level") in {"error", "critical"}]
    if errors or failures or any(not r["watertight"] or r["volume_m3"] <= 0 for r in meshes):
        raise ValueError("Validazione IFC esterno fallita: schema o solidi non validi. "
                         + str(errors[:1] or failures[:1] or [r for r in meshes if not r["watertight"] or r["volume_m3"] <= 0][:1]))
    return {"schema": "IFC4", "mode": "exterior",
            "counts": {c: len(m.by_type(c)) for c in ["IfcWall", "IfcRoof", "IfcSlab", "IfcCovering",
                        "IfcSpace", "IfcBuildingStorey", "IfcOpeningElement", "IfcDoor", "IfcWindow"]},
            "schema_messages": logger.statements, "geometry_failures": failures, "meshes": meshes,
            "space_area_m2": 0.0, "space_side_boundary_length_m": {},
            "building_envelope_closed": False, "surface_intersections_checked": False,
            "validation_scope": "EXPRESS and individually closed solids; no building topology or semantic certification"}
