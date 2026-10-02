#!/usr/bin/env python3
"""P102 shared case machinery — the synthetic actinv-r2s-source-1 fixture
and the export-source alara driver used by every P102 gate.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = ROOT / "target/debug/actinv"
ACTINV_RELEASE = ROOT / "target/release/actinv"

# shutdown time used for every gate that doesn't vary it deliberately
SHUTDOWN_T_S = 4000.0


def fixture_r2s() -> str:
    """A small actinv-r2s-source-1 document: three cells — mixed group
    sets (cell-alpha has two groups, cell-beta-ß one, overlapping on
    neither centroid), an all-zero cell, and a non-ASCII id (cell-beta-ß,
    German sharp s, two-byte UTF-8). All three share step_t_s so the
    cooling basis is well-defined. Deterministic."""
    lines = [
        {
            "record": "header",
            "schema": "actinv-r2s-source-1",
            "mesh_result_sha256": "aa" * 32,
            "spec_fingerprint_sha256": "bb" * 32,
            "canonical_flux_sha256": "cc" * 32,
            "step": 7,
            "band_semantics": {"note": "fixture"},
        },
        {
            "record": "cell",
            "ordinal": 0,
            "id": "cell-alpha",
            "bounds_cm": [[0.0, 1.0], [0.0, 2.0], [0.0, 3.0]],
            "volume_cm3": 2.0,
            "photons_s": 9.0,
            "step_t_s": 10000.0,
            "sigma_photons_s_independent": 0.5,
            "sigma_photons_s_conservative": 1.0,
            "groups": [
                {"centroid_eV": 100000.0, "photons_s": 4.0},
                {"centroid_eV": 1000000.0, "photons_s": 5.0},
            ],
        },
        {
            "record": "cell",
            "ordinal": 1,
            "id": "cell-beta-ß",
            "bounds_cm": [[1.0, 2.5], [0.5, 0.75], [-1.0, -0.5]],
            "volume_cm3": 3.0,
            "photons_s": 6.0,
            "step_t_s": 10000.0,
            "groups": [
                {"centroid_eV": 500000.0, "photons_s": 6.0},
            ],
        },
        {
            "record": "cell",
            "ordinal": 2,
            "id": "cell-gamma-zero",
            "bounds_cm": [[5.0, 6.0], [5.0, 6.0], [5.0, 6.0]],
            "volume_cm3": 1.0,
            "photons_s": 0.0,
            "step_t_s": 10000.0,
            "sigma_photons_s_independent": 0.0,
            "sigma_photons_s_conservative": 0.0,
            "groups": [],
        },
        {"record": "footer", "cell_count": 3, "total_photons_s": 15.0},
    ]
    return "\n".join(json.dumps(r) for r in lines) + "\n"


def _cells(doc_text: str) -> list:
    return [json.loads(l) for l in doc_text.splitlines()
            if json.loads(l).get("record") == "cell"]


def fixture_bad_volume() -> str:
    """cell-alpha's volume_cm3 degraded to zero."""
    recs = [json.loads(l) for l in fixture_r2s().splitlines() if l.strip()]
    for r in recs:
        if r.get("id") == "cell-alpha":
            r["volume_cm3"] = 0.0
    return "\n".join(json.dumps(r) for r in recs) + "\n"


def fixture_unequal_step_t_s() -> str:
    """cell-beta's step_t_s diverges from the rest."""
    recs = [json.loads(l) for l in fixture_r2s().splitlines() if l.strip()]
    for r in recs:
        if r.get("id") == "cell-beta-ß":
            r["step_t_s"] = 20000.0
    return "\n".join(json.dumps(r) for r in recs) + "\n"


def fixture_missing_step_t_s() -> str:
    """cell-gamma-zero carries no step_t_s at all."""
    recs = [json.loads(l) for l in fixture_r2s().splitlines() if l.strip()]
    for r in recs:
        if r.get("id") == "cell-gamma-zero":
            del r["step_t_s"]
    return "\n".join(json.dumps(r) for r in recs) + "\n"


def actinv_bin() -> Path:
    if ACTINV_RELEASE.exists():
        return ACTINV_RELEASE
    return ACTINV


def run_export_alara(doc_text: str, out_dir: Path, work: Path,
                     shutdown_t_s: float | None = SHUTDOWN_T_S,
                     extra_args: list | None = None) -> subprocess.CompletedProcess:
    src = work / "in.ndjson"
    src.write_text(doc_text)
    cmd = [str(actinv_bin()), "export-source", "alara", str(src), str(out_dir)]
    if shutdown_t_s is not None:
        cmd += ["--shutdown-t-s", repr(shutdown_t_s) if isinstance(shutdown_t_s, float)
                else str(shutdown_t_s)]
    if extra_args:
        cmd += extra_args
    return subprocess.run(cmd, capture_output=True, text=True)


def naive_sum(xs):
    """Left-to-right f64 fold — mirrors Rust `Iterator::sum` (Python 3.12+
    `sum()` is compensated and differs from Rust's left-to-right fold at
    the last ulp)."""
    t = 0.0
    for x in xs:
        t += x
    return t


def rust_fmt(x: float) -> str:
    """Replicate Rust `Display` for f64: shortest round-trip decimal, never
    e-notation, integral values print without a fraction (1.0 -> "1")."""
    if x == 0.0:
        import math
        return "-0" if math.copysign(1.0, x) < 0 else "0"
    r = repr(x)
    if "e" in r or "E" in r:
        from decimal import Decimal
        d = Decimal(r)
        s = format(d, "f")
        return s
    if r.endswith(".0"):
        return r[:-2]
    return r


def sanitize_alara_id(raw: str) -> str:
    """Python mirror of the Rust byte-wise sanitizer (decision 1): every
    byte outside ASCII [A-Za-z0-9._-] becomes one '_', including each byte
    of a multi-byte UTF-8 character."""
    out = []
    for b in raw.encode("utf-8"):
        ch = chr(b)
        if ch.isascii() and (ch.isalnum() or ch in "._-"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/p102_fixture.ndjson")
    out.write_text(fixture_r2s())
    print(out)
