#!/usr/bin/env python3
"""P191 checker (protocols/ACTINV-P191_PROTOCOL.md): ACTINV's R2S with and without mixture self-shielding against
OpenMC 0.15.3's own R2S with direct reaction-rate tallies, on P181's model at 8 mesh elements, on the same
ENDF/B-VIII.1 cross sections. Reads results/p191_record.json written by `controls/p191_harness.py assemble` and writes
results/p191_verdict.json. G0-G3 are evaluated here; G4 (CI on the pushed evidence commit) is external.

  G0  The SHA-256 of the protocol, controls/p191_harness.py and this checker are in protocols/protocol_hash.txt.
  G1  Provenance: P181's pinned inputs (chain, cross sections, decay data, archive) with the ENDF/B-VIII.1 709-group
      library of P181 Amendment A; the harness, P181's harness, protocol, adapter, _defaults.py, demo and group files
      equal to this checkout's; every arm run at the registration commit (the oldest commit that added the protocol)
      on a clean tracked tree; all four arms present; the harness in the AGENTS.md scope and every run scope at
      10 GiB, no swap, 128 tasks, 2 CPUs; P181's plan; the arms' configurations as registered; not a rehearsal.
  G2  Completeness: openmc_direct, actinv_mixture and actinv_plain exited 0, completed all three steps and are not
      censored, with finite times, a dose, a photon strength and a flux. openmc_flux may be censored (context only).
  G3  The decision, computed and always reported: dose ratios to openmc_direct with propagated 1-sigma, photon
      strength and flux ratios, the AGREES / DIFFERS label (|R_m - 1| <= 0.10, P181's band) and the shielding label.
      A measurement, not a gate.

Verdict: P191-PASS iff G0, G1 and G2 pass (G4 external). The allowed sentences are filled from the record.

Usage: check_p191.py [--record FILE] [--no-write]
       check_p191.py --selftest
"""
import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p181 as c181  # noqa: E402
import check_p181a as c181a  # noqa: E402
import p191_harness as h191  # noqa: E402

h = h191.h
GIB = 1 << 30
PROTOCOL_REL = "protocols/ACTINV-P191_PROTOCOL.md"
HASHED = [PROTOCOL_REL, "controls/p191_harness.py", "controls/check_p191.py"]
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = h191.RECORD
VERDICT = ROOT / "results" / "p191_verdict.json"

OPENMC_VERSION = "0.15.3"
ACTINV_VERSION_PREFIX = "actinv 1."
# SHA-256 of the actinv binary every arm must record (built from the registration commit's sources)
ACTINV_BINARY_SHA256 = "fa21795bd0137d8870f435d2618ceadd1493bec54e05f6b526eb8978151c8662"  # built from df1d494's sources
AGREE_TOL = 0.10  # P181's dose band
SHIELD_SIGMAS = 2.0
PINS = {k: c181.PINS[k] for k in ("chain_xml", "cross_sections_xml", "decay_primary", "decay_fallback")}
PINS["actinv_library"] = c181a.ENDF_LIBRARY
REPO_INPUTS = {"harness": "controls/p191_harness.py", "p181_harness": "controls/p181_harness.py",
               "protocol": PROTOCOL_REL, **{k: c181.REPO_INPUTS[k] for k in
                                            ("adapter", "adapter_defaults", "demo_steel_cube", "groups_json")}}
CORE = ("openmc_direct", "actinv_mixture", "actinv_plain")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def round_up(x, digits=3):
    return math.ceil(x * 10 ** digits - 1e-9) / 10 ** digits


# ------------------------------------------------------------------------------------------------------ gates

def g0(root=ROOT, log=None):
    log = HASH_LOG.read_text() if log is None else log
    problems = []
    for rel in HASHED:
        path = Path(root) / rel
        if not path.is_file():
            problems.append(f"missing {rel}")
        elif f"{sha256(path)}  {rel}" not in log:
            problems.append(f"{rel} hash not in protocol_hash.txt")
    return problems


def registration_commit():
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--", PROTOCOL_REL], capture_output=True,
                         text=True, cwd=ROOT).stdout.split()
    return out[-1] if out else None  # git log lists newest first


