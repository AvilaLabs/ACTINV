#!/usr/bin/env python3
"""P57 G4 — determinism: repeated emits of the corpus document are
byte-identical across all three formats.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p57_case as p57  # noqa: E402

RESULT = ROOT / "results/g4_p57_determinism.json"
CORPUS = ROOT / "results/p52_r2s_source.ndjson"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    checks = {}
    digests = {}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for fmt in ("openmc", "mcnp", "serpent"):
            hs = []
            for rep in range(3):
                out = td / f"{fmt}.{rep}"
                r = subprocess.run(
                    [str(p57.ACTINV), "export-source", fmt,
                     str(CORPUS), str(out)],
                    capture_output=True, text=True)
                assert r.returncode == 0, r.stderr
                hs.append(sha(out))
            checks[f"{fmt}_byte_identical"] = len(set(hs)) == 1
            digests[fmt] = hs[0]

    evidence = {"schema": "actinv-p57-g4-determinism-1",
                "pass": all(checks.values()), "checks": checks,
                "sha256": digests}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps(evidence, indent=1, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
