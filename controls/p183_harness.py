#!/usr/bin/env python3
"""P183 harness (protocols/ACTINV-P183_PROTOCOL.md): P182's harness, unchanged in builds, inputs, problems, order,
limits and timing, with its work directory at target/p183-work, its record at results/p183_record.json, and the
comparison at assemble done with check_p183's pruning-bound test.

  systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- \\
    env RAYON_NUM_THREADS=2 python3 controls/p183_harness.py {prepare|run|assemble [--delete-outputs]}
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p182_harness as h  # noqa: E402

SCHEMA = "actinv-p183-record-1"
h.WORK = h.MAIN / "target" / "p183-work"
h.TIMINGS = h.WORK / "timings.json"
h.RECORD = ROOT / "results" / "p183_record.json"
h.SCHEMA = SCHEMA


def main(argv=None):
    import check_p183  # noqa: E402  (routes the assemble comparison through P183's test)
    check_p183.patch()
    h.main(argv)


if __name__ == "__main__":
    main()
