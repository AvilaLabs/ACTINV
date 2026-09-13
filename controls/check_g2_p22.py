#!/usr/bin/env python3
"""Independent checker for the P22 G2 exercises record.

Imports no ACTINV production, audit or scoring module. Independently:

- rehashes the frozen protocol and confirms opening-commit ancestry;
- re-reads the P22 re-run records (``p22_g2_first_use.json``,
  ``p22_g2_performance.json``) and the sealed CB1 records, re-deriving
  every comparison: internal checks true, process-level ``median_ms``
  within ``2x`` sealed, ``peak_rss_bytes`` within ``1.25x`` sealed;
- verifies the v1.0.0 tag build reported ``actinv 1.0.0`` and the
  clean-clone head build reported a working ``--version``;
- verifies the mesh leg closed its 1,000-cell footer and stayed within
  ``1.25x`` the P21 1,000-cell RSS record (502,814,720 bytes);
- requires the exercised modules' SHA-256s to match the files on disk.

``--self-test`` plants mutations into copies of the record and proves
each is rejected. Emits ``results/g2_p22_check.json``.
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
REPORT = ROOT / "results/g2_p22_exercises.json"
OUTPUT = ROOT / "results/g2_p22_check.json"
RESULTS = ROOT / "results"
CONTROLS = ROOT / "controls"

PROTOCOL_SHA256 = "86f8509f58780a82923fcfdd9996db0e557a1eba845f553a0835b64f8a3cf971"
OPENING_COMMIT = "c4a361f0e1c1929e508decdd33848a789dcd9ef0"
MESH_RSS_BOUND = int(402_251_776 * 1.25)
PERF_MEDIAN_BAND = 2.0
PERF_RSS_BAND = 1.25

PROCESS_MEDIAN_PATHS = (
    ("actinv_process_startup", None),
    ("actinv_public_example_warm_cache", None),
    ("identical_data_standalone", "actinv_1_0_0_full_diagnostics"),
    ("identical_data_standalone", "alara_2_9_2_number_density"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_report(report: dict, failures: list[str]) -> None:
    if report.get("schema") != "actinv-p22-g2-exercises-1":
        failures.append("schema is not actinv-p22-g2-exercises-1")
    if report.get("mesh_rss_bound_bytes") != MESH_RSS_BOUND:
        failures.append("mesh RSS bound is not 1.25x the P21 record")
    if report.get("perf_median_band") != PERF_MEDIAN_BAND:
        failures.append("performance median band is not 2x")
    if report.get("perf_rss_band") != PERF_RSS_BAND:
        failures.append("performance RSS band is not 1.25x")

    legs = report.get("legs") or {}
    for name in ("v100_build", "performance", "first_use", "clean_clone", "mesh_1k"):
        leg = legs.get(name)
        if not isinstance(leg, dict):
            failures.append(f"leg {name} missing")
        elif leg.get("pass") is not True:
            failures.append(f"leg {name} does not record pass")

    build = legs.get("v100_build") or {}
    if build.get("version_stdout") != "actinv 1.0.0":
        failures.append("v1.0.0 build did not report actinv 1.0.0")
    clone = legs.get("clean_clone") or {}
    version = clone.get("version_stdout") or ""
    if not version.startswith("actinv ") or clone.get("version_returncode") != 0:
        failures.append("clean-clone build did not produce a working --version")
    for name in ("v100_build", "clean_clone"):
        leg = legs.get(name) or {}
        digest = leg.get("binary_sha256") or ""
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            failures.append(f"{name} lacks a 64-hex binary sha256")
        binary = ROOT / (leg.get("target_dir") or "") / "release" / "actinv"
        if digest and binary.exists() and sha256(binary) != digest:
            failures.append(f"{name} binary digest does not match the file on disk")

    for name, out_name, sealed_name in (
        ("first_use", "p22_g2_first_use.json", "cb1_first_use.json"),
        ("performance", "p22_g2_performance.json", "cb1_performance.json"),
    ):
        leg = legs.get(name) or {}
        run = leg.get("run") or {}
        module_rel = run.get("module")
        if module_rel and (ROOT / module_rel).exists():
            if run.get("module_sha256") != sha256(ROOT / module_rel):
                failures.append(f"{name} module digest differs from the file on disk")
        else:
            failures.append(f"{name} lacks an exercised-module record")
        if run.get("returncode") != 0:
            failures.append(f"{name} re-run did not return 0")
        out_path = RESULTS / out_name
        sealed_path = RESULTS / sealed_name
        if not out_path.exists() or not sealed_path.exists():
            failures.append(f"{name} lacks its records")
            continue
        candidate = json.loads(out_path.read_text(encoding="utf-8"))
        sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
        if candidate.get("pass") is not True:
            failures.append(f"{name} re-run's internal pass is not true")
        if name == "performance":
            recorded = leg.get("comparisons") or {}
            for top, sub in PROCESS_MEDIAN_PATHS:
                label = f"{top}.{sub}" if sub else top
                cnode = candidate.get(top) or {}
                snode = sealed.get(top) or {}
                if sub:
                    cnode, snode = cnode.get(sub) or {}, snode.get(sub) or {}
                for metric, band in (
                    ("median_ms", PERF_MEDIAN_BAND),
                    ("peak_rss_bytes", PERF_RSS_BAND),
                ):
                    try:
                        ratio = float(cnode.get(metric)) / float(snode.get(metric))
                    except (TypeError, ValueError, ZeroDivisionError):
                        failures.append(f"{label}.{metric} is not measurable")
                        continue
                    if ratio > band:
                        failures.append(f"{label}.{metric} exceeds the {band}x band")
                if label not in recorded:
                    failures.append(f"{label} comparison not recorded")

    mesh = legs.get("mesh_1k") or {}
    if mesh.get("cells") != 1000 or mesh.get("closed_footer") is not True:
        failures.append("mesh leg did not close a 1,000-cell footer")
    rss = mesh.get("peak_rss_bytes")
    if not isinstance(rss, int) or rss <= 0 or rss > MESH_RSS_BOUND:
        failures.append("mesh leg peak RSS missing or beyond the 1.25x bound")
    if mesh.get("output_sha256") and len(mesh["output_sha256"]) != 64:
        failures.append("mesh output digest malformed")

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
        failures.append("results/g2_p22_exercises.json is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p22-g2-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "leg_missing": lambda r: r["legs"].pop("mesh_1k"),
        "version_forged": lambda r: r["legs"]["v100_build"].__setitem__(
            "version_stdout", "actinv 9.9.9"
        ),
        "mesh_rss_forged": lambda r: r["legs"]["mesh_1k"].__setitem__(
            "peak_rss_bytes", MESH_RSS_BOUND + 1
        ),
        "mesh_cells_forged": lambda r: r["legs"]["mesh_1k"].__setitem__("cells", 999),
        "bound_weakened": lambda r: r.__setitem__("perf_median_band", 10.0),
        "clone_broken": lambda r: r["legs"]["clean_clone"].__setitem__(
            "version_returncode", 1
        ),
        "comparison_gap": lambda r: r["legs"]["performance"]["comparisons"].pop(
            "actinv_process_startup", None
        ),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p22-g2-selftest-") as directory:
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
