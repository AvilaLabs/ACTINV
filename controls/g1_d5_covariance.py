#!/usr/bin/env python3
"""D5 covariance carry — the emitted joint state conditions later
assays on the same result: a measurement on one entangled member moves
its correlated siblings, exactly as a Kalman H-row update prescribes.

Checks:
  - a mixture fusion emits assimilated.state (responses, ln_mean,
    ln_covariance) — square, symmetric, SPD, and consistent with the
    written marginals
  - a scalar assay on one member conditions the joint state: the
    correlated sibling's marginal moves to the analytically recomputed
    posterior, and the summary lists it under jointly_moved
  - a member untouched by covariance stays put (gain exactly zero)
  - an entries assay on a carried state conditions every member jointly
  - a time-mismatched assay drops the covariance honestly
  - a malformed carried state is refused, not silently ignored
  - the scalar-only path emits no state (back-compat)
"""
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "target" / "release" / "actinv"
OUT = ROOT / "results" / "g1_d5_covariance.json"
checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def cli(*args, expect_err=None):
    r = subprocess.run([str(BIN), *args], capture_output=True, text=True)
    if expect_err is not None:
        assert r.returncode != 0, f"{args}: expected failure, got 0"
        assert expect_err in r.stderr, \
            f"{args}: stderr {r.stderr[-500:]} lacks '{expect_err}'"
        return None
    assert r.returncode == 0, f"{args}: {r.stderr[-800:]}"
    return r.stdout


def marginal(result, t_s, resp):
    step = next(s for s in result["steps"] if abs(s["t_s"] - t_s) < 0.01)
    r = step["uncertainty"]["responses"][resp]
    return r


def condition(state, meas_idx, meas, meas_su):
    """One H=e_i conditioning step on (ln_mean, ln_cov) — independent
    recompute of the Kalman update."""
    mu = list(state["ln_mean"])
    n = len(mu)
    cov = [list(row) for row in state["ln_covariance"]]
    s_m = meas_su / meas
    m = [cov[i][meas_idx] for i in range(n)]
    s_var = cov[meas_idx][meas_idx] + s_m * s_m
    nu = math.log(meas) - mu[meas_idx]
    gains = [mi / s_var for mi in m]
    mu = [mu[i] + gains[i] * nu for i in range(n)]
    cov = [[cov[i][j] - m[i] * m[j] / s_var for j in range(n)]
           for i in range(n)]
    return {"ln_mean": mu, "ln_covariance": cov, "gains": gains}


tmp = Path(tempfile.mkdtemp(prefix="d5_cov_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
bp = tmp / "base.json"
bp.write_text(json.dumps(base, sort_keys=True) + "\n")
rp = tmp / "run.json"
cli("run", str(bp), str(rp))
res = json.loads(rp.read_text())
resps = next(s for s in res["steps"]
             if abs(s["t_s"] - 0.9) < 0.2)["uncertainty"]["responses"]
n57, s57 = (resps["activity:Mn57"]["nominal"],
            resps["activity:Mn57"]["combined_standard_uncertainty"])
n57m, s57m = (resps["activity:Mn57m1"]["nominal"],
              resps["activity:Mn57m1"]["combined_standard_uncertainty"])

# ── Stage 1: mixture assay emits the joint state ──────────────────────
c1, c2 = 1.0, 0.5
y0 = c1 * n57 + c2 * n57m
meas = y0 * 1.4
mix = tmp / "mix.json"
mix.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9,
    "value": meas, "standard_uncertainty": meas * 0.02,
    "mixture": [{"response": "activity:Mn57", "coefficient": c1},
                {"response": "activity:Mn57m1", "coefficient": c2}],
}))
fused1 = tmp / "fused1.json"
cli("assimilate", "--result", str(rp), "--assay", str(mix),
    "--out", str(tmp / "mix.out.json"), "--emit-result", str(fused1))
r1 = json.loads(fused1.read_text())
st = r1["assimilated"]["state"]

