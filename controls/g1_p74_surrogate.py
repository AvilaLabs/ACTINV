#!/usr/bin/env python3
"""P74 G1 — certified surrogate: fit on certified-edge grid solves,
emit ŷ(x) ⊕ ε_cert with the documented bound formula.

Checks the honest core: an eval at a fresh interior point must bracket
a full solve at the same x within epsilon_cert; grid-node evals are
near-exact; out-of-domain evals refuse; the artifact carries the
training sha, holdout residual, and Lipschitz estimate."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g1_p74_surrogate.json"

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


tmp = Path(tempfile.mkdtemp(prefix="p74_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
bp = tmp / "base.json"
bp.write_text(json.dumps(base, sort_keys=True) + "\n")

surspec = {
    "schema": "actinv-surrogate-1",
    "base_spec": str(bp),
    "axes": [{"kind": "step_dt", "step": 2, "bounds": [0.3, 0.5],
              "points": 3}],
    "responses": [{"response": "heat.total", "time_s": 0.9,
                   "edge": "conservative_upper"}],
    "holdout_points": 3,
    "seed": 74,
}
sp = tmp / "sur.json"
sp.write_text(json.dumps(surspec, sort_keys=True) + "\n")

fit_dir = tmp / "fit"
cli("surrogate", "fit", str(sp), str(fit_dir))
artifact = json.loads((fit_dir / "surrogate.json").read_text())

check("artifact schema + solver", artifact.get("schema") == "actinv-surrogate-1"
      and "actinv-core" in artifact.get("solver", ""))
check("spec sha + certificate formula",
      bool(artifact.get("base_spec_sha256"))
      and "holdout_max_residual" in artifact["certificate"]["formula"])
r = artifact["responses"][0]
check("response carries values/residual/lipschitz",
      len(r["values"]) == 3 and r["holdout_max_residual"] >= 0
      and r["lipschitz_estimate"] >= 0)
check("holdout points recorded",
      len(artifact["training"]["holdout_x"]) == 3)


def evaluate(x):
    xp = tmp / "x.json"
    xp.write_text(json.dumps({"x": x}))
    ep = tmp / "ev.json"
    cli("surrogate", "eval", str(fit_dir / "surrogate.json"), str(xp), str(ep))
    return json.loads(ep.read_text())


# Grid node — surrogate ≈ node value, epsilon ~ residual only.
ev = evaluate([0.3])
sv = ev["responses"][0]
check("grid-node eval near-exact",
      abs(sv["surrogate"] - r["values"][0])
      <= max(1e-30, abs(r["values"][0]) * 1e-12),
      f"ŷ={sv['surrogate']:.4e} node={r['values'][0]:.4e}")
check("epsilon_cert bounded and positive",
      sv["epsilon_cert"] >= sv["holdout_max_residual"] >= 0)

# Interior point — band must contain a fresh certified solve at the x.
test_x = 0.42
ev2 = evaluate([test_x])
sv2 = ev2["responses"][0]
import copy
probe = copy.deepcopy(base)
probe["schedule"][2]["dt"] = f"{test_x} s"
sp2 = tmp / "probe.json"
sp2.write_text(json.dumps(probe, sort_keys=True) + "\n")
op = tmp / "probe_out.json"
cli("run", str(sp2), str(op))
true_edge = None
res = json.loads(op.read_text())
for st in res["steps"]:
    if abs(st["t_s"] - 0.9) < 0.2:
        rr = st.get("uncertainty", {}).get("responses", {}).get("heat.total")
        if rr:
            true_edge = rr["conservative_interval"][1]
        else:
            true_edge = st["responses"]["heat.total"]
        break
assert true_edge is not None, "probe solve missing heat.total"
lo, hi = sv2["band"]
check(
    "surrogate band brackets a fresh certified solve",
    lo - 1e-60 <= true_edge <= hi + 1e-60,
    f"band=[{lo:.4e},{hi:.4e}] true={true_edge:.4e} eps={sv2['epsilon_cert']:.3e}",
)

# Out-of-domain — refused, not extrapolated.
xp = tmp / "xbad.json"
xp.write_text(json.dumps({"x": [0.9]}))
cli("surrogate", "eval", str(fit_dir / "surrogate.json"), str(xp),
    expect_err="outside surrogate domain")
check("out-of-domain eval refused", True)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
print(f"{len(checks)-len(failed)}/{len(checks)} checks passed → {OUT}")
raise SystemExit(1 if failed else 0)
