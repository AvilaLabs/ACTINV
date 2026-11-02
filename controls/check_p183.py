#!/usr/bin/env python3
"""P183 checker (protocols/ACTINV-P183_PROTOCOL.md): P182's gates on P183's record, with the pruning-bound parts
tested as P183 says (P182's own test, or an excess of at most 1e-6 of the overall total).

  G0  The protocol's SHA-256 is in protocols/protocol_hash.txt.
  G1  P182's G1 (pins, clean tree, bounded scope) on P183's record and repository inputs.
  G2  P182's G2 (all 268 problems under both builds, exit 0, registered order).
  G3  Every problem meets Unchanged and Pruning record, totals as P183 says.
  G4  P182's quiet-machine gate.
  G5  CI green on the evidence commit (checked after the push, not here).

  python3 controls/check_p183.py              check results/p183_record.json, write results/p183_verdict.json
  python3 controls/check_p183.py --no-write   recompute and compare with the committed verdict
  python3 controls/check_p183.py --compare KIND BASE CAND [BMIN]   compare one output pair (dry run)
  python3 controls/check_p183.py --selftest
"""
import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p183_harness as h3  # noqa: E402  (sets P182's harness to P183's paths and schema)
import check_p182 as c  # noqa: E402
import check_p182a as a  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P183_PROTOCOL.md"
RECORD = c.h.RECORD
VERDICT = ROOT / "results" / "p183_verdict.json"
c.REPO_INPUTS = {
    "harness": "controls/p182_harness.py", "checker": "controls/check_p182.py",
    "protocol": "protocols/ACTINV-P182_PROTOCOL.md", "amendment_checker": "controls/check_p182a.py",
    "p183_harness": "controls/p183_harness.py", "p183_checker": "controls/check_p183.py",
    "p183_protocol": "protocols/ACTINV-P183_PROTOCOL.md",
}
DISCLOSURE = ("The protocol was written after two failed checks of the same change on the pruning-bound totals, "
              "with results identical every time.")


def pruning_checks(base_rp, cand_rp, bmin):
    """P182's checks with the four parts taken out of its totals test, then the parts tested as P183 says."""
    strip = (lambda rp: {k: v for k, v in rp.items() if k not in a.PARTS} if isinstance(rp, dict) else rp)
    out = a.original["pruning_checks"](strip(base_rp), strip(cand_rp), bmin)
    out.update(part_violations=0, part_excess_max_over_overall=None)
    if not isinstance(base_rp, dict) or not isinstance(cand_rp, dict):
        return out

    def fail(text):
        out["ok"] = False
        out["part_violations"] += 1
        if len(out["problems"]) < c.MAX_PATHS:
            out["problems"].append(text)

    for field, quantity in a.PARTS.items():
        b, v = base_rp.get(field), cand_rp.get(field)
        if b is None and v is None:
            continue
        if not c.finite(b, v):
            if b != v:
                fail(f"{field}: {b} vs {v}")
            continue
        if b > 0:  # reported, not gated
            for key, pick in (("part_ratio_min", min), ("part_ratio_max", max)):
                out.setdefault(key, {})[field] = v / b
        if not c.at_least(v, b):
            fail(f"{field}: candidate {v} below the baseline's {b}")
            continue
        overall = base_rp.get(a.OVERALL[quantity])
        within_p182 = not (b > 0 and v > b * (1.0 + c.TOTAL_UP))
        within_overall = c.finite(overall) and overall > 0 and v - b <= c.TOTAL_UP * overall
        if c.finite(overall) and overall > 0:
            excess = (v - b) / overall
            if out["part_excess_max_over_overall"] is None or excess > out["part_excess_max_over_overall"]:
                out["part_excess_max_over_overall"] = excess
        if not (within_p182 or within_overall):
            fail(f"{field}: candidate {v} against baseline {b} (overall {overall})")
    return out


def patch():
    c.pruning_checks, c.empty_checks, c.merge_checks = pruning_checks, a.empty_checks, a.merge_checks


def g0():
    logged = c.HASH_LOG.read_text()
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    return [] if f"{c.sha256(PROTOCOL)}  protocols/{PROTOCOL.name}" in logged else \
        [f"{PROTOCOL.name} hash not in protocol_hash.txt"]


def evaluate(record, repo=None):
    gates = {"G0": g0(), "G1": c.g1(record, repo), "G2": c.g2(record)}
    gates["G3"] = a.g3(record) if not gates["G2"] else ["runs incomplete"]
    gates["G4"] = c.quiet(record)
    result, sentences = c.summary(record) if not gates["G2"] and not gates["G3"] else ({}, [])
    if result:
        comps = [record["comparisons"][p["id"]]["pruning"] for p in record["plan"]["problems"]]
        excess = [x["part_excess_max_over_overall"] for x in comps if x.get("part_excess_max_over_overall") is not None]
        result["pruning"]["part_excess_max_over_overall"] = max(excess) if excess else None
    return gates, result, sentences


# ------------------------------------------------------------------------------------------------------ self-test

