#!/usr/bin/env python3
"""P25c release covariance — sidecar matched to the patched artifact.

The covariance index is cryptographically bound to the activation
library it was built against (``activation_library_sha256``), so the
``tendl-2025-patched`` artifact needs its own sidecar.  This control
copies the surviving patched sources into a covariance stage and runs
bounded ``actinv build-covariance`` rounds; any file that fails closed
is ledgered and evicted exactly like the activation build.

Prerequisite: ``controls/p25c_release_build.py`` must have completed
(``results/p25c_release_build.json`` with ``npz_sha256``).  Resumable:
rerun freely; writes ``results/p25c_release_covariance.json``.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25c-release"
BUILD_RECORD = RESULTS / "p25c_release_build.json"
NPZ = WORK / "tendl-2025-patched-neutron-709g.npz"
STAGE = WORK / "cov-stage"
FAILED = WORK / "cov-stage-failed"
CACHE = WORK / "cov-cache"
ACTINV = ROOT / "target" / "release" / "actinv"
COV = WORK / "tendl-2025-patched-neutron-709g.cov.npz"
RECORD = RESULTS / "p25c_release_covariance.json"
ROUND_TIMEOUT_S = 14400.0

FAIL_RE = re.compile(r"(n-[A-Za-z]{1,3}\d{2,4}[a-z]?\.tendl)")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_round() -> dict:
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(ACTINV), "build-covariance", str(STAGE), str(NPZ),
             str(COV), "--workers", "2", "--cache", str(CACHE)],
            capture_output=True, text=True, timeout=ROUND_TIMEOUT_S,
            cwd=ROOT)
        return {"exit": proc.returncode,
                "seconds": round(time.monotonic() - t0, 3),
                "message": (proc.stderr.strip() + "\n"
                            + proc.stdout.strip())[-2000:]}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout",
                "seconds": round(time.monotonic() - t0, 3),
                "message": f"timeout {ROUND_TIMEOUT_S}s"}


def main() -> int:
    build = json.loads(BUILD_RECORD.read_text())
    if not build["artifact"]["npz_sha256"]:
        raise SystemExit("activation artifact missing -- run "
                         "controls/p25c_release_build.py first")
    for d in (STAGE, FAILED, CACHE):
        d.mkdir(parents=True, exist_ok=True)

    # stage the surviving patched sources (post-eviction stage dir)
    for src in sorted((WORK / "stage").iterdir()):
        dst = STAGE / src.name
        if not dst.exists() and not (FAILED / src.name).exists():
            shutil.copyfile(src, dst)

    rounds = json.loads(RECORD.read_text()).get("rounds", []) \
        if RECORD.is_file() else []
    while True:
        res = run_round()
        res["round"] = len(rounds) + 1
        res["staged"] = len(list(STAGE.iterdir()))
        res["failed_so_far"] = len(list(FAILED.iterdir()))
        rounds.append(res)
        print(f"round {res['round']}: exit {res['exit']} "
              f"({res['seconds']}s) staged={res['staged']} "
              f"failed={res['failed_so_far']}", flush=True)
        if res["exit"] == 0:
            break
        m = FAIL_RE.search(res["message"])
        if res["exit"] == "timeout" or not m:
            rounds.append({"fatal": res["message"][-1500:]})
            break
        bad = m.group(1)
        src = STAGE / bad
        if not src.exists():
            rounds.append({"fatal": f"unparseable failure: {bad}",
                           "message": res["message"][-1500:]})
            break
        (FAILED / bad).mkdir(exist_ok=True)
        detail = {"file": bad, "round": res["round"],
                  "message": res["message"][-800:],
                  "source_sha256": sha256(src)}
        (FAILED / bad / "failure.json").write_text(
            json.dumps(detail, indent=1) + "\n")
        shutil.move(str(src), str(FAILED / bad / bad))
        print(f"  evicted {bad}", flush=True)

    index = COV.with_name(COV.stem + "_index.json")
    failure_ledger = {}
    for d in sorted(FAILED.iterdir()):
        f = d / "failure.json"
        if f.is_file():
            failure_ledger[d.name] = json.loads(f.read_text())

    idx = json.loads(index.read_text()) if index.is_file() else None
    record = {
        "schema": "actinv-p25c-release-covariance-1",
        "activation_npz_sha256": build["artifact"]["npz_sha256"],
        "rounds": rounds,
        "staged_files": len(list(STAGE.iterdir())),
        "failed_files": len(failure_ledger),
        "failure_ledger": failure_ledger,
        "artifact": {
            "npz": str(COV),
            "npz_sha256": sha256(COV) if COV.is_file() else None,
            "index": str(index) if index.is_file() else None,
            "index_sha256": sha256(index) if index.is_file() else None,
            "files": idx.get("files") if idx else None,
            "files_with_mf33": idx.get("files_with_mf33") if idx else None,
            "mf33_sections": idx.get("mf33_sections") if idx else None,
            "components": idx.get("components") if idx else None,
            "activation_library_sha256":
                idx.get("activation_library_sha256") if idx else None,
        },
    }
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps(record["artifact"], indent=1))
    return 0 if record["artifact"]["npz_sha256"] else 1


if __name__ == "__main__":
    sys.exit(main())
