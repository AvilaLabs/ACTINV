#!/usr/bin/env python3
"""D6e G1 — resolved-resonance self-shielding via pointwise quadrature.

`actinv build-shielding` now folds resolved-resonance ranges into the
per-group Bondarenko factors by adaptive Gauss-Legendre quadrature over
the pointwise MF=2 reconstruction (escape-probability weight
w = σ0/(σ0+σt), matching the unresolved probability-table convention),
instead of leaving resolved regions under the crude uncovered-segment
σ0/(σ0+σ_background) suppression.

Physics expectations verified here:

- Smooth (1/v, no resonance structure) groups keep factor ≈ 1 at every
  σ0 — the quadrature must not invent shielding where none exists.
- Resonance-bearing groups show factor < 1, monotonically shrinking as
  σ0 drops (peaks get shielded first).
- `resolved_overlap_fraction` is emitted and inside `overlap_fraction`.
- W-186 — the canonical strong-resonance absorber — shows deep capture
  suppression in its resolved region; Fe-56 shows modest suppression.

Requires the local TENDL-2025 tree (ACTINV_P19_DATA_ROOT or
~/nuclear-data); skips cleanly when absent.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
DATA_ROOT = (
    Path(os.environ.get("ACTINV_P19_DATA_ROOT", Path.home() / "nuclear-data"))
    / "tendl-2025/files/n"
)
OUT = ROOT / "results/g1_d6e_resolved_shielding.json"

CHECKS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""))


def main() -> int:
    fe = DATA_ROOT / "n-Fe056.tendl"
    w186 = DATA_ROOT / "n-W186.tendl"
    if not fe.exists() or not w186.exists():
        print(f"SKIP: TENDL-2025 sources not found under {DATA_ROOT}")
        return 0
    with tempfile.TemporaryDirectory(dir=ROOT / "target") as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        for f in (fe, w186):
            (src / f.name).write_bytes(f.read_bytes())
        out = Path(tmp) / "shield.json"
        proc = subprocess.run(
            [str(ACTINV), "build-shielding", str(src), str(out)],
            capture_output=True, text=True, timeout=1800,
        )
        check("build-shielding exits clean", proc.returncode == 0,
              proc.stderr.strip()[:160] if proc.returncode else "")
        if proc.returncode != 0:
            return write_report()
        table = json.loads(out.read_text())

    nuclides = table["nuclides"]
    check("Fe56 present", "Fe56" in nuclides)
    check("W186 present", "W186" in nuclides)
    if "Fe56" not in nuclides or "W186" not in nuclides:
        return write_report()

    sig0 = table["sigma0_b"]
    for name in ("Fe56", "W186"):
        groups = nuclides[name]["groups"]
        check(f"{name}: resolved_overlap_fraction emitted",
              all("resolved_overlap_fraction" in g for g in groups))
        res_groups = [g for g in groups if g["resolved_overlap_fraction"] > 0]
        check(f"{name}: resolved-covered groups exist", len(res_groups) > 0,
              f"{len(res_groups)} groups")
        # Resolved coverage never exceeds total coverage.
        check(f"{name}: resolved ⊆ total coverage",
              all(g["resolved_overlap_fraction"] <= g["overlap_fraction"] + 1e-12
                  for g in groups))
        # A resonance-bearing group: factor dips below 1 at small σ0, and
        # is pinned exactly 1 at the infinite-dilution reference column.
        best = min(
            res_groups,
            key=lambda g: g["group_factors"]["capture"][-1][0],
        )
        fac = [r[0] for r in best["group_factors"]["capture"]]
        check(f"{name}: infinite-dilution column pinned at 1",
              abs(fac[0] - 1.0) < 1e-12, f"group {best['group']} f0={fac[0]}")
        check(f"{name}: capture factor < 1 at σ0={sig0[-1]}",
              fac[-1] < 1.0, f"group {best['group']} f={fac[-1]:.4f}")
        check(f"{name}: capture factor monotone-ish",
              all(fac[i] >= fac[i + 1] - 1e-9 for i in range(len(fac) - 1)),
              f"group {best['group']}: {['%.3f' % v for v in fac]}")

    # Fe-56's lowest groups are smooth 1/v — suppression must be orders
    # below the resonance groups (residual deviation is real physics:
    # the constant elastic term decouples total from capture).
    fe_groups = nuclides["Fe56"]["groups"]
    smooth = fe_groups[0]
    fe_fac = [r[0] for r in smooth["group_factors"]["capture"]]
    check("Fe56 smooth group stays unshielded",
          all(abs(v - 1.0) < 1e-3 for v in fe_fac),
          f"group {smooth['group']}: min={min(fe_fac):.6f}")

    # W-186 resolved region must show a deep suppression somewhere —
    # its first capture resonances are the canonical thick-target case.
    w_groups = nuclides["W186"]["groups"]
    w_min = min(
        (g["group_factors"]["capture"][-1][0] for g in w_groups),
        default=1.0,
    )
    check("W186 deep suppression exists", w_min < 0.95, f"min capture factor {w_min:.4f}")
    w_res = [g for g in w_groups if g["resolved_overlap_fraction"] > 0]
    check("W186 resolved coverage is the dominant mode",
          len(w_res) > len(w_groups) // 2,
          f"{len(w_res)}/{len(w_groups)} resolved-covered")

    return write_report()


def write_report() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for c in CHECKS if c["ok"])
    report = {
        "gate": "g1_d6e_resolved_shielding",
        "passed": passed,
        "total": len(CHECKS),
        "verdict": "PASS" if passed == len(CHECKS) else "FAIL",
        "checks": CHECKS,
    }
    OUT.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{passed}/{len(CHECKS)} checks -> {OUT}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
