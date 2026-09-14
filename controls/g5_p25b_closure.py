#!/usr/bin/env python3
"""P25b G5 — closure: complete outcome accounting, gate evaluation,
verdict.

Population (a): the 47 isotopic IRDFF-II targets — outcomes from both
the v1.0.1 G1 census (+ Amendment-1 rebind) and the 1.1.0 union
census, published side by side because the coverage floors were
frozen against v1.0.1 measurements before the builder-version
confound was discovered.

Population (b): the P25 neutron isomeric rows — per the frozen scope
the denominator is per-evaluation: rows whose *target* nuclide has a
file in that corpus.  Outcomes re-derived live through the unchanged
``g5_p18b_heldout.score_heldout`` against the union+anchor artifacts.

Gates (frozen): complete outcome accounting; coverage floor +
dosimetry-critical sub-census; comparable-case nonregression on both
partitions; honest qualification label (no blind evidence exists —
all scoring is retrospective).

Writes ``results/g5_p25b_accounting.json`` and
``results/verdict_p25b.json``; regenerates the results manifest.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25b-g4-union"

CORPORA = ["tendl_2023", "fendl_32c", "eaf_2010"]
DOSIMETRY = {"Ni-58", "Au-197", "Ag-109", "Nb-93", "In-113", "In-115"}
FLOORS = {"eaf_2010": 47, "tendl_2023": 41, "fendl_32c": 34}

PRIOR_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL", "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS", "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS", "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL", "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL", "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL", "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL", "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS", "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL", "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL", "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS", "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS", "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS", "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL", "verdict_p26.json": "P26-FAIL",
}

EVIDENCE = [
    "g0_p25b_seals.json", "g0_p25b_check.json", "g1_p25b_census.json",
    "g1_p25b_check.json", "g2_p25b_traces.json", "g2_p25b_check.json",
    "g3_p25b_proposal.json", "g3_p25b_check.json",
    "g4_p25b_rebind.json", "g4_p25b_build.json", "g4_p25b_score.json",
    "g4_p25b_isomer.json", "g4_p25b_union.json",
    "g4_p25b_union_irdff_score.json", "g4_p25b_union_check.json",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def heldout_row_outcomes(artifact: Path, index_path: Path) -> dict:
    """Per-row candidate outcome over the frozen neutron ledger."""
    import g5_p18b_heldout as g5
    work = WORK / "heldout-g5"
    work.mkdir(parents=True, exist_ok=True)
    for f in work.iterdir():
        f.unlink()
    shutil.copyfile(artifact, work / "candidate-neutron.npz")
    if index_path.is_file():
        shutil.copyfile(index_path, work / "candidate-neutron_index.json")
    out = g5.score_heldout(work=work, report=None)
    rows = {}
    for r in out["ledger"]:
        if r["projectile"] != "neutron" or not r.get("isomer_liso"):
            continue
        seg = r["family_id"].split("|")
        tz, ta = seg[1].split("-")
        target_za = int(tz) * 1000 + int(ta)
        if r["status"] != "eligible":
            outcome = f"eligibility:{r.get('predicate', 'unknown')}"
        else:
            c = r.get("candidate") or {}
            st = c.get("status", "unevaluated")
            if st == "scored" and math.isfinite(c.get("ln_cm") or float("nan")):
                outcome = "scored"
            elif st == "scored":
                outcome = "zero_prediction" if c.get("calculated") in (0, None) \
                    else "undefined_ratio:nonfinite_ln"
            elif st == "zero_denominator":
                outcome = "zero_prediction"
            elif st == "target_absent":
                outcome = "construction_failed:target_absent"
            elif st == "build_failed_g3":
                outcome = "construction_failed:state_identity"
            elif st == "energy_outside_groups":
                outcome = "undefined_ratio:energy_outside_groups"
            elif st == "library_absent":
                outcome = "construction_failed:library_absent"
            else:
                outcome = f"undefined_ratio:{st}"
        rows[r["family_id"] + "|" + str(r.get("row", len(rows)))] = {
            "family_id": r["family_id"], "target_za": target_za,
            "row_status": r["status"], "candidate_status":
            (r.get("candidate") or {}).get("status"), "outcome": outcome}
    return rows


def main() -> int:
    union = json.loads((RESULTS / "g4_p25b_union.json").read_text())
    g1 = json.loads((RESULTS / "g1_p25b_census.json").read_text())
    rebind = json.loads((RESULTS / "g4_p25b_rebind.json").read_text())
    g1_ok = {c: (g1["histograms"].get(c) or {}).get("ok", 0)
             for c in CORPORA}
    recovered = Counter(r["corpus"] for r in rebind["rows"]
                        if r.get("class") == "ok")

    # ---- population (a): construction accounting, both builders ----
    accounting = {"schema": "actinv-p25b-g5-accounting-1",
                  "populations": {}, "corpora": {}}
    for corpus in CORPORA:
        rows = union["corpora"][corpus]["construction"]
        pop_a = {}
        for e in rows:
            if not e["in_irdff"]:
                continue
            if e["class"] == "ok":
                oc = "constructed"
            elif e["class"] == "corpus_incomplete":
                oc = "corpus_incomplete"
            else:
                msg = e.get("message", "")
                if "LAW=-5" in msg:
                    oc = "construction_failed:capability"
                elif "timeout" in msg:
                    oc = "construction_failed:bound_limited"
                elif "MF=8/LMF=9" in msg or "conflict" in msg:
                    oc = "construction_failed:product_state_conflict"
                elif "emitted state sum" in msg:
                    oc = "construction_failed:emitted_sum_exceeds_total"
                elif "ELFS" in msg:
                    oc = "construction_failed:invalid_state_energy"
                elif "log-log interpolation" in msg or "interp" in msg.split("MT")[0]:
                    oc = "construction_failed:interp"
                elif "Breit-Wigner" in msg:
                    oc = "construction_failed:resonance_inconsistency"
                elif "no matching MF=3 total" in msg:
                    oc = "construction_failed:missing_total"
                else:
                    oc = "construction_failed:other"
            pop_a[e["target"]] = oc

        # ---- population (b): per-row outcomes, per-evaluation denom ----
        aug = (union.get("anchor_augmentation") or {}) \
            .get("corpora", {}).get(corpus, {})
        art = aug.get("artifact") or union["corpora"][corpus]["artifact"]
        npz = Path(art["npz"])
        idx = npz.with_name(npz.stem + "_index.json")
        row_out = heldout_row_outcomes(npz, idx)
        files_by_target = {}
        for e in rows:
            if e["in_isomeric"]:
                files_by_target[e["za"]] = bool(e.get("candidate_files"))
        pop_b, excluded = {}, 0
        for key, r in row_out.items():
            if files_by_target.get(r["target_za"]):
                pop_b[key] = r["outcome"]
            else:
                excluded += 1
        hist = Counter(pop_b.values())

        # ---- gates ----
        v101 = g1_ok[corpus] + recovered.get(corpus, 0)
        v110 = sum(1 for v in pop_a.values() if v == "constructed")
        dos_ok_v110 = sum(1 for t, v in pop_a.items()
                        if t in DOSIMETRY and v == "constructed")
        iso = union["corpora"][corpus]["scoring"]["isomeric_partition"]
        ir = union["corpora"][corpus]["scoring"]["irdff_partition"]
        accounting["corpora"][corpus] = {
            "population_a": pop_a,
            "population_a_histogram": dict(Counter(pop_a.values())),
            "population_b_rows": pop_b,
            "population_b_histogram": dict(hist),
            "population_b_excluded_unshipped": excluded,
            "coverage": {
                "floor": FLOORS[corpus],
                "v101_post_repair_ok": v101,
                "v110_union_ok": v110,
                "floor_met_v101": v101 >= FLOORS[corpus],
                "floor_met_v110": v110 >= FLOORS[corpus],
                "dosimetry_critical_ok_v110": f"{dos_ok_v110}/6",
            },
            "nonregression": {
                "irdff": ir.get("nonregression"),
                "isomeric": iso.get("nonreg"),
                "comparable_irdff": ir.get("comparable_rows"),
                "paired_isomeric": iso.get("paired_rows"),
            },
        }
        print(corpus, "A:", dict(Counter(pop_a.values())),
              "B:", dict(hist), flush=True)

    accounting["populations"] = {
        "a_irdff_isotopic_targets":
            len(union["population"]["irdff_targets"]),
        "b_isomeric_targets": len(union["population"]["isomeric_targets"]),
        "b_isomeric_rows_total": union["population"]["isomeric_rows"],
    }
    acc_path = RESULTS / "g5_p25b_accounting.json"
    acc_path.write_text(json.dumps(accounting, indent=1, sort_keys=True)
                        + "\n")

    # ---- verdict ----
    failures = []
    for name, want in PRIOR_VERDICTS.items():
        got = json.loads((RESULTS / name).read_text()).get("verdict")
        if got != want:
            failures.append(f"{name}: {got!r} != {want!r}")
    proc = subprocess.run(["git", "tag", "-l", "v1.1*"],
                          capture_output=True, text=True, cwd=ROOT)
    tags = proc.stdout.split()
    if tags:
        failures.append(f"release tag exists: {tags}")
    changelog = (ROOT / "CHANGELOG.md").read_text()
    if "## Unreleased" not in changelog:
        failures.append("release hold marker (## Unreleased) missing")

    per_corpus = {}
    for corpus in CORPORA:
        blk = accounting["corpora"][corpus]
        cov = blk["coverage"]; nr = blk["nonregression"]
        nr_irdff = nr["irdff"] or {}
        nr_iso = nr["isomeric"] or {}
        reasons = []
        if not cov["floor_met_v101"]:
            reasons.append("coverage floor unmet (v1.0.1 census)")
        if not cov["floor_met_v110"]:
            reasons.append("coverage floor unmet (1.1.0 builder)")
        if nr_irdff.get("median_ok") is False \
                or nr_irdff.get("p90_ok") is False \
                or nr_irdff.get("coverage_ok") is False:
            reasons.append("IRDFF comparable-case nonregression failed")
        if nr_iso.get("median_ok") is False \
                or nr_iso.get("p90_ok") is False \
                or nr_iso.get("coverage_ok") is False:
            reasons.append("isomeric comparable-case nonregression failed")
        if nr_iso.get("unscored_candidate"):
            reasons.append("isomeric partition unscored "
                           "(builder/corpus capability)")
        per_corpus[corpus] = {
            "qualified": not reasons, "reasons": reasons,
            "coverage": cov, "nonregression": nr}

    verdict = {
        "schema": "actinv-p25b-verdict-1",
        "verdict": "P25b-FAIL",
        "headline": ("no alternate evaluation survives construction "
                     "qualification with nonregressing comparable-case "
                     "accuracy under the release-candidate builder"),
        "blind_evidence": False,
        "evidence_label": ("all scoring retrospective; every benchmark "
                           "partition is consumed evidence"),
        "builder_version_confound": (
            "G1 census and Amendment-1 floors were measured with the "
            "v1.0.1 builder; the release-relevant union census used the "
            "1.1.0 release candidate, whose added state validation "
            "(state_catalog, ELIS/ELFS checks, emitted-state sums) is "
            "stricter. Floors met under v1.0.1, unmet under 1.1.0 for "
            "all three candidates; the verdict fails under either "
            "reading because no candidate passes nonregression."),
        "per_corpus": per_corpus,
        "key_findings": [
            "TENDL-2023 carries the same TALYS emitted-sum>total defect "
            "class as TENDL-2025 (19 union files) plus unsupported "
            "MF=6 LAW=-5 fission files; it passes IRDFF nonregression "
            "on 21 comparable rows but fails the isomeric partition "
            "(median |ln C/E| 0.250 vs baseline 0.162, 253 paired rows).",
            "EAF-2010's format path emits no state_catalog under the "
            "1.1.0 builder and 12 files carry negative MF=8 ELFS "
            "excitation energies: the isomeric partition is unscored "
            "by builder capability and IRDFF nonregression fails "
            "(0.156 vs 0.055 on 24 comparable rows).",
            "FENDL-3.2c lacks isomer-target files for all 32 needed "
            "product states and 23 union targets are corpus_incomplete; "
            "IRDFF nonregression fails (0.096 vs 0.040 on 16 rows).",
            "Upstream confirmed the TENDL-2025 defect root cause "
            "(unflushed TALYS channelsout.f90 array); the fix lands in "
            "the next TENDL release, which post-dates every candidate "
            "evaluated here.",
        ],
        "release_consequence": (
            "ACTINV 1.1.0 remains blocked on neutron data: no qualified "
            "alternate candidate exists. Options recorded: wait for the "
            "corrected TENDL release; or a new phase adding state-catalog "
            "emission for the EAF format plus bounded ELFS adjudication, "
            "which could revisit EAF-2010's construction coverage under "
            "explicit isomeric-identity limitations."),
        "prior_verdict_failures": failures,
        "evidence_sha256": {n: sha256(RESULTS / n) for n in EVIDENCE
                            if (RESULTS / n).is_file()},
        "accounting_sha256": sha256(acc_path),
        "protocol_sha256": sha256(
            ROOT / "protocols" / "ACTINV-P25b_PROTOCOL.md"),
        "amendment_1_sha256": sha256(
            ROOT / "protocols" / "ACTINV-P25b_AMENDMENT_1.md"),
    }
    (RESULTS / "verdict_p25b.json").write_text(
        json.dumps(verdict, indent=1, sort_keys=True) + "\n")

    # ---- manifest regeneration (once, at closure) ----
    manifest = RESULTS / "manifest.sha256"
    lines = []
    for p in sorted(RESULTS.glob("*.json")):
        lines.append(f"{sha256(p)}  {p.name}")
    manifest.write_text("\n".join(lines) + "\n")
    print(json.dumps({"verdict": verdict["verdict"],
                      "failures": failures,
                      "manifest_entries": len(lines)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
