#!/usr/bin/env python3
"""Reduced fusion-isotope chain; ACTINV core verification, not transport reproduction."""

import argparse
from decimal import Decimal, localcontext
import json
import math
from pathlib import Path
import subprocess
import tempfile

from check_fusion_isotope_001 import compare, digest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples/fusion_medical_isotopes/ac225/reduced_case.json"
REFERENCE = ROOT / "examples/fusion_medical_isotopes/ac225/reference.json"
PROTOCOL = ROOT / "protocols/FUSION-ISOTOPE-001-AMENDMENT-1.md"
COEFFICIENTS = ROOT / "data/cram_coefficients.json"
RESULTS = ROOT / "results/fusion-isotope-001"
# Re-seated after the CB2 kernel commits changed cram_probe.rs and sparse.rs (Amendment 2); the
# 2026-09-18 receipt, reduced-chain.json, stays unchanged as the record of the 710969c run.
RECEIPT = RESULTS / "reduced-chain-2026-09-23.json"
NAMES = ["Th230", "Th229", "Ra225", "Ac225", "downstream_sink"]


def model(case, reference):
    p, c = reference["published"], reference["conventions"]
    conventions = case["conventions"]
    feed = (p["th230_mass_kg"] * 1000 / conventions["th230_molar_mass_g"]
            * conventions["avogadro_per_mol"])
    source = p["source_area_m2"] * 10000 * p["emitted_neutrons_per_cm2_s"]
    production = source * p["th229_atoms_per_emitted_neutron"]
    day = conventions["seconds_per_day"]
    decay = case["daughter_decay_reference"]
    rates = [production / feed,
             math.log(2) / (p["th229_half_life_years"] * c["seconds_per_year"]),
             math.log(2) / (decay["ra225_half_life_days"] * day),
             math.log(2) / (decay["ac225_half_life_days"] * day), 0.0]
    return feed, production, rates


def analytic(initial, losses, transfers, seconds):
    """Closed-form Bateman paths, independent of matrix/CRAM implementation."""
    with localcontext() as context:
        context.prec = 80
        a = [Decimal(str(value)) for value in losses]
        b = [Decimal(str(value)) for value in transfers]
        t = Decimal(str(seconds))
        answer = [Decimal(0) for _ in initial]
        for start, value in enumerate(initial):
            amplitude = Decimal(str(value))
            if not amplitude:
                continue
            for stop in range(start, len(initial)):
                if stop > start:
                    amplitude *= b[stop - 1]
                if not amplitude:
                    break
                subtotal = Decimal(0)
                for middle in range(start, stop + 1):
                    denominator = Decimal(1)
                    for other in range(start, stop + 1):
                        if middle != other:
                            denominator *= a[other] - a[middle]
                    if not denominator:
                        raise ValueError("Bateman control requires distinct rates on active paths")
                    subtotal += (-a[middle] * t).exp() / denominator
                answer[stop] += amplitude * subtotal
        return [float(value) for value in answer]


def matrix(rates):
    triplets = []
    for column, rate in enumerate(rates[:-1]):
        if rate:
            triplets.extend([(column, column, -rate), (column + 1, column, rate)])
    return triplets


def probe_step(binary, work, initial, rates, seconds):
    """Single leaf process. run(timeout=...) kills and reaps it on timeout."""
    coeff = json.loads(COEFFICIENTS.read_text())["Cram16Solver"]
    triplets = matrix(rates)
    lines = [f"{len(initial)} {len(triplets)}"]
    lines += [f"{i} {j} {v:.17e}" for i, j, v in triplets]
    lines += [f"{seconds:.17e}", " ".join(f"{v:.17e}" for v in initial),
              str(coeff["alpha0"]), str(len(coeff["theta_re"]))]
    lines += [" ".join(str(v) for v in row) for row in zip(
        coeff["theta_re"], coeff["theta_im"], coeff["alpha_re"], coeff["alpha_im"])]
    with tempfile.TemporaryDirectory(prefix="fusion-chain-", dir=work) as directory:
        input_path = Path(directory) / "input.txt"
        output_path = Path(directory) / "output.txt"
        input_path.write_text("\n".join(lines) + "\n")
        subprocess.run([str(binary), str(input_path), str(output_path), "1"],
                       check=True, capture_output=True, text=True, timeout=60)
        values = [float(line) for line in output_path.read_text().splitlines()
                  if not line.startswith("#")]
    if len(values) != len(initial) or not all(math.isfinite(v) for v in values):
        raise ValueError("invalid ACTINV probe output")
    return values


def score(actual, expected, feed, tolerances):
    if len(actual) != len(expected) or not all(math.isfinite(v) for v in actual):
        raise ValueError("non-finite or incomplete inventory")
    absolute, relative = tolerances["absolute_atoms"], tolerances["relative"]
    normalized = max(abs(a - e) / (absolute + relative * abs(e))
                     for a, e in zip(actual, expected))
    conservation = abs(math.fsum(actual) - feed) / feed
    return {"max_error_over_tolerance": normalized,
            "conservation_relative_error": conservation,
            "passed": (normalized <= 1 and min(actual) >= -absolute
                       and conservation <= tolerances["conservation_relative"])}


