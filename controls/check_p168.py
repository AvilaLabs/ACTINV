#!/usr/bin/env python3
"""P168 checker (protocols/ACTINV-P168_PROTOCOL.md): ACTINV's solver against high-precision references in extreme
regimes (the limits map). Reads results/p168_limits.json written by controls/p168_limits.py. G0-G3 are evaluated here;
G4 (CI on the pushed evidence commit) is external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P168_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Pins: the references manifest, the registered source binary, the crates tree of the source commit, the registered
      constants; a clean tracked tree; the bounded cgroup.
  G2  Fidelity: every registered case at both CRAM orders exactly once; every reference file reproduces its manifest
      hash; each run record is internally consistent (status derived from the recorded worst errors and thresholds,
      step counts from the references, step times to 1e-9); the closed forms (two-member Bateman and the Jordan blocks
      of case 2, the triple chain of 5d, the periodic steady state and undepleted pulse-train solution) agree with
      the committed references to 1e-12 in double precision.
  G3  The limits-map summary and allowed sentences are recomputed and written to results/p168_verdict.json (with
      --no-write, compared with it).

Verdict: P168-PASS if G0-G3 hold (G4 external). Where the solver holds or breaks is reported, not gated.

Usage: check_p168.py [--record FILE] [--no-write]
       check_p168.py --selftest
"""
import argparse
import copy
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
from p168 import cases, compare, thin, to_actinv  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P168_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p168_limits.json"
VERDICT = ROOT / "results" / "p168_verdict.json"
DATA = ROOT / "results" / "p168"

SCHEMA = "actinv-p168-limits-1"
PINS = {
    "refs_manifest_sha256": "998cff380f68d808ffecb45386419fecd8e568798ae4b01b00568835908c7974",
    "binary_sha256": "186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194",  # the P163 release build
    "source_commit": "d6f516b",  # commit whose crates tree built that binary
}
GIB = 1 << 30
GUI_CRATE = "crates/actinv-gui/"


def changed_crate_paths(source_commit, head):
    """Paths under crates/ that differ between the source commit and `head` (empty if git cannot tell)."""
    if not head:
        return []
    return git("diff", "--name-only", source_commit, head, "--", "crates").split()
ANALYTIC_TOLERANCE = 1e-12
STATUSES = ("holds", "breaks", "fails-to-run")


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
            for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P168_AMENDMENT_*.md"))]
            if f"{sha256(path)}  protocols/{path.name}" not in logged]


def cgroup_ok(cg):
    try:
        quota, period = cg["cpu.max"].split()
        return (int(cg["memory.max"]) <= 6 * GIB and cg["memory.swap.max"] == "0" and int(cg["pids.max"]) <= 128
                and quota != "max" and int(quota) <= 2 * int(period))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def g1(record, *, pins, data_dir, source_tree=None):
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("wrong schema")
    manifest = data_dir / "refs_manifest.json"
    if pins["refs_manifest_sha256"] != sha256(manifest):
        problems.append("checked-out references manifest is not the pin")
    if record.get("refs_manifest_sha256") != pins["refs_manifest_sha256"]:
        problems.append("references manifest in the record is not the pin")
    registered = {"orders": list(cases.ORDERS), "timeout_s": cases.TIMEOUT_S, "thresholds": cases.THRESHOLDS,
                  "activity_fraction": cases.ACTIVITY_FRACTION}
    if record.get("constants") != registered:
        problems.append("recorded constants are not the registered ones")
    environment = record.get("environment", {})
    if len(pins["binary_sha256"]) != 64:
        problems.append("source binary is not registered yet")
    elif environment.get("binary_sha256") != pins["binary_sha256"]:
        problems.append("binary is not the registered source binary")
    if pins["source_commit"].startswith("TO-BE") and source_tree is None:
        problems.append("source commit is not registered yet")
    else:
        expected = source_tree if source_tree is not None else git("rev-parse", f"{pins['source_commit']}:crates")
        if not expected or environment.get("crates_tree") != expected:
            # the CLI binary does not link actinv-gui: a tree that differs only there is the same solver
            changed = changed_crate_paths(pins["source_commit"], environment.get("git_head", ""))
            if not changed or any(not path.startswith(GUI_CRATE) for path in changed):
                problems.append(f"crates tree differs from {pins['source_commit']}'s outside {GUI_CRATE}")
    if not environment.get("clean_tree"):
        problems.append("tracked tree was not clean")
    if not cgroup_ok(environment.get("cgroup", {})):
        problems.append("harness cgroup is not the bounded scope")
    return problems


