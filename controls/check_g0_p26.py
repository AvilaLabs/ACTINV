#!/usr/bin/env python3
"""P24/P26-pattern independent G0 checker for P26.

Imports no production module.  Rehashes the protocol, re-verifies the
opening-commit ancestry, re-checks every prior verdict verbatim,
re-derives the comparator census independently, and rejects planted
mutations of the seal record.

Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
RECORD = RESULTS / "g0_p26_seals.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P26_PROTOCOL.md"
PROTOCOL_SHA256 = "0dd9be843e4e195d3f3ebb9a9084f233af3d0eedf5045bbb48d77d665f5d1e06"
OPENING_COMMIT = "3ae2f2656f6e6e56ad401378d9f5c6a96f1cf80d"

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
    "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS",
    "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL",
}

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def census() -> dict:
    out = {}
    probes = {
        "fispact": ["fispact", "fispact-II", "fispact2"],
        "openmc": ["openmc"],
        "alara": ["alara", "ALARA"],
        "scale_origen": ["scale", "origen", "origen-rs"],
        "njoy": ["njoy", "njoy2016"],
        "actinv_v101": ["actinv"],
    }
    for name, exes in probes.items():
        found = [e for e in exes if shutil.which(e)]
        out[name] = "executable" if found else "not_available"
    for mod in ("openmc", "actinv"):
        r = subprocess.run([sys.executable, "-c", f"import {mod}"],
                           capture_output=True)
        if r.returncode == 0:
            out.setdefault("openmc" if mod == "openmc" else "actinv_v101",
                           "executable")
    return out


def check_report(record: dict) -> list[str]:
    local: list[str] = []
    if record.get("schema") != "actinv-p26-g0-seal-1":
        local.append("schema")
    if record.get("protocol_sha256") != PROTOCOL_SHA256:
        local.append("protocol hash field")
    if record.get("opening_commit") != OPENING_COMMIT:
        local.append("opening commit field")
    if not record.get("pass"):
        local.append("seal pass is false")
    for name, want in EXPECTED_VERDICTS.items():
        if record.get("prior_verdicts", {}).get(name) != want:
            local.append(f"recorded {name} != {want}")
    core = record.get("core", {})
    if core.get("status") not in ("pinned", "unavailable"):
        local.append("core identity status invalid")
    if core.get("status") == "unavailable" and not core.get("consequence"):
        local.append("core unavailable without a recorded consequence")
    if not record.get("production_path", {}).get("clean"):
        local.append("production path not clean of prototype references")
    return local


def main() -> int:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol sha256 mismatch")
    if subprocess.run(
            ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
            cwd=REPO).returncode != 0:
        failures.append("opening commit is not an ancestor of HEAD")
    record = json.loads(RECORD.read_text())

    for name, want in EXPECTED_VERDICTS.items():
        path = RESULTS / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
        elif json.loads(path.read_text()).get("verdict") != want:
            failures.append(f"{name} verdict mismatch")

    live = census()
    for name, blk in record.get("comparator_census", {}).items():
        if name in live and blk.get("status") != live[name]:
            failures.append(f"census {name}: recorded {blk.get('status')} != live {live[name]}")

    failures.extend(check_report(record))

    mutations = 0
    rejected = 0
    plants = [
        lambda r: r.update({"pass": False}),
        lambda r: r["prior_verdicts"].update({"verdict_p24.json": "P24-PASS"}),
        lambda r: r["core"].update({"status": "unavailable", "consequence": ""}),
        lambda r: r["production_path"].update({"clean": False}),
        lambda r: r.update({"protocol_sha256": "0" * 64}),
    ]
    for plant in plants:
        m = copy.deepcopy(record)
        plant(m)
        mutations += 1
        if check_report(m):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p26-g0-check-1",
        "pass": not failures,
        "failures": failures,
        "protocol_sha256": PROTOCOL_SHA256,
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g0_p26_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
