#!/usr/bin/env python3
"""P4-G2 (a): FENDL twins. A TENDL-2023 target is a twin of the FENDL-3.2c file of the same nuclide when their MF=2 sections
have identical numeric content. For every twin, the library's 709-group capture (own reconstruction + SIGMA1, 293.6 K) is
compared with the same collapse of IAEA's NJOY ACE: one-group on the FNS Fe spectrum <= 3e-3; per-group <= 1e-2 where
sigma >= 1e-4 b. Usage: g2_fendl_twins.py LIBRARY_NPZ LIBRARY_INDEX [--fetch]"""
import os, sys, json, glob, re, subprocess, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
from endf_common import sections, fields, endf_float
from tendl_build import group_avg_grid, BOUNDS
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
FD = os.path.expanduser("~/nuclear-data/fendl-3.2c"); TD = os.path.expanduser("~/nuclear-data/tendl-2023/files"); FURL = "https://www-nds.iaea.org/fendl/data/neutron"
lib = np.load(sys.argv[1]); idx = json.load(open(sys.argv[2])); fetch = "--fetch" in sys.argv
tk_of = {}
for k, t in enumerate(idx["targets"]):
    if t.get("za") is not None: tk_of[(t["za"], t["liso"])] = k
def mf2_numeric(path):
    nums = []
    for (mat, mf, mt), lines in sections(path):
        if mf == 2 and mt == 151:
            for l in lines: nums.append(l[:66].strip())
            break
    return "\n".join(nums)
def za_of(path):
    for (mat, mf, mt), lines in sections(path):
        if mf == 1 and mt == 451: f = fields(lines[0]); return int(round(endf_float(f[0]))), int(fields(lines[1])[3])
twins = []; checked = 0
for fe in sorted(glob.glob(os.path.join(FD, "endf", "*.endf"))):
    za, liso = za_of(fe); checked += 1
    cand = [f for f in glob.glob(os.path.join(TD, "*.dat")) if re.search(rf"_{za // 1000:03d}-[A-Za-z]+-{za % 1000}_", f)]
    if not cand or (za, liso) not in tk_of: continue
    a = mf2_numeric(fe); b = mf2_numeric(cand[0])
    if a and a == b: twins.append({"za": za, "liso": liso, "fendl": os.path.basename(fe), "tendl": os.path.basename(cand[0]), "tk": tk_of[(za, liso)]})
print(f"FENDL files checked {checked}; MF=2-identical twins: {len(twins)}", file=sys.stderr)
import g1_collapse as g1; phi = g1.flux_asc
import openmc.data
results = []
for tw in twins:
    stem = re.sub(r"n_\d+_(\d+)-([A-Za-z]+)-(\d+)\.endf", lambda m: f"{int(m.group(1)):02d}{m.group(2)}{int(m.group(3)):03d}", tw["fendl"]).replace("_", "")
    z = tw["za"] // 1000; sym = re.search(r"-([A-Za-z]+)-", tw["fendl"]).group(1); a = tw["za"] % 1000; ace_name = f"{z:02d}{sym}{'_' if len(sym) == 1 else ''}{a:03d}"
    ace = os.path.join(FD, "ace", ace_name)
    if not os.path.exists(ace):
        if not fetch: results.append({**tw, "status": "ACE not local (run with --fetch)"}); continue
        os.makedirs(os.path.dirname(ace), exist_ok=True); subprocess.run(["curl", "-s", "-m", "900", "-A", "Mozilla/5.0", "-o", ace, f"{FURL}/ace/{ace_name}"]); subprocess.run(["curl", "-s", "-m", "60", "-A", "Mozilla/5.0", "-o", ace + ".xsd", f"{FURL}/ace/{ace_name}.xsd"])
    try:
        inc = openmc.data.IncidentNeutron.from_ace(ace); T = list(inc.reactions[102].xs)[0]; E = inc.energy[T]; s = inc.reactions[102].xs[T](E); g_njoy = group_avg_grid(E, s)
    except Exception as e: results.append({**tw, "status": "ACE unreadable: " + repr(e)[:120]}); continue
    rows = [i for i, r in enumerate(lib["rows"]) if r[0] == tw["tk"] and r[1] == 102 and r[2] == -1]
    if not rows: results.append({**tw, "status": "no capture row in library"}); continue
    g_lib = lib["sig"][rows[0]]; one_lib = float(g_lib @ phi / phi.sum()); one_njoy = float(g_njoy @ phi / phi.sum())
    m = g_njoy >= 1e-4; per = np.abs(g_lib[m] - g_njoy[m]) / g_njoy[m]
    results.append({**tw, "status": "compared", "one_group_lib": one_lib, "one_group_njoy": one_njoy, "one_group_rel": abs(one_lib - one_njoy) / one_njoy, "n_groups": int(m.sum()), "per_group_max_rel": float(per.max()) if m.any() else None, "per_group_median_rel": float(np.median(per)) if m.any() else None})
cmp = [r for r in results if r["status"] == "compared"]
out = {"n_fendl_checked": checked, "n_twins": len(twins), "n_compared": len(cmp), "pass": bool(cmp) and all(r["one_group_rel"] <= 3e-3 and (r["per_group_max_rel"] or 0) <= 1e-2 for r in cmp),
       "worst_one_group": max((r["one_group_rel"] for r in cmp), default=None), "worst_per_group": max((r["per_group_max_rel"] or 0 for r in cmp), default=None), "results": results}
json.dump(out, open(os.path.join(RES, "g2_fendl_twins.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)); print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1)); [print(" ", r["fendl"], r["status"], r.get("one_group_rel"), r.get("per_group_max_rel")) for r in results]
