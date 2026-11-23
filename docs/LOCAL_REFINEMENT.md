# Experimental local opposing-face refinement — method 0.7

This prototype refines the supported main-level walls of the geometry-only
(`G`) unified reconstruction. It does not discover an independent complete wall
graph. Its implementation is distinct from the frozen 0.6 affine/MILP benchmark
control. The legacy density experiment therefore does not validate this profile.

## Run

```bash
python backend/run_pipeline.py --check
python backend/run_pipeline.py --input scene.las --output scene_local_F.ifc --profile experimental_local_fuzzy
python backend/run_pipeline.py --input scene.las --output scene_local_C.ifc --profile experimental_local_continuous
```

Choose the corresponding English profile in the CloudCompare dialog after
building the updated source against the matching CloudCompare/Qt version.
Unified mode is mandatory for these profiles. No reference IFC is accepted by
the reconstruction API. Existing output files are never overwritten.

## Evidence and frame

Evidence comprises the engine's 5 cm representative voxel points and 20-neighbour
PCA normals, not all original raw records. Points are already relative to the
stored global coordinate origin. Local fitting uses
`Qxy = (Pxy - origin_xy) @ R.T`; IFC placement restores `R.T`, `origin_xy` and
`coordinate_origin_m` exactly once. The current implementation is axis constrained.

Use actual G polygon transverse bounds, not possibly stale nominal width.
Require five interior cross-sections to agree with that width within 2 mm;
tapered or partitioned walls abstain. Minimum longitudinal extent is 1.5 m.
Affine edits preserve the actual polygon and its holes/components rather than
replacing it with a rectangle.

Within 20 cm of the G boundaries, propose planes from a 1 cm normal-coordinate
histogram smoothed by `[1,2,1]` (peak spacing five bins, prominence 12). Refit
each face with a median inside a 2.5 cm band. At least 24 representative points
per face, 12 fitting points and six validation points are required. Near-horizontal
normals are selected with `abs(Nz)<0.2`, planarity `>0.30`, and axis angle `<=3°`.

Admissible pairs require each face's 5th–95th percentile height >=0.60 m,
overlapping Z >=0.30 m, >=3 shared 0.5 m longitudinal strips covering >=25%
of the G wall length, separation 0.06–0.70 m, normal disagreement with the G
axis <=3°, and fitting 95th percentile residual <=0.03 m. These are local
height requirements: no minimum fraction of the full storey height is imposed.
They are expert fixed parameters, not learned or statistically calibrated.

Each shared strip must itself contain >=6 points per face, intersecting local
5th–95th percentile Z intervals by >=0.30 m and positively intersecting local
longitudinal intervals. Shared length is the sum of these actual longitudinal
intersections, not the number of strip identifiers times 0.5 m. The reported
shared Z is the minimum intersection height among retained strip domains.
Alternating disjoint low/high patches therefore cannot manufacture a pair from
their global bounding boxes.

## Matched controls and internal validation

Longitudinal strips are anchored at the G polygon tangent minimum. Strips whose
integer index modulo three is zero supply internal validation; the other strips
supply fitting. The split shares preprocessing and candidate generation with
fitting: it is a conservative internal guard, not an independent test.

For normal coordinates `x` and proposed faces `a,b`,

`loss(x;a,b) = mean(min(abs(x-a),abs(x-b),0.10 m))`.

The fitting gain `g` is old minus new loss; the validation gain must be
nonnegative (numerical tolerance 1e-9 m). Four memberships are:

1. `max(0,1-max_face_angle/3°)`;
2. `max(0,1-max_fitting_P95_residual/0.03 m)`;
3. `min(1, shared_strip_length/(0.5*G_length))`;
4. `min(1, minimum_shared_strip_Z/0.8 m)`.

Unobserved floor/ceiling anchors are not inserted as zero memberships. The
continuous and fuzzy runs receive exactly the same candidate pool. They differ
only in `A(q)=mean(q)` or `A(q)=min(q)`:

`U = g*A(q) - 0.02*max_face_motion`.

Identity is always available at utility zero. Candidate pool SHA-256 and both
utilities are recorded in `inference.json`. Scores are not probabilities.

## Geometry, selection and metadata

Map actual G transverse faces onto the proposed pair by `n'=k*n+b`. Admit
`0.5<=k<=3` and maximum face movement <=0.20 m. Longitudinal coordinates, holes,
components, heights and source-derived wall identifiers remain fixed. The new
width is extrapolated across the original G height and is labeled accordingly.

Sort positive-utility candidates deterministically by decreasing utility and
candidate identifier; apply at most one per wall. Each sequential edit preserves
original <=3 cm contacts within 3 cm of the current neighbour and rejects new
intersection area >0.001 m² for originally nonincident pairs. This is a greedy
2D guard, not a global MILP, watertight building certificate or 3D collision test.

After selection, `position`, `thickness`, `t0/t1`, IFC `Thickness_m`,
`GeometricProfileWidth_m`, `Qto_WallBaseQuantities.Width` and the swept profile
use the same actual transverse bounds. `PhysicalThicknessStatus` remains
`UNKNOWN_UNVERIFIED`, even after accepting a pair: geometric separation alone
does not establish that both faces belong to the same physical wall.
`ObservedOpposingPairHypothesis` and `LocalRefinementCandidate` separate the
local evidence hypothesis from assumed material thickness. Single-face walls
retain G geometry and explicitly unknown physical thickness.

Original provisional G spaces are re-clipped against the modified wall union
with the existing 2.5 cm clearance. Polygon components greater than 1e-6 m² are
retained; split identifiers receive stable suffixes, areas are updated, and
first-level boundaries are regenerated on IFC export. This does not re-segment
missing rooms or validate room semantics. The exporter still validates IFC4
schema, positive volume, closed individual solids and room non-overlap.

## Verification and scientific limits

`tests/test_local_pairs.py` includes a positive locally observed 0.85 m-high,
20 cm pair refining a 12 cm proxy; single-face and 0.35 m furniture abstention;
frame equivariance; profile-hole preservation; contact/overlap protection;
same-pool controls with differing decisions; real IFC solid/Qto/property width
agreement; and actual CLI-to-IFC jobs for both English profiles.

These are software and synthetic geometry checks, not evidence of reconstruction
accuracy on independent buildings. The method cannot recover invisible thickness
from a single face, certify furniture rejection generally, reconstruct doors and
windows, handle arbitrary curved walls or multiple storeys, or establish a fuzzy
accuracy benefit without matched independent evaluation.
