# Method 0.5: unified reconstruction with bounded topology edits

This is an expert-parameter pilot. The implementation uses only the selected cloud, explicit input scale and configured resource budgets. Reference IFCs are comparison material, never inference inputs. No learning, calibrated probabilities or benchmark superiority is claimed.

## Shared preprocessing

Stream LAS/LAZ or XYZ64, reject non-finite values, hash the input, restore metric coordinates and retain the global origin. Use one representative per 5 cm voxel; estimate PCA normals and planarity from 20 neighbours. An explicit voxel budget bounds memory. Detect dominant horizontal elevations and fourfold vertical-normal concentration.

## One workflow

The GUI always requests `unified`. First evaluate candidate floor/ceiling pairs: height 1.8–8 m, at least 5 m² of horizontal occupancy on each plane, overlapping XY support, and no substantial intermediate plane. Ambiguous competing storeys do not create arbitrary room levels. If a pair and wall topology are usable, generate the main-level interior. Otherwise record a room-evidence decision and continue with supported wall/roof surfaces. Expected inference-domain failures are reported; file, dependency and IFC validation failures still stop publication.

Additional surface candidates are merged into the same IFC building, suppressing the scanned ceiling as a duplicate roof and suppressing wall surfaces already explained by the interior footprint. Additional roofs must be above the selected ceiling by more than 0.35 m. An exterior wall must lie mostly outside the interior footprint and extend vertically beyond that level to be added. This conservative fusion does not reconstruct every attic, facade layer or multi-storey connection.

## Interior geometry

Estimate dominant orthogonal directions from vertical normals; the cloud no longer needs manual azimuth alignment to XY. Propose wall faces using 2.5 cm plane histograms, 4.5 cm face bands and 10 cm longitudinal bins. Bridge supported internal gaps up to 1.2 m; require at least 0.8 m length and half-level height evidence. Pair compatible faces 7.5–45 cm apart. Single-face thickness defaults to 12 cm and is explicitly inferred.

The monotone zero-order Sugeno support reduces to weights `[0.20, 0.20, 0.25, 0.15, 0.20]` on verticality, planarity, relative height persistence, floor contact and continuity. Acceptance threshold: 0.70 plus geometric residual/height checks. The legacy seven-rule score is retained for research comparison.

Snap endpoints toward nearby perpendicular walls within 0.45 m. Form a common footprint from floor support within 10 cm and ceiling support within 18 cm of the fitted elevations. The wider ceiling band accommodates small finish offsets; it does not fit separate local ceiling heights. Prefer closed wall loops when they explain at least 70% of supported footprint. Otherwise regularize at 30 cm and snap outline vertices to nearby wall faces. Clip wall tips to this common footprint and resolve overlapping wall footprints. Floor and ceiling therefore use the same outline as the wall system.

Provisional spaces use masked distance-transform watershed. Space count is data-derived, not a target copied from a reference. Virtual boundaries remain explicit; they are not observed walls. This stage still approximates a single horizontal interior level.

## Building surface geometry

Normal-guided deterministic RANSAC (seed 48021) proposes vertical walls and roofs. Walls are fit vertically; roof normals require |nz| >= 0.60. Candidate roof points must be near the local top of a 30 cm XY column. Roof refinement uses robust inverse-square residual weights with an 8 mm floor, a 3.5 cm fitting band and 5° normal agreement to avoid averaging adjacent shallow slopes. Up to 28 wall planes and 18 roof planes are considered; connected patches can exceed those counts.

The surface support score is a weighted monotone combination of planarity, P95 residual, normal alignment, area and raster coverage: `[0.25, 0.30, 0.20, 0.10, 0.15]`, threshold 0.65. A support score is not a semantic classifier. Roof candidates additionally need at least 8 nearby 30 cm wall-top cells with elevation agreement within 0.75 m; this rejects some sloping terrain and furniture. Sparse, heavily occluded roofs can consequently be omitted.

Regularization and connection steps:

1. Remove small contour teeth by bounded opening/closing and simplification at 22 cm. Use a convex outline only if its area changes by at most 8% and its deviation is at most the declared tolerance; large recesses remain.
2. Merge overlapping parallel roof observations within 30 cm and 4°. Paired faces estimate thickness; otherwise roof thickness is assumed 15 cm. Preserve the observed upper roof face and extrude downward vertically.
3. Snap supported ridge/valley boundaries within 35 cm. Resolve roof footprint overlaps. Preserve measured eaves/overhangs instead of trimming roofs to walls.
4. Reject wall fragments shorter than 0.60 m, lower than 1.40 m or with very low area coverage. Approximate remaining wall extents by vertical parametric strips. This fills some unobserved portions and can close real openings.
5. Pair parallel wall faces up to 85 cm apart with angular, plan-overlap and height-overlap checks. Otherwise assume 20 cm thickness. These fixed expert limits require review on other building types.
6. Intersect nearby wall endpoints within 50 cm; stabilize neighbouring bases only within 30 cm. Remove redundant plan overlaps and tiny slivers. Curved facades remain multiple planar segments.
7. Trim wall solids under supported roof planes, favouring the roof with greatest measured footprint overlap. A clipping domain may extend at most 50 cm beyond the sampled roof outline. The roof itself is not extended by this operation. Use unbounded plane cuts when a roof covers the complete wall footprint, and finite parametric cutters for partial coverage.
8. Use millimetre precision for geometric overlays to avoid zero-width numerical slivers. Record all joins, rejected candidates and clipping operations in the report.

## IFC and verification

Write IFC4 extrusions and Boolean results, preserve metric global placement, and attach source/evidence/review properties. Interior spaces and first-level boundaries are exported only by the interior branch. Surface-only jobs do not invent rooms or storeys. Validate EXPRESS, triangulate elements and require closed meshes with positive volume before transactional publication. Interior spaces must not overlap.

These checks distinguish **valid individual solids** from **a globally continuous building**. Connection diagnostics and footprint overlap checks are not a global manifold-envelope proof. Missing walls, genuine openings, steps between roofs, unsupported bases, sloped floors, thin objects and curved interfaces can remain unresolved. There is no automatic door/window/MEP reconstruction. Construction-ready geometry requires review.

## Implementation references

- [IfcOpenShell Boolean geometry API](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/geometry/add_boolean/index.html)
- [IfcOpenShell Clipping utility](https://docs.ifcopenshell.org/autoapi/ifcopenshell/util/data/index.html)
- [buildingSMART IFC4 IfcBooleanClippingResult](https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD1/HTML/schema/ifcgeometricmodelresource/lexical/ifcbooleanclippingresult.htm)
- [Shapely geometry operations](https://shapely.readthedocs.io/en/stable/manual.html)
