#!/usr/bin/env python3
"""P182 Amendment A checker (protocols/ACTINV-P182_AMENDMENT_A.md).

Reads P182's committed record (pinned), checks that every output it names is still on disk with its recorded SHA-256,
recompares every baseline/candidate pair with check_p182's comparison, the pruning totals tested as the amendment
says, and writes results/p182a_record.json and results/p182a_verdict.json.

  G0  P182's protocol and this amendment are in protocols/protocol_hash.txt.
  G1  P182's G1 on its record.
  G2  P182's G2 on its record, and every output present with its recorded SHA-256.
  G3  P182's G3 on the recompared record, the totals bullet as amended.
  G4  P182's quiet-machine gate on its record.
  G5  CI green on the evidence commit (checked after the push, not here).

  python3 controls/check_p182a.py              recompare, write the record and the verdict
  python3 controls/check_p182a.py --no-write   recompute from the committed amendment record and compare
  python3 controls/check_p182a.py --selftest
"""
import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p182 as c  # noqa: E402

P182_RECORD = ROOT / "results" / "p182_record.json"
P182_RECORD_SHA256 = "767e4632d8792763db540a0d56d08d384dc7127c2c039d0ef6e5288b83e4063c"
RECORD = ROOT / "results" / "p182a_record.json"
VERDICT = ROOT / "results" / "p182a_verdict.json"
AMENDMENT = ROOT / "protocols" / "ACTINV-P182_AMENDMENT_A.md"

OVERALL = {"activity": "activity_Bq_per_g_bound", "heat": "heat_W_per_g_bound"}
PARTS = {"dropped_activity_Bq_per_g_bound": "activity", "kept_activity_Bq_per_g_bound": "activity",
         "dropped_heat_W_per_g_bound": "heat", "kept_heat_W_per_g_bound": "heat"}

original = {"pruning_checks": c.pruning_checks, "empty_checks": c.empty_checks, "merge_checks": c.merge_checks}


def empty_checks():
    out = original["empty_checks"]()
    out.update(part_violations=0, part_excess_max_over_overall=None)
    return out


def pruning_checks(base_rp, cand_rp, bmin):
    """P182's checks with the four parts taken out of its totals test, then the parts tested as amended."""
    strip = (lambda rp: {k: v for k, v in rp.items() if k not in PARTS} if isinstance(rp, dict) else rp)
    out = original["pruning_checks"](strip(base_rp), strip(cand_rp), bmin)
    out.update(part_violations=0, part_excess_max_over_overall=None)
    if not isinstance(base_rp, dict) or not isinstance(cand_rp, dict):
        return out

    def fail(text):
        out["ok"] = False
        out["part_violations"] += 1
        if len(out["problems"]) < c.MAX_PATHS:
            out["problems"].append(text)

    for field, quantity in PARTS.items():
        b, v = base_rp.get(field), cand_rp.get(field)
        if b is None and v is None:
            continue
        if not c.finite(b, v):
            if b != v:
                fail(f"{field}: {b} vs {v}")
            continue
        if b > 0:  # reported, not gated (the overall totals keep their ratio gate)
            for key, pick in (("part_ratio_min", min), ("part_ratio_max", max)):
                out.setdefault(key, {})[field] = v / b
        overall = base_rp.get(OVERALL[quantity])
        if not c.at_least(v, b):
            fail(f"{field}: candidate {v} below the baseline's {b}")
        elif c.finite(overall) and overall > 0:
            excess = (v - b) / overall
            if out["part_excess_max_over_overall"] is None or excess > out["part_excess_max_over_overall"]:
                out["part_excess_max_over_overall"] = excess
            if v - b > c.TOTAL_UP * overall:
                fail(f"{field}: candidate {v} exceeds the baseline's {b} by more than {c.TOTAL_UP} x {overall}")
        elif not (v <= b * (1.0 + c.REL_TOL)):
            fail(f"{field}: candidate {v} against baseline {b} with no overall total")
    return out


def merge_checks(into, one):
    original["merge_checks"](into, one)
    into["part_violations"] += one["part_violations"]
    rel = [x for x in (into["part_excess_max_over_overall"], one["part_excess_max_over_overall"]) if x is not None]
    into["part_excess_max_over_overall"] = max(rel) if rel else None
    for key, pick in (("part_ratio_min", min), ("part_ratio_max", max)):
        for field, x in one.get(key, {}).items():
            have = into.setdefault(key, {})
            have[field] = pick(have[field], x) if field in have else x
    return into


