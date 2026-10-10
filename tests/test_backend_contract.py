from pathlib import Path
import csv
import json
import math
import subprocess
import sys

import numpy as np


def make_room_cloud(path: Path) -> None:
    rng = np.random.default_rng(7)
    pts = []

    # Four walls of a 6 x 4 m room, 2.8 m high.
    # South wall: 0.9 x 2.1 m door at floor level.
    for _ in range(12000):
        x = rng.uniform(0.0, 6.0)
        z = rng.uniform(0.0, 2.8)
        if not (2.55 <= x <= 3.45 and 0.0 <= z <= 2.1):
            pts.append((x, rng.normal(0, 0.004), z))

    # North wall: 1.4 x 1.1 m window with 0.9 m sill.
    for _ in range(12000):
        x = rng.uniform(0.0, 6.0)
        z = rng.uniform(0.0, 2.8)
        if not (1.8 <= x <= 3.2 and 0.9 <= z <= 2.0):
            pts.append((x, 4.0 + rng.normal(0, 0.004), z))

    for _ in range(8000):
        y = rng.uniform(0.0, 4.0)
        z = rng.uniform(0.0, 2.8)
        pts.append((rng.normal(0, 0.004), y, z))
        pts.append((6.0 + rng.normal(0, 0.004), y, z))

    # Strong horizontal floor and ceiling levels make storey detection explicit.
    for _ in range(5000):
        pts.append((rng.uniform(0, 6), rng.uniform(0, 4), rng.normal(0, 0.003)))
        pts.append((rng.uniform(0, 6), rng.uniform(0, 4), 2.8 + rng.normal(0, 0.003)))

    # Interior clutter: deliberately short and low; it must not become a BIM wall.
    for _ in range(1000):
        pts.append((rng.uniform(2.0, 3.0), 2.0 + rng.normal(0, 0.01), rng.uniform(0, 1.2)))

    np.savetxt(path, np.asarray(pts), fmt="%.6f")


def test_cli_contract(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    backend = root / "backend"
    cloud = tmp_path / "room.xyz"
    out = tmp_path / "out"
    out.mkdir()
    make_room_cloud(cloud)

    request = {
        "method": "Fuzzy-RESF-BIM",
        "version": "3.0-semantic-topological",
        "preset": "Fuzzy-RESF-BIM 3.0 (Semantic)",
        "input": str(cloud),
        "output": str(out),
        "plane_spacing_m": 0.01,
        "plane_tolerance_m": 0.03,
        "raster_cell_m": 0.05,
        "angle_step_deg": 2.0,
        "confidence_threshold": 0.50,
        "max_orientation_families": 12,
        "max_points": 250000,
        "multi_peak": True,
        "topology": True,
        "occlusion": True,
        "fuzzy": True,
        "instance_consolidation": True,
        "geometric_feedback": True,
        "export_ifc": True,
        "export_diagnostics": True,
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(backend / "fuzzy_resf_cli.py"), "--request", str(request_path)],
        cwd=backend,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    for name in (
        "result.json",
        "bim_model.json",
        "walls.csv",
        "openings.csv",
        "spaces.csv",
        "slabs.csv",
        "resf_candidates.csv",
        "fuzzy_resf.ifc",
    ):
        assert (out / name).exists(), name
    assert (out / "fuzzy_resf.ifc").stat().st_size > 1024

    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "success"
    assert result["version"] == "3.0-semantic-topological"
    # A simple rectangular room should resolve to physical wall instances, not dozens
    # of nearly coincident sheet-like plane hypotheses.
    assert 4 <= result["walls"] <= 6
    assert result["spaces"] >= 1
    assert result["slabs"] >= 2
    assert result["doors"] >= 1
    assert result["windows"] >= 1

    with (out / "walls.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert "state" in rows[0], "walls.csv must remain compatible with CloudCompare visualization"
    assert all(r["state"] in {"Permanent", "OccludedPermanent", "Uncertain", "Clutter"} for r in rows)
    lengths = [
        math.hypot(float(r["x2"]) - float(r["x1"]), float(r["y2"]) - float(r["y1"]))
        for r in rows
    ]
    assert min(lengths) >= 0.55
    assert max(lengths) <= 7.0, "building-wide sheet/plane regression detected"

    model = json.loads((out / "bim_model.json").read_text(encoding="utf-8"))
    assert len(model["walls"]) == result["walls"]
    assert any(o["kind"] == "Door" for o in model["openings"])
    assert any(o["kind"] == "Window" for o in model["openings"])
    assert model["spaces"]
    assert any(s["kind"] == "FLOOR" for s in model["slabs"])
    assert any(s["kind"] == "CEILING" for s in model["slabs"])

    ifc = (out / "fuzzy_resf.ifc").read_text(encoding="utf-8").upper()
    for token in (
        "IFCBUILDINGSTOREY",
        "IFCWALLSTANDARDCASE",
        "IFCOPENINGELEMENT",
        "IFCRELVOIDSELEMENT",
        "IFCRELFILLSELEMENT",
        "IFCDOOR",
        "IFCWINDOW",
        "IFCSLAB",
        "IFCCOVERING",
        "IFCSPACE",
    ):
        assert token in ifc, token
