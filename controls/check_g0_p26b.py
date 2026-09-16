#!/usr/bin/env python3
"""P26b G0 independent checker.

Imports no production, conversion or scoring module.  Rehashes the
protocol, re-verifies opening-commit ancestry, re-checks every prior
verdict verbatim, re-derives the comparator census and the FENDL file
inventory independently, re-pins the binary/data identities, and
rejects planted mutations of the seal record.

Writes ``results/g0_p26b_check.json``; exit nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
RECORD = RESULTS / "g0_p26b_seals.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P26b_PROTOCOL.md"
PROTOCOL_SHA256 = "a1c433c842824c97637f5673a68c26b388dac1f8a237c009417ccb8d10d55327"
OPENING_COMMIT = "79b069ddaadc104b4d6d2131311b64202894b2d8"

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
    "verdict_p25b.json": "P25b-FAIL",
    "verdict_p25c.json": "P25c-PASS",
    "verdict_p26.json": "P26-FAIL",
}

ND = Path.home() / "nuclear-data"
FENDL = ND / "fendl-3.2c"
EXPECTED_ELEMENTS = {"FE", "W", "AG", "CO", "CR", "CU", "MN", "MO",
                     "NB", "NI", "TA", "V"}
EXPECTED_NUCLIDES = 36

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def live_census() -> dict:
    probes = {
        "fispact": (["fispact", "fispact-II", "fispact2"], []),
        "openmc": (["openmc"], []),
        "alara": (["alara", "ALARA"],
                  [ND / "alara-2.9.2-build" / "src" / "alara"]),
        "scale_origen": (["scale", "origen", "origen-rs"], []),
        "njoy": (["njoy", "njoy2016"],
                 [ND / "njoy2016.79-build" / "njoy"]),
        "actinv": (["actinv"], [REPO / "target" / "release" / "actinv"]),
    }
    out = {}
    for name, (exes, known) in probes.items():
        found = [e for e in exes if shutil.which(e)] + [
            str(p) for p in known
            if p.is_file() and os.access(p, os.X_OK)]
        out[name] = "executable" if found else "not_available"
    for mod, name in (("openmc", "openmc"), ("actinv", "actinv")):
        r = subprocess.run([sys.executable, "-c", f"import {mod}"],
                           capture_output=True)
        if r.returncode == 0:
            out[name] = "executable"
    return out


def check_report(record: dict) -> list[str]:
    local: list[str] = []
    if record.get("schema") != "actinv-p26b-g0-seal-1":
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
    core = record.get("identity_pins", {}).get("avila_core", {})
    if core.get("status") != "pinned" or not core.get("head"):
        local.append("avila core identity not pinned")
    ukdd = record.get("identity_pins", {}).get("ukdd_2020", {})
    if ukdd.get("status") != "pinned" or not ukdd.get("tree_sha256"):
        local.append("ukdd-2020 not pinned")
    fendl = record.get("identity_pins", {}).get("fendl_3_2c", {})
    if not fendl.get("sample_ok") or not fendl.get("manifest_sha256"):
        local.append("fendl identity/sample invalid")
    subset = record.get("executable_subset", {})
    if subset.get("nuclide_count") != EXPECTED_NUCLIDES:
        local.append("nuclide count != 36")
    elements = (set(subset.get("contract_elements", {}).get("base", {}))
                | set(subset.get("contract_elements", {}).get("impurity", {})))
    if elements != EXPECTED_ELEMENTS:
        local.append("contract element set mismatch")
    if subset.get("fendl_files_missing"):
        local.append("declared nuclides missing from FENDL corpus")
    part = record.get("partition", {}).get("p26b_qualifying", {})
    if part.get("consumption") != "once, at G3":
        local.append("qualifying partition not single-use")
    return local


def main() -> int:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol sha256 mismatch")
    if subprocess.run(["git", "merge-base", "--is-ancestor",
                       OPENING_COMMIT, "HEAD"], cwd=REPO).returncode != 0:
        failures.append("opening commit is not an ancestor of HEAD")
    record = json.loads(RECORD.read_text())

    for name, want in EXPECTED_VERDICTS.items():
        path = RESULTS / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
        elif json.loads(path.read_text()).get("verdict") != want:
            failures.append(f"{name} verdict mismatch")

    live = live_census()
    for name, blk in record.get("comparator_census", {}).items():
        if name in live and blk.get("status") != live[name]:
            failures.append(
                f"census {name}: recorded {blk.get('status')} != live {live[name]}")

    # Independently re-derive the FENDL inventory for declared elements.
    files = {p.name for p in (FENDL / "endf").glob("*.endf")}
    declared = record.get("executable_subset", {}).get("nuclides", [])
    truly_missing = []
    for iso in declared:
        want_suffix = "-" + iso.lower() + ".endf"
        if not any(n.lower().endswith(want_suffix) for n in files):
            truly_missing.append(iso)
    if truly_missing:
        failures.append(f"checker finds declared nuclides absent: {truly_missing}")

    # Independently verify the pinned binary identities still resolve.
    pins = record.get("identity_pins", {})
    for key, field in (("alara", "path"), ("njoy", "path")):
        p = Path(pins.get(key, {}).get(field, ""))
        if p.is_file() and sha256(p) != pins[key].get("sha256"):
            failures.append(f"{key} binary sha256 drifted")

    failures.extend(check_report(record))

    mutations = 0
    rejected = 0
    plants = [
        lambda r: r.update({"pass": False}),
        lambda r: r["prior_verdicts"].update({"verdict_p26.json": "P26-PASS"}),
        lambda r: r["identity_pins"]["avila_core"].update({"status": "unavailable"}),
        lambda r: r["executable_subset"].update({"nuclide_count": 20}),
        lambda r: r["identity_pins"]["ukdd_2020"].update({"tree_sha256": None}),
        lambda r: r["partition"]["p26b_qualifying"].update(
            {"consumption": "unlimited"}),
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
        "schema": "actinv-p26b-g0-check-1",
        "pass": not failures,
        "failures": failures,
        "protocol_sha256": PROTOCOL_SHA256,
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g0_p26b_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
