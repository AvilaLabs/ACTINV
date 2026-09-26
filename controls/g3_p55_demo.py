#!/usr/bin/env python3
"""P55 G3 demonstration — the qualified inverse on the real corpus:
FNS Fe activation spectrum, TENDL-2025 banded uncertainties, two
irradiation segments. Measured activities are synthesized from a
banded forward run at known multipliers; the inverse must recover the
history with its honest correlated band.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p55_case as p55  # noqa: E402

RESULT = ROOT / "results/g3_p55_demo.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
BASE = ROOT / "examples/fns_fe_5min.json"
LIB = ROOT / "actinv-data/v1.1.0/activation/tendl-2025-neutron-709g.npz"
COV = ROOT / "actinv-data/v1.1.0/uncertainty/tendl-2025-neutron-709g.cov.npz"

TRUTHS = [1.4, 0.8]
NUCLIDES = ["Mn56", "Mn54", "Fe59"]


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def corpus_spec() -> dict:
    base = json.loads(BASE.read_text())
    base["library"]["path"] = str(LIB)
    base["library"]["sha256"] = sha256_file(LIB)
    base["decay"]["primary"] = str(ROOT / base["decay"]["primary"])
    base["decay"]["fallback"] = str(ROOT / base["decay"]["fallback"])
    # two irradiation segments: 300 s on, 24 h cool, 300 s on, 24 h cool —
    # Mn56 (t1/2 ≈ 2.58 h) segregates the segments; Mn54/Fe59 see both.
    base["schedule"] = [{"dt": "300 s", "flux": 1.0},
                        {"dt": "86400 s", "flux": 0.0},
                        {"dt": "300 s", "flux": 1.0},
                        {"dt": "86400 s", "flux": 0.0}]
    base["uncertainty"] = {
        "covariance": {"path": str(COV), "sha256": sha256_file(COV)},
        "responses": [f"activity:{n}" for n in NUCLIDES],
        "confidence_level": 0.95,
        "require_complete": False}
    return base


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    t0 = time.monotonic()
    work_root = ROOT / "target/preflight-tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_root, prefix="p55-g3-") as td:
        work = Path(td)
        spec = corpus_spec()

        truth = json.loads(json.dumps(spec))
        truth["schedule"][0]["flux"] = TRUTHS[0]
        truth["schedule"][2]["flux"] = TRUTHS[1]
        fwd = p55.forward(ACTINV, truth, work, "truth")
        checks["truth_is_trace"] = fwd["mode"] == "trace"
        by_step = {s["step"]: s for s in fwd["steps"]}

        meas = {"format": "actinv-reverse-input-1", "measurements": [
            {"step": 4, "nuclide": n,
             "activity_Bq_per_g": by_step[4]["activity_Bq_per_g"][n] * w,
             "sigma_Bq_per_g": by_step[4]["activity_Bq_per_g"][n] * 0.01}
            for n, w in zip(NUCLIDES, (1.02, 0.985, 1.01))] + [
            {"step": 2, "nuclide": "Mn54",
             "activity_Bq_per_g": by_step[2]["activity_Bq_per_g"]["Mn54"],
             "sigma_Bq_per_g":
                 by_step[2]["activity_Bq_per_g"]["Mn54"] * 0.01}]}
        sp = work / "spec.json"; sp.write_text(json.dumps(spec))
        rc, log, out = p55.qualified(ACTINV, sp, meas, work, "corpus")
        checks["emitted"] = rc == 0
        if rc == 0:
            # persist for the independent checker (G5)
            (ROOT / "results/p55_corpus.ndjson").write_text(out.read_text())
            (ROOT / "results/p55_corpus_spec.json").write_text(
                json.dumps(spec, indent=1) + "\n")
        if rc != 0:
            details["emit_error"] = log[-400:]
            raise RuntimeError(log[-2000:])
        records = [json.loads(l) for l in out.read_text().splitlines()
                   if l.strip()]
        est = {r["segment"]: r for r in records
               if r["record"] == "estimate"}
        post = next(r for r in records if r["record"] == "posterior")
        ident = next(r for r in records if r["record"] == "identifiability")
        cons = next(r for r in records if r["record"] == "consistency")

        # The moat claim: honest correlated posterior on recovered history.
        checks["both_segments_resolved"] = (
            ident["segments"][0]["resolvable"] and
            ident["segments"][1]["resolvable"])
        checks["posterior_nonzero"] = all(
            est[j]["posterior_sigma"] > 0.0 for j in (0, 1))
        checks["correlation_emitted"] = len(post["correlation"]) == 2
        checks["intervals_contain_truth"] = all(
            est[j]["confidence_interval"][0] <= TRUTHS[j]
            <= est[j]["confidence_interval"][1] for j in (0, 1))
        checks["sensitivity_records_present"] = sum(
            r["record"] == "sensitivity" for r in records) == 8  # 4 meas × 2 seg
        checks["forward_runs_two"] = records[0]["forward_runs"] == 2
        details["estimates"] = {
            s: {"multiplier": est[j]["multiplier"],
                "posterior_sigma": est[j]["posterior_sigma"],
                "relative_sigma": est[j]["relative_posterior_sigma"],
                "truth": TRUTHS[j]} for j, s in ((0, 1), (1, 3))}
        details["posterior_correlation"] = post["correlation"]
        details["chi_square"] = cons["chi_square"]
        details["resolvable_count"] = ident["resolvable_count"]
        details["wall_time_s"] = time.monotonic() - t0
        details["n_measurement"] = len(meas["measurements"])

    evidence = {"schema": "actinv-p55-g3-demo-1",
                "checks": checks, "details": details,
                "pass": all(checks.values())}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"checks": checks, "details": details},
                     indent=2, sort_keys=True, default=str))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
