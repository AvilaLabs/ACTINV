#!/usr/bin/env python3
"""P47 G4 verdict — aggregates gate records into the verdict.
PASS only if all gates hold; CONDITIONAL for declared conditions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/verdict_p47.json"


def load(name):
    return json.loads((ROOT / "results" / name).read_text())


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    seal = load("g0_p47_seals.json")
    g1 = load("g1_p47_completeness.json")
    g2 = load("g2_p47_controls.json")
    g3 = load("g3_p47_report.json")

    conditions = [
        "geometry provenance: self-produced — no lawful external "
        "benchmark (SINBAD) was available; the condition is named "
        "verbatim, not discharged by silence",
        "the dose leg reports detector air dose with MC std-dev via "
        "EnergyFunctionFilter (E*mu_en,air) — a dose estimate on the "
        "executed geometry, still not a qualified dosimetry "
        "prediction (no detector model)",
        "contact-proxy/transported ratio is reported with named "
        "drivers; no dose claim is made from the proxy",
        "MCNP distributed-source emission remains a documented "
        "placeholder (no licensed MCNP verification route)",
    ]
    sys.path.insert(0, str(ROOT / "controls"))
    import p47_artifacts as p47a
    cur = p47a.verify()
    gates = {
        "g0_seal": all(
            r["present"] and
            r["actual_sha256"] == seal["artifacts"][n]["sha256"]
            for n, r in cur.items()),
        "g1_completeness": g1["all_pass"],
        "g2_controls": g2["all_pass"],
        "g3_report_issued": bool(
            g3["activation_comparison"]["n_inside_band"]
            == g3["activation_comparison"]["n_top50"]),
    }
    all_pass = all(gates.values())
    verdict = "P47-CONDITIONAL" if all_pass else "P47-FAIL"
    # PASS vs CONDITIONAL: external geometry unavailable is a
    # protocol-declared condition -> CONDITIONAL ceiling
    basis = (
        f"dose leg verified byte-identical and qualified by the "
        f"analytic uncollided control (rel_dev "
        f"{g2['controls'][0]['detail']['rel_dev']:.4f} inside "
        f"tolerance); propagated-band comparison issued "
        f"({g3['activation_comparison']['n_inside_band']}/"
        f"{g3['activation_comparison']['n_top50']} inside band); "
        f"self-produced geometry condition named verbatim")
    out = {
        "spec": "actinv-verdict-1",
        "phase": "P47",
        "verdict": verdict,
        "gates": gates,
        "basis": basis,
        "conditions": conditions,
        "dose_table": g3["dose_table"],
        "comparison": {
            "median_rel_top50": g3["activation_comparison"]
            ["median_rel_top50"],
            "max_rel_top50": g3["activation_comparison"]
            ["max_rel_top50"],
            "inside_band": f"{g3['activation_comparison']['n_inside_band']}"
                           f"/{g3['activation_comparison']['n_top50']}",
        },
        "contact_proxy_vs_transported":
            g3["contact_proxy_vs_transported"],
        "evidence_sha256": {
            "seal": sha256(ROOT / "results/g0_p47_seals.json"),
            "g1": sha256(ROOT / "results/g1_p47_completeness.json"),
            "g2": sha256(ROOT / "results/g2_p47_controls.json"),
            "g3": sha256(ROOT / "results/g3_p47_report.json"),
            "p32_dose": sha256(ROOT / "results/p32_dose.json"),
            "p32_tally_error": sha256(
                ROOT / "results/p32_tally_error.json"),
        },
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"verdict": verdict, "gates": gates}))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
