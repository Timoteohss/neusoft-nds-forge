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

## Remaining validation gates before real data

1. Extend tests as more adapter semantics are implemented. The tiny synthetic
   clipping PBF proves only a few-way bbox/polygon clip and metadata retention;
   it does not cover a real region extract or map semantics beyond clipped road
   geometry.
2. Confirm the implementation computes fingerprints from all relevant source
   file contents and code inputs; changes must invalidate the changed stage and
   its descendants. A changed unrelated input may conservatively invalidate
   additional stages.
3. The missing `--allow-network` gate must be evaluated before creating the
   workspace or staging directory. The suite includes a regression test for
   this behavior.
4. Keep `validated` reserved for stage-specific checks. Generic file hashes or
   successful execution cannot imply Neusoft compatibility.
5. If a future adapter shells out to native tools, pass an argv array without a
   shell and add an adversarial path/argument regression test. The current
   registry invokes Python callables and has no subprocess adapter.
6. Once implemented, add isolated tests for package assembly, format profiles,
   provenance/licensing, and native/device validators. Real vehicle tests need a
   separate explicit authorization and a rollback plan.

Registry contract: a plugin may read upstream artifacts through declared
dependency directories (`StepContext.dependency_outputs`); their execution
digests, including output hashes, contribute to dependent fingerprints. Direct
external files must be declared by dotted config key in
`Step.external_inputs`; the engine resolves, exposes, and hashes those files or
directories. Undeclared paths hidden in plugin code are unsupported.