# a region of P182's 4,096-element ENDF/B-VIII.1 mesh with no overall total (ordinal 615 in shape; values from the
# region ledger 336 names)
NULL_TOTAL_REGION = {
    "activity_Bq_per_g_bound": None, "heat_W_per_g_bound": None,
    "kept_activity_Bq_per_g_bound": None, "kept_heat_W_per_g_bound": None,
    "dropped_activity_Bq_per_g_bound": 2.627724643442729e-13, "dropped_heat_W_per_g_bound": 8.998463199561456e-26,
    "threshold_atoms_per_g": 1e-08, "unbounded_components": ["H1", "H2", "H3", "He3", "He4"], "dropped": [],
}
# P182's F 1996 five-minute ENDF/B-VIII.1 record (ledger 334)
F_1996 = {
    "activity_Bq_per_g_bound": 0.00013094466738770334, "heat_W_per_g_bound": 1.9267709314532932e-18,
    "dropped_activity_Bq_per_g_bound": 0.00013094466738770334, "dropped_heat_W_per_g_bound": 1.9267709314532932e-18,
    "kept_activity_Bq_per_g_bound": 3.222920176868151e-35, "kept_heat_W_per_g_bound": 2.0240332078977886e-47,
    "threshold_atoms_per_g": 1e-08, "unbounded_components": [], "dropped": [],
}


def selftest():
    failures = []
    if not c.selftest():  # P182's own checks, unpatched
        failures.append("P182's selftest")
    patch()

    def check(base, mutate):
        b, v = copy.deepcopy(base), copy.deepcopy(base)
        mutate(v)
        return pruning_checks(b, v, 1e-8)

    accept = {
        "null-total region, dropped parts looser by 2.2e-9": (NULL_TOTAL_REGION, lambda v: v.update(
            dropped_activity_Bq_per_g_bound=2.627724649200515e-13, dropped_heat_W_per_g_bound=8.998463205042285e-26)),
        "F 1996 kept parts as the candidate gave them": (F_1996, lambda v: v.update(
            kept_activity_Bq_per_g_bound=3.223875757242342e-35, kept_heat_W_per_g_bound=2.0248845186865274e-47)),
        "identical records": (F_1996, lambda v: None),
    }
    refuse = {
        "null-total region, a dropped part looser by 1e-5": (NULL_TOTAL_REGION, lambda v: v.update(
            dropped_activity_Bq_per_g_bound=2.627724643442729e-13 * (1 + 1e-5))),
        "a part looser than both tests allow": (F_1996, lambda v: v.update(
            kept_activity_Bq_per_g_bound=3.222920176868151e-35 + 2e-6 * 0.00013094466738770334)),
        "a part below the baseline": (F_1996, lambda v: v.update(dropped_heat_W_per_g_bound=1e-18)),
        "a part null in one record only": (F_1996, lambda v: v.update(kept_heat_W_per_g_bound=None)),
        "an overall total above 1 + 1e-6": (F_1996, lambda v: v.update(
            activity_Bq_per_g_bound=0.00013094466738770334 * (1 + 1e-5))),
        "a changed threshold": (F_1996, lambda v: v.update(threshold_atoms_per_g=1e-9)),
        "a new unbounded component": (NULL_TOTAL_REGION, lambda v: v.update(unbounded_components=["H1"])),
    }
    for name, (base, mutate) in accept.items():
        out = check(base, mutate)
        if not out["ok"]:
            failures.append(f"refused: {name}: {out['problems']}")
    for name, (base, mutate) in refuse.items():
        if check(base, mutate)["ok"]:
            failures.append(f"not caught: {name}")
    # every pair P182's totals test accepts, P183 accepts (parts within 1 + 1e-6 of their own baseline)
    for field in a.PARTS:
        out = check(F_1996, lambda v: v.update({field: F_1996[field] * (1 + 0.9e-6)}))
        if not out["ok"]:
            failures.append(f"refused a part P182 accepts: {field}")
    rec, _ = c.synthetic_record()
    rec["schema"] = h3.SCHEMA
    rec["comparisons"] = {p["id"]: {"unchanged": True, "kept_sets_equal": True, "pruning": a.empty_checks()}
                          for p in rec["plan"]["problems"]}
    if a.g3(rec):
        failures.append(f"g3 refused a clean record: {a.g3(rec)[:2]}")
    rec["comparisons"][rec["plan"]["problems"][0]["id"]]["pruning"]["part_violations"] = 1
    if not a.g3(rec):
        failures.append("g3 missed a part violation")
    for f in failures:
        print(f"selftest: {f}")
    print("selftest: PASS" if not failures else "selftest: FAIL")
    return not failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--compare", nargs="+", metavar="ARG")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    patch()
    if args.compare:
        kind, base, cand = args.compare[:3]
        bmin = float(args.compare[3]) if len(args.compare) > 3 else c.DEFAULT_BMIN
        out = c.compare_files(kind, base, cand, bmin)
        pr = out["pruning"]
        print(json.dumps({"unchanged": out["unchanged"], "kept_sets_equal": out["kept_sets_equal"], "ok": pr["ok"],
                          "part_violations": pr["part_violations"], "problems": pr["problems"][:5]}))
        return 0 if out["unchanged"] and pr["ok"] else 1
    record = json.loads(args.record.read_text())
    gates, result, sentences = evaluate(record)
    passed = all(not problems for problems in gates.values())
    if not passed:
        sentences = []
    verdict = {
        "protocol": "ACTINV-P183",
        "verdict": "P183-PASS (pending G5, CI on the evidence commit)" if passed else "P183-FAIL",
        "gates": {name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
        "summary": result,
        "allowed_sentences": sentences,
        "disclosure": DISCLOSURE,
    }
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems[:20]:
            print(f"  {problem}")
    if args.no_write:
        recorded = json.loads(VERDICT.read_text())
        if recorded.get("summary") != json.loads(json.dumps(result)) or \
                recorded.get("allowed_sentences") != sentences:
            print("verdict file differs from the recomputed summary")
            passed = False
    elif args.record.resolve() == RECORD.resolve():
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("outcome:")
    for sentence in sentences:
        print(f"  {sentence}")
    print(verdict["verdict"] if passed else "P183-FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
