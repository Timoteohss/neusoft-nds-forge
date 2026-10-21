"""Unit tests for Native Parser Runner and external oracle contracts (Issue #37 / [6.2])."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from nds_forge.validation.native import (
    NativeArtifactHashMismatchError,
    NativeParserRunner,
    NativeProbeResult,
    NativeRunStatus,
    _sha256_file,
)


class NativeValidationRunnerTests(unittest.TestCase):
    """Test suite for NativeParserRunner with fake executable probes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Create dummy candidate artifact
        self.artifact_path = self.root / "tile_540673410.bin"
        self.artifact_path.write_bytes(b"\x00\x01\x02\x03FAKE_ROUTE_BLOB")
        self.artifact_sha = _sha256_file(self.artifact_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_fake_runner(self, script_body: str) -> Path:
        """Create an executable fake native runner script."""
        script_path = self.root / "fake_native_runner.sh"
        script_path.write_text(f"#!/bin/sh\n{script_body}\n", encoding="utf-8")
        # Make executable
        st = os.stat(script_path)
        os.chmod(script_path, st.st_mode | stat.S_IEXEC)
        return script_path

    def test_fake_runner_success_pass_and_hash_binding(self) -> None:
        runner_script = self._create_fake_runner("""
echo "PARSED TILE HEADER"
echo "INTERSECTIONS: 50"
echo "LINKS: 100"
exit 0
""")
        runner = NativeParserRunner(runtime_path=runner_script, timeout_seconds=5.0)
        res = runner.probe_tile(
            tile_id=540673410,
            artifact_path=self.artifact_path,
            expected_artifact_sha256=self.artifact_sha,
        )

        self.assertEqual(res.status, NativeRunStatus.PASS)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.artifact_sha256, self.artifact_sha)
        self.assertGreater(len(res.runtime_sha256), 0)
        self.assertIn("PARSED TILE HEADER", res.stdout)
        # Three diagnostic lines are not three parsed binary objects.
        self.assertEqual(res.stdout.count("\n"), 3)
        self.assertEqual(res.parsed_items_count, 0)
        # CRITICAL ACCEPTANCE CRITERIA: parser pass NEVER equals route pass
        self.assertFalse(res.route_pass)
        self.assertIn("PARSER_PASS is solely a binary format decoding check", res.boundary_note)

    def test_fake_runner_failure_non_zero_exit(self) -> None:
        runner_script = self._create_fake_runner("""
echo "ERROR: Truncated ROUTE header" >&2
exit 2
""")
        runner = NativeParserRunner(runtime_path=runner_script, timeout_seconds=5.0)
        res = runner.probe_tile(
            tile_id=540673410,
            artifact_path=self.artifact_path,
        )

        self.assertEqual(res.status, NativeRunStatus.FAIL)
        self.assertEqual(res.exit_code, 2)
        self.assertIn("Truncated ROUTE header", res.stderr)
        self.assertFalse(res.route_pass)

    def test_fake_runner_timeout_handled(self) -> None:
        runner_script = self._create_fake_runner("""
sleep 2
exit 0
""")
        # Set short timeout of 0.2 seconds
        runner = NativeParserRunner(runtime_path=runner_script, timeout_seconds=0.2)
        res = runner.probe_tile(
            tile_id=540673410,
            artifact_path=self.artifact_path,
        )

        self.assertEqual(res.status, NativeRunStatus.TIMEOUT)
        self.assertIn("timed out after 0.2s", res.stderr)
        self.assertFalse(res.route_pass)

    def test_missing_runtime_handled_cleanly(self) -> None:
        non_existent_runner = self.root / "does_not_exist_runner.bin"
        runner = NativeParserRunner(runtime_path=non_existent_runner)
        res = runner.probe_tile(
            tile_id=540673410,
            artifact_path=self.artifact_path,
        )

        self.assertEqual(res.status, NativeRunStatus.MISSING_RUNTIME)
        self.assertIn("Native runtime executable not found", res.stderr)
        self.assertFalse(res.route_pass)
        self.assertEqual(res.runtime_sha256, "")

    def test_runtime_hash_mismatch_returns_result_and_blocks_execution(self) -> None:
        runner_script = self._create_fake_runner("exit 0")
        actual_runtime_sha = _sha256_file(runner_script)
        runner = NativeParserRunner(runtime_path=runner_script, expected_runtime_sha256="0" * 64)
        with patch("nds_forge.validation.native.subprocess.run") as execute:
            result = runner.probe_tile(540673410, self.artifact_path, self.artifact_sha)
            execute.assert_not_called()
        self.assertEqual(result.status, NativeRunStatus.HASH_MISMATCH)
        self.assertEqual(result.runtime_sha256, actual_runtime_sha)
        self.assertEqual(result.artifact_sha256, self.artifact_sha)
        self.assertIn("Native runtime SHA-256 mismatch", result.stderr)
        self.assertIn(actual_runtime_sha, result.stderr)
        self.assertIsNone(result.exit_code)
        self.assertFalse(result.route_pass)
        self.assertEqual(result.parsed_items_count, 0)

    def test_empty_stdout_does_not_invent_one_parsed_item(self) -> None:
        runner_script = self._create_fake_runner("exit 0")
        result = NativeParserRunner(runner_script).probe_tile(540673410, self.artifact_path)
        self.assertEqual(result.status, NativeRunStatus.PASS)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.parsed_items_count, 0)
        self.assertFalse(result.route_pass)

    def test_artifact_hash_mismatch_blocks_execution(self) -> None:
        runner_script = self._create_fake_runner("exit 0")
        runner = NativeParserRunner(runtime_path=runner_script)
        res = runner.probe_tile(
            tile_id=540673410,
            artifact_path=self.artifact_path,
            expected_artifact_sha256="0000000000000000000000000000000000000000000000000000000000000000",
        )

        self.assertEqual(res.status, NativeRunStatus.HASH_MISMATCH)
        self.assertIn("Candidate artifact hash mismatch", res.stderr)


if __name__ == "__main__":
    unittest.main()
