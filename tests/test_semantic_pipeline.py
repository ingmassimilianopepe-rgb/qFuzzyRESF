"""Behavioral integration checks for the auditable semantic reconstruction.

Synthetic truth tests plumbing and geometric invariance. It is not a real-scan
accuracy benchmark and does not calibrate fuzzy confidence values.
"""
import json
from pathlib import Path
import subprocess
import sys

import ifcopenshell
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from bim_reconstruction import RESFCandidate
from semantic_pipeline import STAGES, feedback, make_instances, run_pipeline, visibility_evidence
from resf_fuzzy import infer_wall
from semantic_resf_cli import main


def synthetic_room(angle=27.0, translation=(643000., 4872000., 170.)):
    rng = np.random.default_rng(104)
    chunks = []
    for y in (0., 4.):
        n = 5000
        chunks.append(np.column_stack((rng.uniform(0, 6, n), y + rng.normal(0, .003, n),
                                       rng.uniform(0, 2.8, n))))
    for x in (0., 6.):
        n = 3800
        chunks.append(np.column_stack((x + rng.normal(0, .003, n), rng.uniform(0, 4, n),
                                       rng.uniform(0, 2.8, n))))
    for z in (0., 2.8):
        n = 6000
        chunks.append(np.column_stack((rng.uniform(0, 6, n), rng.uniform(0, 4, n),
                                       z + rng.normal(0, .002, n))))
    points = np.vstack(chunks)
    theta = np.radians(angle)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    points[:, :2] = points[:, :2] @ rotation.T
    return points + np.asarray(translation)


def request_for(tmp_path, points, **options):
    source = tmp_path / "points.npy"
    np.save(source, points)
    return {"input": str(source), "output": str(tmp_path / "run"),
            "voxel_m": .04, "raster_cell_m": .10, "normal_neighbors": 20, **options}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def candidate(number=1, d=1., t0=0., t1=1.):
    return RESFCandidate(number, 1, 0., d, t0, t1, 0., 3.,
                         1., 1., 1., 1., 1., 0., 1., 0., state="Permanent")


def test_rotated_georeferenced_room_exports_real_ifc_and_every_stage(tmp_path):
    points = synthetic_room()
    result = run_pipeline(request_for(tmp_path, points))
    assert result["status"] == "success"
    assert (result["walls"], result["spaces"], result["slabs"], result["openings"]) == (4, 1, 2, 0)
    manifest = read(result["manifest"])
    assert manifest["status"] == "complete"
    assert [stage["name"] for stage in manifest["stages"]] == list(STAGES)
    assert len(manifest["inputs"]["input"]["sha256"]) == 64
    model = read(result["bim_model_json"])
    assert model["spaces"][0]["area"] == pytest.approx(24., abs=.2)
    angles = [wall["theta_deg"] % 180 for wall in model["walls"]]
    assert sum(abs((angle - 27. + 90.) % 180. - 90.) < 2. for angle in angles) == 2
    assert sum(abs((angle - 117. + 90.) % 180. - 90.) < 2. for angle in angles) == 2
    assert model["coordinate_frame"]["origin"][0] > 640000
    assert all(abs(wall["x1"]) < 10. and abs(wall["y1"]) < 10. for wall in model["walls"])
    for wall in model["walls"]:
        provenance = model["provenance"][f"Wall:{wall['id']}"]
        assert provenance["thickness_status"] == "assumed_thickness_surface_used_as_axis"
        assert provenance["semantic_status"] == "inferred_from_geometry_and_topology"
        assert all(record["accepted_after_feedback"] for record in provenance["feedback"])
    validation = read(result["ifc_validation_json"])
    assert validation["schema_valid"] and validation["geometry_valid"]
    assert all(check["actual_volume_m3"] > 0 for check in validation["geometry_checks"])
    ifc = ifcopenshell.open(result["ifc"])
    assert len(ifc.by_type("IfcWall")) == 4
    assert len(ifc.by_type("IfcSpace")) == 1
    assert len(ifc.by_type("IfcSlab")) == len(ifc.by_type("IfcCovering")) == 1
    assert not ifc.by_type("IfcDoor") and not ifc.by_type("IfcWindow")


