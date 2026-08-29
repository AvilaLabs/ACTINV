#!/usr/bin/env python3
"""Independently verify the complete, truthfully failed P18-G2 corpus audit."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "results/g2_p18_corpus_audit.json"
CHANGED = ROOT / "results/g2_p18_changed_identities.json.gz"
RESULT = ROOT / "results/g2_p18_check.json"
CONTROL = ROOT / "controls/g2_p18_corpus_audit.py"
PROTOCOL = ROOT / "protocols/ACTINV-P18_PROTOCOL.md"
AMENDMENT = ROOT / "protocols/ACTINV-P18_AMENDMENT_1.md"
G0 = ROOT / "results/g0_p18_seal.json"
G1 = ROOT / "results/g1_p18_state_identity.json"

PROTOCOL_SHA256 = "002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09"
AMENDMENT_SHA256 = "8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0"
G0_SHA256 = "e419e8eeb506c28440744e8a3ceb6922f1d066b8d673f534ce0567dc59f82af3"
G1_SHA256 = "e7f51a6f23523956c349a634253ff86e0d986c482a5416caa26a3025156bbeb8"
CONTROL_SHA256 = "81c636b400636ed7a784c806e28fa02a4a1b5d8960a8b5f27eaabc38946dc390"
EVIDENCE_SHA256 = "e20fba865c36131f27bce7ac110957336c55d9c8455e79a8ddac0edde66df9cb"
CHANGED_SHA256 = "606347ce4d12788451be3a4a3765bfa5305613631eb37ea5de30d163546e083e"
CHANGED_CANONICAL_SHA256 = "869eb618265f1159879cf644f44f67ad7189d24b8eaf455f299c905d8a102785"

EXPECTED_IMPLEMENTATION = {
    "activation.rs": "6afba14b7210ad2997f03f866f1d5956336c1d2ffed434d40d6d1790c67c669d",
    "builder.rs": "9cbf0cfbdd83c9e72884f37bb7c0019bef9b3f7a71a179817916b92b2f359bd9",
    "groups.rs": "cce5586c142b252ba3d845295f0727a2920992f94d305f20da036334ccb8179e",
    "p18_corpus_probe.rs": "4f7afb6c2b4e388596f235f05d63555d03d573d18061e17463c863cdda0aa9e8",
}

EXPECTED_CORPORA = {
    "neutron": {
        "manifest_sha256": "a6d17f996153d2671c0c51bfb6303e2a87a5af03e0696bfb34d668a31dbfb2a2",
        "file_manifest_sha256": "b1ea3fe043ec243e2df0a3894206872c2ce18c3b4541c19b35029b3ed3e7b15c",
        "checkpoint_sha256": "9de033be0c68fce389f0832acc479edc474d7162075c96c8533751ccbb863b25",
        "checkpoint_bytes": 74_977_389,
        "declarations": (416_618, 1_555, 41_591),
        "failed_files": 2_365,
        "comparisons": (3_847_936, 1_859_362, 30_236_723, 14_579_167),
        "mf_comparisons": (2_064_857, 48_458_331),
        "violations": (231_830, 448_938, 582_170, 865_875),
        "mf_violations": (85_198, 2_043_615),
        "total_violations": 2_128_813,
    },
    "proton": {
        "manifest_sha256": "98a8bd55784c326b8696de91f494111326378e776a975a512e59806a8c9ec2ef",
        "file_manifest_sha256": "0458a6c20e0b2fbb23934d2672304d210ceef74b0fc2807e9d9271c9aacf6ffd",
        "checkpoint_sha256": "bd962c9e25e2bf5e8c919202ab8eeacc1c78b6278ba2a31bd26d636fcccbaade",
        "checkpoint_bytes": 73_727_458,
        "declarations": (409_650, 0, 42_108),
        "failed_files": 2_411,
        "comparisons": (1_364_642, 656_120, 6_667_272, 3_212_136),
        "mf_comparisons": (0, 11_900_170),
        "violations": (24, 92_807, 275, 82_777),
        "mf_violations": (0, 175_883),
        "total_violations": 175_883,
    },
    "deuteron": {
        "manifest_sha256": "afb52c55b2a1babca998cc3d8af0f7004c64f85d160e3c5aabf16a05839355d9",
        "file_manifest_sha256": "feaa774185fb215e45c6fdf6bb26670bfeae9e4263386cfcccd4b7abcd3fa47f",
        "checkpoint_sha256": "6e328ff5749c7a9c8f475c109fdb6c871dd28bc14e00f82eed7836f5ea0a04dd",
        "checkpoint_bytes": 75_841_984,
        "declarations": (416_363, 0, 47_037),
        "failed_files": 2_417,
        "comparisons": (1_846_362, 888_372, 7_465_770, 3_595_104),
        "mf_comparisons": (0, 13_795_608),
        "violations": (32, 135_510, 186, 128_544),
        "mf_violations": (0, 264_272),
        "total_violations": 264_272,
    },
    "alpha": {
        "manifest_sha256": "e3aaf11e60c46b43361796c2c297bab4fb714fe57ab26a315594f2b4799dfdbf",
        "file_manifest_sha256": "ca8bd5ea75d3cc3590a9f4115d94ec54f2cc110a09275b782ace3d608b1b7c81",
        "checkpoint_sha256": "1da6f0954fc6e17abd0a518d6bf1c6bd15b3fa71b417fd72c0bc42be3fb432e0",
        "checkpoint_bytes": 70_541_955,
        "declarations": (402_698, 0, 32_879),
        "failed_files": 2_253,
        "comparisons": (827_432, 398_950, 5_172_174, 2_499_174),
        "mf_comparisons": (0, 8_897_730),
        "violations": (22, 45_570, 79, 32_976),
        "mf_violations": (0, 78_647),
        "total_violations": 78_647,
    },
}

EXPECTED_CATALOG_HASHES = {
    "neutron": "a057b1873e9eab4b741c5d2e3d53603864a953b64b86c36643bffec62da7ea37",
    "proton": "35b3ec5239635c20cd4138a811196ce7208e83f2994624ca650ca0e958f186ab",
    "deuteron": "73537a68daa88f7d9cab0cb5705d2a338b14fa5bb4ce7fc90db14f072e092e21",
    "alpha": "af491ae5d8325552a125875ae3b36ea41c0d72757c84521e8fbc80544689d665",
}

EXPECTED_MAPPINGS = {
    "neutron": (446_102, 406_661, 28, 10_948, 2_542, 308, 165),
    "proton": (437_040, 392_939, 44, 11_589, 2_722, 128, 50),
    "deuteron": (448_843, 406_472, 25, 11_514, 2_720, 130, 54),
    "alpha": (421_571, 379_945, 46, 11_269, 2_735, 115, 56),
}

EXPECTED_ACCOUNTING_HASHES = {
    "neutron": "87116e8fd6a5b973f0e40bd534eabb99877a2c19a21fea0bbd6a9f0e29825b05",
    "proton": "2aa3553464a5e7e95e2cbf4ce16b2703fd6553f2418511f8adb88d5baa0f91c9",
    "deuteron": "7c64e9400f1bdf32a64b14ae85fb4e69ae834bd38c187e1453f17663112e428c",
    "alpha": "cf7e925047f933f04066b98a65b68a9d17e920142b992fd9686706dd631189a2",
}

COMPARISON_FIELDS = (
    "pointwise_individual_comparisons",
    "pointwise_sum_comparisons",
    "collapsed_individual_comparisons",
    "collapsed_sum_comparisons",
)
VIOLATION_FIELDS = (
    "pointwise_individual_violations",
    "pointwise_sum_violations",
    "collapsed_individual_violations",
    "collapsed_sum_violations",
)
CONFLICTS = {
    "ambiguous_catalog_excitation_match",
    "ground_excitation_conflict",
    "negative_q_excitation_conflict",
    "mf8_q_excitation_conflict",
}
CHANGED_KEYS = {
    "projectile",
    "file",
    "source_sha256",
    "target_za",
    "target_liso",
    "mat",
    "mt",
    "zap",
    "raw_lfs",
    "old_rank_liso",
    "candidate_liso",
    "decision",
    "excitation_ev",
    "catalog_elis_ev",
    "catalog_file",
    "catalog_source_sha256",
    "mfs",
    "declarations",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


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


def validate_worst(projectile: str, row: dict[str, Any]) -> None:
    worst = row["worst"]
    require(worst is not None and worst["excess_b"] > 0.0, f"{projectile}: worst violation")
    absolute = 1e-12 if worst["scope"] == "pointwise" else 1e-14
    tolerance = max(
        absolute,
        5e-10 * max(worst["total_b"], worst["peak_total_b"]),
    )
    require(tolerance == worst["tolerance_b"], f"{projectile}: worst tolerance")
    excess = worst["partial_b"] - worst["total_b"] - tolerance
    require(
        math.isclose(excess, worst["excess_b"], rel_tol=2e-15, abs_tol=1e-18),
        f"{projectile}: worst excess arithmetic",
    )


def validate_corpus(projectile: str, row: dict[str, Any]) -> None:
    expected = EXPECTED_CORPORA[projectile]
    for field in (
        "manifest_sha256",
        "file_manifest_sha256",
        "checkpoint_sha256",
        "checkpoint_bytes",
        "failed_files",
    ):
        require(row[field] == expected[field], f"{projectile}: {field}")
    require(row["files"] == row["targets"] == 2_850, f"{projectile}: complete inventory")
    declarations = tuple(row[f"mf{mf}_declarations"] for mf in (8, 9, 10))
    require(declarations == expected["declarations"], f"{projectile}: declarations")
    require(sum(declarations) == row["declarations"], f"{projectile}: declaration sum")
    require(
        row["exact_duplicate_declarations"] == 0
        and row["duplicate_or_descriptor_issues"] == 0
        and row["missing_totals"] == 0,
        f"{projectile}: declaration/total integrity",
    )
    comparisons = tuple(row[field] for field in COMPARISON_FIELDS)
    mf_comparisons = (row["mf9_comparisons"], row["mf10_comparisons"])
    require(comparisons == expected["comparisons"], f"{projectile}: comparisons")
    require(mf_comparisons == expected["mf_comparisons"], f"{projectile}: MF comparisons")
    require(sum(comparisons) == sum(mf_comparisons), f"{projectile}: comparison accounting")
    violations = tuple(row[field] for field in VIOLATION_FIELDS)
    mf_violations = (row["mf9_violations"], row["mf10_violations"])
    require(violations == expected["violations"], f"{projectile}: violations")
    require(mf_violations == expected["mf_violations"], f"{projectile}: MF violations")
    require(
        sum(violations) == sum(mf_violations) == row["violations"]
        == expected["total_violations"],
        f"{projectile}: violation accounting",
    )
    require(row["inventory_pass"] is True, f"{projectile}: inventory verdict")
    validate_worst(projectile, row)


def validate_mapping(projectile: str, row: dict[str, Any], corpus: dict[str, Any]) -> None:
    expected = EXPECTED_MAPPINGS[projectile]
    require(row["declarations"] == corpus["declarations"], f"{projectile}: mapping count")
    require(row["accounted"] == row["declarations"], f"{projectile}: declaration accounting")
    require(sum(row["decision_counts"].values()) == row["declarations"], f"{projectile}: decisions")
    require(sum(row["mf_counts"].values()) == row["declarations"], f"{projectile}: mapping MF count")
    require(
        row["mf_counts"]
        == {
            str(mf): corpus[f"mf{mf}_declarations"]
            for mf in (8, 9, 10)
            if corpus[f"mf{mf}_declarations"]
        },
        f"{projectile}: mapping MF identity",
    )
    observed = (
        row["emitted"],
        row["catalog_backed_emitted"],
        row["conflicts"],
        row["changed_identities"],
        row["changed_files"],
        row["unaffected_files"],
        row["conservation_pass_unaffected_files"],
    )
    require(observed == expected, f"{projectile}: mapping summary")
    require(row["identity_backed_emitted"] == row["emitted"], f"{projectile}: identity backing")
    require(row["changed_files"] + row["unaffected_files"] == 2_850, f"{projectile}: file partition")
    conflict_count = sum(row["decision_counts"].get(name, 0) for name in CONFLICTS)
    require(conflict_count == row["conflicts"] > 0, f"{projectile}: conflicts")
    require(row["accounting_sha256"] == EXPECTED_ACCOUNTING_HASHES[projectile], f"{projectile}: accounting hash")
    require(row["pass"] is False, f"{projectile}: mapping must fail")
    require(len(row["unaffected_sample"]) == 16, f"{projectile}: unaffected sample")
    require(len(set(row["unaffected_sample"])) == 16, f"{projectile}: unaffected uniqueness")
    require(len(row["conflict_examples"]) == 10, f"{projectile}: conflict examples")
    for example in row["conflict_examples"]:
        require(example["decision"] == "mf8_q_excitation_conflict", f"{projectile}: conflict type")
        derived = example["qm_ev"] - example["qi_ev"]
        require(example["matching_mf8_elfs_ev"], f"{projectile}: MF8 comparison")
        require(
            all(
                abs(elfs - derived) > max(1.0, 5e-6 * max(abs(elfs), abs(derived)))
                for elfs in example["matching_mf8_elfs_ev"]
            ),
            f"{projectile}: conflict arithmetic",
        )


def validate_changed(evidence: dict[str, Any], compressed: bytes, rows: list[dict[str, Any]]) -> None:
    metadata = evidence["changed_identities"]
    require(
        metadata
        == {
            "artifact": "results/g2_p18_changed_identities.json.gz",
            "artifact_sha256": CHANGED_SHA256,
            "canonical_sha256": CHANGED_CANONICAL_SHA256,
            "compressed_bytes": 898_317,
            "count": 45_320,
            "uncompressed_bytes": 19_346_122,
        },
        "changed-identity metadata",
    )
    payload = canonical_json(rows)
    require(len(payload) == metadata["uncompressed_bytes"], "changed payload bytes")
    require(hashlib.sha256(payload).hexdigest() == metadata["canonical_sha256"], "changed payload hash")
    require(len(compressed) == metadata["compressed_bytes"], "changed compressed bytes")
    require(hashlib.sha256(compressed).hexdigest() == metadata["artifact_sha256"], "changed artifact hash")
    require(gzip.compress(payload, compresslevel=9, mtime=0) == compressed, "deterministic gzip")
    require(len(rows) == metadata["count"], "changed row count")

    counts: Counter[str] = Counter()
    files: dict[str, set[str]] = defaultdict(set)
    keys: set[tuple[Any, ...]] = set()
    order = []
    for row in rows:
        require(set(row) == CHANGED_KEYS, "changed row schema")
        projectile = row["projectile"]
        require(projectile in EXPECTED_CORPORA, "changed projectile")
        require(row["old_rank_liso"] is not None, "changed baseline identity")
        require(row["old_rank_liso"] != row["candidate_liso"], "identity actually changed")
        require(row["mfs"] == sorted(set(row["mfs"])), "changed MF set")
        require(set(row["mfs"]).issubset({8, 9, 10}), "changed MF values")
        require(row["declarations"] > 0, "changed declaration count")
        key = (
            projectile,
            row["file"],
            row["mat"],
            row["mt"],
            row["zap"],
            row["raw_lfs"],
            row["old_rank_liso"],
            row["candidate_liso"],
            row["decision"],
        )
        require(key not in keys, "changed identity unique")
        keys.add(key)
        counts[projectile] += 1
        files[projectile].add(row["file"])
        order.append((projectile, row["file"], row["mat"], row["mt"], row["zap"], row["raw_lfs"]))
    require(order == sorted(order), "changed identity order")
    for projectile, mapping in evidence["mappings"].items():
        require(counts[projectile] == mapping["changed_identities"], f"{projectile}: changed count")
        require(len(files[projectile]) == mapping["changed_files"], f"{projectile}: changed files")


def validate_evidence(evidence: dict[str, Any], compressed: bytes, rows: list[dict[str, Any]]) -> None:
    require(evidence["schema"] == "actinv-p18-g2-corpus-audit-1", "G2 schema")
    require(evidence["gate"] == "P18-G2", "G2 gate")
    require(evidence["protocol_sha256"] == PROTOCOL_SHA256, "protocol identity")
    require(evidence["amendment_sha256"] == AMENDMENT_SHA256, "amendment identity")
    require(evidence["g0_sha256"] == G0_SHA256, "G0 identity")
    require(evidence["g1_sha256"] == G1_SHA256, "G1 identity")
    require(evidence["control_source_sha256"] == CONTROL_SHA256, "control identity")
    require(evidence["implementation_source_sha256"] == EXPECTED_IMPLEMENTATION, "implementation identity")
    require(evidence["measurement_values_read"] is False, "measurement quarantine")
    require(evidence["heldout_values_read"] is False, "held-out quarantine")
    require(
        evidence["limits"]
        == {
            "pointwise_abs_b": 1e-12,
            "collapsed_abs_b": 1e-14,
            "relative": 5e-10,
            "process_virtual_memory_bytes": 12_288_000_000,
            "single_array_bytes": 1_073_741_824,
        },
        "frozen limits",
    )
    require(set(evidence["corpora"]) == set(EXPECTED_CORPORA), "corpus set")
    for projectile, corpus in evidence["corpora"].items():
        validate_corpus(projectile, corpus)
        catalog = evidence["catalogs"][projectile]
        require(
            catalog
            == {
                "duplicate_evaluations": 0,
                "file_order_invariant": True,
                "issues": [],
                "nuclides": 2_323,
                "sha256": EXPECTED_CATALOG_HASHES[projectile],
                "states": 2_850,
            },
            f"{projectile}: catalog",
        )
        validate_mapping(projectile, evidence["mappings"][projectile], corpus)
    validate_changed(evidence, compressed, rows)
    require(
        evidence["fixture_tests"]
        == {
            "command": "cargo test -p actinv-data builder::tests -- --quiet",
            "returncode": 0,
            "pass": True,
        },
        "Rust mutation fixtures",
    )
    require(
        evidence["checks"]
        == {
            "complete_inventory": True,
            "duplicate_and_descriptor_consistency": True,
            "catalog_consistency_and_file_order": True,
            "declaration_accounting": False,
            "state_partial_conservation": False,
        },
        "G2 checks",
    )
    require(evidence["audit_complete"] is True, "audit complete")
    require(evidence["gate_pass"] is False and evidence["pass"] is False, "frozen G2 failure")
    require(
        not any(value.startswith("/") or "/tmp/" in value for value in strings(evidence)),
        "absolute path leaked into evidence",
    )


def mutation_plants(
    evidence: dict[str, Any], compressed: bytes, rows: list[dict[str, Any]]
) -> dict[str, bool]:
    plants: dict[str, bool] = {}

    def evidence_plant(name: str, mutate) -> None:
        mutant = copy.deepcopy(evidence)
        mutate(mutant)
        try:
            validate_evidence(mutant, compressed, rows)
        except AssertionError:
            plants[name] = True
        else:
            plants[name] = False

    evidence_plant("violation_count", lambda value: value["corpora"]["neutron"].__setitem__("violations", 0))
    evidence_plant("declaration_count", lambda value: value["corpora"]["proton"].__setitem__("declarations", 0))
    evidence_plant(
        "decision_count",
        lambda value: value["mappings"]["alpha"]["decision_counts"].__setitem__("mf8_q_excitation_conflict", 0),
    )
    evidence_plant("artifact_hash", lambda value: value["changed_identities"].__setitem__("artifact_sha256", "0" * 64))
    evidence_plant("threshold", lambda value: value["limits"].__setitem__("relative", 5e-9))
    evidence_plant(
        "excitation_conflict",
        lambda value: value["mappings"]["deuteron"]["conflict_examples"][0].__setitem__(
            "qi_ev", -16_460_448.0
        ),
    )

    changed_mutant = list(rows)
    changed_mutant[0] = copy.deepcopy(changed_mutant[0])
    changed_mutant[0]["candidate_liso"] = changed_mutant[0]["old_rank_liso"]
    try:
        validate_evidence(evidence, compressed, changed_mutant)
    except AssertionError:
        plants["changed_identity"] = True
    else:
        plants["changed_identity"] = False
    return dict(sorted(plants.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    require(sha256(PROTOCOL) == PROTOCOL_SHA256, "protocol bytes changed")
    require(sha256(AMENDMENT) == AMENDMENT_SHA256, "amendment bytes changed")
    require(sha256(G0) == G0_SHA256, "G0 bytes changed")
    require(sha256(G1) == G1_SHA256, "G1 bytes changed")
    require(sha256(CONTROL) == CONTROL_SHA256, "G2 control bytes changed")
    require(sha256(EVIDENCE) == EVIDENCE_SHA256, "G2 evidence bytes changed")
    require(sha256(CHANGED) == CHANGED_SHA256, "changed-identity artifact bytes changed")

    evidence = json.loads(EVIDENCE.read_text())
    compressed = CHANGED.read_bytes()
    payload = gzip.decompress(compressed)
    rows = json.loads(payload)
    require(isinstance(rows, list), "changed artifact root")
    validate_evidence(evidence, compressed, rows)
    plants = mutation_plants(evidence, compressed, rows)
    result = {
        "schema": "actinv-p18-g2-independent-check-1",
        "gate": "P18-G2",
        "evidence_sha256": EVIDENCE_SHA256,
        "changed_identities_sha256": CHANGED_SHA256,
        "checker_source_sha256": sha256(Path(__file__)),
        "checks": {
            "frozen_sources_and_thresholds": True,
            "complete_four_corpus_inventory": True,
            "declaration_and_comparison_accounting": True,
            "catalog_and_file_order_invariance": True,
            "changed_identity_artifact": True,
            "failure_arithmetic": True,
            "measurement_quarantine": True,
            "truthful_g2_failure": True,
        },
        "mutation_plants": plants,
        "audit_complete": True,
        "scientific_gate_pass": False,
        "verdict": "P18-G2-FAIL",
    }
    result["pass"] = all(result["checks"].values()) and all(plants.values())
    if arguments.no_write:
        require(RESULT.is_file(), f"missing committed G2 checker result {RESULT}")
        require(
            json.loads(RESULT.read_text()) == result,
            "committed G2 checker result is not reproducible",
        )
    else:
        RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
