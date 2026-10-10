from pathlib import Path
import csv
import json
import math
import subprocess
import sys

import numpy as np


def make_fragmented_room(path: Path) -> None:
    rng = np.random.default_rng(17)
    pts = []

    # 8 x 5 x 3 m room. True walls are intentionally interrupted by openings and
    # furniture-scale occlusions: reconstruction must return continuous BIM walls.
    for _ in range(24000):
        x = rng.uniform(0, 8)
        z = rng.uniform(0, 3)
        if 3.55 <= x <= 4.45 and z <= 2.15:  # true door
            continue
        if 0.90 <= x <= 2.25 and 0.25 <= z <= 2.55:  # large occlusion
            continue
        pts.append((x, rng.normal(0, 0.006), z))

    for _ in range(24000):
        x = rng.uniform(0, 8)
        z = rng.uniform(0, 3)
        if 2.00 <= x <= 3.50 and 0.90 <= z <= 2.10:  # true window
            continue
        if 5.10 <= x <= 6.50 and 0.30 <= z <= 2.70:  # large occlusion
            continue
        pts.append((x, 5 + rng.normal(0, 0.006), z))

    for _ in range(16000):
        y = rng.uniform(0, 5)
        z = rng.uniform(0, 3)
        if not (1.20 <= y <= 2.20 and 0.20 <= z <= 2.60):
            pts.append((rng.normal(0, 0.006), y, z))
        if not (3.00 <= y <= 4.00 and 0.20 <= z <= 2.60):
            pts.append((8 + rng.normal(0, 0.006), y, z))

    # Floor and ceiling support the room envelope completion stage.
    for _ in range(12000):
        x = rng.uniform(0, 8)
        y = rng.uniform(0, 5)
        pts.append((x, y, rng.normal(0, 0.004)))
        pts.append((x, y, 3 + rng.normal(0, 0.004)))

    # Strong but low/mid-height planar clutter: these must not become BIM walls.
    for _ in range(5000):
        pts.append((2.2 + rng.normal(0, 0.008), rng.uniform(0.7, 2.0), rng.uniform(0, 1.5)))
    for _ in range(5000):
        pts.append((rng.uniform(4.5, 6.0), 2.6 + rng.normal(0, 0.008), rng.uniform(0, 1.7)))

    np.savetxt(path, np.asarray(pts), fmt="%.6f")


def test_fragmented_room_becomes_continuous_bim(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    backend = root / "backend"
    cloud = tmp_path / "fragmented_room.xyz"
    out = tmp_path / "out"
    out.mkdir()
    make_fragmented_room(cloud)

    request = {
        "method": "Fuzzy-RESF-BIM",
        "version": "3.1-topology-completion",
        "preset": "Fuzzy-RESF-BIM 3.1 (Topology completion)",
        "input": str(cloud),
        "output": str(out),
        "plane_spacing_m": 0.01,
        "plane_tolerance_m": 0.03,
        "raster_cell_m": 0.05,
        "angle_step_deg": 2.0,
        "confidence_threshold": 0.50,
        "max_orientation_families": 12,
        "max_points": 300000,
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

    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "success"
    assert result["version"] == "3.1-topology-completion"
    assert 4 <= result["walls"] <= 5, result
    assert result["spaces"] >= 1
    assert result["slabs"] >= 2
    assert result["doors"] == 1, "furniture/occlusion gaps must not become false doors"
    assert result["windows"] >= 1

    with (out / "walls.csv").open(newline="", encoding="utf-8") as f:
        walls = list(csv.DictReader(f))
    lengths = sorted(
        math.hypot(float(w["x2"]) - float(w["x1"]), float(w["y2"]) - float(w["y1"]))
        for w in walls
    )
    # The four physical room walls must be reconstructed across the removed fragments.
    assert sum(length >= 4.5 for length in lengths) >= 4, lengths
    assert sum(length >= 7.0 for length in lengths) >= 2, lengths
    assert min(lengths) >= 2.0, "short furniture-like planar fragment leaked into BIM"
    assert any(w["source"] in {"coplanar_gap_completion", "horizontal_envelope_completion", "paired_faces"} for w in walls)

    openings = list(csv.DictReader((out / "openings.csv").open(newline="", encoding="utf-8")))
    assert sum(o["kind"] == "Door" for o in openings) == 1
    assert any(o["kind"] == "Window" for o in openings)

    ifc = (out / "fuzzy_resf.ifc").read_text(encoding="utf-8").upper()
    for token in (
        "IFCWALLSTANDARDCASE",
        "IFCOPENINGELEMENT",
        "IFCDOOR",
        "IFCWINDOW",
        "IFCSPACE",
        "IFCSLAB",
    ):
        assert token in ifc, token
