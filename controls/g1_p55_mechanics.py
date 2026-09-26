#!/usr/bin/env python3
"""P55 G1 mechanics — the qualified inverse emits on a synthetic banded
problem and rejects malformed inputs. G1 never asserts physics; it asserts
the contract.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p53_fixture as fx  # noqa: E402
import p55_case as p55  # noqa: E402

RESULT = ROOT / "results/g1_p55_mechanics.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/debug/actinv"))
SCHEMA = "actinv-reverse-qualified-1"


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    work_root = ROOT / "target/preflight-tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_root, prefix="p55-g1-") as td:
        work = Path(td)
        fxmap = fx.make_fixture(work)
        sha = fx.fx.sha256

        # truth run at multipliers [1.5, 0.6] synthesizes measurements
        fwd = p55.forward(ACTINV, p55.run_spec(fxmap, (1.5, 0.6), sha),
                          work, "truth")
        checks["truth_run_ok"] = fwd["mode"] == "trace"

        meas = p55.measurements_from(fwd)
        spec = p55.run_spec(fxmap, sha=sha)  # unit multipliers: steps 1 and 3
        sp = work / "spec.json"; sp.write_text(json.dumps(spec))

        rc, log, out = p55.qualified(ACTINV, sp, meas, work, "good")
        checks["emits"] = rc == 0 and out.exists()
        if rc != 0:
            details["emit_error"] = log[-400:]
        records = [json.loads(l) for l in out.read_text().splitlines()
                   if l.strip()] if out.exists() else []
        kinds = [r["record"] for r in records]
        checks["header_first"] = bool(kinds) and kinds[0] == "header"
        checks["header_schema"] = bool(records) and \
            records[0]["schema"] == SCHEMA
        checks["estimate_per_segment"] = kinds.count("estimate") == 2
        checks["sensitivity_records"] = kinds.count("sensitivity") == 6
        checks["posterior_record"] = "posterior" in kinds
        checks["identifiability_record"] = "identifiability" in kinds
        checks["consistency_record"] = "consistency" in kinds
        est = {r["step"]: r for r in records if r["record"] == "estimate"}
        checks["estimates_finite"] = all(
            r["multiplier"] >= 0.0 and r["posterior_sigma"] > 0.0
            for r in est.values())
        checks["steps_are_1_3"] = sorted(est) == [1, 3]
        checks["gains_emitted"] = all(
            len(r.get("top_sensitivity_contributors", [])) > 0
            for r in est.values())
        post = next(r for r in records if r["record"] == "posterior")
        checks["posterior_shapes"] = (
            len(post["covariance"]) == 2 and len(post["correlation"][0]) == 2)
        checks["covariance_psd_diag"] = all(
            post["covariance"][j][j] > 0.0 for j in range(2))
        checks["corr_diag_one"] = all(
            abs(post["correlation"][j][j] - 1.0) < 1e-12 for j in range(2))
        checks["c_model_emitted"] = "covariance_model" in post
        ident = next(r for r in records if r["record"] == "identifiability")
        checks["ident_structure"] = all(
            "resolvable" in s and "reasons" in s for s in ident["segments"])
        cons = next(r for r in records if r["record"] == "consistency")
        checks["pulls_emitted"] = len(cons["pulls"]) == 3
        checks["pulls_carry_measurements"] = all(
            "sigma_Bq_per_g" in p and "activity_Bq_per_g" in p
            for p in cons["pulls"])

        # ---- rejections ---------------------------------------------------
        bad = json.loads(json.dumps(meas))
        del bad["measurements"][0]["sigma_Bq_per_g"]
        rc, log, _ = p55.qualified(ACTINV, sp, bad, work, "nosigma")
        checks["reject_sigma_less"] = rc != 0 and "sigma" in log

        bad = json.loads(json.dumps(meas))
        bad["measurements"][0]["nuclide"] = "Co60"
        rc, log, _ = p55.qualified(ACTINV, sp, bad, work, "unbanded")
        checks["reject_unbanded_nuclide"] = rc != 0 and "activity:Co60" in log

        nospec = p55.run_spec(fxmap, sha=sha); del nospec["uncertainty"]
        np_ = work / "nounc.json"; np_.write_text(json.dumps(nospec))
        rc, log, _ = p55.qualified(ACTINV, np_, meas, work, "nounc")
        checks["reject_unbanded_spec"] = rc != 0 and "uncertainty block" in log

        # Fe56 is in the decay library (the bulk parent) but never produced
        # as an activity — every segment leaves it at causal zero
        dead = p55.run_spec(fxmap, sha=sha)
        dead["uncertainty"]["responses"].append("activity:Fe56")
        dp = work / "dead.json"; dp.write_text(json.dumps(dead))
        bad = json.loads(json.dumps(meas))
        bad["measurements"][0]["nuclide"] = "Fe56"
        rc, log, _ = p55.qualified(ACTINV, dp, bad, work, "dead")
        checks["reject_never_produced"] = rc != 0 and "never produced" in log

        rc, log, _ = p55.qualified(
            ACTINV, sp, {"measurements": [meas["measurements"][0]]},
            work, "underdet")
        checks["reject_underdetermined"] = rc != 0 and \
            "cannot determine" in log

        fed = p55.run_spec(fxmap, sha=sha)
        fed["schedule"][0]["feed"] = {"Fe56": 1.0}
        fp = work / "fed.json"; fp.write_text(json.dumps(fed))
        rc, log, _ = p55.qualified(ACTINV, fp, meas, work, "fed")
        checks["reject_feed_schedule"] = rc != 0 and "outside the linear" in log

        details["kinds"] = kinds
        details["estimates"] = est

    evidence = {"schema": "actinv-p55-g1-mechanics-1",
                "checks": checks, "details": details,
                "pass": all(checks.values())}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
