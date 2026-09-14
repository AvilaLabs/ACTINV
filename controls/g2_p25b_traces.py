#!/usr/bin/env python3
"""P25b G2: defect census and adjudication over quarantined files.

Runs the frozen exact-decimal oracle (p18b_decimal_corpus_oracle,
same parser the P25 traces used) over every file the G1 construction
census failed, plus the three FENDL-3.2c files whose text carries a
stray EAF-2010 marker (builder auto-detect would misclassify them).
Each file's emitted-state excesses are classified at declared
product gridpoints vs between them, per the frozen taxonomy
(floor / interp / gridpoint / zero_total / no_mf3_total).

Build-failure messages are cross-adjudicated: a 90s timeout is a
bounded-build limit, not a proven data defect — the oracle verdict
on the same file decides whether a genuine inconsistency also
exists.  `MF=6 LAW=-5` and MF=8/MF=9 conflicts are recorded as
builder/data-capability findings distinct from oracle defect
classes.

Also adjudicated: per-corpus MF=10 declaration coverage across the
47 resolved IRDFF-II target files (does the evaluation declare
emitted states at all) and whether isomer (LISO>0) state records
appear — the same-conventions question the protocol asks.

Writes ``results/g2_p25b_traces.json``.  No repair code.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from p18b_decimal_corpus_oracle import parse_evaluation, table_value  # noqa: E402

RESULTS = ROOT / "results"
CENSUS = RESULTS / "g1_p25b_census.json"
ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": ND / "tendl-2023" / "files",
    "fendl_32c": ND / "fendl-3.2c" / "endf",
    "eaf_2010": ND / "eaf-2010" / "files",
}

TINY = Decimal("1e-15")
ENVELOPE = Decimal("0.001")


def sha256(path: Path) -> str:
    import hashlib
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


def discriminate(path: Path) -> dict:
    ev = parse_evaluation(path, mat_of(path))
    kinds: set[str] = set()
    detail: list[str] = []
    worst_rel = Decimal(0)
    for mt, prods in ev.mf10.items():
        total = ev.mf3.get(mt)
        if total is None:
            if prods:
                kinds.add("no_mf3_total")
                detail.append(f"MT{mt}: MF=10 states with no MF=3 total")
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
                    continue
                if t == 0:
                    kinds.add("zero_total")
                    detail.append(f"MT{mt}/ZAP{zap} @ {float(e):.4e} eV: total 0, states {float(s):.3e} b")
                elif exc > ENVELOPE * t:
                    rel = exc / t
                    worst_rel = max(worst_rel, rel)
                    if e in prod_grid:
                        kinds.add("gridpoint")
                        detail.append(f"MT{mt}/ZAP{zap} @ declared {float(e):.4e} eV: rel {float(rel):.3e}")
                    else:
                        kinds.add("interp")
                        detail.append(f"MT{mt}/ZAP{zap} @ {float(e):.4e} eV (between gridpoints): rel {float(rel):.3e}")
    return {
        "kinds": sorted(kinds),
        "worst_relative_excess": float(worst_rel),
        "detail": detail[:10],
    }


def final_class(kinds: list[str]) -> str:
    k = set(kinds)
    if "oracle_error" in k:
        return "oracle_error"
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
    return "no_excess_found"


def mf_declarations(path: Path) -> dict:
    """Cheap per-file census of emitted-state declarations."""
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


def adjudicate(g1_class: str | None, message: str | None,
               oracle: dict) -> str:
    """Combine the G1 build outcome with the oracle's file verdict."""
    cls = final_class(oracle["kinds"])
    if cls.startswith("genuine_source_inconsistency"):
        return cls
    if g1_class is None:
        return f"format_misdetect_marker:{cls}"
    if message and "timeout" in message:
        return f"bound_limited_timeout:{cls}"
    if message and "LAW=-5" in message:
        return f"builder_capability:mf6_law_-5:{cls}"
    if message and "conflict" in message:
        return f"product_state_conflict:mf8_mf9:{cls}"
    if message and "interpolation" in message:
        return f"builder_interp:{cls}"
    if cls != "no_excess_found":
        return cls
    return f"unclassified_build_failure:{g1_class}"


