#!/usr/bin/env python3
"""P163 harness (protocols/ACTINV-P163_PROTOCOL.md): ACTINV against OpenMC's flux mode on the steel-cube R2S demo.

Runs one OpenMC 0.15.3 R2S pipeline per (arm, mesh size, repetition) as a bounded child process and writes
results/p163_speed.json for controls/check_p163.py. Arms:

  actinv        ActinvR2SManager (contrib/openmc_r2s): OpenMC step 1 with the adapter's slim flux tally, `actinv mesh`
                for step 2, OpenMC step 3.
  openmc_flux   openmc.deplete.R2SManager with step 1 micro_kwargs = {reaction_rate_mode: "flux", energies: the 709
                group boundaries}: a multigroup flux tally, then cross sections collapsed from it for every chain
                nuclide; OpenMC's own step 2 (IndependentOperator + PredictorIntegrator) and step 3.
  openmc_default  openmc.deplete.R2SManager with no micro_kwargs (the arm recorded in results/openmc_r2s_demo.json:
                direct per-nuclide, per-reaction tallies in one energy group). Reference point, smallest size only.

Run it from the bounded scope AGENTS.md prescribes, with the OpenMC 0.15.3 interpreter:

  systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- \\
    env CARGO_BUILD_JOBS=1 RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 TMPDIR=$PWD/target/p163-tmp \\
    ~/micromamba/envs/openmc0153dd/bin/python controls/p163_harness.py --actinv-bin target/release/actinv \\
    --xs-archive-sha256 <sha256 of the ENDF/B-VIII.1 archive you extracted>

The harness refuses to start unless its cgroup shows MemoryMax <= 6 GiB, no swap and a CPU quota <= 200%. Every
child has a hard wall-clock limit; on expiry its whole process group is terminated, then killed, then reaped. The
orchestrator itself imports no OpenMC and holds no model. Nothing here is run by the protocol author; a first
execution is a development run (see the protocol, "Development runs").
"""
import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P163_PROTOCOL.md"
CONTRIB = ROOT / "contrib" / "openmc_r2s"
GROUPS_JSON = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
OUT_DEFAULT = ROOT / "results" / "p163_speed.json"
HOME = Path.home()

SCHEMA = "actinv-p163-speed-1"
THREADS = 2
PARTICLES = 20000
BATCHES = 5
SEED_BASE = 1000
TIMESTEPS = [365.25 * 86400.0, 1.0e6]
SOURCE_RATES = [1.0e17, 0.0]
PHOTON_TIME_INDEX = 2
# mesh cells -> mesh dimension per axis
DIMENSIONS = {8: 2, 64: 4, 512: 8}
# registered plan: repetitions per arm and size, and the hard wall-clock limit per run in seconds
REPS = {"actinv": {8: 3, 64: 3, 512: 3}, "openmc_flux": {8: 3, 64: 2, 512: 1}, "openmc_default": {8: 1}}
LIMIT_S = {"actinv": 900, "openmc_flux": 3600, "openmc_default": 3000}
ARM_ORDER = ["actinv", "openmc_flux", "openmc_default"]
GIB = 1 << 30
# Memory cap of each run's own scope, in GiB. OpenMC's flux mode loads every chain nuclide's cross sections for
# every region and needed more than 6 GiB at 8 cells (development run 1), so its arms get a larger cap.
MEMORY_MAX_GIB = {"actinv": 6, "openmc_flux": 9, "openmc_default": 9}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------- environment record

