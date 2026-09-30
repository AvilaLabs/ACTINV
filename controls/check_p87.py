#!/usr/bin/env python3
"""P87 checker (protocols/ACTINV-P87_PROTOCOL.md): Python binding for `actinv budget`.

    python3 controls/check_p87.py run     # CLI vs Python budget documents (target/p87/)
    python3 controls/check_p87.py check   # verdict -> results/p87_verdict.json

Run with the CI venv's python after target/p87/build.sh has installed the wheel built from this tree.
The CI replay (G3) step log is copied to target/p87/ci_replay_summary.log.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "target" / "p87"
CLI = ROOT / "target" / "release" / "actinv"
PROTOCOL = ROOT / "protocols" / "ACTINV-P87_PROTOCOL.md"
VERDICT = ROOT / "results" / "p87_verdict.json"
INPUTS = ["ss316ln_exvessel", "eurofer97_exvessel"]
TIMING_KEYS = {"ms", "elapsed_ms"}
UNIT_TESTS = ("test_budget_schema_error_raises", "test_budget_mapping_and_path_parity")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in TIMING_KEYS}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def cmd_run() -> None:
    import actinv
    log = {"cli_sha256": sha(CLI), "python_module": actinv.__file__, "runs": {}}
    for name in INPUTS:
        spec = ROOT / "controls" / "p79" / f"{name}.json"
        out = WORK / f"{name}.cli.json"
        t0 = time.monotonic()
        p = subprocess.run([str(CLI), "budget", str(spec), str(out)], cwd=ROOT, capture_output=True, text=True)
        cli_s = time.monotonic() - t0
        t0 = time.monotonic()
        doc = actinv.budget(spec)
        py_s = time.monotonic() - t0
        (WORK / f"{name}.python.json").write_text(json.dumps(doc, indent=1, sort_keys=True))
        log["runs"][name] = {"cli_returncode": p.returncode, "cli_stderr_tail": p.stderr[-300:], "cli_s": cli_s,
                             "python_s": py_s}
        print(name, p.returncode, f"cli {cli_s:.0f}s python {py_s:.0f}s", flush=True)
    (WORK / "run_log.json").write_text(json.dumps(log, indent=1, sort_keys=True))


def cmd_check() -> int:
    log = json.loads((WORK / "run_log.json").read_text())
    registered = f"{sha(PROTOCOL)}  protocols/ACTINV-P87_PROTOCOL.md" in (ROOT / "protocols/protocol_hash.txt").read_text()
    blog = (WORK / "build.log").read_text()
    rc = dict(re.findall(r"^(fmt|clippy|wheel|install|pytest) rc=(\d+)$", blog, re.M))
    tests = (WORK / "pytest.txt").read_text()
    unit = {name: re.search(rf"^{name} .*\.\.\. ok$", tests, re.M) is not None for name in UNIT_TESTS}
    g1 = all(rc.get(k) == "0" for k in ("fmt", "clippy", "wheel", "install", "pytest")) and all(unit.values())

    g2 = {}
    for name in INPUTS:
        run = log["runs"][name]
        cli_path, py_path = WORK / f"{name}.cli.json", WORK / f"{name}.python.json"
        present = cli_path.exists() and py_path.exists()
        cli = json.loads(cli_path.read_text()) if present else None
        py = json.loads(py_path.read_text()) if present else None
        equal = present and strip(cli) == strip(py)
        verification = [None if d is None else d.get("verification", {}).get("verified") for d in (cli, py)]
        g2[name] = {"pass": equal and run["cli_returncode"] in (0, 3) and verification[0] == verification[1]
                    and (run["cli_returncode"] == 3) == (verification[0] is False),
                    "documents_equal": equal, "cli_returncode": run["cli_returncode"],
                    "verified_cli_python": verification}

    steps = re.findall(r"^STEP (\d+) (\S+)$", (WORK / "ci_replay_summary.log").read_text(), re.M)
    failed = [n for code, n in steps if code != "0"]
    g3 = {"pass": bool(steps) and not failed, "steps": len(steps), "failed": failed}

    verdict = {
        "protocol": "ACTINV-P87",
        "inputs": {"protocol_sha256": sha(PROTOCOL), "cli_sha256": log["cli_sha256"]},
        "G0": {"pass": registered},
        "G1": {"pass": g1, "exit_codes": rc, "unit_tests": unit},
        "G2": {"pass": all(v["pass"] for v in g2.values()), "inputs": g2},
        "G3": g3,
    }
    verdict["pass"] = all(verdict[g]["pass"] for g in ("G0", "G1", "G2", "G3"))
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"G0": registered, "G1": g1, "G2": {k: v["pass"] for k, v in g2.items()},
                      "G3": g3["pass"], "G3_failed": failed, "pass": verdict["pass"]}, indent=1))
    return 0


if __name__ == "__main__":
    fn = {"run": cmd_run, "check": cmd_check}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    fn[sys.argv[1]]()
