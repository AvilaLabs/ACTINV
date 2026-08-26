#!/usr/bin/env python3
"""P9-G4: pulse boundaries against dense exponentials and OpenMC CRAM48."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np
import openmc
from openmc.deplete.cram import CRAM48
from scipy.linalg import expm
from scipy.sparse import csr_matrix

from p9_fixtures import ROOT, base_spec, inventory, make_fixture, relative, run_spec, write_json

RESULTS = Path(os.environ.get("ACTINV_P9_RESULTS", ROOT / "results"))
INITIAL = np.asarray([1.0e20, 0.0])  # Fe-56, Mn-56
TOTAL_FLUX = 1.0e22
BASE_RATE = 4.0 * TOTAL_FLUX * 1.0e-24
DECAY = math.log(2.0) / 100.0


def matrix(multiplier: float) -> np.ndarray:
    rate = BASE_RATE * multiplier
    return np.asarray([[-rate, DECAY], [rate, -DECAY]], dtype=float)


def references(schedule: list[tuple[float, float]]) -> tuple[list[np.ndarray], list[np.ndarray]]:
    dense = INITIAL.copy()
    cram48 = INITIAL.copy()
    dense_rows, cram_rows = [], []
    for duration, multiplier in schedule:
        operator = matrix(multiplier)
        dense = expm(operator * duration) @ dense
        cram48 = CRAM48(csr_matrix(operator), cram48, duration)
        dense_rows.append(dense.copy())
        cram_rows.append(cram48.copy())
    return dense_rows, cram_rows


def specification(fixture: dict[str, Path], schedule: list[tuple[float, float]]) -> dict:
    return base_spec(
        fixture,
        composition={"Fe56": float(INITIAL[0])},
        schedule=[{"dt": f"{duration:.17e} s", "flux": multiplier} for duration, multiplier in schedule],
        mode="coupled",
        total_flux=TOTAL_FLUX,
    )


def vector(step: dict) -> np.ndarray:
    values = inventory(step)
    return np.asarray([values.get("Fe56", 0.0), values.get("Mn56", 0.0)])


def comparison(calculated: np.ndarray, reference: np.ndarray, floor: float) -> dict:
    absolute = np.abs(calculated - reference)
    resolvable = np.abs(reference) > floor
    relative_values = absolute[resolvable] / np.abs(reference[resolvable])
    return {
        "maximum_absolute_atoms_per_g": float(np.max(absolute)),
        "maximum_resolvable_relative": float(np.max(relative_values)) if relative_values.size else 0.0,
        "within_relative_or_floor": bool(
            np.all(absolute <= np.maximum(1.0e-8 * np.abs(reference), floor))
        ),
    }


def final_vector(result: dict) -> np.ndarray:
    return vector(result["steps"][-1])


def main() -> None:
    work = Path(os.environ.get("ACTINV_P9_WORK", tempfile.mkdtemp(prefix="actinv-p9-g4-"))) / "g4"
    fixture = make_fixture(work)
    schedule = [(3.0, 1.0), (5.0, 0.0), (2.0, 0.5), (7.0, 0.0), (1.0, 2.0)]
    result = run_spec(work, "boundaries", specification(fixture, schedule))
    dense, openmc_rows = references(schedule)
    boundary_rows = []
    time = exposure = 0.0
    worst_dense = worst_openmc = 0.0
    exposure_time_worst = 0.0
    for step, dense_reference, openmc_reference, (duration, multiplier) in zip(
        result["steps"], dense, openmc_rows, schedule
    ):
        time += duration
        exposure += duration * multiplier
        calculated = vector(step)
        floor = step["numerical_floor_atoms_per_g"]
        dense_comparison = comparison(calculated, dense_reference, floor)
        openmc_comparison = comparison(calculated, openmc_reference, floor)
        worst_dense = max(worst_dense, dense_comparison["maximum_resolvable_relative"])
        worst_openmc = max(worst_openmc, openmc_comparison["maximum_resolvable_relative"])
        expected_fluence = TOTAL_FLUX * exposure
        exposure_time_worst = max(
            exposure_time_worst,
            relative(step["t_s"], time),
            relative(step["flux_weighted_time_s"], exposure),
            relative(step["fluence_n_cm2"], expected_fluence),
        )
        boundary_rows.append(
            {
                "step": step["step"],
                "duration_s": duration,
                "multiplier": multiplier,
                "time_s": step["t_s"],
                "flux_weighted_time_s": step["flux_weighted_time_s"],
                "fluence_n_cm2": step["fluence_n_cm2"],
                "actinv": calculated.tolist(),
                "dense": dense_reference.tolist(),
                "openmc_cram48": openmc_reference.tolist(),
                "dense_comparison": dense_comparison,
                "openmc_comparison": openmc_comparison,
            }
        )

    split_schedule = [(2.0, 1.0), (3.0, 1.0), (4.0, 0.0)]
    merged_schedule = [(5.0, 1.0), (4.0, 0.0)]
    split = run_spec(work, "split", specification(fixture, split_schedule))
    merged = run_spec(work, "merged", specification(fixture, merged_schedule))
    split_vector, merged_vector = final_vector(split), final_vector(merged)
    split_merged_relative = float(
        np.max(np.abs(split_vector - merged_vector) / np.maximum(np.abs(merged_vector), 1.0e-300))
    )

    gapped_schedule = [(1.0, 1.0), (10.0, 0.0), (1.0, 1.0)]
    averaged_schedule = [(12.0, 1.0 / 6.0)]
    gapped = run_spec(work, "gapped", specification(fixture, gapped_schedule))
    averaged = run_spec(work, "averaged", specification(fixture, averaged_schedule))
    gapped_dense = references(gapped_schedule)[0][-1]
    averaged_dense = references(averaged_schedule)[0][-1]
    gapped_vector, averaged_vector = final_vector(gapped), final_vector(averaged)
    predicted_mn_difference = float(gapped_dense[1] - averaged_dense[1])
    calculated_mn_difference = float(gapped_vector[1] - averaged_vector[1])
    gap_difference_relative = relative(calculated_mn_difference, predicted_mn_difference)
    gapped_match = comparison(
        gapped_vector, gapped_dense, gapped["steps"][-1]["numerical_floor_atoms_per_g"]
    )
    averaged_match = comparison(
        averaged_vector, averaged_dense, averaged["steps"][-1]["numerical_floor_atoms_per_g"]
    )

    output = {
        "model": {
            "state_order": ["Fe56", "Mn56"],
            "initial_atoms_per_g": INITIAL.tolist(),
            "base_reaction_rate_per_s": BASE_RATE,
            "mn56_decay_constant_per_s": DECAY,
            "openmc_version": openmc.__version__,
            "openmc_solver": "openmc.deplete.cram.CRAM48",
        },
        "boundaries": boundary_rows,
        "worst_actinv_vs_dense_resolvable_relative": worst_dense,
        "worst_actinv_vs_openmc_cram48_resolvable_relative": worst_openmc,
        "time_exposure_fluence_worst_relative": exposure_time_worst,
        "split_merged": {
            "split_final": split_vector.tolist(),
            "merged_final": merged_vector.tolist(),
            "maximum_relative": split_merged_relative,
        },
        "gaps_vs_average": {
            "same_time_s": 12.0,
            "same_flux_weighted_time_s": 2.0,
            "gapped_final": gapped_vector.tolist(),
            "averaged_final": averaged_vector.tolist(),
            "predicted_mn_difference_atoms_per_g": predicted_mn_difference,
            "calculated_mn_difference_atoms_per_g": calculated_mn_difference,
            "difference_relative": gap_difference_relative,
            "relative_effect": abs(calculated_mn_difference) / averaged_vector[1],
            "gapped_dense_comparison": gapped_match,
            "averaged_dense_comparison": averaged_match,
        },
    }
    output["pass"] = bool(
        all(row["dense_comparison"]["within_relative_or_floor"] for row in boundary_rows)
        and all(row["openmc_comparison"]["within_relative_or_floor"] for row in boundary_rows)
        and worst_dense <= 1.0e-8
        and worst_openmc <= 1.0e-8
        and exposure_time_worst <= 1.0e-12
        and split_merged_relative <= 1.0e-10
        and gapped_match["within_relative_or_floor"]
        and averaged_match["within_relative_or_floor"]
        and gap_difference_relative <= 1.0e-8
        and output["gaps_vs_average"]["relative_effect"] > 1.0e-6
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g4_p9_pulses_openmc.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
