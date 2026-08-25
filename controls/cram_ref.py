#!/usr/bin/env python3
"""ACTINV P1-G2 Python reference: own sparse complex LU (same Gilbert–Peierls algorithm as the Rust crate),
CRAM-16 in OpenMC's IPF recurrence, Fe-56 irradiation schedule, controls (1) analytic 3-chain, (2) dense expm,
(4) conservation with leakage; exports the matrix for the Rust probe (control 3 runs in g2_compare.py)."""
import os, sys, json, math, time, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chain import build
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
YEAR = 365.25 * 86400.0; DAY = 86400.0; PHI = 1e14
MT_STEP = {102: (0, 1), 103: (-1, 0), 107: (-2, -3), 16: (0, -1), 105: (-1, -2), 104: (-1, -1), 28: (-1, -1), 22: (-2, -4), 32: (-1, -2), 111: (-2, -1)}
# ---------------- own sparse LU (CSC; Gilbert-Peierls with partial pivoting)
class CSC:
    def __init__(self, n, trip):
        cols = [dict() for _ in range(n)]
        for i, j, v in trip: cols[j][i] = cols[j].get(i, 0) + v
        self.n = n; self.colptr = [0]; self.rowidx = []; self.vals = []
        for c in cols:
            for i in sorted(c): self.rowidx.append(i); self.vals.append(c[i])
            self.colptr.append(len(self.rowidx))
def lu(a):
    n = a.n; lp = [0] * (n + 1); li = []; lx = []; up = [0] * (n + 1); ui = []; ux = []; pinv = [-1] * n
    x = [0j] * n; xi = [0] * n; mark = [0] * n; stamp = 0
    for k in range(n):
        lp[k] = len(li); up[k] = len(ui); stamp += 1; top = n
        for r in a.rowidx[a.colptr[k]:a.colptr[k + 1]]:  # reach (iterative DFS)
            if mark[r] == stamp: continue
            stack = [r]; pstack = [lp[pinv[r]] if pinv[r] >= 0 else 0]; mark[r] = stamp
            while stack:
                j = stack[-1]; jj = pinv[j]; done = True
                if jj >= 0:
                    p = pstack[-1]; p2 = lp[jj + 1]
                    while p < p2:
                        i = li[p]; p += 1
                        if mark[i] != stamp: mark[i] = stamp; pstack[-1] = p; stack.append(i); pstack.append(lp[pinv[i]] if pinv[i] >= 0 else 0); done = False; break
                if done: stack.pop(); pstack.pop(); top -= 1; xi[top] = j
        for p in range(top, n): x[xi[p]] = 0j
        for p in range(a.colptr[k], a.colptr[k + 1]): x[a.rowidx[p]] = a.vals[p]
        for px in range(top, n):
            j = xi[px]; jj = pinv[j]
            if jj < 0: continue
            xj = x[j]
            for p in range(lp[jj] + 1, lp[jj + 1]): x[li[p]] -= lx[p] * xj
        ipiv = -1; amax = -1.0
        for p in range(top, n):
            i = xi[p]
            if pinv[i] < 0:
                t = abs(x[i])
                if t > amax: amax = t; ipiv = i
            else: ui.append(pinv[i]); ux.append(x[i])
        if ipiv < 0 or amax <= 0: raise ZeroDivisionError("singular at %d" % k)
        if pinv[k] < 0 and abs(x[k]) >= amax: ipiv = k
        piv = x[ipiv]; ui.append(k); ux.append(piv); pinv[ipiv] = k; li.append(ipiv); lx.append(1.0 + 0j)
        for p in range(top, n):
            i = xi[p]
            if pinv[i] < 0: li.append(i); lx.append(x[i] / piv)
            x[i] = 0j
    lp[n] = len(li); up[n] = len(ui); li = [pinv[v] for v in li]
    return (n, lp, li, lx, up, ui, ux, pinv)
def solve(f, b):
    n, lp, li, lx, up, ui, ux, pinv = f; x = [0j] * n
    for i in range(n): x[pinv[i]] = b[i]
    for j in range(n):
        xj = x[j] / lx[lp[j]]; x[j] = xj
        for p in range(lp[j] + 1, lp[j + 1]): x[li[p]] -= lx[p] * xj
    for j in range(n - 1, -1, -1):
        xj = x[j] / ux[up[j + 1] - 1]; x[j] = xj
        for p in range(up[j], up[j + 1] - 1): x[ui[p]] -= ux[p] * xj
    return x