def g1(record, repo=None, registration=None):
    problems = []
    repo = repo if repo is not None else {k: sha256(ROOT / v) for k, v in REPO_INPUTS.items()}
    registration = registration if registration is not None else registration_commit()
    if record.get("schema") != h191.RECORD_SCHEMA:
        problems.append("wrong record schema")
    if record.get("rehearsal"):
        problems.append("a rehearsal record")
    arms = record.get("arms", {})
    for arm in h191.ARMS:
        if arm not in arms:
            problems.append(f"arm {arm} is missing")
    if registration is None:
        problems.append("no registration commit found")
    binaries = set()
    for arm in h191.ARMS:
        a = arms.get(arm)
        if a is None:
            continue
        tag = f"{arm}:"
        if a.get("schema") != h191.SCHEMA or a.get("arm") != arm:
            problems.append(f"{tag} wrong arm schema or name")
        if a.get("rehearsal"):
            problems.append(f"{tag} a rehearsal arm")
        inputs = a.get("inputs", {})
        for name, digest in {**PINS, **repo}.items():
            if inputs.get(name, {}).get("sha256") != digest:
                problems.append(f"{tag} input {name} hash is not the pin")
        if ACTINV_BINARY_SHA256 is not None and inputs.get("actinv_binary", {}).get("sha256") != ACTINV_BINARY_SHA256:
            problems.append(f"{tag} actinv binary is not the pin")
        if arm in h191.ACTINV_ARMS:
            binaries.add(inputs.get("actinv_binary", {}).get("sha256"))
        env = a.get("environment", {})
        if env.get("xs_archive_sha256") != c181.XS_ARCHIVE:
            problems.append(f"{tag} cross-section archive digest is not the pin")
        if env.get("openmc_version") != OPENMC_VERSION:
            problems.append(f"{tag} OpenMC is not {OPENMC_VERSION}")
        if arm in h191.ACTINV_ARMS and not str(env.get("actinv_version", "")).startswith(ACTINV_VERSION_PREFIX):
            problems.append(f"{tag} no ACTINV version")
        if env.get("git_head") != registration:
            problems.append(f"{tag} not run at the registration commit")
        if env.get("git_dirty_paths"):
            problems.append(f"{tag} tracked tree was not clean")
        if not c181.scope_ok(env.get("cgroup"), 6):
            problems.append(f"{tag} harness cgroup is not the bounded scope")
        plan = a.get("plan", {})
        if (plan.get("memory_max_gib") != h.MEMORY_MAX_GIB or plan.get("limit_s") != h.LIMIT_S
                or plan.get("dim") != h191.DIM or plan.get("particles") != h.PARTICLES
                or plan.get("batches") != h.BATCHES or plan.get("seed") != h.SEED
                or plan.get("timesteps_s") != h.TIMESTEPS or plan.get("source_rates") != h.SOURCE_RATES
                or plan.get("photon_time_index") != h.PHOTON_TIME_INDEX):
            problems.append(f"{tag} plan differs from the registered limits, size or transport")
        cg = a.get("cgroup")
        if cg is not None and not (c181.scope_ok(cg, h.MEMORY_MAX_GIB)
                                   and int(cg["memory_max"]) == h.MEMORY_MAX_GIB * GIB):
            problems.append(f"{tag} run scope is not 10 GiB / no swap / 128 tasks / 2 CPUs")
        if a.get("memory_max_gib") != h.MEMORY_MAX_GIB:
            problems.append(f"{tag} memory_max_gib is not {h.MEMORY_MAX_GIB}")
        conf = a.get("config", {})
        if a.get("complete"):
            if (conf.get("particles") != h.PARTICLES or conf.get("mesh_dimension") != [h191.DIM] * 3
                    or conf.get("threads") != h.THREADS or conf.get("seed") != h.SEED):
                problems.append(f"{tag} run configuration is not P181's")
            if arm == "openmc_direct" and (conf.get("micro_kwargs_keys") or conf.get("reaction_rate_mode") != "direct"):
                problems.append(f"{tag} was not run with OpenMC's default (direct) tallies")
            if arm == "openmc_flux" and (conf.get("reaction_rate_mode") != "flux"
                                         or conf.get("energy_groups_in_flux") != c181.GROUPS):
                problems.append(f"{tag} was not run in flux mode on 709 groups")
            if arm in h191.ACTINV_ARMS and conf.get("energy_groups_in_flux") != c181.GROUPS:
                problems.append(f"{tag} flux is not on 709 groups")
        if arm in h191.ACTINV_ARMS and a.get("complete"):
            spec = a.get("mesh_spec") or {}
            present = bool(spec.get("self_shielding_present"))
            block = spec.get("self_shielding")
            if arm == "actinv_mixture" and not (present and isinstance(block, dict) and "mixture" in block):
                problems.append(f"{tag} the mesh spec has no mixture self_shielding block")
            if arm == "actinv_plain" and (present or block):
                problems.append(f"{tag} the mesh spec has a self_shielding block")
    if len(binaries) > 1:
        problems.append("the ACTINV arms used different binaries")
    return problems


