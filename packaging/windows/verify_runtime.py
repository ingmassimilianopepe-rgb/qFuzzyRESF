#!/usr/bin/env python3
"""Runtime probe kept in a file so Windows shell quoting cannot alter Python code."""
import argparse
import importlib
import importlib.metadata
import json
from pathlib import Path
import struct
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("interpreter", "modules"), required=True)
    parser.add_argument("--backend", type=Path)
    args = parser.parse_args(argv)
    report = {
        "executable": sys.executable,
        "version": list(sys.version_info[:3]),
        "bits": struct.calcsize("P") * 8,
        "supported": (3, 10) <= sys.version_info[:2] <= (3, 12)
        and struct.calcsize("P") == 8,
    }
    if not report["supported"]:
        report["error"] = "Serve Python 3.10, 3.11 o 3.12 a 64 bit."
        print(json.dumps(report))
        return 3
    if args.mode == "modules":
        try:
            report["modules"] = {}
            for name in ("numpy", "scipy", "shapely", "ifcopenshell"):
                importlib.import_module(name)
                report["modules"][name] = importlib.metadata.version(name)
            import ifcopenshell
            # Exercises the native IFC binding, not only Python metadata.
            ifcopenshell.file(schema="IFC4").create_entity("IfcWall", GlobalId=ifcopenshell.guid.new())
            if args.backend is None or not (args.backend / "semantic_resf_cli.py").is_file():
                raise RuntimeError("Backend v8 non trovato nel pacchetto.")
            sys.path.insert(0, str(args.backend.resolve()))
            for module in ("fuzzy_resf_cli", "semantic_pipeline", "resf_ifc"):
                importlib.import_module(module)
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            print(json.dumps(report))
            return 4
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
