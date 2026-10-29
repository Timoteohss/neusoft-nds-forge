# Handoff: native ROUTE turn restrictions (2026-10-25, session 2)

This supersedes the previous handoff. Everything below is tied to the binary

`../geely-ex2-investigation/neusoft-maps/native/libNdsLib.so`

SHA-256 `c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55`
(identical in `artifacts/NAVI-installed.apk` and on the AVD).

**Format proven: yes, for the structural encoding listed below.**
**Semantics of the `0x9a`/`0x9b` attribute (prohibition vs permission) proven: no.**
**Writer/encoder: not implemented, and must not be implemented from the current evidence.**

## 1. Corrections to the previous handoff

* Header index 4 is **not** a turn-restriction table and is **not decoded** by
  the RoutingTile parser `0x10e6a0`. The parser stores the ten raw offsets at
  `tile+0x24..0x48`; the decode blocks use indices 0,1,2,3,5,8,9 only. Index 4
  is stored at `tile+0x34` and never read in that function.
* Header index 5 **is** decoded, but it is a per-link table of `{15-bit key,
  bit offset}` records pointing at lists of values bounded by the external-tile
  count. It is not a restriction table either.
* The transition data the native API returns comes from the **attribute-pattern
  table** (`tile+0x70`), which is built in memory from section 0 and section 3.
  It is not serialized as its own section.

## 2. Proven tile layout (RoutingTile, offsets from the decompiled parser)

| Offset | Meaning |
|---:|---|
| `+0x20` | tile id |
| `+0x24..0x48` | the ten raw ROUTE header offsets (`u32` each) |
| `+0x4c` | level byte (argument 4 of the parser) |
| `+0x4d` | coordinate bit width = `31 - level - shift` |
| `+0x4e` | `shift` |
| `+0x50`, `+0x54` | origin (NDS x, y) |
| `+0x60` | `u16` from section 0 |
| `+0x64` | `u32` from section 0 |
| `+0x68`, `+0x69` | codec widths selected from the model enum |
| `+0x70` | self-relative pointer to 8-byte attribute-pattern records |
| `+0x78` | pattern count (scratch-owned vector length) |
| `+0x80..0xdf` | 0x60-byte basic-attribute copy taken from the provider |
| `+0xe0` | pattern index after the last link |
| `+0xf0` / `+0xf8` | intersection records `{u32 bit offset, u32 pattern index}` / count |
| `+0x100` | pattern index after the last intersection |
| `+0x108` / `+0x110` | link records, stride 12 / link count |
| `+0x118` / `+0x120` | section-5 table, stride 4 / count |
| `+0x138` / `+0x140` | external tile ids (`u32`) / count |
| `+0x148` / `+0x150` | `u16` table used by pattern keys `0x8..0xb` / count |

Link record (12 bytes): `u32 flags(0xffe00000 mask)`, `u16 intersection A`,
`u16 intersection B` (both `0x8000 | index`), `u32 pattern start & 0x3ffff`.

## 3. Attribute patterns and `0x9a`/`0x9b` (proven structure)

Pattern record (8 bytes, `tile+0x70`):

```
u16 word : bits 0..12 key, bits 13..14 direction (1 fwd, 2 rev, 3 both), bit 15 group start
u16 pad
u32 bit offset into the tile blob
```

For keys `0x9a`/`0x9b` the payload at that absolute bit offset is:

```
u8 count
count x ( 1-bit external, [15-bit external index if external], 16-bit (dir<<15 | link) )
```

The getter materializes each target as
`(routing_level << 48) | (tile << 16) | (link << 1) | ((dir_bit) ^ 1)`, where
`tile` is the tile itself for local targets or `external_tiles[index]`
(section 9) for external ones.

`0x112804` (`nds_rt_getContinuedTurnRestrictions` core) scans the pattern range
of one link, keeps records where `(word & 0x1ffe) == 0x9a` and
`(word >> 13) & 3 == (dir ? 2 : 1)`, and emits one 32-byte element per record:

```
+0x00  std::vector<u64> targets {begin, end, cap}
+0x18  u8 is_0x9a (exact key == 0x9a)
+0x19  u8 attribute-condition result
+0x1a  u8 flags bit 4
+0x1b  u8 flags bit 6
+0x1c  u8 derived continuation flag
```

## 4. Getter contracts (proven)

```
void* nds_rt_getTransitions(handle, uint64 id, uint32 direction, void* ctx, uint32* count)
        export 0x939c0 -> body 0xf82e8
void* nds_rt_getContinuedTurnRestrictions(handle, uint64 id, void* ctx, uint32* count)
        export 0x939d0 -> body 0xf91c4
void* nds_rt_getTransitionAttributes(handle, uint64 id, uint64 from, void* out, uint32 flag)
        export 0x939c8 -> body 0xf8d34
void* nds_rt_getAttributeTimeConditions(handle, uint64 id, uint32 kind, void* ctx, uint32* count)
        export 0x939d4 -> body 0xf92d0
void* nds_rt_getAllRoutingLevels(handle, uint32* count_a)
        export 0x93980 -> body 0xf4674   (returns one byte per NDS level, [13,11,9,7,5])
uint32 nds_rt_getRoutingProducts(handle)
        export 0x93988 -> body 0xf46e0   (returns a count; 1 for the retained dbs)
```

`handle` is the object returned by `nds_createRoutingDbHandle(db)` (size `0xc10`);
`handle+0xb8` holds the RoutingProvider whose vtable `+0xc0` returns the tile
blob and `+0xc8` parses it.

`id` = `(routing_level << 48) | (tile << 16) | (link << 1) | dir`.

