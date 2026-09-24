#!/usr/bin/env python3
"""P49 G2 controls — optimizer semantics enforced on synthetic and
end-to-end paths before the real campaign.

(a) unit controls: planted-optimum recovery, infeasible-landscape honesty,
    feasibility-first ranking, seeded determinism, joint renormalization;
(b) end-to-end: an unsatisfiable nominal constraint must yield
    infeasible:true and no best_feasible;
(c) a band-edge constraint on a spec lacking `uncertainty` must ledger
    constraint_not_computable (never silently skipped);
(d) artifact freeze re-verified.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p49_artifacts as p49a  # noqa: E402

ACTINV = ROOT / "target/release/actinv"
WORK = ROOT / "results/p49_g2_work"


def fail(msg):
    print(json.dumps({"gate": "G2", "pass": False, "reason": msg}))
    return 1


def run_opt(optspec, outdir, timeout=1800):
    return subprocess.run(
        [str(ACTINV), "optimize", str(optspec), str(outdir)],
        capture_output=True, text=True, cwd=ROOT, timeout=timeout)


def main() -> int:
    missing = {n: r["path"] for n, r in p49a.verify().items()
               if not r["present"]}
    if missing:
        return fail(f"sealed artifacts missing: {missing}")

    checks = {}

    # (a) unit controls on the synthetic engine
    t = subprocess.run(
        ["cargo", "test", "-p", "actinv-cli", "optimize::tests"],
        capture_output=True, text=True, cwd=ROOT)
    checks["unit_tests"] = t.returncode == 0 and "5 passed" in t.stdout
    if not checks["unit_tests"]:
        return fail(f"unit controls failed: {t.stdout[-400:]}{t.stderr[-400:]}")

    # fixtures
    WORK.mkdir(parents=True, exist_ok=True)
    link = WORK / "actinv-data"
    if not link.exists():
        link.symlink_to(ROOT / "actinv-data")
    base = json.loads((ROOT / "examples/fns_fe_5min.json").read_text())
    base["schedule"] = base["schedule"][:6]
    (WORK / "mini_base.json").write_text(json.dumps(base))

    def optspec_with(constraints, name):
        p = WORK / name
        p.write_text(json.dumps({
            "schema": "actinv-optimize-1",
            "base_spec": "mini_base.json",
            "design_axes": [
                {"kind": "composition_fraction", "element": "NI",
                 "bounds": [0.0, 1.0]}],
            "objective": {"response": "heat.total", "time_s": 461.0,
                          "edge": "nominal", "direction": "min"},
            "constraints": constraints,
            "optimizer": {"algorithm": "lhs_coordinate", "seed": 7,
                          "init_points": 4, "refine_points": 2},
        }))
        return p

    # (b) unsatisfiable constraint -> infeasible, no best_feasible
    p = optspec_with([{"name": "impossible", "response": "activity.total",
                       "time_s": 461.0, "edge": "nominal", "sense": "le",
                       "limit": 1e-30}], "infeasible_opt.json")
    r = run_opt(p, WORK / "infeasible_out")
    if r.returncode != 0:
        return fail(f"infeasible run crashed: {r.stderr[-300:]}")
    res = json.loads((WORK / "infeasible_out/optimize_result.json").read_text())
    checks["infeasible_flag"] = res["infeasible"] is True
    checks["no_best"] = res["best_feasible"] is None
    if not (checks["infeasible_flag"] and checks["no_best"]):
        return fail("infeasible box returned a best candidate")

    # (c) band edge without uncertainty -> constraint_not_computable
    p = optspec_with([{"name": "band", "response": "heat.total",
                       "time_s": 461.0, "edge": "normal_upper",
                       "sense": "le", "limit": 1e-3}], "band_opt.json")
    r = run_opt(p, WORK / "band_out")
    if r.returncode != 0:
        return fail(f"band-edge run crashed: {r.stderr[-300:]}")
    ledger = [json.loads(l) for l in
              (WORK / "band_out/optimize_ledger.jsonl").read_text().splitlines()
              if l.strip()]
    checks["not_computable"] = all(
        "not_computable" in json.dumps(row.get("constraints", {}))
        for row in ledger)
    res = json.loads((WORK / "band_out/optimize_result.json").read_text())
    checks["band_infeasible"] = res["infeasible"] is True
    if not (checks["not_computable"] and checks["band_infeasible"]):
        return fail("band-edge constraint on bandless spec not ledgered "
                    "constraint_not_computable")

    # (d) artifact freeze — compare against the G0 seal; the only lawful
    # drift is the Amendment-1 optimize.rs (mechanical memory repair)
    seal = json.loads((ROOT / "results/g0_p49_seals.json").read_text())
    sealed = {n: r["sha256"] for n, r in seal["artifacts"].items()}
    current = p49a.verify()
    # Amendment 1 (committed 5b3cb46): optimize.rs memory repair + the
    # protocol's own append-only amendment section
    AMENDED = {"optimize_module", "protocol"}
    unexpected = {}
    amended = {}
    for n, sha in sealed.items():
        cur = current.get(n, {}).get("sha256")
        if cur is None:
            unexpected[n] = "missing"
        elif cur != sha:
            (amended if n in AMENDED else unexpected)[n] = cur
    checks["artifact_freeze"] = not unexpected
    checks["declared_amendment_only"] = (
        set(amended) <= AMENDED and not unexpected)
    amended_report = {n: {"sealed": sealed[n][:16],
                          "current": amended[n][:16]}
                      for n in amended}

    record = {"gate": "G2", "pass": all(checks.values()), "checks": checks,
              "amended_artifacts": amended_report}
    (ROOT / "results/p49_g2.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
