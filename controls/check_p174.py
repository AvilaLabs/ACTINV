#!/usr/bin/env python3
"""P174 checker (protocols/ACTINV-P174_PROTOCOL.md): below-floor states in activity and heat totals. Reads
results/p174_floor.json written by controls/p174_floor.py, recomputes every metric from its raw numbers and the committed
references, applies the decision rule and writes results/p174_verdict.json. G5 (CI on the pushed evidence commit) is
external.

  G0  The protocol (and any amendment), the harness and this checker are in protocols/protocol_hash.txt.
  G1  Limits and environment: the pinned references, the master binary and the registered constants; a candidate binary
      that differs from it; the harness as hashed; the candidate's crates/ sources hashed in the record; a clean tracked
      tree; the bounded cgroup; every registered run, and every discovered example, recorded exactly once.
  G2  Selection set, per candidate A and B: every run ran; the candidate contract holds (bound present and consistent,
      excluded states absent from the activity table, B and C refine exactly when the bound exceeds REL_TOL of the
      total, C's reconstructed states meet the threshold and rate separation, unresolved bounds named in the ledger); no bound violated; case 5d at CRAM-16 fixed (within TOL of the reference or
      covered by the bound) in activity and heat, and the status quo does break there; no total that held within TOL
      under the status quo is lost; the screen certificate of 5d contains the reference.
  G3  Held-out set: contract and bound validity as above (screen certificates included); no total that held is lost;
      the actinide guard (activity and heat within TOL of the reference at every step of h7 and h8); for B, M_tot at
      most A's at every step; for B and C, wall time at most COST_RATIO where they re-solve, no measurable cost
      where they do not (held-out cases and examples).
  G4  Nonregression: every public example runs and the status quo equals the master binary in `steps[*]`; each candidate
      differs only by added keys, apart from the steps where the master flagged a below-floor state (A) or from the first
      such step on (B, C: their changed state carries forward).

Decision, in this order: C if C passes G2-G4; otherwise B if B passes; otherwise A if A passes (A's G3 includes the
actinide guard: total activity within TOL of the reference on h7 and h8); otherwise the status quo stays. All arms are
reported. Verdict: P174-PASS if G0 and G1 hold, whatever the decision.

Usage: check_p174.py [--record FILE] [--no-write]
       check_p174.py --self-test
"""
import argparse
import copy
import decimal
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p174_floor as H  # noqa: E402
from p168 import compare  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P174_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p174_floor.json"
VERDICT = ROOT / "results" / "p174_verdict.json"

PINS = {
    "p168_refs_manifest_sha256": "998cff380f68d808ffecb45386419fecd8e568798ae4b01b00568835908c7974",
    "p174_refs_manifest_sha256": "748a344c88af6155667ea173c1f10b5afa081c0f6cf73a55862ef8e1fdf5b63b",  # results/p174/refs_manifest.json, made by make-refs
    "base_commit": H.BASE_COMMIT,
    "base_binary_sha256": "186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194",  # P163 release, d6f516b
}
FIX_CASE = "5d_defective_decay_triple"
DECISION_ORDER = ("C", "B", "A")  # fixed at registration: C if it passes, else B, else A, else the status quo
GIB = 1 << 30
INF = float("inf")
E_TOTAL = Decimal(repr(sum(H.ENERGIES_EV.values()))) * Decimal(repr(H.EV))  # heat per decay of a synthetic nuclide, J
BOUND_SLACK = Decimal("1e-12")  # the bound is a float sum; this much rounding is not a violation
TABLE_TOLERANCE = 1e-9  # reported activity entry against lambda N of the non-excluded states
CERT_TOLERANCE = Decimal("1e-9")
FLOOR_TOLERANCE = 1e-9
B_VS_A_SLACK = 1e-9
RESPONSES = ("act", "heat")
PUBLIC, FIXTURE = H.GROUP_PUBLIC, H.GROUP_FIXTURE


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def median(values):
    return H.median(values)


# --------------------------------------------------------------------------------------------------------- world
def default_world():
    return {"cases": H.case_list(), "screen": set(H.screen_cases()), "check_files": True,
            "refs_dir": {"selection": H.P168_REFS, "heldout": H.P174_REFS},
            "manifest": {"selection": H.P168_MANIFEST, "heldout": H.P174_MANIFEST},
            "pin_key": {"selection": "p168_refs_manifest_sha256", "heldout": "p174_refs_manifest_sha256"},
            "discover": H.discover, "hash_log": None, "ref_docs": None, "actinide": set(H.ACTINIDE_CASES)}


_REF_CACHE = {}


def reference_of(world, case_set, name, scheduled):
    key = (case_set, name, scheduled, id(world.get("ref_docs")))
    if key not in _REF_CACHE:
        if world.get("ref_docs") is not None:
            doc = world["ref_docs"][(case_set, name)]
        else:
            doc = json.loads((world["refs_dir"][case_set] / f"{name}.json").read_text())
        _REF_CACHE[key] = compare.Reference(doc, scheduled)
        _REF_CACHE[key].doc = doc
    return _REF_CACHE[key]


# ------------------------------------------------------------------------------------------------------------ G0
def g0(world):
    paths = [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P174_AMENDMENT_*.md")),
             ROOT / "controls" / "p174_floor.py", ROOT / "controls" / "check_p174.py"]
    logged = world["hash_log"] if world.get("hash_log") is not None else (HASH_LOG.read_text() if HASH_LOG.is_file()
                                                                          else "")
    problems = []
    for path in paths:
        if not path.is_file():
            problems.append(f"missing {path.relative_to(ROOT)}")
        elif f"{sha256(path)}  {path.relative_to(ROOT)}" not in logged:
            problems.append(f"{path.relative_to(ROOT)} hash not in protocol_hash.txt")
    return problems


# ------------------------------------------------------------------------------------------------------------ G1
def cgroup_ok(cg):
    try:
        quota, period = cg["cpu.max"].split()
        return (int(cg["memory.max"]) <= 6 * GIB and cg["memory.swap.max"] == "0" and int(cg["pids.max"]) <= 128
                and quota != "max" and int(quota) <= 2 * int(period))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def expected_run_keys(world):
    keys = []
    for case in world["cases"]:
        for order in H.ORDERS:
            for arm in H.ARMS:
                keys.append((case["set"], case["name"], order, arm, False))
                if case["name"] in world["screen"]:
                    keys.append((case["set"], case["name"], order, arm, True))
    return keys


def run_key(run):
    return (run.get("set"), run.get("case"), run.get("cram_order"), run.get("arm"), bool(run.get("screen")))


def index_runs(record):
    index, duplicates = {}, []
    for run in record.get("solver", {}).get("runs", []):
        key = run_key(run)
        if key in index:
            duplicates.append(key)
        index[key] = run
    return index, duplicates


def g1(record, world, pins):
    problems = []
    if record.get("schema") != H.SCHEMA:
        problems.append("wrong schema")
    solver, nonreg = record.get("solver"), record.get("nonregression")
    if not solver or not nonreg:
        return problems + ["the record lacks the solver or the nonregression section"]
    registered = json.loads(json.dumps(H.constants()))
    for name, section in (("solver", solver), ("nonregression", nonreg)):
        if section.get("constants") != registered:
            problems.append(f"{name}: recorded constants are not the registered ones")
    if solver.get("partial"):
        problems.append("the solver section is partial")
    env = record.get("environment", {})
    if world["check_files"]:
        if env.get("harness_sha256") != sha256(ROOT / "controls" / "p174_floor.py"):
            problems.append("the record was made by a different harness than the one hashed at registration")
        for case_set in ("selection", "heldout"):
            pin = pins[world["pin_key"][case_set]]
            manifest = world["manifest"][case_set]
            if len(pin) != 64:
                problems.append(f"{case_set} references manifest is not pinned yet")
            elif not manifest.is_file() or sha256(manifest) != pin:
                problems.append(f"checked-out {case_set} references manifest is not the pin")
            elif env.get("refs_manifests", {}).get("p168" if case_set == "selection" else "p174") != pin:
                problems.append(f"{case_set} references manifest in the record is not the pin")
            else:
                files = json.loads(manifest.read_text()).get("files", {})
                used = {c["name"] for c in world["cases"] if c["set"] == case_set}
                for name in sorted(used):
                    path = world["refs_dir"][case_set] / f"{name}.json"
                    if files.get(f"{name}.json") != (sha256(path) if path.is_file() else None):
                        problems.append(f"reference {case_set}/{name} does not reproduce its manifest hash")
        for path, digest in (env.get("candidate_sources") or {}).items():
            if not (ROOT / path).is_file() or sha256(ROOT / path) != digest:
                problems.append(f"candidate source {path} is not the recorded file")
        head = env.get("git_head")
        if head and git("rev-parse", f"{head}:crates") != env.get("crates_tree"):
            problems.append("recorded crates tree is not the recorded commit's")
    if not env.get("candidate_sources"):
        problems.append("the record names no candidate source files")
    binary, base_binary = env.get("binary_sha256"), env.get("base_binary_sha256")
    if len(pins["base_binary_sha256"]) != 64 or base_binary != pins["base_binary_sha256"]:
        problems.append("the master binary is not the pinned one")
    if not binary or binary == base_binary:
        problems.append("the candidate binary is missing or equals the master binary")
    if nonreg.get("binary_sha256") != binary or nonreg.get("base_binary_sha256") != base_binary:
        problems.append("the nonregression section used other binaries")
    if not env.get("clean_tree"):
        problems.append("tracked tree was not clean")
    if not cgroup_ok(env.get("cgroup", {})):
        problems.append("harness cgroup is not the bounded scope")
    index, duplicates = index_runs(record)
    problems += [f"duplicate run {key}" for key in duplicates]
    expected = expected_run_keys(world)
    problems += [f"missing run {key}" for key in expected if key not in index]
    problems += [f"unregistered run {key}" for key in index if key not in set(expected)]
    for key, run in index.items():
        if run.get("binary_sha256") != binary:
            problems.append(f"run {key} used another binary")
        if not (isinstance(run.get("wall_s"), list) and run["wall_s"] and all(finite(w) and w >= 0 for w in run["wall_s"])):
            problems.append(f"run {key} has no usable wall times")
    paths = [e.get("path") for e in nonreg.get("examples", [])]
    if len(paths) != len(set(paths)):
        problems.append("an example is recorded twice")
    found = sorted(e["path"] for e in world["discover"]())
    if sorted(paths) != found:
        problems.append("the examples in the record are not the tracked actinv-spec-1 problems")
    return problems


