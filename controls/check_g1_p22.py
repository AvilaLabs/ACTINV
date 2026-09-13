#!/usr/bin/env python3
"""Independent checker for the P22 G1 numerical-battery re-run.

Imports no ACTINV production, audit or scoring module. Independently:

- rehashes the frozen protocol and confirms opening-commit ancestry;
- re-reads both the sealed CB1 records and the P22 re-run records and
  re-derives every comparison: continuous metrics within the frozen
  relative band `1e-6`, integer counts exact, each re-run's internal
  ``pass`` true, and (for ALARA) ``identical_inputs`` equal to the sealed
  record;
- requires each re-run to record the exercised module's SHA-256 matching
  the file on disk, proving the committed measurement code ran unchanged;
- requires the G1 record's ``pass`` flag true.

``--self-test`` plants mutations into copies of the record and proves
each is rejected. Emits ``results/g1_p22_check.json``.
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


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P22_PROTOCOL.md"
REPORT = ROOT / "results/g1_p22_battery.json"
OUTPUT = ROOT / "results/g1_p22_check.json"
RESULTS = ROOT / "results"
CONTROLS = ROOT / "controls"

PROTOCOL_SHA256 = "86f8509f58780a82923fcfdd9996db0e557a1eba845f553a0835b64f8a3cf971"
OPENING_COMMIT = "c4a361f0e1c1929e508decdd33848a789dcd9ef0"
REL_BAND = 1e-6

NUMERICAL_WORST = (
    "absolute_over_initial_norm",
    "relative_above_tolerance_crossover",
    "resolvable_relative",
    "split_merged_absolute_over_initial_norm",
)
ALARA_WORST = (
    "worst_alara_vs_analytic_relative",
    "worst_actinv_vs_analytic_relative",
)
FNS_CONTINUOUS = (
    "median_pooled_abs_log_C_over_E",
    "p90_pooled_abs_log_C_over_E",
    "pooled_geometric_mean_C_over_E",
    "median_experiment_maximum_abs_log_C_over_E",
)
FNS_COUNTS = (
    "experiments_scored",
    "experiments_total",
    "experiments_all_points_within_30_percent",
    "points_scored",
    "positive_sigma_points",
)
SEALED = {
    "numerical": "cb1_numerical.json",
    "alara": "cb1_alara.json",
    "fns": "cb1_fns.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dig(obj: dict, dotted: str):
    for part in dotted.split("."):
        obj = (obj or {}).get(part)
    return obj


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


def check_report(report: dict, failures: list[str]) -> None:
    if report.get("schema") != "actinv-p22-g1-battery-1":
        failures.append("schema is not actinv-p22-g1-battery-1")
    if report.get("relative_band") != REL_BAND:
        failures.append("recorded band differs from the frozen 1e-6")

    runs = report.get("runs") or {}
    for name in SEALED:
        run = runs.get(name) or {}
        module_rel = run.get("module")
        if not module_rel or not (ROOT / module_rel).exists():
            failures.append(f"{name} run lacks a module path")
            continue
        if run.get("module_sha256") != sha256(ROOT / module_rel):
            failures.append(f"{name} module digest differs from the file on disk")
        if run.get("returncode") != 0:
            failures.append(f"{name} re-run did not return 0")

    legs = report.get("legs") or {}
    for name, sealed_file in SEALED.items():
        leg = legs.get(name)
        if not isinstance(leg, dict):
            failures.append(f"leg {name} missing")
            continue
        candidate_path = RESULTS / f"p22_g1_{name}.json"
        sealed_path = RESULTS / sealed_file
        if not candidate_path.exists() or not sealed_path.exists():
            failures.append(f"leg {name} lacks its records")
            continue
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
        if candidate.get("pass") is not True:
            failures.append(f"{name} candidate re-run's internal pass is not true")
        recorded = leg.get("comparisons") or {}
        required = set(NUMERICAL_WORST if name == "numerical"
                       else ALARA_WORST if name == "alara"
                       else set(FNS_CONTINUOUS) | set(FNS_COUNTS))
        if set(recorded) != required:
            failures.append(f"leg {name} does not record every required comparison")
        if name == "numerical":
            cw, sw = candidate.get("worst") or {}, sealed.get("worst") or {}
            for key in NUMERICAL_WORST:
                delta = rel_delta(cw.get(key), sw.get(key))
                if delta is None or delta > REL_BAND:
                    failures.append(f"numerical worst.{key} drifts beyond the band")
                try:
                    if float(cw.get(key)) > float(sw.get(key)) * (1.0 + REL_BAND):
                        failures.append(f"numerical worst.{key} larger than sealed")
                except (TypeError, ValueError):
                    failures.append(f"numerical worst.{key} is not numeric")
                recorded = ((leg.get("comparisons") or {}).get(key) or {})
                if recorded.get("within_band") is not True:
                    failures.append(f"numerical worst.{key} not recorded within band")
            if leg.get("no_larger") is not True:
                failures.append("numerical leg does not record no_larger")
        elif name == "alara":
            for key in ALARA_WORST:
                delta = rel_delta(candidate.get(key), sealed.get(key))
                if delta is None or delta > REL_BAND:
                    failures.append(f"alara {key} drifts beyond the band")
            if candidate.get("identical_inputs") != sealed.get("identical_inputs"):
                failures.append("alara identical_inputs differ from the sealed record")
        else:
            cs = (candidate.get("summary") or {}).get("actinv_tendl2025") or {}
            ss = (sealed.get("summary") or {}).get("actinv_tendl2025") or {}
            for key in FNS_CONTINUOUS:
                delta = rel_delta(cs.get(key), ss.get(key))
                if delta is None or delta > REL_BAND:
                    failures.append(f"fns {key} drifts beyond the band")
            for key in FNS_COUNTS:
                if cs.get(key) != ss.get(key):
                    failures.append(f"fns count {key} differs from the sealed record")
        if leg.get("pass") is not True:
            failures.append(f"leg {name} does not record pass")
    if report.get("pass") is not True:
        failures.append("record does not carry pass")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode == 0
    if not ancestor:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of HEAD")
    if not REPORT.exists():
        failures.append("results/g1_p22_battery.json is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p22-g1-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "leg_missing": lambda r: r["legs"].pop("fns"),
        "band_forged": lambda r: r.__setitem__("relative_band", 0.5),
        "run_module_forged": lambda r: r["runs"]["numerical"].__setitem__(
            "module_sha256", "0" * 64
        ),
        "run_rc_forged": lambda r: r["runs"]["alara"].__setitem__("returncode", 1),
        "leg_pass_forge": lambda r: r["legs"]["numerical"].__setitem__("pass", False),
        "comparison_gap": lambda r: r["legs"]["fns"]["comparisons"].pop(
            "p90_pooled_abs_log_C_over_E", None
        ),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p22-g1-selftest-") as directory:
            planted = Path(directory) / "planted.json"
            planted.write_text(json.dumps(candidate), encoding="utf-8")
            failures: list[str] = []
            check_report(json.loads(planted.read_text(encoding="utf-8")), failures)
            rejected.append(bool(failures))
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(mutations)} report mutations rejected")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    if not args.no_write:
        OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
