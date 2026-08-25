"""Decay-heat evaluator for ANY inventory: P (W/g) = sum_i lambda_i N_i (E_light + E_EM + E_heavy)_i, energies in eV
from ENDF/B-VIII.0 MT=457 (own parser). Inventory: {(ZA, LISO): atoms per gram}."""
import os, sys, math
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from endf_decay import parse_decay_file
from decay_sources import merged_records
EV = 1.602176634e-19; LN2 = math.log(2.0)
_D = None
def decay_table(path=None):
    global _D
    if _D is None:
        if path is None: recs, prov, _stats = merged_records()
        else: recs = parse_decay_file(path); prov = {}
        _D = {}
        for r in recs.values():
            lam = 0.0 if (r["nst"] == 1 or r["half_life"] <= 0) else LN2 / r["half_life"]
            e = r["energies"] + [0.0] * 6
            key = (int(round(r["za"])), r["liso"]); _D[key] = {"lambda": lam, "E_light": e[0], "E_EM": e[2], "E_heavy": e[4], "nst": r["nst"], "source": prov.get(key, "ENDF/B-VIII.0")}
    return _D
def heat_W_per_g(inv, parts=("E_light", "E_EM", "E_heavy")):
    """Returns (total W/g, per-nuclide dict W/g, ledger list of nuclides with no decay data)."""
    D = decay_table(); tot = 0.0; per = {}; missing = []
    for key, N in inv.items():
        d = D.get(key)
        if d is None: missing.append({"za": key[0], "liso": key[1], "atoms_per_g": N}); continue
        p = d["lambda"] * N * sum(d[k] for k in parts) * EV
        if p != 0.0: per[key] = p; tot += p
    return tot, per, missing