def expected_steps(case, doc):
    scheduled = 2 * case["pulses"] if case["pulses"] else len(doc["schedule"])
    return scheduled, sum(1 for s in doc["reference_steps"] if s["step"] <= scheduled)


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def check_run(run, case, scheduled, kept, binary_sha):
    """Internal consistency of one run record; returns a list of problems."""
    where = f"{run.get('case')} cram{run.get('cram_order')}"
    problems = []
    if run.get("mode") != case["mode"] or run.get("pulses_run") != case["pulses"]:
        problems.append(f"{where}: mode or pulse count is not the registered one")
    if run.get("steps_scheduled") != scheduled:
        problems.append(f"{where}: {run.get('steps_scheduled')} scheduled steps, expected {scheduled}")
    if binary_sha is not None and run.get("binary_sha256") != binary_sha:
        problems.append(f"{where}: run used a different binary")
    if run.get("status") not in STATUSES:
        return problems + [f"{where}: status {run.get('status')}"]
    if not finite(run.get("wall_s")) or run["wall_s"] < 0:
        problems.append(f"{where}: wall time {run.get('wall_s')}")
    if run.get("timed_out") and not (finite(run.get("wall_s")) and run["wall_s"] >= cases.TIMEOUT_S):
        problems.append(f"{where}: timed out before the timeout")
    status = run["status"]
    if status == "fails-to-run":
        if not (run.get("timed_out") or run.get("exit_status") != 0 or run.get("reason")):
            problems.append(f"{where}: fails-to-run with a clean exit")
        if run.get("worst") is not None or run.get("first_failure") is not None:
            problems.append(f"{where}: fails-to-run carries error values")
        return problems
    if run.get("timed_out") or run.get("exit_status") != 0:
        problems.append(f"{where}: {status} after a failed process")
    metrics = ["M1", "M2"] + (["M3"] if case["trace"] else [])
    worst = run.get("worst")
    if not isinstance(worst, dict) or sorted(worst) != sorted(metrics):
        return problems + [f"{where}: worst errors are not {metrics}"]
    if not all(isinstance(worst[m].get("value"), (int, float)) and not math.isnan(worst[m]["value"]) for m in metrics):
        return problems + [f"{where}: a worst error is not a number"]
    if run.get("steps_compared") != kept:
        problems.append(f"{where}: {run.get('steps_compared')} steps compared, expected {kept}")
    if not (finite(run.get("time_error")) and run["time_error"] <= cases.TIME_TOLERANCE):
        problems.append(f"{where}: step times differ from the reference by {run.get('time_error')}")
    derived = compare.status_of(worst)
    if derived != status:
        problems.append(f"{where}: status {status} but the recorded errors give {derived}")
    first = run.get("first_failure")
    if status == "holds" and first is not None:
        problems.append(f"{where}: holds with a first failure")
    if status == "breaks":
        if not first or first.get("metric") not in metrics or not first.get("value", 0) > cases.THRESHOLDS[first["metric"]] \
                or first.get("nuclide") is None or not isinstance(first.get("step"), int):
            problems.append(f"{where}: breaks without a valid first failure")
        elif first["value"] > worst[first["metric"]]["value"] * (1 + 1e-12):
            problems.append(f"{where}: first failure exceeds the worst value")
    return problems


def _close(computed, recorded, tolerance, *, absolute=False):
    if absolute:
        return abs(computed - recorded) <= tolerance
    return abs(computed - recorded) <= tolerance * abs(recorded)


def _initial(doc, name):
    return float(doc["initial_atoms_per_g"][name])


