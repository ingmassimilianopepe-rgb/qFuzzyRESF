"""Actual CLI publication, frozen catalogue provenance and partial-output semantics."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import ifcopenshell
import ifcopenshell.util.element
import numpy as np
import pytest

from fuzzyresf.cli import run

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT.parent / 'semantic_n25' / 'runtime_deps'


def job(tmp_path, points, profile='experimental_catalogue_crisp'):
    source = tmp_path / 'scan.xyz64'
    np.asarray(points, dtype='<f8').tofile(source)
    return {'schema_version': 1, 'input': {'path': str(source)},
            'output': str(tmp_path / (profile + '.ifc')), 'profile': profile}


@pytest.mark.parametrize('profile', ['experimental_catalogue_crisp',
                                   'experimental_catalogue_continuous',
                                   'experimental_catalogue_fuzzy'])
def test_actual_cli_to_ifc_three_catalogue_arms(tmp_path, two_rooms, profile):
    request = job(tmp_path, two_rooms, profile)
    output = Path(request['output'])
    result = subprocess.run([sys.executable, str(ROOT / 'backend/run_pipeline.py'),
                             '--input', request['input']['path'], '--output', str(output),
                             '--profile', profile], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((output.with_suffix('.fuzzyresf') / 'summary.json').read_text())
    assert summary['method_version'].startswith('0.8')
    assert summary['profile_calibration'] == 'EXPERT_UNCALIBRATED'
    assert summary['reference_used'] is False
    model = ifcopenshell.open(str(output))
    for wall in model.by_type('IfcWall'):
        props = ifcopenshell.util.element.get_psets(wall)['ScanToBIM_Evidence']
        assert props['PhysicalThicknessStatus'] == 'UNKNOWN_UNVERIFIED'
        assert props['PhysicalMeasurement'] is False
        assert props['MaterialCompositionStatus'] == 'UNKNOWN'
        assert props['FamilyHypothesis']
        assert props['CatalogueSourceSHA256'] == summary['catalogue_source']['sha256']
        assert props['GeometricProfileWidth_m'] == pytest.approx(props['Thickness_m'])
    assert not model.by_type('IfcDoor') and not model.by_type('IfcWindow')


def test_custom_catalogue_read_once_before_cloud_and_frozen(tmp_path, two_rooms, monkeypatch):
    import fuzzyresf.cloud as cloud
    request = job(tmp_path, two_rooms)
    catalogue = tmp_path / 'custom.json'
    raw = b'{"schema_version":1,"catalogue_id":"custom","units":"m","families":[]}\n'
    catalogue.write_bytes(raw)
    request['catalogue'] = str(catalogue)
    analyse = cloud.analyse
    def changed_after_validation(*args, **kwargs):
        catalogue.write_text('malformed after read')
        return analyse(*args, **kwargs)
    monkeypatch.setattr(cloud, 'analyse', changed_after_validation)
    assert run(request) == 0
    folder = Path(request['output']).with_suffix('.fuzzyresf')
    assert (folder / 'catalogue_source.json').read_bytes() == raw
    summary = json.loads((folder / 'summary.json').read_text())
    assert summary['catalogue_source']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert summary['catalogue_source']['path'] == str(catalogue.resolve())
    snapshot = json.loads((folder / 'catalogue_snapshot.json').read_text())
    assert snapshot['catalogue_id'] == 'custom'


@pytest.mark.parametrize('contents', [None, '{bad', '{"schema_version":99}'])
def test_missing_or_malformed_catalogue_fails_before_cloud(tmp_path, monkeypatch, contents):
    import fuzzyresf.cli as cli
    request = job(tmp_path, [[0., 0., 0.]])
    catalogue = tmp_path / 'bad.json'
    if contents is not None:
        catalogue.write_text(contents)
    request['catalogue'] = str(catalogue)
    monkeypatch.setattr(cli, 'dependencies', lambda: pytest.fail('dependencies reached before catalogue validation'))
    with pytest.raises((ValueError, FileNotFoundError)):
        run(request)
    assert not Path(request['output']).exists()


def test_catalogue_not_silently_ignored_by_other_profiles(tmp_path):
    request = job(tmp_path, [[0., 0., 0.]], 'geometry_only')
    request['catalogue'] = 'ignored.json'
    with pytest.raises(ValueError, match='catalogue is supported only'):
        run(request)
    request['profile'] = 'experimental_catalogue_crisp'
    request['mode'] = 'exterior'
    with pytest.raises(ValueError, match='requires unified'):
        run(request)


def test_partial_surface_ifc_keeps_unknown_and_catalogue_provenance(tmp_path):
    from test_exterior import house
    request = job(tmp_path, house())
    assert run(request) == 0
    output = Path(request['output'])
    summary = json.loads((output.with_suffix('.fuzzyresf') / 'summary.json').read_text())
    assert summary['rooms_supported'] is False
    model = ifcopenshell.open(str(output))
    project_props = ifcopenshell.util.element.get_psets(model.by_type('IfcProject')[0])['ScanToBIM_Evidence']
    assert project_props['MethodVersion'].startswith('0.8')
    assert project_props['CatalogueSourceSHA256'] == summary['catalogue_source']['sha256']
    for element in model.by_type('IfcElement'):
        props = ifcopenshell.util.element.get_psets(element)['ScanToBIM_Evidence']
        assert props['FamilyHypothesis'] == 'UNKNOWN'
        assert props['PhysicalThicknessStatus'] == 'UNKNOWN_UNVERIFIED'
        assert props['PhysicalMeasurement'] is False
        assert props['CatalogueSourcePath'] == summary['catalogue_source']['path']