def arm_ok(a):
    if not a:
        return False
    c = a.get("config", {})
    return bool(a.get("exit_code") == 0 and a.get("status") == "ok" and not a.get("censored") and a.get("complete")
                and finite(a.get("step1_s"), a.get("step2_s"), a.get("step3_s"), a.get("dose_rate_Sv_h"),
                           a.get("dose_rate_sd_Sv_h"), a.get("photons_per_s"), c.get("flux_sum_n_cm_per_src"))
                and a["dose_rate_Sv_h"] > 0 and a["photons_per_s"] > 0 and a["dose_rate_sd_Sv_h"] >= 0
                and c.get("flux_sum_n_cm_per_src") > 0)


def g2(record):
    arms = record.get("arms", {})
    problems = []
    for arm in CORE:
        a = arms.get(arm)
        if a is None:
            problems.append(f"{arm} is missing")
        elif a.get("censored"):
            problems.append(f"{arm} is censored ({a.get('censor_reason')})")
        elif not arm_ok(a):
            problems.append(f"{arm} did not complete with finite times, dose, strength and flux "
                            f"(status {a.get('status')}, exit {a.get('exit_code')})")
    return problems


# ------------------------------------------------------------------------------------------------------ decision

def ratio(num, den, num_sd, den_sd):
    r = num / den
    return r, abs(r) * math.hypot(num_sd / num, den_sd / den)


def decision(arms):
    """The computed outcome; None when the three core arms lack a usable dose."""
    if not all(arm_ok(arms.get(a)) for a in CORE):
        return None
    d, m, p = (arms[a] for a in ("openmc_direct", "actinv_mixture", "actinv_plain"))
    out = {"dose_Sv_h": {a: {"mean": arms[a]["dose_rate_Sv_h"], "sd": arms[a]["dose_rate_sd_Sv_h"]}
                         for a in h191.ARMS if arm_ok(arms.get(a))}}
    rel = {k: v["dose_rate_sd_Sv_h"] / v["dose_rate_Sv_h"] for k, v in (("d", d), ("m", m), ("p", p))}
    for key, a in (("R_m", m), ("R_p", p)):
        r, s = ratio(a["dose_rate_Sv_h"], d["dose_rate_Sv_h"], a["dose_rate_sd_Sv_h"], d["dose_rate_sd_Sv_h"])
        out[key] = {"ratio": r, "sigma": s}
    f = arms.get("openmc_flux")
    if arm_ok(f):
        r, s = ratio(f["dose_rate_Sv_h"], d["dose_rate_Sv_h"], f["dose_rate_sd_Sv_h"], d["dose_rate_sd_Sv_h"])
        out["R_f"] = {"ratio": r, "sigma": s}
    out["photon_strength_ratio"] = {"mixture": m["photons_per_s"] / d["photons_per_s"],
                                    "plain": p["photons_per_s"] / d["photons_per_s"]}
    if arm_ok(f):
        out["photon_strength_ratio"]["openmc_flux"] = f["photons_per_s"] / d["photons_per_s"]
    fd = d["config"]["flux_sum_n_cm_per_src"]
    out["flux_sum_ratio"] = {"mixture": m["config"]["flux_sum_n_cm_per_src"] / fd,
                             "plain": p["config"]["flux_sum_n_cm_per_src"] / fd}
    out["agreement"] = "AGREES" if abs(out["R_m"]["ratio"] - 1.0) <= AGREE_TOL + 1e-9 else "DIFFERS"
    # |ln R_m| - |ln R_p| = s_m (x_m - y) - s_p (x_p - y), x = ln of an ACTINV dose, y = ln of the direct dose, s the
    # sign of each ratio's log; the arms' errors are taken as independent (the shared flux seed makes this
    # conservative), the direct arm's error entering with weight (s_p - s_m)
    lm, lp = math.log(out["R_m"]["ratio"]), math.log(out["R_p"]["ratio"])
    sm, sp = (1.0 if lm >= 0 else -1.0), (1.0 if lp >= 0 else -1.0)
    diff = abs(lm) - abs(lp)
    sigma = math.sqrt(rel["m"] ** 2 + rel["p"] ** 2 + ((sp - sm) * rel["d"]) ** 2)
    out["shielding"] = {"abs_ln_Rm_minus_abs_ln_Rp": diff, "sigma": sigma, "sigmas": SHIELD_SIGMAS,
                        "label": ("SHIELDING HELPS" if diff < -SHIELD_SIGMAS * sigma else
                                  "SHIELDING HURTS" if diff > SHIELD_SIGMAS * sigma else "SHIELDING NEUTRAL")}
    t = {a: arms[a]["step1_s"] + arms[a]["step2_s"] for a in ("openmc_direct", "actinv_mixture", "actinv_plain")}
    out["timing_s"] = {
        "steps_1_and_2": t,
        "step1": {a: arms[a]["step1_s"] for a in CORE}, "step2": {a: arms[a]["step2_s"] for a in CORE},
        "step3": {a: arms[a]["step3_s"] for a in CORE},
        "memory_peak_gib": {a: arms[a]["memory_peak_bytes"] / GIB for a in h191.ARMS
                            if finite((arms.get(a) or {}).get("memory_peak_bytes"))},
    }
    return out


