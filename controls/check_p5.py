#!/usr/bin/env python3
"""ACTINV P5 verdict: G1 readers, G2 composition, G3 entry-point identity, G4 physics unchanged, G5 pathways,
G6 ledger and certificate, G7 mode selection. Plus G0, the CRAM coefficient control added during the phase."""
import os, sys, json, glob
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
def load(n):
    p = os.path.join(RES, n); return json.load(open(p)) if os.path.exists(p) else None
gates = {}
for tag, fn, fmt in (
    ("G0 cram coefficients", "g0_cram_coefficients.json", lambda d: f"r(0) exact to {d['CRAM16']['r0_error']:.1e}, abs vs exp {d['CRAM16']['worst_abs_vs_exp_on_[-50,0]']:.1e}, generated == recorded"),
    ("G1 readers", "g1_rust_readers.json", lambda d: f"decay {d['decay']['n_mismatch']} mismatches over {d['decay']['n_checked']}; library {d['library']['rows']} rows byte-identical"),
    ("G2 composition", "g2_rust_composition.json", lambda d: f"{d['n_compositions']} materials, atoms {d['worst_atoms_rel']:.1e}, mass {d['worst_mass_balance_rel']:.1e}"),
    ("G3 entry points", "g3_entry_points.json", lambda d: f"{d['scalars_compared_per_pair']} scalars, {d['cli_vs_python']['n_differences']} differences, certificates identical"),
    ("G4 physics unchanged", "g4_physics_unchanged.json", lambda d: f"{d['n_compared']} experiments, worst {d['worst_abs_uW_g']:.1e} uW/g abs, {d['worst_rel_above_peak_fraction']:.1e} rel"),
    ("G5 pathways", "g5_pathways.json", lambda d: f"closure {d['closure']:.1e}; planted removal exact to {d['planted']['drop_matches_contribution_rel']:.1e}"),
    ("G6 ledger/certificate", "g6_ledger_certificate.json", lambda d: f"{d['present']}/{d['required_categories']} categories; planted failure identical across entry points"),
    ("G7 mode selection", "g7_mode_selection.json", lambda d: f"auto=trace at burnup {d['low_burnup']['burnup']:.1e}; coupled floor {d['low_burnup']['floor_ratio_coupled_over_trace']:.1e}x trace's"),
):
    d = load(fn)
    gates[tag] = "UNSCORED" if d is None else (("PASS" if d.get("pass") else "FAIL") + " — " + fmt(d))
amended = [os.path.basename(p) for p in glob.glob(os.path.join(RES, "..", "protocols", "ACTINV-P5_AMENDMENT_*.md"))]
if any(v == "UNSCORED" for v in gates.values()): verdict = "UNSCORED"
elif any(v.startswith("FAIL") for v in gates.values()): verdict = "P5-FAIL"
else: verdict = "P5-CONDITIONAL" if amended else "P5-PASS"
out = {"gates": gates, "amendments": amended, "verdict": verdict}
json.dump(out, open(os.path.join(RES, "verdict_p5.json"), "w"), indent=1); print(json.dumps(out, indent=1))
sys.exit(0 if verdict.startswith("P5-PASS") or verdict.startswith("P5-COND") else (2 if verdict == "P5-FAIL" else 3))