# ---------------------------------------------------------------------------------------------------- metrics
def relative(reported, reference):
    """|reported - reference| / reference as a float; infinite for a value that is not finite, or when the reference is
    zero and the report is not, or beyond Decimal's range."""
    if not finite(reported):
        return INF
    error = abs(Decimal(reported) - reference)
    if reference == 0:
        return 0.0 if error == 0 else INF
    try:
        return float(error / reference)
    except decimal.Overflow:
        return INF


def covered(reported, reference, bound):
    return finite(reported) and finite(bound) and abs(Decimal(reported) - reference) <= Decimal(bound)


def step_calc(reference, ref_step, rec):
    inv = ref_step["inv"]
    act_ref = sum((reference.lam[n] * r for n, r in inv.items()), Decimal(0))
    heat_ref = act_ref * E_TOTAL
    excluded = [n for n in rec["excluded"] if n in inv]
    excl_act = sum((reference.lam[n] * inv[n] for n in excluded), Decimal(0))
    bound = rec.get("bound")
    b_act = bound.get("activity_Bq_per_g") if bound else 0.0
    b_heat = bound.get("heat_W_per_g") if bound else 0.0
    out = {"ref": {"act": act_ref, "heat": heat_ref}, "excl": {"act": excl_act, "heat": excl_act * E_TOTAL},
           "bound": {"act": b_act, "heat": b_heat}, "M": {}, "covered": {}}
    for response, key in (("act", "activity_total"), ("heat", "heat_total")):
        out["M"][response] = relative(rec[key], out["ref"][response])
        out["covered"][response] = covered(rec[key], out["ref"][response], out["bound"][response])
    return out


def calculate(record, world):
    """{run key: [per kept step calc]} for every run that ran."""
    calc = {}
    index, _ = index_runs(record)
    for key, run in index.items():
        if not run.get("ran") or not run.get("steps"):
            continue
        reference = reference_of(world, run["set"], run["case"], run["steps_scheduled"])
        by_step = {s["step"]: s for s in reference.steps}
        calc[key] = [step_calc(reference, by_step[rec["step"]], rec) for rec in run["steps"]]
    return index, calc


def reference_for(world, run):
    return reference_of(world, run["set"], run["case"], run["steps_scheduled"])


def holds(value):
    return value <= H.TOL_TOTAL  # NaN compares false


# ----------------------------------------------------------------------------------------------------- items
def feed_graph(reference, multiplier):
    """(parents, rho) of the reference model for a step with the given flux multiplier: parents[d] = {j: feed rate j->d},
    rho[n] = lambda_n + reaction loss rate. Decay branchings and one-group reactions are the doc's."""
    doc = reference.doc
    lam = {n: float(v) for n, v in reference.lam.items()}
    flux = float(Decimal(str(doc.get("flux_n_cm2_s", "0")))) * multiplier
    parents, rho = {}, dict(lam)
    for source, modes in doc.get("decays", {}).items():
        for mode in modes:
            parents.setdefault(mode["to"], {})[source] = parents.get(mode["to"], {}).get(source, 0.0) + \
                lam[source] * float(Decimal(mode["branching"]))
    for reaction in doc.get("reactions", []):
        rho[reaction["from"]] = rho.get(reaction["from"], 0.0) + float(Decimal(reaction["loss_sigma_b"])) * 1e-24 * flux
        for product in reaction["products"]:
            rate = float(Decimal(product["sigma_b"])) * 1e-24 * flux
            if rate > 0:
                parents.setdefault(product["to"], {})[reaction["from"]] = parents.get(product["to"], {}).get(
                    reaction["from"], 0.0) + rate
    return parents, rho


def separation_violations(name, parents, rho, reconstructed, present):
    """Nearest resolved ancestors of `name`, walking up through reconstructed parents, whose removal rate is not at
    least SEPARATION_RATIO times slower than name's. `present` is the set of states with a positive population."""
    found, seen, stack = [], {name}, [name]
    while stack:
        for j, rate in parents.get(stack.pop(), {}).items():
            if j in seen or rate <= 0 or j not in present:
                continue
            seen.add(j)
            if j in reconstructed:
                stack.append(j)
            else:
                found.append(j)
    return [j for j in found if not rho[name] >= H.SEPARATION_RATIO * rho[j] * (1 - 1e-9)]


def separation_problems(reference, rec, at):
    doc = reference.doc
    schedule = doc.get("schedule")
    if not isinstance(schedule, list) or not rec.get("reconstructed"):
        return []  # pulse-train schedules are dicts; their cases reconstruct nothing that is checked here
    multiplier = float(Decimal(str(schedule[rec["step"] - 1]["flux_multiplier"])))
    parents, rho = feed_graph(reference, multiplier)
    ref_step = next(s for s in reference.steps if s["step"] == rec["step"])
    present = {n for n, v in ref_step["inv"].items() if v > 0}
    rebuilt = set(rec["reconstructed"])
    return [f"{at}: {name} was reconstructed but its removal rate is under {H.SEPARATION_RATIO:.0f}x that of resolved "
            f"ancestor(s) {bad}" for name in rec["reconstructed"]
            for bad in [separation_violations(name, parents, rho, rebuilt, present)] if bad]


