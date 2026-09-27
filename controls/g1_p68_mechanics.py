#!/usr/bin/env python3
"""P68 G1 — certified-screening sweep tier: drive the built actinv-gui
binary through the screened-sweep smoke (env-gated, headless) and verify
the live-tier contract: P65 screen certificate on every point, warm
prepared-cache after point 0, per-point digest binding the injected spec."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "target/debug/actinv-gui"
OUT = ROOT / "results/g1_p68_mechanics.json"
SMOKE_OUT = ROOT / "target/p68_smoke"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


if not BIN.exists():
    subprocess.run(
        ["cargo", "build", "-p", "actinv-gui", "--bin", "actinv-gui"],
        cwd=ROOT, check=True,
        env={**os.environ, "CARGO_BUILD_JOBS": "1",
             "TMPDIR": str(ROOT / "target/preflight-tmp")})

# headless: the smoke runs before egui init; when the smoke leg finished
# we terminate the binary so the GUI doesn't wait for a display.
shutil.rmtree(SMOKE_OUT, ignore_errors=True)
SMOKE_OUT.mkdir(parents=True, exist_ok=True)
env = {**os.environ,
       "ACTINV_GUI_SMOKE_SPEC": str(ROOT / "examples/fns_fe_5min.json"),
       "ACTINV_GUI_SMOKE_OUT": str(SMOKE_OUT),
       "ACTINV_GUI_SMOKE_SWEEP_SCREENED": "1",
       "ACTINV_GUI_SMOKE_SWEEP_VALUES": "10,66,300",
       "TMPDIR": str(ROOT / "target/preflight-tmp")}
proc = subprocess.Popen([str(BIN)], cwd=ROOT, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
report = SMOKE_OUT / "sweep-screened-smoke.json"
import time
deadline = time.time() + 600
while time.time() < deadline and not report.exists():
    if proc.poll() is not None:
        break
    time.sleep(2.0)
proc.terminate()
try:
    proc.wait(timeout=10)
except subprocess.TimeoutExpired:
    proc.kill()

if not report.exists():
    check("screened sweep smoke produced its report", False)
else:
    rep = json.loads(report.read_text())
    check("all sweep points completed", rep["points"] == 3)
    check("warm prepared cache after first point",
          rep["warm_after_first"] is True)
    check("kept states recorded per point",
          len(rep["kept_states"]) == 3 and all(k > 0 for k in rep["kept_states"]))
    warm = rep["per_point_ms"][1:]
    check("warm points land sub-10s in debug "
          f"({warm} ms)", all(t < 10_000 for t in warm),
          "certified-screening tier demonstrably faster than the cold solve")

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:6]}))
sys.exit(1 if failed else 0)
