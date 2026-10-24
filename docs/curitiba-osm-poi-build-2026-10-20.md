# Curitiba: real OSM POIs and package evidence — 2026-10-20

## What changed

The previous Curitiba package contained **10 curated POIs**, despite the OSM
compiler and Paraná configuration existing in the repository. The active
Curitiba configuration still selected `profile_curated`. That earlier package
must not be described as containing the general OSM POI extraction.

`examples/neusoft-curitiba-v9.toml` now selects `source = "osm"`, with the pinned
18-category native profile described in [native category evidence](poi-native-categories.md).
`examples/neusoft-curitiba-v9-curated.toml` preserves the original ten-POI mode
with a separate workspace. No curated input records were edited or removed.

## Real extraction and output

A complete offline Curitiba CLI build executed all 13 stages, with zero cached
stages. It used the pinned Paraná PBF as source and the existing Curitiba
boundary, not the whole state as output. The new POI intermediate has SHA-256
`d38bf92c97f8367d406c80f8cf84449ca3b19a56a25b47d5f494eae4bfbfb6da`.
The result contains **2,152 OSM POIs**, from 1,449 nodes and 703 ways, across all
18 enabled categories. Every accepted row is marked `osm_compiled`, preserving
its OSM source identity/tags and source hash; curated data was not used.

| Accepted category | Count |
|---|---:|
| Coffee/tea | 81 |
| Restaurant | 641 |
| Cinema | 14 |
| Attractions | 39 |
| Museum | 33 |
| Hotel | 95 |
| Convenience store | 110 |
| Department store | 51 |
| Mall-Shopping Complex | 56 |
| Drugstore or Pharmacy | 338 |
| Bookstore | 12 |
| ATM | 21 |
| Bank | 185 |
| Gas station | 195 |
| Post Office | 22 |
| Car Rental | 7 |
| Parking lot | 201 |
| Library | 51 |

The compiler evaluated 13,480 POI candidates and rejected 11,328. Reasons are
exclusive according to the compiler's validation order; for example, an
unsupported tag without a name is counted under missing name first.

| Rejection reason | Count |
|---|---:|
| Unmapped category | 5,823 |
| Missing name/brand/operator | 5,118 |
| Invalid or unresolved coordinates | 367 |
| Spatial/name/category duplicate | 20 |

The dataset includes 514,687 nodes, 49,117 ways and 19,793 relations, including
supporting road entities and relations beyond accepted POIs. Those totals are
not POI counts. No relation POI was accepted. The extractor does not perform a
second pass to collect every untagged member way of a POI multipolygon;
partial/unresolved relations are rejected rather than assigned guessed
coordinates. Nodes and ways provide the demonstrated coverage. Complete POI
relation geometry remains a gap. Way representative points are vertex means,
not polygon centroids. Unnamed and unsupported-category POIs remain excluded.

## Package and verification

The package contains 21 map files totalling **85,144,414 bytes**, excluding its
sidecar. `POI.NDS` and `FTS.NDS` each contain 2,152 POI entries; every FTS category
path matches the corresponding numeric POI category ID. Actual queries on the
decoded final FTS database returned 49 matches for `Farmacia`, 82 for `Posto`,
56 for `Hotel`, and 79 for `Restaurante`. These are name-term matches, not
category totals. All six generated ZV databases were decoded and passed SQLite
`quick_check`. All 21 sidecar byte counts and SHA-256 values matched.

Compared with the previous curated package, only `FTS.NDS`, `POI.NDS` and
`metadata.json` changed. The other 18 map files remained byte-identical,
including BMD, NAME and ROUTE. That is a bounded comparison, not a native SDK
validation.

The first complete OSM build exposed a manifest classification bug: SHARED
content is derived metadata, but membership in the encrypted-container set
incorrectly overrode its role to `generated`. The ordinary inventory checker
correctly failed with `wrong origin role for db/NAV/UR439/SHARED.NDS`.
The assembler now gives derived-content roles precedence over container
storage format. A regression checks SHARED and all 21 inventory roles.

Only the registered production `package.assemble` and `container.pack`
executors were then rerun in fresh staging, using the already-generated layer
artifacts. Before reuse, all outputs of their seven direct upstream dependency
manifests were checked against actual hashes. The independent runner records
source/config hashes, executor identities, input-manifest hashes and new output
hashes. It explicitly states `engine_state_updated = false`: this targeted
rerun does not forge cache fingerprints or claim a second complete CLI build.
The ordinary inventory checker passed the new package unchanged: five generated,
four derived and twelve copied assets. All 21 map files remained byte-identical
to the first OSM build; only the sidecar's origin classification changed.

The final sidecar SHA-256 is
`7e3ffd08a2fb1def0be8b64dff116940812c31a4b23b5d4229b53e29720a5ded`.
The selected package remains **unvalidated for native parser, SDK, UI, guidance
and vehicle use**. A successful extraction, SQL search or inventory check does
not close those gates. No Paraná build or car operation occurred in this work.

## Local outputs and preservation

Paths below are relative to `examples/work/neusoft-curitiba-v9/` and are ignored
local data, not repository inputs:

- `artifacts/package-final/`: first full OSM build, preserved with the SHARED
  origin-role error in its sidecar.
- `artifacts/package-final-osm-fixed/`: corrected package from the targeted
  production executors; normal inventory/hash checker passed.
- `artifacts/package-osm-fixed/`: logical SQLite package produced by the targeted
  production assembler, before container packing.
- `backups/before-osm-pois-20261020/`: complete prior artifacts/state/config
  snapshot. The prior curated final package was compared by SHA-256 and byte
  count before rebuilding; the backup includes hash manifests for its contents.
- `curitiba-osm-full-build-20261020.log`: complete CLI result with 13 completed
  stages and `overall_validation = "unvalidated"`.
- `curitiba-osm-full-build-validation-20261020.json`: first OSM package checks,
  including the original SHARED inventory failure, retained as evidence.
- `curitiba-osm-targeted-rerun-20261020.json`: explicit independent runner record
  for the corrected package at its separate publication path.

The corrected package is published as `artifacts/package-final-osm-fixed`
and was checked normally. Promotion was not performed before observation was
stopped at the user's request; both output directories are preserved. No output file or manifest was
patched by hand to obtain a passing result. The pipeline engine's previous
`container__pack.json` state remains historical and has the first OSM sidecar
hash; it is not rewritten to attest to the targeted output. A future normal
run must resolve its own cache/code fingerprints.

Runtime keys were supplied through external `file:` providers using temporary
mode-0600 TOML files. The files were removed after execution. No key values,
private maps or PBF data are added to the repository.
