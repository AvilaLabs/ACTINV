#!/usr/bin/env python3
"""Derive and verify P104's retained replay failure without executing its gates."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main(write: bool) -> int:
    g1_path = ROOT / "results/g1_p104_classes.json"
    replay_path = ROOT / "results/p104_readonly_replay_attempt.json"
    g1 = json.loads(g1_path.read_text())
    replay = json.loads(replay_path.read_text())
    failed = replay["emitted_results"][-1]
    valid = (
        g1["pass"] is True
        and len(g1["vectors"]) == 126
        and all(item["pass"] for item in g1["vectors"].values())
        and all(item["pass"] for item in g1["invalid_inputs"].values())
        and replay["exit_code"] == 1
        and replay["g2_executed"] is False
        and failed["pass"] is False
        and failed["persisted_result_matches"] is True
        and failed["vectors"] == g1["vectors"]
        and (ROOT / "results/p104_g1_attempt_1.json").is_file()
        and (ROOT / "results/g0_p104_successor_v1.json").is_file()
    )
    verdict = {
        "schema": "actinv-p104-verdict-1", "phase": "P104",
        "verdict": "P104-FAIL", "repair_rounds": 1,
        "gates": {"G0": "PASS", "G1_write": "PASS", "G1_read_only_replay": "FAIL",
                  "G2": "NOT EXECUTED", "G3": "NOT COMPLETE"},
        "source_vectors_passed": 126,
        "g1_result_sha256": hashlib.sha256(g1_path.read_bytes()).hexdigest(),
        "replay_attempt_sha256": hashlib.sha256(replay_path.read_bytes()).hexdigest(),
        "reason": "Checker inserts a diagnostic field before repeating equality; required read-only replay fails before G2.",
        "successor": "P105",
    }
    result_path = ROOT / "results/p104_verdict.json"
    if write:
        if valid:
            result_path.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n")
    else:
        valid &= json.loads(result_path.read_text()) == verdict
    print("P104 retained failure integrity:", "PASS" if valid else "FAIL")
    return 0 if valid else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    raise SystemExit(main(parser.parse_args().write))
