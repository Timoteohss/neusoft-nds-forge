# Independent Brazil / Paraná map identity

The vehicle-verified independent Brazil package is the reference for this
path. It is a **local experimental namespace**, not an official Neusoft
product or licensed update region. The pipeline reproduces its identity and
package layout from declared values; it does not copy a previous package as a
build input.

## Declared identity

`examples/neusoft-brazil-parana.toml` declares `[neusoft.region_binding]`:

| Field | Value | Meaning |
|---|---|---|
| `product_group_id` | `9000` | Local product group |
| `nav_product_id` | `90000011` | `ROOT.NDS` NAV product row |
| `deco_product_id` | `90000012` | `ROOT.NDS` DECO product row |
| `update_region_set_id` | `9100` | `PRODUCT.NDS` update-region set |
| `update_region_id` | `9102` | Paraná update region |
| `ur_code` | `UR9102` | `db/NAV/UR9102/` directory |
| `area_code` | `AC` | `Area.txt` and version area segment |
| `version_name` | `N.J6.AC.26.00.02.00` | `versionTable.versionName` |
| `iso_country_code` | `BRA` | `SHARED.NDS.regionMetadataTable` |
| time zone | id `6`, `America/Sao_Paulo`, offset `-12` quarters (UTC-3) | SHARED time-zone tables |

`nds_forge.region_binding` validates the namespace before planning: IDs are
distinct positive 31-bit integers, `ur_code == UR<update_region_id>`, the
version area equals `area_code`, and São Paulo must encode UTC-3 as `-12`
quarter-hours. `names.region_id`, `poi.region_id`, `route.region_id` and every
`container_keys` path must agree with the binding.

`profiles/neusoft-brazil-parana.profile.json` records the same values through
the `TargetProfile` contract. Brazil authority is only accepted when
`country_id == 76`; the profile deliberately leaves
`claim_brazil_authority=false` because the IDs are local.

## Paths and package rows

`RegionBinding.required_paths()` derives all 21 package paths. Generated ZV
databases are:

```
db/NAV/UR9102/{BMD,NAME,FTS,POI,ROUTE,SHARED}.NDS
```

The six bootstrap schema seeds are located by filename, decoded with the
runtime key, and written under `UR9102`; no `UR439` path is inherited.
Static bootstrap assets are remapped from their source UR directory.

`package.assemble` writes:

- `BMD.NDS`: level 11 tile blobs from `geometry.bmd`.
- `NAME.NDS` / `FTS.NDS`: name records, substitutions, POI FTS and address
  tables with `updateRegionId=9102`.
- `POI.NDS`: POI rows and virtual tiles from the coverage catalog.
- `ROUTE.NDS`: level 13 main blobs plus AUX rows.
- `SHARED.NDS`: level metadata, `isoCountryCode=BRA`, and the declared time
  zone.
- `PRODUCT.NDS`: `tileContentIndexTable`, `updateRegionTable`,
  `updateRegionNameTable`, `updateRegionSetTable`,
  `updateRegionSetNameTable`, `productNameTable`, `versionTable` and URI rows
  rewritten to the binding.
- `ROOT.NDS`: NAV and DECO product IDs plus product group.
- `Area.txt`: `AC`.
- `metadata.json`: product IDs/group, update-region/set names, `UR9102` paths,
  recomputed file hashes and sizes.

Every generated SQLite database passes `PRAGMA quick_check` after population,
after `VACUUM`, and after ZV roundtrip in `container.pack`. The package layout
and artifact manifest carry the binding, required paths, per-file origin and
`validation_status=unvalidated`.

## Layer logic carried from the verified package

- **BMD level 11:** one continuous quantized polyline per way part per tile.
  The splitter cuts only at tile edges and keeps the shared boundary vertex;
  collapsed fragments are dropped. Name associations stay below the 32767
  per-tile limit and features below 65535.
- **ROUTE level 13:** degree-2 chains merge only within one tile when both
  links are bidirectional, same class and not a protected restriction node.
  Cross-tile node copies are indexed globally, so every shared node receives
  an external reference with a 15-bit tile ordinal and a 16-bit link ID.
  Intersections, link counts and degrees are bounded before encoding; AUX
  rows retain a `uint16` offset guard.
- **NAME / FTS / POI / SHARED:** `rowid` is the SDK identity for FTS results;
  `updateRegionId` is the declared region; names and POIs carry Morton
  coordinates, aliases and substitution rows.

## Build and verification

```sh
python -m nds_forge plan --config examples/neusoft-brazil-parana.toml
python -m nds_forge run --config examples/neusoft-brazil-parana.toml \
  --target container.pack --allow-experimental
```

The run still needs a local PBF, the declared bootstrap directory and six
runtime keys (`NDS_KEY_BMD`, `NDS_KEY_NAME`, `NDS_KEY_FTS`, `NDS_KEY_POI`,
`NDS_KEY_ROUTE`, `NDS_KEY_SHARED`). Keys are resolved only at runtime and never
enter manifests or artifacts.

## Limits

- Generation is not native-parser, SDK, UI or vehicle validation. The verified
  vehicle result is historical evidence for the reference package, not proof
  for every new candidate.
- `validation.native` and `validation.device` remain `planned`; no stage
  invokes the vehicle automatically.
- TMC/EXTTMC and other static bootstrap assets are copied unchanged. They do
  not establish Brazilian traffic coverage.
- OSM turn restrictions are compiled diagnostically for supported via-node
  cases; via-way/conditional restrictions, guidance and legal speeds remain
  incomplete.
- Do not present `90000011`, `90000012`, `9100` or `9102` as official Neusoft
  IDs.
