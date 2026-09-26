#!/usr/bin/env python3
"""P53 G2 — exact-value validation of the correlated spatial band.

Two-cell fixture on the P53 2-group library: cell spectra differ in
*shape* (not merely scale), so the cross-cell correlation is a genuine
partial value (rho < 1) and the closed-form joint variance is computed
here directly from the raw covariance sidecar — an independent
implementation of J^T Sigma_joint J.

Leg B plants a non-positive-semidefinite self block; the joint export must
apply the P20 exclusion identically (self block excluded, cascade into the
cross block) and the Python reference reproduces the resulting variance
with the defective terms removed.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p53_fixture as fx  # noqa: E402
import p53_artifacts as p53a  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/debug/actinv"))
OUT = ROOT / "results/g2_p53_exactness.json"
EMIT_STEP = 2

BOUNDS = fx.BOUNDS
ROWS = fx.ROWS
SIGMA2 = fx.SIGMA
COV_MATS = fx.COV_MATS
DEFECT_COV = fx.DEFECT_COV


# ---------------------------------------------------------------------------
# Python reference: joint collapse over spectra — same semantics as the Rust
# sparse path (vector_for_grid weighting, P20-style block exclusions on the
# assembled joint matrix).
# ---------------------------------------------------------------------------

def vector_for_grid(row: int, base: int, grid, relative: bool,
                    flux, total: float) -> np.ndarray:
    out = np.zeros(len(grid) - 1)
    if total == 0.0:
        return out
    for g, fg in enumerate(flux):
        if fg == 0.0:
            continue
        low, high = BOUNDS[g], BOUNDS[g + 1]
        if relative:
            mult = SIGMA2[row, g]
        else:
            mult = 1.0 if row == base else SIGMA2[row, g] / SIGMA2[base, g]
        for k in range(len(grid) - 1):
            width = max(0.0, min(high, grid[k + 1]) - max(low, grid[k]))
            out[k] += fg / (high - low) / total * width * mult
    return out


def joint_matrix(mats, phis: list[list[float]], covered: list[int],
                 exclude: bool):
    """Assemble the (S·n_covered)^2 joint covariance; optionally apply the
    exclusion rules to each (mt, mt1) block."""
    s_count = len(phis)
    n = len(covered)
    totals = [float(np.sum(p)) for p in phis]
    base = {(int(r[0]), int(r[1])): i for i, r in enumerate(ROWS)
            if int(r[2]) == -1}
    sigma = np.zeros((s_count * n, s_count * n))
    for (mt, mt1), m in mats.items():
        left_rows = [i for i, r in enumerate(covered)
                     if int(ROWS[r, 1]) == mt]
        right_rows = [i for i, r in enumerate(covered)
                      if int(ROWS[r, 1]) == mt1]
        for li in left_rows:
            lr = covered[li]
            lb = base[(0, mt)]
            for ri in right_rows:
                rr = covered[ri]
                rb = base[(0, mt1)]
                for sl in range(s_count):
                    lv = vector_for_grid(lr, lb, BOUNDS, False,
                                         phis[sl], totals[sl])
                    for sr in range(s_count):
                        rv = vector_for_grid(rr, rb, BOUNDS, False,
                                             phis[sr], totals[sr])
                        v = float(lv @ m @ rv)
                        sigma[sl * n + li, sr * n + ri] += v
                        if mt != mt1:
                            sigma[sr * n + ri, sl * n + li] += v
    if not exclude:
        return sigma, []
    excluded = []
    by_key = {}
    for p, row in enumerate(covered):
        by_key.setdefault(int(ROWS[row, 1]), []).append(p)
    mts = sorted(by_key)
    for a_i, mt_a in enumerate(mts):
        for mt_b in mts[a_i:]:
            self_block = mt_a == mt_b
            if not self_block and \
                    (min(mt_a, mt_b), max(mt_a, mt_b)) not in mats:
                continue
            params_a = [s * n + p for s in range(s_count)
                        for p in by_key[mt_a]]
            params_b = [s * n + p for s in range(s_count)
                        for p in by_key[mt_b]]
            joint = sorted(set(params_a + params_b))
            sub = sigma[np.ix_(joint, joint)]
            sym = 0.5 * (sub + sub.T)
            max_asym = float(np.abs(sub - sub.T).max())
            max_entry = float(np.abs(sub).max()) if sub.size else 0.0

            def is_block_term(pi, pj):
                if self_block:
                    return True
                return (pi in params_a and pj in params_b) or \
                    (pi in params_b and pj in params_a)

            if max_asym > 1e-9 * max_entry and max_asym > 0.0:
                excluded.append((mt_a, mt_b, "asymmetric_block"))
                for pi in joint:
                    for pj in joint:
                        if is_block_term(pi, pj):
                            sigma[pi, pj] = 0.0
                continue
            w = np.linalg.eigvalsh(sym)
            lmin, lmax = float(w[0]), float(w[-1])
            if (lmax > 0.0 and lmin < -1e-10 * lmax) or \
                    (lmax <= 0.0 and lmin < -1e-30):
                excluded.append((mt_a, mt_b, "non_positive_semidefinite"))
                for pi in joint:
                    for pj in joint:
                        if is_block_term(pi, pj):
                            sigma[pi, pj] = 0.0
    return sigma, excluded


def run_pair(fixture_dir: Path, mats, fluxes: list[list[float]],
             require_complete: bool = True):
    """Build fixture + flux + mesh spec, run mesh and joint export."""
    work = fixture_dir
    work.mkdir(parents=True, exist_ok=True)
    fxmap = fx.make_fixture(work, mats)
    flux_path = work / "flux.ndjson"
    fx.write_flux(flux_path, fluxes)
    spec = fx.mesh_spec(fxmap, flux_path)
    spec["uncertainty"]["require_complete"] = require_complete
    spec_path = work / "mesh_spec.json"
    spec_path.write_text(json.dumps(spec, indent=1))
    mesh_out = work / "mesh.ndjson"
    joint_out = work / "joint.ndjson"
    r = subprocess.run([str(BIN), "mesh", str(spec_path), str(mesh_out)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"mesh failed: {r.stderr[-800:]}"
    r = subprocess.run([str(BIN), "export-r2s-joint", str(mesh_out),
                        str(spec_path), str(EMIT_STEP), str(joint_out)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, \
        f"export-r2s-joint failed: {r.stderr[-800:]}"
    recs = [json.loads(l) for l in joint_out.read_text().splitlines()
            if l.strip()]
    return recs, mesh_out


def python_joint(mesh_path: Path, fluxes, mats, exclude=True):
    """Reference J^T Sigma J from mesh records + raw covariance values."""
    covered = [1, 3]  # MT102/MT103 product rows on the fixture library
    s_count = len(fluxes)
    n = len(covered)
    j = np.zeros(s_count * n)
    cells = [json.loads(l) for l in mesh_path.read_text().splitlines()
             if l.strip() and json.loads(l)["record"] == "cell"]
    for c, rec in enumerate(cells):
        step = [s for s in rec["result"]["steps"]
                if s["step"] == EMIT_STEP][0]
        responses = step["uncertainty"]["responses"]
        for entry in step["photon_source"]["by_nuclide"]:
            strength = sum(g["photons_s"] for g in entry["groups"])
            resp = responses.get(f"activity:{entry['nuclide']}")
            if resp is None or resp["nominal"] <= 0:
                continue
            w = strength / resp["nominal"]
            for s in resp.get("sensitivities", []):
                p = s["parameter"]
                if p["library_row"] in covered:
                    j[c * n + covered.index(p["library_row"])] += \
                        w * s["value"]
    sigma, excluded = joint_matrix(mats, fluxes, covered, exclude)
    var = float(j @ sigma @ j)
    cell_var = [float(j[c * n:(c + 1) * n]
                      @ sigma[c * n:(c + 1) * n, c * n:(c + 1) * n]
                      @ j[c * n:(c + 1) * n]) for c in range(s_count)]
    cov01 = float(j[0:n] @ sigma[0:n, n:2 * n] @ j[n:2 * n])
    return var, cell_var, cov01, excluded


def main() -> int:
    checks: dict[str, bool] = {}
    problems: list[str] = []
    detail = {}
    with tempfile.TemporaryDirectory(prefix="p53-g2-", dir="target") as d:
        work = Path(d)
        # ---- leg A: well-formed covariance, partial correlation ----------
        recs, mesh_path = run_pair(work / "a", COV_MATS,
                                   [[1.0, 1.0], [3.0, 0.5]])
        footer = recs[-1]
        corr = [r for r in recs if r["record"] == "correlation"][0]
        var_ref, cell_var_ref, cov01_ref, _ = python_joint(
            mesh_path, [[1.0, 1.0], [3.0, 0.5]], COV_MATS)
        sig_corr = footer["sigma_total_correlated"]
        detail["a"] = {
            "emitted_sigma_corr": sig_corr,
            "python_sigma_corr": math.sqrt(max(var_ref, 0.0)),
            "rho01_emitted": corr["rho"][0][1]}
        checks["sigma_corr_exact"] = abs(
            sig_corr - math.sqrt(max(var_ref, 0.0))) <= \
            1e-9 * max(math.sqrt(abs(var_ref)), 1e-30)
        checks["cell_sigma_exact"] = all(
            abs(c["sigma_photons_s_correlated"]
                - math.sqrt(max(v, 0.0)))
            <= 1e-9 * max(math.sqrt(abs(v)), 1e-30)
            for c, v in zip(
                [r for r in recs if r["record"] == "cell"], cell_var_ref))
        rho01_ref = cov01_ref / math.sqrt(
            max(cell_var_ref[0], 1e-300) * max(cell_var_ref[1], 1e-300))
        checks["rho01_exact"] = abs(
            (corr["rho"][0][1] or 0.0) - rho01_ref) <= 1e-9
        checks["rho_partial"] = corr["rho"][0][1] is not None \
            and corr["rho"][0][1] < 0.9999
        checks["no_exclusions"] = footer["excluded_blocks"] == 0

        # ---- leg B: planted non-PSD self block → exclusion cascade -------
        work_b = work / "b"
        recs_b, mesh_b = run_pair(work_b, DEFECT_COV,
                                  [[1.0, 1.0], [3.0, 0.5]],
                                  require_complete=False)
        footer_b = recs_b[-1]
        var_b, _, _, excluded_b = python_joint(
            mesh_b, [[1.0, 1.0], [3.0, 0.5]], DEFECT_COV, exclude=True)
        detail["b"] = {
            "emitted_sigma_corr": footer_b["sigma_total_correlated"],
            "python_sigma_corr": math.sqrt(max(var_b, 0.0)),
            "excluded": excluded_b}
        checks["b_exclusions"] = footer_b["excluded_blocks"] >= 1
        checks["b_sigma_exact"] = abs(
            footer_b["sigma_total_correlated"]
            - math.sqrt(max(var_b, 0.0))) <= \
            1e-9 * max(math.sqrt(abs(var_b)), 1e-30)

    problems.extend(k for k, v in checks.items() if not v)
    result = {"pass": not problems, "checks": checks,
              "detail": detail, "problems": problems}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
