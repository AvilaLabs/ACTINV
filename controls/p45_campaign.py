#!/usr/bin/env python3
"""P45 sealed timing harness — executes both frozen workloads on every
measured arm and records a resumable per-case ledger.

Usage:
  python3 controls/p45_campaign.py [--workload campaign|mesh]
                                  [--arms alara,actinv_fendl,...]
                                  [--repeat N] [--resume]

Every case is timed per arm: per-invocation arms (alara, actinv_fendl,
actinv_tendl) run `REPEAT` subprocess invocations and report
median-of-repeats as `warm_s` with the first as `cold_s`; batch arms
(openmc) run once per workload inside a TemporarySession and report
session_init + per-case deplete_s. Per-arm failures are ledgered
(`arm_failure`), never retried silently. All runs happen inside the
caller's cgroup.

Ledger: results/p45_ledger.jsonl (append-only; --resume skips
completed case×arm rows whose input digest still matches).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import g2_p26b_leg as leg  # noqa: E402  (sealed ALARA/ACTINV drivers)
from g2_p26b_contract import sha256_file, sha256_array  # noqa: E402

POPULATION = ROOT / "results" / "p45_population.json"
LEDGER = ROOT / "results" / "p45_ledger.jsonl"
WORK = Path(os.environ.get("ACTINV_P45_WORK",
                           Path.home() / "nuclear-data" / "p45-work"))

ACTINV = leg.ACTINV
ALARA = leg.ALARA
OPENMC_PY = Path("/home/connoravila/micromamba/envs/openmc016/bin/python")
OPENMC_DRIVER = ROOT / "controls" / "p45_openmc_driver.py"
CHAIN_XML = Path.home() / "nuclear-data" / "p32-work" / "chain" / \
    "depletion" / "chain.xml"
XS_XML = Path.home() / "nuclear-data" / "endfb-viii.1-hdf5" / \
    "cross_sections.xml"
BOUNDS = ROOT / "crates" / "actinv-data" / "data" / \
    "fispact_709_groups.json"
TENDL_NPZ = ROOT / "actinv-data" / "v1.1.0" / "activation" / \
    "tendl-2025-patched-neutron-709g.npz"
FENDL_NPZ = leg.ACTINV_FENDL_NPZ
ELELIB = leg.ELELIB
IDX = leg.ALARA_LIB_BASE.with_suffix(".idx")
COOL_DT_S = leg.COOL_DT_S
MESH_IRR_S = 300.0
PER_CASE_TIMEOUT_S = 300.0
OPENMC_BATCH_TIMEOUT_S = 5400.0
REACH_DEPTH = 3

INVOCATION_ARMS = ("alara", "actinv_fendl", "actinv_tendl")
ALL_ARMS = INVOCATION_ARMS + ("openmc",)
MESH_ARMS = ("alara", "openmc", "actinv_mesh")


def build_mesh_spec(cells: list[dict], work_dir: Path) -> Path:
    """Write the flux ndjson + mesh spec for the 16-cell workload."""
    bounds_desc = json.loads(BOUNDS.read_text())["boundaries_eV"]
    bounds_asc = list(reversed(bounds_desc))
    flux_path = work_dir / "flux.ndjson"
    with flux_path.open("w") as fh:
        fh.write(json.dumps({
            "record": "header", "schema": "actinv-flux-1",
            "source": {
                "format": "p45-synthetic",
                "path": str(POPULATION),
                "sha256": sha256_file(POPULATION),
                "metadata": {"recipe": "fns_709*(1+0.5*sin(2*pi*i*g/709"
                                       "+i*0.7)) rescaled to fns total"}},
            "energy_boundaries_eV": bounds_asc,
            "flux_units": "n cm^-2 s^-1",
            "cell_count": len(cells)}) + "\n")
        flux_sum = 0.0
        for c in cells:
            flux_asc = list(reversed(c["flux_descending"]))
            flux_sum += sum(flux_asc)
            fh.write(json.dumps({
                "record": "cell", "ordinal": c["cell"],
                "id": str(c["cell"]), "volume_cm3": 1.0,
                "flux_per_group": flux_asc,
                "flux_total": sum(flux_asc)}) + "\n")
        fh.write(json.dumps({
            "record": "footer", "cell_count": len(cells),
            "flux_sum_over_cells": flux_sum,
            "volume_integrated_flux": flux_sum}) + "\n")
    spec = {
        "spec": "actinv-mesh-spec-1",
        "title": "P45 distinct-spectrum mesh workload",
        "projectile": "neutron",
        "library": {"path": str(TENDL_NPZ),
                    "sha256": sha256_file(TENDL_NPZ)},
        "decay": {"primary": str(leg.DECAY_PRIMARY),
                  "fallback": str(leg.DECAY_FALLBACK)},
        "material": {"mass_g": 1.0, "basis": "wt_percent",
                     "composition": {"FE": 100.0}},
        "schedule": ([{"dt": "300 s", "flux": 1.0}]
                     + [{"dt": f"{dt} s", "flux": 0.0}
                        for dt in COOL_DT_S]),
        "flux": {"path": str(flux_path),
                 "sha256": sha256_file(flux_path)},
        "options": {"mode": "auto", "prune": "rate",
                    "bmin_atoms_per_g": 1e-08,
                    "temperature_K": 293.6},
        "chunk_cells": 4, "threads": 2,
    }
    spec_path = work_dir / "mesh_spec.json"
    spec_path.write_text(json.dumps(spec))
    return spec_path


def run_actinv_mesh(cells: list[dict], work_dir: Path,
                    repeat: int) -> dict:
    spec_path = build_mesh_spec(cells, work_dir)
    runs = []
    for i in range(repeat):
        out = work_dir / f"mesh_result_{i}.ndjson"
        r = leg.timed_run([str(ACTINV), "mesh", str(spec_path), str(out)],
                          work_dir, timeout=OPENMC_BATCH_TIMEOUT_S)
        rec = {"invocation": i, "wall_s": r["wall_s"],
               "returncode": r["returncode"],
               "out_sha256": sha256_file(out) if out.exists() else None}
        if r["returncode"] != 0:
            rec["failure"] = r.get("failure") or "actinv_mesh_error"
        runs.append(rec)
    walls = sorted(x["wall_s"] for x in runs)
    out = {"status": "executed" if all(
               x["returncode"] == 0 for x in runs) else "arm_failure",
           "cold_s": runs[0]["wall_s"],
           "warm_s": walls[len(walls) // 2],
           "runs": runs,
           "spec_sha256": sha256_file(spec_path)}
    # per-cell results from the last invocation's output
    last = work_dir / f"mesh_result_{len(runs) - 1}.ndjson"
    if last.exists() and out["status"] == "executed":
        per_cell = {}
        try:
            for line in last.read_text().splitlines():
                rec = json.loads(line)
                if rec.get("record") != "cell":
                    continue
                name = f"cell_{rec['ordinal']:02d}"
                res = rec.get("result") or {}
                cdir = work_dir / name
                cdir.mkdir(exist_ok=True)
                (cdir / "out.json").write_text(json.dumps(res))
                per_cell[name] = leg.parse_actinv_result(
                    cdir / "out.json", MESH_IRR_S)
            out["per_cell"] = per_cell
        except Exception as e:
            out["cell_parse_failure"] = repr(e)
    return out


def context() -> dict:
    return {
        "host": platform.node(), "machine": platform.machine(),
        "kernel": platform.release(), "python": platform.python_version(),
        "resource_limits": {"MemoryMax": "6G", "MemorySwapMax": 0,
                            "TasksMax": 128, "CPUQuota": "200%",
                            "jobs_at_once": 1},
        "identities": {
            "actinv": {"path": str(ACTINV),
                       "sha256": sha256_file(ACTINV)},
            "alara": {"path": str(ALARA),
                      "sha256": sha256_file(ALARA)},
            "openmc_python": {"path": str(OPENMC_PY),
                              "sha256": sha256_file(OPENMC_PY)},
            "openmc_driver": {"path": str(OPENMC_DRIVER),
                              "sha256": sha256_file(OPENMC_DRIVER)},
            "chain_xml": {"path": str(CHAIN_XML),
                          "sha256": sha256_file(CHAIN_XML)},
            "xs_xml": {"path": str(XS_XML),
                       "sha256": sha256_file(XS_XML)},
            "bounds": {"path": str(BOUNDS),
                       "sha256": sha256_file(BOUNDS)},
            "tendl_npz": {"path": str(TENDL_NPZ),
                          "sha256": sha256_file(TENDL_NPZ)},
            "fendl_npz": {"path": str(FENDL_NPZ),
                          "sha256": sha256_file(FENDL_NPZ)},
            "fendl_lib": {"path": str(leg.ALARA_LIB_BASE) + ".lib",
                          "sha256": sha256_file(
                              str(leg.ALARA_LIB_BASE) + ".lib")},
            "alara_dmp_base": {"path": str(leg.ALARA_LIB_BASE) + ".dmp",
                               "sha256": sha256_file(
                                   str(leg.ALARA_LIB_BASE) + ".dmp")},
            "elelib": {"path": str(ELELIB),
                       "sha256": sha256_file(ELELIB)},
            "decay_primary": {"path": str(leg.DECAY_PRIMARY),
                              "sha256": sha256_file(leg.DECAY_PRIMARY)},
            "decay_fallback": {"path": str(leg.DECAY_FALLBACK),
                               "sha256": sha256_file(
                                   leg.DECAY_FALLBACK)},
        },
    }


def digest_case_arm(case_name: str, arm: str, inputs: dict) -> str:
    h = hashlib.sha256()
    h.update(case_name.encode())
    h.update(arm.encode())
    for k in sorted(inputs):
        h.update(f"{k}={inputs[k]}".encode())
    return h.hexdigest()


def load_ledger() -> dict[str, dict]:
    done = {}
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[f"{r['workload']}|{r['case']}|{r['arm']}"] = r
    return done


def append(row: dict) -> None:
    with LEDGER.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def run_actinv(case: dict, arm: str, case_dir: Path,
               repeat: int) -> dict:
    lib = FENDL_NPZ if arm == "actinv_fendl" else TENDL_NPZ
    lib_key = "fendl_npz" if arm == "actinv_fendl" else "tendl_npz"
    flux = leg.fns_spectrum() if case["spectrum"] == "fns_709" \
        else case["flux_descending"]
    spec = leg.build_actinv_spec(
        case, flux=flux, descending=True,
        lib=lib, lib_sha=sha256_file(lib))
    spec_path = case_dir / "spec.json"
    spec_path.write_text(json.dumps(spec))
    runs = []
    for i in range(repeat):
        out = case_dir / f"out_{i}.json"
        r = leg.timed_run([str(ACTINV), "run", str(spec_path), str(out)],
                          case_dir, timeout=PER_CASE_TIMEOUT_S)
        rec = {"invocation": i, "wall_s": r["wall_s"],
               "returncode": r["returncode"]}
        if r["returncode"] == 0 and out.exists():
            res = leg.parse_actinv_result(out, case["irradiation_s"])
            rec["result"] = res
            rec["out_sha256"] = sha256_file(out)
            try:
                rec["internal_ms"] = json.loads(
                    out.read_text()).get("ms")
            except Exception:
                pass
        else:
            rec["failure"] = r.get("failure") or "actinv_error"
        runs.append(rec)
    walls = sorted(x["wall_s"] for x in runs)
    return {"status": "executed" if all(
                x["returncode"] == 0 for x in runs) else "arm_failure",
            "cold_s": runs[0]["wall_s"],
            "warm_s": walls[len(walls) // 2],
            "runs": runs,
            "result": runs[0].get("result"),
            "internal_ms": runs[0].get("internal_ms"),
            "spec_sha256": sha256_file(spec_path),
            "lib_key": lib_key}


def run_alara(case: dict, case_dir: Path, elelib: dict, hl: dict,
              flux_desc: list[float], repeat: int) -> dict:
    runs = []
    for i in range(repeat):
        cdir = case_dir if i == 0 else case_dir.with_name(
            case_dir.name + f"_rep{i}")
        cdir.mkdir(parents=True, exist_ok=True)
        io = leg.write_alara_case(cdir, case, flux_desc, elelib)
        r = leg.timed_run([str(ALARA), "case.in"], cdir,
                          timeout=PER_CASE_TIMEOUT_S)
        (cdir / "case.stdout").write_text(r.get("stdout", ""))
        rec = {"invocation": i, "wall_s": r["wall_s"],
               "returncode": r["returncode"], "input_sha256": io}
        if r["returncode"] == 0:
            try:
                rec["result"] = leg.parse_alara_result(
                    cdir, hl,
                    n_roots=len(case["required_isotopes"]["alara"]))
            except Exception as e:
                rec["failure"] = f"alara_output_parse: {e!r}"
        else:
            rec["failure"] = r.get("failure") or "alara_error"
        runs.append(rec)
    walls = sorted(x["wall_s"] for x in runs)
    ok = all(x["returncode"] == 0 for x in runs) \
        and not runs[0].get("failure")
    return {"status": "executed" if ok else "arm_failure",
            "cold_s": runs[0]["wall_s"],
            "warm_s": walls[len(walls) // 2],
            "runs": runs,
            "result": runs[0].get("result")}


def openmc_job(workload: str, cases: list[dict],
               flux_by_name: dict[str, dict], work_dir: Path) -> dict:
    """Batch all `cases` through the OpenMC driver in one session."""
    groups = [{"name": n, "flux_descending": f["flux_descending"],
               "total_flux": f["total_flux"]}
              for n, f in flux_by_name.items()]
    job_cases = []
    for c in cases:
        ts = [c["irradiation_s"]] + COOL_DT_S
        sr = [flux_by_name[c["group"]]["total_flux"]] \
            + [0.0] * len(COOL_DT_S)
        job_cases.append({
            "case": c["case"], "group": c["group"],
            "composition_isotope_fraction":
                c["composition_isotope_fraction"],
            "timesteps_s": ts, "source_rates": sr})
    job = {"work_dir": str(work_dir), "chain_file": str(CHAIN_XML),
           "bounds_file": str(BOUNDS), "reach_depth": REACH_DEPTH,
           "groups": groups, "cases": job_cases}
    jpath = work_dir / "openmc_job.json"
    jpath.write_text(json.dumps(job))
    env = dict(os.environ,
               OPENMC_CROSS_SECTIONS=str(XS_XML))
    t0 = time.monotonic()
    proc = subprocess.run(
        [str(OPENMC_PY), str(OPENMC_DRIVER), str(jpath)],
        cwd=work_dir, env=env, capture_output=True, text=True,
        timeout=OPENMC_BATCH_TIMEOUT_S)
    wall = time.monotonic() - t0
    (work_dir / "openmc_driver.stdout").write_text(proc.stdout)
    (work_dir / "openmc_driver.stderr").write_text(proc.stderr)
    results_path = work_dir / "openmc_results.json"
    out = {"batch_wall_s": wall, "returncode": proc.returncode,
           "job_sha256": sha256_file(jpath)}
    if results_path.exists():
        out["results_sha256"] = sha256_file(results_path)
        out["results"] = json.loads(results_path.read_text())
    else:
        out["failure"] = f"driver_no_results rc={proc.returncode}"
    return out


def expand_composition(wt: dict, elelib: dict) -> dict:
    """wt_fraction elements -> isotope fractions via elelib abundances."""
    comp = {}
    for el, wf in wt.items():
        sym = el.capitalize()
        info = elelib.get(sym.upper()) or elelib.get(sym)
        if info is None:
            raise KeyError(f"elelib lacks element {el}")
        for A, frac in info["isotopes"].items():
            comp[f"{sym}{A}"] = wf * frac
    return comp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workload", choices=["campaign", "mesh", "all"],
                    default="all")
    ap.add_argument("--arms", default=",".join(ALL_ARMS))
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--cases", default=None,
                    help="comma-separated case names (development "
                         "feasibility runs only; G3 runs all)")
    args = ap.parse_args()

    pop = json.loads(POPULATION.read_text())
    arms = args.arms.split(",")
    done = load_ledger() if args.resume else {}
    elelib = leg.elelib_parse()
    hl = leg.idx_parse(IDX)
    fns = leg.fns_spectrum()

    workloads = ["campaign", "mesh"] if args.workload == "all" \
        else [args.workload]
    t_all = time.monotonic()
    for wl in workloads:
        if wl == "campaign":
            cases = pop["campaign"]["cases"]
            wl_arms = [a for a in arms if a != "actinv_mesh"]
        else:
            cases = pop["mesh"]["cells"]
            wl_arms = [a for a in arms if a in MESH_ARMS]
        if args.cases:
            keep = set(args.cases.split(","))
            cases = [c for c in cases if c["case"] in keep]

        for case in cases:
            cname = case["case"]
            for arm in wl_arms:
                if arm not in INVOCATION_ARMS:
                    continue            # batch arms handled below
                key = f"{wl}|{cname}|{arm}"
                dg = digest_case_arm(cname, arm, {"arm": arm})
                if key in done and done[key].get("input_digest") == dg:
                    continue
                row = {"workload": wl, "case": cname, "arm": arm,
                       "input_digest": dg,
                       "recorded_at": time.time()}
                case_dir = WORK / wl / cname / arm
                case_dir.mkdir(parents=True, exist_ok=True)
                try:
                    if arm == "alara":
                        flux = fns if wl == "campaign" \
                            else case["flux_descending"]
                        res = run_alara(case, case_dir, elelib, hl,
                                        flux, args.repeat)
                    else:
                        res = run_actinv(case, arm, case_dir,
                                         args.repeat)
                    row["arms"] = {arm: res}
                except Exception as e:
                    row["arms"] = {arm: {"status": "arm_failure",
                                         "reason": repr(e)}}
                append(row)

        # actinv_mesh batch leg (one `actinv mesh` invocation)
        if wl == "mesh" and "actinv_mesh" in arms:
            wdir = WORK / "mesh" / "_actinv_mesh"
            wdir.mkdir(parents=True, exist_ok=True)
            key = "mesh|_batch|actinv_mesh"
            dg = digest_case_arm("_batch", "actinv_mesh", {"wl": "mesh"})
            if not (key in done and done[key].get("input_digest") == dg):
                res = run_actinv_mesh(cases, wdir, args.repeat)
                append({"workload": "mesh", "case": "_batch",
                        "arm": "actinv_mesh", "input_digest": dg,
                        "arms": {"actinv_mesh": res},
                        "recorded_at": time.time()})

        # openmc batch leg (one invocation per workload)
        if "openmc" in arms:
            if wl == "campaign":
                jcases = []
                for c in cases:
                    cc = dict(c)
                    cc["group"] = "fns_709"
                    cc["composition_isotope_fraction"] = \
                        expand_composition(
                            c["composition_wt_fraction"], elelib)
                    jcases.append(cc)
                flux_by_name = {"fns_709": {
                    "flux_descending": fns, "total_flux": sum(fns)}}
            else:
                jcases = []
                for c in cases:
                    cc = dict(c)
                    cc["group"] = cc["case"]
                    cc["composition_isotope_fraction"] = \
                        expand_composition(
                            c["composition_wt_fraction"], elelib)
                    jcases.append(cc)
                flux_by_name = {cc["case"]: {
                    "flux_descending": c["flux_descending"],
                    "total_flux": sum(c["flux_descending"])}
                    for cc, c in zip(jcases, cases)}
            wdir = WORK / wl / "_openmc_batch"
            wdir.mkdir(parents=True, exist_ok=True)
            key = f"{wl}|_batch|openmc"
            dg = digest_case_arm("_batch", "openmc", {"wl": wl})
            if not (key in done and done[key].get("input_digest") == dg):
                res = openmc_job(wl, jcases, flux_by_name, wdir)
                append({"workload": wl, "case": "_batch", "arm": "openmc",
                        "input_digest": dg,
                        "arms": {"openmc": res},
                        "recorded_at": time.time()})
            else:
                res = done[key]["arms"]["openmc"]
            # per-case rows so the ledger shape matches other arms
            # (also repairs torn ledgers: batch done, case rows lost)
            cases_res = (res.get("results") or {}).get("cases", {})
            for c in jcases:
                cn = c["case"]
                ckey = f"{wl}|{cn}|openmc"
                if ckey in done:
                    continue
                row = {"workload": wl, "case": cn, "arm": "openmc",
                       "input_digest": digest_case_arm(cn, "openmc",
                                                     {}),
                       "recorded_at": time.time(),
                       "arms": {"openmc": cases_res.get(cn, {
                           "status": "arm_failure",
                           "reason": "absent from batch results"})}}
                append(row)
                done[ckey] = row

    print(json.dumps({"wall_s": time.monotonic() - t_all,
                      "workloads": workloads, "arms": arms}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
