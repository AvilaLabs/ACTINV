#!/usr/bin/env python3
"""CB1-G2: rerun and compact the frozen identical-data ACTINV/ALARA control."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "cb1_alara.json"
SOURCE_CONTROL = ROOT / "controls/g5_p9_alara.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sanitized_hashes(values: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in values.items() if name.endswith("_sha256")}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="actinv-cb1-alara-") as directory:
        work = Path(directory)
        evidence = work / "evidence"
        environment = os.environ.copy()
        environment["ACTINV_P9_RESULTS"] = str(evidence)
        environment["ACTINV_P9_WORK"] = str(work / "work")
        run = subprocess.run(
            [sys.executable, str(SOURCE_CONTROL)],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
            check=False,
        )
        source_result_path = evidence / "g5_p9_alara.json"
        if run.returncode != 0 or not source_result_path.is_file():
            raise RuntimeError(
                f"ALARA differential control failed ({run.returncode}):\n"
                f"{(run.stdout + run.stderr)[-2000:]}"
            )
        source = json.loads(source_result_path.read_text(encoding="utf-8"))

    analytic = source["analytic_comparison"]
    output = {
        "schema": "actinv-cb1-alara-1",
        "access": {
            "ACTINV": "executed",
            "ALARA": "executed",
            "OpenMC": "not-applicable",
            "FISPACT-II": "not-available",
            "SCALE/ORIGEN": "not-available",
        },
        "rerun": {
            "source_control": "controls/g5_p9_alara.py",
            "source_control_sha256": sha256(SOURCE_CONTROL),
            "subprocess_returncode": run.returncode,
            "official_reference_conversion_and_run": bool(
                source["official_reference"]["conversion_succeeded"]
                and source["official_reference"]["run_succeeded"]
            ),
        },
        "implementations": {
            "alara_version": source["alara"]["version"],
            "alara_source_commit": source["alara"]["commit"],
            "actinv_version": "1.0.0",
        },
        "identical_inputs": {
            "groups": source["data"]["groups"],
            "cross_section_barns": source["data"]["cross_section_barns"],
            "flux_n_cm2_s": source["data"]["flux_n_cm2_s"],
            "official_input_hashes": sanitized_hashes(source["data"]["official_hashes"]),
            "activation_subset_sha256": source["data"]["activation_subset_sha256"],
            "timeline": source["timeline"],
        },
        "reaction_rate": source["rates"],
        "shutdown_inventory": source["comparisons"],
        "maximum_inventory_relative_above_1e-10_initial": source[
            "maximum_inventory_relative_above_1e-10_initial"
        ],
        "analytic": analytic,
        "worst_actinv_vs_analytic_relative": max(
            row["actinv_relative"] for row in analytic.values()
        ),
        "worst_alara_vs_analytic_relative": max(
            row["alara_relative"] for row in analytic.values()
        ),
        "tolerances": {
            "collapsed_rate_relative": 1.0e-12,
            "inventory_relative": 5.0e-4,
        },
        "pass": bool(source["pass"]),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
