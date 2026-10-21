# Pipeline validation contract

This suite checks the orchestration contract with tiny temporary fixtures. It
does not build or validate a real OSM extract, Neusoft database, vehicle package,
or SDK-compatible map. `synthetic-fixture.pbf` in the stdlib-only orchestration
tests is deliberately plain test text, not a valid PBF; that exercises hashing
and provenance only. With optional `pyosmium` and `shapely` installed, a separate
test generates a tiny valid PBF in a temporary directory at runtime and exercises
the experimental clipping adapter through the CLI and engine. No PBF is checked
into the repository.

Run from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

If the optional clipping dependencies are available, all clipping tests run;
otherwise they are explicitly skipped. The isolated optional-dependency smoke
can be run with `python -m unittest discover -s tests -p test_osm_extract.py -v`
inside an environment containing `pyosmium` and `shapely`.

The suite uses a registered, explicitly experimental **fake executor** for one
synthetic stage. It writes one JSON fixture only; it is not an OSM parser or a
Neusoft compiler. The tests cover these guarantees:

| Contract | Evidence in tests |
|---|---|
| Planning is a dry run | CLI and library `plan` report no side effects/network and create no workspace |
| Planned stages fail closed | `package.assemble` cannot publish a package or create partial upstream state |
| Execution and validation are separate | Synthetic `source.verify` remains `unvalidated`; explicit validation yields only `partial` |
| Resume checks changed inputs | Changing bbox configuration or source bytes at the same path changes fingerprints and rebuilds affected stages |
| Stage publication is atomic | Simulated directory-publish failure restores the prior complete output |
| Extension registry stays gated | A synthetic callable executor receives private staging/dependency paths, requires experimental opt-in, and cannot mark its result validated |
| Extension graph remains well-formed | Cycles and missing dependencies fail before workspace mutation; external executor code changes alter fingerprints |
| Plugin dependencies are fingerprinted | Changing the upstream source content changes its output digest and reruns a dependent plugin stage |
| Declared plugin files are fingerprinted | A declared config path is exposed in `StepContext`, and changing same-path bytes invalidates that stage and its descendants; missing paths fail before workspace mutation |
| Adapter failure is not published | A fake executor that raises preserves any old complete output/manifest, publishes no partial output, writes a separate failure record, makes `status` show `failed` + `unvalidated`, and blocks validation of the stale success |
| Experimental OSM clip is exercised | Optional valid synthetic PBF tests bbox and polygon clipping, tag preservation, unresolved-region rejection, and CLI opt-in; outputs remain unvalidated/partial |
| Basic path injection is rejected | Project IDs containing traversal syntax are invalid |
| Network requires opt-in | URL-backed acquisition without `--allow-network` fails before workspace mutation |

The suite intentionally does not claim real-region map correctness, schema or
topology validity, native parser acceptance, SDK behavior, or device validation.
Those require implemented stages and independent validators. No proprietary
APK/library/map/key, vehicle credential, or real extract is needed for these
tests.

CI installs the `test` extra, which includes `jsonschema`, and schema tests
assert that validation actually ran. `TargetProfile.validate_against_schema`,
`OSMDataset.validate_against_schema`, and `ProjectConfig.schema_validated`
return an explicit boolean: `True` means the corresponding JSON Schema was
checked; `False` means the schema file or optional validator was unavailable,
so callers must not treat the data as schema-validated. Validation failures
raise the relevant domain error. Project TOML loading also validates against
`schemas/project.schema.json` when the validator is installed.

## Curitiba v9 package inventory gate

`tests/fixtures/neusoft_curitiba_v9_package.json` records the 21 expected
package paths and their origin mechanism without embedding any package data.
The standalone checker in `tests/e2e/package_inventory.py` verifies a future
`container.pack` `artifact-manifest.json` against that inventory, the actual
file sizes and SHA-256 values, declared input digests, and source/license
provenance for copied external assets. It refuses a sidecar that claims
external validation. Run its synthetic contract tests with:

```sh
python3 -m unittest tests.test_package_inventory -v
```

Those tests write only temporary bytes labelled as synthetic data. A pass
means inventory and byte-hash declarations are internally consistent; it does
not establish that a generated layer is semantically equivalent to Curitiba,
that copied external assets are genuine, or that native parsers, the SDK, the
UI, or guidance accept the package. The v9 reference package remains an
out-of-repository semantic oracle and must not be used as a build input.

### Public input bundles

The profile pins two public, versioned release bundles, both excluding key
material:

- `nds-israel-base-v1.0.0` provides declared schema seeds and static package
  assets. Archive SHA-256:
  `0b564acdd834087753d8e1b7f17cdfa737015d9a0b073c4f6af8378b2019445b`.
