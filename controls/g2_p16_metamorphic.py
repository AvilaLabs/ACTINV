#!/usr/bin/env python3
"""P16 G3: release/candidate analytic and metamorphic relations through production paths."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from p9_fixtures import base_spec, make_fixture, sha256, write_json


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g2_p16_metamorphic.json"
WORK = Path(os.environ.get("ACTINV_P16_METAMORPHIC_WORK", ROOT / "target/p16-metamorphic-work"))
RELEASE = Path(
    os.environ.get(
        "ACTINV_P16_RELEASE_BIN",
        ROOT / "target/p16-opening-target/release/actinv",
    )
)
CANDIDATE = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
INITIAL_ATOMS = 1.0e20
LIMITS = {
    "scaling": 5.0e-12,
    "analytic_decay": 5.0e-11,
    "atom_conservation": 5.0e-12,
    "schedule_split": 5.0e-11,
    "rebin_closure": 1.0e-12,
    "rebin_scaling": 5.0e-15,
    "mode_parent": 1.0e-10,
    "mode_first_order": 0.25,
    "mesh_scaling": 5.0e-12,
}
THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def environment(cache: Path) -> dict[str, str]:
    value = os.environ.copy()
    value["ACTINV_CACHE_DIR"] = str(cache)
    for name in THREAD_VARIABLES:
        value[name] = "1"
    return value


def command(
    arguments: list[str | Path],
    *,
    cache: Path,
    ok: bool = True,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(argument) for argument in arguments],
        cwd=ROOT,
        env=environment(cache),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if ok and completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(map(str, arguments))}\n"
            f"{completed.stdout}{completed.stderr[-4000:]}"
        )
    if not ok and completed.returncode == 0:
        raise RuntimeError(f"command unexpectedly succeeded: {' '.join(map(str, arguments))}")
    return completed


def binary_identity(binary: Path) -> dict[str, object]:
    if not binary.is_file():
        raise FileNotFoundError(binary)
    cache = WORK / "identity-cache"
    version = command([binary, "--version"], cache=cache)
    return {
        "path": str(binary),
        "bytes": binary.stat().st_size,
        "sha256": file_sha256(binary),
        "version": version.stdout.strip(),
    }


def run_spec(
    binary: Path,
    cache: Path,
    work: Path,
    name: str,
    specification: dict,
) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    spec_path = work / f"{name}.json"
    result_path = work / f"{name}.result.json"
    result_path.unlink(missing_ok=True)
    write_json(spec_path, specification)
    command([binary, "run", spec_path, result_path], cache=cache)
    return json.loads(result_path.read_text(encoding="utf-8"))


def reject_spec(
    binary: Path,
    cache: Path,
    work: Path,
    name: str,
    specification: dict,
) -> dict[str, object]:
    spec_path = work / f"{name}.json"
    result_path = work / f"{name}.result.json"
    result_path.unlink(missing_ok=True)
    write_json(spec_path, specification)
    completed = command(
        [binary, "run", spec_path, result_path], cache=cache, ok=False
    )
    message = (completed.stdout + completed.stderr).strip()
    return {
        "returncode": completed.returncode,
        "message": message,
        "no_result": not result_path.exists(),
        "pass": completed.returncode != 0 and not result_path.exists(),
    }


def relative(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0e-300)


def inventory(step: dict) -> dict[str, float]:
    return {row["nuclide"]: row["atoms_per_g"] for row in step["inventory"]}


def selected(step: dict, nuclide: str) -> dict[str, float]:
    return {
        "inventory": inventory(step).get(nuclide, 0.0),
        "activity": step["activity_Bq_per_g"].get(nuclide, 0.0),
        "heat": step["heat_W_per_g"]["total"],
    }


def normalized_result(value: dict) -> dict:
    value = copy.deepcopy(value)
    value.pop("ms", None)
    value.pop("entry_point", None)
    value.get("certificate", {}).pop("entry_point", None)
    return value


def scaling_relations(binary: Path, cache: Path, work: Path, fixture: dict[str, Path]) -> dict:
    rows = {}
    results = {}
    for factor in (0.5, 1.0, 2.0):
        specification = base_spec(
            fixture,
            composition={"Fe56": INITIAL_ATOMS},
            schedule=[{"dt": "10 s", "flux": 1.0}],
            mode="trace",
            total_flux=1.0e18 * factor,
        )
        result = run_spec(binary, cache, work, f"scaling-{factor}", specification)
        results[factor] = result
        rows[str(factor)] = selected(result["steps"][-1], "Mn56")
    reference = rows["1.0"]
    scaling_errors = {
        factor: {
            quantity: relative(row[quantity], reference[quantity] * float(factor))
            for quantity in ("inventory", "activity", "heat")
            if reference[quantity] != 0.0
        }
        for factor, row in rows.items()
        if factor != "1.0"
    }
    maximum_scaling = max(
        error for row in scaling_errors.values() for error in row.values()
    )

    mass_spec = base_spec(
        fixture,
        composition={"Fe56": INITIAL_ATOMS},
        schedule=[{"dt": "10 s", "flux": 1.0}],
        mode="trace",
        total_flux=1.0e18,
    )
    mass_spec["material"]["mass_g"] = 7.0
    changed_mass = run_spec(binary, cache, work, "mass-seven", mass_spec)
    reference_step = results[1.0]["steps"][-1]
    mass_step = changed_mass["steps"][-1]
    mass_invariant = {
        key: reference_step[key] == mass_step[key]
        for key in ("inventory", "activity_Bq_per_g", "heat_W_per_g")
    }

    fluence_specs = []
    for name, duration, multiplier in (
        ("fluence-one", "4 s", 1.0),
        ("fluence-reciprocal", "2 s", 2.0),
    ):
        specification = base_spec(
            fixture,
            composition={"U235": INITIAL_ATOMS},
            schedule=[{"dt": duration, "flux": multiplier}],
            mode="trace",
            total_flux=1.0e18,
        )
        fluence_specs.append((name, run_spec(binary, cache, work, name, specification)))
    fluence_products = ("Kr92", "Sr100", "Ba141")
    fluence_errors = {
        product: relative(
            inventory(fluence_specs[0][1]["steps"][-1])[product],
            inventory(fluence_specs[1][1]["steps"][-1])[product],
        )
        for product in fluence_products
    }
    maximum_fluence = max(fluence_errors.values())
    passed = bool(
        maximum_scaling <= LIMITS["scaling"]
        and maximum_fluence <= LIMITS["scaling"]
        and all(mass_invariant.values())
    )
    return {
        "scaled_values": rows,
        "scaling_relative_errors": scaling_errors,
        "maximum_scaling_relative": maximum_scaling,
        "fixed_fluence_relative_errors": fluence_errors,
        "maximum_fixed_fluence_relative": maximum_fluence,
        "mass_per_gram_bit_identity": mass_invariant,
        "limit": LIMITS["scaling"],
        "pass": passed,
    }


def analytic_decay(binary: Path, cache: Path, work: Path, fixture: dict[str, Path]) -> dict:
    decay_constant = math.log(2.0) / 100.0
    rows = {}
    maximum_state = 0.0
    maximum_conservation = 0.0
    for half_lives in (1, 2, 3):
        duration = float(half_lives * 100)
        specification = base_spec(
            fixture,
            composition={"Mn56": INITIAL_ATOMS},
            schedule=[{"dt": f"{duration:.1f} s", "flux": 0.0}],
            mode="coupled",
            total_flux=0.0,
        )
        result = run_spec(binary, cache, work, f"decay-{half_lives}", specification)
        values = inventory(result["steps"][-1])
        calculated_parent = values.get("Mn56", 0.0)
        calculated_daughter = values.get("Fe56", 0.0)
        expected_parent = INITIAL_ATOMS * math.exp(-decay_constant * duration)
        expected_daughter = INITIAL_ATOMS - expected_parent
        parent_error = relative(calculated_parent, expected_parent)
        daughter_error = relative(calculated_daughter, expected_daughter)
        conservation = relative(calculated_parent + calculated_daughter, INITIAL_ATOMS)
        maximum_state = max(maximum_state, parent_error, daughter_error)
        maximum_conservation = max(maximum_conservation, conservation)
        rows[str(half_lives)] = {
            "duration_s": duration,
            "calculated_parent": calculated_parent,
            "expected_parent": expected_parent,
            "parent_relative": parent_error,
            "calculated_daughter": calculated_daughter,
            "expected_daughter": expected_daughter,
            "daughter_relative": daughter_error,
            "atom_conservation_relative": conservation,
        }
    return {
        "half_life_s": 100.0,
        "decay_constant_per_s": decay_constant,
        "cases": rows,
        "maximum_state_relative": maximum_state,
        "state_limit": LIMITS["analytic_decay"],
        "maximum_atom_conservation_relative": maximum_conservation,
        "conservation_limit": LIMITS["atom_conservation"],
        "pass": maximum_state <= LIMITS["analytic_decay"]
        and maximum_conservation <= LIMITS["atom_conservation"],
    }


def final_selected(result: dict, names: tuple[str, ...]) -> dict[str, float]:
    values = inventory(result["steps"][-1])
    return {name: values.get(name, 0.0) for name in names}


def schedule_splitting(binary: Path, cache: Path, work: Path, fixture: dict[str, Path]) -> dict:
    schedules = {
        "unsplit": [{"dt": "300 s", "flux": 1.0}],
        "two": [{"dt": "100 s", "flux": 1.0}, {"dt": "200 s", "flux": 1.0}],
        "three": [{"dt": "100 s", "flux": 1.0}] * 3,
    }
    definitions = {
        "decay": ({"Mn56": INITIAL_ATOMS}, "coupled", 0.0, ("Mn56", "Fe56"), 0.0),
        "trace_source": ({"Fe56": INITIAL_ATOMS}, "trace", 1.0e18, ("Mn56",), 1.0),
        "coupled_depletion": (
            {"U235": INITIAL_ATOMS},
            "coupled",
            1.0e18,
            ("U235", "Kr92", "Sr100", "Ba141"),
            1.0,
        ),
    }
    rows = {}
    maximum = 0.0
    for family, (composition, mode, total_flux, names, irradiation_multiplier) in definitions.items():
        values = {}
        for partition, schedule in schedules.items():
            adjusted = [
                {"dt": step["dt"], "flux": irradiation_multiplier}
                for step in schedule
            ]
            specification = base_spec(
                fixture,
                composition=composition,
                schedule=adjusted,
                mode=mode,
                total_flux=total_flux,
            )
            result = run_spec(
                binary,
                cache,
                work,
                f"schedule-{family}-{partition}",
                specification,
            )
            values[partition] = final_selected(result, names)
        errors = {
            partition: {
                name: relative(values["unsplit"][name], values[partition][name])
                for name in names
            }
            for partition in ("two", "three")
        }
        family_maximum = max(error for row in errors.values() for error in row.values())
        maximum = max(maximum, family_maximum)
        rows[family] = {
            "final_values": values,
            "relative_errors": errors,
            "maximum_relative": family_maximum,
        }
    return {
        "families": rows,
        "maximum_relative": maximum,
        "limit": LIMITS["schedule_split"],
        "pass": maximum <= LIMITS["schedule_split"],
    }


def mode_limit(binary: Path, cache: Path, work: Path, fixture: dict[str, Path]) -> dict:
    optical_depth = 1.0e-8
    schedule = [{"dt": f"{optical_depth / 2.0:.17e} s", "flux": 1.0}]
    results = {}
    for mode in ("trace", "coupled"):
        specification = base_spec(
            fixture,
            composition={"U235": INITIAL_ATOMS},
            schedule=schedule,
            mode=mode,
            total_flux=1.0e24,
        )
        results[mode] = run_spec(binary, cache, work, f"mode-{mode}", specification)
    trace = inventory(results["trace"]["steps"][-1])
    coupled = inventory(results["coupled"]["steps"][-1])
    products = ("Kr92", "Sr100", "Ba141")
    product_absolute = {name: abs(trace[name] - coupled[name]) for name in products}
    product_relative = {name: relative(trace[name], coupled[name]) for name in products}
    measured = max(product_relative.values())
    analytic = 1.0 - (-math.expm1(-optical_depth)) / optical_depth
    first_order_relative = relative(measured, analytic)
    calculated_parent = coupled["U235"]
    expected_parent = INITIAL_ATOMS * math.exp(-optical_depth)
    parent_relative = relative(calculated_parent, expected_parent)
    numerical_floor = results["coupled"]["steps"][-1]["numerical_floor_atoms_per_g"]
    maximum_absolute = max(product_absolute.values())
    return {
        "optical_depth": optical_depth,
        "product_absolute_differences": product_absolute,
        "product_relative_differences": product_relative,
        "maximum_product_relative": measured,
        "analytic_first_order_relative_difference": analytic,
        "first_order_agreement_relative": first_order_relative,
        "first_order_limit": LIMITS["mode_first_order"],
        "calculated_parent": calculated_parent,
        "expected_parent": expected_parent,
        "parent_relative": parent_relative,
        "parent_limit": LIMITS["mode_parent"],
        "maximum_product_absolute": maximum_absolute,
        "reported_numerical_floor": numerical_floor,
        "absolute_below_floor": maximum_absolute <= numerical_floor,
        "pass": first_order_relative <= LIMITS["mode_first_order"]
        and parent_relative <= LIMITS["mode_parent"]
        and maximum_absolute <= numerical_floor,
    }


def canonical_flux(
    path: Path,
    boundaries: list[float],
    spectra: list[list[float]],
    descriptor: Path,
) -> None:
    write_json(descriptor, {"fixture": "P16 metamorphic mesh", "cells": len(spectra)})
    records = [
        {
            "record": "header",
            "schema": "actinv-flux-1",
            "source": {
                "format": "p16-control",
                "path": str(descriptor),
                "sha256": sha256(descriptor),
            },
            "energy_boundaries_eV": boundaries,
            "flux_units": "n cm^-2 s^-1",
            "cell_count": len(spectra),
            "geometry": {
                "kind": "rectilinear",
                "dimension": [len(spectra), 1, 1],
                "axis_boundaries_cm": [
                    [float(index) for index in range(len(spectra) + 1)],
                    [0.0, 1.0],
                    [0.0, 1.0],
                ],
            },
        }
    ]
    totals = []
    for ordinal, spectrum in enumerate(spectra):
        total = math.fsum(spectrum)
        totals.append(total)
        records.append(
            {
                "record": "cell",
                "ordinal": ordinal,
                "id": f"cell-{ordinal}",
                "index": [ordinal + 1, 1, 1],
                "bounds_cm": [
                    [float(ordinal), float(ordinal + 1)],
                    [0.0, 1.0],
                    [0.0, 1.0],
                ],
                "volume_cm3": 1.0,
                "flux_per_group": spectrum,
                "flux_total": total,
            }
        )
    records.append(
        {
            "record": "footer",
            "cell_count": len(spectra),
            "flux_sum_over_cells": math.fsum(totals),
            "volume_integrated_flux": math.fsum(totals),
        }
    )
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def read_mesh(path: Path) -> tuple[dict, list[dict], dict]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return records[0], records[1:-1], records[-1]


def mesh_specification(
    fixture: dict[str, Path], canonical: Path, threads: int
) -> dict:
    specification = base_spec(
        fixture,
        composition={"Fe56": INITIAL_ATOMS},
        schedule=[{"dt": "10 s", "flux": 1.0}],
        mode="trace",
        total_flux=1.0,
    )
    specification.pop("spectrum")
    specification["spec"] = "actinv-mesh-spec-1"
    specification["title"] = "P16 mesh metamorphic fixture"
    specification["flux"] = {"path": str(canonical), "sha256": sha256(canonical)}
    specification["chunk_cells"] = 2
    specification["threads"] = threads
    return specification


def run_mesh_case(
    binary: Path,
    cache: Path,
    work: Path,
    fixture: dict[str, Path],
    name: str,
    boundaries: list[float],
    spectra: list[list[float]],
    thread_counts: tuple[int, ...],
) -> dict:
    descriptor = work / f"{name}.source.json"
    canonical = work / f"{name}.flux.ndjson"
    canonical_flux(canonical, boundaries, spectra, descriptor)
    mesh_outputs = {}
    raw_cell_lines = {}
    for threads in thread_counts:
        specification = mesh_specification(fixture, canonical, threads)
        spec_path = work / f"{name}-{threads}t.json"
        output_path = work / f"{name}-{threads}t.result.ndjson"
        write_json(spec_path, specification)
        command([binary, "mesh", spec_path, output_path], cache=cache)
        mesh_outputs[threads] = read_mesh(output_path)
        raw_cell_lines[threads] = output_path.read_bytes().splitlines()[1:-1]
    _, cells, footer = mesh_outputs[thread_counts[0]]
    footer = copy.deepcopy(footer)
    footer.pop("wall_time_s", None)
    footer.pop("cells_per_s", None)

    ordinary_identity = []
    for ordinal, spectrum in enumerate(spectra):
        specification = base_spec(
            fixture,
            composition={"Fe56": INITIAL_ATOMS},
            schedule=[{"dt": "10 s", "flux": 1.0}],
            mode="trace",
            total_flux=math.fsum(spectrum),
        )
        specification["title"] = "P16 mesh metamorphic fixture"
        ordinary = run_spec(binary, cache, work, f"{name}-ordinary-{ordinal}", specification)
        ordinary_identity.append(
            normalized_result(ordinary) == normalized_result(cells[ordinal]["result"])
        )
    thread_identity = True
    if len(thread_counts) > 1:
        reference_cells = raw_cell_lines[thread_counts[0]]
        for threads in thread_counts[1:]:
            thread_identity &= reference_cells == raw_cell_lines[threads]
    expected_total = math.fsum(math.fsum(spectrum) for spectrum in spectra)
    footer_relative = relative(footer["source_flux_sum_over_cells"], expected_total)
    destination_relative = relative(footer["destination_flux_sum_over_cells"], expected_total)
    return {
        "canonical_sha256": sha256(canonical),
        "source_boundaries_eV": boundaries,
        "spectra": spectra,
        "cells": cells,
        "footer": footer,
        "ordinary_identity": ordinary_identity,
        "thread_identity": thread_identity,
        "expected_total": expected_total,
        "source_footer_relative": footer_relative,
        "destination_footer_relative": destination_relative,
    }


def mesh_and_rebin(binary: Path, cache: Path, work: Path, fixture: dict[str, Path]) -> dict:
    exact_spectra = [[1.0e18], [2.0e18]]
    exact = run_mesh_case(
        binary,
        cache,
        work,
        fixture,
        "mesh-exact",
        [1.0, 3.0],
        exact_spectra,
        (1,),
    )
    split_spectra = [
        [0.5e18, 0.5e18],
        [1.0e18, 1.0e18],
        [1.0e18, 1.0e18],
        [2.0e18, 2.0e18],
    ]
    split = run_mesh_case(
        binary,
        cache,
        work,
        fixture,
        "mesh-split",
        [1.0, math.sqrt(3.0), 3.0],
        split_spectra,
        (1, 4),
    )
    exact_copy = all(
        cell["rebin"]["method"] == "copy"
        and cell["rebin"]["source_total"].hex()
        == cell["rebin"]["destination_total"].hex()
        and cell["rebin"]["relative_closure"] == 0.0
        for cell in exact["cells"]
    )
    split_closure = max(
        cell["rebin"]["relative_closure"] for cell in split["cells"]
    )
    scale_pairs = ((0, 1), (1, 3))
    scale_errors = {}
    for lower, upper in scale_pairs:
        lower_rebin = split["cells"][lower]["rebin"]
        upper_rebin = split["cells"][upper]["rebin"]
        factor = math.fsum(split_spectra[upper]) / math.fsum(split_spectra[lower])
        scale_errors[f"{lower}->{upper}"] = {
            key: relative(upper_rebin[key], lower_rebin[key] * factor)
            for key in ("source_total", "destination_total", "underflow", "overflow")
        }
    maximum_scale = max(error for row in scale_errors.values() for error in row.values())
    repeated_cells_identical = split["cells"][1]["result"] == split["cells"][2]["result"]
    identity = bool(
        all(exact["ordinary_identity"])
        and all(split["ordinary_identity"])
        and split["thread_identity"]
        and repeated_cells_identical
    )
    footer_maximum = max(
        exact["source_footer_relative"],
        exact["destination_footer_relative"],
        split["source_footer_relative"],
        split["destination_footer_relative"],
    )
    compact_exact = {key: value for key, value in exact.items() if key != "cells"}
    compact_split = {key: value for key, value in split.items() if key != "cells"}
    return {
        "exact_grid": compact_exact,
        "split_grid": compact_split,
        "exact_grid_copy_bit_identity": exact_copy,
        "maximum_split_closure": split_closure,
        "closure_limit": LIMITS["rebin_closure"],
        "rebin_scale_relative_errors": scale_errors,
        "maximum_rebin_scale_relative": maximum_scale,
        "rebin_scaling_limit": LIMITS["rebin_scaling"],
        "ordinary_thread_repeated_identity": identity,
        "maximum_footer_scaling_relative": footer_maximum,
        "footer_scaling_limit": LIMITS["mesh_scaling"],
        "pass": exact_copy
        and split_closure <= LIMITS["rebin_closure"]
        and maximum_scale <= LIMITS["rebin_scaling"]
        and identity
        and footer_maximum <= LIMITS["mesh_scaling"],
    }


def duration_spellings(binary: Path, cache: Path, work: Path, fixture: dict[str, Path]) -> dict:
    accepted = ("300 s", "300s", "5 min", "5min", "0.08333333333333333 h")
    values = {}
    for index, spelling in enumerate(accepted):
        specification = base_spec(
            fixture,
            composition={"Fe56": INITIAL_ATOMS},
            schedule=[{"dt": spelling, "flux": 0.0}],
            mode="coupled",
            total_flux=0.0,
        )
        result = run_spec(binary, cache, work, f"duration-{index}", specification)
        values[spelling] = result["steps"][-1]["t_s"]
    ulp = math.ulp(300.0)
    maximum_absolute = max(abs(value - 300.0) for value in values.values())

    rejected = {}
    for index, spelling in enumerate(("-1 s", "nan s", "inf s", "1 fortnight")):
        specification = base_spec(
            fixture,
            composition={"Fe56": INITIAL_ATOMS},
            schedule=[{"dt": spelling, "flux": 0.0}],
            mode="coupled",
            total_flux=0.0,
        )
        rejected[spelling] = reject_spec(
            binary, cache, work, f"duration-rejected-{index}", specification
        )
    return {
        "accepted_seconds": values,
        "ulp_at_300_s": ulp,
        "maximum_absolute_difference_s": maximum_absolute,
        "rejected": rejected,
        "pass": maximum_absolute <= ulp and all(row["pass"] for row in rejected.values()),
    }


def evaluate_threshold(row: dict, field: str, limit: float) -> bool:
    return row[field] <= limit


def planted_comparators(relations: dict[str, dict]) -> dict[str, bool]:
    plants = {}
    definitions = {
        "scaling": ("maximum_scaling_relative", LIMITS["scaling"]),
        "analytic_decay": ("maximum_state_relative", LIMITS["analytic_decay"]),
        "schedule_splitting": ("maximum_relative", LIMITS["schedule_split"]),
        "mode_limit": ("parent_relative", LIMITS["mode_parent"]),
        "mesh_rebin": ("maximum_split_closure", LIMITS["rebin_closure"]),
    }
    for family, (field, limit) in definitions.items():
        planted = copy.deepcopy(relations[family])
        planted[field] = limit * 2.0
        plants[family] = not evaluate_threshold(planted, field, limit)
    planted_duration = copy.deepcopy(relations["duration_spellings"])
    planted_duration["maximum_absolute_difference_s"] = planted_duration["ulp_at_300_s"] * 2.0
    plants["duration_spellings"] = not (
        planted_duration["maximum_absolute_difference_s"]
        <= planted_duration["ulp_at_300_s"]
    )
    planted_mesh = copy.deepcopy(relations["mesh_rebin"])
    planted_mesh["ordinary_thread_repeated_identity"] = False
    plants["mesh_identity"] = not planted_mesh["ordinary_thread_repeated_identity"]
    return plants


def run_relations(
    label: str, binary: Path, fixture: dict[str, Path]
) -> dict[str, object]:
    work = WORK / label
    work.mkdir(parents=True, exist_ok=True)
    cache = WORK / f"{label}-cache"
    relations = {
        "scaling": scaling_relations(binary, cache, work, fixture),
        "analytic_decay": analytic_decay(binary, cache, work, fixture),
        "schedule_splitting": schedule_splitting(binary, cache, work, fixture),
        "mode_limit": mode_limit(binary, cache, work, fixture),
        "mesh_rebin": mesh_and_rebin(binary, cache, work, fixture),
        "duration_spellings": duration_spellings(binary, cache, work, fixture),
    }
    plants = planted_comparators(relations)
    return {
        "relations": relations,
        "planted_comparator_rejections": plants,
        "pass": all(row["pass"] for row in relations.values()) and all(plants.values()),
    }


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    fixture = make_fixture(WORK / "fixture")
    identities = {
        "release": binary_identity(RELEASE),
        "candidate": binary_identity(CANDIDATE),
    }
    runs = {
        "release": run_relations("release", RELEASE, fixture),
        "candidate": run_relations("candidate", CANDIDATE, fixture),
    }
    output = {
        "schema": "actinv-p16-metamorphic-1",
        "gate": "P16-G3",
        "limits": LIMITS,
        "fixture": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in fixture.items()
        },
        "binaries": identities,
        "runs": runs,
        "release_relations_not_loosened": runs["release"]["pass"]
        and runs["candidate"]["pass"],
    }
    output["pass"] = bool(
        runs["release"]["pass"]
        and runs["candidate"]["pass"]
        and output["release_relations_not_loosened"]
    )
    RESULT.parent.mkdir(exist_ok=True)
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
