#!/usr/bin/env python3
"""ACTINV P9 verdict: fission yields, coupled burn-up, pulses and decay heat."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def load(name: str):
    path = RESULTS / name
    return json.loads(path.read_text()) if path.exists() else None


def score(data, detail: str) -> str:
    if data is None:
        return "UNSCORED"
    return ("PASS" if data.get("pass") else "FAIL") + " — " + detail


def main() -> None:
    g1 = load("g1_p9_composition_yields.json")
    g2 = load("g2_p9_fission_matrix.json")
    g3 = load("g3_p9_coupled_auto.json")
    g4 = load("g4_p9_pulses_openmc.json")
    g5 = load("g5_p9_alara.json")
    g6 = load("g6_p9_conderc.json")
    gates = {
        "G1 composition/NFPY": score(
            g1,
            "pending"
            if g1 is None
            else (
                f"Rust/OpenMC max {g1['official_u235']['rust_vs_openmc_worst_relative']:.2e}; "
                f"yield-sum max deviation {max(row['deviation_from_two'] for row in g1['official_u235']['independent_sums']):.2e}"
            ),
        ),
        "G2 fission matrix/conservation": score(
            g2,
            "pending"
            if g2 is None
            else (
                f"matrix max {g2['worst_matrix_relative']:.2e}; "
                f"mapped+leakage={g2['midpoint_balance']['mapped_yield_sum'] + g2['midpoint_balance']['leakage_yield_sum']:.6g}"
            ),
        ),
        "G3 coupled/auto": score(
            g3,
            "pending"
            if g3 is None
            else (
                f"threshold max {g3['threshold_worst_relative']:.2e}; "
                f"coupled parent {g3['coupled_parent']['relative']:.2e}"
            ),
        ),
        "G4 pulses/OpenMC": score(
            g4,
            "pending"
            if g4 is None
            else (
                f"dense {g4['worst_actinv_vs_dense_resolvable_relative']:.2e}; "
                f"OpenMC CRAM48 {g4['worst_actinv_vs_openmc_cram48_resolvable_relative']:.2e}"
            ),
        ),
        "G5 ALARA identical data": score(
            g5,
            "pending"
            if g5 is None
            else (
                f"rate {g5['rates']['relative_difference']:.1e}; "
                f"inventory max {g5['maximum_inventory_relative_above_1e-10_initial']:.2e}"
            ),
        ),
        "G6 CoNDERC/provenance/regression": score(
            g6,
            "pending"
            if g6 is None
            else (
                f"Dickens total geom C/E {g6['dickens_pulse']['aggregate']['total']['geometric_mean_C_over_E']:.4f}; "
                f"Yarnell total {g6['yarnell_20000s']['aggregate']['total']['geometric_mean_C_over_E']:.4f}; "
                f"regression differences {g6['pre_p9_regression']['differences']}"
            ),
        ),
    }
    amendment = ROOT / "protocols" / "ACTINV-P9_AMENDMENT_A.md"
    if any(value == "UNSCORED" for value in gates.values()):
        verdict = "UNSCORED"
    elif any(value.startswith("FAIL") for value in gates.values()):
        verdict = "P9-FAIL"
    else:
        verdict = "P9-CONDITIONAL" if amendment.exists() else "P9-PASS"
    output = {
        "gates": gates,
        "repair_round": amendment.exists(),
        "amendment": amendment.name if amendment.exists() else None,
        "verdict": verdict,
    }
    (RESULTS / "verdict_p9.json").write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if verdict in ("P9-PASS", "P9-CONDITIONAL") else (2 if verdict == "P9-FAIL" else 3))


if __name__ == "__main__":
    main()
