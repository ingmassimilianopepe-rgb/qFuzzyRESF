# Build and installation

## Matching application and plugin

Use the bundled application. The plugin targets CloudCompare 2.14 / Qt 6 at commit `001bfd04a13101bfee84a28a6eaa3c7a6338b526`. Do not copy its DLL to CloudCompare 2.13 / Qt 5 or an arbitrary build. Windows CI uses Visual Studio 2022 x64 and Qt 6.8.3 `msvc2022_64`; application, plugin, Qt, MSVC and OpenMP runtime files are packaged together.

CI artifact name: `qFuzzyRESF-0.3.0-Windows-CloudCompare-001bfd0`. Artifacts are retained for 30 days. These are test builds, not signed stable installers. Python dependencies are installed separately. Linux native packages require compatible Ubuntu 24.04 Qt/OpenGL/LASzip system libraries.

## Python setup

Install Python 3.12 x64 with the Python launcher. Extract the entire Windows ZIP, then double-click `INSTALL_qFuzzyRESF.cmd` beside CloudCompare.exe. It finds `backend/requirements.txt` from its own location, avoiding working-directory errors. The environment is `%LOCALAPPDATA%\qFuzzyRESF\.venv`; the existing environment and previously selected interpreter can be reused. The installer prints the complete `python.exe` path. Select it in the plugin if necessary.

No PowerShell execution-policy change is needed. An optional `scripts/install_backend.ps1` is provided. Linux: `sh scripts/install_backend.sh` from a source checkout. Package installation needs access to PyPI; reconstruction itself is local.

## Source build

Requirements: CMake >=3.20, C++17 compiler and Qt 6. On Ubuntu 24.04:

```bash
sudo apt install build-essential cmake qt6-base-dev qt6-svg-dev qt6-tools-dev qt6-tools-dev-tools libqt6opengl6-dev libgl1-mesa-dev liblaszip-dev
git clone https://github.com/CloudCompare/CloudCompare.git
cd CloudCompare
git checkout 001bfd04a13101bfee84a28a6eaa3c7a6338b526
git submodule update --init --recursive --depth 1 libs/qCC_db/extern/CCCoreLib libs/qPCP libs/CCAppCommon/QDarkStyleSheet plugins/core/IO/MeshIO
git clone --branch feature/cloudcompare-ifc-tool https://github.com/ingmassimilianopepe-rgb/qFuzzyRESF.git plugins/private/qFuzzyRESF
cmake -S . -B build -DPLUGIN_STANDARD_QFUZZYRESF=ON -DPLUGIN_IO_QLAS=ON -DOPTION_BUILD_CCVIEWER=OFF -DCCCORELIB_USE_TBB=OFF -DCCCORELIB_USE_CGAL=OFF -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=../dist
cmake --build build --config Release --parallel 3
cmake --install build --config Release
```

The plugin directory must be named `qFuzzyRESF` because CloudCompare resolves its QRC by directory name. The Python engine is embedded in Qt resources; editing the extracted `backend` directory does **not** update an already compiled plugin DLL.

For Windows, set `CMAKE_PREFIX_PATH` to Qt 6.8.3 and the LASzip install prefix, then run `windeployqt --release` on the installed executable. The full reproducible packaging procedure is in `.github/workflows/ci.yml`. It builds LASzip from `fa089bd8b2b4ca5631e199d257374c32a125f73f`; the `laszip_common.h` compatibility copy and a linking probe are included. Native tests enable `QFUZZYRESF_BUILD_SMOKE_TESTS`.

For a relocated Linux artifact:

```bash
LD_LIBRARY_PATH="$PWD/lib/cloudcompare${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ./bin/CloudCompare
```

The minimal package includes LAS/LAZ I/O, not every optional CloudCompare reader. E57 and other formats may require building the corresponding I/O plugin or converting the input.

## Troubleshooting

| Symptom | Action |
|---|---|
| Plugin absent | Check matching application/Qt build and plugin loading log |
| Action disabled | Select exactly one point cloud, not a group or mesh |
| Python cannot start | Select the full environment interpreter path |
| `ModuleNotFoundError` | Run the installer and select that same interpreter |
| Requirements file missing | Extract the entire ZIP and run its root installer |
| Rooms not generated | Read the evidence decision; verify units/Z and surveyed floor/ceiling support |
| Multiple interior levels | Crop a level for room reconstruction; surface inference remains available |
| Existing output | Choose a new IFC name |
| Voxel budget exceeded | Crop unnecessary surroundings or raise the budget with sufficient RAM |

A manual desktop GUI run and independent IFC-editor review remain necessary before production use.
