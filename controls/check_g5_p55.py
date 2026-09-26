#!/usr/bin/env python3
"""P55 G5 independent checker — re-derives the qualified inverse from the
raw inputs on the corpus case: the design matrix columns, the model
covariance via the ported sparse collapse, the two-pass NNLS→GLS solve,
the posterior, and the consistency ledger. Every emitted value must match;
planted mutations in any field class must be caught.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_g5_p53 as g5p53  # noqa: E402

RESULT = ROOT / "results/check_g5_p55.json"
RIDGE = 1e-9
TOL = 1e-6

DOC = ROOT / "results/p55_corpus.ndjson"
SPEC = ROOT / "results/p55_corpus_spec.json"


def nnls_enum(A: np.ndarray, b: np.ndarray) -> np.ndarray:
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


def load_doc(path: Path):
    records = [json.loads(l) for l in path.read_text().splitlines()
               if l.strip()]
    header = next(r for r in records if r["record"] == "header")
    segs = header["segments"]
    k = len(segs)
    sens = [r for r in records if r["record"] == "sensitivity"]
    m = max(r["measurement"] for r in sens) + 1
    A = np.zeros((m, k))
    jmaps = [[{} for _ in range(m)] for _ in range(k)]
    for r in sens:
        A[r["measurement"], r["segment"]] = r["a_Bq_per_g"]
        jmaps[r["segment"]][r["measurement"]] = {
            int(row): v for row, v in r["entries"].items()}
    cons = next(r for r in records if r["record"] == "consistency")
    y = np.array([p["activity_Bq_per_g"] for p in cons["pulls"]])
    sig = np.array([p["sigma_Bq_per_g"] for p in cons["pulls"]])
    return records, header, segs, A, jmaps, y, sig, cons


def evaluate(doc_path: Path, spec: dict, cov_state=None) -> dict:
    records, header, segs, A, jmaps, y, sig, cons = load_doc(doc_path)
    k, m = A.shape[1], A.shape[0]
    selected = sorted({row for seg in jmaps for mp in seg for row in mp})
    phi = spec["spectrum"]["flux_per_group"]
    if cov_state is None or set(cov_state.get("selected", [])) != set(selected):
        cov_state = g5p53.build_joint_covariance(
            Path(spec["library"]["path"]),
            Path(spec["uncertainty"]["covariance"]["path"]),
            [phi], selected)
        cov_state["selected"] = selected
    pos = {row: i for i, row in enumerate(cov_state["covered"])}
    n = len(cov_state["covered"])
    J = np.zeros((k, m, n))
    for s in range(k):
        for i in range(m):
            for row, v in jmaps[s][i].items():
                if row in pos:
                    J[s, i, pos[row]] += v

    w0 = 1.0 / sig
    f0 = nnls_enum(A * w0[:, None], y * w0)
    G = np.einsum("k,kim->im", f0, J)
    C_model = np.zeros((m, m))
    for (l, r), v in cov_state["entries"].items():
        C_model += np.outer(G[:, l] * v, G[:, r])
    C = C_model + np.diag(sig ** 2)
    L = np.linalg.cholesky(C)
    aw = np.linalg.solve(L, A)
    f = nnls_enum(aw, np.linalg.solve(L, y))
    prec = aw.T @ aw
    lam = RIDGE * float(np.trace(prec)) / k
    Cpost = np.linalg.inv(prec + lam * np.eye(k))
    psig = np.sqrt(np.diag(Cpost))
    resid = y - A @ f
    chi2 = float(np.sum(np.linalg.solve(L, resid) ** 2))
    corr = Cpost / np.outer(psig, psig)
    K = Cpost @ np.linalg.solve(C, A).T
    return {"A": A, "f0": f0, "f": f, "C_model": C_model, "C": C,
            "Cpost": Cpost, "psig": psig, "chi2": chi2,
            "corr": corr, "K": K, "resid": resid,
            "cov_state": cov_state}


def compare(doc: Path, spec: dict, ref=None) -> list[str]:
    records = [json.loads(l) for l in doc.read_text().splitlines()
               if l.strip()]
    header = next(r for r in records if r["record"] == "header")
    k = len(header["segments"])
    if ref is None:
        ref = evaluate(doc, spec)
    est = {r["segment"]: r for r in records if r["record"] == "estimate"}
    post = next(r for r in records if r["record"] == "posterior")
    cons = next(r for r in records if r["record"] == "consistency")
    problems = []
    for j in range(k):
        if abs(ref["f"][j] - est[j]["multiplier"]) > \
                TOL * max(abs(ref["f"][j]), abs(est[j]["multiplier"]), 1.0):
            problems.append(f"segment {j} multiplier mismatch "
                            f"{est[j]['multiplier']} vs {ref['f'][j]}")
        if abs(ref["f0"][j] - est[j]["first_pass_multiplier"]) > \
                TOL * max(abs(ref["f0"][j]), 1.0):
            problems.append(f"segment {j} first-pass mismatch")
        if abs(ref["psig"][j] - est[j]["posterior_sigma"]) > \
                TOL * max(ref["psig"][j], 1e-300):
            problems.append(f"segment {j} posterior_sigma mismatch")
    if not np.allclose(np.asarray(post["covariance"]), ref["Cpost"],
                       rtol=TOL, atol=0.0):
        problems.append("posterior covariance mismatch")
    if not np.allclose(np.asarray(post["correlation"]), ref["corr"],
                       rtol=TOL, atol=1e-9):
        problems.append("posterior correlation mismatch")
    if not np.allclose(np.asarray(post["covariance_model"]),
                       ref["C_model"], rtol=TOL, atol=0.0):
        problems.append("model covariance mismatch")
    if not np.allclose(np.asarray(post["covariance_total"]), ref["C"],
                       rtol=TOL, atol=0.0):
        problems.append("total covariance mismatch")
    if abs(cons["chi_square"] - ref["chi2"]) > \
            TOL * max(abs(cons["chi_square"]), abs(ref["chi2"]), 1.0):
        problems.append("chi_square mismatch")
    epulls = np.array([p["pull"] for p in cons["pulls"]])
    resid = ref["resid"] / np.sqrt(np.diag(ref["C"]))
    if not np.allclose(epulls, resid, rtol=TOL, atol=1e-9):
        problems.append("pulls mismatch")
    return problems


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    spec = json.loads(SPEC.read_text())
    ref = evaluate(DOC, spec)
    problems = compare(DOC, spec, ref)
    checks["all_emitted_values_reproduce"] = not problems
    details["problems"] = problems
    details["ref_f"] = ref["f"].tolist()
    details["ref_psig"] = ref["psig"].tolist()
    details["ref_corr"] = ref["corr"].tolist()

    # mutation legs — every field class must be caught
    import copy
    import tempfile
    doc_text = DOC.read_text()
    with tempfile.TemporaryDirectory(dir=ROOT / "target") as td:
        tdir = Path(td)
        # mutation 1: tamper one (measurement, segment) sensitivity map —
        # scale every entry so C_model must shift
        mutated = []
        for line in doc_text.splitlines():
            r = json.loads(line)
            if r.get("record") == "sensitivity" and \
                    r["measurement"] == 0 and r["segment"] == 0:
                r["entries"] = {k: v * 1.5 for k, v in r["entries"].items()}
            mutated.append(json.dumps(r))
        mp = tdir / "mut1.ndjson"; mp.write_text("\n".join(mutated))
        checks["mutation_sensitivity_caught"] = bool(compare(mp, spec))
        # mutation 2: tamper an estimate
        mutated = []
        for line in doc_text.splitlines():
            r = json.loads(line)
            if r.get("record") == "estimate" and r["segment"] == 0:
                r["multiplier"] *= 1.1
            mutated.append(json.dumps(r))
        mp = tdir / "mut2.ndjson"; mp.write_text("\n".join(mutated))
        checks["mutation_estimate_caught"] = bool(compare(mp, spec))
        # mutation 3: tamper a posterior off-diagonal
        mutated = []
        for line in doc_text.splitlines():
            r = json.loads(line)
            if r.get("record") == "posterior":
                r["covariance"][0][1] *= 1.5
                r["correlation"][0][1] *= 1.5
            mutated.append(json.dumps(r))
        mp = tdir / "mut3.ndjson"; mp.write_text("\n".join(mutated))
        checks["mutation_posterior_caught"] = bool(compare(mp, spec))

    evidence = {"schema": "actinv-p55-g5-check-1",
                "checks": checks, "details": details,
                "pass": all(checks.values())}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"checks": checks, "problems": problems},
                     indent=2, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
