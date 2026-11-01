#!/usr/bin/env python3
"""P180 checker (protocols/ACTINV-P180_PROTOCOL.md): wall time of ACTINV 1.7.1 and OpenMC 0.15.3 depletion on P172's 132
JAEA FNS problems. Reads results/p180_speed.json written by `controls/p180_speed.py assemble`. G0-G3 are evaluated here;
G4 (CI on the pushed evidence commit) is external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P180_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Pinned inputs (the released actinv 1.7.1 binary, both libraries and indices, chain, cross-section index, decay
      files, FNS archive, abundance table); actinv 1.7.1 and OpenMC 0.15.3; P172's OpenMC driver unchanged; the bounded
      cgroup; a clean tracked tree.
  G2  Fidelity: every problem ran in all three arms with exit 0; every ACTINV result has a finite decay heat at every
      cooling time and every OpenMC case executed with finite heat and its three phase times; the warm-up ran.
  G3  Quiet machine: no other transient scope of this user at the start, at the end or before any problem, and a median
      1-minute load average before each process of at most 3.0. Then the summary and the allowed sentences are
      recomputed and written to results/p180_verdict.json (with --no-write, compared with it).

Verdict: P180-PASS if G0-G3 hold (G4 external). The times are reported, not gated.

Usage: check_p180.py [--record FILE] [--no-write]
       check_p180.py --selftest
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
import p180_speed as p180  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P180_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = p180.RECORD
VERDICT = ROOT / "results" / "p180_verdict.json"

ACTINV_VERSION = "actinv 1.7.1"
MAX_MEDIAN_LOAD = 3.0
PHASES = ("session_init_s", "micros_s", "deplete_s")


def g0():
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    logged = HASH_LOG.read_text()
    return [f"{path.name} hash not in protocol_hash.txt"
            for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P180_AMENDMENT_*.md"))]
            if f"{c172.sha256(path)}  protocols/{path.name}" not in logged]


def g1(record):
    problems = []
    if record.get("schema") != p180.SCHEMA:
        problems.append("wrong schema")
    for name, (_, digest) in p180.PINNED.items():
        if record["inputs"].get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not the pin")
    if record.get("actinv_version") != ACTINV_VERSION:
        problems.append(f"ACTINV is not {ACTINV_VERSION}")
    if record.get("openmc_version") != c172.p172.OPENMC_VERSION:
        problems.append(f"OpenMC is not {c172.p172.OPENMC_VERSION}")
    environment = record["environment"]
    if not environment.get("driver_sha256") or environment.get("driver_sha256") != record.get("p172_driver_sha256"):
        problems.append("the OpenMC driver differs from P172's")
    if not environment.get("clean_tree"):
        problems.append("tracked tree was not clean")
    if not c172.p172.cgroup_ok(environment.get("cgroup", {}), 6):
        problems.append("cgroup is not the bounded scope")
    return problems


def g2(record):
    problems = []
    cases = record["cases"]
    if len(cases) != p180.PROBLEMS or len({c["case"] for c in cases}) != len(cases):
        problems.append(f"{len(cases)} distinct problems, expected {p180.PROBLEMS}")
    if any(record.get("warmup", {}).get(arm, {}).get("returncode") != 0 for arm in p180.ARMS):
        problems.append("the warm-up did not run cleanly")
    for c in cases:
        for arm in p180.ARMS:
            t = c.get(arm, {})
            if t.get("returncode") != 0:
                problems.append(f"{c['case']}: {arm} exited {t.get('returncode')}")
            if not (c172.good(t.get("wall_s")) and t["wall_s"] > 0 and c172.good(t.get("cpu_s"))):
                problems.append(f"{c['case']}: {arm} has no time")
        for arm in ("actinv_endf8", "actinv_tendl"):
            if not c.get(arm, {}).get("steps_ok"):
                problems.append(f"{c['case']}: {arm} result lacks a finite heat at every cooling time")
        o = c.get("openmc", {})
        if o.get("status") != "executed" or not o.get("heat_ok"):
            problems.append(f"{c['case']}: OpenMC case not executed with finite heat")
        if not all(c172.good(o.get(k)) and o[k] >= 0 for k in PHASES):
            problems.append(f"{c['case']}: OpenMC phase times missing")
    return problems[:200]


def loads(record):
    return [c[arm]["loadavg_1min_before"] for c in record["cases"] for arm in p180.ARMS]


def quiet(record):
    problems = []
    if record.get("other_scopes_start") or record.get("other_scopes_end"):
        problems.append("another transient scope ran at the start or end")
    busy = [c["case"] for c in record["cases"] if c.get("other_scopes_before")]
    if busy:
        problems.append(f"another transient scope ran before {len(busy)} problems (first {busy[0]})")
    values = loads(record)
    if not values or statistics.median(values) > MAX_MEDIAN_LOAD:
        problems.append(f"median 1-minute load average above {MAX_MEDIAN_LOAD}")
    return problems


def spread(values):
    v = sorted(values)
    n = len(v)
    return {"n": n, "median": statistics.median(v), "mean": math.fsum(v) / n, "min": v[0], "max": v[-1],
            "p10": v[max(0, math.ceil(0.1 * n) - 1)], "p90": v[min(n - 1, math.ceil(0.9 * n) - 1)],
            "total": math.fsum(v)}


def ratio_spread(values):
    out = spread(values)
    out["geometric_mean"] = math.exp(math.fsum(math.log(x) for x in values) / len(values))
    del out["total"]
    return out


def summary(record):
    cases = record["cases"]
    wall = {arm: spread([c[arm]["wall_s"] for c in cases]) for arm in p180.ARMS}
    cpu = {arm: spread([c[arm]["cpu_s"] for c in cases]) for arm in p180.ARMS}
    phases = {k: spread([c["openmc"][k] for c in cases]) for k in PHASES}
    phases["other_s"] = spread([c["openmc"]["wall_s"] - sum(c["openmc"][k] for k in PHASES) for c in cases])
    ratios = {
        "openmc_over_actinv_endf8_wall": ratio_spread([c["openmc"]["wall_s"] / c["actinv_endf8"]["wall_s"]
                                                       for c in cases]),
        "openmc_deplete_over_actinv_endf8_wall": ratio_spread([c["openmc"]["deplete_s"] / c["actinv_endf8"]["wall_s"]
                                                               for c in cases]),
        "openmc_over_actinv_tendl_wall": ratio_spread([c["openmc"]["wall_s"] / c["actinv_tendl"]["wall_s"]
                                                       for c in cases]),
        "openmc_over_actinv_endf8_cpu": ratio_spread([c["openmc"]["cpu_s"] / c["actinv_endf8"]["cpu_s"]
                                                      for c in cases]),
    }
    diagnostics = {}
    for key in ("openmc_vs_p172_max_abs_log", "actinv_endf8_vs_p179_max_abs_log"):
        values = [c[key] for c in cases if c172.good(c.get(key))]
        diagnostics[key] = {"cases": len(values), "median": statistics.median(values) if values else None,
                            "max": max(values) if values else None}
    result = {"problems": len(cases), "wall_s": wall, "cpu_s": cpu, "openmc_phases_s": phases, "ratios": ratios,
              "agreement_diagnostics": diagnostics, "loadavg_1min": spread(loads(record)),
              "run_wall_s": record["wall_s"], "cpu_model": record["environment"].get("cpu_model")}
    a, t, o = wall["actinv_endf8"]["median"], wall["actinv_tendl"]["median"], wall["openmc"]["median"]
    r, r2 = ratios["openmc_over_actinv_endf8_wall"]["median"], ratios["openmc_deplete_over_actinv_endf8_wall"]["median"]
    sentences = [
        f"On the {len(cases)} JAEA FNS decay-heat problems of P172, each solved in its own process on the same two CPU "
        f"cores with the same ENDF/B-VIII.1 cross sections, ACTINV 1.7.1 took a median of {a:.2f} s per problem from "
        f"start to finish and OpenMC {record['openmc_version']} depletion {o:.1f} s; the median per-problem ratio was "
        f"{r:.3g}.",
        f"Counting only OpenMC's depletion step, without its start-up and cross-section collapse, the median "
        f"per-problem ratio was {r2:.3g}.",
        f"With its shipped TENDL-2025 data, ACTINV took a median of {t:.2f} s per problem.",
    ]
    return result, sentences


def evaluate(record):
    gates = {"G0": g0(), "G1": g1(record), "G2": g2(record)}
    gates["G3"] = quiet(record) if not gates["G2"] else ["fidelity failed"]
    result, sentences = summary(record) if not gates["G2"] else ({}, [])
    return gates, result, sentences


# ------------------------------------------------------------------------------------------------------ self-test

def synthetic_record():
    cg = {"memory.max": str(6 * c172.p172.GIB), "memory.swap.max": "0", "pids.max": "128",
          "cpu.max": "200000 100000"}
    ok = {"returncode": 0, "wall_s": 1.0, "cpu_s": 1.5, "loadavg_1min_before": 2.0, "max_rss_kib": 1}
    record = {"schema": p180.SCHEMA, "inputs": {k: {"sha256": v} for k, (_, v) in p180.PINNED.items()},
              "actinv_version": ACTINV_VERSION, "openmc_version": c172.p172.OPENMC_VERSION,
              "environment": {"driver_sha256": "d" * 64, "clean_tree": True, "cgroup": cg, "cpu_model": "x"},
              "p172_driver_sha256": "d" * 64, "wall_s": 100.0, "other_scopes_start": [], "other_scopes_end": [],
              "warmup": {arm: dict(ok) for arm in p180.ARMS}, "cases": []}
    for i in range(p180.PROBLEMS):
        record["cases"].append({
            "case": f"M{i}_e", "material": f"M{i}", "experiment": "e", "other_scopes_before": [],
            "actinv_endf8": dict(ok, steps_ok=True), "actinv_tendl": dict(ok, wall_s=0.5, steps_ok=True),
            "openmc": dict(ok, wall_s=40.0, cpu_s=60.0, status="executed", heat_ok=True, session_init_s=5.0,
                           micros_s=10.0, deplete_s=20.0),
            "openmc_vs_p172_max_abs_log": 1e-6, "actinv_endf8_vs_p179_max_abs_log": 1e-3})
    return record


def selftest():
    failures = []
    gates, result, sentences = evaluate(synthetic_record())
    if any(gates[g] for g in ("G1", "G2", "G3")):
        failures.append(f"clean synthetic record fails: {gates}")
    else:
        if result["ratios"]["openmc_over_actinv_endf8_wall"]["median"] != 40.0:
            failures.append("wall ratio wrong")
        if result["openmc_phases_s"]["other_s"]["median"] != 5.0:
            failures.append("OpenMC remainder wrong")
        if "median per-problem ratio was 40." not in sentences[0] or "ratio was 20." not in sentences[1]:
            failures.append(f"sentences wrong: {sentences}")
    cases = {
        "nonzero exit": lambda r: r["cases"][0]["openmc"].update(returncode=1),
        "missing heat": lambda r: r["cases"][1]["actinv_endf8"].update(steps_ok=False),
        "OpenMC not executed": lambda r: r["cases"][2]["openmc"].update(status="arm_failure"),
        "phase missing": lambda r: r["cases"][0]["openmc"].pop("deplete_s"),
        "problem missing": lambda r: r["cases"].pop(),
        "warm-up failed": lambda r: r["warmup"]["openmc"].update(returncode=1),
        "other binary": lambda r: r["inputs"]["actinv_bin"].update(sha256="0" * 64),
        "other version": lambda r: r.update(actinv_version="actinv 1.7.0"),
        "driver changed": lambda r: r["environment"].update(driver_sha256="e" * 64),
        "dirty tree": lambda r: r["environment"].update(clean_tree=False),
        "unbounded": lambda r: r["environment"]["cgroup"].update({"cpu.max": "max 100000"}),
        "other scope": lambda r: r["cases"][0].update(other_scopes_before=["run-x.scope"]),
        "other scope at end": lambda r: r.update(other_scopes_end=["run-y.scope"]),
        "busy machine": lambda r: [c[arm].update(loadavg_1min_before=5.0) for c in r["cases"] for arm in p180.ARMS],
    }
    for name, mutate in cases.items():
        record = synthetic_record()
        mutate(record)
        gates, _, _ = evaluate(record)
        if not (gates["G1"] or gates["G2"] or gates["G3"]):
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
    if not passed:
        sentences = []
    verdict = {
        "protocol": "ACTINV-P180",
        "verdict": "P180-PASS (pending G4, CI on the evidence commit)" if passed else "P180-FAIL",
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
    elif not p180.REHEARSAL:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("outcome:")
    for sentence in sentences:
        print("  " + sentence)
    print(verdict["verdict"] if passed else "P180-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
