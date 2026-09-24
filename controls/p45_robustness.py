#!/usr/bin/env python3
"""P45 robustness leg — capability-asymmetry cost measurement.

Builds the frozen study over the 24-case Cartesian core of the variant
campaign (12 dopant materials × {60 s, 86400 s} on fns_709) with
`robustness { samples: 16, channels: [cross_sections, decay_constants],
seed: 20260924, first_order_comparison: false }` and runs it under the
caller cgroup, timed. No comparator admits this capability — it is
timed and reported, never folded into a speed ratio.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import g2_p26b_leg as leg  # noqa: E402
from g2_p26b_contract import sha256_file  # noqa: E402

POPULATION = ROOT / "results" / "p45_population.json"
OUT = ROOT / "results" / "p45_robustness.json"
WORK = Path.home() / "nuclear-data" / "p45-work" / "robustness"

ACTINV = leg.ACTINV
TENDL_NPZ = ROOT / "actinv-data" / "v1.1.0" / "activation" / \
    "tendl-2025-patched-neutron-709g.npz"
COV_NPZ = Path.home() / "nuclear-data" / "p43-work" / "p43.cov.npz"
COOL_DT_S = leg.COOL_DT_S
DOPANTS = ["co", "cr", "mn", "v"]
LEVELS_WPPM = [10, 1000, 100000]
IRR_S = [60.0, 86400.0]


def build_study(pop: dict) -> dict:
    """Materials × schedules study matching the 24-case core."""
    fns = leg.fns_spectrum()
    by_name = {c["case"]: c for c in pop["campaign"]["cases"]}
    materials = []
    for d in DOPANTS:
        for lv in LEVELS_WPPM:
            # composition comes from any case of that material
            src = by_name[f"fe_{d}{lv}wppm__fns_709__60s"]
            materials.append({
                "name": f"fe_{d}{lv}wppm",
                "mass_g": 1.0,
                "composition": {el: wf * 100.0 for el, wf in
                                src["composition_wt_fraction"].items()}})
    cool_steps = [{"dt": f"{dt} s", "flux": 0.0} for dt in COOL_DT_S]
    study = {
        "study": "actinv-study-1",
        "study_id": "p45-robustness",
        "library": {"path": str(TENDL_NPZ),
                    "sha256": sha256_file(TENDL_NPZ)},
        "decay": {"primary": str(leg.DECAY_PRIMARY),
                  "fallback": str(leg.DECAY_FALLBACK)},
        "cases": {"materials": materials,
                  "spectra": [{"name": "fns_709",
                               "flux_per_group": fns,
                               "structure": "fispact-709",
                               "descending": True,
                               "total": sum(fns)}],
                  "schedules": [
                      {"name": "irr_60s",
                       "steps": [{"dt": "60 s", "flux": 1.0}]
                                + cool_steps},
                      {"name": "irr_86400s",
                       "steps": [{"dt": "86400 s", "flux": 1.0}]
                                + cool_steps}]},
        "responses": ["total_atoms_per_g", "total_activity_bq_per_g",
                      "decay_heat_w_per_g"],
        "options": {"mode": "auto", "prune": "rate",
                    "bmin_atoms_per_g": 1e-08,
                    "temperature_K": 293.6,
                    "outputs": ["inventory", "activity", "heat",
                                "ledger", "certificate"]},
        "robustness": {
            "samples": 16,
            "seed": 20260924,
            "covariance": {"path": str(COV_NPZ),
                           "sha256": sha256_file(COV_NPZ)},
            "channels": {
                "cross_section_mf33": True,
                "decay_constants": True,
                "fission_yields": False,
                "flux_rel_std": 0.0,
                "composition_rel_std": {},
            },
            "responses": ["total_atoms_per_g",
                          "total_activity_bq_per_g",
                          "decay_heat_w_per_g"],
            "first_order_comparison": False,
        },
    }
    return study


def main() -> int:
    pop = json.loads(POPULATION.read_text())
    study = build_study(pop)
    WORK.mkdir(parents=True, exist_ok=True)
    spath = WORK / "robustness_study.json"
    spath.write_text(json.dumps(study))
    out_path = WORK / "robustness_out"
    t0 = time.monotonic()
    r = leg.timed_run(
        [str(ACTINV), "study", "run", str(spath), str(out_path)],
        WORK, timeout=5400.0)
    wall = time.monotonic() - t0
    rec_path = out_path / "study_record.json"
    rec = {"spec": "actinv-p45-robustness-1",
           "study_sha256": sha256_file(spath),
           "wall_s": wall, "returncode": r["returncode"],
           "record_sha256": sha256_file(rec_path)
           if rec_path.exists() else None}
    if rec_path.exists():
        o = json.loads(rec_path.read_text())
        rec["n_cases"] = len(o.get("cases") or [])
        rec["failed_samples"] = sum(
            (c.get("robustness") or {}).get("n_failed_samples") or 0
            for c in o.get("cases") or [])
        rec["study_status"] = o.get("status")
    if r["returncode"] != 0:
        rec["failure"] = r.get("failure") or "study_error"
        rec["stderr_tail"] = (r.get("stderr") or "")[-2000:]
    OUT.write_text(json.dumps(rec, indent=1))
    print(json.dumps(rec))
    return 0 if r["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