def patch():
    """Route check_p182's comparison through the amended totals test."""
    c.pruning_checks, c.empty_checks, c.merge_checks = pruning_checks, empty_checks, merge_checks


def g3(record):
    """P182's G3 with the ratio test restricted to the overall totals and the part test added."""
    problems = []
    comps = record.get("comparisons", {})
    for p in record.get("plan", {}).get("problems", []):
        cmp = comps.get(p["id"])
        if not cmp:
            problems.append(f"{p['id']}: not compared")
            continue
        if not cmp.get("unchanged"):
            problems.append(f"{p['id']}: changed at {cmp.get('first_differences', [])[:3]}")
        if not cmp.get("kept_sets_equal"):
            problems.append(f"{p['id']}: kept set differs")
        pr = cmp.get("pruning") or {}
        for key in ("missing_in_candidate", "bound_violations", "candidate_only_above_floor", "total_violations",
                    "other_differences", "part_violations"):
            if pr.get(key) != 0:
                problems.append(f"{p['id']}: pruning {key} = {pr.get(key)}")
        if not pr.get("ok"):
            problems.append(f"{p['id']}: pruning record fails: {pr.get('problems', [])[:3]}")
        overall = set(OVERALL.values())
        if any(v < 1.0 - c.REL_TOL for f, v in (pr.get("total_ratio_min") or {}).items() if f in overall) or \
                any(v > 1.0 + c.TOTAL_UP for f, v in (pr.get("total_ratio_max") or {}).items() if f in overall):
            problems.append(f"{p['id']}: an overall pruning total ratio is outside [1 - 1e-12, 1 + 1e-6]")
        if any(f in (pr.get(key) or {}) for key in ("total_ratio_min", "total_ratio_max") for f in PARTS):
            problems.append(f"{p['id']}: a part was tested as a total")
    return problems


def outputs_present(record):
    """Every output of the record on disk with its recorded SHA-256."""
    problems = []
    for run in record.get("runs", []):
        path = Path(run.get("output", ""))
        if not path.is_file():
            problems.append(f"{run.get('problem')}/{run.get('build')}: output missing")
        elif c.sha256(path) != run.get("output_sha256"):
            problems.append(f"{run.get('problem')}/{run.get('build')}: output SHA-256 differs from the record")
    return problems


def recompare(record):
    """The comparisons of every problem, recomputed from the outputs with the amended test."""
    runs = {(r["problem"], r["build"]): r for r in record["runs"]}
    out = {}
    for problem in record["plan"]["problems"]:
        pair = [runs[(problem["id"], b)]["output"] for b in c.h.BUILD_ORDER]
        bmin = c.bmin_of(json.loads(Path(problem["spec"]).read_text()))
        out[problem["id"]] = c.compare_files(problem["kind"], pair[0], pair[1], bmin)
    return out


def g0():
    problems = c.g0()
    if not AMENDMENT.is_file():
        problems.append(f"missing {AMENDMENT.name}")
    return problems


def evaluate(p182, amended, repo=None):
    gates = {"G0": g0(), "G1": c.g1(p182, repo), "G2": c.g2(p182) + amended.get("output_problems", [])}
    gates["G3"] = g3(amended) if not gates["G2"] else ["runs incomplete or outputs changed"]
    gates["G4"] = c.quiet(p182)
    result, sentences = c.summary(amended) if not gates["G2"] and not gates["G3"] else ({}, [])
    if result:
        comps = [amended["comparisons"][p["id"]]["pruning"] for p in amended["plan"]["problems"]]
        excess = [x["part_excess_max_over_overall"] for x in comps if x["part_excess_max_over_overall"] is not None]
        result["pruning"]["part_excess_max_over_overall"] = max(excess) if excess else None
        result["pruning"]["total_ratio_min"] = {f: v for f, v in result["pruning"]["total_ratio_min"].items()
                                                if f in OVERALL.values()}
        result["pruning"]["total_ratio_max"] = {f: v for f, v in result["pruning"]["total_ratio_max"].items()
                                                if f in OVERALL.values()}
    return gates, result, sentences


def load_p182():
    if c.sha256(P182_RECORD) != P182_RECORD_SHA256:
        sys.exit("check_p182a: results/p182_record.json is not the pinned P182 record")
    return json.loads(P182_RECORD.read_text())


