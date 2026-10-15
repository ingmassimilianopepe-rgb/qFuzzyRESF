"""Physical and semantic export checks, using an actual IFC parser and kernel."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
ifcopenshell = pytest.importorskip("ifcopenshell")
import ifcopenshell.geom
import ifcopenshell.util.placement
import ifcopenshell.util.element

from bim_reconstruction import BIMModel, Opening, Slab, Space, Storey, Wall
from resf_ifc import write_semantic_ifc


def make_model():
    # Rotated wall at a nonzero storey elevation is intentional: these expose
    # host-relative opening placement and doubled-storey-elevation mistakes.
    wall = Wall(1, 1, 10, 20, 13, 24, 8, 11, 0.2, 0, 0.9, 0.9, 0.95, "paired_faces")
    door = Opening(1, 1, 1, "Door", 1.5, 1.0, 0.0, 2.1, 0.8)
    window = Opening(2, 1, 1, "Window", 3.5, 1.0, 1.0, 1.2, 0.8)
    model = BIMModel([Storey(1, 8, 11, 0.9)], [wall], [door, window], [], [], [], {})
    model.coordinate_frame = {"origin": [500000, 4200000, 100], "scale_to_m": 1.0}
    model.provenance = {"Wall:1": {"thickness_status": "measured_paired_faces", "candidate_ids": [1, 2]}}
    return model


def test_real_ifc_schema_host_void_volumes_and_placement(tmp_path):
    model = make_model()
    path = tmp_path / "semantic.ifc"
    report = write_semantic_ifc(model, path)
    assert report["schema_valid"] and report["geometry_valid"]
    f = ifcopenshell.open(str(path))
    assert f.schema == "IFC4"
    assert len(f.by_type("IfcRelVoidsElement")) == 2
    assert len(f.by_type("IfcRelFillsElement")) == 2
    host = f.by_type("IfcWall")[0]
    assert all(rel.RelatingBuildingElement == host for rel in f.by_type("IfcRelVoidsElement"))
    wall_check = next(c for c in report["geometry_checks"] if c["ifc_class"] == "IfcWall")
    assert wall_check["actual_volume_m3"] == pytest.approx(5 * 0.2 * 3 - (1 * 2.1 + 1 * 1.2) * 0.2)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, host)
    xyz = np.asarray(shape.geometry.verts).reshape(-1, 3)
    assert xyz[:, 2].min() == pytest.approx(8)
    assert xyz[:, 2].max() == pytest.approx(11)
    matrix = ifcopenshell.util.placement.get_local_placement(host.ObjectPlacement)
    assert matrix[:3, 3] == pytest.approx([10, 20, 8])
    assert matrix[:3, 0] == pytest.approx([0.6, 0.8, 0])
    door = f.by_type("IfcDoor")[0]
    door_matrix = ifcopenshell.util.placement.get_local_placement(door.ObjectPlacement)
    assert door_matrix[:3, 3] == pytest.approx([10.6, 20.8, 8])
    assert door.OverallHeight == pytest.approx(2.1)
    assert door.Representation is None  # assembly thickness was not measured
    psets = ifcopenshell.util.element.get_psets(host)
    assert psets["Pset_FuzzyRESF_Reconstruction"]["ThicknessEvidence"] == "measured_paired_faces"
    assert f.by_type("IfcSIUnit")[0].Name == "METRE"


def test_holes_ceiling_semantics_space_aggregation_and_origin(tmp_path):
    model = make_model()
    outer = [[10, 20], [14, 20], [14, 24], [10, 24]]
    hole = [[11, 21], [12, 21], [12, 22], [11, 22]]
    floor = Slab(1, 1, "Floor", outer, 8.0, 0.25, 0.8)
    floor.provenance = {"thickness_source": "assumed_config"}
    ceiling = Slab(2, 1, "Ceiling", outer, 11.0, 0.1, 0.8)
    space = Space(1, 1, "Room with shaft", outer, 8.0, 11.0, 15.0, 0.8)
    for item in (floor, ceiling, space):
        item.holes = [hole]
    model.slabs = [floor, ceiling]
    model.spaces = [space]
    model.coordinate_frame["restore_global_coordinates"] = True
    path = tmp_path / "holes.ifc"
    report = write_semantic_ifc(model, path)
    f = ifcopenshell.open(str(path))
    assert len(f.by_type("IfcSlab")) == 1
    floor_psets = ifcopenshell.util.element.get_psets(f.by_type("IfcSlab")[0])
    assert floor_psets["Pset_FuzzyRESF_Reconstruction"]["ThicknessEvidence"] == "assumed_config"
    assert f.by_type("IfcCovering")[0].PredefinedType == "CEILING"
    assert len(f.by_type("IfcArbitraryProfileDefWithVoids")) == 3
    space_obj = f.by_type("IfcSpace")[0]
    assert any(space_obj in rel.RelatedObjects for rel in f.by_type("IfcRelAggregates"))
    assert not any(space_obj in rel.RelatedElements for rel in f.by_type("IfcRelContainedInSpatialStructure"))
    assert next(c for c in report["geometry_checks"] if c["ifc_class"] == "IfcSlab")["actual_volume_m3"] == pytest.approx(15 * 0.25)
    floor_matrix = ifcopenshell.util.placement.get_local_placement(f.by_type("IfcSlab")[0].ObjectPlacement)
    ceiling_matrix = ifcopenshell.util.placement.get_local_placement(f.by_type("IfcCovering")[0].ObjectPlacement)
    assert floor_matrix[:3, 3] == pytest.approx([500010, 4200020, 107.75])
    assert ceiling_matrix[:3, 3] == pytest.approx([500010, 4200020, 111])


def test_generic_opening_has_no_semantic_fill_and_review_is_not_exported(tmp_path):
    model = make_model()
    model.openings[0].kind = "OpenVoid"
    model.openings[1].kind = "Unknown"
    review = Wall(2, 1, 0, 0, 2, 0, 8, 11, 0.2, 0, 0.4, 0.4, 0.4, "uncertain", state="Review")
    model.walls.append(review)
    report = write_semantic_ifc(model, tmp_path / "review.ifc")
    assert report["counts"]["IfcOpeningElement"] == 1
    assert report["counts"]["IfcWall"] == 1
    assert report["counts"]["IfcDoor"] == report["counts"]["IfcWindow"] == 0
    assert len(report["skipped"]) == 2


def test_invalid_opening_does_not_overwrite_previous_result(tmp_path):
    model = make_model()
    path = tmp_path / "protected.ifc"
    path.write_text("previous valid result", encoding="utf8")
    model.openings[0].offset = -10
    with pytest.raises(ValueError, match="outside host"):
        write_semantic_ifc(model, path)
    assert path.read_text() == "previous valid result"
