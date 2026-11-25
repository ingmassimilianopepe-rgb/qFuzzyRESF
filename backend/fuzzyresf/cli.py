"""Versioned JSON job interface and transactional result publication.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import sys
import tempfile
import time


CATALOGUE_PROFILES = {"experimental_catalogue_crisp": "crisp",
                      "experimental_catalogue_continuous": "mean",
                      "experimental_catalogue_fuzzy": "minimum"}


def emit(event, **values):
    print(
        json.dumps({"event": event, **values}, ensure_ascii=False, allow_nan=False),
        flush=True,
    )


def progress(percent, stage):
    emit("progress", percent=percent, stage=stage)


def dependencies():
    versions = {}
    for name in (
        "numpy",
        "scipy",
        "shapely",
        "skimage",
        "laspy",
        "lazrs",
        "ifcopenshell",
        "trimesh",
        "pytest",
    ):
        module = importlib.import_module(name)
        versions[name] = getattr(module, "__version__", "installed")
    return versions


def validate_request(request):
    if request.get("schema_version") != 1:
        raise ValueError("schema_version must be 1.")
    source = request["input"]
    path = Path(source["path"]).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Input file not found.")
    output = Path(request["output"]).expanduser().absolute()
    if output.suffix.lower() != ".ifc":
        raise ValueError("Output file must have the .ifc extension.")
    if not output.parent.is_dir():
        raise ValueError("Output folder does not exist.")
    diagnostics = output.with_suffix(".fuzzyresf")
    if output.exists() or diagnostics.exists():
        raise ValueError(
            "Output or diagnostic folder already exists. Choose a new name; files are never overwritten."
        )
    scale = request.get("meters_per_unit", 1.0)
    if (
        isinstance(scale, bool)
        or not isinstance(scale, (float, int))
        or not math.isfinite(scale)
        or not 1e-6 <= scale <= 1000
    ):
        raise ValueError("Invalid conversion factor to metres.")
    profile = request.get("profile", "monotone")
    if profile not in {"monotone", "legacy", "geometry_only", "evidence", "relational", "experimental_local_continuous", "experimental_local_fuzzy", *CATALOGUE_PROFILES}:
        raise ValueError("Unknown profile.")
    mode = request.get("mode", "unified")
    if mode not in {"unified", "interior", "exterior"}:
        raise ValueError("Unknown mode: use unified (default), interior or exterior.")
    if (profile.startswith("experimental_local_") or profile in CATALOGUE_PROFILES) and mode != "unified":
        raise ValueError("Experimental local/catalogue inference requires unified mode.")
    if "catalogue" in request and profile not in CATALOGUE_PROFILES:
        raise ValueError("catalogue is supported only by experimental_catalogue profiles.")
    if mode != "interior" and profile == "legacy":
        raise ValueError("The legacy profile is only supported by the interior research pipeline.")
    limits = request.get("limits", {})
    for name, default, minimum, maximum in [
        ("max_voxels", 3_000_000, 500, 20_000_000),
        ("max_raster_cells", 4_000_000, 1000, 20_000_000),
    ]:
        value = limits.get(name, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} is outside [{minimum}, {maximum}].")
        limits[name] = value
    fmt = source.get("format", path.suffix[1:].lower())
    if fmt not in {"las", "laz", "xyz64"}:
        raise ValueError("Unsupported input format.")
    return path, output, diagnostics, float(scale), profile, limits, fmt


def write_json(path, value):
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def run(request):
    path, output, diagnostics, scale, profile, limits, fmt = validate_request(request)
    catalogue = None
    catalogue_source = None
    catalogue_bytes = None
    if profile in CATALOGUE_PROFILES:
        from .catalogue_semantics import validate_catalogue
        catalogue_path = Path(request.get("catalogue") or Path(__file__).with_name("catalogue_default.json")).expanduser().resolve()
        # Freeze exactly the bytes that were validated, before any cloud processing.
        catalogue_bytes = catalogue_path.read_bytes()
        catalogue = validate_catalogue(json.loads(catalogue_bytes.decode("utf-8")))
        catalogue_source = {"path": str(catalogue_path), "sha256": hashlib.sha256(catalogue_bytes).hexdigest(),
                            "catalogue_id": catalogue["catalogue_id"]}
    versions = dependencies()
    from .cloud import analyse
    from .inference import infer
    from .ifc import build_ifc

    mode = request.get("mode", "unified")
    start = time.perf_counter()
    # Same filesystem for atomic hard-link publication. Existing names never replaced.
    with tempfile.TemporaryDirectory(
        prefix=".qfuzzyresf-", dir=request.get("work_dir", output.parent)
    ) as folder:
        work = Path(folder)
        P, N, PL, audit = analyse(
            path,
            fmt,
            scale,
            limits["max_voxels"],
            progress,
            request["input"].get("count"),
        )
        audit["source"] = request["input"].get("name", path.name)
        audit["cloudcompare"] = request.get("cloudcompare", {})
        audit["mode"] = mode
        warnings = [
            "Experimental reconstruction: review inferred thicknesses, connections and room semantics.",
            "Wall completion can cover real openings. Doors and windows are not classified.",
        ]
        if profile in {"evidence", "relational"}:
            warnings.append("Expert uncalibrated hypothesis selection; support scores are not probabilities. No reference model is used.")
        for warning in warnings:
            emit("warning", message=warning)
        if mode == "unified":
            from .unified import infer_unified
            from .exterior_ifc import build_exterior_ifc
            seed_profile = "geometry_only" if profile.startswith("experimental_local_") or profile in CATALOGUE_PROFILES else profile
            inference = infer_unified(P, N, PL, audit, seed_profile, limits["max_raster_cells"], progress)
            if profile.startswith("experimental_local_"):
                from .local_pairs import refine
                progress(70, "Checking locally observed opposing wall faces")
                inference = refine(inference, P, N, PL,
                                   "minimum" if profile.endswith("fuzzy") else "mean")
                inference["profile"] = profile
                inference["method_version"] = "0.7 experimental local"
                warning = ("Experimental G-seeded local refinement uses 5 cm voxel evidence. "
                           "Physical thickness remains unverified; rooms are re-clipped, not re-segmented. "
                           "Internal strip validation is not an independent test.")
                warnings.append(warning)
                emit("warning", message=warning)
            if profile in CATALOGUE_PROFILES:
                from .catalogue_semantics import infer_catalogue
                progress(70, "Selecting observed geometry and catalogue family hypotheses")
                inference = infer_catalogue(inference, P, N, PL, catalogue, CATALOGUE_PROFILES[profile])
                inference["profile"] = profile
                inference["method_version"] = "0.8 experimental catalogue"
                inference["catalogue_source"] = catalogue_source
                audit["catalogue_source"] = catalogue_source
                inference["catalogue_scope"] = "GENERAL_REFERENCE_PRIOR; family hypothesis only; physical thickness and materials UNKNOWN; partial surfaces UNKNOWN"
                warning = ("Experimental catalogue family hypotheses use general reference examples, not as-built facts or regulations. "
                           "Physical thickness and materials remain UNKNOWN; doors and windows are not reconstructed.")
                warnings.append(warning)
                emit("warning", message=warning)
            builder = build_ifc if "main_level" in inference else build_exterior_ifc
            for decision in inference["decisions"]:
                if decision["status"] in {"NOT_SUPPORTED", "NOT_RECONSTRUCTED"}:
                    warning = "Rooms not generated: " + decision["reason"]
                    warnings.append(warning)
                    emit("warning", message=warning)
        elif mode == "exterior":
            from .exterior import infer_exterior
            from .exterior_ifc import build_exterior_ifc
            inference = infer_exterior(P, N, PL, audit, profile, limits["max_raster_cells"], progress)
            from .topology import regularize_structure
            inference = regularize_structure(inference, P, N, PL)
            builder = build_exterior_ifc
        else:
            inference = infer(P, N, PL, audit, profile, limits["max_raster_cells"], progress)
            inference["mode"] = "interior"
            builder = build_ifc
        del P, N, PL
        write_json(work / "audit.json", audit)
        write_json(work / "inference.json", inference)
        progress(75, "Creating and validating IFC4")
        candidate = work / "model.ifc"
        validation = builder(inference, candidate)
        lengths = validation["space_side_boundary_length_m"]
        virtual_fraction = lengths.get("virtual", 0) / max(sum(lengths.values()), 1e-12)
        if mode == "exterior" and not validation["counts"]["IfcRoof"]:
            warning = "No supported roof: exported wall candidates only."
            warnings.append(warning)
            emit("warning", message=warning)
        if virtual_fraction > 0.3:
            warning = f"{virtual_fraction:.1%} of the room side perimeter is virtual: room topology requires review."
            warnings.append(warning)
            emit("warning", message=warning)
        write_json(work / "validation.json", validation)
        summary = {
            "tool_version": "0.4.0",
            "mode": mode,
            "method_version": inference.get("method_version", "0.6"),
            "schema_version": 1,
            "profile": profile,
            "profile_calibration": "EXPERT_UNCALIBRATED" if profile in {"evidence", "relational"} or profile.startswith("experimental_local_") or profile in CATALOGUE_PROFILES else "EXPERT_BASELINE",
            "model_scope": "SUPPORTED_MAIN_LEVEL_WITH_ADDITIONAL_SURFACES" if "main_level" in inference else "PARTIAL_OBSERVED_SURFACES",
            "rooms_supported": "main_level" in inference,
            "global_envelope_certified": False,
            "source_sha256": audit["sha256"],
            "ifc_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "counts": validation["counts"],
            "space_area_m2": validation["space_area_m2"],
            "virtual_side_fraction": virtual_fraction,
            "warnings": warnings,
            "reference_used": False,
            "python": platform.python_version(),
            "dependencies": versions,
            "runtime_s": time.perf_counter() - start,
        }
        if catalogue_source is not None:
            summary["catalogue_source"] = catalogue_source
            summary["catalogue_scope"] = inference["catalogue_scope"]
            (work / "catalogue_source.json").write_bytes(catalogue_bytes)
            write_json(work / "catalogue_snapshot.json", catalogue)
        write_json(work / "summary.json", summary)
        # mkdir is the exclusive claim on a result name; clean up only our own files.
        diagnostics.mkdir()
        linked = False
        try:
            for name in (
                "audit.json",
                "inference.json",
                "validation.json",
                "summary.json",
            ):
                shutil.copy2(work / name, diagnostics / name)
            if catalogue_source is not None:
                for name in ("catalogue_source.json", "catalogue_snapshot.json"):
                    shutil.copy2(work / name, diagnostics / name)
            os.link(candidate, output)  # fails if another writer created output
            linked = True
        except BaseException:
            shutil.rmtree(diagnostics)
            raise
        if linked:
            progress(100, "IFC validated and saved")
            emit("result", output=str(output), diagnostics=str(diagnostics), **summary)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="qFuzzyRESF: point cloud to IFC4 (pilot)"
    )
    parser.add_argument(
        "--check", action="store_true", help="Check Python dependencies"
    )
    parser.add_argument("--request", type=Path, help="Job JSON schema 1")
    parser.add_argument("--input", type=Path, help="LAS/LAZ or XYZ64 cloud")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--meters-per-unit", type=float, default=1.0)
    parser.add_argument(
        "--profile", choices=["monotone", "legacy", "geometry_only", "evidence", "relational", "experimental_local_continuous", "experimental_local_fuzzy", *CATALOGUE_PROFILES], default="monotone"
    )
    parser.add_argument("--catalogue", type=Path, help="Optional typed JSON catalogue for experimental_catalogue profiles")
    parser.add_argument("--mode", choices=["unified", "interior", "exterior"], default="unified")
    parser.add_argument("--max-voxels", type=int, default=3_000_000)
    args = parser.parse_args(argv)

    def interrupted(signum, frame):
        raise KeyboardInterrupt("Processing cancelled")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.check:
            emit("ready", python=sys.executable, versions=dependencies())
            return 0
        if args.request:
            request = json.loads(args.request.read_text(encoding="utf-8"))
        else:
            if not args.input or not args.output:
                parser.error("Provide --request or both --input and --output")
            request = {
                "schema_version": 1,
                "input": {"path": str(args.input)},
                "output": str(args.output),
                "meters_per_unit": args.meters_per_unit,
                "profile": args.profile,
                "mode": args.mode,
                "limits": {"max_voxels": args.max_voxels},
            }
        if args.catalogue is not None:
            request["catalogue"] = str(args.catalogue)
        return run(request)
    except KeyboardInterrupt:
        emit(
            "cancelled",
            message="Processing cancelled. No incomplete IFC published.",
        )
        return 130
    except Exception as exc:
        emit("error", message=f"{type(exc).__name__}: {exc}")
        return 1
