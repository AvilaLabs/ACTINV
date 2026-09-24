#!/usr/bin/env python3
"""P46 G3 conformance — sealed-code identity, corpus integrity,
refusal probes, and no-fabrication checks before sealed scoring.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p46_corpora as p46c  # noqa: E402
import p46_score as p46s  # noqa: E402

SEAL = ROOT / "results/g0_p46_seals.json"
OUT = ROOT / "results/g3_p46_conformance.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(name, ok, detail=None):
    d = {"probe": name, "pass": bool(ok)}
    if detail is not None:
        d["detail"] = detail
    return d


def main() -> int:
    seal = json.loads(SEAL.read_text())
    checks = []

    # p1: sealed code identity — every frozen file matches its seal
    code = {
        "driver": ROOT / "controls/p46_corpora.py",
        "scorer": ROOT / "controls/p46_score.py",
        "report": ROOT / "controls/p46_report.py",
        "p44_bands": ROOT / "controls/p44_bands.py",
        "p44_band_coverage": ROOT / "controls/p44_band_coverage.py",
        "corpus_reader": ROOT / "controls/harness/fispact_io.py",
    }
    mism = {k: sha256(p) for k, p in code.items()
            if sha256(p) != seal["code_sha256"].get(k)}
    checks.append(check("sealed_code_identity", not mism, mism))

    # p2: corpus integrity — each admitted corpus sha matches the seal
    bad = {}
    for c, meta in seal["corpora"].items():
        actual = sha256(Path(meta["path"]))
        if actual != meta["sha256"]:
            bad[c] = actual
    checks.append(check("corpus_integrity", not bad, bad))

    # p3: corpus indexes present and parseable, target counts > 0
    counts = {}
    for c, p in p46s.INDEX_PATHS.items():
        try:
            j = json.loads(p.read_text())
            counts[c] = len(j.get("targets") or [])
        except Exception as e:
            counts[c] = f"err:{e}"
    checks.append(check(
        "corpus_indexes",
        all(isinstance(v, int) and v > 0 for v in counts.values()),
        counts))

    # p4: recommendation construction refuses missing evidence
    try:
        p46s.recommendation_row(
            "X", {"recommended": ["a"], "metric": "m",
                  "candidates": {"a": {"n_scored": 1,
                                       "median_ce": 1.0,
                                       "mean_abs_ln_ce": 0.0}}},
            "", "", {})
        refused = False
    except (ValueError, KeyError):
        refused = True
    checks.append(check("evidence_refusal", refused))

    # p5: no-fabrication — scoring a row for a corpus that never ran
    # cannot produce a scored outcome
    rows = [{"corpus": "eaf-2010", "material": "Fe",
             "experiment": "1996exp_5min", "status": "failed",
             "error": "x"}]
    sc = p46s.score_ledger(rows, {"eaf-2010": {26}})
    cell = sc["material_tables"].get("eaf-2010|Fe") or {}
    checks.append(check(
        "no_fabricated_scores",
        cell.get("n_scored") == 0 and cell.get("n_failed") == 1,
        cell))

    # p6: element coverage extraction is real (differs per corpus)
    elems = {c: len(p46s.corpus_elements(p))
             for c, p in p46s.INDEX_PATHS.items()}
    checks.append(check(
        "element_coverage_real",
        len(set(elems.values())) >= 3 and elems["fendl-3.2c"] < 15,
        elems))

    out = {"spec": "actinv-p46-g3c-1",
           "probes": checks,
           "all_pass": all(c["pass"] for c in checks)}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "probes": {c["probe"]: c["pass"]
                                 for c in checks}}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
