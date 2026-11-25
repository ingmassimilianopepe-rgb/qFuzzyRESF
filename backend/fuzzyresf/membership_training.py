"""Standalone, building-held-out fitting of monotone pair membership functions.

This module deliberately has no dependency on, or integration with, inference.
The resulting score is a fuzzy grade, not a calibrated probability.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, log_expit

COLUMNS = (
    "building_id", "scene_id", "pair_id", "angle_deg", "residual_p95_m",
    "shared_length_fraction", "shared_z_m", "label_same_wall", "annotation_source",
)
FEATURES = COLUMNS[3:7]
SCALES = np.array([15.0, 0.1, 1.0, 3.0])
DIRECTIONS = np.array([-1.0, -1.0, 1.0, 1.0])
INITIAL = np.array([1.0, 1.0, 0.5, 1.0, 0.0, 0.0, 0.0, 0.0])
LAMBDAS = (0.1, 0.01, 0.001)


def _json_write(path: Path, value: Any) -> bytes:
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    return data


def _inventory(raw: bytes) -> tuple[list[dict[str, str]], set[str]]:
    """Read lexical CSV records only; do not interpret any held-out numeric cell."""
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise ValueError("CSV must have the exact documented columns in order")
    rows = list(reader)
    if not rows:
        raise ValueError("CSV contains no annotation rows")
    buildings: set[str] = set()
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("CSV rows must have exactly nine cells")
        building = row["building_id"]
        if not building or building != building.strip():
            raise ValueError("building_id must be nonempty with no surrounding whitespace")
        buildings.add(building)
    return rows, buildings


def validate_roles(roles: Any, buildings: set[str]) -> dict[str, list[str]]:
    if not isinstance(roles, dict) or set(roles) != {"train", "validation", "test"}:
        raise ValueError("roles must contain exactly train, validation, and test")
    seen: set[str] = set()
    result = {}
    for role in ("train", "validation", "test"):
        ids = roles[role]
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i or i != i.strip() for i in ids):
            raise ValueError(f"{role} must be a nonempty list of physical building IDs")
        if len(ids) != len(set(ids)) or seen.intersection(ids):
            raise ValueError("building roles must be disjoint and contain no duplicate IDs")
        seen.update(ids)
        result[role] = sorted(ids)
    if seen != buildings:
        raise ValueError("building roles must cover exactly every CSV building_id")
    return result


def parse_role(rows: list[dict[str, str]], building_ids: list[str], dataset_kind: str,
               require_both_classes: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Validate numeric data only for the explicitly requested building role."""
    selected = set(building_ids)
    features, labels, groups = [], [], []
    keys = set()
    prefixes = ("manual:", "independent_measurement:") if dataset_kind == "real_annotations" else ("synthetic:",)
    for row in rows:
        if row["building_id"] not in selected:
            continue
        for field in ("scene_id", "pair_id"):
            if not row[field] or row[field] != row[field].strip():
                raise ValueError(f"{field} must be nonempty with no surrounding whitespace")
        key = tuple(row[field] for field in COLUMNS[:3])
        if key in keys:
            raise ValueError(f"duplicate building/scene/pair key: {key}")
        keys.add(key)
        source = row["annotation_source"]
        if not any(source.startswith(prefix) and source[len(prefix):].strip() for prefix in prefixes):
            raise ValueError(f"annotation_source requires nonempty {prefixes} provenance for {dataset_kind}")
        try:
            values = [float(row[field]) for field in FEATURES]
        except (ValueError, TypeError) as exc:
            raise ValueError("features must be numeric, in degrees and metres as named") from exc
        if not np.isfinite(values).all():
            raise ValueError("features must be finite")
        angle, residual, fraction, shared_z = values
        if not (0 <= angle <= 90 and residual >= 0 and 0 <= fraction <= 1 and shared_z >= 0):
            raise ValueError("invalid physical feature bounds: angle [0,90] degrees, residual/Z >=0 metres, fraction [0,1]")
        if row["label_same_wall"] not in ("0", "1"):
            raise ValueError("label_same_wall must be exactly 0 or 1")
        features.append(values)
        labels.append(int(row["label_same_wall"]))
        groups.append(row["building_id"])
    if not features:
        raise ValueError("each building role must contain annotation rows")
    if require_both_classes and set(labels) != {0, 1}:
        raise ValueError("training and validation roles must each contain both binary classes")
    return np.asarray(features), np.asarray(labels), np.asarray(groups)


