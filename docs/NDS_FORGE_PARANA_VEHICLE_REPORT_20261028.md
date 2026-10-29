# NDS Forge — Paraná package and vehicle validation report (2026-10-28/29)

## Scope and status

| Dimension | Result |
|---|---|
| Implementation | Completed for the host-side pipeline in this checkout. |
| Execution | `container.pack` completed for `neusoft-brazil-parana`. |
| Offline validation | Passed (`generated_offline`), with the documented declaration-only warning. |
| Native/SDK validation | **Not executed**: vehicle `10.10.10.116:5555` was unreachable. |
| Vehicle deployment | **Blocked**: no ADB endpoint, no route probe, no `sdk.ini` change, no vehicle files written. |

This report does not claim Neusoft natively accepts the candidate or that a
route calculation succeeds in the vehicle. Historical probe results from prior
packages are not evidence for this build.

## Build invocation

```sh
cd /Users/timoteosousa/apps/nds-forge
set -a; . "$HOME/.config/nds-forge/keys.env"; set +a
.venv/bin/python -m nds_forge run \
  --config examples/neusoft-brazil-parana.toml \
  --target container.pack --allow-experimental --progress
```

- Host: macOS 25.5.0 arm64, Python 3.14.2 in `.venv`.
- Wall time: `4264.92 s` (~71 min).
- Peak RSS: `2,735,177,728` bytes (~2.55 GiB).
- All 15 stages completed. Overall validation remains `unvalidated`.

### Inputs

| Input | Value |
|---|---|
| PBF | `examples/work/neusoft-curitiba-v9/inputs/curitiba-v9-inputs/parana-latest.osm.pbf` |
| PBF SHA-256 | `cc2f1f5aa4b46378969fce2274a14bb4c3f2e2f8bbf63f280086fc231580d885` |
| Bootstrap | `examples/work/neusoft-curitiba-v9/inputs/israel-original-base-v1.0.0` (`neusoft-curitiba-v9`, 21 files) |
| Region | bbox `[-54.62, -26.72, -48.02, -22.51]`, no municipal polygon |
| Keys | six `env:NDS_KEY_*` values, resolved only at runtime; never written to artifacts |

## Output identity

| Field | Value |
|---|---|
| Profile | `neusoft-brazil-parana` |
| Product group | `9000` |
| NAV / DECO product | `90000011` / `90000012` |
| Update-region set / region | `9100` / `9102` |
| Update-region directory | `UR9102` |
| Area / version | `AC` / `N.J6.AC.26.00.02.00` |
| Country / time zone | `BRA` / `America/Sao_Paulo` (`-12` quarter-hours) |
| Package label | `Brazil / Paraná OSM` |

The package contains exactly the 21 paths derived from the binding, including
`db/DECO/UR0/*` world-map assets (not remapped to `UR9102`) and
`db/NAV/UR9102/*`.

## Package artifacts

Package directory:
`examples/work/neusoft-brazil-parana/artifacts/package-final/`

