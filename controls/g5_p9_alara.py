#!/usr/bin/env python3
"""P9-G5: identical-data Fe-56(n,p)Mn-56 pulse comparison with ALARA 2.9.2.

ALARA converts the official EAF/FENDL text itself.  ACTINV consumes an NPZ made from
the same reaction card and the same FENDL decay evaluation.  The EAF arrays are
high-to-low energy; both the cross section and flux are reversed at ACTINV's
ascending-energy interface, so the independently calculated dot product is unchanged.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
from scipy.linalg import expm

from p9_fixtures import BIN, ROOT, command, inventory, relative, sha256, write_json


RESULTS = Path(os.environ.get("ACTINV_P9_RESULTS", ROOT / "results"))
ALARA_SOURCE = Path(
    os.environ.get("ACTINV_ALARA_SOURCE", Path.home() / "nuclear-data" / "alara-2.9.2")
)
ALARA_BUILD = Path(
    os.environ.get("ACTINV_ALARA_BUILD", Path.home() / "nuclear-data" / "alara-2.9.2-build")
)
ALARA_BIN = Path(os.environ.get("ACTINV_ALARA_BIN", ALARA_BUILD / "src" / "alara"))
ENDF_DECAY_FALLBACK = Path(
    os.environ.get(
        "ACTINV_ENDF_DECAY",
        Path.home() / "nuclear-data" / "endfb-viii.0-decay" / "bulk" / "endf-b-viii-0_decay.dat",
    )
)

EXPECTED = {
    "alara_commit": "faa5b330460fe865e38fc788f1b792ea33d13d1b",
    "sample1_sha256": "f5eced7f053d74c31d59ddb9314ea92b160b0f55f2c81c51ffe4a5a2a9f90f65",
    "sample3_sha256": "ba12dc9dcf05952eafd3490273dc954eeb207b1df76bd2fcd1d8097402dd9787",
    "activation_sha256": "f45ced4d5676c993f6b6dd562d5e312e897eabb959dc6ebba56bbeaecde22312",
    "decay_sha256": "810f3b8ca46dd55b965e37b84c9793057a7ee53aa2a194a2fcb1ff0d1b681940",
    "element_sha256": "bdfcfdb255d89b4988be9fab4279c36fb9615709ee6a738e963591db6146c290",
    "fallback_sha256": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
}
GROUPS = 175
FLUX_PER_ACTIVE_GROUP = 1.0e16
INITIAL_ACTINV = 1.0e20
SCHEDULE = [(1.0, 1.0) if index % 2 == 0 else (5.0, 0.0) for index in range(19)]


def checked_inputs() -> dict[str, Path]:
    sample = ALARA_SOURCE / "sample"
    paths = {
        "sample1": sample / "sample1",
        "sample3": sample / "sample3",
        "activation": sample / "data" / "truncated_fendlg-2.0_175_for_samples_only",
        "decay": sample / "data" / "truncated_fendld-2.0_for_samples_only",
        "element": sample / "data" / "myElelib",
        "fallback": ENDF_DECAY_FALLBACK,
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing P9 ALARA inputs: " + ", ".join(missing))
    actual = {f"{name}_sha256": sha256(path) for name, path in paths.items()}
    mismatches = {
        key: {"expected": EXPECTED[key], "actual": value}
        for key, value in actual.items()
        if EXPECTED[key] != value
    }
    if mismatches:
        raise RuntimeError(f"pinned ALARA input mismatch: {json.dumps(mismatches, sort_keys=True)}")
    return paths


def build_alara() -> dict:
    commit = command(["git", "-C", ALARA_SOURCE, "rev-parse", "HEAD"]).stdout.strip()
    if commit != EXPECTED["alara_commit"]:
        raise RuntimeError(f"ALARA commit is {commit}, expected {EXPECTED['alara_commit']}")
    if (ALARA_BUILD / "build.ninja").is_file():
        built = command(["ninja", "-C", ALARA_BUILD, "alara"], timeout=300.0)
        build_command = ["ninja", "-C", str(ALARA_BUILD), "alara"]
    elif (ALARA_BUILD / "Makefile").is_file():
        built = command(["make", "-C", ALARA_BUILD, "alara"], timeout=300.0)
        build_command = ["make", "-C", str(ALARA_BUILD), "alara"]
    else:
        raise FileNotFoundError(
            f"no configured ALARA build at {ALARA_BUILD}; configure the pinned source first"
        )
    if not ALARA_BIN.is_file():
        raise FileNotFoundError(f"ALARA binary was not built at {ALARA_BIN}")
    version_result = command([ALARA_BIN, "-V"])
    version = (version_result.stdout + version_result.stderr).strip()
    if "ALARA 2.9.2" not in version:
        raise RuntimeError(f"unexpected ALARA version: {version}")
    return {
        "source": str(ALARA_SOURCE),
        "build_directory": str(ALARA_BUILD),
        "binary": str(ALARA_BIN),
        "commit": commit,
        "version": version,
        "build_command": build_command,
        "build_output_tail": (built.stdout + built.stderr).strip().splitlines()[-10:],
    }


def official_reference(work: Path) -> dict:
    copied_sample = work / "official-sample"
    shutil.copytree(ALARA_SOURCE / "sample", copied_sample)
    (copied_sample / "output").mkdir(exist_ok=True)
    (copied_sample / "dump_files").mkdir(exist_ok=True)
    converted = command([ALARA_BIN, "sample1"], cwd=copied_sample, timeout=300.0)
    run = command([ALARA_BIN, "sample3"], cwd=copied_sample, timeout=300.0)
    transcript = run.stdout + run.stderr
    pulse_markers = {
        "solver_output": "Zone output requested:" in transcript,
        "ten_pulses": "num_pulses_per_level: [10]" in transcript,
        "five_second_delay": "delay_seconds_per_level: [5]" in transcript,
    }
    if not all(pulse_markers.values()):
        raise RuntimeError("official ALARA sample3 did not report its expected pulse solution")
    generated = sorted(
        str(path.relative_to(copied_sample))
        for directory in (copied_sample / "output", copied_sample / "dump_files")
        for path in directory.iterdir()
        if path.is_file()
    )
    return {
        "conversion_succeeded": converted.returncode == 0,
        "run_succeeded": run.returncode == 0,
        "pulse_markers": pulse_markers,
        "generated_files": generated,
        "sample1_sha256": sha256(copied_sample / "sample1"),
        "sample3_sha256": sha256(copied_sample / "sample3"),
    }


def extract_fe56_np(activation: Path) -> tuple[list[str], np.ndarray]:
    lines = activation.read_text().splitlines()
    marker = next(index for index, line in enumerate(lines) if line.startswith("#"))
    start = next(index for index, line in enumerate(lines) if line.startswith(" 260560 1030"))
    count = int(lines[start].split()[2])
    values: list[float] = []
    cursor = start + 3
    while len(values) < count:
        values.extend(float(value) for value in lines[cursor].split())
        cursor += 1
    if len(values) != count:
        raise RuntimeError(f"Fe-56(n,p) record declared {count} values but supplied {len(values)}")
    # The EAF reader pads a short record with its final value through all 175 groups.
    values.extend([values[-1]] * (GROUPS - len(values)))
    subset = lines[: marker + 1] + lines[start:cursor]
    return subset, np.asarray(values, dtype=np.float64)


def write_actinv_library(work: Path, cross_sections_high_to_low: np.ndarray) -> tuple[Path, Path]:
    library = work / "fendl2-fe56-np.npz"
    rows = np.asarray([[0, 103, -1, -1, 0], [0, 103, 25056, 0, -1]], dtype=np.int64)
    # ACTINV's library arrays are ascending-energy, unlike the EAF card.
    ascending = cross_sections_high_to_low[::-1]
    sig = np.vstack([ascending, ascending])
    bounds = np.arange(1.0, GROUPS + 2.0, dtype=np.float64)
    np.savez(library, rows=rows, sig=sig, bounds=bounds)
    index = library.with_name(library.stem + "_index.json")
    write_json(
        index,
        {
            "groups": GROUPS,
            "n_rows": 2,
            "temperature_K": 293.6,
            "sha256_npz": sha256(library),
            "targets": [{"za": 26056, "liso": 0, "awr": 55.454, "ledger": []}],
        },
    )
    return library, index


def write_flux(path: Path) -> np.ndarray:
    high_to_low = np.zeros(GROUPS, dtype=np.float64)
    high_to_low[0] = FLUX_PER_ACTIVE_GROUP
    path.write_text(
        "\n".join(
            " ".join(f"{value:.8E}" for value in high_to_low[start : start + 6])
            for start in range(0, GROUPS, 6)
        )
        + "\n"
    )
    return high_to_low


def custom_alara(
    work: Path, paths: dict[str, Path], subset: list[str], flux_path: Path
) -> tuple[dict[str, dict[str, float]], dict]:
    activation_subset = work / "fendl2-fe56-np.eaf"
    activation_subset.write_text("\n".join(subset) + "\n")
    library_stem = work / "fendl2-fe56-np-bin"
    conversion_input = work / "convert-fe56"
    conversion_input.write_text(
        f"convert_lib eaflib alaralib {activation_subset} {paths['decay']} {library_stem}\n"
    )
    conversion = command([ALARA_BIN, conversion_input.name], cwd=work, timeout=300.0)

    alara_input = work / "fe56-pulses"
    alara_input.write_text(
        f"""geometry rectangular

