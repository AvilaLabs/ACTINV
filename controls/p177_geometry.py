#!/usr/bin/env python3
"""P177 harness (protocols/ACTINV-P177_PROTOCOL.md): P176's geometry self-shielding arms re-scored with
`self_shielding.geometry.profile: "endfb8"`.

P176's A2 and A3 failed every run because ACTINV's strict ENDF reader refused the ENDF/B-VIII.1 Cu-63 evaluation (its RML
photon pair). The optional geometry `profile` normalizes each resonance file before it is read; this harness runs the same
arms with `"profile": "endfb8"` and nothing else changed. P177 has no Bondarenko arm and no table mode.

Arms (fixed in ARMS below, names as in P176 so P176's row logic applies):
  A0  default: no `self_shielding` section. Control; its loss rates must reproduce P173's total-row rates (G1).
  A2  geometry, a foil of the tallied region's thickness, `profile: "endfb8"`.
  A3  geometry as A2 with the thickness of the whole model body.

Everything else is P176's, by import from p176_selfshield: the problem builder (foil thickness, the resonance-file rule, the
evaluation pins ENDF_FILES, the step and summary), the reading of a result, the row construction (q, R_ss, r_ss), the pins of
the inputs, and the decision rules (in check_p177.py, from check_p176.py).

Modes (each its own process; none runs unless asked):
  arm --arm A --model M      writes results/p177/arm_<A>_<M>.json (one ACTINV run per region of the model)
  assemble                   merges everything into results/p177_geometry.json

P177_REHEARSAL=1: every output goes under target/p177-rehearsal*, the tallied flux is replaced by a flat 1.0 per group
(no OpenMC result enters), and nothing is scored or kept. It exists to find harness defects before the scored run.

Run each mode inside the bounded systemd scope AGENTS.md prescribes. Every mode refuses to start without an enforced
cgroup. Nothing here is run by the protocol author.
"""
import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
if os.environ.get("P173_REHEARSAL") == "1":
    sys.exit("p177_geometry: P173_REHEARSAL must not be set (P177 reads P173's scored records)")
import p176_selfshield as p176  # noqa: E402  (the problem builder, row construction and pins; reads nothing at import)
import p173_ce_rates as p173  # noqa: E402

REHEARSAL = os.environ.get("P177_REHEARSAL") == "1"
OUT_DIR = ROOT / "target" / "p177-rehearsal" if REHEARSAL else ROOT / "results" / "p177"
RECORD = OUT_DIR / "record.json" if REHEARSAL else ROOT / "results" / "p177_geometry.json"
WORK = ROOT / "target" / ("p177-rehearsal-work" if REHEARSAL else "p177-work")
SCHEMA = "actinv-p177-geometry-1"

ACTINV_BIN = p176.ACTINV_BIN
# The release build of ACTINV master 4af8e68 (geometry `profile`); set from that build before registration. While None,
# G1 fails and every mode refuses to run.
ACTINV_BIN_SHA256 = "372dd99f271cdd2b2b6881d46499c5ec7cc11f74033dba5da42d3a69ce2f8fcd"

PROFILE = "endfb8"
ARMS = {
    "A0": p176.ARMS["A0"],
    "A2": {"label": "geometry, foil of the tallied region's thickness, profile endfb8",
           "self_shielding": {"geometry": {"shape": "foil", "length": "region", "reactions": "default (all)",
                                           "profile": PROFILE}}},
    "A3": {"label": "geometry, foil of the whole model body's thickness, profile endfb8",
           "self_shielding": {"geometry": {"shape": "foil", "length": "body", "reactions": "default (all)",
                                           "profile": PROFILE}}},
}
ARM_ORDER = ("A0", "A2", "A3")
SCORED_ARMS = ("A2", "A3")


def constants_digest():
    """SHA-256 of every constant that defines the arms (the profile included), the spec each arm writes and the decision
    thresholds, with P176's constants and P176's harness file, whose builders this harness calls (hashed by G0)."""
    blob = {"arms": ARMS, "arm_order": ARM_ORDER, "scored_arms": SCORED_ARMS, "profile": PROFILE,
            "p176_constants": p176.constants_digest(), "p176_harness_sha256": p173.sha256_file(Path(p176.__file__)),
            "title": "P177 {arm} {region}", "rehearsal_flux": "flat 1.0 per group"}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------ utilities

