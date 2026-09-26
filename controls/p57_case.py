#!/usr/bin/env python3
"""P57 shared case machinery — the synthetic actinv-r2s-source-1 fixture
and the export-source driver used by every P57 gate.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = ROOT / "target/debug/actinv"

# independent openmc python, when the env exists (G3 uses it for a real
# from_xml_element round-trip; absence is recorded, not fatal)
OPENMC_PY = Path("/home/connoravila/.local/share/mamba/envs/openmc/bin/python3")


def fixture_r2s() -> str:
    """A small actinv-r2s-source-1 document: two ordinary cells with mixed
    group counts, one zero-strength/zero-group cell. Deterministic."""
    lines = [
        {
            "record": "header",
            "schema": "actinv-r2s-source-1",
            "mesh_result_sha256": "aa" * 32,
            "spec_fingerprint_sha256": "bb" * 32,
            "canonical_flux_sha256": "cc" * 32,
            "step": 2,
            "band_semantics": {"note": "fixture"},
        },
        {
            "record": "cell",
            "ordinal": 0,
            "id": "cell-alpha",
            "bounds_cm": [[0.0, 1.0], [0.0, 2.0], [0.0, 3.0]],
            "volume_cm3": 6.0,
            "photons_s": 10.0,
            "sigma_photons_s_independent": 0.5,
            "sigma_photons_s_conservative": 1.0,
            "groups": [
                {"centroid_eV": 100000.0, "photons_s": 4.0},
                {"centroid_eV": 500000.0, "photons_s": 6.0},
                {"centroid_eV": 2000000.0, "photons_s": 0.0},
            ],
        },
        {
            "record": "cell",
            "ordinal": 1,
            "id": "cell-beta/2",
            "bounds_cm": [[1.0, 2.5], [0.5, 0.75], [-1.0, -0.5]],
            "volume_cm3": 0.375,
            "photons_s": 5.0,
            "groups": [
                {"centroid_eV": 300000.0, "photons_s": 5.0},
            ],
        },
        {
            "record": "cell",
            "ordinal": 2,
            "id": "cell-empty",
            "bounds_cm": [[5.0, 6.0], [5.0, 6.0], [5.0, 6.0]],
            "volume_cm3": 1.0,
            "photons_s": 0.0,
            "sigma_photons_s_independent": 0.0,
            "sigma_photons_s_conservative": 0.0,
            "groups": [
                {"centroid_eV": 400000.0, "photons_s": 0.0},
            ],
        },
        {"record": "footer", "cell_count": 3, "total_photons_s": 15.0},
    ]
    return "\n".join(json.dumps(r) for r in lines) + "\n"


def fixture_bad_bounds() -> str:
    """Same fixture with cell-beta given a degenerate bounds axis."""
    doc = json.loads(fixture_r2s().splitlines()[1])
    lines = fixture_r2s().splitlines()
    recs = [json.loads(l) for l in lines]
    recs[2]["bounds_cm"][0] = [1.0, 1.0]
    return "\n".join(json.dumps(r) for r in recs) + "\n"


def run_export(fmt: str, doc_text: str, out: Path) -> subprocess.CompletedProcess:
    work = out.parent
    src = work / "in.ndjson"
    src.write_text(doc_text)
    return subprocess.run(
        [str(ACTINV), "export-source", fmt, str(src), str(out)],
        capture_output=True, text=True)


def total_from_ndjson(path: Path) -> tuple:
    """Header/footer totals for checks."""
    recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    cells = [r for r in recs if r.get("record") == "cell"]
    total = sum(r.get("photons_s", 0.0) for r in cells)
    return recs, cells, total


def naive_sum(xs):
    """Left-to-right f64 fold — mirrors Rust `Iterator::sum` (Python 3.12+
    `sum()` uses compensated summation and differs at the last ulp)."""
    t = 0.0
    for x in xs:
        t += x
    return t


def probs(groups) -> list:
    """Mirror of normalized_probs: the exact ratios s_i/sum — no residual
    fudging; the double-precision sum is within a few ulp of 1.0."""
    if not groups:
        return []
    tot = naive_sum(s for _, s in groups)
    return [s / tot for _, s in groups]


def rust_fmt(x: float) -> str:
    """Replicate Rust `Display` for f64: shortest round-trip decimal, never
    e-notation, integral values print without a fraction (1.0 -> "1")."""
    if x == 0.0:
        import math
        return "-0" if math.copysign(1.0, x) < 0 else "0"
    r = repr(x)
    if "e" in r or "E" in r:
        # expand exponent form into fixed decimal — Display never uses
        # exponents
        from decimal import Decimal
        d = Decimal(r)
        s = format(d, "f")
        return s
    if r.endswith(".0"):
        return r[:-2]
    return r


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/p57_fixture.ndjson")
    out.write_text(fixture_r2s())
    print(out)
