#!/usr/bin/env python3
"""P51 G2 hot/cold bit-identity — every battery spec's worker result equals
the fresh `actinv run` document modulo the declared exclusion fields, and a
repeat worker request reports warm:true with an identical document.
Battery: synthetic nominal (Groupwise path), synthetic uncertainty (Dense
path), and the committed corpus probe (real data).
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
import p51_worker as pw  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g2_p51_identity.json"
WORK = ROOT / "target/p51-battery"
EXCLUDED_KEYS = {"ms", "entry_point"}


def strip(value):
    """Remove declared wall-clock/provenance fields for identity compare."""
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items()
                if k not in EXCLUDED_KEYS}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def cold_run(spec: dict, path: Path) -> dict:
    path.write_text(json.dumps(spec))
    out = subprocess.run([str(ACTINV), "run", str(path)], cwd=ROOT,
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f"cold run failed: {out.stderr[:400]}")
    return json.loads(out.stdout)


def main() -> int:
    problems: list[str] = []
    details: dict = {}

    WORK.mkdir(parents=True, exist_ok=True)
    fixture = fx.make_fixture(WORK)
    battery = {
        "synthetic_nominal": fx.specification(
            fixture, mode="trace", cram_order=16, uncertainty=False),
        "synthetic_uq": fx.specification(
            fixture, mode="trace", cram_order=16),
        "corpus_probe": json.load(
            open(ROOT / "examples/p51_battery/corpus_probe.json")),
    }

    worker = pw.Worker()
    try:
        request_id = 0
        for name, spec in battery.items():
            spec_path = WORK / f"{name}.json"
            cold = cold_run(spec, spec_path)
            request_id += 1
            first = worker.run(spec, request_id)
            request_id += 1
            second = worker.run(spec, request_id)
            if not (first.get("ok") and second.get("ok")):
                problems.append(f"{name}: worker run failed "
                                f"{first.get('error') or second.get('error')}")
                continue
            w1, w2 = strip(first["result"]), strip(second["result"])
            c = strip(cold)
            entry = {
                "warm_first": first["timing_ms"]["warm"],
                "warm_second": second["timing_ms"]["warm"],
                "cold_equals_first": w1 == c,
                "warm_equals_cold": w2 == c,
            }
            details[name] = entry
            if w1 != c:
                problems.append(f"{name}: first worker result differs")
            if w2 != c:
                problems.append(f"{name}: warm worker result differs")
            if first["timing_ms"]["warm"] is not False:
                problems.append(f"{name}: first solve not cold")
            if second["timing_ms"]["warm"] is not True:
                problems.append(f"{name}: repeat solve not warm")
            (WORK / f"{name}_cold.json").write_text(
                json.dumps(cold, sort_keys=True))
            (WORK / f"{name}_warm.json").write_text(
                json.dumps(second["result"], sort_keys=True))
    finally:
        worker.stop()

    record = {"schema": "actinv-p51-g2-1", "battery": details,
              "problems": problems, "pass": not problems,
              "artifacts": str(WORK.relative_to(ROOT))}
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"], "battery": details,
                      "problems": problems}))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