def require_binary():
    if ACTINV_BIN_SHA256 is None:
        sys.exit("p177_geometry: the actinv binary digest is not pinned")
    digest = p173.sha256_file(ACTINV_BIN)
    if digest != ACTINV_BIN_SHA256:
        sys.exit(f"p177_geometry: {ACTINV_BIN} is not the pinned binary ({digest})")


def environment_record(cg):
    status = p173.git("status", "--porcelain", "--untracked-files=no")
    return {"git_head": p173.git("rev-parse", "HEAD"), "crates_tree": p173.git("rev-parse", "HEAD:crates"),
            "clean_tree": status == "", "cgroup": cg, "harness_sha256": p173.sha256_file(Path(__file__)),
            "groups_sha256": p173.sha256_file(p173.GROUPS), "constants_sha256": constants_digest(),
            "rehearsal": REHEARSAL, "python": platform.python_version(), "platform": platform.platform()}


def run_cmd(cmd, timeout, cwd=None):
    """Run `cmd`; on timeout the child is killed and reaped (subprocess.run does both). Returns (returncode or None,
    stdout tail, stderr tail, wall seconds)."""
    env = dict(os.environ, ACTINV_CACHE_DIR=str(WORK / "cache"), PYTHONHASHSEED="0", RAYON_NUM_THREADS="2")
    start = time.time()
    try:
        done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        return done.returncode, done.stdout[-2000:], done.stderr[-2000:], time.time() - start
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout} s", time.time() - start


# ------------------------------------------------------------------------------------------------------ P173 data, problem

def load_p173_data():
    """P176's data from the pinned P173 OpenMC files; under P177_REHEARSAL=1 the flux is a flat 1.0 per group instead."""
    bounds = p173.groups_ascending()
    data = {}
    for model in p173.MODEL_ORDER:
        final = json.loads((p176.P173_DIR / f"openmc_{model}.json").read_text())["final"]
        if REHEARSAL:
            flux = {region: [1.0] * (len(bounds) - 1) for region in p173.regions_of(model)}
        else:
            flux = {region: list(final["flux"][region]["mean"]) for region in p173.regions_of(model)}
        data[model] = {"flux": flux, "atoms": dict(final["atom_densities_atom_b_cm"]),
                       "density": final["density_g_cm3"]}
    return data


def build_spec(arm, model, region, data, endf_dir=None):
    """The ACTINV problem of one (arm, region): P176's, with the title of this protocol and, in A2 and A3, the geometry
    `profile`."""
    if arm not in ARM_ORDER:
        raise KeyError(arm)
    spec = p176.build_spec(arm, model, region, data, None, endf_dir)
    spec["title"] = f"P177 {arm} {region}"
    if arm != "A0":
        spec["self_shielding"]["geometry"]["profile"] = PROFILE
    return spec


# ------------------------------------------------------------------------------------------------------ arm mode

def run_arm(arm, model, out):
    cg = p176.require_bounded()
    require_binary()
    p176.require_pins()
    data = load_p173_data()
    work = WORK / "runs"
    work.mkdir(parents=True, exist_ok=True)
    runs = []
    for region in p173.regions_of(model):
        stem = f"{arm}_{region.replace(':', '_')}"
        spec = build_spec(arm, model, region, data)
        spec_path, result_path = work / f"{stem}_spec.json", work / f"{stem}_result.json"
        spec_path.write_text(json.dumps(spec))
        if result_path.exists():
            result_path.unlink()
        entry = {"arm": arm, "model": model, "region": region, "spec_sha256": p176.spec_digest(spec),
                 "flux_total_cm_per_source": math.fsum(spec["spectrum"]["flux_per_group"]), "ok": False}
        if arm in SCORED_ARMS:
            geometry = spec["self_shielding"]["geometry"]
            entry["foil_thickness_cm"] = geometry["length_cm"]
            entry["geometry_profile"] = geometry["profile"]
            entry["resonance_files"] = [f["path"].rsplit("/", 1)[-1] for f in geometry["resonance_files"]]
        rc, _, stderr, wall = run_cmd([ACTINV_BIN, "validate", spec_path], 600)
        entry["validate_returncode"] = rc
        if rc == 0:
            rc, _, stderr, wall = run_cmd([ACTINV_BIN, "run", spec_path, result_path], p176.RUN_TIMEOUT_S)
        entry.update(run_returncode=rc, wall_s=wall, stderr_tail=stderr if rc != 0 else "")
        if rc == 0 and result_path.is_file():
            try:
                result = json.loads(result_path.read_text())
                entry["rates"] = p176.reaction_rates_of(result, model)
                entry["shielding"] = p176.shielding_summary(result)
                entry["ok"] = True
            except (ValueError, KeyError) as error:
                entry["error"] = str(error)
        runs.append(entry)
    p173.write_atomic(out, {"schema": SCHEMA + "-arm", "arm": arm, "model": model, "runs": runs,
                            "actinv_bin_sha256": p173.sha256_file(ACTINV_BIN),
                            "actinv_version": run_cmd([ACTINV_BIN, "--version"], 60)[1].strip(),
                            "environment": environment_record(cg)})


