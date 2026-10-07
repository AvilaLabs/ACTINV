#!/usr/bin/env python3
"""Verify the immutable, CI-transport-only terminal P116 disposition."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "2117f3b5df715ce832658f58250103789be845bb"
PROTOCOL = "protocols/ACTINV-P116_PROTOCOL.md"
PROTOCOL_SHA256 = "cf5841dafbd2470604b36d75fdafa2e2fc730e33abb26f6e44a4068e6388a8aa"
IMPLEMENTATION_SHA256 = "be2e96c12e5fd5356eb493bc14909a782f6f6cba2fb739645d264ae0f1b249fa"
CI_SHA256 = "df939adb6bcb2a81bb9bca58df8638ea16ce0cdfcf94f89f1e07b3d302ff3c73"
VERDICT_SHA256 = "7d782e476b051cc2e7d9666e984511167f30d6e1e5ec73afc127d34ac9dac710"
DISCOVERY_SHA256 = "c007355933dd2f685f6189714a96277e6448a9da0802c0c18db331f72d660039"
ARTIFACT_SHA256 = {
    "results/g0_p116_twin_waste.json": "7e08a5f94e2cce3d62229c1e1233938270420c27556abb1a81e3f304ebc4cf78",
    "results/g1_p116_twin_waste.json": "74f6eab80ee223c81b6f7391f3f4147d7fa38ffeac55485f3d2a05e8618a65b9",
    "results/g2_p116_twin_waste.json": "5e1cb32140fa7fe3000bf2d22e54b30af021d992f689d1a1a7ff1038b979fb6b",
    "results/g3_p116_quality.json": "746876fc7c2d9da2432c5e1a816702a648f18c9ae920798c2639f9670082dd5b",
}
ARCHIVE = ROOT / "results/failures/p116_ci"
DISCOVERY = ARCHIVE / "discovery.json"
IMPLEMENTATION = ROOT / "results/p116_implementation_commit.json"
CI_RUNS = ROOT / "results/p116_ci_runs.json"
VERDICT = ROOT / "results/p116_verdict.json"
EXPECTED_ARCHIVE_FILES = {
    "logs/p116-ci-controls-attempt1.log": "c37a90530459fbcf47869e2de29b90467f9cc477444818440f127e75d8c414dd",
    "logs/p116-ci-controls-attempt2.log": "1f4ec06167f63c4e5511dd82e9a0d0c759b32e76c71190fcde0f731d1a101094",
    "logs/p116-ci-fns-iron-attempt1.log": "2bf0241b25f347b88d9be960a8612a0008fe36f9f2c5495b45f57264810322bc",
    "logs/p116-ci-fns-iron-attempt2.log": "289747aba4aa751790eeb707ddea1ee69397ae70cbcf3954142b7e160e9ab292",
    "metadata/p116-ci-controls-attempt1.json": "d092a73a48fbcb1ad53e6cfb5dccdf252e32bde7fba3645801dac3befb88746e",
    "metadata/p116-ci-controls-attempt2.json": "4c5d40173bd00792ff12c92fd6513b0a9f6b00e38e3bb788fbb4e3b4ce1bccfb",
    "metadata/p116-ci-fns-iron-attempt1.json": "735bcb2bf9b505190acd1dc2ab2f9ee711f577cf703b223f1446c72ecf428cc5",
    "metadata/p116-ci-fns-iron-attempt2.json": "6334e98097c75f2e3814ac012791feab3226d8ff723c755a0b05d94e70b78ca5",
    "metadata/p116-ci-iaea-headers.txt": "e09724cfccc8e04ad6892379c25eb6ef4f6ac3813535d0b3a3f9687b8689099a",
    "metadata/p116-ci-terminal.json": "78b5f445eaa230093f3c169fa062ca48e152c5627201f54c8585d82e070b6217",
    "pre_ci_local_verdict.json": "f3be6d6ada4afbfe28f9f32a06be300c219c94b0fda45f31051124ebf6fad606",
    "source/p116-preserve-ci-failure.py": "8c72ee70265e710a46bd4e768bc6ee53d5bbb6c3aac08138317e0b47c2357220",
}
WORKFLOWS = {
    "Build handbook", "desktop builds", "Build browser workbench",
    "fusion-isotope", "fns-iron", "controls",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _safe_file(relative: str) -> Path:
    posix = PurePosixPath(relative)
    if (not relative or posix.is_absolute() or ".." in posix.parts or "." in posix.parts
            or "\\" in relative or posix.as_posix() != relative):
        raise ValueError(f"unsafe path: {relative!r}")
    path = ROOT / relative
    path.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    cursor = ROOT
    for part in posix.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink in evidence: {relative}")
    if not path.is_file():
        raise ValueError(f"not a regular file: {relative}")
    return path


def _read(relative: str):
    return json.loads(_safe_file(relative).read_text(encoding="utf-8"))


def _archive_files() -> tuple[dict[str, str], bool]:
    discovery = _read("results/failures/p116_ci/discovery.json")
    recorded = discovery.get("files_sha256") if isinstance(discovery, dict) else None
    expected_inputs = {
        "target/p116-ci-terminal.json": EXPECTED_ARCHIVE_FILES["metadata/p116-ci-terminal.json"],
        "target/p116-ci-iaea-headers.txt": EXPECTED_ARCHIVE_FILES["metadata/p116-ci-iaea-headers.txt"],
    }
    for name in ("controls", "fns-iron"):
        for attempt in (1, 2):
            stem = f"p116-ci-{name}-attempt{attempt}"
            expected_inputs[f"target/{stem}.json"] = EXPECTED_ARCHIVE_FILES[f"metadata/{stem}.json"]
            expected_inputs[f"target/{stem}.log"] = EXPECTED_ARCHIVE_FILES[f"logs/{stem}.log"]
    if (not isinstance(recorded, dict) or recorded != EXPECTED_ARCHIVE_FILES
            or _sha(DISCOVERY.read_bytes()) != DISCOVERY_SHA256
            or discovery.get("schema") != "actinv-p116-ci-failure-archive-1"
            or discovery.get("implementation_commit") != COMMIT
            or discovery.get("terminal_runs_sha256") != EXPECTED_ARCHIVE_FILES["metadata/p116-ci-terminal.json"]
            or discovery.get("source_inputs_sha256") != expected_inputs
            or discovery.get("terminal_workflow_count") != 6
            or discovery.get("terminal_success_count") != 4
            or discovery.get("terminal_failure_count") != 2
            or discovery.get("attempt_count") != 4
            or discovery.get("pre_ci_local_verdict_sha256") != EXPECTED_ARCHIVE_FILES["pre_ci_local_verdict.json"]):
        return {}, False
    try:
        observed = {}
        for relative, expected in EXPECTED_ARCHIVE_FILES.items():
            path = _safe_file(f"results/failures/p116_ci/{relative}")
            raw = path.read_bytes()
            if _sha(raw) != expected:
                return {}, False
            observed[relative] = expected
        terminal = json.loads((ARCHIVE / "metadata/p116-ci-terminal.json").read_text(encoding="utf-8"))
        if not isinstance(terminal, list) or len(terminal) != 6:
            return {}, False
        terminal_by_name = {row.get("workflowName"): row for row in terminal if isinstance(row, dict)}
        if set(terminal_by_name) != WORKFLOWS:
            return {}, False
        if sum(row.get("conclusion") == "success" for row in terminal_by_name.values()) != 4:
            return {}, False
        if {name for name, row in terminal_by_name.items() if row.get("conclusion") == "failure"} != {
                "controls", "fns-iron"}:
            return {}, False
        if any(row.get("headSha") != COMMIT or row.get("status") != "completed"
               for row in terminal_by_name.values()):
            return {}, False
        for name, run_id in (("controls", 37216443618), ("fns-iron", 37216443631)):
            for attempt in (1, 2):
                stem = f"p116-ci-{name}-attempt{attempt}"
                metadata = json.loads((ARCHIVE / f"metadata/{stem}.json").read_text(encoding="utf-8"))
                if (not isinstance(metadata, dict) or metadata.get("id") != run_id
                        or metadata.get("name") != name or metadata.get("head_sha") != COMMIT
                        or metadata.get("run_attempt") != attempt or metadata.get("status") != "completed"
                        or metadata.get("conclusion") != "failure"
                        or metadata.get("html_url") != terminal_by_name[name].get("url")):
                    return {}, False
                log = (ARCHIVE / f"logs/{stem}.log").read_text(encoding="utf-8", errors="replace").lower()
                if "403" not in log:
                    return {}, False
                if name == "controls" and ("requested url returned error: 403" not in log
                                            or "exit code 22" not in log):
                    return {}, False
                if name == "fns-iron" and ("http status: 403" not in log or "exit code 1" not in log):
                    return {}, False
        headers = (ARCHIVE / "metadata/p116-ci-iaea-headers.txt").read_text(encoding="utf-8", errors="replace").lower()
        if "403" not in headers or "cf-mitigated: challenge" not in headers:
            return {}, False
        pre_ci = json.loads((ARCHIVE / "pre_ci_local_verdict.json").read_text(encoding="utf-8"))
        if (not isinstance(pre_ci, dict) or pre_ci.get("verdict") != "P116-LOCAL-PASS"
                or pre_ci.get("gates", {}).get("G3_CI") != "PENDING"):
            return {}, False
        physical = set()
        directories = set()
        for path in ARCHIVE.rglob("*"):
            if path.is_symlink():
                return {}, False
            if path.is_file():
                physical.add(path.relative_to(ARCHIVE).as_posix())
            elif path.is_dir():
                directories.add(path.relative_to(ARCHIVE).as_posix())
            else:
                return {}, False
        if (physical != set(EXPECTED_ARCHIVE_FILES) | {"discovery.json"}
                or directories != {"logs", "metadata", "source"}):
            return {}, False
    except (OSError, ValueError):
        return {}, False
    return observed, True


def _check_control_sources(g0: dict) -> tuple[int, bool]:
    controls = g0.get("control_sha256")
    if not isinstance(controls, dict) or not controls:
        return 0, False
    try:
        import sys

        controls_dir = str(ROOT / "controls")
        if controls_dir not in sys.path:
            sys.path.insert(0, controls_dir)
        import check_p116

        if set(controls) != set(check_p116.CONTROL_FILES):
            return 0, False
        for relative, expected in controls.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                return len(controls), False
            # P120: checked against the implementation commit's Git object, not the working tree.
            if _sha(check_p116._git_blob(COMMIT, relative)) != expected:
                return len(controls), False
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError):
        return len(controls), False
    return len(controls), True


def _current_rust_matches(g3: dict, implementation: dict) -> bool:
    try:
        import sys

        controls_dir = str(ROOT / "controls")
        if controls_dir not in sys.path:
            sys.path.insert(0, controls_dir)
        import check_p116
        import check_p116_verdict

        sealed = g3.get("production_rust_sha256")
        paths = check_p116._rust_paths_at_commit(COMMIT)
        # P120: the implementation commit's Git objects, not the working tree.
        return (isinstance(sealed, dict) and len(sealed) == 100 and len(paths) == 100
                and set(sealed) == paths
                and {path: _sha(check_p116._git_blob(COMMIT, path)) for path in paths} == sealed
                and check_p116_verdict._source_commit_matches(g3, implementation))
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False


def verify() -> dict:
    """Return a fail-closed check of P116 history; this does not run the science CLI."""
    try:
        import sys

        controls_dir = str(ROOT / "controls")
        if controls_dir not in sys.path:
            sys.path.insert(0, controls_dir)
        import check_p116_verdict

        protocol = _safe_file(PROTOCOL)
        implementation = _read("results/p116_implementation_commit.json")
        ci_runs = _read("results/p116_ci_runs.json")
        verdict = _read("results/p116_verdict.json")
        g0, g1, g2, g3 = (
            _read(f"results/g{i}_p116_twin_waste.json") if i < 3 else _read("results/g3_p116_quality.json")
            for i in range(4)
        )
        if any(not isinstance(item, dict) for item in (implementation, verdict, g0, g1, g2, g3)):
            raise ValueError("missing or malformed P116 evidence object")
        artifacts_ok = True
        for relative, expected in ARTIFACT_SHA256.items():
            artifacts_ok &= _sha(_safe_file(relative).read_bytes()) == expected
        archive_hashes, archive_ok = _archive_files()
        control_count, control_sources_ok = _check_control_sources(g0)
        if not isinstance(protocol, Path) or _sha(protocol.read_bytes()) != PROTOCOL_SHA256:
            raise ValueError("P116 protocol hash differs")
        if (_sha(_safe_file("results/p116_implementation_commit.json").read_bytes()) != IMPLEMENTATION_SHA256
                or _sha(_safe_file("results/p116_ci_runs.json").read_bytes()) != CI_SHA256
                or _sha(_safe_file("results/p116_verdict.json").read_bytes()) != VERDICT_SHA256):
            raise ValueError("P116 implementation, CI or verdict record hash differs")
        if (_sha(_safe_file("results/failures/p116_ci/discovery.json").read_bytes()) != DISCOVERY_SHA256
                or implementation.get("commit_sha") != COMMIT
                or verdict.get("verdict") != "P116-FAIL"
                or verdict.get("implementation_commit") != COMMIT
                or verdict.get("gates") != {
                    "CI_evidence_consistent": "FAIL", "G0": "PASS", "G1": "PASS", "G2": "PASS",
                    "G3_CI": "FAIL", "G3_local": "PASS", "historical_p113_failure": "PASS",
                    "historical_p114_failure": "PASS", "historical_p115_failure": "PASS",
                    "protocol": "PASS", "source_commit": "PASS"}
                or implementation.get("g0_sha256") != ARTIFACT_SHA256["results/g0_p116_twin_waste.json"]
                or implementation.get("g1_sha256") != ARTIFACT_SHA256["results/g1_p116_twin_waste.json"]
                or implementation.get("g2_sha256") != ARTIFACT_SHA256["results/g2_p116_twin_waste.json"]
                or implementation.get("g3_sha256") != ARTIFACT_SHA256["results/g3_p116_quality.json"]):
            raise ValueError("terminal P116 identity or disposition differs from its frozen record")
        if (g0.get("pass") is not True or g1.get("pass") is not True or g2.get("pass") is not True
                or g3.get("pass") is not True or g3.get("repair_rounds") != 1
                or g1.get("request_count") != 35 or g1.get("component_target_count") != 138
                or g1.get("independent_comparison_count") != 138
                or g1.get("repeat_byte_identical") is not True
                or len(g1.get("mutations_rejected", {})) != 43
                or len(g1.get("refusal_controls", {}).get("checks", {})) != 50):
            raise ValueError("P116 local science or quality summary no longer matches the frozen qualification")
        if not isinstance(ci_runs, list) or len(ci_runs) != 6:
            raise ValueError("P116 terminal CI summary must contain six rows")
        names = [row.get("workflowName") if isinstance(row, dict) else None for row in ci_runs]
        if len(set(names)) != 6 or set(names) != WORKFLOWS:
            raise ValueError("P116 terminal CI workflow identities differ")
        if sum(row.get("conclusion") == "success" for row in ci_runs) != 4:
            raise ValueError("P116 terminal CI summary no longer has four successful workflows")
        failure_rows = {row.get("workflowName"): row for row in ci_runs if row.get("conclusion") == "failure"}
        if set(failure_rows) != {"controls", "fns-iron"}:
            raise ValueError("P116 terminal CI failures are not the two pinned data workflows")
        for name in WORKFLOWS:
            row = next(row for row in ci_runs if row["workflowName"] == name)
            if (row.get("headSha") != COMMIT or row.get("status") != "completed"
                    or type(row.get("databaseId")) is not int or row.get("url") !=
                    f"https://github.com/AvilaLabs/ACTINV/actions/runs/{row['databaseId']}"):
                raise ValueError(f"P116 terminal CI row identity differs: {name}")
        with open(VERDICT, "rb") as stream:
            persisted_verdict = stream.read()
        derived = check_p116_verdict.derive()
        derived_equal = _canonical(derived) == persisted_verdict
        rust_source_check = _current_rust_matches(g3, implementation)
        if (not derived_equal or derived.get("verdict") != "P116-FAIL"
                or derived.get("gates") != verdict.get("gates")):
            raise ValueError("unchanged P116 verdict derivation does not equal its persisted terminal FAIL")
        passed = artifacts_ok and archive_ok and control_sources_ok and rust_source_check and derived_equal
        if not passed:
            raise ValueError("P116 artifact/archive/source binding failed")
        return {"schema": "actinv-p116-history-verification-1", "pass": True,
            "phase": "P116", "verdict": "P116-FAIL", "checkpoint_commit": COMMIT,
            "protocol_sha256": PROTOCOL_SHA256, "implementation_sha256": IMPLEMENTATION_SHA256,
            "ci_runs_sha256": CI_SHA256, "terminal_verdict_sha256": VERDICT_SHA256,
            "p116_artifact_sha256": ARTIFACT_SHA256, "ci_archive_discovery_sha256": DISCOVERY_SHA256,
            "ci_archive_files_sha256": archive_hashes, "ci_archive_file_count": len(archive_hashes) + 1,
            "control_source_count": control_count, "control_sources_match_git": control_sources_ok,
            "all_100_rust_sources_match_git": rust_source_check, "local_science_pass": True,
            "failed_workflows": ["controls", "fns-iron"], "successful_workflow_count": 4,
            "derived_verdict_matches_persisted": derived_equal}
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, StopIteration) as error:
        return {"schema": "actinv-p116-history-verification-1", "pass": False,
            "phase": "P116", "error": f"{type(error).__name__}: {error}"}


def main() -> int:
    report = verify()
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report.get("pass") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
