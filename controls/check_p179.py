#!/usr/bin/env python3
"""P179 checker (protocols/ACTINV-P179_PROTOCOL.md): P172's identical-data ACTINV arm on the repaired ENDF/B-VIII.1
library. Reads results/p179_endf8_repaired.json written by `controls/p179_endf8_repaired.py assemble`. G0-G3 are
evaluated here; G4 (CI on the pushed evidence commit) is external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P179_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Pinned inputs (both libraries and indices, P172's sealed record and arm, decay files, FNS archive); P172's module;
      P172's harness, cb2_fns.py and group structure unchanged; the bounded cgroup; a clean tracked tree.
  G2  Fidelity: all 132 experiments with P172's points and times; the arm's own checks pass; at most 5% of points
      dropped; the libraries differ only in MT 102 rows of ledger 316's 15 targets; on every experiment whose
      composition holds none of their elements, the rerun reproduces P172's arm within 1e-4 in ln.
  G3  The summary and allowed sentences are recomputed and written to results/p179_verdict.json (with --no-write,
      compared with it).

Verdict: P179-PASS if G0-G3 hold (G4 external). Accuracy is reported, not gated.

Usage: check_p179.py [--record FILE] [--no-write]
       check_p179.py --selftest
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p172 as c172  # noqa: E402
import p179_endf8_repaired as p179  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P179_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = p179.RECORD
VERDICT = ROOT / "results" / "p179_verdict.json"

EXPERIMENTS = 132
CONTROL_TOLERANCE = 1e-4  # |ln(rerun / P172)| on experiments without a repaired element
CHANGED = 0.01  # a point "changes" when |ln(rerun / P172)| exceeds this
PRIMARY = c172.PRIMARY  # actinv_tendl2025, actinv_endf8 (here the rerun), openmc


def g0():
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    logged = HASH_LOG.read_text()
    return [f"{path.name} hash not in protocol_hash.txt"
            for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P179_AMENDMENT_*.md"))]
            if f"{c172.sha256(path)}  protocols/{path.name}" not in logged]


def g1(record):
    problems = []
    if record.get("schema") != p179.SCHEMA:
        problems.append("wrong schema")
    for name, (_, digest) in p179.PINNED.items():
        if record["inputs"].get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not the pin")
    environment, before = record["environment"], record.get("p172_environment", {})
    if environment.get("module_sha256") != p179.MODULE_SHA256 or before.get("module_sha256") != p179.MODULE_SHA256:
        problems.append("the arm did not run with P172's module")
    for key in ("harness_sha256", "cb2_script_sha256", "groups_sha256"):
        if not environment.get(key) or environment.get(key) != before.get(key):
            problems.append(f"{key} differs from P172's")
    if not environment.get("clean_tree"):
        problems.append("tracked tree was not clean")
    if not c172.p172.cgroup_ok(environment.get("cgroup", {}), 6):
        problems.append("cgroup is not the bounded scope")
    return problems


def controls_and_direct(experiments):
    repaired = set(p179.REPAIRED_ELEMENTS)
    direct = [e for e in experiments if repaired & set(e.get("elements") or ())]
    control = [e for e in experiments if e.get("elements") and not repaired & set(e["elements"])]
    return control, direct


def g2(record):
    problems = []
    experiments = record["experiments"]
    if len(experiments) != EXPERIMENTS:
        problems.append(f"{len(experiments)} experiments, expected {EXPERIMENTS}")
    if len({(e["material"], e["experiment"]) for e in experiments}) != len(experiments):
        problems.append("duplicate experiments")
    arm = record.get("actinv_record", {})
    failed = [name for name in c172.CB2_REQUIRED_CHECKS if arm.get("checks", {}).get(name) is not True]
    if failed:
        problems.append(f"the arm's own checks failed: {', '.join(failed)}")
    diff = record.get("library_diff", {})
    if not diff.get("same_layout"):
        problems.append("the libraries do not share rows and group bounds")
    rows = diff.get("differing_rows", [])
    if not rows:
        problems.append("the libraries do not differ")
    if any(r["mt"] != 102 for r in rows):
        problems.append("a differing row is not MT 102")
    targets = {r["product_za"] - 1 for r in rows if r["product_za"] > 0}
    if targets != set(p179.REPAIRED_TARGETS):
        problems.append(f"differing capture targets {sorted(targets)} are not ledger 316's")
    for e in experiments:
        name = f"{e['material']}/{e['experiment']}"
        if not e.get("elements"):
            problems.append(f"{name}: no composition")
        if e["n_points"] != len(e["points"]) or not e["points"]:
            problems.append(f"{name}: no points or wrong point count")
            continue
        if e.get("actinv_endf8_points") != e["n_points"]:
            problems.append(f"{name}: the rerun has a different point count")
        for p in e["points"]:
            if p.get("actinv_endf8_time_s") != p["time_s"] or p.get("actinv_endf8_measured") != p["measured"]:
                problems.append(f"{name} t={p['time_s']}: the rerun disagrees on the measured point")
            for key in ("actinv_tendl2025", "actinv_endf8", "openmc", "actinv_endf8_p172"):
                if not c172.good(p.get(key)):
                    problems.append(f"{name} t={p['time_s']}: {key} has no finite value")
    control, _ = controls_and_direct(experiments)
    for e in control:
        for p in e["points"]:
            new, old = p.get("actinv_endf8"), p.get("actinv_endf8_p172")
            if c172.good(new) and c172.good(old) and new > 0 and old > 0:
                if abs(math.log(new / old)) > CONTROL_TOLERANCE:
                    problems.append(f"control {e['material']}/{e['experiment']} t={p['time_s']}: rerun differs "
                                    f"from P172's arm")
            elif new != old:
                problems.append(f"control {e['material']}/{e['experiment']} t={p['time_s']}: sign or zero changed")
    points, _ = c172.common_points(experiments)
    total = sum(e["n_points"] for e in experiments)
    if total and (total - len(points)) > c172.MAX_DROPPED_FRACTION * total:
        problems.append(f"{total - len(points)} of {total} points have a non-positive calculation in some arm")
    return problems[:200]


def summary(record):
    experiments = record["experiments"]
    points, dropped = c172.common_points(experiments)
    points = [p for p in points if c172.good(p.get("actinv_endf8_p172")) and p["actinv_endf8_p172"] > 0]
    arms = {a: c172.stats(c172.arm_logs(points, a)) for a in PRIMARY}
    arms["actinv_endf8_p172"] = c172.stats([math.log(p["actinv_endf8_p172"] / p["measured"]) for p in points])
    control, direct = controls_and_direct(experiments)
    direct_keys = {(e["material"], e["experiment"]) for e in direct}
    shifts = [math.log(p["actinv_endf8"] / p["actinv_endf8_p172"]) for p in points]
    per_direct = []
    for e in direct:
        ps = [p for p in points if (p["material"], p["experiment"]) == (e["material"], e["experiment"])]
        if ps:
            row = {"material": e["material"], "experiment": e["experiment"], "points": len(ps)}
            for key in ("actinv_endf8", "actinv_endf8_p172", "openmc"):
                row[key + "_median_abs_log_C_over_E"] = statistics.median(
                    abs(math.log(p[key] / p["measured"])) for p in ps)
            row["median_log_rerun_over_p172"] = statistics.median(
                math.log(p["actinv_endf8"] / p["actinv_endf8_p172"]) for p in ps)
            per_direct.append(row)
    direct_points = [p for p in points if (p["material"], p["experiment"]) in direct_keys]
    result = {
        "common_points": len(points), "experiments": len({(p["material"], p["experiment"]) for p in points}),
        "dropped_points": [{k: d[k] for k in ("material", "experiment", "time_s")} for d in dropped],
        "arms": arms,
        "bootstrap_openmc_minus_actinv_endf8": c172.bootstrap_difference(points, "actinv_endf8", "openmc"),
        "control_experiments": len(control), "direct_experiments": len(direct),
        "points_changed_over_1_percent": sum(abs(s) > CHANGED for s in shifts),
        "direct_points": len(direct_points),
        "direct_arms": ({key: c172.stats([math.log(p[key] / p["measured"]) for p in direct_points])
                         for key in ("actinv_endf8", "actinv_endf8_p172", "openmc")} if direct_points else {}),
        "per_direct_experiment": per_direct,
        "library_differing_rows": len(record["library_diff"]["differing_rows"]),
    }
    e = arms["actinv_endf8"]
    sentences = [
        f"With its R-matrix-limited capture repaired and otherwise the same ENDF/B-VIII.1 cross sections as OpenMC, "
        f"ACTINV has a median |ln C/E| of {e['median_abs_log_C_over_E']:.3f} on {e['points']} measured decay-heat "
        f"points from {result['experiments']} JAEA FNS experiments.",
        f"The repair changes {result['points_changed_over_1_percent']} of those points by more than 1%, all in the "
        f"{len(direct)} experiments whose samples contain Ca, Cl, Cu, Fe, Rh, Si, V or W.",
    ]
    return result, sentences


def evaluate(record):
    gates = {"G0": g0(), "G1": g1(record), "G2": g2(record)}
    result, sentences = summary(record) if not gates["G2"] else ({}, [])
    gates["G3"] = [] if sentences else ["summary not computed (fidelity failed)"]
    return gates, result, sentences


# ------------------------------------------------------------------------------------------------------ self-test

def synthetic_record():
    cg = {"memory.max": str(6 * c172.p172.GIB), "memory.swap.max": "0", "pids.max": "128",
          "cpu.max": "200000 100000"}
    same = {"harness_sha256": "h" * 64, "cb2_script_sha256": "c" * 64, "groups_sha256": "g" * 64}
    rows = [{"row": i, "mt": 102, "product_za": za + 1, "state": 0} for i, za in enumerate(p179.REPAIRED_TARGETS)]
    record = {"schema": p179.SCHEMA, "inputs": {k: {"sha256": v} for k, (_, v) in p179.PINNED.items()},
              "environment": {**same, "module_sha256": p179.MODULE_SHA256, "clean_tree": True, "cgroup": cg},
              "p172_environment": {**same, "module_sha256": p179.MODULE_SHA256},
              "actinv_record": {"pass": True, "checks": {c: True for c in c172.CB2_REQUIRED_CHECKS}},
              "library_diff": {"same_layout": True, "differing_rows": rows, "sig_rows": 100},
              "experiments": []}
    for i in range(EXPERIMENTS):
        direct = i < 30
        points = []
        for t in (50.0, 500.0, 5000.0):
            old = 0.8
            points.append({"time_s": t, "measured": 1.0, "sigma": 0.1, "actinv_tendl2025": 1.1, "openmc": 0.6,
                           "openmc_boundary_match": True, "actinv_endf8_p172": old,
                           "actinv_endf8": old * (1.25 if direct else 1.0), "actinv_endf8_time_s": t,
                           "actinv_endf8_measured": 1.0})
        record["experiments"].append({"material": f"M{i}", "experiment": "e", "n_points": 3, "points": points,
                                      "actinv_endf8_points": 3, "openmc_status": "executed",
                                      "elements": ["FE"] if direct else ["NI"]})
    return record


def selftest():
    failures = []
    gates, result, sentences = evaluate(synthetic_record())
    if gates["G1"] or gates["G2"] or gates["G3"]:
        failures.append(f"clean synthetic record fails: {gates}")
    else:
        if result["points_changed_over_1_percent"] != 90 or result["control_experiments"] != 102:
            failures.append(f"change counts wrong: {result['points_changed_over_1_percent']}")
        if abs(result["arms"]["actinv_endf8_p172"]["median_abs_log_C_over_E"] - abs(math.log(0.8))) > 1e-12:
            failures.append("P172 arm statistics wrong")
        if "changes 90 of those points" not in sentences[1]:
            failures.append(f"sentence wrong: {sentences[1]}")
    cases = {
        "control moved": lambda r: r["experiments"][100]["points"][0].update(actinv_endf8=0.81),
        "non-capture row differs": lambda r: r["library_diff"]["differing_rows"].append(
            {"row": 99, "mt": 16, "product_za": 26053, "state": 0}),
        "wrong target set": lambda r: r["library_diff"]["differing_rows"].pop(),
        "other module": lambda r: r["environment"].update(module_sha256="m" * 64),
        "harness changed": lambda r: r["environment"].update(cb2_script_sha256="x" * 64),
        "dirty tree": lambda r: r["environment"].update(clean_tree=False),
        "unbounded": lambda r: r["environment"]["cgroup"].update({"memory.max": "max"}),
        "input not pinned": lambda r: r["inputs"]["repaired_library"].update(sha256="0" * 64),
        "experiment missing": lambda r: r["experiments"].pop(),
        "point time differs": lambda r: r["experiments"][0]["points"][0].update(actinv_endf8_time_s=51.0),
        "no composition": lambda r: r["experiments"][5].update(elements=[]),
        "arm check failed": lambda r: r["actinv_record"]["checks"].update(input_identities_match=False),
    }
    for name, mutate in cases.items():
        record = synthetic_record()
        mutate(record)
        gates, _, _ = evaluate(record)
        if not (gates["G1"] or gates["G2"]):
            failures.append(f"not caught: {name}")
    for failure in failures:
        print("selftest:", failure)
    print("selftest:", "PASS" if not failures else "FAIL")
    return not failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    record = json.loads(args.record.read_text())
    gates, result, sentences = evaluate(record)
    passed = all(not problems for problems in gates.values())
    verdict = {
        "protocol": "ACTINV-P179",
        "verdict": "P179-PASS (pending G4, CI on the evidence commit)" if passed else "P179-FAIL",
        "gates": {name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
        "summary": result,
        "allowed_sentences": sentences,
    }
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems[:20]:
            print(f"  {problem}")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if recorded.get("summary") != json.loads(json.dumps(result)) or recorded.get(
                    "allowed_sentences") != sentences:
                print("G3: FAIL (verdict file differs from the recomputed summary)")
                passed = False
    elif not p179.REHEARSAL:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("outcome:")
    for sentence in sentences:
        print("  " + sentence)
    print(verdict["verdict"] if passed else "P179-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
