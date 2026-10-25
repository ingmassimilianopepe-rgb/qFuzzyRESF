"""Run the compiled plugin/GUI bridge smoke test with installed CC libraries."""

import os
from pathlib import Path
import subprocess
import sys

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
raise SystemExit(result.returncode)