**The `routing_level` field is 0 for the application's own calls on both
retained products.** It is not the NDS level; the NDS level is derivable from the
tile id (`bit_length - 17`). Passing the NDS level (13) or another guess makes
the blob lookup fail because `provider+0x340` maps at most two routing slots.

`nds_rt_getTransitions` output entry (24 bytes):

```
+0x00 u64 target id
+0x08 u8, +0x09 u8, +0x0a u16 mask0, +0x0c..+0x12 four more u16 masks, +0x14 u8
```

The `u16` masks are expanded by `0x1f8b0c` from the `u16` table that pattern key
`0x8..0xb` selects; each mask has one bit per transition entry of the same
intersection. Whether the bits mean allowed or forbidden is not proven.

## 5. Section 3 and section 5 (proven, statically verified)

`tools/route_transitions.py` decodes both without the native library.

* Section 3: `u16 count`, then `count` records of
  `u16 (0x8000|dir, link)`, `var15`, `u16`. The parser turns each into a key
  `0x8c` pattern record. Verified: decoded link indices stay below the tile's
  link count (e.g. 13 downlinks, max link 44 of 45).
* Section 5: `u16 count`, then per record `15-bit key`, then the entry count
  (`u8` for model < 11, 29-bit codec otherwise), then that many `var15` values.
  The stored bit offset is the current position relative to the section start.
  Verified: tile 540413880 gives `(key 0, offset 31)` and `(key 1, offset 62)`,
  exactly the `section5Rows` the native parser produced.

## 6. Differential verification against the native library

Environment: isolated AVD `emulator-5554`, Frida 17.24.3, process
`com.neusoft.service.sdk`, database copy of the retained ISR product.

* 300 level-13 tiles scanned with the real provider.
* 16 tiles contain `0x9a`/`0x9b` patterns.
* 109 `getContinuedTurnRestrictions` records were compared with the static
  decoder: **109/109 identical target sets, including external-tile targets**.
  Result: `native/route-transition-research-20261025/static-verification.json`
  and `hunt-300tiles-native.json`.

Additional checks:

* `nds_open` on a copy of the original ISR database returns null unless
  `db/DECO/PRODUCT.NDS` (and its UR0 files) is present. `ROOT.NDS` alone or
  `ROOT.NDS` + `NAV` is not enough. This was the reason the previous attempt
  failed, not encryption.
* The blob the provider returns for tile `540413880` is **1028 bytes**, exactly
  the length of that tile in the decoded ROUTE sqlite.
* The route engine lives in `libna_onecore.so` (caller `0x25c7c39c`); it called
  `nds_rt_getTransitions` many times during a local route and **never called
  `nds_rt_getContinuedTurnRestrictions`** (hook evidence).

## 6b. Plain-SQLite workbench (step 0, verified)

A database assembled only from plain SQLite files opens through `nds_open` and
returns byte-identical getter results, so tiles can be edited with `sqlite3`
without the container keys:

```
db/ROOT.NDS, db/ROOT.NDK, db/Area.txt, db/NAV/PRODUCT.NDS, db/DECO/**
    from live-test-source/Geely_E245_ISR/db
db/NAV/UR439/ROUTE.NDS, db/NAV/UR439/SHARED.NDS
    from decoded/live-test-source/Geely_E245_ISR/db  (plain sqlite,
    ROUTE sha256 ca78fd55...)
```

* `nds_open` succeeds only after the SDK process has been started once by a real
  route (VFS registration); the DECO product is still required.
* Tile `540413880` link 0 through the plain db returns exactly the same targets
  as through the compressed container (`20360fb80002/0000/0041/001a` and
  `20360fb90022/0001`).
* Byte edit round-trip: patching only the 16 payload bits at bit 4337..4352 of
  tile `540421470` (first target link 14 -> 19) changed the native
  `getContinuedTurnRestrictions` output from `20362d5e001d` to `20362d5e0027`
  with the other two targets untouched. Patched file sha256
  `bc9c45101120031a8bc84ce035fcff023f373841406c2d8fd826c60f4866f5d8`.
* Evidence: `plain-db-step0.json` in this directory.

This is the facility required for the A/B experiments (mask polarity, key
`0x9a`/`0x9b`, `count=0`).

## 6c. Transition-mask polarity (step 1, verified)

Consumer (route engine in `libna_onecore.so`, call return offset `0x172939c`,
getter through vtable `+0x88`):

1. Finds the entry whose id equals `from_id ^ ((~dirflag) & 1)`.
2. If `dirflag == 0`, the mask is `entries[found].mask0` (u16 at `entry+0x0a`);
   if `dirflag == 1`, the mask is `1 << found_index`.
3. For each candidate `j`: `dirflag == 0` tests `mask & (1 << j)`;
   `dirflag == 1` tests `entries[j].mask0 & mask`.
4. `cbz w9 -> 0x1729758` drops the candidate when the bit is clear. Further
   filters read `u16` at `entry+0x0c`, `+0x0e`, `+0x10`, `+0x12`.

**Bit set = candidate allowed; bit clear = candidate not considered.**

Runtime A/B on the plain-SQLite CAJURU copy (route
`-25.4546358,-49.2203373 -> -25.4706999,-49.2113838`, option 1):

| Experiment | getTransitions hits modified | Distance | Result field 1 |
|---|---:|---:|---:|
| control | 0 | 2355.27 m | 7 |
| negative control: link 0 of tile 664743895 (never queried) | 0 | 2355.27 m | 7 |
| all entry masks zeroed on every call | 5077 | 372.85 m | 10 |
| zeroed only tile 664746624 | 161 | 2036 m | 13 |
| zeroed only tile 664746626 | 2832 | 827 m | 16 |
| zeroed only tile 664743895 | 18 | 2218 m | 19 |

