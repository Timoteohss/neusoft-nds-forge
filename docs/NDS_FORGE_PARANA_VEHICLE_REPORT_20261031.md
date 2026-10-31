# NDS Forge — Paraná package vehicle deployment and native route validation (2026-10-31)

## Scope and status

| Dimension | Result |
|---|---|
| Implementation | Existing `neusoft-brazil-parana` package used unchanged. |
| Package verification | 21/21 manifest paths match declared SHA-256; total 151,806,547 bytes. |
| Vehicle deployment | Deployed to `/data/user/0/com.neusoft.na.navigation/files/map-test-nds-forge-parana/`; 21/21 remote hashes match. |
| `sdk.ini` switch | `path_mapdata` changed to the new directory; prior file backed up. |
| Service restart | `com.neusoft.service.sdk` and `com.neusoft.na.navigation` restarted; SDK reloaded the package. |
| Native route validation | Executed: three local routes returned `onRouteResult.status=7`, `calculator=0`, with matching request echo. |
| UI route display | Executed: app-originated route request returned the same 7/0 result and the vehicle displayed the blue route with three alternatives. |
| Guidance | Not started. |
| Kernel/MCU/VHAL/CAN/firmware | Not modified. |

Attribution: map data displayed and routed by this package derives from
© OpenStreetMap contributors, ODbL. The route probe report intentionally omits
raw coordinates and geometry; coordinates here are public street locations in
Paraná used for acceptance only.

## Target

| Field | Value |
|---|---|
| Endpoint | `10.10.10.116:5555` |
| Device | `IHU629G`, Android 9 |
| App | `com.neusoft.na.navigation` / `com.neusoft.hmi.activity.MainActivity` |
| SDK process | `com.neusoft.service.sdk` (uid `u0_a35`) |
| Native library | `/system/app/NAVI/lib/arm64/libNdsLib.so` |
| Library SHA-256 | `c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55` |
| Route Thrift port | `40604` (ports `40600–40614` listened by the SDK after restart) |

The mission text listed `com.neusoft.na.navigation.MainActivity`; the installed
launcher activity is `com.neusoft.hmi.activity.MainActivity`, resolved from
`cmd package resolve-activity`. The latter was used.

## 1. Source package verification

Package:
`examples/work/neusoft-brazil-parana/artifacts/package-final/`

- `artifact-manifest.json` declares 21 files, 151,806,547 bytes.
- Recomputed SHA-256 for all 21 files: 21/21 match the manifest, 0 mismatches.
- Structure matches the Neusoft layout: `db/ROOT.NDS`, `db/ROOT.NDK`,
  `db/Area.txt`, `db/metadata.json`, `db/NAV/PRODUCT.NDS`,
  `db/NAV/UR9102/*`, `db/DECO/PRODUCT.NDS`, `db/DECO/UR0/*`.
- `db/Area.txt` = `AC` (2 bytes, with newline).
- `db/ROOT.NDS` SHA-256
  `7513b4bfea0418087a03895b98d93659717266f25d3e72728f8c696e1f986f6e`.
- `db/metadata.json` SHA-256
  `8a5c385aedf7785c25cddb2d411b7b090d03b22f7fc153cf76dbdbb0c14db456`;
  product `Brazil`, NAV product `90000011`, update region `UR9102`,
  version `N.J6.AC.26.00.02.00`.
- `db/NAV/UR9102/ROUTE.NDS` SHA-256
  `2194f264ca532b911c7940548cddae79e4f61bcf3ae9a9df0788cb5ab37b7c8e`.
- Package includes the marker-0 / omitted-straight-link encoding fix from
  `3f50e89 fix(route-aux): encode straight-link geometry fallbacks`; the
  manifest records `validation_status=unvalidated`, so container integrity is
  not SDK acceptance.

## 2. Deployment

Target directory:
`/data/user/0/com.neusoft.na.navigation/files/map-test-nds-forge-parana/db/`

Commands executed (root adb shell):

```sh
F=/data/user/0/com.neusoft.na.navigation/files
D=$F/map-test-nds-forge-parana
adb -s 10.10.10.116:5555 shell "mkdir -p $D/db"
adb -s 10.10.10.116:5555 push \
  examples/work/neusoft-brazil-parana/artifacts/package-final/db/. $D/db/
adb -s 10.10.10.116:5555 shell "chown -R u0_a35:u0_a35 $D"
adb -s 10.10.10.116:5555 shell "chmod -R u=rwX,go= $D"
adb -s 10.10.10.116:5555 shell \
  "chcon -R u:object_r:app_data_file:s0:c512,c768 $D"
```