def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def cgroup_record():
    """Read-only inspection of the cgroup this process lives in (cgroup v2)."""
    rel = None
    text = read_text("/proc/self/cgroup")
    for line in (text or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            rel = parts[2]
    base = Path("/sys/fs/cgroup") / (rel or "").lstrip("/")
    return {"path": rel, "memory_max": read_text(base / "memory.max"), "memory_swap_max": read_text(base / "memory.swap.max"),
            "pids_max": read_text(base / "pids.max"), "cpu_max": read_text(base / "cpu.max")}


def cgroup_enforced(rec):
    """(ok, reason). Bytes <= 6 GiB, swap exactly 0, tasks <= 128, CPU quota <= 2 CPUs."""
    try:
        if int(rec["memory_max"]) > 6 * GIB:
            return False, "memory.max above 6 GiB"
        if int(rec["memory_swap_max"]) != 0:
            return False, "memory.swap.max not 0"
        if int(rec["pids_max"]) > 128:
            return False, "pids.max above 128"
        quota, period = rec["cpu_max"].split()
        if int(quota) * 1.0 / int(period) > 2.0 + 1e-9:
            return False, "cpu.max above 200%"
    except (TypeError, ValueError, KeyError, AttributeError):
        return False, "cgroup limits unreadable or unset"
    return True, "ok"


def lscpu_model():
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith("Model name"):
            return line.split(":", 1)[1].strip()
    return None


def mem_total_kib():
    for line in (read_text("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1])
    return None


def run_text(cmd, timeout=30):
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return (p.stdout + p.stderr).strip()[-400:]
    except (OSError, subprocess.SubprocessError) as e:
        return f"error: {e}"


def child_env(args, work):
    env = dict(os.environ)
    env.update({"OMP_NUM_THREADS": str(THREADS), "RAYON_NUM_THREADS": str(THREADS), "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1", "OPENMC_CROSS_SECTIONS": str(args.xs), "PYTHONHASHSEED": "0",
                "TMPDIR": str(work / "tmp"), "OPENMC_CHAIN_FILE": str(args.chain)})
    return env


# ---------------------------------------------------------------------------------------------- bounded child

def scoped(cmd, memory_gib):
    """`cmd` inside its own transient systemd scope: `memory_gib` of memory, no swap, 128 tasks, two CPUs. A run that
    exceeds its memory is killed alone; the harness, in its own scope, records it."""
    return ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={memory_gib}G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%", "--"] + [str(c) for c in cmd]


def run_child(cmd, env, cwd, limit_s):
    """Run `cmd` in its own session with a hard wall-clock limit. Returns (status, exit_code, wall_s, stderr_tail).
    On expiry the process group gets SIGTERM, ten seconds, then SIGKILL, and is always reaped."""
    t0 = time.monotonic()
    # `systemd-run --scope` moves itself into the new scope and execs the command, so the pid and process group
    # are the command's own
    p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         start_new_session=True)
    status = "ok"
    try:
        _, err = p.communicate(timeout=limit_s)
    except subprocess.TimeoutExpired:
        status = "timeout"
        for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 30)):
            try:
                os.killpg(p.pid, sig)
            except OSError:
                pass
            try:
                _, err = p.communicate(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                err = b""
        else:
            err = b""
        p.wait()
    wall = time.monotonic() - t0
    code = p.returncode
    if status == "ok" and code != 0:
        status = "killed" if code in (-9, -15, 137, 143) else "failed"
    return status, code, wall, (err or b"").decode(errors="replace")[-600:]


# ---------------------------------------------------------------------------------------------- the plan

def planned(smoke):
    """(arm, cells, rep) in execution order: repetitions interleave the arms so slow drift hits all of them."""
    if smoke:
        return [("actinv", 8, 0), ("openmc_flux", 8, 0)]
    runs = []
    for cells in sorted(DIMENSIONS):
        top = max(REPS[a].get(cells, 0) for a in ARM_ORDER)
        for rep in range(top):
            runs.extend((arm, cells, rep) for arm in ARM_ORDER if rep < REPS[arm].get(cells, 0))
    return runs


def median_h1(runs, arm, cells):
    vals = [r["step1_s"] + r["step2_s"] for r in runs if r["arm"] == arm and r["cells"] == cells and r["status"] == "ok"]
    return statistics.median(vals) if vals else None


def skip_rule(arm, cells, runs):
    """Registered rule: an OpenMC run at a larger size is attempted only if the previous size's median steps-1-2 time,
    scaled linearly by the cell ratio, fits the run's limit. Returns None to run, else the recorded decision."""
    if arm != "openmc_flux" or cells == 8:
        return None
    prev = {64: 8, 512: 64}[cells]
    base = median_h1(runs, arm, prev)
    if base is None:
        return {"rule": "previous size has no completed run", "projected_s": None, "limit_s": LIMIT_S[arm]}
    projected = base * cells / prev
    if projected > LIMIT_S[arm]:
        return {"rule": "linear projection above the limit", "projected_s": projected, "limit_s": LIMIT_S[arm]}
    return None


def write_atomic(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def orchestrate(args):
    cg = cgroup_record()
    ok, why = cgroup_enforced(cg)
    if not ok:
        sys.exit(f"p163_harness: refusing to run: {why} ({cg}). Launch inside the bounded systemd-run scope.")
    work = Path(args.work).resolve()
    if work.exists():
        shutil.rmtree(work)
    (work / "tmp").mkdir(parents=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir)
    files = {
        "chain_xml": args.chain, "cross_sections_xml": args.xs, "actinv_library": args.library,
        "decay_primary": args.decay_primary, "decay_fallback": args.decay_fallback, "actinv_binary": args.actinv_bin,
        "groups_json": GROUPS_JSON, "harness": Path(__file__).resolve(), "protocol": PROTOCOL,
        "demo_steel_cube": CONTRIB / "demo_steel_cube.py", "adapter": CONTRIB / "actinv_openmc_r2s.py",
    }
    inputs = {}
    for k, v in files.items():
        v = Path(v)
        if not v.is_file():
            sys.exit(f"p163_harness: input missing: {k} = {v}")
        inputs[k] = {"path": str(v), "sha256": sha256_file(v)}
    probe_cmd = [sys.executable, str(Path(__file__).resolve()), "--probe"]
    probe = json.loads(subprocess.run(probe_cmd, capture_output=True, text=True, timeout=300,
                                      env=child_env(args, work)).stdout or "{}")
    record = {
        "schema": SCHEMA, "smoke": bool(args.smoke), "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "cpu_model": lscpu_model(), "logical_cpus": os.cpu_count(), "mem_total_kib": mem_total_kib(),
            "kernel": platform.release(), "platform": platform.platform(), "python": sys.version.split()[0],
            "python_executable": sys.executable, "openmc_version": probe.get("openmc_version"),
            "numpy_version": probe.get("numpy_version"), "cgroup": cg,
            "env": {k: os.environ.get(k) for k in ("RAYON_NUM_THREADS", "OMP_NUM_THREADS", "TMPDIR")},
            "child_threads": THREADS, "git_head": run_text(["git", "rev-parse", "HEAD"]),
            "git_dirty_paths": run_text(["git", "status", "--porcelain", "--", "controls", "contrib", "protocols",
                                         "crates"]),
            "actinv_version": run_text([args.actinv_bin, "--version"]),
            "xs_archive_sha256": args.xs_archive_sha256, "load_average_start": list(os.getloadavg()),
        },
        "inputs": inputs,
        "plan": {"particles": PARTICLES, "batches": BATCHES, "seed_base": SEED_BASE, "timesteps_s": TIMESTEPS,
                 "source_rates": SOURCE_RATES, "photon_time_index": PHOTON_TIME_INDEX, "dimensions": DIMENSIONS,
                 "reps": {a: {str(k): v for k, v in d.items()} for a, d in REPS.items()}, "limit_s": LIMIT_S,
                 "memory_max_gib": MEMORY_MAX_GIB},
        "attempts": [], "skips": [],
    }
    write_atomic(out, record)
    runs_done = []
    for arm, cells, rep in planned(args.smoke):
        decision = skip_rule(arm, cells, runs_done)
        if decision is not None:
            record["skips"].append({"arm": arm, "cells": cells, "rep": rep, **decision})
            write_atomic(out, record)
            continue
        rundir = work / f"{arm}_{cells}_{rep}"
        rundir.mkdir()
        result = rundir / "result.json"
        cmd = [sys.executable, str(Path(__file__).resolve()), "--child", "--arm", arm, "--cells", str(cells),
               "--rep", str(rep), "--out", str(result), "--rundir", str(rundir), "--actinv-bin", args.actinv_bin,
               "--library", args.library, "--decay-primary", args.decay_primary,
               "--decay-fallback", args.decay_fallback, "--chain", args.chain, "--xs", args.xs]
        status, code, wall, err = run_child(scoped(cmd, MEMORY_MAX_GIB[arm]), child_env(args, work), rundir,
                                            LIMIT_S[arm])
        entry = {"arm": arm, "cells": cells, "rep": rep, "status": status, "exit_code": code, "wall_s": wall,
                 "limit_s": LIMIT_S[arm], "memory_max_gib": MEMORY_MAX_GIB[arm],
                 "stderr_tail": err if status != "ok" else ""}
        if result.is_file():
            try:
                entry.update(json.loads(result.read_text()))
            except ValueError:
                entry["status"] = "failed"
        if entry["status"] == "ok" and "step1_s" not in entry:
            entry["status"] = "failed"
        if entry["status"] in ("timeout", "killed"):
            # right-censored: the run needed at least `wall_s` (the limit, or the time of the cgroup kill)
            entry.update({"censored": True, "step1_s": None, "step2_s": None, "step3_s": None})
        record["attempts"].append(entry)
        if entry["status"] == "ok":
            runs_done.append(entry)
        shutil.rmtree(rundir, ignore_errors=True)
        write_atomic(out, record)
    record["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record["environment"]["load_average_end"] = list(os.getloadavg())
    write_atomic(out, record)
    print(f"p163_harness: wrote {out} ({len(record['attempts'])} runs, {len(record['skips'])} skipped by rule)")
    return 0


# ---------------------------------------------------------------------------------------------- child (one run)

def probe():
    import numpy
    import openmc
    print(json.dumps({"openmc_version": openmc.__version__, "numpy_version": numpy.__version__}))
    return 0


def child(args):
    import struct

    import numpy as np
    import openmc
    import openmc.deplete
    from openmc.deplete import pool

    pool.NUM_PROCESSES = THREADS
    openmc.config["chain_file"] = args.chain
    openmc.config["cross_sections"] = args.xs
    sys.path.insert(0, str(CONTRIB))
    import demo_steel_cube as demo  # noqa: E402
    from actinv_openmc_r2s import ActinvR2SManager, library_bounds  # noqa: E402

    rundir = Path(args.rundir)
    dim = DIMENSIONS[args.cells]
    seed = SEED_BASE + args.rep
    bounds = np.asarray(library_bounds(args.library), dtype=float)
    ref = np.sort(np.asarray(json.loads(GROUPS_JSON.read_text())["boundaries_eV"], dtype=float))
    bounds_rel = float(np.max(np.abs(bounds - ref) / ref)) if bounds.shape == ref.shape else float("inf")
    bounds_sha = hashlib.sha256(struct.pack(f"<{len(ref)}d", *ref)).hexdigest()

    model, mesh, det = demo.build_model(PARTICLES)
    model.settings.seed = seed
    mesh.dimension = (dim, dim, dim)
    pm = demo.photon_model(model, det, PARTICLES)
    pm.settings.seed = seed + 500

    micro_kwargs = None
    if args.arm == "actinv":
        mgr = ActinvR2SManager(model, mesh, pm, library=args.library, decay_primary=args.decay_primary,
                               decay_fallback=args.decay_fallback, actinv_bin=args.actinv_bin, threads=THREADS)
        mode = "adapter-slim (one throwaway nuclide, (n,gamma), flux tally on the library groups)"
    elif args.arm == "openmc_flux":
        mgr = openmc.deplete.R2SManager(model, mesh, pm)
        micro_kwargs = {"reaction_rate_mode": "flux", "energies": [float(x) for x in bounds]}
        mode = "flux"
    else:
        mgr = openmc.deplete.R2SManager(model, mesh, pm)
        mode = "direct (OpenMC defaults)"

    # what this harness passes; R2SManager adds its own file paths (path_input, path_statepoint) to the same dict
    passed_keys = sorted(micro_kwargs or {})

    # timing boundaries (protocol "Timing"): each step is bracketed by monotonic clock reads, nothing else is inside.
    t0 = time.monotonic()
    mgr.step1_neutron_transport(output_dir=rundir / "neutron", micro_kwargs=micro_kwargs)
    t1 = time.monotonic()
    mgr.step2_activation(TIMESTEPS, SOURCE_RATES, output_dir=rundir / "activation")
    t2 = time.monotonic()
    mgr.step3_photon_transport(time_indices=[PHOTON_TIME_INDEX], output_dir=rundir / "photon")
    t3 = time.monotonic()

    # the run's own scope: its limits, and the most memory the run and its children held at once
    cg = cgroup_record()
    peak = read_text(Path("/sys/fs/cgroup") / (cg["path"] or "").lstrip("/") / "memory.peak")
    mean, sd = demo.dose_pSv_s(mgr, PHOTON_TIME_INDEX)
    strength = sum(src.strength for ms in mgr.get_decay_photon_source_mesh(PHOTON_TIME_INDEX) for src in ms.sources)
    micros = mgr.results["micros"]
    fluxes = mgr.results["fluxes"]
    flux_arr = [np.asarray(f, dtype=float).ravel() for f in fluxes]
    out = {
        "step1_s": t1 - t0, "step2_s": t2 - t1, "step3_s": t3 - t2,
        "dose_per_photon_pSv_cm2": mean, "dose_sd": sd, "photons_per_s": strength,
        "dose_rate_Sv_h": mean / demo.DET_VOLUME_CM3 * 3600.0 * 1e-12,
        "config": {
            "openmc_version": openmc.__version__, "arm": args.arm, "step1_mode": mode,
            "reaction_rate_mode": (micro_kwargs or {}).get("reaction_rate_mode", "direct"),
            "micro_kwargs_keys": passed_keys,
            "energy_groups_in_flux": int(flux_arr[0].size), "n_regions": len(fluxes),
            "n_micro_nuclides": len(micros[0].nuclides), "n_micro_reactions": len(micros[0].reactions),
            "flux_sum_n_cm_per_src": float(sum(f.sum() for f in flux_arr)),
            "bounds_sha256_ascending": bounds_sha, "bounds_max_rel_diff_vs_groups_json": bounds_rel,
            "particles": PARTICLES, "batches": BATCHES, "seed": seed, "photon_seed": seed + 500,
            "threads": THREADS, "pool_processes": THREADS, "mesh_dimension": [dim, dim, dim],
            "timesteps_s": TIMESTEPS, "source_rates": SOURCE_RATES,
            "photon_time_index": PHOTON_TIME_INDEX, "photon_particles": PARTICLES,
        },
        "cgroup": cg, "memory_peak_bytes": int(peak) if peak and peak.isdigit() else None,
        "peak_rss_mib": {"self": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
                         "children": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0},
    }
    Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true", help="print the OpenMC version (internal)")
    ap.add_argument("--child", action="store_true", help="run one arm/size/rep (internal)")
    ap.add_argument("--smoke", action="store_true", help="development run: 8 cells, one repetition, no default arm")
    ap.add_argument("--arm", choices=ARM_ORDER)
    ap.add_argument("--cells", type=int, choices=sorted(DIMENSIONS))
    ap.add_argument("--rep", type=int, default=0)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--rundir")
    ap.add_argument("--work", default=str(ROOT / "target" / "p163-work"))
    ap.add_argument("--actinv-bin", default=os.environ.get("ACTINV_BIN", str(ROOT / "target" / "release" / "actinv")))
    ap.add_argument("--data-dir", default=os.environ.get("ACTINV_DATA_DIR", str(HOME / "Documents/actinv/actinv-data/v1.1.0")))
    ap.add_argument("--library")
    ap.add_argument("--decay-primary")
    ap.add_argument("--decay-fallback")
    ap.add_argument("--chain", default=str(HOME / "nuclear-data/p32-work/chain/depletion/chain.xml"))
    ap.add_argument("--xs", default=str(HOME / "nuclear-data/endfb-viii.1-hdf5/cross_sections.xml"))
    ap.add_argument("--xs-archive-sha256", default=None)
    args = ap.parse_args(argv)
    d = Path(args.data_dir)
    args.library = args.library or str(d / "activation" / "tendl-2025-neutron-709g.npz")
    args.decay_primary = args.decay_primary or str(d / "decay" / "endf-b-viii-0_decay.dat")
    args.decay_fallback = args.decay_fallback or str(d / "decay" / "jeff-3-3_decay.dat")
    if args.probe:
        return probe()
    if args.child:
        return child(args)
    return orchestrate(args)


if __name__ == "__main__":
    sys.exit(main())
