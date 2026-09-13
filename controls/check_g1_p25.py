#!/usr/bin/env python3
"""Independent checker for the P25 G1 cause census.

Imports no ACTINV production, audit or scoring module. Independently:

- re-reads the sealed P18b held-out ledger and recomputes the row
  outcome census (scored / zero_prediction_scored / construction_failed
  / undefined_ratio / eligibility) — requiring that all 1,945 rows are
  accounted exactly once and that 29 zero-prediction rows exist;
- re-derives family→file mapping for every ``build_failed_g3`` row and
  requires every mapped file to carry a re-derived failure entry — no
  outcome may rest on "quarantined in an earlier run";
- requires each recorded failure message to carry a taxonomy class and,
  for conservation failures, parsed magnitudes;
- recomputes the failure-class rollup from the per-file entries;
- requires the MF=8/MF=10 declaration scan to cover every quarantined
  file;
- rehashes the frozen protocol and Amendment A.

``--self-test`` plants mutations into copies of the record and proves
each is rejected. Emits ``results/g1_p25_check.json``.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P25_PROTOCOL.md"
AMENDMENT_A = ROOT / "protocols/ACTINV-P25_AMENDMENT_A.md"
REPORT = ROOT / "results/g1_p25_census.json"
OUTPUT = ROOT / "results/g1_p25_check.json"
HELDOUT = ROOT / "results/g5_p18b_heldout.json"

PROTOCOL_SHA256 = "ccd9bd98e513609532ed3a9871ce455be81828651e8ecf623d2a253a9f9fb552"
AMENDMENT_A_SHA256 = "214992c3709803b4991926251622731cf47071903fd255d51c03ceec17b483e8"

SYMBOLS = (
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg",
    "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr",
    "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br",
    "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd",
    "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La",
    "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er",
    "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au",
    "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
)
CODE = {"neutron": "n", "proton": "p", "deuteron": "d", "alpha": "a"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def family_target_file(projectile: str, family_id: str) -> str | None:
    try:
        target = family_id.split("|")[1]
        zt, at = int(target.split("-")[0]), int(target.split("-")[1])
        return f"{CODE[projectile]}-{SYMBOLS[zt - 1]}{at:03d}.tendl"
    except (IndexError, ValueError):
        return None


def independent_outcome(row: dict) -> str:
    """Own re-derivation of the row outcome — not the control's."""
    if row["status"] != "eligible":
        return f"eligibility:{row['status']}"
    cand = row.get("candidate") or {}
    status = cand.get("status")
    if status == "scored":
        cm = cand.get("cm")
        ln = cand.get("ln_cm")
        if cm == 0 or (ln is not None and not math.isfinite(ln)):
            return "zero_prediction_scored"
        return "scored"
    if status == "build_failed_g3":
        return "construction_failed:build_failed_g3"
    if status is None:
        return "construction_failed:unbuilt"
    return f"undefined_ratio:{status}"


