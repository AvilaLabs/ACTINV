#!/usr/bin/env python3
"""Tangent-restriction mechanics gate: the study first_order_comparison
runs the MF33 tangent solve restricted to covariance-covered rows
(internal diagnostic flag). Restricted and dense propagation must emit
identical first_order_std values — uncovered parameters contribute
nothing to the covered contraction — while the restricted build drops
the tangent count to the covered subset.

Runs one small study twice (restricted, then ACTINV_DENSE_COMPARISON_
TANGENTS=1) and compares per-response local-vs-nonlinear stds plus the
restricted run's reported tangent count.

Usage:
  python controls/g1_d2_tangent_restriction.py   # writes
      results/g1_d2_tangent_restriction.json
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = ROOT / "target/debug/actinv"
DATA = Path(os.environ.get("ACTINV_DATA", Path.home() / "nuclear-data"))
OUT = ROOT / "results/g1_d2_tangent_restriction.json"
WORK = ROOT / "target/d2_restriction"

checks: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def cgroup(cmd: list[str]) -> list[str]:
    return [
        "systemd-run", "--user", "--scope", "-q",
        "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
        "-p", "TasksMax=128", "-p", "CPUQuota=200%",
        "--", "env", f"TMPDIR={ROOT / 'target/preflight-tmp'}",
        "RAYON_NUM_THREADS=2", *cmd,
    ]


def lvn_stds(record: dict) -> dict[str, dict[str, float]]:
    out = {}
    for case in record["cases"]:
        lvn = (case.get("robustness") or {}).get("local_vs_nonlinear") or {}
        for response, per_time in (lvn.get("comparisons") or {}).items():
            for tkey, entry in per_time.items():
                out[f"{case['case_id']}::{response}::{tkey}"] = {
                    "first_order_std": entry.get("first_order_std"),
                    "sampled_std": entry.get("sampled_std"),
                }
    return out


def main() -> int:
    campaign = DATA / "p43-work/p43-campaign.json"
    if not campaign.is_file():
        check("p43 campaign spec present", False, str(campaign))
        return finish()
    study = json.loads(campaign.read_text())
    # one case: Fe pulse — partially covered chain, dense path affordable
    study["cases"]["materials"] = [
        m for m in study["cases"]["materials"] if m["name"] == "fe"
    ]
    study["cases"]["schedules"] = [
        s for s in study["cases"]["schedules"] if "pulse" in s["name"]
    ]
    study["robustness"]["samples"] = 4
    study["robustness"]["first_order_comparison"] = True
    # Fe declares no fission-yield files; the channel must be off for a
    # Fe-only study (the u235 campaign case is what carried it).
    study["robustness"]["channels"]["fission_yields"] = False
    study["study_id"] = "d2-tangent-restriction"
    WORK.mkdir(parents=True, exist_ok=True)
    spec_path = WORK / "study.json"
    spec_path.write_text(json.dumps(study, indent=1))

    env_dense = dict(os.environ, ACTINV_DENSE_COMPARISON_TANGENTS="1")
    walls = {}
    records = {}
    for tag, env in (("restricted", None), ("dense", env_dense)):
        out_dir = WORK / tag
        # study run resumes completed legs from a prior out dir — stale
        # records would shadow the binary under test, so start clean.
        shutil.rmtree(out_dir, ignore_errors=True)
        r = subprocess.run(
            cgroup([str(ACTINV), "study", "run", str(spec_path), str(out_dir)]),
            capture_output=True, text=True, env=env, timeout=3600)
        walls[tag] = r.returncode
        if r.returncode != 0:
            check(f"{tag} run exits clean", False, (r.stderr or "")[-300:])
            return finish()
        records[tag] = json.loads((out_dir / "study_record.json").read_text())

    check("both runs complete", walls["restricted"] == walls["dense"] == 0)

    rs, ds = lvn_stds(records["restricted"]), lvn_stds(records["dense"])
    check("local_vs_nonlinear evaluated on both", bool(rs) and bool(ds),
          f"{len(rs)} vs {len(ds)} comparisons")
    keys = set(rs) & set(ds)
    check("comparison keys match", rs.keys() == ds.keys(),
          f"{len(keys)} shared")
    mism = []
    for k in sorted(keys):
        a, b = rs[k]["first_order_std"], ds[k]["first_order_std"]
        if a is None or b is None:
            continue
        if not math.isclose(a, b, rel_tol=0.0, abs_tol=0.0):
            mism.append(f"{k}: {a} vs {b}")
    check("restricted first_order_std == dense first_order_std (bitwise)",
          not mism, "; ".join(mism[:3]))

    # tangent accounting: the restricted comparison must declare its
    # compact working set — fewer tangents than declared parameters.
    for case in records["restricted"]["cases"]:
        lvn = (case.get("robustness") or {}).get("local_vs_nonlinear") or {}
        ti, ap = lvn.get("tangents_integrated"), lvn.get("active_parameters")
        check(f"{case['case_id']}: tangents_integrated emitted", ti is not None,
              f"lvn keys: {sorted(lvn)}")
        if isinstance(ti, int) and isinstance(ap, int):
            check(f"{case['case_id']}: tangents_integrated < active_parameters",
                  0 < ti < ap, f"{ti} vs {ap}")
    # the dense run must NOT carry the restricted counters (byte-identical
    # emission for the default path)
    for case in records["dense"]["cases"]:
        lvn = (case.get("robustness") or {}).get("local_vs_nonlinear") or {}
        check(f"{case['case_id']}: dense emits no tangents_integrated",
              "tangents_integrated" not in lvn)

    return finish()


def finish() -> int:
    failed = [c for c in checks if not c["pass"]]
    OUT.write_text(json.dumps(
        {"pass": not failed, "n": len(checks), "checks": checks}, indent=1))
    print(json.dumps({"pass": not failed, "n": len(checks),
                      "failed": [c["name"] for c in failed]}, indent=1))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
