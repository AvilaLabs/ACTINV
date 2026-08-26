#!/usr/bin/env python3
"""ACTINV P8 verdict: flux interchange and independent mesh execution gates G1–G6."""
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
    g1 = load("g1_p8_canonical_rebin.json")
    g2 = load("g2_p8_openmc.json")
    g3 = load("g3_p8_mcnp.json")
    g4 = load("g4_p8_provenance.json")
    g5 = load("g5_p8_mesh_identity.json")
    g6 = load("g6_p8_scaling_regression.json")
    gates = {
        "G1 canonical/FISPACT/rebin": score(
            g1,
            "pending"
            if g1 is None
            else f"repeat bytes={g1['repeat_bytes_identical']}; rebin max {g1['split_rebin_max_abs_difference']:.1e}",
        ),
        "G2 OpenMC statepoint": score(
            g2,
            "pending"
            if g2 is None
            else (
                f"two filter orders at max {max(v['max_flux_relative'] for v in g2['comparisons'].values()):.1e}; "
                f"48 MiB padding added {g2['bounded_window']['peak_rss_extra_bytes']} RSS bytes"
            ),
        ),
        "G3 MCNP readers": score(
            g3,
            "pending"
            if g3 is None
            else (
                f"meshtal {g3['meshtal']['max_flux_relative']:.1e}, mctal {g3['mctal']['max_flux_relative']:.1e}; "
                "five unsupported plants named"
            ),
        ),
        "G4 provenance/interchange": score(
            g4,
            "pending"
            if g4 is None
            else (
                f"four repeat identities={all(g4['repeat_bytes_identical'].values())}; "
                f"cross-format exact={g4['cross_format_spectrum_and_total_exact']}"
            ),
        ),
        "G5 mesh identity/determinism": score(
            g5,
            "pending"
            if g5 is None
            else (
                f"{sum(g5['ordinary_identity_per_cell'])}/{g5['cells']} exact ordinary identities, "
                f"{g5['distinct_pruned_state_counts']} pruning counts"
            ),
        ),
        "G6 scaling/regression": score(
            g6,
            "pending"
            if g6 is None
            else (
                f"RSS spread {g6['bounded_memory']['measured_peak_rss_spread_bytes']} bytes; "
                "10^6 row explicitly extrapolated; tests/Clippy pass"
            ),
        ),
    }
    amendment = ROOT / "protocols" / "ACTINV-P8_AMENDMENT_A.md"
    if any(value == "UNSCORED" for value in gates.values()):
        verdict = "UNSCORED"
    elif any(value.startswith("FAIL") for value in gates.values()):
        verdict = "P8-FAIL"
    else:
        verdict = "P8-CONDITIONAL" if amendment.exists() else "P8-PASS"
    output = {
        "gates": gates,
        "repair_round": amendment.exists(),
        "amendment": amendment.name if amendment.exists() else None,
        "verdict": verdict,
    }
    (RESULTS / "verdict_p8.json").write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if verdict in ("P8-PASS", "P8-CONDITIONAL") else (2 if verdict == "P8-FAIL" else 3))


if __name__ == "__main__":
    main()
