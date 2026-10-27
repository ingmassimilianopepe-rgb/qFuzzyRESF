# Job contract

A job is a UTF-8 JSON object, schema version 1:

```json
{
  "schema_version": 1,
  "input": {"path": "C:/data/building.las", "format": "las"},
  "output": "C:/data/building_v03.ifc",
  "meters_per_unit": 1.0,
  "mode": "unified",
  "profile": "monotone",
  "limits": {"max_voxels": 3000000, "max_raster_cells": 4000000}
}
```

Input formats: `las`, `laz`, `xyz64`. XYZ64 is headerless little-endian float64 XYZ triples; optional `input.count` is checked. CloudCompare exports original global coordinates, reversing Global Shift/Scale. Optional `input.name` is diagnostic provenance. `work_dir` must be on the same filesystem as the output for hard-link publication.

Scale must be finite and in [1e-6, 1000]. Voxel limits: 500–20,000,000; raster cells: 1,000–20,000,000. Parent output directory must exist. Neither the IFC nor its `.fuzzyresf` diagnostic directory may already exist.

Default mode is now `unified`. Historical explicit `interior` / `exterior` values remain available through CLI/JSON, not the GUI. Profiles are `monotone` and `geometry_only`; `legacy` is retained only for explicit interior jobs.

Stdout is newline-delimited JSON with events `ready`, `progress`, `warning`, `result`, `error`, `cancelled`. Stages and application-authored messages are English. Successful results identify output/diagnostic paths, software/method versions, hashes, counts, dependency versions and warnings. `inference.json` also records the unified room-evidence decision and topology edits.

Return codes: 0 success; 1 error; 130 cancellation. The native frontend uses QProcess without a shell, UTF-8 streams and two BLAS/OpenMP threads. Cancel terminates Python, then kills it if needed. Temporary files are owned by the job. Validation occurs before exclusive publication; existing outputs are never replaced. A complete validated IFC may exist if cancellation arrives after the final commit point.
