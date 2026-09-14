#!/usr/bin/env python3
"""P24 G2 independent checker.

Verifies the scorer-audit record without importing the scorer or the
definition module: rehashes the audited modules, checks the audit's
static checks and fixture outcomes are all green, verifies the frozen
scorer hash matches the recorded freeze identity, and replants the
mutations on the record itself.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
AUDIT = RESULTS / "g2_p24_scorer_audit.json"
DEFS_RECORD = RESULTS / "g1_p24_definitions.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P24_PROTOCOL.md"
PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"
SCORER = REPO / "controls" / "p24_scorer.py"
DEFINITIONS = REPO / "controls" / "p24_definitions.py"
P17_SCORING = REPO / "controls" / "p17_scoring.py"
G1_CHECK = RESULTS / "g1_p24_check.json"
FRESH_TABLES = [26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 41, 42, 43, 44, 45, 46]

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_record(record: dict, live_scorer_hash: str | None = None) -> list[str]:
    local = []
    if live_scorer_hash is not None:
        if record.get("freeze", {}).get("scorer_sha256") != live_scorer_hash:
            local.append("freeze scorer hash mismatch")
    if not record.get("protocol_sha256_verified"):
        local.append("protocol_sha256_verified is false")
    if not record.get("pass"):
        local.append("audit record pass is false")
    if not all(c.get("pass") for c in record.get("static_checks", [])):
        local.append("a static check failed")
    if len(record.get("fixtures", [])) != 27:
        local.append("fixture count is not the frozen 27")
    if not all(f.get("pass") for f in record.get("fixtures", [])):
        local.append("a fixture failed")
    mt = record.get("mutation_tests", {})
    if mt.get("plants", 0) < 1 or mt.get("rejected") != mt.get("plants"):
        local.append("mutation plants not all rejected")
    return local


def main() -> int:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol hash mismatch")
    record = json.loads(AUDIT.read_text())

    # rehash audited modules against the recorded hashes
    for rel, recorded in record.get("module_hashes", {}).items():
        p = REPO / rel
        if not p.exists():
            failures.append(f"audited module missing: {rel}")
        elif sha256(p) != recorded:
            failures.append(f"audited module hash mismatch: {rel}")

    # freeze identity must match live files
    freeze = record.get("freeze", {})
    if freeze.get("scorer_sha256") != sha256(SCORER):
        failures.append("freeze scorer hash != live scorer")
    if freeze.get("definitions_sha256") != sha256(DEFINITIONS):
        failures.append("freeze definitions hash != live module")

    # scorer must import the frozen modules, not reimplement
    src = SCORER.read_text()
    if "from p24_definitions import" not in src:
        failures.append("scorer does not import frozen definitions")
    if "from p17_scoring import" not in src:
        failures.append("scorer does not import unchanged P17 metrics")
    if re.search(r"np\.quantile|exp\(.*mean\(.*log", src):
        failures.append("scorer reimplements metric arithmetic")
    # scorer must not hard-code fresh-table row counts or values
    if re.search(r"EXPECTED_ROWS\s*=\s*\{[^}]*\b(2[6-9]|3[0-5]|4[1-6])\b", src):
        failures.append("scorer hard-codes fresh-table expected rows")

    # every fresh table has a frozen spec; no consumed table is re-scored
    specs = set(record.get("module_hashes", {}))
    audit_src_tables = set()
    m = re.search(r"TABLE_SPECS[^=]*=\s*\{(.*?)\n\}", src, re.S)
    if m:
        audit_src_tables = set(int(x) for x in re.findall(r"^\s{4}(\d+):", m.group(1), re.M))
    if audit_src_tables != set(FRESH_TABLES):
        failures.append(f"TABLE_SPECS {sorted(audit_src_tables)} != sealed fresh set")

    # the G1 record must be green for the audit to rest on it
    if not json.loads(G1_CHECK.read_text()).get("pass"):
        failures.append("G1 check record is not green")
    defs = json.loads(DEFS_RECORD.read_text())
    if defs["frozen_definition_module"]["sha256"] != sha256(DEFINITIONS):
        failures.append("definition module hash differs from frozen G1 record")

    # record-level checks + mutations
    local = check_record(record, sha256(SCORER))
    failures.extend(local)

    mutations = 0
    rejected = 0
    plants = [
        lambda r: r.update({"pass": False}),
        lambda r: r["static_checks"][0].update({"pass": False}),
        lambda r: r["fixtures"].clear(),
        lambda r: r["freeze"].update({"scorer_sha256": "0" * 64}),
        lambda r: r["mutation_tests"].update(rejected=0),
    ]
    for plant in plants:
        m_ = copy.deepcopy(record)
        plant(m_)
        mutations += 1
        if check_record(m_, sha256(SCORER)):
            rejected += 1
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p24-g2-check-1",
        "pass": not failures,
        "failures": failures,
        "protocol_sha256": PROTOCOL_SHA256,
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g2_p24_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
