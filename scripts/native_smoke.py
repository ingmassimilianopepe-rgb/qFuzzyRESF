"""Run the compiled plugin/GUI bridge smoke test with installed CC libraries."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile

import laspy
import numpy as np

build, dist = map(Path, sys.argv[1:3])
windows = sys.platform == "win32"
exe = next(build.rglob("qFuzzyRESF_smoke.exe" if windows else "qFuzzyRESF_smoke"))
plugin = next(dist.rglob("qFuzzyRESF.dll" if windows else "libqFuzzyRESF.so"))
env = dict(os.environ)
if windows:
    app = next(dist.rglob("CloudCompare.exe"))
    env["PATH"] = str(app.parent.resolve()) + os.pathsep + env["PATH"]
result = subprocess.run(
    [str(exe.resolve()), str(plugin.resolve()), sys.executable], env=env, timeout=210
)
if result.returncode:
    raise SystemExit(result.returncode)

# Also verify that the packaged application can import the users' LAS/LAZ
# inputs through its real qLAS I/O plugin, then save a non-empty CC BIN.
app = next(dist.rglob("CloudCompare.exe" if windows else "CloudCompare"))
with tempfile.TemporaryDirectory(prefix="qfuzzyresf-io-") as folder:
    folder = Path(folder)
    cloud = laspy.create(point_format=3, file_version="1.2")
    cloud.x = np.linspace(0, 5, 100)
    cloud.y = np.sin(cloud.x)
    cloud.z = np.cos(cloud.x)
    for extension in ("las", "laz"):
        source = folder / f"scan.{extension}"
        target = folder / f"imported_{extension}.bin"
        cloud.write(source)
        subprocess.run(
            [
                str(app.resolve()),
                "-SILENT",
                "-AUTO_SAVE",
                "OFF",
                "-O",
                str(source),
                "-C_EXPORT_FMT",
                "BIN",
                "-SAVE_CLOUDS",
                "FILE",
                str(target),
            ],
            env=env,
            check=True,
            timeout=90,
        )
        if not target.exists() or target.stat().st_size == 0:
            raise RuntimeError(f"Packaged CloudCompare failed to import {extension}")
print("Packaged CloudCompare LAS and LAZ import passed.")
