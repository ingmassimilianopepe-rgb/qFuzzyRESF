"""Software-only checks; the generated labels are not real annotation evidence."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from fuzzyresf import membership_training as learner


@pytest.fixture
def annotation_files(tmp_path):
    rows = []
    for building in ("physical_a", "physical_b", "physical_c"):
        for index in range(8):
            positive = index % 2 == 0
            rows.append(dict(zip(learner.COLUMNS, [building, "scene", str(index),
                "2" if positive else "45", "0.01" if positive else "0.5",
                "0.9" if positive else "0.1", "2.8" if positive else "0.2",
                "1" if positive else "0", "synthetic:unit-test-generator"])))
    csv_path = tmp_path / "annotations.csv"
    roles_path = tmp_path / "roles.json"
    roles_path.write_text(json.dumps({"train": ["physical_a"], "validation": ["physical_b"], "test": ["physical_c"]}))
    def save():
        with csv_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=learner.COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    save()
    return csv_path, roles_path, rows, save


def test_actual_cli_synthetic_three_physical_groups(annotation_files, tmp_path):
    annotations, roles, _, _ = annotation_files
    output = tmp_path / "cli_fit"
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / "scripts/train_memberships.py"),
               "--annotations", str(annotations), "--roles", str(roles),
               "--output", str(output), "--dataset-kind", "synthetic_demo"]
    result = subprocess.run(command, cwd=root, env=os.environ.copy(), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    model_raw = (output / "fitted_model.json").read_bytes()
    model = json.loads(model_raw)
    expected_sha = hashlib.sha256(model_raw).hexdigest()
    assert (output / "model.sha256").read_text().split()[0] == expected_sha
    assert json.loads(result.stdout)["fitted_model_sha256"] == expected_sha
    assert model["source"]["annotations_sha256"] == hashlib.sha256(annotations.read_bytes()).hexdigest()
    assert model["synthetic_software_only"] is True
    assert model["inference_integration"] is False
    assert model["config"]["probability_calibration"] is False
    assert sum(len(value) for value in model["parameters"].values()) == 8
    assert len(model["validation_selection"]) == 3
    report = json.loads((output / "test_evaluation.json").read_text())
    assert set(report["per_building"]) == {"physical_c"}
    assert report["threshold"] == 0.5
    assert report["building_macro"]["balanced_accuracy"] == 1.0
    # Re-running cannot alter the completed output directory.
    repeated = subprocess.run(command, cwd=root, env=os.environ.copy(), text=True, capture_output=True)
    assert repeated.returncode != 0
    assert (output / "fitted_model.json").read_bytes() == model_raw


def test_building_overlap_rejected_before_fit(annotation_files, tmp_path, monkeypatch):
    annotations, roles, _, _ = annotation_files
    roles.write_text(json.dumps({"train": ["physical_a"], "validation": ["physical_a", "physical_b"], "test": ["physical_c"]}))
    monkeypatch.setattr(learner, "fit", lambda *args: pytest.fail("must reject split before fitting"))
    with pytest.raises(ValueError, match="disjoint"):
        learner.run(annotations, roles, tmp_path / "fit", "synthetic_demo")
    assert not (tmp_path / "fit").exists()


@pytest.mark.parametrize("column,value,error", [
    ("angle_deg", "91", "physical feature bounds"),
    ("residual_p95_m", "-0.1", "physical feature bounds"),
    ("shared_length_fraction", "1.1", "physical feature bounds"),
    ("shared_z_m", "3 m", "numeric"),
    ("shared_z_m", "nan", "finite"),
    ("annotation_source", "manual:unverified-demo", "provenance"),
])
def test_invalid_units_bounds_and_provenance(annotation_files, column, value, error):
    _, _, rows, _ = annotation_files
    rows[0][column] = value
    with pytest.raises(ValueError, match=error):
        learner.parse_role(rows, ["physical_a"], "synthetic_demo", True)


def test_real_annotations_require_traceable_declared_source(annotation_files):
    _, _, rows, _ = annotation_files
    with pytest.raises(ValueError, match="provenance"):
        learner.parse_role(rows, ["physical_a"], "real_annotations", True)
    for row in rows:
        row["annotation_source"] = "manual:review-log-42"
    assert len(learner.parse_role(rows, ["physical_a"], "real_annotations", True)[0]) == 8


def test_model_frozen_before_any_test_numeric_parse_and_bad_label_preserves_fit(annotation_files, tmp_path, monkeypatch):
    annotations, roles, rows, save = annotation_files
    rows[-1]["label_same_wall"] = "invalid-held-out-label"
    save()
    output = tmp_path / "frozen_fit"
    original = learner.parse_role
    called = []
    def checked_parse(records, building_ids, kind, require_both_classes=False):
        called.append(tuple(building_ids))
        if building_ids == ["physical_c"]:
            assert (output / "fitted_model.json").is_file()
            sha = hashlib.sha256((output / "fitted_model.json").read_bytes()).hexdigest()
            assert (output / "model.sha256").read_text().split()[0] == sha
        return original(records, building_ids, kind, require_both_classes)
    monkeypatch.setattr(learner, "parse_role", checked_parse)
    with pytest.raises(ValueError, match="label_same_wall"):
        learner.run(annotations, roles, output, "synthetic_demo")
    assert called == [("physical_a",), ("physical_b",), ("physical_c",)]
    assert (output / "fitted_model.json").is_file()
    assert not (output / "test_evaluation.json").exists()
    audit = json.loads((output / "audit.json").read_text())
    assert audit["status"] == "error"
    assert audit["model_frozen_before_test_parse"] is True
    assert "label_same_wall" in audit["error"]


def test_buildings_receive_equal_weight_despite_different_pair_counts():
    y = np.zeros(10)
    predicted = np.array([1.0] * 9 + [0.0])
    groups = np.array(["large"] * 9 + ["small"])
    assert learner.building_macro_mse(y, predicted, groups) == pytest.approx(0.5)
    assert np.mean((y - predicted) ** 2) == pytest.approx(0.9)


def test_memberships_remain_monotone_after_parameterization():
    origin = np.array([[10.0, 0.05, 0.5, 1.5]])
    baseline = learner.grades(origin, learner.INITIAL)[0]
    for index, direction in enumerate(learner.DIRECTIONS):
        increased = origin.copy()
        increased[0, index] += learner.SCALES[index] * 0.2
        delta = learner.grades(increased, learner.INITIAL)[0] - baseline
        assert delta * direction > 0
