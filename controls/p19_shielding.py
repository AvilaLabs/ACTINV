#!/usr/bin/env python3
"""P19 G1 producer: build the deterministic shielding table on the test set.

Stages the six frozen TENDL-2025 ENDF-6 evaluations into a private directory,
runs ``actinv build-shielding`` on the frozen sigma0/temperature grids, and
records the emitted artifact's hash, coverage, and invariant probes
(infinite-dilution factor == 1, sigma0 monotonicity, finite group values).
The checker ``check_g1_p19.py`` re-derives content independently; this producer
only executes the build and records what was emitted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g1_p19_shield_table.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
DATA_ROOT = Path(
    os.environ.get("ACTINV_P19_DATA_ROOT", Path.home() / "nuclear-data")
) / "tendl-2025/files/n"
MATERIALS = ROOT / "controls/p19_core/inputs/materials.json"

TEST_SET = ("W-186", "Ag-107", "Ta-181", "Nb-93", "U-238", "Fe-56")
SIGMA0_B = [1.0e10, 1.0e5, 1.0e4, 1.0e3, 1.0e2, 1.0e1, 3.0, 1.0, 0.3, 0.1]
TEMPERATURES_K = [293.6, 600.0, 900.0, 1200.0]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    manifest = json.loads(MATERIALS.read_text(encoding="utf-8"))
    names = {entry["material"]: entry["file"] for entry in manifest["materials"]}
    missing = [m for m in TEST_SET if m not in names]
    if missing:
        raise SystemExit(f"materials manifest omits {missing}")

    # Fixed staging path: the artifact records the input path in provenance,
    # so a stable directory keeps the emitted table byte-deterministic.
    stage_root = ROOT / "target/p19_g1_stage"
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True)
    try:
        stage = stage_root / "evals"
        stage.mkdir()
        eval_sha = {}
        for material in TEST_SET:
            source = DATA_ROOT / names[material]
            if not source.exists():
                raise SystemExit(f"missing evaluation {source}")
            eval_sha[material] = sha256(source)
            shutil.copyfile(source, stage / source.name)

        out = stage_root / "shield.json"
        # Persistent per-evaluation checkpoints: reruns only recompute files
        # whose bytes changed (the cache keys on the source sha256).
        cache = ROOT / "target/p19-shield-cache"
        cache.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        completed = subprocess.run(
            [str(ACTINV), "build-shielding", str(stage), str(out),
             "--cache", str(cache)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=3600,
        )
        elapsed = time.monotonic() - start
        print(completed.stdout)
        if completed.returncode or not out.exists():
            raise SystemExit("build-shielding failed")

        # The artifact itself is the payload; persist it before assembling the
        # record so a record-writing defect cannot lose the built table.
        artifact_path = ROOT / "results/g1_p19_shield_artifact.json"
        artifact_path.write_bytes(out.read_bytes())
        artifact = json.loads(out.read_text(encoding="utf-8"))
        artifact_sha = sha256(out)

        nuclides = artifact["nuclides"]
        probes = {}
        for material in TEST_SET:
            key = material.replace("-", "")
            if key not in nuclides:
                raise SystemExit(f"artifact omits {material}")
            node = nuclides[key]["nodes"][0]
            total = node["bondarenko_b"]["total"]
            sig0 = artifact["sigma0_b"]
            # Infinite-dilution factor identity: bval(1e10) == sigi.
            inf_factor = total[0][0] / node["infinite_dilution_b"][0]
            monotone = all(
                total[i + 1][0] <= total[i][0] * (1.0 + 1e-9)
                for i in range(len(sig0) - 1)
            )
            finite = all(
                v == v and abs(v) != float("inf")
                for row in total
                for col in row
                for v in [col]
            )
            probes[material] = {
                "nodes": len(nuclides[key]["nodes"]),
                "groups": len(nuclides[key].get("groups", [])),
                "inf_factor": inf_factor,
                "monotone_in_sigma0": monotone,
                "finite": finite,
            }

        record = {
            "schema": "actinv-p19-g1-shield-1",
            "builder": completed.stdout.strip(),
            "wall_seconds": round(elapsed, 2),
            "artifact_sha256": artifact_sha,
            "eval_sha256": eval_sha,
            "sigma0_b": artifact["sigma0_b"],
            "temperatures_K": artifact["temperatures_K"],
            "format": artifact["format"],
            "probes": probes,
        }
        RESULT.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {RESULT} and {artifact_path} ({artifact_sha[:16]}…)")
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


if __name__ == "__main__":
    main()
