#!/usr/bin/env python3
"""P191 harness (protocols/ACTINV-P191_PROTOCOL.md): a same-data shutdown-dose comparison on P181's model at mesh
dimension 2 (8 elements), ACTINV's R2S with and without mixture self-shielding against OpenMC 0.15.3's own R2S.

  run --arm ARM   one arm, one repetition, in its own scope; writes results/p191/ARM.json atomically
  assemble        results/p191_record.json from the four arm files

Arms, all on P181's chain file, OpenMC cross sections, model, source, schedule, transport settings (20000 x 5 batches,
seed 1000, photon seed 1500), two threads, detector dose tally and ACTINV decay data:

  openmc_direct  openmc.deplete.R2SManager with no micro_kwargs: direct per-nuclide, per-reaction tallies (P163's
                 `openmc_default` arm); OpenMC's own step 2 and step 3.
  actinv_mixture actinv.openmc.ActinvR2SManager on the ENDF/B-VIII.1 709-group library (P181 Amendment A's) with
                 self_shielding="mixture".
  actinv_plain   the same with self_shielding=None.
  openmc_flux    P181's `openmc_flux` arm: reaction_rate_mode "flux" on the 709 group boundaries. Context only.

P181's harness (controls/p181_harness.py) is imported unchanged: the model, the heartbeat, the cgroup inspection, the
environment helpers and the bounded-child helpers (scoped, run_child, child_env, wait_for_headroom) are its own. This
file adds only the arm logic and the record. Launch like P181's, from the AGENTS.md scope:

  systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- \\
    env RAYON_NUM_THREADS=2 TMPDIR=$PWD/target/p191-tmp \\
    ~/micromamba/envs/openmc0153dd/bin/python controls/p191_harness.py run --arm ARM --actinv-bin ACTINV \\
    --xs-archive-sha256 <sha256 of the ENDF/B-VIII.1 archive>

`--rehearsal` writes to target/p191-rehearsal with tiny particle counts; its records are never evidence.
"""
import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import struct
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p181_harness as h  # noqa: E402

# P181 waited up to 30 min for MemAvailable to clear the cap plus 1 GiB, then ran anyway; P191's arms peak well
# under the cap (7.2 GB rehearsed) and the workstation now has swap, so wait at most 2 min and record what was free
h.HEADROOM_WAIT_S = 120

PROTOCOL = ROOT / "protocols" / "ACTINV-P191_PROTOCOL.md"
OUT_DIR = ROOT / "results" / "p191"
RECORD = ROOT / "results" / "p191_record.json"
REHEARSAL_DIR = ROOT / "target" / "p191-rehearsal"
HOME = Path.home()

SCHEMA = "actinv-p191-arm-1"
RECORD_SCHEMA = "actinv-p191-record-1"
ARMS = ["openmc_direct", "actinv_mixture", "actinv_plain", "openmc_flux"]
ACTINV_ARMS = ("actinv_mixture", "actinv_plain")
DIM = 2
REHEARSAL_PARTICLES = 200
DEFAULT_LIBRARY = HOME / "nuclear-data" / "endfb-viii.1-p178" / "endfb81_all_709g.npz"


def arm_dir(rehearsal):
    return REHEARSAL_DIR if rehearsal else OUT_DIR


def record_path(rehearsal):
    return REHEARSAL_DIR / "record.json" if rehearsal else RECORD


# ---------------------------------------------------------------------------------------------- orchestrator

def repo_files():
    return {"harness": Path(__file__).resolve(), "p181_harness": Path(h.__file__).resolve(), "protocol": PROTOCOL,
            "demo_steel_cube": h.CONTRIB / "demo_steel_cube.py", "adapter": h.PYSRC / "actinv" / "openmc.py",
            "adapter_defaults": h.PYSRC / "actinv" / "_defaults.py", "groups_json": h.GROUPS_JSON}


def ledger_summary(path):
    """Per-material mixture self-shielding summary from `actinv mesh`'s result NDJSON; None when absent."""
    path = Path(path)
    if not path.is_file():
        return None
    rows = []
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("record") != "cell":
                continue
            result = rec.get("result") or {}
            ledger = result.get("ledger") or rec.get("ledger") or {}
            mixture = (ledger.get("shielding") or {}).get("mixture")
            if mixture is None:
                continue
            rows.append({"id": rec.get("id"), "profile": mixture.get("profile"),
                         "temperature_K": mixture.get("temperature_K"),
                         "covered_n": len(mixture.get("covered") or []),
                         "background_estimated": mixture.get("background_estimated") or [],
                         "evaluations_unused_n": len(mixture.get("evaluations_unused") or []),
                         "zero_k_clip_nuclides": sorted(mixture.get("zero_k_clips") or {}),
                         "channels_summarised": len(mixture.get("scales") or {})})
    return rows or None


