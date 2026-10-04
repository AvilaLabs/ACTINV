#!/usr/bin/env python3
"""Preserve and qualify the terminal P116 exact-commit CI failure.

Coordinator-only, one-shot writer. This file is intentionally stored under
ignored target/ and must be reviewed before execution. It performs no network
requests and invokes no independent process runner; Git identity checks reuse
the already reviewed bounded check_p116._git_blob helper.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "2117f3b5df715ce832658f58250103789be845bb"
ARCHIVE_REL = "results/failures/p116_ci"
TERMINAL_REL = "target/p116-ci-terminal.json"
FAILED = {
    "controls": {
        1: ("target/p116-ci-controls-attempt1.json", "target/p116-ci-controls-attempt1.log"),
        2: ("target/p116-ci-controls-attempt2.json", "target/p116-ci-controls-attempt2.log"),
    },
    "fns-iron": {
        1: ("target/p116-ci-fns-iron-attempt1.json", "target/p116-ci-fns-iron-attempt1.log"),
        2: ("target/p116-ci-fns-iron-attempt2.json", "target/p116-ci-fns-iron-attempt2.log"),
    },
}
IAEA_HEADERS_REL = "target/p116-ci-iaea-headers.txt"
GITHUB_RUNS_REL = {
    "Build handbook", "desktop builds", "Build browser workbench",
    "fusion-isotope", "fns-iron", "controls",
}
EXPECTED_FAILURES = {"controls", "fns-iron"}
ARTIFACTS = {
    "results/g0_p116_twin_waste.json": ROOT / "results/g0_p116_twin_waste.json",
    "results/g1_p116_twin_waste.json": ROOT / "results/g1_p116_twin_waste.json",
    "results/g2_p116_twin_waste.json": ROOT / "results/g2_p116_twin_waste.json",
    "results/g3_p116_quality.json": ROOT / "results/g3_p116_quality.json",
}
IMPLEMENTATION_REL = "results/p116_implementation_commit.json"
CI_REL = "results/p116_ci_runs.json"
VERDICT_REL = "results/p116_verdict.json"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, ValueError) as error:
        raise ValueError(f"invalid {label}: {error}") from error


def _regular_input(relative: str) -> tuple[Path, bytes]:
    path = ROOT / relative
    resolved = path.resolve(strict=True)
    resolved.relative_to(ROOT.resolve(strict=True))
    cursor = ROOT
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink in input path: {relative}")
    if not path.is_file():
        raise ValueError(f"not a regular input file: {relative}")
    return path, path.read_bytes()


def _validate_failed_attempts(terminal: list, raw_inputs: dict[str, bytes]) -> tuple[list[dict], dict[str, Any]]:
    if len(terminal) != 6 or any(not isinstance(row, dict) for row in terminal):
        raise ValueError("terminal GitHub summary must contain exactly six run objects")
    names = [row.get("workflowName") for row in terminal]
    if (len(set(names)) != 6 or set(names) != GITHUB_RUNS_REL
            or any(type(row.get("databaseId")) is not int or row.get("databaseId") <= 0
                   or row.get("headSha") != COMMIT or row.get("status") != "completed"
                   or row.get("url") != f"https://github.com/AvilaLabs/ACTINV/actions/runs/{row['databaseId']}"
                   for row in terminal)):
        raise ValueError("terminal run identities, commit, URLs or completion states differ")
    failures = {row["workflowName"] for row in terminal if row.get("conclusion") != "success"}
    if failures != EXPECTED_FAILURES or sum(row.get("conclusion") == "success" for row in terminal) != 4:
        raise ValueError("expected precisely four successes and controls/fns-iron failures")
    if any(row.get("conclusion") != "failure" for row in terminal
           if row["workflowName"] in EXPECTED_FAILURES):
        raise ValueError("failed workflows must have literal failure conclusions")

    by_name = {row["workflowName"]: row for row in terminal}
    prior_refs: dict[str, Any] = {}
    for name, attempts in FAILED.items():
        expected_run = by_name[name]
        entries = []
        for attempt in (1, 2):
            metadata_rel, log_rel = attempts[attempt]
            metadata = _json_bytes(raw_inputs[metadata_rel], metadata_rel)
            log = raw_inputs[log_rel]
            if (not isinstance(metadata, dict) or type(metadata.get("id")) is not int
                    or metadata.get("id") != expected_run["databaseId"]
                    or metadata.get("name") != name or metadata.get("head_sha") != COMMIT
                    or type(metadata.get("run_attempt")) is not int
                    or metadata.get("run_attempt") != attempt
                    or metadata.get("status") != "completed"
                    or metadata.get("conclusion") != "failure"
                    or metadata.get("html_url") != expected_run["url"]):
                raise ValueError(f"{name} attempt {attempt} metadata does not bind to terminal run")
            text = log.decode("utf-8", errors="replace").lower()
            if "403" not in text:
                raise ValueError(f"{name} attempt {attempt} log does not evidence HTTP 403")
            if name == "controls" and ("requested url returned error: 403" not in text
                                        or "exit code 22" not in text):
                raise ValueError("controls log does not show the expected pinned-download 403 failure")
            if name == "fns-iron" and ("http status: 403" not in text or "exit code 1" not in text):
                raise ValueError("fns-iron log does not show the expected pinned-download 403 failure")
            entries.append({"attempt": attempt, "metadata_path": f"metadata/{Path(metadata_rel).name}",
                "metadata_sha256": _sha(raw_inputs[metadata_rel]),
                "log_path": f"logs/{Path(log_rel).name}", "log_sha256": _sha(log),
                "status": metadata["status"], "conclusion": metadata["conclusion"]})
        prior_refs[name] = entries

    headers = raw_inputs[IAEA_HEADERS_REL].decode("utf-8", errors="replace").lower()
    if "403" not in headers or "cf-mitigated: challenge" not in headers:
        raise ValueError("pinned IAEA endpoint header evidence is missing the HTTP challenge")

    runs = []
    for summary in terminal:
        run = dict(summary)
        if run["workflowName"] in prior_refs:
            run["attempt_history"] = prior_refs[run["workflowName"]]
        runs.append(run)
    return runs, prior_refs


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_replace(path: Path, content: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", COMMIT):
        raise ValueError("implementation identity is malformed")
    archive = ROOT / ARCHIVE_REL
    implementation_path = ROOT / IMPLEMENTATION_REL
    ci_path = ROOT / CI_REL
    verdict_path = ROOT / VERDICT_REL
    for destination in (archive, implementation_path, ci_path):
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"refusing to overwrite {destination.relative_to(ROOT)}")

    raw_relatives = [TERMINAL_REL]
    for attempts in FAILED.values():
        for metadata_rel, log_rel in attempts.values():
            raw_relatives.extend((metadata_rel, log_rel))
    raw_relatives.append(IAEA_HEADERS_REL)
    raw_inputs = {relative: _regular_input(relative)[1] for relative in raw_relatives}
    terminal = _json_bytes(raw_inputs[TERMINAL_REL], TERMINAL_REL)
    if not isinstance(terminal, list):
        raise ValueError("terminal GitHub result is not a list")
    runs, prior_refs = _validate_failed_attempts(terminal, raw_inputs)

    # This coordinator script lives under ignored target/, so explicitly load
    # the reviewed project checkers from controls rather than depending on the
    # caller's working-directory-specific sys.path.
    controls_path = ROOT / "controls"
    if str(controls_path) not in sys.path:
        sys.path.insert(0, str(controls_path))
    import check_p116
    import check_p116_verdict

    original_verdict_path, original_verdict_raw = _regular_input(VERDICT_REL)
    before = {relative: path.read_bytes() for relative, path in ARTIFACTS.items()}
    artifact_sha = {}
    for relative, path in ARTIFACTS.items():
        raw = before[relative]
        if check_p116._git_blob(COMMIT, relative) != raw:
            raise ValueError(f"current scientific artifact differs from implementation commit: {relative}")
        artifact_sha[relative] = _sha(raw)

    prior_derived = check_p116_verdict.derive()
    if (prior_derived.get("verdict") != "P116-LOCAL-PASS"
            or _canonical(prior_derived) != original_verdict_raw):
        raise ValueError("live verdict is not the exact currently derived LOCAL-PASS")
    prior_verdict_sha = _sha(original_verdict_raw)
    record = {"schema": "actinv-p116-implementation-1", "commit_sha": COMMIT}
    for index, (relative, _path) in enumerate(ARTIFACTS.items()):
        record[f"g{index}_sha256"] = artifact_sha[relative]
    if not check_p116_verdict._implementation_record_ok(record):
        raise ValueError("implementation record does not bind exact current Git blobs")
    if check_p116_verdict._ci_state(True, record, runs, True) != (False, False):
        raise ValueError("CI record is not the exact completed four-pass/two-failure state")

    script_path = Path(__file__).resolve(strict=True)
    script_relative = script_path.relative_to(ROOT).as_posix()
    archive_blobs: dict[str, bytes] = {
        f"metadata/{Path(TERMINAL_REL).name}": raw_inputs[TERMINAL_REL],
        "pre_ci_local_verdict.json": original_verdict_raw,
        "metadata/p116-ci-iaea-headers.txt": raw_inputs[IAEA_HEADERS_REL],
        "source/p116-preserve-ci-failure.py": script_path.read_bytes(),
    }
    for attempts in FAILED.values():
        for metadata_rel, log_rel in attempts.values():
            archive_blobs[f"metadata/{Path(metadata_rel).name}"] = raw_inputs[metadata_rel]
            archive_blobs[f"logs/{Path(log_rel).name}"] = raw_inputs[log_rel]
    files_sha = {relative: _sha(content) for relative, content in sorted(archive_blobs.items())}
    discovery = {"schema": "actinv-p116-ci-failure-archive-1", "phase": "P116",
        "implementation_commit": COMMIT, "terminal_runs_source": TERMINAL_REL,
        "terminal_runs_sha256": _sha(raw_inputs[TERMINAL_REL]),
        "source_inputs_sha256": {relative: _sha(content) for relative, content in sorted(raw_inputs.items())},
        "attempt_history": prior_refs, "attempt_count": 4, "terminal_workflow_count": 6,
        "terminal_completed_count": 6, "terminal_success_count": 4, "terminal_failure_count": 2,
        "failed_workflows": sorted(EXPECTED_FAILURES), "failure_reason": "pinned IAEA archive HTTP 403",
        "artifact_sha256": artifact_sha, "pre_ci_local_verdict_sha256": prior_verdict_sha,
        "preservation_script_source": script_relative, "files_sha256": files_sha}
    discovery_bytes = _canonical(discovery)
    archive_blobs["discovery.json"] = discovery_bytes

    # Create the immutable evidence bundle and result records only after every
    # input, artifact and expected status has been validated.
    archive.mkdir(parents=True, exist_ok=False)
    for relative, content in archive_blobs.items():
        _write_new(archive / relative, content)
    _write_new(implementation_path, _canonical(record))
    _write_new(ci_path, _canonical(runs))

    if not check_p116_verdict._implementation_record_ok(record):
        raise ValueError("written implementation record failed its unchanged verifier")
    if check_p116_verdict.read(implementation_path) != record or check_p116_verdict.read(ci_path) != runs:
        raise ValueError("written implementation/CI records differ from validated inputs")
    terminal_verdict = check_p116_verdict.derive()
    if terminal_verdict.get("verdict") != "P116-FAIL":
        raise ValueError(f"expected terminal P116-FAIL, got {terminal_verdict.get('verdict')!r}")
    if not (terminal_verdict.get("gates", {}).get("G3_CI") == "FAIL"
            and terminal_verdict.get("gates", {}).get("CI_evidence_consistent") == "FAIL"):
        raise ValueError("terminal disposition did not explicitly retain the failed CI evidence")
    expected_gates = dict(prior_derived["gates"])
    expected_gates.update({"G3_CI": "FAIL", "CI_evidence_consistent": "FAIL"})
    if terminal_verdict.get("gates") != expected_gates:
        raise ValueError("a scientific or source gate changed during CI preservation")
    final_verdict_bytes = _canonical(terminal_verdict)
    if verdict_path.read_bytes() != original_verdict_raw:
        raise ValueError("live verdict changed after preflight; refusing replacement")
    _atomic_replace(verdict_path, final_verdict_bytes)

    if any(path.read_bytes() != before[relative] for relative, path in ARTIFACTS.items()):
        raise RuntimeError("G0-G3 evidence changed during CI failure preservation")
    actual_files = {path.relative_to(archive).as_posix()
                    for path in archive.rglob("*") if path.is_file()}
    if actual_files != set(archive_blobs) or any(
            (archive / relative).read_bytes() != raw
            for relative, raw in archive_blobs.items()):
        raise RuntimeError("retained archive differs from the validated exact bytes")
    print(json.dumps({"phase":"P116", "verdict":"P116-FAIL",
        "implementation_commit":COMMIT, "failed_workflows":sorted(EXPECTED_FAILURES),
        "archive":ARCHIVE_REL, "archive_file_count":len(archive_blobs),
        "pre_ci_local_verdict_sha256":prior_verdict_sha,
        "terminal_verdict_sha256":_sha(final_verdict_bytes)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