def contract(run, arm, reference):
    """The candidate's output contract on one plain run of arm A, B or C."""
    lam = {n: float(v) for n, v in reference.lam.items()}
    where = f"{run['case']} cram{run['cram_order']} {arm}"
    problems = []
    policy = H.ARMS[arm]
    ledger = run.get("ledger_below_floor")
    if not isinstance(ledger, dict) or ledger.get("policy") != policy:
        problems.append(f"{where}: no ledger.below_floor block for policy {policy}")
        ledger = {}
    summary = run.get("summary") or {}
    if summary.get("n_steps_bound_present") is None or run.get("steps") is None:
        return problems + [f"{where}: no step summary"]
    if sorted(ledger.get("refined_steps", [])) != sorted(summary.get("refined_step_ids", [])):
        problems.append(f"{where}: ledger.refined_steps differs from the steps flagged refined")
    unresolved = {u.get("step") for u in ledger.get("unresolved_steps", []) if isinstance(u, dict)}
    if arm == "A" and summary.get("refined_step_ids"):
        problems.append(f"{where}: {arm} refined a step")
    if arm == "C" and ledger.get("reconstruct_threshold") != H.RECONSTRUCT_THRESHOLD:
        problems.append(f"{where}: ledger does not carry the reconstruction threshold")
    for rec in run["steps"]:
        at = f"{where} step {rec['step']}"
        bound = rec.get("bound")
        if not bound:
            problems.append(f"{at}: no below_floor_bound")
            continue
        b_act, b_heat = bound.get("activity_Bq_per_g"), bound.get("heat_W_per_g")
        if bound.get("policy") != policy:
            problems.append(f"{at}: bound policy {bound.get('policy')}")
        if not (finite(b_act) and finite(b_heat) and b_act >= 0 and b_heat >= 0):
            problems.append(f"{at}: bound is not a nonnegative finite number")
            continue
        n_inv, n_flag, n_bound = rec["n_excluded_inventory"], rec["n_below"], bound.get("n_states_excluded")
        rebuilt = rec.get("reconstructed", [])
        if arm != "C" and (rebuilt or bound.get("reconstructed_states")):
            problems.append(f"{at}: {arm} reconstructed a state")
        if not (n_inv + len(rebuilt) == n_flag and n_inv == n_bound):
            problems.append(f"{at}: state counts disagree (excluded in inventory {n_inv}, reconstructed {len(rebuilt)}, "
                            f"flag {n_flag}, bound {n_bound})")
        if arm == "C":
            if bound.get("n_reconstructed") != len(rebuilt) or rec.get("n_reconstructed_listed") != len(rebuilt):
                problems.append(f"{at}: reconstructed count {bound.get('n_reconstructed')} differs from the "
                                f"{len(rebuilt)} reconstructed states in the inventory")
            if bound.get("reconstruct_threshold") != H.RECONSTRUCT_THRESHOLD:
                problems.append(f"{at}: reconstruction threshold {bound.get('reconstruct_threshold')}")
            for name in rebuilt:
                if not lam.get(name, 0.0) * rec["dt_s"] >= H.RECONSTRUCT_THRESHOLD * (1 - 1e-9):
                    problems.append(f"{at}: {name} was reconstructed with lambda x step length "
                                    f"{lam.get(name, 0.0) * rec['dt_s']:.3g} below {H.RECONSTRUCT_THRESHOLD}")
            if bound.get("n_separation_fallback") is None:
                problems.append(f"{at}: no n_separation_fallback")
            problems += separation_problems(reference, rec, at)
            fallbacks = sum(bound.get(k) or 0 for k in ("n_cycle_fallback", "n_parent_fallback", "n_memory_fallback",
                                                         "n_separation_fallback"))
            relaxed = [n for n in rec["excluded"] if lam.get(n, 0.0) * rec["dt_s"] >= H.RECONSTRUCT_THRESHOLD]
            if relaxed and fallbacks < 1:
                problems.append(f"{at}: {relaxed[:3]} met the threshold, were not reconstructed and no fallback is counted")
        if rec["unmapped"]:
            problems.append(f"{at}: {rec['unmapped']} inventory nuclides are not in the reference")
        if rec["excluded_reported_activity_max"] != 0:
            problems.append(f"{at}: an excluded state is still in the activity table")
        if not rec["table_dev"] <= TABLE_TOLERANCE:
            problems.append(f"{at}: activity table departs from lambda N of the kept states by {rec['table_dev']:.2e}")
        if n_flag == 0 and (b_act != 0 or b_heat != 0):
            problems.append(f"{at}: a bound with no excluded state")
        order = bound.get("order")
        if order not in H.ALPHA0:
            problems.append(f"{at}: bound order {order}")
        elif not rec["floor"] >= H.ALPHA0[order] * rec["inventory_max"] * (1 - FLOOR_TOLERANCE):
            problems.append(f"{at}: floor is below alpha0({order}) x max(N)")
        if not finite(bound.get("numerical_floor_atoms_per_g")) or not math.isclose(
                bound["numerical_floor_atoms_per_g"], rec["floor"], rel_tol=1e-12):
            problems.append(f"{at}: bound floor is not the step's numerical_floor_atoms_per_g")
        refined = bool(bound.get("refined"))
        if refined and not (arm in ("B", "C") and run["cram_order"] == 16 and order == 48):
            problems.append(f"{at}: refined step is not a CRAM-16 step re-solved at CRAM-48 by B or C")
        if not refined and order != run["cram_order"]:
            problems.append(f"{at}: unrefined step solved at CRAM-{order}")
        material = b_act > H.REL_TOL * rec["activity_total"]
        if arm in ("B", "C"):
            if run["cram_order"] == 16 and not refined and material:
                problems.append(f"{at}: {arm} left a material bound ({b_act:.3e} Bq/g) unrefined")
            if material and rec["step_id"] not in unresolved:
                problems.append(f"{at}: {arm}'s unresolved bound is not named in the ledger")
    return problems


def validity(run, calc, arm):
    """Bound validity: the reported bounds are at least the excluded states' reference contributions."""
    where = f"{run['case']} cram{run['cram_order']} {arm}"
    problems = []
    for rec, c in zip(run["steps"], calc):
        if rec.get("bound") is None:
            problems.append(f"{where} step {rec['step']}: no bound")
            continue
        for response in RESPONSES:
            if not finite(c["bound"][response]) or Decimal(c["bound"][response]) * (1 + BOUND_SLACK) < c["excl"][response]:
                problems.append(f"{where} step {rec['step']}: {response} bound {c['bound'][response]!r} is below the "
                                f"excluded states' reference {float(c['excl'][response]):.3e}")
    return problems


def certificate(run, calc, arm):
    """The screen certificate contains the reference total at every kept step."""
    where = f"{run['case']} cram{run['cram_order']} {arm} screen"
    problems = []
    for rec, c in zip(run["steps"], calc):
        cert = rec.get("cert") or {}
        for response, name in (("act", "activity.total"), ("heat", "heat.total")):
            entry = cert.get(name)
            reference = c["ref"][response]
            if not entry or not (finite(entry.get("lower")) and finite(entry.get("upper"))):
                problems.append(f"{where} step {rec['step']}: no finite {name} certificate")
                continue
            slack = abs(reference) * CERT_TOLERANCE
            if Decimal(entry["lower"]) > reference + slack or Decimal(entry["upper"]) < reference - slack:
                problems.append(f"{where} step {rec['step']}: {name} [{entry['lower']:.6e}, {entry['upper']:.6e}] "
                                f"excludes the reference {float(reference):.6e}")
    return problems


def lost(run_sq, calc_sq, run_arm, calc_arm, arm):
    """Totals that held within TOL under the status quo and do not under the candidate."""
    where = f"{run_arm['case']} cram{run_arm['cram_order']} {arm}"
    out = []
    for rec, a, b in zip(run_arm["steps"], calc_sq, calc_arm):
        for response in RESPONSES:
            if holds(a["M"][response]) and not holds(b["M"][response]):
                out.append(f"{where} step {rec['step']}: {response} total went from {a['M'][response]:.2e} to "
                           f"{b['M'][response]:.2e} of the reference")
    return out


def fixed(run, calc, arm):
    where = f"{run['case']} cram{run['cram_order']} {arm}"
    return [f"{where} step {rec['step']}: {response} total off by {c['M'][response]:.2e} and not covered by the bound"
            for rec, c in zip(run["steps"], calc) for response in RESPONSES
            if not (holds(c["M"][response]) or c["covered"][response])]


def cost_problem(label, sq_walls, b_walls, triggered):
    sq, b = median(sq_walls), median(b_walls)
    if triggered:
        if not (b <= H.COST_RATIO * sq or b - sq <= H.NOISE_S):
            return f"{label}: B took {b:.3f} s against {sq:.3f} s where it re-solved (limit {H.COST_RATIO}x)"
    elif not (b - sq <= max(H.NOISE_S, H.NOISE_FRACTION * sq)):
        return f"{label}: B took {b:.3f} s against {sq:.3f} s where it did not re-solve"
    return None


# ------------------------------------------------------------------------------------------------------ gates
def run_pair(index, key_tail, case, order, arm):
    return index.get((case["set"], case["name"], order, arm, key_tail))


def g2(index, calc, world, arm):
    problems = []
    sq_broke_fix_case = False
    for case in (c for c in world["cases"] if c["set"] == "selection"):
        for order in H.ORDERS:
            run, sq = run_pair(index, False, case, order, arm), run_pair(index, False, case, order, "status_quo")
            key = ("selection", case["name"], order, arm, False)
            if run is None or key not in calc:
                problems.append(f"{case['name']} cram{order} {arm}: did not run ({run and run.get('reason')})")
                continue
            problems += contract(run, arm, reference_for(world, run)) + validity(run, calc[key], arm)
            sq_key = ("selection", case["name"], order, "status_quo", False)
            if sq_key not in calc:
                problems.append(f"{case['name']} cram{order} status_quo: did not run")
                continue
            problems += lost(sq, calc[sq_key], run, calc[key], arm)
            if case["name"] == FIX_CASE and order == 16:
                sq_broke_fix_case = any(not holds(c["M"]["act"]) for c in calc[sq_key]) and any(
                    r["n_below"] > 0 for r in sq["steps"])
                problems += fixed(run, calc[key], arm)
            screen = index.get(("selection", case["name"], order, arm, True))
            if case["name"] == FIX_CASE and screen is not None:
                skey = ("selection", case["name"], order, arm, True)
                problems += certificate(screen, calc[skey], arm) if skey in calc else [f"{case['name']} screen did not run"]
    if not sq_broke_fix_case:
        problems.append(f"{FIX_CASE} cram16: the status quo does not show a below-floor total error "
                        "(the defect is not reproduced)")
    return problems


