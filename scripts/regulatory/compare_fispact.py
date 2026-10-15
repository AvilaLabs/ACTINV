#!/usr/bin/env python3
"""Compare ACTINV radiological tables with FISPACT-II 2012 data files, for QA only.

The FISPACT files (`clear_2012`, `a2_2012`, `hazards_2012` of the UKAEA decay-2012 bundle)
carry no licence and must never be bundled or copied into this repository.  This script
only reads them from a path you give it and prints a report.

  clear     clearance limits (FISPACT Bq/kg, rows tagged IAEA-G-1.7) against a table of
            Bq/kg coefficients (e.g. bundled:euratom-2013-59-annex-vii-a)
  a2        A2 values (FISPACT TBq, rows tagged "A2 VALUE") against a table of A2 in Bq
            (e.g. bundled:us-49cfr173-a2)
  hazards   ingestion / inhalation coefficients (FISPACT Sv/Bq, rows tagged ICRP72)
            against a table made by fetch_icrp119.py --fispact-convention

A table argument is a JSON file path or `bundled:NAME` (read from crates/actinv-core/data/regulatory).
Each selected response is compared separately.  Nuclides are matched by (Z, A, isomer state).

Usage:
  python3 -I scripts/regulatory/compare_fispact.py clear bundled:euratom-2013-59-annex-vii-a CLEAR_2012
  python3 -I scripts/regulatory/compare_fispact.py a2 bundled:us-49cfr173-a2 A2_2012 --json out.json
  python3 -I scripts/regulatory/compare_fispact.py hazards icrp119.json HAZARDS_2012
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from pathlib import Path


def load_common():
    path = Path(__file__).resolve().parent / "regulatory_common.py"
    spec = importlib.util.spec_from_file_location("regulatory_common", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_common()
BUNDLED_DIR = Path(__file__).resolve().parents[2] / "crates" / "actinv-core" / "data" / "regulatory"

LIST_LIMIT = 40
TBQ_TO_BQ = 1e12


def load_table(reference: str) -> dict:
    path = BUNDLED_DIR / f"{reference[len('bundled:'):]}.json" if reference.startswith("bundled:") else Path(reference)
    table = json.loads(path.read_text(encoding="utf-8"))
    if table.get("format") != C.TABLE_FORMAT:
        raise ValueError(f"{path}: not an {C.TABLE_FORMAT} file")
    return table


def key_to_za(key: str) -> int:
    parsed = C.parse_nuclide(key)
    if parsed is None:
        raise ValueError(f"bad nuclide key {key!r}")
    _canonical, z, a, state = parsed
    return C.za_key(z, a, state)


def read_fispact(path: Path, kind: str) -> dict[int, tuple]:
    """ZA -> (values..., tag).  clear/a2: (value, tag).  hazards: (ingestion, inhalation, tag)."""
    rows: dict[int, tuple] = {}
    if kind == "hazards":
        pattern = re.compile(r"^([A-Za-z]{1,2})\s*-\s*(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
    else:
        pattern = re.compile(r"^\s*\d+\s+([A-Za-z]{1,2})\s*-\s*(\S+)\s+(\d+)\s+(\S+)\s+(.+?)\s*$")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        za = int(match.group(3))
        if kind == "hazards":
            rows[za] = (float(match.group(4)), float(match.group(5)), match.group(6))
        else:
            rows[za] = (float(match.group(4)), match.group(5))
    if not rows:
        raise ValueError(f"{path}: no data rows recognised")
    return rows


def close(a: float, b: float, rtol: float) -> bool:
    return math.isclose(a, b, rel_tol=rtol, abs_tol=0.0)


def compare(label: str, ours: dict[str, float], theirs: dict[int, float], rtol: float, names: dict[int, str]) -> dict:
    ours_by_za = {key_to_za(k): (k, v) for k, v in ours.items()}
    common = sorted(set(ours_by_za) & set(theirs))
    mismatches = [
        {"nuclide": ours_by_za[za][0], "actinv": ours_by_za[za][1], "fispact": theirs[za], "ratio_actinv_over_fispact": ours_by_za[za][1] / theirs[za] if theirs[za] else None}
        for za in common
        if not close(ours_by_za[za][1], theirs[za], rtol)
    ]
    return {
        "response": label,
        "compared": len(common),
        "match": len(common) - len(mismatches),
        "match_rate": (len(common) - len(mismatches)) / len(common) if common else None,
        "mismatches": mismatches,
        "only_in_actinv_table": sorted(ours_by_za[za][0] for za in set(ours_by_za) - set(theirs)),
        "only_in_fispact_explicit_rows": sorted(names[za] for za in set(theirs) - set(ours_by_za)),
        "rtol": rtol,
    }


def report(result: dict) -> None:
    rate = result["match_rate"]
    print(
        f"\n{result['response']}: compared {result['compared']}, equal within rtol {result['rtol']:g}: "
        f"{result['match']}" + (f" ({100 * rate:.1f}%)" if rate is not None else "")
    )
    for mismatch in result["mismatches"][:LIST_LIMIT]:
        print(f"  mismatch {mismatch['nuclide']}: ACTINV {mismatch['actinv']:.6g} FISPACT {mismatch['fispact']:.6g}")
    if len(result["mismatches"]) > LIST_LIMIT:
        print(f"  ... {len(result['mismatches']) - LIST_LIMIT} more mismatches")
    for field, text in (
        ("only_in_actinv_table", "in the ACTINV table, not an explicit FISPACT row"),
        ("only_in_fispact_explicit_rows", "explicit FISPACT row, not in the ACTINV table"),
    ):
        items = result[field]
        shown = ", ".join(items[:LIST_LIMIT]) + (" ..." if len(items) > LIST_LIMIT else "")
        print(f"  {len(items)} {text}" + (f": {shown}" if items else ""))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("kind", choices=("clear", "a2", "hazards"))
    parser.add_argument("table", help="table JSON path or bundled:NAME")
    parser.add_argument("fispact_file", type=Path, help="clear_2012, a2_2012 or hazards_2012")
    parser.add_argument("--response", action="append", help="response id to compare (default: all)")
    parser.add_argument("--rtol", type=float, help="relative tolerance (default 1e-6; 0.02 for hazards)")
    parser.add_argument("--ingestion-response", default="ingestion-adult-fispact-max")
    parser.add_argument("--inhalation-response", default="inhalation-adult-fispact-max")
    parser.add_argument("--json", type=Path, help="also write the full result here")
    args = parser.parse_args(argv)

    table = load_table(args.table)
    fispact = read_fispact(args.fispact_file, args.kind)
    results = []

    def names_of(rows):
        return {za: f"Z{za // 10000}-A{za // 10 % 1000}-s{za % 10}" for za in rows}

    if args.kind == "hazards":
        rtol = args.rtol if args.rtol is not None else 0.02
        responses = {r["id"]: r for r in table["responses"]}
        for label, rid, column in (
            ("ingestion", args.ingestion_response, 0),
            ("inhalation", args.inhalation_response, 1),
        ):
            if rid not in responses:
                print(f"error: response {rid!r} not in the table (build it with --fispact-convention)", file=sys.stderr)
                return 1
            explicit = {za: row[column] for za, row in fispact.items() if row[2] == "ICRP72"}
            results.append(compare(f"{label} [{rid}] vs FISPACT ICRP72 rows", responses[rid]["coefficients"], explicit, rtol, names_of(explicit)))
    else:
        rtol = args.rtol if args.rtol is not None else 1e-6
        for response in table["responses"]:
            if args.response and response["id"] not in args.response:
                continue
            if args.kind == "clear":
                explicit = {za: row[0] for za, row in fispact.items() if row[1] == "IAEA-G-1.7"}
                ours = response["coefficients"]  # Bq/kg
            else:
                explicit = {za: row[0] * TBQ_TO_BQ for za, row in fispact.items() if row[1] == "A2 VALUE"}
                ours = response["coefficients"]  # Bq
            results.append(compare(f"[{response['id']}]", ours, explicit, rtol, names_of(explicit)))

    print(f"{args.kind}: {args.table} vs {args.fispact_file.name}")
    for result in results:
        report(result)
    if args.json:
        args.json.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
