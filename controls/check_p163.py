#!/usr/bin/env python3
"""P163 checker (protocols/ACTINV-P163_PROTOCOL.md): ACTINV against OpenMC's flux mode on the steel-cube R2S demo.
Reads results/p163_speed.json written by controls/p163_harness.py. G0-G6 are evaluated here; G7 (CI on the pushed
evidence commit) is external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P163_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Environment and inputs: not a smoke run; OpenMC 0.15.3; the harness's cgroup shows MemoryMax <= 6 GiB, no swap,
      tasks <= 128 and a CPU quota <= 200%; every completed run's own scope shows the same limits with its arm's
      registered memory cap (ACTINV 6 GiB, OpenMC 9 GiB) and a recorded peak; two threads for every tool; the clean-tree flag; every external input equals its pinned
      SHA-256 (chain, library, both decay files, cross_sections.xml, the ENDF/B-VIII.1 archive); every in-repo input
      equals the file now in the checkout.
  G2  The registered plan was carried out and nothing was hidden: each (arm, size, repetition) appears exactly once as an
      attempt or a recorded skip; ACTINV runs all succeeded; OpenMC runs ended ok, timed out, or were killed by the
      cgroup (censored, recorded); every skip is one the registered rule requires and every rule-required skip is there.
  G3  Configuration fidelity: the same model, seed, particles, batches, threads, schedule and photon settings in every
      run; the flux arm used reaction_rate_mode "flux" on the 709 library groups with the whole chain (>= 300 nuclides)
      and no other override; the default arm used none; the ACTINV arm used the adapter's slim step 1.
  G4  Accuracy: dose ratio ACTINV / OpenMC flux in [0.95, 1.05] for every completed pair; at 8 cells flux mode and ACTINV
      each in [0.95, 1.05] of the default arm's dose (or, if that run was censored, within 7% of the recorded demo
      dose); photon strength ratio in [0.5, 2]; summed neutron track length within 2% between the arms.
  G5  Timing validity: positive steps; wall time within 180 s of the step sum; machine quiet at the start (1-min load
      average <= 3).
  G6  Decision rule: per-size claims computed by the registered rule and written to results/p163_verdict.json; with
      --release-notes FILE, every sentence there that compares ACTINV with OpenMC's speed is within them.
  G7  External (CI on the pushed evidence commit).

Verdict: P163-PASS if G0-G6 hold. A measurement that leaves ACTINV slower than OpenMC flux mode is a PASS: the
protocol's output is the set of claims the data allow, and that set may be empty.

Usage: check_p163.py [--record FILE] [--release-notes FILE]
       check_p163.py --selftest
"""
import argparse
import copy
import hashlib
import json
import math
import re
import statistics
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P163_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
GROUPS_JSON = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
RECORD = ROOT / "results" / "p163_speed.json"
VERDICT = ROOT / "results" / "p163_verdict.json"

SCHEMA = "actinv-p163-speed-1"
OPENMC_VERSION = "0.15.3"
GIB = 1 << 30
THREADS = 2
PARTICLES, BATCHES, SEED_BASE = 20000, 5, 1000
TIMESTEPS = [365.25 * 86400.0, 1.0e6]
SOURCE_RATES = [1.0e17, 0.0]
DIMENSIONS = {8: 2, 64: 4, 512: 8}
REPS = {"actinv": {8: 3, 64: 3, 512: 3}, "openmc_flux": {8: 3, 64: 2, 512: 1}, "openmc_default": {8: 1}}
LIMIT_S = {"actinv": 900, "openmc_flux": 3600, "openmc_default": 3000}
MEMORY_MAX_GIB = {"actinv": 6, "openmc_flux": 9, "openmc_default": 9}
ARMS = ["actinv", "openmc_flux", "openmc_default"]
CENSORED = ("timeout", "killed")
DOSE_BAND = (0.95, 1.05)
DEMO_DOSE_SV_H = 1019.7936990070901  # results/openmc_r2s_demo.json, openmc_r2s_defaults.dose_rate_Sv_h
DEMO_BAND = 0.07
PHOTON_BAND = (0.5, 2.0)
FLUX_SUM_TOL = 0.02
MIN_FLUX_NUCLIDES = 300
WALL_SLACK_S = 180.0
LOAD_MAX = 3.0
SPREAD_NOTE = 1.5

