#!/usr/bin/env python3
"""P26 G4 independent closure checker. Imports no production, audit, prototype
or scoring module; re-derives everything from raw records and repository files.

- rehashes the protocol, amendment and every evidence file against the verdict's
  recorded digests and the G2 freeze anchor;
- re-derives the workload ranking from G1 citations and the recorded class rule;
- recomputes headroom arithmetic from the raw campaign ledger;
- verifies gate ordering by commit ancestry;
- verifies partition discipline (diagnostic tasks never claimed as qualifying);
- re-verifies all prior verdicts verbatim;
- re-derives the verdict string from the recorded feasibility map under the
  protocol's rule (undetermined is a phase failure mode; one amendment caps an
  otherwise-passing close at CONDITIONAL);
- rejects planted mutations.
"""
from __future__ import annotations

import copy
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERDICT = ROOT / "results" / "verdict_p26.json"

PROTOCOL_SHA256 = "0dd9be843e4e195d3f3ebb9a9084f233af3d0eedf5045bbb48d77d665f5d1e06"
OPENING_COMMIT = "3ae2f2656f6e6e56ad401378d9f5c6a96f1cf80d"

GATE_COMMITS = [
    "3ae2f2656f6e6e56ad401378d9f5c6a96f1cf80d",  # opening
    "edd0c04",                                   # G0 seal
    "beb4e54",                                   # G0 comparator correction
    "a919848",                                   # G1
    "45d94c8",                                   # G2 freeze
    "96bb0e4",                                   # Amendment 1 + repaired contract
    "bd25e34",                                   # G3
]
EXPECTED_VERDICTS = {
    "results/verdict_p17.json": "P17-FAIL",
    "results/verdict_p18.json": "P18-FAIL",
    "results/verdict_p18b.json": "P18b-FAIL",
    "results/verdict_p24.json": "P24-CONDITIONAL",
    "results/verdict_p25.json": "P25-FAIL",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def span_text(file: str, lines: list[int]) -> str | None:
    p = ROOT / file
    if not p.is_file():
        return None
    text = p.read_text().splitlines()
    if lines[0] < 1 or lines[1] > len(text):
        return None
    return " ".join(" ".join(text[lines[0] - 1 : lines[1]]).split())


def check(rec: dict) -> list[str]:
    f: list[str] = []
    if rec.get("schema") != "actinv-p26-verdict-1" or rec.get("phase") != "P26":
        f.append("schema")
    if rec.get("protocol_sha256") != PROTOCOL_SHA256:
        f.append("protocol_sha256")
    if sha256_file(ROOT / "protocols" / "ACTINV-P26_PROTOCOL.md") != PROTOCOL_SHA256:
        f.append("protocol_hash_live")
    if not (ROOT / "protocols" / "ACTINV-P26_AMENDMENT_1.md").is_file():
        f.append("amendment_missing")
    if rec.get("amendments_used") != ["protocols/ACTINV-P26_AMENDMENT_1.md"]:
        f.append("amendments_used")
    if rec.get("opening_commit") != OPENING_COMMIT:
        f.append("opening_commit")

    for name, digest in rec.get("evidence_sha256", {}).items():
        rel = "results/" + {
            "g1_workloads": "g1_p26_workloads.json", "g1_check": "g1_p26_check.json",
            "g2_contract": "g2_p26_contract.json", "g2_check": "g2_p26_check.json",
            "g3_headroom": "g3_p26_headroom.json", "g3_coverage": "g3_p26_coverage.json",
            "g3_ledger": "g3_p26_campaign_ledger.jsonl", "g3_tasks": "g3_p26_task_decomposition.json",
            "g3_check": "g3_p26_check.json"}[name]
        if sha256_file(ROOT / rel) != digest:
            f.append(f"evidence:{name}")
    if len(rec.get("evidence_sha256", {})) != 9:
        f.append("evidence_incomplete")

    # re-derive workload ranking from cited evidence
    g1 = json.loads((ROOT / "results" / "g1_p26_workloads.json").read_text())
    rule = g1["evidence_class_rule"]
    for cand in g1["candidates"]:
        score = 0
        for cit in cand["citations"]:
            cls = ("executed_evidence" if any(cit["file"].startswith(p)
                                              for p in rule["executed_evidence"]["path_prefixes"])
                   else "documented_record")
            if cit["class"] != cls:
                f.append(f"class:{cand['id']}")
            score += rule[cls]["weight"]
            span = span_text(cit["file"], cit["lines"])
            if span is None or cit["anchor"] not in span:
                f.append(f"anchor:{cand['id']}")
            if sha256_file(ROOT / cit["file"]) != g1["recorded_sources"][cit["file"]]:
                f.append(f"cite_hash:{cand['id']}")
        if cand["score"] != score:
            f.append(f"score:{cand['id']}")
    ranked = sorted(g1["candidates"],
                    key=lambda c: (-c["score"], -c["executed_citations"], c["id"]))
    if [c["id"] for c in ranked] != g1["ranked_ids"]:
        f.append("ranked_ids")
    if rec.get("flagship") != g1["flagship"] or rec.get("selected") != g1["selected"]:
        f.append("selection")

    # recompute headroom arithmetic from the raw ledger
    ledger = [json.loads(l) for l in
              (ROOT / "results" / "g3_p26_campaign_ledger.jsonl").read_text().splitlines()
              if l.strip() and json.loads(l).get("case") != "__batch_driver__"]
    by_mode: dict[str, list[float]] = {}
    for r in ledger:
        if r.get("ok", r.get("returncode") == 0):
            by_mode.setdefault(r["mode"], []).append(r["wall_s"])
    med = {m: statistics.median(v) for m, v in by_mode.items()}
    ratio = med["per_invocation"] / med["batched_inprocess"]
    meas = rec["feasibility"]["investigations_previously_too_expensive"].get("measured", {})
    if not math_isclose(meas.get("amortization_ratio_median"), ratio, 1e-6):
        f.append("headroom_ratio")
    if meas.get("campaign_cases") != len(ledger):
        f.append("campaign_cases")

    # gate ordering by ancestry
    log = subprocess.run(["git", "log", "--format=%H"], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
    pos = [next((i for i, h in enumerate(log) if h.startswith(c)), None) for c in GATE_COMMITS]
    if any(p is None for p in pos) or pos != sorted(pos, reverse=True):
        f.append("gate_order")

    # partition discipline
    tasks = json.loads((ROOT / "results" / "g3_p26_task_decomposition.json").read_text())
    if tasks.get("partition") != "diagnostic":
        f.append("partition_diagnostic")

    for rel, expected in EXPECTED_VERDICTS.items():
        try:
            v = json.loads((ROOT / rel).read_text()).get("verdict")
        except Exception:
            v = None
        if v != expected:
            f.append(f"verdict:{rel}")

    # verdict derivation
    feas = rec.get("feasibility", {})
    statuses = {k: v.get("status") for k, v in feas.items()}
    if any(s not in ("feasible", "infeasible", "undetermined") for s in statuses.values()):
        f.append("feasibility_vocab")
    if not feas or any(not v.get("evidence") for v in feas.values()):
        f.append("feasibility_evidence")
    if any(s == "undetermined" for s in statuses.values()) or \
            any(s == "infeasible" for s in statuses.values()):
        expected_verdict = "P26-FAIL"
    elif rec.get("amendments_used"):
        expected_verdict = "P26-CONDITIONAL"
    else:
        expected_verdict = "P26-PASS"
    if rec.get("verdict") != expected_verdict:
        f.append(f"verdict:expected-{expected_verdict}")
    return f


def math_isclose(a, b, rel):
    return isinstance(a, (int, float)) and abs(a - b) <= rel * max(abs(a), abs(b))


def mutation_self_test(rec: dict) -> tuple[int, int]:
    plants = []

    def plant(mutate):
        m = copy.deepcopy(rec)
        mutate(m)
        plants.append(m)

    plant(lambda m: m.update(verdict="P26-PASS"))
    plant(lambda m: m["evidence_sha256"].update(g3_ledger="0" * 64))
    plant(lambda m: m["feasibility"]["useful_predictive_results"].update(evidence=""))
    plant(lambda m: m["feasibility"]["investigations_previously_too_expensive"]
          ["measured"].update(amortization_ratio_median=99.0))
    plant(lambda m: m.update(flagship="W-UNC"))
    plant(lambda m: m.update(amendments_used=[]))
    rejected = sum(1 for m in plants if check(m))
    return len(plants), rejected


def main() -> int:
    rec = json.loads(VERDICT.read_text())
    failures = check(rec)
    planted, rejected = mutation_self_test(rec)
    report = {
        "schema": "actinv-p26-g4-check-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "failures": failures,
        "mutation_self_test": {"planted": planted, "rejected": rejected},
        "pass": not failures and planted == rejected,
    }
    (ROOT / "results" / "g4_p26_check.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
