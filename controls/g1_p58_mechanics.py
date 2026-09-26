#!/usr/bin/env python3
"""P58 G1 — mechanics: isomer block present on every banded response,
buckets disjoint and summing to the band total, ranked table labelled,
step-level pathway shares emitted, parse rejections, and byte-identical
output when uncertainty.isomer is absent.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_case  # noqa: E402
import p58_fixture  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g1_p58_mechanics.json"

BUCKETS = ("isomer_product_channels", "isomer_target_channels",
           "isomer_decay_constants", "ground_channels")


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


checks = []
tmp = Path(tempfile.mkdtemp(prefix="p58_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)

# --- banded isomer run ------------------------------------------------
res = p58_case.run(ACTINV, p58_case.spec(fx), tmp, "isomer")
u = res["steps"][-1]["uncertainty"]["responses"]

for response in ("activity:Mn57", "activity:Mn57m1", "heat.total"):
    resp = u[response]
    iso = resp.get("isomer")
    check(f"isomer present: {response}", iso is not None)
    if iso is None:
        continue
    sh = iso["variance_shares"]
    total = sum(sh[b] for b in BUCKETS)
    check(f"share sum ~= 1: {response}", abs(total - 1.0) < 5e-15,
          f"sum={total!r}")
    check(f"shares in [0,1]: {response}",
          all(-1e-15 <= sh[b] <= 1.0 + 1e-15 for b in BUCKETS),
          json.dumps(sh))
    propagated = sum(ch["standard_uncertainty"] ** 2
                     for ch in resp["channels"]
                     if ch.get("status") == "propagated")
    check(f"total variance matches: {response}",
          propagated > 0.0 and
          abs(iso["total_propagated_variance"] - propagated)
          <= 1e-12 * propagated,
          f"iso={iso['total_propagated_variance']!r} sum={propagated!r}")

m1 = u["activity:Mn57m1"]["isomer"]
check("Mn57m1 dominated by isomer product channel",
      m1["variance_shares"]["isomer_product_channels"] > 0.99,
      json.dumps(m1["variance_shares"]))
check("Mn57m1 decay-constant share nonzero",
      m1["variance_shares"]["isomer_decay_constants"] > 0.0)
heat = u["heat.total"]["isomer"]
check("heat splits isomer/ground",
      0.2 < heat["variance_shares"]["isomer_product_channels"] < 0.8
      and 0.2 < heat["variance_shares"]["ground_channels"] < 0.8,
      json.dumps(heat["variance_shares"]))
check("top isomer channel labelled",
      any("lfs=1" in e["channel_label"] for e in m1["top_isomer_channels"]),
      json.dumps(m1["top_isomer_channels"][:2]))
check("isomer decay channel labelled",
      any(e["channel"] == "decay_constants" and "lambda" in e["channel_label"]
          for e in m1["top_isomer_channels"]))
check("ground response still lists isomer channels at zero share",
      u["activity:Mn57"]["isomer"]["variance_shares"]
      ["isomer_product_channels"] == 0.0)

# --- pathway shares ----------------------------------------------------
shares = res.get("isomer_pathway_shares")
check("isomer_pathway_shares present", isinstance(shares, list)
      and len(shares) == len(res["steps"]))
emitted = [s for s in shares if s["status"] == "emitted"]
check("pathway shares emitted on all steps",
      len(emitted) == len(res["steps"]))
check("pathway isomer share positive",
      all(s["share"] is not None and s["share"] > 0 for s in emitted))
check("top isomer product is Mn57m1",
      all(s["top_isomer_products"][0]["first_product"] == "Mn57m1"
          for s in emitted))

# --- absent isomer option -> byte-identical record ----------------------
res_off = p58_case.run(ACTINV, p58_case.spec(fx, isomer=False), tmp, "off")
res_on = p58_case.run(ACTINV, p58_case.spec(fx, isomer=False), tmp, "off2")
check("unbanded option: no isomer key on responses",
      all("isomer" not in r for st in res_off["steps"]
          for r in st["uncertainty"]["responses"].values()))
check("isomer_pathway_shares absent when not requested",
      "isomer_pathway_shares" not in res_off)
res_off.pop("ms", None)
res_on.pop("ms", None)
check("repeat run byte-comparable (ms excluded)",
      json.dumps(res_off, sort_keys=True) == json.dumps(res_on, sort_keys=True))

# --- parse rejections ----------------------------------------------------
bad = p58_case.spec(fx)
bad["uncertainty"]["isomer"] = 5
sp = tmp / "bad.spec.json"; sp.write_text(json.dumps(bad))
r = subprocess.run([str(ACTINV), "run", str(sp), str(tmp / "bad.out.json")],
                   cwd=ROOT, text=True, capture_output=True, timeout=60)
check("non-object isomer rejected", r.returncode != 0)

bad2 = p58_case.spec(fx)
bad2["uncertainty"]["isomer"] = {"top": 0}
sp2 = tmp / "bad2.spec.json"; sp2.write_text(json.dumps(bad2))
r2 = subprocess.run([str(ACTINV), "run", str(sp2), str(tmp / "bad2.out.json")],
                    cwd=ROOT, text=True, capture_output=True, timeout=60)
check("isomer.top=0 rejected", r2.returncode != 0
      and "isomer.top" in r2.stderr, r2.stderr[-200:])

bad3 = p58_case.spec(fx)
bad3["uncertainty"]["isomer"] = {"bogus": 1}
sp3 = tmp / "bad3.spec.json"; sp3.write_text(json.dumps(bad3))
r3 = subprocess.run([str(ACTINV), "run", str(sp3), str(tmp / "bad3.out.json")],
                    cwd=ROOT, text=True, capture_output=True, timeout=60)
check("unknown isomer field rejected", r3.returncode != 0)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps(
    {"pass": not failed, "n": len(checks), "failed": failed},
    indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
