#!/usr/bin/env python3
"""P26b G1 independent checker — conversion ledger.

Imports no production, conversion or scoring module.  Re-verifies the
conversion record against the on-disk artifacts: ledger completeness,
per-nuclide KZA/ZA presence claims against the live libraries in both
directions (claimed-present must be present; ledgered-failed must be
absent from the claimed coverage), artifact digests, fail-closed smoke
outcomes, pass-vs-obligations consistency, and rejects planted
mutations.

Writes ``results/g1_p26b_check.json``; exit nonzero on any failure.
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
RECORD = RESULTS / "g1_p26b_conversion.json"
SEALS = json.loads((RESULTS / "g0_p26b_seals.json").read_text())
DECLARED = SEALS["executable_subset"]["nuclides"]

ND = Path.home() / "nuclear-data"
WORK = ND / "p26b-work"

ZNUM = {"V": 23, "CR": 24, "MN": 25, "FE": 26, "CO": 27, "NI": 28,
        "CU": 29, "NB": 41, "MO": 42, "AG": 47, "TA": 73, "W": 74}

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def kza_of(iso: str) -> int:
    sym, a = iso.split("-")
    liso = 1 if a.endswith("m") else 0
    return ZNUM[sym.upper()] * 10000 + int(a.rstrip("mn")) * 10 + liso


def za_of(iso: str) -> int:
    sym, a = iso.split("-")
    return ZNUM[sym.upper()] * 1000 + int(a.rstrip("mn"))


def live_dsv_parents() -> set:
    dsv = Path(RECORD.read_text() and
               json.loads(RECORD.read_text())
               ["artifacts"]["dsv"]["path"])
    parents = set()
    if dsv.is_file():
        for i, line in enumerate(
                dsv.read_text(errors="replace").splitlines()):
            if i == 0:
                continue
            parts = line.split()
            if len(parts) > 4:
                parents.add(int(parts[0]))
    return parents


def live_idx_kzas() -> set:
    idx = Path(json.loads(RECORD.read_text())
               ["artifacts"]["alara_lib"][".idx"]["path"])
    out = set()
    if idx.is_file():
        for line in idx.read_text(errors="replace").splitlines():
            m = re.match(r"^(\d+)\s", line)
            if m:
                out.add(int(m.group(1)))
    return out


def live_actinv_zas() -> set:
    npz = json.loads(RECORD.read_text())["artifacts"]["actinv_npz"]["path"]
    index = Path(str(npz).replace(".npz", "_index.json"))
    if index.is_file():
        return {t["za"] for t in
                json.loads(index.read_text())["targets"]}
    return set()


def expected_pass(record: dict) -> bool:
    """Recompute the G1 obligation predicate: pipeline ran end to end and
    every declared nuclide is ledgered — covered in both libraries or
    carrying a named failure class — with both smokes fail-closed."""
    if record.get("wrapper", {}).get("returncode") != 0:
        return False
    if record.get("convert_lib", {}).get("returncode") != 0:
        return False
    if record.get("actinv_build", {}).get("returncode") != 0:
        return False
    ledger = record.get("ledger", {})
    for iso in DECLARED:
        e = ledger.get(iso)
        if not isinstance(e, dict):
            return False
        covered = e.get("in_alara_lib") is True and e.get("in_actinv_lib") is True
        if not covered and e.get("failure_class") is None:
            return False
    smokes = record.get("fail_closed_smokes", {})
    if smokes.get("alara_absent_element", {}).get("fail_closed") is not True:
        return False
    if smokes.get("actinv_absent_nuclide", {}).get("fail_closed") is not True:
        return False
    return True


def check_report(record: dict) -> list[str]:
    local: list[str] = []
    if record.get("schema") != "actinv-p26b-g1-conversion-1":
        local.append("schema")
    if record.get("pass") != expected_pass(record):
        local.append("record pass inconsistent with G1 obligations")
    ledger = record.get("ledger", {})
    if set(ledger) != set(DECLARED):
        local.append("ledger does not cover exactly the declared set")
    cov = record.get("coverage", {})
    in_both = sum(1 for e in ledger.values()
                  if e.get("in_alara_lib") is True
                  and e.get("in_actinv_lib") is True)
    if cov.get("in_both_libraries") != in_both:
        local.append("coverage count disagrees with ledger")
    if cov.get("in_alara_only") is not None and sorted(
            iso for iso, e in ledger.items()
            if e.get("in_alara_lib") is True
            and e.get("in_actinv_lib") is not True) != \
            cov.get("in_alara_only"):
        local.append("in_alara_only list disagrees with ledger")
    smokes = record.get("fail_closed_smokes", {})
    if smokes.get("alara_absent_element", {}).get("fail_closed") is not True:
        local.append("alara fail-closed smoke not verified")
    if smokes.get("actinv_absent_nuclide", {}).get("fail_closed") is not True:
        local.append("actinv fail-closed smoke not verified")
    for iso, entry in ledger.items():
        in_alara = entry.get("in_alara_lib") is True
        in_actinv = entry.get("in_actinv_lib") is True
        cls = entry.get("failure_class")
        if cls is not None and in_alara and in_actinv:
            local.append(f"{iso} failed but claimed in both libraries")
        if not in_alara and cls is None:
            local.append(f"{iso} absent from alara lib without failure class")
        if not in_actinv and cls is None:
            local.append(f"{iso} absent from actinv lib without failure class")
        if entry.get("wrapper") != "converted" and cls is None:
            local.append(f"{iso} not converted without failure class")
    return local


def main() -> int:
    record = json.loads(RECORD.read_text())

    # Artifact digest re-verification against live filesystem.
    for name, blk in record.get("artifacts", {}).items():
        if not isinstance(blk, dict):
            continue
        if name == "alara_lib":
            for ext, ent in blk.items():
                p = Path(ent.get("path") or "")
                if p.is_file() and ent.get("sha256") != sha256(p):
                    failures.append(f"alara_lib{ext} digest drifted")
        else:
            p = Path(blk.get("path") or "")
            if p.is_file() and blk.get("sha256") != sha256(p):
                failures.append(f"{name} digest drifted")

    # Coverage claims re-derived from live library indexes, checked in both
    # directions: a nuclide claimed present must be present live, and a
    # nuclide ledgered as failed must be absent from the claimed coverage.
    dsv_parents = live_dsv_parents()
    idx_kzas = live_idx_kzas()
    actinv_zas = live_actinv_zas()
    ledger = record.get("ledger", {})
    for iso in DECLARED:
        e = ledger.get(iso, {})
        live_alara = kza_of(iso) in dsv_parents and kza_of(iso) in idx_kzas
        live_actinv = za_of(iso) in actinv_zas
        if e.get("in_alara_lib") is True and not live_alara:
            failures.append(f"{iso} claimed in ALARA lib but absent live")
        if e.get("in_alara_lib") is not True and live_alara:
            failures.append(f"{iso} ledgered absent but present in live "
                            "ALARA lib")
        if e.get("in_actinv_lib") is True and not live_actinv:
            failures.append(f"{iso} claimed in actinv lib but absent live")
        if e.get("in_actinv_lib") is not True and live_actinv:
            failures.append(f"{iso} ledgered absent but present in live "
                            "actinv lib")

    failures.extend(check_report(record))

    mutations = 0
    rejected = 0
    first = next(iso for iso in DECLARED
                 if record["ledger"].get(iso, {}).get("in_actinv_lib")
                 is True)
    gap = next(iso for iso in DECLARED
               if record["ledger"].get(iso, {}).get("failure_class")
               is not None)

    def drop_iso(r):
        r["ledger"][first]["in_alara_lib"] = False

    def claim_gap(r):
        r["ledger"][gap].update(
            {"in_actinv_lib": True, "failure_class": None})

    plants = [
        lambda r: r.update({"pass": not r["pass"]}),
        lambda r: r["coverage"].update({"in_both_libraries": 0}),
        drop_iso,
        lambda r: r["fail_closed_smokes"]["alara_absent_element"].update(
            {"fail_closed": False}),
        lambda r: r["ledger"][first].update({"failure_class": "x"}),
        claim_gap,
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
        "schema": "actinv-p26b-g1-check-1",
        "pass": not failures,
        "failures": failures,
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g1_p26b_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
