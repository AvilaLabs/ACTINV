#!/usr/bin/env python3
"""Verify the retained P103 failure without rerunning or reinterpreting its gates."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    result_path = ROOT / "results/g1_p103_classes.json"
    result = json.loads(result_path.read_text())
    verdict = json.loads((ROOT / "results/p103_verdict.json").read_text())
    vectors = result["vectors"]
    failures = {name: value for name, value in result["invalid_inputs"].items() if not value["pass"]}
    passed = (
        result["pass"] is False
        and len(vectors) == 126
        and all(value["pass"] is True for value in vectors.values())
        and result["declared_h3_relocation"]["pass"] is False
        and set(failures) == {"unequal_cell_timestamps"}
        and verdict["phase"] == "P103"
        and verdict["verdict"] == "P103-FAIL"
        and verdict["gates"]["G1"] == "FAIL"
        and verdict["gates"]["G2"] == "NOT EXECUTED"
        and verdict["source_vectors_passed"] == 126
        and verdict["g1_result_sha256"] == hashlib.sha256(result_path.read_bytes()).hexdigest()
        and (ROOT / "protocols/ACTINV-P103_AMENDMENT_A.md").is_file()
    )
    print("P103 retained failure integrity:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