def test_cli_stop_preserves_exact_source_mapping_and_unit_transform(tmp_path):
    points = np.array([[1000., 2000., 3000.], [1001., 2002., 3003.],
                       [1100., 2100., 3200.], [np.nan, 0., 0.],
                       [1000., 2000., 3000.], [1500., 2500., 3500.]])
    np.save(tmp_path / "points.npy", points)
    payload = {"input": "points.npy", "output": "run", "scale_to_m": .001,
               "coordinate_origin": [1., 2., 3.], "voxel_m": .02}
    request = tmp_path / "request.json"
    request.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["--request", str(request), "--stop-after", "preprocess"]) == 0
    result = read(tmp_path / "run/result.json")
    assert result["status"] == "partial" and result["stopped_after"] == "preprocess"
    assert result["ifc"] is None
    assert not (tmp_path / "run/fuzzy_resf.ifc").exists()
    assert not (tmp_path / "run/steps/03_features.json").exists()
    with np.load(tmp_path / "run/steps/02_preprocess.npz") as data:
        ids = data["source_indices"]
        np.testing.assert_array_equal(data["points"], points[ids] * .001 - [1., 2., 3.])
        assert 3 not in ids
        assert len(set(ids) & {0, 1, 4}) == 1  # One measured row, not an averaged point.


@pytest.mark.parametrize("variant", ["wrong_rows", "outside_probability", "class_sum", "bad_origin"])
def test_bad_sidecars_fail_before_geometry_with_failed_manifest(tmp_path, variant):
    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 0., 1.]])
    request = request_for(tmp_path, points, stop_after="input")
    if variant == "bad_origin":
        path = tmp_path / "origins.npy"
        np.save(path, np.zeros((2, 3)))
        request["scanner_origins"] = str(path)
    else:
        path = tmp_path / "semantics.npz"
        arrays = {"wall": np.ones(2 if variant == "wrong_rows" else 3)}
        if variant == "outside_probability":
            arrays["wall"][0] = 1.1
        elif variant == "class_sum":
            arrays["furniture"] = np.ones(3) * .2
        np.savez(path, **arrays)
        request["semantics"] = str(path)
    with pytest.raises(ValueError):
        run_pipeline(request)
    manifest = read(tmp_path / "run/manifest.json")
    assert manifest["status"] == "failed" and manifest["stages"] == []
    assert not (tmp_path / "run/fuzzy_resf.ifc").exists()