def sentences(dec):
    """Allowed sentences. Timing is steps 1 and 2 of each pipeline: OpenMC's step 1 carries its direct reaction-rate
    tallies and step 2 its depletion solve, ACTINV's step 1 a flux-only tally on the same transport and step 2 the
    activation solve (with the pointwise export for the mixture arm); step 3 is the same photon transport in both."""
    rm, rp = dec["R_m"], dec["R_p"]
    t = dec["timing_s"]["steps_1_and_2"]
    a = (f"On P181's tungsten, copper-alloy, water and steel model with 8 mesh elements, ACTINV's R2S with mixture "
         f"self-shielding gave a shutdown dose {rm['ratio']:.3f} ± {round_up(rm['sigma']):.3f} times that of OpenMC "
         f"0.15.3's own R2S with direct reaction-rate tallies, on the same ENDF/B-VIII.1 cross sections; ACTINV's "
         f"steps 1 and 2 (model to photon source) took {t['actinv_mixture']:.0f} s against "
         f"{t['openmc_direct']:.0f} s for OpenMC's.")
    label = dec["shielding"]["label"]
    tail = {"SHIELDING HELPS": "mixture self-shielding brought ACTINV's dose closer to OpenMC's than ACTINV without it "
                               "by more than two combined standard deviations",
            "SHIELDING NEUTRAL": "mixture self-shielding changed ACTINV's distance from OpenMC's dose by no more "
                                 "than two combined standard deviations",
            "SHIELDING HURTS": "mixture self-shielding took ACTINV's dose further from OpenMC's than ACTINV "
                               "without it by more than two combined standard deviations"}[label]
    b = (f"Without self-shielding ACTINV's dose was {rp['ratio']:.3f} ± {round_up(rp['sigma']):.3f} times OpenMC's; "
         f"{tail} ({label}).")
    return [a, b]


def evaluate(record, repo=None, registration=None, log=None, root=ROOT):
    gates = {"G0": g0(root, log), "G1": g1(record, repo, registration), "G2": g2(record)}
    dec = decision(record.get("arms", {}))
    sent = sentences(dec) if dec and not any(gates.values()) else []
    return gates, dec, sent


# ------------------------------------------------------------------------------------------------------ self-test

