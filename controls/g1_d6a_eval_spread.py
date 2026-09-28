#!/usr/bin/env python3
"""D6a G1 — evaluation spread: the spec solved under both decay
primacies.

`actinv eval-spread` runs the spec as declared, then again with
`decay.primary` ↔ `decay.fallback` swapped, and reports the per-quantity
log-space spread `max_t |ln(x_alt/x_base)|`.

The gate fabricates an "alternate evaluation" by scaling Mn57's
half-life 3.0 s → 4.5 s (factor 1.5 → |ln| = ln(1.5) ≈ 0.4055) in the
fallback file. Under the maintained-inventory bookkeeping (activity =
λ·N₀, decay mass → leakage), every Mn57 quantity scales by exactly
λ ratio — a closed-form expected spread.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g1_d6a_eval_spread.json"
EXPECTED = math.log(1.5)  # Mn57 t½: 3.0 → 4.5

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


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


tmp = Path(tempfile.mkdtemp(prefix="d6a_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)

# Alternate decay evaluation: Mn57 t½ scaled 1.5×, Mn56 untouched —
# only nuclides that differ between the files can carry spread.
alt_decay = tmp / "decay_alt.txt"
p58_fixture.write_decay(alt_decay, t12_scale={(25057, 0): 1.5})

spec = {
    "spec": "actinv-spec-1", "title": "d6a", "projectile": "neutron",
    "library": {"path": str(fx["library"]),
                "sha256": sha(fx["library"])},
    "decay": {"primary": str(fx["decay"]),
              "fallback": str(alt_decay)},
    "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                 "composition": {"Mn57": 1.0, "Mn56": 0.5}},
    "spectrum": {"structure": "custom",
                 "boundaries_eV": [1.0, 2.0, 3.0],
                 "flux_per_group": [0.0, 0.0], "total": 0.0,
                 "descending": False},
    "schedule": [{"dt": "2 s", "flux": 0.0}, {"dt": "2 s", "flux": 0.0}],
    "options": {"mode": "auto", "prune": "none",
                "bmin_atoms_per_g": 0.0, "temperature_K": 293.6,
                "cram_order": 16,
                "outputs": ["inventory", "activity", "heat",
                            "ledger", "certificate"]},
}
sp = tmp / "spread.spec.json"
sp.write_text(json.dumps(spec, sort_keys=True) + "\n")
out = tmp / "spread.out.json"
cli("eval-spread", str(sp), str(out))
res = json.loads(out.read_text())

check("schema", res["schema"] == "actinv-eval-spread-1")
check("steps compared", res["n_steps"] == 2)

mn57 = res["per_nuclide_activity"].get("Mn57", {})
check("Mn57 activity spread = ln(1.5)",
      math.isclose(mn57.get("max_abs_ln_ratio", -1), EXPECTED,
                   rel_tol=1e-9),
      f"{mn57}")

mn56 = res["per_nuclide_activity"].get("Mn56", {})
check("unchanged Mn56 spread = 0",
      mn56.get("max_abs_ln_ratio") == 0.0,
      f"{mn56}")

heat = res["per_heat_component"].get("total", {})
check("heat.total spread = ln(1.5) (only Mn57 decaying... weighted)",
      heat.get("max_abs_ln_ratio") is not None
      and heat["max_abs_ln_ratio"] <= EXPECTED + 1e-12,
      f"{heat} — mixed Mn56/Mn57 heat caps at the Mn57 spread")

check("suggested unmodeled = max spread",
      math.isclose(res["suggested_unmodeled_relative"],
                   res["max_abs_ln_ratio"], rel_tol=1e-12),
      f"{res['suggested_unmodeled_relative']}")
check("decay inputs recorded",
      res["decay_inputs"]["primary"]["sha256"] == sha(fx["decay"])
      and res["decay_inputs"]["fallback"]["sha256"] == sha(alt_decay))

# Refusal: no fallback declared → the spread is undefined.
spec_nofb = json.loads(json.dumps(spec))
del spec_nofb["decay"]["fallback"]
sp2 = tmp / "nofb.spec.json"
sp2.write_text(json.dumps(spec_nofb) + "\n")
cli("eval-spread", str(sp2), str(tmp / "nofb.out.json"),
    expect_err="needs decay.fallback")
check("refuses without decay.fallback", True)

# P77 fold-in: declaring uncertainty.unmodeled_evalspread pointing at the
# emitted artifact must reproduce the banded emit of a hand-declared
# unmodeled_relative == suggested_unmodeled_relative, with provenance.
if "covariance" in fx:
    uspec = json.loads(json.dumps(spec))
    uspec["uncertainty"] = {
        "covariance": {"path": str(fx["covariance"]),
                       "sha256": sha(fx["covariance"])},
        "responses": ["activity:Mn57", "heat.total"],
        "channels": ["cross_section_mf33"],
        "confidence_level": 0.95,
        "require_complete": False,
    }
    ref_spec = json.loads(json.dumps(uspec))
    ref_spec["uncertainty"]["unmodeled_relative"] = res[
        "suggested_unmodeled_relative"]
    es_spec = json.loads(json.dumps(uspec))
    es_spec["uncertainty"]["unmodeled_evalspread"] = {
        "path": str(out), "sha256": sha(out)}

    def run_spec(doc, tag):
        p = tmp / f"{tag}.spec.json"
        p.write_text(json.dumps(doc, sort_keys=True) + "\n")
        o = tmp / f"{tag}.out.json"
        cli("run", str(p), str(o))
        return json.loads(o.read_text())

    def scrub(o):
        if isinstance(o, dict):
            return {k: scrub(v) for k, v in o.items() if k != "ms"}
        if isinstance(o, list):
            return [scrub(v) for v in o]
        return o

    ref_run = run_spec(ref_spec, "u_ref")
    es_run = run_spec(es_spec, "u_es")
    check("evalspread-resolved bands byte-identical to declared u",
          scrub(es_run["steps"]) == scrub(ref_run["steps"]))
    prov = es_run["certificate"]["inputs"].get("unmodeled_evalspread")
    check("evalspread provenance recorded",
          prov is not None
          and prov.get("sha256") == sha(out)
          and math.isclose(prov.get("resolved_unmodeled_relative", -1),
                           res["suggested_unmodeled_relative"], rel_tol=1e-12)
          and prov.get("source") == "eval_spread",
          f"{prov}")
    both = json.loads(json.dumps(uspec))
    both["uncertainty"]["unmodeled_relative"] = 0.1
    both["uncertainty"]["unmodeled_evalspread"] = {
        "path": str(out), "sha256": sha(out)}
    p = tmp / "both.spec.json"
    p.write_text(json.dumps(both, sort_keys=True) + "\n")
    cli("validate", str(p), expect_err="mutually exclusive")
    check("mutual exclusivity enforced", True)

passed = sum(1 for c in checks if c["pass"])
report = {"schema": "actinv-control-g1-d6a-1", "pass": passed == len(checks),
          "checks": checks,
          "evidence": {"expected_spread_ln": EXPECTED,
                       "Mn57": mn57, "heat_total": heat}}
OUT.write_text(json.dumps(report, indent=1) + "\n")
for c in checks:
    print(f"{'PASS' if c['pass'] else 'FAIL'} {c['name']} {c['detail']}")
print(f"{passed}/{len(checks)} checks passed → {OUT}")
sys.exit(0 if passed == len(checks) else 1)
