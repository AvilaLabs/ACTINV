#!/usr/bin/env python3
"""D6c G1 — build-index product-coverage audit on real TENDL files.

Every activation row emits a product state (zap, emitted lfs); when no
target file exists for that state its population can decay but cannot
undergo secondary activation — the population-leak surface. The builder
now writes `product_coverage` into the index: emitted states, covered
count, missing count, and up to 200 named unbuilt products.

Gate recipe: stage Fe-56 alone → every emitted product is unbuilt;
stage Fe-56 + Mn-55 → exactly Mn-55 leaves the unbuilt set and joins the
covered set. Counts and names are asserted independently from the npz
rows themselves.

Requires the local TENDL-2025 tree (ACTINV_P19_DATA_ROOT or
~/nuclear-data); skips cleanly when absent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
DATA_ROOT = (
    Path(os.environ.get("ACTINV_P19_DATA_ROOT", Path.home() / "nuclear-data"))
    / "tendl-2025/files/n"
)
DECAY = ROOT / "actinv-data/v1.1.0/decay/endf-b-viii-0_decay.dat"
OUT = ROOT / "results/g1_d6c_product_coverage.json"

ELEMENTS = [
    "", "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg",
    "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr",
    "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "In", "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf",
    "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po",
    "At", "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U",
]

CHECKS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""))


def nuclide_name(za: int, liso: int) -> str:
    sym = ELEMENTS[za // 1000]
    return f"{sym}{za % 1000}" + (f"m{liso}" if liso > 0 else "")


def emitted_products(npz_path: Path) -> set[tuple[int, int]]:
    rows = np.load(npz_path)["rows"]
    return {
        (int(r[2]), int(r[3]))
        for r in rows
        if int(r[2]) >= 1000 and int(r[3]) >= 0
    }


def build(work: Path, files: list[str], tag: str) -> tuple[dict, Path]:
    src = work / f"src_{tag}"
    src.mkdir()
    for f in files:
        (src / f).write_bytes((DATA_ROOT / f).read_bytes())
    npz = work / f"{tag}.npz"
    proc = subprocess.run(
        [str(ACTINV), "build-library", str(src), str(npz),
         "--decay", str(DECAY)],
        capture_output=True, text=True, timeout=1800,
    )
    assert proc.returncode == 0, f"build-library {tag}: {proc.stderr[-600:]}"
    index = json.loads(npz.with_name(npz.stem + "_index.json").read_text())
    return index, npz


def main() -> int:
    fe = "n-Fe056.tendl"
    mn55 = "n-Mn055.tendl"
    if not (DATA_ROOT / fe).exists() or not (DATA_ROOT / mn55).exists():
        print(f"SKIP: TENDL-2025 sources not found under {DATA_ROOT}")
        return 0
    if not DECAY.exists():
        print(f"SKIP: decay file {DECAY} not found")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="d6c_g1_", dir=ROOT / "target"))

    idx_a, npz_a = build(tmp, [fe], "fe_only")
    idx_b, npz_b = build(tmp, [fe, mn55], "fe_mn55")

    cov_a = idx_a["product_coverage"]
    cov_b = idx_b["product_coverage"]

    check("product_coverage emitted (Fe-only)", cov_a is not None)
    check("product_coverage emitted (Fe+Mn55)", cov_b is not None)

    emitted_a = emitted_products(npz_a)
    emitted_b = emitted_products(npz_b)

    check("emitted count matches npz rows (Fe-only)",
          cov_a["product_states_emitted"] == len(emitted_a),
          f"{cov_a['product_states_emitted']} vs {len(emitted_a)}")
    check("emitted count matches npz rows (Fe+Mn55)",
          cov_b["product_states_emitted"] == len(emitted_b),
          f"{cov_b['product_states_emitted']} vs {len(emitted_b)}")

    # Fe-56 alone: every emitted state is unbuilt (the target itself is
    # not a product).
    check("Fe-only: all emitted products unbuilt",
          cov_a["product_states_with_target"] == 0
          and cov_a["product_states_without_target"] == len(emitted_a),
          f"{cov_a['product_states_without_target']} unbuilt of "
          f"{cov_a['product_states_emitted']}")

    # Adding Mn-55: Mn55 leaves the unbuilt set; coverage counts match
    # the independently computed emitted-minus-targets difference.
    targets_b = {(t["za"], t["liso"]) for t in idx_b["targets"]}
    unbuilt_b = emitted_b - targets_b
    check("Mn55 leaves the unbuilt set",
          "Mn55" in cov_a["unbuilt_products"]
          and "Mn55" not in cov_b["unbuilt_products"])
    check("coverage counts equal independent recompute",
          cov_b["product_states_with_target"]
          == len(emitted_b & targets_b)
          and cov_b["product_states_without_target"] == len(unbuilt_b)
          and cov_b["unbuilt_products"]
          == [nuclide_name(z, l) for z, l in sorted(unbuilt_b)][:200],
          f"{len(unbuilt_b)} unbuilt")

    # Names emitted match the nuclide-name convention exactly.
    targets_a = {(t["za"], t["liso"]) for t in idx_a["targets"]}
    rebuilt_a = [
        nuclide_name(z, l)
        for (z, l) in sorted(emitted_a - targets_a)
    ]
    check("unbuilt_products names match independent recompute",
          cov_a["unbuilt_products"] == rebuilt_a[:200],
          f"{len(rebuilt_a)} missing states")

    check("audit note explains the leak semantics",
          "cannot undergo secondary activation" in cov_a["note"])

    return write_report()


def write_report() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for c in CHECKS if c["ok"])
    report = {
        "gate": "g1_d6c_product_coverage",
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