- `nds-curitiba-v9-inputs-v1.0.1` provides the Curitiba OSM PBF, boundary and
  curated compatibility POI input. Archive SHA-256:
  `4436e697b45335a7c41b91ac1ebc062c957f0e6c75630495101ab389cff6c877`.

`nds-forge fetch-inputs --config examples/neusoft-curitiba-v9.toml
--allow-network` downloads to the config's declared work area and verifies the
archive SHA, safe member paths, manifest, and each listed file's size and
SHA-256 before publishing the directory. Network use is opt-in. The two release
archives were independently downloaded and verified against their archive and
manifest hashes and all listed member hashes; this does not validate any map
output. Curitiba source data is ODbL; retain the attribution recorded in its
release manifest. The private v9 package is not included in either bundle and
remains only a semantic comparison oracle.

`tests/test_bootstrap_contract.py` uses only small synthetic tar archives and
mocked URLs. It checks network opt-in, idempotent reuse, pinned profile values,
archive and member hash failures, path traversal and links, plus redaction of
invalid runtime key values. It does not perform a live release download or
exercise a full v9 build.

## Remaining validation gates before real data

1. The tiny synthetic clipping PBF proves only a few-way bbox/polygon clip and
   metadata retention; it does not cover a real region extract or map semantics
   beyond the tested road-geometry cases.
2. The pipeline tests cover source/config/code/dependency fingerprints and
   downstream invalidation. Real profile runs must still bind every produced
   output to the actual input and code hashes in the sidecar manifests.
3. Keep `validated` reserved for stage-specific checks. Generic file hashes or
   successful execution cannot imply Neusoft compatibility.
4. If a future adapter shells out to native tools, pass an argv array without a
   shell and add an adversarial path/argument regression test. The current
   registry invokes Python callables and has no subprocess adapter.
5. Add isolated real-input tests for package assembly, format profiles,
   provenance/licensing, and native/device validators as those stages land.
   Real vehicle tests use a separate human hardware gate, one exclusive device
   owner, candidate hashes, and a tested rollback plan.

Registry contract: a plugin may read upstream artifacts through declared
dependency directories (`StepContext.dependency_outputs`); their execution
digests, including output hashes, contribute to dependent fingerprints. Direct
external files must be declared by dotted config key in
`Step.external_inputs`; the engine resolves, exposes, and hashes those files or
directories. Undeclared paths hidden in plugin code are unsupported.

## Offline API and pending SDK integration

`perform_offline_validation` accepts the pipeline's `output_hashes` dictionary
and the actual container packer's `files[]` records. It verifies declared sizes
and SHA-256 hashes, rejects duplicate/unsafe paths and empty or missing manifests.
A successful check is evidence of offline byte integrity only. It cannot promote
the result to native, SDK, UI or vehicle tiers, including when the caller marks
the input non-synthetic.

Provenance assessment is reported separately in `details.provenance` with
`applicable`, `checked_items`, `errors` and `status`. Without supported metadata,
the status is `not_assessed` and the report warns instead of asserting provenance
was verified. Explicit OSM declarations require OpenStreetMap attribution and an
ODbL declaration; these may be top-level or in the OSM layer's `source` block.
For `container.pack`, every `files[]` record requires non-empty source/license
provenance. OSM file declarations also require attribution/ODbL; the assembler's
existing combined license/attribution text is accepted. Missing or malformed
declarations fail. Successful declaration checks use `declarations_checked` and
warn that source authenticity and redistribution rights have **not** been proven.
This checks declared metadata, not legal permission, upstream bytes or native
compatibility. Offline tier restrictions remain unchanged.

The SDK route contract currently returns `UNSUPPORTED_SDK_ADAPTER` for every
request. It produces no estimated route and no `SUCCESSFUL_LOCAL` result.
A real SDK adapter, candidate binding and runtime callbacks remain pending;
unit tests and historical UI evidence for another candidate cannot close those
gates. See [the SDK integration status](evidence-sdk-route-harness.md).

The offline regression tests use synthetic temporary files. The container tests
also send the manifest produced by `pack_package_tree` through the offline API;
they do not load a production map or access the vehicle.

`NativeParserRunner.probe_tile` reports runtime pin mismatches as
`NativeProbeResult(status=HASH_MISMATCH, route_pass=False)`, including the observed
runtime digest and a diagnostic message, before executing any runner. An absent
runtime remains `MISSING_RUNTIME`. Diagnostic stdout is preserved but not parsed
into object counts: `parsed_items_count=0` currently means **unknown**, including
for successful or empty stdout. An actual structured-output parser is pending.
Parser success never establishes route calculation, SDK or vehicle acceptance.

O [handoff da revisão atual](handoff-current.md) registra a limpeza da tentativa
Paraná, os fixes e as pendências reais antes de outro build/teste no carro.
