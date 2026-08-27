#!/usr/bin/env python3
"""P10-G7 actual-data control for Amendment N's bounded linearization depth."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(
    os.environ.get(
        "ACTINV_P10_CO58",
        "/home/connoravila/nuclear-data/tendl-2025/files/n-working/n-Co058.tendl",
    )
)
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target/release/dump"))
RESULT = ROOT / "results/g7_p10_co58_linearization.json"
AMENDMENT = ROOT / "protocols/ACTINV-P10_AMENDMENT_N.md"
SOURCE_SHA256 = "bbc3f94bb2bb47148feab4825d882c7619a6c431670c11ec5840b9663002d674"
AMENDMENT_SHA256 = "6f33ab8d4adc127440c97f5cb7d1393859e417e51716f198f2645eb8b74a15c3"
EXPECTED_CERTIFICATE = {
    "zero_k_points": 21_712,
    "output_points": 21_712,
    "zero_k_refinement_passes": 19,
    "output_refinement_passes": 0,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    missing = [str(path) for path in (SOURCE, DUMP, AMENDMENT) if not path.is_file()]
    if missing:
        raise SystemExit(f"missing P10 Co-58 control input(s): {missing}")
    probe = subprocess.run(
        [
            "prlimit",
            "--as=1073741824",
            "--",
            str(DUMP),
            "processed-xs",
            str(SOURCE),
            "102",
            "0",
            "10.35",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    certificate = None
    sample = None
    for line in probe.stdout.splitlines():
        fields = line.split()
        if fields[:1] == ["C"] and len(fields) == 5:
            certificate = {
                "zero_k_points": int(fields[1]),
                "output_points": int(fields[2]),
                "zero_k_refinement_passes": int(fields[3]),
                "output_refinement_passes": int(fields[4]),
            }
        elif fields[:1] == ["X"] and len(fields) == 3:
            sample = {"energy_eV": float(fields[1]), "cross_section_b": float(fields[2])}

    source_hash = sha256(SOURCE)
    amendment_hash = sha256(AMENDMENT)
    result = {
        "schema": "actinv-p10-g7-co58-linearization-1",
        "gate": "P10-G7",
        "source_file": SOURCE.name,
        "source_sha256": source_hash,
        "expected_source_sha256": SOURCE_SHA256,
        "amendment_n_sha256": amendment_hash,
        "expected_amendment_n_sha256": AMENDMENT_SHA256,
        "probe_returncode": probe.returncode,
        "probe_stderr": probe.stderr,
        "certificate": certificate,
        "expected_certificate": EXPECTED_CERTIFICATE,
        "sample": sample,
        "recorded_complete_corpus_scan": {
            "files": 2_850,
            "reactions": 3_303,
            "only_reaction_at_or_above_old_limit": "n-Co058.tendl MT102",
            "maximum_refinement_pass_index": 19,
        },
    }
    result["pass"] = bool(
        source_hash == SOURCE_SHA256
        and amendment_hash == AMENDMENT_SHA256
        and probe.returncode == 0
        and certificate == EXPECTED_CERTIFICATE
        and sample is not None
        and math.isfinite(sample["cross_section_b"])
        and sample["cross_section_b"] >= 0.0
    )
    RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
