#!/usr/bin/env python3
"""Independent checker for the P22 G3 held-out re-score.

Imports no ACTINV production, audit or scoring module — the family fold
is re-derived here from the sealed rows with an independent
implementation (numpy statistics over ``ratio_C_over_E``), then compared
against BOTH the sealed ``family_metrics`` and the G3 record's
``reproduced_family_metrics``. Also verifies:

- the frozen P22 protocol hash and opening-commit ancestry;
- every sealed source identity (scoring/control/helper/checker files,
  P17 protocol and amendment) still matches the files on disk;
- every sealed row is accounted for (94 rows; per-family counts);
- the preserved verdict is exactly the sealed FAIL — no reinterpretation;
- the record's ``pass`` flag is true.

``--self-test`` plants mutations into copies of the record and proves
each is rejected. Emits ``results/g3_p22_check.json``.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P22_PROTOCOL.md"
REPORT = ROOT / "results/g3_p22_heldout.json"
SEALED = ROOT / "results/g5_p17_heldout.json"
OUTPUT = ROOT / "results/g3_p22_check.json"

PROTOCOL_SHA256 = "86f8509f58780a82923fcfdd9996db0e557a1eba845f553a0835b64f8a3cf971"
OPENING_COMMIT = "c4a361f0e1c1929e508decdd33848a789dcd9ef0"
REL_TOL = 1e-12

WITHIN_10 = (0.9, 1.1)
WITHIN_20 = (0.8, 1.2)
WITHIN_30 = (1.0 / 1.3, 1.3)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def independent_family_metrics(rows: list[dict]) -> dict:
    """Independent re-implementation of the P17 fold."""
    from collections import Counter, defaultdict

    families: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        families[row["family"]].append(row)
    out: dict[str, dict] = {}
    for family in sorted(families):
        frows = families[family]
        variants = sorted({v for r in frows for v in r["calculations"]})
        out[family] = {}
        for variant in variants:
            scored = []
            reasons: Counter[str] = Counter()
            for row in frows:
                if row["inclusion"]["status"] != "scored":
                    reasons[row["inclusion"]["reason"]] += 1
                    continue
                calc = row["calculations"].get(variant)
                if calc is None:
                    reasons["variant_reaction_unavailable"] += 1
                    continue
                if calc["status"] != "scored":
                    reasons[calc["reason"]] += 1
                    continue
                scored.append(calc)
            ratios = np.asarray([e["ratio_C_over_E"] for e in scored], dtype=float)
            if len(ratios):
                alog = np.abs(np.log(ratios))
                metrics = {
                    "geometric_mean_C_over_E": float(np.exp(np.mean(np.log(ratios)))),
                    "median_abs_log_C_over_E": float(np.quantile(alog, 0.5, method="linear")),
                    "p90_abs_log_C_over_E": float(np.quantile(alog, 0.9, method="linear")),
                    "maximum_abs_log_C_over_E": float(np.max(alog)),
                    "fraction_within_10_percent": float(np.mean((ratios >= WITHIN_10[0]) & (ratios <= WITHIN_10[1]))),
                    "fraction_within_20_percent": float(np.mean((ratios >= WITHIN_20[0]) & (ratios <= WITHIN_20[1]))),
                    "fraction_within_30_percent": float(np.mean((ratios >= WITHIN_30[0]) & (ratios <= WITHIN_30[1]))),
                }
            else:
                metrics = {k: None for k in (
                    "geometric_mean_C_over_E", "median_abs_log_C_over_E",
                    "p90_abs_log_C_over_E", "maximum_abs_log_C_over_E",
                    "fraction_within_10_percent", "fraction_within_20_percent",
                    "fraction_within_30_percent",
                )}
            out[family][variant] = {
                "scored_rows": len(scored),
                "unscored_rows": len(frows) - len(scored),
                "unscored_reasons": dict(sorted(reasons.items())),
                **metrics,
            }
    return out


def metrics_close(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, dict) or isinstance(b, dict):
        return a == b
    a, b = float(a), float(b)
    scale = max(abs(a), abs(b))
    if scale == 0.0:
        return a == b
    return abs(a - b) / scale <= REL_TOL


def metrics_equal(ref: dict, other: dict, label: str, failures: list[str]) -> None:
    if set(ref) != set(other):
        failures.append(f"{label}: family set differs")
        return
    for family, variants in ref.items():
        if set(variants) != set(other[family]):
            failures.append(f"{label}: {family} variant set differs")
            continue
        for variant, ref_m in variants.items():
            other_m = other[family][variant]
            for key in set(ref_m) | set(other_m):
                if not metrics_close(ref_m.get(key), other_m.get(key)):
                    failures.append(f"{label}: {family}/{variant}/{key} differs")


def check_report(report: dict, sealed: dict, failures: list[str]) -> None:
    if report.get("schema") != "actinv-p22-g3-heldout-1":
        failures.append("schema is not actinv-p22-g3-heldout-1")
    if report.get("sealed_verdict") != sealed.get("verdict"):
        failures.append("preserved verdict differs from the sealed FAIL")
    if report.get("sealed_pass") is not False:
        failures.append("sealed_pass not recorded as false")
    if report.get("relative_tolerance") != REL_TOL:
        failures.append("recorded tolerance is not the frozen 1e-12")

    rows = sealed.get("rows") or []
    derived = independent_family_metrics(rows)
    metrics_equal(sealed.get("family_metrics") or {}, derived, "sealed-vs-independent", failures)
    metrics_equal(
        report.get("reproduced_family_metrics") or {}, derived,
        "reported-vs-independent", failures,
    )

    row_counts = report.get("row_counts") or {}
    if row_counts.get("rows_scored_once") != len(rows):
        failures.append("rows_scored_once does not equal the sealed row count")
    per_family = row_counts.get("per_family") or {}
    expected_family = {"H1": 40, "H2": 33, "H3": 21}
    for family, count in expected_family.items():
        if per_family.get(family) != count:
            failures.append(f"per-family count for {family} is not {count}")

    for field, observed in (report.get("source_checks") or {}).items():
        if observed.get("match") is not True:
            failures.append(f"source check {field} not recorded as matching")

    if report.get("pass") is not True:
        failures.append("record does not carry pass")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode == 0
    if not ancestor:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of HEAD")

    sealed_path = SEALED
    if not sealed_path.exists():
        failures.append("sealed P17 held-out record missing")
        sealed = {}
    else:
        sealed = json.loads(sealed_path.read_text(encoding="utf-8"))

    for field, rel in (
        ("scoring_source_sha256", "controls/p17_scoring.py"),
        ("control_source_sha256", "controls/g5_p17_heldout.py"),
        ("helper_source_sha256", "controls/p17_heldout.py"),
        ("checker_source_sha256", "controls/check_g5_p17.py"),
        ("protocol_sha256", "protocols/ACTINV-P17_PROTOCOL.md"),
        ("amendment_sha256", "protocols/ACTINV-P17_AMENDMENT_1.md"),
    ):
        if sealed and sha256(ROOT / rel) != sealed.get(field):
            failures.append(f"{field}: {rel} no longer matches the sealed hash")

    if not REPORT.exists():
        failures.append("results/g3_p22_heldout.json is missing")
    elif sealed:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), sealed, failures)
    return {
        "schema": "actinv-p22-g3-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    sealed = json.loads(SEALED.read_text(encoding="utf-8"))
    mutations = {
        "verdict_forged": lambda r: r.__setitem__("sealed_verdict", "P17-PASS"),
        "metric_forged": lambda r: r["reproduced_family_metrics"]["H1_SPR_III_table_23"]
            ["actinv_tendl2025_post_failure_diagnostic"].__setitem__("scored_rows", 40),
        "rows_count_forged": lambda r: r["row_counts"].__setitem__("rows_scored_once", 93),
        "source_check_forged": lambda r: r["source_checks"]["scoring_source_sha256"]
            .__setitem__("match", False),
        "tolerance_weakened": lambda r: r.__setitem__("relative_tolerance", 0.5),
        "pass_forged": lambda r: r.__setitem__("pass", False),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p22-g3-selftest-") as directory:
            planted = Path(directory) / "planted.json"
            planted.write_text(json.dumps(candidate), encoding="utf-8")
            failures: list[str] = []
            check_report(json.loads(planted.read_text(encoding="utf-8")), sealed, failures)
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
    result = run_checks()
    if not args.no_write:
        OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