def analytic_checks(data_dir):
    """Closed forms in double precision against the committed references."""
    problems = []
    refs = Path(data_dir) / "refs"
    doc = json.loads((refs / "2_very_short_times.json").read_text())
    nuclides = doc["nuclides"]
    for tag, first in zip("abcde", range(0, 15, 3)):
        a, b = nuclides[first], nuclides[first + 1]
        if tag == "c":  # nearly equal rates: lambda_B - lambda_A is not carried to 1e-12 by the printed constants
            continue
        la, lb, n0 = float(a["lambda_per_s"]), float(b["lambda_per_s"]), _initial(doc, a["name"])
        for step in doc["reference_steps"]:
            t = float(step["t_end_s"])
            inv = step["inventory_atoms_per_g"]
            if la == lb:
                n_a, n_b = n0 * math.exp(-la * t), n0 * la * t * math.exp(-la * t)
            elif la > lb:
                n_a = n0 * math.exp(-la * t)
                n_b = n0 * la / (la - lb) * math.exp(-lb * t) * -math.expm1(-(la - lb) * t)
            else:
                n_a = n0 * math.exp(-la * t)
                n_b = n0 * la / (lb - la) * n_a / n0 * -math.expm1(-(lb - la) * t)
            for name, value in ((a["name"], n_a), (b["name"], n_b)):
                ref = float(inv[name])
                if abs(ref) > 1e-280 and not _close(value, ref, ANALYTIC_TOLERANCE):
                    problems.append(f"2_very_short_times {name} at {step['t_end_s'][:8]} s: closed form {value:.15e}, reference {ref:.15e}")
    doc = json.loads((refs / "5d_defective_decay_triple.json").read_text())
    lam, n0 = float(doc["nuclides"][0]["lambda_per_s"]), _initial(doc, "Co58")
    for step in doc["reference_steps"]:
        t = float(step["t_end_s"])
        ref = float(step["inventory_atoms_per_g"]["Cu58"])
        value = n0 * (lam * t) ** 2 / 2 * math.exp(-lam * t)
        if not _close(value, ref, ANALYTIC_TOLERANCE):
            problems.append(f"5d Cu58 at {step['t_end_s'][:8]} s: closed form {value:.15e}, reference {ref:.15e}")
    for case in cases.CASES:
        if case["pulses"] is None:
            continue
        problems += _pulse_checks(json.loads((refs / f"{case['name']}.json").read_text()))
    return problems


def _pulse_checks(doc):
    problems = []
    name = doc["case"]
    train = doc["schedule"]["pulse_train"]
    steady = doc["periodic_steady_state"]
    lam = float(steady["lambda_per_s"])
    on = float(train["on"]["dt_s"])
    period = on + float(train["off"]["dt_s"])
    reaction = doc["reactions"][0]
    rate = float(reaction["products"][0]["sigma_b"]) * 1e-24 * float(doc["flux_n_cm2_s"])
    n0 = _initial(doc, "Au197")
    a_ss = n0 * rate * -math.expm1(-lam * on) / -math.expm1(-lam * period)
    if not _close(a_ss, float(steady["A_ss_Bq_per_g"]), ANALYTIC_TOLERANCE):
        problems.append(f"{name}: periodic steady state {a_ss:.15e} differs from {steady['A_ss_Bq_per_g']}")
    c = n0 * rate / lam * -math.expm1(-lam * on)
    by_step = {s["step"]: s["inventory_atoms_per_g"] for s in doc["reference_steps"]}
    for row in doc["reference_checkpoints"]:
        n = row["pulse_count"]
        exact = float(row["end_of_pulse"]["Au198"])
        undepleted = c * -math.expm1(-lam * period * n) / -math.expm1(-lam * period)
        recorded = float(row["exact_over_closed_form_minus_1"])  # printed with 6 significant digits
        if abs(exact / undepleted - 1 - recorded) > 1e-5 * abs(recorded) + ANALYTIC_TOLERANCE / 10:
            problems.append(f"{name} n={n}: exact over undepleted closed form differs from the recorded ratio")
        fraction = lam * exact / a_ss
        if not _close(fraction, float(row["fraction_of_periodic_steady_state_activity"]), 1e-11):  # 12 digits recorded
            problems.append(f"{name} n={n}: fraction of the steady state differs")
        kept = by_step.get(2 * n - 1)
        if kept is not None:  # the plain-stepping and period-propagator routes must agree
            for nuclide in ("Au197", "Au198"):
                if not _close(float(kept[nuclide]), float(row["end_of_pulse"][nuclide]), ANALYTIC_TOLERANCE):
                    problems.append(f"{name} n={n}: {nuclide} differs between the stepped and propagator references")
    return problems


