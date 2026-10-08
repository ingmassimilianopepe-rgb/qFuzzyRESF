from pathlib import Path
import json
import subprocess
import sys

import numpy as np


def make_room_cloud(path: Path) -> None:
    rng = np.random.default_rng(7)
    pts = []
    # Four walls of a 6 x 4 m room, 2.8 m high.
    for _ in range(6000):
        x = rng.uniform(0.0, 6.0)
        z = rng.uniform(0.0, 2.8)
        pts.append((x, 0.0 + rng.normal(0, 0.005), z))
        pts.append((x, 4.0 + rng.normal(0, 0.005), z))
    for _ in range(4000):
        y = rng.uniform(0.0, 4.0)
        z = rng.uniform(0.0, 2.8)
        pts.append((0.0 + rng.normal(0, 0.005), y, z))
        pts.append((6.0 + rng.normal(0, 0.005), y, z))
    # floor and mild interior clutter
    for _ in range(3000):
        pts.append((rng.uniform(0, 6), rng.uniform(0, 4), rng.normal(0, 0.003)))
    for _ in range(1500):
        pts.append((rng.uniform(2.0, 3.0), 2.0 + rng.normal(0, 0.01), rng.uniform(0, 1.4)))
    np.savetxt(path, np.asarray(pts), fmt="%.6f")


def test_cli_contract(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    backend = root / "backend"
    cloud = tmp_path / "room.xyz"
    out = tmp_path / "out"
    out.mkdir()
    make_room_cloud(cloud)

    request = {
        "method": "Fuzzy-RESF",
        "version": "1.0",
        "preset": "Fuzzy-RESF",
        "input": str(cloud),
        "output": str(out),
        "plane_spacing_m": 0.03,
        "plane_tolerance_m": 0.04,
        "raster_cell_m": 0.06,
        "angle_step_deg": 5.0,
        "confidence_threshold": 0.45,
        "max_orientation_families": 6,
        "max_points": 200000,
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
    assert (out / "result.json").exists()
    assert (out / "walls.csv").exists()
    assert (out / "fuzzy_resf.ifc").exists()

    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "success"
    assert result["hypotheses"] >= 4
    assert len(result["orientation_families_deg"]) >= 2
