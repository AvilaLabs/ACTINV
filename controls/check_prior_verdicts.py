#!/usr/bin/env python3
"""Check the committed, superseding P1-P11 verdict evidence without re-running bulk-data gates."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [
    ("P1", "results/verdict.json", "P1-PASS"),
    ("P2", "results/verdict_p2.json", "P2-CONDITIONAL"),
    ("P3b", "results/verdict_p3b.json", "P3b-PASS"),
    ("P4b", "results/verdict_p4b.json", "P4b-PASS"),
    ("P5", "results/verdict_p5.json", "P5-PASS"),
    ("P6", "results/verdict_p6.json", "P6-CONDITIONAL"),
    ("P7", "results/verdict_p7.json", "P7-CONDITIONAL"),
    ("P8", "results/verdict_p8.json", "P8-CONDITIONAL"),
    ("P9", "results/verdict_p9.json", "P9-CONDITIONAL"),
    ("P10", "results/verdict_p10.json", "P10-CONDITIONAL"),
    ("P11", "results/verdict_p11.json", "P11-CONDITIONAL"),
]
TERMINAL_GATES = [
    ("P10-G7", "results/g7_p10_complete.json"),
    ("P11-G6", "results/g6_p11_complete.json"),
]
EXPECTED_AMENDMENT_COUNTS = {
    "P2": 3,
    "P6": 1,
    "P7": 1,
    "P8": 1,
    "P9": 1,
    "P10": 18,
    "P11": 5,
}


def main() -> int:
    verdicts: dict[str, str] = {}
    amendment_counts: dict[str, int] = {}
    terminal_gates: dict[str, bool] = {}
    errors: list[str] = []

    for phase, relative, expected in EXPECTED:
        path = ROOT / relative
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"{phase}: cannot read {relative}: {error}")
            continue
        actual = value.get("verdict")
        verdicts[phase] = actual
        if actual != expected:
            errors.append(f"{phase}: expected {expected}, found {actual!r}")
        amendments = value.get("amendments")
        if amendments is None:
            amendments = [path.name for path in sorted((ROOT / "protocols").glob(f"ACTINV-{phase}_AMENDMENT_*.md"))]
        amendment_counts[phase] = len(amendments)
        expected_count = EXPECTED_AMENDMENT_COUNTS.get(phase)
        if expected_count is not None and len(amendments) != expected_count:
            errors.append(
                f"{phase}: expected {expected_count} amendment record(s), found {len(amendments)}"
            )
        for amendment in amendments:
            if not (ROOT / "protocols" / amendment).is_file():
                errors.append(f"{phase}: missing protocols/{amendment}")

    for gate, relative in TERMINAL_GATES:
        path = ROOT / relative
        try:
            passed = json.loads(path.read_text()).get("pass") is True
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"{gate}: cannot read {relative}: {error}")
            passed = False
        terminal_gates[gate] = passed
        if not passed:
            errors.append(f"{gate}: committed terminal evidence is not passing")

    result = {
        "superseding_verdicts": verdicts,
        "amendment_counts": amendment_counts,
        "terminal_gates": terminal_gates,
        "errors": errors,
        "pass": not errors,
    }
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
