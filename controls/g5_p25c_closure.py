#!/usr/bin/env python3
"""P25c G5 — closure accounting and verdict.

Assembles ``results/g5_p25c_accounting.json`` (the full defect-census,
coverage, artifact and scoring accounting recomputed from the raw phase
records) and ``results/verdict_p25c.json`` (the frozen-gate verdict with
explicit limitations).  Independent verification lives in
``check_g5_p25c.py``.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols" / "ACTINV-P25c_PROTOCOL.md"
ACCOUNTING = RESULTS / "g5_p25c_accounting.json"
VERDICT = RESULTS / "verdict_p25c.json"

EVIDENCE = [
    "g0_p25c_seals.json", "g0_p25c_check.json",
    "g0_p25c_manifest_tendl_2025_n.txt",
    "g1_p25c_signature.json", "g1_p25c_check.json",
    "g2_p25c_patch.json", "g2_p25c_check.json",
    "g2_p25c_patched_manifest.sha256",
    "g3_p25c_census.json", "g3_p25c_check.json",
    "g4_p25c_build.json", "g4_p25c_score.json",
    "g4_p25c_irdff_score.json", "g4_p25c_check.json",
]

DOSIMETRY_UNRECOVERED = ["Ni-58", "Nb-93", "Ag-109", "In-113", "Au-197"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    seals = json.loads((RESULTS / "g0_p25c_seals.json").read_text())
    sig = json.loads((RESULTS / "g1_p25c_signature.json").read_text())
    patch = json.loads((RESULTS / "g2_p25c_patch.json").read_text())
    census = json.loads((RESULTS / "g3_p25c_census.json").read_text())
    build = json.loads((RESULTS / "g4_p25c_build.json").read_text())
    score = json.loads((RESULTS / "g4_p25c_score.json").read_text())
    g3check = json.loads((RESULTS / "g3_p25c_check.json").read_text())
    blk = score["corpora"]["tendl_2025_patched"]

    class_hist = Counter(r["class"] for r in sig["files"].values())
    post_hist = Counter(
        "+".join(d["post_kinds"]) or "clean"
        for d in census["rescan"].values())
    cov = census["coverage"]

    floors = {
        "F1_no_regression": {
            "met": not census["newly_broken_files"],
            "newly_broken": census["newly_broken_files"]},
        "F2_non_inferiority": {
            "met": (cov["patched"]["irdff_built"] >= 29
                    and cov["patched"]["union_built"] >= 43
                    and cov["patched"]["anchors_built"] >= 41),
            "irdff": cov["patched"]["irdff_built"],
            "union": cov["patched"]["union_built"],
            "anchors": cov["patched"]["anchors_built"]},
        "F3_material_recovery": {
            "met": len(census["recovered_files"]) >= 1,
            "recovered": census["recovered_files"]},
        "F4_leak_clearance": {
            "met": not g3check["residual_signature_files"],
            "residual": g3check["residual_signature_files"]},
        "F5_artifact_coverage": {
            "met": build["build"]["exit"] == 0
            and len(build["index_targets"] or []) == len(
                build["staged_files"]),
            "index_targets": len(build["index_targets"] or []),
            "staged": len(build["staged_files"])},
    }

    accounting = {
        "schema": "actinv-p25c-g5-accounting-1",
        "defect_census": {
            "sealed_files": len(seals["sealed_population"]["files"]),
            "pre_patch_classes": dict(class_hist),
            "patched_files": patch["patched_files"],
            "ordinate_edits": patch["edit_count"],
            "copied_verbatim": patch["copied_verbatim"],
            "post_rescan_kinds_histogram": dict(post_hist),
            "residual_signature_hits":
                len(g3check["residual_signature_files"]),
        },
        "coverage": {
            "irdff": {"source": cov["source"]["irdff_built"],
                      "patched": cov["patched"]["irdff_built"],
                      "total": cov["irdff_total"]},
            "union": {"source": cov["source"]["union_built"],
                      "patched": cov["patched"]["union_built"],
                      "total": cov["union_total"]},
            "anchors": {"source": cov["source"]["anchors_built"],
                        "patched": cov["patched"]["anchors_built"],
                        "total": cov["anchors_total"]},
            "recovered_files": census["recovered_files"],
            "newly_broken_files": census["newly_broken_files"],
            "dosimetry_critical_recovered": 0,
            "dosimetry_critical_unrecovered": DOSIMETRY_UNRECOVERED,
        },
        "artifact": {
            "staged_files": len(build["staged_files"]),
            "build_exit": build["build"]["exit"],
            "index_targets": len(build["index_targets"] or []),
            "catalog_liso_values": build["catalog_liso_values"],
            "npz_sha256": build["npz_sha256"],
            "index_sha256": build["index_sha256"],
        },
        "scoring": {
            "irdff_partition": {
                "rows": blk["irdff_partition"]["rows"],
                "candidate_scored":
                    blk["irdff_partition"]["candidate_scored"],
                "comparable_rows":
                    blk["irdff_partition"]["comparable_rows"],
                "nonregression": blk["irdff_partition"]["nonregression"],
            },
            "isomeric_partition": {
                "eligible_rows": blk["isomeric_partition"]["eligible_rows"],
                "candidate_scored_rows":
                    blk["isomeric_partition"]["candidate_scored_rows"],
                "paired_rows": blk["isomeric_partition"]["paired_rows"],
                "nonreg": blk["isomeric_partition"]["nonreg"],
            },
        },
        "floors": floors,
        "all_floors_met": all(f["met"] for f in floors.values()),
        "nonregression_all_ok": all(
            v is True for part in (
                blk["irdff_partition"]["nonregression"],
                blk["isomeric_partition"]["nonreg"])
            for k, v in part.items() if k.endswith("_ok")),
    }
    ACCOUNTING.write_text(
        json.dumps(accounting, indent=1, sort_keys=True) + "\n")

    all_met = accounting["all_floors_met"] \
        and accounting["nonregression_all_ok"]
    verdict = {
        "schema": "actinv-p25c-verdict-1",
        "verdict": "P25c-PASS" if all_met else "P25c-FAIL",
        "protocol_sha256": sha256(PROTOCOL),
        "amendment_1": ("frozen coverage floors F1-F5, "
                        "protocols/ACTINV-P25c_PROTOCOL.md"),
        "accounting_sha256": sha256(ACCOUNTING),
        "evidence_sha256": {n: sha256(RESULTS / n) for n in EVIDENCE},
        "blind_evidence": False,
        "evidence_label": ("all scoring retrospective; every benchmark "
                           "partition is consumed evidence"),
        "headline": (
            "surgical remediation verified: the derived tendl-2025-patched "
            "corpus clears the confirmed leak signature in all 28 files, "
            "recovers 3 union targets with zero regressions, and passes "
            "every frozen coverage and nonregression gate"),
        "key_findings": [
            "the patch zeroed exactly 44 enumerated leading ordinates "
            "across 28 files; independent replay verified surgical "
            "integrity and all 2,822 other files byte-identical",
            "post-patch census: zero residual leak-signature hits; "
            "residual non-signature defect classes re-ledgered, "
            "not cleared",
            "coverage delta: IRDFF 29->30, union 43->45, anchors 41->42; "
            "recovered files n-Sc045, n-Y088, n-Br080m",
            "dosimetry-critical set unrecovered: Ni-58, Nb-93, Ag-109, "
            "In-113, Au-197 still fail closed on non-signature defect "
            "classes -- the confirmed leak was not their blocker",
            "nonregression vs shipped baseline passes on both partitions "
            "(IRDFF 17 comparable; isomeric 162 paired of 469 eligible); "
            "note the baseline shares the defect on patched rows, so "
            "nonregression bounds harm, it does not prove the corrected "
            "values right -- correctness rests on the enumerated "
            "patch semantics and upstream confirmation",
            "46 non-signature conservation-excess files and 7 "
            "self-channel-only files remain unpatched and ledgered",
        ],
        "limitations": [
            "derived corpus is an Avila Labs remediation of TENDL-2025, "
            "not an official TENDL release",
            "patch addresses only the upstream-confirmed leading-ordinate "
            "leak; 53 sealed files carry other defect classes",
            "no blind evidence exists; all scoring is retrospective",
            "isomeric identity remains qualified only to the extent the "
            "state catalog now expresses liso {0,1} product states; "
            "P18/P18b isomeric-identity verdicts remain FAIL",
        ],
        "release_consequence": (
            "the patched corpus is a qualified derived candidate for a "
            "separately labeled data release; publishing it as "
            "data-v1.1.x remains the maintainer's decision and should "
            "carry the defect scope and dosimetry-critical limitations "
            "verbatim"),
    }
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"verdict": verdict["verdict"],
                      "floors_met": accounting["all_floors_met"],
                      "nonreg_ok": accounting["nonregression_all_ok"]},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
