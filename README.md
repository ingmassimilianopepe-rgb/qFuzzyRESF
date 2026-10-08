# qFuzzyRESF

`qFuzzyRESF` is a CloudCompare Standard plugin that exposes the Fuzzy-RESF Scan-to-BIM workflow described by Massimiliano Pepe and Donato Palumbo.

The v0.1 architecture keeps the scientific Fuzzy-RESF implementation in a frozen Python backend and uses a C++/Qt CloudCompare plugin as the user interface and data-exchange layer.

## Scope of v0.1

- selected CloudCompare point cloud as input;
- configurable multi-orientation RESF plane sweep;
- multi-peak hypothesis extraction;
- topology, occlusion and fuzzy-reasoning switches;
- stable JSON contract between CloudCompare and the Python backend;
- CSV/JSON diagnostics;
- IFC export hook;
- research presets for the ablation configurations used in the paper.

## Target

- CloudCompare 2.14+
- Qt 6
- CMake 3.20+
- Python 3.10+

## Repository layout

```text
qFuzzyRESF/
├── CMakeLists.txt
├── info.json
├── include/
├── src/
├── resources/
├── backend/
├── tests/
└── docs/
```

## Status

This repository currently contains the v0.1 development scaffold. The C++ plugin shell, frozen configuration and backend contract are implemented first; the full numerical RESF core is then regression-tested against the benchmark outputs used in the paper.

## Authors

- Massimiliano Pepe — Department of Engineering and Geology, “G. d’Annunzio” University of Chieti-Pescara
- Donato Palumbo — Department of Engineering and Geology, “G. d’Annunzio” University of Chieti-Pescara

Corresponding author: massimiliano.pepe@unich.it

## License

GPL-3.0-or-later. See `LICENSE`.
