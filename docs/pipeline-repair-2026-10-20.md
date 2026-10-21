# Pipeline corrections and Paraná experiment reset

## Reset scope

On 2026-10-20 the user authorized stopping the Paraná build and removing its
generated state. At cleanup no process matching `nds_forge.cli` with the Paraná
configuration remained; no signal was sent. Only the following directories were
removed: `examples/work/neusoft-parana-v9/artifacts`, `.staging` and `state`.
The example configuration, source PBF in the Curitiba input area, and shared
Israel bootstrap were preserved. No new Paraná build or vehicle test was run.

## Cache invalidation

The fingerprint previously omitted codecs imported from subpackages by executor
wrappers. It now hashes all shipped Python sources recursively using relative
paths, deliberately invalidating more stages than a minimal import closure.
External plugin executor hashing remains; arbitrary external plugin imports
still require dependency handling by the plugin author.

PBF contents and declared external inputs remain hashed. The new optional
`Step.optional_external_inputs` declares files that may be absent from config;
configured files must exist and are hashed. This lets OSM POIs run without a
curated file while preserving curated same-path input invalidation.

A regression reproduced stale cached reuse before the fix. It now verifies
rebuilding actual pipeline stages and descendants when hashes change in BMD,
AUX, extraction or normalization modules. Another regression changes curated
POI bytes at the same path and verifies regeneration.

## Explicit OSM POI path

`neusoft.poi.source = "profile_curated"` preserves Curitiba compatibility.
`source = "osm"` executes clipping followed by the OSM POI compiler. Extraction
retains standalone tagged POI nodes and non-road POI ways without adding
footprints to BMD road geometry. `osm_tag_categories` must explicitly map OSM
`key=value` tags to native category strings in `poi_table_category_ids`; the
standalone compiler's speculative defaults are never selected automatically.

Numeric string IDs support SQL joins, while `source_ref` and `source_id` preserve
OSM identity. Output hashes are recomputed after conversion. The intermediate
document records input dataset hash, OSM provenance, clipping lineage and mapping.
Partial relations are rejected rather than locating POIs from incomplete members.
Way locations use the vertex mean, not a polygon centroid. Complete multipolygon
extraction, general category compatibility and native POI/UI behavior remain
unvalidated.

The Paraná example no longer points to Curitiba's curated POIs. Its explicit OSM
mapping is intentionally empty and preflight blocks until verified native
categories are supplied. This is a known configuration blocker, not a complete
state-wide POI dataset.

## Unsupported requested capabilities

Python address and route-rule helpers do not encode those features in the native
package. Requesting `pipeline.required_capabilities = ["address_search",
"native_turn_restrictions"]` fails before workspace creation. This gate checks
generation declarations, not native/car acceptance. Administrative and route-rule
limits are described in [state-admin-and-route-limits.md](state-admin-and-route-limits.md).

## Empty AUX selection

The original uncommitted experiment silently retained link zero for a tile with
no selected geometry. Default behavior is fail-closed again. Explicit
`neusoft.route.empty_aux_policy = "retain_first_experimental"` permits that
experiment and reports its count, tile IDs and pending native/SDK validation.
The default is `"reject"`. A local roundtrip does not prove the fallback valid
in the car.

## Checks

```sh
.venv/bin/python -m unittest tests.test_pipeline_contract tests.test_osm_extract \
  tests.test_osm_poi tests.test_osm_poi_pipeline tests.test_route_aux_policy -v
```

These tests use synthetic temporary PBF/JSON/codec data. Optional PBF tests require
the project's OSM dependencies. They exercise actual stage execution, extraction,
IDs, input/code cache invalidation, capability gating and AUX policy reporting.
They do not build Paraná or establish native compatibility.
