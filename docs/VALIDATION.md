# Validation record — 0.3.0

## Regression scope

All **23 local Python tests passed** on 27 October 2026. The suite exercises:

- Default unified reconstruction of two rooms (5 walls, 2 provisional spaces).
- A rotated house with a sloping roof, large survey coordinates and millimetre input.
- Measured roof overhangs retained and wall mesh vertices trimmed below the roof; the shared ridge must not produce protruding wall tips.
- Rooms and additional supported sloping roofs exported in a single IFC/building.
- Preservation of a substantial concave recess during outline regularization.
- Floor/ceiling pairing, ambiguity and the actual 1.037 m diagnostic.
- Geometry/fuzzy ablations, LAS/LAZ and Unicode paths, invalid input, resource limits, cancellation, no overwrite, and failed validation with no output publication.

Native CI builds the real plugin and matching CloudCompare on Windows and Linux, loads the DLL/shared object, checks selection, and runs the actual Qt dialog → XYZ64 → Python → IFC path for interior and shell-only synthetic clouds. It verifies there is no reconstruction-mode combo. Packaged LAS/LAZ import is also tested. See the current PR checks for the final per-platform outcome; a scheduled workflow is not evidence of a passed build.

## Diagnostic case: supplied single building

The earlier `Edificio_BIM_fuzzy_interni_v2.ifc` was inspected directly. Its own evidence properties identify it as **PRELIMINARY SEMIAUTOMATIC**, with visually interpreted section boundaries, manually reviewed wall ends, an interpreted mezzanine and provisional spaces. It contains 5 walls, 2 roofs and 4 spaces, plus slabs, columns, beams and stairs. It is not ground truth and is not a reproducible fully automatic output of the old plugin.

The 0.2.0 plugin produced 28 wall patches and 3 roof candidates. Raster boundaries were extruded independently; this caused teeth, duplicate roof observations, gaps and protruding wall tips. Element closure validation did not test whole-building continuity.

The 0.3.0 diagnostic reconstruction produces 15 regularized wall portions and 2 roof surfaces, including the lower attached roof; 17 closed element meshes and no EXPRESS errors. The floor/ceiling evidence gate still does not support the earlier manually interpreted room layout, so this automatic result has no IfcSpace. Missing floors, stairs, mezzanine interpretation and some curved interfaces remain unresolved. The result is visually cleaner than 0.2.0 but is **not equivalent in completeness to the manually reviewed earlier model**.

## Diagnostic case: CaseStudy1 / TUB1

Using the same cloud features, the 0.2.0 ceiling-only footprint covered about 174.38 m² and left many observed walls outside it. The new shared floor/ceiling/wall footprint covers about 273.85 m². The updated inference produces 44 wall candidates and 13 provisional spaces, one floor and one ceiling. The increase in footprint coverage addresses the missing wing, not a claim of improved semantic accuracy. In the local exported IFC, all 59 wall/slab/covering/space meshes were closed; space overlap was zero and wall footprint overlap was zero to numerical precision.

The supplied reference contains no IfcSpace annotations. Its previously derived room candidates are not validated room ground truth. Counts of 13 inferred spaces must therefore not be described as 13 correctly detected rooms. The reference was not used for reconstruction or alignment fitting.

## Limits on conclusions

These two cases and the synthetic tests are development diagnostics. They are not held-out performance evidence; the method has been revised in response to them. The other five benchmark cases have not been rerun with method 0.5. Manual desktop operation and an independent IFC editor remain to be checked. No complete manifold-building guarantee, door/window accuracy, multi-storey reliability or comparison victory over published methods is claimed.