def run_arm(args):
    cg = h.cgroup_record()
    ok, why = h.cgroup_enforced(cg, 6)
    if not ok:
        sys.exit(f"p191_harness: refusing to run: {why} ({cg}). Launch inside the bounded systemd-run scope.")
    out_dir = arm_dir(args.rehearsal)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(args.work or ROOT / "target" / f"p191-work-{args.arm}").resolve()
    if work.exists():
        shutil.rmtree(work)
    (work / "tmp").mkdir(parents=True)
    files = {"chain_xml": args.chain, "cross_sections_xml": args.xs, "actinv_library": args.library,
             "decay_primary": args.decay_primary, "decay_fallback": args.decay_fallback,
             "actinv_binary": args.actinv_bin, **repo_files()}
    inputs = {}
    for k, v in files.items():
        v = Path(v)
        if not v.is_file():
            sys.exit(f"p191_harness: input missing: {k} = {v}")
        inputs[k] = {"path": str(v), "sha256": h.sha256_file(v)}
    probe = json.loads(subprocess.run([sys.executable, str(Path(h.__file__).resolve()), "--probe"],
                                        capture_output=True, text=True, timeout=300,
                                        env=h.child_env(args, work)).stdout or "{}")
    particles = REHEARSAL_PARTICLES if args.rehearsal else h.PARTICLES
    entry = {
        "schema": SCHEMA, "arm": args.arm, "rehearsal": bool(args.rehearsal),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "cpu_model": h.lscpu_model(), "logical_cpus": os.cpu_count(), "mem_total_kib": h.meminfo_kib("MemTotal"),
            "kernel": platform.release(), "python": sys.version.split()[0], "python_executable": sys.executable,
            "openmc_version": probe.get("openmc_version"), "numpy_version": probe.get("numpy_version"),
            "cgroup": cg, "env": {k: os.environ.get(k) for k in ("RAYON_NUM_THREADS", "OMP_NUM_THREADS", "TMPDIR")},
            "child_threads": h.THREADS, "git_head": h.run_text(["git", "rev-parse", "HEAD"]),
            "git_dirty_paths": h.run_text(["git", "status", "--porcelain", "--untracked-files=no"]),
            "actinv_version": h.run_text([args.actinv_bin, "--version"]),
            "xs_archive_sha256": args.xs_archive_sha256, "load_average_start": list(os.getloadavg()),
        },
        "inputs": inputs,
        "plan": {"particles": particles, "batches": h.BATCHES, "seed": h.SEED, "timesteps_s": h.TIMESTEPS,
                 "source_rates": h.SOURCE_RATES, "photon_time_index": h.PHOTON_TIME_INDEX, "dim": DIM,
                 "limit_s": h.LIMIT_S, "memory_max_gib": h.MEMORY_MAX_GIB, "headroom_gib": h.HEADROOM_GIB,
                 "headroom_wait_s": h.HEADROOM_WAIT_S},
    }
    rundir = work / args.arm
    rundir.mkdir()
    result, heartbeat = rundir / "result.json", rundir / "heartbeat.json"
    cmd = [sys.executable, str(Path(__file__).resolve()), "child", "--arm", args.arm, "--particles", str(particles),
           "--out", str(result), "--heartbeat", str(heartbeat), "--rundir", str(rundir),
           "--actinv-bin", args.actinv_bin, "--library", args.library, "--decay-primary", args.decay_primary,
           "--decay-fallback", args.decay_fallback, "--chain", args.chain, "--xs", args.xs]
    headroom = h.wait_for_headroom()
    before = {"load_average": list(os.getloadavg()), "other_scopes": h.other_scopes(cg["path"]), "headroom": headroom}
    status, code, wall, err = h.run_child(h.scoped(cmd), h.child_env(args, work), rundir, h.LIMIT_S)
    entry.update({"status": status, "exit_code": code, "wall_s": wall, "limit_s": h.LIMIT_S,
                  "memory_max_gib": h.MEMORY_MAX_GIB, "before": before, "stderr_tail": err if status != "ok" else ""})
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
    entry["complete"] = status == "ok" and entry.get("phase_done") == "step3"
    if status == "ok" and not entry["complete"]:
        entry["status"] = "failed"
    # right-censored: the run needed at least wall_s (the limit, or the time of the kill); a kill is attributed to
    # the memory cap only with P181's evidence (the last heartbeat's memory.peak near the cap)
    entry["censored"] = entry["status"] in ("timeout", "killed")
    beat = entry.get("last_heartbeat") or {}
    if entry["censored"]:
        peak = beat.get("memory_peak_bytes")
        entry["censor_reason"] = "timeout" if entry["status"] == "timeout" else (
            "memory" if isinstance(peak, int) and peak >= 9.5 * h.GIB else "killed")
        if entry.get("memory_peak_bytes") is None:
            entry["memory_peak_bytes"] = peak
    if args.arm in ACTINV_ARMS:
        spec_path = rundir / "activation" / "mesh.json"
        if spec_path.is_file():
            spec = json.loads(spec_path.read_text())
            entry["mesh_spec"] = {"self_shielding": spec.get("self_shielding"),
                                  "self_shielding_present": "self_shielding" in spec,
                                  "library": spec.get("library"), "threads": spec.get("threads")}
        entry["ledger_mixture"] = ledger_summary(rundir / "activation" / "mesh_result.ndjson")
    shutil.rmtree(rundir, ignore_errors=True)
    entry["environment"]["load_average_end"] = list(os.getloadavg())
    entry["environment"]["other_scopes_end"] = h.other_scopes(cg["path"])
    out = out_dir / f"{args.arm}.json"
    h.write_atomic(out, entry)
    print(f"p191_harness: wrote {out} (status {entry['status']}, censored {entry['censored']})")
    return 0


