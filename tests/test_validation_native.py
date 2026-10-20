"""Unit tests for Native Parser Runner and external oracle contracts (Issue #37 / [6.2])."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest

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
        self.assertEqual(res.parsed_items_count, 3)
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
