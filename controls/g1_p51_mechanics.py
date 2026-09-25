#!/usr/bin/env python3
"""P51 G1 mechanics — protocol round-trip on the synthetic fixture:
round-trips, id echo, warm flag, malformed-input rejection without death,
stop/EOF clean exits, and worker survival after a failing spec.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p11_fixtures as fx  # noqa: E402
import p51_artifacts as p51a  # noqa: E402
import p51_worker as pw  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g1_p51_mechanics.json"
SCHEMA = pw.REQUEST_SCHEMA


def main() -> int:
    checks: list[str] = []
    problems: list[str] = []

    work = Path(tempfile.mkdtemp(prefix="p51-g1-", dir=ROOT / "target"))
    fixture = fx.make_fixture(work)
    spec = fx.specification(fixture, mode="trace", cram_order=16)

    w = pw.Worker()
    try:
        # round-trip + timing fields + id echo
        r1 = w.run(spec, 1)
        r2 = w.run(spec, 2)
        if not (r1.get("ok") and r1.get("id") == 1 and r2.get("id") == 2):
            problems.append("round-trip/id echo failed")
        elif "result" not in r1 or not isinstance(r1["timing_ms"], dict):
            problems.append("result/timing_ms missing")
        elif r1["timing_ms"].get("warm") is not False \
                or r2["timing_ms"].get("warm") is not True:
            problems.append("warm flag did not flip on repeat spec")
        checks.append("roundtrip_warm_flag")

        # malformed inputs must error but never kill the worker
        rejects = [
            {"not_a": "schema"},
            {"schema": "wrong-schema", "id": 3, "op": "run", "spec": spec},
            {"schema": SCHEMA, "id": "four", "op": "run", "spec": spec},
            {"schema": SCHEMA, "id": 4, "op": "run"},
            {"schema": SCHEMA, "id": 5, "op": "bogus"},
            {"schema": SCHEMA, "id": 6, "op": "run", "spec": spec,
             "surprise": True},
            {"schema": SCHEMA, "id": 7, "op": "run", "spec": {"spec": "nope"}},
        ]
        for i, bad in enumerate(rejects):
            r = w.request(bad)
            if r.get("ok") is not False or "error" not in r:
                problems.append(f"reject {i} not flagged: {r}")
                break
        else:
            checks.append("malformed_rejected")
        r = w.run(spec, 8)
        if not r.get("ok"):
            problems.append("worker died after malformed battery")

        # a spec that fails inside run() returns ok:false, worker survives
        bad_spec = dict(spec)
        bad_spec["material"] = {"mass_g": 1.0, "basis": "atoms_per_g",
                                "composition": {"Xx99": 1.0}}
        r = w.request({"schema": SCHEMA, "id": 9, "op": "run",
                       "spec": bad_spec})
        if r.get("ok") is not False:
            problems.append("failing spec not reported ok:false")
        r = w.run(spec, 10)
        if not r.get("ok") or r["timing_ms"].get("warm") is not True:
            problems.append("worker did not serve warm after a failure")
        checks.append("failure_survival")

        # stop handshake + clean exit
        code = w.stop(timeout_s=5.0)
        if code != 0:
            problems.append(f"stop exit code {code}")
        checks.append("stop_clean_exit")
    finally:
        if w.proc.poll() is None:
            w.kill()

    # EOF (no stop) must exit cleanly too
    proc = subprocess.Popen([str(ACTINV), "worker"], cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    proc.stdin.close()
    try:
        code = proc.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait()
        problems.append("worker did not exit on stdin EOF")
    else:
        if code != 0:
            problems.append(f"EOF exit code {code}")
    checks.append("eof_clean_exit")

    seal_path = ROOT / "results/g0_p51_seals.json"
    drift = None
    if seal_path.exists():
        seal = json.load(open(seal_path))
        current = p51a.verify()
        drift = sorted(r["path"] for n, r in current.items()
                       if r["sha256"] != seal["artifacts"].get(n, {})
                       .get("sha256"))
    record = {"schema": "actinv-p51-g1-1", "checks": checks,
              "problems": problems, "sealed_drift": drift,
              "pass": not problems and not drift}
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"], "checks": checks,
                      "problems": problems}))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
