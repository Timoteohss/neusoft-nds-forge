# Standalone NDS tools

These tools are distributed with the source pipeline so a fresh checkout can
inspect generated artifacts and, when explicitly requested, compare a pinned
Neusoft native reader. They never contain map-key values, a device serial, or a
private GPS coordinate. Native library binaries are not bundled; the profile
is documented in `native/lib-profile.json`.

## ZV-zlib SQLite containers

Install the optional container dependency and the project in the checkout:

```sh
python -m pip install -e '.[container]'
python tools/nds_container.py unpack INPUT.NDS OUTPUT.sqlite --key-file /secure/path/key.bin
python tools/nds_container.py pack INPUT.sqlite OUTPUT.NDS --key-file /secure/path/key.bin
```

`key.bin` is caller-supplied, exactly 16 raw bytes. The CLI reads it only into
memory and does not print it. Outputs are exclusive-create; existing files and
inputs are not overwritten. The package pipeline is preferred for building a
complete package because it checks the 21-path profile, provenance and
metadata hashes.

## Native probes

Sources in `native/`:

| Source | Scope | Notes |
|---|---|---|
| `nds_blob_probe.c` | BMD tile blob parser and point extraction | Direct parser probe for the pinned library profile; use an offline blob copy and a timeout. |
| `nds_native_probe.c` | Symbol-based BMD/database open smoke probe | Historical `nds_open()` path was unsuccessful; do not treat it as a validated database reader. |
| `nds_route_probe.c` | ROUTE header, links/intersections and basic attributes | Hardcoded ABI offsets; profile SHA must match exactly. |
| `nds_route_geo_probe.c` | AUX geometry rows and link-to-geometry lookup | Hardcoded ABI offsets; profile SHA must match exactly. |
| `read_nds_aes_contexts.c` | Read-only process-memory observer for candidate AES schedules | Emits candidate key bytes only to an operator-selected exclusive file with mode 0600. Treat output as secret; do not upload or log it. Requires authorized process access. |
| `read_nds_levels.c` | Read-only scan for scalar BMD level metadata | Process-memory observer; not needed when decoded SHARED.NDS is available. |

The route probes require an operator-provided `libNdsLib.so` with SHA-256
`c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55`, AArch64
Android/Linux, and the ABI profile in `native/lib-profile.json`. Compile on the
target-compatible toolchain, for example:

```sh
aarch64-linux-android-clang -O2 -Wall -Wextra -o nds_blob_probe native/nds_blob_probe.c -ldl
aarch64-linux-android-clang -O2 -Wall -Wextra -o nds_route_probe native/nds_route_probe.c -ldl
aarch64-linux-android-clang -O2 -Wall -Wextra -o nds_route_geo_probe native/nds_route_geo_probe.c -ldl
```

The probes accept caller-selected input paths/tiles and print JSON; run them
under a timeout on a copy of the blob. Do not use these offsets with another
library version. These are isolated offline parsers, not route-calculation or
driving validation.

The AES context tool is sensitive: its output is key material. Only run it on a
process you own and have authorized, pass an explicit PID and a new private
output path, protect the result, and delete it when no longer needed. The tool
does not print candidate key bytes, but the output file contains them.

## ADB / SDK validation

Device-side validation is intentionally separate from artifact generation.
`adb_route_probe.py` makes one `startRoutePlan` request through the SDK Thrift
service. It requires `--execute`, a caller-selected `--device`, `--port`,
`--start LAT LON`, `--end LAT LON`, `--option`, and a new `--output` path. It
waits for `onRouteOption`; without that readiness event, it sends no request.
The report omits coordinates and raw route geometry, retaining only method and
result summaries plus whether request parameters echoed back. The probe never
starts guidance, installs files, swaps maps, restarts the app, or changes
preferences. It creates and removes only an ADB TCP forward. Example (replace
all values with the current, authorized test setup):

```sh
python tools/adb_route_probe.py --execute --device SERIAL --port THRIFT_PORT \
  --start LAT LON --end LAT LON --option OPTION --output /secure/new-report.json
```

No device test is run by the pipeline or CI. This single-request probe is not a
rollback/route-package swap harness and does not establish SDK acceptance by
itself; do not infer device acceptance from offline native parser success.
