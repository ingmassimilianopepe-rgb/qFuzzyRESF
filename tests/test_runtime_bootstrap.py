"""Runtime selection regressions for the portable Windows package.

The fake executables are never launched. These checks reproduce a plugin run
started by a global Python while setup installed dependencies into .venv.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import runtime_bootstrap as runtime


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "qFuzzy RESF"
    app = root / "CloudCompare"
    script = app / "qFuzzyRESF" / "backend" / "fuzzy_resf_cli.py"
    script.parent.mkdir(parents=True)
    script.touch()
    (app / "CloudCompare.exe").touch()
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    return root, script, python


@pytest.fixture
def available_modules(monkeypatch):
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda name: object())


def test_bundled_environment_wins_over_global_python_and_keeps_argument_boundaries(
    bundle, monkeypatch
):
    root, script, python = bundle
    monkeypatch.setattr(runtime.sys, "executable", str(root / "global" / "python.exe"))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime.subprocess, "run", run)
    tail = ["--request", str(root / "run with spaces" / "request.json")]
    with pytest.raises(SystemExit) as caught:
        runtime.ensure_runtime(script, tail)
    assert caught.value.code == 0
    assert len(calls) == 1
    assert calls[0][0] == [str(python), str(script), *tail]
    assert calls[0][1].get("check") is False
    assert not calls[0][1].get("shell", False)


@pytest.mark.parametrize("returncode", [2, 3, 17])
def test_child_failure_is_returned_to_cloudcompare(bundle, monkeypatch, returncode):
    root, script, _ = bundle
    monkeypatch.setattr(runtime.sys, "executable", str(root / "global-python.exe"))
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=returncode))
    with pytest.raises(SystemExit) as caught:
        runtime.ensure_runtime(script, ["--request", "request.json"])
    assert caught.value.code == returncode


def test_correct_bundled_python_does_not_launch_itself(bundle, monkeypatch, available_modules):
    _, script, python = bundle
    monkeypatch.setattr(runtime.sys, "executable", str(python))

    def forbidden_run(*args, **kwargs):
        pytest.fail("Already using .venv; launching again would recurse")

    monkeypatch.setattr(runtime.subprocess, "run", forbidden_run)
    assert runtime.ensure_runtime(script, []) is None


def test_missing_scipy_names_setup_and_runtime_in_error(bundle, monkeypatch, capsys):
    root, script, python = bundle
    monkeypatch.setattr(runtime.sys, "executable", str(python))
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda name: None if name == "scipy" else object())
    with pytest.raises(SystemExit) as caught:
        runtime.ensure_runtime(script, [])
    assert caught.value.code == 3
    message = capsys.readouterr().err
    assert "scipy" in message
    assert "SETUP_QFUZZYRESF.cmd" in message
    assert str(python) in message


def test_package_without_setup_does_not_silently_use_global_python(
    bundle, monkeypatch, available_modules, capsys
):
    _, script, python = bundle
    python.unlink()
    monkeypatch.setattr(runtime.sys, "executable", "/global/python")
    with pytest.raises(SystemExit) as caught:
        runtime.ensure_runtime(script, [])
    assert caught.value.code == 3
    assert "SETUP_QFUZZYRESF.cmd" in capsys.readouterr().err


def test_source_checkout_keeps_its_selected_interpreter(tmp_path, monkeypatch, available_modules):
    script = tmp_path / "repo" / "backend" / "semantic_resf_cli.py"
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(runtime.sys, "executable", "/selected/python")

    def forbidden_run(*args, **kwargs):
        pytest.fail("A source checkout must keep its explicitly selected runtime")

    monkeypatch.setattr(runtime.subprocess, "run", forbidden_run)
    assert runtime.ensure_runtime(script, []) is None


def test_source_checkout_missing_dependency_has_install_command(tmp_path, monkeypatch, capsys):
    script = tmp_path / "repo" / "backend" / "semantic_resf_cli.py"
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda name: None if name == "scipy" else object())
    with pytest.raises(SystemExit) as caught:
        runtime.ensure_runtime(script, [])
    assert caught.value.code == 3
    message = capsys.readouterr().err
    assert "scipy" in message
    assert "pip" in message
    assert "requirements.txt" in message


def test_package_location_requires_cloudcompare_marker(tmp_path, monkeypatch, available_modules):
    script = tmp_path / "code" / "CloudCompare" / "qFuzzyRESF" / "backend" / "fuzzy_resf_cli.py"
    script.parent.mkdir(parents=True)
    script.touch()
    # Directory names alone must not turn a normal checkout into a bundle.
    assert runtime.ensure_runtime(script, []) is None


def test_real_process_handoff_from_python_without_site_packages(tmp_path):
    """A bare Python -S must reach the venv before importing the backend deps."""
    root = tmp_path / "package with spaces"
    app = root / "CloudCompare"
    backend = app / "qFuzzyRESF" / "backend"
    backend.mkdir(parents=True)
    (app / "CloudCompare.exe").touch()
    shutil.copyfile(runtime.__file__, backend / "runtime_bootstrap.py")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")], check=True)
    expected = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    # The dummy modules keep this a launcher test, independent of installed
    # scientific libraries. scipy fails if imported by the wrong interpreter.
    for name in ("numpy", "shapely", "ifcopenshell"):
        (backend / (name + ".py")).write_text("", encoding="utf-8")
    (backend / "scipy.py").write_text(
        "import sys\n"
        f"assert sys.executable == {str(expected)!r}, 'Imported scipy before runtime handoff'\n",
        encoding="utf-8",
    )
    script = backend / "fuzzy_resf_cli.py"
    script.write_text(
        "from runtime_bootstrap import ensure_runtime\n"
        "ensure_runtime()\n"
        "import scipy, json, sys\n"
        "print(json.dumps({'executable': sys.executable, 'args': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    tail = ["--request", str(root / "input with spaces" / "request.json")]
    result = subprocess.run([sys.executable, "-S", str(script), *tail], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"executable": str(expected), "args": tail}
