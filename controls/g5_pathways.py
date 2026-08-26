#!/usr/bin/env python3
"""P5-G5: pathway analysis. (a) contributions sum to each nuclide's population to 1e-12 (measured on the complete
decomposition, before any reporting threshold); (b) planted control — removing one reaction from the library removes
exactly that chain and no other, and the nuclide's population falls by that chain's contribution."""
import os, sys, json, hashlib, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
import actinv
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
spec = json.load(open(os.path.join(ROOT, "examples", "fns_fe_5min.json")))
base = json.loads(actinv.run(json.dumps(spec)))
STEP = 1
paths = base["pathways"][STEP]
# ---- (b) planted removal: drop Fe-56 (n,p) -> Mn-56 from a copy of the library and check the effect is exactly local
import numpy as np
libp = spec["library"]["path"]; L = np.load(libp); idx = json.load(open(libp.replace(".npz", "_index.json")))
tk = next(k for k, t in enumerate(idx["targets"]) if t["za"] == 26056 and t["liso"] == 0)
rows = L["rows"]; sig = L["sig"].copy()
hit = [i for i, r in enumerate(rows) if r[0] == tk and r[1] == 103 and r[2] == 25056]
assert hit, "Fe-56 (n,p) -> Mn-56 row not found"
sig[hit[0]] = 0.0
tmp = os.path.join(RES, "_g5_planted.npz")
np.savez_compressed(tmp, rows=rows, sig=sig, bounds=L["bounds"])
planted_index = json.load(open(libp.replace(".npz", "_index.json")))
planted_index["sha256_npz"] = hashlib.sha256(open(tmp, "rb").read()).hexdigest()
json.dump(planted_index, open(tmp.replace(".npz", "_index.json"), "w"))
sp2 = json.loads(json.dumps(spec)); sp2["library"]["path"] = tmp
planted = json.loads(actinv.run(json.dumps(sp2)))
os.remove(tmp); os.remove(tmp.replace(".npz", "_index.json"))
def chains(res, step, nuc):
    return {(p["from"], p["first_product"]): p["atoms_per_g"] for p in res["pathways"][step].get(nuc, [])}
cb, cp = chains(base, STEP, "Mn56"), chains(planted, STEP, "Mn56")
removed = sorted(set(cb) - set(cp)); kept = sorted(set(cb) & set(cp))
target = ("Fe56", "Mn56")
others_changed = [k for k in kept if abs(cp[k] - cb[k]) / max(cb[k], 1e-300) > 1e-12]
def inv(res, step, nuc):
    return next((n["atoms_per_g"] for n in res["steps"][step]["inventory"] if n["nuclide"] == nuc), 0.0)
drop = inv(base, STEP, "Mn56") - inv(planted, STEP, "Mn56")
expect = cb.get(target, 0.0)
res = {"closure": base["pathway_closure"], "closure_pass": base["pathway_closure"] <= 1e-12,
       "n_steps_with_pathways": len(base["pathways"]), "n_nuclides_step1": len(paths),
       "example_Mn56": [{"from": p["from"], "first_product": p["first_product"], "fraction": p["fraction"]} for p in paths.get("Mn56", [])[:4]],
       "planted": {"removed_chains": [list(k) for k in removed], "expected_removed": list(target),
                   "other_chains_changed": [list(k) for k in others_changed],
                   "population_drop": drop, "chain_contribution": expect,
                   "drop_matches_contribution_rel": abs(drop - expect) / max(expect, 1e-300),
                   "pass": bool(removed == [target] and not others_changed and abs(drop - expect) / max(expect, 1e-300) <= 1e-12)}}
res["pass"] = bool(res["closure_pass"] and res["planted"]["pass"])
json.dump(res, open(os.path.join(RES, "g5_pathways.json"), "w"), indent=1); print(json.dumps(res, indent=1))