def synthetic_arm(arm, dose, sd_rel=0.01, **over):
    cg = {"path": "/x/run-h.scope", "memory_max": str(6 * GIB), "memory_swap_max": "0", "pids_max": "128",
          "cpu_max": "200000 100000"}
    run_cg = dict(cg, memory_max=str(10 * GIB))
    repo = {k: "r" * 64 for k in REPO_INPUTS}
    inputs = {k: {"sha256": v} for k, v in {**PINS, **repo}.items()}
    inputs["actinv_binary"] = {"sha256": ACTINV_BINARY_SHA256 or "b" * 64}
    conf = {"particles": h.PARTICLES, "mesh_dimension": [h191.DIM] * 3, "threads": h.THREADS, "seed": h.SEED,
            "micro_kwargs_keys": [], "reaction_rate_mode": "direct", "energy_groups_in_flux": 1,
            "flux_sum_n_cm_per_src": 50.0}
    if arm in h191.ACTINV_ARMS:
        conf["energy_groups_in_flux"] = c181.GROUPS
    if arm == "openmc_flux":
        conf.update(reaction_rate_mode="flux", micro_kwargs_keys=["energies", "reaction_rate_mode"],
                    energy_groups_in_flux=c181.GROUPS)
    a = {"schema": h191.SCHEMA, "arm": arm, "rehearsal": False, "status": "ok", "exit_code": 0, "censored": False,
         "complete": True, "phase_done": "step3", "wall_s": 300.0, "limit_s": h.LIMIT_S,
         "memory_max_gib": h.MEMORY_MAX_GIB, "step1_s": 100.0, "step2_s": 20.0 if arm in h191.ACTINV_ARMS else 200.0,
         "step3_s": 30.0, "dose_rate_Sv_h": dose, "dose_rate_sd_Sv_h": dose * sd_rel, "photons_per_s": 1e15,
         "memory_peak_bytes": 4 * GIB, "cgroup": run_cg, "config": conf, "inputs": inputs,
         "environment": {"xs_archive_sha256": c181.XS_ARCHIVE, "openmc_version": OPENMC_VERSION,
                         "actinv_version": "actinv 1.7.2", "git_head": "reg", "git_dirty_paths": "", "cgroup": cg},
         "plan": {"memory_max_gib": h.MEMORY_MAX_GIB, "limit_s": h.LIMIT_S, "dim": h191.DIM,
                  "particles": h.PARTICLES, "batches": h.BATCHES, "seed": h.SEED, "timesteps_s": h.TIMESTEPS,
                  "source_rates": h.SOURCE_RATES, "photon_time_index": h.PHOTON_TIME_INDEX}}
    if arm == "actinv_mixture":
        a["mesh_spec"] = {"self_shielding_present": True, "self_shielding": {"mixture": {"pointwise_files": []}}}
    if arm == "actinv_plain":
        a["mesh_spec"] = {"self_shielding_present": False, "self_shielding": None}
    a.update(over)
    return a


def synthetic_record(direct=1.0, mixture=1.02, plain=0.80, flux=1.03, sd_rel=0.01):
    arms = {"openmc_direct": synthetic_arm("openmc_direct", direct, sd_rel),
            "actinv_mixture": synthetic_arm("actinv_mixture", mixture, sd_rel),
            "actinv_plain": synthetic_arm("actinv_plain", plain, sd_rel),
            "openmc_flux": synthetic_arm("openmc_flux", flux, sd_rel)}
    return {"schema": h191.RECORD_SCHEMA, "rehearsal": False, "arms": arms}, {k: "r" * 64 for k in REPO_INPUTS}