def g2(record, *, data_dir):
    problems = []
    manifest = json.loads((data_dir / "refs_manifest.json").read_text())["files"]
    registered = {f"{c['name']}.json" for c in cases.CASES}
    if set(manifest) != registered:
        problems.append("manifest files are not the registered cases")
    refs = data_dir / "refs"
    on_disk = {p.name for p in refs.glob("*.json")}
    if on_disk != registered:
        problems.append(f"reference files {sorted(on_disk ^ registered)} are not the registered set")
    docs = {}
    for name in sorted(registered & on_disk):
        if sha256(refs / name) != manifest.get(name):
            problems.append(f"{name} does not reproduce its manifest hash")
        else:
            docs[name] = json.loads((refs / name).read_text())
    runs = record.get("runs", [])
    seen = {}
    for run in runs:
        seen.setdefault((run.get("case"), run.get("cram_order")), []).append(run)
    wanted = {(c["name"], o) for c in cases.CASES for o in cases.ORDERS}
    for key in sorted(wanted - set(seen), key=str):
        problems.append(f"missing run {key[0]} cram{key[1]}")
    for key in sorted(set(seen) - wanted, key=str):
        problems.append(f"unregistered run {key[0]} cram{key[1]}")
    binary = record.get("environment", {}).get("binary_sha256")
    for key, group in seen.items():
        if len(group) != 1:
            problems.append(f"{len(group)} runs of {key[0]} cram{key[1]}")
        if key in wanted and f"{key[0]}.json" in docs:
            case = cases.BY_NAME[key[0]]
            scheduled, kept = expected_steps(case, docs[f"{key[0]}.json"])
            problems += check_run(group[0], case, scheduled, kept, binary)
    if len(docs) == len(registered):
        problems += analytic_checks(data_dir)
    return problems


def summary(record):
    table = []
    for case in cases.CASES:
        for order in cases.ORDERS:
            run = next(r for r in record["runs"] if r["case"] == case["name"] and r["cram_order"] == order)
            worst = run["worst"] or {}
            table.append({"case": case["name"], "cram_order": order, "status": run["status"],
                          "worst_M1": worst.get("M1", {}).get("value"), "worst_M2": worst.get("M2", {}).get("value"),
                          "worst_M3": worst.get("M3", {}).get("value"), "wall_s": run["wall_s"],
                          "reason": run.get("reason"), "first_failure": run["first_failure"]})
    counts = {str(order): {s: sum(1 for t in table if t["cram_order"] == order and t["status"] == s) for s in STATUSES}
              for order in cases.ORDERS}
    n = len(cases.CASES)
    m1, m2 = cases.THRESHOLDS["M1"], cases.THRESHOLDS["M2"]
    first, second = (str(o) for o in cases.ORDERS)

    def worst_m1(order):
        values = [(t["worst_M1"], t["case"]) for t in table if t["cram_order"] == order and t["worst_M1"] is not None]
        return max(values) if values else (None, None)

    sentences = [
        f"On {n} synthetic stress cases checked against high-precision reference solutions (30 significant digits "
        f"recorded), ACTINV's CRAM-{first} solver held to {m1:.0e} normalized error and {m2:.0e} relative error on "
        f"the nuclides carrying activity in {counts[first]['holds']} cases, broke that bound in "
        f"{counts[first]['breaks']} and did not run to completion in {counts[first]['fails-to-run']}.",
        f"With CRAM-{second} the solver held in {counts[second]['holds']} cases, broke in {counts[second]['breaks']} and "
        f"did not run to completion in {counts[second]['fails-to-run']}.",
    ]
    for order in (first, second):
        value, name = worst_m1(int(order))
        if value is not None:
            sentences.append(f"The largest normalized error (M1) over the cases that ran at CRAM-{order} was "
                             f"{value:.2e} ({name}).")
    rescued = [t["case"] for t in table if t["cram_order"] == int(first) and t["status"] == "breaks"
               and next(u for u in table if u["case"] == t["case"] and u["cram_order"] == int(second))["status"] == "holds"]
    if rescued:
        sentences.append(f"CRAM-{second} held in {len(rescued)} of the {counts[first]['breaks']} cases where "
                         f"CRAM-{first} broke: {', '.join(rescued)}.")
    return {"limits_map": table, "counts": counts}, sentences


