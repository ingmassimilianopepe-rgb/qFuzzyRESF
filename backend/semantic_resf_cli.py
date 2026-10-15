#!/usr/bin/env python3
"""Explicit stepwise CLI; --request is also used by the CloudCompare v8 preset."""
import argparse
import json
from pathlib import Path
import sys
try:
    from .semantic_pipeline import run_pipeline, STAGES
except ImportError:
    from semantic_pipeline import run_pipeline, STAGES


def main(argv=None):
    parser = argparse.ArgumentParser(description="RESF-FUZZY stepwise semantic reconstruction (metres, Z-up)")
    parser.add_argument("--request", help="JSON request; paths relative to this file")
    parser.add_argument("--input", help="XYZ, NPY, NPZ(points), LAS/LAZ (optional laspy)")
    parser.add_argument("--output", help="New output run directory")
    parser.add_argument("--config", help="JSON parameters")
    parser.add_argument("--semantics", help="NPZ aligned per-class probabilities: wall, furniture, door, window")
    parser.add_argument("--scanner-origins", help="NPY measured origin for every point, or one XYZ origin")
    parser.add_argument("--catalog", help="JSON family catalogue (soft dimensional prior)")
    parser.add_argument("--stop-after", choices=STAGES)
    parser.add_argument("--no-ifc", action="store_true")
    args = parser.parse_args(argv)
    request = {}
    base = Path.cwd()
    if args.request:
        path = Path(args.request).resolve()
        request.update(json.loads(path.read_text(encoding="utf-8")))
        base = path.parent
    if args.config:
        request.update(json.loads(Path(args.config).read_text(encoding="utf-8")))
    for key in ("input", "output", "semantics", "scanner_origins", "stop_after"):
        if getattr(args, key) is not None:
            request[key] = getattr(args, key)
            if key in ("input", "output", "semantics", "scanner_origins"):
                request[key] = str(Path(request[key]).resolve())
    if args.catalog:
        request["family_catalog_path"] = str(Path(args.catalog).resolve())
    if args.no_ifc:
        request["export_ifc"] = False
    if not request.get("input") or not request.get("output"):
        parser.error("--input and --output, or a request containing them, are required")
    for key in ("input", "output", "semantics", "scanner_origins", "family_catalog_path"):
        if request.get(key) and not Path(request[key]).is_absolute():
            request[key] = str((base / request[key]).resolve())
    try:
        result = run_pipeline(request)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