def selftest():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "protocols").mkdir()
        (root / "controls").mkdir()
        log = ""
        for rel in HASHED:
            (root / rel).write_text(f"content of {rel}\n")
            log += f"{sha256(root / rel)}  {rel}\n"
        if g0(root, log):
            failures.append(f"G0 fails on a sealed set: {g0(root, log)}")
        (root / "controls/check_p191.py").write_text("edited\n")
        if not g0(root, log):
            failures.append("G0 misses an edited checker")
        if not g0(root, log.replace("controls/p191_harness.py", "controls/other.py")):
            failures.append("G0 misses an unlogged harness")
        (root / PROTOCOL_REL).unlink()
        if not g0(root, log):
            failures.append("G0 misses a missing protocol")

    def run(rec, repo, registration="reg"):
        gates = {"G0": [], "G1": g1(rec, repo, registration), "G2": g2(rec)}
        dec = decision(rec["arms"])
        return gates, dec, (sentences(dec) if dec and not any(gates.values()) else [])

    rec, repo = synthetic_record()
    gates, dec, sent = run(rec, repo)
    if any(gates.values()):
        failures.append(f"clean record fails: {gates}")
    elif len(sent) != 2:
        failures.append(f"sentence count {len(sent)}")
    else:
        want = ("On P181's tungsten, copper-alloy, water and steel model with 8 mesh elements, ACTINV's R2S with "
                "mixture self-shielding gave a shutdown dose 1.020 ± 0.015 times that of OpenMC 0.15.3's own R2S "
                "with direct reaction-rate tallies, on the same ENDF/B-VIII.1 cross sections; ACTINV's steps 1 and 2 "
                "(model to photon source) took 120 s against 300 s for OpenMC's.")
        if sent[0] != want:
            failures.append(f"sentence (a) differs:\n  {sent[0]}\n  {want}")
        if "SHIELDING HELPS" not in sent[1] or "0.800 ± 0.012" not in sent[1]:
            failures.append(f"sentence (b) wrong: {sent[1]}")
        if dec["agreement"] != "AGREES" or "R_f" not in dec:
            failures.append(f"decision wrong: {dec}")

    # the three shielding labels, and AGREES vs DIFFERS
    cases = {"SHIELDING HELPS": (1.02, 0.80), "SHIELDING NEUTRAL": (1.02, 1.03), "SHIELDING HURTS": (0.80, 1.02)}
    for label, (m, p) in cases.items():
        rec, repo = synthetic_record(mixture=m, plain=p)
        dec = decision(rec["arms"])
        if dec["shielding"]["label"] != label:
            failures.append(f"label {label} not produced: {dec['shielding']}")
    rec, repo = synthetic_record(mixture=1.10)
    if decision(rec["arms"])["agreement"] != "AGREES":
        failures.append("R_m = 1.10 is not AGREES")
    rec, repo = synthetic_record(mixture=1.15)
    gates, dec, _ = run(rec, repo)
    if dec["agreement"] != "DIFFERS" or any(gates.values()):
        failures.append("R_m = 1.15 should be DIFFERS and still pass the gates")
    rec, repo = synthetic_record(mixture=0.85)
    if decision(rec["arms"])["agreement"] != "DIFFERS":
        failures.append("R_m = 0.85 is not DIFFERS")
    # large errors turn a difference into NEUTRAL
    rec, repo = synthetic_record(mixture=1.02, plain=0.80, sd_rel=0.2)
    if decision(rec["arms"])["shielding"]["label"] != "SHIELDING NEUTRAL":
        failures.append("noisy arms not NEUTRAL")
    # sigma is rounded up
    if round_up(0.0141) != 0.015 or round_up(0.015) != 0.015:
        failures.append("round_up wrong")

    def broken(name, mutate, expect):
        rec, repo = synthetic_record()
        registration = mutate(rec) or "reg"
        gates, dec, sent = run(rec, repo, registration)
        if not gates[expect]:
            failures.append(f"not caught by {expect}: {name}")
        if sent:
            failures.append(f"sentences written despite {name}")

    broken("missing arm", lambda r: r["arms"].pop("actinv_plain"), "G1")
    broken("missing arm (G2)", lambda r: r["arms"].pop("actinv_plain"), "G2")
    broken("censored direct arm", lambda r: r["arms"]["openmc_direct"].update(
        censored=True, status="killed", exit_code=-9, complete=False, censor_reason="memory"), "G2")
    broken("failed mixture arm", lambda r: r["arms"]["actinv_mixture"].update(exit_code=1, status="failed"), "G2")
    broken("no dose", lambda r: r["arms"]["actinv_plain"].update(dose_rate_Sv_h=None), "G2")
    broken("dirty tree", lambda r: r["arms"]["actinv_mixture"]["environment"].update(git_dirty_paths=" M x"), "G1")
    broken("wrong registration", lambda r: "other", "G1")
    broken("run at another commit", lambda r: r["arms"]["openmc_direct"]["environment"].update(git_head="x"), "G1")
    broken("unbounded harness cgroup", lambda r: r["arms"]["openmc_direct"]["environment"]["cgroup"].update(
        cpu_max="max 100000"), "G1")
    broken("harness above 6 GiB", lambda r: r["arms"]["openmc_direct"]["environment"]["cgroup"].update(
        memory_max=str(12 * GIB)), "G1")
    broken("run scope 12 GiB", lambda r: r["arms"]["actinv_plain"].update(
        cgroup=dict(r["arms"]["actinv_plain"]["cgroup"], memory_max=str(12 * GIB))), "G1")
    broken("run scope with swap", lambda r: r["arms"]["actinv_plain"].update(
        cgroup=dict(r["arms"]["actinv_plain"]["cgroup"], memory_swap_max="max")), "G1")
    broken("run scope 6 GiB (not 10)", lambda r: r["arms"]["actinv_plain"].update(
        cgroup=dict(r["arms"]["actinv_plain"]["cgroup"], memory_max=str(6 * GIB))), "G1")
    broken("rehearsal record", lambda r: r.update(rehearsal=True), "G1")
    broken("rehearsal arm", lambda r: r["arms"]["actinv_mixture"].update(rehearsal=True), "G1")
    broken("wrong pin", lambda r: r["arms"]["actinv_mixture"]["inputs"]["actinv_library"].update(sha256="0" * 64), "G1")
    broken("harness changed", lambda r: r["arms"]["openmc_direct"]["inputs"]["harness"].update(sha256="0" * 64), "G1")
    broken("other OpenMC", lambda r: r["arms"]["openmc_direct"]["environment"].update(openmc_version="0.16.0"), "G1")
    broken("other particles", lambda r: r["arms"]["openmc_direct"]["plan"].update(particles=200), "G1")
    broken("direct arm with micro kwargs", lambda r: r["arms"]["openmc_direct"]["config"].update(
        micro_kwargs_keys=["energies"]), "G1")
    broken("mixture arm without block", lambda r: r["arms"]["actinv_mixture"]["mesh_spec"].update(
        self_shielding_present=False, self_shielding=None), "G1")
    broken("plain arm with block", lambda r: r["arms"]["actinv_plain"]["mesh_spec"].update(
        self_shielding_present=True, self_shielding={"mixture": {}}), "G1")
    broken("different binaries", lambda r: r["arms"]["actinv_plain"]["inputs"]["actinv_binary"].update(
        sha256="c" * 64), "G1")

    # openmc_flux censored: context only, the gates still pass and the flux ratio is absent
    rec, repo = synthetic_record()
    rec["arms"]["openmc_flux"].update(censored=True, status="timeout", exit_code=-15, complete=False,
                                      step1_s=None, dose_rate_Sv_h=None, censor_reason="timeout")
    gates, dec, sent = run(rec, repo)
    if gates["G2"] or any(g for g in ("G1",) if gates[g]) or "R_f" in dec or len(sent) != 2:
        failures.append(f"a censored openmc_flux should not matter: {gates} {dec and 'R_f' in dec}")
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
    gates, dec, sent = evaluate(record)
    passed = all(not problems for problems in gates.values())
    verdict = {
        "protocol": "ACTINV-P191",
        "verdict": "P191-PASS (pending G4, CI on the evidence commit)" if passed else "P191-FAIL",
        "gates": {**{name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
                  "G4": {"pass": None, "problems": ["CI on the evidence commit, checked after the push"]}},
        "decision": dec,
        "ledger_mixture": {a: (record.get("arms", {}).get(a) or {}).get("ledger_mixture") for a in h191.ACTINV_ARMS},
        "allowed_sentences": sent if passed else [],
    }
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems[:20]:
            print(f"  {problem}")
    if dec:
        print(f"G3: R_m = {dec['R_m']['ratio']:.3f} ± {dec['R_m']['sigma']:.3f} ({dec['agreement']}), "
              f"R_p = {dec['R_p']['ratio']:.3f} ± {dec['R_p']['sigma']:.3f}, {dec['shielding']['label']}")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if recorded.get("decision") != json.loads(json.dumps(dec)) or recorded.get(
                    "allowed_sentences") != verdict["allowed_sentences"]:
                print("verdict file differs from the recomputed decision")
                passed = False
    elif args.record.resolve() == RECORD.resolve():
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    for sentence in verdict["allowed_sentences"]:
        print(f"  {sentence}")
    print(verdict["verdict"] if passed else "P191-FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
