#!/usr/bin/env python3
"""P39 G4: closure checks on the verdict and its evidence."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
v = json.loads((ROOT / "results/verdict_p39.json").read_text())
checks = []

checks.append(("disposition_scoped",
               "verdict is conditional, not pass",
               v["disposition"] == "conditional", {}))

g3 = json.loads((ROOT / "results/g3_p39_check.json").read_text())
checks.append(("g3_green", "negative controls all pass",
               g3["all_pass"], {}))

leg = json.loads((ROOT / "results/g2_p39_leg.json").read_text())
checks.append(("g2_executed",
               "sampled cases executed end-to-end with zero arm failures",
               leg["ledger_rows"] >= 36
               and leg["census"].get("executed_with_failures", 0) == 0
               and leg["expected_executable"] == 912, {}))

checks.append(("improvement_measured",
               "verdict records the ratio collapse honestly",
               "0.98x-1.10x" in json.dumps(v["measured_effect"])
               and "1.05x-200x" in json.dumps(v["measured_effect"]), {}))

checks.append(("tolerance_not_overstated",
               "verdict records that the frozen tolerance still fails",
               v["gates"]["g2"]["tolerance_pass"] == 0, {}))

claims = json.dumps(v["claims"])
forbidden = ["solver superiority", "release-ready",
             "scientifically validated", "production-ready"]
hits = [p for p in forbidden if p in claims.lower()]
checks.append(("no_overclaim",
               "claims carry no validation/release language",
               not hits, {"hits": hits}))

result = {"schema": "actinv-p39-g4-1",
          "checks": [{"name": n, "claim": c, "pass": p, "detail": d}
                     for n, c, p, d in checks]}
result["all_pass"] = all(c["pass"] for c in result["checks"])
(ROOT / "results/g4_p39_check.json").write_text(json.dumps(result, indent=1))
print(json.dumps({c["name"]: c["pass"] for c in result["checks"]}, indent=1))
sys.exit(0 if result["all_pass"] else 1)
