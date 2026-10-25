import json
from pathlib import Path
import subprocess
import sys

import ifcopenshell
import ifcopenshell.util.placement
import numpy as np
import pytest

from fuzzyresf.cli import run
from fuzzyresf.cloud import analyse
from fuzzyresf.inference import infer

ROOT = Path(__file__).resolve().parents[1]


def request(tmp_path, points, scale=1.0):
    source = tmp_path / "scan.xyz64"
    np.asarray(points, dtype="<f8").tofile(source)
    return {
        "schema_version": 1,
        "input": {"path": str(source), "count": len(points)},
        "output": str(tmp_path / "result.ifc"),
        "meters_per_unit": scale,
    }


def test_two_rooms_ifc_end_to_end(tmp_path, two_rooms):
    job = request(tmp_path, two_rooms)
    assert run(job) == 0
    model = ifcopenshell.open(job["output"])
    assert model.schema == "IFC4"
    assert len(model.by_type("IfcWall")) == 5
    assert len(model.by_type("IfcSpace")) == 2
    assert not model.by_type("IfcOpeningElement")
    assert all(
        e.Representation.Representations[0].RepresentationType == "SweptSolid"
        for e in model.by_type("IfcSpace")
    )
    report = json.loads((tmp_path / "result.fuzzyresf" / "validation.json").read_text())
    assert not report["schema_messages"]
    assert not report["geometry_failures"]
    assert all(m["watertight"] for m in report["meshes"])
    assert report["space_overlap_m2"] < 1e-8
    assert 35 < report["space_area_m2"] < 40


def test_mm_and_large_global_origin(tmp_path, two_rooms):
    world_origin = np.array([500000.0, 4200000.0, 100.0])
    job = request(tmp_path, (two_rooms + world_origin) * 1000, 0.001)
    assert run(job) == 0
    audit = json.loads((tmp_path / "result.fuzzyresf" / "audit.json").read_text())
    assert np.allclose(audit["actual_min_m"], (two_rooms + world_origin).min(0))
    model = ifcopenshell.open(job["output"])
    space = model.by_type("IfcSpace")[0]
    placement = ifcopenshell.util.placement.get_local_placement(space.ObjectPlacement)
    assert np.linalg.norm(placement[:3, 3] - world_origin) < 5
    assert len(model.by_type("IfcSpace")) == 2
    report = json.loads((tmp_path / "result.fuzzyresf" / "validation.json").read_text())
    assert all(abs(m.get("volume_error_m3", 0)) < 0.01 for m in report["meshes"])


def test_existing_output_preserved(tmp_path, two_rooms):
    job = request(tmp_path, two_rooms)
    output = Path(job["output"])
    output.write_bytes(b"original IFC bytes")
    with pytest.raises(ValueError, match="esistente"):
        run(job)
    assert output.read_bytes() == b"original IFC bytes"


@pytest.mark.parametrize("bad", ["nan", "flat", "truncated", "budget"])
def test_invalid_input_publishes_nothing(tmp_path, two_rooms, bad):
    points = two_rooms.copy()
    if bad == "nan":
        points[0, 0] = np.nan
    if bad == "flat":
        points[:, 2] = 0.0
    job = request(tmp_path, points)
    if bad == "truncated":
        with Path(job["input"]["path"]).open("ab") as f:
            f.write(b"x")
    if bad == "budget":
        job["limits"] = {"max_voxels": 500}
    with pytest.raises(ValueError):
        run(job)
    assert not Path(job["output"]).exists()
    assert not (tmp_path / "result.fuzzyresf").exists()
    assert not list(tmp_path.glob(".qfuzzyresf-*"))


def test_cli_error_is_structured(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "backend/run_pipeline.py"),
            "--input",
            str(tmp_path / "missing.las"),
            "--output",
            str(tmp_path / "x.ifc"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    event = json.loads(result.stdout)
    assert event["event"] == "error"
    assert not (tmp_path / "x.ifc").exists()


def test_cancel_cleans_staging(tmp_path, two_rooms, monkeypatch):
    import fuzzyresf.cli as cli

    job = request(tmp_path, two_rooms)

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "progress", cancel)
    with pytest.raises(KeyboardInterrupt):
        run(job)
    assert not Path(job["output"]).exists()
    assert not list(tmp_path.glob(".qfuzzyresf-*"))


def test_profiles_share_proposals(tmp_path, two_rooms):
    job = request(tmp_path, two_rooms)
    P, N, PL, audit = analyse(
        Path(job["input"]["path"]), "xyz64", 1.0, 200_000, lambda *a: None
    )
    results = [
        infer(P, N, PL, audit, profile=p)
        for p in ["monotone", "legacy", "geometry_only"]
    ]
    geometry = results[2]
    assert [p["id"] for p in results[0]["proposals"]] == [
        p["id"] for p in geometry["proposals"]
    ]
    assert sum(p["accepted"] for p in results[0]["proposals"]) <= sum(
        p["accepted"] for p in geometry["proposals"]
    )
    assert not any(r["reference_used"] for r in results)


@pytest.mark.parametrize("extension", ["las", "laz"])
def test_real_input_formats_and_unicode_path(tmp_path, two_rooms, extension):
    import laspy

    source = tmp_path / f"Edificio città.{extension}"
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [0.0001, 0.0001, 0.0001]
    cloud = laspy.LasData(header)
    cloud.x, cloud.y, cloud.z = two_rooms.T
    cloud.write(source)
    job = {
        "schema_version": 1,
        "input": {"path": str(source)},
        "output": str(tmp_path / "BIM città.ifc"),
    }
    assert run(job) == 0
    model = ifcopenshell.open(job["output"])
    assert len(model.by_type("IfcSpace")) == 2
    assert len(model.by_type("IfcWall")) == 5


def test_ifc_validation_failure_never_publishes(tmp_path, two_rooms, monkeypatch):
    import fuzzyresf.ifc as ifc

    def failed_validation(inference, candidate):
        candidate.write_text("partial STEP model")
        raise ValueError("invalid solid")

    monkeypatch.setattr(ifc, "build_ifc", failed_validation)
    job = request(tmp_path, two_rooms)
    with pytest.raises(ValueError, match="invalid solid"):
        run(job)
    assert not Path(job["output"]).exists()
    assert not (tmp_path / "result.fuzzyresf").exists()
    assert not list(tmp_path.glob(".qfuzzyresf-*"))
