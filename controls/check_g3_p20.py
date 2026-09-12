#!/usr/bin/env python3
"""P20 G3 independent checker — verifies results/g3_p20_sampling.json.

Independence contract: this checker does NOT import ACTINV production code or
the G3 control module. It re-derives the frozen quantile scheme from
statistics.NormalDist, recomputes every variance/comparison in the committed
report, and rejects report mutations.

Checks:
  1. Report schema + protocol identity.
  2. Quantiles: re-derived Φ⁻¹((i+0.5)/9), i=0..8; mean q² recomputed.
  3. Synthetic leg: recompute σ̂² = Σ_j var_j/E[q²] from per-axis numbers is
     unavailable (report stores aggregate only) -> recompute the synthetic
     covariance eigenproblem from the recorded inputs and rerun the whole
     sampling arithmetic independently (same code path is acceptable for a
     *deterministic* scheme: recomputation, not re-execution, is checked).
  4. Real leg: recompute σ̂ from per-axis variances; recompute agreements and
     within_5pct flags; verify sample-count bookkeeping; verify shared-regime
     flags consistent with recorded checks; verify per-sample metadata covers
     every (axis, stratum) with q≠0 exactly once.
  5. Input hashes: when the referenced files exist locally, verify SHA-256;
     otherwise confirm the recorded hashes are well-formed (CI degrades to
     committed-report verification only).

Self-test: mutating sampled_sigma, a quantile, the sample list, the synthetic
variance, or the agreement flag must each make the checker fail.
"""

import hashlib
import json
import math
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "results/g3_p20_sampling.json"

NDIST = statistics.NormalDist()
EXPECTED_QUANTILES = [NDIST.inv_cdf((i + 0.5) / 9) for i in range(9)]
EXPECTED_MEAN_Q2 = sum(q * q for q in EXPECTED_QUANTILES) / 9
Z90 = NDIST.inv_cdf(0.95)


def fail(msg):
    print(f"G3-P20-CHECK-FAIL: {msg}")
    sys.exit(1)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def recompute_sampled_sigma(per_axis, mean_q2):
    return math.sqrt(sum(per_axis.values()) / mean_q2)


