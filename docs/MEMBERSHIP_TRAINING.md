# Standalone membership fitting

This optional tool fits membership curves from independently annotated plane pairs. It does not change pipeline inference, implement ANFIS, or establish probability calibration. No verified real annotation set or real trained model is distributed with this example. A synthetic run checks software behavior only and is not evidence of accuracy on physical buildings.

Use `docs/examples/pair_annotations_template.csv` as the header-only annotation template. Supply your own annotations; the template deliberately contains no invented labels.

## Annotation contract

The CSV must contain exactly these columns, in this order:

| Column | Meaning |
| --- | --- |
| `building_id` | Stable identifier for one physical building |
| `scene_id` | Scene identifier within that building |
| `pair_id` | Pair identifier within that scene |
| `angle_deg` | Plane normal disagreement, in degrees |
| `residual_p95_m` | Nonnegative 95th-percentile geometric residual, in metres |
| `shared_length_fraction` | Shared support length fraction, from 0 to 1 |
| `shared_z_m` | Nonnegative shared vertical support, in metres |
| `label_same_wall` | Binary ground truth: `1` for the same wall, `0` otherwise |
| `annotation_source` | Traceable annotation provenance |

All numeric values must be finite and physically valid. Do not mix metres with millimetres or degrees with radians. For `--dataset-kind real_annotations`, each provenance value must start with `manual:` or `independent_measurement:` and include a nonempty reference after the colon. For `--dataset-kind synthetic_demo`, it must start with `synthetic:` and include a description or generator reference. These prefixes identify the declared source; they do not independently verify its truth.

The target is whether the two observed surfaces belong to the same physical wall. Review furniture, cabinets, occlusion, and distinct nearby walls explicitly. Arbitrary IFC wall thickness alone cannot establish wall-pair ground truth or distinguish furniture from walls. Use manual review or independent measurements with recorded provenance.

## Physical-building split

Create a roles JSON file, for example:

```json
{
  "train": ["building_a"],
  "validation": ["building_b"],
  "test": ["building_c"]
}
```

Each role must contain at least one building. The roles must be disjoint and cover every CSV building exactly once. The operator must ensure that different IDs refer to genuinely different physical buildings: scenes or aliases from the same building must not cross roles. Both training and validation data must contain both binary classes. The held-out test role is reserved for final evaluation.

## Run

From the repository root, with the backend dependencies available:

```bash
python scripts/train_memberships.py \
  --annotations /absolute/path/pair_annotations.csv \
  --roles /absolute/path/building_roles.json \
  --output /absolute/path/new_fit_output \
  --dataset-kind real_annotations
```

Use `--dataset-kind synthetic_demo` only for generated fixtures with `synthetic:` provenance. Keep generated demonstrations separate from real annotations and report their software-only status. Use a new output directory for each run; existing results are not overwritten.

## Fit and evaluation

The model has eight learned parameters: a location and positive slope for each of four monotone sigmoid memberships. Angle and residual memberships decrease with their features; shared-length and shared-height memberships increase. Their geometric mean is the fuzzy grade `q`. The grade is not a calibrated probability.

Fitting uses deterministic SciPy L-BFGS-B optimization of mean squared grade error, averaged equally across training buildings, plus L2 regularization. The regularization coefficient is selected from `0.1`, `0.01`, and `0.001` by validation building-macro mean squared error. Classification uses the fixed threshold `q >= 0.5`; the threshold is not selected on held-out data.

The tool hashes and seals the raw CSV bytes before fitting. It may read identifiers to check the split inventory, but it does not parse held-out test feature values or labels until the fitted model and its SHA-256 freeze have been written. The saved fit records source hashes, configuration, building roles, dataset kind, and whether the run is synthetic software-only. After freezing, held-out reporting includes per-building precision, recall, balanced accuracy, and grade mean squared error, together with equal-building macro averages. These are descriptive held-out metrics; no confidence interval or point-independence claim is made.

If held-out data fail validation after the freeze, the fitted model remains available and an error audit records the failed evaluation. Do not revise a model using held-out results and continue to describe that role as untouched test data. Gather another independent physical-building holdout for a subsequent evaluation.
