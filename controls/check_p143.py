#!/usr/bin/env python3
"""P143: waste operations in the Python API. `w1` runs the sealed waste and twin campaigns (the ci.yml steps P105,
P107, P108, P110, P112, P116) with a recording wrapper standing in for ACTINV_BIN, so every `waste ...` and `twin ...`
call is also made through the Python API and compared byte for byte. `verdict` combines W1 with the coordinator's
W2/W3 and W4 records. Launch it inside the required systemd cgroup; its children inherit that scope and run serially.

    P143_PYTHON=/path/to/venv/bin/python python3 controls/check_p143.py w1
    python3 controls/check_p143.py verdict CI_RUNS.json
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/p143"
PROTOCOL = ROOT / "protocols/ACTINV-P143_PROTOCOL.md"
RECORDER = ROOT / "controls/p143_recorder.py"
BINARY = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
WRAPPER = ROOT / "target/p143/actinv"
PREFIXES = ["P105 ", "P107 ", "P108 ", "P110 ", "P112 ", "P116 "]
FUNCTIONS = ["classify", "budget", "bounds", "composition", "composition_solve", "intrusion_screen", "twin"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def tree_clean() -> bool:
    return git("status", "--porcelain", "--untracked-files=no") == ""


def candidate() -> dict:
    return {"commit": git("rev-parse", "HEAD"), "tree_clean": tree_clean(),
            "binary": str(BINARY.relative_to(ROOT)) if BINARY.is_relative_to(ROOT) else str(BINARY),
            "binary_sha256": sha(BINARY), "protocol_sha256": sha(PROTOCOL), "recorder_sha256": sha(RECORDER)}


def run_logged(argv: list[str], log: Path, env: dict | None = None, timeout: int = 7200) -> int:
    with log.open("wb") as stream:
        child = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, env=env, timeout=timeout)
    return child.returncode


def summarize(log: Path) -> dict:
    records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()] if log.exists() else []
    counts = {f: dict(Counter(r["status"] for r in records if r["function"] == f)) for f in FUNCTIONS}
    successes = {f: sum(1 for r in records if r["function"] == f and r["status"] == "match" and r["cli_exit"] == 0)
                 for f in FUNCTIONS}
    refusals = {f: sum(1 for r in records if r["function"] == f and r["status"] == "match" and r["cli_exit"] != 0)
                for f in FUNCTIONS}
    return {"records": len(records), "by_function": counts, "compared_successes": successes,
            "compared_refusals": refusals, "not_compared": dict(Counter(
                r["reason"] for r in records if r["status"] == "not_compared")),
            "stray_output_records": sum(1 for r in records if r.get("py_stray_bytes")),
            "mismatches": [r for r in records if r["status"] == "mismatch"]}


def w1() -> int:
    python = os.environ.get("P143_PYTHON")
    if not python:
        raise SystemExit("P143_PYTHON must name the venv python with the new wheel installed")
    OUT.mkdir(parents=True, exist_ok=True)
    WRAPPER.parent.mkdir(parents=True, exist_ok=True)
    WRAPPER.write_text(f'#!/bin/sh\nexec {shlex.quote(python)} -I {shlex.quote(str(RECORDER))} "$@"\n')
    WRAPPER.chmod(0o755)
    parity = OUT / "w1_parity.jsonl"
    parity.write_text("")
    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["build-and-control"]
    steps = [s for s in job["steps"] if "run" in s and s.get("name", "").startswith(tuple(PREFIXES))]
    if [s["name"].split()[0] + " " for s in steps] != PREFIXES:
        raise SystemExit("ci.yml does not hold exactly the six expected campaign steps")
    logs = OUT / "w1_logs"
    logs.mkdir(exist_ok=True)
    # The job's /tmp paths move under target/ci-local so no RAM-backed /tmp is used.
    local = ROOT / "target/ci-local"
    local.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, **{k: str(v).replace("/tmp", str(local)) for k, v in (job.get("env") or {}).items()})
    env.update(ACTINV_BIN=str(WRAPPER), P143_REAL_ACTINV=str(BINARY), P143_LOG=str(parity))
    before = candidate()
    rows = []
    for index, step in enumerate(steps):
        log = logs / f"{index:02d}.log"
        code = run_logged(["bash", "-e", "-c", step["run"].replace("python ", "python3 ")], log, env)
        rows.append({"step": step["name"], "exit": code, "log_sha256": sha(log)})
    summary = summarize(parity)
    report = {"schema": "actinv-p143-w1-1", "candidate": before, "python": python, "steps": rows,
              "parity_log": str(parity.relative_to(ROOT)), "parity_log_sha256": sha(parity), **summary,
              "worktree_clean_after": tree_clean()}
    report["functions_without_success"] = [f for f in FUNCTIONS if not summary["compared_successes"][f]]
    report["pass"] = (len(rows) == 6 and all(r["exit"] == 0 for r in rows) and before["tree_clean"]
                      and report["worktree_clean_after"] and not summary["mismatches"]
                      and not report["functions_without_success"])
    (OUT / "w1.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"w1_pass": report["pass"], "failed": [r["step"] for r in rows if r["exit"]],
                      "records": summary["records"], "mismatches": len(summary["mismatches"]),
                      "successes": summary["compared_successes"],
                      "functions_without_success": report["functions_without_success"]}))
    return 0 if report["pass"] else 1


def verdict(ci_path: str) -> int:
    w1r = json.loads((OUT / "w1.json").read_text())
    w23 = json.loads((OUT / "w2_w3.json").read_text())
    w4r = json.loads((OUT / "w4.json").read_text())
    ci = json.loads(Path(ci_path).read_text())
    runs = ci.get("runs", [])
    ci_green = bool(runs) and all(r.get("conclusion") == "success" for r in runs) and any(
        r.get("name") == "controls" for r in runs)

    def gate(record: dict, key: str) -> bool:
        part = record.get(key)
        return (part.get("pass") if isinstance(part, dict) else record.get("pass")) is True

    gates = {"W1": w1r.get("pass") is True, "W2": gate(w23, "w2"), "W3": gate(w23, "w3"),
             "W4": w4r.get("pass") is True and ci_green}
    record = {"schema": "actinv-p143-verdict-1", "protocol_sha256": sha(PROTOCOL), "gates": gates,
              "candidate": w1r["candidate"], "ci": ci,
              "verdict": "P143-PASS" if all(gates.values()) else "P143-FAIL"}
    (ROOT / "results/p143_verdict.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"verdict": record["verdict"], "gates": gates}))
    return 0 if record["verdict"] == "P143-PASS" else 1


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "w1":
        raise SystemExit(w1())
    if command == "verdict" and len(sys.argv) == 3:
        raise SystemExit(verdict(sys.argv[2]))
    raise SystemExit(__doc__)