def grades(features: np.ndarray, parameters: np.ndarray) -> np.ndarray:
    normalized = features / SCALES
    logits = DIRECTIONS * np.exp(parameters[4:]) * (normalized - parameters[:4])
    return np.exp(np.mean(log_expit(logits), axis=1))


def building_macro_mse(y: np.ndarray, predicted: np.ndarray, groups: np.ndarray) -> float:
    return float(np.mean([np.mean((predicted[groups == b] - y[groups == b]) ** 2) for b in np.unique(groups)]))


def fit(train: tuple, validation: tuple) -> tuple[np.ndarray, float, list[dict[str, Any]]]:
    x, y, groups = train
    vx, vy, vg = validation
    trials = []
    candidates = []
    for regularization in LAMBDAS:
        def objective(parameters: np.ndarray) -> float:
            return building_macro_mse(y, grades(x, parameters), groups) + regularization * float(np.mean((parameters - INITIAL) ** 2))
        result = minimize(objective, INITIAL.copy(), method="L-BFGS-B",
                          bounds=[(-10.0, 1000.0)] * 4 + [(-4.0, 4.0)] * 4,
                          options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-7})
        if not result.success or not np.isfinite(result.fun) or not np.isfinite(result.x).all():
            raise ValueError(f"optimizer failed for regularization {regularization}: {result.message}")
        score = building_macro_mse(vy, grades(vx, result.x), vg)
        trials.append({"regularization": regularization, "validation_building_macro_grade_mse": score,
                       "training_regularized_objective": float(result.fun), "iterations": int(result.nit)})
        candidates.append((score, len(candidates), result.x.copy(), regularization))
    _, _, parameters, regularization = min(candidates, key=lambda candidate: candidate[:2])
    return parameters, regularization, trials


def evaluate(features: np.ndarray, labels: np.ndarray, groups: np.ndarray, parameters: np.ndarray) -> dict[str, Any]:
    scores = grades(features, parameters)
    predictions = scores >= 0.5
    by_building = {}
    for building in np.unique(groups):
        mask = groups == building
        actual, predicted = labels[mask], predictions[mask]
        tp = int(np.sum((actual == 1) & predicted))
        fp = int(np.sum((actual == 0) & predicted))
        fn = int(np.sum((actual == 1) & ~predicted))
        tn = int(np.sum((actual == 0) & ~predicted))
        recall = tp / (tp + fn) if tp + fn else None
        specificity = tn / (tn + fp) if tn + fp else None
        by_building[str(building)] = {
            "pair_count": int(mask.sum()), "true_positive": tp, "false_positive": fp,
            "false_negative": fn, "true_negative": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": recall,
            "balanced_accuracy": (recall + specificity) / 2 if recall is not None and specificity is not None else None,
            "grade_mse": float(np.mean((scores[mask] - actual) ** 2)),
        }
    macro, eligible = {}, {}
    for metric in ("precision", "recall", "balanced_accuracy", "grade_mse"):
        available = [report[metric] for report in by_building.values() if report[metric] is not None]
        macro[metric] = float(np.mean(available)) if available else None
        eligible[metric] = len(available)
    return {"threshold": 0.5, "per_building": by_building, "building_macro": macro,
            "macro_eligible_buildings": eligible, "uncertainty": "No confidence intervals; pairs are not assumed independent."}


