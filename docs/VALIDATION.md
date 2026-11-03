# Validation record for version 0.4.0

## Local software checks

All 40 Python tests passed during method 0.6 development. They include the previous 23 checks plus fixed-precision mixed-dimensional overlays, a partitioned parametric gable wall, unknown-support semantics, context-dependent candidate retention, duplicate versus opposite-face constraints, bounded solver fallback and actual evidence/relational room publication. The existing roof-overhang and wall-below-gable tests remain active: mesh closure alone does not establish correct shape.

Native CI builds the actual plugin with matching CloudCompare on Windows and Linux, loads it, tests selection and runs Qt dialog -> XYZ64 -> Python -> IFC for a relational two-room cloud and an evidence sloping-roof shell. Packaged LAS/LAZ import remains tested. Use current PR checks for actual per-platform outcomes; scheduled jobs are not passed checks.

## Baseline real-cloud evidence

The complete method 0.5 / version 0.3.0 paired evaluation on six supplied ISPRS-derived LAS files succeeded only on TUB1. TUB2 and Grainger failed wall-mesh validation; Fire Brigade, UVigo and UoM failed polygon overlays. The additional user-attributed Antwerp building produced partial walls/roofs with no spaces. TUB1 and that building had identical final geometry with and without the old fuzzy gate. This was not evidence of fuzzy accuracy improvement.

The six supplied references contain no IfcSpace. Supplied LAS subsets differ from some official full benchmark inputs. Development wall-surface sampling is not the official observed-surface ISPRS implementation and cannot establish a leaderboard rank.

## Method 0.6 development evaluation

The paired protocol is fixed in EXPERIMENT_PROTOCOL.md. Seven clouds are run under geometry_only, monotone, evidence and relational using identical units and parameters. Current-run artifacts and per-case results are recorded separately with exact source/dependency hashes and published on the PR after completion. Do not infer success from this protocol or from tests on synthetic geometry.

Cached TUB2 and Grainger wall/roof reconstructions passed mandatory schema/mesh checks after the partitioned-solid fix during development. This is a reduced geometry diagnostic, not a full-cloud or whole-building accuracy result. A full-cloud IFC can still be partial and have no supported rooms.

## Scope

The older Edificio_BIM_fuzzy_interni_v2.ifc is explicitly semiautomatic, with manually reviewed/interpreted geometry. It is not independent ground truth or a reproducible automatic baseline. Its four rooms and mezzanine are not claimed as recovered by this tool. No global building-manifold guarantee, door/window accuracy, general multi-storey or curved-room reconstruction, learned calibration or superiority over published methods is claimed. A real desktop/independent IFC-editor review remains necessary.
