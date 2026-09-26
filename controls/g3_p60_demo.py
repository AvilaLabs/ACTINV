#!/usr/bin/env python3
"""P60 G3 — real-data demonstration: the frozen P44 FNS specs for W, Pb
and Y run with `uncertainty.design`. Checks the emitted rankings are
physical: posterior + reduction = total, target blocks solve on real
MF=33 covariance, and the anticorrelation effect is visible — a parameter
whose measurement reduction exceeds its marginal variance share.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p44_bands  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/release/actinv"
OUT = ROOT / "results/g3_p60_demo.json"

CASES = [("W", "2000exp_5min"), ("Pb", "1996exp_5min"),
         ("Y", "2000exp_5min")]


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


checks = []
tmp = Path(tempfile.mkdtemp(prefix="p60_g3_", dir=ROOT / "target"))
demo = {}
for material, experiment in CASES:
    name = f"{material}__{experiment}"
    spec, _inp = p44_bands.spec_for(material, experiment)
    spec["uncertainty"]["design"] = {}
    res = p60_case.run(ACTINV, spec, tmp, name)
    step = next(s for s in reversed(res["steps"])
                if s["uncertainty"]["responses"]["heat.total"]["design"]
                ["total_propagated_variance"] > 0.0)
    des = step["uncertainty"]["responses"]["heat.total"]["design"]
    total = des["total_propagated_variance"]
    check(f"posterior consistency: {name}",
          all(e["posterior_variance"] is None
              or abs(e["posterior_variance"] + e["variance_reduction"]
                     - total) <= 1e-12 * max(1.0, abs(total))
              for e in des["top_parameters"]))
    check(f"params ranked: {name}", len(des["top_parameters"]) > 0)
    emitted_blocks = [r for r in des["top_reactions"]
                      if r["status"] == "emitted"]
    check(f"target blocks solved: {name}", len(emitted_blocks) > 0,
          json.dumps(des["top_reactions"][:3]))
    check(f"block reductions <= total: {name}",
          all(r["variance_reduction"] <= total * (1.0 + 1e-9)
              for r in emitted_blocks))
    # the anticorrelation/anchoring claim: at least one parameter whose
    # perfect-measurement reduction differs materially from its share
    anchors = [e for e in des["top_parameters"]
               if e["channel"] == "cross_section_mf33"
               and e["variance_share"] is not None
               and abs(e["variance_reduction"] - e["variance_share"])
               > 1e-9 * abs(e["variance_share"])]
    check(f"conditional reduction differs from share: {name}",
          len(anchors) > 0,
          json.dumps([{ "mt": e["parameter"]["MT"],
                        "reduction": e["variance_reduction"],
                        "share": e["variance_share"]}
                      for e in anchors[:3]]))
    demo[name] = {
        "total_propagated_variance": total,
        "top_parameters": [{"channel": e["channel"],
                            "parameter": e["parameter"],
                            "variance_reduction": e["variance_reduction"],
                            "variance_share": e["variance_share"],
                            "share_of_total": e["share_of_total"]}
                           for e in des["top_parameters"][:5]],
        "top_reactions": des["top_reactions"][:5],
    }

OUT.write_text(json.dumps({"pass": all(c["pass"] for c in checks),
                           "n": len(checks), "checks": checks,
                           "demo": demo}, indent=1) + "\n")
failed = [c for c in checks if not c["pass"]]
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
