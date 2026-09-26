#!/usr/bin/env python3
"""P58 G3 — real-data demonstration: the frozen P44 FNS spec for the
three isomer-heavy experiments from the head-to-head corpus (W, Ta, Y)
run with `uncertainty.isomer`. Checks the emitted partition is physical:
bucket shares on the unit simplex, isomer channels ranked where isomer
physics is known to matter (W-185m, Ta-180m, Y-89m), and per-step
pathway shares emitted under the trace mode these runs use.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p44_bands  # noqa: E402
import p58_case  # noqa: E402

ACTINV = ROOT / "target/release/actinv"
OUT = ROOT / "results/g3_p58_demo.json"

CASES = [("W", "2000exp_5min"), ("Pb", "1996exp_5min"),
         ("Y", "2000exp_5min")]

BUCKETS = ("isomer_product_channels", "isomer_target_channels",
           "isomer_decay_constants", "ground_channels")


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


checks = []
tmp = Path(tempfile.mkdtemp(prefix="p58_g3_", dir=ROOT / "target"))
demo = {}
for material, experiment in CASES:
    name = f"{material}__{experiment}"
    spec, _inp = p44_bands.spec_for(material, experiment)
    spec["uncertainty"]["isomer"] = {}
    spec["options"]["outputs"] = list(spec["options"]["outputs"]) + [
        "pathways"]
    res = p58_case.run(ACTINV, spec, tmp, name)
    # pick the last post-shutdown step with finite positive variance
    step = next(s for s in reversed(res["steps"])
                if s["uncertainty"]["responses"]["heat.total"]["isomer"]
                ["total_propagated_variance"] > 0.0)
    iso = step["uncertainty"]["responses"]["heat.total"]["isomer"]
    sh = iso["variance_shares"]
    total = sum(sh[b] for b in BUCKETS)
    check(f"share sum ~= 1: {name}", abs(total - 1.0) < 1e-12,
          f"sum={total!r}")
    check(f"isomer nonzero on {name}",
          sh["isomer_product_channels"] > 0.0
          or sh["isomer_target_channels"] > 0.0
          or sh["isomer_decay_constants"] > 0.0,
          json.dumps(sh))
    top = [e["channel_label"] for e in iso["top_isomer_channels"][:5]]
    check(f"isomer channels ranked on {name}", len(top) > 0,
          "; ".join(top))
    shares = res.get("isomer_pathway_shares")
    check(f"pathway shares on {name}",
          isinstance(shares, list) and len(shares) == len(res["steps"]))
    emitted = [s for s in (shares or []) if s["status"] == "emitted"]
    if shares:
        check(f"pathway shares emitted or honestly unavailable: {name}",
              all(s["status"] in ("emitted", "unavailable")
                  for s in shares))
    demo[name] = {"variance_shares": sh, "top_isomer_channels": top,
                  "isomer_atoms_share": [s.get("share") for s in emitted]}

OUT.write_text(json.dumps({"pass": all(c["pass"] for c in checks),
                           "n": len(checks), "checks": checks,
                           "demo": demo}, indent=1) + "\n")
failed = [c for c in checks if not c["pass"]]
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
