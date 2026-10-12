"""IFC2X3 exporter wrapper for Fuzzy-RESF-BIM v6.5."""
from __future__ import annotations

from pathlib import Path
from ifc_bim_export_v6 import write_bim_ifc as _write_v6
from ifc_bim_export_v6_4 import _inject_stairs


def write_bim_ifc(model, path: str | Path, project_name: str = "Fuzzy-RESF-BIM v6.5") -> None:
    path = Path(path)
    _write_v6(model, path, project_name=project_name)
    _inject_stairs(path, model)
