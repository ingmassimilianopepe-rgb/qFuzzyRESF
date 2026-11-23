# qFuzzyRESF — point cloud to BIM IFC

**Version 0.4.0 · experimental method 0.6.** A native C++/Qt plugin for CloudCompare 2.14 / Qt 6, with a local Python engine and an English interface.

The research branch adds **method 0.7 local opposing-face profiles**. These are
source code for integration and testing; this branch does not supply a newly
compiled Windows plugin. See [the local refinement contract](docs/LOCAL_REFINEMENT.md).

## Changes

- Polygon-only normalization after every fixed-precision wall overlay addresses mixed polygon/line GEOS failures found on three supplied scenes.
- Wall roof cuts use partitioned parametric sweeps, full-plane clipping and Boolean union. A declared 2 mm overlap within the original wall footprint avoids coincident Boolean seams; it can change the top within this narrow seam. Mesh closure, positive volume and EXPRESS checks remain mandatory.
- Four legacy profiles separate geometric reconstruction from candidate selection: **Geometry only**, **Monotone fuzzy baseline**, **Evidence support**, and **Relational hypotheses**. Two experimental local opposing-face profiles add a matched mean/minimum comparison.
- Experimental support intervals distinguish missing support from negative evidence. A bounded mixed-integer solver selects finite candidate sets using neighbouring-wall compatibility and duplicate conflicts. This is not global building-geometry optimization or a learned/calibrated classifier.
- Reports separate exported geometry from exterior candidates discarded during fusion and explicitly identify partial surface models.

One **Generate IFC** workflow evaluates supported rooms and additional observed surfaces. The main interior level still assumes predominantly orthogonal walls and a supported horizontal floor/ceiling pair. Multiple-storey spaces, curved rooms, doors/windows, stairs and complete global envelope closure remain unresolved. No superiority over published methods is claimed. See [method](docs/METHOD.md), [research scope](docs/RESEARCH_DIRECTION.md), and [paired protocol](docs/EXPERIMENT_PROTOCOL.md).

## Windows quick start

1. Extract the entire compiled ZIP into a **new folder**. Start its bundled `CloudCompare.exe`; the DLL requires the matching CloudCompare build.
2. Reuse your existing Python environment. If dependencies are missing, install Python **3.12 x64** and run **INSTALL_qFuzzyRESF.cmd** beside CloudCompare.exe. It creates/reuses `%LOCALAPPDATA%\qFuzzyRESF\.venv` and finds requirements independently of the working folder.
3. Open and select exactly one point cloud in the DB tree. Open **Plugins → qFuzzyRESF: Generate BIM IFC** and confirm the title shows **0.4.0**.
4. Confirm original coordinate units, choose a profile and a **new** `.ifc` filename. Monotone fuzzy remains the baseline default; Evidence and Relational are explicit experimental comparisons.
5. Click **Generate IFC**. Review the model and diagnostics in an independent IFC viewer. Valid individual solids do not guarantee a complete or globally continuous building.

Z must be vertical. Global Shift/Scale is restored. Processing stays local; temporary XYZ64 export requires about 24 bytes per point. The default 3 million occupied-voxel budget may be raised if RAM permits. Cancellation stops the child process, existing files are never overwritten, and failed validation never publishes a partial IFC.

## Profiles and command line

```bash
python backend/run_pipeline.py --check
python backend/run_pipeline.py --input building.las --output building.ifc --profile relational
```

| Profile | Role |
| --- | --- |
| `geometry_only` | Geometric baseline with the same repair and regularization stages |
| `monotone` | Previous weighted-membership gate, preserved for comparison |
| `evidence` | Expert geometric-mean fit support, observed-support fraction and ignorance bounds |
| `relational` | Same evidence plus joint finite-candidate selection using wall relations |
| `experimental_local_continuous` | Geometry-only seed, locally observed opposing-face candidates, mean support utility |
| `experimental_local_fuzzy` | Identical candidates and guards, minimum support utility |

Evidence/relational values are **EXPERT_UNCALIBRATED**, not probabilities or statistical confidence intervals. Low observation coverage is recorded as unknown. No scanner-origin/free-space evidence is fabricated. Solver time/resource limits and deterministic fallback are logged; multiple optimal solutions can differ across solver versions.

LAS, LAZ and plugin XYZ64 are supported. `unified` is the default mode; `interior`/`exterior` remain CLI-only compatibility options. `legacy` is supported only for explicit interior jobs.

## Outputs and verification

`name.ifc` contains IFC4 parametric sweeps and Boolean operations. `name.fuzzyresf` contains `audit.json`, `inference.json`, `validation.json`, and `summary.json`, recording input SHA-256, unit conversion, candidates/relations, geometry, scope, warnings and versions. A reference IFC is never an inference input.

See the current PR workflow for compiled-package and native-test outcomes and [validation](docs/VALIDATION.md) for the measured scope. The earlier semiautomatic single-building model includes manually reviewed interpretations and is not an automatic-output ground truth. Object counts are not semantic accuracy scores.

## Authors and licence

Massimiliano Pepe and Donato Palumbo — Department of Engineering and Geology, “G. d’Annunzio” University of Chieti-Pescara. Contact: massimiliano.pepe@unich.it.

GPL-3.0-or-later: [LICENSE](LICENSE). Dependencies retain their licences. User point clouds and reference models are not included.
