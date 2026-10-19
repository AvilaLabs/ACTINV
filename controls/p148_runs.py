#!/usr/bin/env python3
"""P148 R1–R3 on spec runs: the 132 FNS experiments and the long-history cases.

For each case the rate-pruned run (at every threshold) goes through `prune_probe`, and so do two unpruned
references (CRAM-16 and CRAM-48). Each rate run is recomputed independently (`check_p148.recompute`) and checked
for dominance against the CRAM-48 reference at every step:
  - every dropped state's reference population is at most its population bound;
  - every kept state's shortfall (reference minus pruned) is at most its shortfall bound;
  - the change in total activity and in total heat is at most the ledger's bound.
A difference is a violation only if it exceeds the bound by more than 1e-12 of the reference total plus the
CRAM-16/CRAM-48 difference of the same quantity on the unpruned problem.

Cases are independent: `--workers N` runs N at once (inside the caller's cgroup), and each finished case is saved
to OUT_DIR/cases/<case>.json so an interrupted batch resumes. The rows do not depend on the order or the workers.

Usage: p148_runs.py PROBE OUT_DIR [--only MAT_TAG ...] [--thresholds ...] [--workers N]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# run_fns_spec imports the Python binding at module level; only its spec builder is used here.
sys.modules.setdefault("actinv", types.ModuleType("actinv"))
os.environ.setdefault("ACTINV_LIBRARY", os.path.expanduser("~/nuclear-data/eaf-2010/actinv_eaf2010_709g.npz"))
import run_fns_spec  # noqa: E402
import check_p148  # noqa: E402

THRESHOLDS = [1e-12, 1e-10, 1e-8, 1e-6, 1e-4, 1e-2]
# R1 and R2 read inventories, activity, heat and the ledger only. Photon and pathway outputs do not change them
# (checked bit-for-bit on Dy 2000exp_5min) but cost 4 GB and 9 minutes per unpruned reference.
OUTPUTS = ["inventory", "activity", "heat", "ledger"]
SLACK = 1e-12


def fns_cases():
    root = Path(run_fns_spec.FNS)
    for mat in sorted(os.listdir(root)):
        for exp in sorted(root.glob(f"{mat}/*.exp")):
            yield f"{mat}_{exp.stem}", run_fns_spec.build_spec(mat, exp.stem)


def long_cases():
    """R3 long-history cases: FNS iron for one year at constant flux, then one year of cooling (coupled), and
    the same irradiation as twelve monthly pulses with gaps."""
    base = run_fns_spec.build_spec("Fe", "2000exp_5min")
    year = 365.25 * 86400.0
    steady = copy.deepcopy(base)
    steady["title"] = "P148 long: FNS iron spectrum, 1 y irradiation, 1 y cooling"
    steady["schedule"] = [{"dt": f"{year} s", "flux": 1.0}] + [
        {"dt": f"{dt} s", "flux": 0.0} for dt in (60.0, 3600.0, 86400.0, 30 * 86400.0, year)
    ]
    steady["options"]["mode"] = "coupled"
    pulsed = copy.deepcopy(steady)
    pulsed["title"] = "P148 long: FNS iron spectrum, 12 monthly pulses of 20 d, then 1 y cooling"
    pulsed["schedule"] = []
    for _ in range(12):
        pulsed["schedule"] += [{"dt": f"{20 * 86400.0} s", "flux": 1.0}, {"dt": f"{year / 12 - 20 * 86400.0} s", "flux": 0.0}]
    pulsed["schedule"] += [{"dt": f"{year} s", "flux": 0.0}]
    yield "long_fe_1y_steady", steady
    yield "long_fe_1y_pulsed", pulsed


def run_probe(probe: str, spec: dict, tmp: Path, label: str) -> dict:
    spec_path = tmp / f"{label}.spec.json"
    out = tmp / f"{label}.out.json"
    spec_path.write_text(json.dumps(spec))
    subprocess.run([probe, str(spec_path), str(out)], check=True, timeout=3600.0)
    data = json.loads(out.read_text())
    out.unlink()
    return data


def step_maps(result: dict):
    rows = []
    for s in result["steps"]:
        inv = {r["nuclide"]: r["atoms_per_g"] for r in s["inventory"]}
        rows.append((inv, sum(s["activity_Bq_per_g"].values()), s["heat_W_per_g"]["total"]))
    return rows


def dominance(rate: dict, ref48: dict, ref16: dict) -> tuple[list[str], dict]:
    cap = rate["captures"][-1]
    ledger = rate["result"]["ledger"]["rate_pruning"]
    names = cap["names"]
    dropped = {names[i]: check_p148.num(p) for i, p, _ in cap["dropped"]}
    shortfall = {names[i]: check_p148.num(e) for i, e in cap["kept_shortfall"]}
    kept = {names[i] for i in cap["keep"]}
    problems = []
    worst = {"activity_change_rel": 0.0, "heat_change_rel": 0.0, "activity_bound_rel": 0.0, "heat_bound_rel": 0.0,
             "activity_ratio_min": None, "heat_ratio_min": None}
    for k, ((p_inv, p_act, p_heat), (r_inv, r_act, r_heat), (c_inv, c_act, c_heat)) in enumerate(
        zip(step_maps(rate["result"]), step_maps(ref48["result"]), step_maps(ref16["result"]))
    ):
        total_atoms = sum(r_inv.values())
        for name, ref in r_inv.items():
            cram = abs(ref - c_inv.get(name, 0.0))
            tol = SLACK * total_atoms + cram
            if name in dropped and ref > dropped[name] + tol:
                problems.append(f"step {k} dropped {name}: {ref:.6e} > {dropped[name]:.6e}")
            if name in kept:
                gap = ref - p_inv.get(name, 0.0)
                if gap > shortfall.get(name, 0.0) + tol:
                    problems.append(f"step {k} kept {name}: shortfall {gap:.6e} > {shortfall.get(name, 0.0):.6e}")
            if name not in kept and name not in dropped and ref > tol:
                problems.append(f"step {k} {name} neither kept nor dropped but populated {ref:.6e}")
        for label, p, r, c, bound in (("activity", p_act, r_act, c_act, ledger["activity_Bq_per_g_bound"]),
                                      ("heat", p_heat, r_heat, c_heat, ledger["heat_W_per_g_bound"])):
            change = abs(r - p)
            if change > bound + SLACK * abs(r) + abs(r - c):
                problems.append(f"step {k} {label} change {change:.6e} > bound {bound:.6e}")
            if r > 0:
                worst[f"{label}_change_rel"] = max(worst[f"{label}_change_rel"], change / r)
                worst[f"{label}_bound_rel"] = max(worst[f"{label}_bound_rel"], bound / r)
            if change > 0:
                ratio = bound / change
                key = f"{label}_ratio_min"
                worst[key] = ratio if worst[key] is None else min(worst[key], ratio)
    return problems, worst


def run_case(probe: str, name: str, spec: dict, thresholds: list[float], tmp: Path) -> list[dict]:
    rows = []
    refs = {}
    for order in (16, 48):
        ref = copy.deepcopy(spec)
        ref["options"].update({"prune": "none", "cram_order": order, "outputs": OUTPUTS})
        refs[order] = run_probe(probe, ref, tmp, f"{name}_none{order}")
    for bmin in thresholds:
        rs = copy.deepcopy(spec)
        rs["options"].update({"prune": "rate", "bmin_atoms_per_g": bmin, "outputs": OUTPUTS})
        rate = run_probe(probe, rs, tmp, f"{name}_rate")
        cap = rate["captures"][-1]
        mine = check_p148.recompute(cap)
        r1 = check_p148.compare(cap, mine)
        ledger = rate["result"]["ledger"]["rate_pruning"]
        for key, value in check_p148.response_bounds(cap, mine).items():
            if not check_p148.close(float(ledger[key]), value):
                r1.append(f"ledger {key} {ledger[key]!r} vs {value!r}")
        r2, worst = dominance(rate, refs[48], refs[16])
        rows.append({"case": name, "bmin": bmin, "kept": len(cap["keep"]), "total_states": cap["n"],
                     "dropped": len(cap["dropped"]), "unbounded": len(cap["kept_unbounded"]),
                     "run_ms": rate["result"]["ms"], "reference_ms": refs[16]["result"]["ms"],
                     "activity_bound_Bq_per_g": ledger["activity_Bq_per_g_bound"],
                     "heat_bound_W_per_g": ledger["heat_W_per_g_bound"], **worst,
                     "r1_problems": r1[:10], "r2_problems": r2[:10]})
    return rows


def cached_case(probe: str, name: str, spec: dict, thresholds: list[float], cases_dir: Path) -> list[dict]:
    """One case's rows, from OUT_DIR/cases when an earlier run of the same spec and thresholds finished it."""
    key = {"spec": spec, "thresholds": thresholds, "outputs": OUTPUTS,
           "probe_sha256": hashlib.sha256(Path(probe).read_bytes()).hexdigest()}
    path = cases_dir / f"{name}.json"
    if path.is_file():
        saved = json.loads(path.read_text())
        if saved.get("key") == key:
            return saved["rows"]
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        rows = run_case(probe, name, spec, thresholds, Path(tmp))
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps({"key": key, "rows": rows}) + "\n")
    partial.replace(path)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("probe")
    ap.add_argument("out", type=Path)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--thresholds", nargs="*", type=float, default=THRESHOLDS)
    ap.add_argument("--no-long", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases_dir = args.out / "cases"
    cases_dir.mkdir(exist_ok=True)
    cases = list(fns_cases()) + ([] if args.no_long else list(long_cases()))
    if args.only:
        cases = [c for c in cases if c[0] in args.only]
    by_case = {}
    started = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(cached_case, args.probe, name, spec, args.thresholds, cases_dir): name
                   for name, spec in cases}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            name = futures[future]
            case_rows = future.result()
            by_case[name] = case_rows
            print(f"{done:3d}/{len(cases)} {name:24s} kept@1e-8 "
                  f"{next((r['kept'] for r in case_rows if r['bmin'] == 1e-8), '-')} "
                  f"r1 {sum(len(r['r1_problems']) for r in case_rows)} "
                  f"r2 {sum(len(r['r2_problems']) for r in case_rows)} "
                  f"{time.time() - started:.0f}s", file=sys.stderr, flush=True)
    rows = [row for name, _spec in cases for row in by_case[name]]
    summary = {}
    for bmin in args.thresholds:
        sel = [r for r in rows if r["bmin"] == bmin and r["case"].startswith(tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))]
        if sel:
            summary[f"{bmin:g}"] = {
                "fns_experiments": len(sel),
                "median_kept": statistics.median(r["kept"] for r in sel),
                "median_run_ms": statistics.median(r["run_ms"] for r in sel),
                "max_heat_change_rel": max(r["heat_change_rel"] for r in sel),
                "median_heat_change_rel": statistics.median(r["heat_change_rel"] for r in sel),
                "max_heat_bound_rel": max(r["heat_bound_rel"] for r in sel),
                "max_activity_change_rel": max(r["activity_change_rel"] for r in sel),
                "max_activity_bound_rel": max(r["activity_bound_rel"] for r in sel),
            }
    record = {
        "schema": "actinv-p148-runs-1",
        "thresholds": args.thresholds,
        "cases": sorted({r["case"] for r in rows}),
        "summary_fns": summary,
        "rows": rows,
        "r1_pass": all(not r["r1_problems"] for r in rows),
        "r2_pass": all(not r["r2_problems"] for r in rows),
    }
    (args.out / "runs.json").write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps({k: record[k] for k in ("r1_pass", "r2_pass", "summary_fns")}, indent=1))
    return 0 if record["r1_pass"] and record["r2_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
