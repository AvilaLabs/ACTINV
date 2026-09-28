#!/usr/bin/env python3
"""D5 G1 — assay assimilation: fold a measurement into a certified band.

`actinv assimilate` fuses the solver's propagated band with an assay's
declared lognormal uncertainty on ln-scale. A precise assay inside the
prior band shrinks it (Kalman gain ~1); an imprecise one barely moves
it (K ~0). An assay outside the prior band is stamped `conflict`, not
silently absorbed."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g1_d5_assimilate.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def cli(*args, expect_err=None):
    r = subprocess.run([str(ACTINV), *args], cwd=ROOT, text=True,
                       capture_output=True, timeout=600)
    if expect_err is not None:
        assert r.returncode != 0, f"expected failure, got {r.stdout[:300]}"
        assert expect_err in r.stderr or expect_err in r.stdout, \
            f"missing '{expect_err}': {r.stderr[-400:]}"
        return None
    assert r.returncode == 0, f"{args}: {r.stderr[-800:]}"
    return r.stdout


tmp = Path(tempfile.mkdtemp(prefix="d5_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
bp = tmp / "base.json"
bp.write_text(json.dumps(base, sort_keys=True) + "\n")
rp = tmp / "run.json"
cli("run", str(bp), str(rp))
res = json.loads(rp.read_text())

step = next(s for s in res["steps"] if abs(s["t_s"] - 0.9) < 0.2)
prior = step["uncertainty"]["responses"]["heat.total"]
nominal = prior["nominal"]
prior_lo, prior_hi = prior["conservative_interval"]
prior_su = prior["combined_standard_uncertainty"]


def assay(value, su, name):
    p = tmp / f"{name}.json"
    p.write_text(json.dumps({
        "schema": "actinv-assay-1", "response": "heat.total",
        "time_s": 0.9, "value": value, "standard_uncertainty": su}))
    op = tmp / f"{name}.out.json"
    cli("assimilate", "--result", str(rp), "--assay", str(p), "--out", str(op))
    return json.loads(op.read_text())


# Precise assay at the nominal — σ_assay << σ_prior → gain ~1 and the
# posterior tracks the measurement, not the model.
rel_m = 0.01
a = assay(nominal, nominal * rel_m, "precise")
u = a["update"]
check("precise assay gains ~1", u["kalman_gain"] > 0.9,
      f"K={u['kalman_gain']:.3f}")
check("precise assay shrinks band",
      u["posterior_relative_standard_uncertainty"]
      < prior["combined_standard_uncertainty"] / nominal)
check("posterior band brackets the assay",
      u["posterior_band"][0] <= nominal * (1 + 1e-9)
      and nominal * (1 - 1e-9) <= u["posterior_band"][1])
check("consistent verdict inside prior band", a["verdict"] == "consistent")

# Sloppy assay — gain ~0, posterior ≈ prior.
b = assay(nominal, nominal * 100.0, "sloppy")
u2 = b["update"]
check("sloppy assay gains ~0", u2["kalman_gain"] < 0.01,
      f"K={u2['kalman_gain']:.4f}")
check("sloppy assay leaves the band ~unchanged",
      abs(u2["posterior_relative_standard_uncertainty"]
          - prior_su / nominal) / (prior_su / nominal) < 0.05)

# Conflicting assay — outside the prior interval → named verdict.
lo_side = prior_lo * 0.02
c = assay(lo_side, lo_side * 0.01, "conflict")
check("conflict verdict outside prior band", c["verdict"] == "conflict",
      f"verdict={c['verdict']}")

# Error paths — schema guard + unfitted response.
bad = tmp / "bad.json"
bad.write_text(json.dumps({"schema": "wrong", "response": "heat.total",
                           "time_s": 0.9, "value": 1, "standard_uncertainty": 1}))
cli("assimilate", "--result", str(rp), "--assay", str(bad),
    expect_err="schema")
check("non-assay schema refused", True)

missing = tmp / "missing.json"
missing.write_text(json.dumps({"schema": "actinv-assay-1",
                               "response": "no.such.response",
                               "time_s": 0.9, "value": 1,
                               "standard_uncertainty": 1}))
cli("assimilate", "--result", str(rp), "--assay", str(missing),
    expect_err="carries no uncertainty")
check("unfitted response refused", True)

# Provenance — both input shas recorded.
check("provenance shas recorded",
      bool(a["provenance"]["result_sha256"])
      and bool(a["provenance"]["assay_sha256"]))

# --emit-result: the updated document is a valid run result carrying the
# posterior band — and it chains into a second assimilate (D5 sequential).
up = tmp / "updated.json"
cli("assimilate", "--result", str(rp), "--assay", str(tmp / "precise.json"),
    "--emit-result", str(up))
res2 = json.loads(up.read_text())
step2 = next(s for s in res2["steps"] if abs(s["t_s"] - 0.9) < 0.2)
post_resp = step2["uncertainty"]["responses"]["heat.total"]
check("emit-result band equals the posterior",
      abs(post_resp["conservative_interval"][0] - a["update"]["posterior_band"][0])
      <= a["update"]["posterior_band"][0] * 1e-9
      and abs(post_resp["nominal"] - a["update"]["posterior"]) < 1e-60)
check("emit-result stamps assimilation provenance",
      "assimilation" in post_resp and "assimilated" in res2)

# Sequential assimilation: a second precise assay fuses on the posterior.
# Value at the posterior nominal — inside the (narrowed) prior band.
seq = tmp / "seq_assay.json"
post_nominal = post_resp["nominal"]
seq.write_text(json.dumps({"schema": "actinv-assay-1",
                           "response": "heat.total", "time_s": 0.9,
                           "value": post_nominal,
                           "standard_uncertainty": post_nominal * 0.01}))
so = tmp / "seq.out.json"
cli("assimilate", "--result", str(up), "--assay", str(seq), "--out", str(so))
s = json.loads(so.read_text())
check("second assay shrinks the band further",
      s["update"]["posterior_relative_standard_uncertainty"]
      < a["update"]["posterior_relative_standard_uncertainty"],
      f"{s['update']['posterior_relative_standard_uncertainty']:.3e} vs "
      f"{a['update']['posterior_relative_standard_uncertainty']:.3e}")
check("chain verdict consistent",
      s["verdict"] == "consistent")

# --- Multi-nuclide assay ingest (D5 leg: one HPGe-style count, two lines) ---
# A single assay document carrying entries for activity:Mn57 and
# activity:Mn57m1 — the gamma-spectroscopy shape: one count, several
# nuclide activities, one measurement time.
step_m57 = step["uncertainty"]["responses"]["activity:Mn57"]
step_m57m = step["uncertainty"]["responses"]["activity:Mn57m1"]
nom57, nom57m = step_m57["nominal"], step_m57m["nominal"]
assert nom57 > 0 and nom57m > 0, "fixture responses must be positive"

multi = tmp / "multi.json"
multi.write_text(json.dumps({
    "schema": "actinv-assay-1",
    "time_s": 0.9,
    "entries": [
        {"response": "activity:Mn57", "value": nom57 * 0.9,
         "standard_uncertainty": nom57 * 0.02},
        {"response": "activity:Mn57m1", "value": nom57m * 1.1,
         "standard_uncertainty": nom57m * 0.02},
    ],
}))
mo = tmp / "multi.out.json"
cli("assimilate", "--result", str(rp), "--assay", str(multi), "--out", str(mo))
m = json.loads(mo.read_text())
check("multi-entry count reported", m["entries"] == 2 and len(m["updates"]) == 2)
by_resp = {u["response"]: u for u in m["updates"]}
check("each entry fused its own response",
      set(by_resp) == {"activity:Mn57", "activity:Mn57m1"}
      and by_resp["activity:Mn57"]["update"]["kalman_gain"] > 0.9
      and by_resp["activity:Mn57m1"]["update"]["kalman_gain"] > 0.9)
check("overall verdict aggregates (consistent)",
      m["verdict"] == "consistent")
post57 = by_resp["activity:Mn57"]["update"]["posterior"]
post57m = by_resp["activity:Mn57m1"]["update"]["posterior"]
check("posteriors driven toward each measurement",
      abs(post57 / nom57 - 0.9) < 0.05 and abs(post57m / nom57m - 1.1) < 0.05)

# emit-result applies ALL entries — both bands move in the issued result.
mu = tmp / "multi_updated.json"
cli("assimilate", "--result", str(rp), "--assay", str(multi),
    "--emit-result", str(mu))
res3 = json.loads(mu.read_text())
step3 = next(s for s in res3["steps"] if abs(s["t_s"] - 0.9) < 0.2)
check("emit-result moves every entry's band",
      abs(step3["uncertainty"]["responses"]["activity:Mn57"]["nominal"]
          - post57) < post57 * 1e-9
      and abs(step3["uncertainty"]["responses"]["activity:Mn57m1"]["nominal"]
          - post57m) < post57m * 1e-9
      and "assimilation" in step3["uncertainty"]["responses"]["activity:Mn57"]
      and len(res3["assimilated"]["entries"]) == 2)
check("untouched response stays at the prior",
      abs(step3["uncertainty"]["responses"]["heat.total"]["nominal"]
          - nominal) < nominal * 1e-9)

# A conflicting line inside a multi assay marks the whole document.
# (heat.total carries a positive log-scale band; the isomer bands in
# this fixture are linear intervals whose negative lower bound makes
# 'conflict' unreachable — a known band-shape asymmetry.)
conf = tmp / "multi_conflict.json"
conf.write_text(json.dumps({
    "schema": "actinv-assay-1",
    "time_s": 0.9,
    "entries": [
        {"response": "activity:Mn57", "value": nom57,
         "standard_uncertainty": nom57 * 0.02},
        {"response": "heat.total", "value": nominal * 0.02,
         "standard_uncertainty": nominal * 0.0004},
    ],
}))
co = tmp / "conflict.out.json"
cli("assimilate", "--result", str(rp), "--assay", str(conf), "--out", str(co))
cm = json.loads(co.read_text())
check("conflicting entry flags the whole assay",
      cm["verdict"] == "conflict"
      and {u["verdict"] for u in cm["updates"]} == {"consistent", "conflict"})

# Mixed-shape documents are refused as ambiguous.
amb = tmp / "ambig.json"
amb.write_text(json.dumps({
    "schema": "actinv-assay-1", "response": "heat.total",
    "time_s": 0.9, "value": 1.0, "standard_uncertainty": 1.0,
    "entries": [{"response": "heat.total", "value": 1.0,
                 "standard_uncertainty": 1.0}]}))
cli("assimilate", "--result", str(rp), "--assay", str(amb),
    expect_err="multiple measurement shapes")
check("scalar+entries ambiguity refused", True)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
print(f"{len(checks)-len(failed)}/{len(checks)} checks passed → {OUT}")
raise SystemExit(1 if failed else 0)
