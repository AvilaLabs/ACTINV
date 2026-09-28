#!/usr/bin/env python3
"""D5 mixture-assay gate — linear-combination measurements (dose-rate /
gross-activity shape) fused through the Kalman H-row update.

Checks:
  - forward value is exactly Σ cᵢ·nomᵢ and shares sum to 1
  - posterior per term equals the independent Kalman recompute
  - one-term mixture reduces byte-identically to a scalar fusion
  - induced correlations are negative off-diagonal, in (-1, 0)
  - emit-result writes every term's posterior band + mixture provenance
  - a measured combination far outside the forward band → conflict
  - malformed mixture docs are refused
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "target" / "release" / "actinv"
OUT = ROOT / "results" / "g1_d5_mixture.json"
checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def cli(*args, expect_err=None):
    r = subprocess.run([str(BIN), *args], capture_output=True, text=True)
    if expect_err is not None:
        assert r.returncode != 0, f"{args}: expected failure, got 0"
        assert expect_err in r.stderr, \
            f"{args}: stderr {r.stderr[-400:]} lacks '{expect_err}'"
        return None
    assert r.returncode == 0, f"{args}: {r.stderr[-800:]}"
    return r.stdout


tmp = Path(tempfile.mkdtemp(prefix="d5_mix_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
bp = tmp / "base.json"
bp.write_text(json.dumps(base, sort_keys=True) + "\n")
rp = tmp / "run.json"
cli("run", str(bp), str(rp))
res = json.loads(rp.read_text())

step = next(s for s in res["steps"] if abs(s["t_s"] - 0.9) < 0.2)
resps = step["uncertainty"]["responses"]
n57 = resps["activity:Mn57"]["nominal"]
s57 = resps["activity:Mn57"]["combined_standard_uncertainty"]
n57m = resps["activity:Mn57m1"]["nominal"]
s57m = resps["activity:Mn57m1"]["combined_standard_uncertainty"]
assert n57 > 0 and n57m > 0

# Two-term mixture: y = A(Mn57) + 0.5·A(Mn57m1), measured 40% high.
c1, c2 = 1.0, 0.5
y0 = c1 * n57 + c2 * n57m
meas = y0 * 1.4
s_m_rel = 0.02
mix = tmp / "mix.json"
mix.write_text(json.dumps({
    "schema": "actinv-assay-1",
    "time_s": 0.9,
    "value": meas,
    "standard_uncertainty": meas * s_m_rel,
    "mixture": [
        {"response": "activity:Mn57", "coefficient": c1},
        {"response": "activity:Mn57m1", "coefficient": c2},
    ],
}))
mo = tmp / "mix.out.json"
cli("assimilate", "--result", str(rp), "--assay", str(mix), "--out", str(mo))
m = json.loads(mo.read_text())

check("mixture record kinded", m.get("kind") == "mixture")
check("forward value is Σcᵢ·nomᵢ exactly",
      abs(m["measured_combination"]["forward_value"] - y0)
      <= y0 * 1e-12, f"{m['measured_combination']['forward_value']} vs {y0}")
terms = {t["response"]: t for t in m["terms"]}
check("shares sum to one and match cᵢnomᵢ/y₀",
      abs(sum(t["share_of_combination"] for t in m["terms"]) - 1.0) < 1e-12
      and abs(terms["activity:Mn57"]["share_of_combination"]
              - c1 * n57 / y0) < 1e-12)

# Independent Kalman recompute (ln-space H-row).
import math
s1, s2 = s57 / n57, s57m / n57m
h1, h2 = c1 * n57 / y0, c2 * n57m / y0
S = s_m_rel**2 + h1**2 * s1**2 + h2**2 * s2**2
nu = math.log(meas / y0)
k1 = s1**2 * h1 / S
post1 = n57 * math.exp(k1 * nu)
s1p = s1 * math.sqrt(1 - k1 * h1)
check("posterior matches independent Kalman recompute",
      abs(terms["activity:Mn57"]["posterior"] - post1) <= post1 * 1e-12
      and abs(terms["activity:Mn57"]["posterior_relative_standard_uncertainty"]
              - s1p) <= s1p * 1e-12,
      f"{terms['activity:Mn57']['posterior']:.6e} vs {post1:.6e}")

corr = m["induced_correlations"]["matrix"]
check("induced correlations negative off-diagonal, bounded",
      corr[0][0] == 1.0 and corr[1][1] == 1.0
      and -1.0 < corr[0][1] < 0.0
      and math.isclose(corr[0][1], corr[1][0], rel_tol=1e-12),
      f"{corr}")
check("verdict consistent when measurement inside forward band",
      m["verdict"] == "consistent")

# One-term mixture reduces to the scalar fusion exactly.
one = tmp / "one.json"
one.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9, "value": meas,
    "standard_uncertainty": meas * s_m_rel,
    "mixture": [{"response": "activity:Mn57", "coefficient": 1.0}]}))
oo = tmp / "one.out.json"
cli("assimilate", "--result", str(rp), "--assay", str(one), "--out", str(oo))
m1 = json.loads(oo.read_text())
scal = tmp / "scal.json"
scal.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9, "response": "activity:Mn57",
    "value": meas, "standard_uncertainty": meas * s_m_rel}))
so = tmp / "scal.out.json"
cli("assimilate", "--result", str(rp), "--assay", str(scal), "--out", str(so))
sc = json.loads(so.read_text())
check("one-term mixture ≡ scalar fusion exactly",
      abs(m1["terms"][0]["posterior"] - sc["update"]["posterior"])
      <= sc["update"]["posterior"] * 1e-12
      and abs(m1["terms"][0]["posterior_relative_standard_uncertainty"]
              - sc["update"]["posterior_relative_standard_uncertainty"])
      <= sc["update"]["posterior_relative_standard_uncertainty"] * 1e-12,
      f"{m1['terms'][0]['posterior']:.10e} vs {sc['update']['posterior']:.10e}")

# emit-result writes every term's posterior band.
mu = tmp / "mix_updated.json"
cli("assimilate", "--result", str(rp), "--assay", str(mix),
    "--emit-result", str(mu))
res2 = json.loads(mu.read_text())
st2 = next(s for s in res2["steps"] if abs(s["t_s"] - 0.9) < 0.2)
r57 = st2["uncertainty"]["responses"]["activity:Mn57"]
r57m = st2["uncertainty"]["responses"]["activity:Mn57m1"]
check("emit-result moves both term bands",
      abs(r57["nominal"] - terms["activity:Mn57"]["posterior"])
      <= terms["activity:Mn57"]["posterior"] * 1e-9
      and abs(r57m["nominal"] - terms["activity:Mn57m1"]["posterior"])
      <= terms["activity:Mn57m1"]["posterior"] * 1e-9)
check("mixture provenance stamped per response",
      r57["assimilation"]["kind"] == "mixture"
      and res2["assimilated"]["kind"] == "mixture")

# Conflict: measured combination far outside the forward band.
conf = tmp / "mix_conflict.json"
conf.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9,
    "value": y0 * 1e6, "standard_uncertainty": y0 * 1e4,
    "mixture": [{"response": "activity:Mn57", "coefficient": c1},
                {"response": "activity:Mn57m1", "coefficient": c2}]}))
co = tmp / "conflict.out.json"
cli("assimilate", "--result", str(rp), "--assay", str(conf), "--out", str(co))
cm = json.loads(co.read_text())
check("far-outside combination reads conflict", cm["verdict"] == "conflict",
      f"{cm['verdict']}")

# Refusal paths.
bad = tmp / "mix_bad.json"
bad.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9, "value": 1.0,
    "standard_uncertainty": 0.1,
    "mixture": [{"response": "no.such", "coefficient": 1.0}]}))
cli("assimilate", "--result", str(rp), "--assay", str(bad),
    expect_err="carries no uncertainty")
check("unfitted mixture term refused", True)

amb = tmp / "mix_amb.json"
amb.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9, "response": "heat.total",
    "value": 1.0, "standard_uncertainty": 0.1,
    "mixture": [{"response": "heat.total", "coefficient": 1.0}]}))
cli("assimilate", "--result", str(rp), "--assay", str(amb),
    expect_err="multiple measurement shapes")
check("scalar+mixture ambiguity refused", True)

badc = tmp / "mix_badc.json"
badc.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9, "value": 1.0,
    "standard_uncertainty": 0.1,
    "mixture": [{"response": "activity:Mn57"}]}))
cli("assimilate", "--result", str(rp), "--assay", str(badc),
    expect_err="coefficient")
check("missing coefficient refused", True)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
print(f"{len(checks)-len(failed)}/{len(checks)} checks passed → {OUT}")
raise SystemExit(1 if failed else 0)
