#!/usr/bin/env python3
"""P182 checker (protocols/ACTINV-P182_PROTOCOL.md): the faster rate prune leaves every result unchanged. Reads
results/p182_record.json written by controls/p182_harness.py. G0-G4 are evaluated here; G5 (CI on the pushed evidence
commit) is external. This module also holds the comparison functions the harness uses (one source of truth).

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P182_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  The builds' digests and commits, the libraries and the mesh inputs equal the pins; the harness, checker and
      protocol equal this checkout's; a clean tracked tree; the harness in the AGENTS.md scope.
  G2  All 268 problems (132 FNS cases x 2 libraries, 4 meshes) ran under both builds in the registered order, and every
      run exited 0.
  G3  Every problem is unchanged outside ledger.rate_pruning and meets the pruning record.
  G4  Quiet machine: no other transient scope of the user at the start, between problems or at the end; median
      1-minute load average before the runs at most 3.0. Then the summary and the allowed sentences are recomputed and
      written to results/p182_verdict.json (with --no-write, compared with it).

Verdict: P182-PASS if G0-G5 hold (G5 external). The times are reported, not gated.

Usage: check_p182.py [--record FILE] [--no-write]
       check_p182.py --selftest
"""
import argparse
import copy
import hashlib
import itertools
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p182_harness as h  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P182_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = h.RECORD
VERDICT = ROOT / "results" / "p182_verdict.json"
REPO_INPUTS = {"harness": "controls/p182_harness.py", "checker": "controls/check_p182.py",
               "protocol": "protocols/ACTINV-P182_PROTOCOL.md"}

MAX_MEDIAN_LOAD = 3.0
MAX_PATHS = 10
REL_TOL = 1e-12  # a bound or total counts as "at least" the baseline's within this
TOTAL_UP = 1e-6  # a total may grow by this much
FLOOR_FACTOR = 1e-6  # candidate-only bounds lie below this times bmin
DEFAULT_BMIN = 1e-8
TOTALS = ("activity_Bq_per_g_bound", "heat_W_per_g_bound", "dropped_activity_Bq_per_g_bound",
          "dropped_heat_W_per_g_bound", "kept_activity_Bq_per_g_bound", "kept_heat_W_per_g_bound")
