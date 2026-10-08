# qFuzzyRESF backend contract v0.1

The C++ CloudCompare layer communicates with the numerical Python backend through files in a per-run working directory.

## Request

The plugin exports the selected cloud as `input.xyz` and writes `request.json`.

Important request fields include:

- `preset`
- `input`
- `output`
- `plane_spacing_m`
- `plane_tolerance_m`
- `raster_cell_m`
- `angle_step_deg`
- `confidence_threshold`
- `max_orientation_families`
- `multi_peak`
- `topology`
- `occlusion`
- `fuzzy`
- `instance_consolidation`
- `geometric_feedback`
- `export_ifc`

## Required outputs

### `result.json`

Must contain at least:

```json
{
  "status": "success",
  "method": "Fuzzy-RESF",
  "version": "1.0",
  "hypotheses": 12,
  "permanent": 8,
  "occluded_permanent": 1,
  "clutter": 2,
  "uncertain": 1
}
```

### `walls.csv`

The importer expects the following columns:

```text
id,theta_deg,d,x1,y1,x2,y2,z0,z1,thickness,area_support,horizontal_extent,vertical_extent,continuity,boundary_support,occlusion_support,confidence,state
```

Valid `state` values are:

- `Permanent`
- `OccludedPermanent`
- `Clutter`
- `Uncertain`

### `fuzzy_resf.ifc`

When IFC export is enabled, the backend writes an IFC model and adds `Pset_FuzzyRESF` provenance to generated walls.

## Design rule

The file contract is deliberately independent of the internal implementation of RESF. This allows the transparent reference backend to be replaced by the frozen research code or by a future optimized C++ implementation without changing the CloudCompare user interface.