def run(binary, work):
    if digest(CASE) != "1a6f04eadc6887ac0da1739ed0abd07194009e7b3b6d5f9aedfd81a5ea7262fe":
        raise ValueError("frozen case changed; an amendment is required")
    if digest(PROTOCOL) != "a3f3259c4f805d3c0e9250030e467ce4c13466a4a987fed4f1b7674400919cf8":
        raise ValueError("frozen protocol changed; an amendment is required")
    case, reference = json.loads(CASE.read_text()), json.loads(REFERENCE.read_text())
    feed, production, rates = model(case, reference)
    initial = [feed, 0.0, 0.0, 0.0, 0.0]
    day = case["conventions"]["seconds_per_day"]
    tolerances = case["numerical_tolerances"]
    rows = []
    for days in case["irradiation_days"]:
        seconds = days * day
        expected = analytic(initial, rates, rates[:-1], seconds)
        actual = probe_step(binary, work, initial, rates, seconds)
        half = probe_step(binary, work, initial, rates, seconds / 2)
        split = probe_step(binary, work, half, rates, seconds / 2)
        rows.append({"phase": "irradiation", "days": days, "atoms": actual,
                     "analytic_atoms": expected,
                     "numerical": score(actual, expected, feed, tolerances),
                     "split_atoms": split,
                     "split_check": score(split, actual, feed, tolerances)})
    eoi_days = case["cooling_after_irradiation_days"]
    eoi = next(row for row in rows if row["days"] == eoi_days)
    cooling_rates = [0.0, *rates[1:]]
    for days in case["cooling_days"]:
        expected = analytic(eoi["analytic_atoms"], cooling_rates,
                            cooling_rates[:-1], days * day)
        actual = probe_step(binary, work, eoi["atoms"], cooling_rates, days * day)
        rows.append({"phase": "cooling", "days": days, "atoms": actual,
                     "analytic_atoms": expected,
                     "numerical": score(actual, expected, feed, tolerances)})
    comparisons = []
    for year, published in zip(case["paper"]["table6_years"], case["paper"]["table6_th229_ci"]):
        days = year * reference["conventions"]["seconds_per_year"] / day
        row = next(row for row in rows if row["phase"] == "irradiation" and row["days"] == days)
        ci = row["atoms"][1] * rates[1] / reference["conventions"]["bq_per_ci"]
        comparisons.append({"years": year, "published_th229_ci": published,
                            "reduced_model_th229_ci": ci, "residual_ci": ci - published,
                            "relative_residual": (ci - published) / published})
    passed = all(row["numerical"]["passed"] and row.get("split_check", {"passed": True})["passed"]
                 for row in rows)
    return {
        "benchmark": "fusion-isotope-001", "scope": case["scope"],
        "overall_status": "not_reproduced", "actinv_core_executed": True,
        "actinv_cli_executed": False, "openmc_executed": False,
        "numerical_verification": "passed" if passed else "failed",
        "publication_comparison": "unmatched_reduced_model_no_agreement_verdict",
        "hashes": {"case": digest(CASE), "opening_reference": digest(REFERENCE),
                   "protocol": digest(PROTOCOL), "control": digest(Path(__file__)),
                   "probe_source": digest(ROOT / "crates/actinv-core/src/bin/cram_probe.rs"),
                   "cram_source": digest(ROOT / "crates/actinv-core/src/cram.rs"),
                   "sparse_source": digest(ROOT / "crates/actinv-core/src/sparse.rs"),
                   "coefficients": digest(COEFFICIENTS)},
        "execution": {"probe_binary_sha256": digest(binary), "repetitions": 1,
                      "child_timeout_seconds": 60, "cram_order": 16},
        "states": NAMES, "initial_atoms": initial,
        "initial_th229_production_atoms_per_s": production,
        "irradiation_loss_rates_per_s": rates,
        "cooling_loss_rates_per_s": cooling_rates,
        "numerical_tolerances": tolerances, "omissions": case["omissions"],
        "histories": rows, "table6_comparison": comparisons,
    }


def verify(receipt):
    if (receipt["overall_status"] != "not_reproduced"
            or receipt["publication_comparison"] != "unmatched_reduced_model_no_agreement_verdict"
            or receipt["numerical_verification"] != "passed"
            or receipt["actinv_cli_executed"] or receipt["openmc_executed"]
            or not receipt["actinv_core_executed"]):
        raise ValueError("invalid scientific verdict")
    feed = receipt["initial_atoms"][0]
    for row in receipt["histories"]:
        actual_score = score(row["atoms"], row["analytic_atoms"], feed, receipt["numerical_tolerances"])
        compare(row["numerical"], actual_score)
        if not actual_score["passed"]:
            raise ValueError("numerical verification failed")
        if "split_atoms" in row:
            split_score = score(row["split_atoms"], row["atoms"], feed, receipt["numerical_tolerances"])
            compare(row["split_check"], split_score)
            if not split_score["passed"]:
                raise ValueError("split-step verification failed")