Zeroing the masks changes or degenerates the route, so the route engine does
consume these bits. Evidence: `mask-polarity-step1.json`, `mask_ab.js`.

Not yet proven: a `0 -> 1` allowance test, which needs a tile that already has a
real zero bit. The CAJURU tiles have no mask patterns (all masks default to
all-ones), and the retained ISR database cannot be used by the app's route
engine on the AVD: `nds_open` works in the SDK process, but `nds_reopen()` in the
service returns 0 and route requests time out. The retained ISR copy has no
`NS.bin` activation file, so original-data routing on the AVD is blocked by
licensing, not by the format.

## 6d. `getContinuedTurnRestrictions` wiring (step 2 groundwork)

The SDK keeps one interface object whose function fields are the resolved
exports. Dumping it at runtime (`libna_onecore` call site `0x1729398`) gives:

| field | export |
|---:|---|
| `0x70` | `0x939ac` getGeoLineBasicAttributes |
| `0x78` | `0x939b0` getShapePoints |
| `0x80` | `0x939b4` getGeoLinePoints |
| `0x88` | `0x939b8` getIntersection |
| `0x90` | `0x939bc` getNamesOfIntersection |
| `0x98` | `0x939c0` getTransitions |
| `0xa0` | `0x939c8` getTransitionAttributes |
| `0xa8` | `0x939d0` getContinuedTurnRestrictions |
| `0xb0` | `0x939d4` getAttributeTimeConditions |
| `0xb8` | `0x939d8` getUpLink |
| `0xc0` | `0x939dc` findUpperLevelLink |

So the API **is resolved and wired**. `libna_onecore` has five wrapper sites
that forward `object[0xa8]`: `0x1516c90`, `0x1518978`, `0x151c214`, `0x1520704`,
`0x1520d08` (the getTransitions equivalents are `0x1516c78`, `0x151896c`,
`0x151b080`, `0x15206e8`, `0x1520cf0`, `0x152378c`, `0x1525954`).

Runtime: during a local route plus 25 emulator GPS updates, `getTransitions`
was called more than 5000 times and `getContinuedTurnRestrictions` **zero**
times. The route engine consumes transitions only. No guidance path was
exercised, so the consumer that would settle `0x9a` vs `0x9b` is still unknown.

Evidence: `getctr-wiring-step2.json`. Next: start guidance
(`INMAGuidanceController.startGuidance`, or the UI start button) with `0x939d0`
and the five wrappers hooked, then disassemble the caller that fires.


### 6e. Real-car guidance observation (step 2 result)

Device `10.10.10.116:5555` (IHU629G), service `com.neusoft.service.sdk` pid 30735,
guidance active in the UI (1.5 mi, 130 yd, ETA 08:09 PM, "Estrada local").

Libraries on the car are byte-identical to the analysis target:

* `libNdsLib.so` `c34a7e7a...`
* `libna_onecore.so` `0c4b84a1...`

Read-only Frida hooks for 6 s during active guidance:

| getter | calls |
|---|---:|
| `nds_rt_getTransitions` | 100 |
| `nds_rt_getContinuedTurnRestrictions` | **0** |

Conclusion: even with navigation running in the real vehicle, the engine only
uses `getTransitions`. `getContinuedTurnRestrictions` is exported and wired
(field `0xa8`, five wrapper sites) but has no exercised consumer in this
build/app. Therefore:

* the `0x9a`/`0x9b` payload semantics are not observable here and stay unproven;
* restrictions that the router applies must travel through the 24-byte
  transition masks proven in section 6c, not through `0x9a`/`0x9b`;
* the Forge writer should target the attribute-pattern path that produces keys
  `0x8..0xb` and the `u16` mask table, not `0x9a`/`0x9b`.

Not covered: eHorizon/ADAS calls outside guidance. The car was not modified;
the temporary frida-server binary was removed. Evidence:
`car-guidance-observation.json`.


## 6f. Mask attribute encoding (step 3, writer groundwork)

The attribute handler is `0x1140f0` (original vaddr; vtable slot `+0x10` of the
RoutingTile builder, vtable `0x4f75e8`). Signature:

```
(builder, attrId, valuePtr, reader, refType, &header, bitPosition, version)
```

Header construction: `refType 0` clears bits 13..14, `refType 5` sets
`0x6000`, otherwise bits 19..21 of the incoming `u64` become the direction.

Mask family (these append words to the `u16` table at `tile+0x148`):

| attr | words | refType |
|---|---:|---|
| `0x8` | 1 | 0 |
| `0x9` | 2 | 0 |
| `0xa` | 4 | 0 |
| `0xb` | 16 | 0 |

Dedup: if the words equal the current tail of the table, the record reuses that
index. Pattern record: `u16 (attr | dir<<13)`, `u16 count`, `u32 start index`.
The consumer (`0x20f9a4` + `0x1f8b0c`) reads `count`-bit masks per transition
entry.

Flex container (ROUTE header index 8): each group descriptor is 32 bytes
`{u32* begin, u32* end, u32* cap, u32 refType, u32 byteOffset}`; the attribute
ids are the `u32`s in `begin..end`. Parser `0x142c00`: version < 13 reads ids
inline, version 15 uses the descriptor list.

Trace of tile `540413880` (version 15):

| ids | refType | byte offset |
|---|---:|---:|
| 173 (`0xad`) | 0 | 834 |
| 88 (`0x58`) | 1 | 842 |
| 98 (`0x62`) | 2 | 932 |
| 227 (`0xe3`) | 2 | 942 |
| 255 (`0xff`) | 2 | 968 |
| 32, 56 | 2 | 998 |
| 8 | 0 | 1003 |
| 56, 32 | 5 | 1013 |

