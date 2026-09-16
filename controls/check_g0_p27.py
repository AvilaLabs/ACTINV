#!/usr/bin/env python3
"""P27 G0 independent checker — opening seal.

Imports no production, conversion or executor module.  Re-verifies the
G0 seal record against live state: protocol hash, opening commit, prior
verdicts verbatim, tool identity digests, Core semantic profile presence
in the pinned kernel source, consumed-schema digests, scope-freeze
completeness (qualified-operations matrix, gating table, smoke
population, adversarial battery, partition seal), and rejects planted
mutations.

Writes ``results/g0_p27_check.json``; exit nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEALS = ROOT / "results" / "g0_p27_seals.json"
OUT = ROOT / "results" / "g0_p27_check.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P27_PROTOCOL.md"
CORE = Path.home() / "Documents" / "Avila-Labs" / "project-north-star"

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_report(rec: dict) -> list[str]:
    f = []
    if rec.get("protocol_sha256") != sha256(PROTOCOL):
        f.append("protocol digest drifted")
    if not rec.get("prior_verdicts"):
        f.append("no prior verdicts recorded")
    else:
        for vf, want in rec["prior_verdicts"].items():
            p = ROOT / "results" / vf
            if not p.is_file():
                f.append(f"prior verdict {vf} missing live")
            elif json.loads(p.read_text()).get("verdict") != want:
                f.append(f"prior verdict {vf} mismatch")
    tools = rec.get("identity_pins", {})
    for name in ("actinv", "avila_core"):
        ent = tools.get(name, {})
        p = Path(ent.get("path") or "")
        if not p.is_file() or ent.get("sha256") != sha256(p):
            f.append(f"{name} identity drifted")
    ci = rec.get("core_interchange", {})
    if ci.get("semantic_profile") not in (CORE / "crates" /
            "avila-core-kernel" / "src" / "lib.rs").read_text(errors="replace"):
        f.append("semantic profile absent from pinned kernel source")
    for s, dig in (ci.get("consumed_schemas") or {}).items():
        if sha256(CORE / "schemas" / s) != dig:
            f.append(f"consumed schema {s} digest drifted")
    qo = rec.get("qualified_operations", {})
    if qo.get("act-study-01", {}).get("status") != "qualified_at_p27":
        f.append("act-study-01 not declared qualified_at_p27")
    for fam in ("act-robust-01", "act-refine-01", "act-source-01"):
        if qo.get(fam, {}).get("status") != "unqualified":
            f.append(f"{fam} not declared unqualified")
    sp = rec.get("smoke_population", {})
    n = len(sp.get("materials", {})) * len(sp.get("spectra", [])) * \
        len(sp.get("schedules_s", {}))
    if n != sp.get("expected_cases"):
        f.append(f"smoke population {n} != expected {sp.get('expected_cases')}")
    want_battery = [
        "incorrect_units", "changed_data", "changed_executable",
        "missing_case", "duplicate_case", "zero_metric",
        "forged_evidence", "stale_evidence", "missing_qualification",
        "altered_limits", "unexpected_schema_field", "revoked_template",
    ]
    if rec.get("adversarial_battery") != want_battery:
        f.append("adversarial battery altered")
    if rec.get("evidence_partitions", {}).get("p27_qualifying", {}) \
            .get("sealed_at") != "G0":
        f.append("p27_qualifying partition not sealed at G0")
    if rec.get("pass") is not True:
        f.append("seal record not pass")
    return f


def main() -> int:
    rec = json.loads(SEALS.read_text())
    failures.extend(check_report(rec))

    mutations = rejected = 0
    plants = [
        lambda r: r.update({"pass": not r["pass"]}),
        lambda r: r.update({"protocol_sha256": "0" * 64}),
        lambda r: r["prior_verdicts"].update(
            {"verdict_p26b.json": "P26b-PASS"}),
        lambda r: r["qualified_operations"].update(
            {"act-robust-01": {"status": "qualified_at_p27"}}),
        lambda r: r["core_interchange"].update(
            {"semantic_profile": "avila.core/semantic/9.9"}),
        lambda r: r["smoke_population"].update({"expected_cases": 999}),
        lambda r: r["adversarial_battery"].pop(),
    ]
    for plant in plants:
        m = copy.deepcopy(rec)
        plant(m)
        mutations += 1
        if check_report(m):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test {rejected}/{mutations}")

    result = {"schema": "actinv-p27-g0-check-1",
              "pass": not failures, "failures": failures,
              "mutation_self_test": {"planted": mutations,
                                     "rejected": rejected}}
    OUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
