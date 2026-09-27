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

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
print(f"{len(checks)-len(failed)}/{len(checks)} checks passed → {OUT}")
raise SystemExit(1 if failed else 0)
