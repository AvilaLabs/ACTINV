#!/usr/bin/env python3
"""ACTINV P1-G2 part 1: decay network -> sparse Bateman matrix with an explicit leakage row.
Daughters resolved from ENDF RTYP/RFS; anything unresolved (SF, unknown mode, daughter absent) is
booked to leakage with a reason — never dropped. Writes results/chain_stats.json."""
import os, sys, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from endf_decay import parse_decay_file
from decay_sources import merged_records
LN2 = math.log(2.0)
STEP = {1: (1, 0), 2: (-1, 0), 3: (0, 0), 4: (-2, -4), 5: (0, -1), 7: (-1, -1)}  # dZ, dA per elementary mode
def rtyp_digits(rtyp):
    s = ("%.6f" % rtyp).rstrip("0").rstrip(".")
    if "." in s: a, b = s.split("."); return [int(a)] + [int(ch) for ch in b]
    return [int(s)]
def build(path=None):
    if path is None: recs, prov, _stats = merged_records()   # P3-G1: primary + fallback with provenance
    else: recs = parse_decay_file(path); prov = {}
    keys = sorted(recs, key=lambda m: (recs[m]["za"], recs[m]["liso"]))  # order by ZA (≈ Z then A), then isomer
    idx = {(int(round(recs[m]["za"])), recs[m]["liso"]): k for k, m in enumerate(keys)}
    n = len(keys); LEAK = n  # leakage row index
    entries = {}  # (row, col) -> value
    leak = {"sf": 0, "unknown_mode": 0, "daughter_missing": 0, "examples_missing": []}
    lam = []
    for k, m in enumerate(keys):
        r = recs[m]; hl = r["half_life"]
        l = 0.0 if (r["nst"] == 1 or hl <= 0) else LN2 / hl
        lam.append(l)
        if l == 0.0: continue
        entries[(k, k)] = entries.get((k, k), 0.0) - l
        Z, Aa = divmod(int(round(r["za"])), 1000)
        for md in r["modes"]:
            br = md["br"]
            if br <= 0: continue
            digs = rtyp_digits(md["rtyp"]); z, a = Z, Aa; bad = None
            for d in digs:
                if d == 6: bad = "sf"; break
                if d == 0 or d == 10 or d not in STEP: bad = "unknown_mode"; break
                dz, da = STEP[d]; z += dz; a += da
            if bad: leak[bad] += 1; entries[(LEAK, k)] = entries.get((LEAK, k), 0.0) + l * br; continue
            key = (z * 1000 + a, int(round(md["rfs"])))
            j = idx.get(key)
            if j is None:
                # tolerate RFS pointing at an isomer level the library lacks: fall back to ground state, else leak
                j = idx.get((z * 1000 + a, 0))
                if j is None:
                    leak["daughter_missing"] += 1
                    if len(leak["examples_missing"]) < 10: leak["examples_missing"].append([m, r["za"], md["rtyp"], key])
                    entries[(LEAK, k)] = entries.get((LEAK, k), 0.0) + l * br; continue
            entries[(j, k)] = entries.get((j, k), 0.0) + l * br
    return keys, recs, idx, lam, entries, leak
if __name__ == "__main__":
    keys, recs, idx, lam, entries, leak = build()
    n = len(keys); nnz = len(entries); offdiag = sum(1 for (i, j) in entries if i != j and i < n)
    upper = sum(1 for (i, j) in entries if i < j and i < n)
    stats = {"n_nuclides": n, "n_radioactive": sum(1 for l in lam if l > 0), "nnz": nnz, "offdiag": offdiag, "upper_triangular_entries_in_ZA_order": upper,
             "leakage_columns": sum(1 for (i, j) in entries if i == n), "leak": leak,
             "example_Fe55_index": idx.get((26055, 0)), "example_Mn56_index": idx.get((25056, 0)), "example_Ag108m_index": idx.get((47108, 1))}
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"), exist_ok=True)
    json.dump(stats, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "chain_stats.json"), "w"), indent=1)
    print(json.dumps(stats, indent=1))