check("mixture emits a joint state",
      st.get("responses") == ["activity:Mn57", "activity:Mn57m1"]
      and len(st["ln_mean"]) == 2
      and len(st["ln_covariance"]) == 2
      and all(len(row) == 2 for row in st["ln_covariance"]))
check("state agrees with written marginals",
      all(
          abs(math.exp(st["ln_mean"][i])
              - marginal(r1, 0.9, resp)["nominal"])
              <= marginal(r1, 0.9, resp)["nominal"] * 1e-12
          and abs(math.sqrt(st["ln_covariance"][i][i])
                  - marginal(r1, 0.9, resp)
                  ["relative_standard_uncertainty"])
              <= marginal(r1, 0.9, resp)
                 ["relative_standard_uncertainty"] * 1e-12
          for i, resp in enumerate(st["responses"])))

cov = st["ln_covariance"]
spd_2x2 = (cov[0][0] > 0 and cov[1][1] > 0
           and cov[0][0] * cov[1][1] - cov[0][1] * cov[0][1] > 0)
check("carried covariance is symmetric and SPD",
      abs(cov[0][1] - cov[1][0]) < 1e-300 and spd_2x2,
      f"cov={cov}")

# ── Stage 2: a scalar assay on one member pulls the sibling ───────────
sib_prior = marginal(r1, 0.9, "activity:Mn57m1")
sib_nom, sib_s = (sib_prior["nominal"],
                  sib_prior["relative_standard_uncertainty"])
meas2 = math.exp(st["ln_mean"][0]) * 0.8
a2 = tmp / "scalar2.json"
a2.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9,
    "response": "activity:Mn57",
    "value": meas2, "standard_uncertainty": meas2 * 0.05,
}))
fused2 = tmp / "fused2.json"
o2 = tmp / "scalar2.out.json"
cli("assimilate", "--result", str(fused1), "--assay", str(a2),
    "--out", str(o2), "--emit-result", str(fused2))
s2 = json.loads(o2.read_text())
r2 = json.loads(fused2.read_text())

expect = condition(st, 0, meas2, meas2 * 0.05)
sib_post = marginal(r2, 0.9, "activity:Mn57m1")
check("correlated sibling moves to the joint posterior",
      abs(sib_post["nominal"] - math.exp(expect["ln_mean"][1]))
          <= math.exp(expect["ln_mean"][1]) * 1e-12
      and abs(sib_post["nominal"] - sib_nom) > sib_nom * 1e-9,
      f"post={sib_post['nominal']:.6e} expected={math.exp(expect['ln_mean'][1]):.6e} prior={sib_nom:.6e}")
check("sibling posterior σ from the joint covariance",
      abs(sib_post["relative_standard_uncertainty"]
          - math.sqrt(expect["ln_covariance"][1][1]))
          <= math.sqrt(expect["ln_covariance"][1][1]) * 1e-12)
jm = s2.get("jointly_moved", {}).get("responses", [])
check("sibling listed under jointly_moved",
      "activity:Mn57m1" in jm, f"{jm}")
sib_stamp = sib_post.get("assimilation", {})
check("sibling stamped correlated, not measured",
      sib_stamp.get("kind") == "correlated"
      and "activity:Mn57" in sib_stamp.get("pulled_by", []))
st2 = r2["assimilated"]["state"]
cov2 = st2["ln_covariance"]
check("second state still SPD and consistent",
      cov2[0][0] * cov2[1][1] - cov2[0][1] * cov2[0][1] > -1e-30
      and abs(cov2[0][0] - expect["ln_covariance"][0][0])
          <= expect["ln_covariance"][0][0] * 1e-12
      and abs(cov2[0][1] - expect["ln_covariance"][0][1])
          <= max(abs(expect["ln_covariance"][0][1]), 1e-300) * 1e-9)

# A response that was never fused has zero covariance with the state —
# a scalar assay on it must not move the members.
ht_prior = marginal(r2, 0.9, "heat.total")
a3 = tmp / "scalar3.json"
a3.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9,
    "response": "heat.total",
    "value": ht_prior["nominal"] * 0.9,
    "standard_uncertainty": ht_prior["nominal"] * 0.9 * 0.05,
}))
fused3 = tmp / "fused3.json"
cli("assimilate", "--result", str(fused2), "--assay", str(a3),
    "--out", str(tmp / "scalar3.out.json"), "--emit-result", str(fused3))
