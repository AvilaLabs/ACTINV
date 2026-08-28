#!/usr/bin/env python3
"""CB1-G3: compact, hash-pinned prior validation families and historical FNS baselines."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/cb1_prior_validation.json"
INPUTS = {
    "fns_eaf2010": (
        ROOT / "results/fns_summary.json",
        "2ab759a648aa7bc662bbcd5d4d66b775d58452e6b583e9534bb59bc137ed204c",
        "1e38b79a83142627174a5a86b81dafa1ed267f8a",
    ),
    "fns_tendl2023": (
        ROOT / "results/fns_tendl_full_summary.json",
        "15a6b827dd0eb105c286fa84f46f633dfb7bbe7ea25effbe14388eb7512cee23",
        "d29528e98ffb772061b5ee97db66b3d0ce193dd2",
    ),
    "conderc_fission": (
        ROOT / "results/g6_p9_conderc.json",
        "9decf447a3cc622b50278f21280a23c0595040a208965bd7643566a3f9f22ed5",
        "e5421a0e30eb94303482bed2c4b9491b773244e6",
    ),
    "fng_iter": (
        ROOT / "results/g4_p12_fng.json",
        "2561c1bbf0b537fb68d0602bf1361efe2bfeac9c2b3dba96931cbd320fb43198",
        "68ae82bc53ee64a63c8182fb078aa6ac91eb09f9",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def historical_fns(document: dict[str, object]) -> dict[str, object]:
    experiments = [
        row
        for row in document["experiments"]
        if not row.get("error") and row.get("summary", {}).get("actinv")
    ]
    geometric_means = np.asarray(
        [row["summary"]["actinv"]["geomean_CE"] for row in experiments]
    )
    maxima = np.asarray(
        [row["summary"]["actinv"]["max_abs_lnCE"] for row in experiments]
    )
    return {
        "experiments_scored": len(experiments),
        "median_experiment_geometric_mean_C_over_E": float(np.median(geometric_means)),
        "median_experiment_maximum_abs_log_C_over_E": float(np.median(maxima)),
        "experiments_all_points_within_30_percent": int(np.count_nonzero(maxima <= math.log(1.3))),
        "fraction_experiments_all_points_within_30_percent": float(
            np.mean(maxima <= math.log(1.3))
        ),
        "metric_limit": "historical summary lacks paired-point values; pooled metrics are not reconstructed",
    }


def main() -> None:
    loaded = {}
    provenance = {}
    for name, (path, expected, source_commit) in INPUTS.items():
        actual = sha256(path)
        provenance[name] = {
            "path": str(path.relative_to(ROOT)),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "source_commit": source_commit,
            "access": "prior-evidence",
            "matches": actual == expected,
        }
        loaded[name] = json.loads(path.read_text(encoding="utf-8"))

    fission = loaded["conderc_fission"]
    fng = loaded["fng_iter"]
    fission_summary = {
        "Dickens U-235 thermal pulse total": fission["dickens_pulse"]["aggregate"]["total"],
        "Dickens U-235 thermal pulse beta": fission["dickens_pulse"]["aggregate"]["beta"],
        "Dickens U-235 thermal pulse gamma": fission["dickens_pulse"]["aggregate"]["gamma"],
        "Yarnell U-235 thermal 20000 s total": fission["yarnell_20000s"]["aggregate"]["total"],
    }
    fng_summary = {
        "reference": {
            "campaign": fng["reference"]["article"],
            "archive": fng["reference"]["archive"],
            "cell": fng["reference"]["cell"],
            "intervals": fng["reference"]["intervals"],
        },
        "independent_reaction_rate_comparisons": fng["independent_reaction_rates"]["comparisons"],
        "independent_reaction_rate_maximum_relative_error": fng["independent_reaction_rates"][
            "maximum_relative_error"
        ],
        "history_comparison": fng["history_comparison"],
    }
    output = {
        "schema": "actinv-cb1-prior-validation-1",
        "provenance": provenance,
        "historical_fns": {
            "ACTINV/EAF-2010": historical_fns(loaded["fns_eaf2010"]),
            "ACTINV/TENDL-2023": historical_fns(loaded["fns_tendl2023"]),
        },
        "conderc_fission": fission_summary,
        "fng_iter": fng_summary,
        "limitations": [
            "these rows are prior evidence, not fresh CB1 executions",
            "FNS historical files expose experiment summaries but not enough paired values for pooled CB1 metrics",
            "FNG/ITER is an identical-record implementation check, not an independent activation measurement",
        ],
        "pass": all(row["matches"] for row in provenance.values())
        and fission["pass"] is True
        and fng["pass"] is True,
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
