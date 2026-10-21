from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from nds_forge.config import load_config
from nds_forge.adapters.osm_extract import (
    OSM_CLIP_CAPABILITIES_MISSING,
    OSM_CLIP_CAPABILITIES_PRODUCED,
)
from nds_forge.pipeline import (
    PipelineError,
    STEPS,
    _preflight_inputs,
    _topological,
    _current_fingerprint,
    _commit_directory,
    plan,
    run,
    status,
    validate_step,
)
from nds_forge.registry import IMPLEMENTED, EXPERIMENTAL, Step, StepContext, register_step


class PipelineContractTests(unittest.TestCase):
    """Small local contract tests; fixture bytes are not a real OSM extract."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "project.toml"
        self.source_path = self.root / "synthetic-fixture.pbf"
        self.source_path.write_bytes(b"SYNTHETIC TEST FIXTURE; NOT AN OSM PBF\n")
        self.workspace = self.root / "work"
        self._write_config()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_config(self, bbox: str = "[-49.4, -25.6, -49.1, -25.3]") -> None:
        self.config_path.write_text(
            "\n".join(
                [
                    "schema_version = 1",
                    'project_id = "synthetic-smoke"',
                    'workspace = "./work"',
                    "",
                    "[region]",
                    'id = "fixture-region"',
                    'name = "synthetic test region"',
                    'source = "bbox"',
                    f"bbox = {bbox}",
                    "",
                    "[source]",
                    'kind = "pbf"',
                    'path = "./synthetic-fixture.pbf"',
                    'sha256 = ""',
                    'license = "ODbL-1.0"',
                    'attribution = "© OpenStreetMap contributors"',
                    "",
                    "[pipeline]",
                    'target = "source.verify"',
                    "enable_experimental = false",
                    "",
                    "[neusoft]",
                    'format_profile = "unset"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.config = load_config(self.config_path)

    def test_plan_is_side_effect_free_and_marks_experimental_package_opt_in(self) -> None:
        result = plan(self.config, "package.assemble")
        self.assertEqual(result["side_effects"], "none")
        self.assertEqual(result["network"], "not accessed")
        self.assertFalse(self.workspace.exists())
        steps = {item["id"]: item for item in result["steps"]}
        self.assertEqual(steps["package.assemble"]["status"], "experimental")
        self.assertEqual(steps["package.assemble"]["action"], "experimental_opt_in")
        self.assertEqual(steps["osm.clip"]["status"], "experimental")
        self.assertEqual(steps["osm.clip"]["action"], "experimental_opt_in")

    def test_osm_clip_does_not_satisfy_compiled_turn_restrictions(self) -> None:
        self.config.pipeline["required_capabilities"] = ["turn_restrictions"]

        with self.assertRaisesRegex(PipelineError, "Requested capabilities are not implemented.*turn_restrictions"):
            _preflight_inputs(self.config, _topological("osm.clip"), allow_network=False)

        self.assertFalse(self.workspace.exists())

    def test_osm_clip_plan_separates_raw_restriction_relations_from_compiled_rules(self) -> None:
        clip = next(item for item in plan(self.config, "osm.clip")["steps"] if item["id"] == "osm.clip")

        self.assertEqual(tuple(clip["capabilities"]), OSM_CLIP_CAPABILITIES_PRODUCED)
        self.assertEqual(tuple(clip["capabilities_missing"]), OSM_CLIP_CAPABILITIES_MISSING)
        self.assertIn("restriction_relations_raw", clip["capabilities"])
        self.assertNotIn("turn_restrictions", clip["capabilities"])
        self.assertIn("turn_restrictions", clip["capabilities_missing"])

    def test_cli_plan_does_not_create_workspace_or_access_network(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "nds_forge", "plan", "--config", str(self.config_path),
             "--target", "source.verify"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["side_effects"], "none")
        self.assertEqual(document["network"], "not accessed")
        self.assertFalse(self.workspace.exists())

    def test_remote_source_without_opt_in_fails_before_workspace_mutation(self) -> None:
        contents = self.config_path.read_text(encoding="utf-8")
        contents = contents.replace('path = "./synthetic-fixture.pbf"', 'path = ""')
        contents = contents.replace('sha256 = ""', 'sha256 = "' + "a" * 64 + '"')
        contents = contents.replace('license = "ODbL-1.0"',
                                    'url = "https://example.invalid/synthetic.osm.pbf"\n'
                                    'license = "ODbL-1.0"')
        self.config_path.write_text(contents, encoding="utf-8")
        config = load_config(self.config_path)
        with self.assertRaisesRegex(PipelineError, "allow-network"):
            run(config, "source.acquire", allow_network=False)
        self.assertFalse(self.workspace.exists(), "network opt-in gate must run before staging")

    def test_project_identifier_rejects_path_traversal(self) -> None:
        contents = self.config_path.read_text(encoding="utf-8")
        self.config_path.write_text(contents.replace('project_id = "synthetic-smoke"',
                                                     'project_id = "../escape"'),
                                    encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "project_id"):
            load_config(self.config_path)

    def test_registry_rejects_traversal_in_output_directory_or_filename(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe relative path"):
            register_step(Step("fixture.unsafe-dir", IMPLEMENTED, (), "../escape",
                               "TEST ONLY unsafe output directory"))
        with self.assertRaisesRegex(ValueError, "safe relative path"):
            register_step(Step("fixture.unsafe-file", IMPLEMENTED, (), "safe-output",
                               "TEST ONLY unsafe output filename", outputs=("../escape",)))
        self.assertFalse(self.workspace.exists())

    def test_package_prerequisite_failure_is_closed_even_with_experimental_opt_in(self) -> None:
        with self.assertRaisesRegex(PipelineError, "External input"):
            run(self.config, "package.assemble", allow_experimental=True)
        self.assertFalse((self.workspace / "artifacts" / "package").exists())
        self.assertFalse(self.workspace.exists(), "missing profile inputs must fail before workspace mutation")

    def test_synthetic_smoke_keeps_execution_and_validation_separate(self) -> None:
        result = run(self.config, "source.verify")
        self.assertEqual(result["overall_validation"], "unvalidated")
        self.assertEqual([item["id"] for item in result["results"]],
                         ["source.acquire", "region.resolve", "source.verify"])
        manifest_path = self.workspace / "state" / "source__verify.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["execution_status"], "completed")
        self.assertEqual(manifest["validation_status"], "unvalidated")
        self.assertIn("not_applied", manifest["result"]["spatial_filter"])
        validation = validate_step(self.config, "source.verify")
        self.assertEqual(validation["validation_status"], "partial")
        current = {item["id"]: item for item in status(self.config)["steps"]}
        self.assertEqual(current["source.verify"]["validation"], "partial")
        self.assertFalse((self.workspace / "artifacts" / "package").exists())

    def test_resume_invalidates_region_and_transitive_dependants_on_config_change(self) -> None:
        first = run(self.config, "source.verify")
        first_fp = {item["id"]: item["fingerprint"] for item in first["results"]}
        self._write_config("[-49.5, -25.6, -49.1, -25.3]")
        second = run(self.config, "source.verify", resume=True)
        second_fp = {item["id"]: item["fingerprint"] for item in second["results"]}
        self.assertNotEqual(second_fp["region.resolve"], first_fp["region.resolve"])
        self.assertNotEqual(second_fp["source.verify"], first_fp["source.verify"])

    def test_resume_rechecks_same_path_source_content(self) -> None:
        first = run(self.config, "source.verify")
        first_fp = next(item["fingerprint"] for item in first["results"]
                        if item["id"] == "source.acquire")
        self.source_path.write_bytes(b"CHANGED SYNTHETIC FIXTURE; SAME PATH\n")
        second = run(self.config, "source.verify", resume=True)
        acquired = next(item for item in second["results"] if item["id"] == "source.acquire")
        self.assertEqual(acquired["status"], "completed",
                         "changed input bytes must invalidate source.acquire cache")
        self.assertNotEqual(acquired["fingerprint"], first_fp)
        source_manifest = json.loads((self.workspace / "state" / "source__acquire.json").read_text())
        self.assertEqual(source_manifest["result"]["source"]["sha256"],
                         hashlib.sha256(self.source_path.read_bytes()).hexdigest())

    def test_fingerprint_includes_code_content(self) -> None:
        step = next(item for item in STEPS if item.id == "region.resolve")
        before = _current_fingerprint(self.config, step, {})
        from nds_forge import pipeline

        real_sha256 = pipeline.sha256

        def changed_code_hash(path: Path) -> str:
            if path.name == "pipeline.py":
                return "f" * 64
            return real_sha256(path)

        with patch("nds_forge.pipeline.sha256", side_effect=changed_code_hash):
            after = _current_fingerprint(self.config, step, {})
        self.assertNotEqual(before, after)

    def test_resume_rebuilds_when_cached_output_is_modified(self) -> None:
        run(self.config, "source.verify")
        source_json = self.workspace / "artifacts" / "source" / "source.json"
        source_json.write_text("tampered", encoding="utf-8")
        current = {item["id"]: item for item in status(self.config)["steps"]}
        self.assertFalse(current["source.acquire"]["outputs_intact"])
        result = run(self.config, "source.verify", resume=True)
        acquired = next(item for item in result["results"] if item["id"] == "source.acquire")
        self.assertEqual(acquired["status"], "completed")
        self.assertNotEqual(source_json.read_text(encoding="utf-8"), "tampered")

    def test_resume_invalidates_imported_compiler_dependencies(self) -> None:
        """A codec imported by an executor wrapper must invalidate resume too."""
        run(self.config, "source.verify")
        cached = run(self.config, "source.verify", resume=True)
        self.assertTrue(all(item["status"] == "cached" for item in cached["results"]))
        from nds_forge import pipeline
        real_sha256 = pipeline.sha256

        for relative in ("layers/bmd_codec.py", "layers/route_aux.py",
                         "adapters/osm_extract.py", "normalizers/roads.py"):
            def changed_dependency_hash(path, relative=relative):
                if path.as_posix().endswith("nds_forge/" + relative):
                    return "f" * 64
                return real_sha256(path)

            with self.subTest(module=relative), patch(
                    "nds_forge.pipeline.sha256", side_effect=changed_dependency_hash):
                rebuilt = run(self.config, "source.verify", resume=True)
            self.assertTrue(all(item["status"] == "completed" for item in rebuilt["results"]))
            # Restore ordinary fingerprints before testing the next dependency.
            run(self.config, "source.verify", resume=True)

    def test_resource_metrics_recorded_in_manifest_and_results(self) -> None:
        result = run(self.config, "source.verify")
        step_res = next(item for item in result["results"] if item["id"] == "source.verify")
        metrics = step_res.get("resource_metrics")
        self.assertIsNotNone(metrics)
        self.assertGreaterEqual(metrics["duration_seconds"], 0.0)
        self.assertGreater(metrics["peak_rss_bytes"], 0)
        self.assertGreaterEqual(metrics["input_bytes"], 0)
        self.assertGreater(metrics["output_bytes"], 0)

        manifest = json.loads((self.workspace / "state" / "source__verify.json").read_text())
        self.assertIn("resource_metrics", manifest)
        self.assertEqual(manifest["resource_metrics"]["output_bytes"], metrics["output_bytes"])

    def test_interruption_atomicity_and_clean_resume(self) -> None:
        step = Step(
            id="fixture.interrupted-demo",
            status=EXPERIMENTAL,
            dependencies=(),
            output="interrupted-demo",
            description="TEST ONLY: verify atomic failure and resume",
            inputs=(),
            outputs=("data.txt",),
            capabilities=("synthetic_only",),
        )
        should_fail = True

        def failing_executor(context: StepContext) -> dict:
            (context.output_dir / "data.txt").write_text("partial text")
            if should_fail:
                raise RuntimeError("Synthetic interruption midway through execution")
            return {"status": "ok"}

        register_step(step, failing_executor)

        # 1. Run step with failure: raises exception
        with self.assertRaises(RuntimeError):
            run(self.config, step.id, allow_experimental=True)

        # 2. Assert no partial publication in destination directory
        dest_dir = self.workspace / "artifacts" / step.output
        self.assertFalse(dest_dir.exists(), "Interrupted execution must not publish partial artifacts")

        # 3. Assert failure manifest recorded
        fail_path = self.workspace / "state" / f"{step.id.replace('.', '__')}.failure.json"
        self.assertTrue(fail_path.exists())

        # 4. Resume after resolving the cause of interruption
        should_fail = False
        res = run(self.config, step.id, allow_experimental=True, resume=True)
        completed = next(item for item in res["results"] if item["id"] == step.id)
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(dest_dir.exists())
        self.assertEqual((dest_dir / "data.txt").read_text(), "partial text")
        self.assertFalse(fail_path.exists(), "Clean resume must clear previous failure marker")

    def test_registered_synthetic_executor_runs_only_with_experimental_opt_in(self) -> None:
        step = Step(
            id="fixture.synthetic-demo",
            status=EXPERIMENTAL,
            dependencies=("source.acquire",),
            output="fixture-demo",
            description="TEST ONLY: synthetic executor registry smoke",
            inputs=("source.acquire/source.json",),
            outputs=("fixture.json",),
            capabilities=("synthetic_only",),
        )
        observed_context: dict[str, object] = {}

        def fake_executor(context: StepContext) -> dict:
            observed_context["output_dir"] = context.output_dir
            observed_context["dependency_outputs"] = dict(context.dependency_outputs)
            observed_context["allow_experimental"] = context.allow_experimental
            (context.output_dir / "fixture.json").write_text(
                json.dumps({"kind": "synthetic-test-only", "not_a_map": True}) + "\n",
                encoding="utf-8",
            )
            # Executor-supplied validation metadata must not promote validation.
            return {"test_adapter": "fake", "validation_status": "validated"}

        register_step(step, fake_executor)
        with self.assertRaisesRegex(PipelineError, "experimental"):
            run(self.config, step.id, allow_experimental=False)
        self.assertFalse(self.workspace.exists())

        result = run(self.config, step.id, allow_experimental=True)
        self.assertEqual(result["overall_validation"], "unvalidated")
        self.assertEqual(Path(observed_context["output_dir"]).parent.name, ".staging")
        self.assertEqual(observed_context["allow_experimental"], True)
        self.assertEqual(set(observed_context["dependency_outputs"]), {"source.acquire"})
        output = self.workspace / "artifacts" / "fixture-demo" / "fixture.json"
        self.assertTrue(output.is_file())
        manifest = json.loads((self.workspace / "state" / "fixture__synthetic-demo.json").read_text())
        self.assertEqual(manifest["validation_status"], "unvalidated")
        self.assertEqual(validate_step(self.config, step.id)["validation_status"], "partial")

    def test_cycle_is_rejected_before_workspace_mutation(self) -> None:
        register_step(Step("fixture.cycle-a", IMPLEMENTED, ("fixture.cycle-b",),
                           "cycle-a", "TEST ONLY cycle A"))
        register_step(Step("fixture.cycle-b", IMPLEMENTED, ("fixture.cycle-a",),
                           "cycle-b", "TEST ONLY cycle B"))
        with self.assertRaisesRegex(PipelineError, "Ciclo"):
            run(self.config, "fixture.cycle-a")
        self.assertFalse(self.workspace.exists())

    def test_unregistered_dependency_is_rejected_before_workspace_mutation(self) -> None:
        register_step(Step("fixture.missing-dependency", IMPLEMENTED,
                           ("fixture.not-registered",), "missing-dep",
                           "TEST ONLY missing dependency"))
        with self.assertRaisesRegex(PipelineError, "Dependência não registrada"):
            run(self.config, "fixture.missing-dependency")
        self.assertFalse(self.workspace.exists())

    def test_external_plugin_code_change_invalidates_fingerprint(self) -> None:
        plugin_path = self.root / "synthetic_plugin.py"
        plugin_path.write_text("# synthetic plugin version 1\n", encoding="utf-8")
        module_name = "nds_forge_test_synthetic_plugin"
        module = types.ModuleType(module_name)
        module.__file__ = str(plugin_path)

        def executor(_context: StepContext) -> dict:
            return {"synthetic": True}

        executor.__module__ = module_name
        module.executor = executor
        with patch.dict(sys.modules, {module_name: module}):
            step = Step("fixture.external-plugin", EXPERIMENTAL, (),
                        "external-plugin", "TEST ONLY external plugin")
            register_step(step, executor)
            before = _current_fingerprint(self.config, step, {})
            plugin_path.write_text("# synthetic plugin version 2\n", encoding="utf-8")
            after = _current_fingerprint(self.config, step, {})
        self.assertNotEqual(before, after)

    def test_failed_executor_preserves_old_output_but_overrides_stale_validation(self) -> None:
        step = Step("fixture.failing-executor", EXPERIMENTAL, ("source.acquire",),
                    "failing-output", "TEST ONLY failing executor")
        previous = self.workspace / "artifacts" / step.output
        previous.mkdir(parents=True)
        (previous / "previous-complete.txt").write_text("previous-complete", encoding="utf-8")
        prior_manifest_path = self.workspace / "state" / "fixture__failing-executor.json"
        prior_manifest_path.parent.mkdir(parents=True)
        prior_hash = hashlib.sha256((previous / "previous-complete.txt").read_bytes()).hexdigest()
        prior_manifest_path.write_text(json.dumps({
            "schema_version": 1,
            "step_id": step.id,
            "execution_status": "completed",
            "validation_status": "validated",
            "fingerprint": "previous-fingerprint",
            "dependencies": {},
            "output_hashes": {"previous-complete.txt": prior_hash},
            "result": {},
            "completed_at_utc": "2000-01-01T00:00:00+00:00",
        }), encoding="utf-8")

        def fail_after_staging(context: StepContext) -> dict:
            (context.output_dir / "partial.json").write_text("partial", encoding="utf-8")
            raise RuntimeError("synthetic adapter failure")

        register_step(step, fail_after_staging)
        with self.assertRaisesRegex(RuntimeError, "synthetic adapter failure"):
            run(self.config, step.id, allow_experimental=True)
        self.assertEqual((previous / "previous-complete.txt").read_text(encoding="utf-8"),
                         "previous-complete")
        self.assertFalse((previous / "partial.json").exists())
        self.assertEqual(json.loads(prior_manifest_path.read_text(encoding="utf-8"))[
            "validation_status"], "validated", "historical success record must remain unchanged")
        failure_path = self.workspace / "state" / "fixture__failing-executor.failure.json"
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(failure["execution_status"], "failed")
        self.assertEqual(failure["validation_status"], "unvalidated")
        current = {item["id"]: item for item in status(self.config)["steps"]}
        self.assertEqual(current[step.id]["execution"], "failed")
        self.assertEqual(current[step.id]["validation"], "unvalidated")
        with self.assertRaisesRegex(PipelineError, "execução mais recente.*falhou"):
            validate_step(self.config, step.id)

    def test_plugin_fingerprint_tracks_dependency_output_hashes(self) -> None:
        step = Step("fixture.dependency-sensitive", EXPERIMENTAL, ("source.acquire",),
                    "dependency-sensitive", "TEST ONLY dependency hash propagation")

        def copy_dependency_digest(context: StepContext) -> dict:
            source_report = json.loads((context.dependency_outputs["source.acquire"] /
                                        "source.json").read_text(encoding="utf-8"))
            (context.output_dir / "dependency.json").write_text(
                json.dumps({"source_sha256": source_report["sha256"]}) + "\n",
                encoding="utf-8",
            )
            return {"source_sha256": source_report["sha256"]}

        register_step(step, copy_dependency_digest)
        first = run(self.config, step.id, allow_experimental=True)
        first_result = next(item for item in first["results"]
                            if item["id"] == step.id)
        output = self.workspace / "artifacts" / step.output / "dependency.json"
        old_digest = json.loads(output.read_text(encoding="utf-8"))["source_sha256"]

        self.source_path.write_bytes(b"CHANGED INPUT FOR REGISTERED PLUGIN\n")
        second = run(self.config, step.id, allow_experimental=True, resume=True)
        second_result = next(item for item in second["results"]
                             if item["id"] == step.id)
        new_digest = json.loads(output.read_text(encoding="utf-8"))["source_sha256"]
        self.assertEqual(second_result["status"], "completed")
        self.assertNotEqual(second_result["fingerprint"], first_result["fingerprint"])
        self.assertNotEqual(new_digest, old_digest)
        self.assertEqual(new_digest, hashlib.sha256(self.source_path.read_bytes()).hexdigest())

    def test_declared_external_file_invalidates_plugin_and_downstream(self) -> None:
        external_file = self.root / "plugin-input.json"
        external_file.write_text('{"value":"first"}\n', encoding="utf-8")
        with self.config_path.open("a", encoding="utf-8") as config_file:
            config_file.write(f'\n[fixture]\ninput_file = "{external_file}"\n')
        self.config = load_config(self.config_path)

        reader = Step("fixture.external-reader", EXPERIMENTAL, ("source.acquire",),
                      "external-reader", "TEST ONLY declared external-file reader",
                      external_inputs=("fixture.input_file",))
        downstream = Step("fixture.external-downstream", EXPERIMENTAL,
                          (reader.id,), "external-downstream",
                          "TEST ONLY dependent stage after external input")

        def read_declared_file(context: StepContext) -> dict:
            path = context.external_inputs["fixture.input_file"]
            contents = path.read_text(encoding="utf-8")
            (context.output_dir / "read.json").write_text(
                json.dumps({"contents": contents}) + "\n", encoding="utf-8")
            return {"input_path": str(path)}

        def copy_upstream(context: StepContext) -> dict:
            value = (context.dependency_outputs[reader.id] / "read.json").read_text(encoding="utf-8")
            (context.output_dir / "downstream.json").write_text(value, encoding="utf-8")
            return {"copied": True}

        register_step(reader, read_declared_file)
        register_step(downstream, copy_upstream)
        first = run(self.config, downstream.id, allow_experimental=True)
        first_results = {item["id"]: item for item in first["results"]}
        old_reader_fp = first_results[reader.id]["fingerprint"]
        old_downstream_fp = first_results[downstream.id]["fingerprint"]

        external_file.write_text('{"value":"changed at same path"}\n', encoding="utf-8")
        second = run(self.config, downstream.id, allow_experimental=True, resume=True)
        second_results = {item["id"]: item for item in second["results"]}
        self.assertEqual(second_results[reader.id]["status"], "completed")
        self.assertEqual(second_results[downstream.id]["status"], "completed")
        self.assertNotEqual(second_results[reader.id]["fingerprint"], old_reader_fp)
        self.assertNotEqual(second_results[downstream.id]["fingerprint"], old_downstream_fp)
        final_contents = json.loads((self.workspace / "artifacts" / downstream.output /
                                     "downstream.json").read_text(encoding="utf-8"))
        self.assertEqual(final_contents["contents"], '{"value":"changed at same path"}\n')

    def test_missing_declared_external_file_fails_before_workspace_mutation(self) -> None:
        with self.config_path.open("a", encoding="utf-8") as config_file:
            config_file.write('\n[fixture]\ninput_file = "./missing-plugin-input.json"\n')
        self.config = load_config(self.config_path)
        step = Step("fixture.missing-external", EXPERIMENTAL, ("source.acquire",),
                    "missing-external", "TEST ONLY missing declared file",
                    external_inputs=("fixture.input_file",))
        register_step(step, lambda _context: {})
        with self.assertRaisesRegex(PipelineError, "does not exist"):
            run(self.config, step.id, allow_experimental=True)
        self.assertFalse(self.workspace.exists())

    def test_directory_commit_failure_restores_previous_complete_output(self) -> None:
        output = self.root / "artifacts" / "source"
        output.mkdir(parents=True)
        (output / "source.json").write_text("old-complete", encoding="utf-8")
        staging = self.root / "staged-source"
        staging.mkdir()
        (staging / "source.json").write_text("new-complete", encoding="utf-8")
        real_replace = os.replace

        def fail_publish(source, destination):
            if Path(source) == staging:
                raise OSError("synthetic publish failure")
            return real_replace(source, destination)

        with patch("nds_forge.pipeline.os.replace", side_effect=fail_publish):
            with self.assertRaisesRegex(OSError, "synthetic publish failure"):
                _commit_directory(staging, output)
        self.assertEqual((output / "source.json").read_text(), "old-complete")


if __name__ == "__main__":
    unittest.main()
