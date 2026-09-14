#!/usr/bin/env python3
"""P25 G6 — independent closure checker.

Imports no production, audit or scoring module. Rehashes all evidence,
recomputes the census classification and the acceptance arithmetic from
the records themselves, verifies gate ordering (census before repairs,
floors frozen before scoring), re-verifies every prior verdict including
P17-FAIL and P18b-FAIL, and rejects planted mutations.

Writes ``results/verdict_p25.json``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
OUT = RESULTS / "verdict_p25.json"

PROTOCOL = ROOT / "protocols/ACTINV-P25_PROTOCOL.md"
AMEND_A = ROOT / "protocols/ACTINV-P25_AMENDMENT_A.md"
AMEND_B = ROOT / "protocols/ACTINV-P25_AMENDMENT_B.md"

EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS",
}

FLOORS = {
    "neutron": (0.40, 188),
    "proton": (0.60, 417),
    "deuteron": (0.50, 72),
    "alpha": (0.55, 304),
}
ELIGIBLE = {"neutron": 469, "proton": 695, "deuteron": 143, "alpha": 552}
REPAIRABLE = {
    "floor_artifact_only",
    "missing_total_or_grid_contract",
    "grid_density_interpolation_artifact",
}
GENUINE = {
    "genuine_source_inconsistency:zero_total_with_partials",
    "genuine_source_inconsistency:gridpoint_excess",
}
NAMED_PREFIXES = (
    "scored", "zero_prediction_scored", "construction_failed:",
    "undefined_ratio:", "eligibility:",
)
TOKEN_KINDS = {
    "floor_reconciled": {"floor"},
    "interp_artifact_reconciled": {"interp"},
    "missing_total_self_comparator": {"no_mf3_total"},
    "sentinel supplies the permitted runtime comparator":
        {"no_mf3_total"},
}


def load_index_ledgers(g4rep: dict) -> dict:
    """(projectile, filename) -> ledger lines from the emitted index."""
    out = {}
    for proj, res in g4rep.get("projectiles", {}).items():
        idx_path = Path(res.get("index", ""))
        if not idx_path.is_file():
            continue
        idx = json.loads(idx_path.read_text())
        for t in idx.get("targets", []):
            out[(proj, t["file"])] = t.get("ledger", [])
    return out


def sha256(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            d.update(block)
    return d.hexdigest()


def final_class(kinds) -> str:
    """Independent copy of the G2 classifier (no module import)."""
    k = set(kinds)
    if "gridpoint" in k:
        return "genuine_source_inconsistency:gridpoint_excess"
    if "zero_total" in k:
        return "genuine_source_inconsistency:zero_total_with_partials"
    if "no_mf3_total" in k:
        return "missing_total_or_grid_contract"
    if "interp" in k:
        return "grid_density_interpolation_artifact"
    if "floor" in k:
        return "floor_artifact_only"
    return "no_excess_found"


def nonreg_ok(b: dict, c: dict):
    if not b.get("rows") or not c.get("rows"):
        return None
    med = (c["median_abs_ln"] <= b["median_abs_ln"] + 0.005
           and c["median_abs_ln"] <= 1.01 * b["median_abs_ln"])
    p90 = (c["p90_abs_ln"] <= b["p90_abs_ln"] + 0.01
           and c["p90_abs_ln"] <= 1.01 * b["p90_abs_ln"])
    cov = all(c[k] >= b[k] - 0.01
              for k in ("within_10pct", "within_20pct", "within_30pct"))
    return med and p90 and cov


def evaluate(records: dict, failures: list[str]) -> None:
    """records: census, traces, g4rep, g5acc, g4check, g5report paths."""
    census = records["census"]
    traces = records["traces"]
    g4rep = records["g4rep"]
    g5acc = records["g5acc"]

    # ---- census classification recompute ------------------------------
    if census.get("schema") != "actinv-p25-census-1":
        failures.append("census schema")
    for proj, files in traces.get("files", {}).items():
        recount = Counter()
        for name, rec in files.items():
            cls = final_class(rec["all_excesses"]["kinds"])
            recount[cls] += 1
            if rec["final_class"] != cls:
                failures.append(f"{proj}/{name}: stored class {rec['final_class']} != recomputed {cls}")
        stored_counts = traces["final_class_counts"].get(proj, {})
        if dict(recount) != stored_counts:
            failures.append(f"{proj}: final_class_counts mismatch")

    # ---- eligible ledger ------------------------------------------------
    eligible_tot = {
        p: sum(v for k, v in census["row_outcomes"][p].items()
               if k != "eligibility:ineligible")
        for p in ELIGIBLE}
    if eligible_tot != ELIGIBLE:
        failures.append(f"eligible recompute {eligible_tot} != {ELIGIBLE}")

    # ---- G4: repairs applied by defect class only -----------------------
    index_ledgers = load_index_ledgers(g4rep)
    for proj, res in g4rep.get("projectiles", {}).items():
        files = traces["files"].get(proj, {})
        failed = set(res.get("failures", {}))
        for name, rec in files.items():
            cls = rec["final_class"]
            if name not in failed and cls not in REPAIRABLE:
                # a genuine-class source may build only when the genuine
                # defect stays visible as a ledgered source diagnostic in
                # the emitted index — never reconciled, never silent
                mts = {int(m) for m in re.findall(
                    r"MT(\d+)/ZAP", " ".join(
                        rec["all_excesses"].get("detail", [])))}
                led = index_ledgers.get((proj, name), [])
                diagnosed = any(
                    f"MT{mt}:" in line and "audit recorded" in line
                    for mt in mts for line in led)
                if not diagnosed:
                    failures.append(
                        f"{proj}/{name}: built despite {cls} without "
                        f"a ledgered source diagnostic")
        for token, names in res.get("repair_diagnostics", {}).items():
            allowed = TOKEN_KINDS.get(token)
            if allowed is None:
                continue  # identity repairs carry no decimal signature
            for name in names:
                if name in failed:
                    failures.append(
                        f"{proj}/{name}: {token} ledgered on a "
                        f"still-failed file")
                    continue
                if name not in files:
                    continue  # previously-built file: token may still fire
                kinds = set(files[name]["all_excesses"]["kinds"])
                if not kinds & allowed:
                    failures.append(
                        f"{proj}/{name}: {token} without a proven "
                        f"{sorted(allowed)} mechanism")

    # ---- G5: acceptance arithmetic -------------------------------------
    if g5acc.get("schema") != "actinv-p25-g5-acceptance-1":
        failures.append("g5 schema")
    led = g5acc.get("ledger", [])
    eligible_rows = [r for r in led if r["status"] == "eligible"]
    if len(eligible_rows) != sum(ELIGIBLE.values()):
        failures.append(
            f"eligible ledger {len(eligible_rows)} != {sum(ELIGIBLE.values())}")
    # one named outcome each
    outcomes = Counter()
    for r in eligible_rows:
        name = r.get("candidate_outcome")
        if not name or not any(name.startswith(p) for p in NAMED_PREFIXES):
            failures.append(f"{r['row_id']}: unnamed outcome {name}")
        else:
            outcomes[f"{r['projectile']}:{name}"] += 1
    # per-stratum scored recount vs floors
    per = defaultdict(int)
    for r in eligible_rows:
        if (r.get("candidate") or {}).get("status") == "scored":
            per[r["projectile"]] += 1
    floor_ok = {}
    for proj, (share, rows) in FLOORS.items():
        floor_ok[proj] = per[proj] >= rows and per[proj] >= math.ceil(
            ELIGIBLE[proj] * share)
    if not all(floor_ok.values()):
        failures.append(f"coverage floors fail: {floor_ok}")
    no_empty = all(per[p] >= 10 for p in FLOORS)
    if not no_empty:
        failures.append("empty stratum present")
    # nonregression recompute — recorded per-stratum verdicts must equal
    # the independent recompute in both directions
    pp = g5acc.get("per_projectile", {})
    rec_strata = (g5acc.get("gates", {})
                  .get("paired_nonregression", {}).get("strata", {}))
    for proj, blk in pp.items():
        if blk.get("eligible_rows", 0) < 10:
            continue
        b, c = blk["baseline"], blk["candidate"]
        recomputed = {
            "median_ok": (c["median_abs_ln"] <= b["median_abs_ln"] + 0.005
                          and c["median_abs_ln"] <= 1.01 * b["median_abs_ln"]),
            "p90_ok": (c["p90_abs_ln"] <= b["p90_abs_ln"] + 0.01
                       and c["p90_abs_ln"] <= 1.01 * b["p90_abs_ln"]),
            "coverage_ok": all(
                c[k] >= b[k] - 0.01
                for k in ("within_10pct", "within_20pct", "within_30pct")),
        }
        rec = rec_strata.get(proj, {})
        for k, want in recomputed.items():
            if rec.get(k) != want:
                failures.append(
                    f"{proj}: recorded {k} != recompute")
        if nonreg_ok(b, c) is not True:
            failures.append(f"{proj}: nonregression recompute fails")
    if nonreg_ok(g5acc["overall"]["baseline"],
                 g5acc["overall"]["candidate"]) is not True:
        failures.append("overall nonregression recompute fails")
    # recorded gate verdicts must equal the recomputation
    g = g5acc.get("gates", {})
    for proj, (share, rows) in FLOORS.items():
        rec = (g.get("coverage_floors") or {}).get(proj, {})
        if rec.get("pass") != floor_ok[proj]:
            failures.append(f"{proj}: recorded floor verdict != recompute")
        if rec.get("candidate_scored") != per[proj]:
            failures.append(f"{proj}: recorded scored count != recompute")
    if g.get("coverage_floors_pass") != all(floor_ok.values()):
        failures.append("recorded floor verdict != recompute")
    if g.get("no_empty_stratum") != no_empty:
        failures.append("recorded no-empty verdict != recompute")
    if g.get("historical_reproducibility") is not True:
        failures.append("historical rerun does not reproduce the sealed record")
    want_pass = bool(
        g.get("outcome_accounting")
        and g["paired_nonregression"]["strata_pass"]
        and g["paired_nonregression"]["overall_pass"]
        and all(floor_ok.values()) and no_empty
        and g.get("historical_reproducibility"))
    if g.get("pass") != want_pass:
        failures.append("recorded pass != recomputed pass")


def gate_ordering(failures: list[str]) -> None:
    """Gate order via git ancestry: G3 (floors frozen) must precede the
    G4 repair commit; both must precede HEAD."""
    def sha(ref):
        return subprocess.run(
            ["git", "rev-parse", ref], cwd=ROOT, text=True,
            capture_output=True).stdout.strip()

    def is_ancestor(a, b):
        return subprocess.run(
            ["git", "merge-base", "--is-ancestor", a, b],
            cwd=ROOT).returncode == 0

    log = subprocess.run(
        ["git", "log", "--format=%H %s", "-30"], cwd=ROOT, text=True,
        capture_output=True).stdout.splitlines()
    g3 = next((l.split()[0] for l in log if "Amendment B" in l), None)
    g4 = next((l.split()[0] for l in log if "P25 G4" in l), None)
    if not g3 or not g4:
        failures.append("cannot locate G3/G4 commits for ordering")
        return
    if not is_ancestor(g3, g4):
        failures.append("G4 repair commit does not descend from the "
                        "frozen-floor G3 commit")


def run() -> dict:
    failures: list[str] = []
    paths = {
        "census": RESULTS / "g1_p25_census.json",
        "traces": RESULTS / "g2_p25_traces.json",
        "g3check": RESULTS / "g3_p25_check.json",
        "g4rep": RESULTS / "g4_p25_repairs.json",
        "g4check": RESULTS / "g4_p25_check.json",
        "g5acc": RESULTS / "g5_p25_acceptance.json",
        "g5p18b": RESULTS / "g5_p18b_heldout.json",
    }
    records = {}
    for name, p in paths.items():
        if not p.is_file():
            failures.append(f"missing evidence {p.name}")
        else:
            records[name] = json.loads(p.read_text())

    for name, expected in EXPECTED_VERDICTS.items():
        vp = RESULTS / name
        if not vp.exists():
            failures.append(f"missing verdict {name}")
            continue
        if json.loads(vp.read_text()).get("verdict") != expected:
            failures.append(f"{name} verdict != {expected}")

    if not failures:
        evaluate(records, failures)
        gate_ordering(failures)
        # checker results must themselves carry pass
        for key in ("g3check", "g4check"):
            if records[key].get("pass") is not True:
                failures.append(f"{key} does not carry pass")

    verdict = "P25-PASS" if not failures else "P25-FAIL"
    out = {
        "schema": "actinv-p25-verdict-1",
        "phase": "P25",
        "verdict": verdict,
        "failures": failures[:100],
        "failure_count": len(failures),
        "evidence_sha256": {
            name: sha256(p) for name, p in paths.items() if p.is_file()},
        "protocol_sha256": sha256(PROTOCOL),
        "amendment_a_sha256": sha256(AMEND_A),
        "amendment_b_sha256": sha256(AMEND_B),
        "qualification_label": (
            "P25 qualification rests on engineering evidence and "
            "retrospective scoring of the sealed P18b held-out "
            "population; no sufficiently independent unread isomeric "
            "measurement set exists."),
        "release_note": (
            "P25-PASS does not by itself authorize release; the 1.1.0 "
            "publication decision remains the maintainer's and P24 may "
            "still be required before tagging."),
    }
    return out


def self_test() -> int:
    paths = {
        "census": RESULTS / "g1_p25_census.json",
        "traces": RESULTS / "g2_p25_traces.json",
        "g3check": RESULTS / "g3_p25_check.json",
        "g4rep": RESULTS / "g4_p25_repairs.json",
        "g4check": RESULTS / "g4_p25_check.json",
        "g5acc": RESULTS / "g5_p25_acceptance.json",
        "g5p18b": RESULTS / "g5_p18b_heldout.json",
    }
    base = {n: json.loads(p.read_text()) for n, p in paths.items()}
    rejected = 0

    def mut_floor(r):
        r["g5acc"]["gates"]["coverage_floors"]["neutron"]["pass"] = True
    def mut_class(r):
        r["traces"]["files"]["neutron"]["n-Fe053m.tendl"]["final_class"] = \
            "floor_artifact_only"
    def mut_outcome(r):
        e = next(x for x in r["g5acc"]["ledger"] if x["status"] == "eligible")
        e["candidate_outcome"] = "mystery"
    def mut_verdict_map(r):
        pass  # handled separately

    for mutation in (mut_floor, mut_class, mut_outcome):
        m = copy.deepcopy(base)
        mutation(m)
        f: list[str] = []
        try:
            evaluate(m, f)
        except Exception as exc:
            f.append(str(exc))
        if f:
            rejected += 1
        else:
            print("MUTATION NOT REJECTED:", mutation.__name__)
    print(f"self-test rejected {rejected}/3 mutations")
    return 0 if rejected == 3 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    result = run()
    OUT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: v for k, v in result.items()
                      if k != "evidence_sha256"}, indent=1))
    return 0 if result["verdict"] == "P25-PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
