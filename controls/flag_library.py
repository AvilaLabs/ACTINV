#!/usr/bin/env python3
"""P4 Amendment A: write convergence flags from results/g2_tendl_dense1.json into the library index so every run using a
flagged target inherits the flag. Usage: flag_library.py INDEX_JSON"""
import os, sys, json
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
idx_path = sys.argv[1]; idx = json.load(open(idx_path)); g = json.load(open(os.path.join(RES, "g2_tendl_dense1.json")))
worst = {}
for r in g["control_c"]["rows_over"]: worst[r["target"]] = max(worst.get(r["target"], 0.0), r["max_rel"])
n = 0
for t in idx["targets"]:
    if t["file"] in worst: t["convergence_flag"] = f"GRID-SENSITIVE: capture/fission group values change up to {worst[t['file']]:.1e} between grid densities (P4 control c)"; n += 1
idx["convergence_control"] = {"sample_seed": g["sample_seed"], "n_sample": g["n_sample"], "rows": g["control_c"]["n_capture_rows"], "rows_over_1e-3": len(g["control_c"]["rows_over"]), "max_rel": g["control_c"]["max_rel"], "flagged_targets": sorted(worst)}
json.dump(idx, open(idx_path, "w"), indent=1); print(f"flagged {n} targets: {sorted(worst)}")
