#!/usr/bin/env python3
"""P45 G0 opening seal: protocol digest, campaign+mesh population,
comparator and data identities, scoring-code identities, cgroup
envelope. Bound before any benchmark number exists.

Every declared pin is recomputed; a mismatch aborts the seal instead of
recording a false identity.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "g0_p45_seals.json"
DATA = Path.home() / "nuclear-data"

PROTOCOL = "protocols/ACTINV-P45_PROTOCOL.md"

sys.path.insert(0, str(ROOT / "controls"))
import g2_p26b_leg as leg  # noqa: E402
import p45_campaign as camp  # noqa: E402
import p45_robustness  # noqa: E402


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ident(path):
    p = Path(path)
    full = p if p.is_absolute() else ROOT / p
    return {"path": str(path), "sha256": sha(full)}


def pinned(path, declared):
    entry = ident(path)
    if entry["sha256"] != declared:
        raise SystemExit(
            f"pin mismatch for {path}: computed {entry['sha256']}, "
            f"declared {declared}")
    return entry


def main():
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True).stdout.strip()
    pop = json.loads(camp.POPULATION.read_text())
    pop_sha = sha(camp.POPULATION)
    contract_sha = sha(leg.CONTRACT_PATH)

    seals = {
        "schema": "actinv-g0-seal-1",
        "phase": "P45",
        "recorded_at_utc":
            subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                           capture_output=True, text=True).stdout.strip(),
        "opening_commit": head,
        "protocol": PROTOCOL,
        "protocol_sha256": sha(ROOT / PROTOCOL),
        "identities": {
            "actinv_binary": ident("target/release/actinv"),
            "tendl_npz": ident(camp.TENDL_NPZ),
            "fendl_npz": ident(camp.FENDL_NPZ),
            "alara_binary": ident(camp.ALARA),
            "alara_lib": ident(str(leg.ALARA_LIB_BASE) + ".lib"),
            "alara_dmp": ident(str(leg.ALARA_LIB_BASE.parent /
                                   "alara.dmp")),
            "alara_idx": ident(str(camp.IDX)),
            "elelib": ident(str(leg.ELELIB)),
            "openmc_python": ident(camp.OPENMC_PY),
            "openmc_driver": ident(camp.OPENMC_DRIVER),
            "chain_xml": ident(camp.CHAIN_XML),
            "xs_xml": ident(camp.XS_XML),
            "bounds": ident(camp.BOUNDS),
            "covariance_npz": ident(p45_robustness.COV_NPZ),
            "decay_primary": ident(str(leg.DECAY_PRIMARY)),
            "decay_fallback": ident(str(leg.DECAY_FALLBACK)),
        },
        "population": {
            "path": "results/p45_population.json",
            "sha256": pop_sha,
            "campaign_cases": len(pop["campaign"]["cases"]),
            "mesh_cells": len(pop["mesh"]["cells"]),
            "fns_sha256": hashlib.sha256(json.dumps(
                leg.fns_spectrum(),
                separators=(",", ":")).encode()).hexdigest(),
        },
        "contract": {
            "path": "results/g2_p26b_leg_contract.json",
            "sha256": contract_sha,
        },
        "code_sha256": {
            "campaign_harness": sha(ROOT / "controls" /
                                  "p45_campaign.py"),
            "parity_scorer": sha(ROOT / "controls" / "p45_parity.py"),
            "openmc_driver": sha(camp.OPENMC_DRIVER),
            "robustness_driver": sha(ROOT / "controls" /
                                     "p45_robustness.py"),
            "population_builder": sha(ROOT / "controls" /
                                      "p45_population.py"),
            "p26b_leg": sha(ROOT / "controls" / "g2_p26b_leg.py"),
        },
        "frozen_values": {
            "repeat_invocations": 3,
            "per_case_timeout_s": camp.PER_CASE_TIMEOUT_S,
            "openmc_batch_timeout_s": camp.OPENMC_BATCH_TIMEOUT_S,
            "envelope_minutes": 240,
            "reach_depth": camp.REACH_DEPTH,
            "parity_tolerance": 0.10,
            "cooling_times_s": [0.0] + list(camp.COOL_DT_S),
            "robustness": {"samples": 16, "seed": 20260924},
        },
        "envelope": {
            "cgroups": {"MemoryMax": "6G", "MemorySwapMax": 0,
                        "TasksMax": 128, "CPUQuota": "200%",
                        "RAYON_NUM_THREADS": 2},
            "sequential_solver_jobs": 1,
            "wall_minutes_max": 240,
        },
    }
    json.dump(seals, open(OUT, "w"), indent=2, sort_keys=True)
    print(f"sealed P45 at {head} -> {OUT}")


if __name__ == "__main__":
    main()
