import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import building_shell_v7_2 as v72


class _DummyModel:
    def __init__(self):
        self.walls = [object()]
        self.spaces = [object()]
        self.roofs = [object()]
        self.openings = [object()]
        self.stairs = [object()]
        self.summary = {"status": "success"}


def test_v72_preserves_v65_geometry_objects(monkeypatch):
    model = _DummyModel()
    walls = model.walls
    spaces = model.spaces
    roofs = model.roofs
    openings = model.openings
    stairs = model.stairs

    monkeypatch.setattr(v72.v65, "reconstruct_bim", lambda request: model)
    out = v72.reconstruct_bim({"input": "unused"})

    assert out is model
    assert out.walls is walls
    assert out.spaces is spaces
    assert out.roofs is roofs
    assert out.openings is openings
    assert out.stairs is stairs
    assert out.summary["version"] == "7.2-restored-evidence-graph"
    assert out.summary["experimental_v7_object_overlay"] is False
    assert out.restoration_policy["post_v65_wall_promotion"] is False
    assert out.restoration_policy["post_v65_space_repolygonization"] is False
    assert out.restoration_policy["post_v65_roof_refit"] is False
