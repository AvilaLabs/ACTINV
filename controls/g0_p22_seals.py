#!/usr/bin/env python3
"""P22-G0: seal verification and candidate pin.

Verifies, without importing any ACTINV production or audit module:

- every prior verdict file exists and carries its expected verdict
  string (25 verdicts, including honest failures ``P3-FAIL``,
  ``P4-FAIL``, ``P17-FAIL``, ``P18-FAIL``, ``P18b-FAIL`` and the
  conditional passes);
- every sealed CB1 evidence file still hashes to the digest pinned by
  ``results/session_cb1.json`` — the comparison baseline is provably the
  sealed one;
- ``results/g3_p21_executed.json`` names the executed 20,000-cell case
  and its ``actinv_binary_sha256`` equals the candidate binary's digest —
  the executed scale evidence belongs to this exact build;
- the candidate pin: ``target/release/actinv`` and
  ``python/target/release/libactinv.so`` SHA-256s, ``actinv --version``,
  head commit, rustc, kernel, and the measurement scope's cgroup limits.

Writes ``results/g0_p22_seals.json``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g0_p22_seals.json"
PROTOCOL = ROOT / "protocols/ACTINV-P22_PROTOCOL.md"
SESSION_CB1 = ROOT / "results/session_cb1.json"
EXECUTED = ROOT / "results/g3_p21_executed.json"
ACTINV = ROOT / "target/release/actinv"
PYMODULE = ROOT / "python/target/release/libactinv.so"

EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p23.json": "P23-PASS",
}

CB1_EVIDENCE = {
    "access": "cb1_access.json",
    "numerical": "cb1_numerical.json",
    "alara": "cb1_alara.json",
    "fns": "cb1_fns.json",
    "prior_validation": "cb1_prior_validation.json",
    "performance": "cb1_performance.json",
    "mesh_performance": "cb1_mesh_performance.json",
    "first_use": "cb1_first_use.json",
    "capabilities": "cb1_capabilities.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cgroup_record() -> dict[str, str | None]:
    record: dict[str, str | None] = {}
    for name in ("memory.max", "memory.swap.max", "cpu.max", "pids.max"):
        node = Path("/sys/fs/cgroup") / name
        record[name] = (
            node.read_text().strip() if node.exists() else None
        )
    self_cgroup = Path("/proc/self/cgroup").read_text().strip()
    record["self_cgroup"] = self_cgroup
    return record


def main() -> None:
    verdicts: dict[str, dict[str, object]] = {}
    for name, expected in EXPECTED_VERDICTS.items():
        path = ROOT / "results" / name
        observed = None
        if path.exists():
            observed = json.loads(path.read_text(encoding="utf-8")).get("verdict")
        verdicts[name] = {"expected": expected, "observed": observed}

    session = json.loads(SESSION_CB1.read_text(encoding="utf-8"))
    pinned = session["evidence_sha256"]
    seals: dict[str, dict[str, object]] = {}
    for name, filename in CB1_EVIDENCE.items():
        path = ROOT / "results" / filename
        actual = sha256(path) if path.exists() else None
        seals[name] = {
            "file": f"results/{filename}",
            "expected_sha256": pinned.get(name),
            "observed_sha256": actual,
            "matches": actual == pinned.get(name),
        }

    executed = json.loads(EXECUTED.read_text(encoding="utf-8"))
    candidate_sha = sha256(ACTINV)
    module_sha = sha256(PYMODULE)
    version_run = subprocess.run(
        [str(ACTINV), "--version"], text=True, capture_output=True, timeout=60
    )
    rustc_run = subprocess.run(
        ["rustc", "-Vv"], text=True, capture_output=True, timeout=60
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True
    ).stdout.strip()

    record = {
        "schema": "actinv-p22-seals-1",
        "protocol_sha256": sha256(PROTOCOL),
        "candidate": {
            "actinv_binary_sha256": candidate_sha,
            "python_module_sha256": module_sha,
            "actinv_version": version_run.stdout.strip(),
            "rustc": rustc_run.stdout.splitlines()[0] if rustc_run.stdout else None,
            "kernel": platform.release(),
            "platform": platform.platform(),
            "head_commit": head,
            "cgroup": cgroup_record(),
        },
        "verdicts": verdicts,
        "cb1_evidence_seals": seals,
        "executed_scale_link": {
            "record": "results/g3_p21_executed.json",
            "cells": (executed.get("executed_case") or {}).get("cells"),
            "recorded_binary_sha256": executed.get("actinv_binary_sha256"),
            "candidate_binary_sha256": candidate_sha,
            "matches": executed.get("actinv_binary_sha256") == candidate_sha,
        },
    }
    record["pass"] = bool(
        all(v["observed"] == v["expected"] for v in verdicts.values())
        and all(s["matches"] for s in seals.values())
        and record["executed_scale_link"]["cells"] == 20_000
        and record["executed_scale_link"]["matches"]
        and version_run.returncode == 0
    )
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"pass": record["pass"],
                      "verdicts_mismatched": [n for n, v in verdicts.items()
                                            if v["observed"] != v["expected"]],
                      "seals_broken": [n for n, s in seals.items() if not s["matches"]]},
                     indent=2))
    raise SystemExit(0 if record["pass"] else 1)


if __name__ == "__main__":
    main()
