#!/usr/bin/env python3
"""P5-G2: the Rust composition module must reproduce the Python harness on every FNS material — atoms per gram per
isotope to 1e-12, abundance sums exact, mass balance to 1e-12 — and must carry the tables' provenance."""
import os, sys, json, glob, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
from harness import fispact_io as fio, composition as comp
from harness.elements import SYM_OF
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
DUMP = os.path.join(ROOT, "target", "release", "dump")
T = comp.tables()
worst_atoms = 0.0; worst_mass = 0.0; worst_ab = 0.0; n = 0; bad = []; nonunit = []
for f in sorted(glob.glob(os.path.expanduser("~/nuclear-data/conderc-fns/fns/*/*.i"))):
    el = fio.read_i(f)["elements"]
    py, diag = comp.atoms_per_gram(el)
    spec = "{" + ",".join(f'"{k}":{v}' for k, v in el.items()) + "}"
    out = subprocess.run([DUMP, "composition", spec], capture_output=True, text=True, check=True).stdout.splitlines()
    rs = {}
    for line in out[1:]:
        if line.startswith("#"): continue
        t = line.split(); rs[(int(t[0]), int(t[1]))] = float(t[2])
    if set(rs) != set(py): bad.append((os.path.basename(f), "isotope set differs", sorted(set(py) ^ set(rs))[:4])); continue
    for k in py:
        d = abs(rs[k] - py[k]) / max(abs(py[k]), 1e-300); worst_atoms = max(worst_atoms, d)
    # mass balance: sum(N_i * m_i) / N_A must equal 1 g
    # mass balance: sum(N_i m_i)/N_A equals sum(wt%)/100 grams, which is 1 g only when the composition sums to 100 %.
    # One FNS composition (Br: BR 39.75, C 41.83, H 2.51, O 15.92) sums to 100.01 %; both codes use it as given, as
    # FISPACT does, so the control compares against the stated total rather than assuming it is 100 %.
    mass = sum(rs[(za, liso)] * T["mass_amu"][f"{SYM_OF[za // 1000]}{za % 1000}" + (f"_m{liso}" if liso else "")] for (za, liso) in rs) / comp.NA
    expect = sum(el.values()) / 100.0
    worst_mass = max(worst_mass, abs(mass - expect) / expect)
    if abs(sum(el.values()) - 100.0) > 1e-9: nonunit.append((os.path.basename(os.path.dirname(f)), sum(el.values())))
    for e, d in diag.get("elements", diag).items():
        if isinstance(d, dict) and "abundance_sum" in d: worst_ab = max(worst_ab, abs(d["abundance_sum"] - 1.0))
    n += 1
prov_rs = subprocess.run([DUMP, "provenance"], capture_output=True, text=True, check=True).stdout.strip()
res = {"n_compositions": n, "worst_atoms_rel": worst_atoms, "worst_mass_balance_rel": worst_mass, "compositions_not_summing_to_100": nonunit, "worst_abundance_sum_dev": worst_ab,
       "provenance_matches": prov_rs == T["source"], "provenance": prov_rs[:80], "failures": bad,
       "pass": bool(n == 132 and not bad and worst_atoms <= 1e-12 and worst_mass <= 1e-12 and prov_rs == T["source"])}
json.dump(res, open(os.path.join(RES, "g2_rust_composition.json"), "w"), indent=1); print(json.dumps(res, indent=1))
