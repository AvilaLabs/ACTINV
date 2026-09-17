#!/usr/bin/env python3
"""P27 G4 independent closure checker. Imports no production or control
module; re-derives everything from raw records and repository files.

- rehashes the protocol and every evidence file against the verdict's
  recorded digests;
- re-derives gate ordering by commit ancestry;
- re-verifies the smoke population against the frozen G0 seal and the
  manifest/spec bytes on disk (not the checkers' records);
- re-reads the Core run report and requires every stage at its closed
  status with all categorical verdicts pass;
- re-counts the adversarial battery against the frozen G0 list and
  requires every class rejected;
- re-verifies the prior P26/P26b verdicts verbatim;
- re-derives the P27 verdict under the closure rule (all gates closed and
  all battery classes rejected => PASS; anything less => CONDITIONAL/FAIL);
- rejects planted mutations.

Writes ``results/g4_p27_check.json``; exit nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
VERDICT = RESULTS / "verdict_p27.json"
WORK = Path.home() / "nuclear-data" / "p27-work"

PROTOCOL = ROOT / "protocols" / "ACTINV-P27_PROTOCOL.md"
OPENING_COMMIT = "2a6b298c0c9f7ca7a8390acbdd0ff906ce5ce4d6"
PROTOCOL_SHA256 = "70fef47884e59d70655737346fd1a84c2e415fce50cce0fed9c06b216a7fa73e"

GATE_COMMITS = [
    "2a6b298c0c9f7ca7a8390acbdd0ff906ce5ce4d6",  # Open P27
    "6cde74a",                                   # G0 seal
    "23dd45f",                                   # G1
    "1a7ca38",                                   # G2
    "f905365",                                   # G3
]

EXPECTED_PRIOR_VERDICTS = {
    "verdict_p26.json": "P26-FAIL",
    "verdict_p26b.json": "P26b-CONDITIONAL",
}

failures: list[str] = []


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args) -> str:
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def check(v: dict) -> None:
    if v.get("phase") != "P27" or v.get("schema") != "actinv-p27-verdict-1":
        failures.append("verdict phase/schema mismatch")
    if sha256_file(PROTOCOL) != v["protocol_sha256"]:
        failures.append("protocol digest != verdict record")
    if v["opening_commit"] != OPENING_COMMIT:
        failures.append("opening_commit mismatch")

    required_evidence = {
        "g0_seals", "g0_check", "g1_study_record", "g1_check",
        "g2_interchange", "g2_check", "g3_battery", "g3_check"}
    if set(v["evidence_sha256"]) != required_evidence:
        failures.append("evidence set != required keys")
    for name, want in v["evidence_sha256"].items():
        rel = {
            "g0_seals": "g0_p27_seals.json",
            "g0_check": "g0_p27_check.json",
            "g1_study_record": "g1_p27_study.json",
            "g1_check": "g1_p27_check.json",
            "g2_interchange": "g2_p27_interchange.json",
            "g2_check": "g2_p27_check.json",
            "g3_battery": "g3_p27_battery.json",
            "g3_check": "g3_p27_check.json",
        }.get(name)
        if not rel:
            failures.append(f"unknown evidence key {name}")
            continue
        p = RESULTS / rel
        if not p.is_file() or sha256_file(p) != want:
            failures.append(f"evidence {rel} digest mismatch/missing")

    # gate ancestry: each gate commit must descend from the previous
    head = git("rev-parse", "HEAD")
    for i, c in enumerate(GATE_COMMITS):
        try:
            git("merge-base", "--is-ancestor", c, head)
        except subprocess.CalledProcessError:
            failures.append(f"gate commit {c} not an ancestor of HEAD")
        if i:
            try:
                git("merge-base", "--is-ancestor",
                    GATE_COMMITS[i - 1], c)
            except subprocess.CalledProcessError:
                failures.append(f"gate order violated at {c}")

    # smoke population: manifest on disk must match the G0 seal
    g0 = json.loads((RESULTS / "g0_p27_seals.json").read_text())
    smoke = g0["smoke_population"]
    manifest = json.loads((WORK / "study" / "run1" / "manifest.json")
                          .read_text())
    ids = [c["case_id"] for c in manifest["cases"]]
    expected = [
        f"{m}__{sp}__{sc}"
        for m in smoke["materials"]
        for sp in smoke["spectra"]
        for sc in smoke["schedules_s"]
    ]
    if sorted(ids) != sorted(expected):
        failures.append("manifest case ids != frozen smoke population")
    if len(ids) != smoke["expected_cases"]:
        failures.append("manifest case count != frozen population size")
    for c in manifest["cases"]:
        sp = WORK / "study" / "run1" / c["spec"]
        if not sp.is_file() or sha256_file(sp) != c["spec_sha256"]:
            failures.append(f"spec {c['spec']} digest mismatch")

    # Core run report: every stage closed, all verdicts pass
    g2 = json.loads((RESULTS / "g2_p27_interchange.json").read_text())
    rep = json.loads(Path(g2["run_report"]["path"])
                     .read_text().split("\n", 1)[1])
    for stage, want in [("integrity", "complete"), ("compile", "compiled"),
                        ("execution", "executed"), ("bindings", "verified"),
                        ("campaign", "evaluated")]:
        if rep.get(stage, {}).get("status") != want:
            failures.append(f"Core stage {stage} = "
                            f"{rep.get(stage, {}).get('status')}")
    for verd in rep["campaign"]["verdicts"]:
        if verd["verdict"]["status"] != "pass":
            failures.append(f"{verd['requirement_id']} did not pass")

    # battery: exactly the frozen classes, all rejected
    g3 = json.loads((RESULTS / "g3_p27_battery.json").read_text())
    if {i["item"] for i in g3["battery"]} != set(g0["adversarial_battery"]):
        failures.append("battery classes != frozen G0 list")
    if not all(i["rejected"] for i in g3["battery"]):
        failures.append("battery contains an accepted mutation")
    for gate in ("g1_p27_check.json", "g2_p27_check.json",
                 "g3_p27_check.json"):
        if not json.loads((RESULTS / gate).read_text()).get("pass"):
            failures.append(f"{gate} does not record pass")

    # prior verdicts verbatim
    for f, want in EXPECTED_PRIOR_VERDICTS.items():
        pv = json.loads((RESULTS / f).read_text())
        if pv.get("verdict") != want:
            failures.append(f"{f}: {pv.get('verdict')} != {want}")

    # verdict re-derivation
    all_gates = (not failures)
    derived = "P27-PASS" if all_gates else "P27-CONDITIONAL"
    if v["verdict"] != derived:
        failures.append(f"verdict {v['verdict']} != derived {derived}")


def mutation_self_test() -> tuple[int, int]:
    v = json.loads(VERDICT.read_text())
    planted = rejected = 0

    def probe(mut, label):
        nonlocal planted, rejected
        planted += 1
        vv = copy.deepcopy(v)
        mut(vv)
        global failures
        keep = failures.copy()
        failures.clear()
        check(vv)
        if failures:
            rejected += 1
        else:
            print(f"  UNREJECTED: {label}", file=sys.stderr)
        failures.clear()
        failures.extend(keep)

    probe(lambda vv: vv.__setitem__("verdict", "P27-CONDITIONAL"),
          "downgraded verdict string")
    probe(lambda vv: vv["evidence_sha256"].__setitem__(
        "g1_check", "0" * 64), "forged evidence digest")
    probe(lambda vv: vv.__setitem__(
        "protocol_sha256", "0" * 64), "forged protocol digest")
    probe(lambda vv: vv.__setitem__("opening_commit", "0" * 40),
          "forged opening commit")
    probe(lambda vv: vv["evidence_sha256"].pop("g3_battery"),
          "dropped evidence entry")
    probe(lambda vv: vv.__setitem__(
        "phase", "P26b"), "wrong phase")
    return planted, rejected


def main() -> None:
    verdict = json.loads(VERDICT.read_text())
    check(verdict)
    planted, rejected = mutation_self_test()
    result = {
        "gate": "G4", "phase": "P27",
        "pass": not failures and planted == rejected,
        "failures": failures,
        "mutation_self_test": {"planted": planted, "rejected": rejected},
    }
    OUT = RESULTS / "g4_p27_check.json"
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
