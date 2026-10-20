# Inputs and external artifact availability

The public repository contains the NDS Forge code, contracts, synthetic
fixtures, and CI checks. It does not contain Neusoft maps, APKs, native
libraries, private vehicle logs, device credentials, or encryption keys.

The Curitiba v9 profile pins two public input releases. The Israel original
base is [`nds-israel-base-v1.0.0`](https://github.com/Timoteohss/neusoft-nds-forge/releases/tag/nds-israel-base-v1.0.0),
archive SHA-256 `0b564acdd834087753d8e1b7f17cdfa737015d9a0b073c4f6af8378b2019445b`.
The exact Curitiba v9 inputs are [`nds-curitiba-v9-inputs-v1.0.1`](https://github.com/Timoteohss/neusoft-nds-forge/releases/tag/nds-curitiba-v9-inputs-v1.0.1),
archive SHA-256 `4436e697b45335a7c41b91ac1ebc062c957f0e6c75630495101ab389cff6c877`.
The latter contains the exact Paraná PBF snapshot and relation 297514 GeoJSON
boundary, plus a ten-record compatibility POI input. OSM-derived data is
© OpenStreetMap contributors under ODbL 1.0; file-level provenance and hashes
are in each release manifest. These are inputs only, never the generated v9
package/oracle.

Fetch both archives with explicit network opt-in:

```sh
python -m nds_forge fetch-inputs --config examples/neusoft-curitiba-v9.toml --allow-network
```

The downloader requires HTTPS, checks the pinned archive SHA-256, rejects
unsafe tar members, stages each extraction atomically, then verifies every
manifest-listed member size and SHA-256. It reuses only an existing directory
with a matching archive marker and verified manifest. Ordinary `plan` and
`run` do not download these release bundles.

The Curitiba v9 profile's `neusoft.bootstrap_dir` points at the extracted
Israel base. Its `manifest.json` pins every required file by
logical name, relative path, expected output path, byte count, SHA-256, format,
role, source, and license. `bootstrap.verify` fails closed if an asset is
missing, altered, outside the declared directory, or has a mismatched format.
The generated outputs must come from the configured PBF/boundary and source
code; the final v9 package is not an allowed source for generated BMD, NAME,
FTS, POI, or ROUTE layers.

The public Israel bundle contains only the user-authorized original 21-file
database tree plus a sanitized manifest and README. Static files are explicitly
allowlisted; schema seeds are inputs to supported schema extraction only; the
metadata file is reference-only. No key material, APK, native library, device
log, GPS record, or private configuration is included. Rehosting the base does
not assert that its inherited Israel TMC/EXTTMC data represent Brazilian
traffic coverage.

Each generated ZV output has its own provider reference in
`neusoft.container_keys`. `file:<path>` reads an external raw 16-byte file;
`env:<name>` reads 32 hexadecimal characters from an environment variable.
The six matching repository Actions secrets are named `NDS_KEY_BMD`,
`NDS_KEY_NAME`, `NDS_KEY_FTS`, `NDS_KEY_POI`, `NDS_KEY_ROUTE`, and
`NDS_KEY_SHARED`. Secret values are never written to config, bootstrap
manifests, state, logs, or fingerprints. Container packaging is non-cacheable
and requires all six references at runtime. Never add key bytes or key files
to GitHub releases, Actions artifacts, or the repository.

The manual full-build workflow is `Full Curitiba v9 build`; it runs only on
`workflow_dispatch`, does not execute on pull requests or forks, fetches the
two SHA-pinned releases, then requires the six secrets. Its output is an
unvalidated build artifact until separate native/SDK/UI/device gates pass.

Device validation is not a GitHub-hosted workflow. It requires an authorized
operator and protected, isolated hardware environment with an exclusive lease,
immutable candidate hashes, and tested rollback. Never expose ADB endpoints,
vehicle identifiers, GPS coordinates, or credentials in public CI logs.
