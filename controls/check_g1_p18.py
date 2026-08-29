#!/usr/bin/env python3
"""Independent checker for the committed P18-G1 physical-state evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "results/g1_p18_state_identity.json"
RESULT = ROOT / "results/g1_p18_check.json"
CONTROL = ROOT / "controls/g1_p18_state_identity.py"
G0 = ROOT / "results/g0_p18_seal.json"
PROTOCOL = ROOT / "protocols/ACTINV-P18_PROTOCOL.md"
AMENDMENT = ROOT / "protocols/ACTINV-P18_AMENDMENT_1.md"

EXPECTED_EVIDENCE_SHA256 = "e7f51a6f23523956c349a634253ff86e0d986c482a5416caa26a3025156bbeb8"
EXPECTED_G0_SHA256 = "e419e8eeb506c28440744e8a3ceb6922f1d066b8d673f534ce0567dc59f82af3"
EXPECTED_PROTOCOL_SHA256 = "002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09"
EXPECTED_AMENDMENT_SHA256 = "8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0"
EXPECTED_CONTROL_SHA256 = "508a022317c6e72c977f2827fa318e3eac07a09157e09acd93d72fcaea3db8d5"

EXPECTED_IMPLEMENTATION = {
    "activation.rs": "09b630e27a43ace3189a76a9e19df6c25ecc84bca01c38e2ccbf4ac8e5a07a2f",
    "builder.rs": "28bbcc089d1a3ae93e9266e30f8b46cf299324827f56efdc0d4d3206db7c4b1d",
    "covariance.rs": "f80c61953e824af5073bb27b853423b943c51c30b15daf089162ddc38fef9306",
}

EXPECTED_INPUTS = {
    "n-Ag109.tendl": "4d26695b8961386b96bfdc2898b3499fe4a6120e40d0cbd3081544eda2818ca1",
    "n-Ag110.tendl": "5ce300b78dfaecf83696b0bae37174ea56a49c6f84ffb53b3e8289f2850dd78a",
    "n-Ag110m.tendl": "d72de90c2d32247f50fa302f74ad09163491d844818dbf8d3ce19d7c87d536ae",
    "n-Ag116.tendl": "8a1d0639766f9aee47fc7ceb85a418900e48a7cec0e7abfda51051d6aec074c5",
    "n-Ag116m.tendl": "9b11a9230eb4abaca10c7927dde4843f79eb0f3ba96d9905ee2c50bd43fff77d",
    "n-Ag116n.tendl": "cb5497a3cb1835adb65e9431e1342d454bd7f0227d7718335ae586c17ccc063a",
}

EXPECTED_BUILDS = {
    "p17_ag110_sparse_level": {
        "build": {
            "npz_sha256": "498768dc10c488ee1aece4fabe036a51937e09961494ea2794de746eb839885e",
            "index_sha256": "ab804be941eebc4e6987f376a1bdbfb8149bf1b12c5baac0b8a0169541c18e40",
            "targets": 3,
            "rows": 222,
        },
        "reordered_build": {
            "npz_sha256": "eb601e603af26ee2c3897f1ce543a5b8b4bcda0a5087cc9cbc3d0e9b86b0c8c7",
            "index_sha256": "cdaa60136059ac44fe6622acc49780405381af4e6c25ef1d1dec987f219feca0",
            "targets": 3,
            "rows": 222,
        },
    },
    "ag116_two_isomers": {
        "build": {
            "npz_sha256": "245c81a2add96203a184052ddde2dbc2be78193e0298f0b84a18e90edb86fe81",
            "index_sha256": "b6e23ca4cb7d33f3120284afeca03244bc994adcdd1b2c042d0e57d3e8b1eb12",
            "targets": 3,
            "rows": 162,
        },
        "reordered_build": {
            "npz_sha256": "6c5b97fb4c9048af4b68a31111189e2d7775df0200ca8931c7ff856abdb9213c",
            "index_sha256": "0453a6391c02c4959ceae5cf21ce75fbdb0dbf5522f8e8fa2816caa7a64f3afd",
            "targets": 3,
            "rows": 162,
        },
    },
}

EXPECTED_MAPPING = {
    "mt": 102,
    "zap": 47_110,
    "lmf": 9,
    "raw_lfs": 2,
    "elfs_eV": 117_590.0,
    "qm_eV": 6_809_191.0,
    "qi_eV": 6_691_601.0,
    "qm_minus_qi_eV": 117_590.0,
    "mapping_excitation_eV": 117_590.0,
    "canonical_liso": 1,
    "catalog_lis": 2,
    "catalog_elis_eV": 117_590.0,
    "catalog_file": "n-Ag110m.tendl",
    "catalog_source_sha256": EXPECTED_INPUTS["n-Ag110m.tendl"],
    "catalog_evaluations": 1,
    "tolerance_eV": 1.0,
    "excitation_delta_eV": 0.0,
    "decision": "catalog_excitation_match",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def validate_mapping(value: dict[str, Any]) -> None:
    require(value == EXPECTED_MAPPING, "P17 Ag mapping differs from independent ENDF expectation")


def validate_evidence(evidence: dict[str, Any]) -> None:
    require(evidence["schema"] == "actinv-p18-g1-state-identity-1", "G1 schema")
    require(evidence["gate"] == "P18-G1", "G1 gate")
    require(evidence["protocol_sha256"] == EXPECTED_PROTOCOL_SHA256, "protocol identity")
    require(evidence["amendment_sha256"] == EXPECTED_AMENDMENT_SHA256, "amendment identity")
    require(evidence["control_source_sha256"] == EXPECTED_CONTROL_SHA256, "control identity")
    require(evidence["implementation_source_sha256"] == EXPECTED_IMPLEMENTATION, "implementation identity")
    require(evidence["pass"] is True, "G1 verdict")
    require(
        evidence["rust_builder_tests"]
        == {
            "command": "cargo test -p actinv-data builder::tests -- --quiet",
            "returncode": 0,
            "pass": True,
        },
        "Rust builder tests",
    )
    require(
        evidence["mutation_plants"]
        == {
            "raw_level": True,
            "canonical_state": True,
            "catalog_excitation": True,
            "q_identity": True,
        },
        "control mutation plants",
    )
    expected_checks = {
        "source_precision_retained": True,
        "p17_sparse_level_maps_to_m1": True,
        "multiple_isomers_use_catalog_liso": True,
        "omitted_levels_are_not_rank_compressed": True,
        "file_order_invariant": True,
        "unsupported_states_are_explicitly_non_emitted": True,
        "duplicate_agreement_and_conflict_covered": True,
        "generated_endf_fixture_covered": True,
        "cache_schema": "actinv-target-checkpoint-3",
        "library_index_schema": "actinv-library-index-2",
    }
    require(evidence["checks"] == expected_checks, "G1 invariant checklist")
    require(not any(value.startswith("/") or "/tmp/" in value for value in strings(evidence)), "absolute path leaked into evidence")

    cases = evidence["cases"]
    require(set(cases) == set(EXPECTED_BUILDS), "G1 case set")
    for case, expected in EXPECTED_BUILDS.items():
        require(cases[case]["build"] == expected["build"], f"{case}: primary build")
        require(cases[case]["reordered_build"] == expected["reordered_build"], f"{case}: reordered build")
        require(cases[case]["file_order_invariant"] is True, f"{case}: file order")

    p17 = cases["p17_ag110_sparse_level"]
    require(p17["inputs"] == {name: EXPECTED_INPUTS[name] for name in ("n-Ag109.tendl", "n-Ag110.tendl", "n-Ag110m.tendl")}, "P17 Ag inputs")
    validate_mapping(p17["mapping"])
    state = p17["catalog_state"]
    require((state["za"], state["liso"], state["representative"]["lis"], state["representative"]["elis_eV"]) == (47_110, 1, 2, 117_590.0), "Ag110m catalog identity")
    require(state["representative"]["source_sha256"] == EXPECTED_INPUTS["n-Ag110m.tendl"], "Ag110m source")

    ag116 = cases["ag116_two_isomers"]
    require(ag116["inputs"] == {name: EXPECTED_INPUTS[name] for name in ("n-Ag116.tendl", "n-Ag116m.tendl", "n-Ag116n.tendl")}, "Ag116 inputs")
    require(ag116["positive_mapping_count"] == 6, "Ag116 mapping count")
    require(ag116["raw_to_canonical"] == [[1, 1], [4, 2]], "Ag116 sparse mapping")
    states = [
        (
            state["liso"],
            state["representative"]["lis"],
            state["representative"]["elis_eV"],
            state["representative"]["source_sha256"],
        )
        for state in ag116["catalog_states"]
    ]
    require(
        states
        == [
            (0, 0, 0.0, EXPECTED_INPUTS["n-Ag116.tendl"]),
            (1, 1, 47_900.0, EXPECTED_INPUTS["n-Ag116m.tendl"]),
            (2, 4, 129_800.0, EXPECTED_INPUTS["n-Ag116n.tendl"]),
        ],
        "Ag116 catalog identities",
    )


def mutation_plants(evidence: dict[str, Any]) -> dict[str, bool]:
    output = {}
    for name, field, value in (
        ("raw_level", "raw_lfs", 1),
        ("canonical_state", "canonical_liso", 2),
        ("catalog_excitation", "catalog_elis_eV", 117_600.0),
        ("q_identity", "qi_eV", 6_691_600.0),
    ):
        mutant = copy.deepcopy(evidence["cases"]["p17_ag110_sparse_level"]["mapping"])
        mutant[field] = value
        try:
            validate_mapping(mutant)
        except AssertionError:
            output[name] = True
        else:
            output[name] = False
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    require(sha256(EVIDENCE) == EXPECTED_EVIDENCE_SHA256, "G1 evidence bytes changed")
    require(sha256(G0) == EXPECTED_G0_SHA256, "G0 evidence bytes changed")
    require(json.loads(G0.read_text())["pass"] is True, "G0 verdict")
    require(sha256(PROTOCOL) == EXPECTED_PROTOCOL_SHA256, "protocol bytes changed")
    require(sha256(AMENDMENT) == EXPECTED_AMENDMENT_SHA256, "amendment bytes changed")
    require(sha256(CONTROL) == EXPECTED_CONTROL_SHA256, "G1 control bytes changed")
    evidence = json.loads(EVIDENCE.read_text())
    validate_evidence(evidence)
    plants = mutation_plants(evidence)
    result = {
        "schema": "actinv-p18-g1-independent-check-1",
        "gate": "P18-G1",
        "evidence_sha256": EXPECTED_EVIDENCE_SHA256,
        "control_sha256": EXPECTED_CONTROL_SHA256,
        "checker_source_sha256": sha256(Path(__file__)),
        "checks": {
            "frozen_inputs": True,
            "physical_identity": True,
            "source_precision": True,
            "sparse_levels": True,
            "multiple_isomers": True,
            "file_order": True,
            "portable_evidence": True,
        },
        "mutation_plants": plants,
    }
    result["pass"] = all(result["checks"].values()) and all(plants.values())
    if arguments.no_write:
        require(RESULT.is_file(), f"missing committed G1 checker result {RESULT}")
        require(json.loads(RESULT.read_text()) == result, "committed G1 checker result is not reproducible")
    else:
        RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