def g3(index, calc, world, record, arm):
    items = {"contract": [], "bound_valid": [], "no_regression": [], "actinide_guard": [],
             "m_tot_vs_A": [], "cost_triggered": [], "cost_idle": []}
    for case in (c for c in world["cases"] if c["set"] == "heldout"):
        for order in H.ORDERS:
            run, sq = run_pair(index, False, case, order, arm), run_pair(index, False, case, order, "status_quo")
            key = ("heldout", case["name"], order, arm, False)
            sq_key = ("heldout", case["name"], order, "status_quo", False)
            if run is None or key not in calc or sq_key not in calc:
                items["contract"].append(f"{case['name']} cram{order} {arm}: did not run")
                continue
            items["contract"] += contract(run, arm, reference_for(world, run))
            items["bound_valid"] += validity(run, calc[key], arm)
            items["no_regression"] += lost(sq, calc[sq_key], run, calc[key], arm)
            screen_key = ("heldout", case["name"], order, arm, True)
            screen = index.get(screen_key)
            if screen is not None:
                items["bound_valid"] += certificate(screen, calc[screen_key], arm) if screen_key in calc \
                    else [f"{case['name']} cram{order} {arm} screen: did not run"]
            if case["name"] in world["actinide"]:
                for rec, c in zip(run["steps"], calc[key]):
                    for response in RESPONSES:
                        if not holds(c["M"][response]):
                            items["actinide_guard"].append(
                                f"{case['name']} cram{order} step {rec['step']}: {arm} {response} total is "
                                f"{c['M'][response]:.2e} of the reference (limit {H.TOL_TOTAL:.0e})")
            if arm == "B":
                a_key = ("heldout", case["name"], order, "A", False)
                if a_key not in calc:
                    items["m_tot_vs_A"].append(f"{case['name']} cram{order}: A did not run")
                else:
                    for rec, cb, ca in zip(run["steps"], calc[key], calc[a_key]):
                        for response in RESPONSES:
                            if not cb["M"][response] <= ca["M"][response] * (1 + B_VS_A_SLACK) + 1e-15:
                                items["m_tot_vs_A"].append(
                                    f"{case['name']} cram{order} step {rec['step']}: B {response} error "
                                    f"{cb['M'][response]:.2e} exceeds A's {ca['M'][response]:.2e}")
            if arm in ("B", "C"):
                triggered = bool((run.get("summary") or {}).get("refined_step_ids"))
                problem = cost_problem(f"{case['name']} cram{order} {arm}", sq["wall_s"], run["wall_s"], triggered)
                if problem:
                    items["cost_triggered" if triggered else "cost_idle"].append(problem)
    if arm in ("B", "C"):
        for example in record["nonregression"]["examples"]:
            if not example.get("runnable"):
                continue
            sq, cand = example["arms"].get("status_quo"), example["arms"].get(arm)
            if not sq or not cand or not (sq["wall_s"] and cand["wall_s"]):
                continue
            triggered = bool(cand.get("n_refined"))
            problem = cost_problem(f"{example['path']} {arm}", sq["wall_s"], cand["wall_s"], triggered)
            if problem:
                items["cost_triggered" if triggered else "cost_idle"].append(problem)
    return items


def g4(record, arm):
    problems, skipped = [], []
    examples = record["nonregression"]["examples"]
    if not any(e.get("runnable") for e in examples):
        problems.append("no example ran")
    for example in examples:
        path = example["path"]
        if not example.get("runnable"):
            if example.get("group") == PUBLIC:
                problems.append(f"{path}: a public example did not run on the master binary")
            else:
                skipped.append(path)
            continue
        flagged = example.get("flagged_steps", [])
        sq_arm = example["arms"].get("status_quo", {})
        if len(set(sq_arm.get("steps_sha256", []))) > 1:
            problems.append(f"{path}: the status quo is not deterministic")
        if example["diff_steps"].get("status_quo") != []:
            problems.append(f"{path}: the status quo differs from the master binary at steps "
                            f"{example['diff_steps'].get('status_quo')}")
        entry = example["arms"].get(arm, {})
        if entry.get("exits") != [0] * len(entry.get("exits", [None])) or not entry.get("exits"):
            problems.append(f"{path}: {arm} did not run cleanly ({entry.get('exits')})")
            continue
        diffs = example["diff_steps"].get(arm)
        if diffs is None:
            problems.append(f"{path}: no comparison for {arm}")
        elif -1 in diffs:
            problems.append(f"{path}: {arm} changed the number of steps")
        elif arm == "A":
            stray = [d for d in diffs if d not in set(flagged)]
            if stray:
                problems.append(f"{path}: A changed steps {stray[:8]} which the master did not flag")
        else:  # B and C change the state they carry forward
            first = min(flagged) if flagged else None
            stray = [d for d in diffs if first is None or d < first]
            if stray:
                problems.append(f"{path}: B changed steps {stray[:8]} before any step the master flagged")
    return problems, skipped


# ------------------------------------------------------------------------------------------------- evaluation
def evaluate(record, *, world=None, pins=PINS):
    world = world or default_world()
    problems = {"G0": g0(world), "G1": g1(record, world, pins)}
    result = {"gates": {}, "decision": None, "arms": {}}
    if problems["G1"] and any(p.startswith(("the record lacks", "wrong schema")) for p in problems["G1"]):
        return {"G0": problems["G0"], "G1": problems["G1"]}, {"decision": None, "arms": {}}, []
    index, calc = calculate(record, world)
    arms = {}
    for arm in H.CANDIDATES:
        three = g3(index, calc, world, record, arm)
        four, skipped = g4(record, arm)
        arms[arm] = {"G2": g2(index, calc, world, arm), "G3": three, "G4": four, "skipped": skipped}
    passes = {arm: {"G2": not arms[arm]["G2"], "G3": not any(arms[arm]["G3"].values()), "G4": not arms[arm]["G4"]}
              for arm in arms}
    decision = next((arm for arm in DECISION_ORDER if all(passes[arm].values())), "status_quo")
    return {"G0": problems["G0"], "G1": problems["G1"]}, {"decision": decision, "arms": arms, "passes": passes}, \
        sentences(record, world, index, calc, decision, arms)


def fmt(value, digits=3):
    return f"{value:.{digits}g}"


def sentences(record, world, index, calc, decision, arms):
    out = []
    if decision in ("A", "B"):
        out.append("ACTINV no longer reports activity from states below its solver's numerical resolution; "
                   "it reports their bound.")
    if decision == "C":
        out.append("ACTINV reconstructs short-lived nuclides below its solver's numerical resolution from their "
                   "production rate, and reports a bound for the rest.")
    selection = [(c["name"], o) for c in world["cases"] if c["set"] == "selection" for o in H.ORDERS]
    broken = [(n, o) for n, o in selection if ("selection", n, o, "status_quo", False) in calc and any(
        not holds(c["M"][r]) for c in calc[("selection", n, o, "status_quo", False)] for r in RESPONSES)]
    out.append(f"On P168's {len(selection)} case-orders, {len(broken)} had an activity or heat total outside "
               f"{fmt(H.TOL_TOTAL)} of the reference at some step under the status quo.")
    for arm in H.CANDIDATES:
        key = lambda n, o: ("selection", n, o, arm, False)  # noqa: E731
        ok = [(n, o) for n, o in broken if key(n, o) in calc and not fixed(index[key(n, o)], calc[key(n, o)], arm)]
        out.append(f"Candidate {arm} brought {len(ok)} of those {len(broken)} within {fmt(H.TOL_TOTAL)} of the "
                   f"reference or inside its reported bound.")
    heldout = [(c["name"], o) for c in world["cases"] if c["set"] == "heldout" for o in H.ORDERS]
    steps = sum(len(index[("heldout", n, o, "status_quo", False)]["steps"] or []) for n, o in heldout
                if ("heldout", n, o, "status_quo", False) in calc)
    flagged = sum(1 for n, o in heldout if ("heldout", n, o, "status_quo", False) in calc
                  for r in index[("heldout", n, o, "status_quo", False)]["steps"] if r["n_below"] > 0)
    violations = {arm: len(arms[arm]["G3"]["bound_valid"]) for arm in arms}
    refined = sum(len((index[("heldout", n, o, "B", False)].get("summary") or {}).get("refined_step_ids", []))
                  for n, o in heldout if ("heldout", n, o, "B", False) in calc)
    rebuilt = sum(len(r.get("reconstructed", [])) for n, o in heldout if ("heldout", n, o, "C", False) in calc
                  for r in index[("heldout", n, o, "C", False)]["steps"])
    ratios = [median(index[("heldout", n, o, "B", False)]["wall_s"]) / max(median(
        index[("heldout", n, o, "status_quo", False)]["wall_s"]), 1e-9)
        for n, o in heldout if ("heldout", n, o, "B", False) in calc
        and (index[("heldout", n, o, "B", False)].get("summary") or {}).get("refined_step_ids")]
    out.append(f"On the {len(heldout)} held-out case-orders ({steps} scored steps, {flagged} with a below-floor state "
               f"under the status quo) the bound problems were {violations['A']} for A, {violations['B']} for B and "
               f"{violations['C']} for C; B re-solved {refined} steps at CRAM-48 and C reconstructed {rebuilt} states"
               + (f", at a largest wall-time ratio of {fmt(max(ratios))} against the status quo." if ratios else "."))
    if decision == "status_quo":
        failed = sorted(f"{arm} {gate}" for arm in arms for gate in ("G2", "G3", "G4")
                        if (arms[arm][gate] if gate != "G3" else any(arms[arm]["G3"].values())))
        out.append("No candidate met the decision rule (" + ", ".join(failed) + "); below-floor states stay in the "
                   "totals, flagged by n_states_below_floor, and the finding is documented in data-limits.")
    return out