Post-deployment verification:

- 21 files pushed, 0 skipped (151,806,547 bytes).
- 21/21 remote SHA-256 values match the manifest; 0 mismatches.
- Owner `u0_a35:u0_a35`; files `rw-------`, directories `rwx------`.
- SELinux context `u:object_r:app_data_file:s0:c512,c768` on directory and
  files, matching the previously active map directory.
- Free space before deployment: 17 GB available on `/data`.

## 3. `sdk.ini` switch

Backup created before editing:

```
/data/user/0/com.neusoft.na.navigation/files/install/etc/sdk.ini.before-nds-forge-parana-20261031
SHA-256 7a80fb41b6af0a60249cbc38805cad8b395b021c020fc62a303bb928b5d5c57a
```

Edit:

```sh
sed -i 's#^path_mapdata.*#path_mapdata = /data/user/0/com.neusoft.na.navigation/files/map-test-nds-forge-parana/#' \
  $F/install/etc/sdk.ini
```

Resulting line 28:

```
path_mapdata = /data/user/0/com.neusoft.na.navigation/files/map-test-nds-forge-parana/
```

`database = ${main.path_mapdata}db/ROOT.NDS` resolves to
`.../map-test-nds-forge-parana/db/ROOT.NDS`. Current `sdk.ini` SHA-256:
`c859d3a4254ff4b5056ce72757d673607c3d513c4baefca0d09c5fdaa8c5b415`.

## 4. Service restart

```sh
am force-stop com.neusoft.service.sdk
am force-stop com.neusoft.na.navigation
am start -W -n com.neusoft.na.navigation/com.neusoft.hmi.activity.MainActivity
```

- Activity start returned `Status: ok` (`TotalTime: 588 ms`).
- After the final restart: `com.neusoft.service.sdk` pid `29082`,
  `com.neusoft.na.navigation` pid `29042`.
- `netstat` on the device showed the SDK listening on the `40600–40614`
  range; route readiness was observed on `40604` with
  `onRouteOption` and `onRouteOptionCombination`.

## 5. Native route calculations

Tool: `tools/adb_route_probe.py --execute` (read-only, no guidance). The tool
was extended in this session to record each alternative's `distance_m` and
`time_s`; its success criteria are unchanged. Library hash pinning is enforced
before any request.

Readiness events on every run:
`onEnterSOCSettingUpdated`, `onEntrySOCSettingUpdated`,
`onArriveDestSOCSettingUpdated`, `onDestBatteryRangeSettingUpdated`,
`onAutoSRPRouteSettingUpdated`, `onRouteOption`, `onRouteOptionCombination`.

All runs used `--option 3`, `calculation=null`, `guidance_requested=false`.
Every run observed `request_echo_matches=true`.

| Case | Start → end (public coordinates) | Status / calculator | Alternatives (distance m / time s) |
|---|---|---|---|
| Curitiba intra | `-25.454848,-49.220383` → `-25.428863,-49.271209` | **7 / 0** | 7433.75 / 795; 7507.73 / 664; 7965.56 / 680 |
| Curitiba → Paranaguá | `-25.4284,-49.2733` → `-25.5163,-48.5225` | **7 / 0** | 90344.47 / 3777; 91088.36 / 4028; 91995.20 / 4316 |
| Curitiba → Ponta Grossa | `-25.4284,-49.2733` → `-25.0916,-50.1668` | **7 / 0** | 114796.12 / 4696; 120392.98 / 6322 |

Plausibility check against straight-line distance:

| Case | Straight-line km | Route km | Route / straight |
|---|---:|---:|---:|
| Curitiba intra | 5.86 | 7.43 | 1.27 |
| Curitiba → Paranaguá | 76.00 | 90.34 | 1.19 |
| Curitiba → Ponta Grossa | 97.34 | 114.80 | 1.18 |

The values are consistent with the road network (BR-277 to Paranaguá,
BR-376 to Ponta Grossa) and with the mission's ~89 km and ~115 km expectations.
All three cases returned multiple distinct alternatives, which requires route
shapes and not a straight-line placeholder.

