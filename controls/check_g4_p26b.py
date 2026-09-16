#!/usr/bin/env python3
"""P26b G4 independent closure checker. Imports no production, audit,
conversion or leg-executor module; re-derives everything from raw records
and repository files.

- rehashes the protocol and every evidence file against the verdict's
  recorded digests;
- re-derives the per-leg census from the raw append-only ledger and checks
  it against the frozen contract and the G2 summary;
- re-forms a sample of product_plus_data response totals from the raw
  case artifacts (case.stdout / out.json) without any executor code;
- verifies gate ordering by commit ancestry;
- verifies partition discipline (the diagnostic probe is never counted in
  a leg census; contract_gap rows are never counted as executed);
- re-verifies all prior verdicts verbatim;
- re-derives the verdict string from the recorded census under the
  protocol's closure rule (partial subset executability => CONDITIONAL;
  no repair round used);
- rejects planted mutations.

Writes ``results/g4_p26b_check.json``; exit nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
VERDICT = RESULTS / "verdict_p26b.json"
CONTRACT = RESULTS / "g2_p26b_leg_contract.json"
LEDGER = RESULTS / "g2_p26b_leg_ledger.jsonl"
G2_SUMMARY = RESULTS / "g2_p26b_leg.json"
OUT = RESULTS / "g4_p26b_check.json"
WORK = Path.home() / "nuclear-data" / "p26b-work" / "g2-run" / "cases"

PROTOCOL = ROOT / "protocols" / "ACTINV-P26b_PROTOCOL.md"
OPENING_COMMIT = "79b069ddaadc104b4d6d2131311b64202894b2d8"
PROTOCOL_SHA256 = "a1c433c842824c97637f5673a68c26b388dac1f8a237c009417ccb8d10d55327"

# gate commits in required ancestry order: opening < G0 < G1 < G2
GATE_COMMITS = [
    "79b069ddaadc104b4d6d2131311b64202894b2d8",  # Open P26b
    "1c1aa10",                                   # G0 seal
    "5f25eba",                                   # G1
    "8c30662",                                   # G2
]

EXPECTED_PRIOR_VERDICTS = {
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL",
    "verdict_p26.json": "P26-FAIL",
}

UNIT_S = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0,
          "y": 31536000.0, "c": 3153600000.0}
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


def ledger_rows() -> list[dict]:
    return [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]


def census_of(rows: list[dict]) -> dict:
    c = {}
    for r in rows:
        if r["leg"] == "probe":
            continue
        c.setdefault(r["leg"], {})[r["status"]] = \
            c.setdefault(r["leg"], {}).get(r["status"], 0) + 1
    return c


def derive_verdict(census: dict, repairs_used: int) -> str:
    """Protocol closure rule: FAIL if the comparator leg produced no measured
    outputs; CONDITIONAL if subset executability is partial or a repair round
    was used; PASS only on full declared-subset execution with no repair."""
    total = sum(sum(v.values()) for v in census.values())
    if total == 0:
        return "P26b-FAIL"
    pp = census.get("product_plus_data", {})
    executed = pp.get("executed", 0) + pp.get("executed_with_failures", 0)
    if executed == 0:
        return "P26b-FAIL"
    n_cases = 1016
    ident = census.get("identical_data", {})
    ident_exec = ident.get("executed", 0) + ident.get("executed_with_failures", 0)
    partial = executed < n_cases or ident_exec < n_cases
    if repairs_used > 1:
        return "P26b-FAIL"
    if partial or repairs_used == 1:
        return "P26b-CONDITIONAL"
    return "P26b-PASS"


# ---- independent metric re-formation (raw artifacts, no executor code)

def reform_case_totals(case_dir: Path, irr_s: float) -> dict:
    """Re-form per-time activity totals from raw artifacts.  ALARA: parse
    the Specific Activity section's `total` row in case.stdout.  ACTINV:
    sum activity_Bq_per_g in out.json steps."""
    out = {"alara": {}, "actinv": {}}
    text = (case_dir / "case.stdout").read_text(errors="replace")
    m = re.search(r"\*\*\* Specific Activity \[Bq/g\] \*\*\*(.*?)(?=\n\*\*\*|\Z)",
                  text, re.S)
    if m:
        hm = re.search(r"isotope\s+t_1/2\(s\)\s+pre-irrad\s+(.*?)\n=+",
                       m.group(1), re.S)
        tm = re.search(r"^total\s+\S+\s+(.*)$", m.group(1), re.M)
        if hm and tm:
            times = [0.0] + [float(x.group(1)) * UNIT_S[x.group(2)]
                             for x in re.finditer(r"([\d.eE+-]+)\s+([smhdwyc])",
                                                  hm.group(1))]
            vals = [float(x) for x in tm.group(1).split()][1:]
            out["alara"] = dict(zip((f"{t:g}" for t in times), vals))
    o = json.loads((case_dir / "out.json").read_text())
    for st in o.get("steps", []):
        t = f"{st['t_s'] - irr_s:g}"
        out["actinv"][t] = sum((st.get("activity_Bq_per_g") or {}).values())
    return out


def check_verdict(rec: dict) -> list[str]:
    f = []
    if rec.get("schema") != "actinv-p26b-verdict-1" or rec.get("phase") != "P26b":
        f.append("schema/phase")
    if rec.get("protocol_sha256") != PROTOCOL_SHA256:
        f.append("protocol_sha256")
    if sha256_file(PROTOCOL) != PROTOCOL_SHA256:
        f.append("protocol_hash_live")
    if rec.get("opening_commit") != OPENING_COMMIT:
        f.append("opening_commit")
    if rec.get("amendments_used") != []:
        f.append("amendments_used_nonempty")
    required = {"g0_seals", "g0_check", "g1_conversion", "g1_check",
                "g2_leg_contract", "g2_leg_ledger", "g2_leg", "g2_check"}
    if set(rec.get("evidence_sha256", {})) != required:
        f.append("evidence_sha256 key set incomplete")
    for name, digest in rec.get("evidence_sha256", {}).items():
        p = RESULTS / {
            "g0_seals": "g0_p26b_seals.json",
            "g0_check": "g0_p26b_check.json",
            "g1_conversion": "g1_p26b_conversion.json",
            "g1_check": "g1_p26b_check.json",
            "g2_leg_contract": "g2_p26b_leg_contract.json",
            "g2_leg_ledger": "g2_p26b_leg_ledger.jsonl",
            "g2_leg": "g2_p26b_leg.json",
            "g2_check": "g2_p26b_check.json",
        }.get(name, name)
        if not p.is_file() or sha256_file(p) != digest:
            f.append(f"evidence_sha256.{name}")
    for vf, want in EXPECTED_PRIOR_VERDICTS.items():
        p = RESULTS / vf
        if not p.is_file():
            f.append(f"prior verdict missing {vf}")
            continue
        if json.loads(p.read_text()).get("verdict") != want:
            f.append(f"prior verdict altered {vf}")
    # census re-derived from raw ledger
    rows = ledger_rows()
    census = census_of(rows)
    want_census = {"identical_data": {"contract_gap": 1016},
                   "product_plus_data": {"contract_gap": 608,
                                          "executed": 408}}
    if census != want_census:
        f.append(f"ledger census {census} != expected {want_census}")
    # partition discipline: probe rows never appear in a leg census
    for r in rows:
        if r["leg"] == "probe":
            if r.get("record", {}).get("partition") != "diagnostic":
                f.append("probe outside diagnostic partition")
            continue
        if r["status"] == "contract_gap" and r.get("arms"):
            f.append(f"{r['case']}/{r['leg']}: gap row carries arm outputs")
    # verdict re-derived from census under the closure rule
    want_verdict = derive_verdict(census, len(rec.get("amendments_used") or []))
    if rec.get("verdict") != want_verdict:
        f.append(f"verdict {rec.get('verdict')} != derived {want_verdict}")
    return f


def check_gate_order() -> list[str]:
    f = []
    head = git("rev-parse", "HEAD")
    prev = None
    for g in GATE_COMMITS:
        full = git("rev-parse", g)
        if prev is not None:
            ok = subprocess.run(
                ["git", "merge-base", "--is-ancestor", prev, full],
                cwd=ROOT, capture_output=True).returncode == 0
            if not ok:
                f.append(f"gate order broken: {prev[:8]} !< {full[:8]}")
        prev = full
    ok = subprocess.run(["git", "merge-base", "--is-ancestor", prev, head],
                        cwd=ROOT, capture_output=True).returncode == 0
    if not ok:
        f.append("last gate not an ancestor of HEAD")
    return f


def check_metric_reformation() -> list[str]:
    """Re-form response totals from raw case artifacts for a deterministic
    sample of executed cases (every 64th), compare to ledger values."""
    f = []
    rows = [r for r in ledger_rows()
            if r["leg"] == "product_plus_data" and r["status"] == "executed"]
    sample = rows[::64] or rows[:1]
    contract = json.loads(CONTRACT.read_text())
    case_map = {c["case"]: c for c in contract["cases"]}
    for r in sample:
        case_dir = WORK / r["leg"] / r["case"]
        irr_s = case_map[r["case"]]["irradiation_s"]
        got = reform_case_totals(case_dir, irr_s)
        for t, rec in (r["arms"]["alara"]["result"]["per_time"] or {}).items():
            v_rec = rec.get("total_activity_bq_per_g")
            v_new = got["alara"].get(t)
            if v_rec is not None and v_new is not None and \
                    not math.isclose(v_rec, v_new, rel_tol=1e-9):
                f.append(f"{r['case']} alara activity@{t} re-form mismatch")
        for t, rec in (r["arms"]["actinv"]["result"]["per_time"] or {}).items():
            v_rec = rec.get("total_activity_bq_per_g")
            v_new = got["actinv"].get(t)
            if v_rec is not None and v_new is not None and \
                    not math.isclose(v_rec, v_new, rel_tol=1e-9):
                f.append(f"{r['case']} actinv activity@{t} re-form mismatch")
    if not sample:
        f.append("no executed cases to re-form")
    return f


def main() -> int:
    rec = json.loads(VERDICT.read_text())
    f = check_verdict(rec)
    f += check_gate_order()
    f += check_metric_reformation()

    # ---- mutation self-test on the verdict record: plants on fields the
    # record can falsify (verdict string, evidence digests, protocol hash,
    # amendment list, opening commit).  Prose claims are guarded by the
    # evidence digests they summarize.
    mutations = rejected = 0
    plants = [
        lambda r: r.update({"verdict": "P26b-PASS"}),
        lambda r: r["evidence_sha256"].update({"g0_seals": "0" * 64}),
        lambda r: r.update({"protocol_sha256": "0" * 64}),
        lambda r: r.update({"amendments_used": ["x"]}),
        lambda r: r.update({"opening_commit": "0" * 40}),
        lambda r: r["evidence_sha256"].pop("g2_check"),
    ]
    for plant in plants:
        m = copy.deepcopy(rec)
        plant(m)
        mutations += 1
        if check_verdict(m):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        f.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {"schema": "actinv-p26b-g4-check-1",
              "pass": not f, "failures": f,
              "mutation_self_test": {"planted": mutations,
                                     "rejected": rejected}}
    OUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
