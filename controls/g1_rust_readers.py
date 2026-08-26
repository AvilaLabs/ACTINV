#!/usr/bin/env python3
"""P5-G1: the Rust readers must reproduce the Python ones exactly.
(a) decay sublibrary: all 3,821 ENDF/B-VIII.0 materials — half-life, mean energies, every branching ratio and Q, 1e-12.
(b) activation library: every row and every group value bit-identical to numpy."""
import os, sys, json, subprocess, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
from endf_decay import parse_decay_file
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
DUMP = os.path.join(ROOT, "target", "release", "dump")
DECAY = os.path.expanduser("~/nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat")
LIB = os.environ.get("ACTINV_LIBRARY", os.path.expanduser("~/nuclear-data/tendl-2023/actinv_tendl2023_709g.npz"))
def rel(a, b): return abs(a - b) / max(abs(a), abs(b), 1e-300)
# ---- (a) decay
out = subprocess.run([DUMP, "decay", DECAY], capture_output=True, text=True, check=True).stdout.splitlines()
n_rust = int(out[0]); recs = parse_decay_file(DECAY)
py = {}
for r in recs.values(): py[(int(round(r["za"])), r["liso"])] = r
mism = []; checked = 0
for line in out[1:]:
    t = line.split(); za, liso, nst = int(t[0]), int(t[1]), int(t[2]); hl, el, eem, eh = map(float, t[3:7]); nm = int(t[7])
    p = py.get((za, liso))
    if p is None: mism.append((za, liso, "absent in python")); continue
    checked += 1
    e = list(p["energies"]) + [0.0] * 6
    if rel(hl, p["half_life"]) > 1e-12: mism.append((za, liso, "half_life", hl, p["half_life"]))
    for k, (rv, pv) in enumerate(((el, e[0]), (eem, e[2]), (eh, e[4]))):
        if rel(rv, pv) > 1e-12: mism.append((za, liso, f"energy{k}", rv, pv))
    if nm != len(p["modes"]): mism.append((za, liso, "nmodes", nm, len(p["modes"]))); continue
    rm = [tuple(map(float, t[8 + 4 * i:12 + 4 * i])) for i in range(nm)]
    pm = sorted((m["rtyp"], m["rfs"], m["br"], m["q"]) for m in p["modes"])
    for (a_, b_) in zip(rm, pm):
        if any(rel(x, y) > 1e-12 for x, y in zip(a_, b_)): mism.append((za, liso, "mode", a_, b_))
a_pass = len(mism) == 0 and n_rust == len(py)
# ---- (b) library: byte identity of the arrays themselves (a checksum cannot test this — float addition is not associative)
tmp = os.path.join(RES, "_g1_rust_lib")
out = subprocess.run([DUMP, "library", LIB, tmp], capture_output=True, text=True, check=True).stdout.split()
nrows, ng = int(out[0]), int(out[1]); L = np.load(LIB)
rows_bytes = open(tmp + ".rows", "rb").read(); sig_bytes = open(tmp + ".sig", "rb").read()
ref_rows = np.ascontiguousarray(L["rows"], dtype="<i8").tobytes(); ref_sig = np.ascontiguousarray(L["sig"], dtype="<f8").tobytes()
rows_ok = rows_bytes == ref_rows; sig_ok = sig_bytes == ref_sig
first_diff = next((i for i, (x, y) in enumerate(zip(sig_bytes, ref_sig)) if x != y), None) if not sig_ok else None
os.remove(tmp + ".rows"); os.remove(tmp + ".sig")
b_pass = bool(nrows == L["rows"].shape[0] and ng == L["sig"].shape[1] and rows_ok and sig_ok)
res = {"decay": {"n_rust": n_rust, "n_python": len(py), "n_checked": checked, "n_mismatch": len(mism), "examples": [list(map(str, m)) for m in mism[:10]], "pass": bool(a_pass)},
       "library": {"path": LIB, "rows": nrows, "groups": ng, "rows_bytes_identical": bool(rows_ok), "sig_bytes_identical": bool(sig_ok), "sig_bytes": len(sig_bytes), "first_differing_byte": first_diff, "criterion": "byte identity of rows and sig arrays", "pass": b_pass},
       "pass": bool(a_pass and b_pass)}
json.dump(res, open(os.path.join(RES, "g1_rust_readers.json"), "w"), indent=1); print(json.dumps(res, indent=1))
