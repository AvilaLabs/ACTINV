#!/usr/bin/env python3
"""P24 G4 cause-ledger annotator.

Reads the produced G4 row ledger (never the sealed tables — this consumes the
authorized output only), derives the material-mismatch keys via the frozen P17
``mismatch_keys`` semantics, and emits ``results/p24_cause_ledger.json`` — an
append-only cause-ledger segment in the P17 format.  It then annotates
``results/g4_p24_fresh.json`` with the segment's path, digest and keys.

Attribution is derived, not fitted: for each mismatch key the control checks
whether the row's ``source_record.numeric_tokens`` contains a token within the
frozen 30% band of the calculated value while the bound ``measured_value`` is a
different token.  If so the printed-column binding is demonstrably the cause
(``measurement-definition``, confidence ``demonstrated``); otherwise the cause
is recorded ``unresolved`` with the same evidence pointers.  No scored value is
changed; ``signed_log_change`` is 0.0 because no substitution is performed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))

from p17_scoring import canonical_sha256, cause_entry, mismatch_keys  # noqa: E402

LEDGER = ROOT / "results" / "g4_p24_row_ledger.json"
REPORT = ROOT / "results" / "g4_p24_fresh.json"
OUT = ROOT / "results" / "p24_cause_ledger.json"
SCHEMA = "actinv-p24-cause-ledger-segment-1"
PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def attribute(row: dict, variant: str) -> dict:
    calc = row["calculations"][variant]
    measured = row["source_record"].get("measured_value")
    tokens = [t for t in row["source_record"].get("numeric_tokens", [])
              if isinstance(t, (int, float))]
    near = [t for t in tokens
            if t != measured and abs(float(t) / float(calc["value"]) - 1.0) <= 0.30]
    evidence = [
        f"{row['row_id']}#source_record.source_line",
        f"{row['row_id']}#calculations.{variant}",
        f"{row['row_id']}#source_record.numeric_tokens",
    ]
    if near:
        evidence.append(
            "The frozen grammar bound the printed uncertainty-percent token "
            f"({measured}) as the measured value while the printed "
            f"barn-scale value ({near[0]}) sits within the 30% band of the "
            f"calculated {calc['value']:.4g}."
        )
        return dict(primary_cause="measurement-definition",
                    secondary_causes=(), confidence="demonstrated",
                    evidence=evidence)
    evidence.append(
        "No printed token within the 30% band of the calculated value was "
        "found in the source record; the mismatch stands unresolved."
    )
    return dict(primary_cause="unresolved",
                secondary_causes=(), confidence="unresolved",
                evidence=evidence)


def main() -> int:
    rows = json.loads(LEDGER.read_text())["rows"]
    by_id = {row["row_id"]: row for row in rows}
    entries = []
    for key in mismatch_keys(rows):
        row_id, _, variant = key.rpartition("::")
        row = by_id[row_id]
        attr = attribute(row, variant)
        entry = cause_entry(
            key,
            primary_cause=attr["primary_cause"],
            secondary_causes=attr["secondary_causes"],
            controlled_substitution=(
                "none — frozen output stands; the produced C/E is preserved "
                "verbatim and the cause is annotated, not repaired"
            ),
            signed_log_change=0.0,
            evidence=attr["evidence"],
            confidence=attr["confidence"],
        )
        entry["sequence"] = len(entries) + 1
        entry["entry_sha256"] = canonical_sha256(entry)
        entries.append(entry)

    segment = {
        "schema": SCHEMA,
        "protocol_sha256": PROTOCOL_SHA256,
        "append_only": True,
        "input_ledger": {"path": str(LEDGER.relative_to(ROOT)),
                         "sha256": sha256(LEDGER)},
        "scope": "P24 G4 authorized-read material mismatches",
        "taxonomy": ["solver", "chain-construction", "processor", "evaluation",
                     "decay-yield", "measurement-definition",
                     "unsupported-model", "unresolved"],
        "entries": entries,
    }
    OUT.write_text(json.dumps(segment, indent=1, sort_keys=True) + "\n")

    report = json.loads(REPORT.read_text())
    report["cause_ledger"] = {
        "path": str(OUT.relative_to(ROOT)),
        "sha256": sha256(OUT),
        "mismatch_keys": [e["mismatch_key"] for e in entries],
    }
    REPORT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")

    print(json.dumps({
        "written": str(OUT),
        "entries": len(entries),
        "keys": [e["mismatch_key"] for e in entries],
        "causes": [e["primary_cause"] for e in entries],
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
