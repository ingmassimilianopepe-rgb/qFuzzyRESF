# qFuzzyRESF — point cloud to BIM IFC

**Version 0.3.0 · pilot method 0.5.** A native C++/Qt plugin for CloudCompare 2.14 / Qt 6, using a local Python engine. The interface, installer messages and reports are in English.

## What changed

One **Generate IFC** workflow replaces the Interior/Exterior menu. The engine evaluates floor/ceiling evidence, reconstructs supported interior spaces, and combines these with additional observed building surfaces in one IFC. It also runs when only building surfaces are supported, recording why rooms could not be generated.

The geometry now uses regularized boundaries, paired wall faces, bounded wall joins, roof-face deduplication, shared roof ridges where supported, and parametric wall trimming under roofs. Floor and ceiling outlines use both observed horizontal surfaces and the wall graph, reducing raster teeth and wall tips outside the slab. Genuine measured roof overhangs are retained.

This remains a **research prototype**. Individually valid solids and recorded connections do not guarantee a globally closed, complete building. Curved walls are approximated by planar segments. The main interior level uses predominantly orthogonal walls and horizontal floor/ceiling evidence; multiple storeys, curved room boundaries, mezzanines and fully automatic door/window classification are not solved. Fuzzy scores are expert support indices, not calibrated probabilities. No superiority over published methods has been demonstrated.

## Windows: quick start

1. Extract **the whole ZIP** to a new folder. Start the bundled `CloudCompare.exe`; do not place the DLL into a different CloudCompare build.
2. If Python dependencies are not already installed, install Python **3.12 x64** with its `py` launcher, then double-click **INSTALL_qFuzzyRESF.cmd** beside CloudCompare.exe. It locates its requirements independently of the terminal's working folder and creates/reuses `%LOCALAPPDATA%\qFuzzyRESF\.venv`.
3. Open the cloud and select exactly one point cloud in the DB tree.
4. Open **Plugins → qFuzzyRESF: Generate BIM IFC**. Confirm the title shows **0.3.0**. The previously configured Python interpreter is retained.
5. Select the original coordinate units, **Monotone fuzzy**, and a new `.ifc` filename. Click **Generate IFC**. There is no Interior/Exterior selector.
6. Review the IFC in an IFC viewer and inspect the generated reports, especially inferred extents, thicknesses and virtual space boundaries.

The existing Python environment can be reused. Global Shift/Scale is restored before export. Processing stays on the computer; temporary XYZ64 export needs about 24 bytes per point plus output space. The default voxel budget is 3 million and can be raised if RAM permits. Cancellation stops the child process. Existing files are never overwritten, and failed validation never publishes a partial IFC.

## Outputs

- `name.ifc`: IFC4 parametric swept solids and Boolean trimming; evidence properties on reconstructed elements.
- `name.fuzzyresf/audit.json`: input SHA-256, coordinates, unit conversion and sampling evidence.
- `inference.json`: accepted/rejected proposals, fuzzy support, connection edits and room decisions.
- `validation.json`: EXPRESS validation, element mesh closure, positive volume, space overlaps and topology diagnostics.
- `summary.json`: counts, warnings, dependency versions and runtime.

An IFC reference is **never an inference input**. A visually cleaner model can contain inferred completion and still require geometric/semantic review. Missing room evidence is reported, not replaced by an arbitrary room layout.

## Command line

```bash
python backend/run_pipeline.py --check
python backend/run_pipeline.py --input building.las --output building.ifc --meters-per-unit 1
```

LAS, LAZ and plugin XYZ64 are supported. Other formats can be imported through the appropriate CloudCompare I/O plugin. `--mode unified` is the default. `--mode interior` and `--mode exterior` remain CLI-only research/compatibility options. `--profile geometry_only` supplies an ablation; `legacy` is retained for interior research only.

See [Build and installation](docs/BUILD.md), [Method and limitations](docs/METHOD.md), [Job contract](docs/CONTRACT.md), and [Validation](docs/VALIDATION.md).

## Authors and licence

Massimiliano Pepe and Donato Palumbo — Department of Engineering and Geology, “G. d’Annunzio” University of Chieti-Pescara. Contact: massimiliano.pepe@unich.it.

GPL-3.0-or-later: [LICENSE](LICENSE). Dependencies retain their own licences. User point clouds and reference models are not included.
