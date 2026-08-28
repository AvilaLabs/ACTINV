#!/usr/bin/env python3
"""Derive the P13 source or final verdict from the frozen distribution evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols/ACTINV-P13_PROTOCOL.md"
PROTOCOL_SHA256 = "afbc60cb75411b1f10a558f77f2a512412de2f925bbaccada099ac5fd3c2f92c"
CATALOG = ROOT / "crates/actinv-cli/data/actinv-data-catalog-v1.0.0.json"
NOTICE = ROOT / "crates/actinv-cli/data/ACTINV-DATA-NOTICE-v1.0.0.md"
VERDICT = RESULTS / "verdict_p13.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def protocol_check():
    ledger = (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    expected_line = f"{PROTOCOL_SHA256}  protocols/ACTINV-P13_PROTOCOL.md"
    actual = sha256(PROTOCOL) if PROTOCOL.is_file() else None
    return {
        "expected_sha256": PROTOCOL_SHA256,
        "actual_sha256": actual,
        "ledger_entry": expected_line in ledger,
        "pass": actual == PROTOCOL_SHA256 and expected_line in ledger,
    }


def command_result(command, environment=None):
    completed = subprocess.run(
        command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False
    )
    display = ["cargo", *command[1:]] if Path(command[0]).name == "cargo" else command
    return {
        "command": " ".join(display),
        "returncode": completed.returncode,
        "pass": completed.returncode == 0,
        "error_tail": "" if completed.returncode == 0 else "\n".join(
            (completed.stdout + completed.stderr).replace(str(ROOT), "<ROOT>").splitlines()[-30:]
        ),
    }


def evaluate_g1(value):
    checks = {
        "schema": value is not None and value.get("schema") == "actinv-p13-data-distribution-control-1",
        "catalog_version": value is not None and value.get("catalog_version") == "1.0.0",
        "inventory": value is not None and value.get("artifact_count") == 13 and value.get("bundle_count") == 5,
        "checks": value is not None and value.get("pass") is True
        and all(item is True for item in value.get("checks", {}).values()),
        "evidence": value is not None and len(value.get("evidence_identities", {})) == 12
        and all(item is True for item in value.get("evidence_identities", {}).values()),
        "plants": value is not None and len(value.get("planted_rejections", {})) == 6
        and all(item is True for item in value.get("planted_rejections", {}).values()),
        "cli": value is not None and all(item is True for item in value.get("cli", {}).values()),
    }
    return {"checks": checks, "pass": all(checks.values())}


def evaluate_g4(value):
    identities = value.get("identities", {}) if value else {}
    checks = {
        "schema": value is not None and value.get("schema") == "actinv-p13-release-stage-control-1",
        "catalog_version": value is not None and value.get("catalog_version") == "1.0.0",
        "inventory": value is not None and value.get("asset_count") == 12
        and value.get("total_payload_bytes") == 565_231_189,
        "identities": len(identities) == 12 and all(item.get("pass") is True for item in identities.values()),
        "checks": value is not None and value.get("pass") is True
        and all(item is True for item in value.get("checks", {}).values()),
        "no_unexpected": value is not None and value.get("unexpected_entries") == [],
    }
    return {"checks": checks, "pass": all(checks.values())}


def documentation_check():
    requirements = {
        "README.md": ("actinv data fetch", "actinv data verify", "actinv-data/v1.0.0", "prints the exact paths"),
        "docs/DATA.md": ("Easiest setup", "139 MiB", "actinv data manifest", "CC-BY-4.0", "offline"),
        "docs/RELEASE_CHECKLIST.md": ("Versioned data release", "prepare_data_release.py", "data-v1.0.0"),
        "CHANGELOG.md": ("actinv data list/fetch/verify/manifest", "atomic", "official ENDF/B-VIII.0/JEFF-3.3"),
        "crates/actinv-cli/data/ACTINV-DATA-NOTICE-v1.0.0.md": (
            "TENDL-2025", "CC-BY-4.0", "Software and data licences are separate", "Pb-208"
        ),
    }
    files = {}
    for relative, tokens in requirements.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        missing = [token for token in tokens if token not in text]
        files[relative] = {"missing": missing, "pass": not missing}
    return {"files": files, "pass": all(item["pass"] for item in files.values())}


def source_contract():
    catalog = load(CATALOG)
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    source = (ROOT / "crates/actinv-cli/src/lib.rs").read_text(encoding="utf-8")
    cargo = (ROOT / "crates/actinv-cli/Cargo.toml").read_text(encoding="utf-8")
    checks = {
        "catalog_identity": catalog is not None and catalog.get("schema") == "actinv-data-catalog-1"
        and catalog.get("catalog_version") == "1.0.0" and sha256(CATALOG) == "27b77786b89a6303f0805646fc61e4fbcabaaf695dd92e4e15a87948b3d69430",
        "notice_identity": sha256(NOTICE) == "191117ff8aaff8d9981a93c9bb59b418cfe54505dc7363128d2bc5e728a6a62c",
        "no_unsafe": re.search(r"\bunsafe\b", source) is None,
        "pinned_network_dependencies": 'ureq = "=3.4.0"' in cargo and 'zip = { version = "=8.6.0"' in cargo,
        "strict_ci_gates": all(token in ci for token in (
            "cargo fmt --all -- --check",
            "cargo check --workspace --all-targets --all-features",
            "cargo clippy --workspace --all-targets --all-features -- -D warnings",
            "cargo test --workspace --all-targets --all-features",
        )),
        "p13_in_ci": "controls/g1_p13_data_distribution.py" in ci,
        "staging_control": (ROOT / "scripts/prepare_data_release.py").is_file()
        and (ROOT / "controls/g4_p13_release_stage.py").is_file(),
    }
    return {"checks": checks, "pass": all(checks.values())}


def release_check(session, g4):
    if session is None:
        return {"present": False, "pass": False}
    source_commit = session.get("source_commit")
    workflow = session.get("workflow", {})
    release = session.get("release", {})
    smoke = session.get("smoke", {})
    assets = release.get("assets", {})
    staged = g4.get("identities", {}) if g4 else {}
    expected_assets = set(staged) | {"SHA256SUMS", "SIZES"}
    identity_match = all(
        name in assets
        and assets[name].get("bytes") == identity["bytes"]
        and assets[name].get("sha256") == identity["sha256"]
        for name, identity in staged.items()
    )
    checks = {
        "source_commit": isinstance(source_commit, str) and re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None,
        "workflow": workflow.get("head_sha") == source_commit and workflow.get("conclusion") == "success"
        and isinstance(workflow.get("id"), int) and workflow.get("id") > 0,
        "release": release.get("tag") == "data-v1.0.0" and release.get("target_commit") == source_commit
        and isinstance(release.get("id"), int) and release.get("id") > 0,
        "asset_names": set(assets) == expected_assets,
        "asset_identities": identity_match,
        "release_inventories": assets.get("SHA256SUMS", {}).get("sha256") == g4.get("sha256sums_sha256")
        and assets.get("SIZES", {}).get("sha256") == g4.get("sizes_sha256"),
        "smoke": smoke.get("fetch") is True and smoke.get("verify") is True
        and smoke.get("calculation") is True and smoke.get("bundle") == "tendl-2025-neutron",
    }
    return {"present": True, "checks": checks, "pass": all(checks.values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="rerun every required Rust and Python Rust gate")
    parser.add_argument("--require-release", action="store_true")
    parser.add_argument("--no-write", action="store_true", help="derive without replacing the verdict")
    args = parser.parse_args()

    environment = os.environ.copy()
    cargo = environment.get("CARGO", "cargo")
    focused = command_result([cargo, "test", "-p", "actinv-cli", "--all-targets", "--all-features"], environment)
    full_commands = []
    if args.full:
        for command in [
            [cargo, "fmt", "--all", "--", "--check"],
            [cargo, "check", "--workspace", "--all-targets", "--all-features"],
            [cargo, "clippy", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"],
            [cargo, "test", "--workspace", "--all-targets", "--all-features"],
            [cargo, "fmt", "--manifest-path", "python/Cargo.toml", "--", "--check"],
            [cargo, "check", "--manifest-path", "python/Cargo.toml", "--all-targets", "--all-features"],
            [cargo, "clippy", "--manifest-path", "python/Cargo.toml", "--all-targets", "--all-features", "--", "-D", "warnings"],
        ]:
            full_commands.append(command_result(command, environment))

    g1_value = load(RESULTS / "g1_p13_data_distribution.json")
    g4_value = load(RESULTS / "g4_p13_release_stage.json")
    protocol = protocol_check()
    gates = {
        "G1": evaluate_g1(g1_value),
        "G2": {"focused_rust_tests": focused, "pass": focused["pass"]},
        "G3": {"independent_cli": g1_value.get("cli", {}) if g1_value else {},
               "pass": g1_value is not None and all(g1_value.get("cli", {}).values())},
        "G4": evaluate_g4(g4_value),
        "G5": documentation_check(),
        "G6": {
            "source_contract": source_contract(),
            "full_commands": full_commands,
            "pass": source_contract()["pass"] and (not args.full or all(item["pass"] for item in full_commands)),
        },
    }
    source_pass = protocol["pass"] and all(gate["pass"] for gate in gates.values())
    release = release_check(load(RESULTS / "session_p13.json"), g4_value)
    closed = source_pass and release["pass"]
    result = {
        "schema": "actinv-p13-verdict-1",
        "protocol": protocol,
        "gates": gates,
        "source_pass": source_pass,
        "release": release,
        "closed": closed,
        "verdict": "P13-PASS" if closed else ("P13-SOURCE-PASS" if source_pass else "P13-FAIL"),
        "pass": closed if args.require_release else source_pass,
    }
    if not args.no_write:
        VERDICT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
