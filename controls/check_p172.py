#!/usr/bin/env python3
"""P172 checker (protocols/ACTINV-P172_PROTOCOL.md): decay heat on the JAEA FNS corpus, ACTINV against OpenMC depletion.
Reads results/p172_openmc_fns.json written by `controls/p172_openmc_fns.py assemble`. G0-G3 are evaluated here; G4 (CI
on the pushed evidence commit) is external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P172_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Pinned inputs (chain, cross-section index and archive, both ACTINV libraries and indices, decay files, FNS archive,
      abundance table), the pinned ACTINV Python module (the same one for both ACTINV arms), OpenMC 0.15.3, the bounded
      cgroup, a clean tracked tree and the crates tree the pinned module was built from.
  G2  Fidelity: all 132 experiments in every arm; the ACTINV arms' own checks pass; both ACTINV arms and the OpenMC arm
      carry the same measured points and times; every OpenMC case executed with every measurement time on a cooling
      boundary; at most 5% of points dropped for a non-positive calculation; when the P32-chain sensitivity ran, its
      OpenMC 0.15.3 values reproduce the sealed OpenMC 0.16.0 values of the exploratory head-to-head (made with that
      chain) within 0.01 in ln on the points they share.
  G3  The summary and allowed sentences are recomputed and written to results/p172_verdict.json (with --no-write,
      compared with it).

Verdict: P172-PASS if G0-G3 hold (G4 external). Accuracy is reported, not gated.

Usage: check_p172.py [--record FILE] [--no-write]
       check_p172.py --selftest
"""
import argparse
import copy
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p172_openmc_fns as p172  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P172_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p172_openmc_fns.json"
VERDICT = ROOT / "results" / "p172_verdict.json"

PRIMARY = p172.ARMS_PRIMARY
ISOMER = p172.ARM_OPENMC_ISOMER
P32 = p172.ARM_OPENMC_P32
SENSITIVITY = (P32, ISOMER)
KEYS = {"actinv_tendl2025": "actinv_tendl2025", "actinv_endf8": "actinv_endf8", "openmc": "openmc",
        "openmc_isomer": "openmc_isomer", "openmc_p32": "openmc_p32"}
CHAIN_TEXT = f"its published ENDF/B-VIII.1 fast-spectrum depletion chain ({p172.CHAIN_NAME})"
WITHIN_LOG = math.log(1.3)
MAX_DROPPED_FRACTION = 0.05
REPRODUCTION_TOLERANCE = 0.01
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 172
# The cb2_fns checks G2 requires (the protocol's identities, certificate inputs and alignment, plus all 132
# experiments). Its positive-pairs check is not among them: a non-positive calculation is a dropped point under the 5%
# rule, as P171's protocol text said and its checker did not.
CB2_REQUIRED_CHECKS = ("all_132_experiments", "all_certificate_inputs_match", "input_identities_match",
                       "reference_times_align_within_2_percent")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def g0():
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    logged = HASH_LOG.read_text()
    return [f"{path.name} hash not in protocol_hash.txt"
            for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P172_AMENDMENT_*.md"))]
            if f"{sha256(path)}  protocols/{path.name}" not in logged]


def g1(record, *, source_tree=None):
    problems = []
    if record.get("schema") != p172.SCHEMA:
        problems.append("wrong schema")
    for name, (_, digest) in p172.PINNED.items():
        if digest is not None and record["inputs"].get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not the pin")
    environment = record["environment"]
    pin = p172.ACTINV_MODULE_SHA256
    if pin is None:
        problems.append("the ACTINV module digest is not pinned yet")
    elif environment.get("module_sha256") != pin or environment.get("endf8_module_sha256") != pin:
        problems.append("an ACTINV arm did not run with the pinned module")
    if environment.get("openmc_version") != p172.OPENMC_VERSION:
        problems.append(f"OpenMC is not {p172.OPENMC_VERSION}")
    if not environment.get("clean_tree"):
        problems.append("tracked tree was not clean")
    expected = source_tree if source_tree is not None else p172.MODULE_CRATES_TREE
    if environment.get("crates_tree") != expected:
        problems.append("crates tree is not the one the pinned module was built from")
    if not p172.cgroup_ok(environment.get("cgroup", {}), 6):
        problems.append("ACTINV cgroup is not the bounded scope")
    if not p172.cgroup_ok(environment.get("openmc_environment", {}).get("cgroup", {}), p172.MEMORY_GIB):
        problems.append("OpenMC cgroup is not the bounded scope")
    return problems