def check_report(report: dict, failures: list[str]) -> None:
    if report.get("schema") != "actinv-p25-census-1":
        failures.append("schema is not actinv-p25-census-1")
    # Independent row census.
    ledger = json.loads(HELDOUT.read_text(encoding="utf-8"))["ledger"]
    expect: dict[str, dict[str, int]] = {}
    need_files: dict[str, set[str]] = {p: set() for p in CODE}
    zero = 0
    for row in ledger:
        outcome = independent_outcome(row)
        proj = row["projectile"]
        expect.setdefault(proj, {})[outcome] = (
            expect.setdefault(proj, {}).get(outcome, 0) + 1
        )
        if outcome == "construction_failed:build_failed_g3":
            fname = family_target_file(proj, row["family_id"])
            if fname:
                need_files[proj].add(fname)
        if outcome == "zero_prediction_scored":
            zero += 1
    if report.get("row_outcomes") != expect:
        failures.append("row_outcomes differ from independent recomputation")
    if zero != 29:
        failures.append(f"independent census counts {zero} zero-prediction rows, not 29")
    if len(report.get("zero_prediction_rows") or []) != 29:
        failures.append("record does not list 29 zero-prediction rows")
    total_rows = sum(sum(v.values()) for v in expect.values())
    if total_rows != 1945:
        failures.append(f"ledger has {total_rows} rows, not the sealed 1945")

    # Every construction-failed row's file must carry a failure entry.
    file_failures = report.get("file_failures") or {}
    for proj, names in need_files.items():
        got = set((file_failures.get(proj) or {}).keys())
        missing = names - got
        if missing:
            failures.append(
                f"{proj}: {len(missing)} construction-failed files lack "
                f"re-derived failure entries"
            )

    # Per-file entries: class present; conservation entries carry magnitudes.
    for proj, files in file_failures.items():
        for name, entry in files.items():
            cls = entry.get("class")
            if not cls:
                failures.append(f"{proj}/{name}: no taxonomy class")
            if cls in ("tiny_absolute_discrepancy", "conservation_excess_untraced"):
                for field in ("emitted_sum_barn", "runtime_total_barn",
                              "relative_excess", "absolute_excess_barn", "mt", "zap"):
                    if entry.get(field) is None:
                        failures.append(f"{proj}/{name}: missing {field}")
            if entry.get("exit") == 0:
                failures.append(f"{proj}/{name}: quarantined file now builds clean")
            if "earlier run" in (entry.get("message") or ""):
                failures.append(f"{proj}/{name}: reason carried forward, not re-derived")

    # Rollup must match per-file entries.
    rollup = report.get("failure_class_counts") or {}
    for proj, files in file_failures.items():
        expect_counts: dict[str, int] = {}
        for entry in files.values():
            c = entry.get("class", "unclassified")
            expect_counts[c] = expect_counts.get(c, 0) + 1
        if rollup.get(proj) != expect_counts:
            failures.append(f"{proj}: failure_class_counts rollup mismatch")

    # Declaration scan coverage.
    scan = report.get("declaration_scan") or {}
    for proj, files in file_failures.items():
        for name in files:
            if name not in scan:
                failures.append(f"declaration scan misses {name}")

    taxonomy = report.get("taxonomy") or {}
    for cls in ("processing_bug", "state_catalog_mapping",
                "tiny_absolute_discrepancy", "genuine_source_inconsistency",
                "zero_prediction_scored"):
        if cls not in (taxonomy.get("classes") or []):
            failures.append(f"taxonomy omits frozen class {cls}")


def run_checks() -> dict:
    failures: list[str] = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol digest differs from frozen")
    if sha256(AMENDMENT_A) != AMENDMENT_A_SHA256:
        failures.append("Amendment A digest differs from frozen")
    if not REPORT.exists():
        failures.append("results/g1_p25_census.json is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p25-g1-check-1",
        "protocol_sha256": sha256(PROTOCOL),
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    some_proj = next(iter(report["file_failures"]))
    some_file = next(iter(report["file_failures"][some_proj]))
    mutations = {
        "outcome_flip": lambda r: r["row_outcomes"]["neutron"].__setitem__(
            "scored", 9999
        ),
        "zero_drop": lambda r: r.__setitem__("zero_prediction_rows", []),
        "class_erase": lambda r: r["file_failures"][some_proj][some_file].pop(
            "class", None
        ),
        "rollup_lie": lambda r: r["failure_class_counts"][some_proj].__setitem__(
            "tiny_absolute_discrepancy", 0
        ),
        "scan_gap": lambda r: r["declaration_scan"].pop(some_file, None),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p25-g1-selftest-") as directory:
            planted = Path(directory) / "planted.json"
            planted.write_text(json.dumps(candidate), encoding="utf-8")
            failures: list[str] = []
            check_report(json.loads(planted.read_text(encoding="utf-8")), failures)
            rejected.append(bool(failures))
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(mutations)} report mutations rejected")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    record = run_checks()
    if not args.no_write:
        OUTPUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=1, sort_keys=True))
    raise SystemExit(0 if record["pass"] else 1)


if __name__ == "__main__":
    main()