def evaluate(record, *, pins=PINS, data_dir=DATA, source_tree=None):
    gates = {"G0": g0(), "G1": g1(record, pins=pins, data_dir=data_dir, source_tree=source_tree),
             "G2": g2(record, data_dir=data_dir)}
    result, sentences = summary(record) if not gates["G2"] else ({}, [])
    gates["G3"] = [] if sentences else ["summary not computed (fidelity failed)"]
    return gates, result, sentences


# ---------------------------------------------------------------------------------------------------------- selftest
Q_SMALL, Q_LARGE = 2.0 ** -24, 2.0 ** -18  # binary-exact; activity shares 6e-8 (below the M2 cut) and 3.8e-6 (above)
EXACT_STRINGS = {"P": "1", "Q": str(Decimal(Q_SMALL)), "Q2": str(Decimal(Q_LARGE)), "S": "0.5"}


def _synthetic_reference():
    """P decays (T1/2 1 s), Q and Q2 decay with small abundance, S is stable; two steps."""
    def row(name, za, half):
        return {"name": name, "za": za, "liso": 0, "awr": "1.0", "half_life_s": half}
    return {"case": "synthetic", "nuclides": [row("P", 1001, "1"), row("Q", 2001, "1"), row("Q2", 3001, "1"),
                                              row("S", 4001, None)],
            "reference_steps": [{"step": 1, "label": "s1", "t_end_s": "1.0",
                                 "inventory_atoms_per_g": EXACT_STRINGS},
                                {"step": 2, "label": "s2", "t_end_s": "2.0",
                                 "inventory_atoms_per_g": EXACT_STRINGS}]}


def _result(step_values, times=(1.0, 2.0)):
    keys = {"P": (1, 1, 0), "Q": (2, 1, 0), "Q2": (3, 1, 0), "S": (4, 1, 0)}
    steps = []
    for t, values in zip(times, step_values):
        steps.append({"t_s": t, "inventory": [{"Z": keys[n][0], "A": keys[n][1], "LISO": 0, "atoms_per_g": v}
                                               for n, v in values.items()]})
    return steps