The attr-8 group at byte 1003 is `00 80 00 04 ff bd 00 05 ff df`; the handler
emits intersection indices 4 and 5 with mask words `0xffbd` and `0xffdf`, which
are exactly the native `u16` table `[65469, 65503]`.


Attribute-value readers are selected by a jump table at `0x4575e0`
(`int32` offsets relative to the table base):

| attr | reader |
|---:|---|
| `0x8` | `0x46664` (`read_u16`, store u16) |
| `0x9` | `0x47cf8` |
| `0xa` | `0x48018` |
| `0xb` | `0x4797c` |
| `0x58` | `0x466bc` |
| `0x62` | `0x47734` |
| `0xad` | `0x48574` |
| `0xff` | `0x46664` (same reader as `0x8`) |

Flex group header is `2-bit mode` + `var29 entry count`; version 15 takes the
attribute ids and `refType` from the 32-byte descriptor. Empirical mode-0
entry layout for the attr-8 group of tile `540413880`:

```
mode=0, count=2, five zero bits,
entry1: 1 flag bit, u16 header=4,    u16 value=0xffbd
entry2: 1 flag bit, u16 header=5,    u16 value=0xffdf
```

Handler positions (bits 8072 and 8104) match exactly. The code shows a second
skip call after the header read, but the bytes put the value immediately after
the header; that skip's exact semantics remain unresolved and must be settled
before a writer can be trusted.

Still unknown before a writer: the per-entry header codec in the flex group
(the first attr-8 entry occupies 6 bytes, the second 4), and the section-8
header/index layout. Evidence: `mask-attribute-encoding.json`.


### 6g. Section-8 writer validated by construction

`tools/build_flex_mask_fixture.py` rewrites the section-8 window of tile
`540413880` and the native reader accepts it. Final layout:

* descriptor table: `var29 count`, then per descriptor
  `u8 idCount, idCount x u16 attrId, 4-bit refType, 32-bit absolute group byte
  offset`; fields are bit-packed and the table is padded to the next byte;
* group payload at that offset: `2-bit mode (0)`, `var29 entry count`, six
  reserved bits, then per entry `u16 index, u16 mask` (there is no per-entry
  flag bit);
* attr 8 is a 16-bit mask (`0x46664` reads one u16); `0x1f8b0c` splits that word
  into `entryCount`-bit fields, one per transition entry.

Fixture `entries = [[4, 1], [5, 2]]`, generated section
`01 01 00 08 00 00 00 30 a0 00 80 00 04 00 01 00 05 00 02 ...`:

* native parse: tile loaded, 15 patterns, `u16` table `[1, 2]`;
* intersection ranges `4 -> [0,1)`, `5 -> [1,2)`, key-8 patterns at indices 0
  and 1;
* getter check: link 14 direction 0 (arriving at intersection 4) returns three
  transitions with masks `[1, 0, 0]`; direction 1 (intersection 6, unmasked)
  returns `[3, 3]`.

A previous attempt with a per-entry flag bit was rejected (see
`flex-writer-attempt-1.json`); the correction was the six reserved bits and no
per-entry flag. A descriptor-table round-trip against the eight original
descriptors of the tile is byte-exact.

Evidence: `flex-writer-validated.json`.


### 6h. Forge integration (validated natively)

`nds_forge/layers/route_codec.py` now exposes `encode_mask_section(offset,
entries)` and `encode_route_tile(..., intersection_masks=...)`:

* header flags become `0x183` (indices 1, 2, 8, 9) when masks are present;
* the mask section sits between the link section and the external-tile list;
* `tests/test_route_mask_section.py` (unittest, four cases) asserts the exact
  bytes accepted by the native reader and the header/offset layout.

A 74-byte tile produced by the Forge (`shift = 5`, the ISR level-13 shift) was
placed in the `540413880` slot of the plain database and parsed by the native
engine:

* links 2, intersections 3, one pattern, `u16` table `[0xFFFF]`;
* key-8 pattern `[index 0, word 8, count 1, table index 0]`;
* `getTransitions(link 0)`: direction 0 returns two entries with masks
  `[3, 3]`; direction 1 returns one entry with mask `[1]`.

Caveat for future fixtures: adding a *new* row to `routingTileTable` makes the
provider return an empty blob (`pair size 0`); the row must already exist with
its `versionId`, so tests replace an existing tile's `ndsData`.

Evidence: `forge-integration-validated.json`.


### 6i. OSM restrictions compiled into masks (Forge)

`route_graph._compile_tile_transitions` now returns the `CompiledTransitions`
object, and `_intersection_masks` projects each prohibited
`(from_directed_link, to_directed_link)` pair onto one bit:

* `entry` = position of the from link in `node_links[via]`;
* `target` = position of the to link in the same list;
* `bit = entry * count + target`; the word starts as all ones and the bit is
  cleared. `encode_route_tile(..., intersection_masks=...)` writes the section.

A `u16` (attribute 8) covers at most four entries per intersection; larger
degrees are audited and receive no mask yet (attributes 9/10/11 would be needed).
The transition matrix for `only_*` already forbids every other exit, so it maps
to the same single-bit clears.

Tests: `tests/test_route_intersection_masks.py` and
`tests/test_route_mask_section.py` (seven cases); `mypy` clean on both modules.
Evidence: `osm-mask-compilation.json`.

Next: run the full Curitiba build and inspect the emitted masks, then the
CAJURU route A/B (zeroed mask at a junction on the path).


### 6j. Search: street/city names never returned (session 2026-10-26)

