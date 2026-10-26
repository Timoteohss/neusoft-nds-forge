# Street search: native FTS → NAME identity and object class

Date: 2026-10-26. Target: `libNdsLib.so`, SHA-256
`c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55`.
REA 6.7.0 / Ghidra 12.1.2. Addresses below are Ghidra addresses;
subtract `0x100000` for module-relative runtime addresses.

## Two independently observed faults

The generated FTS4 `nameFtsTable` had implicit sequential SQLite rowids, while
`namedObjectId` used the stable BMD/NAME identifiers. The native query does not
return that column as the object identity. The captured query for `Luiz` was:

```sql
SELECT (docId & 4294967295), docId, mortonCode, namedObjectClass as rank
FROM nameFtsTable
WHERE nameFtsTable MATCH '(((((Luiz*)))))'
ORDER BY namedObjectClass, docId DESC LIMIT 500;
```

The native `nds_fts_findNextMatchResult` delegates via vtable `+0x88`, through
`FUN_0017261c` and provider vtable `+0x650`, to `FUN_002588cc` and
`FUN_0026b90c`. The latter reads column 0 as the returned object ID.
In the car capture, `Luiz` produced 206 NAME candidates. One lookup asked NAME
for ID `6143`; its actual `namedObjectId` was `16139`, so the SDK got NULL.

Independently, `_name_blob` hardcoded `00 10` at the beginning of every NAME
blob. `FUN_0017f430` reads the first two bytes as a big-endian unsigned class
using `FUN_002d8c4c` and stores it at parsed object `+0x38`.
`nds_name_getNamedObject` exposes that class at returned object `+4`.
Our street was therefore class **16**, while the FTS row said class **11**.
No meaning for class 16 is required to establish this class mismatch.

## Controlled runtime proof

A temporary Frida hook remapped the third argument of
`nds_name_getNamedObject` from FTS rowid to actual namedObjectId. Objects then
loaded successfully, but street results were still absent. Adding the correct
class to the returned result changed the same OFFLINE request from zero street
results to 20 suggestions, including `Rua Luiz França`,
`Rua Luiz Cavalcanti Filho`, `Rua Luiz Bertolini`, and `Rua Luiz Condessa`.
This was a bounded diagnostic intervention; it is not the delivered fix.

The request uses the shipped Thrift `SugSearchParams` with `netMode=OFFLINE(0)`,
not the default UI mode. Raw request/reply JSON is retained outside this repo.

## Generator changes

`nds_forge/layers/package_assemble.py` now writes:

- NAME: the actual `item['class']` in the first u16 of every generated object;
- FTS: explicit `rowid = item['id']`, so the native low-32-bit expression resolves
  the same object as `namedObjectId` and NAME/BMD.

`rowid` works for FTS4 and the synthetic FTS5 test fixtures. This change keeps
these generated IDs within the existing flat ID scheme; it does not implement
OEM segmented spatial docids. Minimal NAME objects still omit geometry and
parent references. Result coordinates are obtained from the existing FTS Morton
code; house numbers and parent/address formatting need separate implementation.

## Corrections to the previous investigation

The former `STATUS-2026-10-26.md` section 6 should not be used as the causal
explanation of absent street suggestions:

- A nonempty `routingAuxTileTable.nameAttributeLayer` is not required for the
  successful offline suggestion experiment above. It remains missing for road
  names on SDK route/map-matching APIs.
- The `nds_rt_getNamesOfLink` key is `(UR-index << 48) | (tileId << 16) |
  (linkIndex << 1) | direction`, as shown by `FUN_001fa8c0`. The previous
  `tileId << 32` scan was invalid and does not establish absence of references.
- `FUN_0023f824` reads SQLite strings from a database wrapper. Its `+0x330` gate
  must not be read from a NameAttributeLayer pointer. The previous claim that
  this is a layer entry count is unsupported.
- Explicit `OFFLINE(0)` requests make the AVD useful for local search probes if
  its local product metadata/configuration are operational. Its default online
  UI results alone never establish local search compatibility.

## Reproduction and evidence

Evidence directory, outside Git (contains local runtime data):
`../geely-ex2-investigation/neusoft-maps/native/street-search-20261026/`.

- `0x17f430.json`, `0x2d8c4c.json`, `0x193510.json`: REA parser/getter evidence.
- `0x2588cc.json`, `0x26b90c.json`: REA native FTS result path.
- `car-sql.jsonl`: actual native SQL and step observations.
- `car-name-object.jsonl`: uncorrected NAME lookups, IDs and NULL results.
- `docid-remap-probe.jsonl`, `docid-class-probe.jsonl`: controlled interventions.
- `car-offline-before.json`, `car-docid-remap-result.json`,
  `car-docid-class-result.json`: raw offline SDK replies.
- `search_probe.py`: bounded explicit offline request; requires ADB and the
  adjacent project's `tools/probe_route_sdk.py` / `route_app_protocol.py`.

```sh
cd ../geely-ex2-investigation
python3 neusoft-maps/native/street-search-20261026/search_probe.py \
  'Rua Luiz' --device 10.10.10.116:5555 --output /tmp/street-search-result.json
```

The implementation's regression checks compare the native identity expression
against namedObjectId and verify each NAME class, including administrative
records. They exercise generated SQL fixtures and do not constitute SDK proof.

