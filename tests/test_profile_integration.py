"""Exercise the actual experimental selectors through XYZ64 -> IFC publication."""
import json
import numpy as np
import pytest
from fuzzyresf.cli import run


@pytest.mark.parametrize('profile', ['evidence', 'relational'])
def test_supported_rooms_publish_with_auditable_experimental_selection(tmp_path, two_rooms, profile):
    source = tmp_path/'scene.xyz64'
    np.asarray(two_rooms, dtype='<f8').tofile(source)
    output = tmp_path/'scene.ifc'
    assert run({'schema_version':1, 'input':{'path':str(source)},
                'output':str(output), 'profile':profile}) == 0
    folder = output.with_suffix('.fuzzyresf')
    inference = json.loads((folder/'inference.json').read_text())
    summary = json.loads((folder/'summary.json').read_text())
    validation = json.loads((folder/'validation.json').read_text())
    assert summary['counts']['IfcWall'] == 5
    assert summary['counts']['IfcSpace'] == 2
    assert summary['profile_calibration'] == 'EXPERT_UNCALIBRATED'
    assert summary['global_envelope_certified'] is False
    assert inference['reference_used'] is False
    assert inference['hypotheses']['profile'] == profile
    assert all('evidence' in p for p in inference['proposals'])
    assert all(m['watertight'] and m['volume_m3'] > 0 for m in validation['meshes'])
    # Exterior candidates not retained in the final interior model must not
    # masquerade as exported roof/connection metrics.
    assert inference['topology']['retained_additional_surface_ids'] == []
    assert 'additional_surface_topology_pre_fusion' in inference
