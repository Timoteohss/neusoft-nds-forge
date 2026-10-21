#!/usr/bin/env python3
"""Guarded A/B/A route check for a candidate map package.

This is a device-mutating tool. It requires --execute, explicit paths, a
caller-selected serial, exact baseline path match, and a new local report path.
It backs up sdk.ini, verifies every candidate file before switching, runs the
same SDK route request before/during/after, restores the original config in a
finally block, and removes only the uniquely created candidate directory.
It never starts guidance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from adb_route_probe import probe


def _safe_device_path(value: str, app: str) -> str:
    path = PurePosixPath(value)
    prefix = PurePosixPath("/data/user/0") / app / "files"
    if not path.is_absolute() or ".." in path.parts or path == prefix or prefix not in path.parents:
        raise ValueError("Device paths must be below this app's /data/user/0/<package>/files")
    return str(path)


def _manifest_files(path: Path) -> dict[str, str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    records = doc.get("files")
    if isinstance(records, dict):
        result = {str(name): str(value) for name, value in records.items()}
    elif isinstance(records, list):
        result = {str(item["path"]): str(item["sha256"]) for item in records
                  if isinstance(item, dict) and "path" in item and "sha256" in item}
        if len(result) != len(records):
            raise ValueError("Manifest has malformed or duplicate file records")
    else:
        raise ValueError("Package manifest must contain files as a path→SHA map or records list")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
    if not result or any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in result.values()):
        raise ValueError("Manifest file hashes are missing or invalid")
    return result


def _write_private_new(path: Path, payload: dict) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)


def run(args: argparse.Namespace) -> dict:
    if not args.execute:
        raise ValueError("Refusing device changes without --execute")
    if args.output.exists() or not args.output.parent.is_dir():
        raise ValueError("Report output must be a new path inside an existing local directory")
    if not (1 <= args.port <= 65535 and 0 < args.startup_wait <= 300 and
            1 <= args.route_timeout <= 600):
        raise ValueError("Port or wait timeout is outside supported bounds")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_library_sha256):
        raise ValueError("Expected native library SHA-256 must be 64 hexadecimal characters")
    library_path = PurePosixPath(args.library_path_device)
    if not library_path.is_absolute() or ".." in library_path.parts:
        raise ValueError("Native library path must be an absolute safe device path")
    for label, point in (("start", args.start), ("end", args.end)):
        if len(point) != 2 or not (-90 <= point[0] <= 90 and -180 <= point[1] <= 180):
            raise ValueError(f"Invalid {label} latitude/longitude")
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", args.app_package):
        raise ValueError("Invalid app package name")
    if not re.fullmatch(r"[0-9]+:[0-9]+", args.remote_owner):
        raise ValueError("remote-owner must be a numeric uid:gid pair")
    if not re.fullmatch(re.escape(args.app_package) + r"/[A-Za-z0-9_.$]+", args.activity):
        raise ValueError("Activity must be an explicit component belonging to app-package")
    if args.service_package and not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", args.service_package):
        raise ValueError("Invalid SDK service package")
    sdk_ini = _safe_device_path(args.sdk_ini_device, args.app_package)
    candidate_remote = _safe_device_path(args.candidate_device_dir, args.app_package)
    if not candidate_remote.startswith(f"/data/user/0/{args.app_package}/files/map-test/"):
        raise ValueError("Candidate directory must be inside files/map-test/")
    package = args.candidate_package.resolve(strict=True)
    manifest_files = _manifest_files(args.package_manifest)
    actual_package_files = {path.relative_to(package).as_posix()
                            for path in package.rglob("*") if path.is_file()}
    allowed_extra = {"artifact-manifest.json", "package-manifest.json"}
    if actual_package_files - set(manifest_files) - allowed_extra:
        raise ValueError("Candidate package contains files not declared by its manifest")
    if set(manifest_files) - actual_package_files:
        raise ValueError("Candidate package is missing manifest-declared files")
    for rel, expected in manifest_files.items():
        relative = PurePosixPath(rel)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe manifest path: {rel}")
        local = package.joinpath(*relative.parts)
        if not local.is_file() or hashlib.sha256(local.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Candidate manifest/hash mismatch: {rel}")
    if not args.baseline_map_path.endswith("/") or not args.candidate_map_path.endswith("/"):
        raise ValueError("Baseline and candidate map paths must end in '/'")
    if not candidate_remote.endswith("/"):
        candidate_remote += "/"
    if args.candidate_map_path != candidate_remote:
        raise ValueError("Candidate map path must exactly match candidate device directory")

    def adb(*parts: str, timeout: int = 90) -> str:
        result = subprocess.run(["adb", "-s", args.device, *map(str, parts)],
                                check=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()

    if adb("get-state") != "device":
        raise RuntimeError("Selected ADB serial is not in device state")
    current_library_sha256 = adb("shell", "sha256sum", str(library_path)).split()[0]
    if current_library_sha256.lower() != args.expected_library_sha256.lower():
        raise RuntimeError("Selected native library hash does not match the requested profile")
    exists = subprocess.run(["adb", "-s", args.device, "shell", "test", "-e",
                             candidate_remote.rstrip("/")], capture_output=True, text=True,
                            timeout=20, check=False)
    if exists.returncode == 0:
        raise RuntimeError("Candidate device directory already exists; refusing to overwrite")
    original_tmp = tempfile.NamedTemporaryFile(prefix="nds-forge-original-sdkini-", delete=False)  # noqa: SIM115 -- NamedTemporaryFile is explicitly closed before an external ADB pull for cross-platform compatibility
    original_config_path = Path(original_tmp.name)
    original_tmp.close()
    adb("pull", sdk_ini, str(original_config_path))
    original_bytes = original_config_path.read_bytes()
    original_config = original_bytes.decode("utf-8")
    if original_config.count(args.baseline_map_path) != 1:
        original_config_path.unlink(missing_ok=True)
        raise RuntimeError("Active sdk.ini does not contain exactly one expected baseline path")
    token = uuid.uuid4().hex
    remote_backup = sdk_ini + ".nds-forge-backup-" + token
    remote_config = "/data/local/tmp/nds-forge-sdk-" + token
    local_config_path = None
    report = {"schema_version": 1, "started_at_utc": datetime.now(UTC).isoformat(),
              "device_fingerprint": hashlib.sha256(args.device.encode()).hexdigest()[:16],
              "candidate_manifest_sha256": hashlib.sha256(args.package_manifest.read_bytes()).hexdigest(),
              "candidate_device_dir": candidate_remote, "guidance_requested": False,
              "sdk_ini_restored": False, "package_removed": False, "routes": {}}
    candidate_pushed = False
    backup_created = False
    try:
        with tempfile.NamedTemporaryFile(prefix="nds-forge-sdkini-", delete=False) as tmp:
            local_config_path = Path(tmp.name)
            tmp.write(original_bytes)
        candidate_pushed = True
        adb("push", str(package), candidate_remote.rstrip("/"))
        adb("shell", "chown", "-R", args.remote_owner, candidate_remote.rstrip("/"))
        adb("shell", "chmod", "-R", "755", candidate_remote.rstrip("/"))
        hashes = []
        for rel, expected in sorted(manifest_files.items()):
            remote_file = candidate_remote + rel
            actual = adb("shell", "sha256sum", remote_file).split()[0]
            hashes.append({"path": rel, "match": actual == expected})
        report["candidate_hashes_verified"] = all(item["match"] for item in hashes)
        if not report["candidate_hashes_verified"]:
            report["candidate_hash_checks"] = hashes
            raise RuntimeError("Device candidate hash verification failed")
        adb("shell", "cp", "-p", sdk_ini, remote_backup)
        backup_created = True
        report["routes"]["baseline_before"] = probe(
            args.device, args.port, args.start, args.end, timeout=args.route_timeout,
            option=args.route_option, calculation=args.calculation,
            library_path=str(library_path), expected_library_sha256=args.expected_library_sha256.lower())

        candidate_config = original_config.replace(args.baseline_map_path, args.candidate_map_path, 1)
        with local_config_path.open("wb") as handle:
            handle.write(candidate_config.encode())
        adb("push", str(local_config_path), remote_config)
        adb("shell", "chown", args.remote_owner, remote_config)
        adb("shell", "chmod", "600", remote_config)
        adb("shell", "mv", remote_config, sdk_ini)
        _restart(adb, args)
        report["routes"]["candidate"] = probe(
            args.device, args.port, args.start, args.end, timeout=args.route_timeout,
            option=args.route_option, calculation=args.calculation,
            library_path=str(library_path), expected_library_sha256=args.expected_library_sha256.lower())
    finally:
        restore_error = None
        try:
            if backup_created:
                adb("shell", "cp", "-p", remote_backup, sdk_ini)
                adb("shell", "chown", args.remote_owner, sdk_ini)
                adb("shell", "chmod", "600", sdk_ini)
                _restart(adb, args)
                restored_tmp = tempfile.NamedTemporaryFile(prefix="nds-forge-restored-sdkini-", delete=False)  # noqa: SIM115 -- NamedTemporaryFile is explicitly closed before an external ADB pull for cross-platform compatibility
                restored_path = Path(restored_tmp.name)
                restored_tmp.close()
                try:
                    adb("pull", sdk_ini, str(restored_path))
                    restored = restored_path.read_bytes()
                finally:
                    restored_path.unlink(missing_ok=True)
                report["sdk_ini_restored"] = hashlib.sha256(restored).digest() == hashlib.sha256(original_bytes).digest()
                if not report["sdk_ini_restored"]:
                    restore_error = "Restored sdk.ini bytes differ from the original"
                else:
                    report["routes"]["baseline_after"] = probe(
                        args.device, args.port, args.start, args.end, timeout=args.route_timeout,
                        option=args.route_option, calculation=args.calculation,
                        library_path=str(library_path), expected_library_sha256=args.expected_library_sha256.lower())
        except Exception as exc:  # noqa: BLE001 -- Broad catch is intentional at this recovery/reporting boundary; preserve rollback/error aggregation behavior
            restore_error = f"Could not verify rollback: {type(exc).__name__}"
        finally:
            if backup_created and report["sdk_ini_restored"]:
                adb("shell", "rm", "-f", remote_backup)
            elif backup_created:
                report["remote_backup_preserved"] = remote_backup
            if candidate_pushed:
                adb("shell", "rm", "-rf", candidate_remote.rstrip("/"))
                report["package_removed"] = True
            if local_config_path:
                local_config_path.unlink(missing_ok=True)
            original_config_path.unlink(missing_ok=True)
        if restore_error:
            raise RuntimeError(f"{restore_error}; rollback backup preserved at {remote_backup}")
    report["finished_at_utc"] = datetime.now(UTC).isoformat()
    report["aba_route_consistent"] = all(
        report["routes"].get(label, {}).get("success") for label in
        ("baseline_before", "candidate", "baseline_after"))
    return report


def _restart(adb, args: argparse.Namespace) -> None:
    adb("shell", "am", "force-stop", args.app_package)
    if args.service_package:
        adb("shell", "am", "force-stop", args.service_package)
    adb("shell", "am", "start", "-n", args.activity)
    time.sleep(args.startup_wait)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True,
                        help="Explicitly authorize map swap and app restart with automatic rollback")
    parser.add_argument("--device", required=True, help="Caller-selected ADB serial")
    parser.add_argument("--app-package", required=True)
    parser.add_argument("--activity", required=True, help="Fully qualified activity component")
    parser.add_argument("--service-package", help="Optional SDK service package to restart")
    parser.add_argument("--sdk-ini-device", required=True)
    parser.add_argument("--baseline-map-path", required=True,
                        help="Exact path substring currently present once in sdk.ini")
    parser.add_argument("--candidate-map-path", required=True,
                        help="Exact map directory to put in sdk.ini; must match candidate device dir")
    parser.add_argument("--candidate-device-dir", required=True,
                        help="New path below app files/map-test/; must not already exist")
    parser.add_argument("--remote-owner", required=True, help="Numeric uid:gid for package ownership")
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--package-manifest", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--library-path-device", required=True)
    parser.add_argument("--expected-library-sha256", required=True)
    parser.add_argument("--start", nargs=2, type=float, required=True, metavar=("LAT", "LON"))
    parser.add_argument("--end", nargs=2, type=float, required=True, metavar=("LAT", "LON"))
    parser.add_argument("--route-option", type=int, required=True)
    parser.add_argument("--calculation", type=int)
    parser.add_argument("--startup-wait", type=int, default=30)
    parser.add_argument("--route-timeout", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True,
                        help="New local report path; created mode 0600 and never overwritten")
    args = parser.parse_args()
    try:
        report = run(args)
        _write_private_new(args.output, report)
    except Exception as exc:  # noqa: BLE001 -- Broad catch is intentional at this recovery/reporting boundary; preserve rollback/error aggregation behavior
        print(f"adb-route-ab: {type(exc).__name__}: {exc}", file=__import__('sys').stderr)
        return 2
    print(json.dumps({"aba_route_consistent": report["aba_route_consistent"],
                      "sdk_ini_restored": report["sdk_ini_restored"],
                      "package_removed": report["package_removed"],
                      "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
