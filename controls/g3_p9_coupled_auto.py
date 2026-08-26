#!/usr/bin/env python3
"""P9-G3: multiplier-aware burn-up, automatic threshold and coupled depletion."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from p9_fixtures import ROOT, base_spec, inventory, make_fixture, relative, run_spec, write_json

RESULTS = Path(os.environ.get("ACTINV_P9_RESULTS", ROOT / "results"))
INITIAL_ATOMS = 1.0e20
THRESHOLD = 1.0e-6


def duration_for_fraction(fraction: float, rate_per_s: float, multiplier: float = 1.0) -> str:
    optical_depth = -math.log1p(-fraction)
    return f"{optical_depth / (rate_per_s * multiplier):.17e} s"


def solve_u235(
    work: Path,
    fixture: dict[str, Path],
    name: str,
    fraction: float,
    *,
    multiplier: float = 1.0,
    mode: str = "auto",
) -> tuple[dict, float]:
    rate = 2.0
    duration = duration_for_fraction(fraction, rate, multiplier)
    specification = base_spec(
        fixture,
        composition={"U235": INITIAL_ATOMS},
        schedule=[{"dt": duration, "flux": multiplier}],
        mode=mode,
    )
    return run_spec(work, name, specification), -math.log1p(-fraction)


def main() -> None:
    work = Path(os.environ.get("ACTINV_P9_WORK", tempfile.mkdtemp(prefix="actinv-p9-g3-"))) / "g3"
    fixture = make_fixture(work)

    below_fraction = THRESHOLD * (1.0 - 1.0e-6)
    above_fraction = THRESHOLD * (1.0 + 1.0e-6)
    below, below_tau = solve_u235(work, fixture, "threshold-below", below_fraction)
    above, above_tau = solve_u235(work, fixture, "threshold-above", above_fraction)
    threshold_rows = {
        "below": {
            "target_fraction": below_fraction,
            "target_optical_depth": below_tau,
            "ledger_fraction": below["ledger"]["max_burnup_fraction"],
            "ledger_optical_depth": below["ledger"]["max_burnup_optical_depth"],
            "mode": below["mode"],
        },
        "above": {
            "target_fraction": above_fraction,
            "target_optical_depth": above_tau,
            "ledger_fraction": above["ledger"]["max_burnup_fraction"],
            "ledger_optical_depth": above["ledger"]["max_burnup_optical_depth"],
            "mode": above["mode"],
        },
    }
    threshold_worst_relative = max(
        relative(row["target_fraction"], row["ledger_fraction"])
        for row in threshold_rows.values()
    )
    threshold_worst_relative = max(
        threshold_worst_relative,
        *(relative(row["target_optical_depth"], row["ledger_optical_depth"])
          for row in threshold_rows.values()),
    )

    single_multiplier, _ = solve_u235(
        work, fixture, "multiplier-one", 0.75e-6, multiplier=1.0
    )
    multiplier_duration = duration_for_fraction(0.75e-6, 2.0, 1.0)
    doubled_spec = base_spec(
        fixture,
        composition={"U235": INITIAL_ATOMS},
        schedule=[{"dt": multiplier_duration, "flux": 2.0}],
        mode="auto",
    )
    doubled_multiplier = run_spec(work, "multiplier-two", doubled_spec)
    multiplier_changes_choice = (
        single_multiplier["mode"] == "trace"
        and doubled_multiplier["mode"] == "coupled"
        and single_multiplier["ledger"]["max_burnup_fraction"] < THRESHOLD
        and doubled_multiplier["ledger"]["max_burnup_fraction"] >= THRESHOLD
    )

    exposure = 2.0 * 0.25 + 3.0 * 1.5
    isotope_rows = {}
    worst_isotope_relative = 0.0
    for name, sigma in (("U235", 2.0), ("U236", 0.5), ("Fe56", 4.0)):
        total_flux = 1.0e18
        rate = sigma * total_flux * 1.0e-24
        optical_depth = rate * exposure
        fraction = -math.expm1(-optical_depth)
        specification = base_spec(
            fixture,
            composition={name: INITIAL_ATOMS},
            schedule=[
                {"dt": "2 s", "flux": 0.25},
                {"dt": "3 s", "flux": 1.5},
                {"dt": "7 s", "flux": 0.0},
            ],
            mode="auto",
            total_flux=total_flux,
        )
        result = run_spec(work, f"isotope-{name.lower()}", specification)
        optical_relative = relative(optical_depth, result["ledger"]["max_burnup_optical_depth"])
        fraction_relative = relative(fraction, result["ledger"]["max_burnup_fraction"])
        worst_isotope_relative = max(worst_isotope_relative, optical_relative, fraction_relative)
        isotope_rows[name] = {
            "loss_rate_per_s": rate,
            "multiplier_weighted_time_s": exposure,
            "optical_depth": optical_depth,
            "fraction": fraction,
            "ledger_optical_depth": result["ledger"]["max_burnup_optical_depth"],
            "ledger_fraction": result["ledger"]["max_burnup_fraction"],
            "ledger_nuclide": result["ledger"]["max_burnup_nuclide"],
            "mode": result["mode"],
        }

    forced_trace, _ = solve_u235(
        work, fixture, "forced-trace-above", above_fraction, mode="trace"
    )
    forced_coupled, _ = solve_u235(
        work, fixture, "forced-coupled-below", below_fraction, mode="coupled"
    )
    explicit_modes_honored = forced_trace["mode"] == "trace" and forced_coupled["mode"] == "coupled"

    depletion_tau = 0.2
    depletion_spec = base_spec(
        fixture,
        composition={"U235": INITIAL_ATOMS},
        schedule=[{"dt": f"{depletion_tau / 2.0:.17e} s", "flux": 1.0}],
        mode="coupled",
    )
    depletion = run_spec(work, "coupled-parent-depletion", depletion_spec)
    calculated_parent = inventory(depletion["steps"][-1])["U235"]
    exact_parent = INITIAL_ATOMS * math.exp(-depletion_tau)
    parent_depletion_relative = relative(calculated_parent, exact_parent)

    low_tau = 1.0e-8
    low_schedule = [{"dt": f"{low_tau / 2.0:.17e} s", "flux": 1.0}]
    trace_spec = base_spec(
        fixture,
        composition={"U235": INITIAL_ATOMS},
        schedule=low_schedule,
        mode="trace",
    )
    coupled_spec = base_spec(
        fixture,
        composition={"U235": INITIAL_ATOMS},
        schedule=low_schedule,
        mode="coupled",
    )
    trace = run_spec(work, "low-burn-trace", trace_spec)
    coupled = run_spec(work, "low-burn-coupled", coupled_spec)
    trace_inventory = inventory(trace["steps"][-1])
    coupled_inventory = inventory(coupled["steps"][-1])
    products = ["Kr92", "Sr100", "Ba141"]
    product_differences = {
        product: abs(trace_inventory[product] - coupled_inventory[product]) for product in products
    }
    coupled_floor = coupled["steps"][-1]["numerical_floor_atoms_per_g"]
    maximum_product_difference = max(product_differences.values())
    relative_product_difference = max(
        relative(trace_inventory[product], coupled_inventory[product]) for product in products
    )
    analytic_first_order = 1.0 - (-math.expm1(-low_tau)) / low_tau
    first_order_relative = relative(relative_product_difference, analytic_first_order)

    output = {
        "threshold": THRESHOLD,
        "threshold_cases": threshold_rows,
        "threshold_worst_relative": threshold_worst_relative,
        "multiplier_choice": {
            "single_mode": single_multiplier["mode"],
            "single_fraction": single_multiplier["ledger"]["max_burnup_fraction"],
            "double_mode": doubled_multiplier["mode"],
            "double_fraction": doubled_multiplier["ledger"]["max_burnup_fraction"],
            "changes_choice": multiplier_changes_choice,
        },
        "per_initial_isotope": isotope_rows,
        "per_initial_isotope_worst_relative": worst_isotope_relative,
        "explicit_modes_honored": explicit_modes_honored,
        "coupled_parent": {
            "optical_depth": depletion_tau,
            "calculated_atoms_per_g": calculated_parent,
            "exact_atoms_per_g": exact_parent,
            "relative": parent_depletion_relative,
        },
        "low_burnup_trace_vs_coupled": {
            "optical_depth": low_tau,
            "products": product_differences,
            "maximum_absolute_difference_atoms_per_g": maximum_product_difference,
            "coupled_numerical_floor_atoms_per_g": coupled_floor,
            "relative_product_difference": relative_product_difference,
            "analytic_first_order_relative_difference": analytic_first_order,
            "first_order_relative": first_order_relative,
        },
    }
    output["pass"] = bool(
        threshold_rows["below"]["mode"] == "trace"
        and threshold_rows["above"]["mode"] == "coupled"
        and threshold_worst_relative <= 1.0e-12
        and multiplier_changes_choice
        and worst_isotope_relative <= 1.0e-12
        and all(row["ledger_nuclide"] == name for name, row in isotope_rows.items())
        and explicit_modes_honored
        and parent_depletion_relative <= 1.0e-10
        and maximum_product_difference <= coupled_floor
        and first_order_relative <= 0.25
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g3_p9_coupled_auto.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