# External inputs pinned by this checker. "TBD" is refused: fill it from the machine that runs the evidence, in a
# commit that precedes the evidence run (the protocol's "Environment" section says how).
PINS = {
    "chain_xml": "d3b829467cfdda9c624aaeb5709825df378b908ecc63c3f984ade51f8d0444a0",
    "actinv_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    "cross_sections_xml": "b8ffb6104a946bf951dc0f3b9a1f57a60f7f57c4b308cd66df4fd3dd4bc557ce",
    "xs_archive": "b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb",
}
REPO_INPUTS = {"harness": "controls/p163_harness.py", "protocol": "protocols/ACTINV-P163_PROTOCOL.md",
               "demo_steel_cube": "contrib/openmc_r2s/demo_steel_cube.py",
               "adapter": "contrib/openmc_r2s/actinv_openmc_r2s.py",
               "groups_json": "crates/actinv-data/data/fispact_709_groups.json"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def groups_sha():
    ref = sorted(json.loads(GROUPS_JSON.read_text())["boundaries_eV"])
    return hashlib.sha256(struct.pack(f"<{len(ref)}d", *ref)).hexdigest()


def repo_hashes():
    return {k: sha256_file(ROOT / v) for k, v in REPO_INPUTS.items()}


# ---------------------------------------------------------------------------------------------- helpers

def h1(run):
    """Steps 1+2 for a completed run; for a censored run the time it needed at least."""
    if run.get("status") == "ok":
        return run["step1_s"] + run["step2_s"]
    return run["wall_s"]


def attempts_of(rec, arm=None, cells=None):
    return [a for a in rec.get("attempts", []) if (arm is None or a["arm"] == arm) and (cells is None or a["cells"] == cells)]


def ok_runs(rec, arm, cells):
    return sorted((a for a in attempts_of(rec, arm, cells) if a.get("status") == "ok"), key=lambda a: a["rep"])


def plan_items():
    return [(arm, c, r) for arm in ARMS for c in sorted(DIMENSIONS) for r in range(REPS[arm].get(c, 0))]


def skip_required(rec, arm, cells):
    """The registered rule (harness skip_rule), recomputed from the attempts. Returns None or the projected time."""
    if arm != "openmc_flux" or cells == 8:
        return None
    prev = {64: 8, 512: 64}[cells]
    vals = [a["step1_s"] + a["step2_s"] for a in ok_runs(rec, arm, prev)]
    if not vals:
        return math.inf
    projected = statistics.median(vals) * cells / prev
    return projected if projected > LIMIT_S[arm] else None


def result(ok, **kw):
    return {"pass": bool(ok), **kw}


# ---------------------------------------------------------------------------------------------- G0

def gate_g0(lines=None, paths=None, digests=None):
    lines = HASH_LOG.read_text(encoding="utf-8").splitlines() if lines is None else lines
    if paths is None:
        paths = [PROTOCOL] + sorted((ROOT / "protocols").glob("ACTINV-P163_AMENDMENT_*.md"))
    out, ok = {}, True
    for path in paths:
        digest = digests[path.name] if digests else sha256_file(path)
        hit = any(ln.startswith(digest) and ln.split()[-1] == f"protocols/{path.name}" for ln in lines)
        out[path.name] = {"sha256": digest, "in_protocol_hash_txt": hit}
        ok = ok and hit
    return result(ok, **out)


# ---------------------------------------------------------------------------------------------- G1

def cgroup_ok(cg, max_gib=6):
    try:
        if int(cg["memory_max"]) > max_gib * GIB:
            return False, f"memory.max above {max_gib} GiB"
        if int(cg["memory_swap_max"]) != 0:
            return False, "memory.swap.max not 0"
        if int(cg["pids_max"]) > 128:
            return False, "pids.max above 128"
        quota, period = cg["cpu_max"].split()
        if int(quota) / int(period) > 2.0 + 1e-9:
            return False, "cpu.max above 200%"
    except (TypeError, ValueError, KeyError, AttributeError):
        return False, "cgroup limits unreadable or unset"
    return True, "ok"


def gate_g1(rec, actual_repo, pins=None):
    pins = PINS if pins is None else pins
    problems = []
    if rec.get("schema") != SCHEMA:
        problems.append("schema")
    if rec.get("smoke"):
        problems.append("a smoke run is a development run, not evidence")
    env = rec.get("environment", {})
    if env.get("openmc_version") != OPENMC_VERSION:
        problems.append(f"openmc_version {env.get('openmc_version')!r} is not {OPENMC_VERSION}")
    ok, why = cgroup_ok(env.get("cgroup", {}))
    if not ok:
        problems.append(f"cgroup: {why}")
    if (rec.get("plan") or {}).get("memory_max_gib") != MEMORY_MAX_GIB:
        problems.append("the plan's memory caps are not the registered ones")
    for a in rec.get("attempts", []):
        key = (a.get("arm"), a.get("cells"), a.get("rep"))
        cap = MEMORY_MAX_GIB.get(a.get("arm"))
        if a.get("memory_max_gib") != cap:
            problems.append(f"run {key} had memory cap {a.get('memory_max_gib')!r}, not {cap}")
        if a.get("status") != "ok":
            continue
        ok, why = cgroup_ok(a.get("cgroup") or {}, cap or 0)
        if not ok:
            problems.append(f"run {key} scope: {why}")
        peak = a.get("memory_peak_bytes")
        if not isinstance(peak, int) or not 0 < peak <= (cap or 0) * GIB:
            problems.append(f"run {key} has no plausible memory peak")
    if env.get("child_threads") != THREADS or (env.get("env") or {}).get("RAYON_NUM_THREADS") != str(THREADS):
        problems.append("threads are not 2 for every tool")
    for key in ("cpu_model", "logical_cpus", "mem_total_kib", "kernel", "actinv_version"):
        if not env.get(key):
            problems.append(f"environment.{key} missing")
    if not re.fullmatch(r"[0-9a-f]{40}", str(env.get("git_head", ""))):
        problems.append("git_head is not a commit")
    if env.get("git_dirty_paths"):
        problems.append("tracked inputs were modified when the harness started (git status not clean)")
    if env.get("xs_archive_sha256") != pins.get("xs_archive"):
        problems.append("ENDF/B-VIII.1 archive SHA-256 does not match the pin")
    inputs = rec.get("inputs", {})
    for key in ("chain_xml", "actinv_library", "decay_primary", "decay_fallback", "cross_sections_xml"):
        pin = pins.get(key)
        got = (inputs.get(key) or {}).get("sha256")
        if not pin or pin == "TBD":
            problems.append(f"pin for {key} is not set in the checker")
        elif got != pin:
            problems.append(f"{key} differs from its pin")
    if not re.fullmatch(r"[0-9a-f]{64}", str((inputs.get("actinv_binary") or {}).get("sha256", ""))):
        problems.append("actinv binary hash missing")
    for key, digest in actual_repo.items():
        if (inputs.get(key) or {}).get("sha256") != digest:
            problems.append(f"{key} in the record differs from the checkout")
    return result(not problems, problems=problems)


# ---------------------------------------------------------------------------------------------- G2

def gate_g2(rec):
    problems = []
    attempts, skips = rec.get("attempts", []), rec.get("skips", [])
    seen = {}
    for entry in attempts + skips:
        key = (entry.get("arm"), entry.get("cells"), entry.get("rep"))
        seen[key] = seen.get(key, 0) + 1
    plan = set(plan_items())
    for key, n in seen.items():
        if key not in plan:
            problems.append(f"run outside the registered plan: {key}")
        if n != 1:
            problems.append(f"{key} recorded {n} times")
    for key in sorted(plan):
        if key not in seen:
            problems.append(f"registered run missing: {key}")
    skipped = {(s["arm"], s["cells"], s["rep"]) for s in skips}
    for a in attempts:
        key = (a["arm"], a["cells"], a["rep"])
        if a["arm"] == "actinv" and a.get("status") != "ok":
            problems.append(f"ACTINV run failed: {key} {a.get('status')}")
        elif a["arm"] != "actinv" and a.get("status") not in ("ok",) + CENSORED:
            problems.append(f"OpenMC run ended {a.get('status')!r} (a failure, not a censoring): {key}")
        if a.get("status") == "timeout" and a.get("wall_s", 0) < LIMIT_S[a["arm"]] - 1.0:
            problems.append(f"timeout recorded before the limit: {key}")
        if a.get("status") == "ok" and a.get("exit_code") != 0:
            problems.append(f"ok with nonzero exit: {key}")
    for s in skips:
        key = (s["arm"], s["cells"], s["rep"])
        need = skip_required(rec, s["arm"], s["cells"])
        if need is None:
            problems.append(f"skip not allowed by the registered rule: {key}")
        if s["arm"] != "openmc_flux":
            problems.append(f"only OpenMC flux runs may be skipped: {key}")
    for cells in (64, 512):
        for rep in range(REPS["openmc_flux"][cells]):
            key = ("openmc_flux", cells, rep)
            need = skip_required(rec, "openmc_flux", cells)
            if need is not None and key not in skipped and key not in {(a["arm"], a["cells"], a["rep"]) for a in attempts}:
                problems.append(f"rule-required skip not recorded: {key}")
            if need is not None and key not in skipped and key in {(a["arm"], a["cells"], a["rep"]) for a in attempts}:
                pass  # running more than the rule requires is allowed
    return result(not problems, problems=problems, attempts=len(attempts), skips=len(skips))


# ---------------------------------------------------------------------------------------------- G3

def gate_g3(rec, bounds_hash):
    problems = []
    for a in rec.get("attempts", []):
        if a.get("status") != "ok":
            continue
        key = (a["arm"], a["cells"], a["rep"])
        c = a.get("config", {})
        expect = {"openmc_version": OPENMC_VERSION, "arm": a["arm"], "particles": PARTICLES, "batches": BATCHES,
                  "seed": SEED_BASE + a["rep"], "photon_seed": SEED_BASE + a["rep"] + 500, "threads": THREADS,
                  "pool_processes": THREADS, "mesh_dimension": [DIMENSIONS[a["cells"]]] * 3,
                  "timesteps_s": TIMESTEPS, "source_rates": SOURCE_RATES, "photon_time_index": 2,
                  "photon_particles": PARTICLES, "bounds_sha256_ascending": bounds_hash}
        for k, v in expect.items():
            if c.get(k) != v:
                problems.append(f"{key}: config.{k} = {c.get(k)!r}, expected {v!r}")
        if not c.get("bounds_max_rel_diff_vs_groups_json", 1.0) <= 1e-12:
            problems.append(f"{key}: library groups differ from the 709-group file")
        arm = a["arm"]
        if arm == "openmc_flux":
            if c.get("reaction_rate_mode") != "flux" or c.get("micro_kwargs_keys") != ["energies", "reaction_rate_mode"]:
                problems.append(f"{key}: flux arm is not exactly reaction_rate_mode=flux with energies")
            if c.get("energy_groups_in_flux") != 709:
                problems.append(f"{key}: flux arm tallied {c.get('energy_groups_in_flux')} groups")
            if not c.get("n_micro_nuclides", 0) >= MIN_FLUX_NUCLIDES:
                problems.append(f"{key}: flux arm collapsed only {c.get('n_micro_nuclides')} nuclides")
        elif arm == "openmc_default":
            if c.get("reaction_rate_mode") != "direct" or c.get("micro_kwargs_keys"):
                problems.append(f"{key}: default arm was not OpenMC's defaults")
            if c.get("energy_groups_in_flux") != 1:
                problems.append(f"{key}: default arm tallied {c.get('energy_groups_in_flux')} groups")
            if not c.get("n_micro_nuclides", 0) >= MIN_FLUX_NUCLIDES:
                problems.append(f"{key}: default arm tallied only {c.get('n_micro_nuclides')} nuclides")
        else:
            if c.get("n_micro_nuclides") != 1 or c.get("energy_groups_in_flux") != 709:
                problems.append(f"{key}: ACTINV arm is not the adapter's slim 709-group step 1")
        if c.get("n_regions") != a["cells"]:
            problems.append(f"{key}: {c.get('n_regions')} activation regions for a {a['cells']}-cell mesh")
    flux_n = {a["config"]["n_micro_nuclides"] for a in attempts_of(rec, "openmc_flux") if a.get("status") == "ok"}
    def_n = {a["config"]["n_micro_nuclides"] for a in attempts_of(rec, "openmc_default") if a.get("status") == "ok"}
    if flux_n and def_n and flux_n != def_n:
        problems.append(f"flux and default arms cover different nuclide sets: {sorted(flux_n)} vs {sorted(def_n)}")
    return result(not problems, problems=problems)


# ---------------------------------------------------------------------------------------------- G4

def in_band(x, band):
    return band[0] <= x <= band[1]


def gate_g4(rec):
    problems, seen = [], {"pairs": [], "default": [], "bad_sizes": []}

    def bad(cells, text):
        problems.append(text)
        if cells not in seen["bad_sizes"]:
            seen["bad_sizes"].append(cells)

    for cells in sorted(DIMENSIONS):
        flux = {a["rep"]: a for a in ok_runs(rec, "openmc_flux", cells)}
        act = {a["rep"]: a for a in ok_runs(rec, "actinv", cells)}
        for rep in sorted(set(flux) & set(act)):
            f, a = flux[rep], act[rep]
            dose = a["dose_per_photon_pSv_cm2"] / f["dose_per_photon_pSv_cm2"]
            photons = a["photons_per_s"] / f["photons_per_s"]
            track = a["config"]["flux_sum_n_cm_per_src"] / f["config"]["flux_sum_n_cm_per_src"]
            seen["pairs"].append({"cells": cells, "rep": rep, "dose_ratio": dose, "photon_ratio": photons,
                                  "track_length_ratio": track})
            if not in_band(dose, DOSE_BAND):
                bad(cells, f"{cells} cells rep {rep}: dose ratio {dose:.4f} outside {DOSE_BAND}")
            if not in_band(photons, PHOTON_BAND):
                bad(cells, f"{cells} cells rep {rep}: photon strength ratio {photons:.3f} outside {PHOTON_BAND}")
            if abs(track - 1.0) > FLUX_SUM_TOL:
                bad(cells, f"{cells} cells rep {rep}: summed track length differs by {abs(track - 1):.3%}")
    default = ok_runs(rec, "openmc_default", 8)
    mine = [a for a in attempts_of(rec, "openmc_default", 8)]
    for arm in ("openmc_flux", "actinv"):
        for run in ok_runs(rec, arm, 8):
            if default:
                ratio = run["dose_per_photon_pSv_cm2"] / default[0]["dose_per_photon_pSv_cm2"]
                label = "default arm"
                good = in_band(ratio, DOSE_BAND)
            elif mine and mine[0].get("status") in CENSORED:
                ratio = run["dose_rate_Sv_h"] / DEMO_DOSE_SV_H
                label = "recorded demo dose"
                good = abs(ratio - 1.0) <= DEMO_BAND
            else:
                continue
            seen["default"].append({"arm": arm, "rep": run["rep"], "vs": label, "ratio": ratio})
            if not good:
                bad(8, f"{arm} rep {run['rep']} at 8 cells: dose ratio {ratio:.4f} against the {label}")
    if not seen["pairs"] and not seen["default"]:
        problems.append("no completed pair to check")
    return result(not problems, problems=problems, **seen)


# ---------------------------------------------------------------------------------------------- G5

def gate_g5(rec):
    problems = []
    for a in attempts_of(rec):
        if a.get("status") != "ok":
            continue
        key = (a["arm"], a["cells"], a["rep"])
        steps = [a.get("step1_s"), a.get("step2_s"), a.get("step3_s")]
        if not all(isinstance(s, (int, float)) and s > 0 for s in steps):
            problems.append(f"{key}: a step time is not positive")
            continue
        extra = a["wall_s"] - sum(steps)
        if extra < -0.5 or extra > WALL_SLACK_S:
            problems.append(f"{key}: wall time exceeds the steps by {extra:.1f} s")
    load = (rec.get("environment", {}).get("load_average_start") or [None])[0]
    if load is None or load > LOAD_MAX:
        problems.append(f"1-minute load average at start was {load}")
    return result(not problems, problems=problems, load_average_start=load)


# ---------------------------------------------------------------------------------------------- G6

def floor_claim(x):
    """Largest multiplier that may be quoted: one decimal below 10, whole numbers above, always rounded down."""
    return math.floor(x * 10.0) / 10.0 if x < 10.0 else float(math.floor(x))


def spread(values):
    return max(values) / min(values) if len(values) > 1 and min(values) > 0 else 1.0


def compute_claims(rec, g4):
    """Per-size outcome by the registered rule. A size has a claim only if its accuracy pairs exist and passed."""
    bad_sizes = set(g4["bad_sizes"])
    sizes, sentences = {}, []
    for cells in sorted(DIMENSIONS):
        act = ok_runs(rec, "actinv", cells)
        flux_all = [a for a in attempts_of(rec, "openmc_flux", cells) if a.get("status") in ("ok",) + CENSORED]
        if not act or not flux_all:
            sizes[cells] = {"class": "not_measured"}
            continue
        a_h1 = [h1(a) for a in act]
        f_h1 = [h1(a) for a in flux_all]
        paired = any(p["cells"] == cells for p in g4["pairs"])
        censored = any(a.get("status") in CENSORED for a in flux_all)
        low = min(f_h1) / max(a_h1)
        med = statistics.median(f_h1) / statistics.median(a_h1)
        out = {"actinv_h1_s": a_h1, "openmc_flux_h1_s": f_h1, "median_ratio": med, "worst_case_ratio": low,
               "openmc_censored": censored, "actinv_spread": spread(a_h1), "openmc_spread": spread(f_h1)}
        # per arm at this size, in GiB, the largest over completed runs: the working set (ru_maxrss of the run or of
        # its children, whichever is larger) and the scope's memory.peak, which also counts page cache from reading
        # data files and so fills toward the cap whenever the files are larger than it
        out["peak_rss_gib"] = {arm: max((max(a["peak_rss_mib"].values()) / 1024 for a in ok_runs(rec, arm, cells)
                                         if a.get("peak_rss_mib")), default=None) for arm in ("actinv", "openmc_flux")}
        out["memory_peak_gib"] = {arm: max((a["memory_peak_bytes"] / GIB for a in ok_runs(rec, arm, cells)
                                            if isinstance(a.get("memory_peak_bytes"), int)), default=None)
                                  for arm in ("actinv", "openmc_flux")}
        s1 = statistics.median(a["step1_s"] for a in act)
        act_only = [a["step2_s"] for a in act]
        flux_act = [a["step1_s"] + a["step2_s"] - s1 for a in flux_all if a.get("status") == "ok"]
        if flux_act and all(x > 0 for x in flux_act):
            out["activation_only_worst_case_ratio"] = min(flux_act) / max(act_only)
            out["activation_only_median_ratio"] = statistics.median(flux_act) / statistics.median(act_only)
        if cells in bad_sizes or (not paired and not censored):
            out["class"] = "withheld_accuracy"
        elif low >= 1.0:
            out["class"] = "faster"
            out["quotable_multiplier"] = floor_claim(low)
            sentences.append(f"ACTINV is at least {floor_claim(low):g}x faster than OpenMC 0.15.3 flux mode from model to "
                             f"photon source (steps 1 and 2) at {cells} mesh cells, worst case over {len(act)} ACTINV and "
                             f"{len(flux_all)} OpenMC runs{' (OpenMC runs censored at their limit)' if censored else ''}")
        elif med >= 1.0:
            out["class"] = "overlap"
        else:
            out["class"] = "slower"
            sentences.append(f"ACTINV is slower than OpenMC 0.15.3 flux mode at {cells} mesh cells "
                             f"(median {1.0 / med:.2f}x of OpenMC's speed)")
        sizes[cells] = out
    default = ok_runs(rec, "openmc_default", 8)
    act8 = ok_runs(rec, "actinv", 8)
    reference = None
    if default and act8:
        d = h1(default[0])
        reference = {"openmc_default_h1_s": d, "worst_case_ratio_vs_actinv": d / max(h1(a) for a in act8),
                     "quotable_multiplier": floor_claim(d / max(h1(a) for a in act8))}
        sentences.append(f"Against OpenMC 0.15.3 in its default tally mode (per-nuclide reaction-rate tallies, one "
                         f"energy group), one run at 8 mesh cells, ACTINV's steps 1 and 2 are at least "
                         f"{reference['quotable_multiplier']:g}x faster; this is not OpenMC's fastest mode")
    claims_ok = [c for c, s in sizes.items() if s["class"] == "faster"]
    measured = [c for c, s in sizes.items() if s["class"] != "not_measured"]
    general = None
    if measured and len(claims_ok) == len(measured):
        m = min(sizes[c]["quotable_multiplier"] for c in measured)
        general = {"cells_measured": measured, "quotable_multiplier": m}
        sentences.append(f"ACTINV is at least {m:g}x faster than OpenMC 0.15.3 flux mode at every measured size "
                         f"({', '.join(str(c) for c in measured)} mesh cells)")
    return {"sizes": sizes, "reference_default": reference, "general": general, "allowed_sentences": sentences}


SPEED_LINE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:x|×|times)\b[^.\n]*", re.I)


