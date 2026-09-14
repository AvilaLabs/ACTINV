#!/usr/bin/env python3
"""P26 bounded prototype: in-process campaign runner for ACTINV v1.0.1.

Runs the frozen W-CAMPAIGN case list through `actinv.run` inside one process,
amortizing interpreter startup and module import across cases. This is a
prototype: nothing under `prototypes/` is imported, linked or packaged by any
production path. Run it with the installed actinv interpreter:

    /home/connoravila/.local/share/pipx/venvs/actinv/bin/python \
        prototypes/p26_campaign.py SPECS_DIR OUT.jsonl [--start N] [--count M]

Each line of OUT.jsonl is one case record: case id, wall seconds, output
SHA-256 and the contract response summary extracted from the result.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import actinv


def response_summary(result: dict) -> dict:
    steps = result.get("steps", [])
    out = []
    for step in steps:
        act = step.get("activity_Bq_per_g") or {}
        top5 = sorted(act, key=lambda n: -(act.get(n) or 0.0))[:5]
        photon = step.get("photon_source") or {}
        out.append({
            "t_s": step.get("t_s"),
            "total_activity_bq_per_g": sum(act.values()),
            "decay_heat_w_per_g": step.get("heat_W_per_g"),
            "photon_source_total_per_g": sum(photon.get("values", []) or []),
            "top5_nuclides_by_activity": top5,
        })
    return {"per_step": out}


def main() -> int:
    specs_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    start = int(sys.argv[sys.argv.index("--start") + 1]) if "--start" in sys.argv else 0
    count = int(sys.argv[sys.argv.index("--count") + 1]) if "--count" in sys.argv else None

    spec_files = sorted(p for p in specs_dir.glob("*.json") if ".out." not in p.name)
    selected = spec_files[start:] if count is None else spec_files[start:start + count]

    done = set()
    if out_path.is_file():
        for line in out_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["case"])

    with out_path.open("a") as out:
        for spec_path in selected:
            case = spec_path.stem
            if case in done:
                continue
            spec_text = spec_path.read_text()
            t0 = time.perf_counter()
            try:
                result_text = actinv.run(spec_text)
                wall = time.perf_counter() - t0
                result = json.loads(result_text)
                record = {
                    "case": case, "mode": "batched_inprocess", "tool": "actinv_v1_0_1",
                    "wall_s": wall, "ok": True,
                    "output_sha256": hashlib.sha256(result_text.encode()).hexdigest(),
                    "response": response_summary(result),
                }
            except Exception as exc:
                wall = time.perf_counter() - t0
                record = {"case": case, "mode": "batched_inprocess", "tool": "actinv_v1_0_1",
                          "wall_s": wall, "ok": False, "failure": "actinv_error",
                          "error": str(exc)[:400]}
            out.write(json.dumps(record, sort_keys=True) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
