#!/usr/bin/env python3
"""Derive the P11 verdict from the six frozen uncertainty gates."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
AMENDMENTS = [
    ROOT / "protocols/ACTINV-P11_AMENDMENT_A.md",
    ROOT / "protocols/ACTINV-P11_AMENDMENT_B.md",
    ROOT / "protocols/ACTINV-P11_AMENDMENT_C.md",
    ROOT / "protocols/ACTINV-P11_AMENDMENT_D.md",
    ROOT / "protocols/ACTINV-P11_AMENDMENT_E.md",
]


def load(name: str) -> dict[str, object] | None:
    path = RESULTS / name
    return json.loads(path.read_text()) if path.exists() else None


def score(passed: bool | None, detail: str) -> str:
    if passed is None:
        return "UNSCORED"
    return ("PASS" if passed else "FAIL") + " — " + detail


def main() -> None:
    g1 = load("g1_p11_covariance.json")
    g2 = load("g2_p11_collapse.json")
    g3 = load("g3_p11_sensitivity.json")
    g4 = load("g4_p11_propagation.json")
    g5 = load("g5_p11_entry_points.json")
    g6 = load("g6_p11_complete.json")

    gates = {
        "G1 strict MF33 parser/deterministic sidecar": score(
            None if g1 is None else bool(g1.get("pass")),
            "pending"
            if g1 is None
            else (
                f"{g1['sidecar_parity']['components']} components/"
                f"{g1['sidecar_parity']['fields_compared']} fields at "
                f"{g1['sidecar_parity']['maximum_ulp_distance']} ULP; "
                f"12 plants; peak RSS {g1['peak_child_rss_bytes']} bytes"
            ),
        ),
        "G2 covariance collapse/reference": score(
            None if g2 is None else bool(g2.get("pass")),
            "pending"
            if g2 is None
            else (
                f"synthetic max {g2['synthetic']['maximum_relative']:.3e}; "
                f"NJOY max {max(value['maximum_relative_compared'] for value in g2['njoy_errorr']['spectra'].values()):.3e}"
            ),
        ),
        "G3 CRAM48/analytic sensitivities": score(
            None if g3 is None else bool(g3.get("pass")),
            "pending"
            if g3 is None
            else (
                f"connected sensitivity max {g3['sensitivities']['maximum_relative']:.3e}; "
                f"absolute max {g3['sensitivities']['maximum_absolute']:.3e}; "
                f"legacy CRAM16={g3['sensitivities']['legacy_default_equals_explicit_cram16']}"
            ),
        ),
        "G4 propagated variance/sampling": score(
            None if g4 is None else bool(g4.get("pass")),
            "pending"
            if g4 is None
            else (
                f"{g4['samples']} samples; sample relative {g4['sample_relative']:.3e}; "
                f"Rust relative {g4['rust_relative']:.3e}"
            ),
        ),
        "G5 reports/certificates/entry points": score(
            None if g5 is None else bool(g5.get("pass")),
            "pending"
            if g5 is None
            else (
                f"{g5['response_records']} response records; "
                f"entry identity={all(value for name, value in g5['entry_points'].items() if name != 'labels')}; "
                f"10 plants"
            ),
        ),
        "G6 complete corpus/regression/docs": score(
            None if g6 is None else bool(g6.get("pass")),
            "pending"
            if g6 is None
            else (
                f"{g6['independent_scan']['files']} files/"
                f"{g6['independent_scan']['components']} components; "
                f"fresh/cached identity={g6['identity']['npz_byte_identical'] and g6['identity']['index_byte_identical']}; "
                f"covered eligible rows={g6['coverage']['covered_rows']}/{g6['coverage']['eligible_non_mf10_rows']}"
            ),
        ),
    }
    if any(value == "UNSCORED" for value in gates.values()):
        verdict = "UNSCORED"
    elif any(value.startswith("FAIL") for value in gates.values()):
        verdict = "P11-FAIL"
    else:
        verdict = "P11-CONDITIONAL" if any(path.exists() for path in AMENDMENTS) else "P11-PASS"
    output = {
        "gates": gates,
        "repair_round": any(path.exists() for path in AMENDMENTS),
        "amendments": [path.name for path in AMENDMENTS if path.exists()],
        "verdict": verdict,
    }
    (RESULTS / "verdict_p11.json").write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(
        0
        if verdict in ("P11-PASS", "P11-CONDITIONAL")
        else (2 if verdict == "P11-FAIL" else 3)
    )


if __name__ == "__main__":
    main()
