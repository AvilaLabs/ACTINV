#!/usr/bin/env python3
"""P61 G3 — real-data demonstration: FNS W/Y/Pb runs with the audit
output. The completeness verdict must reflect each library's real defect
inventory (W carries no-evaluated-decay products in TENDL), and every
emitted defect rate must trace back to the run's own ledger maps.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p44_bands  # noqa: E402
import p61_case  # noqa: E402

ACTINV = ROOT / "target/release/actinv"
OUT = ROOT / "results/g3_p61_demo.json"

CASES = [("W", "2000exp_5min"), ("Pb", "1996exp_5min"),
         ("Y", "2000exp_5min")]


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


checks = []
tmp = Path(tempfile.mkdtemp(prefix="p61_g3_", dir=ROOT / "target"))
demo = {}
for material, experiment in CASES:
    name = f"{material}__{experiment}"
    spec, _inp = p44_bands.spec_for(material, experiment)
    spec.pop("uncertainty", None)
    spec["options"]["outputs"] = list(spec["options"].get("outputs", []))
    if "audit" not in spec["options"]["outputs"]:
        spec["options"]["outputs"].append("audit")
    res = p61_case.run(ACTINV, spec, tmp, name)
    comp = res["ledger"].get("completeness")
    check(f"completeness present: {name}", comp is not None)
    if comp is None:
        continue
    check(f"status honest: {name}",
          comp["status"] in ("complete", "incomplete"))
    check(f"status vs inventory consistent: {name}",
          (comp["status"] == "complete")
          == (not comp["reaction_channel"]["defects"]
              and not comp["decay_channel"]["defects"]
              and not comp["unquantified"]))
    check(f"channel totals positive: {name}",
          comp["reaction_channel"]
          ["total_production_rate_coefficient_per_s"] > 0.0
          and comp["decay_channel"]
          ["total_decay_rate_coefficient_per_s"] > 0.0)
    # every quantified rate traces to a ledger field
    ledger = res["ledger"]
    maps = {
        "products_no_evaluated_decay_data": "products_no_evaluated_decay_data",
        "products_unmapped_to_leakage": "products_unmapped_to_leakage",
        "isomer_fell_back_to_ground":
            "isomer_state_absent_from_decay_library_used_ground",
        "fission_no_yields_to_leakage": "fission_no_yields_to_leakage",
    }
    for d in comp["reaction_channel"]["defects"]:
        cls = d["class"]
        if cls in maps:
            acc = 0.0
            for v in (ledger.get(maps[cls]) or {}).values():
                acc += v
            check(f"{cls} rate traces to ledger: {name}",
                  d["rate_coefficient_per_s"] == acc,
                  f"{d['rate_coefficient_per_s']!r}")
    demo[name] = {
        "status": comp["status"],
        "reaction_defects": [
            {"class": d["class"], "instances": d["instances"],
             "fraction": d.get("fraction_of_channel_flow"),
             "largest": [e["name"] for e in d.get("largest", [])]}
            for d in comp["reaction_channel"]["defects"]],
        "decay_defects": [
            {"class": d["class"], "instances": d["instances"]}
            for d in comp["decay_channel"]["defects"]],
        "unquantified": [d["class"] for d in comp["unquantified"]],
    }

OUT.write_text(json.dumps({"pass": all(c["pass"] for c in checks),
                           "n": len(checks), "checks": checks,
                           "demo": demo}, indent=1) + "\n")
failed = [c for c in checks if not c["pass"]]
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