| Path | Bytes | SHA-256 |
|---|---:|---|
| `artifact-manifest.json` | — | `c9e20ac61f32c38d54ec15b711321d2e0333621124dcd6edfaef64208c1abaed` |
| `db/Area.txt` | 2 | `472e73d796e20aa8ff9059e6316f218e0322548f661ec4dc267507ed66317404` |
| `db/NAV/PRODUCT.NDS` | 409600 | `b4c1ffada8cf75c7653321ab94e4445ecdf3b3b5665015e10f3e1fb647f53661` |
| `db/ROOT.NDS` | 24576 | `7513b4bfea0418087a03895b98d93659717266f25d3e72728f8c696e1f986f6e` |
| `db/NAV/UR9102/BMD.NDS` | 22856008 | `e209767af22ed83e698f257ef304336c1e340d1d35e021a963ca4722b7f26f18` |
| `db/NAV/UR9102/NAME.NDS` | 1131367 | `5a50641a3a8ff9f4dfc20f6994dac603b85f78c3e5abae0fa931493d5403d522` |
| `db/NAV/UR9102/FTS.NDS` | 14068801 | `bb8baea196661c77e80c3ff60fa4905df4d20d97bf16b1bf70a06e683c308379` |
| `db/NAV/UR9102/POI.NDS` | 1848419 | `cfd394f7b8b6c1dba5359194254ee08a79bd070f43a4e573032ecdca2cfe3a39` |
| `db/NAV/UR9102/ROUTE.NDS` | 32286575 | `2194f264ca532b911c7940548cddae79e4f61bcf3ae9a9df0788cb5ab37b7c8e` |
| `db/NAV/UR9102/SHARED.NDS` | 6656 | `1e2e67d85580d9c3ff0384aebdc417881a53cef4ae296a75ef2123214c9d6368` |
| `db/metadata.json` | 6779 | `8a5c385aedf7785c25cddb2d411b7b090d03b22f7fc153cf76dbdbb0c14db456` |

Total declared package bytes: `151,806,547`.

`package-layout.json`:
`examples/work/neusoft-brazil-parana/artifacts/package/package-layout.json`
declares `profile_id=neusoft-brazil-parana`, 21 required paths, 21 file
records, and `validation_status=unvalidated`.

## Verification executed

- `artifact-manifest.json`: 21/21 declared sizes and SHA-256 values match.
- ZV round-trip unpack of all six generated databases: `PRAGMA integrity_check`
  returned `ok` for BMD, NAME, FTS, POI, ROUTE and SHARED.
- `PRODUCT.NDS`: `versionTable.versionName = N.J6.AC.26.00.02.00`;
  `updateRegionTable = (9102, isDirty=0, 2026-01-01, 2026, 1)`;
  `updateRegionSetTable = (9100, 9102)`;
  `updateRegionNameTable = (9102, 1, 'Paraná')`;
  `urBuildingBlockVersionTable` URIs use `UR9102/`.
- `SHARED.NDS`: time zone `6`, `utcOffset=-12`,
  `timeZoneNameId=0 -> 'Brasilia Time'`, `1 -> 'BRT'`,
  `America/Sao_Paulo`.
- `Area.txt` = `AC` (2 bytes, with newline).
- Layer counts: BMD 2518 tiles; ROUTE 35216 tiles and 35216 AUX rows;
  NAME 66840 named objects; POI 8955 rows; FTS 9716 house-number ranges and
  329715 bindings.
- `perform_offline_validation`: `is_valid=True`, tier `generated_offline`,
  errors `[]`; warning: provenance declarations only, source authenticity and
  redistribution rights are not verified.
- Python suite: `298 tests`, `OK (skipped=1)`.

No native parser, SDK callback, UI, guidance or vehicle check was performed.

## Defects fixed to complete this build

| File | Defect | Fix |
|---|---|---|
| `nds_forge/layers/route_graph.py` | Route builder rejected the Brazil profile name although its policy is the reviewed v9 level-13/shift-4 policy. | Accept `neusoft-curitiba-v9` and `neusoft-brazil-parana`; level/shift guard remains. |
| `nds_forge/layers/route_aux.py` | AUX builder rejected the Brazil profile name. | Same explicit identity allowlist. |
| `nds_forge/layers/route_attribute_compiler.py` | Two corrupt OSM house numbers (`1535571106`, `1535571118`) exceeded native var29 and aborted a tile. | Skip ranges outside `0..2^29-1`; regression test added. |
| `nds_forge/pipeline.py` | `bootstrap.verify` used `neusoft.profile_id` instead of the declared `bootstrap_profile_id`, failing the bootstrap bundle check. | Use `bootstrap_profile_id` with fallback; smoke-verified against the Israel bundle. |
| `nds_forge/layers/package_assemble.py` | Time-zone rows used `nameId/nameString`, but the seed schema uses `timeZoneNameId/timeZoneNameString`; `updateRegionTable` omitted `isDirty` and used wrong release-date column names; FTS provenance lacked full OSM attribution. | Write both schema variants; add `isDirty`, `mapDataReleaseDate`, `releaseYear`, `releaseMonth`; add `© OpenStreetMap contributors`. Tests updated. |
| `nds_forge/region_binding.py` | `DECO/UR0/*` world-map paths and metadata entries were remapped to `UR9102`. | Preserve `db/DECO/UR0/` and `DECO/UR0/` paths. |

