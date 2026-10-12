"""Fuzzy-RESF-BIM v7.2: restoration of the evidence-graph-preserving BIM path.

The v7.0/v7.1 object layers introduced a second reconstruction pass after the
scan-supported Fuzzy-RESF graph. On the historic-building scan used during
interactive development this could change a previously good model: additional
walls could be promoted, the room graph could be re-polygonized from those new
walls, and roof facets could be replaced by a different support model.

v7.2 deliberately removes that source of drift. Geometry is produced by the
v6.5 evidence-graph-preserving backend -- the revision selected after the earlier
real-scan comparison -- and is passed through unchanged. v7.2 only records the
restoration policy in diagnostics; it does not add, delete, merge, extend, snap,
or re-fit any BIM object after v6.5 has finished.

This is intentional: the point-cloud/RESF evidence graph remains authoritative,
and later experimental object hypotheses are no longer allowed to overwrite the
wall, room, roof, opening or stair geometry by default.
"""
from __future__ import annotations

import building_shell_v6_5 as v65


RESTORED_BASELINE_COMMIT = "ddadadf772a52cf9bbcdab88286e550a5118603f"
RESTORED_BASELINE_BLOB = "e858e27ebe05c0719a2618705767ddfc9c06f089"


def reconstruct_bim(request: dict):
    """Return the proven evidence-graph BIM geometry without a second object pass."""
    model = v65.reconstruct_bim(request)

    # Diagnostics only.  Do not touch any geometry or confidence/state field here.
    model.restoration_policy = {
        "mode": "v6.5_evidence_graph_geometry_preserved",
        "baseline_commit": RESTORED_BASELINE_COMMIT,
        "baseline_blob": RESTORED_BASELINE_BLOB,
        "post_v65_wall_promotion": False,
        "post_v65_wall_merging": False,
        "post_v65_space_repolygonization": False,
        "post_v65_roof_refit": False,
        "post_v65_opening_refilter": False,
        "post_v65_stair_redetection": False,
    }

    summary = model.summary
    summary.update({
        "version": "7.2-restored-evidence-graph",
        "restoration_baseline": "Fuzzy-RESF-BIM 6.5 evidence-graph-preserving",
        "restoration_baseline_commit": RESTORED_BASELINE_COMMIT,
        "wall_reconstruction": "v6.5_evidence_graph_preserved_no_second_wall_hypothesis_pass",
        "space_reconstruction": "v6.5_same_exported_wall_graph_room_polygonization_preserved",
        "roof_reconstruction": "v6.5_connected_upper_envelope_support_preserved",
        "opening_reconstruction": "v6.5_scan_supported_opening_filter_preserved",
        "stair_reconstruction": "v6.5_multiband_stair_detection_preserved",
        "postprocessing_policy": "diagnostics_only_after_v6.5_no_geometry_mutation",
        "experimental_v7_object_overlay": False,
        "reference_bim_used_at_runtime": False,
    })
    return model
