"""Unit tests for single-owner car lease, watchdog, and durable rollback (Issue #38 / [6.3])."""

from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from pathlib import Path

from nds_forge.validation.device_lease import (
    DeviceLeaseConflictError,
    DeviceLeaseManager,
    _sha256_file,
)


class DeviceLeaseTests(unittest.TestCase):
    """Test suite for DeviceLeaseManager, concurrency exclusivity, and durable rollback."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.state_dir = self.root / "state"
        self.target_dir = self.root / "car_device_nav"
        self.candidate_dir = self.root / "candidate_pkg"

        self.target_dir.mkdir()
        self.candidate_dir.mkdir()

        # Populate initial target state (existing map in car)
        (self.target_dir / "Area.txt").write_text("ORIGINAL_AREA_ISR", encoding="utf-8")
        (self.target_dir / "ROOT.NDS").write_bytes(b"ORIGINAL_ROOT_DATA")

        # Populate candidate state (new Curitiba package)
        (self.candidate_dir / "Area.txt").write_text("CANDIDATE_AREA_CWB", encoding="utf-8")
        (self.candidate_dir / "ROOT.NDS").write_bytes(b"CANDIDATE_ROOT_DATA")

        cand_files = sorted(self.candidate_dir.rglob("*"))
        cand_hashes = [_sha256_file(f) for f in cand_files if f.is_file()]
        self.candidate_hash = hashlib.sha256("".join(cand_hashes).encode("utf-8")).hexdigest()

        self.mgr = DeviceLeaseManager(self.state_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_single_owner_exclusive_lease(self) -> None:
        # Operator 1 acquires lease
        lease1 = self.mgr.acquire_lease("operator_alice", "lease-001")
        self.assertTrue(lease1.active)

        # Operator 2 attempts to acquire lease concurrently -> fails
        with self.assertRaises(DeviceLeaseConflictError) as ctx:
            self.mgr.acquire_lease("operator_bob", "lease-002")
        self.assertIn("Device is leased exclusively to 'operator_alice'", str(ctx.exception))

        # Alice releases lease
        self.mgr.release_lease("operator_alice")

        # Now Bob can acquire lease
        lease2 = self.mgr.acquire_lease("operator_bob", "lease-002")
        self.assertTrue(lease2.active)

    def test_clean_deployment_within_watchdog(self) -> None:
        self.mgr.acquire_lease("operator_alice", "lease-001")

        report = self.mgr.deploy_candidate_with_rollback(
            operator="operator_alice",
            target_dir=self.target_dir,
            candidate_dir=self.candidate_dir,
            expected_candidate_hash=self.candidate_hash,
            raw_device_endpoint="192.168.1.105:5555",
            watchdog_timeout_s=5.0,
        )

        self.assertTrue(report.success)
        self.assertFalse(report.rolled_back)
        self.assertFalse(report.guidance_enabled)
        # Verify redaction
        self.assertNotIn("192.168.1.105", report.device_id_redacted)
        self.assertIn("[REDACTED_DEVICE_", report.device_id_redacted)
        # Verify candidate replaced target
        self.assertEqual((self.target_dir / "Area.txt").read_text(), "CANDIDATE_AREA_CWB")

    def test_candidate_hash_mismatch_aborts_without_touching_target(self) -> None:
        self.mgr.acquire_lease("operator_alice", "lease-001")

        report = self.mgr.deploy_candidate_with_rollback(
            operator="operator_alice",
            target_dir=self.target_dir,
            candidate_dir=self.candidate_dir,
            expected_candidate_hash="bad_hash_00000000000000000000000000000000",
            raw_device_endpoint="usb:device:SN12345678",
        )

        self.assertFalse(report.success)
        self.assertFalse(report.rolled_back)
        self.assertIn("Candidate hash mismatch", report.reason)
        # Target state must remain untouched
        self.assertEqual((self.target_dir / "Area.txt").read_text(), "ORIGINAL_AREA_ISR")

    def test_simulated_disconnect_triggers_durable_recovery(self) -> None:
        self.mgr.acquire_lease("operator_alice", "lease-001")

        def simulate_disconnect(staged_dir: Path) -> None:
            # Partially corrupt target during deploy
            (staged_dir / "Area.txt").write_text("CORRUPTED_PARTIAL")
            raise ConnectionResetError("Device connection dropped unexpectedly")

        report = self.mgr.deploy_candidate_with_rollback(
            operator="operator_alice",
            target_dir=self.target_dir,
            candidate_dir=self.candidate_dir,
            expected_candidate_hash=self.candidate_hash,
            raw_device_endpoint="10.0.0.42:5555",
            deploy_fn=simulate_disconnect,
        )

        self.assertFalse(report.success)
        self.assertTrue(report.rolled_back)
        self.assertIn("ConnectionResetError", report.reason)
        # Durable recovery: original state restored byte-for-byte!
        self.assertEqual((self.target_dir / "Area.txt").read_text(), "ORIGINAL_AREA_ISR")
        self.assertEqual((self.target_dir / "ROOT.NDS").read_bytes(), b"ORIGINAL_ROOT_DATA")

    def test_watchdog_timeout_triggers_durable_recovery(self) -> None:
        self.mgr.acquire_lease("operator_alice", "lease-001")

        def simulate_hang(staged_dir: Path) -> None:
            time.sleep(0.3)

        report = self.mgr.deploy_candidate_with_rollback(
            operator="operator_alice",
            target_dir=self.target_dir,
            candidate_dir=self.candidate_dir,
            expected_candidate_hash=self.candidate_hash,
            raw_device_endpoint="10.0.0.42:5555",
            deploy_fn=simulate_hang,
            watchdog_timeout_s=0.1,  # Short timeout
        )

        self.assertFalse(report.success)
        self.assertTrue(report.rolled_back)
        self.assertIn("TimeoutError", report.reason)
        # Original state restored byte-for-byte
        self.assertEqual((self.target_dir / "Area.txt").read_text(), "ORIGINAL_AREA_ISR")


if __name__ == "__main__":
    unittest.main()