def check_recorded(recorded, fresh):
    if recorded.keys() != fresh.keys():
        raise ValueError("receipt schema mismatch")
    verify(recorded)
    verify(fresh)
    # Build/platform identities are recorded, not required to match across CI machines.
    for evidence in (recorded, fresh):
        identity = evidence["execution"]["probe_binary_sha256"]
        if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("invalid binary identity")
    for key in fresh:
        if key not in {"execution", "histories", "table6_comparison"}:
            compare(recorded[key], fresh[key], key)
    compare({k: v for k, v in recorded["execution"].items() if k != "probe_binary_sha256"},
            {k: v for k, v in fresh["execution"].items() if k != "probe_binary_sha256"})
    if len(recorded["histories"]) != len(fresh["histories"]):
        raise ValueError("history count mismatch")
    for saved, live in zip(recorded["histories"], fresh["histories"]):
        compare(saved["phase"], live["phase"])
        compare(saved["days"], live["days"])
        compare(saved["analytic_atoms"], live["analytic_atoms"])
        # Solver roundoff and tiny error scores may differ by platform. Both
        # histories must independently pass the predeclared analytic bound.
        if saved.keys() != live.keys():
            raise ValueError("history schema mismatch")
    for evidence in (recorded, fresh):
        for row, year, published in zip(evidence["table6_comparison"],
                                       json.loads(CASE.read_text())["paper"]["table6_years"],
                                       json.loads(CASE.read_text())["paper"]["table6_th229_ci"]):
            compare(row["years"], year)
            compare(row["published_th229_ci"], published)
            history = next(h for h in evidence["histories"]
                           if h["phase"] == "irradiation" and h["days"] == year * 365.25)
            ci = history["atoms"][1] * evidence["irradiation_loss_rates_per_s"][1] / 3.7e10
            compare(row["reduced_model_th229_ci"], ci)
            compare(row["residual_ci"], ci - published)
            compare(row["relative_residual"], (ci - published) / published)
        if len(evidence["table6_comparison"]) != 5:
            raise ValueError("publication checkpoint count mismatch")


def table(receipt):
    lines = ["| Irradiation (years) | Paper Th-229 (Ci) | Reduced model (Ci) | Difference |",
             "| ---: | ---: | ---: | ---: |"]
    for row in receipt["table6_comparison"]:
        lines.append(f'| {row["years"]} | {row["published_th229_ci"]:,} | '
                     f'{row["reduced_model_th229_ci"]:,.2f} | {row["relative_residual"]:+.2%} |')
    return "\n".join(lines) + "\n"


def plot(receipt, destination):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = receipt["table6_comparison"]
    figure, axes = plt.subplots(2, 1, figsize=(7, 6), sharex=True,
                                gridspec_kw={"height_ratios": [3, 1]}, layout="constrained")
    years = [row["years"] for row in rows]
    axes[0].plot(years, [row["reduced_model_th229_ci"] for row in rows], "o-", label="ACTINV reduced chain")
    axes[0].plot(years, [row["published_th229_ci"] for row in rows], "s--", label="Paper Table 6")
    axes[0].set(ylabel="Th-229 inventory (Ci)", title="Reduced-model comparison — not a matched reproduction")
    axes[0].legend()
    axes[1].plot(years, [100 * row["relative_residual"] for row in rows], "o-", color="tab:orange")
    axes[1].set(xlabel="Irradiation (Julian years)", ylabel="Difference (%)")
    for axis in axes:
        axis.grid(alpha=0.25)
    figure.savefig(destination, metadata={"Date": None})
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, default=ROOT / "target/release/cram_probe")
    parser.add_argument("--write", action="store_true", help="Create new receipt; never overwrite")
    args = parser.parse_args()
    work = ROOT / "target/preflight-tmp"
    work.mkdir(parents=True, exist_ok=True)
    fresh = run(args.probe.resolve(), work)
    verify(fresh)
    receipt_path = RECEIPT
    if args.write:
        with receipt_path.open("x") as stream:
            json.dump(fresh, stream, indent=2, allow_nan=False)
            stream.write("\n")
        (RESULTS / "comparison.md").write_text(table(fresh))
        plot(fresh, RESULTS / "comparison.svg")
    recorded = json.loads(receipt_path.read_text())
    check_recorded(recorded, fresh)
    if (RESULTS / "comparison.md").read_text() != table(recorded):
        raise ValueError("comparison table is stale")
    print("ACTINV reduced-chain numerical verification passed; publication NOT REPRODUCED.")
    print(table(fresh))


if __name__ == "__main__":
    main()
