#!/usr/bin/env python3
"""Close P18 while preserving its complete, checker-derived P18-FAIL verdict."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P18_PROTOCOL.md"
AMENDMENT = ROOT / "protocols/ACTINV-P18_AMENDMENT_1.md"
SESSION = ROOT / "results/session_p18.json"
VERDICT = ROOT / "results/verdict_p18.json"

PROTOCOL_SHA256 = "002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09"
AMENDMENT_SHA256 = "8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0"
OPENING_COMMIT = "7a2d1f47b62155c0f7a22a4e0b9ec5d6e6730bc8"
SOURCE_EVIDENCE_COMMIT = "a460b6e4092d57ff228c6fb04ec41a12f575dd25"
WORKFLOW_RUN_ID = 33_257_767_713
WORKFLOW_JOB_ID = 99_114_368_015
CANONICAL_REPOSITORY = "https://github.com/AvilaLabs/ACTINV.git"

EVIDENCE_PATHS = {
    "family_seal": "results/p18_family_seal.json",
    "g0_seal": "results/g0_p18_seal.json",
    "g0_check": "results/g0_p18_check.json",
    "g1_identity": "results/g1_p18_state_identity.json",
    "g1_check": "results/g1_p18_check.json",
    "g2_audit": "results/g2_p18_corpus_audit.json",
    "g2_changed_identities": "results/g2_p18_changed_identities.json.gz",
    "g2_check": "results/g2_p18_check.json",
}
EXPECTED_EVIDENCE = {
    "family_seal": "fb2fd35b02aa4d9629d9740d638b7650f97e8ba9d8c4dcd70ee238c31c45dfed",
    "g0_seal": "e419e8eeb506c28440744e8a3ceb6922f1d066b8d673f534ce0567dc59f82af3",
    "g0_check": "8ea0b916657dbd5becba9116e3c1d11a17eb2cb0138e77d7fd0b49bb56df8f59",
    "g1_identity": "e7f51a6f23523956c349a634253ff86e0d986c482a5416caa26a3025156bbeb8",
    "g1_check": "5fc277fb1dfe91e9f97a8d49efece5bbb13c04128e1043a91fe839c2371b4ebc",
    "g2_audit": "e20fba865c36131f27bce7ac110957336c55d9c8455e79a8ddac0edde66df9cb",
    "g2_changed_identities": "606347ce4d12788451be3a4a3765bfa5305613631eb37ea5de30d163546e083e",
    "g2_check": "edf0164abca1e1740902d0acdc076da43018a512e8d67b6efd40f787a822e35f",
}
COMPONENT_CHECKERS = {
    "G0": "controls/check_g0_p18.py",
    "G1": "controls/check_g1_p18.py",
    "G2": "controls/check_g2_p18.py",
}
PRODUCTION_CHANGES = [
    "crates/actinv-data/src/activation.rs",
    "crates/actinv-data/src/bin/p18_corpus_probe.rs",
    "crates/actinv-data/src/builder.rs",
    "crates/actinv-data/src/covariance.rs",
    "crates/actinv-data/src/groups.rs",
]
UNAUTHORIZED_EVIDENCE = (
    "results/g3_p18_runtime.json",
    "results/g4_p18_diagnostic.json",
    "results/p18_unseal_authorization.json",
    "results/g5_p18_heldout.json",
    "results/g6_p18_artifacts.json",
)
MANIFEST_EXCLUDED = {
    "MANIFEST.sha256",
    "results/g6_p12_complete.json",
    "results/verdict_p12.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


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


def evidence_hashes() -> dict[str, str | None]:
    return {
        name: sha256(ROOT / relative) if (ROOT / relative).is_file() else None
        for name, relative in EVIDENCE_PATHS.items()
    }


def source_evidence_hashes() -> dict[str, str | None]:
    return {
        name: git_file_sha(SOURCE_EVIDENCE_COMMIT, relative)
        for name, relative in EVIDENCE_PATHS.items()
    }


def production_changes() -> list[str]:
    output = git_output(
        [
            "diff",
            "--name-only",
            f"{OPENING_COMMIT}..{SOURCE_EVIDENCE_COMMIT}",
            "--",
            "crates",
            "python",
            "data",
            "examples",
            "Cargo.toml",
            "Cargo.lock",
            "pyproject.toml",
        ]
    )
    return sorted(output.splitlines()) if output else []


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
    values = {name: run_component(relative) for name, relative in COMPONENT_CHECKERS.items()}
    return {
        "G0": values["G0"] is not None and values["G0"].get("pass") is True,
        "G1": values["G1"] is not None and values["G1"].get("pass") is True,
        "G2_failure_verified": values["G2"] is not None
        and values["G2"].get("pass") is True
        and values["G2"].get("audit_complete") is True
        and values["G2"].get("scientific_gate_pass") is False
        and values["G2"].get("verdict") == "P18-G2-FAIL",
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
        actual = (ROOT / "MANIFEST.sha256").read_text()
    except OSError:
        actual = ""
    return {
        "entries": len(paths),
        "paper_in_inventory": any(path.startswith("paper/") for path in paths),
        "byte_identical": actual == expected,
        "pass": actual == expected,
    }


def release_boundary() -> dict[str, Any]:
    cargo = tomllib.loads((ROOT / "Cargo.toml").read_text())
    python = tomllib.loads((ROOT / "python/pyproject.toml").read_text())
    version = cargo["workspace"]["package"]["version"]
    tags = git_output(["tag", "--list", "v1.1*"])
    return {
        "cargo_version": version,
        "python_version": python["project"]["version"],
        "v1_1_tags": [] if not tags else tags.splitlines(),
        "pass": version == "1.0.1"
        and python["project"]["version"] == "1.0.1"
        and not tags,
    }


def primary_gate_checks() -> dict[str, bool]:
    g0 = load(ROOT / EVIDENCE_PATHS["g0_seal"]) or {}
    g1 = load(ROOT / EVIDENCE_PATHS["g1_identity"]) or {}
    g2 = load(ROOT / EVIDENCE_PATHS["g2_audit"]) or {}
    return {
        "G0_pass": g0.get("schema") == "actinv-p18-g0-seal-control-1"
        and g0.get("pass") is True
        and g0.get("checks", {}).get("dependent_fields_absent") is True,
        "G1_pass": g1.get("schema") == "actinv-p18-g1-state-identity-1"
        and g1.get("pass") is True,
        "G2_failure": g2.get("schema") == "actinv-p18-g2-corpus-audit-1"
        and g2.get("audit_complete") is True
        and g2.get("gate_pass") is False
        and g2.get("pass") is False
        and g2.get("measurement_values_read") is False
        and g2.get("heldout_values_read") is False
        and g2.get("checks", {}).get("state_partial_conservation") is False,
    }


def session_record_valid(value: dict[str, Any], hashes: dict[str, str | None]) -> bool:
    workflow = value.get("workflow", {})
    audit = value.get("audit", {})
    gates = value.get("gates", {})
    boundary = value.get("release_boundary", {})
    failure = value.get("failure", {})
    successor = value.get("successor", {})
    return bool(
        value.get("schema") == "actinv-p18-session-1"
        and value.get("canonical_repository") == CANONICAL_REPOSITORY
        and value.get("protocol_sha256") == PROTOCOL_SHA256
        and value.get("amendment_sha256") == AMENDMENT_SHA256
        and value.get("opening_commit") == OPENING_COMMIT
        and value.get("source_evidence_commit") == SOURCE_EVIDENCE_COMMIT
        and workflow
        == {
            "name": "controls",
            "run_id": WORKFLOW_RUN_ID,
            "job_id": WORKFLOW_JOB_ID,
            "head_sha": SOURCE_EVIDENCE_COMMIT,
            "head_branch": "master",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-08-29T14:30:15Z",
            "completed_at": "2026-08-29T14:38:15Z",
            "substantive_steps": 42,
            "url": f"https://github.com/AvilaLabs/ACTINV/actions/runs/{WORKFLOW_RUN_ID}",
        }
        and value.get("evidence_sha256") == hashes == EXPECTED_EVIDENCE
        and audit
        == {
            "files": 11_400,
            "declarations": 1_810_499,
            "changed_identities": 45_320,
            "mapping_conflicts": 143,
            "comparison_violations": 2_647_615,
            "missing_totals": 0,
            "duplicate_or_descriptor_issues": 0,
            "measurement_values_read": False,
            "heldout_values_read": False,
        }
        and gates.get("G0_provenance_and_partition") is True
        and gates.get("G1_physical_product_identity") is True
        and gates.get("G2_corpus_conservation") is False
        and all(
            gates.get(name) == "NOT_AUTHORIZED"
            for name in (
                "G3_runtime_identity",
                "G4_diagnostic_and_unseal_authorization",
                "G5_heldout_score",
                "G6_full_rebuild_and_artifacts",
                "G7_release_closure",
            )
        )
        and value.get("production_changes_since_opening") == PRODUCTION_CHANGES
        and boundary
        == {
            "current_public_version": "1.0.1",
            "v1.1.0_authorized": False,
            "changed_default_released": False,
            "data_catalog_released": False,
            "existing_release_modified": False,
        }
        and failure
        == {
            "class": "frozen state-partial conservation and excitation-consistency thresholds failed",
            "threshold_relaxed": False,
            "row_or_file_excluded_after_result": False,
            "second_repair_attempted": False,
        }
        and successor.get("name") == "P18b"
        and successor.get("status") == "UNOPENED"
        and value.get("closure_record_valid") is True
        and value.get("phase_success") is False
        and value.get("verdict") == "P18-FAIL"
        and value.get("pass") is False
    )


def expected_verdict(hashes: dict[str, str | None]) -> dict[str, Any]:
    return {
        "schema": "actinv-p18-verdict-1",
        "closed": True,
        "closure_record_valid": True,
        "phase_success": False,
        "verdict": "P18-FAIL",
        "failure_class": "frozen state-partial conservation and excitation-consistency thresholds failed",
        "gates": {
            "G0": True,
            "G1": True,
            "G2": False,
            "G3": "NOT_AUTHORIZED",
            "G4": "NOT_AUTHORIZED",
            "G5": "NOT_AUTHORIZED",
            "G6": "NOT_AUTHORIZED",
            "G7": "NOT_AUTHORIZED",
        },
        "heldout_values_unsealed": False,
        "v1.1.0_authorized": False,
        "source_evidence_commit": SOURCE_EVIDENCE_COMMIT,
        "workflow_run_id": WORKFLOW_RUN_ID,
        "evidence_sha256": hashes,
    }


def mutation_plants(session: dict[str, Any], hashes: dict[str, str | None]) -> dict[str, bool]:
    plants: dict[str, tuple[dict[str, Any], dict[str, str | None]]] = {}
    for name in (
        "gate",
        "downstream_authorization",
        "heldout",
        "release",
        "evidence",
        "workflow",
        "inventory",
    ):
        plants[name] = (copy.deepcopy(session), copy.deepcopy(hashes))
    plants["gate"][0]["gates"]["G2_corpus_conservation"] = True
    plants["downstream_authorization"][0]["gates"]["G3_runtime_identity"] = True
    plants["heldout"][0]["audit"]["heldout_values_read"] = True
    plants["release"][0]["release_boundary"]["v1.1.0_authorized"] = True
    plants["evidence"][1]["g2_audit"] = "0" * 64
    plants["workflow"][0]["workflow"]["conclusion"] = "failure"
    plants["inventory"][0]["audit"]["files"] = 11_399
    return {
        name: not session_record_valid(planted, planted_hashes)
        for name, (planted, planted_hashes) in plants.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    require(sha256(PROTOCOL) == PROTOCOL_SHA256, "P18 protocol bytes changed")
    require(sha256(AMENDMENT) == AMENDMENT_SHA256, "P18 amendment bytes changed")
    hashes = evidence_hashes()
    require(hashes == EXPECTED_EVIDENCE, "P18 evidence hashes changed")
    require(source_evidence_hashes() == EXPECTED_EVIDENCE, "G2 commit does not bind the evidence")
    require(is_ancestor(OPENING_COMMIT, SOURCE_EVIDENCE_COMMIT), "P18 opening is not an ancestor")
    require(is_ancestor(SOURCE_EVIDENCE_COMMIT, "HEAD"), "G2 checkpoint is not an ancestor of HEAD")
    require(production_changes() == PRODUCTION_CHANGES, "P18 production-change inventory")

    session = load(SESSION)
    require(session is not None, "missing P18 session")
    require(session_record_valid(session, hashes), "invalid P18 session")
    plants = mutation_plants(session, hashes)
    components = component_checks()
    primary = primary_gate_checks()
    manifest = tracked_manifest_check()
    boundary = release_boundary()
    unauthorized_absent = all(not (ROOT / path).exists() for path in UNAUTHORIZED_EVIDENCE)

    expected = expected_verdict(hashes)
    if not arguments.no_write:
        VERDICT.write_text(json.dumps(expected, indent=1, sort_keys=True) + "\n")
    verdict = load(VERDICT)
    checks = {
        "protocol_and_amendment": True,
        "checkpoint_commit_and_workflow": True,
        "evidence_hashes": True,
        "G0_G1_pass_G2_fails": all(primary.values()),
        "independent_components": all(components.values()),
        "downstream_evidence_absent": unauthorized_absent,
        "measurement_and_heldout_quarantine": session["audit"]["measurement_values_read"] is False
        and session["audit"]["heldout_values_read"] is False,
        "release_boundary": boundary["pass"],
        "closure_mutation_plants": len(plants) == 7 and all(plants.values()),
        "deterministic_verdict": verdict == expected,
        "tracked_manifest": manifest["pass"],
    }
    output = {
        "schema": "actinv-p18-closure-check-1",
        "checks": checks,
        "primary_gates": primary,
        "component_checkers": components,
        "mutation_plants": plants,
        "release_boundary": boundary,
        "manifest": manifest,
        "closed": all(checks.values()),
        "phase_success": False,
        "verdict": "P18-FAIL",
        "pass": all(checks.values()),
    }
    displayed = {
        "schema": output["schema"],
        "checks": checks,
        "primary_gates": primary,
        "component_checkers": components,
        "mutation_plants": plants,
        "closed": output["closed"],
        "phase_success": False,
        "verdict": "P18-FAIL",
        "pass": output["pass"],
    }
    print(json.dumps(displayed, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
