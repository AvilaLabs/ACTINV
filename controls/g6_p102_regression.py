#!/usr/bin/env python3
"""P102 G6 — no regression: on the P57 fixture and on the corpus mesh
source, the OpenMC/MCNP/Serpent emits are byte-identical before and after
the ALARA change (reference built from master 24bfb1e). The full
`cargo test` passes for the touched crates (recorded separately in the
ledger; this script checks the byte-identity half).
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

RESULT = ROOT / "results/g6_p102_regression.json"
CORPUS = ROOT / "results/p52_r2s_source.ndjson"
REF_BIN = ROOT / "target/p102-g6-ref/target-ref/release/actinv"
CANDIDATE_BIN = ROOT / "target/release/actinv"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def emit_all(binary: Path, doc_text: str, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    src = work / "in.ndjson"
    src.write_text(doc_text)
    out = {}
    for fmt in ("openmc", "mcnp", "serpent"):
        o = work / f"out.{fmt}"
        r = subprocess.run([str(binary), "export-source", fmt, str(src), str(o)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        out[fmt] = sha(o)
    return out


def main() -> int:
    checks = {}
    if not REF_BIN.exists():
        checks["reference_binary_present"] = (
            f"skipped: {REF_BIN} absent — build the pre-P102 reference "
            "from master 24bfb1e first")
        evidence = {"schema": "actinv-p102-g6-regression-1", "pass": False,
                    "checks": checks}
        RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
        print(json.dumps(checks))
        return 1
    checks["reference_binary_present"] = True

    fixture = p57.fixture_r2s()
    digests = {}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ref_dir = td / "ref"
        cand_dir = td / "cand"
        ref_dir.mkdir()
        cand_dir.mkdir()

        for name, doc in (("p57_fixture", fixture), ("corpus", CORPUS.read_text())):
            ref = emit_all(REF_BIN, doc, ref_dir / name)
            cand = emit_all(CANDIDATE_BIN, doc, cand_dir / name)
            digests[name] = {"ref": ref, "candidate": cand}
            for fmt in ("openmc", "mcnp", "serpent"):
                checks[f"{name}_{fmt}_byte_identical"] = ref[fmt] == cand[fmt]

    evidence = {"schema": "actinv-p102-g6-regression-1",
                "pass": all(v is True for v in checks.values()),
                "checks": checks, "sha256": digests}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps(checks, indent=1, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
