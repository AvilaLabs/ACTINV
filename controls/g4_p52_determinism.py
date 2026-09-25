#!/usr/bin/env python3
"""P52 G4 — determinism + resume under the P52 workload: the demo mesh spec
run twice must produce byte-identical output and an identical r2s document;
a truncated output resumed with resume:true must converge byte-identically.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p52_artifacts as p52a  # noqa: E402
import g3_p52_demo as demo  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g4_p52_determinism.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def run_mesh(spec_path: Path, out_path: Path, timeout: int = 3000):
    r = subprocess.run([str(BIN), "mesh", str(spec_path), str(out_path)],
                       capture_output=True, text=True, timeout=timeout)
    return r


def export(mesh: Path, step: int, out: Path):
    return subprocess.run([str(BIN), "export-r2s", str(mesh), str(step),
                           str(out)],
                          capture_output=True, text=True, timeout=600)


def main() -> int:
    problems = []
    checks = {}
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="p52-g4-", dir="target") as d:
        work = Path(d)
        flux_path = demo.build_flux(work)
        spec = demo.build_spec(flux_path)

        # --- leg A: two uninterrupted runs ----------------------------------
        spec_path = work / "spec.json"
        spec_path.write_text(json.dumps(spec, indent=1))
        a, b = work / "a.ndjson", work / "b.ndjson"
        if run_mesh(spec_path, a).returncode != 0:
            problems.append("first run failed")
        if run_mesh(spec_path, b).returncode != 0:
            problems.append("second run failed")
        sha_a, sha_b = sha256_file(a), sha256_file(b)
        checks["run_twice_identical"] = sha_a == sha_b

        ra, rb = work / "ra.ndjson", work / "rb.ndjson"
        export(a, demo.EMIT_STEP, ra)
        export(b, demo.EMIT_STEP, rb)
        checks["r2s_identical"] = sha256_file(ra) == sha256_file(rb)

        # --- leg B: truncate + resume ---------------------------------------
        spec_r = dict(spec)
        spec_r["resume"] = True
        spec_r_path = work / "spec_resume.json"
        spec_r_path.write_text(json.dumps(spec_r, indent=1))
        trunc = work / "trunc.ndjson"
        # cut after the header + first complete cell record
        lines = a.read_bytes().split(b"\n")
        n_keep = 2  # header + first cell (cell records are one line each)
        trunc.write_bytes(b"\n".join(lines[:n_keep]) + b"\n")
        r = run_mesh(spec_r_path, trunc)
        checks["resume_succeeds"] = r.returncode == 0
        if r.returncode == 0:
            checks["resume_identical"] = sha256_file(trunc) == sha_a
        else:
            checks["resume_identical"] = False
            problems.append(f"resume failed: {r.stderr[-800:]}")

    failed = [k for k, v in checks.items() if not v]
    result = {
        "pass": not failed and not problems,
        "checks": checks,
        "wall_s": time.monotonic() - t0,
        "problems": problems + failed,
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