r3 = json.loads(fused3.read_text())
sib3 = marginal(r3, 0.9, "activity:Mn57m1")
check("uncorrelated assay leaves carried members untouched",
      abs(sib3["nominal"] - sib_post["nominal"])
          <= sib_post["nominal"] * 1e-12,
      f"{sib3['nominal']:.6e} vs {sib_post['nominal']:.6e}")

# ── Entries assay on a carried state conditions every member ──────────
e_meas = {resp: marginal(r3, 0.9, resp)["nominal"] * 1.1
          for resp in ("activity:Mn57", "activity:Mn57m1")}
a4 = tmp / "entries.json"
a4.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": 0.9,
    "entries": [{"response": r, "value": v,
                 "standard_uncertainty": v * 0.05}
                for r, v in e_meas.items()],
}))
fused4 = tmp / "fused4.json"
o4 = tmp / "entries.out.json"
cli("assimilate", "--result", str(fused3), "--assay", str(a4),
    "--out", str(o4), "--emit-result", str(fused4))
r4 = json.loads(fused4.read_text())
# Sequential conditioning recompute over the carried state — the
# entries arrive in assay order: Mn57 first, then Mn57m1.
st3 = r3["assimilated"]["state"]
order = [st3["responses"].index(r) for r in ("activity:Mn57", "activity:Mn57m1")]
e4 = condition(st3, order[0], e_meas["activity:Mn57"],
               e_meas["activity:Mn57"] * 0.05)
e4b = condition({"ln_mean": e4["ln_mean"],
                 "ln_covariance": e4["ln_covariance"]},
                order[1], e_meas["activity:Mn57m1"],
                e_meas["activity:Mn57m1"] * 0.05)
check("entries on a carried state match sequential conditioning",
      all(
          abs(marginal(r4, 0.9, resp)["nominal"]
              - math.exp(e4b["ln_mean"][i]))
              <= math.exp(e4b["ln_mean"][i]) * 1e-12
          for i, resp in enumerate(st3["responses"])
          if resp in e_meas),
      f"{e4b['ln_mean']}")

# ── Time mismatch drops the covariance honestly ───────────────────────
other_t = next(s["t_s"] for s in res["steps"] if abs(s["t_s"] - 0.9) > 0.2)
a5 = tmp / "time.json"
a5.write_text(json.dumps({
    "schema": "actinv-assay-1", "time_s": other_t,
    "response": "activity:Mn57",
    "value": 1e-3, "standard_uncertainty": 1e-5,
}))
o5 = tmp / "time.out.json"
cli("assimilate", "--result", str(fused4), "--assay", str(a5),
    "--out", str(o5), "--emit-result", str(tmp / "fused5.json"))
s5 = json.loads(o5.read_text())
check("time-mismatched assay drops the carried covariance",
      "dropped" in s5.get("joint_prior", "")
      and "state" not in json.loads((tmp / "fused5.json").read_text())
                          .get("assimilated", {}))

# ── Malformed state refuses instead of silently ignoring ──────────────
bad = json.loads(fused2.read_text())
bad["assimilated"]["state"]["ln_covariance"][0][1] *= 2.0
bp2 = tmp / "tampered.json"
bp2.write_text(json.dumps(bad))
cli("assimilate", "--result", str(bp2), "--assay", str(a2),
    expect_err="assimilated.state")

# ── Back-compat: a scalar-only fusion writes no state ─────────────────
o6 = tmp / "plain.json"
cli("assimilate", "--result", str(rp), "--assay", str(a2),
    "--emit-result", str(tmp / "plain_fused.json"), "--out", str(o6))
check("scalar-only fusion emits no carried state",
      "state" not in json.loads((tmp / "plain_fused.json").read_text())
                       .get("assimilated", {}))

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(f"{len(checks) - len(failed)}/{len(checks)} checks passed → {OUT}")
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
sys.exit(1 if failed else 0)
