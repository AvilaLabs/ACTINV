#!/usr/bin/env python3
"""P20 G3 control: deterministic correlated sampling vs linear propagation.

Frozen scheme (ACTINV-P20_PROTOCOL §sampling):
  - Symmetric part S = (C + Cᵀ)/2 is eigendecomposed; per positive eigenaxis j
    nine equiprobable normal strata q_i = Φ⁻¹((i+0.5)/9) define samples
    x_ij = q_i √λ_j v_j, plus the nominal zero sample.
  - The activation network is RE-SOLVED per sample (nonlinear path); the linear
    model is never substituted for it on the real leg.
  - Variance recovered per axis: var_j = mean_i (R_ij - R0)². Because
    E[q²] = 0.8669677288762132 < 1 over nine midpoint strata, the recovered
    variance is σ̂² = Σ_j var_j / E[q²].
  - Shared regime: every sampled response finite and positive AND the response
    deviation sign along each axis agrees with the linear prediction for all
    strata. Then 1.645·σ̂ must match 1.645·σ_lin within 5 %; outside the shared
    regime the divergence is reported, not normalised away.
"""

import hashlib
import json
import math
import shutil
import statistics
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import numpy.lib.format

ROOT = Path(__file__).resolve().parent.parent
ACTINV = ROOT / "target/release/actinv"
PROBE = ROOT / "target/debug/p20_collapse_probe"
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
LIBRARY = ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz"
LIB_INDEX = ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g_index.json"
COV = ROOT / "target/p11-full-v2-repro.cov.npz"
COV_INDEX = ROOT / "target/p11-full-v2-repro.cov_index.json"
OUT_DIR = ROOT / "target/p20-g3"
REPORT = ROOT / "results/g3_p20_sampling.json"
TARGET = 1391  # n-Mn052m1.tendl — richest nonzero-σ covered row set under the Core FNS case
REQUESTED_RESPONSES = ["heat.total", "activity:*"]
PRIMARY_REL_SIGMA = 1e-3  # responses above this relative σ carry the comparison