def _compare_tests(failures):
    ref = compare.Reference(_synthetic_reference(), 2)
    exact = {"P": 1.0, "Q": Q_SMALL, "Q2": Q_LARGE, "S": 0.5}

    def run(values, trace=False):
        return compare.compare_run(ref, _result(values), trace, 2)

    clean = run([exact, exact])
    if compare.status_of(clean["worst"]) != "holds" or clean["worst"]["M1"]["value"] != 0.0 or clean["first_failure"]:
        failures.append(f"exact result should hold: {clean}")
    # M2 threshold: Q carries 6e-8 of the activity (excluded), Q2 3.8e-6 (included)
    half_q = dict(exact, Q=1.5 * Q_SMALL)
    got = run([half_q, half_q])
    if got["worst"]["M2"]["value"] != 0.0 or got["worst"]["M1"]["value"] > 1e-7 or compare.status_of(got["worst"]) != "breaks":
        failures.append(f"Q below the activity threshold must not enter M2: {got['worst']}")
    q2 = dict(exact, Q2=Q_LARGE * (1 + 1e-3))
    got = run([exact, q2])
    if abs(got["worst"]["M2"]["value"] - 1e-3) > 1e-9 or got["worst"]["M2"]["nuclide"] != "Q2" or got["first_failure"]["step"] != 2 \
            or got["first_failure"]["metric"] != "M2":
        failures.append(f"Q2 above the threshold must give M2 = 1e-3 at step 2: {got}")
    # stable nuclide: M1 only; missing nuclide counts as zero
    s_off = dict(exact, S=0.5 * (1 + 1e-6))
    got = run([s_off, exact])
    if got["worst"]["M2"]["value"] != 0.0 or abs(got["worst"]["M1"]["value"] - 0.5e-6 / 1.0) > 1e-12 or got["first_failure"]["metric"] != "M1":
        failures.append(f"stable nuclide must enter M1 but not M2: {got['worst']}")
    missing = {k: v for k, v in exact.items() if k != "S"}
    got = run([missing, exact])
    if abs(got["worst"]["M1"]["value"] - 0.5) > 1e-12 or got["worst"]["M1"]["nuclide"] != "S":
        failures.append(f"missing nuclide must count as zero: {got['worst']}")
    # M2 at the bound holds, M1 at the bound holds, just above breaks
    at_bound = dict(exact, P=1.0 + 5e-11)
    if compare.status_of(run([at_bound, exact])["worst"]) != "holds":
        failures.append("M1 = 5e-11 must hold")
    above = dict(exact, P=1.0 + 2e-6)
    if run([above, exact])["first_failure"]["metric"] not in ("M1", "M2"):
        failures.append("2e-6 on P must break")
    # trace: M3 covers every reference nuclide, including trace and stable ones
    got = run([half_q, half_q], trace=True)
    if abs(got["worst"]["M3"]["value"] - 0.5) > 1e-12 or got["worst"]["M3"]["nuclide"] != "Q":
        failures.append(f"M3 must cover Q: {got['worst']}")
    if "M3" in run([exact, exact])["worst"]:
        failures.append("M3 must be absent outside the trace case")
    # Amendment A: a nuclide far below float range that no metric scores must not stop the comparison
    tiny = compare.Reference(_synthetic_reference(), 2)
    for step in tiny.steps:
        step["inv"]["Q"] = Decimal("1.2e-434729198902")
    try:
        got = compare.compare_run(tiny, _result([dict(exact, Q=1e-300), dict(exact, Q=0.0)]), False, 2)
        if compare.status_of(got["worst"]) != "holds":
            failures.append(f"an unscored nuclide far below float range must not change the status: {got['worst']}")
    except Exception as error:  # noqa: BLE001
        failures.append(f"an unscored nuclide far below float range stopped the comparison: {error!r}")
    # Amendment A: M3 leaves out references below 1e-300 atoms/g, which a double cannot carry
    deep = compare.Reference(_synthetic_reference(), 2)
    for step in deep.steps:
        step["inv"]["Q"] = Decimal("1e-1120")
    got = compare.compare_run(deep, _result([dict(exact, Q=0.0), dict(exact, Q=0.0)]), True, 2)
    if got["worst"]["M3"]["value"] != 0.0:
        failures.append(f"M3 must leave out references below the floor: {got['worst']}")
    nan = dict(exact, P=float("nan"))
    if compare.status_of(run([nan, exact])["worst"]) != "breaks" or run([nan, exact])["worst"]["M1"]["value"] != math.inf:
        failures.append("NaN must break with an infinite error")
    try:
        compare.compare_run(ref, _result([exact]), False, 2)
        failures.append("a short result must raise")
    except ValueError:
        pass
    scored = run([exact, exact])
    for args, expected in (((0, False, scored), "holds"), ((0, False, run([above, exact])), "breaks"),
                           ((1, False, None), "fails-to-run"), ((-9, True, None), "fails-to-run"),
                           ((0, False, None, "unreadable"), "fails-to-run")):
        if compare.classify_run(*args)["status"] != expected:
            failures.append(f"classify_run{args[:2]} should be {expected}")
    if compare.classify_run(0, True, scored)["status"] != "fails-to-run":
        failures.append("a timeout must fail to run even with scores")


def _helper_tests(failures):
    kept = thin.kept_pulse_steps(10_000)
    if len(kept) != 220 or kept[:3] != [1, 2, 3] or kept[19] != 20 or kept[-1] != 20_000 or 2 * 150 in kept \
            or not {199, 200, 19_999}.issubset(kept):
        failures.append(f"kept pulse steps wrong: {len(kept)} {kept[18:24]} {kept[-3:]}")
    if thin.kept_pulse_steps(5) != list(range(1, 11)):
        failures.append("short train must keep all steps")
    for value, text in (("6.931472e9", "6.931472+9"), ("0.9976", "9.976000-1"), ("6.931472e-10", "6.931472-10"),
                        ("9.9999999", "1.000000+1"), ("0", "0.000000+0"), (1, "1.000000+0"), ("1e-9", "1.000000-9")):
        if to_actinv.endf_f(value) != text:
            failures.append(f"endf_f({value}) = {to_actinv.endf_f(value)}, expected {text}")
    if to_actinv.daughter(27060, 3) != (27, 60) or to_actinv.daughter(92238, 4) != (90, 234):
        failures.append("daughter rules wrong")


