#!/usr/bin/env python3
"""P102 G4 — determinism: repeated ALARA exports of the corpus document are
byte-identical directory contents (every file plus the index).
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p102_case as p102  # noqa: E402

RESULT = ROOT / "results/g4_p102_determinism.json"
CORPUS = ROOT / "results/p52_r2s_source.ndjson"


def dir_digest(d: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.iterdir())}


def main() -> int:
    checks = {}
    digests = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for rep in range(3):
            out = td / f"rep{rep}"
            r = p102.run_export_alara(CORPUS.read_text(), out, td)
            assert r.returncode == 0, r.stderr
            digests.append(dir_digest(out))

    checks["same_file_set_all_reps"] = all(
        set(d.keys()) == set(digests[0].keys()) for d in digests)
    checks["byte_identical_all_reps"] = all(d == digests[0] for d in digests)

    evidence = {"schema": "actinv-p102-g4-determinism-1",
                "pass": all(checks.values()), "checks": checks,
                "sha256": digests[0]}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"pass": evidence["pass"], "checks": checks,
                      "n_files": len(digests[0])}))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