def assemble(args):
    d = arm_dir(args.rehearsal)
    arms = {}
    for arm in ARMS:
        path = d / f"{arm}.json"
        if not path.is_file():
            sys.exit(f"p191_harness: missing arm file {path}")
        arms[arm] = json.loads(path.read_text())
    record = {"schema": RECORD_SCHEMA, "rehearsal": any(a.get("rehearsal") for a in arms.values()), "arms": arms,
              "assembled_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    out = record_path(args.rehearsal)
    out.parent.mkdir(parents=True, exist_ok=True)
    h.write_atomic(out, record)
    print(f"p191_harness: wrote {out} ({len(arms)} arms)")
    return 0


# ---------------------------------------------------------------------------------------------- child (one arm)

def child(args):
    import numpy as np
    import openmc
    import openmc.deplete
    from openmc.deplete import pool

    beat = h.Heartbeat(args.heartbeat)
    pool.NUM_PROCESSES = h.THREADS
    openmc.config["chain_file"] = args.chain
    openmc.config["cross_sections"] = args.xs
    # actinv.openmc from this checkout without the native extension, as P181's child does
    pkg = types.ModuleType("actinv")
    pkg.__path__ = [str(h.PYSRC / "actinv")]
    sys.modules["actinv"] = pkg
    from actinv.openmc import ActinvR2SManager, library_bounds  # noqa: E402
    sys.path.insert(0, str(h.CONTRIB))
    import demo_steel_cube as demo  # noqa: E402

    rundir = Path(args.rundir)
    particles = args.particles
    bounds = np.asarray(library_bounds(args.library), dtype=float)
    ref = np.sort(np.asarray(json.loads(h.GROUPS_JSON.read_text())["boundaries_eV"], dtype=float))
    bounds_rel = float(np.max(np.abs(bounds - ref) / ref)) if bounds.shape == ref.shape else float("inf")
    bounds_sha = hashlib.sha256(struct.pack(f"<{len(ref)}d", *ref)).hexdigest()

    model, mesh, det = h.build_model(particles)
    model.settings.seed = h.SEED
    mesh.dimension = (DIM, DIM, DIM)
    pm = demo.photon_model(model, det, particles)
    pm.settings.seed = h.SEED + 500

    micro_kwargs = None
    if args.arm in ACTINV_ARMS:
        shielding = "mixture" if args.arm == "actinv_mixture" else None
        mgr = ActinvR2SManager(model, mesh, pm, library=args.library, decay_primary=args.decay_primary,
                               decay_fallback=args.decay_fallback, actinv_bin=args.actinv_bin, threads=h.THREADS,
                               self_shielding=shielding)
        mode = f"actinv.openmc flux-only step 1 on the library groups, self_shielding={shielding!r}"
    elif args.arm == "openmc_flux":
        mgr = openmc.deplete.R2SManager(model, mesh, pm)
        micro_kwargs = {"reaction_rate_mode": "flux", "energies": [float(x) for x in bounds]}
        mode = "flux"
    else:
        mgr = openmc.deplete.R2SManager(model, mesh, pm)
        mode = "direct (OpenMC defaults)"

    out = {"config": {"openmc_version": openmc.__version__, "arm": args.arm, "step1_mode": mode,
                      "reaction_rate_mode": (micro_kwargs or {}).get("reaction_rate_mode", "direct"),
                      "micro_kwargs_keys": sorted(micro_kwargs or {}), "bounds_sha256_ascending": bounds_sha,
                      "bounds_max_rel_diff_vs_groups_json": bounds_rel, "particles": particles,
                      "batches": h.BATCHES, "seed": h.SEED, "photon_seed": h.SEED + 500, "threads": h.THREADS,
                      "pool_processes": h.THREADS, "mesh_dimension": [DIM, DIM, DIM], "timesteps_s": h.TIMESTEPS,
                      "source_rates": h.SOURCE_RATES, "photon_time_index": h.PHOTON_TIME_INDEX}}

    def save(phase):
        cg = h.cgroup_record()
        peak = h.read_text(Path("/sys/fs/cgroup") / (cg["path"] or "").lstrip("/") / "memory.peak")
        out.update({"phase_done": phase, "cgroup": cg,
                    "memory_peak_bytes": int(peak) if peak and peak.isdigit() else None,
                    "peak_rss_mib": {"self": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
                                     "children": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0}})
        h.write_atomic(args.out, out)

    # timing boundaries as P163 and P181: each step is bracketed by monotonic clock reads, nothing else is inside
    beat.set("step1")
    t0 = time.monotonic()
    mgr.step1_neutron_transport(output_dir=rundir / "neutron", micro_kwargs=micro_kwargs)
    t1 = time.monotonic()
    flux_arr = [np.asarray(f, dtype=float).ravel() for f in mgr.results["fluxes"]]
    out["step1_s"] = t1 - t0
    out["config"].update({"n_regions": len(flux_arr), "energy_groups_in_flux": int(flux_arr[0].size),
                          "flux_sum_n_cm_per_src": float(sum(f.sum() for f in flux_arr))})
    if args.arm not in ACTINV_ARMS:
        micros = mgr.results["micros"]
        out["config"].update({"n_micro_nuclides": len(micros[0].nuclides),
                              "n_micro_reactions": len(micros[0].reactions)})
    del flux_arr
    save("step1")
    beat.set("step2")
    t1 = time.monotonic()
    mgr.step2_activation(h.TIMESTEPS, h.SOURCE_RATES, output_dir=rundir / "activation")
    t2 = time.monotonic()
    out["step2_s"] = t2 - t1
    save("step2")
    beat.set("step3")
    t2 = time.monotonic()
    mgr.step3_photon_transport(time_indices=[h.PHOTON_TIME_INDEX], output_dir=rundir / "photon")
    t3 = time.monotonic()
    out["step3_s"] = t3 - t2
    mean, sd = demo.dose_pSv_s(mgr, h.PHOTON_TIME_INDEX)
    strength = sum(src.strength for ms in mgr.get_decay_photon_source_mesh(h.PHOTON_TIME_INDEX)
                   for src in ms.sources)
    scale = 1.0 / demo.DET_VOLUME_CM3 * 3600.0 * 1e-12
    out.update({"dose_per_photon_pSv_cm2": mean, "dose_sd": sd, "photons_per_s": strength,
                "dose_rate_Sv_h": mean * scale, "dose_rate_sd_Sv_h": sd * scale})
    beat.set("done")
    save("step3")
    beat.stop.set()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--rehearsal", action="store_true", help="tiny particle counts, outputs in target/p191-rehearsal")
        p.add_argument("--actinv-bin", default=os.environ.get("ACTINV_BIN", str(ROOT / "target" / "release" / "actinv")))
        p.add_argument("--data-dir", default=os.environ.get("ACTINV_DATA_DIR",
                                                            str(HOME / "Documents/actinv/actinv-data/v1.2.0")))
        p.add_argument("--library", default=str(DEFAULT_LIBRARY), help="the ENDF/B-VIII.1 709-group library")
        p.add_argument("--decay-primary")
        p.add_argument("--decay-fallback")
        p.add_argument("--chain", default=str(HOME / "nuclear-data/p32-work/chain/depletion/chain.xml"))
        p.add_argument("--xs", default=str(HOME / "nuclear-data/endfb-viii.1-hdf5/cross_sections.xml"))

    p_run = sub.add_parser("run", help="one arm in its own scope")
    common(p_run)
    p_run.add_argument("--arm", choices=ARMS, required=True)
    p_run.add_argument("--work")
    p_run.add_argument("--xs-archive-sha256", default=None)
    p_asm = sub.add_parser("assemble", help="results/p191_record.json from the arm files")
    p_asm.add_argument("--rehearsal", action="store_true")
    p_child = sub.add_parser("child", help="internal: run one arm")
    common(p_child)
    for name in ("--arm",):
        p_child.add_argument(name, choices=ARMS, required=True)
    p_child.add_argument("--particles", type=int, required=True)
    for name in ("--out", "--heartbeat", "--rundir"):
        p_child.add_argument(name, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "assemble":
        return assemble(args)
    d = Path(args.data_dir)
    args.decay_primary = args.decay_primary or str(d / "decay" / "endf-b-viii-0_decay.dat")
    args.decay_fallback = args.decay_fallback or str(d / "decay" / "jeff-3-3_decay.dat")
    return child(args) if args.cmd == "child" else run_arm(args)


if __name__ == "__main__":
    sys.exit(main())
