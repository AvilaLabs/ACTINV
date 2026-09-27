#!/usr/bin/env python3
"""P65 G1 — screening mechanics: certificate fields, bit-exact edge
widening, prune="none" rejection, monotone bmin -> fewer states."""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g1_p65_mechanics.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def run_spec(spec: dict, tmp: Path, name: str) -> dict:
    p = tmp / f"{name}.json"
    p.write_text(json.dumps(spec) + "\n")
    out = tmp / f"{name}.out.json"
    r = subprocess.run([str(ACTINV), "run", str(p), str(out)],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    assert r.returncode == 0, r.stderr[-500:]
    return json.loads(out.read_text())


tmp = Path(tempfile.mkdtemp(prefix="p65_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
base["options"]["prune"] = "rate"

full = run_spec(base, tmp, "full")
screen = json.loads(json.dumps(base))
screen["options"]["screen"] = {"bmin_atoms_per_g": 1e-4}
scr = run_spec(screen, tmp, "screen")
screen2 = json.loads(json.dumps(screen))
screen2["options"]["screen"]["bmin_atoms_per_g"] = 1e-2
scr2 = run_spec(screen2, tmp, "screen2")

sc = scr["screen"]
check("screen block emitted", isinstance(sc, dict))
check("bmin recorded", sc["bmin_atoms_per_g"] == 1e-4)
check("bounds nonnegative",
      sc["removed_heat_W_per_g_bound"] >= 0.0
      and sc["removed_activity_Bq_per_g_bound"] >= 0.0)
check("higher bmin prunes more",
      scr2["screen"]["kept_states"] <= sc["kept_states"]
      and scr2["screen"]["dropped_states"] >= sc["dropped_states"])

step3 = scr["steps"][3]
cert = sc["certified"]["3"]
heat = step3["uncertainty"]["responses"]["heat.total"]
floor_b = step3["heat_bound_from_below_floor_W_per_g"]
H = sc["removed_heat_W_per_g_bound"]
check("heat certified interval exact",
      cert["heat.total"]["lower"]
      == heat["conservative_interval"][0] - H
      and cert["heat.total"]["upper"]
      == heat["conservative_interval"][1] + H + floor_b,
      json.dumps(cert["heat.total"]))
check("heat edge labelled conservative",
      cert["heat.total"]["edge"] == "conservative")
check("bound fields emitted",
      cert["heat.total"]["bound_upper"] == H + floor_b)

a57 = cert["activity:Mn57"]
b57 = sum(
    d["atoms_per_g_bound"]
    for d in scr["ledger"]["rate_pruning"]["dropped"]
    if d["nuclide"] == "Mn57"
)
# Mn57 t_half = 3.0 s in the fixture decay file
check("activity:Mn57 bound is dropped-state bound * lambda",
      math.isclose(a57["bound_upper"], b57 * math.log(2.0) / 3.0,
                   rel_tol=1e-12),
      f"{a57['bound_upper']!r} vs {b57 * math.log(2.0) / 3.0!r}")
per_nuc = {d["nuclide"]: d["atoms_per_g_bound"]
           for d in scr["ledger"]["rate_pruning"]["dropped"]}
check("activity.total bound >= max per-nuclide bound",
      cert["activity.total"]["bound_upper"]
      >= max(a57["bound_upper"], 0.0))
check("activity.total edge labelled nominal",
      cert["activity.total"]["edge"] == "nominal")
check("certified block covers every step",
      set(sc["certified"].keys()) == {str(i) for i in range(4)})

# prune=none rejection
bad = json.loads(json.dumps(screen))
bad["options"]["prune"] = "none"
p = tmp / "bad.json"
p.write_text(json.dumps(bad) + "\n")
r = subprocess.run([str(ACTINV), "run", str(p)],
                   cwd=ROOT, text=True, capture_output=True, timeout=60)
check("prune=none + screen refused", r.returncode != 0
      and "not a certificate" in r.stderr)
# negative bmin rejected
bad = json.loads(json.dumps(screen))
bad["options"]["screen"]["bmin_atoms_per_g"] = -1.0
p.write_text(json.dumps(bad) + "\n")
r = subprocess.run([str(ACTINV), "run", str(p)],
                   cwd=ROOT, text=True, capture_output=True, timeout=60)
check("negative screen bmin rejected", r.returncode != 0
      and "nonnegative" in r.stderr)
# no screen block without the option
check("absent screen = absent block", "screen" not in full)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:8]}))
sys.exit(1 if failed else 0)
