#!/usr/bin/env python3
"""P9-G2: independent dense fission assembly, interpolation and leakage conservation."""
from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path

from p9_fixtures import (
    FISSION_PROBE,
    INDEPENDENT_TABLES,
    ROOT,
    command,
    make_fixture,
    relative,
    write_json,
)

RESULTS = Path(os.environ.get("ACTINV_P9_RESULTS", ROOT / "results"))
RATE_U235 = 2.0
RATE_U236 = 0.5
RATE_FE56 = 4.0


def independent_effective(energy: float) -> tuple[dict[str, float], dict]:
    low_energy, low_products = INDEPENDENT_TABLES[0]
    high_energy, high_products = INDEPENDENT_TABLES[1]
    low = {f"{za}_{state}": value for za, state, value, _ in low_products}
    high = {f"{za}_{state}": value for za, state, value, _ in high_products}
    if energy <= low_energy:
        return low, {
            "lower": low_energy,
            "upper": low_energy,
            "weight": 0.0,
            "clamped": energy < low_energy,
        }
    if energy >= high_energy:
        return high, {
            "lower": high_energy,
            "upper": high_energy,
            "weight": 0.0,
            "clamped": energy > high_energy,
        }
    weight = (energy - low_energy) / (high_energy - low_energy)
    products = {
        key: low.get(key, 0.0) * (1.0 - weight) + high.get(key, 0.0) * weight
        for key in set(low) | set(high)
    }
    return products, {
        "lower": low_energy,
        "upper": high_energy,
        "weight": weight,
        "clamped": False,
    }


def probe(fixture: dict[str, Path], yields: str | Path, energy: float) -> dict:
    return json.loads(
        command(
            [
                FISSION_PROBE,
                fixture["decay"],
                fixture["library"],
                fixture["index"],
                yields,
                repr(energy),
                "1e24",
            ]
        ).stdout
    )


def matrix_from_probe(output: dict) -> dict[tuple[str, str], float]:
    matrix: dict[tuple[str, str], float] = defaultdict(float)
    for entry in output["triplets"]:
        matrix[(entry["row"], entry["column"])] += entry["value_per_s"]
    return dict(matrix)


def expected_matrix(products: dict[str, float] | None) -> dict[tuple[str, str], float]:
    matrix = {
        ("92235_0", "92235_0"): -RATE_U235,
        ("92236_0", "92236_0"): -RATE_U236,
        ("LEAK", "92236_0"): RATE_U236,
        ("26056_0", "26056_0"): -RATE_FE56,
        ("25056_0", "26056_0"): RATE_FE56,
    }
    if products is None:
        matrix[("LEAK", "92235_0")] = RATE_U235
        return matrix
    for product, value in products.items():
        row = product if product != "54140_0" else "LEAK"
        matrix[(row, "92235_0")] = matrix.get((row, "92235_0"), 0.0) + value * RATE_U235
    return matrix


def compare_matrix(actual: dict, expected: dict) -> tuple[float, list[str]]:
    mismatches = []
    worst = 0.0
    if set(actual) != set(expected):
        mismatches.append(
            f"entry sets differ: extra={sorted(set(actual) - set(expected))}, "
            f"missing={sorted(set(expected) - set(actual))}"
        )
    for key in set(actual) & set(expected):
        deviation = relative(actual[key], expected[key])
        worst = max(worst, deviation)
        if deviation > 1.0e-12:
            mismatches.append(f"{key}: {actual[key]} != {expected[key]}")
    return worst, mismatches