def _synthetic_record(data_dir):
    runs = []
    for case in cases.CASES:
        doc = json.loads((data_dir / "refs" / f"{case['name']}.json").read_text())
        scheduled, kept = expected_steps(case, doc)
        for order in cases.ORDERS:
            worst = {"M1": {"value": 1e-14, "step": 1, "label": "x", "nuclide": "N"},
                     "M2": {"value": 1e-9, "step": 1, "label": "x", "nuclide": "N"}}
            if case["trace"]:
                worst["M3"] = {"value": 1e-9, "step": 1, "label": "x", "nuclide": "N"}
            runs.append({"case": case["name"], "cram_order": order, "mode": case["mode"], "pulses_run": case["pulses"],
                         "steps_scheduled": scheduled, "decay_only": False, "binary_sha256": "b" * 64, "wall_s": 1.5,
                         "exit_status": 0, "timed_out": False, "stderr_tail": "", "result_bytes": 10,
                         "steps_compared": kept, "time_error": 1e-15, "status": "holds", "reason": None,
                         "worst": worst, "first_failure": None})
    return {"schema": SCHEMA, "protocol": "ACTINV-P168",
            "environment": {"binary_sha256": "b" * 64, "crates_tree": "tree", "clean_tree": True,
                            "cgroup": {"memory.max": str(6 * GIB), "memory.swap.max": "0", "pids.max": "128",
                                       "cpu.max": "200000 100000"}},
            "refs_manifest_sha256": PINS["refs_manifest_sha256"],
            "constants": {"orders": list(cases.ORDERS), "timeout_s": cases.TIMEOUT_S, "thresholds": cases.THRESHOLDS,
                          "activity_fraction": cases.ACTIVITY_FRACTION},
            "runs": runs}


def _find(record, name, order):
    return next(r for r in record["runs"] if r["case"] == name and r["cram_order"] == order)


