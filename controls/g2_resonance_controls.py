#!/usr/bin/env python3
"""P3-G2 controls: (a) 0 K reconstruction vs openmc.data on TENDL-2023 Fe-56/Ag-107/W-186 MT 2 and 102;
(b) Doppler invariants (1/v, constant); (c) single SLBW resonance broadened vs analytic psi/chi;
(d) 293.6 K TENDL one-group sigma on the FNS Fe spectrum vs EAF-2010. Writes results/g2_resonance.json."""
import os, sys, json, math, time, numpy as np
_T0 = time.time()
def _lap(msg): print(f"[{time.time() - _T0:6.0f} s] {msg}", file=sys.stderr, flush=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
from resonance import parse_mf2, reconstruct_range, resonance_energies, KCONST
from doppler import broaden, KB
from endf_common import fields, endf_float, read_tab1, sections
import openmc.data
from scipy.special import wofz
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"); TD = os.path.expanduser("~/nuclear-data/tendl-eaf-test/TENDL-2023")
FILES = {"Fe56": "n_026-Fe-56_2631.dat", "Ag107": "n_047-Ag-107_4725.dat", "W186": "n_074-W-186_7443.dat"}
out = {"control_a": {}, "control_b": {}, "control_c1": {}, "control_c2": {}, "control_d": {}}
def mf3(path, mt):
    for (mat, mf, mt_), lines in sections(path):
        if mf == 3 and mt_ == mt: (qm, qi, _, lr, nr, np_, nbt, x, y), _ = read_tab1(lines, 1); return np.array(x), np.array(y), nbt
    return None
def interp_lin(x, y, xs): return np.interp(xs, x, y)
worst_a = 0.0; FD = os.path.expanduser("~/nuclear-data/fendl-3.2c")
FENDL = {"Fe56": ("endf/n_2631_26-Fe-56.endf", "ace/26Fe056"), "Ag107": ("endf/n_4725_47-Ag-107.endf", "ace/47Ag107")}
for nuc, (ef, af) in FENDL.items():   # P3 Amendment A: own reconstruction + own Doppler vs IAEA's NJOY-processed ACE (293.6 K), same evaluation
    r2 = parse_mf2(os.path.join(FD, ef)); rg = r2["isotopes"][0]["ranges"][0]; awr = r2["AWR"]
    ace = openmc.data.IncidentNeutron.from_ace(os.path.join(FD, af)); Tkey = list(ace.reactions[102].xs)[0]
    Eace = ace.energy[Tkey]; m = (Eace > rg["EL"] * 1.0001) & (Eace < rg["EH"] * 0.9999); Eg = Eace[m]
    Eres = resonance_energies(rg)
    # Amendment B §1: resonance-adaptive dense 0 K grid, broaden there, sample at the ACE points
    widths = np.concatenate([(Lg["GN"] + Lg["GG"] + Lg.get("GF", np.zeros_like(Lg["GN"])) + Lg.get("GFA", np.zeros_like(Lg["GN"])) * 0) for Lg in rg["L"]]); Er_all = np.concatenate([Lg["ER"] for Lg in rg["L"]])
    dense = [np.logspace(np.log10(rg["EL"]), np.log10(rg["EH"]), 20000), Eg]
    for e, g in zip(Er_all, widths):
        if rg["EL"] < e < rg["EH"]: dense.append(e + max(g, 1e-3) * np.linspace(-40, 40, 161))
    Ed = np.unique(np.concatenate(dense)); Ed = Ed[(Ed > rg["EL"]) & (Ed < rg["EH"])]
    _lap(f"{nuc}: reconstructing {Ed.size} points"); own0d = reconstruct_range(rg, Ed, awr); _lap(f"{nuc}: reconstructed")
    rec = {"reference": "FENDL-3.2c ACE (NJOY2016, 293.6 K)", "LRF": rg["LRF"], "NAPS": rg["NAPS"], "EL": rg["EL"], "EH": rg["EH"], "n_resonances": int(Eres.size), "n_ace_points_in_RRR": int(Eg.size), "n_dense_0K_points": int(Ed.size)}
    for mt, key in ((2, "elastic"), (102, "capture")):
        x3, y3, nbt3 = mf3(os.path.join(FD, ef), mt); s0d = own0d[key] + interp_lin(x3, y3, Ed)
        sT = broaden(Ed, s0d, 293.6, awr, Eout=Eg); s0 = np.interp(Eg, Ed, s0d); _lap(f"{nuc} MT{mt}: broadened")
        s_ace = ace.reactions[mt].xs[Tkey](Eg)
        rel = np.abs(sT - s_ace) / np.maximum(np.abs(s_ace), 1e-300)
        # exclude the top 0.1 % of energies of the RRR (BROADR/ACE thinning edge effects) and points where ACE < 1e-6 b
        keep = (Eg < rg["EH"] * 0.999) & (s_ace > 1e-6)
        w = float(np.max(rel[keep])); worst_a = max(worst_a, w)
        rec[f"MT{mt}"] = {"max_rel": w, "median_rel": float(np.median(rel[keep])), "p99_rel": float(np.percentile(rel[keep], 99)), "n": int(keep.sum()), "E_at_max": float(Eg[keep][np.argmax(rel[keep])]),
                          "own_293K_at_1eV_b": float(np.interp(1.0, Eg, sT)), "ace_at_1eV_b": float(np.interp(1.0, Eg, s_ace)), "own_0K_at_1eV_b": float(np.interp(1.0, Eg, s0))}
    out["control_a"][nuc] = rec; print(nuc, json.dumps(rec))
out["control_a"]["pass"] = bool(worst_a <= 3e-3); out["control_a"]["worst"] = worst_a
# ---- (b) Doppler invariants at 293.6 K, awr = 55.45
E = np.logspace(-5, 5, 4000); T = 293.6; awr = 55.454; kT = KB * T / awr
sv = 10.0 / np.sqrt(E); bv = broaden(E, sv, T, awr); m = (E > 1e-3) & (E < 1e4); e1 = float(np.max(np.abs(bv[m] - sv[m]) / sv[m]))
sc = np.full_like(E, 3.0); bc = broaden(E, sc, T, awr); mc = (np.sqrt(E / kT) >= 10) & (E < 1e4); e2 = float(np.max(np.abs(bc[mc] - sc[mc]) / sc[mc]))
out["control_b"] = {"one_over_v_max_rel": e1, "constant_max_rel_y_ge_10": e2, "pass": bool(max(e1, e2) <= 1e-6), "window_eV_1v": [1e-3, 1e4], "constant_window": "y>=10"}
print("control b:", out["control_b"])
# ---- (c1) exact-kernel brute-force quadrature vs analytic SIGMA1 (Amendment B §3)
def brute(E, sig, Eout, T, awr):
    kT = KB * T / awr; x = np.sqrt(E / kT); y = np.sqrt(Eout / kT); out_ = []
    for yy in y:
        xf = np.linspace(max(0.0, yy - 10), yy + 10, 200001); Ef = xf * xf * kT; sf = np.interp(Ef, E, sig, left=sig[0], right=sig[-1])
        low = Ef < E[0]; sf[low] = sig[0] * x[0] / np.maximum(xf[low], 1e-300)
        out_.append(np.trapezoid(xf * xf * sf * (np.exp(-(xf - yy) ** 2) - np.exp(-(xf + yy) ** 2)), xf) / (yy * yy * np.sqrt(np.pi)))
    return np.array(out_)
T = 293.6; awr = 55.454; Eb = np.logspace(-3, 6, 4000); Eo = np.array([0.01, 1.0, 100.0, 1e4, 1e5])
c1 = {}
Er, G = 1e5, 2.0; line = (Er / Eb) * 0.01 * 0.05 / ((Eb - Er) ** 2 + G * G / 4) + 1e-3
for name, sig in (("1/v", 10 / np.sqrt(Eb)), ("constant", np.full_like(Eb, 3.0)), ("linear", 1 + Eb / 1e4), ("line", line)):
    bo = broaden(Eb, sig, T, awr, Eo); br = brute(Eb, sig, Eo, T, awr); c1[name] = float(np.max(np.abs(bo - br) / np.abs(br)))
out["control_c1"] = {"max_rel_by_function": c1, "pass": bool(max(c1.values()) <= 1e-6)}; print("control c1:", out["control_c1"])
# ---- (c2) psi-function reference (Gaussian-kernel approximation, O(Gamma_D/E_r)), with the 1/E factor, E_r = 1e5 eV
Er, Gn, Gg = 1e5, 0.4, 1.6; G = Gn + Gg
Efine = np.linspace(Er - 25 * G, Er + 25 * G, 40001); sig0 = (Er / Efine) * Gn * Gg / ((Efine - Er) ** 2 + G * G / 4)
num = broaden(Efine, sig0, T, awr)
Delta = np.sqrt(4 * KB * T * Er / awr); x = 2 * (Efine - Er) / G; beta = G / Delta
psi = beta * np.sqrt(np.pi) * wofz((x + 1j) * beta / 2).real / 2
ana = (Er / Efine) * Gn * Gg / (G * G / 4) * psi
win = np.abs(Efine - Er) <= 20 * G; peak = np.abs(Efine - Er) <= G
e_peak = float(np.max(np.abs(num[peak] - ana[peak]) / ana[peak])); e_wing = float(np.max(np.abs(num[win] - ana[win]) / ana[win]))
out["control_c2"] = {"Er": Er, "Gn": Gn, "Gg": Gg, "Doppler_width_eV": float(Delta), "beta": float(beta), "GammaD_over_Er": float(Delta / Er), "max_rel_peak": e_peak, "max_rel_wings_20G": e_wing, "pass": bool(e_peak <= 2e-3 and e_wing <= 2e-3)}
print("control c2:", out["control_c2"])
# ---- (d) TENDL-2023 at 293.6 K, one-group on the FNS Fe spectrum, vs EAF-2010 (from P2 library) 
import g1_collapse as g1
Lib = np.load(os.path.expanduser("~/nuclear-data/eaf-2010/actinv_eaf2010_709g.npz")); idxj = json.load(open(os.path.expanduser("~/nuclear-data/eaf-2010/actinv_eaf2010_709g_index.json")))
tk = [k for k, t in enumerate(idxj["targets"]) if t["za"] == 26056 and t["liso"] == 0][0]; phi = g1.flux_asc
def eaf_one_group(mt): 
    rows = [r for r, (t, m, z, l, s) in enumerate(Lib["rows"]) if t == tk and m == mt and z == -1]; return float(Lib["sig"][rows[0]] @ phi / phi.sum()) if rows else None
path = os.path.join(TD, FILES["Fe56"]); r2 = parse_mf2(path); rg = r2["isotopes"][0]["ranges"][0]; awr = r2["AWR"]
d = {}
for mt in (102, 103, 107, 16):
    x3, y3, nbt3 = mf3(path, mt)
    if mt == 102:
        Eres = resonance_energies(rg); Er = Eres[(Eres > rg["EL"]) & (Eres < rg["EH"])]
        # reconstruction grid: log grid + dense points around each resonance (±50 half-widths at 0 K need widths; use ±0.5% window with 60 points)
        Eg = [np.logspace(np.log10(rg["EL"]), np.log10(rg["EH"]), 20000)]
        for e in Er: Eg.append(e * (1 + np.linspace(-5e-3, 5e-3, 121)))
        Eg = np.unique(np.concatenate(Eg)); Eg = Eg[(Eg >= rg["EL"]) & (Eg <= rg["EH"])]
        s0 = reconstruct_range(rg, Eg, awr)["capture"] + interp_lin(x3, y3, Eg)
        sT = broaden(Eg, s0, 293.6, awr)
        # union with the MF3 grid above EH (URR/fast: LSSF=1 -> MF3 as is)
        hi = x3 > rg["EH"]; Eall = np.concatenate([Eg, x3[hi]]); sall = np.concatenate([sT, y3[hi]])
        grid = g1.union_grid(Eall); s_on = np.interp(grid, Eall, sall); d["102"] = {"tendl2023_293K": g1.collapse(s_on, grid), "tendl2023_0K": g1.collapse(np.interp(grid, Eall, np.concatenate([s0, y3[hi]])), grid), "eaf2010": eaf_one_group(102), "n_resonances_RRR": int(Er.size)}
    else:
        grid = g1.union_grid(x3); s_on = g1.interp_eval(x3, y3, nbt3, grid); d[str(mt)] = {"tendl2023": g1.collapse(s_on, grid), "eaf2010": eaf_one_group(mt)}
out["control_d"] = d; print("control d:", json.dumps(d, indent=1))
out["pass"] = out["control_a"]["pass"] and out["control_b"]["pass"] and out["control_c1"]["pass"] and out["control_c2"]["pass"]
json.dump(out, open(os.path.join(RES, "g2_resonance.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)); print("PASS" if out["pass"] else "FAIL")
