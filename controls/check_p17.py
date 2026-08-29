#!/usr/bin/env python3
"""Independently close P17 while preserving its frozen P17-FAIL verdict.

The phase failed because its post-unseal assumptions were falsified, not
because the evidence could not be reproduced.  This checker therefore exits
successfully only when the failure, the complete diagnostic record, the
append-only cause ledger, the clean evidence workflow, and the repository
inventory all agree with the closure session.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols" / "ACTINV-P17_PROTOCOL.md"
AMENDMENT = ROOT / "protocols" / "ACTINV-P17_AMENDMENT_1.md"
SESSION = RESULTS / "session_p17.json"
VERDICT = RESULTS / "verdict_p17.json"

PROTOCOL_SHA256 = "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f"
AMENDMENT_SHA256 = "e78c84d9f80c9bc6b7f0e79050206991515d283f43deeabd32f42c325f53581e"
OPENING_COMMIT = "f9e6a5c8faf15f1748f1b2c4683889ea8a631c9d"
UNSEAL_COMMIT = "b7039e2bff4346d9c39c283fcd2aabd768a8e628"
CANONICAL_REPOSITORY = "https://github.com/AvilaLabs/ACTINV.git"

EVIDENCE_PATHS = {
    "g0_seal": "results/g0_p17_seal.json",
    "g1_operators": "results/g1_p17_operators.json",
    "g2_identical_data": "results/g2_p17_identical_data.json",
    "g3_processing": "results/g3_p17_processing.json",
    "g4_diagnostics": "results/g4_p17_diagnostics.json",
    "g4_check": "results/g4_p17_check.json",
    "unseal_authorization": "results/p17_unseal_authorization.json",
    "unseal_check": "results/p17_unseal_check.json",
    "g5_heldout": "results/g5_p17_heldout.json",
    "g5_check": "results/g5_p17_check.json",
    "cause_ledger_open": "results/p17_cause_ledger.json",
    "cause_ledger_heldout": "results/p17_cause_ledger_g5.json",
}
COMPONENT_CHECKERS = {
    "G4": "controls/check_g4_p17.py",
    "unseal": "controls/check_p17_unseal.py",
    "G5_G6": "controls/check_g5_p17.py",
}
MANIFEST_EXCLUDED = {
    "MANIFEST.sha256",
    "results/g6_p12_complete.json",
    "results/verdict_p12.json",
}
PRODUCTION_PREFIXES = ("crates/", "python/", "data/", "examples/")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def git_output(arguments: list[str]) -> str | None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def git_file_sha(commit: str, relative: str) -> str | None:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return hashlib.sha256(completed.stdout).hexdigest() if completed.returncode == 0 else None


def is_ancestor(ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def run_component(relative: str) -> dict[str, Any] | None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / relative), "--no-write"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def component_checks() -> dict[str, bool]:
    return {
        name: (value := run_component(relative)) is not None and value.get("pass") is True
        for name, relative in COMPONENT_CHECKERS.items()
    }


def tracked_manifest_check() -> dict[str, Any]:
    inventory = git_output(["ls-files", "--cached", "-z"])
    if inventory is None:
        return {"pass": False, "reason": "git inventory unavailable"}
    paths = sorted(
        path for path in inventory.split("\0") if path and path not in MANIFEST_EXCLUDED
    )
    expected = "".join(f"{sha256(ROOT / path)}  ./{path}\n" for path in paths)
    try:
        actual = (ROOT / "MANIFEST.sha256").read_text(encoding="utf-8")
    except OSError:
        actual = ""
    return {
        "entries": len(paths),
        "excluded": sorted(MANIFEST_EXCLUDED),
        "paper_in_inventory": any(path.startswith("paper/") for path in paths),
        "byte_identical": actual == expected,
        "pass": actual == expected,
    }


def protocol_check() -> dict[str, Any]:
    protocol = sha256(PROTOCOL) if PROTOCOL.is_file() else None
    amendment = sha256(AMENDMENT) if AMENDMENT.is_file() else None
    return {
        "protocol_sha256": protocol,
        "amendment_sha256": amendment,
        "pass": protocol == PROTOCOL_SHA256 and amendment == AMENDMENT_SHA256,
    }


def basic_gate_checks(values: dict[str, dict[str, Any] | None]) -> dict[str, bool]:
    g0 = values["g0_seal"] or {}
    g1 = values["g1_operators"] or {}
    g2 = values["g2_identical_data"] or {}
    g3 = values["g3_processing"] or {}
    source = g0.get("source", {})
    return {
        "G0": g0.get("schema") == "actinv-p17-seal-1"
        and g0.get("pass") is True
        and source.get("opening_commit") == OPENING_COMMIT
        and source.get("production_paths_changed_since_opening") == []
        and source.get("protocol", {}).get("actual_sha256") == PROTOCOL_SHA256
        and source.get("protocol", {}).get("expected_sha256") == PROTOCOL_SHA256,
        "G1": g1.get("schema") == "actinv-p17-operators-1" and g1.get("pass") is True,
        "G2": g2.get("schema") == "actinv-p17-g2-identical-data-1"
        and g2.get("protocol_sha256") == PROTOCOL_SHA256
        and g2.get("pass") is True,
        "G3": g3.get("schema") == "actinv-p17-g3-processing-1"
        and g3.get("protocol_sha256") == PROTOCOL_SHA256
        and g3.get("pass") is True,
    }


def heldout_record_valid(
    heldout: dict[str, Any], ledger: dict[str, Any], checked: dict[str, Any]
) -> bool:
    failed = heldout.get("frozen_gate_evidence", {}).get("failed_conditions", {})
    post = heldout.get("post_failure_checks", {})
    rows = heldout.get("row_counts", {})
    entries = ledger.get("entries", [])
    return bool(
        heldout.get("schema") == "actinv-p17-heldout-failure-1"
        and heldout.get("protocol_sha256") == PROTOCOL_SHA256
        and heldout.get("amendment_sha256") == AMENDMENT_SHA256
        and heldout.get("unseal_commit") == UNSEAL_COMMIT
        and heldout.get("verdict") == "P17-FAIL"
        and heldout.get("pass") is False
        and heldout.get("post_failure_diagnostics_complete") is True
        and rows
        == {
            "H1": 40,
            "H2": 33,
            "H3": 21,
            "post_failure_production_material_mismatches": 3,
            "source_total": 94,
        }
        and set(failed)
        == {
            "amendment_1_bare_means_unshielded_for_capture_foils",
            "amendment_1_mapping_covers_publication_Ag109gm_alias",
            "amendment_1_uniform_960s_EOI_reproduces_observable",
            "no_second_post_unseal_repair_needed",
        }
        and all(value is False for value in failed.values())
        and len(post) == 5
        and all(value is True for value in post.values())
        and ledger.get("schema") == "actinv-p17-cause-ledger-segment-1"
        and ledger.get("protocol_sha256") == PROTOCOL_SHA256
        and ledger.get("amendment_sha256") == AMENDMENT_SHA256
        and ledger.get("append_only") is True
        and len(entries) == 3
        and [entry.get("sequence") for entry in entries] == [1, 2, 3]
        and len({entry.get("mismatch_key") for entry in entries}) == 3
        and checked.get("schema") == "actinv-p17-heldout-check-1"
        and checked.get("pass") is True
        and checked.get("summary", {}).get("verdict") == "P17-FAIL"
        and checked.get("result_sha256") == sha256(RESULTS / "g5_p17_heldout.json")
        and checked.get("ledger_sha256") == sha256(RESULTS / "p17_cause_ledger_g5.json")
    )


def mutation_checks(
    heldout: dict[str, Any], ledger: dict[str, Any], checked: dict[str, Any]
) -> dict[str, bool]:
    plants: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for name in ("verdict", "phase_pass", "row_count", "failure_condition", "ledger", "hash"):
        plants[name] = (copy.deepcopy(heldout), copy.deepcopy(ledger), copy.deepcopy(checked))
    plants["verdict"][0]["verdict"] = "P17-CONDITIONAL"
    plants["phase_pass"][0]["pass"] = True
    plants["row_count"][0]["row_counts"]["source_total"] = 93
    plants["failure_condition"][0]["frozen_gate_evidence"]["failed_conditions"][
        "no_second_post_unseal_repair_needed"
    ] = True
    plants["ledger"][1]["entries"].pop()
    plants["hash"][2]["result_sha256"] = "0" * 64
    return {
        name: not heldout_record_valid(planted, planted_ledger, planted_check)
        for name, (planted, planted_ledger, planted_check) in plants.items()
    }


def evidence_hashes() -> dict[str, str | None]:
    return {
        name: sha256(ROOT / relative) if (ROOT / relative).is_file() else None
        for name, relative in EVIDENCE_PATHS.items()
    }


def source_production_changes(commit: str) -> list[str]:
    output = git_output(["diff", "--name-only", f"{OPENING_COMMIT}..{commit}"])
    if output is None:
        return ["<git-diff-unavailable>"]
    return sorted(
        path
        for path in output.splitlines()
        if path.startswith(PRODUCTION_PREFIXES)
        or path in {"Cargo.toml", "Cargo.lock", "pyproject.toml"}
    )


def session_check(current_hashes: dict[str, str | None]) -> dict[str, Any]:
    value = load(SESSION)
    if value is None:
        return {"present": False, "pass": False}
    commit = value.get("source_evidence_commit")
    workflow = value.get("workflow", {})
    valid_commit = isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) is not None
    commit_hashes = {
        name: git_file_sha(commit, relative) if valid_commit else None
        for name, relative in EVIDENCE_PATHS.items()
    }
    checks = {
        "schema": value.get("schema") == "actinv-p17-session-1",
        "repository": value.get("canonical_repository") == CANONICAL_REPOSITORY,
        "protocol": value.get("protocol_sha256") == PROTOCOL_SHA256
        and value.get("amendment_sha256") == AMENDMENT_SHA256,
        "commits": valid_commit
        and value.get("opening_commit") == OPENING_COMMIT
        and value.get("unseal_commit") == UNSEAL_COMMIT
        and is_ancestor(OPENING_COMMIT, commit)
        and is_ancestor(UNSEAL_COMMIT, commit)
        and is_ancestor(commit, "HEAD"),
        "workflow": isinstance(workflow, dict)
        and workflow.get("name") == "controls"
        and workflow.get("head_sha") == commit
        and workflow.get("head_branch") in {"master", "main"}
        and workflow.get("event") == "push"
        and workflow.get("status") == "completed"
        and workflow.get("conclusion") == "success"
        and isinstance(workflow.get("run_id"), int)
        and workflow.get("run_id", 0) > 0
        and isinstance(workflow.get("url"), str)
        and workflow["url"].startswith("https://github.com/AvilaLabs/ACTINV/actions/runs/"),
        "evidence_hashes": value.get("evidence_sha256") == current_hashes,
        "checkpoint_evidence": commit_hashes == current_hashes,
        "no_production_change": valid_commit and source_production_changes(commit) == [],
        "failure_preserved": value.get("verdict") == "P17-FAIL"
        and value.get("phase_success") is False
        and value.get("closure_record_valid") is True,
    }
    return {
        "present": True,
        "checks": checks,
        "source_commit_evidence_sha256": commit_hashes,
        "production_changes": source_production_changes(commit) if valid_commit else None,
        "pass": all(checks.values()),
    }


def expected_verdict(hashes: dict[str, str | None]) -> dict[str, Any]:
    return {
        "schema": "actinv-p17-verdict-1",
        "closed": True,
        "closure_record_valid": True,
        "phase_success": False,
        "verdict": "P17-FAIL",
        "failure_class": "frozen post-unseal assumptions falsified; second repair required",
        "gates": {
            "G0": True,
            "G1": True,
            "G2": True,
            "G3": True,
            "G4": True,
            "G5": False,
            "G6": True,
            "G7": True,
        },
        "evidence_sha256": hashes,
    }


def verdict_check(hashes: dict[str, str | None]) -> bool:
    value = load(VERDICT)
    return value == expected_verdict(hashes)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true", help="do not replace the deterministic verdict")
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()

    values = {name: load(ROOT / relative) for name, relative in EVIDENCE_PATHS.items()}
    protocol = protocol_check()
    basics = basic_gate_checks(values)
    components = component_checks()
    heldout = values["g5_heldout"] or {}
    ledger = values["cause_ledger_heldout"] or {}
    checked = values["g5_check"] or {}
    heldout_valid = heldout_record_valid(heldout, ledger, checked)
    plants = mutation_checks(heldout, ledger, checked) if heldout and ledger and checked else {}
    hashes = evidence_hashes()

    if not arguments.no_write:
        VERDICT.write_text(
            json.dumps(expected_verdict(hashes), indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    session = session_check(hashes)
    manifest = tracked_manifest_check()
    checks = {
        "protocol_and_amendment": protocol["pass"],
        "G0_through_G3": all(basics.values()),
        "independent_components": all(components.values()),
        "expected_G5_failure": heldout_valid,
        "closure_mutation_plants": len(plants) == 6 and all(plants.values()),
        "session": session["pass"],
        "deterministic_verdict": verdict_check(hashes),
        "tracked_manifest": manifest["pass"],
    }
    output = {
        "schema": "actinv-p17-closure-check-1",
        "protocol": protocol,
        "basic_gates": basics,
        "component_checkers": components,
        "heldout_failure_valid": heldout_valid,
        "mutation_plants": plants,
        "evidence_sha256": hashes,
        "session": session,
        "manifest": manifest,
        "checks": checks,
        "closed": all(checks.values()),
        "phase_success": False,
        "verdict": "P17-FAIL",
        "pass": all(checks.values()),
    }
    displayed = output if arguments.verbose else {
        "schema": output["schema"],
        "checks": checks,
        "basic_gates": basics,
        "component_checkers": components,
        "mutation_plants": plants,
        "closed": output["closed"],
        "phase_success": False,
        "verdict": "P17-FAIL",
        "pass": output["pass"],
    }
    print(json.dumps(displayed, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