def render(gates, result):
    return {"protocol": "ACTINV-P174", "gates": {name: {"pass": not p, "problems": p[:200]} for name, p in gates.items()},
            "decision": result["decision"],
            "decision_order": list(DECISION_ORDER),
            "candidates": {arm: {"G2": {"pass": not data["G2"], "problems": data["G2"][:200]},
                                 "G3": {k: {"pass": not v, "problems": v[:200]} for k, v in data["G3"].items()},
                                 "G4": {"pass": not data["G4"], "problems": data["G4"][:200],
                                        "fixtures_not_runnable": data["skipped"]}}
                           for arm, data in result.get("arms", {}).items()}}


# ------------------------------------------------------------------------------------------------- self-test
def _fake_world(legit_flagged):
    """Three-nuclide references: P (1e6 s, lambda_P/lambda_D = 1e-7), D (0.1 s), S stable. Selection: the fix case and an ordinary case whose
    D state sits below the CRAM-16 scale but is accurate (legit_flagged) or does not exist. Held-out: two cases."""
    def doc(name, steps):
        return {"case": name, "decays": {"P": [{"to": "D", "branching": "1"}]}, "reactions": [],
                "flux_n_cm2_s": "0", "schedule": [{"dt_s": "10", "flux_multiplier": "0"}] * 2,
                "nuclides": [{"name": "P", "za": 27058, "liso": 0, "half_life_s": "1000000"},
                                           {"name": "D", "za": 28058, "liso": 0, "half_life_s": "0.1"},
                                           {"name": "S", "za": 29058, "liso": 0, "half_life_s": None}],
                "reference_steps": [{"step": i + 1, "label": "", "t_end_s": str(10 * (i + 1)),
                                     "inventory_atoms_per_g": {"P": p, "D": d, "S": "1e22"}}
                                    for i, (p, d) in enumerate(steps)]}
    ordinary = [("1e10", "1e3" if legit_flagged else "1e9"), ("1e9", "1e2" if legit_flagged else "1e8")]
    docs = {("selection", FIX_CASE): doc(FIX_CASE, [("1e15", "1e15"), ("1.0e-3", "2.0e-3")]),
            ("selection", "ordinary"): doc("ordinary", ordinary),
            ("heldout", "hA"): doc("hA", [("1e12", "1e12"), ("1.0e-3", "2.0e-3")]),
            ("heldout", "hB"): doc("hB", ordinary)}
    cases = [{"name": FIX_CASE, "set": "selection"}, {"name": "ordinary", "set": "selection"},
             {"name": "hA", "set": "heldout"}, {"name": "hB", "set": "heldout"}]
    return {"cases": cases, "screen": {FIX_CASE, "hA", "hB"}, "check_files": False, "ref_docs": docs,
            "refs_dir": {}, "manifest": {}, "pin_key": {}, "discover": lambda: [
                {"path": "examples/a.json"}, {"path": "examples/b.json"}, {"path": "controls/x/f.json"}],
            "hash_log": None, "actinide": {"hB"}}


def _synthetic_step(world, case, order, arm, step_index, noisy):
    """What a correct candidate prints for one step (and what the status quo prints, with CRAM-16 noise on the fix case)."""
    reference = reference_of(world, case["set"], case["name"], 2)
    inv = {n: float(v) for n, v in reference.steps[step_index]["inv"].items()}
    start = {n: float(v) for n, v in reference.steps[step_index - 1]["inv"].items()} if step_index else \
        {n: 0.0 for n in inv}
    lam = {"P": math.log(2) / 1e6, "D": math.log(2) / 0.1, "S": 0.0}
    dt = 10.0

    def solve(solve_order):
        values = dict(inv)
        if noisy and solve_order == 16 and case["name"] in (FIX_CASE, "hA") and step_index == 1:
            values.update(P=2.0e5, D=3.0e5)  # CRAM-16 noise below the scale 2.1e6
        return values, H.ALPHA0[solve_order] * max(values.values())

    values, floor = solve(order)
    refined = False
    policy = H.ARMS[arm]

    def treat(values, floor):
        below = [n for n, v in values.items() if 0 < v < floor]
        rebuilt, falls = [], {"cycle": 0, "parent": 0, "memory": 0, "separation": 0}
        if policy == "equilibrium":
            bounded = []
            for name in ("P", "D"):  # parents first
                if name not in below or lam[name] * dt < H.RECONSTRUCT_THRESHOLD:
                    if name in below:
                        bounded.append(name)
                    continue
                parents = [] if name == "P" else ["P"]
                if any(p in bounded for p in parents):
                    falls["parent"] += 1
                    bounded.append(name)
                    continue
                if any(lam[name] < H.SEPARATION_RATIO * lam[p] for p in parents if p not in rebuilt):
                    falls["separation"] += 1
                    bounded.append(name)
                    continue
                production = sum(lam[p] * values[p] for p in parents)
                value = production / lam[name]
                if start[name] * math.exp(-lam[name] * dt) > H.MEMORY_FRACTION * value:
                    falls["memory"] += 1
                    bounded.append(name)
                    continue
                values[name] = value
                rebuilt.append(name)
            excluded = bounded
        else:
            excluded = below
        kept = {n: v for n, v in values.items() if n not in excluded and lam[n] > 0}
        table_all = {n: lam[n] * v for n, v in values.items() if lam[n] > 0}
        table = table_all if policy is None else {n: lam[n] * v for n, v in kept.items()}
        bound = sum(floor * lam[n] for n in excluded)
        return excluded, rebuilt, falls, table, bound

    excluded, rebuilt, falls, table, bound = treat(values, floor)
    if policy in ("refine", "equilibrium") and order == 16 and bound > H.REL_TOL * sum(table.values()):
        values, floor = solve(48)
        refined = True
        excluded, rebuilt, falls, table, bound = treat(values, floor)
    n_excluded = len(excluded)
    block = None
    if policy is not None:
        block = {"policy": policy, "order": 48 if refined else order, "refined": refined,
                 "n_states_excluded": n_excluded, "numerical_floor_atoms_per_g": floor, "activity_Bq_per_g": bound,
                 "heat_W_per_g": bound * float(E_TOTAL), "dose": None}
        if policy == "equilibrium":
            block.update(n_reconstructed=len(rebuilt), reconstructed_states=list(rebuilt),
                         reconstruct_threshold=H.RECONSTRUCT_THRESHOLD, n_cycle_fallback=falls["cycle"],
                         n_parent_fallback=falls["parent"], n_memory_fallback=falls["memory"],
                         n_separation_fallback=falls["separation"])
    total = math.fsum(table.values())
    rec = {"step": step_index + 1, "step_id": step_index + 1, "t_s": dt * (step_index + 1), "dt_s": dt,
           "floor": floor, "n_below": n_excluded + len(rebuilt), "n_excluded_inventory": n_excluded,
           "excluded": excluded, "reconstructed": rebuilt, "n_reconstructed_listed": len(rebuilt), "unmapped": 0,
           "inventory_max": max(values.values()), "activity_total": total, "heat_total": total * float(E_TOTAL),
           "table_dev": 0.0, "excluded_reported_activity_max": 0.0 if policy is not None else
           max([lam[n] * values[n] for n in excluded], default=0.0), "bound": block, "cert": None}
    return rec, refined, bound