def good(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def g2(record):
    problems = []
    experiments = record["experiments"]
    if len(experiments) != p172.EXPERIMENTS:
        problems.append(f"{len(experiments)} experiments, expected {p172.EXPERIMENTS}")
    if len({(e["material"], e["experiment"]) for e in experiments}) != len(experiments):
        problems.append("duplicate experiments")
    for arm, info in record.get("actinv_records", {}).items():
        checks = info.get("checks", {})
        failed = [name for name in CB2_REQUIRED_CHECKS if checks.get(name) is not True]
        if failed:
            problems.append(f"ACTINV arm {arm}: own checks failed: {', '.join(failed)}")
    if set(record.get("actinv_records", {})) != {"tendl2025", "endf8"}:
        problems.append("ACTINV arms are not tendl2025 and endf8")
    ran = [a for a in record.get("sensitivity_arms", []) if a in SENSITIVITY]
    for e in experiments:
        name = f"{e['material']}/{e['experiment']}"
        if e["n_points"] != len(e["points"]) or not e["points"]:
            problems.append(f"{name}: no points or wrong point count")
            continue
        if e.get("actinv_endf8_points") != e["n_points"]:
            problems.append(f"{name}: ACTINV/ENDF-8 arm has a different point count")
        if e.get("openmc_status") != "executed":
            problems.append(f"{name}: OpenMC case status {e.get('openmc_status')}")
        for arm in ran:
            if e.get(arm + "_status") != "executed":
                problems.append(f"{name}: {arm} case status {e.get(arm + '_status')}")
        for p in e["points"]:
            if not (good(p["measured"]) and p["measured"] > 0 and good(p["time_s"]) and p["time_s"] > 0):
                problems.append(f"{name}: invalid measurement at {p['time_s']}")
            if p.get("actinv_endf8_time_s") != p["time_s"] or p.get("actinv_endf8_measured") != p["measured"]:
                problems.append(f"{name} t={p['time_s']}: ACTINV arms disagree on the measured point")
            for arm in PRIMARY + tuple(ran):
                if not good(p.get(KEYS[arm])):
                    problems.append(f"{name} t={p['time_s']}: arm {arm} has no finite value")
            for key in ["openmc_boundary_match"] + [a + "_boundary_match" for a in ran]:
                if p.get(key) is not True:
                    problems.append(f"{name} t={p['time_s']}: {key} is not true")
    points, dropped = common_points(experiments)
    total = sum(e["n_points"] for e in experiments)
    if total and (total - len(points)) > MAX_DROPPED_FRACTION * total:
        problems.append(f"{total - len(points)} of {total} points have a non-positive calculation in some arm")
    if P32 in ran:
        rep = record.get("reproduction_vs_openmc_0_16_record") or {}
        if rep.get("points", 0) < 300:
            problems.append("fewer than 300 points shared with the sealed OpenMC 0.16.0 record")
        elif not (rep.get("max_abs_log_ratio") is not None and rep["max_abs_log_ratio"] <= REPRODUCTION_TOLERANCE):
            problems.append("OpenMC 0.15.3 with the P32 chain does not reproduce the sealed 0.16.0 values")
    return problems[:200]


def common_points(experiments):
    """Points with a positive measurement and a positive calculation in all three primary arms."""
    kept, dropped = [], []
    for e in experiments:
        for p in e["points"]:
            ok = good(p["measured"]) and p["measured"] > 0 and all(
                good(p.get(KEYS[a])) and p[KEYS[a]] > 0 for a in PRIMARY)
            (kept if ok else dropped).append({**p, "material": e["material"], "experiment": e["experiment"]})
    return kept, dropped


def stats(logs):
    """logs: ln(C/E) per point."""
    n = len(logs)
    ab = sorted(abs(v) for v in logs)
    return {"points": n, "geometric_mean_C_over_E": math.exp(math.fsum(logs) / n),
            "median_abs_log_C_over_E": statistics.median(ab),
            "p90_abs_log_C_over_E": ab[min(n - 1, math.ceil(0.9 * n) - 1)], "max_abs_log_C_over_E": ab[-1],
            "within_30_percent_points": sum(v <= WITHIN_LOG for v in ab)}


def arm_logs(points, arm):
    return [math.log(p[KEYS[arm]] / p["measured"]) for p in points]


def bootstrap_difference(points, arm_a, arm_b):
    """95% interval of median|ln C/E|(arm_b) - median|ln C/E|(arm_a), resampling experiments with replacement."""
    by = {}
    for p in points:
        by.setdefault((p["material"], p["experiment"]), []).append(p)
    keys = sorted(by)
    rng = random.Random(BOOTSTRAP_SEED)
    diffs = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [q for k in (rng.choice(keys) for _ in keys) for q in by[k]]
        diffs.append(statistics.median(abs(v) for v in arm_logs(sample, arm_b))
                     - statistics.median(abs(v) for v in arm_logs(sample, arm_a)))
    diffs.sort()
    return {"low": diffs[int(0.025 * len(diffs))], "high": diffs[int(0.975 * len(diffs)) - 1],
            "resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED}


def summary(record):
    experiments = record["experiments"]
    points, dropped = common_points(experiments)
    arms = {a: stats(arm_logs(points, a)) for a in PRIMARY}
    by = {}
    for p in points:
        by.setdefault((p["material"], p["experiment"]), []).append(p)
    per_experiment = []
    for (material, experiment), ps in sorted(by.items()):
        row = {"material": material, "experiment": experiment, "points": len(ps)}
        for a in PRIMARY:
            logs = arm_logs(ps, a)
            row[a] = {"geometric_mean_C_over_E": math.exp(math.fsum(logs) / len(logs)),
                      "median_abs_log_C_over_E": statistics.median(abs(v) for v in logs),
                      "all_within_30_percent": all(abs(v) <= WITHIN_LOG for v in logs)}
        per_experiment.append(row)
    for a in PRIMARY:
        arms[a]["experiments_all_points_within_30_percent"] = sum(r[a]["all_within_30_percent"] for r in per_experiment)
        arms[a]["median_over_experiments_of_median_abs_log"] = statistics.median(
            r[a]["median_abs_log_C_over_E"] for r in per_experiment)
    result = {"common_points": len(points), "experiments": len(by),
              "dropped_points": [{k: d[k] for k in ("material", "experiment", "time_s")} for d in dropped],
              "arms": arms, "per_experiment": per_experiment,
              "bootstrap_openmc_minus_actinv_tendl2025": bootstrap_difference(points, "actinv_tendl2025", "openmc"),
              "bootstrap_openmc_minus_actinv_endf8": bootstrap_difference(points, "actinv_endf8", "openmc")}
    sens_points = {}
    for arm in SENSITIVITY:
        covered = [p for p in points if good(p.get(arm)) and p[arm] > 0]
        if covered:
            sens_points[arm] = covered
            result[arm] = {"stats": stats(arm_logs(covered, arm)), "openmc_same_points": stats(arm_logs(covered, "openmc")),
                           "actinv_tendl2025_same_points": stats(arm_logs(covered, "actinv_tendl2025"))}
    a, e, o = arms["actinv_tendl2025"], arms["actinv_endf8"], arms["openmc"]
    sentences = [
        f"Against {a['points']} measured decay-heat points from {len(by)} JAEA FNS experiments, ACTINV with its shipped "
        f"TENDL-2025 data has a median |ln C/E| of {a['median_abs_log_C_over_E']:.3f}; OpenMC {p172.OPENMC_VERSION} "
        f"depletion with {CHAIN_TEXT} and ENDF/B-VIII.1 has {o['median_abs_log_C_over_E']:.3f}.",
        f"Given the same ENDF/B-VIII.1 cross sections as OpenMC, ACTINV has a median |ln C/E| of "
        f"{e['median_abs_log_C_over_E']:.3f} on the same points.",
    ]
    if ISOMER in sens_points:
        sentences.append(
            f"With isomer branching ratios from the ENDF/B-VIII.1 library added to that chain, OpenMC has a median "
            f"|ln C/E| of {result[ISOMER]['stats']['median_abs_log_C_over_E']:.3f} on the "
            f"{len(sens_points[ISOMER])} of those points it covers.")
    if P32 in sens_points:
        sentences.append(
            f"With a chain built from ENDF/B-VIII.0 decay data and TENDL-2025 reaction channels instead "
            f"(Chain.from_endf, ground-state targets), OpenMC has a median |ln C/E| of "
            f"{result[P32]['stats']['median_abs_log_C_over_E']:.3f} on the {len(sens_points[P32])} of those points "
            f"it covers.")
    return result, sentences


def evaluate(record, *, source_tree=None):
    gates = {"G0": g0(), "G1": g1(record, source_tree=source_tree), "G2": g2(record)}
    result, sentences = summary(record) if not gates["G2"] else ({}, [])
    gates["G3"] = [] if sentences else ["summary not computed (fidelity failed)"]
    return gates, result, sentences


def synthetic_record(sens=()):
    cg = {"memory.max": str(6 * p172.GIB), "memory.swap.max": "0", "pids.max": "128", "cpu.max": "200000 100000"}
    record = {"schema": p172.SCHEMA,
              "inputs": {k: {"sha256": v or "f" * 64} for k, (_, v) in p172.PINNED.items()},
              "environment": {"module_sha256": "m" * 64, "endf8_module_sha256": "m" * 64,
                              "openmc_version": p172.OPENMC_VERSION, "clean_tree": True, "crates_tree": "tree",
                              "cgroup": cg, "openmc_environment": {"cgroup": cg}},
              "actinv_records": {"tendl2025": {"pass": True, "checks": {c: True for c in CB2_REQUIRED_CHECKS}},
                                 "endf8": {"pass": True, "checks": {c: True for c in CB2_REQUIRED_CHECKS}}},
              "sensitivity_arms": list(sens),
              "reproduction_vs_openmc_0_16_record": (
                  {"points": 400, "max_abs_log_ratio": 1e-4} if P32 in sens else None),
              "experiments": []}
    for i in range(p172.EXPERIMENTS):
        points = []
        for j, t in enumerate((50.0, 500.0, 5000.0)):
            point = {"time_s": t, "measured": 1.0, "sigma": 0.1, "actinv_tendl2025": 1.1, "actinv_endf8": 0.9,
                     "actinv_endf8_time_s": t, "actinv_endf8_measured": 1.0, "openmc": 0.5 + 0.01 * (i % 5),
                     "openmc_boundary_match": True}
            for arm, value in ((ISOMER, 0.8), (P32, 0.45)):
                if arm in sens:
                    point.update({arm: value, arm + "_boundary_match": True})
            points.append(point)
        record["experiments"].append({"material": f"M{i}", "experiment": "e", "n_points": 3, "points": points,
                                      "actinv_endf8_points": 3, "openmc_status": "executed",
                                      **{arm + "_status": "executed" for arm in sens}})
    return record


def actinv_arm_writes_into_new_directory():
    """run_actinv_arm against a stand-in cb2_fns that writes its result as the real one does."""
    import tempfile
    import types
    fake = types.ModuleType("cb2_fns")
    fake.EXPECTED = {}

    def fake_main():  # as the real one: write, then always raise SystemExit (1 when its own checks fail)
        fake.RESULT.write_text(json.dumps({"experiments": []}))
        raise SystemExit(1)
    fake.main = fake_main
    saved = {name: getattr(p172, name) for name in ("require_bounded", "sha256_file", "environment_record")}
    saved_env = {key: os.environ.get(key) for key in ("ACTINV_LIBRARY", "ACTINV_CB1_FNS_WORK")}
    saved_module = sys.modules.get("cb2_fns")
    sys.modules["cb2_fns"] = fake
    p172.require_bounded = lambda gib: {}
    p172.sha256_file = lambda path: "s" * 64
    p172.environment_record = lambda cg: {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "not-yet" / "actinv_tendl2025.json"
            try:
                p172.run_actinv_arm("tendl2025", out)
            except FileNotFoundError:
                return ["the ACTINV arm does not create its result directory"]
            except SystemExit:
                return ["the ACTINV arm lets cb2_fns's closing SystemExit through and writes no record block"]
            block = json.loads(out.read_text()).get("p172", {})
            if block.get("arm") != "tendl2025" or block.get("cb2_exit_code") != 1:
                return ["the ACTINV arm record lacks its p172 block or cb2_fns's exit code"]
            return []
    finally:
        for name, value in saved.items():
            setattr(p172, name, value)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if saved_module is None:
            sys.modules.pop("cb2_fns", None)
        else:
            sys.modules["cb2_fns"] = saved_module


def selftest():
    failures = []
    saved_pin = p172.ACTINV_MODULE_SHA256
    p172.ACTINV_MODULE_SHA256 = "m" * 64
    try:
        def run(label, mutate, gate, sens=()):
            trial = synthetic_record(sens)
            mutate(trial)
            gates, result, sentences = evaluate(trial, source_tree="tree")
            bad = [name for name in ("G1", "G2") if gates[name]]
            if gate is None and bad:
                failures.append(f"{label}: expected pass, failed {bad}: {[gates[b][:3] for b in bad]}")
            if gate is not None and gate not in bad:
                failures.append(f"{label}: expected {gate} to fail")
            return result, sentences

        result, sentences = run("clean", lambda r: None, None)
        a, e, o = (result["arms"][k] for k in PRIMARY)
        if (result["common_points"] != 396 or result["experiments"] != 132
                or abs(a["median_abs_log_C_over_E"] - math.log(1.1)) > 1e-12
                or abs(e["geometric_mean_C_over_E"] - 0.9) > 1e-12 or abs(o["median_abs_log_C_over_E"]
                                                                          - abs(math.log(0.5))) > 0.05):
            failures.append(f"clean summary wrong: {a} {e} {o}")
        if len(sentences) != 2 or not sentences[0].startswith("Against 396 measured decay-heat points from 132 JAEA"):
            failures.append(f"sentences wrong: {sentences}")
        if "OpenMC 0.15.3 depletion with its published ENDF/B-VIII.1 fast-spectrum depletion chain (chain_endfb81_fast.xml) and ENDF/B-VIII.1 has" not in sentences[0]:
            failures.append("chain identity missing from the first sentence")
        lo = result["bootstrap_openmc_minus_actinv_tendl2025"]
        if not lo["low"] > 0:
            failures.append(f"bootstrap interval wrong: {lo}")
        result, sentences = run("sensitivities", lambda r: None, None, sens=(P32, ISOMER))
        if len(sentences) != 4 or abs(result[ISOMER]["stats"]["median_abs_log_C_over_E"]
                                      - abs(math.log(0.8))) > 1e-12 or "chain_endfb81_fast.xml" not in sentences[0]:
            failures.append(f"sensitivity summary wrong: {sentences}")
        run("schema", lambda r: r.update(schema="x"), "G1")
        run("chain", lambda r: r["inputs"]["chain_xml"].update(sha256="0" * 64), "G1")
        run("xs", lambda r: r["inputs"]["cross_sections_xml"].update(sha256="0" * 64), "G1")
        run("library", lambda r: r["inputs"]["endf8_library"].update(sha256="0" * 64), "G1")
        run("module", lambda r: r["environment"].update(endf8_module_sha256="0" * 64), "G1")
        run("version", lambda r: r["environment"].update(openmc_version="0.16.0"), "G1")
        run("dirty", lambda r: r["environment"].update(clean_tree=False), "G1")
        run("tree", lambda r: r["environment"].update(crates_tree="other"), "G1")
        run("cgroup", lambda r: r["environment"]["cgroup"].update({"pids.max": "max"}), "G1")
        run("openmc cgroup", lambda r: r["environment"]["openmc_environment"]["cgroup"].update(
            {"memory.max": str(9 * p172.GIB)}), "G1")
        run("dropped experiment", lambda r: r["experiments"].pop(), "G2")
        run("endf8 points", lambda r: r["experiments"][3].update(actinv_endf8_points=2), "G2")
        run("measured differs", lambda r: r["experiments"][5]["points"][1].update(actinv_endf8_measured=2.0), "G2")
        run("openmc failed", lambda r: r["experiments"][7].update(openmc_status="arm_failure"), "G2")
        run("boundary", lambda r: r["experiments"][9]["points"][0].update(openmc_boundary_match=False), "G2")
        run("nan", lambda r: r["experiments"][2]["points"][2].update(openmc=float("nan")), "G2")
        run("arm check", lambda r: r["actinv_records"]["endf8"]["checks"].update(input_identities_match=False), "G2")
        run("positive pairs is not required", lambda r: r["actinv_records"]["endf8"].update(
            **{"pass": False, "checks": dict(r["actinv_records"]["endf8"]["checks"],
                                             all_experiments_have_positive_measurement_pairs=False)}), None)
        run("few shared", lambda r: r["reproduction_vs_openmc_0_16_record"].update(points=10), "G2", sens=(P32,))

        def zero_many(r):
            for e in r["experiments"][:10]:
                for p in e["points"]:
                    p["openmc"] = 0.0
        run("dropped points", zero_many, "G2")
        run("isomer failed", lambda r: r["experiments"][4].update(openmc_isomer_status="arm_failure"), "G2",
            sens=(ISOMER,))
        run("p32 failed", lambda r: r["experiments"][4].update(openmc_p32_status="not_run"), "G2", sens=(P32,))
        run("p32 not reproduced", lambda r: r["reproduction_vs_openmc_0_16_record"].update(max_abs_log_ratio=0.2), "G2",
            sens=(P32,))
        run("no reproduction needed without p32", lambda r: None, None)
        p172.ACTINV_MODULE_SHA256 = None
        gates, _, _ = evaluate(synthetic_record(), source_tree="tree")
        if not any("not pinned" in x for x in gates["G1"]):
            failures.append("an unpinned module digest must fail G1")
        # branch-ratio helper on a synthetic library: target 0 = 74-186 with (n,2n) split 25/75 between states
        rows = [[0, 16, -1, -1, 0], [0, 16, 74185, 0, -1], [0, 16, 74185, 1, -1], [0, 102, -1, -1, 0],
                [0, 102, 74187, 0, -1]]
        sig = [[1.0, 1.0], [0.75, 0.75], [0.25, 0.25], [2.0, 2.0], [2.0, 2.0]]
        names = {"W186", "W185", "W185_m1"}
        namer = lambda z, a, m: f"W{a}" + (f"_m{m}" if m else "")  # noqa: E731
        ratios = p172.isomer_branch_ratios(rows, sig, [{"za": 74186, "liso": 0}], [0.5, 0.5], namer, names)
        if (set(ratios) != {"(n,2n)"} or abs(ratios["(n,2n)"]["W186"]["W185_m1"] - 0.25) > 1e-12
                or abs(ratios["(n,2n)"]["W186"]["W185"] - 0.75) > 1e-12):
            failures.append(f"isomer branch ratios wrong: {ratios}")
        if p172.isomer_branch_ratios(rows, sig, [{"za": 74186, "liso": 0}], [0.5, 0.5], namer, {"W186", "W185"}):
            failures.append("a missing isomer must fold into the ground state and yield no split")
        if p172.match_cooling_index([10.0, 100.0, 1000.0], 99.0) != (1, True) or \
                p172.match_cooling_index([10.0, 100.0, 1000.0], 300.0)[1]:
            failures.append("cooling boundary matching wrong")
        # P171's two harness defects: the ACTINV arm must create its result directory, and must survive cb2_fns's
        # closing SystemExit with its record block written
        failures += actinv_arm_writes_into_new_directory()
    finally:
        p172.ACTINV_MODULE_SHA256 = saved_pin
    print("selftest:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
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
        "protocol": "ACTINV-P172",
        "verdict": "P172-PASS (pending G4, CI on the evidence commit)" if passed else "P172-FAIL",
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
    else:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    for sentence in sentences:
        print("  " + sentence)
    print(verdict["verdict"] if passed else "P172-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
