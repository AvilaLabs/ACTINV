#!/usr/bin/env python3
"""Regression plants for the independent P12 evidence checker."""

from __future__ import annotations

from copy import deepcopy
import json
import tomllib

import check_p12
import g5_p12_release


def load(gate: str) -> dict[str, object]:
    path = check_p12.RESULTS / check_p12.GATE_FILES[gate]
    return json.loads(path.read_text())


def main() -> int:
    baseline = check_p12.derive(through_g5=True)
    plants: dict[str, bool] = {}

    plants["manifest_exclusion_scope"] = check_p12.MANIFEST_EXCLUDED == (
        "MANIFEST.sha256",
        "results/g6_p12_complete.json",
        "results/verdict_p12.json",
    )

    g1 = load("G1")
    planted = deepcopy(g1)
    planted["independent_dense_response"]["maximum_relative"] = 1.0
    plants["g1_numeric_bound"] = not check_p12.evaluate_g1(planted)["pass"]

    g2 = load("G2")
    planted = deepcopy(g2)
    planted["inputs"]["meija_pdf"]["sha256"] = "0" * 64
    plants["g2_primary_hash"] = not check_p12.evaluate_g2(planted)["pass"]

    g3 = load("G3")
    planted = deepcopy(g3)
    planted["full"]["deterministic"]["cases"] = 999_999
    plants["g3_partition_count"] = not check_p12.evaluate_g3(planted)["pass"]
    planted = deepcopy(g3)
    planted["source"]["reader_source_sha256"][check_p12.READER_SOURCES[0]] = "0" * 64
    plants["g3_reader_snapshot"] = not check_p12.evaluate_g3(planted)["pass"]
    planted = deepcopy(g3)
    planted["control_sha256"] = "0" * 64
    plants["g3_control_snapshot"] = not check_p12.evaluate_g3(planted)["pass"]

    g4 = load("G4")
    planted = deepcopy(g4)
    planted["history_comparison"]["nuclides"]["Co58"]["endpoints"] = 169
    plants["g4_endpoint_count"] = not check_p12.evaluate_g4(planted)["pass"]

    g5 = load("G5")
    planted = deepcopy(g5)
    planted["standalone_binary"]["version_output"] = "actinv 0.0.0"
    plants["g5_version"] = not check_p12.evaluate_g5(planted)["pass"]
    with (check_p12.ROOT / "Cargo.toml").open("rb") as stream:
        current_version = tomllib.load(stream)["workspace"]["package"]["version"]
    plants["g5_snapshot_survives_patch_version"] = (
        current_version != "1.0.0" and check_p12.evaluate_g5(g5)["pass"] is True
    )
    workspace_lock = g5_p12_release.read_toml(check_p12.ROOT / "Cargo.lock")
    plants["g5_workspace_lock_self_match"] = g5_p12_release.packaged_lock_matches_workspace(
        workspace_lock, workspace_lock
    )
    planted_lock = deepcopy(workspace_lock)
    for package in planted_lock["package"]:
        if package.get("name") not in g5_p12_release.CRATES and "checksum" in package:
            package["checksum"] = "0" * 64
            break
    plants["g5_packaged_lock_drift"] = not g5_p12_release.packaged_lock_matches_workspace(
        workspace_lock, planted_lock
    )

    gate_hashes = {
        filename: check_p12.sha256(check_p12.RESULTS / filename)
        for gate, filename in check_p12.GATE_FILES.items()
        if gate != "G6"
    }
    g6 = {
        "schema": "actinv-p12-g6-complete-1",
        "gate": "P12-G6",
        "release_commit": "1" * 40,
        "gate_result_sha256": gate_hashes,
        "protocol_sha256": check_p12.PROTOCOL_HASHES,
        "session": {"pass": True},
        "manifest": {"pass": True},
        "remote": {"pass": True},
        "github": {"pass": True},
        "regeneration": {"pass": True},
        "pass": True,
    }
    plants["g6_incomplete_evidence"] = not check_p12.evaluate_g6(g6)["pass"]

    result = {
        "baseline_through_g5": baseline["pass"] is True,
        "plants": plants,
    }
    result["pass"] = result["baseline_through_g5"] and all(plants.values())
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
