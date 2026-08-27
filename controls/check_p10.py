#!/usr/bin/env python3
"""Derive the P10 verdict from the seven frozen data-completeness gates."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
AMENDMENTS = [
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_A.md",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_B.md",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_C.md",
]


def load(name: str):
    path = RESULTS / name
    return json.loads(path.read_text()) if path.exists() else None


def score(passed: bool | None, detail: str) -> str:
    if passed is None:
        return "UNSCORED"
    return ("PASS" if passed else "FAIL") + " — " + detail


def main() -> None:
    g1 = load("g1_p10_builder.json")
    g2 = load("g2_p10_rmatrix.json")
    g3_njoy = load("g3_p10_unresolved_njoy.json")
    g3_quadrature = load("g3_p10_unresolved_quadrature.json")
    g4 = load("g4_p10_temperature_narrow.json")
    g5 = load("g5_p10_charged.json")
    g6 = load("g6_p10_projectile_runtime.json")
    g7 = load("g7_p10_complete.json")

    g1_fields = (
        sum(
            item["fields_compared"]
            for item in g1["parser_parity"]["inputs"].values()
        )
        if g1
        else 0
    )
    g1_determinism = g1["determinism_and_cache"] if g1 else {}
    g2_integral = g2["flat_lethargy_integral"] if g2 else {}
    g3_pass = (
        None
        if g3_njoy is None or g3_quadrature is None
        else bool(g3_njoy.get("pass") and g3_quadrature.get("pass"))
    )
    g4_density = g4["density_convergence"] if g4 else {}
    g5_pointwise = g5["pointwise_2025"] if g5 else {}
    g5_worst = (
        max(
            (entry["max_relative_to_official"] for entry in g5_pointwise.values()),
            default=0.0,
        )
        if g5
        else 0.0
    )
    g7_builds = g7.get("builds", {}) if g7 else {}
    g7_targets = sum(
        int(item.get("targets", 0)) for item in g7_builds.values()
    )

    gates = {
        "G1 Rust builder/parity/determinism": score(
            None if g1 is None else bool(g1.get("pass")),
            "pending"
            if g1 is None
            else (
                f"{g1_fields} retained fields; max parser ULP 2; "
                f"fresh/cached identity={g1_determinism.get('byte_identical')}"
            ),
        ),
        "G2 R-matrix-limited W-186": score(
            None if g2 is None else bool(g2.get("pass")),
            "pending"
            if g2 is None
            else (
                f"{g2['structure']['resonances']} resonances; "
                f"group max {g2['ccfe_709']['maximum_relative']:.3e}; "
                f"integral {g2_integral.get('relative', float('nan')):.3e}"
            ),
        ),
        "G3 Ag-107 unresolved averages": score(
            g3_pass,
            "pending"
            if g3_pass is None
            else (
                f"NJOY group max {g3_njoy['capture_groups']['max_relative']:.3e}; "
                f"quadrature max {g3_quadrature['maximum_quadrature_relative']:.3e}"
            ),
        ),
        "G4 temperature/ultra-narrow": score(
            None if g4 is None else bool(g4.get("pass")),
            "pending"
            if g4 is None
            else (
                f"{g4['fr226_ultra_narrow']['line_count']} analytic lines; "
                f"density max {g4_density.get('maximum_relative', float('nan')):.3e}; "
                f"flags={len(g4_density.get('convergence_flags', []))}"
            ),
        ),
        "G5 charged particles/FISPACT rows": score(
            None if g5 is None else bool(g5.get("pass")),
            "pending"
            if g5 is None
            else (
                f"TENDL-2025 point max {g5_worst:.3e}; "
                f"processed-row max {max(item['max_row_relative_to_processed'] for item in g5['processed_2017'].values()):.3e}"
            ),
        ),
        "G6 runtime projectile contract": score(
            None if g6 is None else bool(g6.get("pass")),
            "pending"
            if g6 is None
            else (
                f"entry-point identity={all(g6['entry_point_identity'].values())}; "
                f"analytic max {g6['max_analytic_relative_error']:.3e}; "
                f"legacy neutron={g6['pre_p10_neutron']['pass']}"
            ),
        ),
        "G7 complete builds/provenance/regression": score(
            None if g7 is None else bool(g7.get("pass")),
            "pending"
            if g7 is None
            else (
                f"{len(g7_builds)} libraries/{g7_targets} targets; "
                f"cached identities={all(item.get('cached_identity', False) for item in g7_builds.values())}; "
                f"EAF={g7.get('eaf_regression', {}).get('pass', False)}; "
                f"regressions={g7.get('regressions', {}).get('pass', False)}"
            ),
        ),
    }
    if any(value == "UNSCORED" for value in gates.values()):
        verdict = "UNSCORED"
    elif any(value.startswith("FAIL") for value in gates.values()):
        verdict = "P10-FAIL"
    else:
        verdict = "P10-CONDITIONAL" if any(path.exists() for path in AMENDMENTS) else "P10-PASS"
    output = {
        "gates": gates,
        "repair_round": any(path.exists() for path in AMENDMENTS),
        "amendments": [path.name for path in AMENDMENTS if path.exists()],
        "verdict": verdict,
    }
    (RESULTS / "verdict_p10.json").write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(
        0
        if verdict in ("P10-PASS", "P10-CONDITIONAL")
        else (2 if verdict == "P10-FAIL" else 3)
    )


if __name__ == "__main__":
    main()
