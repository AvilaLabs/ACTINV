#!/usr/bin/env python3
"""P25b G2 independent checker.

Re-runs the frozen exact-decimal oracle over every quarantined file
independently (same pinned parser, independently re-executed
classification), recomputes the adjudication histogram and the
per-corpus MF=9/MF=10 declaration coverage, rehashes the G1 census
input, and rejects planted mutations of the trace record.

Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from p18b_decimal_corpus_oracle import parse_evaluation, table_value  # noqa: E402

RESULTS = ROOT / "results"
RECORD = RESULTS / "g2_p25b_traces.json"
CENSUS = RESULTS / "g1_p25b_census.json"
ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": ND / "tendl-2023" / "files",
    "fendl_32c": ND / "fendl-3.2c" / "endf",
    "eaf_2010": ND / "eaf-2010" / "files",
}
TINY = Decimal("1e-15")
ENVELOPE = Decimal("0.001")

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mat_of(path: Path) -> int:
    for ln in path.read_text("ascii", "replace").splitlines()[:12]:
        try:
            if int(ln[70:72]) == 1 and int(ln[72:75]) == 451:
                return int(ln[66:70])
        except (ValueError, IndexError):
            pass
    raise ValueError(f"{path.name}: no MF=1/MT=451 header")


def discriminate(path: Path) -> set[str]:
    """Independent re-derivation of the kind set only."""
    ev = parse_evaluation(path, mat_of(path))
    kinds: set[str] = set()
    for mt, prods in ev.mf10.items():
        total = ev.mf3.get(mt)
        if total is None:
            if prods:
                kinds.add("no_mf3_total")
            continue
        byzap: dict[int, list] = defaultdict(list)
        for pr in prods:
            if pr.zap >= 0:
                byzap[pr.zap].append(pr)
        for zap, plist in byzap.items():
            xs = sorted(set(total.x) | set().union(*(set(pr.table.x) for pr in plist)))
            prod_grid = set().union(*(set(pr.table.x) for pr in plist))
            for e in xs:
                t = table_value(total, e, "right", "value")
                s = sum(table_value(pr.table, e, "right", "value") for pr in plist)
                if s <= t:
                    continue
                exc = s - t
                if exc <= TINY:
                    kinds.add("floor")
                elif t == 0:
                    kinds.add("zero_total")
                elif exc > ENVELOPE * t:
                    kinds.add("gridpoint" if e in prod_grid else "interp")
    return kinds


def mf_declarations(path: Path) -> dict:
    mf10 = mf9 = 0
    for raw in path.read_bytes().split(b"\n"):
        line = raw.rstrip(b"\r")
        if len(line) < 75:
            continue
        mf = line[70:72]
        if mf == b"10":
            mf10 += 1
        elif mf == b" 9":
            mf9 += 1
    return {"mf10_lines": mf10, "mf9_lines": mf9}


def final_class(kinds) -> str:
    k = set(kinds)
    if "gridpoint" in k:
        return "genuine_source_inconsistency:gridpoint_excess"
    if "zero_total" in k:
        return "genuine_source_inconsistency:zero_total_with_partials"
    if "no_mf3_total" in k:
        return "missing_total_or_grid_contract"
    if "interp" in k:
        return "grid_density_interpolation_artifact"
    if "floor" in k:
        return "floor_artifact_only"
    if "oracle_error" in k:
        return "oracle_error"
    return "no_excess_found"


def live_values() -> tuple[dict, dict]:
    """Recompute the oracle kind sets and coverage once."""
    record = json.loads(RECORD.read_text())
    oracle: dict[str, list] = {}
    for t in record.get("traces", []):
        src = CORPORA[t["corpus"]] / t["file"]
        try:
            oracle[t["file"]] = sorted(discriminate(src))
        except Exception:
            oracle[t["file"]] = ["oracle_error"]
        print(f"rechecked {t['file']}: {oracle[t['file']]}", flush=True)
    census = json.loads(CENSUS.read_text())
    coverage: dict[str, dict] = {}
    for corpus in CORPORA:
        rows = [r for r in census["ledger"]
                if r["corpus"] == corpus and r.get("file")]
        live = {"resolved": len(rows), "mf10_files": 0, "mf9_files": 0,
                "no_emitted_state_records": 0}
        for r in rows:
            d = mf_declarations(CORPORA[corpus] / r["file"])
            live["mf10_files"] += bool(d["mf10_lines"])
            live["mf9_files"] += bool(d["mf9_lines"])
            live["no_emitted_state_records"] += (
                not d["mf10_lines"] and not d["mf9_lines"])
        coverage[corpus] = live
    return oracle, coverage


def check_record(record: dict, live_oracle: dict,
                 live_coverage: dict) -> list[str]:
    local: list[str] = []
    if record.get("schema") != "actinv-p25b-g2-traces-1":
        local.append("schema")
    if record.get("g1_census_sha256") != sha256(CENSUS):
        local.append("g1 census hash")
    hist = defaultdict(int)
    for t in record.get("traces", []):
        adj = t.get("adjudication", "")
        hist[adj.split(":")[0]] += 1
        if not adj:
            local.append(f"{t.get('file')}: no adjudication")
        src = CORPORA.get(t.get("corpus"), Path(".")) / t.get("file", "")
        if not src.is_file() or sha256(src) != t.get("file_sha256"):
            local.append(f"{t.get('file')}: file hash")
        kinds = t.get("oracle", {}).get("kinds")
        if not isinstance(kinds, list):
            local.append(f"{t.get('file')}: oracle kinds not a list")
        else:
            if sorted(kinds) != live_oracle.get(t.get("file")):
                local.append(f"{t.get('file')}: oracle kinds")
            if not adj.endswith(final_class(kinds)):
                local.append(f"{t.get('file')}: adjudication/kinds suffix")
    if dict(hist) != record.get("adjudication_histogram"):
        local.append("adjudication histogram")
    if record.get("quarantined_count") != sum(
            1 for t in record.get("traces", [])
            if t.get("g1_class") != "not_in_target_set"):
        local.append("quarantined count")
    for corpus, blk in record.get("format_coverage", {}).items():
        if blk != live_coverage.get(corpus):
            local.append(f"coverage {corpus}")
    return local


def main() -> int:
    record = json.loads(RECORD.read_text())
    live_oracle, live_coverage = live_values()

    for t in record.get("traces", []):
        if sorted(t["oracle"]["kinds"]) != live_oracle.get(t["file"]):
            failures.append(f"oracle {t['file']} mismatch")
    for corpus, blk in record.get("format_coverage", {}).items():
        if blk != live_coverage.get(corpus):
            failures.append(f"coverage {corpus} mismatch")

    failures.extend(check_record(record, live_oracle, live_coverage))

    mutations = 0
    rejected = 0
    plants = [
        lambda r: r["traces"][0].update({"adjudication": "x"}),
        lambda r: r["traces"][1]["oracle"].update({"kinds": ["gridpoint"]}),
        lambda r: r.update({"quarantined_count": 0}),
        lambda r: r["format_coverage"]["eaf_2010"].update({"mf10_files": 0}),
        lambda r: r.update({"g1_census_sha256": "0" * 64}),
    ]
    for plant in plants:
        m = copy.deepcopy(record)
        plant(m)
        mutations += 1
        if check_record(m, live_oracle, live_coverage):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p25b-g2-check-1",
        "pass": not failures,
        "failures": failures,
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    (RESULTS / "g2_p25b_check.json").write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
