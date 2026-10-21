# Native POI category evidence and supported OSM mapping

Status: offline source evidence established; initial Paraná configuration enabled.
This is category compatibility evidence, not validation of the new Paraná map in
the Neusoft SDK or car. No statewide build or vehicle action was run for this task.

## Scope and identities

The original Israel release `nds-israel-base-v1.0.0` provides the category tables
retained by this adapter. The wrapped inputs in the bootstrapped release match
the original investigation source files byte for byte. The historical decoded
snapshots are identified separately below; no key is stored in this document.

The machine-readable [evidence catalog](poi-category-evidence.json) records 41
candidate semantic mappings covering 43 OSM tags. Only the 18 candidates whose
observed native FTS records each have one POI category membership are enabled in
the initial profile `neusoft-israel-poi-single-membership-v1`. The other candidates
remain evidence for later work, not supported configuration.

| File | Original wrapped SHA-256 | Historical decoded SHA-256 |
|---|---|---|
| `db/NAV/UR439/POI.NDS` | `638cf12d23c86925542dedc288608a772986152a1390bab12594fc1253ea24dc` | `d4c0be1655ce936a8746449a814a5e0dd0db38fab9c72a28924114649a209c37` |
| `db/NAV/UR439/FTS.NDS` | `e66028968c9ff043a0b41dca37aa07caf0f19a62ea022409fa444ef1447e41f9` | `8359967487c38e1d5fd25a80d3db20e7369a1b282633e4601ed76717b0f2ae0c` |

The decoded source has 154,039 `poiTable` rows and 122,183 `poiFtsTable` rows.
Counts differ because a native POI can have multiple category memberships.
`catId` is the native numeric category ID. `standardCat` is a different field and
must not be used interchangeably. The FTS `poiCategoryId` is a string containing
space-separated U+E000-prefixed category tokens; it is not a plain numeric ID.
Its exact observed token order is preserved. For example, restaurant is
`\uE0001001 \uE0001112`, pharmacy is `\uE0001007 \uE0001326`, and fuel is
`\uE0001008 \uE0001099`.

## Method and reproducible checks

Inspect quiescent decoded copies read-only. REA `inspect_sqlite_database`
recorded schema, categories and native labels. The following evidence IDs refer
to the local REA session; hashes and SQL make the conclusions independently
reproducible without that session:

- Schema: `ev_1b35b49faf1f0bac5c9d674f8edc4bbc482948cdbd4b25c1082b6e2306785bd8`.
- Complete category labels: `ev_c13fa72712daaff7495b4e055850d3a26c77fbfe1de120d422fa892bbaf25608`.
- Category/name relation: `ev_8f2a5cf84097d48d3f4a066ae06797b1541cdc67928eaa275b089030bb86455a`.
- Category metadata: `ev_090baa6d9c55cf5a310fca01e35cc70b55afe1499c6fc70ddbd6c499930d0a22`.
- FTS identity/schema inventory: `ev_8a98a3fef7926e3e8c633585f4e666b7d15d3cf4655e4bfe74829aee05d8d943`.

REA does not execute FTS virtual tables. The cross-database aggregates below
were therefore executed using Python SQLite connections with URI `mode=ro`,
against the same identified snapshots, with POI attached as `p`. These are
whole-table aggregates, not examples inferred from category popularity.

```sql
-- In POI.NDS: resolve numeric IDs through the actual category/name relation.
SELECT c.catId, n.nameString
FROM poiCatToNameStringCollTable AS c
JOIN poiCatNameStringCollTable AS n USING (nameStringCollectionId)
WHERE c.catCollectionId = 0 AND n.languageCode = 1;

-- With FTS.NDS as main and POI.NDS attached read-only as p:
-- First enumerate observed exact FTS strings and associated POI memberships.
SELECT f.poiCategoryId, p.catId, COUNT(*)
FROM poiFtsTable AS f JOIN p.poiTable AS p ON f.poiId = p.poiId
GROUP BY f.poiCategoryId, p.catId;

-- For one exact FTS string selected above, check every membership, not only
-- whether the chosen primary ID occurs somewhere in the result.
SELECT p.catId, COUNT(*)
FROM poiFtsTable AS f JOIN p.poiTable AS p ON f.poiId = p.poiId
WHERE f.poiCategoryId = :exact_fts_category
GROUP BY p.catId ORDER BY p.catId;
```

Native labels/IDs and exact native FTS strings are observations. Choosing the
corresponding OSM tag is an explicit semantic adapter decision; the original
Israel data does not contain these OSM tags. Tags with no supported mapping
remain rejected/reported, rather than receiving a fallback category.

