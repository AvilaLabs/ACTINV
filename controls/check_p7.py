#!/usr/bin/env python3
"""ACTINV P7 verdict: decay-photon reader/source/dose/export/provenance gates G1–G6."""
from __future__ import annotations

import json
import sys
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
    g1 = load("g1_p7_spectrum_reader.json")
    g2 = load("g2_p7_source_conservation.json")
    g3 = load("g3_p7_inventory_identity.json")
    g4 = load("g4_p7_dose.json")
    g5 = load("g5_p7_exports.json")
    g6 = load("g6_p7_provenance_regression.json")
    gates = {
        "G1 spectrum reader": score(
            g1,
            "pending" if g1 is None else (
                f"{g1['python_audit']['sections']} sections/{g1['python_audit']['spectra']} spectra; "
                f"selected-field max {g1['max_relative_field_difference']:.2e}"
            ),
        ),
        "G2 source conservation": score(
            g2,
            "pending" if g2 is None else (
                f"count {g2['worst_count_closure_relative']:.2e}, E_EM {g2['worst_energy_to_E_EM_relative']:.2e}; "
                "missing/outside plants recovered"
            ),
        ),
        "G3 inventory identity": score(
            g3,
            "pending" if g3 is None else (
                f"{g3['steps']} steps/{g3['photon_nuclide_rows']} nuclide rows; "
                f"CLI/Python differences {g3['cli_vs_python_differences']}"
            ),
        ),
        "G4 dose references": score(
            g4,
            "pending" if g4 is None else (
                f"Co60 {g4['references']['Co60']['relative']:.2%}, "
                f"Cs137 equilibrium {g4['references']['Cs137_Ba137m_equilibrium']['relative']:.2%} from reference"
            ),
        ),
        "G5 transport exports": score(
            g5,
            "pending" if g5 is None else (
                f"{g5['nonzero_groups']} groups, strength {g5['strength_relative']:.1e}, "
                f"MCNP max line {g5['mcnp_max_line_length']}"
            ),
        ),
        "G6 provenance/regression": score(
            g6,
            "pending" if g6 is None else (
                f"hash failures hard={g6['hash_mismatch_hard_errors']}; "
                f"pre-P7 scalar differences {g6['pre_P7_scalar_regression']['differences']}"
            ),
        ),
    }
    amendment = ROOT / "protocols" / "ACTINV-P7_AMENDMENT_A.md"
    if any(value == "UNSCORED" for value in gates.values()):
        verdict = "UNSCORED"
    elif any(value.startswith("FAIL") for value in gates.values()):
        verdict = "P7-FAIL"
    else:
        verdict = "P7-CONDITIONAL" if amendment.exists() else "P7-PASS"
    output = {
        "gates": gates,
        "repair_round": amendment.exists(),
        "amendment": amendment.name if amendment.exists() else None,
        "verdict": verdict,
    }
    (RESULTS / "verdict_p7.json").write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if verdict in ("P7-PASS", "P7-CONDITIONAL") else (2 if verdict == "P7-FAIL" else 3))


if __name__ == "__main__":
    main()
