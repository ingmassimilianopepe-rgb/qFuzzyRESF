"""IFC4 parametric swept solids, evidence properties and first-level boundaries.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import json
import uuid
import collections
from pathlib import Path
import numpy as np
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.geom
import ifcopenshell.validate
from shapely.geometry import shape, LineString
from shapely.ops import unary_union
import trimesh


def build_ifc(I, output):
    out = Path(output)
    f = ifcopenshell.file(schema="IFC4")
    api = ifcopenshell.api.run
    ns = uuid.UUID("35002abc-acbe-4675-b78e-9f04eb32e804")
    R = np.array(I["rotation_world_to_uv"])
    T = np.eye(4)
    T[:2, :2] = R.T
    T[:2, 3] = (
        np.array(I["origin_xy"]) + np.array(I.get("coordinate_origin_m", [0, 0, 0]))[:2]
    )
    T[2, 3] = I.get("coordinate_origin_m", [0, 0, 0])[2]
    H = I["main_level"]["height_m"]
    zf = I["main_level"]["floor_z"]
    zc = I["main_level"]["ceiling_z"]

    def root(cls, name):
        e = api("root.create_entity", f, ifc_class=cls, name=name)
        e.GlobalId = ifcopenshell.guid.compress(
            uuid.uuid5(ns, I["source_sha256"] + name).hex
        )
        return e

    def props(e, values, name="ScanToBIM_Evidence"):
        p = api("pset.add_pset", f, product=e, name=name)
        api("pset.edit_pset", f, pset=p, properties=values)

    def pt(p):
        return f.create_entity("IfcCartesianPoint", Coordinates=tuple(map(float, p)))

    def dr(p):
        return f.create_entity("IfcDirection", DirectionRatios=tuple(map(float, p)))

    def ax(p=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0)):
        return f.create_entity(
            "IfcAxis2Placement3D", Location=pt(p), Axis=dr(z), RefDirection=dr(x)
        )

    def curve(coords):
        return f.create_entity("IfcPolyline", Points=[pt(c) for c in coords])

    def profile(p):
        outer = curve(p.exterior.coords)
        if len(p.interiors):
            return f.create_entity(
                "IfcArbitraryProfileDefWithVoids",
                ProfileType="AREA",
                OuterCurve=outer,
                InnerCurves=[curve(h.coords) for h in p.interiors],
            )
        return f.create_entity(
            "IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=outer
        )

    def polys(g):
        from .topology import polygons
        return polygons(g)

    def solids(g, z, h):
        return [
            f.create_entity(
                "IfcExtrudedAreaSolid",
                SweptArea=profile(p),
                Position=ax((0, 0, z)),
                ExtrudedDirection=dr((0, 0, 1)),
                Depth=float(h),
            )
            for p in polys(g)
        ]

    project = root(
        "IfcProject", "qFuzzyRESF | " + I.get("source_filename", "Cloud") + ""
    )
    api(
        "unit.assign_unit",
        f,
        units=[
            api("unit.add_si_unit", f, unit_type=t)
            for t in ["LENGTHUNIT", "AREAUNIT", "VOLUMEUNIT"]
        ],
    )
    ctx = api("context.add_context", f, context_type="Model")
    body = api(
        "context.add_context",
        f,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=ctx,
    )
    site = root("IfcSite", "Site; CRS unspecified")
    building = root("IfcBuilding", "Partially reconstructed building")
    level = root("IfcBuildingStorey", "Main surveyed level")
    level.Elevation = zf + I.get("coordinate_origin_m", [0, 0, 0])[2]
    for parent, child in [(project, site), (site, building), (building, level)]:
        api("aggregate.assign_object", f, products=[child], relating_object=parent)
        api("geometry.edit_object_placement", f, product=child, matrix=np.eye(4))
    props(
        project,
        {
            "MethodVersion": "0.5 pilot",
            "Profile": I.get("profile", "monotone"),
            "SourceSHA256": I["source_sha256"],
            "ReferenceUsedForReconstruction": False,
            "ReviewRequired": True,
            "Scope": I["scope"],
            "Units": "Metres; explicit input scale; global coordinates preserved; CRS unspecified",
            "RoomTopologyValidated": False,
            "Method": "PCA normals, axis plane proposals, monotone Sugeno support, paired faces, masked distance watershed",
            "FuzzyScoreMeaning": "Support index; not a calibrated probability",
            "ExperimentalStatus": "Fixed v0.5 pilot parameters; no population-level performance claim",
            "OpeningsStatus": "Not reconstructed; bridged wall gaps may cover real openings",
        },
    )
    entities = {}

    def add(cls, name, g, z, h, pr, col=(0.55, 0.62, 0.68), spatial=False):
        e = root(cls, name)
        api("geometry.edit_object_placement", f, product=e, matrix=T)
        if g is not None:
            rep = f.create_entity(
                "IfcShapeRepresentation",
                ContextOfItems=body,
                RepresentationIdentifier="Body",
                RepresentationType="SweptSolid",
                Items=solids(g, z, h),
            )
            api("geometry.assign_representation", f, product=e, representation=rep)
            st = api("style.add_style", f, name=name)
            api(
                "style.add_surface_style",
                f,
                style=st,
                attributes={
                    "SurfaceColour": dict(zip(["Red", "Green", "Blue"], col)),
                    "Transparency": 0.85
                    if spatial
                    else (0.65 if cls == "IfcCovering" else 0.0),
                },
            )
            api(
                "style.assign_representation_styles",
                f,
                shape_representation=rep,
                styles=[st],
            )
        if spatial:
            api("aggregate.assign_object", f, products=[e], relating_object=level)
        else:
            api("spatial.assign_container", f, products=[e], relating_structure=level)
        props(
            e,
            {
                "ReviewRequired": True,
                "SourceSHA256": I["source_sha256"],
                "MethodVersion": "0.5",
                **pr,
            },
        )
        entities[name.split()[0]] = e
        return e

    wall_polys = []
    for w in I["walls"]:
        p = shape(w["polygon"])
        wall_polys.append(p)
        e = add(
            "IfcWall",
            w["id"] + " Wall candidate",
            p,
            zf,
            H,
            {
                "SupportScore": w["score"],
                "Thickness_m": w["thickness"],
                "ThicknessEvidence": w["thickness_status"],
                "ProposalIds": ",".join(w["proposal_ids"]),
                "Height_m": H,
                "HeightEvidence": "Shared fitted horizontal planes; extrapolated per wall",
                "ExtentEvidence": w["extent_status"],
                "EndpointExtensions_m": json.dumps(w["endpoint_extension_m"]),
                "Openings": "No subtraction; requires survey review",
            },
        )
        e.PredefinedType = "NOTDEFINED"
        q = api("pset.add_qto", f, product=e, name="Qto_WallBaseQuantities")
        api(
            "pset.edit_qto",
            f,
            qto=q,
            properties={
                "Length": w["t1"] - w["t0"],
                "Width": w["thickness"],
                "Height": H,
                "GrossVolume": p.area * H,
            },
        )
    env = shape(I["envelope"])
    floor = add(
        "IfcSlab",
        "FL001 Floor; assumed thickness",
        env,
        zf - 0.15,
        0.15,
        {
            "ObservedFaceZ_m": zf,
            "Thickness_m": 0.15,
            "ThicknessEvidence": "ASSUMED",
            "FootprintEvidence": I["envelope_status"],
        },
        (0.55, 0.55, 0.55),
    )
    floor.PredefinedType = "FLOOR"
    ceiling = add(
        "IfcCovering",
        "CL001 Ceiling; assumed thickness",
        env,
        zc,
        0.10,
        {
            "ObservedFaceZ_m": zc,
            "Thickness_m": 0.10,
            "ThicknessEvidence": "ASSUMED",
            "FootprintEvidence": I["envelope_status"],
        },
        (0.82, 0.82, 0.82),
    )
    ceiling.PredefinedType = "CEILING"
    colors = [(0.4, 0.65, 0.8), (0.92, 0.6, 0.4), (0.45, 0.7, 0.45), (0.7, 0.5, 0.8)]
    spaces = []
    for i, s in enumerate(I["spaces"]):
        p = shape(s["polygon"])
        e = add(
            "IfcSpace",
            s["id"] + " Provisional space",
            p,
            zf,
            H,
            {
                "Status": s["status"],
                "Segmentation": "Masked watershed of wall-distance clearance",
                "Area_m2": p.area,
                "Boundaries": "Physical candidate walls and explicitly virtual separators",
                "OccupancyUse": "UNASSIGNED",
            },
            colors[i % len(colors)],
            True,
        )
        e.PredefinedType = "INTERNAL"
        e.CompositionType = "ELEMENT"
        e.LongName = "Provisional space requiring review"
        props(e, {"IsExternal": False}, "Pset_SpaceCommon")
        q = api("pset.add_qto", f, product=e, name="Qto_SpaceBaseQuantities")
        api(
            "pset.edit_qto",
            f,
            qto=q,
            properties={"NetFloorArea": p.area, "NetVolume": p.area * H, "Height": H},
        )
        spaces.append((e, p, s))
    # First-level boundaries only; virtual interfaces have no physical wall geometry.
    boundaries = []
    virtuals = {}
    side_lengths = collections.Counter()

    def plane_surface(vertices, holes=[]):
        pts = np.array(vertices, float)
        o = pts[0]
        ex = pts[1] - o
        ex /= np.linalg.norm(ex)
        cross = np.cross(ex, pts - o)
        n = cross[np.argmax(np.linalg.norm(cross, axis=1))]
        n /= np.linalg.norm(n)
        ey = np.cross(n, ex)

        def c2(vertices):
            a = np.array(vertices)
            xy = np.c_[(a - o) @ ex, (a - o) @ ey].tolist()
            return curve(xy + [xy[0]])

        return f.create_entity(
            "IfcCurveBoundedPlane",
            BasisSurface=f.create_entity("IfcPlane", Position=ax(o, n, ex)),
            OuterBoundary=c2(vertices),
            InnerBoundaries=[c2(h) for h in holes],
        )

    def boundary(e, label, vertices, element, holes=[]):
        physical = element is not None
        if element is None:
            a, b = vertices[:2]
            key = tuple(sorted(tuple(round(v, 5) for v in xy) for xy in [a[:2], b[:2]]))
            if key not in virtuals:
                virtuals[key] = add(
                    "IfcVirtualElement",
                    f"VE{len(virtuals) + 1:04d} Virtual boundary",
                    None,
                    0,
                    0,
                    {"Status": "VIRTUAL SPACE CLOSURE; NO WALL OBSERVED"},
                )
            element = virtuals[key]
        rel = f.create_entity(
            "IfcRelSpaceBoundary",
            GlobalId=ifcopenshell.guid.compress(
                uuid.uuid5(ns, I["source_sha256"] + label).hex
            ),
            Name=label,
            Description="First-level candidate boundary. Not certified for thermal simulation.",
            RelatingSpace=e,
            RelatedBuildingElement=element,
            ConnectionGeometry=f.create_entity(
                "IfcConnectionSurfaceGeometry",
                SurfaceOnRelatingElement=plane_surface(vertices, holes),
            ),
            PhysicalOrVirtualBoundary="PHYSICAL" if physical else "VIRTUAL",
            InternalOrExternalBoundary="NOTDEFINED",
        )
        boundaries.append(rel)

    for e, p, s in spaces:
        exterior = list(p.exterior.coords)[:-1]
        holes = [list(r.coords)[:-1] for r in p.interiors]
        for label, z, element in [("floor", zf, floor), ("ceiling", zc, ceiling)]:
            boundary(
                e,
                s["id"] + "_" + label,
                [[a, b, z] for a, b in exterior],
                element,
                [[[a, b, z] for a, b in r] for r in holes],
            )
        for ri, ring in enumerate([p.exterior, *p.interiors]):
            c = list(ring.coords)
            for j, (a, b) in enumerate(zip(c[:-1], c[1:])):
                edge = LineString([a, b])
                mids = [edge.interpolate(t, normalized=True) for t in [0.05, 0.5, 0.95]]
                distances = [max(poly.distance(q) for q in mids) for poly in wall_polys]
                ii = int(np.argmin(distances))
                element = (
                    entities[I["walls"][ii]["id"]] if distances[ii] <= 0.13 else None
                )
                side_lengths["physical" if element else "virtual"] += edge.length
                boundary(
                    e,
                    f"{s['id']}_r{ri}_e{j}",
                    [[*a, zf], [*b, zf], [*b, zc], [*a, zc]],
                    element,
                )
    from .exterior_ifc import add_surfaces
    add_surfaces(f, project, building, body, I)
    f.write(str(out))
    # Validate the saved STEP file and all exported solids, including room holes.
    m = ifcopenshell.open(str(out))
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(m, logger, express_rules=True)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    failed = []
    mesh_records = []
    for e in m.by_type("IfcElement") + m.by_type("IfcSpace"):
        if e.is_a("IfcVirtualElement"):
            continue
        try:
            sh = ifcopenshell.geom.create_shape(settings, e)
            v = np.array(sh.geometry.verts).reshape(-1, 3)
            faces = np.array(sh.geometry.faces).reshape(-1, 3)
            mesh = trimesh.Trimesh(vertices=v, faces=faces, process=False)
            rec = {
                "guid": e.GlobalId,
                "name": e.Name,
                "class": e.is_a(),
                "area_m2": float(mesh.area),
                "volume_m3": float(mesh.volume),
                "watertight": bool(mesh.is_watertight),
            }
            if e.is_a("IfcSpace"):
                p = next(p for ee, p, _ in spaces if ee.GlobalId == e.GlobalId)
                rec["analytical_volume_m3"] = p.area * H
                rec["volume_error_m3"] = abs(mesh.volume - p.area * H)
            mesh_records.append(rec)
        except Exception as ex:
            failed.append({"guid": e.GlobalId, "error": str(ex)})
    areas = [p.area for _, p, _ in spaces]
    overlap = sum(
        p.intersection(q).area
        for i, (_, p, _) in enumerate(spaces)
        for _, q, _ in spaces[i + 1 :]
    )
    wall_overlap = sum(p.area for p in wall_polys) - unary_union(wall_polys).area
    validation = {
        "schema": "IFC4",
        "topology": I.get("topology", {}),
        "counts": {
            c: len(m.by_type(c))
            for c in [
                "IfcWall",
                "IfcRoof",
                "IfcBuildingStorey",
                "IfcSlab",
                "IfcCovering",
                "IfcSpace",
                "IfcOpeningElement",
                "IfcDoor",
                "IfcWindow",
                "IfcVirtualElement",
                "IfcRelSpaceBoundary",
            ]
        },
        "schema_messages": logger.statements,
        "geometry_failures": failed,
        "meshes": mesh_records,
        "space_area_m2": sum(areas),
        "envelope_area_m2": env.area,
        "space_overlap_m2": overlap,
        "wall_overlap_plan_area_m2": wall_overlap,
        "boundary_counts": dict(
            collections.Counter(b.PhysicalOrVirtualBoundary for b in boundaries)
        ),
        "space_side_boundary_length_m": dict(side_lengths),
        "boundary_semantics": "First level; 0.13m association tolerance; room discretization and clearance cause boundary offsets; no thermal validation",
    }

    errors = [m for m in logger.statements if m.get("level") in {"error", "critical"}]
    if errors or failed or any(not m["watertight"] or m["volume_m3"] <= 0 for m in mesh_records):
        raise ValueError("IFC validation failed: invalid schema or solids")
    if overlap > 1e-6:
        raise ValueError("Reconstructed spaces overlap")
    return validation
