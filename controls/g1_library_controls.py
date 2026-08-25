#!/usr/bin/env python3
"""P2-G1 controls on a seeded 40-file sample: (a) 709-group-library one-group sigma on the FNS Fe spectrum == pointwise
collapse (P1 method) to 1e-12; (b) pointwise sigma vs openmc low-level TAB1 reader <= 1e-6; (c) MF=8 header mismatches
from the build ledger == 0 and parse failures == 0. Writes results/g1_library.json."""
import os, sys, json, glob, random, io, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
import g1_collapse as g1   # provides interp_eval, collapse (FNS Fe spectrum), union_grid, bounds, flux_asc
from eaflib_build import parse_file, group_avg
import openmc.data
from openmc.data.endf import get_head_record, get_tab1_record
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"); LIB = os.path.expanduser("~/nuclear-data/eaf-2010")
L = np.load(os.path.join(LIB, "actinv_eaf2010_709g.npz")); ROWS, SIG = L["rows"], L["sig"]; IDXJ = json.load(open(os.path.join(LIB, "actinv_eaf2010_709g_index.json")))
tfile = {t["file"]: k for k, t in enumerate(IDXJ["targets"])}
phi = g1.flux_asc; wsum = phi.sum()
files = sorted(glob.glob(os.path.join(LIB, "files", "*.dat"))); rng = random.Random(20260825); sample = rng.sample(files, 40)
worst_a = 0.0; worst_b = 0.0; n_a = n_b = 0; details = []
for path in sample:
    za, liso, mf3, mf8, mf9, mf10 = parse_file(path); tk = tfile[os.path.basename(path)]
    lib_rows = {(int(mt), int(zap), int(lfs)): SIG[r] for r, (t, mt, zap, lfs, lmf) in enumerate(ROWS) if t == tk}
    ev = openmc.data.endf.Evaluation(path)
    for mt, (nbt, x, y) in mf3.items():
        grid = g1.union_grid(x); s = g1.interp_eval(x, y, nbt, grid); one_pt = g1.collapse(s, grid)
        one_lib = float(lib_rows[(mt, -1, -1)] @ phi / wsum)
        if one_pt != 0: worst_a = max(worst_a, abs(one_lib - one_pt) / abs(one_pt)); n_a += 1
        fh = io.StringIO(ev.section[(3, mt)]); get_head_record(fh); params, tab = get_tab1_record(fh); s_omc = tab(grid)
        if np.any(s_omc != 0): worst_b = max(worst_b, float(np.max(np.abs(s - s_omc) / np.maximum(np.abs(s_omc), 1e-300)))); n_b += 1
    details.append({"file": os.path.basename(path), "za": za, "liso": liso, "n_mf3": len(mf3), "n_mf10": len(mf10)})
led = IDXJ["ledger"]
out = {"sample_seed": 20260825, "n_files": len(sample), "control_a": {"n_reactions": n_a, "max_rel": float(worst_a), "pass": bool(worst_a <= 1e-12)}, "control_b": {"n_reactions": n_b, "max_rel": float(worst_b), "pass": bool(worst_b <= 1e-6)},
       "control_c": {"parse_failures": len(led["parse_failures"]), "mf8_header_mismatches": len(led["mf8_header_mismatch"]), "pass": len(led["parse_failures"]) == 0 and len(led["mf8_header_mismatch"]) == 0},
       "library": {"targets": len(IDXJ["targets"]), "rows": IDXJ["n_rows"], "sha256_npz": IDXJ["sha256_npz"], "build_seconds": IDXJ["build_seconds"]}, "sample": details}
out["control_a"]["pass"] = bool(out["control_a"]["pass"]); out["control_b"]["pass"] = bool(out["control_b"]["pass"]); out["control_c"]["pass"] = bool(out["control_c"]["pass"]); out["pass"] = out["control_a"]["pass"] and out["control_b"]["pass"] and out["control_c"]["pass"]; out["note"] = f"a: {n_a} reactions max {worst_a:.1e}; b: {n_b} max {worst_b:.1e}; c: {len(led['parse_failures'])} failures / {len(led['mf8_header_mismatch'])} mismatches"
json.dump(out, open(os.path.join(RES, "g1_library.json"), "w"), indent=1); print(json.dumps({k: v for k, v in out.items() if k != "sample"}, indent=1))