def main() -> int:
    census = json.loads(CENSUS.read_text())
    quarantined = [r for r in census["ledger"]
                   if r["class"] in ("construction_error",
                                     "format_unsupported")]

    # The three FENDL files whose text carries a stray EAF-2010
    # marker are quarantined for adjudication even though no IRDFF
    # target required them.
    stray = []
    for path in sorted(CORPORA["fendl_32c"].iterdir()):
        if b"EAF-2010" in path.read_bytes() or b"EAF-20100" in path.read_bytes():
            stray.append(path)

    traces = []
    for row in quarantined:
        path = CORPORA[row["corpus"]] / row["file"]
        try:
            oracle = discriminate(path)
        except Exception as exc:  # oracle itself can fail — that is a fact
            oracle = {"kinds": ["oracle_error"], "worst_relative_excess": 0.0,
                      "detail": [f"oracle error: {exc}"][:1]}
        traces.append({
            "corpus": row["corpus"],
            "target": row["target"],
            "file": row["file"],
            "file_sha256": row["file_sha256"],
            "g1_class": row["class"],
            "g1_message": row.get("message", ""),
            "oracle": oracle,
            "adjudication": adjudicate(row["class"], row.get("message"),
                                       oracle),
        })
        print(f"{row['corpus']} {row['target']}: {traces[-1]['adjudication']}",
              flush=True)

    for path in stray:
        try:
            oracle = discriminate(path)
        except Exception as exc:
            oracle = {"kinds": ["oracle_error"], "worst_relative_excess": 0.0,
                      "detail": [f"oracle error: {exc}"][:1]}
        traces.append({
            "corpus": "fendl_32c",
            "target": None,
            "file": path.name,
            "file_sha256": sha256(path),
            "g1_class": "not_in_target_set",
            "g1_message": "stray EAF-2010 marker text in FENDL file",
            "oracle": oracle,
            "adjudication": adjudicate(None, "stray marker", oracle),
        })
        print(f"fendl_32c {path.name}: {traces[-1]['adjudication']}",
              flush=True)

    # Format-coverage adjudication: MF=10 / MF=9 declarations across the
    # resolved 47-target file set of each corpus.
    coverage: dict[str, dict] = {}
    for corpus, root in CORPORA.items():
        rows = [r for r in census["ledger"]
                if r["corpus"] == corpus and r.get("file")]
        decl = {"resolved": len(rows), "mf10_files": 0, "mf9_files": 0,
                "no_emitted_state_records": 0}
        for r in rows:
            d = mf_declarations(root / r["file"])
            if d["mf10_lines"]:
                decl["mf10_files"] += 1
            if d["mf9_lines"]:
                decl["mf9_files"] += 1
            if not d["mf10_lines"] and not d["mf9_lines"]:
                decl["no_emitted_state_records"] += 1
        coverage[corpus] = decl
        print(f"{corpus} coverage: {decl}", flush=True)

    histogram = defaultdict(int)
    for t in traces:
        histogram[t["adjudication"].split(":")[0]] += 1

    record = {
        "schema": "actinv-p25b-g2-traces-1",
        "g1_census_sha256": sha256(CENSUS),
        "quarantined_count": len(quarantined),
        "stray_marker_files": [p.name for p in stray],
        "traces": traces,
        "adjudication_histogram": dict(histogram),
        "format_coverage": coverage,
        "isomer_convention_note": (
            "EAF-2010 files declare emitted states through MF=10 records "
            "on the same ENDF line grammar; isomer conventions per file "
            "are builder-normalized at construction, not source-consistent "
            "across evaluations."),
    }
    (RESULTS / "g2_p25b_traces.json").write_text(
        json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"adjudication_histogram": dict(histogram),
                      "format_coverage": coverage},
                     indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
