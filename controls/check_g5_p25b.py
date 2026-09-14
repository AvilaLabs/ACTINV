#!/usr/bin/env python3
"""P25b G5 closure checker — imports no production, audit or scoring
module.  Rehashes every evidence artifact, recomputes the outcome
accounting from raw records, re-derives the coverage floor table under
both builders, verifies gate ordering via git ancestry, re-verifies
all prior verdicts verbatim, and rejects planted mutations.

Writes ``results/g5_p25b_check.json``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
VERDICT = RESULTS / "verdict_p25b.json"
ACCOUNTING = RESULTS / "g5_p25b_accounting.json"
HELDOUT = RESULTS / "g5_p18b_heldout.json"
G1 = RESULTS / "g1_p25b_census.json"
REBIND = RESULTS / "g4_p25b_rebind.json"
UNION = RESULTS / "g4_p25b_union.json"
OUT = RESULTS / "g5_p25b_check.json"

CORPORA = ["tendl_2023", "fendl_32c", "eaf_2010"]
FLOORS = {"eaf_2010": 47, "tendl_2023": 41, "fendl_32c": 34}
DOSIMETRY = {"Ni-58", "Au-197", "Ag-109", "Nb-93", "In-113", "In-115"}

PRIOR_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL", "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS", "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS", "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL", "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL", "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL", "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL", "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS", "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL", "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL", "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS", "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS", "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS", "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL", "verdict_p26.json": "P26-FAIL",
}

GATE_COMMITS = [  # G0, G1, G2, G3, G4 — each must be an ancestor of the next
    "3fd5d5b", "e8f54a1", "81c4c7d", "cb6da18", "7cf2a64",
]

NAMED_OUTCOMES = re.compile(
    r"^(constructed|scored|zero_prediction|corpus_incomplete|"
    r"construction_failed:[a-z_0-9]+|format_unsupported|"
    r"undefined_ratio:[a-z_0-9]+|eligibility:[a-z_0-9]+)$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          cwd=ROOT).stdout.strip()


def check(verdict: dict, accounting: dict, floors: dict) -> list[str]:
    failures = []
    if verdict.get("verdict") not in ("P25b-PASS", "P25b-FAIL"):
        failures.append("verdict value not in vocabulary")
    if verdict.get("verdict") == "P25b-PASS" and not any(
            (pc or {}).get("qualified")
            for pc in (verdict.get("per_corpus") or {}).values()):
        failures.append("P25b-PASS with no qualified candidate")
    if verdict.get("blind_evidence") is not False:
        failures.append("blind_evidence must be false in this phase")

    # evidence hashes
    for name, want in (verdict.get("evidence_sha256") or {}).items():
        p = RESULTS / name
        if not p.is_file() or sha256(p) != want:
            failures.append(f"evidence hash mismatch: {name}")
    if sha256(ACCOUNTING) != verdict.get("accounting_sha256"):
        failures.append("accounting hash mismatch")

    # gate ordering by git ancestry
    for a, b in zip(GATE_COMMITS, GATE_COMMITS[1:]):
        r = subprocess.run(["git", "merge-base", "--is-ancestor", a, b],
                           capture_output=True, cwd=ROOT)
        if r.returncode != 0:
            failures.append(f"gate ordering: {a} not ancestor of {b}")

    # release hold
    if subprocess.run(["git", "tag", "-l", "v1.1*"], capture_output=True,
                      text=True, cwd=ROOT).stdout.split():
        failures.append("v1.1* tag exists")
    if "## Unreleased" not in (ROOT / "CHANGELOG.md").read_text():
        failures.append("release hold marker missing")

    # prior verdicts verbatim
    for name, want in PRIOR_VERDICTS.items():
        got = json.loads((RESULTS / name).read_text()).get("verdict")
        if got != want:
            failures.append(f"prior verdict {name}: {got!r} != {want!r}")

    # accounting recomputed from raw records
    union = json.loads(UNION.read_text())
    for corpus in CORPORA:
        blk = accounting["corpora"].get(corpus) or {}
        rows = [e for e in union["corpora"][corpus]["construction"]
                if e["in_irdff"]]
        pop_a = blk.get("population_a") or {}
        if len(pop_a) != 47 or len(pop_a) != len(rows):
            failures.append(f"{corpus}: population_a size {len(pop_a)}")
        if any(not NAMED_OUTCOMES.match(v) for v in pop_a.values()):
            failures.append(f"{corpus}: unnamed population_a outcome")
        hist = Counter(pop_a.values())
        if dict(hist) != (blk.get("population_a_histogram") or {}):
            failures.append(f"{corpus}: population_a histogram mismatch")
        constructed = hist.get("constructed", 0)
        if constructed != blk["coverage"]["v110_union_ok"]:
            failures.append(f"{corpus}: v110 ok count mismatch")
        dos = sum(1 for t, v in pop_a.items()
                  if t in DOSIMETRY and v == "constructed")
        if f"{dos}/6" != blk["coverage"]["dosimetry_critical_ok_v110"]:
            failures.append(f"{corpus}: dosimetry subcensus mismatch")

        pop_b = blk.get("population_b_rows") or {}
        hb = Counter(pop_b.values())
        if dict(hb) != (blk.get("population_b_histogram") or {}):
            failures.append(f"{corpus}: population_b histogram mismatch")
        if any(not NAMED_OUTCOMES.match(v) for v in pop_b.values()):
            failures.append(f"{corpus}: unnamed population_b outcome")
        # denominator: rows on shipped isomeric targets only
        shipped = {e["za"] for e in
                   union["corpora"][corpus]["construction"]
                   if e["in_isomeric"] and e.get("candidate_files")}
        ledger = json.loads(HELDOUT.read_text())["ledger"]
        want_rows = 0
        for r in ledger:
            if r["projectile"] != "neutron" or not r.get("isomer_liso"):
                continue
            tz, ta = r["family_id"].split("|")[1].split("-")
            if int(tz) * 1000 + int(ta) in shipped:
                want_rows += 1
        if len(pop_b) != want_rows:
            failures.append(
                f"{corpus}: population_b denominator {len(pop_b)} "
                f"!= {want_rows}")

        cov = blk["coverage"]
        if floors[corpus]["v110_irdff_ok"] != cov["v110_union_ok"]:
            failures.append(f"{corpus}: coverage table vs accounting")
        if cov.get("floor_met_v110") != \
                (cov["v110_union_ok"] >= FLOORS[corpus]):
            failures.append(f"{corpus}: floor_met_v110 inconsistent")
        if cov.get("floor_met_v101") != \
                (cov["v101_post_repair_ok"] >= FLOORS[corpus]):
            failures.append(f"{corpus}: floor_met_v101 inconsistent")

        # verdict consistency: qualified <=> no reasons
        pc = (verdict.get("per_corpus") or {}).get(corpus) or {}
        if pc.get("qualified") != (not pc.get("reasons")):
            failures.append(f"{corpus}: qualified/reasons inconsistent")
        if pc.get("qualified") and verdict.get("verdict") == "P25b-FAIL":
            failures.append(f"{corpus}: qualified under FAIL verdict")
    return failures


def main() -> int:
    verdict = json.loads(VERDICT.read_text())
    accounting = json.loads(ACCOUNTING.read_text())

    g1 = json.loads(G1.read_text())
    g1_ok = {c: (g1["histograms"].get(c) or {}).get("ok", 0)
             for c in CORPORA}
    rebind = json.loads(REBIND.read_text())
    recovered = Counter(r["corpus"] for r in rebind["rows"]
                        if r.get("class") == "ok")
    union = json.loads(UNION.read_text())
    floors = {}
    for corpus in CORPORA:
        v110 = sum(1 for e in union["corpora"][corpus]["construction"]
                   if e["in_irdff"] and e["class"] == "ok")
        floors[corpus] = {
            "floor": FLOORS[corpus],
            "v101_post_repair_ok": g1_ok[corpus] + recovered.get(corpus, 0),
            "v110_irdff_ok": v110,
            "floor_met_v101":
                g1_ok[corpus] + recovered.get(corpus, 0) >= FLOORS[corpus],
            "floor_met_v110": v110 >= FLOORS[corpus]}

    failures = check(verdict, accounting, floors)

    mutations = []
    def expect_reject(label, v=None, a=None):
        vv = copy.deepcopy(verdict if v is None else v)
        aa = copy.deepcopy(accounting if a is None else a)
        if check(vv, aa, floors):
            mutations.append(label)

    v = copy.deepcopy(verdict); v["verdict"] = "P25b-PASS"
    expect_reject("verdict flip rejected", v=v)
    v = copy.deepcopy(verdict)
    v["per_corpus"]["eaf_2010"]["qualified"] = True
    v["per_corpus"]["eaf_2010"]["reasons"] = []
    expect_reject("qualified flip rejected", v=v)
    a = copy.deepcopy(accounting)
    a["corpora"]["eaf_2010"]["coverage"]["v110_union_ok"] = 47
    expect_reject("coverage inflation rejected", a=a)
    a = copy.deepcopy(accounting)
    a["corpora"]["eaf_2010"]["coverage"]["floor_met_v110"] = True
    expect_reject("floor flag flip rejected", a=a)
    a = copy.deepcopy(accounting)
    a["corpora"]["tendl_2023"]["population_a"]["Ni-58"] = "constructed"
    expect_reject("outcome rewrite rejected", a=a)
    v = copy.deepcopy(verdict); v["blind_evidence"] = True
    expect_reject("blind-evidence claim rejected", v=v)

    out = {
        "schema": "actinv-p25b-g5-check-1",
        "pass": not failures and len(mutations) == 6,
        "failures": failures,
        "mutations_rejected": mutations,
        "coverage_floors": floors,
        "verdict_sha256": sha256(VERDICT),
        "accounting_sha256": sha256(ACCOUNTING),
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
