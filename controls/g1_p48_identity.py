#!/usr/bin/env python3
"""P48 G1 — interactive-path identity. Runs the sealed release binary's
sweep smoke mode on the flagship workload; every generated spec's
worker-path result must equal the direct solver result on the identical
spec (without_timings equality). Also re-verifies artifact hashes.
Emits results/g1_p48_identity.json + the raw smoke report at
~/nuclear-data/p48-work/g1/sweep-smoke.json.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p48_artifacts as p48a  # noqa: E402

OUT = ROOT / "results/g1_p48_identity.json"
WORK = Path.home() / "nuclear-data/p48-work/g1"
BIN = ROOT / "target/release/actinv-gui"
VALUES = "0.5,1.0,2.0"


def main() -> int:
    seal = json.loads((ROOT / "results/g0_p48_seals.json").read_text())
    # Freeze check: every sealed artifact must be byte-identical.
    cur = p48a.verify()
    drift = {n: {"sealed": seal["artifacts"][n]["sha256"],
                 "actual": cur[n]["sha256"]}
             for n in seal["artifacts"]
             if cur[n]["sha256"] != seal["artifacts"][n]["sha256"]}
    if drift:
        OUT.write_text(json.dumps(
            {"pass": False, "artifact_drift": drift}, indent=2))
        return 1
    if not BIN.exists():
        OUT.write_text(json.dumps(
            {"pass": False, "error": f"release binary missing: {BIN}"}))
        return 1

    spec_dir = WORK / "specdir"
    spec_dir.mkdir(parents=True, exist_ok=True)
    data_link = spec_dir / "actinv-data"
    if not data_link.exists():
        data_link.symlink_to(ROOT / "actinv-data")
    spec = spec_dir / "spec.json"
    spec.write_text(
        (ROOT / "examples/fns_fe_5min.json").read_text())
    outdir = WORK / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update({
        "ACTINV_GUI_SMOKE_SPEC": str(spec),
        "ACTINV_GUI_SMOKE_OUT": str(outdir),
        "ACTINV_GUI_SMOKE_SWEEP": "1",
        "ACTINV_GUI_SMOKE_SWEEP_VALUES": VALUES,
        "ACTINV_GUI_SMOKE_MODEL_ONLY": "1",
        "TMPDIR": str(ROOT / "target/preflight-tmp"),
    })
    t0 = time.time()
    proc = subprocess.run(
        ["systemd-run", "--user", "--scope",
         "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
         "-p", "TasksMax=128", "-p", "CPUQuota=200%",
         "--", "env", *(f"{k}={v}" for k, v in env.items()
                        if k.startswith("ACTINV") or k == "TMPDIR"),
         str(BIN)],
        capture_output=True, text=True,
        timeout=seal["envelope_s"])
    elapsed = time.time() - t0
    report_path = outdir / "sweep-smoke.json"
    if proc.returncode != 0 or not report_path.exists():
        OUT.write_text(json.dumps({
            "pass": False,
            "error": "sweep smoke failed",
            "stderr": proc.stderr[-2000:],
            "elapsed_s": elapsed}, indent=2))
        return 1

    report = json.loads(report_path.read_text())
    result = {
        "schema": "actinv-p48-g1-1",
        "pass": bool(report.get("identity_all_worker_eq_direct")),
        "points": report.get("points"),
        "point_spec_sha256": report.get("point_spec_sha256"),
        "smoke_report": str(report_path),
        "elapsed_s": elapsed,
        "binary": "target/release/actinv-gui (release build)",
        "hardware": {
            "machine": platform.machine(),
            "system": platform.system(),
            "release": platform.release(),
            "cpu_model": platform.processor() or "unknown",
        },
        "cgroup": "MemoryMax=6G MemorySwapMax=0 TasksMax=128 CPUQuota=200%",
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": result["pass"],
                      "points": result["points"],
                      "elapsed_s": round(elapsed, 1)}))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