def cram_step(n, trip, n0, dt, C):
    y = list(map(float, n0))
    for th, al in zip(C["theta"], C["alpha"]):
        m = CSC(n, [(i, j, dt * v) for i, j, v in trip] + [(j, j, -th) for j in range(n)])
        z = solve(lu(m), [complex(v) for v in y])
        for i in range(n): y[i] += 2.0 * (al * z[i]).real
    return [v * C["alpha0"] for v in y]
def main():
    # ---------------- coefficients (recorded in ACT-P0 from openmc.deplete.cram; Pusa 2016)
    cc = json.load(open(os.path.expanduser("~/Documents/Avila-Labs/scouting/act-p0/results/cram_coefficients.json")))["Cram16Solver"]
    C = {"alpha0": cc["alpha0"], "theta": [complex(a, b) for a, b in zip(cc["theta_re"], cc["theta_im"])], "alpha": [complex(a, b) for a, b in zip(cc["alpha_re"], cc["alpha_im"])]}
    res = {"controls": {}, "timing": {}}
    # ---------------- control (1): analytic 3-chain A->B->C
    l1, l2 = math.log(2) / 100.0, math.log(2) / 1000.0; t = 500.0
    trip3 = [(0, 0, -l1), (1, 0, l1), (1, 1, -l2), (2, 1, l2)]
    y3 = cram_step(3, trip3, [1.0, 0.0, 0.0], t, C)
    NA = math.exp(-l1 * t); NB = l1 / (l2 - l1) * (math.exp(-l1 * t) - math.exp(-l2 * t)); NC = 1 - NA - NB
    err3 = max(abs(y3[0] - NA) / NA, abs(y3[1] - NB) / NB, abs(y3[2] - NC) / NC)
    res["controls"]["analytic_3chain"] = {"pass": err3 <= 1e-10, "max_rel": err3, "cram": y3, "exact": [NA, NB, NC]}
    print("control 1 analytic 3-chain: max rel", err3)
    # ---------------- full matrix: decay network + leakage row + Fe-56 reactions from G1
    keys, recs, idx, lam, entries, leak = build(); n = len(keys) + 1  # +1 leakage state
    g1 = json.load(open(os.path.join(RES, "g1_collapse.json")))
    sig = {t["mt"]: t["sigma_own_b"] for t in g1["tests"] if t["nuclide"] == "Fe56" and "isomer_lfs" not in t and "sigma_own_b" in t}
    fe = idx[(26056, 0)]; rx_entries = {}; rx_ledger = []
    for mt, s in sig.items():
        dz, da = MT_STEP[mt]; rate = s * 1e-24 * PHI; j = idx.get((26056 + dz * 1000 + da, 0))
        if j is None: rx_ledger.append({"mt": mt, "product_za": 26056 + dz * 1000 + da, "rate_per_s": rate, "disposition": "product not in library -> leakage"}); j = n - 1
        rx_entries[(j, fe)] = rx_entries.get((j, fe), 0.0) + rate; rx_entries[(fe, fe)] = rx_entries.get((fe, fe), 0.0) - rate
    trip_decay = [(i, j, v) for (i, j), v in entries.items()]
    trip_irr = trip_decay + [(i, j, v) for (i, j), v in rx_entries.items()]
    n0 = [0.0] * n; n0[fe] = 1.0
    t0 = time.time(); y_irr = cram_step(n, trip_irr, n0, YEAR, C); t_py = time.time() - t0
    print("python own-LU CRAM step (irradiation 1 y): %.1f s" % t_py); res["timing"]["python_s_per_step"] = t_py
    y_c1d = cram_step(n, trip_decay, y_irr, DAY, C); y_c1y = cram_step(n, trip_decay, y_c1d, YEAR - DAY, C); y_c100y = cram_step(n, trip_decay, y_c1y, 99 * YEAR, C)
    # ---------------- control (2): dense expm on the closed reachable sub-network (P1 Amendment A)
    import scipy.linalg as sla
    from collections import deque
    succ = {}
    for i, j, v in trip_irr:
        if i != j and v != 0: succ.setdefault(j, set()).add(i)
    reach = {fe}; dq = deque([fe])
    while dq:
        j = dq.popleft()
        for i in succ.get(j, ()):
            if i not in reach: reach.add(i); dq.append(i)
    sub = sorted(reach); pos = {g: k for k, g in enumerate(sub)}; ns = len(sub)
    def dense(trip):
        M = np.zeros((ns, ns))
        for i, j, v in trip:
            if i in pos and j in pos: M[pos[i], pos[j]] += v
        return M
    n0s = np.zeros(ns); n0s[pos[fe]] = 1.0
    t0 = time.time(); ref_irr = sla.expm(dense(trip_irr) * YEAR) @ n0s; t_expm = time.time() - t0
    ref_c1d = sla.expm(dense(trip_decay) * DAY) @ ref_irr
    def cmp(a, b):
        a = np.asarray([a[g] for g in sub]); b = np.asarray(b); tot = b.sum(); m = b > 1e-15 * tot
        return float(np.max(np.abs(a[m] - b[m]) / np.abs(b[m]))), int(m.sum())
    e_irr, k_irr = cmp(y_irr, ref_irr); e_c1d, k_c1d = cmp(y_c1d, ref_c1d)
    outside = float(sum(y_irr[i] for i in range(n) if i not in reach))
    res["controls"]["dense_expm"] = {"pass": max(e_irr, e_c1d) <= 1e-6 and outside <= 1e-15 * sum(y_irr), "domain": "closed reachable sub-network (Amendment A)", "sub_network_size": ns,
                                     "max_rel_irradiation_1y": e_irr, "n_compared": k_irr, "max_rel_cooling_1d": e_c1d, "n_compared_cooling": k_c1d, "expm_seconds": t_expm,
                                     "cram_mass_outside_subnetwork": outside, "sub_network_za": [names_za for names_za in ([ (int(round(recs[keys[g]]["za"])), recs[keys[g]]["liso"]) if g < n - 1 else "leakage" for g in sub ])]}
    print("control 2 dense expm on %d-nuclide reachable sub-network: irr max rel %.3e (%d), cooling max rel %.3e (%d); mass outside %.1e; expm %.2f s" % (ns, e_irr, k_irr, e_c1d, k_c1d, outside, t_expm))
    # ---------------- control (4): conservation incl. leakage (nuclide count)
    tot = [sum(v) for v in (n0, y_irr, y_c1d, y_c1y, y_c100y)]
    cons = max(abs(v - tot[0]) / tot[0] for v in tot[1:])
    res["controls"]["conservation"] = {"pass": cons <= 1e-12, "max_rel_dev": cons, "totals": tot, "leakage_atoms": [y_irr[-1], y_c1d[-1], y_c1y[-1], y_c100y[-1]]}
    print("control 4 conservation (incl. leakage): max rel dev %.3e; leakage atoms at 100 y: %.3e" % (cons, y_c100y[-1]))
    # ---------------- top nuclides + missing-data ledger v0 (P1-G3 data)
    names = {k: (int(round(recs[m]["za"])), recs[m]["liso"]) for k, m in enumerate(keys)}
    def top(y, k=8):
        order = np.argsort(-np.array(y[:-1]))[:k]; return [{"za": names[i][0], "liso": names[i][1], "atoms_frac": y[i] / sum(y), "activity_Bq_per_atom0": y[i] * lam[i]} for i in order]
    res["inventory"] = {"after_1y_irradiation": top(y_irr), "after_1d_cooling": top(y_c1d), "after_1y_cooling": top(y_c1y), "after_100y_cooling": top(y_c100y)}
    res["ledger_v0"] = {"reaction_products_not_in_library": rx_ledger, "decay_daughters_missing": leak["daughter_missing"], "sf_branches_to_leakage": leak["sf"], "examples": leak["examples_missing"],
                        "leakage_fraction_of_atoms": {"1y_irr": y_irr[-1] / sum(y_irr), "100y": y_c100y[-1] / sum(y_c100y)}}
    # ---------------- export for Rust (control 3)
    with open(os.path.join(RES, "g2_matrix_irr.txt"), "w") as f:
        f.write("%d %d\n" % (n, len(trip_irr)))
        for i, j, v in trip_irr: f.write("%d %d %.17e\n" % (i, j, v))
        f.write("%.17e\n" % YEAR); f.write(" ".join("%.17e" % v for v in n0) + "\n"); f.write("%.17e\n" % C["alpha0"]); f.write("%d\n" % len(C["theta"]))
        for th, al in zip(C["theta"], C["alpha"]): f.write("%.17e %.17e %.17e %.17e\n" % (th.real, th.imag, al.real, al.imag))
    np.save(os.path.join(RES, "g2_python_irr.npy"), np.array(y_irr))
    json.dump(res, open(os.path.join(RES, "g2_python.json"), "w"), indent=1)
    print("top after 1 y irradiation:", [(t["za"], t["liso"], "%.3e" % t["atoms_frac"]) for t in res["inventory"]["after_1y_irradiation"][:6]])
    print("Fe-56 sigma used (b):", {k: "%.4e" % v for k, v in sig.items()}); print("reaction ledger:", rx_ledger)

if __name__ == "__main__":
    main()
