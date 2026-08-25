#!/usr/bin/env python3
"""P4-G2 (b) and (c) on a seeded 40-target sample (seed 20260826): (c) grid convergence — capture group values at density D
vs 2D agree <= 1e-3 on groups with sigma >= 1e-4 b; (b) non-resonant MTs: library group values = pointwise collapse (P2
method) to 1e-12. Usage: g2_tendl_controls.py FILES_DIR OUT_DIR --dense D"""
import os, sys, json, glob, random, subprocess, shutil, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
files_dir, out_dir, dense = sys.argv[1], sys.argv[2], float(sys.argv[sys.argv.index("--dense") + 1])
files = sorted(glob.glob(os.path.join(files_dir, "*.dat"))); rng = random.Random(20260826); sample = rng.sample(files, 40)
sd = os.path.join(out_dir, "sample_files"); os.makedirs(sd, exist_ok=True)
for f in sample:
    dst = os.path.join(sd, os.path.basename(f))
    if not os.path.exists(dst): os.symlink(f, dst)
def build(d, name): subprocess.run([sys.executable, os.path.join(ROOT, "controls", "tendl_build.py"), sd, out_dir, "--workers", "3", "--dense", str(d), "--name", name], check=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
build(dense, "sampleD"); build(2 * dense, "sample2D")
A = np.load(os.path.join(out_dir, "sampleD.npz")); B = np.load(os.path.join(out_dir, "sample2D.npz")); ia = json.load(open(os.path.join(out_dir, "sampleD_index.json"))); ib = json.load(open(os.path.join(out_dir, "sample2D_index.json")))
ka = {tuple(r): i for i, r in enumerate(A["rows"].tolist())}; kb = {tuple(r): i for i, r in enumerate(B["rows"].tolist())}
worst_c = 0.0; n_c = 0; worst_rows = []
for key, i in ka.items():
    tk, mt, zap, lfs, lmf = key
    if mt not in (102, 18) or key not in kb: continue
    sa, sb = A["sig"][i], B["sig"][kb[key]]; m = sb >= 1e-4
    if m.any():
        rel = float(np.max(np.abs(sa[m] - sb[m]) / sb[m])); n_c += 1
        if rel > worst_c: worst_c = rel
        if rel > 1e-3: worst_rows.append({"target": ia["targets"][tk]["file"], "mt": mt, "zap": zap, "lfs": lfs, "max_rel": rel, "group": int(np.argmax(np.where(m, np.abs(sa - sb) / np.maximum(sb, 1e-300), 0)))})
# (b) non-resonant consistency vs pointwise collapse
import g1_collapse as g1
from tendl_build import parse_file, interp_tab1
worst_b = 0.0; n_b = 0
for t in ia["targets"][:40]:
    tk = ia["targets"].index(t); za, liso, awr, mf3, mf8, mf9, mf10 = parse_file(os.path.join(sd, t["file"])); resonant = set(t.get("resonant_mts", []))
    for mt, (nbt, x, y) in mf3.items():
        key = (tk, mt, -1, -1, 0)
        if key not in ka or mt in resonant: continue
        grid = g1.union_grid(x); s = g1.interp_eval(x, y, nbt, grid); one_pt = g1.collapse(s, grid); one_lib = float(A["sig"][ka[key]] @ g1.flux_asc / g1.flux_asc.sum())
        if one_pt != 0: worst_b = max(worst_b, abs(one_lib - one_pt) / abs(one_pt)); n_b += 1
out = {"dense": dense, "sample_seed": 20260826, "n_sample": len(sample), "control_c": {"n_capture_rows": n_c, "max_rel": worst_c, "pass": bool(worst_c <= 1e-3), "rows_over": worst_rows[:20]},
       "control_b": {"n_reactions": n_b, "max_rel": worst_b, "pass": bool(worst_b <= 1e-12)}, "build_seconds": [ia["build_seconds"], ib["build_seconds"]], "errors": [ia["n_errors"], ib["n_errors"]],
       "ledgers": {t["file"]: t["ledger"] for t in ia["targets"] if t["ledger"]}}
json.dump(out, open(os.path.join(RES, f"g2_tendl_dense{dense:g}.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)); print(json.dumps({k: v for k, v in out.items() if k != "ledgers"}, indent=1, default=str))
