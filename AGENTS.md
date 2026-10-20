# Contributor instructions

- Read `README.md`, `docs/stages.md`, `docs/adapter-contract.md`, and `docs/validation.md` before changing pipeline stages.
- Keep implementation, execution, and validation status separate. Never claim a Neusoft capability that a stage does not actually produce.
- Do not add proprietary maps, PBF extracts, APKs, native libraries, vehicle logs containing personal data, credentials, or keys to this repository.
- OSM-derived artifacts must record source hash, attribution, license, region, tool/config versions, and output hashes.
- Default work is offline. Network acquisition, native probes, and vehicle validation require their explicit per-task gates and must not run from ordinary pipeline execution.

## Agent skills

### Issue tracker

GitHub Issues in `Timoteohss/neusoft-nds-forge`; use `gh` from this checkout. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five configured GitHub triage labels listed in `docs/agents/triage-labels.md`; new issues enter `needs-triage`.

### Domain docs

Single-context repository. Read the relevant stage and adapter docs; no `CONTEXT.md` or ADRs currently exist. See `docs/agents/domain.md`.
