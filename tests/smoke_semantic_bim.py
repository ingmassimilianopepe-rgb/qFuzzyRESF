from __future__ import annotations

from pathlib import Path
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fuzzy_resf_core import reconstruct_model
from ifc_export import write_ifc


def make_room(path: Path) -> None:
    rng = np.random.default_rng(17)
    pts = []

    # 6 x 4 x 2.8 m room. Door on y=0, window on y=4.
    for wall in ("y0", "y4", "x0", "x6"):
        for _ in range(16000):
            if wall in ("y0", "y4"):
                x = rng.uniform(0.0, 6.0)
                z = rng.uniform(0.0, 2.8)
                if wall == "y0" and 2.2 < x < 3.1 and z < 2.1:
                    continue
                if wall == "y4" and 1.0 < x < 2.5 and 0.9 < z < 2.0:
                    continue
                y = (0.0 if wall == "y0" else 4.0) + rng.normal(0.0, 0.005)
            else:
                y = rng.uniform(0.0, 4.0)
                z = rng.uniform(0.0, 2.8)
                x = (0.0 if wall == "x0" else 6.0) + rng.normal(0.0, 0.005)
            pts.append((x, y, z))

    # Horizontal floor samples must not create false interior walls.
    for _ in range(6000):
        pts.append((rng.uniform(0, 6), rng.uniform(0, 4), rng.normal(0, 0.003)))

    np.savetxt(path, np.asarray(pts), fmt="%.6f")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cloud = td / "room.xyz"
        make_room(cloud)
        request = {
            "input": str(cloud),
            "output": str(td),
            "plane_spacing_m": 0.03,
            "plane_tolerance_m": 0.04,
            "raster_cell_m": 0.06,
            "angle_step_deg": 2.0,
            "confidence_threshold": 0.45,
            "max_orientation_families": 8,
            "max_points": 200000,
            "topology": True,
            "occlusion": True,
            "fuzzy": True,
            "instance_consolidation": True,
            "export_ifc": True,
        }
        model, summary = reconstruct_model(request)
        assert summary["modeled_walls"] >= 4, summary
        assert summary["doors"] >= 1, summary
        assert summary["windows"] >= 1, summary
        assert summary["slabs"] >= 2, summary

        ifc = td / "semantic.ifc"
        write_ifc(model, ifc)
        text = ifc.read_text(encoding="utf-8").upper()
        assert ifc.stat().st_size > 5000
        assert text.count("IFCWALLSTANDARDCASE(") >= 4
        assert "IFCDOOR(" in text
        assert "IFCWINDOW(" in text
        assert "IFCOPENINGELEMENT(" in text
        assert text.count("IFCSLAB(") >= 2

    print("semantic BIM smoke test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
