#!/usr/bin/env python3
"""P40 G4: closure checks on the verdict and its evidence."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
v = json.loads((ROOT / "results/verdict_p40.json").read_text())
checks = []

checks.append(("disposition_scoped",
               "verdict is conditional, not pass",
               v["verdict"] == "P40-CONDITIONAL", {}))

g3 = json.loads((ROOT / "results/g3_p40_check.json").read_text())
checks.append(("g3_green", "classifier negative controls all pass",
               g3["all_pass"], {}))

cls = json.loads((ROOT / "results/g2_p40_classes.json").read_text())
checks.append(("census_complete",
               "912/912 cases classified, zero skips",
               cls["n_cases"] == 912 and not cls["skipped"], {}))

rows = [json.loads(l)["record"]
        for l in (ROOT / "results/g2_p39_leg_ledger.jsonl")
        .read_text().splitlines()]
checks.append(("arm_failures_zero",
               "no arm failures across the full executable census",
               len(rows) == 912
               and all(r["status"] != "executed_with_failures"
                       for r in rows), {}))

checks.append(("open_classes_named",
               "pre-registered rule 2 fired: 'other'>1% on >5% of cases "
               "is named as open classes, not folded into floors",
               "short_lived_products_absent_in_actinv"
               in v["divergence_classes"]
               and "common_nuclide_magnitude" in v["divergence_classes"]
               and v["divergence_classes"]
               ["short_lived_products_absent_in_actinv"]["status"]
               .startswith("OPEN"), {}))

claims = json.dumps(v["claims"]).lower() \
    + json.dumps(v["scoped_equivalence"]).lower()
forbidden = ["solver superiority", "release-ready",
             "scientifically validated", "production-ready"]
hits = [p for p in forbidden if p in claims]
checks.append(("no_overclaim",
               "claims carry no validation/release language",
               not hits, {"hits": hits}))

result = {"schema": "actinv-p40-g4-1",
          "checks": [{"name": n, "claim": c, "pass": p, "detail": d}
                     for n, c, p, d in checks]}
result["all_pass"] = all(c["pass"] for c in result["checks"])
(ROOT / "results/g4_p40_check.json").write_text(json.dumps(result, indent=1))
print(json.dumps({c["name"]: c["pass"] for c in result["checks"]}, indent=1))
sys.exit(0 if result["all_pass"] else 1)
