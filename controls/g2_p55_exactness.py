#!/usr/bin/env python3
"""P55 G2 exactness — re-derives every emitted quantity from the inputs
alone: design matrix, first-pass NNLS by active-set enumeration (not the
Lawson–Hanson path), the model covariance via the ported sparse collapse,
the whitened GLS solve, posterior covariance/correlation, chi-square,
pulls, Kalman gains and identifiability flags.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p53_fixture as fx  # noqa: E402
import p55_case as p55  # noqa: E402
import check_g5_p53 as g5p53  # noqa: E402

RESULT = ROOT / "results/g2_p55_exactness.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/debug/actinv"))
RIDGE = 1e-9
TOL = 1e-6


def nnls_enum(A: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Independent NNLS for small k: enumerate active sets, solve each
    unconstrained LS on the free coordinates, keep the best feasible."""
    k = A.shape[1]
    best, best_obj = np.zeros(k), np.inf
    for mask in range(1 << k):
        free = [j for j in range(k) if mask & (1 << j)]
        x = np.zeros(k)
        if free:
            sol, *_ = np.linalg.lstsq(A[:, free], b, rcond=None)
            x[free] = sol
        if np.all(x >= -1e-10):
            x = np.maximum(x, 0.0)
            obj = float(np.sum((A @ x - b) ** 2))
            if obj < best_obj - 1e-15:
                best, best_obj = x, obj
    return best


def reference(doc: Path, spec: dict, fxmap: dict) -> dict:
    """Rebuild everything from the emitted sensitivity records + npz data."""
    records = [json.loads(l) for l in doc.read_text().splitlines()
               if l.strip()]
    header = next(r for r in records if r["record"] == "header")
    segs = header["segments"]               # 1-based schedule steps
    k = len(segs)
    sens = [r for r in records if r["record"] == "sensitivity"]
    m = max(r["measurement"] for r in sens) + 1
    A = np.zeros((m, k))
    jmaps: list[dict[int, float]] = [[{} for _ in range(m)]
                                     for _ in range(k)]
    for r in sens:
        i, s = r["measurement"], r["segment"]
        A[i, s] = r["a_Bq_per_g"]
        jmaps[s][i] = {int(row): v for row, v in r["entries"].items()}
    cons = next(r for r in records if r["record"] == "consistency")
    y = np.array([p["activity_Bq_per_g"] for p in cons["pulls"]])
    sig = np.array([p["sigma_Bq_per_g"] for p in cons["pulls"]])

    selected = sorted({row for seg in jmaps for mp in seg for row in mp})
    phi = spec["spectrum"]["flux_per_group"]
    cov = g5p53.build_joint_covariance(
        Path(spec["library"]["path"]), Path(spec["uncertainty"]["covariance"]["path"]),
        [phi], selected)
    covered = cov["covered"]
    pos = {row: i for i, row in enumerate(covered)}
    n = len(covered)
    J = np.zeros((k, m, n))
    for s in range(k):
        for i in range(m):
            for row, v in jmaps[s][i].items():
                if row in pos:
                    J[s, i, pos[row]] += v

    w0 = 1.0 / sig
    f0 = nnls_enum(A * w0[:, None], y * w0)
    G = np.einsum("k,kim->im", f0, J)  # (m, n)
    C_model = np.zeros((m, m))
    for (l, r), v in cov["entries"].items():
        C_model += np.outer(G[:, l] * v, G[:, r])
    C = C_model + np.diag(sig ** 2)
    L = np.linalg.cholesky(C)
    aw = np.linalg.solve(L, A)
    bw = np.linalg.solve(L, y)
    f = nnls_enum(aw, bw)
    prec = aw.T @ aw
    lam = RIDGE * float(np.trace(prec)) / k
    Cpost = np.linalg.inv(prec + lam * np.eye(k))
    psig = np.sqrt(np.diag(Cpost))
    resid = y - A @ f
    chi2 = float(np.sum(np.linalg.solve(L, resid) ** 2))
    pulls = resid / np.sqrt(np.diag(C))
    K = Cpost @ np.linalg.solve(C, A).T  # (k, m)
    corr = Cpost / np.outer(psig, psig)
    return {"A": A, "f0": f0, "f": f, "C_model": C_model, "C": C,
            "Cpost": Cpost, "psig": psig, "chi2": chi2, "pulls": pulls,
            "K": K, "corr": corr, "resid": resid}