## Vehicle validation: blocked

Target: `10.10.10.116:5555`, authorized test vehicle (IHU629G, app
`com.neusoft.na.navigation`, SDK service `com.neusoft.service.sdk`, both uid
`10035`/`u0_a35`).

The device was reachable earlier in the session and had the prior
`map-test-parana-statewide` package. It became unreachable before deployment:

- `adb connect 10.10.10.116:5555` timed out repeatedly.
- `ping 10.10.10.116` returned 100% loss / `Host is down`.
- `nc -vz 10.10.10.116 5555` failed (`Host is down`).
- A `/24` scan for TCP 5555 found no alternate ADB endpoint.
- The host remains on `10.10.10.217/24`; no ARP entry exists for the vehicle.
- The coordinator was notified by escalation and asked for power/network
  recovery or an alternate authorized endpoint; no reply was received.

Because the vehicle was unreachable, **no** of the following were done:

- No candidate directory was created under
  `/data/user/0/com.neusoft.na.navigation/files/map-test-nds-forge-parana/`.
- No file permissions/chown/SELinux context were changed.
- `sdk.ini` was not edited and no backup was created for this attempt.
- Navigation/SDK processes were not restarted.
- No route probe was sent; result code `7/0` was not observed for this
  candidate.

The vehicle was not modified in any way.

## Remaining work to close acceptance

1. Power/wake the vehicle and confirm `adb -s 10.10.10.116:5555 shell true`.
2. Create
   `/data/user/0/com.neusoft.na.navigation/files/map-test-nds-forge-parana/Geely_EX2_BR_PARANA_r1/db`
   and copy `artifacts/package-final/db/.` into it; set owner `u0_a35:u0_a35`
   and mode `u=rwX,go=`.
3. Back up
   `/data/user/0/com.neusoft.na.navigation/files/install/etc/sdk.ini`, then set
   `path_mapdata` to the new directory.
4. Force-stop `com.neusoft.na.navigation` and `com.neusoft.service.sdk`, restart
   the navigation activity, and confirm the SDK loads the package.
5. Run the authorized route probe on the SDK Thrift port range `40600-40614`
   with the profile library SHA-256
   `c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55` and
   start/end points in Curitiba (e.g. `-25.429827,-49.2725303` to
   `-25.4280546,-49.2712694`, option 3). Acceptance is
   `onRouteResult.status=7`, `calculator=0`, non-empty alternatives and matching
   request echo.
6. If any step fails, restore the saved `sdk.ini`, restart the app and remove
   only the new candidate directory.

## Documentation

`README.md` now contains a step-by-step guide for any Brazilian state or
municipality: OSM PBF and IBGE boundary, TOML identity/time zone, six container
keys, generation, and inspection/deployment. `README.pt-BR.md` mirrors the
five-step quick guide. Both state the experimental/unvalidated limits and OSM
attribution/license.

## Limitations

- Generation and offline byte integrity do not prove native parser, SDK, UI or
  guidance acceptance.
- The ROUTE graph uses level 13/shift 4; native turn restrictions, via-way and
  conditional restrictions, guidance and legal speeds are incomplete.
- The region is a bbox, not the IBGE state polygon; border spill is possible.
- POI coverage is limited to the configured 18 native categories.
- Inherited TMC/EXTTMC and other static bootstrap assets are copied unchanged
  and do not establish Brazilian traffic coverage.
