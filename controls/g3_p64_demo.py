#!/usr/bin/env python3
"""P64 G3 — real-data demo: decide over the frozen P44 FNS W case
(TENDL-2025 709g). One constraint is set at the *measured* heat value
from the corpus (certifies band-vs-measurement agreement) and one at a
comfortable multiple; the document's margins, overcertify count, audit
verdict, and measurement targets are asserted honest."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p44_bands  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/release/actinv"
OUT = ROOT / "results/g3_p64_demo.json"
DECISION = ROOT / "results/p64_decision_w.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p64_g3_", dir=ROOT / "target"))
spec, inp = p44_bands.spec_for("W", "1996exp_5min")
run_path = tmp / "w_run.json"
run_path.write_text(json.dumps(spec, sort_keys=True) + "\n")
t_irr = float(inp["t_irr_s"])

# Measured heat for this experiment from the sealed corpus.
sealed = json.loads((ROOT / "results/p44_sealed_coverage.json").read_bytes())
w_exp = next(e for e in sealed["experiments"]
             if e["material"] == "W" and e["experiment"] == "1996exp_5min")
pts = [p for p in w_exp["points"]
       if isinstance(p["bands"]["first_order"].get("band"), dict)
       and p["bands"]["first_order"]["band"]["nominal"] > 0]
check("measured bandable points present", len(pts) >= 3)
t1, t2 = pts[0], pts[-1]

dspec = {
    "schema": "actinv-decide-1",
    "run_spec": str(run_path),
    "decision": {"measurement_top": 5, "constraints": [
        # band-vs-measurement agreement at the earliest cooling point
        {"name": "heat_matches_measured", "response": "heat.total",
         "time_s": t_irr + t1["t_s"], "edge": "normal_upper", "sense": "ge",
         "limit": t1["measured_W_g"]},
        # roomy limit at the last cooling point — certifies
        {"name": "heat_budget", "response": "heat.total",
         "time_s": t_irr + t2["t_s"], "edge": "conservative_upper",
         "sense": "le", "limit": t2["measured_W_g"] * 5.0},
    ]},
}
dp = tmp / "decide.json"
dp.write_text(json.dumps(dspec) + "\n")
r = subprocess.run([str(ACTINV), "decide", str(dp), str(DECISION)],
                   cwd=ROOT, text=True, capture_output=True, timeout=3600)
check("decide ran on real data", r.returncode == 0, r.stderr[-400:])
doc = json.loads(DECISION.read_text())

c0, c1 = doc["constraints"]
check("measurement-agreement constraint evaluated",
      c0["value"] > 0 and isinstance(c0["satisfied"], bool)
      and c0["nominal"] is not None,
      json.dumps({k: c0[k] for k in ("value", "nominal", "violation")}))
check("roomy constraint certified",
      c1["satisfied"] is True and c1["violation"] < 0)
check("verdict honest either way",
      doc["verdict"]["certified"] == (c0["satisfied"] and c1["satisfied"]))
check("overcertify count is an honest integer",
      doc["verdict"]["nominal_would_overcertify"] in (0, 1))
check("statement names confidence",
      "0.68" in doc["verdict"]["statement"])
check("audit verdict emitted (P61 surface)",
      isinstance(doc["completeness"], dict)
      and doc["completeness"].get("status") in ("complete", "incomplete",
                                                "degraded"))
check("measurement targets ranked",
      any(m.get("top_parameters") for m in doc["measurements"].values()),
      json.dumps(list(doc["measurements"])))

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks,
                           "verdict": doc["verdict"]}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:8],
                  "certified": doc["verdict"]["certified"],
                  "overcertify": doc["verdict"]["nominal_would_overcertify"]}))
sys.exit(1 if failed else 0)
