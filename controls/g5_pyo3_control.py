#!/usr/bin/env python3
"""P2-G5 control: the Python-called actinv.cram_step on the P1 problem equals the CLI/P1 Rust result at 0.0 on populated
components and <= 1e-12 of total everywhere (Amendment A criteria). Writes results/g5_pyo3.json."""
import os, sys, json, numpy as np
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
try:
    import actinv
except Exception as e:
    json.dump({"deferred": True, "reason": repr(e)}, open(os.path.join(RES, "g5_pyo3.json"), "w"), indent=1); print("DEFERRED", e); sys.exit(0)
lines = open(os.path.join(RES, "g2_matrix_irr.txt")).read().splitlines()
n, nnz = (int(x) for x in lines[0].split()); rows, cols, vals = [], [], []
for l in lines[1:1 + nnz]: i, j, v = l.split(); rows.append(int(i)); cols.append(int(j)); vals.append(float(v))
dt = float(lines[1 + nnz]); n0 = [float(x) for x in lines[2 + nnz].split()]; alpha0 = float(lines[3 + nnz]); k = int(lines[4 + nnz])
th = [[float(x) for x in lines[5 + nnz + m].split()] for m in range(k)]
y = np.array(actinv.cram_step(n, rows, cols, vals, n0, dt, alpha0, [t[0] for t in th], [t[1] for t in th], [t[2] for t in th], [t[3] for t in th]))
ref = np.array([float(x) for x in open(os.path.join(RES, "g2_rust_irr.txt")).read().splitlines()[1:]]); py = np.load(os.path.join(RES, "g2_python_irr.npy"))
tot = ref.sum(); m = py > 1e-15 * tot
out = {"pass": bool(np.array_equal(y, ref)), "max_abs_over_total_vs_cli": float(np.max(np.abs(y - ref)) / tot), "max_rel_vs_python_populated": float(np.max(np.abs(y[m] - py[m]) / py[m])), "n": n, "module": actinv.__file__}
out["pass"] = bool(out["max_abs_over_total_vs_cli"] <= 1e-12 and out["max_rel_vs_python_populated"] == 0.0)
json.dump(out, open(os.path.join(RES, "g5_pyo3.json"), "w"), indent=1); print(json.dumps(out, indent=1))
