#!/usr/bin/env python3
"""P22-G3: one-time re-score of the sealed P17 held-out partition.

Reads ``results/g5_p17_heldout.json`` — the sealed 94-row partition —
exactly once and re-scores every row through the unchanged
``controls/p17_scoring.py`` fold. Before scoring, the control verifies
that every sealed source identity still matches the files on disk:
``p17_scoring.py``, ``p17_heldout.py``, ``g5_p17_heldout.py``,
``check_g5_p17.py``, the P17 protocol and Amendment 1.

The reproduced family metrics must equal the sealed ``family_metrics``
within relative ``1e-12`` (the fold is deterministic; the band only
admits platform float noise). The outcome is published as-is: the
held-out families remain FAIL under P17's frozen gates, and this record
preserves that verdict verbatim — no metric, exclusion, eligibility or
threshold is changed by this re-score.

Writes ``results/g3_p22_heldout.json``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SEALED = ROOT / "results/g5_p17_heldout.json"
RESULT = ROOT / "results/g3_p22_heldout.json"

sys.path.insert(0, str(ROOT / "controls"))

import p17_scoring  # noqa: E402

REL_TOL = 1e-12

SOURCE_MAP = {
    "control_source_sha256": "controls/g5_p17_heldout.py",
    "checker_source_sha256": "controls/check_g5_p17.py",
    "helper_source_sha256": "controls/p17_heldout.py",
    "scoring_source_sha256": "controls/p17_scoring.py",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def close(a, b, tol: float = REL_TOL) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    a, b = float(a), float(b)
    scale = max(abs(a), abs(b))
    if scale == 0.0:
        return a == b
    return abs(a - b) / scale <= tol


def main() -> None:
    sealed = json.loads(SEALED.read_text(encoding="utf-8"))
    failures: list[str] = []

    # -- sealed source identities ----------------------------------------
    source_checks = {}
    for field, rel in SOURCE_MAP.items():
        path = ROOT / rel
        observed = sha256(path)
        expected = sealed.get(field)
        source_checks[field] = {
            "path": rel, "expected": expected, "observed": observed,
            "match": observed == expected,
        }
        if observed != expected:
            failures.append(f"{field}: {rel} hash drifted from the sealed record")
    for field, rel in (
        ("protocol_sha256", "protocols/ACTINV-P17_PROTOCOL.md"),
        ("amendment_sha256", "protocols/ACTINV-P17_AMENDMENT_1.md"),
    ):
        observed = sha256(ROOT / rel)
        source_checks[field] = {
            "path": rel, "expected": sealed.get(field), "observed": observed,
            "match": observed == sealed.get(field),
        }
        if observed != sealed.get(field):
            failures.append(f"{field}: {rel} hash drifted from the sealed record")

    # -- row accounting ---------------------------------------------------
    rows = sealed.get("rows") or []
    row_counts = sealed.get("row_counts") or {}
    per_family: dict[str, int] = {}
    for row in rows:
        short = str(row["family"]).split("_", 1)[0]
        per_family[short] = per_family.get(short, 0) + 1
    if len(rows) != row_counts.get("source_total"):
        failures.append(
            f"sealed rows {len(rows)} != source_total {row_counts.get('source_total')}"
        )
    for family in ("H1", "H2", "H3"):
        if per_family.get(family) != row_counts.get(family):
            failures.append(
                f"family {family} row count {per_family.get(family)} "
                f"!= sealed {row_counts.get(family)}"
            )

    # -- one-time re-score through the unchanged fold --------------------
    reproduced = p17_scoring.all_family_metrics(rows)
    sealed_metrics = sealed.get("family_metrics") or {}
    comparisons: dict[str, dict[str, dict[str, object]]] = {}
    if set(reproduced) != set(sealed_metrics):
        failures.append("reproduced family set differs from sealed")
    for family, variants in sealed_metrics.items():
        comparisons[family] = {}
        for variant, sealed_m in variants.items():
            rep_m = (reproduced.get(family) or {}).get(variant)
            if rep_m is None:
                failures.append(f"{family}/{variant} missing from reproduced metrics")
                comparisons[family][variant] = {"match": False}
                continue
            keys = set(sealed_m) | set(rep_m)
            entry = {"match": True, "fields": {}}
            for key in sorted(keys):
                sv, rv = sealed_m.get(key), rep_m.get(key)
                if key in ("unscored_reasons",):
                    same = sv == rv
                else:
                    same = close(rv, sv)
                entry["fields"][key] = {
                    "sealed": sv, "reproduced": rv, "match": same,
                }
                if not same:
                    entry["match"] = False
                    failures.append(f"{family}/{variant}/{key} not reproduced")
            comparisons[family][variant] = entry

    # -- publish the outcome as-is ---------------------------------------
    record = {
        "schema": "actinv-p22-g3-heldout-1",
        "sealed_record": "results/g5_p17_heldout.json",
        "sealed_evidence_sha256": sealed.get("evidence_sha256"),
        "sealed_verdict": sealed.get("verdict"),
        "sealed_pass": sealed.get("pass"),
        "sealed_failure_reason": sealed.get("failure_reason"),
        "source_checks": source_checks,
        "row_counts": {
            "rows_scored_once": len(rows),
            "per_family": per_family,
            "sealed": row_counts,
        },
        "relative_tolerance": REL_TOL,
        "family_metric_comparisons": comparisons,
        "reproduced_family_metrics": reproduced,
        "verdict_preserved": sealed.get("verdict"),
        "note": (
            "held-out families remain FAIL under P17's frozen gates; "
            "this re-score changes no metric, exclusion or threshold"
        ),
        "failures": failures,
        "pass": not failures,
    }
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"pass": record["pass"], "failures": failures[:10]}, indent=2))
    raise SystemExit(0 if record["pass"] else 1)


if __name__ == "__main__":
    main()
