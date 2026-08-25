#!/usr/bin/env python3
"""P1-G2 control (3): Rust vs Python on the same matrix/step; merges timing into results/g2_solver.json."""
import os, json, numpy as np
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
py = np.load(os.path.join(RES, "g2_python_irr.npy"))
lines = open(os.path.join(RES, "g2_rust_irr.txt")).read().splitlines(); hdr = lines[0]; rs = np.array([float(x) for x in lines[1:]])
meta = dict(kv.split("=") for kv in hdr.lstrip("# ").split())
tot = py.sum(); m = py > 1e-15 * tot
rel = float(np.max(np.abs(rs[m] - py[m]) / np.abs(py[m]))); absmax = float(np.max(np.abs(rs - py)))
g2 = json.load(open(os.path.join(RES, "g2_python.json")))
g2["controls"]["rust_vs_python"] = {"pass": rel <= 1e-12, "max_rel": rel, "max_abs": absmax, "n_compared": int(m.sum())}
g2["timing"].update({"rust_ms_per_cram16_step": float(meta["ms_per_step"]), "rust_reps": int(meta["reps"]), "matrix_n": int(meta["n"]), "matrix_nnz": int(meta["nnz"]), "max_LU_nnz": int(meta["max_LU_nnz"])})
json.dump(g2, open(os.path.join(RES, "g2_solver.json"), "w"), indent=1)
print("control 3 rust vs python: max rel %.3e (abs %.3e) over %d nuclides; rust %.3f ms/step; LU nnz %s" % (rel, absmax, m.sum(), float(meta["ms_per_step"]), meta["max_LU_nnz"]))
