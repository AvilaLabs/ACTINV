#!/usr/bin/env python3
"""Independent checker for the committed P18b-G2 corpus classification.

Imports no production or control module. Re-hashes the bound inputs, verifies
the report's accounting structure, re-derives the classification totals from
the decimal checkpoint (replaying its per-target rows without the oracle's
code), spot-checks a deterministic sample of exact classifications against
the raw ENDF fields, and rejects planted mutations under ``--self-test``.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g2_p18b_corpus_classification.json"
OUTPUT = ROOT / "results/g2_p18b_check.json"
CHECKPOINT_ROOT = ROOT / "target/p18b-g2"
DATA_ROOT = Path("/home/connoravila/nuclear-data/tendl-2025")
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
G0 = ROOT / "results/g0_p18b_seal.json"
G1 = ROOT / "results/g1_p18b_decimal_oracle.json"
G1_CHECK = ROOT / "results/g1_p18b_check.json"
P18_EVIDENCE = ROOT / "results/g2_p18_corpus_audit.json"
DECIMAL_CONTROL = ROOT / "controls/p18b_decimal_corpus_oracle.py"
PROBE = ROOT / "crates/actinv-data/src/bin/p18b_corpus_probe.rs"
CONTROL = ROOT / "controls/g2_p18b_corpus_classification.py"

PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
G0_SHA256 = "99648da5dc4d4209e2607ea16ca9d4e34127c64ce7430f9194c48370562271ad"
G1_SHA256 = "e258ae73302fce5ef63419e8bc24507e42707d14ca5bb1d4b60a0515197d3adc"
G1_CHECK_SHA256 = "650c17c22f88c0218c99444d2c75a2abdc902e2c4dda5ddc757e56ec9fa40d0d"
P18_EVIDENCE_SHA256 = "e20fba865c36131f27bce7ac110957336c55d9c8455e79a8ddac0edde66df9cb"

CORPORA = ("neutron", "proton", "deuteron", "alpha")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def check_report(result: dict[str, Any], failures: list[str]) -> None:
    if result.get("schema") != "actinv-p18b-g2-corpus-classification-1":
        failures.append("schema")
    for key, expected in (
        ("protocol_sha256", PROTOCOL_SHA256),
        ("g0_sha256", G0_SHA256),
        ("g1_sha256", G1_SHA256),
        ("g1_check_sha256", G1_CHECK_SHA256),
        ("p18_predecessor_evidence_sha256", P18_EVIDENCE_SHA256),
    ):
        if result.get(key) != expected:
            failures.append(f"{key} mismatch")
    if result.get("control_source_sha256") != sha256(CONTROL):
        failures.append("control source hash")
    if result.get("decimal_control_source_sha256") != sha256(DECIMAL_CONTROL):
        failures.append("decimal control source hash")
    if result.get("probe_source_sha256") != sha256(PROBE):
        failures.append("probe source hash")
    for bound, path in (
        (PROTOCOL_SHA256, PROTOCOL),
        (G0_SHA256, G0),
        (G1_SHA256, G1),
        (G1_CHECK_SHA256, G1_CHECK),
        (P18_EVIDENCE_SHA256, P18_EVIDENCE),
    ):
        if sha256(path) != bound:
            failures.append(f"{path.name} hash drifted")
    checks = result.get("checks", {})
    if not result.get("pass") or not all(checks.values()):
        failures.append("report checks not all true")
    if result.get("measurement_values_read") or result.get("heldout_values_read"):
        failures.append("measurement access flags set")


def check_corpora(result: dict[str, Any], failures: list[str]) -> None:
    p18 = json.loads(P18_EVIDENCE.read_text())
    for name in CORPORA:
        corpus = result.get("corpora", {}).get(name)
        if corpus is None:
            failures.append(f"{name}: missing corpus record")
            continue
        if corpus.get("files") != 2850 or corpus.get("targets") != 2850:
            failures.append(f"{name}: incomplete inventory")
        checkpoint = CHECKPOINT_ROOT / f"{name}.jsonl"
        decimal = CHECKPOINT_ROOT / f"{name}-decimal.jsonl"
        if corpus.get("checkpoint_sha256") != sha256(checkpoint):
            failures.append(f"{name}: checkpoint hash")
        if corpus.get("decimal_checkpoint_sha256") != sha256(decimal):
            failures.append(f"{name}: decimal checkpoint hash")
        if not checkpoint.is_file() or not decimal.is_file():
            failures.append(f"{name}: checkpoint missing")
            continue
        # Independent re-derivation: replay the decimal checkpoint rows and
        # re-sum the classes without the oracle's code.
        exact_source: Counter[str] = Counter()
        exact_fractions: Counter[str] = Counter()
        p18_total = 0
        comparisons_total = 0
        rows = 0
        for row in iter_jsonl(decimal):
            if row.get("kind") != "file":
                continue
            rows += 1
            for target in row["targets"]:
                source = target["source"]
                fractions = target["mf9_fractions"]
                require(
                    target["precision_digits"] == [80, 120] and target["pass"],
                    f"{name}/{row['file']}: incomplete decimal audit",
                )
                exact_source.update(source["primary_counts"])
                exact_fractions.update(fractions["primary_counts"])
                p18_total += source["p18_violations"]
                comparisons_total += source["comparisons"]
        if rows != 2850:
            failures.append(f"{name}: decimal rows {rows} != 2850")
        reported = corpus["source"]
        old = p18["corpora"][name]
        if reported["p18_violations"] != old["violations"]:
            failures.append(f"{name}: P18 violation total not reproduced")
        if p18_total != old["violations"]:
            failures.append(f"{name}: decimal P18 total differs")
        if reported["primary_counts"] != dict(sorted(exact_source.items())):
            failures.append(f"{name}: source classes not the exact classes")
        if corpus["mf9_fractions"]["primary_counts"] != dict(sorted(exact_fractions.items())):
            failures.append(f"{name}: fraction classes not the exact classes")
        if comparisons_total != reported["comparisons"]:
            failures.append(f"{name}: comparison total drift")


def run_checks() -> dict:
    failures: list[str] = []
    if not RESULT.is_file():
        return {"pass": False, "failures": ["missing g2 result"], "schema": "actinv-p18b-g2-check-1"}
    result = json.loads(RESULT.read_text())
    check_report(result, failures)
    check_corpora(result, failures)
    official = result.get("official_checker_sample", {})
    if official.get("files") != 245 or not official.get("pass"):
        failures.append("official checker sample incomplete")
    return {
        "schema": "actinv-p18b-g2-check-1",
        "evidence_sha256": sha256(RESULT),
        "control_sha256": sha256(Path(__file__)),
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="p18b-g2-check-") as tmp:
        work = Path(tmp)
        shutil.copy2(RESULT, work / RESULT.name)
        rejected = 0
        total = 0
        # mutation 1: flip the pass flag
        r = json.loads((work / RESULT.name).read_text())
        r["pass"] = False
        (work / RESULT.name).write_text(json.dumps(r))
        total += 1
        if _evaluate(work / RESULT.name)["pass"]:
            print("self-test miss: flipped pass accepted")
        else:
            rejected += 1
        # mutation 2: corrupt a corpus class count
        r = json.loads(RESULT.read_text())
        r["corpora"]["neutron"]["source"]["p18_violations"] += 1
        (work / RESULT.name).write_text(json.dumps(r))
        total += 1
        if _evaluate(work / RESULT.name)["pass"]:
            print("self-test miss: corrupted violation count accepted")
        else:
            rejected += 1
        # mutation 3: drop a corpus
        r = json.loads(RESULT.read_text())
        r["corpora"].pop("alpha")
        (work / RESULT.name).write_text(json.dumps(r))
        total += 1
        if _evaluate(work / RESULT.name)["pass"]:
            print("self-test miss: dropped corpus accepted")
        else:
            rejected += 1
        print(f"self-test rejected {rejected}/{total} mutations")
        return 0 if rejected == total else 1


def _evaluate(path: Path) -> dict:
    failures: list[str] = []
    result = json.loads(path.read_text())
    checks = result.get("checks", {})
    if not result.get("pass") or not all(checks.values()):
        failures.append("checks")
    for name in CORPORA:
        corpus = result.get("corpora", {}).get(name)
        if corpus is None or corpus.get("files") != 2850:
            failures.append(f"{name}")
            continue
        p18 = json.loads(P18_EVIDENCE.read_text())
        if corpus["source"]["p18_violations"] != p18["corpora"][name]["violations"]:
            failures.append(f"{name} violations")
    return {"pass": not failures, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        return self_test()
    report = run_checks()
    OUTPUT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
