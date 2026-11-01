#!/usr/bin/env python3
"""P181 harness (protocols/ACTINV-P181_PROTOCOL.md): ACTINV against OpenMC's flux mode on a mixed-material mesh as the
element count rises, inside the same memory limit.

Runs one OpenMC 0.15.3 R2S pipeline per (arm, mesh size) as a bounded child process and writes
results/p181_scale.json for controls/check_p181.py. Arms:

  actinv        actinv.openmc.ActinvR2SManager (python/pysrc/actinv/openmc.py at the checked-out commit): OpenMC
                step 1 with a flux-only tally on the library groups, `actinv mesh` for step 2, OpenMC step 3.
  openmc_flux   openmc.deplete.R2SManager with step 1 micro_kwargs = {reaction_rate_mode: "flux", energies: the 709
                group boundaries}, OpenMC's own step 2 (IndependentOperator + PredictorIntegrator) and step 3: P163's
                arm, unchanged.

The model has a 14.1 MeV point source at the centre of a tungsten sphere inside spherical shells of CuCrZr and water,
in SS316 steel, which fills the rest of a 20 cm cube; the activation mesh spans the cube. The shell surfaces cut
mesh elements at every size, so OpenMC splits elements by material and the activation regions outnumber the elements.

Run it from the bounded scope AGENTS.md prescribes, with the OpenMC 0.15.3 interpreter:

  systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- \\
    env RAYON_NUM_THREADS=2 TMPDIR=$PWD/target/p181-tmp \\
    ~/micromamba/envs/openmc0153dd/bin/python controls/p181_harness.py --actinv-bin ACTINV \\
    --xs-archive-sha256 <sha256 of the ENDF/B-VIII.1 archive>

The harness refuses to start unless its cgroup shows MemoryMax <= 6 GiB, no swap and a CPU quota <= 200%. Each run
gets its own transient scope with MEMORY_MAX_GIB (the owner allowed more than 6 GiB for this protocol), no swap,
128 tasks and two CPUs, and a hard wall-clock limit; on expiry its whole process group is terminated, then killed,
then reaped. The orchestrator imports no OpenMC and holds no model.
"""
import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P181_PROTOCOL.md"
CONTRIB = ROOT / "contrib" / "openmc_r2s"
PYSRC = ROOT / "python" / "pysrc"
GROUPS_JSON = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
OUT_DEFAULT = ROOT / "results" / "p181_scale.json"
HOME = Path.home()

SCHEMA = "actinv-p181-scale-1"
THREADS = 2
PARTICLES = 20000
BATCHES = 5
SEED = 1000
TIMESTEPS = [365.25 * 86400.0, 1.0e6]
SOURCE_RATES = [1.0e17, 0.0]
PHOTON_TIME_INDEX = 2
# mesh dimension per axis, in execution order; elements = dim ** 3
DIMS = [2, 4, 8, 16, 24, 32]
SMOKE_DIMS = [2, 4]
ARM_ORDER = ["actinv", "openmc_flux"]
# the same limits for both arms: memory of each run's own scope (GiB) and wall clock per run (s)
MEMORY_MAX_GIB = 10
LIMIT_S = 4 * 3600
# before a run, wait (up to HEADROOM_WAIT_S) until MemAvailable leaves this much above the run's cap
HEADROOM_GIB = 1.0
HEADROOM_WAIT_S = 1800
HEARTBEAT_S = 5.0
GIB = 1 << 30