def test_existing_output_is_preserved_and_never_implicitly_resumed(tmp_path):
    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 0., 1.]])
    request = request_for(tmp_path, points, stop_after="input")
    output = Path(request["output"])
    output.mkdir()
    sentinel = output / "result.json"
    sentinel.write_text('{"existing": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="previous run"):
        run_pipeline(request)
    assert sentinel.read_text(encoding="utf-8") == '{"existing": true}'
    assert not (output / "steps").exists()


def test_missing_ray_coverage_is_unknown_and_observed_free_space_is_zero_occlusion():
    wall = candidate()
    wall.z1 = 1.
    points = np.array([[1., .25, .25], [2., .75, .25]])
    assert visibility_evidence(wall, points, None, {})["occlusion"] is None
    origins = np.array([[0., .25, .25], [0., .75, .25]])
    result = visibility_evidence(wall, points, origins, {"visibility_cell_m": .5})
    assert result["visibility_evidence_available"]
    assert result["occlusion"] == 0.
    assert result["free_space_fraction"] == pytest.approx(1 / 3)
    # A return on the plane is observed support; it does not observe the empty cells.
    result = visibility_evidence(wall, points[:1], origins[:1], {"visibility_cell_m": .5})
    assert result["occlusion"] is None and not result["visibility_evidence_available"]


def test_free_ray_dominates_blocked_ray_in_same_cell():
    wall = candidate()
    wall.z1 = 1.
    points = np.array([[.5, .75, .25], [2., .75, .25]])
    origins = np.array([[0., .75, .25], [0., .75, .25]])
    result = visibility_evidence(wall, points, origins, {"visibility_cell_m": .5})
    assert result["ray_counts"]["blocked_cells"] == 0
    assert result["ray_counts"]["free_cells"] == 1
    assert result["occlusion"] == 0.


def test_strong_external_furniture_evidence_prevents_wall_creation(tmp_path):
    points = synthetic_room(translation=(0., 0., 0.))
    request = request_for(tmp_path, points, stop_after="fuzzy")
    path = tmp_path / "semantics.npz"
    np.savez(path, wall=np.full(len(points), .05), furniture=np.full(len(points), .95))
    request["semantics"] = str(path)
    result = run_pipeline(request)
    assert result["status"] == "partial"
    decisions = read(tmp_path / "run/steps/07_fuzzy.json")
    assert len(decisions) == 4
    assert all(not item["accepted"] for item in decisions.values())
    assert all(item["state"] in {"Uncertain", "Clutter"} for item in decisions.values())


def test_face_pairing_requires_mutually_unambiguous_and_vertical_overlap():
    candidates = [candidate(i + 1, offset, 0., 4.) for i, offset in enumerate((0., .15, .30))]
    evidence = {c.id: {"observed_z0": 0., "observed_z1": 3.} for c in candidates}
    decisions = {c.id: {"accepted": True, "confidence": .8, "semantic_status": "inferred"} for c in candidates}
    walls, _, _ = make_instances(candidates, evidence, decisions,
                                 {"pair_wall_faces": True, "max_wall_thickness_m": .20})
    assert len(walls) == 3  # The middle face can belong to either neighboring surface.
    candidates = candidates[:2]
    evidence[1] = {"observed_z0": 0., "observed_z1": 1.}
    evidence[2] = {"observed_z0": 2., "observed_z1": 3.}
    walls, _, _ = make_instances(candidates, evidence, decisions,
                                 {"pair_wall_faces": True, "max_wall_thickness_m": .20})
    assert len(walls) == 2  # Separated panels do not prove one full-height solid.


def test_feedback_rejects_an_instance_displaced_from_its_observations():
    c = candidate(t1=4.)
    y, z = np.meshgrid(np.linspace(0, 4, 81), np.linspace(0, 3, 61))
    points = np.column_stack((np.ones(y.size), y.ravel(), z.ravel()))
    evidence = {c.id: {"A_star": 1., "E_H": 1., "E_V": 1., "C": 1., "N": 1.,
                      "floor_contact": 1., "ceiling_contact": 1., "observed_z0": 0.,
                      "observed_z1": 3., "support_indices": list(range(len(points)))}}
    decisions = {c.id: infer_wall(evidence[c.id], {})}
    assert decisions[c.id]["accepted"]
    walls, provenance, _ = make_instances([c], evidence, decisions, {})
    walls[0].x1 += 2.
    walls[0].x2 += 2.
    accepted, rejected, audit = feedback(walls, [c], evidence, decisions, provenance, points, {})
    assert accepted == [] and len(rejected) == 1
    assert not audit[0]["accepted"]


def test_legacy_entrypoint_dispatches_explicit_version8_request(tmp_path):
    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 0., 1.]])
    request = request_for(tmp_path, points, version="8.0-stepwise-semantic", stop_after="input")
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    entrypoint = Path(__file__).resolve().parents[1] / "backend/fuzzy_resf_cli.py"
    process = subprocess.run([sys.executable, str(entrypoint), "--request", str(path)],
                             text=True, capture_output=True, check=False, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
    result = read(tmp_path / "run/result.json")
    assert result["status"] == "partial" and result["stopped_after"] == "input"
    assert result["version"].startswith("8.")