def _synthetic_record(world):
    runs = []
    for case in world["cases"]:
        reference = reference_of(world, case["set"], case["name"], 2)
        for order in H.ORDERS:
            for arm in H.ARMS:
                for screen in ((False, True) if case["name"] in world["screen"] else (False,)):
                    recs, refined_ids, bounds = [], [], []
                    for i in range(2):
                        rec, refined, bound = _synthetic_step(world, case, order, arm, i, True)
                        if screen:
                            ref = sum((reference.lam[n] * r for n, r in reference.steps[i]["inv"].items()), Decimal(0))
                            low = float(ref) - (rec["bound"] or {}).get("activity_Bq_per_g", 0.0) if arm != "status_quo" \
                                else rec["activity_total"]
                            high = max(rec["activity_total"], float(ref)) + (rec["bound"] or {}).get(
                                "activity_Bq_per_g", 0.0)
                            heat_low = low * float(E_TOTAL)
                            rec["cert"] = {"activity.total": {"edge": "nominal", "lower": min(low, float(ref)),
                                                              "upper": high, "bound_lower": 0.0, "bound_upper": 0.0},
                                           "heat.total": {"edge": "nominal", "lower": min(heat_low, float(ref) * float(E_TOTAL)),
                                                          "upper": high * float(E_TOTAL), "bound_lower": 0.0,
                                                          "bound_upper": 0.0}}
                        recs.append(rec)
                        if refined:
                            refined_ids.append(rec["step_id"])
                    ledger = None
                    if H.ARMS[arm] is not None:
                        unresolved = [{"step": r["step_id"]} for r in recs
                                      if r["bound"] and r["bound"]["activity_Bq_per_g"] > H.REL_TOL * r["activity_total"]]
                        ledger = {"policy": H.ARMS[arm], "rel_tol": H.REL_TOL, "refined_steps": refined_ids,
                                  "unresolved_steps": unresolved}
                        if arm == "C":
                            ledger["reconstruct_threshold"] = H.RECONSTRUCT_THRESHOLD
                    triggered = bool(refined_ids)
                    rebuilt_steps = sum(1 for r in recs if r["reconstructed"])
                    wall = 0.010 if arm in ("status_quo", "A") else (0.016 if triggered else 0.0105)
                    runs.append({"set": case["set"], "case": case["name"], "cram_order": order, "arm": arm,
                                 "screen": screen, "mode": "coupled", "steps_scheduled": 2, "binary_sha256": "c" * 64,
                                 "exit_status": 0, "timed_out": False, "wall_s": [wall] * 5, "ran": True,
                                 "reason": None, "steps": recs, "ledger_below_floor": ledger, "p168": None,
                                 "summary": {"n_steps_flagged": sum(1 for r in recs if r["n_below"]),
                                             "max_n_below": max(r["n_below"] for r in recs),
                                             "n_steps_bound_present": sum(1 for r in recs if r["bound"]),
                                             "n_steps_reconstructed": rebuilt_steps,
                                             "refined_step_ids": refined_ids}})
    examples = []
    for path, group in (("examples/a.json", PUBLIC), ("examples/b.json", PUBLIC), ("controls/x/f.json", FIXTURE)):
        arms = {arm: {"exits": [0, 0, 0], "wall_s": [1.0, 1.0, 1.0], "steps_sha256": ["h"] * 3, "n_steps": 3,
                      "n_refined": 0, "n_reconstructed_steps": 0} for arm in H.ARMS}
        examples.append({"path": path, "group": group, "spec_sha256": "d" * 64, "runnable": True,
                         "base": {"exits": [0], "wall_s": [1.0]}, "arms": arms, "flagged_steps": [2],
                         "diff_steps": {"status_quo": [], "A": [2], "B": [2], "C": [2]}})
    constants = json.loads(json.dumps(H.constants()))
    return {"schema": H.SCHEMA, "protocol": "ACTINV-P174",
            "environment": {"binary_sha256": "c" * 64, "base_binary_sha256": PINS["base_binary_sha256"],
                            "clean_tree": True, "git_head": None,
                            "cgroup": {"memory.max": str(6 * GIB), "memory.swap.max": "0", "pids.max": "128",
                                       "cpu.max": "200000 100000"},
                            "candidate_sources": {"crates/actinv-core/src/run.rs": "e" * 64}},
            "solver": {"constants": constants, "runs": runs, "partial": False},
            "nonregression": {"constants": constants, "binary_sha256": "c" * 64,
                              "base_binary_sha256": PINS["base_binary_sha256"], "examples": examples}}


def _find(record, case, order, arm, screen=False):
    return next(r for r in record["solver"]["runs"] if (r["case"], r["cram_order"], r["arm"], bool(r["screen"])) ==
                (case, order, arm, screen))