## Validation status

- Controlled native SDK proof: observed on the car, explicit offline request.
- Generator: implemented; 15 targeted tests pass after loading the pipeline
  registry (`import nds_forge.pipeline`). A direct isolated test invocation
  exposed a pre-existing registry initialization dependency, not a data failure.
- Uninstrumented SDK and app UI: passed on the car with the corrected fixture.
- Fresh full build: all 14 stages completed; decoded search tables equal the
  exact installed databases. See the final verification below.

## Uninstrumented car validation

The controlled package `Geely_EX2_CURITIBA_v10_searchfix` is now active under
`/data/user/0/com.neusoft.na.navigation/files/map-test-20261026/`.
It was built by calling the production `_write_names` on the retained layer
inputs. A frozen copy of that exact writer is retained as
`package_assemble.searchfix.py` (SHA-256
`4b917e31f7ee65126ddb2f83ce12ecf55157af0d2144f1b4d783d180498a1238`),
alongside `street-search-writer.patch`, in the evidence directory. Against the original installed v10 package, the database payload changes
are confined to NAME, FTS, and metadata hashes. The original package is intact.

The SDK was restarted and all Frida hooks/server were stopped before these
explicit OFFLINE requests:

| Query | SDK result |
| --- | --- |
| `Rua Luiz` | 20 suggestions, including Rua Luiz França |
| `Brasil` | Avenida Brasil |
| `Avenida Brasil` | Avenida Brasil (tested in first street-only candidate) |
| `Miguel Caluf` | Rua Miguel Caluf |
| `Luiz França` | Rua Luiz França and Travessa Professor Álvaro Luiz Junqueira França (first candidate) |
| `Farmatotal` | Farmatotal, Farmatotal, Farmatotal Centenário |
| `zzzxqsemrua` | zero results |

The Farmatotal control exposed the same implicit-rowid defect in `poiFtsTable`:
the original package returned `Porcini Trattoria`, `Bio Posto Tarumã`, and
`Bio Posto Tarumã` for that query. Explicit `rowid = poiId` fixes that join too.
The regression test now exercises both native identity expressions.

Raw final SDK replies: `car-final-*.json`. They are runtime records and remain
outside Git. No APK/native-library patch, data clear, or geometry/route change
was needed to make the suggestions work.

Rollback configuration backup:
`/data/user/0/com.neusoft.na.navigation/files/install/etc/sdk.ini.before-street-search-20261026`.
The prior package is `Geely_EX2_CURITIBA_v10_fix`; changing `path_mapdata` back
to that directory and restarting the app restores it.

The final complete pipeline rebuild completed in a fresh Python process after
the POI correction. The first run had already imported the writer before that
follow-up change; use `forge-build-final.json` for the corrected run.
The on-car fixture was also generated in a fresh process with all final changes.

UI confirmation was also obtained with ordinary ADB taps/text entry: typing
`Brasil` in the real app displayed **Avenida Brasil** in
`com.neusoft.na.navigation:id/tvTitle`. Evidence:
`car-ui-street.xml` and `car-ui-street-search-working.png` outside the repo.
The car is left on that search screen; no route or guidance was started.

Final on-car packed database hashes:

- NAME: `396d8d41853eb7a375da9d85bb92989af794a3b9054562924d6ba204c63381d8`
- FTS: `f96773d959680ddd74f798aa64ef0e9a50883574b70224494f6f36f252021a64`

`validation-final.json` binds the offline result assertions, UI evidence,
installation receipt and exact on-car hashes. A newly packed SQLite file can
have a different physical hash while carrying identical rows, so compare both
artifact hashes and decoded semantic content when checking another build.

Concurrent address-number work appeared in `address.py`, `package_steps.py`,
`steps.py`, and additional hunks of `package_assemble.py` during this session.
It is not part of this validated search change and has not been installed on
the car. Preserve that work. Keep the tested NAME/FTS fixture separate from any
later full-package build that includes those additional changes.


## Final full-build verification

`forge-build-final.json` records completion of all 14 stages using
`examples/curitiba-v10-full-masks.toml` and no network acquisition.
`full-build-validation.json` compares decoded tables against NAME/FTS pulled
back from the validated car installation:

- `namedObjectTable`: 9,573 identical rows;
- `nameFtsTable`: 9,573 identical rows, zero native-ID mismatches;
- `poiFtsTable`: 2,152 identical rows, zero native-ID mismatches;
- zero NAME class mismatches against the canonical name records.

Full-build packed NAME SHA-256:
`64a670d855177e7003a349530052303720d1847ba60617fbb04141b52ca2144c`.
Full-build packed FTS SHA-256:
`53066cdd2f92c4c2093d4f975c2172bdefc5f2ccdac522d2744360d57d945a63`.
Physical SQLite packing differs from the tested fixture; row equality is
verified independently. Other package layers are outside this search validation.

Targeted tests: 15 passed (writer, municipality assignment/extraction, street
search). Ruff passed for the changed writer and regression test; mypy passed
for the writer before the concurrent address wiring. The exact tested writer
snapshot and source hash above preserve that scope.
REA observations were also saved in `rea-search.snapshot.json` (32 evidence
records). Both the car's temporary Frida server and the server started on the
AVD were stopped. The corrected map remains active on the car.
