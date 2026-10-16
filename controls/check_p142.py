#!/usr/bin/env python3
"""P142: twin waste qualified on the current code. Runs Q1 and Q2 and records their actual exits; `verdict` derives
the disposition from the records and the CI runs of the pushed evidence commit. Launch it inside the required systemd
cgroup; its children inherit that scope and run serially.

    python3 controls/check_p142.py q1        # check_p116.py --no-write with the candidate release binary
    python3 controls/check_p142.py q2        # the ci.yml steps P103 through P118
    python3 controls/check_p142.py verdict CI_RUNS.json
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/p142"
PROTOCOL = ROOT / "protocols/ACTINV-P142_PROTOCOL.md"
BINARY = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
SEALED_G1 = ROOT / "results/g1_p116_twin_waste.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def candidate() -> dict:
    return {"commit": git("rev-parse", "HEAD"), "tree_clean": git("status", "--porcelain", "--untracked-files=no") == "",
            "binary": str(BINARY.relative_to(ROOT)) if BINARY.is_relative_to(ROOT) else str(BINARY),
            "binary_sha256": sha(BINARY), "protocol_sha256": sha(PROTOCOL)}


def run_logged(argv: list[str], log: Path, env: dict | None = None, timeout: int = 3600) -> int:
    with log.open("wb") as stream:
        child = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, env=env, timeout=timeout)
    return child.returncode


def q1() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    before = sha(SEALED_G1)
    env = dict(os.environ, ACTINV_BIN=str(BINARY))
    code = run_logged([sys.executable, "controls/check_p116.py", "--no-write"], OUT / "q1_check_p116.log", env)
    report = {"schema": "actinv-p142-q1-1", "candidate": candidate(), "argv": ["python3", "controls/check_p116.py",
              "--no-write"], "exit": code, "sealed_g1_sha256": before, "sealed_g1_unchanged": sha(SEALED_G1) == before,
              "log_sha256": sha(OUT / "q1_check_p116.log")}
    report["pass"] = code == 0 and report["sealed_g1_unchanged"] and report["candidate"]["tree_clean"]
    (OUT / "q1.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"q1_pass": report["pass"], "exit": code}))
    return 0 if report["pass"] else 1


def q2() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["build-and-control"]
    steps, active = [], False
    for step in job["steps"]:
        name = step.get("name", "")
        active = active or name.startswith("P103")
        if active and "run" in step:
            steps.append(step)
        if name.startswith("P118"):
            break
    logs = OUT / "q2_logs"
    logs.mkdir(exist_ok=True)
    # The job's /tmp paths move under target/ci-local so no RAM-backed /tmp is used.
    local = str(ROOT / "target/ci-local")
    env = dict(os.environ, **{k: str(v).replace("/tmp", local) for k, v in (job.get("env") or {}).items()})
    env["ACTINV_BIN"] = str(BINARY)
    rows = []
    for index, step in enumerate(steps):
        log = logs / f"{index:02d}.log"
        code = run_logged(["bash", "-e", "-c", step["run"].replace("python ", "python3 ")], log, env)
        rows.append({"step": step["name"], "exit": code, "log_sha256": sha(log)})
    report = {"schema": "actinv-p142-q2-1", "candidate": candidate(), "steps": rows,
              "worktree_clean_after": git("status", "--porcelain", "--untracked-files=no") == ""}
    report["pass"] = (len(rows) == 14 and all(r["exit"] == 0 for r in rows) and report["worktree_clean_after"]
                      and report["candidate"]["tree_clean"])
    (OUT / "q2.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"q2_pass": report["pass"], "failed": [r["step"] for r in rows if r["exit"]]}))
    return 0 if report["pass"] else 1


def verdict(ci_path: str) -> int:
    q1r, q2r = (json.loads((OUT / f"{q}.json").read_text()) for q in ("q1", "q2"))
    q3r = json.loads((OUT / "q3.json").read_text())
    ci = json.loads(Path(ci_path).read_text())
    runs = ci.get("runs", [])
    q4 = bool(runs) and all(r.get("conclusion") == "success" for r in runs) and any(
        r.get("name") == "controls" for r in runs)
    gates = {"Q1": q1r.get("pass") is True, "Q2": q2r.get("pass") is True, "Q3": q3r.get("pass") is True, "Q4": q4}
    record = {"schema": "actinv-p142-verdict-1", "protocol_sha256": sha(PROTOCOL), "gates": gates,
              "candidate": q1r["candidate"], "ci": ci,
              "verdict": "P142-PASS" if all(gates.values()) else "P142-FAIL"}
    (ROOT / "results/p142_verdict.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"verdict": record["verdict"], "gates": gates}))
    return 0 if record["verdict"] == "P142-PASS" else 1


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "q1":
        raise SystemExit(q1())
    if command == "q2":
        raise SystemExit(q2())
    if command == "verdict" and len(sys.argv) == 3:
        raise SystemExit(verdict(sys.argv[2]))
    raise SystemExit(__doc__)