# model: shell radii (cm) around the source; steel fills the rest of the cube of half-width CUBE_HALF
R_W, R_CU, R_WATER = 4.0, 5.5, 6.5
CUBE_HALF = 10.0


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
    for line in (read_text("/proc/self/cgroup") or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            rel = parts[2]
    base = Path("/sys/fs/cgroup") / (rel or "").lstrip("/")
    return {"path": rel, "memory_max": read_text(base / "memory.max"), "memory_swap_max": read_text(base / "memory.swap.max"),
            "pids_max": read_text(base / "pids.max"), "cpu_max": read_text(base / "cpu.max")}


def cgroup_enforced(rec, memory_gib):
    """(ok, reason). Bytes <= memory_gib GiB, swap exactly 0, tasks <= 128, CPU quota <= 2 CPUs."""
    try:
        if int(rec["memory_max"]) > memory_gib * GIB:
            return False, f"memory.max above {memory_gib} GiB"
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


def meminfo_kib(key):
    for line in (read_text("/proc/meminfo") or "").splitlines():
        if line.startswith(key + ":"):
            return int(line.split()[1])
    return None


def run_text(cmd, timeout=30):
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return (p.stdout + p.stderr).strip()[-400:]
    except (OSError, subprocess.SubprocessError) as e:
        return f"error: {e}"


def other_scopes(own):
    """Transient systemd scopes of this user other than the harness's own."""
    try:
        out = subprocess.run(["systemctl", "--user", "list-units", "--type=scope", "--state=running", "--no-legend",
                              "--plain"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ["error: systemctl"]
    own = (own or "").rsplit("/", 1)[-1]
    return sorted(u.split()[0] for u in out.splitlines() if u.startswith("run-") and u.split()[0] != own)


def child_env(args, work):
    env = dict(os.environ)
    env.update({"OMP_NUM_THREADS": str(THREADS), "RAYON_NUM_THREADS": str(THREADS), "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1", "OPENMC_CROSS_SECTIONS": str(args.xs), "PYTHONHASHSEED": "0",
                "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(work / "tmp"), "OPENMC_CHAIN_FILE": str(args.chain)})
    return env


# ---------------------------------------------------------------------------------------------- bounded child

def scoped(cmd):
    """`cmd` inside its own transient systemd scope: MEMORY_MAX_GIB of memory, no swap, 128 tasks, two CPUs. A run that
    exceeds its memory is killed alone; the harness, in its own scope, records it."""
    return ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={MEMORY_MAX_GIB}G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%", "--"] + [str(c) for c in cmd]


def run_child(cmd, env, cwd, limit_s):
    """Run `cmd` in its own session with a hard wall-clock limit. Returns (status, exit_code, wall_s, stderr_tail).
    On expiry the process group gets SIGTERM, ten seconds, then SIGKILL, and is always reaped. A SIGKILL the harness
    did not send is reported as "killed": the kernel at the scope's memory limit, or systemd-oomd under memory
    pressure."""
    t0 = time.monotonic()
    # `systemd-run --scope` moves itself into the new scope and execs the command, so the pid and process group
    # are the command's own
    errlog = Path(cwd) / "stderr.log"
    with open(errlog, "wb") as err_stream:
        p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=err_stream,
                             start_new_session=True)
        status = "ok"
        try:
            p.wait(timeout=limit_s)
        except subprocess.TimeoutExpired:
            status = "timeout"
            for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 30)):
                try:
                    os.killpg(p.pid, sig)
                except OSError:
                    pass
                try:
                    p.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    pass
            p.wait()
    wall = time.monotonic() - t0
    code = p.returncode
    if status == "ok" and code != 0:
        status = "killed" if code in (-9, 137) else "failed"
    err = errlog.read_bytes()[-2000:].decode(errors="replace")
    return status, code, wall, err


# ---------------------------------------------------------------------------------------------- the plan

def h1(entry):
    if entry.get("step1_s") is None or entry.get("step2_s") is None:
        return None
    return entry["step1_s"] + entry["step2_s"]


def decide(arm, dim, attempts, smoke):
    """Registered rules, applied before each run. Returns None to run, else the recorded reason for not running.

    1. stop: an arm whose previous size did not complete steps 1 and 2 is not run at larger sizes.
    2. projection: an arm is not run at a size where its previous size's steps-1-2 time, scaled by the element ratio,
       exceeds the run limit. Not applied in a smoke run."""
    dims = SMOKE_DIMS if smoke else DIMS
    i = dims.index(dim)
    if i == 0:
        return None
    prev = dims[i - 1]
    last = next((a for a in attempts if a["arm"] == arm and a["dim"] == prev), None)
    if last is None:
        return {"rule": "stop", "reason": f"not run at {prev ** 3} elements"}
    t = h1(last) if last.get("steps12_complete") else None
    if t is None:
        return {"rule": "stop", "reason": f"steps 1 and 2 did not complete at {prev ** 3} elements ({last['status']})"}
    projected = t * (dim ** 3) / (prev ** 3)
    if not smoke and projected > LIMIT_S:
        return {"rule": "projection", "projected_s": projected, "limit_s": LIMIT_S}
    return None


def write_atomic(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def wait_for_headroom():
    """MemAvailable before a run, waiting up to HEADROOM_WAIT_S for it to exceed the run's cap plus HEADROOM_GIB."""
    need = (MEMORY_MAX_GIB + HEADROOM_GIB) * GIB / 1024
    t0 = time.monotonic()
    while True:
        avail = meminfo_kib("MemAvailable") or 0
        if avail >= need or time.monotonic() - t0 >= HEADROOM_WAIT_S:
            return {"mem_available_kib": avail, "needed_kib": int(need), "ok": avail >= need,
                    "waited_s": time.monotonic() - t0}
        time.sleep(60)


def orchestrate(args):
    cg = cgroup_record()
    ok, why = cgroup_enforced(cg, 6)
    if not ok:
        sys.exit(f"p181_harness: refusing to run: {why} ({cg}). Launch inside the bounded systemd-run scope.")
    work = Path(args.work).resolve()
    if work.exists():
        shutil.rmtree(work)
    (work / "tmp").mkdir(parents=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    files = {
        "chain_xml": args.chain, "cross_sections_xml": args.xs, "actinv_library": args.library,
        "decay_primary": args.decay_primary, "decay_fallback": args.decay_fallback, "actinv_binary": args.actinv_bin,
        "groups_json": GROUPS_JSON, "harness": Path(__file__).resolve(), "protocol": PROTOCOL,
        "demo_steel_cube": CONTRIB / "demo_steel_cube.py", "adapter": PYSRC / "actinv" / "openmc.py",
        "adapter_defaults": PYSRC / "actinv" / "_defaults.py",
    }
    inputs = {}
    for k, v in files.items():
        v = Path(v)
        if not v.is_file():
            sys.exit(f"p181_harness: input missing: {k} = {v}")
        inputs[k] = {"path": str(v), "sha256": sha256_file(v)}
    probe_cmd = [sys.executable, str(Path(__file__).resolve()), "--probe"]
    probe = json.loads(subprocess.run(probe_cmd, capture_output=True, text=True, timeout=300,
                                      env=child_env(args, work)).stdout or "{}")
    record = {
        "schema": SCHEMA, "smoke": bool(args.smoke), "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "cpu_model": lscpu_model(), "logical_cpus": os.cpu_count(), "mem_total_kib": meminfo_kib("MemTotal"),
            "kernel": platform.release(), "platform": platform.platform(), "python": sys.version.split()[0],
            "python_executable": sys.executable, "openmc_version": probe.get("openmc_version"),
            "numpy_version": probe.get("numpy_version"), "cgroup": cg,
            "env": {k: os.environ.get(k) for k in ("RAYON_NUM_THREADS", "OMP_NUM_THREADS", "TMPDIR")},
            "child_threads": THREADS, "git_head": run_text(["git", "rev-parse", "HEAD"]),
            "git_dirty_paths": run_text(["git", "status", "--porcelain", "--untracked-files=no"]),
            "actinv_version": run_text([args.actinv_bin, "--version"]),
            "xs_archive_sha256": args.xs_archive_sha256, "load_average_start": list(os.getloadavg()),
            "other_scopes_start": other_scopes(cg["path"]),
        },
        "inputs": inputs,
        "plan": {"particles": PARTICLES, "batches": BATCHES, "seed": SEED, "timesteps_s": TIMESTEPS,
                 "source_rates": SOURCE_RATES, "photon_time_index": PHOTON_TIME_INDEX,
                 "dims": SMOKE_DIMS if args.smoke else DIMS, "arms": ARM_ORDER, "limit_s": LIMIT_S,
                 "memory_max_gib": MEMORY_MAX_GIB, "headroom_gib": HEADROOM_GIB,
                 "model_cm": {"tungsten": R_W, "cucrzr": R_CU, "water": R_WATER,
                              "cube_half": CUBE_HALF}},
        "attempts": [], "skips": [],
    }
    write_atomic(out, record)
    for dim in (SMOKE_DIMS if args.smoke else DIMS):
        for arm in ARM_ORDER:
            decision = decide(arm, dim, record["attempts"], args.smoke)
            if decision is not None:
                record["skips"].append({"arm": arm, "dim": dim, "elements": dim ** 3, **decision})
                write_atomic(out, record)
                continue
            rundir = work / f"{arm}_{dim}"
            rundir.mkdir()
            result = rundir / "result.json"
            heartbeat = rundir / "heartbeat.json"
            cmd = [sys.executable, str(Path(__file__).resolve()), "--child", "--arm", arm, "--dim", str(dim),
                   "--out", str(result), "--heartbeat", str(heartbeat), "--rundir", str(rundir),
                   "--actinv-bin", args.actinv_bin, "--library", args.library,
                   "--decay-primary", args.decay_primary, "--decay-fallback", args.decay_fallback,
                   "--chain", args.chain, "--xs", args.xs]
            headroom = wait_for_headroom()
            before = {"load_average": list(os.getloadavg()), "other_scopes": other_scopes(cg["path"]),
                      "headroom": headroom}
            status, code, wall, err = run_child(scoped(cmd), child_env(args, work), rundir, LIMIT_S)
            entry = {"arm": arm, "dim": dim, "elements": dim ** 3, "status": status, "exit_code": code,
                     "wall_s": wall, "limit_s": LIMIT_S, "memory_max_gib": MEMORY_MAX_GIB, "before": before,
                     "stderr_tail": err if status != "ok" else ""}
            for path, key in ((result, None), (heartbeat, "last_heartbeat")):
                if path.is_file():
                    try:
                        data = json.loads(path.read_text())
                    except ValueError:
                        data = {"unreadable": True}
                    if key:
                        entry[key] = data
                    else:
                        entry.update(data)
            entry["steps12_complete"] = h1(entry) is not None and entry.get("phase_done") in ("step2", "step3")
            entry["complete"] = status == "ok" and entry.get("phase_done") == "step3"
            if status == "ok" and not entry["complete"]:
                entry["status"] = "failed"
            record["attempts"].append(entry)
            shutil.rmtree(rundir, ignore_errors=True)
            write_atomic(out, record)
    record["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record["environment"]["load_average_end"] = list(os.getloadavg())
    record["environment"]["other_scopes_end"] = other_scopes(cg["path"])
    write_atomic(out, record)
    print(f"p181_harness: wrote {out} ({len(record['attempts'])} runs, {len(record['skips'])} not run by rule)")
    return 0


# ---------------------------------------------------------------------------------------------- child (one run)

def probe():
    import numpy
    import openmc
    print(json.dumps({"openmc_version": openmc.__version__, "numpy_version": numpy.__version__}))
    return 0


def build_model(particles):
    """The P181 model: 14.1 MeV point source at the centre of a tungsten sphere, then CuCrZr and water shells, then SS316
    steel to the faces of a 20 cm cube; the demo's air detector shell at 30-31 cm. Returns (model, mesh, detector cell)."""
    import openmc

    def mat(name, density, fractions):
        m = openmc.Material(name=name)
        m.set_density("g/cm3", density)
        for el, w in fractions.items():
            m.add_element(el, w, "wo")
        return m

    tungsten = mat("W", 19.25, {"W": 100.0})
    cucrzr = mat("CuCrZr", 8.90, {"Cu": 98.9, "Cr": 0.8, "Zr": 0.3})
    water = mat("water", 1.0, {"H": 11.19, "O": 88.81})
    steel = mat("SS316", 7.93, {"Fe": 65.06, "Cr": 17.5, "Ni": 12.25, "Mo": 2.5, "Mn": 1.8, "Si": 0.5, "Co": 0.05,
                                "Nb": 0.01, "Ta": 0.01, "Cu": 0.3, "C": 0.03})
    air = mat("air", 0.0012, {"N": 0.755, "O": 0.245})
    s_w, s_cu, s_water = (openmc.Sphere(r=r) for r in (R_W, R_CU, R_WATER))
    box = openmc.model.RectangularParallelepiped(-CUBE_HALF, CUBE_HALF, -CUBE_HALF, CUBE_HALF, -CUBE_HALF, CUBE_HALF)
    inner, outer = openmc.Sphere(r=30.0), openmc.Sphere(r=31.0)
    world = openmc.Sphere(r=40.0, boundary_type="vacuum")
    cells = [openmc.Cell(fill=tungsten, region=-s_w),
             openmc.Cell(fill=cucrzr, region=+s_w & -s_cu),
             openmc.Cell(fill=water, region=+s_cu & -s_water),
             openmc.Cell(fill=steel, region=+s_water & -box),
             openmc.Cell(region=+box & -inner)]
    det = openmc.Cell(fill=air, region=+inner & -outer)
    cells += [det, openmc.Cell(region=+outer & -world)]
    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    settings.particles = particles
    settings.batches = BATCHES
    settings.source = openmc.IndependentSource(space=openmc.stats.Point(),
                                               energy=openmc.stats.Discrete([14.1e6], [1.0]))
    model = openmc.Model(openmc.Geometry(cells), openmc.Materials([tungsten, cucrzr, water, steel, air]), settings)
    mesh = openmc.RegularMesh()
    mesh.lower_left = (-CUBE_HALF,) * 3
    mesh.upper_right = (CUBE_HALF,) * 3
    mesh.dimension = (2, 2, 2)
    return model, mesh, det


class Heartbeat:
    """Writes the run's phase and its scope's memory.current / memory.peak every HEARTBEAT_S seconds, so a run killed
    at its memory limit still leaves how far it got and how much it held."""

    def __init__(self, path):
        self.path = Path(path)
        self.phase = "start"
        self.base = Path("/sys/fs/cgroup") / (cgroup_record()["path"] or "").lstrip("/")
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def snapshot(self):
        cur, peak = read_text(self.base / "memory.current"), read_text(self.base / "memory.peak")
        return {"phase": self.phase, "memory_current_bytes": int(cur) if cur and cur.isdigit() else None,
                "memory_peak_bytes": int(peak) if peak and peak.isdigit() else None,
                "rss_self_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0}

    def loop(self):
        while not self.stop.wait(HEARTBEAT_S):
            try:
                write_atomic(self.path, self.snapshot())
            except OSError:
                pass

    def set(self, phase):
        self.phase = phase
        write_atomic(self.path, self.snapshot())


def child(args):
    import struct
    import types

    import numpy as np
    import openmc
    import openmc.deplete
    from openmc.deplete import pool

    beat = Heartbeat(args.heartbeat)
    pool.NUM_PROCESSES = THREADS
    openmc.config["chain_file"] = args.chain
    openmc.config["cross_sections"] = args.xs
    # actinv.openmc from this checkout, without the native extension the package's __init__ loads (the adapter runs
    # the `actinv` binary given here and needs nothing else from the package)
    pkg = types.ModuleType("actinv")
    pkg.__path__ = [str(PYSRC / "actinv")]
    sys.modules["actinv"] = pkg
    from actinv.openmc import ActinvR2SManager, library_bounds  # noqa: E402
    sys.path.insert(0, str(CONTRIB))
    import demo_steel_cube as demo  # noqa: E402

    rundir = Path(args.rundir)
    dim = args.dim
    bounds = np.asarray(library_bounds(args.library), dtype=float)
    ref = np.sort(np.asarray(json.loads(GROUPS_JSON.read_text())["boundaries_eV"], dtype=float))
    bounds_rel = float(np.max(np.abs(bounds - ref) / ref)) if bounds.shape == ref.shape else float("inf")
    bounds_sha = hashlib.sha256(struct.pack(f"<{len(ref)}d", *ref)).hexdigest()

    model, mesh, det = build_model(PARTICLES)
    model.settings.seed = SEED
    mesh.dimension = (dim, dim, dim)
    pm = demo.photon_model(model, det, PARTICLES)
    pm.settings.seed = SEED + 500

    micro_kwargs = None
    if args.arm == "actinv":
        mgr = ActinvR2SManager(model, mesh, pm, library=args.library, decay_primary=args.decay_primary,
                               decay_fallback=args.decay_fallback, actinv_bin=args.actinv_bin, threads=THREADS)
        mode = "actinv.openmc flux-only step 1 on the library groups"
    else:
        mgr = openmc.deplete.R2SManager(model, mesh, pm)
        micro_kwargs = {"reaction_rate_mode": "flux", "energies": [float(x) for x in bounds]}
        mode = "flux"
    passed_keys = sorted(micro_kwargs or {})

    out = {"config": {"openmc_version": openmc.__version__, "arm": args.arm, "step1_mode": mode,
                      "micro_kwargs_keys": passed_keys, "bounds_sha256_ascending": bounds_sha,
                      "bounds_max_rel_diff_vs_groups_json": bounds_rel, "particles": PARTICLES, "batches": BATCHES,
                      "seed": SEED, "photon_seed": SEED + 500, "threads": THREADS, "pool_processes": THREADS,
                      "mesh_dimension": [dim, dim, dim], "timesteps_s": TIMESTEPS, "source_rates": SOURCE_RATES,
                      "photon_time_index": PHOTON_TIME_INDEX}}

    def save(phase):
        cg = cgroup_record()
        peak = read_text(Path("/sys/fs/cgroup") / (cg["path"] or "").lstrip("/") / "memory.peak")
        out.update({"phase_done": phase, "cgroup": cg,
                    "memory_peak_bytes": int(peak) if peak and peak.isdigit() else None,
                    "peak_rss_mib": {"self": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
                                     "children": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0}})
        write_atomic(args.out, out)

    # timing boundaries as P163: each step is bracketed by monotonic clock reads, nothing else is inside
    beat.set("step1")
    t0 = time.monotonic()
    mgr.step1_neutron_transport(output_dir=rundir / "neutron", micro_kwargs=micro_kwargs)
    t1 = time.monotonic()
    fluxes = mgr.results["fluxes"]
    flux_arr = [np.asarray(f, dtype=float).ravel() for f in fluxes]
    out["step1_s"] = t1 - t0
    out["config"].update({"n_regions": len(fluxes), "energy_groups_in_flux": int(flux_arr[0].size),
                          "flux_sum_n_cm_per_src": float(sum(f.sum() for f in flux_arr))})
    if args.arm == "openmc_flux":
        micros = mgr.results["micros"]
        out["config"].update({"n_micro_nuclides": len(micros[0].nuclides),
                              "n_micro_reactions": len(micros[0].reactions)})
    del flux_arr
    save("step1")
    beat.set("step2")
    t1 = time.monotonic()
    mgr.step2_activation(TIMESTEPS, SOURCE_RATES, output_dir=rundir / "activation")
    t2 = time.monotonic()
    out["step2_s"] = t2 - t1
    save("step2")
    beat.set("step3")
    t2 = time.monotonic()
    mgr.step3_photon_transport(time_indices=[PHOTON_TIME_INDEX], output_dir=rundir / "photon")
    t3 = time.monotonic()
    out["step3_s"] = t3 - t2
    mean, sd = demo.dose_pSv_s(mgr, PHOTON_TIME_INDEX)
    strength = sum(src.strength for ms in mgr.get_decay_photon_source_mesh(PHOTON_TIME_INDEX) for src in ms.sources)
    out.update({"dose_per_photon_pSv_cm2": mean, "dose_sd": sd, "photons_per_s": strength,
                "dose_rate_Sv_h": mean / demo.DET_VOLUME_CM3 * 3600.0 * 1e-12})
    beat.set("done")
    save("step3")
    beat.stop.set()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true", help="print the OpenMC version (internal)")
    ap.add_argument("--child", action="store_true", help="run one arm and size (internal)")
    ap.add_argument("--smoke", action="store_true", help="rehearsal: 8 and 64 elements only")
    ap.add_argument("--arm", choices=ARM_ORDER)
    ap.add_argument("--dim", type=int, choices=sorted(set(DIMS)))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--heartbeat")
    ap.add_argument("--rundir")
    ap.add_argument("--work", default=str(ROOT / "target" / "p181-work"))
    ap.add_argument("--actinv-bin", default=os.environ.get("ACTINV_BIN", str(ROOT / "target" / "release" / "actinv")))
    ap.add_argument("--data-dir", default=os.environ.get("ACTINV_DATA_DIR",
                                                         str(HOME / "Documents/actinv/actinv-data/v1.2.0")))
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
