#!/usr/bin/env python3
"""P38 G4: closure checks on the verdict and its evidence."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
v = json.loads((ROOT / "results/verdict_p38.json").read_text())
checks = []

checks.append(("disposition_scoped",
               "verdict is conditional, not pass",
               v["disposition"] == "conditional", {}))

g3 = json.loads((ROOT / "results/g3_p38_check.json").read_text())
checks.append(("g3_green", "negative controls all pass",
               g3["all_pass"], {}))

leg = json.loads((ROOT / "results/g2_p38_leg.json").read_text())
checks.append(("g2_executed",
               "sampled cases executed end-to-end with zero arm failures",
               leg["ledger_rows"] >= 40
               and leg["census"].get("executed_with_failures", 0) == 0
               and leg["expected_executable"] == 912, {}))

checks.append(("coverage_gap_named",
               "verdict names the lumped-MT coverage gap as the next "
               "blocker",
               "600-849" in json.dumps(v["identical_data_findings"]), {}))

checks.append(("w183_reclassified",
               "W-183's masked defect is recorded, not silently admitted",
               v["scope_amendment"]["admit_count"] == 7
               and "W-183" in json.dumps(
                   v["scope_amendment"]["reclassification"]), {}))

claims = json.dumps(v["claims"])
forbidden = ["solver superiority", "release-ready",
             "scientifically validated", "production-ready"]
hits = [p for p in forbidden if p in claims.lower()]
checks.append(("no_overclaim",
               "claims carry no validation/release language",
               not hits, {"hits": hits}))

result = {"schema": "actinv-p38-g4-1",
          "checks": [{"name": n, "claim": c, "pass": p, "detail": d}
                     for n, c, p, d in checks]}
result["all_pass"] = all(c["pass"] for c in result["checks"])
(ROOT / "results/g4_p38_check.json").write_text(json.dumps(result, indent=1))
print(json.dumps({c["name"]: c["pass"] for c in result["checks"]}, indent=1))
sys.exit(0 if result["all_pass"] else 1)
