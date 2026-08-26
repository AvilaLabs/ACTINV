#!/usr/bin/env python3
"""FNS harness over the P5 path: build an actinv-spec-1 document per experiment and solve it with the Rust core
(via the Python binding). This replaces the Python problem assembly in controls/run_fns.py — the harness is now a
third entry point to the same binary, not a second implementation. Writes results/<dir>/<material>_<tag>.json."""
import os, sys, json, glob, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
from harness import fispact_io as fio
import actinv
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
FNS = os.path.expanduser("~/nuclear-data/conderc-fns/fns")
OUT = os.path.join(RES, os.environ.get("ACTINV_FNS_DIR", "fns_spec")); os.makedirs(OUT, exist_ok=True)
LIB = os.environ.get("ACTINV_LIBRARY", os.path.expanduser("~/nuclear-data/tendl-2023/actinv_tendl2023_fns_709g.npz"))
PRIM = os.path.expanduser("~/nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat")
FB = os.path.expanduser("~/nuclear-data/jeff-3.3-decay/bulk/jeff-3-3_decay.dat")
def build_spec(mat, tag):
    d = os.path.join(FNS, mat)
    i = fio.read_i(os.path.join(d, f"TENDL-2017_{tag}.i"))
    vals = []
    for line in open(os.path.join(d, f"{tag}_fluxes")):
        try: vals += [float(x) for x in line.split()]
        except ValueError: break
    cool = i["cooling_cum_s"]
    sched = [{"dt": f"{i['t_irr_s']} s", "flux": 1.0}, {"dt": f"{cool[0]} s", "flux": 0.0}]
    sched += [{"dt": f"{cool[k] - cool[k-1]} s", "flux": 0.0} for k in range(1, len(cool))]
    return {"spec": "actinv-spec-1", "title": f"FNS {mat} {tag}",
            "library": {"path": LIB}, "decay": {"primary": PRIM, "fallback": FB},
            "material": {"mass_g": i["mass_kg"] * 1000.0, "basis": "wt_percent", "composition": i["elements"]},
            "spectrum": {"structure": "fispact-709", "flux_per_group": vals[:709], "total": i["flux_total"], "descending": True},
            "schedule": sched,
            "options": {"mode": os.environ.get("ACTINV_MODE", "auto"), "prune": os.environ.get("ACTINV_PRUNE_MODE", "rate"),
                        "bmin_atoms_per_g": float(os.environ.get("ACTINV_BMIN", "1e-8")), "temperature_K": 293.6}}
def main():
    exps = [(m, os.path.basename(f)[:-4]) for m in sorted(os.listdir(FNS)) for f in sorted(glob.glob(os.path.join(FNS, m, "*.exp")))]
    t0 = time.time(); nerr = 0
    for k, (mat, tag) in enumerate(exps):
        rec = {"material": mat, "experiment": tag, "library": os.path.basename(LIB)}
        try:
            spec = build_spec(mat, tag); rec["spec"] = spec
            r = json.loads(actinv.run(json.dumps(spec)))
            rec.update({"mode": r["mode"], "pruned_states": r["pruned_states"], "total_states": r["total_states"], "ms": r["ms"],
                        "ledger": r["ledger"], "certificate": r["certificate"],
                        "heat_uW_g": [s["heat_W_per_g"]["total"] * 1e6 for s in r["steps"][1:]],
                        "heat_split_uW_g": [{k2: v * 1e6 for k2, v in s["heat_W_per_g"].items()} for s in r["steps"][1:]],
                        "floor_heat_fraction": [s["heat_bound_from_below_floor_W_per_g"] / s["heat_W_per_g"]["total"] if s["heat_W_per_g"]["total"] > 0 else 0.0 for s in r["steps"][1:]],
                        "steps": [{"t_s": s["t_s"], "n_inventory": len(s["inventory"]), "total_atoms_per_g": s["total_atoms_per_g"],
                                   "numerical_floor_atoms_per_g": s["numerical_floor_atoms_per_g"], "n_states_below_floor": s["n_states_below_floor"],
                                   "heat_bound_from_below_floor_W_per_g": s["heat_bound_from_below_floor_W_per_g"],
                                   "leakage_atoms_per_g": s["leakage_atoms_per_g"], "negative_atoms_zeroed": s["negative_atoms_zeroed"],
                                   "inventory": s["inventory"], "activity_Bq_per_g": s["activity_Bq_per_g"]} for s in r["steps"]]})
        except Exception as e:
            import traceback; rec["error"] = traceback.format_exc()[-500:]; nerr += 1
        json.dump(rec, open(os.path.join(OUT, f"{mat}_{tag}.json"), "w"))
        if k % 20 == 0 or rec.get("error"):
            print(f"{k+1:3d}/{len(exps)} {mat:8s} {tag:16s} " + (f"ERR {rec['error'][-90:]}" if rec.get("error") else
                  f"{rec['mode']:7s} {rec['pruned_states']:5d} states {rec['ms']:7.1f} ms  first-cool {rec['heat_uW_g'][0]:.4e} uW/g"), file=sys.stderr, flush=True)
    print(f"{len(exps)} experiments, {nerr} errors, {time.time()-t0:.0f} s -> {OUT}", file=sys.stderr)
if __name__ == "__main__": main()