Symptom (car, `Geely_EX2_CURITIBA_v10_full_masks` and `_v10_fix`): typing
`Rua Luiz`, `Rua` or `Curitiba` returns only POIs; no name entries. Same with
the Cajuru package on the AVD.

Confirmed data bug and fix: the five example configs used TOML *literal*
strings (`criterion_a = '\ue1651'`), so `nameFtsTable.criterionA..C` and
`nameFtsSubstitutionTable.substitutionId` contained the ASCII text
`\\ue1651` instead of U+E1651 + `1`. The OEM stores `EE 85 A5 31`. Fixed by
switching those lines to double quotes in `examples/*.toml`; the rebuilt
package now stores the PUA bytes and the substitution ids match the OEM shape.

The symptom persists after that fix, so the PUA bug was real but not the only
cause.

Compared against the OEM Israel FTS/NAME (bootstrap v1.0.0):
- FTS/NAME schemas are byte-identical; only row counts differ.
- `nameFtsAuxTable` is readable with the device `sqlite3` (ours 24,003 terms,
  OEM 137,218); Python's sqlite3 cannot read either (`malformed`).
- Road row shape differences were normalized in the patched packages
  (criterionD/J/searchTags NULL) with no effect.
- Rewriting every `mortonCode` to the positive 31-bit interleave had no effect.
- Dropping the OEM FTS bytes into our package makes the app report
  "Nenhum dado de mapa", so the engine validates FTS/NAME consistency.

SDK wiring discovered with Frida on the car (`com.neusoft.service.sdk`):
- the UI process does not load `libNdsLib.so`; the search runs in the SDK
  process;
- no `sqlite3_prepare*` calls are observable there (SQLite is statically
  linked inside `libNdsLib.so`);
- the public search surface is exported and usable from a probe:
  `nds_createFtsDbHandle`, `nds_fts_startSearchMatch`,
  `nds_fts_startCrossroadSearchMatch`, `nds_fts_GetMatchedRows`,
  `nds_fts_findNextMatch`, `nds_name_getMatchingListItems`,
  `nds_name_getNumberOfMatchingListItems`, `nds_sli_*`.

Next step: a native probe (AVD) that opens a NAME/FTS DB through
`nds_createFtsDbHandle` and calls `nds_fts_startSearchMatch`, run against the
built and OEM databases. This isolates the engine reader from the UI.


### 6k. Search engine trace (Frida, car `IHU629G`)

Attached to `com.neusoft.service.sdk` (the UI process never loads
`libNdsLib.so`) with frida-server 17.24.3 and hooked the exported surface.

- `nds_fts_startSearchMatch` **is** called for every keystroke; it returns 1 and
  the engine iterates matches with `nds_fts_findNextMatchResult`
  (e.g. 226 matches for `Rua`).
- The filled match struct starts with two u32 fields followed by an i64
  (negative for Curitiba) and two more i64 values. The first value matches the
  FTS rowid, not `namedObjectId`.
- During a search `nds_name_getNamedObject` is only ever called with
  `namedObjectId = 0` (hit and miss counts identical on the OEM map), so those
  calls are metadata lookups, not the result mapping.
- Area filter confirmed: with the **OEM Israel** package installed on the car
  at a Curitiba position, `Herzl` returns nothing; at an Israel position (AVD)
  it returns `Herzliya`, `Herzl Avenue` and house numbers. Name suggestions are
  therefore limited to the search area.
- Our package at Curitiba finds matches but still shows no names, so the
  remaining difference is in how the area/tile filter sees our rows.

Next experiment: hook `nds_fts_getTileIdsInSearchArea` while panning the map
(it is not called during typing) and compare the engine's tile list with
`(mortonCode >> 40) & 0xFFFFFF` for our NAME rows.


### 6l. Root cause of missing name results: ROUTE links carry no name attributes

Full Frida trace of every libNdsLib export during a search (625 exports hooked).
The suggestion pipeline is:

1. `nds_fts_startSearchMatch` / `nds_fts_findNextMatchResult` — matches are
   found (226 for `Rua`, 414 iterator calls for `Rua Luiz`).
2. `nds_rt_findNearestLink` — the engine maps the match to a route link.
3. `nds_rt_getNamesOfLink`, `nds_rt_getAdminHierarchiesOfLink`,
   `nds_rt_getRoadNumbersOfLink` — all return **0** on our tiles (no names).
4. Name suggestions are therefore dropped; POIs do not use this path and keep
   appearing.

`build_link_attributes` (`nds_forge/layers/route_attributes.py`) emits only
length, road_raw, access_raw, speed and bearings; `route_codec` still raises
`NotImplementedError("Auxiliary non-inline road attributes")` for the flexible
attribute block that carries the link name. `nds_rt_getNamesOfLink` dispatches
through the tile handle vtable (indirect `blr`), so the attribute id has to be
recovered by parsing an OEM ROUTE tile or by dynamic tracing.

Experiments that did **not** change the symptom (all on the car): OEM row shape
(criterionD/J/searchTags NULL), positive 31-bit morton, swapped x/y morton,
`docid == namedObjectId`. Area filter is real (OEM package returns no names when
the position is outside Israel), but it is not the blocker for our tiles.


OEM ROUTE tiles use flags `0x3f3` with sections 0-9 and a section-8 descriptor
table (first byte `0x23`; ~35 attribute descriptors for names, admin hierarchy,
road numbers, postal codes). Our tiles use `0x181`/`0x183` with a single
section-8 descriptor (attribute 8, transition masks). The descriptor table
layout for the OEM multi-attribute form is not decoded yet; the reader dispatches
through the tile-handle vtable, so it must be recovered dynamically (Frida on
`nds_rt_getNamesOfLink` over an OEM tile) or by extending the route probe.


