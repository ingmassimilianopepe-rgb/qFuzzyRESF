# Building qFuzzyRESF inside CloudCompare

qFuzzyRESF v0.1 is designed as a CloudCompare Standard plugin.

## Requirements

- CloudCompare source tree, target branch compatible with 2.14+
- Qt 6
- CMake 3.20+
- C++17 compiler
- Python 3.10+
- NumPy

## Add the plugin to a CloudCompare source tree

Clone this repository under CloudCompare's Standard plugin directory, for example:

```text
CloudCompare/
└── plugins/
    └── core/
        └── Standard/
            └── qFuzzyRESF/
```

Then enable the plugin in CMake:

```text
PLUGIN_STANDARD_QFUZZYRESF=ON
```

Configure and build CloudCompare normally.

## Python backend

Install the Python dependency:

```bash
python -m pip install -r plugins/core/Standard/qFuzzyRESF/backend/requirements.txt
```

At runtime qFuzzyRESF looks for the backend in:

```text
<CloudCompare application directory>/qFuzzyRESF/backend
```

For development, the most convenient option is to point the plugin directly at the repository backend using:

```bash
# Linux/macOS
export QFUZZYRESF_BACKEND_DIR=/absolute/path/to/qFuzzyRESF/backend

# Windows PowerShell
$env:QFUZZYRESF_BACKEND_DIR='C:\absolute\path\to\qFuzzyRESF\backend'
```

The Python executable can also be selected from the plugin dialog.

## First smoke test

1. Start CloudCompare.
2. Load a point cloud.
3. Select exactly one point cloud in the DB tree.
4. Run **Plugins → Fuzzy-RESF Scan-to-BIM**.
5. Keep the `Fuzzy-RESF` preset.
6. Run the reconstruction.

The plugin creates a temporary run folder containing:

```text
input.xyz
request.json
result.json
walls.csv
fuzzy_resf.ifc
```

and adds the reconstructed hypotheses to the CloudCompare DB tree under:

```text
<source cloud>_FuzzyRESF
├── Permanent
├── OccludedPermanent
├── Clutter
└── Uncertain
```

## Research status

The C++/Qt layer and backend data contract are intended to remain stable. The Python numerical core in v0.1 is a transparent reference implementation of the Fuzzy-RESF stages and is being regression-aligned with the frozen benchmark implementation used for the paper.
