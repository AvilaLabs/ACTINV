#!/usr/bin/env python3
"""D6b G1 — curated decay overrides.

`decay.overrides` names an `actinv-decay-overrides-1` document whose
per-nuclide corrections are applied after the primary+fallback merge,
before chain construction, and recorded in the ledger + certificate.

The fixture's decay set carries Mn57 (ZA 25057, LISO 0, t½ = 3.0 s,
energies 0.7/1.1/0.2 MeV). A spec seeded with Mn57 and a zero-flux
schedule maintains the declared inventory each step while its decay
mass accumulates to leakage (the daughter Fe57 carries no record).
Closed-form predictions under that bookkeeping:

  activity   = λ · N₀          — half-life override scales it by λ'/λ
  leakage    = λ · N₀ · T      — identical scaling
  heat.total = λ · N₀ · ΣE     — deposited-energy override check
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
OUT = ROOT / "results/g1_d6b_decay_overrides.json"
T_S = 4.0  # single 4 s decay step — closed-form comparisons

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


tmp = Path(tempfile.mkdtemp(prefix="d6b_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)


def decay_spec(overrides: Path | None) -> dict:
    decay = {"primary": str(fx["decay"])}
    if overrides is not None:
        decay["overrides"] = str(overrides)
    return {
        "spec": "actinv-spec-1", "title": "d6b", "projectile": "neutron",
        "library": {"path": str(fx["library"]),
                    "sha256": sha(fx["library"])},
        "decay": decay,
        "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                     "composition": {"Mn57": 1.0}},
        "spectrum": {"structure": "custom",
                     "boundaries_eV": [1.0, 2.0, 3.0],
                     "flux_per_group": [0.0, 0.0], "total": 0.0,
                     "descending": False},
        "schedule": [{"dt": f"{T_S} s", "flux": 0.0}],
        "options": {"mode": "auto", "prune": "none",
                    "bmin_atoms_per_g": 0.0, "temperature_K": 293.6,
                    "cram_order": 16,
                    "outputs": ["inventory", "activity", "heat",
                                "ledger", "certificate"]},
    }


def run_spec(doc: dict, name: str) -> dict:
    sp = tmp / f"{name}.spec.json"
    out = tmp / f"{name}.out.json"
    sp.write_text(json.dumps(doc, sort_keys=True) + "\n")
    cli("run", str(sp), str(out))
    return json.loads(out.read_text())


def overrides(entries, name="ov.json") -> Path:
    p = tmp / name
    p.write_text(json.dumps(
        {"schema": "actinv-decay-overrides-1", "overrides": entries}))
    return p


# ── baseline ─────────────────────────────────────────────────────────
base = run_spec(decay_spec(None), "base")
st = base["steps"][-1]
A0 = st["activity_Bq_per_g"]["Mn57"]
H0 = st["heat_W_per_g"]["total"]
L0 = st["leakage_atoms_per_g"]
lam0 = math.log(2) / 3.0
check("baseline Mn57 activity present", A0 > 0.0, f"{A0:.4e}")
check("baseline heat present", H0 > 0.0, f"{H0:.4e}")
check("baseline applies no overrides",
      base["ledger"]["decay_overrides_applied"] == [])

# ── half-life override: 3 s → 6 s ────────────────────────────────────
ov_hl = overrides([{
    "nuclide": "Mn57", "half_life_s": 6.0,
    "source": "gate synthetic", "reason": "half-life patch check"}])
res_hl = run_spec(decay_spec(ov_hl), "hl")
st_hl = res_hl["steps"][-1]
A1 = st_hl["activity_Bq_per_g"]["Mn57"]
L1 = st_hl["leakage_atoms_per_g"]
# Seeded inventory is maintained, so activity is λ·N₀ — halving λ halves
# both the activity coefficient and the leakage rate exactly.
check("half-life override rescales activity exactly",
      math.isclose(A1 / A0, 0.5, rel_tol=1e-9),
      f"measured {A1/A0:.6f} expected 0.5")
check("half-life override rescales leakage rate exactly",
      math.isclose(L1 / L0, 0.5, rel_tol=1e-9),
      f"measured {L1/L0:.6f} expected 0.5")
check("override recorded in ledger",
      any("Mn57" in line and "half_life_s" in line
          for line in res_hl["ledger"]["decay_overrides_applied"]),
      f"{res_hl['ledger']['decay_overrides_applied']}")
check("override file hashed into certificate inputs",
      res_hl["certificate"]["inputs"]["decay_overrides"]["sha256"]
      == sha(ov_hl))

# ── deposited-energy override: em 1.1 → 2.2 MeV ──────────────────────
ov_en = overrides([{
    "nuclide": "Mn57", "e_em_eV": 2.2e6,
    "source": "gate synthetic", "reason": "energy patch check"}])
res_en = run_spec(decay_spec(ov_en), "en")
H1 = res_en["steps"][-1]["heat_W_per_g"]["total"]
# Total decay power scales by (0.7 + 2.2 + 0.2) / (0.7 + 1.1 + 0.2).
check("em-energy override rescales heat exactly",
      math.isclose(H1 / H0, 3.1e6 / 2.0e6, rel_tol=1e-9),
      f"measured {H1/H0:.6f} expected 1.55")

# ── refusal paths ────────────────────────────────────────────────────
bad_unknown = overrides([{
    "nuclide": "Xe999", "half_life_s": 5.0,
    "source": "gate synthetic", "reason": "unknown nuclide"}], "bad1.json")
sp = tmp / "bad1.spec.json"
sp.write_text(json.dumps(decay_spec(bad_unknown), sort_keys=True) + "\n")
cli("run", str(sp), str(tmp / "bad1.out.json"),
    expect_err="absent from the decay library")

bad_source = overrides([{
    "nuclide": "Mn57", "half_life_s": 6.0, "source": "",
    "reason": "x"}], "bad2.json")
sp = tmp / "bad2.spec.json"
sp.write_text(json.dumps(decay_spec(bad_source), sort_keys=True) + "\n")
cli("run", str(sp), str(tmp / "bad2.out.json"),
    expect_err="non-empty 'source'")

bad_modes = overrides([{
    "nuclide": "Mn57", "modes": [{"rtyp": 1.0, "rfs": 0.0,
                                  "q_eV": 1.0e6, "br": 0.5}],
    "source": "gate synthetic", "reason": "br sum"}], "bad3.json")
sp = tmp / "bad3.spec.json"
sp.write_text(json.dumps(decay_spec(bad_modes), sort_keys=True) + "\n")
cli("run", str(sp), str(tmp / "bad3.out.json"),
    expect_err="branching fractions sum")

bad_noop = overrides([{
    "nuclide": "Mn57",
    "source": "gate synthetic", "reason": "nothing"}], "bad4.json")
sp = tmp / "bad4.spec.json"
sp.write_text(json.dumps(decay_spec(bad_noop), sort_keys=True) + "\n")
cli("run", str(sp), str(tmp / "bad4.out.json"),
    expect_err="changes no decay field")

passed = sum(1 for c in checks if c["pass"])
report = {"schema": "actinv-control-g1-d6b-1", "pass": passed == len(checks),
          "checks": checks,
          "evidence": {"activity_ratio_halved_t12": A1 / A0,
                       "leakage_ratio": L1 / L0,
                       "heat_ratio_doubled_em": H1 / H0}}
OUT.write_text(json.dumps(report, indent=1) + "\n")
for c in checks:
    print(f"{'PASS' if c['pass'] else 'FAIL'} {c['name']} {c['detail']}")
print(f"{passed}/{len(checks)} checks passed → {OUT}")
sys.exit(0 if passed == len(checks) else 1)