LIBRARY_TEXT = {"tendl": "ACTINV's shipped TENDL-2025 data", "endf": "an ENDF/B-VIII.1 library"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def seconds(x):
    return f"{x:.1f}" if x < 100 else f"{x:,.0f}"


# ------------------------------------------------------------------------------------------------------ comparison

def bmin_of(spec):
    """The problem's keep threshold: options.screen.bmin_atoms_per_g, else options.bmin_atoms_per_g, else 1e-8."""
    options = spec.get("options") or {}
    screen = options.get("screen")
    if isinstance(screen, dict) and "bmin_atoms_per_g" in screen:
        return screen["bmin_atoms_per_g"]
    return options.get("bmin_atoms_per_g", DEFAULT_BMIN)


def canonicalize(obj, kind):
    """Remove the timing fields, in place: `ms` of a run result ("run") or of a mesh cell's result; the footer's
    `wall_time_s` and `cells_per_s` (a parsed NDJSON record, kind "mesh"). Returns obj."""
    if kind == "run":
        obj.pop("ms", None)
    elif obj.get("record") == "cell" and isinstance(obj.get("result"), dict):
        obj["result"].pop("ms", None)
    elif obj.get("record") == "footer":
        obj.pop("wall_time_s", None)
        obj.pop("cells_per_s", None)
    return obj


def pop_rate_pruning(obj, kind):
    """Remove and return ledger.rate_pruning (run: the result; mesh: a cell record's result), None if absent."""
    holder = obj if kind == "run" else (obj.get("result") if obj.get("record") == "cell" else None)
    ledger = holder.get("ledger") if isinstance(holder, dict) else None
    return ledger.pop("rate_pruning", None) if isinstance(ledger, dict) else None


def kept_of(obj, kind):
    """The number of kept states (run, cell) or the footer's (min, max); None where the record has none."""
    if kind == "run":
        return obj.get("pruned_states")
    if obj.get("record") == "cell":
        return (obj.get("result") or {}).get("pruned_states")
    if obj.get("record") == "footer":
        return [obj.get("min_pruned_states"), obj.get("max_pruned_states")]
    return None


def diff_paths(a, b, path="", out=None):
    """Paths at which two parsed JSON values differ (exact equality, type included); at most MAX_PATHS."""
    out = [] if out is None else out
    if len(out) >= MAX_PATHS:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key in a and key in b:
                diff_paths(a[key], b[key], f"{path}/{key}", out)
            elif len(out) < MAX_PATHS:
                out.append(f"{path}/{key}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path} (length {len(a)} vs {len(b)})")
        for i, (x, y) in enumerate(zip(a, b)):
            diff_paths(x, y, f"{path}/{i}", out)
    elif type(a) is not type(b) or a != b:
        out.append(path or "/")
    return out[:MAX_PATHS]


def at_least(c, b):
    """The candidate's value c is at least the baseline's b within REL_TOL; non-numbers must be equal."""
    if finite(c, b):
        return c >= b * (1.0 - REL_TOL)
    return c == b


def empty_checks():
    return {"ok": True, "problems": [], "baseline_dropped": 0, "candidate_dropped": 0, "missing_in_candidate": 0,
            "bound_violations": 0, "candidate_only": 0, "candidate_only_above_floor": 0,
            "candidate_only_max_rel_floor": None, "total_violations": 0, "other_differences": 0, "total_ratio_min": {}, "total_ratio_max": {}}


def pruning_checks(base_rp, cand_rp, bmin):
    """The Pruning record bullets for one result's ledger.rate_pruning pair."""
    out = empty_checks()

    def fail(text):
        out["ok"] = False
        if len(out["problems"]) < MAX_PATHS:
            out["problems"].append(text)

    if base_rp is None and cand_rp is None:
        return out
    if not isinstance(base_rp, dict) or not isinstance(cand_rp, dict):
        fail("rate_pruning present in only one result")
        return out
    base = {e["nuclide"]: e for e in base_rp.get("dropped", [])}
    cand = {e["nuclide"]: e for e in cand_rp.get("dropped", [])}
    floor = FLOOR_FACTOR * bmin
    out["baseline_dropped"], out["candidate_dropped"] = len(base), len(cand)
    for name, b in base.items():
        c = cand.get(name)
        if c is None:
            out["missing_in_candidate"] += 1
            fail(f"{name}: dropped by the baseline, not listed by the candidate")
            continue
        for key in ("atoms_per_g_bound", "feed_atoms_per_g_s_bound"):
            if not at_least(c.get(key), b.get(key)):
                out["bound_violations"] += 1
                fail(f"{name}: candidate {key} {c.get(key)} below the baseline's {b.get(key)}")
    for name, c in cand.items():
        if name in base:
            continue
        out["candidate_only"] += 1
        bound = c.get("atoms_per_g_bound")
        if finite(bound) and floor > 0:
            rel = bound / floor
            if out["candidate_only_max_rel_floor"] is None or rel > out["candidate_only_max_rel_floor"]:
                out["candidate_only_max_rel_floor"] = rel
        if not (finite(bound) and bound < floor):
            out["candidate_only_above_floor"] += 1
            fail(f"{name}: candidate-only bound {bound} not below {floor}")
    for field in TOTALS:
        b, c = base_rp.get(field), cand_rp.get(field)
        if b is None and c is None:
            continue
        if not finite(b, c):
            if b != c:
                out["total_violations"] += 1
                fail(f"{field}: {b} vs {c}")
            continue
        bad = c < b * (1.0 - REL_TOL) or (b > 0 and c > b * (1.0 + TOTAL_UP))
        if bad:
            out["total_violations"] += 1
            fail(f"{field}: candidate {c} against baseline {b}")
        if b > 0:
            out["total_ratio_min"][field] = out["total_ratio_max"][field] = c / b
    for key in sorted((set(base_rp) | set(cand_rp)) - {"dropped"} - set(TOTALS)):
        if base_rp.get(key) != cand_rp.get(key):
            out["other_differences"] += 1
            fail(f"{key}: {base_rp.get(key)} vs {cand_rp.get(key)}")
    return out


def merge_checks(into, one):
    """Fold one result's pruning checks into a running total (mesh cells)."""
    into["ok"] = into["ok"] and one["ok"]
    into["problems"] = (into["problems"] + one["problems"])[:MAX_PATHS]
    for key in ("baseline_dropped", "candidate_dropped", "missing_in_candidate", "bound_violations",
                "candidate_only", "candidate_only_above_floor", "total_violations", "other_differences"):
        into[key] += one[key]
    rel = [v for v in (into["candidate_only_max_rel_floor"], one["candidate_only_max_rel_floor"]) if v is not None]
    into["candidate_only_max_rel_floor"] = max(rel) if rel else None
    for key, pick in (("total_ratio_min", min), ("total_ratio_max", max)):
        for field, v in one[key].items():
            into[key][field] = pick(into[key][field], v) if field in into[key] else v
    return into


def compare_run(base, cand, bmin):
    """Compare two parsed `actinv run` results (consumed: timing and rate_pruning are removed from them)."""
    kept = [kept_of(base, "run"), kept_of(cand, "run")]
    rps = []
    for obj in (base, cand):
        canonicalize(obj, "run")
        rps.append(pop_rate_pruning(obj, "run"))
    diffs = diff_paths(base, cand)
    return {"unchanged": not diffs, "first_differences": diffs, "kept_states": kept,
            "kept_sets_equal": kept[0] == kept[1], "pruning": pruning_checks(rps[0], rps[1], bmin), "bmin": bmin}


def compare_mesh_lines(base_lines, cand_lines, bmin):
    """Compare two mesh NDJSON outputs record by record (iterables of text lines; streamed)."""
    diffs, pruning, records, cell_count = [], empty_checks(), 0, None
    kept_equal, kept_cells = True, 0
    for n, (lb, lc) in enumerate(itertools.zip_longest(base_lines, cand_lines)):
        if lb is None or lc is None:
            diffs.append(f"record {n}: one output has fewer records")
            break
        records += 1
        base, cand = json.loads(lb), json.loads(lc)
        if base.get("record") == "header" and cell_count is None:
            cell_count = base.get("cell_count")
        if kept_of(base, "mesh") != kept_of(cand, "mesh"):
            kept_equal = False
        kept_cells += kept_of(base, "mesh") is not None
        rps = []
        for obj in (base, cand):
            canonicalize(obj, "mesh")
            rps.append(pop_rate_pruning(obj, "mesh"))
        if len(diffs) < MAX_PATHS:
            diffs += [f"record {n}{p}" for p in diff_paths(base, cand)][:MAX_PATHS - len(diffs)]
        merge_checks(pruning, pruning_checks(rps[0], rps[1], bmin))
    return {"unchanged": not diffs, "first_differences": diffs, "kept_sets_equal": kept_equal, "pruning": pruning,
            "bmin": bmin, "records": records, "cell_count": cell_count, "records_with_kept_count": kept_cells}


def compare_files(kind, base_path, cand_path, bmin):
    if kind == "run":
        return compare_run(json.loads(Path(base_path).read_text()), json.loads(Path(cand_path).read_text()), bmin)
    with open(base_path) as b, open(cand_path) as c:
        return compare_mesh_lines(b, c, bmin)


# ------------------------------------------------------------------------------------------------------ gates

def g0():
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    logged = HASH_LOG.read_text()
    return [f"{path.name} hash not in protocol_hash.txt"
            for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P182_AMENDMENT_*.md"))]
            if f"{sha256(path)}  protocols/{path.name}" not in logged]


def scope_ok(cg):
    ok, _ = h.h181.cgroup_enforced(cg or {}, 6)
    return ok


def g1(record, repo=None):
    problems = []
    repo = repo if repo is not None else {k: sha256(ROOT / v) for k, v in REPO_INPUTS.items()}
    if record.get("schema") != h.SCHEMA:
        problems.append("wrong schema")
    inputs = record.get("inputs", {})
    for name, digest in {**{k: v[1] for k, v in h.PINS.items()}, **repo}.items():
        if inputs.get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not the pin")
    plan = record.get("plan", {})
    for build, (_, digest, commit) in h.BUILDS.items():
        if plan.get("builds", {}).get(build) != {"commit": commit, "sha256": digest}:
            problems.append(f"{build} build differs from the pinned digest and commit")
    if plan.get("threads") != h.THREADS:
        problems.append("thread count differs from the registered")
    env = record.get("environment", {})
    if env.get("git_dirty_paths") or env.get("git_dirty_paths_end"):
        problems.append("tracked tree was not clean")
    if not scope_ok(env.get("cgroup")):
        problems.append("harness cgroup is not the bounded scope")
    return problems


def g2(record):
    problems = []
    plan = record.get("plan", {})
    cases = plan.get("cases", [])
    if len(set(cases)) != h.CASES:
        problems.append(f"{len(set(cases))} distinct FNS cases, expected {h.CASES}")
    expected = h.plan_problems(cases)
    ids = [p["id"] for p in expected]
    if [p.get("id") for p in plan.get("problems", [])] != ids or len(ids) != h.PROBLEMS:
        problems.append(f"the plan is not the {h.PROBLEMS} registered problems in the registered order")
    if plan.get("order") != [{"problem": p["id"], "first": h.first_build(k)} for k, p in enumerate(expected)]:
        problems.append("the registered build order differs")
    runs = {}
    for r in record.get("runs", []):
        runs.setdefault((r.get("problem"), r.get("build")), []).append(r)
    for k, pid in enumerate(ids):
        for build in h.BUILD_ORDER:
            found = runs.get((pid, build), [])
            if len(found) != 1:
                problems.append(f"{pid} under {build}: {len(found)} runs")
                continue
            r = found[0]
            if r.get("exit_code") != 0 or r.get("timed_out"):
                problems.append(f"{pid} under {build}: exit {r.get('exit_code')}, timed out {r.get('timed_out')}")
            if not (finite(r.get("wall_s"), r.get("cpu_s")) and r.get("output_sha256")):
                problems.append(f"{pid} under {build}: no time or output digest")
            if r.get("position") != (0 if build == h.first_build(k) else 1):
                problems.append(f"{pid} under {build}: not run in the registered order")
    if len(record.get("runs", [])) != 2 * h.PROBLEMS:
        problems.append(f"{len(record.get('runs', []))} runs, expected {2 * h.PROBLEMS}")
    for build in h.BUILD_ORDER:
        if record.get("warmup", {}).get(build, {}).get("exit_code") != 0:
            problems.append(f"the {build} warm-up did not exit 0")
    for name, mesh in h.MESHES.items():
        if record.get("comparisons", {}).get(name, {}).get("cell_count") != mesh["regions"]:
            problems.append(f"{name}: output header does not show {mesh['regions']} regions")
    return problems


def g3(record):
    problems = []
    comps = record.get("comparisons", {})
    for p in record.get("plan", {}).get("problems", []):
        c = comps.get(p["id"])
        if not c:
            problems.append(f"{p['id']}: not compared")
            continue
        if not c.get("unchanged"):
            problems.append(f"{p['id']}: changed at {c.get('first_differences', [])[:3]}")
        if not c.get("kept_sets_equal"):
            problems.append(f"{p['id']}: kept set differs")
        pr = c.get("pruning") or {}
        for key in ("missing_in_candidate", "bound_violations", "candidate_only_above_floor", "total_violations"):
            if pr.get(key) != 0:
                problems.append(f"{p['id']}: pruning {key} = {pr.get(key)}")
        if not pr.get("ok"):
            problems.append(f"{p['id']}: pruning record fails: {pr.get('problems', [])[:3]}")
        if any(v < 1.0 - REL_TOL for v in (pr.get("total_ratio_min") or {}).values()) or \
                any(v > 1.0 + TOTAL_UP for v in (pr.get("total_ratio_max") or {}).values()):
            problems.append(f"{p['id']}: a pruning total ratio is outside [1 - 1e-12, 1 + 1e-6]")
    return problems


def quiet(record):
    problems = []
    env = record.get("environment", {})
    if env.get("other_scopes_start"):
        problems.append(f"other transient scopes at the start: {env['other_scopes_start']}")
    if env.get("other_scopes_end"):
        problems.append(f"other transient scopes at the end: {env['other_scopes_end']}")
    loads = []
    for b in record.get("before", []):
        if b.get("other_scopes"):
            problems.append(f"other transient scopes before {b.get('problem')}")
        if b.get("load_average"):
            loads.append(b["load_average"][0])
    if len(loads) != h.PROBLEMS or statistics.median(loads) > MAX_MEDIAN_LOAD:
        problems.append(f"median 1-minute load average before the runs above {MAX_MEDIAN_LOAD} (or not recorded)")
    return problems


# ------------------------------------------------------------------------------------------------------ summary

def summary(record):
    walls = {(r["problem"], r["build"]): r["wall_s"] for r in record["runs"]}
    problems = record["plan"]["problems"]
    fns = {}
    for lib in h.LIBRARIES:
        ids = [p["id"] for p in problems if p["kind"] == "run" and p["library"] == lib]
        fns[lib] = {"problems": len(ids),
                    **{f"{b}_median_s": statistics.median(walls[(i, b)] for i in ids) for b in h.BUILD_ORDER}}
    meshes = []
    for name, mesh in h.MESHES.items():
        meshes.append({"problem": name, "elements": mesh["elements"], "regions": mesh["regions"],
                       "library": mesh["library"], **{f"{b}_wall_s": walls[(name, b)] for b in h.BUILD_ORDER}})
    comps = [record["comparisons"][p["id"]]["pruning"] for p in problems]
    ratios = {f: [v for c in comps for key in ("total_ratio_min", "total_ratio_max") for v in [c[key].get(f)]
                  if v is not None] for f in TOTALS}
    rel = [c["candidate_only_max_rel_floor"] for c in comps if c["candidate_only_max_rel_floor"] is not None]
    result = {"cpu_model": record.get("environment", {}).get("cpu_model"), "fns": fns, "mesh": meshes,
              "pruning": {"candidate_only_entries": sum(c["candidate_only"] for c in comps),
                          "baseline_dropped_entries": sum(c["baseline_dropped"] for c in comps),
                          "candidate_only_max_bound_over_floor": max(rel) if rel else None,
                          "total_ratio_min": {f: min(v) for f, v in ratios.items() if v},
                          "total_ratio_max": {f: max(v) for f, v in ratios.items() if v}}}
    sentences = []
    for m in meshes:
        sentences.append(
            f"On P181's mixed-material mesh at {m['elements']:,} elements ({m['regions']:,} regions) with "
            f"{LIBRARY_TEXT[m['library']]}, `actinv mesh` took {seconds(m['baseline_wall_s'])} s before the change "
            f"and {seconds(m['candidate_wall_s'])} s after it, with identical results apart from looser bounds on "
            f"nuclides that are dropped anyway (one run each, two threads).")
    t, e = fns["tendl"], fns["endf"]
    sentences.append(
        f"On P172's {h.CASES} FNS problems, the median time per problem went from {t['baseline_median_s']:.2f} s to "
        f"{t['candidate_median_s']:.2f} s with TENDL-2025 and from {e['baseline_median_s']:.2f} s to "
        f"{e['candidate_median_s']:.2f} s with ENDF/B-VIII.1 (one process per problem, two CPU threads).")
    return result, sentences


def evaluate(record, repo=None):
    gates = {"G0": g0(), "G1": g1(record, repo), "G2": g2(record)}
    gates["G3"] = g3(record) if not gates["G2"] else ["runs incomplete"]
    gates["G4"] = quiet(record)
    result, sentences = summary(record) if not gates["G2"] and not gates["G3"] else ({}, [])
    return gates, result, sentences


# ------------------------------------------------------------------------------------------------------ self-test

def synthetic_run_pair():
    entry = lambda n, a, f: {"nuclide": n, "atoms_per_g_bound": a, "feed_atoms_per_g_s_bound": f}  # noqa: E731
    base = {"mode": "auto", "pruned_states": 7, "ms": 123, "steps": [{"t_s": 1.0, "heat": 2.5}],
            "ledger": {"balance": 0.5, "rate_pruning": {
                "threshold_atoms_per_g": 1e-8,
                "dropped": [entry("Fe55", 1e-9, 1e-12), entry("Mn54", 2e-9, 3e-12)],
                "activity_Bq_per_g_bound": 10.0, "heat_W_per_g_bound": 1e-6,
                "dropped_activity_Bq_per_g_bound": 2.0, "dropped_heat_W_per_g_bound": 1e-7,
                "kept_activity_Bq_per_g_bound": 8.0, "kept_heat_W_per_g_bound": 9e-7}}}
    cand = copy.deepcopy(base)
    cand["ms"] = 61
    rp = cand["ledger"]["rate_pruning"]
    rp["dropped"][1]["atoms_per_g_bound"] = 2.5e-9  # looser
    rp["dropped"].append(entry("X99", 1e-16, 1e-20))  # candidate only, below 1e-6 * bmin = 1e-14
    rp["activity_Bq_per_g_bound"] = 10.0 * (1 + 1e-9)
    return base, cand


def mesh_lines(run_pair, regions, cells=2):
    base, cand = run_pair
    out = []
    for obj in (base, cand):
        lines = [json.dumps({"record": "header", "schema": "actinv-mesh-result-1", "cell_count": regions})]
        for i in range(cells):
            lines.append(json.dumps({"record": "cell", "ordinal": i, "result": copy.deepcopy(obj)}))
        lines.append(json.dumps({"record": "footer", "cell_count": regions, "min_pruned_states": 7,
                                 "max_pruned_states": 7, "wall_time_s": 9.0 if obj is base else 4.0,
                                 "cells_per_s": 1.0 if obj is base else 2.0}))
        out.append(lines)
    return out


def synthetic_record():
    cases = [f"m{i:03d}_e{i}" for i in range(h.CASES)]
    problems = h.plan_problems(cases)
    pair = synthetic_run_pair()
    run_cmp = compare_run(copy.deepcopy(pair[0]), copy.deepcopy(pair[1]), 1e-8)
    cg = {"path": "/x/run-h.scope", "memory_max": str(6 << 30), "memory_swap_max": "0", "pids_max": "128",
          "cpu_max": "200000 100000"}
    record = {"schema": h.SCHEMA,
              "inputs": {**{k: {"sha256": v[1]} for k, v in h.PINS.items()},
                         **{k: {"sha256": "r" * 64} for k in REPO_INPUTS}},
              "environment": {"git_dirty_paths": "", "cgroup": cg, "other_scopes_start": [], "other_scopes_end": [],
                              "cpu_model": "x"},
              "plan": {"cases": cases, "problems": problems, "threads": h.THREADS,
                       "builds": {b: {"commit": v[2], "sha256": v[1]} for b, v in h.BUILDS.items()},
                       "order": [{"problem": p["id"], "first": h.first_build(k)} for k, p in enumerate(problems)]},
              "warmup": {b: {"exit_code": 0} for b in h.BUILD_ORDER},
              "runs": [], "before": [], "comparisons": {}}
    for k, p in enumerate(problems):
        mesh = h.MESHES.get(p["id"])
        for pos, build in enumerate(h.build_order(k)):
            base_wall = 40.0 if mesh else 2.0 + (k % 7) * 0.1
            record["runs"].append({"problem": p["id"], "build": build, "position": pos, "exit_code": 0,
                                   "timed_out": False, "wall_s": base_wall * (1.0 if build == "baseline" else 0.5),
                                   "cpu_s": 1.0, "output_sha256": "o" * 64})
        record["before"].append({"problem": p["id"], "load_average": [1.0, 1.0, 1.0], "other_scopes": []})
        if mesh:
            lb, lc = mesh_lines(pair, mesh["regions"])
            record["comparisons"][p["id"]] = compare_mesh_lines(lb, lc, 1e-8)
        else:
            record["comparisons"][p["id"]] = copy.deepcopy(run_cmp)
    repo = {k: "r" * 64 for k in REPO_INPUTS}
    return record, repo


def selftest():
    failures = []
    record, repo = synthetic_record()
    gates, result, sentences = evaluate(record, repo)
    if any(gates[g] for g in ("G1", "G2", "G3", "G4")):
        failures.append(f"clean synthetic record fails: { {g: p[:3] for g, p in gates.items() if p and g != 'G0'} }")
    elif len(sentences) != 5 or "512 elements (776 regions) with ACTINV's shipped TENDL-2025 data" not in sentences[0]:
        failures.append(f"sentences wrong: {sentences}")

    # comparison functions on synthetic outputs
    def run_case(mutate):
        base, cand = synthetic_run_pair()
        mutate(base, cand)
        return compare_run(base, cand, 1e-8)

    def rp(o):
        return o["ledger"]["rate_pruning"]

    clean = run_case(lambda b, c: None)
    if not (clean["unchanged"] and clean["pruning"]["ok"] and clean["kept_sets_equal"]):
        failures.append(f"clean pair not accepted: {clean}")
    elif clean["pruning"]["candidate_only"] != 1 or not 0 < clean["pruning"]["candidate_only_max_rel_floor"] < 1:
        failures.append(f"candidate-only accounting wrong: {clean['pruning']}")
    if run_case(lambda b, c: c.update(ms=1))["unchanged"] is not True:
        failures.append("the run time was not ignored")
    bad = {
        "value change outside rate_pruning": (lambda b, c: c["steps"][0].update(heat=2.6), "unchanged"),
        "ledger value change": (lambda b, c: c["ledger"].update(balance=0.6), "unchanged"),
        "kept count change": (lambda b, c: c.update(pruned_states=8), "kept_sets_equal"),
    }
    for name, (mutate, key) in bad.items():
        if run_case(mutate)[key]:
            failures.append(f"not caught: {name}")
    pruning_bad = {
        "baseline-dropped nuclide missing": lambda b, c: rp(c)["dropped"].pop(0),
        "candidate bound tighter": lambda b, c: rp(c)["dropped"][0].update(atoms_per_g_bound=0.5e-9),
        "candidate feed bound tighter": lambda b, c: rp(c)["dropped"][0].update(feed_atoms_per_g_s_bound=0.0),
        "candidate-only above the floor": lambda b, c: rp(c)["dropped"][-1].update(atoms_per_g_bound=2e-14),
        "total above 1 + 1e-6": lambda b, c: rp(c).update(heat_W_per_g_bound=1.00001e-6),
        "total below 1 - 1e-12": lambda b, c: rp(c).update(kept_heat_W_per_g_bound=9e-7 * (1 - 1e-9)),
        "rate_pruning dropped from one result": lambda b, c: c["ledger"].pop("rate_pruning"),
        "threshold changed": lambda b, c: rp(c).update(threshold_atoms_per_g=1e-9),
        "unbounded component added": lambda b, c: rp(c).update(unbounded_components=[["X1"]]),
    }
    for name, mutate in pruning_bad.items():
        if run_case(mutate)["pruning"]["ok"]:
            failures.append(f"not caught: {name}")
    if not run_case(lambda b, c: rp(c).update(activity_Bq_per_g_bound=10.0 * (1 + 1e-13)))["pruning"]["ok"]:
        failures.append("a total within tolerance was refused")

    # the mesh path
    for name, mutate in {"cell value change": lambda c: c.update(result={"x": 1}),
                         "footer kept range": lambda c: c.update(max_pruned_states=9)}.items():
        lb, lc = mesh_lines(synthetic_run_pair(), 776)
        i = 1 if name == "cell value change" else -1
        obj = json.loads(lc[i])
        mutate(obj)
        lc[i] = json.dumps(obj)
        if compare_mesh_lines(lb, lc, 1e-8)["unchanged"]:
            failures.append(f"mesh not caught: {name}")
    lb, lc = mesh_lines(synthetic_run_pair(), 776)
    if not compare_mesh_lines(lb, lc, 1e-8)["unchanged"] or compare_mesh_lines(lb, lc[:-1], 1e-8)["unchanged"]:
        failures.append("mesh timing fields not ignored, or a missing record not caught")

    # bmin
    if bmin_of({}) != 1e-8 or bmin_of({"options": {"bmin_atoms_per_g": 1e-6}}) != 1e-6 or \
            bmin_of({"options": {"bmin_atoms_per_g": 1e-6, "screen": {"bmin_atoms_per_g": 1e-12}}}) != 1e-12:
        failures.append("bmin_of wrong")

    # gates on records
    def swap_comparison(rec, mutate):
        base, cand = synthetic_run_pair()
        mutate(base, cand)
        rec["comparisons"][rec["plan"]["problems"][5]["id"]] = compare_run(base, cand, 1e-8)

    cases = {
        "physics value changed": lambda r: swap_comparison(r, lambda b, c: c["steps"][0].update(heat=3.0)),
        "dropped nuclide missing": lambda r: swap_comparison(r, lambda b, c: rp(c)["dropped"].pop(0)),
        "bound tighter": lambda r: swap_comparison(r, lambda b, c: rp(c)["dropped"][0].update(
            atoms_per_g_bound=1e-10)),
        "candidate-only above floor": lambda r: swap_comparison(r, lambda b, c: rp(c)["dropped"][-1].update(
            atoms_per_g_bound=1e-9)),
        "total above 1 + 1e-6": lambda r: swap_comparison(r, lambda b, c: rp(c).update(
            dropped_heat_W_per_g_bound=1.00001e-7)),
        "nonzero exit": lambda r: r["runs"][10].update(exit_code=1),
        "timed out": lambda r: r["runs"][10].update(timed_out=True),
        "missing run": lambda r: r["runs"].pop(3),
        "missing problem": lambda r: (r["plan"]["problems"].pop(), r["plan"]["cases"].pop()),
        "run in the wrong order": lambda r: [x.update(position=1 - x["position"]) for x in r["runs"][:2]],
        "baseline pin mismatch": lambda r: r["inputs"]["baseline_binary"].update(sha256="0" * 64),
        "library pin mismatch": lambda r: r["inputs"]["tendl_library"].update(sha256="0" * 64),
        "mesh flux pin mismatch": lambda r: r["inputs"]["flux_d8"].update(sha256="0" * 64),
        "harness changed": lambda r: r["inputs"]["harness"].update(sha256="0" * 64),
        "other candidate commit": lambda r: r["plan"]["builds"]["candidate"].update(commit="abc1234"),
        "dirty tree": lambda r: r["environment"].update(git_dirty_paths=" M x"),
        "unbounded harness": lambda r: r["environment"]["cgroup"].update(cpu_max="max 100000"),
        "wrong mesh regions": lambda r: r["comparisons"]["mesh_d8_tendl"].update(cell_count=775),
        "warm-up failed": lambda r: r["warmup"]["candidate"].update(exit_code=1),
        "other scope before a problem": lambda r: r["before"][4].update(other_scopes=["run-x.scope"]),
        "other scope at the start": lambda r: r["environment"].update(other_scopes_start=["run-y.scope"]),
        "other scope at the end": lambda r: r["environment"].update(other_scopes_end=["run-y.scope"]),
        "busy machine": lambda r: [b.update(load_average=[5.0]) for b in r["before"]],
    }
    for name, mutate in cases.items():
        rec, repo = synthetic_record()
        mutate(rec)
        gates, _, sentences = evaluate(rec, repo)
        if not any(gates[g] for g in ("G1", "G2", "G3", "G4")):
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
        "protocol": "ACTINV-P182",
        "verdict": "P182-PASS (pending G5, CI on the evidence commit)" if passed else "P182-FAIL",
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
                print("G4: FAIL (verdict file differs from the recomputed summary)")
                passed = False
    elif args.record.resolve() == RECORD.resolve():
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("outcome:")
    for sentence in sentences:
        print(f"  {sentence}")
    print(verdict["verdict"] if passed else "P182-FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
