#!/usr/bin/env python3
"""P3-G4: emit results/fns_certificate.json — SHA-256 of every input (library npz + index, decay files, fns.zip, abundance
table, CRAM coefficients, protocols/amendments, solver binary, harness sources) and per-experiment hashes of the stored
inventories and C/E vectors. Re-derivable by controls/check_p3.py."""
import os, sys, json, glob, hashlib
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
def sha(p): h = hashlib.sha256(); h.update(open(p, "rb").read()); return h.hexdigest()
inputs = {"eaf2010_709g_npz": os.path.expanduser("~/nuclear-data/eaf-2010/actinv_eaf2010_709g.npz"), "eaf2010_709g_index": os.path.expanduser("~/nuclear-data/eaf-2010/actinv_eaf2010_709g_index.json"),
          "eaf2010_zips_manifest": os.path.expanduser("~/nuclear-data/eaf-2010/MANIFEST_zips.sha256"), "decay_endfb80": os.path.expanduser("~/nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"),
          "decay_jeff33": os.path.expanduser("~/nuclear-data/jeff-3.3-decay/bulk/jeff-3-3_decay.dat"), "fns_zip": os.path.expanduser("~/nuclear-data/conderc-fns/fns.zip"),
          "abundance_mass_table": os.path.join(RES, "tables", "abundance_mass.json"), "cram_coefficients": os.path.expanduser("~/Documents/Avila-Labs/scouting/act-p0/results/cram_coefficients.json"),
          "solver_binary": os.path.join(ROOT, "target", "release", "actinv-solve")}
for f in sorted(glob.glob(os.path.join(ROOT, "protocols", "*.md"))): inputs["protocol:" + os.path.basename(f)] = f
for f in sorted(glob.glob(os.path.join(ROOT, "controls", "*.py")) + glob.glob(os.path.join(ROOT, "controls", "harness", "*.py")) + glob.glob(os.path.join(ROOT, "crates", "*", "src", "*.rs")) + glob.glob(os.path.join(ROOT, "crates", "*", "src", "bin", "*.rs"))): inputs["source:" + os.path.relpath(f, ROOT)] = f
cert = {"program": "ACTINV", "instrument": "FNS decay-heat harness (P3)", "inputs": {k: {"path": p, "sha256": sha(p) if os.path.exists(p) else None} for k, p in inputs.items()}, "experiments": {}}
for f in sorted(glob.glob(os.path.join(RES, "fns", "*.json"))):
    r = json.load(open(f)); k = os.path.basename(f)[:-5]
    inv_h = hashlib.sha256(json.dumps(r.get("inventories", []), sort_keys=True).encode()).hexdigest(); ce_h = hashlib.sha256(json.dumps(r.get("CE_actinv", []), sort_keys=True).encode()).hexdigest()
    cert["experiments"][k] = {"record_sha256": sha(f), "inventories_sha256": inv_h, "CE_actinv_sha256": ce_h, "n_points": len(r.get("CE_actinv", [])), "error": bool(r.get("error"))}
cert["certificate_body_sha256"] = hashlib.sha256(json.dumps({k: v for k, v in cert.items()}, sort_keys=True).encode()).hexdigest()
json.dump(cert, open(os.path.join(RES, "fns_certificate.json"), "w"), indent=1); print("certificate:", len(cert["inputs"]), "inputs,", len(cert["experiments"]), "experiments, body sha", cert["certificate_body_sha256"][:16])
