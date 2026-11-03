# Paired development protocol for method 0.6

This protocol is fixed before the four-profile real-cloud evaluation. It tests a research implementation; the scenes have already informed development and are not held-out evidence.

## Comparisons

Use seven supplied complete LAS files: six ISPRS-derived cases and the additional single building attributed by the user to Antwerp. Identify all inputs and supplied reference IFCs by SHA-256 and point counts. These supplied subsets need not equal the official full benchmark distributions.

Run these profiles on identical inputs with the same coordinate units, 5 cm voxel sampling, 20-neighbour PCA, resource budgets and geometry repairs:

- `geometry_only`: hard geometry and shared regularization; no fuzzy gate.
- `monotone`: previous weighted-membership gate, for continuity with method 0.5.
- `evidence`: experimental quality-aware expert evidence support.
- `relational`: experimental finite-candidate selection including neighbouring-wall relations.

The last two profiles are **expert and uncalibrated**. No IFC reference, room-count target or scene-specific parameter change may enter inference. Learned calibration requires independent labelled candidate sets and is not implemented by fitting the six references. The relational profile does not implement unconstrained global BIM geometry optimization or general curved-room modelling.

## Outcomes

Record all attempts, including failures, software-source hashes, dependency versions, wall-clock runtime, element counts, level/space evidence decisions, evidence/candidate audits, geometric repairs and final validation. An IFC counts as published only after existing schema/solid checks pass. Never skip a failed wall or disable mesh closure to increase completion counts. A partial surface model is not a complete building.

Measure individually closed positive-volume solids separately from whole-building topology. Unsupported rooms, multi-storey ambiguity and unclassified doors/windows remain explicit. All six supplied reference IFCs lack IfcSpace, so do not compute semantic room accuracy from their object counts.

## Geometry proxy

Use only near-vertical triangles of IfcWall (including subclasses), |normal Z|<0.2, in native IFC world SI coordinates. Sample 20,000 points per direction proportionally to triangle area, seed 20261101. Compute Euclidean distance to the closest target triangle with tolerance 0.10 m. Correctness samples generated -> reference; completeness samples reference -> generated; F1 is their harmonic mean. Report direction-specific median/P95 over all samples.

This includes backs and wall-end faces without official observed/unobserved surface selection; it is a development proxy, not an official ISPRS score. Bounds overlap is only a coordinate sanity check. Do not fit ICP/translation/rotation to generated models. Do not score the earlier semiautomatic single-building IFC until its local-to-world transform has been independently verified.

## Interpretation

First compare method 0.5 and repaired method 0.6 using the same baseline profile: this measures engineering/geometric changes. Then compare four method 0.6 profiles with identical repaired geometry: this isolates candidate-selection changes. Record whether changes survive downstream deduplication and regularization. Candidate-count differences and final mesh validity are not evidence of semantic accuracy.

Report per-case changes and failures. Do not select the most favourable profile per test case and call this a single method. Only declare an accuracy improvement when supported by the paired measurements; otherwise report the absence of gain. Six known cases do not establish generalization; external buildings and independent labels are required.