def main() -> None:
    work = Path(os.environ.get("ACTINV_P9_WORK", tempfile.mkdtemp(prefix="actinv-p9-g2-"))) / "g2"
    fixture = make_fixture(work)
    rows = {}
    worst_matrix_relative = 0.0
    all_mismatches = []
    for label, energy in (("clamp_low", 0.0), ("exact_low", 1.0), ("interpolated", 2.0), ("exact_high", 3.0), ("clamp_high", 4.0)):
        effective, selection = independent_effective(energy)
        output = probe(fixture, fixture["yields"], energy)
        actual_matrix = matrix_from_probe(output)
        matrix_relative, mismatches = compare_matrix(actual_matrix, expected_matrix(effective))
        worst_matrix_relative = max(worst_matrix_relative, matrix_relative)
        all_mismatches.extend(f"{label}: {message}" for message in mismatches)
        rust_effective = output["effective"]
        effective_relative = max(
            [relative(rust_effective["products"].get(key, 0.0), value) for key, value in effective.items()]
            + [relative(rust_effective["sum"], sum(effective.values()))]
        )
        selection_exact = (
            rust_effective["lower_energy_eV"] == selection["lower"]
            and rust_effective["upper_energy_eV"] == selection["upper"]
            and relative(rust_effective["upper_weight"], selection["weight"]) <= 1.0e-15
            and rust_effective["clamped"] is selection["clamped"]
        )
        balance = output["ledger"]["fission_yield_balance"]["92235_0"]
        conservation_relative = relative(
            balance["mapped_yield_sum"] + balance["leakage_yield_sum"],
            balance["raw_yield_sum"],
        )
        source_conservation_relative = relative(
            RATE_U235 * (balance["mapped_yield_sum"] + balance["leakage_yield_sum"]),
            RATE_U235 * balance["raw_yield_sum"],
        )
        rows[label] = {
            "energy_eV": energy,
            "selection": selection,
            "effective_products": rust_effective["products"],
            "effective_relative": effective_relative,
            "selection_exact": selection_exact,
            "matrix_relative": matrix_relative,
            "conservation_relative": conservation_relative,
            "source_conservation_relative": source_conservation_relative,
        }

    midpoint = probe(fixture, fixture["yields"], 2.0)
    midpoint_changed_cumulative = probe(fixture, fixture["yields_cumulative_changed"], 2.0)
    cumulative_cannot_affect = midpoint == midpoint_changed_cumulative
    no_yields = probe(fixture, "-", 2.0)
    no_yields_matrix_relative, no_yields_mismatches = compare_matrix(
        matrix_from_probe(no_yields), expected_matrix(None)
    )
    all_mismatches.extend(f"no-yields: {message}" for message in no_yields_mismatches)

    missing_product = midpoint["ledger"]["fission_yield_products_to_leakage"]
    missing_parent = midpoint["ledger"]["fission_no_yields_to_leakage"]
    distinct_paths = (
        len(missing_product) == 1
        and missing_product[0]["parent"] == "92235_0"
        and missing_product[0]["product"] == "54140_0"
        and relative(missing_product[0]["yield_value"], 0.45) <= 1.0e-15
        and missing_parent == {"92236_0": 0.49999999999999994}
        and no_yields["ledger"]["fission_yield_products_to_leakage"] == []
        and set(no_yields["ledger"]["fission_no_yields_to_leakage"]) == {"92235_0", "92236_0"}
    )
    parent_loss_once = relative(
        matrix_from_probe(midpoint)[("92235_0", "92235_0")], -RATE_U235
    ) <= 1.0e-15
    output = {
        "fixture": {name: {"path": str(path)} for name, path in fixture.items()},
        "energies": rows,
        "worst_matrix_relative": worst_matrix_relative,
        "matrix_mismatches": all_mismatches,
        "parent_loss_once": parent_loss_once,
        "missing_product_and_parent_use_distinct_paths": distinct_paths,
        "cumulative_tables_cannot_affect_matrix_or_ledger": cumulative_cannot_affect,
        "no_yields_matrix_relative": no_yields_matrix_relative,
        "midpoint_balance": midpoint["ledger"]["fission_yield_balance"]["92235_0"],
    }
    output["pass"] = bool(
        not all_mismatches
        and worst_matrix_relative <= 1.0e-12
        and no_yields_matrix_relative <= 1.0e-12
        and all(row["effective_relative"] <= 1.0e-12 for row in rows.values())
        and all(row["selection_exact"] for row in rows.values())
        and all(row["conservation_relative"] <= 1.0e-12 for row in rows.values())
        and all(row["source_conservation_relative"] <= 1.0e-12 for row in rows.values())
        and parent_loss_once
        and distinct_paths
        and cumulative_cannot_affect
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g2_p9_fission_matrix.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