def scan_release_notes(text, claims):
    """Every line comparing speed with OpenMC must name the OpenMC mode and a measured cell count, and any multiplier
    must not exceed what the rule allows for that mode and size ('every measured size' lines use the general claim)."""
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        if "openmc" not in low:
            continue
        hits = [float(m.group(1)) for m in SPEED_LINE.finditer(line)]
        hits = [x for x in hits if not re.search(rf"\b{int(x)}(?:\.0)?\s*(?:mesh\s*)?cells", low)]
        if not (hits or "faster" in low or "slower" in low):
            continue
        mode_flux, mode_default = "flux" in low, "default" in low
        size = re.search(r"\b(8|64|512)\s*(?:mesh\s*)?cells?", low)
        every = "every measured size" in low or "at every" in low
        if not (mode_flux or mode_default):
            problems.append(f"line {n}: speed comparison names no OpenMC mode")
            continue
        if mode_default and not mode_flux:
            limit = (claims["reference_default"] or {}).get("quotable_multiplier", 0.0)
            if not size or int(size.group(1)) != 8:
                problems.append(f"line {n}: the default-mode reference was measured at 8 cells only")
        elif every:
            limit = (claims["general"] or {}).get("quotable_multiplier", 0.0)
        elif size:
            s = claims["sizes"].get(int(size.group(1)), {})
            limit = s.get("quotable_multiplier", 0.0) if s.get("class") == "faster" else 0.0
        else:
            problems.append(f"line {n}: speed comparison names no measured cell count")
            continue
        for x in hits:
            if x > limit + 1e-9:
                problems.append(f"line {n}: {x:g}x exceeds the allowed {limit:g}x")
        if "faster" in low and not hits and limit == 0.0 and "slower" not in low:
            problems.append(f"line {n}: no 'faster' claim is allowed here")
    return problems


