#!/usr/bin/env python3
"""P3-G1 control (a): own parser vs openmc.data.Decay on the JEFF-3.3 decay file, 200 seeded nuclides (seed 20260826);
merge statistics; top-20 half-life disagreements between sources for nuclides in both."""
import os, sys, json, random, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from endf_decay import parse_decay_file
from decay_sources import merged_records, PRIMARY, FALLBACK
import openmc.data; from openmc.data.endf import get_evaluations
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
fb = parse_decay_file(FALLBACK); evs = {ev.material: ev for ev in get_evaluations(FALLBACK)}
rng = random.Random(20260826); sample = rng.sample(sorted(fb), 200); tol = 1e-12; mism = []
def nom(x): return float(getattr(x, "n", x))
def rel(a, b): return abs(a - b) / max(abs(a), abs(b), 1e-300)
for mat in sample:
    r = fb[mat]; d = openmc.data.Decay(evs[mat]); hl = nom(d.half_life) if d.half_life is not None else 0.0
    if rel(hl, r["half_life"]) > tol: mism.append((mat, "half_life", hl, r["half_life"]))
    bo = sorted(nom(m.branching_ratio) for m in d.modes); bp = sorted(m["br"] for m in r["modes"])
    if len(bo) != len(bp) or any(rel(a, b) > tol for a, b in zip(bo, bp)): mism.append((mat, "branching", bo, bp))
    if r["nst"] == 0:
        eo = sorted(nom(v) for v in d.average_energies.values()); ep = sorted(r["energies"][0::2])
        if len(eo) != len(ep) or any(rel(a, b) > tol for a, b in zip(eo, ep)): mism.append((mat, "energies", eo, ep))
recs, prov, stats = merged_records(); prim = parse_decay_file(PRIMARY)
P = {(int(round(r["za"])), r["liso"]): r for r in prim.values()}; F = {(int(round(r["za"])), r["liso"]): r for r in fb.values()}
both = [k for k in P if k in F and P[k]["nst"] == 0 and F[k]["nst"] == 0 and P[k]["half_life"] > 0 and F[k]["half_life"] > 0]
diffs = sorted(((abs(math.log(P[k]["half_life"] / F[k]["half_life"])), k, P[k]["half_life"], F[k]["half_life"]) for k in both), reverse=True)[:20]
out = {"fallback_file": FALLBACK, "n_fallback": len(fb), "n_checked": 200, "n_mismatch": len(mism), "mismatches": [list(map(str, m)) for m in mism[:20]], "pass": len(mism) == 0,
       "merge": {k: v for k, v in stats.items() if k != "added"}, "added_from_fallback_examples": stats["added"][:30], "n_in_both_radioactive": len(both),
       "top20_half_life_disagreements": [{"za": k[0], "liso": k[1], "T12_endfb80_s": a, "T12_jeff33_s": b, "abs_ln_ratio": d} for d, k, a, b in diffs]}
json.dump(out, open(os.path.join(RES, "g1_decay_fallback.json"), "w"), indent=1); print(json.dumps({k: v for k, v in out.items() if k not in ("top20_half_life_disagreements", "added_from_fallback_examples")}, indent=1)); print("top disagreements:", out["top20_half_life_disagreements"][:4])
