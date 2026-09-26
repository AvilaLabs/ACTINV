#!/usr/bin/env python3
"""P61 G1 — mechanics: the completeness block emits only when outputs
explicitly lists "audit", carries an honest status, ranks defect classes,
per-instance rates reproduce the ledger maps, and parse rules hold.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p61_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g1_p61_mechanics.json"


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


checks = []
tmp = Path(tempfile.mkdtemp(prefix="p61_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)

# --- clean fixture: audit present, honest status -----------------------
res = p61_case.run(ACTINV, p61_case.spec(fx), tmp, "aud")
comp = res["ledger"].get("completeness")
check("completeness present under ledger", comp is not None)
check("status field honest enum",
      comp is not None and comp["status"] in ("complete", "incomplete"))
check("channel totals present and nonnegative",
      comp["reaction_channel"]["total_production_rate_coefficient_per_s"]
      > 0.0
      and comp["decay_channel"]
      ["total_decay_rate_coefficient_per_s"] >= 0.0)
check("status reflects defect inventory",
      (comp["status"] == "complete")
      == (not comp["reaction_channel"]["defects"]
          and not comp["decay_channel"]["defects"]
          and not comp["unquantified"]),
      f"status={comp['status']} defects="
      f"{len(comp['reaction_channel']['defects'])}/"
      f"{len(comp['decay_channel']['defects'])}")
for ch in ("reaction_channel", "decay_channel"):
    rates = [d["rate_coefficient_per_s"]
             for d in comp[ch]["defects"] if "rate_coefficient_per_s" in d]
    check(f"{ch} defects sorted desc", rates == sorted(rates,
                                                     reverse=True)
          or len(rates) <= 1)
    check(f"{ch} fractions consistent",
          all(d.get("fraction_of_channel_flow") is None
              or abs(d["fraction_of_channel_flow"]
                     * comp[ch]["total_production_rate_coefficient_per_s"
                              if ch == "reaction_channel"
                              else "total_decay_rate_coefficient_per_s"]
                     - d["rate_coefficient_per_s"]) < 1e-30
              for d in comp[ch]["defects"]))
    check(f"{ch} largest capped at 3",
          all(len(d.get("largest", [])) <= 3
              for d in comp[ch]["defects"]))

# --- defect-planted spec: dropping Mn56's decay record -----------------
res_d = p61_case.run(ACTINV, p61_case.spec_dropping_decay(fx, tmp), tmp,
                     "defect")
comp_d = res_d["ledger"]["completeness"]
check("defect spec -> incomplete", comp_d["status"] == "incomplete")
classes = [d["class"] for d in comp_d["reaction_channel"]["defects"]]
check("products_no_evaluated_decay_data surfaced", "products_no_evaluated_decay_data"
      in classes, json.dumps(classes))
pnd = next((d for d in comp_d["reaction_channel"]["defects"]
            if d["class"] == "products_no_evaluated_decay_data"), None)
ledger_map = res_d["ledger"]["products_no_evaluated_decay_data"]
check("defect rate matches ledger map sum",
      pnd is not None
      and pnd["rate_coefficient_per_s"] == sum(ledger_map.values())
      and pnd["instances"] == len(ledger_map))
check("largest names the dropped nuclide",
      pnd is not None and pnd["largest"][0]["name"] in ledger_map)
check("fraction of channel flow in (0,1]",
      pnd is not None and pnd["fraction_of_channel_flow"] is not None
      and 0.0 < pnd["fraction_of_channel_flow"] <= 1.0)

# --- absent audit -> byte-identical ------------------------------------
res_off = p61_case.run(ACTINV, p61_case.spec(fx, audit=False), tmp, "off")
res_off2 = p61_case.run(ACTINV, p61_case.spec(fx, audit=False), tmp, "off2")
check("no completeness key when absent",
      "completeness" not in res_off["ledger"])
res_off.pop("ms", None)
res_off2.pop("ms", None)
check("repeat run byte-comparable (ms excluded)",
      json.dumps(res_off, sort_keys=True)
      == json.dumps(res_off2, sort_keys=True))
# also: a spec with NO outputs list at all must not emit audit
bare = p61_case.spec(fx)
bare["options"].pop("outputs")
res_bare = p61_case.run(ACTINV, bare, tmp, "bare")
check("absent outputs list -> no completeness",
      "completeness" not in res_bare["ledger"])

# --- parse rejection ----------------------------------------------------
bad = p61_case.spec(fx)
bad["options"]["outputs"] = ["audit", "bogus-output"]
sp = tmp / "bad.spec.json"; sp.write_text(json.dumps(bad))
r = subprocess.run([str(ACTINV), "run", str(sp), str(tmp / "bad.out.json")],
                   cwd=ROOT, text=True, capture_output=True, timeout=60)
check("unknown outputs value rejected", r.returncode != 0
      and "outputs" in r.stderr, r.stderr[-200:])

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps(
    {"pass": not failed, "n": len(checks), "failed": failed},
    indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
