#!/usr/bin/env python3
"""P50 G2 controls — frozen VoI correctness checks on synthetic corpora.

Every expected share is computed independently in Python: sensitivities are
read from the emitted record; the collapsed MF=33 covariance is rebuilt with
the p11_covariance reference (the same code that independently verifies the
production collapse) — no emit-path code is reused.

  planted_dominant     — the larger-|share| channel ranks first, and every
                         emitted share matches the independent value exactly
  shares_sum_to_total  — Σ share_i == total_propagated_variance (fp tolerance)
  uncovered_unranked   — a reaction with no MF=33 coverage appears under
                         `unranked`, never in `top`
  anticorrelation      — a strong negative cross term emits a negative
                         variance_share for the right member and the sum
                         still equals the propagated variance
  top_truncation       — top=1 emits exactly the dominant parameter
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p11_fixtures as fx  # noqa: E402
from p11_covariance import collapse, load_activation, read_sidecar  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
OUT = ROOT / "results" / "g2_p50_controls.json"
RESPONSE = "heat.total"


def run_spec(work: Path, name: str, spec: dict) -> dict:
    spec_path, out = work / f"{name}.json", work / f"{name}.result.json"
    fx.write_json(spec_path, spec)
    proc = subprocess.run(
        [str(ACTINV), "run", str(spec_path), str(out)],
        capture_output=True, text=True, timeout=300, cwd=ROOT)
    if proc.returncode != 0:
        raise RuntimeError(f"{name}: actinv run failed: {proc.stderr[-800:]}")
    return json.load(open(out))


def write_covariance_variant(path: Path, activation: Path,
                             components: np.ndarray, values: np.ndarray) -> Path:
    """p11 covariance sidecar with caller-declared components/values."""
    np.savez(
        path,
        components=components.astype(np.int64),
        grid_offsets=np.asarray([0, 2], dtype=np.int64),
        grid_values=np.asarray(fx.BOUNDS, dtype=np.float64),
        values=values.astype(np.float64),
    )
    activation_index = activation.with_name(activation.stem + "_index.json")
    index = path.with_name(path.stem + "_index.json")
    mts = sorted({int(row[1]) for row in components})
    fx.write_json(index, {
        "schema": "actinv-covariance-index-1",
        "projectile": "neutron",
        "activation_library": str(activation),
        "activation_library_sha256": fx.sha256(activation),
        "activation_index": str(activation_index),
        "activation_index_sha256": fx.sha256(activation_index),
        "group_boundary_sha256": fx.group_hash(fx.BOUNDS),
        "builder_fingerprint": "2" * 64,
        "source_manifest_sha256": "3" * 64,
        "targets": [{"target": 0, "file": "p11-synthetic.endf",
                     "source_sha256": "1" * 64, "mat": 2631, "za": 26056,
                     "liso": 0, "mf33_sections": len(mts),
                     "components": len(components),
                     "lb_counts": {"0": len(components)}}],
        "files": 1, "files_with_mf33": 1, "mf33_sections": len(mts),
        "components": int(len(components)), "lb_counts": {"0": int(len(components))},
        "columns": "synthetic control", "sha256_npz": fx.sha256(path)})
    return index


def collapsed_matrix(spec: dict, activation_path: Path, covariance_path: Path):
    """Independent collapsed covariance + covered library-row order."""
    flux = np.asarray(spec["spectrum"]["flux_per_group"], dtype=np.float64)
    activation = load_activation(activation_path)
    selected = list(range(len(activation["rows"])))
    collapsed = collapse(read_sidecar(covariance_path), activation, flux, selected)
    n = len(collapsed["row_indices"])
    matrix = np.asarray(collapsed["covariance_barn2"]).reshape(n, n)
    return matrix, collapsed["row_indices"]


def expected_shares(result: dict, matrix: np.ndarray, covered_rows: list[int],
                    response: str, step: int = 0):
    """share_i = s_i·(Σ·s)_i over covered (spectrum, row) positions."""
    band = result["steps"][step]["uncertainty"]["responses"][response]
    covered = [r for r in band["sensitivities"]
               if r["parameter"]["covariance_covered"]
               and not r["parameter"]["covariance_excluded"]
               and r["parameter"]["spectrum"] == 0]
    order = [r["parameter"]["library_row"] for r in covered]
    index = {row: i for i, row in enumerate(covered_rows)}
    s = np.asarray([r["value"] for r in covered])
    shares = s * (matrix[np.ix_([index[r] for r in order],
                                [index[r] for r in order])] @ s)
    return covered, shares


def voi_entries(result: dict, response: str, step: int = 0) -> list[dict]:
    return (result["steps"][step]["uncertainty"]["responses"][response]
            ["voi"]["top"])


def mt_of(entry: dict) -> int:
    return entry["parameter"]["MT"]


def main() -> int:
    checks: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="p50-g2-", dir="target") as d:
        work = Path(d)
        fixture = fx.make_fixture(work)

        def spec_with(voi_top: int, covariance: Path | None = None) -> dict:
            spec = fx.specification(fixture, mode="trace", cram_order=16)
            spec["uncertainty"]["require_complete"] = False
            spec["uncertainty"]["voi"] = {"top": voi_top}
            spec["uncertainty"]["responses"] = [RESPONSE]
            if covariance is not None:
                spec["uncertainty"]["covariance"] = {
                    "path": str(covariance),
                    "sha256": fx.sha256(covariance)}
            return spec

        # --- planted dominant + exact shares + sum rule --------------------
        result = run_spec(work, "full", spec_with(10))
        matrix, covered_rows = collapsed_matrix(
            spec_with(10), fixture["library"], fixture["covariance"])
        covered, shares = expected_shares(result, matrix, covered_rows, RESPONSE)
        emitted = voi_entries(result, RESPONSE)
        dominant_mt = covered[int(np.argmax(np.abs(shares)))]["parameter"]["MT"]
        emitted_by_mt = {mt_of(e): e for e in emitted}
        exact = all(
            abs(emitted_by_mt[r["parameter"]["MT"]]["variance_share"]
                - shares[i]) <= 1e-6 * max(1.0, abs(shares[i]))
            for i, r in enumerate(covered))
        total = result["steps"][0]["uncertainty"]["responses"][RESPONSE] \
            ["voi"]["total_propagated_variance"]
        checks["planted_dominant"] = {
            "pass": emitted and mt_of(emitted[0]) == dominant_mt and exact,
            "dominant_mt": int(dominant_mt)}
        checks["shares_sum_to_total"] = {
            "pass": abs(float(np.sum(shares)) - total)
                    <= 1e-6 * max(1.0, abs(total)),
            "sum": float(np.sum(shares)), "total": total}

        # --- uncovered parameter lands in unranked, never in top ------------
        cov_uncovered = work / "cov_uncovered.npz"
        write_covariance_variant(
            cov_uncovered, fixture["library"],
            components=np.asarray([[0, 102, 102, 0, 0, 0, 0, 0, 1]]),
            values=np.asarray([4.0e-4]))
        result_u = run_spec(work, "uncovered", spec_with(10, cov_uncovered))
        band_u = (result_u["steps"][0]["uncertainty"]["responses"][RESPONSE])
        voi_u = band_u["voi"]
        uncovered_mt103 = any(
            e["parameter"]["MT"] == 103
            for e in band_u["sensitivities"]
            if e["value"] != 0.0 and not e["parameter"]["covariance_covered"])
        ranked_mts = {mt_of(e) for e in voi_u["top"]}
        unranked_xs = voi_u["unranked"].get("cross_section_mf33", {})
        checks["uncovered_unranked"] = {
            "pass": (uncovered_mt103 and 103 not in ranked_mts
                     and unranked_xs.get("count", 0) >= 1
                     and unranked_xs.get("sensitivity_l2", 0.0) > 0.0),
            "unranked": unranked_xs}

        # --- anticorrelation: negative share emitted, sum still exact -------
        cov_anti = work / "cov_anti.npz"
        write_covariance_variant(
            cov_anti, fixture["library"],
            components=np.asarray([[0, 102, 102, 0, 0, 0, 0, 0, 1],
                                   [0, 102, 103, 0, 0, 0, 0, 1, 1],
                                   [0, 103, 103, 0, 0, 0, 0, 2, 1]]),
            values=np.asarray([4.0e-4, -5.0e-4, 9.0e-4]))
        result_a = run_spec(work, "anti", spec_with(10, cov_anti))
        matrix_a, rows_a = collapsed_matrix(
            spec_with(10, cov_anti), fixture["library"], cov_anti)
        _, shares_a = expected_shares(result_a, matrix_a, rows_a, RESPONSE)
        emitted_a = voi_entries(result_a, RESPONSE)
        has_negative = any(e["variance_share"] < 0.0 for e in emitted_a)
        expected_negative = bool(np.any(shares_a < 0.0))
        total_a = (result_a["steps"][0]["uncertainty"]["responses"][RESPONSE]
                   ["voi"]["total_propagated_variance"])
        checks["anticorrelation"] = {
            "pass": (expected_negative and has_negative
                     and abs(float(np.sum(shares_a)) - total_a)
                         <= 1e-6 * max(1.0, abs(total_a))),
            "shares": [float(x) for x in shares_a]}

        # --- top truncation --------------------------------------------------
        result_t = run_spec(work, "trunc", spec_with(1))
        emitted_t = voi_entries(result_t, RESPONSE)
        checks["top_truncation"] = {
            "pass": len(emitted_t) == 1
                    and mt_of(emitted_t[0]) == dominant_mt}

    passed = all(c["pass"] for c in checks.values())
    OUT.write_text(json.dumps({"schema": "actinv-p50-g2-1",
                               "checks": checks, "pass": passed},
                              indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": passed, "checks": list(checks)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
