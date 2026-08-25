#!/usr/bin/env python3
"""P2-G2 controls: write the P1 Fe-56 problem in the actinv-solve format, run pruned and unpruned, compare with each
other, with the P1 Rust step result (bit-for-bit) and with the Python reference (0.0). Writes results/g2_cli.json."""
import os, sys, json, subprocess, time, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cram_ref
from chain import build
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results"); BIN = os.path.join(ROOT, "target", "release", "actinv-solve")
YEAR, DAY, PHI = cram_ref.YEAR, cram_ref.DAY, cram_ref.PHI
cc = json.load(open(os.path.expanduser("~/Documents/Avila-Labs/scouting/act-p0/results/cram_coefficients.json")))["Cram16Solver"]
keys, recs, idx, lam, entries, leak = build(); n = len(keys) + 1; fe = idx[(26056, 0)]
g1 = json.load(open(os.path.join(RES, "g1_collapse.json")))
sig = {t["mt"]: t["sigma_own_b"] for t in g1["tests"] if t["nuclide"] == "Fe56" and "isomer_lfs" not in t and "sigma_own_b" in t}
react = {}
for mt, s in sig.items():
    dz, da = cram_ref.MT_STEP[mt]; r = s * 1e-24; j = idx.get((26056 + dz * 1000 + da, 0), n - 1)
    react[(j, fe)] = react.get((j, fe), 0.0) + r; react[(fe, fe)] = react.get((fe, fe), 0.0) - r
def write_problem(path, prune, sched):
    with open(path, "w") as f:
        f.write("ACTINV-PROBLEM 1\nn %d\n" % n); f.write("decay %d\n" % len(entries))
        for (i, j), v in entries.items(): f.write("%d %d %.17e\n" % (i, j, v))
        f.write("reaction %d\n" % len(react))
        for (i, j), v in react.items(): f.write("%d %d %.17e\n" % (i, j, v))
        f.write("n0 1\n%d 1.0\n" % fe); f.write("cram %.17e %d\n" % (cc["alpha0"], len(cc["theta_re"])))
        for a, b, c, d in zip(cc["theta_re"], cc["theta_im"], cc["alpha_re"], cc["alpha_im"]): f.write("%.17e %.17e %.17e %.17e\n" % (a, b, c, d))
        f.write("schedule %d\n" % len(sched))
        for dt, phi in sched: f.write("%.17e %.17e\n" % (dt, phi))
        f.write("prune %d\n" % prune)
def read_result(path):
    lines = open(path).read().splitlines(); hdr = dict(zip(lines[1].split()[0::2], lines[1].split()[1::2])); steps = []; i = 2
    while i < len(lines):
        p = lines[i].split(); k = int(p[5]); vec = {}
        for l in lines[i + 1:i + 1 + k]: a, b = l.split(); vec[int(a)] = float(b)
        steps.append({"t": float(p[3]), "vec": vec}); i += 1 + k
    return hdr, steps
sched = [(YEAR, PHI), (DAY, 0.0), (YEAR - DAY, 0.0), (99 * YEAR, 0.0)]
out = {"controls": {}, "timing": {}}
res = {}
for prune in (0, 1):
    pf = os.path.join(RES, f"g2_problem_prune{prune}.txt"); rf = os.path.join(RES, f"g2_result_prune{prune}.txt"); write_problem(pf, prune, sched)
    t0 = time.time(); subprocess.run([BIN, pf, rf], check=True, capture_output=True); wall = time.time() - t0
    hdr, steps = read_result(rf); res[prune] = steps; out["timing"][f"prune{prune}"] = {"ms_total": float(hdr["ms_total"]), "ms_per_step_max": float(hdr["ms_per_step_max"]), "pruned_size": int(hdr["pruned"]), "wall_s_incl_io": wall}
def dense(vec): v = np.zeros(n); [v.__setitem__(i, x) for i, x in vec.items()]; return v
worst_abs = 0.0; worst_rel_big = 0.0   # P2 Amendment A criteria
for s in range(len(sched)):
    a, b = dense(res[1][s]["vec"]), dense(res[0][s]["vec"]); tot = b.sum(); worst_abs = max(worst_abs, float(np.max(np.abs(a - b)) / tot))
    big = b > 1e-3 * tot; worst_rel_big = max(worst_rel_big, float(np.max(np.abs(a[big] - b[big]) / b[big])))
out["controls"]["pruned_vs_unpruned"] = {"pass": worst_abs <= 1e-12 and worst_rel_big <= 1e-12, "max_abs_over_total": worst_abs, "max_rel_components_gt_1e-3": worst_rel_big, "pruned_size": out["timing"]["prune1"]["pruned_size"], "criterion": "P2 Amendment A"}
rust_p1 = np.array([float(x) for x in open(os.path.join(RES, "g2_rust_irr.txt")).read().splitlines()[1:]]); py = np.load(os.path.join(RES, "g2_python_irr.npy"))
u = dense(res[0][0]["vec"]); m = py > 1e-15 * py.sum()
out["controls"]["unpruned_vs_p1_rust"] = {"pass": float(np.max(np.abs(u - rust_p1)) / rust_p1.sum()) <= 1e-12, "max_abs_over_total": float(np.max(np.abs(u - rust_p1)) / rust_p1.sum()), "criterion": "P2 Amendment A"}
out["controls"]["unpruned_vs_python"] = {"pass": float(np.max(np.abs(u[m] - py[m]) / py[m])) == 0.0, "max_rel": float(np.max(np.abs(u[m] - py[m]) / py[m]))}
json.dump(out, open(os.path.join(RES, "g2_cli.json"), "w"), indent=1); print(json.dumps(out, indent=1))
