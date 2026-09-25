#!/usr/bin/env python3
"""P51 G3 amortization — measured cold-vs-warm ledger on the corpus probe:
3 fresh-process `actinv run` solves vs one warm-up plus 3 warm solves in a
persistent worker. Wall time is ledgered per request with hardware context;
the warm median must beat the cold median by the protocol threshold (0.5x).
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p51_worker as pw  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g3_p51_amortization.json"
SPEC = ROOT / "examples/p51_battery/corpus_probe.json"
COLD_N = 3
WARM_N = 3
RATIO = 0.5


def cold_wall_ms() -> float:
    t0 = time.monotonic()
    out = subprocess.run([str(ACTINV), "run", str(SPEC),
                          "/dev/null"], cwd=ROOT, capture_output=True,
                         text=True, timeout=600)
    wall = (time.monotonic() - t0) * 1e3
    if out.returncode != 0:
        raise RuntimeError(f"cold run failed: {out.stderr[:300]}")
    return wall


def main() -> int:
    spec = json.load(open(SPEC))
    record: dict = {"schema": "actinv-p51-g3-1", "spec": str(SPEC),
                    "hardware": {"platform": platform.platform(),
                                 "machine": platform.machine(),
                                 "processor": platform.processor(),
                                 "memory_cap": "MemoryMax=4G (scope)"},
                    "cold_wall_ms": [], "warm": []}
    problems: list[str] = []

    for _ in range(COLD_N):
        record["cold_wall_ms"].append(round(cold_wall_ms(), 1))

    worker = pw.Worker()
    try:
        warm = []
        for i in range(WARM_N + 1):
            t0 = time.monotonic()
            r = worker.run(spec, i + 1)
            r["_client_wall_ms"] = (time.monotonic() - t0) * 1e3
            warm.append(r)
        record["warm"] = [
            {"solve_ms": round(r["timing_ms"]["solve_ms"], 1),
             "fingerprint_ms": round(r["timing_ms"]["fingerprint_ms"], 2),
             "client_wall_ms": round(r["_client_wall_ms"], 1),
             "warm": r["timing_ms"]["warm"], "ok": r.get("ok")}
            for r in warm]
    finally:
        worker.stop()

    if not all(r.get("ok") for r in warm):
        problems.append("a worker solve failed")
    if warm and warm[0]["timing_ms"]["warm"] is not False:
        problems.append("first worker solve was not cold")
    if any(r["timing_ms"]["warm"] is not True for r in warm[1:]):
        problems.append("a repeat solve was not warm")

    cold_med = statistics.median(record["cold_wall_ms"])
    # The honest comparison is client-observed wall: request send to response
    # read, which includes the worker's queue/parse/serialize — the same
    # scope the cold wall covers for `actinv run`.
    warm_med = statistics.median(
        [r["client_wall_ms"] for r in record["warm"][1:]])
    record["cold_median_ms"] = round(cold_med, 1)
    record["warm_median_solve_ms"] = round(warm_med, 1)
    record["ratio"] = round(warm_med / cold_med, 4)
    if warm_med > cold_med * RATIO:
        problems.append(f"warm median {warm_med:.0f}ms exceeds "
                        f"{RATIO}x cold median {cold_med:.0f}ms")
    record["problems"] = problems
    record["pass"] = not problems
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"], "cold_median_ms":
                      record["cold_median_ms"],
                      "warm_median_solve_ms": record["warm_median_solve_ms"],
                      "ratio": record["ratio"], "problems": problems}))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
