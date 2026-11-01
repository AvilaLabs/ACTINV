#!/usr/bin/env python3
"""P181 Amendment A harness (protocols/ACTINV-P181_AMENDMENT_A.md): P181's ACTINV arm again, on the ENDF/B-VIII.1
709-group library, at P181's six mesh sizes under P181's limits and rules. OpenMC is not rerun.

This is P181's harness (controls/p181_harness.py, unchanged) with its arm list narrowed to `actinv`, its record and
work directory moved, and the amendment's files added to the record's inputs. Every run is P181's `--child` run with
`--library` pointing at the ENDF/B-VIII.1 library. Launch it exactly like P181's harness, from the AGENTS.md scope:

  systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- \\
    env RAYON_NUM_THREADS=2 TMPDIR=$PWD/target/p181a-tmp \\
    ~/micromamba/envs/openmc0153dd/bin/python controls/p181a_harness.py --actinv-bin ACTINV \\
    --library ENDF_LIBRARY --xs-archive-sha256 <sha256 of the ENDF/B-VIII.1 archive>
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p181_harness as h  # noqa: E402

AMENDMENT = ROOT / "protocols" / "ACTINV-P181_AMENDMENT_A.md"
OUT = ROOT / "results" / "p181a_scale.json"
WORK = ROOT / "target" / "p181a-work"
ARMS = ["actinv"]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--child" in argv or "--probe" in argv or "--smoke" in argv:
        sys.exit("p181a_harness: runs the full campaign only (P181's harness runs each child)")
    if "--library" not in argv:
        sys.exit("p181a_harness: --library (the ENDF/B-VIII.1 709-group library) is required")
    extra = {"amendment": AMENDMENT, "amendment_harness": Path(__file__).resolve()}
    for name, path in extra.items():
        if not path.is_file():
            sys.exit(f"p181a_harness: missing {name}: {path}")
    digests = {name: {"path": str(path), "sha256": sha256(path)} for name, path in extra.items()}
    h.ARM_ORDER = ARMS
    code = h.main(argv + ["--out", str(OUT), "--work", str(WORK)])
    if code == 0 and OUT.is_file():
        record = json.loads(OUT.read_text())
        record["inputs"].update(digests)
        record["amendment"] = "A"
        h.write_atomic(OUT, record)
    return code


if __name__ == "__main__":
    sys.exit(main())
