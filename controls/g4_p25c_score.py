#!/usr/bin/env python3
"""P25c G4 — score the patched union+anchor artifact through both
frozen partitions, mirroring ``g4_p25b_union_score``.

Partition (a): the P24 fresh IRDFF partition — the unchanged
``g4_p25b_score.stage_score`` context and scorer, run with the
``tendl-2025-patched`` union artifact as ``candidate``; output lands in
``results/g4_p25c_irdff_score.json``.

Partition (b): the P25 isomeric neutron held-out rows — the unchanged
``g5_p18b_heldout.score_heldout`` with the artifact as
``candidate-neutron.npz``; metrics restricted to neutron rows, with
the paired bootstrap keyed by the frozen P18b protocol hash.

Combined record: ``results/g4_p25c_score.json``.  All scoring is
retrospective — every benchmark partition is consumed evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25c-g4"
CENSUS = RESULTS / "g3_p25c_census.json"
BUILD = RESULTS / "g4_p25c_build.json"
IRDFF_SCORE = RESULTS / "g4_p25c_irdff_score.json"
OUTPUT = RESULTS / "g4_p25c_score.json"
CORPUS = "tendl_2025_patched"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def percentile(values, pct):
    if not values:
        return float("nan")
    return float(np.percentile(np.asarray(values, float), pct))


def metrics(abs_lns) -> dict:
    vals = [v for v in abs_lns if math.isfinite(v)]
    if not vals:
        return {"rows": 0}
    return {
        "rows": len(vals),
        "median_abs_ln": percentile(vals, 50),
        "p90_abs_ln": percentile(vals, 90),
        "within_10pct": sum(v <= math.log(1.1) for v in vals) / len(vals),
        "within_20pct": sum(v <= math.log(1.2) for v in vals) / len(vals),
        "within_30pct": sum(v <= math.log(1.3) for v in vals) / len(vals),
    }


def nonreg(b: dict, c: dict) -> dict:
    res = {"median_ok": None, "p90_ok": None, "coverage_ok": None}
    if b.get("rows") and c.get("rows"):
        res["median_ok"] = (
            c["median_abs_ln"] <= b["median_abs_ln"] + 0.005
            and c["median_abs_ln"] <= 1.01 * b["median_abs_ln"])
        res["p90_ok"] = (
            c["p90_abs_ln"] <= b["p90_abs_ln"] + 0.01
            and c["p90_abs_ln"] <= 1.01 * b["p90_abs_ln"])
        res["coverage_ok"] = all(
            c[k] >= b[k] - 0.01
            for k in ("within_10pct", "within_20pct", "within_30pct"))
    elif not c.get("rows"):
        res["unscored_candidate"] = True
    return res


def score_isomer_partition(artifact: Path, index_path: Path) -> dict:
    """Eligible neutron isomeric held-out rows only; the scorer runs
    over its full frozen ledger and the neutron slice is the eligible
    population.  Scorer-level blocks are recorded verbatim."""
    import g4_p18b_diagnostics as g4
    import g5_p18b_heldout as g5

    work = WORK / "heldout-work"
    work.mkdir(parents=True, exist_ok=True)
    for f in work.iterdir():
        f.unlink()
    shutil.copyfile(artifact, work / "candidate-neutron.npz")
    if index_path.is_file():
        shutil.copyfile(index_path, work / "candidate-neutron_index.json")
    out = g5.score_heldout(work=work, report=None)
    ledger = out["ledger"]

    neutron = [r for r in ledger if r["projectile"] == "neutron"]
    eligible = [r for r in neutron if r["status"] == "eligible"]
    status_hist = {}
    for r in eligible:
        s = (r.get("candidate") or {}).get("status", "absent")
        status_hist[s] = status_hist.get(s, 0) + 1

    def scored(rows, label):
        return [r[label]["ln_cm"] for r in rows
                if r.get(label, {}).get("ln_cm") is not None
                and math.isfinite(r[label]["ln_cm"])]

    paired = [r for r in eligible
              if r.get("baseline", {}).get("ln_cm") is not None
              and math.isfinite(r["baseline"]["ln_cm"])
              and r.get("candidate", {}).get("ln_cm") is not None
              and math.isfinite(r["candidate"]["ln_cm"])]
    base_m = metrics([abs(v) for v in scored(paired, "baseline")])
    cand_m = metrics([abs(v) for v in scored(paired, "candidate")])
    cand_all = metrics([abs(v) for v in scored(eligible, "candidate")])

    fam_pairs = defaultdict(list)
    for r in paired:
        fam_pairs[r["family_id"]].append({
            "baseline": {"ln_cm": r["baseline"]["ln_cm"]},
            "candidate": {"ln_cm": r["candidate"]["ln_cm"]}})
    boot = g4.paired_bootstrap(list(fam_pairs.values()),
                             bytes.fromhex(g4.PROTOCOL_SHA256))
    return {
        "ledger_rows": len(neutron),
        "eligible_rows": len(eligible),
        "candidate_status_histogram": status_hist,
        "candidate_scored_rows": len(scored(eligible, "candidate")),
        "candidate_metrics_all": cand_all,
        "paired_rows": len(paired),
        "paired_baseline": base_m,
        "paired_candidate": cand_m,
        "paired_bootstrap": boot,
        "nonreg": nonreg(base_m, cand_m),
        "scorer_overall": out.get("overall"),
        "scorer_per_projectile": out.get("per_proj"),
        "scorer_bootstrap": out.get("bootstrap"),
        "scorer_stats": out.get("stats"),
    }


def main() -> int:
    import g4_p25b_score as g4s

    build = json.loads(BUILD.read_text())
    npz = Path(build["npz"])
    index_path = Path(build["index"]) if build.get("index") \
        else npz.with_name(npz.stem + "_index.json")
    if not (npz.is_file() and build["build"]["exit"] == 0):
        raise RuntimeError("patched union artifact missing or failed build")

    # Partition (a): reuse the audited stage_score machinery verbatim.
    g4s.SCORE = IRDFF_SCORE
    art_block = {"npz": str(npz), "index": str(index_path),
                 "npz_sha256": build["npz_sha256"] or sha256(npz)}
    irdff = g4s.stage_score({"artifacts": {CORPUS: art_block}})

    # Partition (b): isomeric held-out rows.
    isomer = score_isomer_partition(npz, index_path)

    record = {
        "schema": "actinv-p25c-g4-score-1",
        "retrospective": True,
        "label": ("all scoring is retrospective: every benchmark "
                  "partition is consumed evidence"),
        "g3_census_sha256": sha256(CENSUS),
        "g4_build_sha256": sha256(BUILD),
        "g4_irdff_score_sha256": sha256(IRDFF_SCORE),
        "corpora": {
            CORPUS: {
                "irdff_partition": irdff["corpora"][CORPUS],
                "isomeric_partition": isomer,
                "artifact_sha256": sha256(npz),
                "index_sha256": sha256(index_path)
                if index_path.is_file() else None,
            },
        },
    }
    OUTPUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({
        "irdff": {"rows": irdff["corpora"][CORPUS].get("rows"),
                  "scored": irdff["corpora"][CORPUS].get("candidate_scored"),
                  "comparable":
                      irdff["corpora"][CORPUS].get("comparable_rows"),
                  "nonreg": irdff["corpora"][CORPUS].get("nonregression")},
        "isomer": {"eligible": isomer["eligible_rows"],
                   "scored": isomer["candidate_scored_rows"],
                   "paired": isomer["paired_rows"],
                   "nonreg": isomer["nonreg"]},
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
