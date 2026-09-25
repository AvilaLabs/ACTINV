#!/usr/bin/env python3
"""P52 G2 — one-cell deplete parity: ACTINV (TENDL-709g activation library)
vs openmc.deplete (ENDF microXS + chain.xml), identical material (pure Fe56),
identical multigroup flux, identical schedule. Tolerance rtol=0.5 on every
nuclide contributing >=1% of either arm's end-of-irradiation activity; the
full comparison is ledgered regardless.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p52_artifacts as p52a  # noqa: E402
import p52_openmc_parity as parity  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g2_p52_parity.json"
TOLERANCE = 0.5
DOMINANCE_FRACTION = 0.01


def sha256_file(path: Path) -> str:
    return parity.sha256_file(path)


def run_actinv_arm(work: Path) -> dict:
    """One-cell mesh on the same FNS flux; return per-step atoms + activity."""
    flux = parity.fns_flux_descending()
    total = sum(flux)
    bounds_desc = json.loads((parity.BOUNDS).read_text())["boundaries_eV"]
    flux_asc = list(reversed(flux))
    bounds_asc = list(reversed(bounds_desc))
    flux_path = work / "parity_flux.ndjson"
    with flux_path.open("w") as fh:
        fh.write(json.dumps({
            "record": "header", "schema": "actinv-flux-1",
            "source": {"format": "actinv-spec-1",
                       "path": "examples/fns_fe_5min.json",
                       "sha256": sha256_file(
                           ROOT / "examples/fns_fe_5min.json")},
            "energy_boundaries_eV": bounds_asc,
            "flux_units": "n cm^-2 s^-1", "cell_count": 1}) + "\n")
        fh.write(json.dumps({"record": "cell", "ordinal": 0, "id": "parity",
                             "bounds_cm": [[0, 1]] * 3, "volume_cm3": 1.0,
                             "flux_per_group": flux_asc,
                             "flux_total": total}) + "\n")
        fh.write(json.dumps({"record": "footer", "cell_count": 1,
                             "flux_sum_over_cells": total,
                             "volume_integrated_flux": total * 1.0}) + "\n")

    spec = {
        "spec": "actinv-mesh-spec-1",
        "title": "P52 parity: pure Fe56 one-cell FNS",
        "projectile": "neutron",
        "library": {"path": "actinv-data/v1.1.0/activation/"
                            "tendl-2025-patched-neutron-709g.npz",
                    "sha256": sha256_file(ROOT / "actinv-data/v1.1.0/activation/"
                                          "tendl-2025-patched-neutron-709g.npz")},
        "decay": {"primary": "catalog:endfb-viii-0-decay",
                  "fallback": "catalog:jeff-3-3-decay"},
        "material": {"mass_g": 1.0, "basis": "atom_fraction",
                     "composition": {"Fe56": 1.0}},
        "schedule": ([{"dt": f"{parity.IRR_S:g} s", "flux": 1.0}]
                     + [{"dt": f"{dt:g} s", "flux": 0.0}
                        for dt in parity.COOL_S]),
        "options": {"mode": "auto", "prune": "rate",
                    "bmin_atoms_per_g": 1e-10, "temperature_K": 293.6},
        "flux": {"path": str(flux_path),
                 "sha256": sha256_file(flux_path)},
    }
    spec_path = work / "parity_mesh_spec.json"
    spec_path.write_text(json.dumps(spec, indent=1))
    out_path = work / "parity_mesh.ndjson"
    t0 = time.monotonic()
    r = subprocess.run([str(BIN), "mesh", str(spec_path), str(out_path)],
                       capture_output=True, text=True, timeout=1800)
    wall = time.monotonic() - t0
    if r.returncode != 0:
        raise SystemExit(f"actinv mesh failed: {r.stderr[-2000:]}")
    rec = None
    for line in out_path.read_text().splitlines():
        j = json.loads(line)
        if j.get("record") == "cell":
            rec = j
            break
    steps = {}
    for st in rec["result"]["steps"]:
        inv = {n["nuclide"]: n["atoms_per_g"] for n in st["inventory"]}
        act = st.get("activity_Bq_per_g") or {}
        steps[st["step"]] = {"atoms_per_g": inv,
                             "activity_bq_per_g": act}
    return {"steps": steps, "wall_s": wall,
            "result_sha256": sha256_file(out_path)}


def main() -> int:
    drift = {n: r["sha256"] for n, r in p52a.verify().items()}
    problems = []
    work = ROOT / "target/p52-parity"
    t0 = time.monotonic()

    openmc_res = parity.run_openmc_parity(work)
    actinv = run_actinv_arm(work)
    wall_s = time.monotonic() - t0

    case = openmc_res["cases"].get("fe56_parity", {})
    if case.get("status") != "executed":
        problems.append(f"openmc arm did not execute: {case.get('reason')}")
        OUT.write_text(json.dumps({"pass": False, "problems": problems}))
        return 1
    hl = {n: s for n, s in openmc_res["half_life_s"].items() if s}

    def norm(name: str) -> str:
        # openmc 'Mn52_m1' -> actinv 'Mn52m1'
        return name.replace("_m", "m")

    om_atoms = {norm(n): v for n, v in
                case["atoms_atom_per_cm3"].items()}
    hl = {norm(n): s for n, s in hl.items()}

    # Per-step activity (atoms * ln2/half-life) for the dominance ranking.
    def om_activity(step_idx: int) -> dict:
        return {n: atoms[step_idx] * math.log(2) / hl[n]
                for n, atoms in om_atoms.items()
                if n in hl and step_idx < len(atoms)}

    # Steps: driver produces len(timesteps)+1 entries; actinv steps are
    # 1..len(schedule)+? — match by index: openmc idx i corresponds to
    # actinv step i+1? Verify by lengths; compare at end-of-irradiation and
    # each cooling endpoint.
    n_steps = len([st for st in actinv["steps"]])
    n_om = len(next(iter(om_atoms.values())))
    # actinv steps numbered 1..N: irradiation step 1, coolings 2..N.
    # openmc entries: initial + after each timestep -> len = N+1.
    comparisons = {}
    dominant = set()
    end_act = om_activity(1)
    a_end = actinv["steps"].get(1, {}).get("activity_bq_per_g", {})
    total_a = sum(a_end.values()) or 1.0
    total_o = sum(end_act.values()) or 1.0
    for n, a in a_end.items():
        if a / total_a >= DOMINANCE_FRACTION:
            dominant.add(n)
    for n, a in end_act.items():
        if a / total_o >= DOMINANCE_FRACTION:
            dominant.add(n)

    if not dominant:
        problems.append("no dominant nuclides identified in either arm")

    # openmc entries: index 0 = t=0 initial, index i = after timestep i.
    # actinv steps are numbered 1..N over the same schedule: step i
    # corresponds to openmc index i (both are 'after timestep i').
    for om_idx in range(1, n_om):
        act_step = om_idx
        st = actinv["steps"].get(act_step)
        if st is None:
            problems.append(f"actinv missing step for openmc index {om_idx}")
            continue
        inv = st["atoms_per_g"]
        rows = {}
        for n in sorted(dominant):
            om = om_atoms.get(n)
            av = inv.get(n, 0.0)
            ov = om[om_idx] if om and om_idx < len(om) else None
            if ov is None:
                rows[n] = {"actinv": av, "openmc": None}
                problems.append(
                    f"dominant nuclide {n} missing from openmc arm")
                continue
            rel = abs(av - ov) / max(abs(ov), 1e-300)
            rows[n] = {"actinv": av, "openmc": ov, "rel": rel}
            if rel > TOLERANCE:
                problems.append(
                    f"step {act_step}: {n} rel diff {rel:.3f} > {TOLERANCE}")
        comparisons[str(act_step)] = rows

    result = {
        "pass": not problems,
        "tolerance": TOLERANCE,
        "dominance_fraction": DOMINANCE_FRACTION,
        "dominant_nuclides": sorted(dominant),
        "actinv_steps": n_steps, "openmc_entries": n_om,
        "wall_s": wall_s, "actinv_mesh_wall_s": actinv["wall_s"],
        "openmc_deplete_s": case.get("deplete_s"),
        "comparisons": comparisons,
        "artifacts": drift,
        "problems": problems,
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": result["pass"], "dominant": sorted(dominant),
                      "problems": problems[:8]}))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