Dynamic detail for the next session (same lib SHA):
- `nds_rt_getNamesOfLink` (`0x939f4` -> `0xfa8c0`) takes the tile handle in x0
  and dispatches through `[handle+8]` -> vtable slot `0x100`. On the car the
  slot resolved to runtime `0x729b9ff7ac` with module base `0x729b8c0000`,
  i.e. static offset `0x13f7ac`.
- The slot is called as `(obj, 0, link_hi, ...)` and returns 0 on our tiles.
- Parsing the OEM section-8 descriptor table with the mask-section layout
  (var29 count, 8-bit field, 16-bit id, 4-bit type, 32-bit offset) yields
  implausible offsets for every tested tile (count 6 and 35). The multi-attribute
  section-8 form therefore differs from the single-descriptor mask form and has
  to be decoded from the reader itself.
- Reproducing the OEM success on the AVD after map switches failed: the earlier
  working run had the OEM map already showing Israel. Fresh-boot the AVD with
  the OEM package before tracing `nds_rt_getNamesOfLink`, otherwise the search
  area yields zero matches and the function is never called.

Next: recover the flexible-attribute entry for link names (namedObjectId list)
from an OEM ROUTE tile or by calling `nds_rt_getNamesOfLink` on an OEM tile, add
it to the link encoder, and re-check the car search.


### 6m. AVD search is online-only; OEM-link oracle unavailable

- On the AVD (OEM Israel package) `Herzl` and `Dizengoff` return full results,
  including `Dizengoff Street` and `Herzl Avenue`, with **zero** calls to any
  `nds_fts_*`, `nds_name_*`, `nds_sli_*` export and zero `sqlite3_prepare*`
  calls in either process.
- Blocking TCP 80/443 with iptables zeroes those results, so the AVD search is
  served by the online service, not the local FTS. The earlier AVD success was
  therefore not a local-search reference.
- On the car (OEM package) the camera was moved to Tel Aviv with the thrift
  `setPosition`; `Herzl` still returns nothing, so the local search area follows
  the GPS position, not the camera. The car is in Brazil, so an in-area OEM
  sample cannot be exercised there either.
- Blocking uid 10233 did not affect the AVD results (quote: 0 packets rejected),
  and the online path runs under another identity.

Consequence: the successful `nds_rt_getNamesOfLink` reference read cannot be
produced with the available hardware. The link-name attribute format has to be
recovered statically (reader at static offset `0x13f7ac`) or with a synthetic
harness, not by differential tracing.


### 6n. ROUTE link-name reader decoded (static)

`nds_rt_getNamesOfLink` calls vtable slot `0x100` of the tile reader object
(`[handle+8]`). The static body at `0x13f824` (entry `0x13f7ac` includes the
exception table) does:

1. `[obj+0x330]` compared with `0xb`; below 11 it returns 0 immediately.
   Our tiles carry a single attribute descriptor; the OEM tiles carry dozens.
2. `0x1e7110(obj+0x1a0, 1, id, 0)` looks the attribute up; the id passed by
   `getNamesOfLink` is **0** and the type is **1**.
3. The entries are iterated with `0x1e6f84`, which returns `0x64` while a value
   exists; each value is copied as a **std::string** (32-byte element, 0x20
   stride) into the output vector via `0x1f9440`/`strlen`/`memcpy`.

So link names are literal strings in the tile attribute map, not NAME ids.

Section-0 and section-8 bytes of the OEM tiles were dumped. The section-8
descriptor table does not parse with the mask-section layout (var29 count,
8-bit field, 16-bit id, 4-bit type, 32-bit offset) nor with six alternative
width/prefix layouts; the attribute map is therefore built by a different
parser that still has to be located. Header sizing was confirmed:
`header_bytes = 2 + 4 * popcount(flags & 0x3ff)` (OEM `0x3f3` -> 34, ours
`0x183` -> 18).

Next: locate the attribute-map constructor (it reads the section-8 blob and
builds the `obj+0x1a0` map) and reproduce the descriptor/group encoding for
attribute id 0 (string values per link).


### 6o. Root cause confirmed: empty `routingAuxTileTable.nameAttributeLayer`

With Ghidra configured (`/opt/homebrew/Cellar/ghidra/12.1.2/libexec`, native
`os/mac_arm_64/decompile` present) the decompiler confirms the reader chain:

- `nds_rt_getNamesOfLink` -> tile reader -> `FUN_0023f824(reader, attributeId,
  vector)`:
  - returns false when `[reader+0x330] < 0xb`;
  - looks up attribute **id 0, type 1**;
  - iterates values as SQLite rows (`SQLITE_ROW` 100 / `SQLITE_DONE` 101) and
    pushes `std::string` values.
- The value layer is loaded from `routingAuxTileTable.nameAttributeLayer`
  (`SELECT nameAttributeLayer FROM routingAuxTileTable WHERE id = ?1`) by
  `FUN_0020b4e0`, which logs `NameAttributeLayer not found: %x (UR %u)` when the
  layer is absent, then returns 0.

Data comparison (unpacked ROUTE.NDS):
- OEM bootstrap: **3879 of 4224** aux tiles have `nameAttributeLayer`
  (samples 268-304 bytes, e.g. `03 80 80 4A 10 00 00 01 B0 08 04 A9 ...`).
- Our Curitiba v10 build: **0 of 103**; only `ndsData` (geometry) is written.

That is why the search finds FTS matches but every name suggestion is dropped.

