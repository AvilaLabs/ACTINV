#!/usr/bin/env python3
"""P2-G3 harness controls: (a) Mn-56 hand calculation vs evaluator; (b) .out TOTAL HEAT -> kW/kg == .nuclides Total at all
matching steps for every experiment with a .nuclides file; (c) composition closure for all 132 experiments.
Writes results/g3_harness.json."""
import os, sys, json, glob, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import fispact_io as fio, composition as comp, decayheat as dh
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"); DATA = os.path.expanduser("~/nuclear-data/conderc-fns/fns")
D = dh.decay_table(); mn = D[(25056, 0)]; tot, per, miss = dh.heat_W_per_g({(25056, 0): 1e15}); hand = mn["lambda"] * 1e15 * (mn["E_light"] + mn["E_EM"] + mn["E_heavy"]) * 1.602176634e-19
a = {"evaluator_W_g": tot, "hand_W_g": hand, "rel": abs(tot - hand) / hand}; a["pass"] = a["rel"] <= 1e-10
worst_b = 0.0; n_b = 0; worst_c_ab = 0.0; worst_c_mass = 0.0; n_c = 0; issues = []
T = comp.tables()
for mat in sorted(os.listdir(DATA)):
    for ef in sorted(glob.glob(os.path.join(DATA, mat, "*.exp"))):
        tag = os.path.basename(ef)[:-4]; d = os.path.join(DATA, mat)
        inp = fio.read_i(os.path.join(d, f"TENDL-2017_{tag}.i"))
        of, nf = os.path.join(d, f"TENDL-2017_{tag}.out"), os.path.join(d, f"TENDL-2017_{tag}.nuclides")
        if os.path.exists(of) and os.path.exists(nf):
            h = fio.read_out_heat(of); nuc = fio.read_nuclides(nf)
            cool = [x for x in h if x["cooling"]]; k = min(len(cool), len(nuc["total_kW_kg"]) - 1)
            for j in range(k):
                ref = nuc["total_kW_kg"][j + 1]; val = cool[j]["heat_kW"] / inp["mass_kg"]
                if ref > 0: worst_b = max(worst_b, abs(val - ref) / ref); n_b += 1
        else: issues.append({"experiment": f"{mat}/{tag}", "missing": [p for p in (of, nf) if not os.path.exists(p)]})
        inv, diag = comp.atoms_per_gram(inp["elements"]); n_c += 1
        for el, dd in diag["elements"].items():
            if isinstance(dd, str): issues.append({"experiment": f"{mat}/{tag}", "element": el, "issue": dd}); continue
            worst_c_ab = max(worst_c_ab, abs(dd["abundance_sum"] - 1.0))
        # mass balance: sum_i N_i M_i / NA over isotopes of each element == wt%/100 g per gram
        mass_tot = 0.0
        for (za, liso), N in inv.items():
            z, A = divmod(za, 1000); from harness.elements import SYM_OF; name = f"{SYM_OF[z]}{A}" + (f"_m{liso}" if liso else ""); mass_tot += N * T["mass_amu"][name] / comp.NA
        worst_c_mass = max(worst_c_mass, abs(mass_tot - sum(inp["elements"].values()) / 100.0))
out = {"control_a": a, "control_b": {"n_points": n_b, "max_rel": worst_b, "pass": worst_b <= 1e-6}, "control_c": {"n_experiments": n_c, "max_abundance_sum_dev": worst_c_ab, "max_mass_balance_dev_g": worst_c_mass, "pass": worst_c_ab <= 1e-12 and worst_c_mass <= 1e-12, "issues": issues}}
out["pass"] = a["pass"] and out["control_b"]["pass"] and out["control_c"]["pass"]
conv = lambda o: o.item() if hasattr(o, "item") else str(o)
json.dump(out, open(os.path.join(RES, "g3_harness.json"), "w"), indent=1, default=conv); print(json.dumps(out, indent=1, default=conv))
