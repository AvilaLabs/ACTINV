#!/usr/bin/env python3
"""Close P18b while preserving its complete, checker-derived P18b-FAIL verdict."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = Path(__file__).resolve().parent
sys.path.insert(0, str(CONTROLS))
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
SESSION = ROOT / "results/session_p18b.json"
VERDICT = ROOT / "results/verdict_p18b.json"

PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
OPENING_COMMIT = "bf540efc3cd9525d17f69a525ab6732c648bfe93"
G0_CHECKPOINT = "8745787c4e7ab8dad4fdeaed5f8cc0309735bb55"
G1_CHECKPOINT = "b31c759e12a185e04801105611a0b2618ee9dfd5"
G2_CHECKPOINT = "6a513021e9a224c29ad239a8c4a3777ee8a64d0b"
G3_CHECKPOINT = "33e529928b47968637cc21d4accd5125e2bf45e8"
SOURCE_EVIDENCE_COMMIT = "0150b87ae5da8944d5d11aba04ef69d3afe97117"
WORKFLOW_RUN_ID = 34_696_870_664
WORKFLOW_JOB_ID = 103_561_739_621
CANONICAL_REPOSITORY = "https://github.com/AvilaLabs/ACTINV.git"

# Tracked evidence; hashes are verified against the working tree at HEAD.
EVIDENCE_PATHS = {
    "g0_check": "results/g0_p18b_check.json",
    "g1_check": "results/g1_p18b_check.json",
    "g2_check": "results/g2_p18b_check.json",
    "g3_check": "results/g3_p18b_check.json",
    "g4_diagnostics": "results/g4_p18b_diagnostics.json",
    "g5_heldout": "results/g5_p18b_heldout.json",
    "g5_run1_coverage_limited": "results/g5_p18b_heldout_run1_coverage_limited.json",
    "p16_performance": "results/p16_performance.json",
}
EXPECTED_EVIDENCE = {
    "g0_check": "5f15c85a3460648514ee24e39954c8a0f9e10c274fac04074a3b5e552129551d",
    "g1_check": "650c17c22f88c0218c99444d2c75a2abdc902e2c4dda5ddc757e56ec9fa40d0d",
    "g2_check": "fb05923e1ab9535ffef61da4029cd07ddbfc0fecd3211f2ae98f8d2fffe9735d",
    "g3_check": "da34b2a66159e9d3f5e326418fec3a59cf31eb612c1de17cf5d79e4d3bad887b",
    "g4_diagnostics": "2506def240099dcc87a091210f17d8a04b408a2292c38b551c5cec889935a42f",
    "g5_heldout": "3cf4c369c1d1b95cca33f436948d49d71740789638c354988d253dcc697d51ad",
    "g5_run1_coverage_limited": "a0d6412e78f163f7cd272957306110efe83da2519cac5ff1c8a43fa597ab9bf8",
    "p16_performance": "fec0d31710c7fd8babac2f6d5baa1704274fcf5e5deca7337e27f953a30f8a1b",
}
# Evidence that must already exist at the authorization commit; G5 outputs
# must NOT (held-out values were sealed until the green G4 workflow).
PRE_UNSEAL_EVIDENCE = (
    "g0_check",
    "g1_check",
    "g2_check",
    "g3_check",
    "g4_diagnostics",
    "p16_performance",
)
POST_UNSEAL_EVIDENCE = ("g5_heldout", "g5_run1_coverage_limited")
# Local-only build attestation: verified when the untracked file is present.
LOCAL_EVIDENCE = {
    "g4_build_report": (
        "target/g4-p18b/build_report.json",
        "65b7a062cb954ac0aac3e27fb92428cdb660a541e152e2adf89a554b52edfc99",
    ),
}
COMPONENT_CHECKERS = {
    "G0": "controls/check_g0_p18b.py",
    "G1": "controls/check_g1_p18b.py",
    "G2": "controls/check_g2_p18b.py",
    "G3": "controls/check_g3_p18b.py",
    "G4": "controls/check_g4_p18b.py",
}
UNAUTHORIZED_EVIDENCE = (
    "results/g6_p18b_release.json",
    "results/g6_p18b_artifacts.json",
    "results/g7_p18b_closure.json",
    "results/p18b_release_authorization.json",
)
MANIFEST_EXCLUDED = {
    "MANIFEST.sha256",
    "results/g6_p12_complete.json",
    "results/verdict_p12.json",
}
GATE_NAMES = (
    "G0_provenance_and_partition",
    "G1_decimal_and_checker_oracle",
    "G2_corpus_classification",
    "G3_runtime_conservation",
    "G4_diagnostic_and_unseal_authorization",
    "G5_heldout_score",
    "G6_full_rebuild_and_artifacts",
    "G7_release_closure",
)
FAILURE_CLASS = (
    "held-out stratum nonregression gate failed: proton median and within-30pct"
    " regressed, alpha p90 regressed, and deuteron plus alpha strata had"
    " insufficient candidate coverage to demonstrate nonregression under genuine"
    " TENDL-2025 state-partial source defects"
)
PRODUCTION_CHANGES = [
    "Cargo.lock",
    "Cargo.toml",
    "crates/actinv-cli/Cargo.toml",
    "crates/actinv-cli/data/iron-example.json",
    "crates/actinv-cli/src/command.rs",
    "crates/actinv-cli/src/lib.rs",
    "crates/actinv-cli/src/workflow.rs",
    "crates/actinv-cli/tests/validation.rs",
    "crates/actinv-core/Cargo.toml",
    "crates/actinv-core/src/bin/fission_probe.rs",
    "crates/actinv-core/src/chain.rs",
    "crates/actinv-core/src/damage.rs",
    "crates/actinv-core/src/lib.rs",
    "crates/actinv-core/src/mesh.rs",
    "crates/actinv-core/src/prune.rs",
    "crates/actinv-core/src/reverse.rs",
    "crates/actinv-core/src/run.rs",
    "crates/actinv-core/src/shielding.rs",
    "crates/actinv-core/src/spec.rs",
    "crates/actinv-data/Cargo.toml",
    "crates/actinv-data/src/bin/p18b_corpus_probe.rs",
    "crates/actinv-data/src/bin/p18b_oracle_probe.rs",
    "crates/actinv-data/src/builder.rs",
    "crates/actinv-data/src/lib.rs",
    "crates/actinv-data/src/library.rs",
    "crates/actinv-data/src/prepared.rs",
    "crates/actinv-data/src/resonance.rs",
    "crates/actinv-data/src/shielding.rs",
    "crates/actinv-gui/Cargo.toml",
    "crates/actinv-gui/assets/avila-labs-logo.png",
    "crates/actinv-gui/build.rs",
    "crates/actinv-gui/src/app.rs",
    "crates/actinv-gui/src/capture.rs",
    "crates/actinv-gui/src/main.rs",
    "crates/actinv-gui/src/model.rs",
    "crates/actinv-gui/src/options.rs",
    "crates/actinv-gui/src/smoke.rs",
    "crates/actinv-gui/src/tour.rs",
    "crates/actinv-gui/src/transport.rs",
    "crates/actinv-gui/src/visuals.rs",
    "crates/actinv-gui/src/worker.rs",
    "examples/README.md",
    "examples/damage_demo.json",
    "examples/damage_table_demo.json",
    "examples/feed_removal_demo.json",
    "examples/mesh_demo.json",
    "examples/mesh_flux.ndjson",
    "examples/pulsed_demo.json",
    "examples/reverse_demo_measurements.json",
    "examples/reverse_demo_problem.json",
    "examples/shielding_demo.json",
    "python/README.md",
    "python/src/lib.rs",
    "python/src/objects.py",
]


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


def pre_unseal_binding() -> bool:
    return all(
        git_file_sha(SOURCE_EVIDENCE_COMMIT, EVIDENCE_PATHS[name])
        == EXPECTED_EVIDENCE[name]
        for name in PRE_UNSEAL_EVIDENCE
    )


def post_unseal_ordering() -> bool:
    return all(
        git_file_sha(SOURCE_EVIDENCE_COMMIT, EVIDENCE_PATHS[name]) is None
        for name in POST_UNSEAL_EVIDENCE
    )


def local_evidence_check() -> dict[str, Any]:
    present = {
        name: sha256(ROOT / relative) == expected
        for name, (relative, expected) in LOCAL_EVIDENCE.items()
        if (ROOT / relative).is_file()
    }
    return {"verified": present, "pass": all(present.values())}


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
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        marker = completed.stdout.strip().splitlines()
        if marker and marker[-1].endswith("-PASS"):
            return {"pass": True, "marker": marker[-1]}
        return None
    return value if isinstance(value, dict) else None


SEAL = ROOT / "results/p18_family_seal.json"


def calculated_ratio(kind: str, sigma_g: float, sigma_m: float, sigma_t: float):
    """Independent re-derivation of the frozen printed-form IR ratio."""
    if kind in {"G+M", "M/G", "G/M"}:
        total = sigma_g + sigma_m
    elif kind in {"M+T", "G+T", "M/T", "G/T"}:
        total = sigma_t
    else:
        return None
    if total <= 0.0 or not math.isfinite(total):
        return None
    return sigma_m / total


def g5_evaluate(report: dict) -> list[str]:
    """Independently re-derive the held-out report's arithmetic and gate
    verdicts without importing the frozen scorer modules."""
    failures = []
    checks = report.get("checks", {})
    if report.get("schema") != "actinv-g5-p18b-heldout-1":
        failures.append("schema")
    if checks.get("protocol_hash") is not True:
        failures.append("protocol hash")
    if checks.get("supplement_hash") is not True:
        failures.append("supplement hash")
    if report.get("quarantine", {}).get("heldout_values_read") is not True:
        failures.append("heldout read flag")
    seal = load(SEAL) or {}
    sealed = {
        row["row_id"]
        for fam in seal.get("families", [])
        if fam.get("partition") == "heldout"
        for row in fam.get("rows", [])
    }
    ledger = report.get("ledger", [])
    if {e["row_id"] for e in ledger} != sealed:
        failures.append("heldout ledger coverage")
    for entry in ledger:
        for label in ("baseline", "candidate"):
            block = entry.get(label) or {}
            if block.get("status") != "scored":
                continue
            calc = calculated_ratio(
                entry["measurement_type"],
                block["sigma_g"],
                block["sigma_m"],
                block["sigma_t"],
            )
            if calc is None:
                continue
            measured = entry["measured"]
            cm = calc / measured if measured != 0 else float("inf")
            if not math.isclose(cm, block["cm"], rel_tol=1e-12, abs_tol=0.0):
                failures.append(f"{entry['row_id']}/{label}: cm")
            if block["ln_cm"] is not None and math.isfinite(block["ln_cm"]):
                if not math.isclose(
                    math.log(cm), block["ln_cm"], rel_tol=1e-12, abs_tol=0.0
                ):
                    failures.append(f"{entry['row_id']}/{label}: ln_cm")
    gates = report.get("gates", {})
    if not isinstance(gates.get("pass"), bool):
        failures.append("gate result missing")
        return failures

    def nonreg_ok(base: dict, cand: dict):
        if not base.get("rows") or not cand.get("rows"):
            return None
        med = (
            cand["median_abs_ln"] <= base["median_abs_ln"] + 0.005
            and cand["median_abs_ln"] <= 1.01 * base["median_abs_ln"]
        )
        p90 = (
            cand["p90_abs_ln"] <= base["p90_abs_ln"] + 0.01
            and cand["p90_abs_ln"] <= 1.01 * base["p90_abs_ln"]
        )
        cov = all(
            cand[k] >= base[k] - 0.01
            for k in ("within_10pct", "within_20pct", "within_30pct")
        )
        return med and p90 and cov

    want_strata = True
    for blk in report.get("per_projectile", {}).values():
        if blk["eligible_rows"] < 10:
            continue
        if nonreg_ok(blk["baseline"], blk["candidate"]) is not True:
            want_strata = False
    want_overall = (
        nonreg_ok(report["overall"]["baseline"], report["overall"]["candidate"])
        is True
    )
    want_pass = bool(
        want_strata
        and want_overall
        and gates.get("mapping_rule4_pass")
        and gates.get("benefit", {}).get("satisfied")
    )
    if gates.get("strata_pass") != want_strata:
        failures.append("strata_pass inconsistent with recorded metrics")
    if gates.get("overall_pass") != want_overall:
        failures.append("overall_pass inconsistent with recorded metrics")
    if gates.get("pass") != want_pass:
        failures.append("gate pass inconsistent with strata/overall/mapping/benefit")
    return failures


def g5_internal_consistency() -> dict[str, Any]:
    """Re-derive the held-out gate verdict from the committed report."""
    report = load(ROOT / EVIDENCE_PATHS["g5_heldout"])
    if report is None:
        return {"pass": False, "reason": "g5 report unreadable"}
    failures = g5_evaluate(report)
    gates = report.get("gates", {})
    return {
        "evaluate_failures": failures,
        "overall_pass": gates.get("overall_pass") is True,
        "strata_pass": gates.get("strata_pass") is False,
        "mapping_rule4_pass": gates.get("mapping_rule4_pass") is True,
        "benefit_satisfied": gates.get("benefit", {}).get("satisfied") is True,
        "heldout_values_read": report.get("quarantine", {}).get("heldout_values_read") is True,
        "prior_run_preserved": report.get("prior_coverage_limited_run") is not None,
        "pass": not failures
        and gates.get("overall_pass") is True
        and gates.get("strata_pass") is False
        and gates.get("mapping_rule4_pass") is True
        and gates.get("benefit", {}).get("satisfied") is True
        and gates.get("pass") is False
        and report.get("quarantine", {}).get("heldout_values_read") is True
        and report.get("prior_coverage_limited_run") is not None,
    }


def g5_self_test() -> bool:
    """Plant mutations on the committed held-out report and require the
    independent evaluator to reject every one."""
    report = load(ROOT / EVIDENCE_PATHS["g5_heldout"])
    if report is None:
        return False

    def mut_ledger(rep):
        rep["ledger"] = rep["ledger"][:-1]

    def mut_gate(rep):
        rep["gates"]["pass"] = not rep["gates"]["pass"]

    def mut_row(rep):
        entry = next(
            x for x in rep["ledger"]
            if (x.get("baseline") or {}).get("status") == "scored"
        )
        entry["baseline"]["cm"] += 0.5

    def mut_flag(rep):
        rep["quarantine"]["heldout_values_read"] = False

    rejected = 0
    for mutation in (mut_ledger, mut_gate, mut_row, mut_flag):
        planted = copy.deepcopy(report)
        mutation(planted)
        if g5_evaluate(planted):
            rejected += 1
    return rejected == 4


def component_checks() -> dict[str, bool]:
    values = {name: run_component(relative) for name, relative in COMPONENT_CHECKERS.items()}
    checks = {
        name: value is not None and value.get("pass") is True
        for name, value in values.items()
    }
    checks["G5_internal"] = g5_internal_consistency()["pass"]
    checks["G5_self_test"] = g5_self_test()
    return checks


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


def session_record_valid(value: dict[str, Any], hashes: dict[str, str | None]) -> bool:
    workflow = value.get("workflow", {})
    audit = value.get("audit", {})
    gates = value.get("gates", {})
    boundary = value.get("release_boundary", {})
    failure = value.get("failure", {})
    successor = value.get("successor", {})
    outcome = value.get("g5_outcome", {})
    return bool(
        value.get("schema") == "actinv-p18-session-1"
        and value.get("canonical_repository") == CANONICAL_REPOSITORY
        and value.get("protocol_sha256") == PROTOCOL_SHA256
        and value.get("amendment_sha256") is None
        and value.get("opening_commit") == OPENING_COMMIT
        and value.get("g0_checkpoint") == G0_CHECKPOINT
        and value.get("g1_checkpoint") == G1_CHECKPOINT
        and value.get("g2_checkpoint") == G2_CHECKPOINT
        and value.get("g3_checkpoint") == G3_CHECKPOINT
        and value.get("g4_authorization_commit") == SOURCE_EVIDENCE_COMMIT
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
            "created_at": "2026-09-12T13:35:55Z",
            "completed_at": "2026-09-12T13:49:03Z",
            "substantive_steps": 56,
            "url": f"https://github.com/AvilaLabs/ACTINV/actions/runs/{WORKFLOW_RUN_ID}",
        }
        and value.get("evidence_sha256")
        == {**hashes, "g4_build_report": LOCAL_EVIDENCE["g4_build_report"][1]}
        and audit
        == {
            "heldout_families": 180,
            "ledger_rows": 1945,
            "eligible_rows": 1859,
            "baseline_scored": 1286,
            "candidate_scored": 385,
            "paired_rows": 356,
            "candidate_build_failed_g3": 1239,
            "candidate_built_files": {
                "neutron": 96,
                "proton": 97,
                "deuteron": 10,
                "alpha": 93,
            },
            "candidate_quarantined": {
                "neutron": 141,
                "proton": 133,
                "deuteron": 76,
                "alpha": 47,
            },
            "heldout_executions": 2,
            "coverage_limited_execution_preserved": True,
            "measurement_values_read": True,
            "heldout_values_read": True,
        }
        and all(gates.get(name) is True for name in GATE_NAMES[:5])
        and gates.get("G5_heldout_score") is False
        and all(
            gates.get(name) == "NOT_AUTHORIZED"
            for name in ("G6_full_rebuild_and_artifacts", "G7_release_closure")
        )
        and outcome
        == {
            "overall_pass": True,
            "strata_pass": False,
            "mapping_rule4_pass": True,
            "benefit_satisfied": True,
            "stratum_failures": {
                "proton": ["median_abs_ln", "within_30pct"],
                "deuteron": ["zero candidate-scored rows of 143 eligible"],
                "alpha": ["p90_abs_ln"],
            },
        }
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
            "class": FAILURE_CLASS,
            "threshold_relaxed": False,
            "row_or_file_excluded_after_result": False,
            "second_repair_attempted": False,
        }
        and successor.get("name") == "P18c"
        and successor.get("status") == "UNOPENED"
        and value.get("closure_record_valid") is True
        and value.get("phase_success") is False
        and value.get("verdict") == "P18b-FAIL"
        and value.get("pass") is False
    )


def expected_verdict(hashes: dict[str, str | None]) -> dict[str, Any]:
    return {
        "schema": "actinv-p18-verdict-1",
        "closed": True,
        "closure_record_valid": True,
        "phase_success": False,
        "verdict": "P18b-FAIL",
        "failure_class": FAILURE_CLASS,
        "gates": {
            "G0": True,
            "G1": True,
            "G2": True,
            "G3": True,
            "G4": True,
            "G5": False,
            "G6": "NOT_AUTHORIZED",
            "G7": "NOT_AUTHORIZED",
        },
        "g5_detail": {
            "overall_pass": True,
            "strata_pass": False,
            "mapping_rule4_pass": True,
            "benefit_satisfied": True,
            "stratum_failures": {
                "proton": ["median_abs_ln", "within_30pct"],
                "deuteron": ["zero candidate-scored rows of 143 eligible"],
                "alpha": ["p90_abs_ln"],
            },
            "overall_candidate_median_abs_ln": 0.19427327539643097,
            "overall_candidate_p90_abs_ln": 0.9624776831681298,
            "identity_corrections_rank_artifact": 6912,
            "prior_coverage_limited_execution": (
                "run 1 scored only 128 of 1859 eligible rows because diagnostic"
                " staging omitted held-out target files; report preserved at"
                " results/g5_p18b_heldout_run1_coverage_limited.json and cited"
                " inside the final report"
            ),
        },
        "heldout_values_unsealed": True,
        "v1.1.0_authorized": False,
        "source_evidence_commit": SOURCE_EVIDENCE_COMMIT,
        "workflow_run_id": WORKFLOW_RUN_ID,
        "evidence_sha256": {
            **hashes,
            "g4_build_report": LOCAL_EVIDENCE["g4_build_report"][1],
        },
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
    plants["gate"][0]["gates"]["G5_heldout_score"] = True
    plants["downstream_authorization"][0]["gates"]["G6_full_rebuild_and_artifacts"] = True
    plants["heldout"][0]["audit"]["coverage_limited_execution_preserved"] = False
    plants["release"][0]["release_boundary"]["v1.1.0_authorized"] = True
    plants["evidence"][1]["g5_heldout"] = "0" * 64
    plants["workflow"][0]["workflow"]["conclusion"] = "failure"
    plants["inventory"][0]["audit"]["eligible_rows"] = 1858
    return {
        name: not session_record_valid(planted, planted_hashes)
        for name, (planted, planted_hashes) in plants.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    require(sha256(PROTOCOL) == PROTOCOL_SHA256, "P18b protocol bytes changed")
    hashes = evidence_hashes()
    require(hashes == EXPECTED_EVIDENCE, "P18b evidence hashes changed")
    require(pre_unseal_binding(), "authorization commit does not bind pre-unseal evidence")
    require(post_unseal_ordering(), "held-out evidence exists before authorization commit")
    require(is_ancestor(OPENING_COMMIT, SOURCE_EVIDENCE_COMMIT), "P18b opening is not an ancestor")
    require(is_ancestor(SOURCE_EVIDENCE_COMMIT, "HEAD"), "authorization commit is not an ancestor of HEAD")
    for checkpoint in (G0_CHECKPOINT, G1_CHECKPOINT, G2_CHECKPOINT, G3_CHECKPOINT):
        require(is_ancestor(checkpoint, SOURCE_EVIDENCE_COMMIT), f"{checkpoint} not an ancestor")
    require(production_changes() == PRODUCTION_CHANGES, "P18b production-change inventory")

    session = load(SESSION)
    require(session is not None, "missing P18b session")
    require(session_record_valid(session, hashes), "invalid P18b session")
    plants = mutation_plants(session, hashes)
    components = component_checks()
    g5_consistency = g5_internal_consistency()
    manifest = tracked_manifest_check()
    boundary = release_boundary()
    local = local_evidence_check()
    unauthorized_absent = all(not (ROOT / path).exists() for path in UNAUTHORIZED_EVIDENCE)

    expected = expected_verdict(hashes)
    if not arguments.no_write:
        VERDICT.write_text(json.dumps(expected, indent=1, sort_keys=True) + "\n")
    verdict = load(VERDICT)
    checks = {
        "protocol_bytes": True,
        "checkpoint_commits_and_workflow": True,
        "evidence_hashes": True,
        "pre_unseal_binding": True,
        "post_unseal_ordering": True,
        "local_build_attestation": local["pass"],
        "gates_G0_G4_pass_G5_fails": all(
            session["gates"].get(name) is True for name in GATE_NAMES[:5]
        )
        and session["gates"].get("G5_heldout_score") is False,
        "independent_components": all(components.values()),
        "downstream_evidence_absent": unauthorized_absent,
        "release_boundary": boundary["pass"],
        "closure_mutation_plants": len(plants) == 7 and all(plants.values()),
        "deterministic_verdict": verdict == expected,
        "tracked_manifest": manifest["pass"],
    }
    output = {
        "schema": "actinv-p18b-closure-check-1",
        "checks": checks,
        "component_checkers": components,
        "g5_consistency": g5_consistency,
        "mutation_plants": plants,
        "release_boundary": boundary,
        "manifest": manifest,
        "closed": all(checks.values()),
        "phase_success": False,
        "verdict": "P18b-FAIL",
        "pass": all(checks.values()),
    }
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
