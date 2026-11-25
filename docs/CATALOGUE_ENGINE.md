# Experimental catalogue hypothesis engine

`infer_catalogue(I, P, N, PL, catalogue, aggregation='minimum')` returns a deep
copy of a G inference. It needs no reference BIM or GT API. It refines only
existing axis-aligned G wall profiles. It does not find missing walls, classify
doors/windows, interpret OWL, prove construction identity, or infer hidden layers.

## Catalogue input

Schema version 1 requires a nonempty `catalogue_id`, `units: "m"`, and a
`families` list. Wall families contain `id`, `ifc_class: "IfcWall"`,
`dimension_role: "finished_wall_thickness"`, finite `nominal_m` in [.06, .70],
a documented nonempty `source` object, `applicability: "GENERAL_REFERENCE"`,
`layers_m`, and `physical_measurement: false`. Layers, when listed, must sum to
the finished dimension. They are catalogue reference data, never inferred model
layers. Wrong units, roles, malformed sources and nonfinite values are rejected.
`UNKNOWN`, with `kind: "free_geometry"`, null nominal and empty layers, is always
available and added without modifying the caller's catalogue.

## Candidate generation and gates

The historical 1 cm histogram detects source anchors. Every anchor creates a
free observed-pair proposal and, for every nominal family, probes source voxel
bands on **both sides at anchor ± nominal**. A counterface need not have a
histogram maximum. Every nominal attempt remains in the audit even when blind,
out of range, or otherwise nonexportable. Identity and UNKNOWN/free geometry
remain available. Family hypotheses use the nominal separation; free-pair
hypotheses use measured face separation. Both are geometry hypotheses.

Export requires two observed faces, each at least .60 m high; normal disagreement
at most 3 degrees; source residual P95 at most .03 m; separation .06–.70 m;
transverse scale .5–3; and maximum face motion .20 m. Actual per-strip vertical
intersections must reach .30 m in at least three .50 m strips, with the sum of
actual longitudinal intersections covering at least 25% of the G wall length.
Shared strip identifiers or global height overlap alone are insufficient.
Interleaved held-out source strips must show nonnegative capped-loss improvement.
These strips share preprocessing and are not independent validation data.

Only a transverse affine transform of the original polygon is allowed: holes,
clipping and longitudinal domains are preserved. The constant-width guard is
reused from 0.7. Static and sequential 2D guards preserve incident G contacts to
.03 m and reject more than .001 m² of new nonincident overlap. These guards do
not establish 3D topology or building closure.

## Selection and interpretation

Five geometry memberships measure normal agreement, residual, shared length,
shared Z and preservation of incident G contacts. The final junction term is an
explicit G proxy, not a source junction classifier. With source nearest-face
loss capped at .10 m, all three controls use

`utility = (old source loss - candidate source loss) * support - .02 * max motion`.

Support is the arithmetic mean, minimum, or crisp indicator that **all five**
memberships are at least .5. Candidate generation never depends on the selected
control. Every candidate stores all three utilities and a canonical pool SHA-256
makes equality reviewable. Deterministic greedy selection accepts positive
utility candidates while applying the sequential guards.

The expert catalogue Gaussian `exp(-.5*((observed separation - nominal)/.03)^2)`
is diagnostic only. It is neither a probability nor trained evidence and never
enters utility, preventing source dimension error from being counted twice.
A family id labels a dimension hypothesis and is never recognition proof.
Physical thickness remains `UNKNOWN_UNVERIFIED`, even with two observed faces.
Full-height extrusion from partial-height observations remains an extrapolation.

The complete audit is `I['catalogue_inference']`; `I['local_refinement']` marks
compatibility for geometric width export. Actual polygon width, position and
`t0`/`t1` metadata are recomputed after selection. Original provisional G spaces
are re-clipped to changed material as in 0.7, without room discovery. Evidence
uses 5 cm voxel representatives and PCA normals, not original raw records.
