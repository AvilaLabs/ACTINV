#!/usr/bin/env python3
"""P5-G6: every ledger category of docs/LEDGER.md must be present through every entry point, and a planted failure
(a deleted decay record) must surface identically through the CLI, the Python API and the harness."""
import os, sys, json, subprocess, shutil, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
import actinv
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
CLI = os.path.join(ROOT, "target", "release", "actinv")
spec = json.load(open(os.path.join(ROOT, "examples", "fns_fe_5min.json")))
REQUIRED = ["mode", "max_burnup_fraction", "composition_weight_percent_total", "composition_not_summing_to_100",
            "composition_isotopes_absent_from_decay_library", "composition_elements_unknown",
            "products_no_evaluated_decay_data", "fission_no_yields_to_leakage", "products_unmapped_to_leakage",
            "isomer_state_absent_from_decay_library_used_ground", "targets_absent_from_decay_library",
            "bulk_production_dropped", "decay_daughters_missing", "spontaneous_fission_branches_to_leakage",
            "decay_nuclides_from_fallback", "negative_atoms_zeroed_per_step", "bulk_background_heat_W_per_g",
            "numerical_floor", "assembly"]
base = json.loads(actinv.run(json.dumps(spec)))
present = [k for k in REQUIRED if k in base["ledger"]]
missing = [k for k in REQUIRED if k not in base["ledger"]]
# ---- planted failure: remove Mn-56's decay record (MF=8/MT=457) from a copy of the primary sublibrary
src = spec["decay"]["primary"]; tmp = os.path.join(RES, "_g6_decay.dat")
from endf_decay import parse_decay_file                     # look the MAT up; never guess it
TARGET_ZA, TARGET_LISO = 25056, 0                            # Mn-56, the dominant contributor for an iron sample
recs = parse_decay_file(src)
MAT = next(m for m, r in recs.items() if int(round(r["za"])) == TARGET_ZA and r["liso"] == TARGET_LISO)
keep, dropped = [], 0
for line in open(src, errors="replace"):
    if len(line) >= 75:
        try: mat, mf, mt = int(line[66:70]), int(line[70:72]), int(line[72:75])
        except ValueError: mat = mf = mt = 0
        if mf == 8 and mt == 457 and mat == MAT:
            dropped += 1; continue
    keep.append(line)
open(tmp, "w").writelines(keep)
sp2 = json.loads(json.dumps(spec)); sp2["decay"]["primary"] = tmp; sp2["decay"]["fallback"] = ""
py = json.loads(actinv.run(json.dumps(sp2)))
sp2_path = os.path.join(RES, "_g6_spec.json"); json.dump(sp2, open(sp2_path, "w"))
out = os.path.join(RES, "_g6_cli.json"); subprocess.run([CLI, "run", sp2_path, out], check=True, capture_output=True)
cli = json.load(open(out))
sys.path.insert(0, os.path.join(ROOT, "controls")); import run_fns_spec  # harness entry point uses the same binding
har = json.loads(actinv.run(json.dumps(sp2)))
for f in (tmp, sp2_path, out): os.remove(f)
def surfaced(r):
    l = r["ledger"]
    return {"mn56_absent": any(k.startswith("25056") for k in l["products_no_evaluated_decay_data"]),
            "atoms_to_leakage": r["steps"][1]["leakage_atoms_per_g"] > 0.0,
            "heat_changed": r["steps"][1]["heat_W_per_g"]["total"] != base["steps"][1]["heat_W_per_g"]["total"]}
s_py, s_cli, s_har = surfaced(py), surfaced(cli), surfaced(har)
identical = (json.dumps(py["ledger"], sort_keys=True) == json.dumps(cli["ledger"], sort_keys=True) ==
             json.dumps(har["ledger"], sort_keys=True))
res = {"required_categories": len(REQUIRED), "present": len(present), "missing": missing,
       "planted": {"decay_records_removed": dropped, "nuclide": f"Mn-56 (MAT {MAT})",
                   "surfaced_python": s_py, "surfaced_cli": s_cli, "surfaced_harness": s_har,
                   "ledgers_identical_across_entry_points": identical,
                   "pass": bool(all(s_py.values()) and s_py == s_cli == s_har and identical)},
       "certificate_keys": sorted(base["certificate"].keys()),
       "pass": bool(not missing and all(s_py.values()) and s_py == s_cli == s_har and identical)}
json.dump(res, open(os.path.join(RES, "g6_ledger_certificate.json"), "w"), indent=1); print(json.dumps(res, indent=1))