def selftest():
    failures = []
    _compare_tests(failures)
    _helper_tests(failures)
    pins = dict(PINS, binary_sha256="b" * 64, source_commit="abcdef1")
    record = _synthetic_record(DATA)

    def run(label, mutate, gate, data_dir=DATA, base=record):
        trial = copy.deepcopy(base)
        mutate(trial)
        gates, result, sentences = evaluate(trial, pins=pins, data_dir=data_dir, source_tree="tree")
        bad = [name for name in ("G1", "G2") if gates[name]]
        if gate is None and bad:
            failures.append(f"{label}: expected pass, failed {bad}: {[gates[b] for b in bad]}")
        if gate is not None and gate not in bad:
            failures.append(f"{label}: expected {gate} to fail")
        return result, sentences

    # a mixed limits map: holds, breaks and fails-to-run at both orders
    def mixed(r):
        run16 = _find(r, "3_co59_flux1e16", 16)
        run16.update(status="breaks", worst={"M1": {"value": 3e-5, "step": 4, "label": "x", "nuclide": "Co60"},
                                             "M2": {"value": 1e-3, "step": 4, "label": "x", "nuclide": "Co60"}},
                     first_failure={"metric": "M1", "value": 3e-5, "step": 4, "label": "x", "nuclide": "Co60"})
        slow = _find(r, "4_pulse_train_0p1s_1e5", 48)
        slow.update(status="fails-to-run", timed_out=True, exit_status=-9, wall_s=1800.0, reason="timeout",
                    worst=None, first_failure=None, steps_compared=None, time_error=None)
    result, sentences = run("mixed", mixed, None)
    if result["counts"]["16"] != {"holds": 18, "breaks": 1, "fails-to-run": 0} \
            or result["counts"]["48"] != {"holds": 18, "breaks": 0, "fails-to-run": 1}:
        failures.append(f"counts wrong: {result['counts']}")
    if not sentences[0].startswith("On 19 synthetic stress cases") or "held to 1e-10 normalized error" not in sentences[0] \
            or "in 18 cases, broke that bound in 1" not in sentences[0]:
        failures.append(f"sentence wrong: {sentences[0]}")
    run("clean", lambda r: None, None)
    run("binary", lambda r: r["environment"].update(binary_sha256="0" * 64), "G1")
    run("dirty", lambda r: r["environment"].update(clean_tree=False), "G1")
    run("cgroup", lambda r: r["environment"]["cgroup"].update({"memory.max": str(8 * GIB)}), "G1")
    run("manifest", lambda r: r.update(refs_manifest_sha256="0" * 64), "G1")
    run("constants", lambda r: r["constants"].update(timeout_s=60), "G1")
    run("crates", lambda r: r["environment"].update(crates_tree="other"), "G1")
    global changed_crate_paths
    real_changed = changed_crate_paths
    try:
        changed_crate_paths = lambda source, head: ["crates/actinv-gui/src/web_app.rs"]
        run("crates differ in gui only", lambda r: r["environment"].update(crates_tree="other"), None)
        changed_crate_paths = lambda source, head: ["crates/actinv-gui/src/web_app.rs", "crates/actinv-core/src/cram.rs"]
        run("crates differ in core", lambda r: r["environment"].update(crates_tree="other"), "G1")
    finally:
        changed_crate_paths = real_changed
    run("missing", lambda r: r["runs"].pop(), "G2")
    run("duplicate", lambda r: r["runs"].append(copy.deepcopy(r["runs"][0])), "G2")
    run("status", lambda r: _find(r, "7_tungsten_burnup", 16).update(status="breaks"), "G2")
    run("holds above bound", lambda r: _find(r, "7_tungsten_burnup", 16)["worst"]["M2"].update(value=1e-3), "G2")
    run("nan", lambda r: _find(r, "7_tungsten_burnup", 16)["worst"]["M1"].update(value=float("nan")), "G2")
    run("no M3", lambda r: _find(r, "6b_dynamic_range_trace", 16)["worst"].pop("M3"), "G2")
    run("steps", lambda r: _find(r, "2_very_short_times", 16).update(steps_compared=4), "G2")
    run("pulses", lambda r: _find(r, "4_pulse_train_1s_1e6", 48).update(pulses_run=1_000_000), "G2")
    run("time", lambda r: _find(r, "1a_u238_chain_pure_parent", 16).update(time_error=1e-6), "G2")
    run("fake timeout", lambda r: _find(r, "7_tungsten_burnup", 16).update(
        status="fails-to-run", timed_out=True, wall_s=3.0, reason="timeout", worst=None, first_failure=None), "G2")
    run("clean exit fails", lambda r: _find(r, "7_tungsten_burnup", 16).update(
        status="fails-to-run", worst=None, first_failure=None), "G2")
    run("other binary", lambda r: _find(r, "7_tungsten_burnup", 16).update(binary_sha256="c" * 64), "G2")
    with tempfile.TemporaryDirectory() as temporary:
        copied = Path(temporary) / "p168"
        shutil.copytree(DATA, copied)
        run("copy clean", lambda r: None, None, data_dir=copied)
        target = copied / "refs" / "2_very_short_times.json"
        doc = json.loads(target.read_text())
        doc["reference_steps"][3]["inventory_atoms_per_g"]["Mg30"] = "1.00000000000000000000000000000e+19"
        target.write_text(json.dumps(doc, indent=1) + "\n")
        run("tampered reference", lambda r: None, "G2", data_dir=copied)
        shutil.copy(DATA / "refs" / "2_very_short_times.json", target)
        pulse = copied / "refs" / "4_pulse_train_0p1s_1e5.json"
        doc = json.loads(pulse.read_text())
        doc["periodic_steady_state"]["A_ss_Bq_per_g"] = "3.0158e+11"
        pulse.write_text(json.dumps(doc, indent=1) + "\n")
        problems = analytic_checks(copied)
        if not any("periodic steady state" in p for p in problems):
            failures.append("tampered steady state not detected by the analytic checks")
    if analytic_checks(DATA):
        failures.append(f"analytic checks fail on the committed references: {analytic_checks(DATA)[:3]}")
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
        "protocol": "ACTINV-P168",
        "verdict": "P168-PASS (pending G4, CI on the evidence commit)" if passed else "P168-FAIL",
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
    print(verdict["verdict"] if passed else "P168-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