def gate_g6(rec, g4, notes_text=None):
    claims = compute_claims(rec, g4)
    problems = []
    for cells, s in claims["sizes"].items():
        if s["class"] == "not_measured" and ok_runs(rec, "actinv", cells) and any(
                a.get("status") in ("ok",) + CENSORED for a in attempts_of(rec, "openmc_flux", cells)):
            problems.append(f"{cells} cells measured but unclassified")
    if notes_text is not None:
        problems += scan_release_notes(notes_text, claims)
    return result(not problems, problems=problems, notes_checked=notes_text is not None, claims=claims)


# ---------------------------------------------------------------------------------------------- driver

def evaluate(rec, actual_repo, bounds_hash, hash_gate, pins=None, notes_text=None):
    g1 = gate_g1(rec, actual_repo, pins)
    g2 = gate_g2(rec)
    g3 = gate_g3(rec, bounds_hash)
    g4 = gate_g4(rec)
    g5 = gate_g5(rec)
    g6 = gate_g6(rec, g4, notes_text)
    gates = {"G0": hash_gate, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
    return gates


# ---------------------------------------------------------------------------------------------- selftest

def synth(speed=10.0, cells_sizes=(8, 64, 512)):
    """A passing record: ACTINV 20 s for steps 1+2, OpenMC flux `speed` times that, default 40x."""
    pins = {k: "a" * 64 for k in PINS}
    repo = {k: "b" * 64 for k in REPO_INPUTS}
    bh = "c" * 64
    inputs = {k: {"path": k, "sha256": pins[k]} for k in ("chain_xml", "actinv_library", "decay_primary",
                                                           "decay_fallback", "cross_sections_xml")}
    inputs["actinv_binary"] = {"path": "x", "sha256": "d" * 64}
    inputs.update({k: {"path": k, "sha256": repo[k]} for k in repo})
    rec = {"schema": SCHEMA, "smoke": False, "inputs": inputs, "skips": [], "attempts": [],
           "environment": {"cpu_model": "x", "logical_cpus": 8, "mem_total_kib": 1, "kernel": "7", "actinv_version": "a",
                           "openmc_version": OPENMC_VERSION, "child_threads": 2, "env": {"RAYON_NUM_THREADS": "2"},
                           "git_head": "e" * 40, "git_dirty_paths": "", "xs_archive_sha256": pins["xs_archive"],
                           "load_average_start": [0.5, 0.5, 0.5],
                           "cgroup": {"memory_max": str(6 * GIB), "memory_swap_max": "0", "pids_max": "128",
                                      "cpu_max": "200000 100000"}},
           "plan": {"memory_max_gib": dict(MEMORY_MAX_GIB)}}
    for arm, cells, rep in plan_items():
        if cells not in cells_sizes:
            continue
        scale = cells / 8.0
        s1, s2 = 8.0, 12.0 * (1 + scale / 10)
        if arm == "openmc_flux":
            s1, s2 = 8.0, (20.0 * speed - 8.0)
        if arm == "openmc_default":
            s1, s2 = 700.0, 100.0
        s1, s2 = s1 * (1 + 0.02 * rep), s2 * (1 + 0.02 * rep)
        cfg = {"openmc_version": OPENMC_VERSION, "arm": arm, "particles": PARTICLES, "batches": BATCHES,
               "seed": SEED_BASE + rep, "photon_seed": SEED_BASE + rep + 500, "threads": 2, "pool_processes": 2,
               "mesh_dimension": [DIMENSIONS[cells]] * 3, "timesteps_s": TIMESTEPS, "source_rates": SOURCE_RATES,
               "photon_time_index": 2, "photon_particles": PARTICLES, "bounds_sha256_ascending": bh,
               "bounds_max_rel_diff_vs_groups_json": 0.0, "n_regions": cells, "flux_sum_n_cm_per_src": 10.0,
               "reaction_rate_mode": "direct" if arm == "openmc_default" else "flux" if arm == "openmc_flux" else "direct",
               "micro_kwargs_keys": ["energies", "reaction_rate_mode"] if arm == "openmc_flux" else [],
               "energy_groups_in_flux": 1 if arm == "openmc_default" else 709,
               "n_micro_nuclides": 1 if arm == "actinv" else 500, "n_micro_reactions": 9}
        rec["attempts"].append({"arm": arm, "cells": cells, "rep": rep, "status": "ok", "exit_code": 0,
                                "step1_s": s1, "step2_s": s2, "step3_s": 5.0, "wall_s": s1 + s2 + 5.0 + 20.0,
                                "dose_per_photon_pSv_cm2": 3.3e15 * (1 + 0.01 * rep), "dose_sd": 3e13,
                                "photons_per_s": 7e15, "dose_rate_Sv_h": 1030.0, "config": cfg,
                                "memory_max_gib": MEMORY_MAX_GIB[arm], "memory_peak_bytes": GIB,
                                "peak_rss_mib": {"self": 512.0, "children": 256.0},
                                "cgroup": {"memory_max": str(MEMORY_MAX_GIB[arm] * GIB), "memory_swap_max": "0",
                                           "pids_max": "128", "cpu_max": "200000 100000"}})
    return rec, repo, bh, pins


def selftest():
    def expect(cond, what):
        if not cond:
            raise SystemExit(f"selftest failed: {what}")

    ok_hash = result(True)

    def run(rec, repo, bh, pins, notes=None):
        return evaluate(rec, repo, bh, ok_hash, pins, notes)

    def failed(gates, name):
        return not gates[name]["pass"]

    rec, repo, bh, pins = synth(10.0)
    gates = run(rec, repo, bh, pins)
    # a good record passes everything; the default arm is not slower-gated
    for g, v in gates.items():
        expect(v["pass"], f"a good record passes {g}: {v.get('problems')}")
    claims = gates["G6"]["claims"]
    expect(all(claims["sizes"][c]["class"] == "faster" for c in DIMENSIONS), "all sizes classed faster")
    expect(claims["sizes"][8]["quotable_multiplier"] < 10.0 * 1.0, "multiplier rounded down, never up")
    expect(claims["general"] is not None and claims["general"]["quotable_multiplier"] <= min(
        claims["sizes"][c]["worst_case_ratio"] for c in DIMENSIONS) + 1e-9, "general claim is the weakest size")
    expect(claims["reference_default"] is not None, "default reference claim present")
    expect(floor_claim(9.99) == 9.9 and floor_claim(10.7) == 10.0, "floor_claim rounds down")

    # G0
    paths = [PROTOCOL]
    expect(gate_g0(["abc  protocols/ACTINV-P163_PROTOCOL.md"], paths, {PROTOCOL.name: "abc"})["pass"], "G0 hit")
    expect(not gate_g0(["abc  protocols/ACTINV-P162_PROTOCOL.md"], paths, {PROTOCOL.name: "abc"})["pass"], "G0 miss")
    expect(not gate_g0([], paths, {PROTOCOL.name: "abc"})["pass"], "G0 empty log")

    # G1 mutations
    def mutate(fn, gate):
        r, rp, b, p = synth(10.0)
        fn(r, rp, b, p)
        return failed(run(r, rp, b, p), gate)
    expect(mutate(lambda r, *_: r["environment"]["cgroup"].update(memory_max="max"), "G1"), "unbounded memory refused")
    expect(mutate(lambda r, *_: r["environment"]["cgroup"].update(memory_swap_max="max"), "G1"), "swap refused")
    expect(mutate(lambda r, *_: r["environment"]["cgroup"].update(cpu_max="max 100000"), "G1"), "no CPU quota refused")
    expect(mutate(lambda r, *_: r["environment"]["cgroup"].update(cpu_max="400000 100000"), "G1"), "400% refused")

    def first(arm):
        return lambda r, *_: next(a for a in r["attempts"] if a["arm"] == arm)
    expect(mutate(lambda r, *_: first("actinv")(r)["cgroup"].update(memory_max=str(9 * GIB)), "G1"),
           "an ACTINV run above 6 GiB refused")
    expect(mutate(lambda r, *_: first("openmc_flux")(r)["cgroup"].update(memory_max=str(10 * GIB)), "G1"),
           "an OpenMC run above its cap refused")
    expect(mutate(lambda r, *_: first("openmc_flux")(r)["cgroup"].update(memory_swap_max="max"), "G1"),
           "a run with swap refused")
    expect(mutate(lambda r, *_: first("openmc_flux")(r).update(memory_max_gib=12), "G1"), "a changed cap refused")
    expect(mutate(lambda r, *_: first("openmc_flux")(r).update(memory_peak_bytes=None), "G1"), "a missing peak refused")
    expect(mutate(lambda r, *_: r["plan"]["memory_max_gib"].update(openmc_flux=16), "G1"), "a changed plan refused")
    expect(run(*synth(10.0))["G6"]["claims"]["sizes"][8]["memory_peak_gib"] == {"actinv": 1.0, "openmc_flux": 1.0},
           "memory peaks reported per arm and size")
    expect(run(*synth(10.0))["G6"]["claims"]["sizes"][8]["peak_rss_gib"] == {"actinv": 0.5, "openmc_flux": 0.5},
           "working sets reported per arm and size")
    expect(mutate(lambda r, *_: r["environment"].update(openmc_version="0.16.0"), "G1"), "wrong OpenMC refused")
    expect(mutate(lambda r, *_: r.update(smoke=True), "G1"), "smoke run refused")
    expect(mutate(lambda r, *_: r["environment"].update(git_dirty_paths=" M controls/x.py"), "G1"), "dirty tree refused")
    expect(mutate(lambda r, *_: r["inputs"]["chain_xml"].update(sha256="0" * 64), "G1"), "chain pin enforced")
    expect(mutate(lambda r, rp, b, p: r["inputs"]["harness"].update(sha256="0" * 64), "G1"), "harness drift caught")
    expect(mutate(lambda r, rp, b, p: p.update(cross_sections_xml="TBD") or r["inputs"]["cross_sections_xml"].update(
        sha256="TBD"), "G1"), "an unset pin refused")
    expect(mutate(lambda r, *_: r["environment"].update(child_threads=8), "G1"), "threads must be 2")

    # G2 mutations
    expect(mutate(lambda r, *_: r["attempts"].pop(0), "G2"), "missing run caught")
    expect(mutate(lambda r, *_: r["attempts"].append(dict(r["attempts"][0])), "G2"), "duplicate run caught")
    expect(mutate(lambda r, *_: r["attempts"].append(dict(r["attempts"][0], rep=9)), "G2"), "run outside plan caught")
    expect(mutate(lambda r, *_: r["attempts"][0].update(status="failed", exit_code=1), "G2"), "ACTINV failure caught")
    flux8 = next(i for i, a in enumerate(rec["attempts"]) if a["arm"] == "openmc_flux")
    expect(mutate(lambda r, *_: r["attempts"][flux8].update(status="failed", exit_code=1), "G2"),
           "an OpenMC failure is not a censoring")
    # skip recorded although the rule says run
    def bogus_skip(r, *_):
        idx = next(i for i, a in enumerate(r["attempts"]) if a["arm"] == "openmc_flux" and a["cells"] == 64 and a["rep"] == 1)
        a = r["attempts"].pop(idx)
        r["skips"].append({"arm": a["arm"], "cells": a["cells"], "rep": a["rep"]})
    expect(mutate(bogus_skip, "G2"), "a skip the rule does not require is refused")

    # a legitimate censoring and rule-skip: flux at 8 cells hit the limit, so 64 and 512 are skipped
    def censor_8(r, *_):
        new = []
        for a in r["attempts"]:
            if a["arm"] == "openmc_flux" and a["cells"] == 8:
                a.update(status="timeout", exit_code=-15, wall_s=3600.0, censored=True, step1_s=None, step2_s=None,
                         step3_s=None)
            if a["arm"] == "openmc_flux" and a["cells"] in (64, 512):
                r["skips"].append({"arm": a["arm"], "cells": a["cells"], "rep": a["rep"]})
                continue
            new.append(a)
        r["attempts"] = new
    r2, rp2, b2, p2 = synth(10.0)
    censor_8(r2)
    g = run(r2, rp2, b2, p2)
    expect(g["G2"]["pass"], f"censored 8-cell run with rule skips passes G2: {g['G2']['problems']}")
    expect(g["G6"]["claims"]["sizes"][8]["openmc_censored"], "censoring recorded in the claims")
    expect(g["G6"]["claims"]["sizes"][64]["class"] == "not_measured", "a skipped size yields no claim")
    r3, rp3, b3, p3 = synth(10.0)
    censor_8(r3)
    r3["skips"] = [s for s in r3["skips"] if s["cells"] != 512]
    expect(failed(run(r3, rp3, b3, p3), "G2"), "a rule-required skip that is not recorded is caught")

    # G3 mutations
    def flux_cfg(key, value):
        def f(r, *_):
            for a in r["attempts"]:
                if a["arm"] == "openmc_flux":
                    a["config"][key] = value
        return f
    expect(mutate(flux_cfg("reaction_rate_mode", "direct"), "G3"), "flux arm must use flux mode")
    expect(mutate(flux_cfg("micro_kwargs_keys", ["energies", "nuclides", "reaction_rate_mode"]), "G3"),
           "flux arm may not throttle nuclides")
    expect(mutate(flux_cfg("n_micro_nuclides", 12), "G3"), "flux arm must cover the chain")
    expect(mutate(flux_cfg("energy_groups_in_flux", 1), "G3"), "flux arm must use 709 groups")
    expect(mutate(flux_cfg("seed", 5), "G3"), "seed must match the registered rule")
    expect(mutate(flux_cfg("threads", 8), "G3"), "OpenMC threads must be 2")
    expect(mutate(flux_cfg("bounds_sha256_ascending", "0" * 64), "G3"), "group structure pinned")
    expect(mutate(lambda r, *_: [a["config"].update(n_micro_nuclides=3) for a in r["attempts"] if a["arm"] == "actinv"],
                  "G3"), "the ACTINV arm must be the slim adapter")
    expect(mutate(lambda r, *_: [a["config"].update(n_micro_nuclides=400) for a in r["attempts"]
                                  if a["arm"] == "openmc_default"], "G3"), "arms must cover the same nuclides")

    # G4 mutations
    def dose(arm, factor):
        def f(r, *_):
            for a in r["attempts"]:
                if a["arm"] == arm:
                    a["dose_per_photon_pSv_cm2"] *= factor
        return f
    expect(mutate(dose("actinv", 1.2), "G4"), "dose 20% off caught")
    expect(mutate(dose("actinv", 0.9), "G4"), "dose 10% low caught")
    expect(mutate(dose("openmc_flux", 1.2), "G4"), "flux arm off the default arm caught")
    expect(mutate(lambda r, *_: [a.update(photons_per_s=a["photons_per_s"] * 3) for a in r["attempts"]
                                  if a["arm"] == "actinv"], "G4"), "photon strength sanity band")
    expect(mutate(lambda r, *_: [a["config"].update(flux_sum_n_cm_per_src=12.0) for a in r["attempts"]
                                  if a["arm"] == "actinv"], "G4"), "neutron track length must agree")
    # default arm censored: the recorded demo dose stands in
    def default_oom(r, *_):
        for a in r["attempts"]:
            if a["arm"] == "openmc_default":
                a.update(status="killed", exit_code=-9, wall_s=700.0, censored=True, step1_s=None, step2_s=None,
                         step3_s=None)
    r4, rp4, b4, p4 = synth(10.0)
    default_oom(r4)
    g = run(r4, rp4, b4, p4)
    expect(g["G4"]["pass"], f"synthetic dose 1030 Sv/h is within 7% of the recorded demo dose: {g['G4']['problems']}")
    expect(g["G2"]["pass"], "a cgroup-killed default arm is a recorded censoring")
    expect(g["G6"]["claims"]["reference_default"] is None, "no default reference when it did not finish")
    expect(g["G4"]["default"] and all(d["vs"] == "recorded demo dose" for d in g["G4"]["default"]),
           "recorded demo dose used when default censored")

    # G5 mutations
    expect(mutate(lambda r, *_: r["attempts"][0].update(step2_s=-1.0), "G5"), "negative step caught")
    expect(mutate(lambda r, *_: r["attempts"][0].update(wall_s=r["attempts"][0]["wall_s"] + 500), "G5"),
           "wall time far above steps caught")
    expect(mutate(lambda r, *_: r["environment"].update(load_average_start=[6.0, 4.0, 3.0]), "G5"), "busy machine caught")

    # G6 decision rule
    slow, rs, bs, ps = synth(0.5)
    g = run(slow, rs, bs, ps)
    expect(g["G6"]["pass"] and all(s["class"] == "slower" for s in g["G6"]["claims"]["sizes"].values()),
           "slower than OpenMC is classed slower, withdrawn, and still a PASS")
    expect(g["G6"]["claims"]["general"] is None, "no general speed claim when slower")
    expect(not any("faster than OpenMC 0.15.3 flux" in s for s in g["G6"]["claims"]["allowed_sentences"]),
           "no faster-than sentence when slower")
    tie, rt, bt, pt = synth(1.0)
    for a in tie["attempts"]:
        if a["arm"] == "openmc_flux" and a["rep"] == 0:
            a["step2_s"] *= 1.4
    g = run(tie, rt, bt, pt)
    expect(g["G6"]["claims"]["sizes"][8]["class"] in ("overlap", "faster", "slower"), "tie classed")
    ov, ro, bo, po = synth(2.0)
    for a in ov["attempts"]:
        if a["arm"] == "actinv" and a["cells"] == 8 and a["rep"] == 2:
            a["step2_s"] *= 4  # one slow ACTINV repetition makes the worst case cross 1
    g = run(ov, ro, bo, po)
    expect(g["G6"]["claims"]["sizes"][8]["class"] == "overlap", "overlapping repetitions give no claim")
    expect(g["G6"]["claims"]["general"] is None, "an overlap at one size blocks the general claim")

    # release notes
    base, rb, bb, pb = synth(10.0)
    claims = run(base, rb, bb, pb)["G6"]["claims"]
    cap = claims["sizes"][64]["quotable_multiplier"]
    good_note = f"ACTINV is {cap:g}x faster than OpenMC flux mode at 64 mesh cells (steps 1 and 2).\nOther text.\n"
    expect(scan_release_notes(good_note, claims) == [], "an in-bound claim passes the scan")
    over = f"ACTINV is {cap * 2:g}x faster than OpenMC flux mode at 64 mesh cells.\n"
    expect(scan_release_notes(over, claims), "an inflated claim is caught")
    expect(scan_release_notes("ACTINV is 3x faster than OpenMC flux mode.\n", claims), "no cell count is caught")
    expect(scan_release_notes("ACTINV is faster than OpenMC at 8 mesh cells.\n", claims), "no mode is caught")
    demo_line = "ACTINV is 44x faster than OpenMC at 8 mesh cells.\n"
    expect(scan_release_notes(demo_line, claims), "the old demo figure without a mode is caught")
    default_note = "Against OpenMC default tally mode, 8 mesh cells, ACTINV is 30x faster.\n"
    expect(scan_release_notes(default_note, claims) == [], "a default-mode claim within the reference passes")
    expect(scan_release_notes("Against OpenMC default tally mode, 8 mesh cells, ACTINV is 90x faster.\n", claims),
           "a default-mode claim above the reference is caught")
    every_ok = f"ACTINV is {claims['general']['quotable_multiplier']:g}x faster than OpenMC flux mode at every measured size.\n"
    expect(scan_release_notes(every_ok, claims) == [], "the general claim passes")
    too_big = every_ok.replace(f"{claims['general']['quotable_multiplier']:g}x", "50x")
    expect(scan_release_notes(too_big, claims), "the general claim is bounded by the weakest size")
    sl, rl, bl, pl = synth(0.5)
    cl = run(sl, rl, bl, pl)["G6"]["claims"]
    expect(scan_release_notes("ACTINV is 3x faster than OpenMC flux mode at 8 mesh cells.\n", cl),
           "a faster claim is refused when ACTINV measured slower")
    expect(not run(base, rb, bb, pb, notes="ACTINV is 3000x faster than OpenMC flux mode at 8 mesh cells.\n")["G6"]["pass"],
           "G6 fails on a bad release note")

    # groups hash helper agrees with itself
    expect(len(groups_sha()) == 64, "groups hash")
    print("P163 selftest: PASS")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--record", default=str(RECORD))
    ap.add_argument("--release-notes")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    rec = json.loads(Path(args.record).read_text())
    notes = Path(args.release_notes).read_text() if args.release_notes else None
    gates = evaluate(rec, repo_hashes(), groups_sha(), gate_g0(), notes_text=notes)
    ok = all(g["pass"] for g in gates.values())
    out = {"schema": "actinv-p163-verdict-1", "protocol": "protocols/ACTINV-P163_PROTOCOL.md",
           "protocol_sha256": sha256_file(PROTOCOL), "checker_sha256": sha256_file(Path(__file__)),
           "record": str(args.record), "record_sha256": sha256_file(args.record),
           "gates": {k: ("PASS" if v["pass"] else "FAIL") for k, v in gates.items()} | {"G7": "external"},
           "details": gates, "allowed_claims": gates["G6"]["claims"], "pass": ok}
    VERDICT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    for k, v in gates.items():
        print(f"P163 {k}: {'PASS' if v['pass'] else 'FAIL'}")
        for p in v.get("problems", [])[:8]:
            print(f"   {p}")
    for s in gates["G6"]["claims"]["allowed_sentences"]:
        print(f"   allowed: {s}")
    print(f"P163: {'PASS' if ok else 'FAIL'} ({VERDICT})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
