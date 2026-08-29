#!/usr/bin/env python3
"""Verify the immutable P17 G4 -> G5 unseal authorization."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION = ROOT / "results" / "p17_unseal_authorization.json"
RESULT = ROOT / "results" / "p17_unseal_check.json"
PROTOCOL_SHA256 = "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f"
CHECKPOINT = "7c4c39bfb8b5de12dff644cd1b635dc9010f3d28"
WORKFLOW_ID = 33229661931
EVIDENCE_PATHS = {
    "g1_operators": "results/g1_p17_operators.json",
    "g2_identical_data": "results/g2_p17_identical_data.json",
    "g3_processing": "results/g3_p17_processing.json",
    "g4_diagnostics": "results/g4_p17_diagnostics.json",
    "g4_independent_check": "results/g4_p17_check.json",
    "cause_ledger": "results/p17_cause_ledger.json",
    "diagnostic_schema": "controls/fixtures/p17_diagnostic_schema.json",
    "cause_ledger_schema": "controls/fixtures/p17_cause_ledger_schema.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()
    authorization = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    checkpoint_object = git("cat-file", "-t", CHECKPOINT)
    ancestor = git("merge-base", "--is-ancestor", CHECKPOINT, "HEAD")
    subject = git("show", "-s", "--format=%s", CHECKPOINT)
    remote_contains = git("branch", "-r", "--contains", CHECKPOINT)
    checkpoint_hashes = {}
    checkpoint_files_present = True
    for role, path in EVIDENCE_PATHS.items():
        content = git("show", f"{CHECKPOINT}:{path}")
        checkpoint_files_present &= content.returncode == 0
        checkpoint_hashes[role] = (
            hashlib.sha256(content.stdout).hexdigest() if content.returncode == 0 else None
        )
    tracked_results = git("ls-tree", "-r", "--name-only", CHECKPOINT, "results")
    result_names = tracked_results.stdout.decode("utf-8", errors="strict").splitlines()
    held_out_absent = not any(
        name.endswith(
            (
                "g5_p17_heldout.json",
                "p17_heldout_rows.json",
                "p17_heldout_score.json",
            )
        )
        for name in result_names
    )
    expected_keys = {
        "schema",
        "protocol_sha256",
        "checkpoint",
        "workflow",
        "evidence",
        "held_out_families_authorized",
        "numeric_held_out_rows_read_before_authorization",
        "authorized",
        "pass",
    }
    workflow = authorization["workflow"]
    checks = {
        "authorization_fields_exact": set(authorization) == expected_keys,
        "protocol_bound": authorization["protocol_sha256"] == PROTOCOL_SHA256,
        "checkpoint_exists": checkpoint_object.returncode == 0
        and checkpoint_object.stdout.strip() == b"commit",
        "checkpoint_is_ancestor": ancestor.returncode == 0,
        "checkpoint_subject_matches": subject.stdout.decode().strip()
        == "Establish P17 diagnostic unseal checkpoint",
        "checkpoint_pushed_to_origin": remote_contains.returncode == 0
        and "origin/master" in remote_contains.stdout.decode(),
        "authorization_checkpoint_matches": authorization["checkpoint"]["commit"]
        == CHECKPOINT,
        "successful_clean_workflow_bound": workflow
        == {
            "database_id": WORKFLOW_ID,
            "name": "controls",
            "event": "push",
            "head_sha": CHECKPOINT,
            "created_at": "2026-08-29T02:43:39Z",
            "completed_at": "2026-08-29T02:50:05Z",
            "status": "completed",
            "conclusion": "success",
            "url": "https://github.com/AvilaLabs/ACTINV/actions/runs/33229661931",
        },
        "checkpoint_evidence_present": checkpoint_files_present,
        "checkpoint_evidence_hashes_match": authorization["evidence"]
        == checkpoint_hashes,
        "no_held_out_result_at_checkpoint": tracked_results.returncode == 0
        and held_out_absent,
        "all_three_families_authorized": set(
            authorization["held_out_families_authorized"]
        )
        == {"H1", "H2", "H3"},
        "no_numeric_row_read_before_authorization": authorization[
            "numeric_held_out_rows_read_before_authorization"
        ]
        is False,
        "authorization_verdict": authorization["authorized"] is True
        and authorization["pass"] is True,
    }
    output = {
        "schema": "actinv-p17-unseal-check-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "authorization_sha256": sha256(AUTHORIZATION),
        "checker_source_sha256": sha256(Path(__file__)),
        "checkpoint": CHECKPOINT,
        "workflow_database_id": WORKFLOW_ID,
        "checks": checks,
        "pass": all(checks.values()),
    }
    if not arguments.no_write:
        RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