## Enabled initial subset

The final column counts native POIs matched by exact FTS string and category ID.
Every FTS string in this table has exactly the one listed POI membership.
Labels shown in English are native language-code-1 labels; full token labels
and escaped FTS strings are recorded in the JSON catalog.

| OSM tag | Native category | ID | Native matched POIs |
|---|---|---:|---:|
| `amenity=cafe` | Coffee/tea | 1051 | 1423 |
| `amenity=restaurant` | Restaurant | 1112 | 10555 |
| `amenity=cinema` | Cinema | 1303 | 78 |
| `tourism=attraction` | Attractions | 1072 | 2507 |
| `tourism=museum` | Museum | 1307 | 434 |
| `tourism=hotel` | Hotel | 1076 | 1268 |
| `shop=convenience` | Convenience store | 1054 | 4783 |
| `shop=department_store` | Department store | 1059 | 64 |
| `shop=mall` | Mall-Shopping Complex | 1323 | 589 |
| `amenity=pharmacy` | Drugstore or Pharmacy | 1326 | 1151 |
| `shop=books` | Bookstore | 1329 | 436 |
| `amenity=atm` | ATM | 1024 | 103 |
| `amenity=bank` | Bank | 1030 | 1060 |
| `amenity=fuel` | Gas station | 1099 | 960 |
| `amenity=post_office` | Post Office | 1341 | 302 |
| `amenity=car_rental` | Car Rental | 1350 | 205 |
| `amenity=parking` | Parking lot | 1097 | 1643 |
| `amenity=library` | Library | 1355 | 233 |

## Deferred categories and limits

The writer currently emits one category row per generated POI. The following
semantic candidates have multiple native memberships for their exact FTS
strings and are intentionally not activated by this profile:

| Native candidate | Observed memberships |
|---|---|
| Fast Food | 1112, 1365 |
| Bar or Pub | 1302, 1366 |
| Rest area | 1111, 2000 |
| Train Station | 1315, 1379 |
| Hostel | 1319, 1383 |
| Guest House | 1319, 1384 |
| Park-Recreation Area | 1320, 1386 |
| Beach | 1320, 1387 |
| Amusement Park | 1321, 1388 |
| Zoo | 1321, 1389 |
| Grocery | 1325, 1395 |
| Bakery and Baked Goods Store | 1325, 1397 |
| Car Wash-Detailing | 1026, 1405 |
| Car Repair | 1026, 1406 |
| Police Station | 1339, 1403 |
| Dentist-Dental Office | 1352, 1410 |
| Clinics | 1352, 1411 |
| City Hall | 1353, 1414 |
| Higher Education | 1354, 1416 |
| School | 1354, 1417 |
| Cemetery | 1359, 1418 |
| Public Restroom-Toilets | 1359, 1419 |
| Hospital | 1075, 1352 |

Hospital is a useful example: all 34 observed hospital FTS records use
`\uE0001009 \uE0001075 \uE0001352`, with both numeric memberships 1075
(Hospital) and 1352 (Healthcare Facilities). That semantic relationship is
known, but reproducing both memberships needs writer support and focused tests.

Charging stations use numeric category 1344 (Charging station), plus 1431
(Normal Charge), 1432 (Fast Charge), or both. The observed FTS strings and counts
are:

| Exact FTS string | Matched POIs | Native memberships |
|---|---:|---|
| `\uE0001008 \uE0001344 \uE0001431` | 2,455 | 1344, 1431 |
| `\uE0001008 \uE0001344 \uE0001432` | 318 | 1344, 1432 |
| `\uE0001008 \uE0001344 \uE0001431 \uE0001432` | 183 | 1344, 1431, 1432 |

The generic OSM tag `amenity=charging_station` does not establish normal/fast
subtype. No plain generic charging FTS string was observed in this source.
Whether secondary memberships are necessary for every native category browser,
filter and nearby-search path remains unvalidated; SQL proves their existence,
not that omission is harmless. Supporting charging therefore requires an
explicit subtype mapping rule and multiple-membership generation, followed by
native search validation. No subtype is assumed in the initial profile.

A Fire Department label (1404) exists in the category catalog but there are no
POIs using it in this Israel source; no matching native FTS pair was established.
It is not enabled merely because a label exists.

The native category catalog also exposes `showInSearchTree`; a compatible
numeric ID does not promise a dedicated UI entry or visible icon. Exact FTS
strings and native row membership are the bounded compatibility claims here.
The new package still needs SDK and vehicle checks for category searches,
nearby lists, selected POI navigation and presentation. Full OSM coverage,
unnamed POIs and all possible tag combinations are not claimed by this subset.