def run(annotations: Path, roles_path: Path, output: Path, dataset_kind: str) -> dict[str, Any]:
    if dataset_kind not in ("real_annotations", "synthetic_demo"):
        raise ValueError("dataset_kind must be real_annotations or synthetic_demo")
    # Snapshot and seal raw source bytes before any fitting. All subsequent reads
    # use these immutable snapshots, even if the source file changes on disk.
    raw = annotations.read_bytes()
    roles_raw = roles_path.read_bytes()
    rows, buildings = _inventory(raw)
    roles = validate_roles(json.loads(roles_raw), buildings)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "dataset_kind": dataset_kind, "synthetic_software_only": dataset_kind == "synthetic_demo",
        "source": {"annotations_path": str(annotations.resolve()), "annotations_sha256": hashlib.sha256(raw).hexdigest(),
                   "roles_path": str(roles_path.resolve()), "roles_sha256": hashlib.sha256(roles_raw).hexdigest()},
        "building_roles": roles,
        "physical_building_identity": "Supplied by user; distinct identifiers must denote distinct physical buildings.",
    }
    _json_write(output / "source_manifest.json", manifest)
    frozen = False
    try:
        train = parse_role(rows, roles["train"], dataset_kind, require_both_classes=True)
        validation = parse_role(rows, roles["validation"], dataset_kind, require_both_classes=True)
        parameters, regularization, trials = fit(train, validation)
        model = {**manifest, "format_version": 1, "model_type": "monotone_sigmoid_geometric_mean",
                 "parameters": {"centers_normalized": parameters[:4].tolist(), "log_slopes": parameters[4:].tolist()},
                 "config": {"features": list(FEATURES), "feature_scales": SCALES.tolist(), "directions": DIRECTIONS.tolist(),
                            "regularization_candidates": list(LAMBDAS), "selected_regularization": regularization,
                            "regularization_reference": INITIAL.tolist(), "optimizer": "scipy.optimize.minimize:L-BFGS-B",
                            "optimizer_bounds": [[-10.0, 1000.0]] * 4 + [[-4.0, 4.0]] * 4,
                            "maxiter": 1000, "ftol": 1e-12, "gtol": 1e-7,
                            "objective": "equal-building mean squared grade error + lambda * mean((parameters-reference)^2)",
                            "selection": "lowest validation building-macro grade MSE; first candidate wins exact ties",
                            "threshold": 0.5, "probability_calibration": False}, "validation_selection": trials,
                 "inference_integration": False}
        model_bytes = _json_write(output / "fitted_model.json", model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()
        with (output / "model.sha256").open("x") as handle:
            handle.write(model_sha + "  fitted_model.json\n")
            handle.flush()
            os.fsync(handle.fileno())
        frozen = True
        # The model and its SHA have been written and flushed before this call.
        test = parse_role(rows, roles["test"], dataset_kind)
        report = {"fitted_model_sha256": model_sha, **evaluate(*test, parameters)}
        _json_write(output / "test_evaluation.json", report)
        _json_write(output / "audit.json", {"status": "evaluated", "model_frozen_before_test_parse": True,
                                           "fitted_model_sha256": model_sha})
        return report
    except Exception as exc:
        _json_write(output / "audit.json", {"status": "error", "model_frozen_before_test_parse": frozen,
                                           "error_type": type(exc).__name__, "error": str(exc)})
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--roles", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dataset-kind", required=True, choices=("real_annotations", "synthetic_demo"))
    args = parser.parse_args(argv)
    try:
        report = run(args.annotations, args.roles, args.output, args.dataset_kind)
    except (OSError, ValueError, UnicodeError, csv.Error) as exc:
        parser.exit(2, f"membership training failed: {exc}\n")
    print(json.dumps({"output": str(args.output.resolve()), "fitted_model_sha256": report["fitted_model_sha256"],
                      "dataset_kind": args.dataset_kind}, sort_keys=True))
    return 0
