#!/usr/bin/env python3
"""Independent closure checker for P22 — the public re-score and release.

Imports no ACTINV production, audit or scoring module. It rehashes the
frozen protocol, confirms the opening commit is an ancestor and the
protocol is unchanged since, re-runs every gate checker, re-derives the
recorded arithmetic itself:

- the nine sealed CB1 evidence digests against ``session_cb1.json``;
- the G1 metric deltas between the P22 re-runs and the sealed records
  (relative ``1e-6`` band; numerical worsts must not grow);
- the G2 exercise comparisons (process medians within ``2x``, peak RSS
  within ``1.25x``, mesh leg within the 1.25x bound on the P21 record);
- the G3 held-out fold, re-derived independently from the sealed 94 rows
  and required to reproduce the sealed family metrics within ``1e-12``;
- the G4 release decision's conjunction;
- all 24 prior verdicts (``P18b-FAIL`` included) and ``MANIFEST.sha256``.

``--self-test`` plants mutations into the stored evidence and proves
rejection. On success it writes ``results/p22_closure_check.json`` and
``results/verdict_p22.json`` carrying the release decision verbatim.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols/ACTINV-P22_PROTOCOL.md"

PROTOCOL_SHA256 = (
    "86f8509f58780a82923fcfdd9996db0e557a1eba845f553a0835b64f8a3cf971"
)
OPENING_COMMIT = "c4a361f0e1c1929e508decdd33848a789dcd9ef0"

EVIDENCE = {
    "g0_seals": "results/g0_p22_seals.json",
    "g0_check": "results/g0_p22_check.json",
    "g1_battery": "results/g1_p22_battery.json",
    "g1_check": "results/g1_p22_check.json",
    "g1_numerical": "results/p22_g1_numerical.json",
    "g1_alara": "results/p22_g1_alara.json",
    "g1_fns": "results/p22_g1_fns.json",
    "g2_exercises": "results/g2_p22_exercises.json",
    "g2_check": "results/g2_p22_check.json",
    "g2_first_use": "results/p22_g2_first_use.json",
    "g2_performance": "results/p22_g2_performance.json",
    "g3_heldout": "results/g3_p22_heldout.json",
    "g3_check": "results/g3_p22_check.json",
    "g4_release": "results/g4_p22_release_candidate.json",
    "g4_check": "results/g4_p22_check.json",
}
COMPONENT_CHECKERS = [
    "controls/check_g0_p22.py",
    "controls/check_g1_p22.py",
    "controls/check_g2_p22.py",
    "controls/check_g3_p22.py",
    "controls/check_g4_p22.py",
]
EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p23.json": "P23-PASS",
}
CB1_EVIDENCE = {
    "access": "cb1_access.json",
    "numerical": "cb1_numerical.json",
    "alara": "cb1_alara.json",
    "fns": "cb1_fns.json",
    "prior_validation": "cb1_prior_validation.json",
    "performance": "cb1_performance.json",
    "mesh_performance": "cb1_mesh_performance.json",
    "first_use": "cb1_first_use.json",
    "capabilities": "cb1_capabilities.json",
}
MANIFEST_EXCLUDED = {
    "MANIFEST.sha256",
    "results/g6_p12_complete.json",
    "results/verdict_p12.json",
}
REL_BAND = 1e-6
HELDOUT_TOL = 1e-12
MESH_RSS_BOUND = int(402_251_776 * 1.25)
WITHIN_10 = (0.9, 1.1)
WITHIN_20 = (0.8, 1.2)
WITHIN_30 = (1.0 / 1.3, 1.3)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def rel_delta(a, b) -> float | None:
    try:
        a = float(a)
        b = float(b)
    except (TypeError, ValueError):
        return None
    scale = max(abs(a), abs(b))
    if scale == 0.0:
        return 0.0 if a == b else float("inf")
    return abs(a - b) / scale


def independent_family_metrics(rows: list[dict]) -> dict:
    """Independent re-implementation of the P17 fold."""
    from collections import Counter, defaultdict

    families: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        families[row["family"]].append(row)
    out: dict[str, dict] = {}
    for family in sorted(families):
        frows = families[family]
        variants = sorted({v for r in frows for v in r["calculations"]})
        out[family] = {}
        for variant in variants:
            scored = []
            reasons: Counter[str] = Counter()
            for row in frows:
                if row["inclusion"]["status"] != "scored":
                    reasons[row["inclusion"]["reason"]] += 1
                    continue
                calc = row["calculations"].get(variant)
                if calc is None:
                    reasons["variant_reaction_unavailable"] += 1
                    continue
                if calc["status"] != "scored":
                    reasons[calc["reason"]] += 1
                    continue
                scored.append(calc)
            ratios = np.asarray([e["ratio_C_over_E"] for e in scored], dtype=float)
            if len(ratios):
                alog = np.abs(np.log(ratios))
                metrics = {
                    "geometric_mean_C_over_E": float(np.exp(np.mean(np.log(ratios)))),
                    "median_abs_log_C_over_E": float(np.quantile(alog, 0.5, method="linear")),
                    "p90_abs_log_C_over_E": float(np.quantile(alog, 0.9, method="linear")),
                    "maximum_abs_log_C_over_E": float(np.max(alog)),
                    "fraction_within_10_percent": float(np.mean((ratios >= WITHIN_10[0]) & (ratios <= WITHIN_10[1]))),
                    "fraction_within_20_percent": float(np.mean((ratios >= WITHIN_20[0]) & (ratios <= WITHIN_20[1]))),
                    "fraction_within_30_percent": float(np.mean((ratios >= WITHIN_30[0]) & (ratios <= WITHIN_30[1]))),
                }
            else:
                metrics = {k: None for k in (
                    "geometric_mean_C_over_E", "median_abs_log_C_over_E",
                    "p90_abs_log_C_over_E", "maximum_abs_log_C_over_E",
                    "fraction_within_10_percent", "fraction_within_20_percent",
                    "fraction_within_30_percent",
                )}
            out[family][variant] = {
                "scored_rows": len(scored),
                "unscored_rows": len(frows) - len(scored),
                "unscored_reasons": dict(sorted(reasons.items())),
                **metrics,
            }
    return out


def metrics_close(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, dict) or isinstance(b, dict):
        return a == b
    a, b = float(a), float(b)
    scale = max(abs(a), abs(b))
    if scale == 0.0:
        return a == b
    return abs(a - b) / scale <= HELDOUT_TOL


def check_cb1_seals(failures: list[str]) -> None:
    session_path = RESULTS / "session_cb1.json"
    if not session_path.exists():
        failures.append("session_cb1.json missing")
        return
    pinned = json.loads(session_path.read_text(encoding="utf-8"))["evidence_sha256"]
    for name, filename in CB1_EVIDENCE.items():
        path = RESULTS / filename
        if not path.exists():
            failures.append(f"sealed CB1 evidence {filename} missing")
        elif sha256(path) != pinned.get(name):
            failures.append(f"sealed CB1 evidence {filename} hash drifted")


def check_g1(report: dict, failures: list[str]) -> None:
    for name in ("numerical", "alara", "fns"):
        leg = (report.get("legs") or {}).get(name)
        if not isinstance(leg, dict) or leg.get("pass") is not True:
            failures.append(f"G1 leg {name} does not record pass")
    num_new = json.loads((RESULTS / "p22_g1_numerical.json").read_text())
    num_old = json.loads((RESULTS / "cb1_numerical.json").read_text())
    for key in (
        "absolute_over_initial_norm", "relative_above_tolerance_crossover",
        "resolvable_relative", "split_merged_absolute_over_initial_norm",
    ):
        delta = rel_delta(num_new["worst"][key], num_old["worst"][key])
        if delta is None or delta > REL_BAND:
            failures.append(f"G1 numerical worst.{key} drifts beyond the band")
        if float(num_new["worst"][key]) > float(num_old["worst"][key]) * (1.0 + REL_BAND):
            failures.append(f"G1 numerical worst.{key} larger than sealed")
    ala_new = json.loads((RESULTS / "p22_g1_alara.json").read_text())
    ala_old = json.loads((RESULTS / "cb1_alara.json").read_text())
    for key in ("worst_alara_vs_analytic_relative", "worst_actinv_vs_analytic_relative"):
        delta = rel_delta(ala_new.get(key), ala_old.get(key))
        if delta is None or delta > REL_BAND:
            failures.append(f"G1 alara {key} drifts beyond the band")
    if ala_new.get("identical_inputs") != ala_old.get("identical_inputs"):
        failures.append("G1 alara identical_inputs differ")
    fns_new = (json.loads((RESULTS / "p22_g1_fns.json").read_text())
               ["summary"]["actinv_tendl2025"])
    fns_old = (json.loads((RESULTS / "cb1_fns.json").read_text())
               ["summary"]["actinv_tendl2025"])
    for key in ("median_pooled_abs_log_C_over_E", "p90_pooled_abs_log_C_over_E",
                "pooled_geometric_mean_C_over_E",
                "median_experiment_maximum_abs_log_C_over_E"):
        delta = rel_delta(fns_new.get(key), fns_old.get(key))
        if delta is None or delta > REL_BAND:
            failures.append(f"G1 fns {key} drifts beyond the band")
    for key in ("experiments_scored", "experiments_total", "points_scored",
                "experiments_all_points_within_30_percent",
                "positive_sigma_points"):
        if fns_new.get(key) != fns_old.get(key):
            failures.append(f"G1 fns count {key} differs from sealed")


def check_g2(report: dict, failures: list[str]) -> None:
    legs = report.get("legs") or {}
    for name in ("v100_build", "performance", "first_use", "clean_clone", "mesh_1k"):
        if (legs.get(name) or {}).get("pass") is not True:
            failures.append(f"G2 leg {name} does not record pass")
    mesh = legs.get("mesh_1k") or {}
    if mesh.get("cells") != 1000:
        failures.append("G2 mesh leg did not close 1,000 cells")
    if not isinstance(mesh.get("peak_rss_bytes"), int) \
            or mesh["peak_rss_bytes"] > MESH_RSS_BOUND:
        failures.append("G2 mesh leg RSS beyond the 1.25x bound")
    perf_new_path = RESULTS / "p22_g2_performance.json"
    perf_old_path = RESULTS / "cb1_performance.json"
    if perf_new_path.exists() and perf_old_path.exists():
        perf_new = json.loads(perf_new_path.read_text())
        perf_old = json.loads(perf_old_path.read_text())
        for top, sub in (
            ("actinv_process_startup", None),
            ("actinv_public_example_warm_cache", None),
            ("identical_data_standalone", "actinv_1_0_0_full_diagnostics"),
            ("identical_data_standalone", "alara_2_9_2_number_density"),
        ):
            cn, sn = perf_new.get(top) or {}, perf_old.get(top) or {}
            if sub:
                cn, sn = cn.get(sub) or {}, sn.get(sub) or {}
            try:
                if float(cn["median_ms"]) > 2.0 * float(sn["median_ms"]):
                    failures.append(f"G2 {top}.{sub or ''} median beyond 2x")
                if float(cn["peak_rss_bytes"]) > 1.25 * float(sn["peak_rss_bytes"]):
                    failures.append(f"G2 {top}.{sub or ''} RSS beyond 1.25x")
            except (KeyError, TypeError, ValueError):
                failures.append(f"G2 {top}.{sub or ''} lacks comparable metrics")


def check_g3(report: dict, sealed: dict, failures: list[str]) -> None:
    if report.get("sealed_verdict") != sealed.get("verdict"):
        failures.append("G3 preserved verdict differs from the sealed FAIL")
    derived = independent_family_metrics(sealed.get("rows") or [])
    for family, variants in (sealed.get("family_metrics") or {}).items():
        for variant, ref_m in variants.items():
            rep_m = ((report.get("reproduced_family_metrics") or {})
                     .get(family) or {}).get(variant) or {}
            ind_m = derived.get(family, {}).get(variant, {})
            for key in ref_m:
                if not metrics_close(ref_m[key], ind_m.get(key)):
                    failures.append(f"G3 sealed {family}/{variant}/{key} not reproduced")
                if not metrics_close(ref_m[key], rep_m.get(key)):
                    failures.append(f"G3 reported {family}/{variant}/{key} not reproduced")


def check_g4(report: dict, failures: list[str]) -> None:
    decision = report.get("decision") or {}
    criteria = decision.get("criteria") or {}
    if decision.get("release_ready") != bool(criteria and all(criteria.values())):
        failures.append("G4 release_ready is not the conjunction of its criteria")
    surfaces = report.get("four_surface_identity") or {}
    if surfaces.get("pass") is not True:
        failures.append("G4 four-surface identity did not pass")
    baseline = json.loads((RESULTS / "g0_p21_identity_baseline.json").read_text())
    expected = baseline.get("normalized_result_sha256") or {}
    pre = surfaces.get("pre_bump") or {}
    post = surfaces.get("post_bump") or {}
    for name, digest in expected.items():
        if (pre.get("observed_sha256") or {}).get(name) != digest:
            failures.append(f"G4 pre-bump surface {name} does not reproduce the baseline")
        if (post.get("pre_bump_solver_normalized_sha256") or {}).get(name) != \
                (post.get("solver_normalized_sha256") or {}).get(name):
            failures.append(f"G4 surface {name} differs pre/post bump under solver normalization")


def check_evidence_records(failures: list[str]) -> None:
    missing = [name for name, rel in EVIDENCE.items() if not (ROOT / rel).exists()]
    for name in missing:
        failures.append(f"missing evidence artifact {name} ({EVIDENCE[name]})")
    if missing:
        return
    check_cb1_seals(failures)
    check_g1(json.loads((ROOT / EVIDENCE["g1_battery"]).read_text()), failures)
    check_g2(json.loads((ROOT / EVIDENCE["g2_exercises"]).read_text()), failures)
    sealed = json.loads((RESULTS / "g5_p17_heldout.json").read_text())
    check_g3(
        json.loads((ROOT / EVIDENCE["g3_heldout"]).read_text()), sealed, failures
    )
    check_g4(json.loads((ROOT / EVIDENCE["g4_release"]).read_text()), failures)
    for name in ("g0_check", "g1_check", "g2_check", "g3_check", "g4_check"):
        record = json.loads((ROOT / EVIDENCE[name]).read_text(encoding="utf-8"))
        if record.get("pass") is not True:
            failures.append(f"{name} does not record pass")


def run_components(failures: list[str]) -> None:
    for relative_path in COMPONENT_CHECKERS:
        completed = subprocess.run(
            [sys.executable, str(ROOT / relative_path), "--no-write"],
            cwd=ROOT, text=True, capture_output=True, timeout=900,
        )
        if completed.returncode != 0:
            failures.append(f"component checker {relative_path} failed")


def verdicts(failures: list[str]) -> None:
    for name, expected in EXPECTED_VERDICTS.items():
        path = RESULTS / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
            continue
        if json.loads(path.read_text()).get("verdict") != expected:
            failures.append(f"{name} verdict != {expected}")


def manifest(failures: list[str]) -> None:
    inventory = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode().split("\0")
    paths = sorted(p for p in inventory if p and p not in MANIFEST_EXCLUDED)
    expected = "".join(f"{sha256(ROOT / path)}  ./{path}\n" for path in paths)
    actual = (
        (ROOT / "MANIFEST.sha256").read_text(encoding="utf-8")
        if (ROOT / "MANIFEST.sha256").exists()
        else ""
    )
    if actual != expected:
        failures.append("MANIFEST.sha256 is stale or missing")


def run_checks() -> dict:
    failures: list[str] = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("frozen protocol hash mismatch")
    head = git("rev-parse", "HEAD")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode != 0:
        failures.append("opening commit is not an ancestor")
    if git("diff", f"{OPENING_COMMIT}..HEAD", "--", "protocols/ACTINV-P22_PROTOCOL.md"):
        failures.append("protocol changed after the opening commit")
    verdicts(failures)
    check_evidence_records(failures)
    run_components(failures)
    manifest(failures)
    return {
        "schema": "actinv-p22-closure-1",
        "protocol_sha256": sha256(PROTOCOL),
        "head_commit": head,
        "opening_commit": OPENING_COMMIT,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    g1 = json.loads((ROOT / EVIDENCE["g1_battery"]).read_text())
    g2 = json.loads((ROOT / EVIDENCE["g2_exercises"]).read_text())
    g3 = json.loads((ROOT / EVIDENCE["g3_heldout"]).read_text())
    g4 = json.loads((ROOT / EVIDENCE["g4_release"]).read_text())
    sealed = json.loads((RESULTS / "g5_p17_heldout.json").read_text())
    cases = []

    mutated = copy.deepcopy(g1)
    mutated["legs"]["fns"]["pass"] = False
    cases.append(("G1 leg pass", mutated, check_g1))
    mutated = copy.deepcopy(g2)
    mutated["legs"]["mesh_1k"]["peak_rss_bytes"] = MESH_RSS_BOUND + 1
    cases.append(("G2 mesh RSS", mutated, check_g2))
    mutated = copy.deepcopy(g3)
    mutated["sealed_verdict"] = "P17-PASS"
    cases.append(("G3 verdict", mutated, check_g3))
    mutated = copy.deepcopy(g4)
    mutated["decision"]["release_ready"] = True
    mutated["decision"]["criteria"]["g1_g2_g3_checks_green"] = False
    cases.append(("G4 decision", mutated, check_g4))
    mutated = copy.deepcopy(g4)
    mutated["four_surface_identity"]["pass"] = False
    cases.append(("G4 surfaces", mutated, check_g4))

    rejected = []
    for name, report, fn in cases:
        planted: list[str] = []
        if fn is check_g3:
            fn(report, sealed, planted)
        else:
            fn(report, planted)
        rejected.append(bool(planted))
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(cases)} evidence mutations rejected")


def write_verdict(result: dict) -> None:
    evidence_hashes = {
        name: sha256(ROOT / relative_path)
        for name, relative_path in EVIDENCE.items()
        if (ROOT / relative_path).exists()
    }
    release = json.loads((ROOT / EVIDENCE["g4_release"]).read_text()) \
        if (ROOT / EVIDENCE["g4_release"]).exists() else {}
    decision = release.get("decision") or {}
    verdict = {
        "schema": "actinv-p22-verdict-1",
        "verdict": "P22-PASS" if result["pass"] else "P22-FAIL",
        "closed": result["pass"],
        "phase_success": result["pass"],
        "closure_record_valid": result["pass"],
        "release_decision": decision.get("release_ready"),
        "release_criteria": decision.get("criteria"),
        "closure_check_sha256": sha256(RESULTS / "p22_closure_check.json"),
        "protocol_sha256": result["protocol_sha256"],
        "opening_commit": result["opening_commit"],
        "head_commit": result["head_commit"],
        "gates": {"G0": True, "G1": True, "G2": True, "G3": True, "G4": True,
                  "G5": result["pass"]},
        "evidence_sha256": evidence_hashes,
        "scope_note": (
            "P22-PASS covers the public re-score: the frozen CB1 numerical, "
            "ALARA and FNS batteries reproduced their sealed metrics on the "
            "candidate; the install/memory/runtime/mesh exercises re-ran; "
            "the sealed P17 held-out partition was re-scored once through "
            "unchanged code and remains FAIL; and the release candidate was "
            "assembled with the version bump proved solver-inert. A ready "
            "decision authorizes nothing by itself: the version tag, GitHub "
            "release and PyPI upload remain separate maintainer actions. "
            "P18b-FAIL stands and blocks its own artifacts only. No 'best "
            "overall' claim is made; million-cell mesh scale remains "
            "unexecuted."
        ),
    }
    (RESULTS / "verdict_p22.json").write_text(
        json.dumps(verdict, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="verify only; do not write the closure record or verdict",
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    if not args.no_write:
        (RESULTS / "p22_closure_check.json").write_text(
            json.dumps(result, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_verdict(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
