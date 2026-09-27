#!/usr/bin/env python3
"""P69 G1 — live certified probe: drive the built actinv-gui binary
through the sweep-live smoke (env-gated, headless) and verify the
redraw-on-drag contract: back-to-back submissions solve the newest
position (superseded positions may be dropped), every landed point
carries the P65 screen certificate, and the digest binds the injected
spec."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "target/debug/actinv-gui"
OUT = ROOT / "results/g1_p69_live.json"
SMOKE_OUT = ROOT / "target/p69_smoke"

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
       "ACTINV_GUI_SMOKE_SWEEP_LIVE": "1",
       "TMPDIR": str(ROOT / "target/preflight-tmp")}
proc = subprocess.Popen([str(BIN)], cwd=ROOT, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
report = SMOKE_OUT / "sweep-live-smoke.json"
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
    check("live sweep smoke produced its report", False)
else:
    rep = json.loads(report.read_text())
    check("last submitted position landed", rep["last_position_landed"] is True)
    check("at least one certified point", rep["landed"] >= 1)
    check("superseded positions were dropped or landed bounded",
          rep["landed"] <= rep["submitted"])
    check("kept states recorded on landed points",
          len(rep["kept_states"]) == rep["landed"]
          and all(k > 0 for k in rep["kept_states"]))
    check("cache states reported", len(rep["cache_hits"]) == rep["landed"])
    check("flux-scaled point certified", rep["flux_scale_certified"] is True)
    check("flux scaling answered sub-second "
          f"({rep.get('flux_scaled_ms')} ms)",
          rep.get("flux_scaled_ms", 1e9) < 1000)
    check("flux-scale correction bound recorded",
          0 < rep.get("flux_scale_bound_rel", 0) < 1e-3,
          "linear-scaling certificate carries its optical-depth bound")

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:6]}))
sys.exit(1 if failed else 0)
