#!/usr/bin/env python3
"""Print a decay-heat table and the dominant nuclides from an ACTINV mesh result.

Usage: python summarize.py result.ndjson [cell-ordinal]
"""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "result.ndjson"
want = int(sys.argv[2]) if len(sys.argv) > 2 else 0

for line in open(path):
    rec = json.loads(line)
    if rec.get("record") != "cell" or rec["ordinal"] != want:
        continue
    steps = rec["result"]["steps"]
    print(f"cell {rec['id']}: {len(steps)} schedule steps")
    print(f"{'t [s]':>12} {'heat [W/g]':>12} {'activity [Bq/g]':>14}  top nuclides")
    for s in steps:
        inv = s.get("activity_Bq_per_g") or {}
        top = sorted(inv.items(), key=lambda kv: -kv[1])[:4]
        tops = ", ".join(f"{n} {v:.3g}" for n, v in top if v > 0)
        heat = s.get("heat_W_per_g") or {}
        total_heat = heat.get("total", 0.0) if isinstance(heat, dict) else heat
        total_act = sum(inv.values())
        print(f"{s['t_s']:12.4g} {total_heat:12.4g} {total_act:14.4g}  {tops}")
    break
else:
    sys.exit(f"no cell {want} in {path}")