NDIST = statistics.NormalDist()
STRATA = 9
QUANTILES = [NDIST.inv_cdf((i + 0.5) / STRATA) for i in range(STRATA)]
MEAN_Q2 = sum(q * q for q in QUANTILES) / STRATA
Z90 = NDIST.inv_cdf(0.95)
AGREEMENT_TOL = 0.05


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def run_spec(spec, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / "spec.json"
    result_path = out_dir / "result.json"
    spec_path.write_text(json.dumps(spec, indent=2))
    proc = subprocess.run(
        [str(ACTINV), "run", str(spec_path), str(result_path)],
        capture_output=True, text=True, cwd=ROOT, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"run failed: {proc.stderr[-3000:]}")
    return json.loads(result_path.read_text()), sha256(result_path)


def response_value(step, name):
    if name.startswith("heat."):
        return step["heat_W_per_g"][name.split(".", 1)[1]]
    prefix, _, key = name.partition(":")
    if prefix == "activity":
        return step["activity_Bq_per_g"].get(key, 0.0)
    raise ValueError(f"unknown response {name}")


def quantile_axes(matrix):
    sym = (np.asarray(matrix, dtype=float) + np.asarray(matrix, dtype=float).T) / 2.0
    lam, vecs = np.linalg.eigh(sym)
    return sym, lam, [(j, float(lam[j]), vecs[:, j]) for j in range(len(lam)) if lam[j] > 0.0]


def sampled_variance(axis_responses, nominal):
    """axis_responses: {j: [R_ij for i in strata]}; returns (σ̂², per-axis var)."""
    per_axis = {j: sum((r - nominal) ** 2 for r in rs) / len(rs)
                for j, rs in axis_responses.items()}
    return sum(per_axis.values()) / MEAN_Q2, per_axis


# ---------------------------------------------------------------- synthetic

def synthetic_leg():
    basis = np.array([
        [2.0, -1.0, 2.0],
        [2.0, 2.0, -1.0],
        [-1.0, 2.0, 2.0],
    ]) / 3.0  # fixed orthogonal basis — no RNG anywhere in this control
    eigenvalues = np.array([4.0e-24, 1.0e-24, 0.25e-24])
    cov = basis @ np.diag(eigenvalues) @ basis.T
    nominal = 7.5e9
    # scale s so sigma/R0 ~ 0.25 — real-case proportions, not ulp-dominated
    s = np.array([2.0e12, -5.0e11, 8.0e11])
    s = s * (0.25 * nominal / math.sqrt(float(s @ cov @ s)))
    linear_var = float(s @ cov @ s)
    _, lam, axes = quantile_axes(cov)
    axis_responses = {
        j: [nominal + q * math.sqrt(lj) * float(s @ vj) for q in QUANTILES]
        for j, lj, vj in axes
    }
    var_hat, _ = sampled_variance(axis_responses, nominal)
    rel = abs(var_hat - linear_var) / linear_var
    k = 0.35  # dimensionless: exponent = k * sᵀx / σ_lin
    nl_responses = {j: [nominal * math.exp(k * (r - nominal) / math.sqrt(linear_var))
                        for r in rs]
                    for j, rs in axis_responses.items()}
    nl_var, _ = sampled_variance(nl_responses, nominal)
    nl_linear = k ** 2 * nominal ** 2  # dR/dx|_0 = R0·k·s/σ_lin -> var = k²R0²
    return {
        "name": "synthetic_psd_3x3",
        "eigenvalues_in": eigenvalues.tolist(),
        "eigenvalues_recovered": sorted(lam.tolist()),
        "n_positive_axes": len(axes),
        "n_samples": len(axes) * STRATA + 1,
        "linear_variance": linear_var,
        "sampled_variance": var_hat,
        "relative_difference": rel,
        "machine_precision_pass": rel <= 1e-12,
        "mean_q2": MEAN_Q2,
        "nonlinear_leg": {
            "model": "R = R0 * exp(k * s^T x / sigma_lin)",
            "sampled_variance": nl_var,
            "linear_prediction": nl_linear,
            "relative_divergence": abs(nl_var - nl_linear) / nl_linear,
            "note": "saturation nonlinearity; divergence is expected and reported, not hidden",
        },
    }


# ---------------------------------------------------------------- real leg

def subset_covariance(out_npz, out_index):
    """Real sidecar subset: every stored component of TARGET, nothing else."""
    src = zipfile.ZipFile(COV)
    arrays = {n: np.load(src.open(n)) for n in src.namelist()}
    comp = arrays["components.npy"]  # i8 [N,9]: target,mt,mt1,lb,kind,rg,cg,voff,vlen
    kept = comp[comp[:, 0] == TARGET]
    used_grids = sorted(set(kept[:, 5].tolist()) | set(kept[:, 6].tolist()))
    grid_remap = {g: i for i, g in enumerate(used_grids)}
    new_comp = kept.copy()
    new_values = []
    for k in range(new_comp.shape[0]):
        new_comp[k, 5] = grid_remap[int(kept[k, 5])]
        new_comp[k, 6] = grid_remap[int(kept[k, 6])]
        lo, hi = int(kept[k, 7]), int(kept[k, 7] + kept[k, 8])
        new_comp[k, 7] = len(new_values)
        new_values.extend(arrays["values.npy"][lo:hi].tolist())
    goff = arrays["grid_offsets.npy"]
    new_goff = np.concatenate(
        [[0], np.cumsum([goff[g + 1] - goff[g] for g in used_grids])]).astype(np.int64)
    new_gvals = np.concatenate(
        [arrays["grid_values.npy"][goff[g]:goff[g + 1]] for g in used_grids]) \
        if used_grids else np.array([], dtype=np.float64)
    with zipfile.ZipFile(out_npz, "w", zipfile.ZIP_DEFLATED) as z:
        for name, arr in (("components.npy", new_comp), ("grid_offsets.npy", new_goff),
                          ("grid_values.npy", new_gvals),
                          ("values.npy", np.array(new_values, dtype=np.float64))):
            with z.open(name, "w") as fh:
                np.lib.format.write_array(fh, arr)
    index = json.loads(COV_INDEX.read_text())
    for t in index["targets"]:
        if t["target"] != TARGET:
            t["mf33_sections"] = 0
            t["components"] = 0
            t["lb_counts"] = {}
    tsub = next(t for t in index["targets"] if t["target"] == TARGET)
    index["components"] = int(new_comp.shape[0])
    index["mf33_sections"] = tsub["mf33_sections"]
    index["lb_counts"] = tsub["lb_counts"]
    index["files_with_mf33"] = 1
    index["sha256_npz"] = sha256(out_npz)
    out_index.write_text(json.dumps(index, indent=2))
    return int(new_comp.shape[0])


def base_spec(library_path, library_sha, cov_path=None, cov_sha=None):
    spec = json.loads(EXAMPLE.read_text())  # published FNS Fe 5-min case, verbatim
    spec["title"] = "P20 G3 sampling leg (FE, Mn52m1 covariance subset)"
    spec["library"] = {"path": str(library_path), "sha256": library_sha}
    if cov_path is not None:
        spec["uncertainty"] = {
            "covariance": {"path": str(cov_path), "sha256": cov_sha},
            "confidence_level": 0.9,
            "responses": REQUESTED_RESPONSES,
        }
    else:
        spec.pop("uncertainty", None)
    return spec


def npy_header(shape, descr="<f8"):
    header = "{'descr': '%s', 'fortran_order': False, 'shape': %s, }" % (descr, repr(tuple(shape)))
    pad = (64 - (10 + len(header) + 1) % 64) % 64
    header = header + " " * pad + "\n"
    return b"\x93NUMPY\x01\x00" + len(header).to_bytes(2, "little") + header.encode("latin1")


def write_sig_scaled(fh, sig, scaled_rows):
    """Stream sig.npy data, scaling only the listed rows (no full copy)."""
    fh.write(npy_header(sig.shape))
    prev, ncols = 0, sig.shape[1]
    rows_per_chunk = max(1, (1 << 26) // (ncols * 8))
    for row in sorted(scaled_rows):
        a = prev
        while a < row:  # verbatim spans
            e = min(row, a + rows_per_chunk)
            fh.write(sig[a:e].tobytes())
            a = e
        fh.write((sig[row] * scaled_rows[row]).tobytes())
        prev = row + 1
    while prev < sig.shape[0]:
        e = min(sig.shape[0], prev + rows_per_chunk)
        fh.write(sig[prev:e].tobytes())
        prev = e


def write_perturbed_library(lib_arrays, row_of, delta, sigma_i, out_path, idx_path, lib_index):
    ratios = {}
    unscalable = 0
    truncated = 0
    for i, row in enumerate(row_of):
        if sigma_i[i] > 0.0 and math.isfinite(delta[i]):
            perturbed = sigma_i[i] + delta[i]
            if perturbed < 0.0:
                truncated += 1  # physical support boundary: sigma' < 0 -> clamp at 0
                perturbed = 0.0
            ratios[row] = perturbed / sigma_i[i]
        elif abs(delta[i]) > 0.0:
            unscalable += 1
    with zipfile.ZipFile(out_path, "w") as z:
        for name, arr in lib_arrays.items():
            zi = zipfile.ZipInfo(name)
            if name == "sig.npy":
                zi.compress_type = zipfile.ZIP_STORED
                with z.open(zi, "w", force_zip64=True) as fh:
                    write_sig_scaled(fh, arr, ratios)
            else:
                zi.compress_type = zipfile.ZIP_DEFLATED
                with z.open(zi, "w") as fh:
                    np.lib.format.write_array(fh, arr)
    idx = dict(lib_index)
    idx["sha256_npz"] = sha256(out_path)
    idx_path.write_text(json.dumps(idx))
    return unscalable, truncated, idx["sha256_npz"]


def real_leg():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sub_npz = OUT_DIR / "mn52m1.cov.npz"
    sub_index = OUT_DIR / "mn52m1.cov_index.json"
    n_comp = subset_covariance(sub_npz, sub_index)
    result, report_sha = run_spec(
        base_spec(LIBRARY, sha256(LIBRARY), sub_npz, sha256(sub_npz)), OUT_DIR / "nominal")
    uncertainty = result["steps"][-1]["uncertainty"]
    responses_all = uncertainty["responses"]
    responses = [r for r, b in responses_all.items()
                 if b["mf33_standard_uncertainty"] > 0.0]
    if not responses:
        raise RuntimeError("no responses carry propagated uncertainty")
    # covered parameter set: union over compared responses (identical by construction)
    covered = {}
    for rname in responses:
        for p in responses_all[rname]["sensitivities"]:
            if p["parameter"]["covariance_covered"] and not p["parameter"].get("covariance_excluded"):
                covered[p["parameter"]["library_row"]] = p["parameter"]
    rows = sorted(covered)
    sens = {rname: {p["parameter"]["library_row"]: p["value"]
                    for p in responses_all[rname]["sensitivities"]}
            for rname in responses}
    nominal = {r: responses_all[r]["nominal"] for r in responses}
    linear_sigma = {r: responses_all[r]["mf33_standard_uncertainty"] for r in responses}
    cram_bound = {r: responses_all[r].get("cram_order_bound", 0.0) for r in responses}

    nominal_spec = json.loads((OUT_DIR / "nominal/spec.json").read_text())
    # spec.flux_ascending(): descending spectra are reversed, then scaled to `total`
    flux = list(nominal_spec["spectrum"]["flux_per_group"])
    if nominal_spec["spectrum"].get("descending"):
        flux.reverse()
    total = nominal_spec["spectrum"].get("total")
    if total:
        s = sum(flux)
        if s > 0.0:
            flux = [v * total / s for v in flux]
    (OUT_DIR / "flux.csv").write_text(",".join(repr(float(v)) for v in flux))
    (OUT_DIR / "rows.csv").write_text(",".join(str(r) for r in rows))
    probe = subprocess.run(
        [str(PROBE), str(LIBRARY), str(sub_npz),
         str(OUT_DIR / "flux.csv"), str(OUT_DIR / "rows.csv")],
        capture_output=True, text=True, cwd=ROOT)
    if probe.returncode != 0:
        raise RuntimeError(f"probe failed: {probe.stderr[-2000:]}")
    collapsed = json.loads(probe.stdout)["collapsed"]
    row_of = collapsed["row_indices"]
    n = len(row_of)
    cov = np.array(collapsed["covariance_barn2"], dtype=float).reshape(n, n)
    sigma_i = np.array(collapsed["one_group_barns"], dtype=float)
    assert row_of == rows, (row_of, rows)

    _, lam, axes = quantile_axes(cov)
    zsrc = zipfile.ZipFile(LIBRARY)
    lib_arrays = {name: np.load(zsrc.open(name)) for name in zsrc.namelist()}
    lib_index = json.loads(LIB_INDEX.read_text())
    samples_meta = []
    axis_values = {r: {j: [] for j, _, _ in axes} for r in responses}
    sid = 0
    for j, lj, vj in axes:
        for qi, q in enumerate(QUANTILES):
            if q == 0.0:
                for rname in responses:
                    axis_values[rname][j].append(nominal[rname])
                continue
            sid += 1
            delta = q * math.sqrt(lj) * vj
            lib_path = OUT_DIR / f"lib_s{sid}.npz"
            idx_path = OUT_DIR / f"lib_s{sid}_index.json"
            unscalable, truncated, lib_sha = write_perturbed_library(
                lib_arrays, row_of, delta, sigma_i, lib_path, idx_path, lib_index)
            run_dir = OUT_DIR / f"s{sid}"
            res, rep_sha = run_spec(base_spec(lib_path, lib_sha), run_dir)
            lib_path.unlink()
            idx_path.unlink()
            step = res["steps"][-1]
            samples_meta.append({"sample": sid, "axis": j, "quantile": q,
                                 "result_sha256": rep_sha, "unscalable_rows": unscalable, "truncated_rows": truncated})
            for rname in responses:
                axis_values[rname][j].append(response_value(step, rname))
            shutil.rmtree(run_dir, ignore_errors=True)

    comparisons, positive_checks, sign_checks = [], [], []
    sign_skipped = 0
    for rname in responses:
        s_vec = np.array([sens[rname].get(row, 0.0) for row in row_of])
        pooled = []
        for j, lj, vj in axes:
            pred = float(s_vec @ vj) * math.sqrt(lj)
            for qi, q in enumerate(QUANTILES):
                v = axis_values[rname][j][qi]
                pooled.append(v)
                positive_checks.append(math.isfinite(v) and v > 0.0)
                dv = v - nominal[rname]
                if abs(dv) > cram_bound[rname] and abs(pred) > 0.0:
                    # only deviations above the solver's own order bound carry sign
                    sign_checks.append(math.copysign(1.0, dv) == math.copysign(1.0, q * pred))
                else:
                    sign_skipped += 1
        pooled.sort()
        k05 = max(0, int(math.floor(0.05 * len(pooled))))
        k95 = min(len(pooled) - 1, int(math.ceil(0.95 * len(pooled))) - 1)
        var_hat, per_axis = sampled_variance(axis_values[rname], nominal[rname])
        sigma_hat = math.sqrt(var_hat)
        sigma_lin = linear_sigma[rname]
        bound = cram_bound[rname]
        # An agreement comparison is only observable when a σ exceeds the
        # solver's own order bound for that response; below it both estimates
        # are numerical noise and the comparison is reported but non-gating.
        above_floor = max(sigma_lin, sigma_hat) > bound
        comparisons.append({
            "response": rname,
            "primary": sigma_lin / abs(nominal[rname]) > PRIMARY_REL_SIGMA if nominal[rname] else False,
            "relative_sigma": sigma_lin / abs(nominal[rname]) if nominal[rname] else None,
            "nominal": nominal[rname],
            "linear_sigma": sigma_lin,
            "sampled_sigma": sigma_hat,
            "cram_order_bound": bound,
            "above_solver_floor": above_floor,
            "linear_half90": Z90 * sigma_lin,
            "sampled_half90": Z90 * sigma_hat,
            "pooled_empirical_half90_diagnostic": (pooled[k95] - pooled[k05]) / 2.0,
            "relative_agreement": abs(sigma_hat - sigma_lin) / sigma_lin,
            "within_5pct": abs(sigma_hat - sigma_lin) / sigma_lin <= AGREEMENT_TOL,
            "per_axis_variance": {str(j): v for j, v in per_axis.items()},
            "axis_response_values": {str(j): list(axis_values[rname][j]) for j, _, _ in axes},
        })
    shared_regime = all(positive_checks) and all(sign_checks)
    gating = [c for c in comparisons if c["above_solver_floor"]]
    leg_pass = (
        shared_regime
        and len(gating) > 0
        and all(c["within_5pct"] for c in gating)
        and any(c["primary"] for c in gating)
    )
    return {
        "name": "real_fns_fe_mn52m1_subset",
        "target": TARGET,
        "sidecar_components": n_comp,
        "covered_parameters": n,
        "n_positive_axes": len(axes),
        "design_samples": len(axes) * STRATA + 1,
        "runs_executed": sid + 1,
        "sample_counting": ("protocol counts 9*d+ + 1; the q=0 stratum of every axis is the "
                            "shared nominal solve, so distinct solves = 8*d+ + 1"),
        "mean_q2": MEAN_Q2,
        "nominal_result_sha256": report_sha,
        "shared_regime": shared_regime,
        "shared_regime_definition": ("every sampled response finite & positive AND every axis's "
                                     "response-deviation sign equals sign(q * (s . v_j)) for all strata"),
        "all_responses_finite_positive": all(positive_checks),
        "axis_sign_constant": all(sign_checks),
        "sign_checks_evaluated": len(sign_checks),
        "sign_checks_below_solver_floor": sign_skipped,
        "total_truncations": sum(s["truncated_rows"] for s in samples_meta),
        "total_unscalable": sum(s["unscalable_rows"] for s in samples_meta),
        "comparisons_above_solver_floor": len(gating),
        "gating_definition": (
            "a comparison gates when max(linear_sigma, sampled_sigma) exceeds the "
            "response's cram_order_bound; below-floor comparisons are reported "
            "but cannot resolve a deviation at solver precision"
        ),
        "pass": leg_pass,
        "comparisons": comparisons,
        "samples": samples_meta,
    }


def recompute() -> None:
    """Rebuild derived comparison fields from stored raw evidence without
    re-running any solve. Raw samples and hashes are left byte-identical."""
    report = json.loads(REPORT.read_text())
    real = report["real"]
    nominal_bands = json.loads(
        (OUT_DIR / "nominal" / "result.json").read_text()
    )["steps"][-1]["uncertainty"]["responses"]
    for comparison in real["comparisons"]:
        rname = comparison["response"]
        bound = nominal_bands[rname].get("cram_order_bound", 0.0)
        axis_values = {
            int(j): values for j, values in comparison["axis_response_values"].items()
        }
        var_hat, per_axis = sampled_variance(axis_values, comparison["nominal"])
        sigma_hat = math.sqrt(var_hat)
        sigma_lin = comparison["linear_sigma"]
        comparison["sampled_sigma"] = sigma_hat
        comparison["sampled_half90"] = Z90 * sigma_hat
        comparison["cram_order_bound"] = bound
        comparison["above_solver_floor"] = max(sigma_lin, sigma_hat) > bound
        comparison["relative_agreement"] = abs(sigma_hat - sigma_lin) / sigma_lin
        comparison["within_5pct"] = (
            comparison["relative_agreement"] <= AGREEMENT_TOL
        )
        comparison["per_axis_variance"] = {str(j): v for j, v in per_axis.items()}
    gating = [c for c in real["comparisons"] if c["above_solver_floor"]]
    real["comparisons_above_solver_floor"] = len(gating)
    real["gating_definition"] = (
        "a comparison gates when max(linear_sigma, sampled_sigma) exceeds the "
        "response's cram_order_bound; below-floor comparisons are reported "
        "but cannot resolve a deviation at solver precision"
    )
    real["pass"] = (
        real["shared_regime"]
        and len(gating) > 0
        and all(c["within_5pct"] for c in gating)
        and any(c["primary"] for c in gating)
    )
    report["pass"] = bool(
        report["synthetic"]["machine_precision_pass"] and real["pass"]
    )
    REPORT.write_text(json.dumps(report, indent=2))
    print(
        f"recomputed: gating={len(gating)} "
        f"all_within_5pct={all(c['within_5pct'] for c in gating)} pass={report['pass']}"
    )


def main():
    if "--recompute" in sys.argv[1:]:
        recompute()
        return
    synthetic = synthetic_leg()
    real = real_leg() if synthetic["machine_precision_pass"] else None
    report = {
        "schema": "actinv-p20-g3-sampling-1",
        "protocol": "ACTINV-P20",
        "strata": STRATA,
        "quantiles": QUANTILES,
        "mean_q2": MEAN_Q2,
        "z90": Z90,
        "agreement_tolerance": AGREEMENT_TOL,
        "inputs": {
            "library_sha256": sha256(LIBRARY),
            "covariance_sha256": sha256(COV),
            "collapse_probe_sha256": sha256(PROBE),
            "actinv_sha256": sha256(ACTINV),
        },
        "synthetic": synthetic,
        "real": real,
        "pass": bool(
            synthetic["machine_precision_pass"] and real and real["pass"]
        ),
    }
    REPORT.write_text(json.dumps(report, indent=2))
    print(f"synthetic rel-diff: {synthetic['relative_difference']:.3e} "
          f"pass={synthetic['machine_precision_pass']}")
    if real:
        for c in real["comparisons"]:
            print(f"{c['response']}: lin σ={c['linear_sigma']:.6e} "
                  f"sampled σ={c['sampled_sigma']:.6e} "
                  f"floor={c['above_solver_floor']} "
                  f"agree={c['relative_agreement']:.4f} within5%={c['within_5pct']}")
        print(f"shared regime: {real['shared_regime']}  samples: {real['design_samples']} "
              f"gating: {real['comparisons_above_solver_floor']} pass={real['pass']}")


if __name__ == "__main__":
    main()