Ghidra address note: the program is loaded with a +0x100000 image offset
(file 0x13f824 -> Ghidra 0x23f824; `address_to_file_offset` confirms).

Next: decode the `nameAttributeLayer` blob (class RTTI
`N2na5funit3nds18NameAttributeLayer11AttrCollectE` at 0x55ad50, provider vtable
slot 0x200, attach through `FUN_00142928`), then encode it in
`route_aux.py`/`route_codec.py` (attribute id 0, string references per link).


Layer internals decoded with Ghidra (see `name-layer-builder.md` next to this
file): `FUN_001ed314` collects the refs and lays out the in-memory layer
(default/links/lines counts at 0x12/0x20/0x30/0x40, 0xFFFF ref cap, ref kind in
bits 20-22); the loader `FUN_0020b4e0` fetches the blob through the provider
vtable slot 0x200 and parses it via `FUN_00142928` -> collector vtable[0].
Remaining unknown: that collector parser (the blob is not the 0x80-byte
in-memory struct; samples are 31-613 bytes).


Flexible-attribute interpreter: `FUN_00146104(attrId, bitReader, ctx, out)`
(file 0x146104 -> Ghidra 0x246104). Ids < 0x45c dispatch through a 1116-entry
jump table at 0x5575e0; ids >= 0x4000 use the switch: 0x4000 (bit pairs),
0x4002 (value + optional extra), 0x4003 (byte + bit offset + skip), 0x4005
(3-bit + two u16), 0x4007 (value + bit flags), 0x4013 (conditional skips),
0x4001/0x4006 no-op. This is the value codec the name layer entries use; the
container framing of the `nameAttributeLayer` blob is still the open piece.


Collector callback decoded: `FUN_001ec76c(refType, value, bitReader, index, key, entry,
... )` handles refTypes 0x90-0x98 (name refs), 0x99/0xD1 (markers), 0xB6/0xBA
(house-number data), storing bit offsets into the builder structures
(`FUN_001ed314`). AttrCollect vtable at 0x5f72c8: slot0 `0x1edbb4` destructor,
slot1 `0x1ee7e0` deleting destructor, slot2 `0x1ec76c` collector.

The blob container loop that feeds this collector is still unidentified; the
loader's `collector->vtable[0]` call resolves to a provider-returned object, not
the AttrCollect vtable.


Flexible-attribute container identified: `FUN_001388d4` (asserts
`/home/buildbot/src/funit/nds/src/bmd/BmdTile.cpp` and `RangeList::parse`). It
reads a 7-bit section bitmask, per-section bit offsets and counts, then the
packed entries; the same bit-packed container family is what the name layer
uses. Implementing the name layer encoder therefore means emitting this
container with attribute id 0 and per-link name refs (refTypes 0x90-0x98).


Layer framing decoded (Ghidra): `FUN_00149c5c` = AttributeMapList parser.
`FUN_00149f2c` reads the header: map count (u8 for version<7, u16 >=7); per map
an id list (u8 count, u16 ids; u32 ids for version>=0xD), a type field and a
BIT offset to the map container; `FUN_002d8b7c` seeks to it and
`FUN_00142c00` parses the container (version<7 legacy layout or >=7 kind 0/1/2).
The name map is id 0, type 1; entries carry refs (0x90-0x98) to name records.

Open: exact bit-level field widths/order must be verified against the OEM
samples by reimplementing `FUN_00149f2c` + `FUN_00142c00` in Python and
round-tripping the collected blobs before writing the encoder in route_aux.


Correction after reading the loader: the layer is parsed by the **collector
object's vtable[0]** (`(**puVar2)(puVar2, &bitReader, version, 0)` in
`FUN_00142928`), where the collector comes from `provider->vtable[0x60]`.
`FUN_00149c5c` (AttributeMapList) is a different consumer. The AttrCollect
vtable at 0x5f72c8 has slots 0x1edbb4 (dtor), 0x1ee7e0 (deleting dtor),
0x1ec76c (collector), so the layer parser class is another vtable reached via
the provider.


Layer reader factory decoded: `FUN_002dfbc8(blobStruct, &reader)` builds a
13-byte descriptor {u8 type=1; u64 data pointer; u32 byte length}. The blob
therefore starts at bit 0 with no version prefix; the parser receives this
descriptor. `FUN_00142928` calls `collector->vtable[0](collector, reader, out, 0)`
where the collector comes from `provider->vtable[0x60]`. The map-list layout
described earlier is NOT reached from this path, so the layer parser class is
still the one missing piece; it must be captured dynamically (hook
`FUN_00142928` while a layer loads) or found by locating the vtable whose slot
0x60 returns that class.

## 7. OSM correlation (partial)

Junction at approximately `30.3006 N, 35.1335 E` (Route 13 / Route 90 area,
Negev, Israel), tile `540421470`, 18 intersections, 20 links.

* Native link 15 connects intersections 12 and 13; those match OSM nodes
  `2027827132` and `6240051470` on way `628029638`.
* Native link 15, direction bit 1, has a `0x9b` record whose payload decodes to
  targets `14/1`, `17/0`, `18/1` (three links forming the local triangle
  13-11-15).
* Overpass returns 11 restriction relations within 1.5 km, including
  `9264757 only_straight_on` from way `628029638` via node `2027827132` to way
  `628029610`, and three `no_u_turn` relations at the same via node.
* `nds_rt_getTransitions(link 15, dir bit 1, flag 1)` enumerates the transitions
  at intersection 12: entries `15/1` and `13/0` with masks `[3,3]`.

What this proves: the from-link and the OSM to-maneuver are identifiable in
native data, and the `0x9b` payload is a *list of links around the junction*,
not the OSM to-way. What it does not prove: that the list encodes this OSM
prohibition, that the transition masks forbid it, or that the router applies it.