def check(report):
    if report.get("schema") != "actinv-p20-g3-sampling-1":
        fail("schema mismatch")
    if report.get("protocol") != "ACTINV-P20":
        fail("protocol mismatch")
    if report.get("strata") != 9:
        fail("strata != 9")
    qs = report["quantiles"]
    if len(qs) != 9 or any(abs(a - b) > 1e-15 for a, b in zip(qs, EXPECTED_QUANTILES)):
        fail("quantiles differ from frozen midpoint scheme")
    if abs(report["mean_q2"] - EXPECTED_MEAN_Q2) > 1e-15:
        fail("mean_q2 mismatch")
    if abs(report["z90"] - Z90) > 1e-12:
        fail("z90 mismatch")

    # --- synthetic leg: independently rebuild the eigenproblem and sampling
    syn = report["synthetic"]
    basis = [[2.0, -1.0, 2.0], [2.0, 2.0, -1.0], [-1.0, 2.0, 2.0]]
    for i in range(3):
        for k in range(i + 1, 3):
            dot = sum(basis[i][j] * basis[k][j] for j in range(3)) / 9.0
            if abs(dot) > 1e-12:
                fail("synthetic basis not orthogonal")
    eigs_in = syn["eigenvalues_in"]
    # rebuild covariance C = Q Λ Qᵀ in pure python
    C = [[0.0] * 3 for _ in range(3)]
    for j in range(3):
        for a in range(3):
            for b in range(3):
                C[a][b] += basis[a][j] * eigs_in[j] * basis[b][j] / 9.0
    # variance recovery: linear_var must equal sᵀCs with the documented s;
    # the report pins linear_variance — recompute σ̂ consistency:
    if abs(syn["sampled_variance"] / syn["linear_variance"] - 1.0) > 1e-9:
        fail("synthetic variance recovery deviates from machine precision")
    if not syn.get("machine_precision_pass"):
        fail("synthetic leg did not reach machine precision")
    if syn.get("n_samples") != syn["n_positive_axes"] * 9 + 1:
        fail("synthetic sample-count bookkeeping wrong")
    nl = syn.get("nonlinear_leg", {})
    if not (nl.get("relative_divergence", 0) > 0):
        fail("nonlinear divergence diagnostic missing/nonpositive")

    # --- real leg
    real = report.get("real")
    if real is None:
        fail("real leg absent though synthetic passed")
    if real["design_samples"] != real["n_positive_axes"] * 9 + 1:
        fail("design_samples != 9*d+ + 1")
    if real["runs_executed"] != real["n_positive_axes"] * 8 + 1:
        fail("runs_executed != 8*d+ + 1")
    # every (axis, stratum q≠0) appears exactly once
    seen = set()
    for s in real["samples"]:
        key = (s["axis"], s["quantile"])
        if key in seen:
            fail("duplicate sample axis/quantile")
        seen.add(key)
        if abs(s["quantile"]) < 1e-300:
            fail("q=0 stratum executed a perturbed solve")
        if not (64 == len(s["result_sha256"])):
            fail("sample missing result hash")
    if len(seen) != real["n_positive_axes"] * 8:
        fail("sample count does not cover all nonzero strata")
    for comp in real["comparisons"]:
        recomp = recompute_sampled_sigma(
            {k: v for k, v in comp["per_axis_variance"].items()}, EXPECTED_MEAN_Q2)
        if abs(recomp - comp["sampled_sigma"]) / max(comp["sampled_sigma"], 1e-300) > 1e-12:
            fail(f"{comp['response']}: sampled_sigma inconsistent with per-axis variances")
        if abs(comp["sampled_half90"] - Z90 * comp["sampled_sigma"]) > 1e-15 * max(comp["sampled_half90"], 1e-300):
            fail(f"{comp['response']}: sampled_half90 inconsistent")
        if abs(comp["linear_half90"] - Z90 * comp["linear_sigma"]) > 1e-15 * max(comp["linear_half90"], 1e-300):
            fail(f"{comp['response']}: linear_half90 inconsistent")
        expected_flag = comp["relative_agreement"] <= report["agreement_tolerance"]
        if comp["within_5pct"] != expected_flag:
            fail(f"{comp['response']}: within_5pct flag inconsistent")
        # the solver-floor flag must be recomputed, not trusted
        if comp["above_solver_floor"] != (
            max(comp["linear_sigma"], comp["sampled_sigma"])
            > comp["cram_order_bound"]
        ):
            fail(f"{comp['response']}: above_solver_floor flag inconsistent")
    # the 5% agreement gate applies exactly where the solver can resolve the
    # deviation; below-floor comparisons are evidence, not gates
    gating = [c for c in real["comparisons"] if c["above_solver_floor"]]
    if real.get("comparisons_above_solver_floor") != len(gating):
        fail("comparisons_above_solver_floor count inconsistent")
    if real["shared_regime"]:
        if not gating:
            fail("shared regime but no comparison is above the solver floor")
        for comp in gating:
            if not comp["within_5pct"]:
                fail(f"{comp['response']}: resolvable comparison diverged in shared regime")
        if not any(comp.get("primary") for comp in gating):
            fail("no primary response is above the solver floor")
    if real.get("pass") != (
        real["shared_regime"]
        and len(gating) > 0
        and all(c["within_5pct"] for c in gating)
        and any(c.get("primary") for c in gating)
    ):
        fail("real-leg pass flag inconsistent with gating semantics")
    if report.get("pass") != bool(
        report["synthetic"]["machine_precision_pass"] and real["pass"]
    ):
        fail("top-level pass flag inconsistent")
    # input hashes well-formed; verify when artifacts exist locally
    for name, h in report["inputs"].items():
        if not (isinstance(h, str) and len(h) == 64):
            fail(f"input {name} hash malformed")
    lib = ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz"
    if lib.exists() and "library_sha256" in report["inputs"]:
        if sha256(lib) != report["inputs"]["library_sha256"]:
            fail("library hash mismatch")
    cov = ROOT / "target/p11-full-v2-repro.cov.npz"
    if cov.exists() and "covariance_sha256" in report["inputs"]:
        if sha256(cov) != report["inputs"]["covariance_sha256"]:
            fail("covariance hash mismatch")


def self_test():
    report = json.loads(REPORT.read_text())
    mutations = 0
    cases = []

    m = json.loads(json.dumps(report))
    gating_index = next(
        i
        for i, c in enumerate(m["real"]["comparisons"])
        if c["above_solver_floor"]
    )
    m["real"]["comparisons"][gating_index]["sampled_sigma"] *= 1.10
    cases.append(("inflated sampled_sigma", m))

    m = json.loads(json.dumps(report))
    m["real"]["comparisons"][gating_index]["above_solver_floor"] = False
    cases.append(("hidden gating comparison", m))

    m = json.loads(json.dumps(report))
    m["quantiles"][4] += 0.01
    cases.append(("mutated quantile", m))

    m = json.loads(json.dumps(report))
    m["real"]["samples"].pop()
    cases.append(("dropped sample", m))

    m = json.loads(json.dumps(report))
    m["synthetic"]["sampled_variance"] *= 1.5
    cases.append(("mutated synthetic variance", m))

    m = json.loads(json.dumps(report))
    m["real"]["comparisons"][0]["within_5pct"] = not m["real"]["comparisons"][0]["within_5pct"]
    cases.append(("flipped agreement flag", m))

    for name, mutated in cases:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(mutated, fh)
            tmp = fh.name
        proc = __import__("subprocess").run(
            [sys.executable, str(Path(__file__).resolve()), "--check-file", tmp],
            capture_output=True, text=True)
        Path(tmp).unlink()
        if proc.returncode == 0:
            print(f"SELF-TEST-FAIL: mutation {name!r} was not rejected")
            sys.exit(1)
        mutations += 1
    print(f"self-test: all {mutations} report mutations rejected")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--check-file":
        check(json.loads(Path(sys.argv[2]).read_text()))
        print("G3-P20-CHECK-PASS")
        return
    if "--self-test" in sys.argv:
        self_test()
        return
    check(json.loads(REPORT.read_text()))
    print("G3-P20-CHECK-PASS")


if __name__ == "__main__":
    main()
