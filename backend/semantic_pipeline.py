"""Auditable RESF–Mamdani reconstruction, independent of legacy shell recovery.

All geometry is local, in metres, Z-up. This module never supplies fabricated
semantic probabilities or visibility. See docs/SEMANTIC_PIPELINE.md for scope.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
from dataclasses import is_dataclass
from pathlib import Path
import time

import numpy as np
from scipy.spatial import cKDTree

try:
    from .bim_reconstruction import BIMModel, Wall, write_csv
    from .resf_geometry import prepare_points, estimate_normals, detect_levels, extract_candidates
    from .resf_fuzzy import infer_wall
    from .resf_objects import reconstruct_objects
    from .resf_ifc import write_semantic_ifc
except ImportError:
    from bim_reconstruction import BIMModel, Wall, write_csv
    from resf_geometry import prepare_points, estimate_normals, detect_levels, extract_candidates
    from resf_fuzzy import infer_wall
    from resf_objects import reconstruct_objects
    from resf_ifc import write_semantic_ifc

STAGES = ("input", "preprocess", "features", "storeys", "resf", "evidence",
          "fuzzy", "instances", "feedback", "objects", "export")
VERSION = "8.0-stepwise-semantic"


def serial(value):
    """Include dynamic provenance/holes as well as dataclass fields."""
    if is_dataclass(value):
        return {k: serial(v) for k, v in vars(value).items()}
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(serial(value), indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_points(path):
    path = Path(path)
    if path.suffix.lower() == ".npy":
        points = np.load(path, allow_pickle=False)
    elif path.suffix.lower() == ".npz":
        with np.load(path, allow_pickle=False) as data:
            points = data["points"]
    elif path.suffix.lower() in {".las", ".laz"}:
        try:
            import laspy
        except ImportError as exc:
            raise ValueError("LAS needs laspy; LAZ also needs lazrs (pip install 'laspy[lazrs]')") from exc
        data = laspy.read(path)
        points = np.column_stack((data.x, data.y, data.z))
    else:
        points = np.loadtxt(path, usecols=(0, 1, 2), ndmin=2)
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
        raise ValueError("Input must contain at least three XYZ rows")
    if not np.isfinite(points).all(axis=1).any():
        raise ValueError("No finite XYZ rows")
    return points


def _sidecars(request, count, scale, origin):
    probabilities = {}
    if request.get("semantics"):
        with np.load(request["semantics"], allow_pickle=False) as data:
            for key in data.files:
                value = np.asarray(data[key], dtype=float)
                if value.shape != (count,) or not np.isfinite(value).all() or np.any((value < 0) | (value > 1)):
                    raise ValueError(f"Semantic array {key} must have one finite probability in [0,1] per input row")
                probabilities[key.lower()] = value
        if probabilities and np.max(np.sum(list(probabilities.values()), axis=0)) > 1.00001:
            raise ValueError("Semantic class probabilities must sum to at most one per point")
    origins = None
    if request.get("scanner_origins"):
        origins = np.asarray(np.load(request["scanner_origins"], allow_pickle=False), dtype=float)
        if origins.shape == (3,):
            origins = np.broadcast_to(origins, (count, 3)).copy()
        if origins.shape != (count, 3) or not np.isfinite(origins).all():
            raise ValueError("scanner_origins must be finite XYZ, either (3,) or (N,3) aligned with input")
        origins = origins * scale - origin
    return probabilities, origins


def axes(candidate):
    angle = math.radians(candidate.theta_deg)
    normal = np.array([math.cos(angle), math.sin(angle)])
    return normal, np.array([-normal[1], normal[0]])


def visibility_evidence(candidate, points, origins, config):
    """Per-cell ray evidence; unknown coverage never becomes occluded support.

    A return before the candidate plane blocks that ray; a return beyond it
    observes free space. Free observations dominate blocked observations.
    Scores are divided by all unoccupied cells, penalising unknown coverage.
    """
    if origins is None or not config.get("occlusion", True):
        return {"occlusion": None, "visibility_evidence_available": False}
    normal, tangent = axes(candidate)
    cell = float(config.get("visibility_cell_m", 0.15))
    if not np.isfinite(cell) or cell <= 0:
        raise ValueError("visibility_cell_m must be positive")
    nt = max(1, int(math.ceil((candidate.t1 - candidate.t0) / cell)))
    nz = max(1, int(math.ceil((candidate.z1 - candidate.z0) / cell)))
    if nt * nz > 2_000_000:
        raise ValueError("Visibility raster too large")
    direction = points - origins
    denominator = direction[:, :2] @ normal
    alpha = np.divide(candidate.d - origins[:, :2] @ normal, denominator,
                      out=np.full(len(points), np.nan), where=np.abs(denominator) > 1e-9)
    # A finite maximum continuation prevents distant unobserved extrapolation.
    extension = np.linalg.norm(direction, axis=1) * np.maximum(alpha - 1, 0)
    maximum_depth = float(config.get("max_occlusion_depth_m", 1.0))
    if not np.isfinite(maximum_depth) or maximum_depth < 0:
        raise ValueError("max_occlusion_depth_m must be finite and nonnegative")
    valid = np.isfinite(alpha) & (alpha > 0) & (extension <= maximum_depth)
    ids = np.flatnonzero(valid)
    hits = origins[ids] + alpha[ids, None] * direction[ids]
    t = hits[:, :2] @ tangent
    valid_hit = (t >= candidate.t0) & (t <= candidate.t1) & (hits[:, 2] >= candidate.z0) & (hits[:, 2] <= candidate.z1)
    ids, hits, t = ids[valid_hit], hits[valid_hit], t[valid_hit]
    cells = np.minimum(nt - 1, ((t - candidate.t0) / cell).astype(int)) * nz + np.minimum(nz - 1, ((hits[:, 2] - candidate.z0) / cell).astype(int))
    residual = (points[ids, :2] @ normal - candidate.d)
    tolerance = float(config.get("plane_tolerance_m", 0.04)) * 2
    observed = np.unique(cells[np.abs(residual) <= tolerance])
    free = np.setdiff1d(np.unique(cells[(alpha[ids] < 1) & (np.abs(residual) > tolerance)]), observed)
    blocked = np.setdiff1d(np.unique(cells[(alpha[ids] > 1) & (np.abs(residual) > tolerance)]), np.union1d(free, observed))
    missing = max(1, nt * nz - len(observed))
    available = bool(len(free) + len(blocked))
    return {"occlusion": len(blocked) / missing if available else None,
            "free_space_fraction": len(free) / missing if available else None,
            "visibility_coverage": (len(blocked) + len(free)) / missing,
            "visibility_evidence_available": available,
            "ray_counts": {"observed_cells": len(observed), "free_cells": len(free), "blocked_cells": len(blocked), "total_cells": nt * nz}}


def make_instances(candidates, evidence, decisions, config):
    """Consolidate observed faces; unknown thickness is explicitly a prior.

    Automatic pairing is opt-in because two parallel planes can also be two
    distinct partitions. Default preserves each accepted surface hypothesis.
    """
    accepted = [c for c in candidates if decisions[c.id]["accepted"]]
    walls, provenance, consumed = [], {}, set()
    thickness = float(config.get("assumed_wall_thickness_m", 0.20))
    if not np.isfinite(thickness) or thickness <= 0:
        raise ValueError("assumed_wall_thickness_m must be positive")
    maximum_thickness = float(config.get("max_wall_thickness_m", 0.50))
    if not np.isfinite(maximum_thickness) or maximum_thickness < 0.08:
        raise ValueError("max_wall_thickness_m must be finite and at least 0.08")

    def compatible(a, b):
        if a.id == b.id or a.storey_id != b.storey_id:
            return None
        an, at = axes(a)
        bn, bt = axes(b)
        endpoints = np.array([bn * b.d + bt * b.t0, bn * b.d + bt * b.t1])
        offsets = endpoints @ an
        distance = abs(float(offsets.mean()) - a.d)
        interval = sorted(endpoints @ at)
        overlap = max(0, min(a.t1, interval[1]) - max(a.t0, interval[0]))
        ae, be = evidence[a.id], evidence[b.id]
        zoverlap = max(0, min(ae["observed_z1"], be["observed_z1"]) - max(ae["observed_z0"], be["observed_z0"]))
        zextent = max(ae["observed_z1"] - ae["observed_z0"], be["observed_z1"] - be["observed_z0"], 1e-9)
        if abs(float(bn @ an)) > math.cos(math.radians(2)) and np.ptp(offsets) <= 0.03 and 0.08 <= distance <= maximum_thickness and overlap / max(a.t1 - a.t0, b.t1 - b.t0) > 0.90 and zoverlap / zextent > 0.90:
            return (distance, b, float(offsets.mean()), interval)
        return None

    pairs = {a.id: [match for b in accepted if (match := compatible(a, b)) is not None]
             for a in accepted} if config.get("pair_wall_faces", False) else {}
    for c in accepted:
        if c.id in consumed:
            continue
        normal, tangent = axes(c)
        other = None
        if config.get("pair_wall_faces", False):
            options = pairs[c.id]
            # Multiple compatible surfaces are unresolved, never greedily paired.
            if len(options) == 1 and len(pairs[options[0][1].id]) == 1 and options[0][1].id not in consumed:
                other = options[0]
        d, t0, t1, width = c.d, c.t0, c.t1, thickness
        sources = [c.id]
        status = "assumed_thickness_surface_used_as_axis"
        if other is not None:
            width, b, offset, interval = other
            d = (d + offset) / 2
            t0, t1 = max(t0, interval[0]), min(t1, interval[1])
            sources.append(b.id)
            consumed.add(b.id)
            status = "paired_observed_surfaces_assuming_same_wall"
        consumed.add(c.id)
        p0, p1 = normal * d + tangent * t0, normal * d + tangent * t1
        ev = evidence[c.id]
        # Height follows measured extents; small unmeasured edge extensions are
        # left explicit instead of filling the complete storey automatically.
        z0 = min(evidence[s]["observed_z0"] for s in sources)
        z1 = max(evidence[s]["observed_z1"] for s in sources)
        w = Wall(len(walls) + 1, c.storey_id, *map(float, p0), *map(float, p1), z0, z1,
                 width, c.theta_deg, min(decisions[s]["confidence"] for s in sources),
                 c.A_star, c.C, "RESF_Mamdani", state=c.state,
                 resf_A=c.A_star, resf_EH=c.E_H, resf_EV=c.E_V, resf_C=c.C, resf_N=c.N, resf_O=c.O)
        provenance[f"Wall:{w.id}"] = {"candidate_ids": sources, "thickness_status": status,
            "thickness_m": width, "axis_status": "inferred", "height_status": "observed_extent",
            "semantic_status": decisions[c.id]["semantic_status"], "confidence_kind": "uncalibrated_fuzzy_support",
            "observed_reference_planes": [{"d": next(x for x in candidates if x.id == s).d,
                "theta_deg": next(x for x in candidates if x.id == s).theta_deg} for s in sources]}
        walls.append(w)
    # Bounded endpoint intersections only; no building envelope or hull rescue.
    tolerance = float(config.get("junction_tolerance_m", 0.25))
    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("junction_tolerance_m must be finite and nonnegative")
    changes = []
    original = {w.id: np.array([[w.x1, w.y1], [w.x2, w.y2]]) for w in walls}
    moves = {}
    for i, a in enumerate(walls):
        for b in walls[i + 1:]:
            if a.storey_id != b.storey_id:
                continue
            ap, bp = original[a.id], original[b.id]
            av, bv = ap[1] - ap[0], bp[1] - bp[0]
            system = np.column_stack((av, -bv))
            if abs(np.linalg.det(system)) < 1e-8:
                continue
            ab = np.linalg.solve(system, bp[0] - ap[0])
            hit = ap[0] + ab[0] * av
            if not (-tolerance <= ab[0] * np.linalg.norm(av) <= np.linalg.norm(av) + tolerance and -tolerance <= ab[1] * np.linalg.norm(bv) <= np.linalg.norm(bv) + tolerance):
                continue
            for wall, ends in ((a, ap), (b, bp)):
                distances = np.linalg.norm(ends - hit, axis=1)
                end = int(np.argmin(distances))
                key = (wall.id, end)
                if distances[end] <= tolerance and (key not in moves or distances[end] < moves[key][0]):
                    moves[key] = (float(distances[end]), hit)
    for w in walls:
        ends = original[w.id].copy()
        for end in (0, 1):
            if (w.id, end) in moves:
                distance, hit = moves[(w.id, end)]
                ends[end] = hit
                changes.append({"wall_id": w.id, "endpoint": end, "distance_m": distance})
        if np.linalg.norm(ends[1] - ends[0]) > 0.20:
            w.x1, w.y1, w.x2, w.y2 = map(float, ends.ravel())
    return walls, provenance, changes


def feedback(walls, candidates, evidence, decisions, provenance, points, config):
    """Independent observed-reference distance/coverage gate after consolidation.

    Both directions are reported. Completion surfaces remain hypotheses and are
    not misreported as measured. No global model accuracy is inferred here.
    """
    by_id = {c.id: c for c in candidates}
    accepted, rejected, audit = [], [], []
    tolerance = float(config.get("feedback_tolerance_m", 0.12))
    sample_step = float(config.get("feedback_sample_m", 0.15))
    if min(tolerance, sample_step) <= 0 or not np.isfinite([tolerance, sample_step]).all():
        raise ValueError("Feedback tolerance/sample spacing must be positive and finite")
    for wall in walls:
        sources = provenance[f"Wall:{wall.id}"]["candidate_ids"]
        records = []
        for sid in sources:
            c, ev = by_id[sid], evidence[sid]
            observed_normal, _ = axes(c)
            final_start = np.array([wall.x1, wall.y1])
            final_end = np.array([wall.x2, wall.y2])
            tangent = final_end - final_start
            length = float(np.linalg.norm(tangent))
            if length <= 1e-9:
                raise ValueError("Feedback received a zero-length wall")
            tangent /= length
            normal = np.array([tangent[1], -tangent[0]])
            if normal @ observed_normal < 0:
                normal = -normal
            model_d = float(final_start @ normal)
            if len(sources) > 1:
                # Select the actual final solid face nearest this measured face.
                reference_point = observed_normal * c.d
                model_d += min((-wall.thickness / 2, wall.thickness / 2),
                               key=lambda offset: abs(float(reference_point @ normal) - model_d - offset))
            support = points[np.asarray(ev["support_indices"], dtype=int)]
            plane_distance = np.abs(support[:, :2] @ normal - model_d)
            # Use the final instance endpoint bounds projected on each observed
            # face; a snap extending a surface must be checked for support.
            ends = np.array([[wall.x1, wall.y1], [wall.x2, wall.y2]]) @ tangent
            lo, hi = sorted(ends)
            ts = np.linspace(lo, hi, max(2, min(1000, int((hi - lo) / sample_step) + 1)))
            zs = np.linspace(wall.z0, wall.z1, max(2, min(500, int((wall.z1 - wall.z0) / sample_step) + 1)))
            tt, zz = np.meshgrid(ts, zs)
            xy = normal * model_d + tt.ravel()[:, None] * tangent
            sample = np.column_stack((xy, zz.ravel()))
            distances = cKDTree(support).query(sample, workers=1)[0]
            coverage = float(np.mean(distances <= tolerance))
            along = support[:, :2] @ tangent
            outside_t = np.maximum(np.maximum(lo - along, along - hi), 0.)
            outside_z = np.maximum(np.maximum(wall.z0 - support[:, 2], support[:, 2] - wall.z1), 0.)
            forward_distances = np.sqrt(plane_distance ** 2 + outside_t ** 2 + outside_z ** 2)
            forward = float(np.mean(forward_distances <= tolerance))
            # Observed support ratio plus symmetric coverage, not residual alone.
            geometric_support = min(forward, coverage)
            ev["geometric_support"] = geometric_support
            result = infer_wall(ev, config)
            decisions[sid] = result
            c.G, c.state, c.confidence = geometric_support, result["state"], result["confidence"]
            records.append({"candidate_id": sid, "cloud_to_reference_p95_m": float(np.quantile(forward_distances, .95)),
                "reference_to_cloud_p95_m": float(np.quantile(distances, .95)), "sample_count": len(sample),
                "observed_reference_coverage": coverage, "geometric_support": geometric_support,
                "accepted_after_feedback": result["accepted"]})
        wall.geometric_feedback = min(r["geometric_support"] for r in records)
        ok = all(r["accepted_after_feedback"] for r in records)
        if ok:
            wall.confidence = min(decisions[s]["confidence"] for s in sources)
            wall.state = "OccludedPermanent" if any(decisions[s]["state"] == "OccludedPermanent" for s in sources) else "Permanent"
            accepted.append(wall)
        else:
            wall.state = "Uncertain"
            rejected.append(wall)
        provenance[f"Wall:{wall.id}"]["feedback"] = records
        audit.append({"wall_id": wall.id, "accepted": ok, "reference_faces": records,
            "hidden_faces": "unobserved; not included in measured residuals"})
    return accepted, rejected, audit


class Run:
    def __init__(self, request):
        self.request = request
        self.output = Path(request["output"]).resolve()
        self.stop = request.get("stop_after", "export")
        if self.stop not in STAGES:
            raise ValueError(f"stop_after must be one of {STAGES}")
        self.output.mkdir(parents=True, exist_ok=True)
        if any((self.output / name).exists() for name in ("steps", "result.json", "fuzzy_resf.ifc", "bim_model.json")):
            raise ValueError("Output directory contains a previous run; choose a new directory")
        (self.output / "steps").mkdir()
        self.manifest = {"version": VERSION, "status": "running", "config": request, "stages": [],
                         "confidence_interpretation": "uncalibrated fuzzy support, not a probability"}
        self.manifest["config_sha256"] = hashlib.sha256(json.dumps(serial(request), sort_keys=True, allow_nan=False).encode()).hexdigest()
        self.manifest["runtime"] = {"python": platform.python_version(), "platform": platform.platform()}
        for package in ("numpy", "scipy", "shapely", "ifcopenshell"):
            try:
                self.manifest["runtime"][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                self.manifest["runtime"][package] = None
        self.manifest["source_sha256"] = {name: digest(Path(__file__).with_name(name)) for name in
            ("semantic_pipeline.py", "resf_geometry.py", "resf_fuzzy.py", "resf_objects.py", "resf_ifc.py")}
        self.clock = time.perf_counter()

    def stage(self, name, data, **arrays):
        path = self.output / "steps" / f"{STAGES.index(name) + 1:02d}_{name}"
        write_json(path.with_suffix(".json"), data)
        if arrays:
            np.savez_compressed(path.with_suffix(".npz"), **arrays)
        now = time.perf_counter()
        self.manifest["stages"].append({"name": name, "status": "complete", "seconds": now - self.clock,
            "json": str(path.with_suffix(".json")), "npz": str(path.with_suffix(".npz")) if arrays else None})
        self.clock = now
        write_json(self.output / "manifest.json", self.manifest)
        print(f"[{STAGES.index(name) + 1}/{len(STAGES)}] {name}", flush=True)
        if name == self.stop and name != "export":
            self.manifest["status"] = "partial"
            result = {"status": "partial", "version": VERSION, "stopped_after": name,
                      "manifest": str(self.output / "manifest.json"), "ifc": None}
            write_json(self.output / "manifest.json", self.manifest)
            write_json(self.output / "result.json", result)
            raise _Stop(result)


class _Stop(Exception):
    def __init__(self, result):
        self.result = result


def run_pipeline(request):
    """Execute request and materialise every completed stage; no implicit resume."""
    request = dict(request)
    if request.get("fuzzy") is False or request.get("geometric_feedback") is False or request.get("instance_consolidation") is False:
        raise ValueError("v8 requires fuzzy inference, instance construction and geometric feedback")
    if "confidence_threshold" in request and "decision_threshold" not in request:
        request["decision_threshold"] = request["confidence_threshold"]
    if request.get("family_catalog_path"):
        request["family_catalog"] = json.loads(Path(request["family_catalog_path"]).read_text(encoding="utf-8"))
    run = Run(request)
    try:
        input_path = Path(request["input"]).resolve()
        raw = load_points(input_path)
        scale = float(request.get("scale_to_m", 1.0))
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("scale_to_m must be positive and finite")
        finite = raw[np.isfinite(raw).all(axis=1)]
        origin = np.asarray(request.get("coordinate_origin", np.min(finite * scale, axis=0)), dtype=float)
        if origin.shape != (3,) or not np.isfinite(origin).all():
            raise ValueError("coordinate_origin must contain three finite metre coordinates")
        frame = {"origin": origin.tolist(), "scale_to_m": scale, "gravity": "+Z",
                 "equation": "local_metres = input_xyz * scale_to_m - origin",
                 "restore_global_coordinates": bool(request.get("restore_global_coordinates", False)),
                 "cloudcompare_coordinates": request.get("cloudcompare_coordinates")}
        probabilities, origins = _sidecars(request, len(raw), scale, origin)
        fingerprints = {"input": {"path": str(input_path), "sha256": digest(input_path)}}
        for key in ("semantics", "scanner_origins", "family_catalog_path"):
            if request.get(key):
                fingerprints[key] = {"path": str(Path(request[key]).resolve()), "sha256": digest(request[key])}
        run.manifest["inputs"] = fingerprints
        run.stage("input", {"rows": len(raw), "coordinate_frame": frame, "inputs": fingerprints,
            "semantic_classes": sorted(probabilities), "scanner_origins_available": origins is not None})
        points, indices, prep = prepare_points(raw * scale - origin, request)
        probabilities = {key: values[indices] for key, values in probabilities.items()}
        origins = origins[indices] if origins is not None else None
        run.stage("preprocess", prep, points=points, source_indices=indices)
        normals, planarity = estimate_normals(points, request)
        run.stage("features", {"normal_method": "local PCA; unoriented normals", "point_count": len(points)}, normals=normals, planarity=planarity)
        storeys = detect_levels(points, normals, request)
        if not storeys:
            raise ValueError("No supported storey interval; provide config.storeys in local metres")
        run.stage("storeys", storeys)
        candidates, evidence, diagnostics = extract_candidates(points, normals, planarity, storeys, request)
        run.stage("resf", {"candidates": candidates, "diagnostics": diagnostics})
        for c in candidates:
            ev = evidence[c.id]
            ids = np.asarray(ev["support_indices"], dtype=int)
            for source, target in (("wall", "semantic_wall"), ("furniture", "semantic_furniture")):
                if source in probabilities:
                    ev[target] = float(np.mean(probabilities[source][ids]))
            ev.update(visibility_evidence(c, points, origins, request))
            ev["semantic_evidence_available"] = bool(probabilities)
            c.semantic_wall = ev.get("semantic_wall", 0.)
            c.O = ev.get("occlusion") or 0.
        run.stage("evidence", evidence)
        decisions = {c.id: infer_wall(evidence[c.id], request) for c in candidates}
        for c in candidates:
            c.state, c.confidence = decisions[c.id]["state"], decisions[c.id]["confidence"]
        run.stage("fuzzy", decisions)
        walls, provenance, snaps = make_instances(candidates, evidence, decisions, request)
        run.stage("instances", {"walls": walls, "provenance": provenance, "endpoint_adjustments": snaps})
        walls, uncertain, feedback_audit = feedback(walls, candidates, evidence, decisions, provenance, points, request)
        run.stage("feedback", {"accepted_walls": walls, "uncertain_walls": uncertain, "audit": feedback_audit, "updated_decisions": decisions})
        model = BIMModel(storeys, walls, [], [], [], candidates, {})
        model.provenance, model.coordinate_frame = provenance, frame
        objects = reconstruct_objects(points, normals, model, request, probabilities, origins)
        for name in ("openings", "spaces", "slabs"):
            setattr(model, name, objects[name])
            kind = {"openings": "Opening", "spaces": "Space", "slabs": "Slab"}[name]
            for obj in objects[name]:
                provenance[f"{kind}:{obj.id}"] = getattr(obj, "provenance", {})
        model.uncertain_walls = uncertain
        model.object_decisions = objects["decisions"]
        model.method_metadata = objects["provenance"]
        run.stage("objects", {"openings": model.openings, "spaces": model.spaces, "slabs": model.slabs,
            "decisions": objects["decisions"], "provenance": provenance, "method": model.method_metadata})
        result = export_model(model, run.output, request)
        run.stage("export", result)
        run.manifest["status"] = "complete"
        write_json(run.output / "manifest.json", run.manifest)
        write_json(run.output / "result.json", result)
        return result
    except _Stop as stopped:
        return stopped.result
    except Exception as exc:
        run.manifest["status"] = "failed"
        run.manifest["error"] = f"{type(exc).__name__}: {exc}"
        write_json(run.output / "manifest.json", run.manifest)
        write_json(run.output / "result.json", {"status": "error", "version": VERSION, "error": run.manifest["error"]})
        raise


def export_model(model, output, config):
    result = {"status": "success", "version": VERSION, "method": "RESF-Mamdani-stepwise",
        "walls": len(model.walls), "openings": len(model.openings), "spaces": len(model.spaces), "slabs": len(model.slabs),
        "doors": sum(o.kind == "Door" for o in model.openings), "windows": sum(o.kind == "Window" for o in model.openings),
        "storeys": len(model.storeys), "resf_candidates": len(model.candidates), "uncertain_walls": len(model.uncertain_walls),
        "warnings": ["Fuzzy scores are not calibrated probabilities.",
            "Unobserved thickness and hidden surfaces remain explicit assumptions.",
            "Columns, stairs, roofs and curved/inclined walls are outside v8 scope."]}
    if not model.walls:
        result["warnings"].append("No accepted walls: inspect the evidence and fuzzy stages; no rescue geometry was inserted.")
    for name in ("walls", "openings", "spaces", "slabs", "candidates", "uncertain_walls"):
        values = getattr(model, name)
        if name == "candidates":
            label = "resf_candidates"
        else:
            label = name
        path = output / f"{label}.csv"
        if values:
            # Only declared scalar/geometry fields, dynamic provenance stays JSON.
            fields = list(values[0].__dataclass_fields__)
            write_csv(values, path, fields)
        else:
            path.write_text("id\n", encoding="utf-8")
        result[f"{label}_csv"] = str(path)
    model.summary = result.copy()
    write_json(output / "bim_model.json", model)
    write_json(output / "object_decisions.json", model.object_decisions)
    result.update(bim_model_json=str(output / "bim_model.json"), object_decisions_json=str(output / "object_decisions.json"),
                  manifest=str(output / "manifest.json"), ifc=None)
    if config.get("export_ifc", True) and (model.walls or model.slabs or model.spaces):
        validation = write_semantic_ifc(model, output / "fuzzy_resf.ifc", config.get("project_name", "Fuzzy RESF Semantic"))
        write_json(output / "ifc_validation.json", validation)
        result["ifc"] = str(output / "fuzzy_resf.ifc")
        result["ifc_validation_json"] = str(output / "ifc_validation.json")
    return result