## 6. UI route display on the vehicle

After the SDK probes, the app itself was used to request a route through the
normal search/history UI. Loopback traffic was captured with `tcpdump` on ports
`40600–40614` to prove the request path.

Captured app-originated exchange:

- `startRoutePlan` client → SDK on port 40604.
- `onRouteResult` SDK → client on port 40604:
  `status=7`, `calculator=0`,
  alternatives `19149.95 m / 1313 s`, `20731.48 m / 1309 s`,
  `17704.03 m / 1474 s`.
- The app displayed `Recomendação 22 min · 19,2 km`,
  `Alternativo 1 22 min · 20,7 km`, `Alternativo 2 25 min · 17,7 km`,
  matching the captured SDK result.
- The map rendered the blue route polyline with direction arrows, the
  current-position arrow, and the destination pin.

Screenshot evidence:

- `.../screens/02-ui-route-display.png` — route drawn on the vehicle display.
- `.../screens/03-fresh-map-render.png` — Curitiba street grid rendered after a
  clean restart.
- `.../screens/01-after-restart.png` — same map state immediately after restart.

A manual `showRoutes`/`highlightRoute` injection on port 40606 moved the camera
to a world view without a visible polyline; it was not accepted as visual
evidence. The app-originated route path above is the accepted route-display
evidence.

## 7. Evidence files

Directory:
`examples/work/neusoft-brazil-parana/artifacts/vehicle-validation-20261031/`

| File | SHA-256 |
|---|---|
| `route-curitiba-intra.json` | `a7c0dbb68a4bc33b8a58f3bc3895c97134ccf977cc38094b5037478194c301f4` |
| `route-curitiba-paranagua.json` | `b2285817c04b6cbea9af3fdd0e6d89fa4c8a958b7e4d2a562a8cf7bcd4681dd6` |
| `route-curitiba-ponta-grossa.json` | `ec4a9b33abe5712f9a0beac6fa14eb01175d0ea662c3cf0d9feae4d3be0caf62` |
| `ui-route-sdk-result.json` | `c1de75e009732e62db15e5b6ae96ad51b138a73610c42529d5776ffec52397ba` |
| `screens/01-after-restart.png` | `8d182a1e613b9230ac4cc31862853056fce00040d86258edcf79e56ab69c64d8` |
| `screens/02-ui-route-display.png` | `17610bde3a8cb8087d9930c6c74d850ba8bb50356b48d7fb5f5eccfa6fe69126` |
| `screens/03-fresh-map-render.png` | `cf0a2956ba79a9611067ad645482ec9052730e0e8322920abdb2c34dcd0bf198` |

The three `route-*.json` files retain only status, calculator, alternative
count, distances/times and request-echo flag; they deliberately omit raw
coordinates and geometry. `ui-route-sdk-result.json` is the sanitized projection
of the captured UI-originated `onRouteResult`.

## 8. Rollback

```sh
F=/data/user/0/com.neusoft.na.navigation/files
cp -a $F/install/etc/sdk.ini.before-nds-forge-parana-20261031 \
      $F/install/etc/sdk.ini
am force-stop com.neusoft.na.navigation
am force-stop com.neusoft.service.sdk
am start -W -n com.neusoft.na.navigation/com.neusoft.hmi.activity.MainActivity
rm -rf $F/map-test-nds-forge-parana
```

The prior `sdk.ini` pointed at
`/data/user/0/com.neusoft.na.navigation/files/map-test-parana-statewide/Geely_EX2_BR_PARANA_statewide_r1/`;
restoring the backup returns to that package.

## 9. Limitations

- This validates local route calculation and route display, not guidance,
  maneuvers, traffic, or legal speed accuracy. Guidance was not started.
- Only three origin/destination pairs were probed; coverage is not exhaustive.
- `onRouteResult.status=7` is the SDK's accepted-result value observed in this
  session; `calculator=0` is the local calculator. No cloud calculator was used.
- The package remains `unvalidated` in the manifest and depends on a bbox region,
  not the IBGE Paraná polygon; border spill and POI coverage limits from the
  2026-10-28 report still apply.
- The ROUTE layer uses level 13/shift 4; native turn restrictions, via-way and
  conditional restrictions remain incomplete.
- No APK, native library, kernel, MCU, VHAL, CAN or firmware was modified.
