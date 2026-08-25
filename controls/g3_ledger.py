#!/usr/bin/env python3
"""P1-G3: missing-data ledger v0 with a planted failure. A seeded product nuclide of the Fe-56 chain is deleted
from a copy of the decay library; the run must name it in the ledger and report the activity fraction that the
unmodified run attributes to it. Writes results/g3_ledger.json."""
import os, sys, json, math, random, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain, cram_ref
from endf_decay import parse_decay_file
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
YEAR, DAY, PHI = cram_ref.YEAR, cram_ref.DAY, cram_ref.PHI
cc = json.load(open(os.path.expanduser("~/Documents/Avila-Labs/scouting/act-p0/results/cram_coefficients.json")))["Cram16Solver"]
C = {"alpha0": cc["alpha0"], "theta": [complex(a, b) for a, b in zip(cc["theta_re"], cc["theta_im"])], "alpha": [complex(a, b) for a, b in zip(cc["alpha_re"], cc["alpha_im"])]}
g1 = json.load(open(os.path.join(RES, "g1_collapse.json")))
sig = {t["mt"]: t["sigma_own_b"] for t in g1["tests"] if t["nuclide"] == "Fe56" and "isomer_lfs" not in t and "sigma_own_b" in t}
products = sorted({(26056 + chain_dz * 1000 + chain_da) for mt, (chain_dz, chain_da) in cram_ref.MT_STEP.items() if mt in sig})
def run(recs_filter=None):
    """Build chain (optionally with a nuclide removed), irradiate 1 y + cool 1 d / 1 y; return states, ledger."""
    path = os.path.expanduser("~/nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat")
    recs = parse_decay_file(path)
    if recs_filter: recs = {m: r for m, r in recs.items() if not recs_filter(r)}
    # rebuild with the filtered records via chain.build internals
    import types
    orig = chain.parse_decay_file; chain.parse_decay_file = lambda p: recs
    try: keys, recs2, idx, lam, entries, leak = chain.build()
    finally: chain.parse_decay_file = orig
    n = len(keys) + 1; fe = idx[(26056, 0)]; rx_ledger = []; rx = {}
    for mt, s in sig.items():
        dz, da = cram_ref.MT_STEP[mt]; rate = s * 1e-24 * PHI; za = 26056 + dz * 1000 + da; j = idx.get((za, 0))
        if j is None: rx_ledger.append({"mt": mt, "product_za": za, "rate_per_s": rate, "disposition": "product has no decay record in library -> booked to leakage"}); j = n - 1
        rx[(j, fe)] = rx.get((j, fe), 0.0) + rate; rx[(fe, fe)] = rx.get((fe, fe), 0.0) - rate
    trip_d = [(i, j, v) for (i, j), v in entries.items()]; trip_i = trip_d + [(i, j, v) for (i, j), v in rx.items()]
    n0 = [0.0] * n; n0[fe] = 1.0
    y1 = cram_ref.cram_step(n, trip_i, n0, YEAR, C); y2 = cram_ref.cram_step(n, trip_d, y1, DAY, C); y3 = cram_ref.cram_step(n, trip_d, y2, YEAR - DAY, C)
    names = {k: (int(round(recs2[m]["za"])), recs2[m]["liso"]) for k, m in enumerate(keys)}
    return {"n": n, "idx": idx, "lam": lam, "names": names, "states": {"1y_irr": y1, "1d_cool": y2, "1y_cool": y3}, "rx_ledger": rx_ledger, "leak": {k: v for k, v in leak.items() if k != "examples_missing"}}
rng = random.Random(20260825); target = rng.choice(products)
base = run()
def activity_share(res, za):
    k = res["idx"].get((za, 0)); out = {}
    for t, y in res["states"].items():
        act = np.array(y[:-1]) * np.array(res["lam"]); out[t] = float(act[k] / act.sum()) if k is not None and act.sum() > 0 else None
    return out
share = activity_share(base, target)
planted = run(recs_filter=lambda r: int(round(r["za"])) == target and r["liso"] == 0)
named = [e for e in planted["rx_ledger"] if e["product_za"] == target]
leak_frac = {t: y[-1] / sum(y) for t, y in planted["states"].items()}
out = {"seed": 20260825, "candidate_products_za": products, "planted_deleted_za": target,
       "unmodified_activity_share_of_target": share, "planted_ledger_entries_naming_target": named,
       "planted_leakage_atom_fraction": leak_frac, "baseline_leakage_atom_fraction": {t: y[-1] / sum(y) for t, y in base["states"].items()},
       "pass": bool(named) and all(v is not None for v in share.values())}
rad = [za for za in products if base["idx"].get((za, 0)) is not None and base["lam"][base["idx"][(za, 0)]] > 0]
target2 = rng.choice(rad); share2 = activity_share(base, target2)
planted2 = run(recs_filter=lambda r: int(round(r["za"])) == target2 and r["liso"] == 0)
out["supplementary_radioactive"] = {"deleted_za": target2, "unmodified_activity_share": share2, "ledger_entries_naming_it": [e for e in planted2["rx_ledger"] if e["product_za"] == target2],
                                    "leakage_atom_fraction_after_deletion": {t: y[-1] / sum(y) for t, y in planted2["states"].items()}}
print("supplementary: deleted radioactive ZA", target2, "| activity share (unmodified):", {k: "%.3e" % v for k, v in share2.items()}, "| named:", bool(out["supplementary_radioactive"]["ledger_entries_naming_it"]))
json.dump(out, open(os.path.join(RES, "g3_ledger.json"), "w"), indent=1)
print("planted deletion of ZA", target, "-> ledger names it:", bool(named), "| activity share it carried (unmodified):", {k: "%.3e" % v for k, v in share.items()}, "| leakage atoms after deletion:", {k: "%.3e" % v for k, v in leak_frac.items()})
print("PASS" if out["pass"] else "FAIL")