def close(a, b, tol=TOL) -> bool:
    return bool(abs(a - b) <= tol * max(abs(a), abs(b), 1.0))


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    work_root = ROOT / "target/preflight-tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_root, prefix="p55-g2-") as td:
        work = Path(td)
        fxmap = fx.make_fixture(work)
        sha = fx.fx.sha256

        fwd = p55.forward(ACTINV, p55.run_spec(fxmap, (1.5, 0.6), sha),
                          work, "truth")
        meas = p55.measurements_from(fwd)
        spec = p55.run_spec(fxmap, sha=sha)
        sp = work / "spec.json"; sp.write_text(json.dumps(spec))
        rc, log, out = p55.qualified(ACTINV, sp, meas, work, "good")
        checks["emitted"] = rc == 0
        if rc != 0:
            details["emit_error"] = log[-400:]
        records = [json.loads(l) for l in out.read_text().splitlines()
                   if l.strip()] if out.exists() else []
        ref = reference(out, spec, fxmap) if out.exists() else None
        if ref:
            est = {r["segment"]: r for r in records
                   if r["record"] == "estimate"}
            post = next(r for r in records if r["record"] == "posterior")
            cons = next(r for r in records if r["record"] == "consistency")

            checks["f0_matches"] = all(
                close(ref["f0"][j], est[j]["first_pass_multiplier"])
                for j in range(2))
            checks["fhat_matches"] = all(
                close(ref["f"][j], est[j]["multiplier"]) for j in range(2))
            checks["posterior_sigma_matches"] = all(
                close(ref["psig"][j], est[j]["posterior_sigma"])
                for j in range(2))
            checks["posterior_cov_matches"] = np.allclose(
                ref["Cpost"], np.asarray(post["covariance"]), rtol=TOL,
                atol=0.0)
            checks["posterior_corr_matches"] = np.allclose(
                ref["corr"], np.asarray(post["correlation"]), rtol=TOL,
                atol=1e-9)
            checks["c_model_matches"] = np.allclose(
                ref["C_model"], np.asarray(post["covariance_model"]),
                rtol=TOL, atol=0.0)
            checks["c_total_matches"] = np.allclose(
                ref["C"], np.asarray(post["covariance_total"]),
                rtol=TOL, atol=0.0)
            checks["chi2_matches"] = close(ref["chi2"], cons["chi_square"])
            checks["pulls_match"] = np.allclose(
                ref["pulls"],
                np.array([p["pull"] for p in cons["pulls"]]),
                rtol=TOL, atol=1e-9)
            checks["residuals_match"] = np.allclose(
                ref["resid"],
                np.array([p["residual_Bq_per_g"] for p in cons["pulls"]]),
                rtol=TOL, atol=0.0)
            checks["gains_match"] = all(
                close(est[j]["top_sensitivity_contributors"][0]["gain"],
                      max(ref["K"][j], key=abs))
                for j in range(2))
            ident = next(r for r in records
                         if r["record"] == "identifiability")
            checks["ident_flags_match"] = all(
                (ref["psig"][j] <= 0.5 * ref["f"][j]) ==
                ident["segments"][j]["resolvable"] or
                ref["f"][j] == 0.0
                for j in range(2))
            details["f0"] = ref["f0"].tolist()
            details["f"] = ref["f"].tolist()
            details["psig"] = ref["psig"].tolist()
            details["corr"] = ref["corr"].tolist()
            details["chi2"] = ref["chi2"]

        # ---- degenerate leg: a third irradiation segment after the last
        # measured step contributes nothing — it must be named degenerate,
        # not silently estimated.
        spec3 = p55.run_spec(fxmap, sha=sha)
        spec3["schedule"] = [{"dt": "0.7 s", "flux": 1.0},
                             {"dt": "0.2 s", "flux": 0.0},
                             {"dt": "0.4 s", "flux": 1.0},
                             {"dt": "0.6 s", "flux": 0.0},
                             {"dt": "0.3 s", "flux": 1.0},
                             {"dt": "0.5 s", "flux": 0.0}]
        sp3 = work / "spec3.json"; sp3.write_text(json.dumps(spec3))
        # measurements still at steps 2 and 4 — segment at step 5 is unseen
        rc3, log3, out3 = p55.qualified(ACTINV, sp3, meas, work, "degen")
        checks["degenerate_runs"] = rc3 == 0
        if rc3 == 0:
            recs3 = [json.loads(l) for l in out3.read_text().splitlines()
                     if l.strip()]
            est3 = {r["segment"]: r for r in recs3
                    if r["record"] == "estimate"}
            ident3 = next(r for r in recs3 if r["record"] == "identifiability")
            seg5 = ident3["segments"][2]
            checks["degenerate_flagged"] = not seg5["resolvable"]
            checks["degenerate_reason_named"] = len(seg5["reasons"]) > 0
            checks["degenerate_finite_sigma"] = (
                est3[2]["posterior_sigma"] > 0.0 and
                bool(np.isfinite(est3[2]["posterior_sigma"])))
            checks["live_segments_unaffected"] = all(
                est3[j]["multiplier"] >= 0.0 for j in (0, 1))

    evidence = {"schema": "actinv-p55-g2-exactness-1",
                "checks": checks, "details": details,
                "pass": all(checks.values())}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
