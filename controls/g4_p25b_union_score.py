#!/usr/bin/env python3
"""P25b G4 — score the union+anchor artifacts through both frozen
folds and append results to ``results/g4_p25b_union.json``.

Partition (a): the P24 fresh IRDFF partition — the unchanged
``g4_p25b_score.stage_score`` context and scorer, run with each union
artifact as ``candidate``; output is redirected to
``results/g4_p25b_union_irdff_score.json`` so the earlier (v1.0.1
artifact) score record stays distinct.

Partition (b): the P25 isomeric neutron held-out rows — the unchanged
``g5_p18b_heldout.score_heldout`` with each union artifact as
``candidate-neutron.npz``; partition metrics restricted to
``projectile == "neutron"`` rows (the P25b eligible slice).
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

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25b-g4-union"
RECORD = RESULTS / "g4_p25b_union.json"
IRDFF_SCORE = RESULTS / "g4_p25b_union_irdff_score.json"

CORPORA = ["tendl_2023", "fendl_32c", "eaf_2010"]


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
    """P25b population (b): eligible neutron isomeric held-out rows
    only.  The scorer runs over its full frozen ledger; the neutron
    slice is this phase's eligible population.  The scorer's own
    overall/per-projectile blocks are recorded verbatim."""
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

    record = json.loads(RECORD.read_text())
    aug = record.get("anchor_augmentation") or {}

    # Partition (a): reuse the audited stage_score machinery verbatim
    # against the union+anchor artifacts.
    build = {"artifacts": {}}
    for corpus in CORPORA:
        art = (aug.get("corpora") or {}).get(corpus, {}).get("artifact") \
            or record["corpora"][corpus]["artifact"]
        npz = Path(art["npz"])
        index_path = npz.with_name(npz.stem + "_index.json")
        assert npz.is_file() and art["exit"] == 0, \
            f"{corpus}: artifact missing or failed build"
        build["artifacts"][corpus] = {
            "npz": str(npz), "index": str(index_path),
            "npz_sha256": art.get("npz_sha256") or sha256(npz)}
    g4s.SCORE = IRDFF_SCORE
    irdff = g4s.stage_score(build)

    for corpus in CORPORA:
        art = (aug.get("corpora") or {}).get(corpus, {}).get("artifact") \
            or record["corpora"][corpus]["artifact"]
        npz = Path(art["npz"])
        index_path = npz.with_name(npz.stem + "_index.json")
        isomer = score_isomer_partition(npz, index_path)
        record["corpora"][corpus]["scoring"] = {
            "irdff_partition": irdff["corpora"][corpus],
            "isomeric_partition": isomer,
            "artifact_sha256": sha256(npz),
            "index_sha256": sha256(index_path)
            if index_path.is_file() else None,
        }
        print(json.dumps({
            corpus: {
                "irdff": {"rows": irdff["corpora"][corpus].get("rows"),
                          "comparable":
                              irdff["corpora"][corpus].get("comparable_rows"),
                          "cand_median":
                              (irdff["corpora"][corpus]
                               .get("candidate_metrics") or {})
                              .get("median_abs_ln"),
                          "nonreg": irdff["corpora"][corpus]
                              .get("nonregression")},
                "isomer": {"eligible": isomer["eligible_rows"],
                           "scored": isomer["candidate_scored_rows"],
                           "paired": isomer["paired_rows"],
                           "cand_median":
                               isomer["paired_candidate"].get("median_abs_ln"),
                           "nonreg": isomer["nonreg"]},
            }}, indent=1), flush=True)

    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
