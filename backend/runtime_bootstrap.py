"""Resolve the packaged Python before loading any third-party dependency.

The CloudCompare DLL may start the PATH interpreter. For a portable package,
relaunch the same CLI with its own .venv, even when CloudCompare.exe was opened
directly. No package is installed automatically during reconstruction.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys


def _failure(message, package_root, backend):
    print('qFuzzyRESF: ' + message, file=sys.stderr)
    print('Interprete in uso: ' + sys.executable, file=sys.stderr)
    if package_root is not None:
        print('Chiudere CloudCompare ed eseguire: ' + str(package_root / 'SETUP_QFUZZYRESF.cmd'), file=sys.stderr)
        print('Poi avviare: ' + str(package_root / 'START_QFUZZYRESF.cmd'), file=sys.stderr)
    else:
        print('Installare i requisiti con questo interprete: "' + sys.executable + '" -m pip install -r "' + str(backend / 'requirements.txt') + '"', file=sys.stderr)
    raise SystemExit(3)


def ensure_runtime(script_path=None, argv=None):
    """Validate runtime or relaunch and exit with the child status.

    ``argv`` contains arguments after the script name. The launcher and request
    paths are passed as separate arguments, preserving spaces and Unicode.
    Source checkouts keep their explicitly selected interpreter.
    """
    script = Path(script_path or sys.argv[0]).resolve()
    backend = script.parent
    package_root = None
    # Standard layout: ROOT/CloudCompare/qFuzzyRESF/backend/cli.py.
    if len(backend.parents) >= 3:
        possible = backend.parents[2]
        if (possible / 'CloudCompare' / 'CloudCompare.exe').is_file() and backend == possible / 'CloudCompare' / 'qFuzzyRESF' / 'backend':
            package_root = possible
    if package_root is not None:
        candidates = (package_root / '.venv' / 'Scripts' / 'python.exe',
                      package_root / '.venv' / 'bin' / 'python')
        interpreter = next((p for p in candidates if p.is_file()), None)
        if interpreter is None:
            _failure('Ambiente Python del pacchetto assente o incompleto.', package_root, backend)
        # Do not resolve symlinks here: a venv can link to the base executable.
        actual = os.path.normcase(os.path.abspath(sys.executable))
        expected = os.path.normcase(os.path.abspath(interpreter))
        if actual != expected:
            try:
                result = subprocess.run([str(interpreter), str(script), *(list(argv) if argv is not None else sys.argv[1:])], check=False)
            except OSError as exc:
                _failure('Impossibile avviare il Python del pacchetto: ' + str(exc), package_root, backend)
            raise SystemExit(result.returncode)
    missing = []
    for name in ('numpy', 'scipy', 'shapely', 'ifcopenshell'):
        try:
            available = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            missing.append(name)
    if missing:
        _failure('Dipendenze Python mancanti: ' + ', '.join(missing) + '.', package_root, backend)