def selftest():
    failures = []
    pins = dict(PINS, p174_refs_manifest_sha256="a" * 64)
    log_ok = "".join(f"{sha256(p)}  {p.relative_to(ROOT)}\n" for p in (
        PROTOCOL, ROOT / "controls" / "p174_floor.py", ROOT / "controls" / "check_p174.py") if p.is_file())

    def check(label, condition):
        if not condition:
            failures.append(label)

    def judge(record, world):
        gates, result, text = evaluate(record, world=world, pins=pins)
        return gates, result, text

    def failed(gates, result):
        found = {name for name, p in gates.items() if p}
        for arm, data in result.get("arms", {}).items():
            found |= {f"{arm}.G2"} if data["G2"] else set()
            found |= {f"{arm}.G3.{k}" for k, v in data["G3"].items() if v}
            found |= {f"{arm}.G4"} if data["G4"] else set()
        return found

    world = _fake_world(legit_flagged=True)
    world["hash_log"] = "(not under test)"
    world_no_g0 = dict(world)
    base = _synthetic_record(world)
    # the unmutated synthetic world: only G0 (the fake log) fails; A loses legit totals, B passes everything
    gates, result, text = judge(base, world)
    found = failed(gates, result)
    check("baseline G1 clean", not gates["G1"])
    check("baseline decision is C when B and C both pass", result["decision"] == "C" and all(
        result["passes"]["B"].values()) and all(result["passes"]["C"].values()))
    check("baseline A fails its no-regression items", "A.G2" in found and "A.G3.no_regression" in found)
    check("A loses activity on the actinide case", "A.G3.actinide_guard" in found)
    check("baseline B and C clean", not any(f.startswith(("B.", "C.")) for f in found))
    check("sentences", text and "reconstructs short-lived nuclides" in text[0])
    # G0
    world["hash_log"] = log_ok
    check("G0 with the real hashes", not g0(world) or all("not in protocol_hash" in p or "missing" in p for p in g0(world)))
    world["hash_log"] = ""
    check("G0 rejects an empty log", len(g0(world)) >= 2)
    world["hash_log"] = "(not under test)"

    def mutate(label, change, expect, record=base, world=world, expect_decision=None):
        mutated = copy.deepcopy(record)
        change(mutated)
        gates, result, _ = judge(mutated, world)
        found = failed(gates, result)
        if not any(e in found for e in expect):
            failures.append(f"{label}: expected one of {expect}, got {sorted(found)}")
        if expect_decision is not None and result.get("decision") != expect_decision:
            failures.append(f"{label}: decision {result.get('decision')} not {expect_decision}")

    # G1
    mutate("constants", lambda r: r["solver"]["constants"].update(rel_tol=1e-3), ["G1"])
    mutate("partial", lambda r: r["solver"].update(partial=True), ["G1"])
    mutate("binary equals master", lambda r: r["environment"].update(binary_sha256=PINS["base_binary_sha256"]), ["G1"])
    mutate("master binary", lambda r: r["environment"].update(base_binary_sha256="0" * 64), ["G1"])
    mutate("dirty", lambda r: r["environment"].update(clean_tree=False), ["G1"])
    mutate("cgroup", lambda r: r["environment"]["cgroup"].update({"memory.max": str(9 * GIB)}), ["G1"])
    mutate("no candidate sources", lambda r: r["environment"].update(candidate_sources={}), ["G1"])
    mutate("missing run", lambda r: r["solver"]["runs"].pop(), ["G1"])
    mutate("duplicate run", lambda r: r["solver"]["runs"].append(copy.deepcopy(r["solver"]["runs"][0])), ["G1"])
    mutate("run used another binary", lambda r: r["solver"]["runs"][3].update(binary_sha256="f" * 64), ["G1"])
    mutate("example dropped", lambda r: r["nonregression"]["examples"].pop(), ["G1"])
    real = default_world()
    world_pin = dict(world, check_files=True, pin_key=real["pin_key"], manifest=real["manifest"],
                     refs_dir=real["refs_dir"])
    # After registration the held-out pin is set and the checked-out manifest must reproduce it.
    pin_problems = g1(base, world_pin, PINS)
    check("held-out manifest pinned and reproduced",
          not any("heldout references manifest is not pinned" in p or "checked-out heldout" in p for p in pin_problems))
    check("wrong pin rejected", any("checked-out heldout" in p for p in
                                    g1(base, world_pin, dict(PINS, p174_refs_manifest_sha256="0" * 64))))

    # G2 and G3, mutations of B (the adopted candidate) unless stated
    mutate("A bound zero at 5d",
           lambda r: _find(r, FIX_CASE, 16, "A")["steps"][1]["bound"].update(activity_Bq_per_g=0.0), ["A.G2"])
    base_a = copy.deepcopy(base)

    def a_understated(r):
        step = _find(r, FIX_CASE, 16, "A")["steps"][1]
        step["bound"]["activity_Bq_per_g"] = 1e-30
        step["bound"]["heat_W_per_g"] = 1e-30
    mutate("A bound below the excluded reference (and no longer covering)", a_understated, ["A.G2"])
    mutate("excluded state left in the table",
           lambda r: _find(r, "ordinary", 16, "B")["steps"][0].update(excluded_reported_activity_max=1.0), ["B.G2"])
    mutate("activity table departs from lambda N",
           lambda r: _find(r, "ordinary", 48, "B")["steps"][0].update(table_dev=1e-3), ["B.G2"])
    mutate("excluded counts disagree",
           lambda r: _find(r, "ordinary", 48, "B")["steps"][0].update(n_excluded_inventory=3), ["B.G2"])
    mutate("unmapped nuclide", lambda r: _find(r, "ordinary", 48, "B")["steps"][0].update(unmapped=1), ["B.G2"])
    mutate("floor below alpha0 max N",
           lambda r: _find(r, "ordinary", 48, "B")["steps"][0].update(floor=1e-60), ["B.G2"])
    mutate("policy missing", lambda r: _find(r, "ordinary", 16, "B")["steps"][0].update(bound=None), ["B.G2"])
    mutate("ledger refined steps wrong",
           lambda r: _find(r, "ordinary", 16, "B")["ledger_below_floor"].update(refined_steps=[]), ["B.G2"])
    mutate("run did not run", lambda r: _find(r, FIX_CASE, 16, "B").update(ran=False, steps=None, reason="exit status 1"),
           ["B.G2"])

    def b_unrefined(r):  # B leaves the 5d bound material and unrefined: contract and fix both fail
        run = _find(r, FIX_CASE, 16, "A")
        b = _find(r, FIX_CASE, 16, "B")
        b["steps"], b["summary"], b["ledger_below_floor"] = (copy.deepcopy(run["steps"]), copy.deepcopy(run["summary"]),
                                                             copy.deepcopy(run["ledger_below_floor"]))
        for step in b["steps"]:
            if step["bound"]:
                step["bound"]["policy"] = "refine"
        b["ledger_below_floor"]["policy"] = "refine"
    mutate("B does not refine a material bound", b_unrefined, ["B.G2"])

    def sq_exact_5d(r):
        step = _find(r, FIX_CASE, 16, "status_quo")["steps"][1]
        step["activity_total"] = 2 * float(math.log(2) / 1000 * 1.0e-3)
        step["heat_total"] = step["activity_total"] * float(E_TOTAL)
        step["n_below"] = step["n_excluded_inventory"] = 0
        step["excluded"] = []
    mutate("status quo does not break 5d", sq_exact_5d, ["A.G2", "B.G2"])

    def b_lost_total(r):
        step = _find(r, "ordinary", 48, "B")["steps"][0]
        step["activity_total"] *= 1.001
    mutate("B loses a total the status quo holds", b_lost_total, ["B.G2"])

    def cert_wrong(r):
        step = _find(r, FIX_CASE, 16, "B", True)["steps"][1]
        step["cert"]["activity.total"]["lower"] = 1.0
    mutate("screen certificate excludes the reference", cert_wrong, ["B.G2"])
    mutate("no certificate", lambda r: _find(r, FIX_CASE, 16, "B", True)["steps"][1].update(cert=None), ["B.G2"])

    def heldout_bound(r):
        step = _find(r, "hA", 16, "A")["steps"][1]
        step["bound"]["activity_Bq_per_g"] = 1e-30
    mutate("held-out bound violated", heldout_bound, ["A.G3.bound_valid"])
    mutate("held-out B bound violated",
           lambda r: _find(r, "hA", 16, "B")["steps"][0].update(excluded=["P"]) or
           _find(r, "hA", 16, "B")["steps"][0]["bound"].update(activity_Bq_per_g=0.0), ["B.G3.bound_valid", "B.G2",
                                                                                         "B.G3.contract"])

    def b_worse_than_a(r):
        _find(r, "hB", 16, "B")["steps"][0]["activity_total"] *= 3.0
    mutate("B worse than A", b_worse_than_a, ["B.G3.m_tot_vs_A"])

    def heldout_lost(r):
        _find(r, "hB", 48, "B")["steps"][1]["heat_total"] *= 1.01
    mutate("held-out total lost", heldout_lost, ["B.G3.no_regression"])

    def cost_up(r):
        for run in r["solver"]["runs"]:
            if run["arm"] == "B" and run["set"] == "heldout" and run["summary"]["refined_step_ids"]:
                run["wall_s"] = [0.5] * 5
    mutate("B too slow where it re-solves", cost_up, ["B.G3.cost_triggered"], expect_decision="C")

    def cost_idle(r):
        for run in r["solver"]["runs"]:
            if run["arm"] == "B" and run["set"] == "heldout" and not run["summary"]["refined_step_ids"]:
                run["wall_s"] = [0.2] * 5
    mutate("B slower where it does not re-solve", cost_idle, ["B.G3.cost_idle"])

    def example_cost(r):
        r["nonregression"]["examples"][0]["arms"]["B"]["wall_s"] = [1.5] * 3
    mutate("B slower on an idle example", example_cost, ["B.G3.cost_idle"])
    # the same cost excess inside the noise floor is not a finding
    quiet = copy.deepcopy(base)
    for run in quiet["solver"]["runs"]:
        if run["arm"] == "B" and run["set"] == "heldout":
            run["wall_s"] = [0.010 + 0.015] * 5
    check("start-up noise is not a cost", not any(failed(*judge(quiet, world)[:2]) & {"B.G3.cost_idle"}))

    # G4
    mutate("A changes an unflagged step",
           lambda r: r["nonregression"]["examples"][0]["diff_steps"].update(A=[0]), ["A.G4"])
    mutate("B changes a step before the first flagged one",
           lambda r: r["nonregression"]["examples"][0]["diff_steps"].update(B=[0]), ["B.G4"])
    mutate("step count changes", lambda r: r["nonregression"]["examples"][1]["diff_steps"].update(B=[-1]), ["B.G4"])
    mutate("status quo differs from master",
           lambda r: r["nonregression"]["examples"][0]["diff_steps"].update(status_quo=[1]), ["A.G4", "B.G4"])
    mutate("status quo nondeterministic",
           lambda r: r["nonregression"]["examples"][0]["arms"]["status_quo"].update(steps_sha256=["a", "b", "a"]),
           ["B.G4"])
    mutate("candidate crashes on an example",
           lambda r: r["nonregression"]["examples"][1]["arms"]["B"].update(exits=[0, 1, 0]), ["B.G4"])
    mutate("public example not runnable",
           lambda r: r["nonregression"]["examples"][0].update(runnable=False), ["B.G4"])
    quiet = copy.deepcopy(base)
    quiet["nonregression"]["examples"][2]["runnable"] = False
    check("a fixture that cannot run is listed, not failed", not judge(quiet, world)[1]["arms"]["B"]["G4"]
          and judge(quiet, world)[1]["arms"]["B"]["skipped"] == ["controls/x/f.json"])

    # candidate C
    def c_short_lived(r):  # reconstructs P, whose lambda x step length is far below the threshold
        step = _find(r, "ordinary", 48, "C")["steps"][0]
        step["reconstructed"] = ["P"]
        step["n_reconstructed_listed"] = 1
        step["n_below"] += 1
        step["bound"].update(n_reconstructed=1, reconstructed_states=["P"])
    mutate("C reconstructs a state that is short-lived by no measure", c_short_lived, ["C.G2"])
    mutate("C threshold in the block",
           lambda r: _find(r, "ordinary", 16, "C")["steps"][0]["bound"].update(reconstruct_threshold=10.0), ["C.G2"])
    mutate("C threshold in the ledger",
           lambda r: _find(r, "ordinary", 16, "C")["ledger_below_floor"].update(reconstruct_threshold=10.0), ["C.G2"])
    mutate("C count differs", lambda r: _find(r, "ordinary", 16, "C")["steps"][0]["bound"].update(n_reconstructed=0),
           ["C.G2"])

    def c_unexplained_exclusion(r):  # D met the threshold, was excluded as A would and no fallback is counted
        step = _find(r, "ordinary", 16, "C")["steps"][0]
        step.update(reconstructed=[], n_reconstructed_listed=0, excluded=["D"], n_excluded_inventory=1)
        step["bound"].update(n_reconstructed=0, reconstructed_states=[], n_states_excluded=1)
    mutate("C excludes a relaxed state without a counted fallback", c_unexplained_exclusion, ["C.G2"])
    mutate("C reconstructed value wrong on the actinide case",
           lambda r: _find(r, "hB", 16, "C")["steps"][0].update(activity_total=_find(r, "hB", 16, "C")["steps"][0][
               "activity_total"] * 1.001), ["C.G3.actinide_guard"], expect_decision="B")
    mutate("A reconstructs", lambda r: _find(r, "ordinary", 16, "A")["steps"][0].update(reconstructed=["D"]),
           ["A.G2"])

    def c_slow(r):
        for run in r["solver"]["runs"]:
            if run["arm"] == "C" and run["set"] == "heldout" and run["summary"]["refined_step_ids"]:
                run["wall_s"] = [0.5] * 5
    mutate("C too slow where it refines", c_slow, ["C.G3.cost_triggered"], expect_decision="B")

    def c_slow_idle(r):
        for run in r["solver"]["runs"]:
            if run["arm"] == "C" and run["set"] == "heldout" and not run["summary"]["refined_step_ids"]:
                run["wall_s"] = [0.3] * 5
    mutate("C slower where it only reconstructs or does nothing", c_slow_idle, ["C.G3.cost_idle"],
           expect_decision="B")

    def c_bounds_without_refining(r):  # the trigger fired on 5d (a material bound at CRAM-16) and C only bounded
        a, c = _find(r, FIX_CASE, 16, "A"), _find(r, FIX_CASE, 16, "C")
        c["steps"], c["summary"] = copy.deepcopy(a["steps"]), copy.deepcopy(a["summary"])
        for step in c["steps"]:
            step["bound"].update(policy="equilibrium", n_reconstructed=0, reconstructed_states=[],
                                 reconstruct_threshold=H.RECONSTRUCT_THRESHOLD, n_cycle_fallback=0,
                                 n_parent_fallback=0, n_memory_fallback=0, n_separation_fallback=0)
        c["ledger_below_floor"] = dict(copy.deepcopy(a["ledger_below_floor"]), policy="equilibrium",
                                       reconstruct_threshold=H.RECONSTRUCT_THRESHOLD)
    mutate("C bounds without refining when the trigger fired", c_bounds_without_refining, ["C.G2"])

    # rate separation: the same record judged against a reference whose parent decays only 2x slower than D
    slower = copy.deepcopy(world["ref_docs"])
    for key in slower:
        for nuclide in slower[key]["nuclides"]:
            if nuclide["name"] == "P":
                nuclide["half_life_s"] = "0.05"
    mutate("C reconstructs a state that fails rate separation", lambda r: None, ["C.G2"],
           world=dict(world, ref_docs=slower))
    chain_parents = {"Pb214": {"Po218": 3.7e-3}, "Po218": {"Rn222": 2.1e-6}}
    chain_rho = {"Rn222": 2.1e-6, "Po218": 3.7e-3, "Pb214": 4.3e-4}
    check("Pb-214 is separated from resolved Rn-222 through reconstructed Po-218",
          separation_violations("Pb214", chain_parents, chain_rho, {"Po218"}, set(chain_rho)) == [])
    check("measured against the reconstructed intermediate it would fail",
          separation_violations("Pb214", chain_parents, chain_rho, set(), set(chain_rho)) == ["Po218"])
    check("a resolved ancestor that is too fast is caught",
          separation_violations("Pb214", chain_parents, dict(chain_rho, Pb214=4.0e-5), {"Po218"}, set(chain_rho))
          == ["Rn222"])
    check("absent ancestors do not count",
          separation_violations("Pb214", chain_parents, chain_rho, set(), {"Pb214"}) == [])

    # the decision rule: C, then B, then A, then the status quo
    world_clean = _fake_world(legit_flagged=False)
    world_clean["hash_log"] = "(not under test)"
    record_clean = _synthetic_record(world_clean)
    gates_c, result_c, _ = judge(record_clean, world_clean)
    check("clean world: all three candidates pass and C is adopted", not [
        f for f in failed(gates_c, result_c) if f != "G0"] and result_c["decision"] == "C")
    if [f for f in failed(gates_c, result_c) if f != "G0"]:
        print("  clean world failures:", sorted(failed(gates_c, result_c)),
              [p for a in result_c["arms"].values() for p in a["G2"][:3]])

    def break_c(r):
        _find(r, "ordinary", 48, "C")["steps"][0]["activity_total"] *= 1.001

    def break_b(r):
        _find(r, "ordinary", 48, "B")["steps"][0]["activity_total"] *= 1.001
    mutate("C fails, B passes: B is adopted", break_c, ["C.G2"], record=record_clean, world=world_clean,
           expect_decision="B")

    def break_cb(r):
        break_c(r)
        break_b(r)
    mutate("C and B fail, A passes: A is adopted", break_cb, ["B.G2", "C.G2"], record=record_clean,
           world=world_clean, expect_decision="A")
    mutate("C and B fail and A fails the actinide guard: the status quo stays", break_cb, ["B.G2"],
           record=base, world=world, expect_decision="status_quo")

    def break_all(r):
        break_cb(r)
        _find(r, "hA", 16, "A")["steps"][1]["bound"]["activity_Bq_per_g"] = 1e-30
    mutate("all three fail: the status quo stays", break_all, ["A.G3.bound_valid"], record=record_clean,
           world=world_clean, expect_decision="status_quo")
    mutate("B and C both pass: C, not B", lambda r: None, ["G0"], record=record_clean, world=world_clean,
           expect_decision="C")

    # helpers
    check("relative error", relative(1.0, Decimal(1)) == 0.0 and relative(0.0, Decimal(0)) == 0.0
          and relative(1.0, Decimal(0)) == INF and relative(float("nan"), Decimal(1)) == INF)
    check("relative overflow", relative(1.0, Decimal("1e-999999999")) == INF)
    check("covered", covered(0.0, Decimal("1e-8"), 1e-7) and not covered(0.0, Decimal("1e-6"), 1e-7))
    check("NaN does not hold", not holds(float("nan")) and holds(0.0) and not holds(2e-6))
    check("cost rule", cost_problem("x", [0.004] * 3, [0.009] * 3, True) is None
          and cost_problem("x", [1.0] * 3, [2.5] * 3, True) is not None
          and cost_problem("x", [1.0] * 3, [1.15] * 3, False) is not None
          and cost_problem("x", [1.0] * 3, [1.05] * 3, False) is None)
    print("check_p174 self-test:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


# ----------------------------------------------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(0 if selftest() else 1)
    record = json.loads(args.record.read_text())
    gates, result, text = evaluate(record)
    rendered = render(gates, result)
    rendered["allowed_sentences"] = text
    infrastructure = all(not p for p in gates.values())
    rendered["verdict"] = (f"P174-PASS (decision: {result['decision']}; pending G5, CI on the evidence commit)"
                           if infrastructure else "P174-FAIL")
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems[:20]:
            print(f"  {problem}")
    for arm, data in result.get("arms", {}).items():
        g3_fail = [k for k, v in data["G3"].items() if v]
        print(f"candidate {arm}: G2 {'PASS' if not data['G2'] else 'FAIL'}, G3 "
              f"{'PASS' if not g3_fail else 'FAIL ' + ', '.join(g3_fail)}, G4 {'PASS' if not data['G4'] else 'FAIL'}")
        for problem in (data["G2"] + data["G4"])[:10]:
            print(f"  {problem}")
        for item in g3_fail:
            for problem in data["G3"][item][:5]:
                print(f"  [{item}] {problem}")
    passed = infrastructure
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            current = json.loads(json.dumps(rendered, sort_keys=True))
            if recorded != current:
                print("verdict file differs from the recomputed verdict")
                passed = False
        else:
            print("no verdict file to compare")
            passed = False
    else:
        VERDICT.write_text(json.dumps(rendered, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    for sentence in text:
        print("  " + sentence)
    print(rendered["verdict"] if passed else "P174-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
