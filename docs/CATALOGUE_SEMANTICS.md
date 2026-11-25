# Method 0.8: catalogue semantic hypotheses

The three experimental catalogue profiles run only in unified mode. They start
from the same `geometry_only` reconstruction and use the same local candidate
pool and admissibility guards. `experimental_catalogue_crisp` uses a crisp
support control, `experimental_catalogue_continuous` uses mean aggregation, and
`experimental_catalogue_fuzzy` uses minimum aggregation. Support values are
expert indices, not calibrated probabilities. This change evaluates the method;
it does not retune arbitrary thresholds for individual buildings.

## Input and provenance

Use `--catalogue custom.json`, or the optional catalogue file field in the English
dialog. With no path, the engine uses `backend/fuzzyresf/catalogue_default.json`.
A supplied catalogue is rejected for non-catalogue profiles rather than silently
ignored. Catalogue jobs require unified mode. The request contains no reference
IFC, ground-truth model or material annotation input.

The typed JSON has `schema_version: 1`, a `catalogue_id`, `units: "m"` and a
`families` array. Each family identifies its IFC class and dimensional meaning;
wall thickness hypotheses use finished-wall thickness, not an arbitrary door
coordination dimension. Empty `families` is valid and supplies implicit UNKNOWN.
The catalogue is read, validated and hashed once before cloud analysis. The
frozen request-time content and source SHA-256 make later changes to the source
file distinguishable from the catalogue actually used for reconstruction.

The bundled catalogue is `MetricHandbook1999_examples`. Its W241 example has
finished thickness 0.241 m and layers 0.013/0.215/0.013 m; W280 has finished
thickness 0.280 m and layers 0.013/0.102/0.050/0.102/0.013 m. UNKNOWN permits free
geometry with no nominal thickness. The examples derive from David Adler,
*Metric Handbook*, second edition (1999), ISBN 0750608994, Table III, examples
1A/2A, printed page 40-7 (PDF page 625).

These examples have `GENERAL_REFERENCE` applicability and are not physical
measurements. They are a general prior, not an as-built description, a regulatory
requirement or evidence of measured materials. Catalogue layer descriptions are
reference examples only and are not reconstructed physical layers.

## Output meaning and limits

IFC and JSON report the selected family as a hypothesis. Physical thickness and
materials remain UNKNOWN. Geometric profile width describes the actual exported
profile and must agree with that geometry; it is not promoted to measured
construction thickness. Source catalogue path/hash and unknown scope are retained
for partial models and for additional surfaces outside the supported main-level
semantic analysis. An unsupported or unobservable surface keeps UNKNOWN even
when another wall receives a family hypothesis.

The method does not reconstruct doors or windows. Bridged wall completion may
cover real openings, and door coordination sizes cannot be interpreted as wall
thickness. Room clipping does not establish complete building topology or a
certified envelope. No new native Windows build is claimed by these source
changes.

## Controlled comparisons

Compare all three full-catalogue profiles using the same input, limits, candidate
pool and exported scope. This isolates aggregation within that pool. For the
catalogue contribution, compare `experimental_catalogue_crisp` with the full
catalogue against the same profile with
[`examples/catalogue_unknown_only.json`](examples/catalogue_unknown_only.json).
The empty-family control retains the same crisp engine and adds implicit
UNKNOWN. Comparing `geometry_only` directly against a full catalogue run also
changes local refinement and therefore confounds the catalogue contribution.

No trained profile is exposed. A standalone independent trainer is an auxiliary
tool; the repository currently has no real annotated training dataset from which
a trained reconstruction profile can be claimed. Synthetic fixtures and internal
strip checks do not replace independent annotations or held-out real-building
evaluation. Object counts alone do not measure semantic accuracy.