# ------------------------------------------------------------------------------------------------------ assembly

def assemble(record_path):
    cg = p176.require_bounded()
    p176.require_pins()
    p173_rows = {(r["model"], r["region"], r["target"], r["mt"]): r
                 for r in json.loads(p176.P173_RECORD.read_text())["rows"]}
    runs, rows, a0_check, cgroups = [], [], [], []
    bin_digests, versions = set(), set()
    arm_docs = {}
    for arm in ARM_ORDER:
        for model in p173.MODEL_ORDER:
            doc = json.loads((OUT_DIR / f"arm_{arm}_{model}.json").read_text())
            arm_docs[(arm, model)] = doc
            cgroups.append({"where": f"arm:{arm}:{model}", "cgroup": doc["environment"]["cgroup"]})
            bin_digests.add(doc["actinv_bin_sha256"])
            versions.add(doc["actinv_version"])
            runs += doc["runs"]
    a0 = {(model, run["region"]): run for (arm, model), doc in arm_docs.items() if arm == "A0" for run in doc["runs"]}
    for arm in ARM_ORDER:
        for model in p173.MODEL_ORDER:
            by_region = {run["region"]: run for run in arm_docs[(arm, model)]["runs"]}
            for region in p173.regions_of(model):
                for target, mt, _ in p173.model_spec(model)["reactions"]:
                    key = p173.rate_key(target, mt)
                    p173_row = p173_rows[(model, region, target, mt)]
                    run, base = by_region[region], a0[(model, region)]
                    dep_arm = (run.get("rates") or {}).get(key)
                    dep_a0 = (base.get("rates") or {}).get(key)
                    if arm == "A0":
                        total = p173_row.get("total_row_R_actinv")
                        a0_check.append({"model": model, "region": region, "target": target, "mt": mt,
                                         "p173_total_row_rate_per_atom": None if total is None else total * 1e-24,
                                         "a0_loss_rate_per_atom": dep_a0, "a0_run_ok": base["ok"]})
                    else:
                        rows.append(p176.build_ss_row(arm, p173_row, dep_a0, dep_arm, run["ok"] and base["ok"]))
    cgroups.append({"where": "assemble", "cgroup": cg})
    env = environment_record(cg)
    inputs = {name: {"path": str(path), "sha256": p173.sha256_file(path)} for name, (path, _) in p176.PINNED.items()}
    document = {"schema": SCHEMA, "inputs": inputs,
                "environment": {**env, "cgroups": cgroups, "actinv_bin_sha256": sorted(bin_digests),
                                "actinv_version": sorted(versions)},
                "geometry_profile": PROFILE,
                "endf_files": {nuc: {"file": name, "sha256": digest} for nuc, (name, digest) in p176.ENDF_FILES.items()},
                "runs": runs, "a0_check": {"tolerance": p176.G1_TOLERANCE, "rows": a0_check}, "rows": rows}
    p173.write_atomic(record_path, document)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("assemble")
    p = sub.add_parser("arm")
    p.add_argument("--arm", choices=list(ARM_ORDER), required=True)
    p.add_argument("--model", choices=list(p173.MODEL_ORDER), required=True)
    args = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    if args.mode == "arm":
        run_arm(args.arm, args.model, OUT_DIR / f"arm_{args.arm}_{args.model}.json")
    else:
        assemble(RECORD)


if __name__ == "__main__":
    main()
