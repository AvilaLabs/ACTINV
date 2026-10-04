"""Source-only tests for the archived original P116 G1 evidence."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p116_repair_control as repair


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _receipt(name: str, log_sha: str) -> dict:
    return {
        "argv": repair.EXPECTED_ARGV[name], "child_exit_code": 0, "cwd": ".",
        "error": None, "gate": name, "log_path": f"target/p116-{name}.log",
        "log_sha256": log_sha, "phase": "P116", "schema": "actinv-roadmap-gate-receipt-1",
        "status": "completed", "timeout_s": repair.EXPECTED_TIMEOUTS.get(name, 600.0),
        "resources": {
            "cgroup_limits": repair.RESOURCE_LIMITS,
            "cgroup_path": "/user.slice/user-1000.slice/user@1000.service/app.slice/example.scope",
            "environment": repair.RESOURCE_ENV, "platform": "linux",
            "tmpdir": {"filesystem": "ext4", "mount_point": "/", "path": "target/preflight-tmp"},
        },
    }


class P116RepairControlTests(unittest.TestCase):
    def test_archive_members_accept_exact_bytes_and_refuse_missing_extra_and_git_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p116-repair-archive-") as temp:
            root = Path(temp)
            archive = root / "archive"
            archive.mkdir()
            content = {"archive/receipt.json": b"receipt bytes", "archive/run.log": b"log bytes"}
            for relative, raw in content.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            hashes = {relative: _sha(raw) for relative, raw in content.items()}
            self.assertEqual(repair._validate_archive_members(
                root, hashes, content, prefix="archive/", expected_count=2), hashes)

            with self.assertRaisesRegex(ValueError, "physical members"):
                repair._validate_archive_members(
                    root, {"archive/receipt.json": hashes["archive/receipt.json"]}, content,
                    prefix="archive/", expected_count=1)

            (archive / "unexpected.log").write_bytes(b"extra")
            with self.assertRaisesRegex(ValueError, "physical members"):
                repair._validate_archive_members(
                    root, hashes, content, prefix="archive/", expected_count=2)
            (archive / "unexpected.log").unlink()

            (archive / "run.log").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "retained bytes changed"):
                repair._validate_archive_members(
                    root, hashes, content, prefix="archive/", expected_count=2)
            (archive / "run.log").write_bytes(content["archive/run.log"])

            with self.assertRaisesRegex(ValueError, "retained Git blob differs"):
                repair._validate_archive_members(
                    root, hashes, {**content, "archive/run.log": b"different Git blob"},
                    prefix="archive/", expected_count=2)

    def test_source_population_requires_exact_95_controls_100_rust_and_metadata(self) -> None:
        controls = {f"controls/frozen_{index:02}.py": "a" * 64 for index in range(95)}
        rust = {f"crates/frozen_{index:03}.rs": "b" * 64 for index in range(repair.RUST_COUNT)}
        source_map = {**controls, **rust, **{path: "c" * 64 for path in repair.METADATA_SOURCES}}
        self.assertEqual(len(source_map), repair.SOURCE_COUNT)
        g0 = {"control_sha256": controls, "inherited_rust_sha256": rust}
        observed, observed_rust = repair._source_population(g0, source_map)
        self.assertEqual(observed, source_map)
        self.assertEqual(observed_rust, rust)
        with self.assertRaisesRegex(ValueError, "initial source map population differs"):
            repair._source_population(g0, {key: value for key, value in source_map.items()
                                           if key != "ledger.md"})
        wrong_type = {**source_map, "ledger.md": 7}
        with self.assertRaisesRegex(ValueError, "exact control/Rust/metadata"):
            repair._source_population(g0, wrong_type)

    def test_amendment_registration_requires_unique_protocol_and_amendment_lines(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p116-amendment-registry-") as temp:
            root = Path(temp)
            (root / "protocols").mkdir()
            registry = root / "protocols/protocol_hash.txt"
            protocol_line = f"{repair.P116_PROTOCOL_SHA256}  protocols/ACTINV-P116_PROTOCOL.md"
            amendment_line = f"{repair.AMENDMENT_SHA256}  protocols/ACTINV-P116_AMENDMENT_A.md"
            registry.write_text(protocol_line + "\n", encoding="utf-8")
            self.assertFalse(repair._amendment_is_registered(root))
            registry.write_text(protocol_line + "\n" + amendment_line + "\n", encoding="utf-8")
            self.assertTrue(repair._amendment_is_registered(root))
            registry.write_text(protocol_line + "\n" + amendment_line + "\n" + amendment_line + "\n",
                                encoding="utf-8")
            self.assertFalse(repair._amendment_is_registered(root))

    def test_initial_receipt_population_binds_exact_names_argv_timeouts_and_integer_exits(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p116-repair-receipts-") as temp:
            root = Path(temp)
            retained: dict[str, str] = {}
            for name in repair.EXPECTED_GATE_NAMES:
                log_rel = f"{repair.ARCHIVE_PREFIX}target/p116-{name}.log"
                receipt_rel = f"{repair.ARCHIVE_PREFIX}results/quality/p116/{name}.json"
                log_raw = f"log:{name}".encode()
                log = root / log_rel
                receipt_path = root / receipt_rel
                log.parent.mkdir(parents=True, exist_ok=True)
                receipt_path.parent.mkdir(parents=True, exist_ok=True)
                log.write_bytes(log_raw)
                receipt_raw = (json.dumps(_receipt(name, _sha(log_raw)), sort_keys=True) + "\n").encode()
                receipt_path.write_bytes(receipt_raw)
                retained[log_rel] = _sha(log_raw)
                retained[receipt_rel] = _sha(receipt_raw)
            exits = {name: 0 for name in repair.EXPECTED_GATE_NAMES}
            result = repair._validate_receipts(root, retained, exits)
            self.assertEqual(len(result), repair.QUALITY_GATE_COUNT)

            changed = root / f"{repair.ARCHIVE_PREFIX}results/quality/p116/workspace_check.json"
            data = json.loads(changed.read_text(encoding="utf-8"))
            data["child_exit_code"] = 0.0
            changed_raw = json.dumps(data, sort_keys=True).encode("utf-8")
            changed.write_bytes(changed_raw)
            retained[f"{repair.ARCHIVE_PREFIX}results/quality/p116/workspace_check.json"] = _sha(changed_raw)
            with self.assertRaisesRegex(ValueError, "receipt identity differs"):
                repair._validate_receipts(root, retained, exits)
            with self.assertRaisesRegex(ValueError, "exactly 25 zeroes"):
                repair._validate_receipts(root, retained, {**exits, "workspace_check": False})


if __name__ == "__main__":
    unittest.main()
