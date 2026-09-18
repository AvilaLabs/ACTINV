#!/usr/bin/env python3
"""Check the sealed opening audit; success does not mean inventory reproduction."""

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "examples/fusion_medical_isotopes/ac225/reference.json"
PROTOCOL = ROOT / "protocols/FUSION-ISOTOPE-001.md"
RECEIPT = ROOT / "results/fusion-isotope-001/opening.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calculate(reference):
    p, c = reference["published"], reference["conventions"]
    for values in (p, c):
        for key, value in values.items():
            if key == "note":
                continue
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ValueError(f"non-numeric input: {key}")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid input: {key}")
            if value == 0 and key != "initial_th229_atoms":
                raise ValueError(f"non-positive input: {key}")
    if c["initial_th229_atoms"] != 0:
        raise ValueError("opening arithmetic requires zero initial product")
    if not 0 < p["target_facing_fraction"] <= 1:
        raise ValueError("invalid target-facing fraction")
    source = p["source_area_m2"] * 10000 * p["emitted_neutrons_per_cm2_s"]
    production = source * p["th229_atoms_per_emitted_neutron"]
    seconds = c["seconds_per_year"]
    decay = math.log(2) / (p["th229_half_life_years"] * seconds)
    gross = decay * production * seconds / c["bq_per_ci"]
    activity = -production * math.expm1(-decay * seconds) / c["bq_per_ci"]
    printed = p["th229_gross_activity_per_full_power_year_ci"]
    return {
        "emitted_source_neutrons_per_s": source,
        "th229_production_atoms_per_s": production,
        "th229_gross_atoms_one_year": production * seconds,
        "th229_gross_activity_one_year_ci": gross,
        "th229_activity_one_year_with_parent_decay_ci": activity,
        "published_gross_activity_one_year_ci": printed,
        "gross_activity_residual_ci": gross - printed,
        "gross_activity_relative_residual": (gross - printed) / printed,
    }


def opening(reference):
    missing = reference["missing_for_reproduction"]
    if not missing or any(not isinstance(item, str) or not item.strip() for item in missing):
        raise ValueError("opening audit requires explicit missing inputs")
    return {
        "benchmark": "fusion-isotope-001",
        "protocol_sha256": digest(PROTOCOL),
        "reference_sha256": digest(REFERENCE),
        "scope": "published_yield_arithmetic_only",
        "overall_status": "not_reproduced",
        "actinv_executed": False,
        "openmc_executed": False,
        "gates": {"G0": "source_audited", "G1": "arithmetic_reported",
                  "G2": "not_executed", "G3": "blocked_missing_inputs"},
        "arithmetic": calculate(reference),
        "missing_for_reproduction": missing,
        "residual_disposition": "unresolved; no normalization fitted; no agreement verdict",
    }


def compare(actual, expected, location="receipt"):
    """Tolerate floating-point roundoff only, never scientific discrepancies."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            raise ValueError(f"key mismatch: {location}")
        for key in expected:
            compare(actual[key], expected[key], f"{location}.{key}")
    elif isinstance(expected, float):
        if (isinstance(actual, bool) or not isinstance(actual, (int, float))
                or not math.isfinite(actual)
                or not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)):
            raise ValueError(f"numeric mismatch: {location}")
    elif type(actual) is not type(expected) or actual != expected:
        raise ValueError(f"value mismatch: {location}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Create, never replace, opening receipt")
    args = parser.parse_args()
    expected = opening(json.loads(REFERENCE.read_text()))
    if args.write:
        with RECEIPT.open("x", encoding="utf-8") as stream:
            json.dump(expected, stream, indent=2, allow_nan=False)
            stream.write("\n")
    compare(json.loads(RECEIPT.read_text()), expected)
    print("Opening audit and arithmetic verified; ACTINV reproduction NOT EXECUTED.")
    print(json.dumps(expected["arithmetic"], indent=2))


if __name__ == "__main__":
    main()
