#!/usr/bin/env python3
"""Exploratory FNS head-to-head: score an OpenMC deplete arm on the sealed
CoNDERC FNS decay-heat corpus against the frozen CB2/CB3 per-point record.

The ACTINV and FISPACT values are read from the sealed records — no ACTINV
re-solve. The OpenMC arm runs `controls/openmc_fns_driver.py` under the
openmc016 env on the same inputs the CB3 runner extracted from the corpus:
same material (natural isotopic expansion of the .i element wt% via the
pinned abundance/mass table), same 709-group flux and total, same
irradiation + cooling boundaries. Decay heat per state is derived as
sum(N_i * lambda_i * E_decay_i) with the chain's own ENDF/B decay data —
the same convention OpenMC R2S uses for decay heat.

This is a product+data comparison (ENDF/B-VIII.1 chain+XS vs TENDL), not a
solver claim; XS/chain coverage gaps are recorded, not hidden.

Usage: python3 controls/openmc_fns_headtohead.py [--work DIR] [--no-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from harness import fispact_io as fio  # noqa: E402

DATA = Path.home() / "nuclear-data"
FNS = DATA / "conderc-fns" / "fns"
CB3 = ROOT / "results" / "cb3_fns_tendl2017.json"
CB2 = ROOT / "results" / "cb2_fns.json"
ABUNDANCE = ROOT / "results" / "tables" / "abundance_mass.json"
BOUNDS = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
DRIVER = ROOT / "controls" / "openmc_fns_driver.py"
OPENMC_PY = Path("/home/connoravila/micromamba/envs/openmc016/bin/python")
CHAIN_XML = DATA / "p32-work" / "chain" / "depletion" / "chain.xml"
XS_XML = DATA / "endfb-viii.1-hdf5" / "cross_sections.xml"
RESULT = ROOT / "results" / "openmc_fns_headtohead.json"
WORK_DEFAULT = ROOT / "scratch" / "openmc-fns-h2h"

# Materials spanning the corpus: structural staples, the (n,p)/(n,a)
# short-irradiation problem cases from FNS_GAP_DIAGNOSIS (Mn, S, W, Pb),
# FISPACT's catastrophic-miss cases (In), an alloy (SS316), and high-Z.
MATERIALS = ["Fe", "Ni", "Cu", "Al", "Ti", "V", "Mn", "Co", "S", "Nb",
             "Mo", "Ta", "W", "Pb", "In", "Ag", "Sn", "Zn", "Y", "SS316"]
PREFER = ["2000exp_5min", "1996exp_5min", "1996exp_7hour", "2000exp_7hour"]

EV_TO_J = 1.602176634e-19
LN2 = math.log(2.0)
WITHIN_LOG = math.log(1.3)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_flux(path: Path) -> list[float]:
    values = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        try:
            values.extend(float(v) for v in line.split())
        except ValueError:
            break
    if len(values) < 709:
        raise ValueError(f"{path.name}: {len(values)} flux groups")
    return values[:709]


def pick_experiments() -> list[tuple[str, str]]:
    """One experiment per material (5 min preferred), plus Fe 7hour."""
    chosen = []
    for mat in MATERIALS:
        d = FNS / mat
        if not d.is_dir():
            continue
        have = {p.stem for p in d.glob("*.exp")}
        for name in PREFER:
            if name in have:
                chosen.append((mat, name))
                break
    if ("Fe", "1996exp_7hour") not in chosen and \
            (FNS / "Fe" / "1996exp_7hour.exp").is_file():
        chosen.append(("Fe", "1996exp_7hour"))
    return chosen


def isotope_fractions(elements: dict[str, float],
                      abundance: dict, mass: dict) -> dict[str, float]:
    """Element wt% -> atom fraction per isotope: f_i ~ w_el * a_i / A_i."""
    raw = {}
    for el, w in elements.items():
        el = el.capitalize()
        for iso, a in (abundance.get(el) or {}).items():
            A = mass.get(iso) or float(
                int("".join(c for c in iso if c.isdigit())))
            raw[iso] = raw.get(iso, 0.0) + w * a / A
    total = sum(raw.values())
    if total <= 0:
        raise ValueError(f"no isotopic expansion for {list(elements)}")
    return {k: v / total for k, v in raw.items()}


def build_job(experiments: list[tuple[str, str]], work: Path) -> tuple[dict, dict]:
    """One job: deduped flux groups + one case per experiment."""
    table = json.loads(ABUNDANCE.read_text())
    abund, mass = table["abundance"], table["mass_amu"]
    groups, cases, meta = {}, [], {}
    for mat, exp in experiments:
        d = FNS / mat
        rec = fio.read_i(d / f"TENDL-2017_{exp}.i")
        flux = read_flux(d / f"{exp}_fluxes")
        total = float(rec["flux_total"])
        key = hashlib.sha256(
            json.dumps([v / total for v in flux]).encode()).hexdigest()[:16]
        if key not in groups:
            groups[key] = {"name": key, "flux_descending": flux,
                           "total_flux": total}
        cooling = rec["cooling_cum_s"]
        timesteps = [rec["t_irr_s"]] + [cooling[0]] + \
            [cooling[i] - cooling[i - 1] for i in range(1, len(cooling))]
        case = f"{mat}_{exp}"
        cases.append({
            "case": case, "group": key,
            "composition_isotope_fraction": isotope_fractions(
                rec["elements"], abund, mass),
            "timesteps_s": timesteps,
            "source_rates": [total] + [0.0] * len(cooling)})
        meta[case] = {"material": mat, "experiment": exp,
                      "t_irr_s": rec["t_irr_s"], "cooling_cum_s": cooling,
                      "elements": rec["elements"], "mass_kg": rec["mass_kg"],
                      "flux_total": total}
    job = {"work_dir": str(work / "driver"),
           "chain_file": str(CHAIN_XML), "bounds_file": str(BOUNDS),
           "reach_depth": 3,
           "groups": list(groups.values()), "cases": cases}
    return job, meta


def run_driver(job: dict, work: Path) -> dict:
    job_path = work / "job.json"
    job_path.write_text(json.dumps(job))
    env = dict(os.environ)
    env["OPENMC_CROSS_SECTIONS"] = str(XS_XML)
    r = subprocess.run([str(OPENMC_PY), str(DRIVER), str(job_path)],
                       capture_output=True, text=True, timeout=5400,
                       env=env)
    (work / "driver.stdout").write_text(r.stdout)
    (work / "driver.stderr").write_text(r.stderr)
    if r.returncode != 0:
        raise SystemExit(f"openmc driver failed:\n{r.stderr[-4000:]}")
    return json.loads(
        (work / "driver" / "openmc_results.json").read_text())


def heat_uW_g(atoms: dict, hl: dict, edec: dict) -> list[float]:
    """sum(N*lam*E) per state -> microW per gram (atoms are per cm3 of a
    1 cm3, 1 g/cm3 material = per gram)."""
    n_states = len(next(iter(atoms.values()))) if atoms else 0
    heat = [0.0] * n_states
    no_energy = []
    for nuc, series in atoms.items():
        t_half, e = hl.get(nuc), edec.get(nuc)
        if not t_half:
            continue
        if not e:
            no_energy.append(nuc)
            continue
        k = LN2 / t_half * e * EV_TO_J * 1.0e6
        for s, n in enumerate(series):
            heat[s] += k * n
    return heat, no_energy


def product_metrics(pairs: list[dict], key: str) -> dict:
    scored = [r for r in pairs
              if r.get(key, 0.0) > 0.0 and r["measured_uW_g"] > 0.0]
    if not scored:
        return {"points": 0,
                "unscored_nonpositive_calculation": len(pairs)}
    logs = [math.log(r[key] / r["measured_uW_g"]) for r in scored]
    abs_log = [abs(v) for v in logs]
    sig = [(r[key] - r["measured_uW_g"]) / r["sigma_uW_g"]
           for r in scored if r["sigma_uW_g"] > 0.0]
    return {
        "points": len(scored),
        "unscored_nonpositive_calculation": len(pairs) - len(scored),
        "geometric_mean_C_over_E": math.exp(
            sum(logs) / len(logs)),
        "maximum_abs_log_C_over_E": max(abs_log),
        "all_points_within_30_percent": all(
            v <= WITHIN_LOG for v in abs_log),
        "positive_sigma_points": len(sig),
        "rms_measurement_sigma": math.sqrt(
            sum(v * v for v in sig) / len(sig)) if sig else None}


def aggregate(records: list[dict], key: str) -> dict:
    scored_pairs = [r for rec in records for r in rec["pairs"]
                    if r.get(key, 0.0) > 0.0 and r["measured_uW_g"] > 0.0]
    if not scored_pairs:
        return {"experiments_scored": 0}
    logs = [math.log(r[key] / r["measured_uW_g"]) for r in scored_pairs]
    abs_log = sorted(abs(v) for v in logs)
    exps = [rec["metrics"][key] for rec in records
            if rec["metrics"].get(key, {}).get("points", 0) > 0]
    return {
        "experiments_scored": len(exps),
        "points_scored": len(scored_pairs),
        "pooled_geometric_mean_C_over_E": math.exp(
            sum(logs) / len(logs)),
        "median_pooled_abs_log_C_over_E": abs_log[len(abs_log) // 2],
        "p90_pooled_abs_log_C_over_E":
            abs_log[min(len(abs_log) - 1, int(0.9 * len(abs_log)))],
        "experiments_all_points_within_30_percent": sum(
            m["all_points_within_30_percent"] for m in exps)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=str(WORK_DEFAULT))
    ap.add_argument("--no-run", action="store_true",
                    help="reuse existing driver output")
    args = ap.parse_args()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    cb3 = json.loads(CB3.read_text())
    cb2 = json.loads(CB2.read_text())
    cb3_by_key = {(r["material"], r["experiment"]): r for r in cb3["records"]}
    cb2_by_key = {(r["material"], r["experiment"]): r for r in cb2["records"]}

    experiments = pick_experiments()
    job, meta = build_job(experiments, work)
    (work / "job.json").write_text(json.dumps(job, indent=1))
    print(f"{len(job['cases'])} cases, {len(job['groups'])} flux groups",
          file=sys.stderr)

    if not args.no_run:
        res = run_driver(job, work)
    else:
        res = json.loads(
            (work / "driver" / "openmc_results.json").read_text())
    hl = res.get("half_life_s") or {}
    edec = res.get("decay_energy_ev") or {}

    records = []
    for case in job["cases"]:
        key = (meta[case["case"]]["material"],
               meta[case["case"]]["experiment"])
        c3, c2 = cb3_by_key.get(key), cb2_by_key.get(key)
        if not c3 or not c2:
            print(f"  ! {key} missing from sealed records",
                  file=sys.stderr)
            continue
        out = res["cases"].get(case["case"]) or {}
        rec = {"material": key[0], "experiment": key[1],
               "n_pairs": len(c3["pairs"]), "openmc_status":
               out.get("status", "absent")}
        if out.get("status") == "executed":
            atoms = out["atoms_atom_per_cm3"]
            heat, no_e = heat_uW_g(atoms, hl, edec)
            t_irr = meta[case["case"]]["t_irr_s"]
            # state s sits at cum time sum(timesteps[:s]); cooling
            # boundary ci sits at t_irr + cooling_cum[ci]
            cooling = meta[case["case"]]["cooling_cum_s"]
            pairs = []
            for row in c3["pairs"]:
                t = row["time_s"]  # cooling offset (s after shutoff)
                s = min(range(len(cooling)),
                        key=lambda i: abs(cooling[i] - t))
                # state index: state 0 = initial, 1 = end irr,
                # 2+k = cooling boundary k
                om_heat = heat[s + 2] if s + 2 < len(heat) else None
                pair = {**row}
                if om_heat is not None:
                    pair["openmc_endfb8_uW_g"] = om_heat
                pair["boundary_match"] = bool(
                    abs(cooling[s] - t) <= max(0.02 * t, 1.0))
                c2row = next((p for p in c2["pairs"]
                              if abs(p["time_s"] - t) <=
                              max(0.02 * t, 1.0)), None)
                if c2row is not None:
                    pair["actinv_tendl2025_uW_g"] = \
                        c2row["actinv_tendl2025_uW_g"]
                pairs.append(pair)
            rec["pairs"] = pairs
            rec["n_decaying_without_energy"] = len(no_e)
            rec["openmc"] = {
                "deplete_s": out.get("deplete_s"),
                "boundary_match_failures": sum(
                    1 for p in pairs if not p["boundary_match"])}
            rec["metrics"] = {
                "openmc_endfb8_uW_g": product_metrics(
                    pairs, "openmc_endfb8_uW_g"),
                "actinv_tendl2017_uW_g": product_metrics(
                    pairs, "actinv_tendl2017_uW_g"),
                "actinv_tendl2025_uW_g": product_metrics(
                    pairs, "actinv_tendl2025_uW_g"),
                "fispact_tendl2017_uW_g": product_metrics(
                    pairs, "fispact_tendl2017_uW_g")}
        else:
            rec["metrics"] = {}
            rec["openmc"] = {"reason": out.get("reason")}
        records.append(rec)

    arms = ["openmc_endfb8_uW_g", "actinv_tendl2017_uW_g",
            "actinv_tendl2025_uW_g", "fispact_tendl2017_uW_g"]
    summary = {a: aggregate(records, a) for a in arms}
    output = {
        "schema": "actinv-openmc-fns-h2h-1",
        "note": "exploratory product+data comparison; ACTINV/FISPACT "
                "values read from sealed cb2/cb3 records, OpenMC arm "
                "executed fresh under openmc016/ENDF-B-VIII.1",
        "access": {"OpenMC": "executed", "ACTINV": "sealed-record",
                   "FISPACT-II": "sealed-published-reference"},
        "input_identities": {
            "chain_xml": sha256(CHAIN_XML), "xs_xml": sha256(XS_XML),
            "cb3_record": sha256(CB3), "cb2_record": sha256(CB2),
            "abundance_mass": sha256(ABUNDANCE),
            "driver": sha256(DRIVER), "control": sha256(Path(__file__))},
        "openmc_run": {
            "session_init_s": res.get("session_init_s"),
            "nuclides_tracked": res.get("nuclides_tracked"),
            "reachable_nuclides": res.get("reachable_nuclides"),
            "xs_missing_reachable": res.get("xs_missing_reachable"),
            "dropped_seed_isotopes":
                res.get("dropped_seed_isotopes"),
            "groups": res.get("groups")},
        "subset_experiments": len(records),
        "summary": summary,
        "records": records}
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps({"summary": summary,
                      "openmc_run": output["openmc_run"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
