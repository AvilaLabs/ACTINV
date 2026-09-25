#!/usr/bin/env python3
"""P50 G0 seal — binds protocol sha, opening commit, and every frozen
artifact hash. Emits results/g0_p50_seals.json. Evidence collected
after this point is sealed evidence.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p50_artifacts as p50a  # noqa: E402

OUT = ROOT / "results/g0_p50_seals.json"
ENVELOPE_S = 7200


def main() -> int:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=ROOT).stdout.strip()
    artifacts = p50a.verify()
    missing = {n: r["path"] for n, r in artifacts.items()
               if not r["present"]}
    if missing:
        print(json.dumps({"sealed": False, "missing": missing}))
        return 1

    seal = {
        "schema": "actinv-p50-seal-1",
        "sealed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime()),
        "opening_commit": commit,
        "protocol_sha256": artifacts["protocol"]["sha256"],
        "artifacts": {n: {"path": r["path"], "sha256": r["sha256"]}
                      for n, r in artifacts.items()},
        "envelope_s": ENVELOPE_S,
        "demonstration": "examples/optimize_ra_steel/opt_v2_winner.json "
                         "(P49 winner: Fe-9Cr, Ni 0, Mo 0, Nb 0.11328 wt%; "
                         "voi.top=20)",
        "spec_flag": {"uncertainty.voi.top": "1..256"},
    }
    OUT.write_text(json.dumps(seal, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"sealed": True, "commit": commit,
                      "protocol_sha256": seal["protocol_sha256"][:16]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