volume
    1.0 zone_0
end

mat_loading
    zone_0 mix_0
end

mixture mix_0
    element fe:56 1.0 1.0
end

element_lib {paths['element']}
data_library alaralib {library_stem}

flux flux_1 {flux_path} 1 0 default

schedule total
    1 s flux_1 pulsed 0 s
end

pulsehistory pulsed
    10 5 s
end

cooling
    1 s
end

output zone
    units Bq cm
    number_density
end

truncation 1e-20
dump_file {work / 'dump_files' / 'fe56-pulses.dump'}
"""
    )
    (work / "output").mkdir(exist_ok=True)
    (work / "dump_files").mkdir(exist_ok=True)
    run = command([ALARA_BIN, alara_input.name], cwd=work, timeout=300.0)
    transcript = run.stdout + run.stderr
    table: dict[str, dict[str, float]] = {}
    in_number_density = False
    for line in transcript.splitlines():
        if line.startswith("*** Number Density"):
            in_number_density = True
            continue
        if not in_number_density:
            continue
        # Some valid out-of-tree ALARA builds omit element symbols in text output
        # ("-56" instead of "mn-56"/"fe-56").  Half-life still distinguishes
        # the two records unambiguously: Mn-56 is radioactive and Fe-56 is stable.
        match = re.match(
            r"\s*((?:(?:fe|mn)?-56))\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", line, re.I
        )
        if match:
            half_life = float(match.group(2))
            label = match.group(1).lower()
            if label == "-56":
                label = "fe-56" if half_life < 0.0 else "mn-56"
            table[label] = {
                "half_life_s": half_life,
                "pre_irrad": float(match.group(3)),
                "shutdown": float(match.group(4)),
            }
        if len(table) == 2:
            break
    if set(table) != {"fe-56", "mn-56"}:
        raise RuntimeError(f"could not parse Fe-56/Mn-56 from ALARA number-density output: {table}")
    return table, {
        "activation_subset": str(activation_subset),
        "activation_subset_sha256": sha256(activation_subset),
        "converted_library_stem": str(library_stem),
        "conversion_input": str(conversion_input),
        "conversion_input_sha256": sha256(conversion_input),
        "conversion_succeeded": conversion.returncode == 0,
        "run_input": str(alara_input),
        "run_input_sha256": sha256(alara_input),
        "run_succeeded": run.returncode == 0,
        "pulse_markers": {
            "ten_pulses": "num_pulses_per_level: [10]" in transcript,
            "five_second_delay": "delay_seconds_per_level: [5]" in transcript,
            "solver_output": "*** Number Density" in transcript,
        },
    }


def actinv_spec(
    paths: dict[str, Path], library: Path, flux_high_to_low: np.ndarray
) -> dict:
    return {
        "spec": "actinv-spec-1",
        "title": "P9 ALARA identical-data Fe-56(n,p)Mn-56 pulses",
        "library": {"path": str(library), "sha256": sha256(library)},
        "decay": {"primary": str(paths["decay"]), "fallback": str(paths["fallback"])},
        "material": {
            "mass_g": 1.0,
            "basis": "atoms_per_g",
            "composition": {"Fe56": INITIAL_ACTINV},
        },
        "spectrum": {
            "structure": "custom",
            "boundaries_eV": np.arange(1.0, GROUPS + 2.0).tolist(),
            "flux_per_group": flux_high_to_low.tolist(),
            "descending": True,
        },
        "schedule": [
            {"dt": f"{duration:.17e} s", "flux": multiplier}
            for duration, multiplier in SCHEDULE
        ],
        "options": {
            "mode": "coupled",
            "prune": "none",
            "bmin_atoms_per_g": 0.0,
            "temperature_K": 293.6,
            "outputs": ["inventory", "activity", "heat", "ledger", "certificate"],
        },
        "fission_yields": {"files": [], "energy": "spectrum_average"},
    }


def run_actinv(work: Path, specification: dict) -> dict:
    spec_path = work / "fe56-pulses.json"
    result_path = work / "fe56-pulses.result.json"
    write_json(spec_path, specification)
    command([BIN, "run", spec_path, result_path], timeout=300.0)
    return json.loads(result_path.read_text())


def analytic(rate: float, decay_constant: float) -> np.ndarray:
    state = np.asarray([1.0, 0.0])
    for duration, multiplier in SCHEDULE:
        reaction = rate * multiplier
        matrix = np.asarray([[-reaction, decay_constant], [reaction, -decay_constant]])
        state = expm(matrix * duration) @ state
    return state


def main() -> None:
    root = Path(os.environ.get("ACTINV_P9_WORK", tempfile.mkdtemp(prefix="actinv-p9-g5-")))
    work = root / "g5"
    work.mkdir(parents=True, exist_ok=True)
    paths = checked_inputs()
    build = build_alara()
    reference = official_reference(work)

    subset, cross_sections_high_to_low = extract_fe56_np(paths["activation"])
    library, index = write_actinv_library(work, cross_sections_high_to_low)
    flux_path = work / "fe56-flux"
    flux_high_to_low = write_flux(flux_path)
    alara_table, alara_run = custom_alara(work, paths, subset, flux_path)
    result = run_actinv(work, actinv_spec(paths, library, flux_high_to_low))

    alara_initial = alara_table["fe-56"]["pre_irrad"]
    if alara_initial <= 0.0:
        raise RuntimeError(f"ALARA reported nonpositive initial Fe-56 population: {alara_initial}")
    alara_normalized = {
        "Fe56": alara_table["fe-56"]["shutdown"] / alara_initial,
        "Mn56": alara_table["mn-56"]["shutdown"] / alara_initial,
    }
    final = result["steps"][-1]
    actinv_inventory = inventory(final)
    actinv_normalized = {
        name: actinv_inventory.get(name, 0.0) / INITIAL_ACTINV for name in ("Fe56", "Mn56")
    }
    comparisons = {
        name: {
            "actinv_fraction_of_initial": actinv_normalized[name],
            "alara_fraction_of_initial": alara_normalized[name],
            "relative_difference": relative(actinv_normalized[name], alara_normalized[name]),
            "above_1e-10_initial": max(actinv_normalized[name], alara_normalized[name]) > 1.0e-10,
        }
        for name in ("Fe56", "Mn56")
    }
    compared = [row for row in comparisons.values() if row["above_1e-10_initial"]]
    maximum_inventory_relative = max(row["relative_difference"] for row in compared)

    # These two dots start from independently parsed layouts and meet only at the scalar rate.
    alara_rate = float(np.dot(cross_sections_high_to_low, flux_high_to_low) * 1.0e-24)
    actinv_rate = float(
        np.dot(cross_sections_high_to_low[::-1], flux_high_to_low[::-1]) * 1.0e-24
    )
    rate_relative = relative(actinv_rate, alara_rate)
    decay_constant = math.log(2.0) / alara_table["mn-56"]["half_life_s"]
    analytic_normalized = analytic(alara_rate, decay_constant)
    analytic_comparison = {
        "Fe56": {
            "reference": float(analytic_normalized[0]),
            "actinv_relative": relative(actinv_normalized["Fe56"], analytic_normalized[0]),
            "alara_relative": relative(alara_normalized["Fe56"], analytic_normalized[0]),
        },
        "Mn56": {
            "reference": float(analytic_normalized[1]),
            "actinv_relative": relative(actinv_normalized["Mn56"], analytic_normalized[1]),
            "alara_relative": relative(alara_normalized["Mn56"], analytic_normalized[1]),
        },
    }

    expected_time = sum(duration for duration, _ in SCHEDULE)
    expected_exposure = sum(duration * multiplier for duration, multiplier in SCHEDULE)
    expected_fluence = FLUX_PER_ACTIVE_GROUP * expected_exposure
    timeline = {
        "segments": len(SCHEDULE),
        "pulses": sum(multiplier > 0.0 for _, multiplier in SCHEDULE),
        "gaps": sum(multiplier == 0.0 for _, multiplier in SCHEDULE),
        "expected_time_s": expected_time,
        "actinv_time_s": final["t_s"],
        "expected_flux_weighted_time_s": expected_exposure,
        "actinv_flux_weighted_time_s": final["flux_weighted_time_s"],
        "expected_fluence_n_cm2": expected_fluence,
        "actinv_fluence_n_cm2": final["fluence_n_cm2"],
        "worst_relative": max(
            relative(final["t_s"], expected_time),
            relative(final["flux_weighted_time_s"], expected_exposure),
            relative(final["fluence_n_cm2"], expected_fluence),
        ),
    }

    actual_hashes = {f"{name}_sha256": sha256(path) for name, path in paths.items()}
    output = {
        "alara": build,
        "official_reference": reference,
        "identical_data_run": alara_run,
        "data": {
            "official_hashes": actual_hashes,
            "activation_subset_sha256": alara_run["activation_subset_sha256"],
            "actinv_library": str(library),
            "actinv_library_sha256": sha256(library),
            "actinv_index": str(index),
            "actinv_index_sha256": sha256(index),
            "flux": str(flux_path),
            "flux_sha256": sha256(flux_path),
            "groups": GROUPS,
            "official_nonzero_cross_sections": int(np.count_nonzero(cross_sections_high_to_low)),
            "active_eaf_group_zero_based": 0,
            "cross_section_barns": float(cross_sections_high_to_low[0]),
            "flux_n_cm2_s": FLUX_PER_ACTIVE_GROUP,
        },
        "rates": {
            "alara_eaf_order_per_s": alara_rate,
            "actinv_ascending_order_per_s": actinv_rate,
            "relative_difference": rate_relative,
        },
        "timeline": timeline,
        "alara_text_inventory": alara_table,
        "comparisons": comparisons,
        "maximum_inventory_relative_above_1e-10_initial": maximum_inventory_relative,
        "analytic_comparison": analytic_comparison,
        "actinv": {
            "mode": result["mode"],
            "numerical_floor_atoms_per_g": final["numerical_floor_atoms_per_g"],
            "certificate": result["certificate"],
            "ledger": result["ledger"],
        },
    }
    output["pass"] = bool(
        build["commit"] == EXPECTED["alara_commit"]
        and reference["conversion_succeeded"]
        and reference["run_succeeded"]
        and all(reference["pulse_markers"].values())
        and alara_run["conversion_succeeded"]
        and alara_run["run_succeeded"]
        and all(alara_run["pulse_markers"].values())
        and actual_hashes == {key: EXPECTED[key] for key in actual_hashes}
        and rate_relative <= 1.0e-12
        and timeline["pulses"] == 10
        and timeline["gaps"] == 9
        and timeline["worst_relative"] <= 1.0e-12
        and len(compared) == 2
        and maximum_inventory_relative <= 5.0e-4
        and max(row["actinv_relative"] for row in analytic_comparison.values()) <= 1.0e-8
        and max(row["alara_relative"] for row in analytic_comparison.values()) <= 5.0e-4
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g5_p9_alara.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