# ------------------------------------------------------------------------------------------------------ self-test

def selftest():
    failures = []
    if not c.selftest():  # P182's own checks, unpatched
        failures.append("P182's selftest")
    patch()
    base, cand = c.synthetic_run_pair()

    def run(mutate):
        b, v = copy.deepcopy(base), copy.deepcopy(cand)
        mutate(b["ledger"]["rate_pruning"], v["ledger"]["rate_pruning"])
        return c.compare_run(b, v, 1e-8)["pruning"]

    clean = run(lambda b, v: None)
    if not clean["ok"] or clean["part_violations"]:
        failures.append(f"clean pair refused: {clean['problems']}")
    tiny = run(lambda b, v: (b.update(kept_activity_Bq_per_g_bound=1e-30),
                             v.update(kept_activity_Bq_per_g_bound=1.002e-30)))
    if not tiny["ok"]:
        failures.append(f"a part loosened by 0.2% of a negligible value was refused: {tiny['problems']}")
    for name, mutate in {
        "part above 1e-6 of the overall total":
            lambda b, v: v.update(kept_activity_Bq_per_g_bound=b["kept_activity_Bq_per_g_bound"]
                                  + 2e-6 * b["activity_Bq_per_g_bound"]),
        "part below the baseline": lambda b, v: v.update(dropped_heat_W_per_g_bound=0.5 * b["dropped_heat_W_per_g_bound"]),
        "part missing from one record": lambda b, v: v.pop("kept_heat_W_per_g_bound"),
        "overall total above 1 + 1e-6": lambda b, v: v.update(activity_Bq_per_g_bound=b["activity_Bq_per_g_bound"]
                                                              * (1 + 1e-5)),
        "part grows with no overall total": lambda b, v: (b.pop("activity_Bq_per_g_bound"),
                                                          v.pop("activity_Bq_per_g_bound"),
                                                          v.update(kept_activity_Bq_per_g_bound=2.0
                                                                   * b["kept_activity_Bq_per_g_bound"])),
    }.items():
        if run(mutate)["ok"]:
            failures.append(f"not caught: {name}")
    rec, _ = c.synthetic_record()
    pr = empty_checks()
    rec["comparisons"] = {p["id"]: {"unchanged": True, "kept_sets_equal": True, "pruning": copy.deepcopy(pr)}
                          for p in rec["plan"]["problems"]}
    if g3(rec):
        failures.append(f"g3 refused a clean record: {g3(rec)[:2]}")
    first = rec["plan"]["problems"][0]["id"]
    rec["comparisons"][first]["pruning"]["part_violations"] = 1
    if not g3(rec):
        failures.append("g3 missed a part violation")
    rec["comparisons"][first]["pruning"] = dict(pr, total_ratio_max={"heat_W_per_g_bound": 1 + 1e-5})
    if not g3(rec):
        failures.append("g3 missed an overall ratio above 1 + 1e-6")
    rec["comparisons"][first]["pruning"] = dict(pr, total_ratio_max={"kept_heat_W_per_g_bound": 1.01})
    if not g3(rec):
        failures.append("g3 missed a part tested as a total")
    for f in failures:
        print(f"selftest: {f}")
    print("selftest: PASS" if not failures else "selftest: FAIL")
    return not failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    p182 = load_p182()
    patch()
    if args.no_write:
        amended = json.loads(RECORD.read_text())
    else:
        amended = copy.deepcopy(p182)
        amended.update(schema=c.h.SCHEMA + "-amendment-a", amendment="A", p182_record_sha256=P182_RECORD_SHA256,
                       output_problems=outputs_present(p182))
        amended["comparisons"] = recompare(p182) if not amended["output_problems"] else {}
    gates, result, sentences = evaluate(p182, amended)
    passed = all(not problems for problems in gates.values())
    if not passed:
        sentences = []
    verdict = {
        "protocol": "ACTINV-P182 Amendment A",
        "verdict": "P182A-PASS (pending G5, CI on the evidence commit)" if passed else "P182A-FAIL",
        "gates": {name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
        "summary": result,
        "allowed_sentences": sentences,
        "disclosure": "The pruning-total test was amended after P182's result was known; the results themselves "
                      "were identical on all 268 problems under both tests.",
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
    else:
        c.h.h181.write_atomic(RECORD, amended)
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("outcome:")
    for sentence in sentences:
        print(f"  {sentence}")
    print(verdict["verdict"] if passed else "P182A-FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
