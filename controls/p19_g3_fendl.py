#!/usr/bin/env python3
"""P19 G3 FENDL-3.2c sanity leg.

Builds a shield-table artifact on the FENDL 211-group structure for the
materials present in both the P19 set and the FENDL-3.2c groupwise library
(Fe-56, Ag-107, W-186), then runs the sanity comparator on the published
FENDL GENDF files.

FENDL's .g files were produced by GROUPR with a different flux weighting
(iwt=11 vs our lethargy iwt=3) and their own sigma0 conventions — this leg
is a coarse sanity bound, not a qualification gate. It catches order-of-
magnitude or structural blunders, nothing finer.

Emits `results/g3_p19_fendl_sanity.json`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g3_p19_fendl_sanity.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
DATA_ROOT = Path(
    os.environ.get("ACTINV_P19_DATA_ROOT", Path.home() / "nuclear-data")
) / "tendl-2025/files/n"
FENDL_DIR = Path.home() / "nuclear-data/fendl-3.2c/group"
GROUPS = ROOT / "controls/p19_core/inputs/fendl_211_groups.json"
SANITY = ROOT / "controls/p19_core/tools/fendl_sanity.py"
CACHE = ROOT / "target/p19-shield-cache"
WORK = ROOT / "target/p19_g3_fendl"

# material -> (tendl eval file, fendl .g file)
MATERIALS = {
    "Fe-56": ("n-Fe056.tendl", "26Fe056.g"),
    "Ag-107": ("n-Ag107.tendl", "47Ag107.g"),
    "W-186": ("n-W186.tendl", "74W_186.g"),
}


def main() -> None:
    if not ACTINV.exists():
        raise SystemExit("release actinv binary missing — build first")
    if not GROUPS.is_file():
        raise SystemExit("fendl_211_groups.json missing")
    missing = [
        m for m, (e, g) in MATERIALS.items()
        if not (DATA_ROOT / e).exists() or not (FENDL_DIR / g).exists()
    ]
    if missing:
        raise SystemExit(f"missing inputs for {missing}")

    stage = WORK / "evals"
    WORK.mkdir(parents=True, exist_ok=True)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for _, (eval_name, _) in MATERIALS.items():
        shutil.copyfile(DATA_ROOT / eval_name, stage / eval_name)

    artifact = WORK / "shield_211.json"
    proc = subprocess.run(
        [str(ACTINV), "build-shielding", str(stage), str(artifact),
         "--groups", str(GROUPS), "--cache", str(CACHE)],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=3600, check=False,
    )
    print(proc.stdout)
    if proc.returncode != 0 or not artifact.exists():
        raise SystemExit("fendl-structure build-shielding failed")

    gfiles = [str(FENDL_DIR / g) for _, (_, g) in MATERIALS.items()]
    proc = subprocess.run(
        [sys.executable, str(SANITY), str(artifact), *gfiles, str(RESULT)],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=300,
    )
    if proc.returncode != 0:
        print(proc.stderr[-800:])
        raise SystemExit("fendl sanity comparator failed")
    record = json.loads(RESULT.read_text())
    print(json.dumps(record, indent=1)[:2000])


if __name__ == "__main__":
    main()