Details and machine-readable evidence:
`native/route-transition-research-20261025/osm-correlation-540421470.json`.

## 8. Standalone probe and its blocker

`tools/nds_transition_probe.c` (built for `aarch64-linux-android28`) opens the
database, creates the routing handle and calls both getters, dumping the parsed
tile when the load succeeds.

Observed in a standalone process on the AVD:

* `nds_open(path, NULL, NULL, NULL)` returns null, even with the NDK path as the
  third argument and the complete DECO product set.
* `nds_initialize(NULL)` blocks forever at `pc 0x20a25c` (return address
  `0x213560`, the VFS registration call `0x209fc8`), inside a list walk. The
  stack was captured with `debuggerd -b`. Do not repeat this call blindly; the
  VFS/zipvfs registration needs the SDK process context.
* Therefore the working path for this session was to call the same exported
  getters inside the already-initialized SDK process through Frida.

## 9. REA configuration (repaired)

`~/.omp/agent/mcp.json` had no Ghidra provider configuration. Current entry:

```json
"env": {
  "REA_JADX_MCP_JAR": ".../jadx-headless-mcp-0.7.1-all.jar",
  "GHIDRA_INSTALL_DIR": "/opt/homebrew/Cellar/ghidra/12.1.2/libexec"
}
```

Backup: `~/.omp/agent/mcp.json.bak-20261025`.
`npx -y rea-agents@6.7.0 doctor --client omp --json` reports
`ghidra: healthy /opt/homebrew/Cellar/ghidra/12.1.2/libexec`.
The running MCP server keeps its old environment until the client reconnects;
until then use the standalone `analyzeHeadless` calls recorded below.

## 10. Reproduction commands

Disassembly/decompilation (Ghidra 12.1.2, image base adds `0x100000`):

```sh
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless \
  /tmp/ghproj nds -import .../native/libNdsLib.so \
  -scriptPath /tmp/ghidra_scripts -postScript Dump2.java 0x20e6a0 0x212804
```

Saved decompilation logs: `ghidra-*.log` in this directory.

Native verification (AVD):

```sh
adb push tools/nds_transition_probe /data/local/tmp/isle/
adb shell 'cd /data/local/tmp/isle && LD_LIBRARY_PATH=/data/local/tmp:/data/local/tmp/isle \
  timeout 45 ./nds_transition_probe /data/local/tmp/isle/db/ROOT.NDS --init 0 540413880 0 --dump'
```

Frida verification (a Python environment with `frida` 17.24.3):

```sh
adb shell 'nohup /data/local/tmp/route-frida-server -D >/data/local/tmp/frida.log 2>&1 &'
python3 native/route-transition-research-20261025/isle_run.py \
  <service-pid> /data/local/tmp/isle/db/ROOT.NDS 540421470 --output /tmp/out.json
```

Static decode and differential check:

```sh
python3 tools/route_transitions.py \
  decoded/live-test-source/Geely_E245_ISR/db/NAV/UR439/ROUTE.NDS 540421470 --model 11
python3 - <<'PY'
import json, sqlite3, sys
sys.path.insert(0, 'tools')
from route_transitions import verify_native_dump
dump = json.load(open('/tmp/out.json'))['hits'][0]
db = sqlite3.connect('decoded/live-test-source/Geely_E245_ISR/db/NAV/UR439/ROUTE.NDS')
blob = db.execute('select ndsData from routingTileTable where id=?', (dump['tile'],)).fetchone()[0]
print(verify_native_dump(blob, dump['tile'], dump))
PY
```

## 11. Next experiments, in order

1. Prove the `0x9a` vs `0x9b` semantics by a route differential: build two
   fixtures identical except one restriction (one with a `0x9a` record, one with
   `0x9b`), calculate natively, and compare the chosen path. Until then no
   encoder.
2. Determine the direction convention of pattern bits 13..14 relative to the
   link's `from/to` ordering (use a one-way link with a known restriction).
3. Identify the consumer of the transition masks. `getTransitions` entries carry
   `u16` masks from pattern keys `0x8..0xb`; hook the `libna_onecore` route code
   that reads them to establish allowed/forbidden polarity.
4. Check `getTransitionAttributes` and `getAttributeTimeConditions` against a
   conditional restriction, which is the only route to OSM `restriction:conditional`.
5. Only after 1 and 3: writer/decoder in `nds_forge/layers/route_codec.py` with
   byte-exact round-trip and the native getter as the differential oracle.

## 12. Tool and evidence inventory

* `tools/route_transitions.py` — proven section 3/5 and `0x9a/0x9b` payload
  decoder plus `verify_native_dump`.
* `tools/nds_transition_probe.c` — standalone probe (builds with NDK 26.1).
* `isle_hunt.js`, `isle_one.js`, `isle_run.py` — Frida experiment in this
  directory.
* `hunt-300tiles-native.json` — 300-tile native scan (16 hits, 109 records).
* `static-verification.json` — 109/109 target sets reproduced.
* `osm-correlation-540421470.json` — OSM junction, ways, relations, limits.
* `ghidra-*.log` — decompiled parser, readers, attribute collection, getters,
  blob getter, level enumeration, sqlite open.

## 13. Operational state

No car installation, no car map change, no application data change. On the
isolated AVD the experiment used `/data/local/tmp/isle` (probe, library and a
copy of the retained ISR database), the pre-existing `route-frida-server`, and
one local route calculation to start the SDK. The database copy and probe are
removed at the end of the session; the app is force-stopped. `GHIDRA_INSTALL_DIR`
was added to the local REA configuration only.