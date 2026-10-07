#!/usr/bin/env python3
"""P120 E1: run the CI preserved-history steps exactly as .github/workflows/ci.yml lists them and record each exit.

Runs every step from "P103 source-seal integrity (historical)" through "P118 terminal failure remains immutable", in
order, each as `bash -e -o pipefail -c <run>` with the step's own env, and records the exit, the log digest and the
working-tree changes the step left behind. It does not judge; check_p120.py compares two such records.

Usage: python3 controls/p120_ci_steps.py --label before|after --actinv-bin PATH
Run it inside the required systemd cgroup with a disk-backed TMPDIR.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
FIRST = "P103 source-seal integrity (historical)"
LAST = "P118 terminal failure remains immutable"
OUT_DIR = ROOT / "results/p120"
STEP_TIMEOUT_S = 3600


def steps() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    all_steps = [s for job in doc["jobs"].values() for s in job.get("steps", [])]
    names = [s.get("name") for s in all_steps]
    if names.count(FIRST) != 1 or names.count(LAST) != 1:
        raise SystemExit("the workflow does not name the first and last steps exactly once")
    return all_steps[names.index(FIRST):names.index(LAST) + 1]


def status() -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    return sorted(line for line in out.splitlines() if not line[3:].startswith(("results/p120/", "target/")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["before", "after"])
    ap.add_argument("--actinv-bin", required=True)
    args = ap.parse_args(argv)
    binary = Path(args.actinv_bin).resolve(strict=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_dir = ROOT / "target/p120-ci-steps" / args.label
    log_dir.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()
    start_status = status()
    records = []
    for index, step in enumerate(steps(), start=1):
        env = dict(os.environ, **{k: str(v) for k, v in (step.get("env") or {}).items()})
        env["ACTINV_BIN"] = str(binary)
        log = log_dir / f"{index:02d}.log"
        with log.open("wb") as handle:
            try:
                proc = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]], cwd=ROOT, env=env,
                                      stdout=handle, stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT_S)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        data = log.read_bytes()
        after = status()
        records.append({"index": index, "name": step["name"], "run": step["run"], "exit": code,
                        "log": str(log.relative_to(ROOT)), "log_sha256": hashlib.sha256(data).hexdigest(),
                        "log_tail": data.decode("utf-8", "replace")[-1500:],
                        "tree_changes": sorted(set(after) - set(start_status))})
        print(f"{index:02d} exit {code}: {step['name']}", flush=True)
    out = {"schema": "actinv-p120-ci-steps-1", "label": args.label, "head": head,
           "actinv_bin_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
           "workflow_sha256": hashlib.sha256(WORKFLOW.read_bytes()).hexdigest(), "steps": records}
    path = OUT_DIR / f"ci_steps_{args.label}.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")
    return 0 if all(r["exit"] == 0 for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
