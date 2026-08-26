#!/usr/bin/env python3
"""P9-G6: CoNDERC U-235 decay heat, provenance, quality, and regression.

Accuracy is reported rather than gated.  The gate covers complete finite-point/channel
coverage, independent per-fission normalization, pinned provenance, and regression.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from p9_fixtures import (
    BIN,
    CONDERC_ROOT,
    OPENMC_PYTHON,
    ROOT,
    U235_NFPY,
    command,
    relative,
    sha256,
    write_json,
)


RESULTS = Path(os.environ.get("ACTINV_P9_RESULTS", ROOT / "results"))
DECAY = Path(
    os.environ.get(
        "ACTINV_ENDF_DECAY",
        Path.home() / "nuclear-data" / "endfb-viii.0-decay" / "bulk" / "endf-b-viii-0_decay.dat",
    )
)
U235_H5 = Path(
    os.environ.get(
        "ACTINV_P9_U235_H5", Path.home() / "nuclear-data" / "endfb-vii.1-hdf5" / "neutron" / "U235.h5"
    )
)
CONDERC_ARCHIVE = Path(
    os.environ.get(
        "ACTINV_P9_CONDERC_ARCHIVE", Path.home() / "nuclear-data" / "conderc-fission.zip"
    )
)
UKAEA_REPORT = Path(
    os.environ.get(
        "ACTINV_P9_UKAEA_REPORT",
        Path.home() / "nuclear-data" / "conderc-fission" / "references" / "UKAEA-R18003.pdf",
    )
)
ORIGEN_REPORT = Path(
    os.environ.get(
        "ACTINV_P9_ORIGEN_REPORT",
        Path.home()
        / "nuclear-data"
        / "conderc-fission"
        / "references"
        / "Gauld_SummaryReport_2019.pdf",
    )
)
PROTOCOL = ROOT / "protocols" / "ACTINV-P9_PROTOCOL.md"
JOULE_PER_MEV = 1.602176634e-13
THERMAL_ENERGY_EV = 0.0253

EXPECTED_HASHES = {
    "protocol": "028c5846865490e9dee5902f22f5ad4be583ee332be9d92ce23efa80c52d39c0",
    "nfpy": "9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41",
    "decay": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "u235_h5": "c2f071a2cf180c5f73bb4f054eb30a6e29b1fc963d69720e7560e32eee91b4eb",
    "conderc_archive": "30756fef88c0f3637246bf8ad8ef1fc5397a3f784e5408f2861bc474993e74a5",
    "pulse_input": "ecba520a89bd8a1046088c40ba300016355220b552e8ca6ae8451f84161aa477",
    "constant_input": "a4d0a80a7f59f16db6ae15119d77468a45facc02d01f4fdaa96036047e372b94",
    "thermal_flux": "46c42e3ea73803bc791c55209d8bb8aeceffaf9e2bbce90aa645ff02acb4f68f",
    "dickens": "2a0442af2c232a86b07c198680461f5acfd3530bcf78bb3d23967f34a018f47b",
    "yarnell": "3d35f4bb7bcf167a5ec2dae8371093b65bea9d6a687156a0be552be23a3aad0c",
    "ukaea_report": "35495e39a3741e8d7d6e2097ba940070d42db1cf8adf6d18bfc488b91b82a2a1",
    "origen_report": "71f22abd8993f72656b00ae80bff02099bbb1bea8f8db4781e33b58f9d273f74",
}


def paths() -> dict[str, Path]:
    fispact = CONDERC_ROOT / "fission" / "Fispact-II" / "Inputs"
    measurements = CONDERC_ROOT / "fission" / "expDataSystem" / "U235_thermal"
    values = {
        "protocol": PROTOCOL,
        "nfpy": U235_NFPY,
        "decay": DECAY,
        "u235_h5": U235_H5,
        "conderc_archive": CONDERC_ARCHIVE,
        "pulse_input": fispact / "U235pulse.i",
        "constant_input": fispact / "U2352E4s.i",
        "thermal_flux": fispact / "fluxes_therm",
        "dickens": measurements / "Dickens_pulse.csv",
        "yarnell": measurements / "Yarnell_20000.csv",
        "ukaea_report": UKAEA_REPORT,
        "origen_report": ORIGEN_REPORT,
    }
    missing = [str(path) for path in values.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing P9 G6 input(s): " + ", ".join(missing))
    actual = {name: sha256(path) for name, path in values.items()}
    mismatch = {
        name: {"expected": EXPECTED_HASHES[name], "actual": digest}
        for name, digest in actual.items()
        if digest != EXPECTED_HASHES[name]
    }
    if mismatch:
        raise RuntimeError(f"P9 G6 pinned input mismatch: {json.dumps(mismatch, sort_keys=True)}")
    return values


def openmc_thermal_fission_xs(path: Path) -> dict:
    program = r"""
import json, openmc, sys
data = openmc.data.IncidentNeutron.from_hdf5(sys.argv[1])
temperature = '294K'
sigma = float(data.reactions[18].xs[temperature](0.0253))
print(json.dumps({'openmc_version': openmc.__version__, 'temperature': temperature,
                  'energy_eV': 0.0253, 'sigma_fission_b': sigma}))
"""
    result = command([OPENMC_PYTHON, "-c", program, path])
    return json.loads(result.stdout.strip().splitlines()[-1])


def fispact_history(path: Path) -> dict:
    text = path.read_text()
    fuel_match = re.search(r"^\s*U235\s+([0-9.E+-]+)\s*$", text, re.M | re.I)
    flux_match = re.search(r"^FLUX\s+([0-9.E+-]+)\s*$", text, re.M | re.I)
    time_match = re.search(r"^TIME\s+([0-9.E+-]+)\s*$", text, re.M | re.I)
    if not all((fuel_match, flux_match, time_match)):
        raise RuntimeError(f"cannot parse FISPACT fuel/flux/time from {path}")
    return {
        "initial_u235_atoms": float(fuel_match.group(1)),
        "flux_n_cm2_s": float(flux_match.group(1)),
        "irradiation_s": float(time_match.group(1)),
        "usefission": bool(re.search(r"^USEFISSION\s*$", text, re.M)),
        "fisyield_u235": bool(re.search(r"^FISYIELD\s+1\s+U235\s*$", text, re.M)),
        "fischchoose_u235": bool(re.search(r"^FISCHOOSE\s+1\s+U235\s*$", text, re.M)),
    }


def measurement_file(path: Path) -> tuple[dict, list[dict]]:
    comments = []
    rows = []
    with path.open() as stream:
        for line in stream:
            if line.startswith("#"):
                comments.append(line.strip())
                continue
            fields = next(csv.reader([line]))
            if not fields or not fields[0].strip():
                continue
            value = lambda index: None if fields[index].strip() == "-" else float(fields[index])
            rows.append(
                {
                    "cooling_time_s": float(fields[0]),
                    "beta": value(1),
                    "beta_uncertainty": value(2),
                    "gamma": value(3),
                    "gamma_uncertainty": value(4),
                    "total": value(5),
                    "total_uncertainty": value(6),
                }
            )
    if not rows or any(right["cooling_time_s"] <= left["cooling_time_s"] for left, right in zip(rows, rows[1:])):
        raise RuntimeError(f"measurement times are empty or non-increasing in {path}")
    metadata = {
        "comments": comments,
        "author_header": comments[0].split(",", 1)[1] if comments and "," in comments[0] else None,
        "irradiation_header": comments[1].split(",", 1)[1] if len(comments) > 1 and "," in comments[1] else None,
    }
    return metadata, rows


def activation_library(work: Path, sigma_fission_b: float) -> tuple[Path, Path]:
    library = work / "u235-thermal-fission.npz"
    rows = np.asarray([[0, 18, -1, -1, 0], [0, 18, 0, 0, 0]], dtype=np.int64)
    sig = np.asarray([[sigma_fission_b], [sigma_fission_b]], dtype=np.float64)
    bounds = np.asarray([0.0, 1.0], dtype=np.float64)
    np.savez(library, rows=rows, sig=sig, bounds=bounds)
    index = library.with_name(library.stem + "_index.json")
    write_json(
        index,
        {
            "groups": 1,
            "n_rows": 2,
            "temperature_K": 293.6,
            "sha256_npz": sha256(library),
            "targets": [{"za": 92235, "liso": 0, "awr": 233.0248, "ledger": []}],
        },
    )
    return library, index


def specification(
    title: str,
    library: Path,
    history: dict,
    measurements: list[dict],
) -> dict:
    previous = 0.0
    cooling = []
    for row in measurements:
        duration = row["cooling_time_s"] - previous
        cooling.append({"dt": f"{duration:.17e} s", "flux": 0.0})
        previous = row["cooling_time_s"]
    return {
        "spec": "actinv-spec-1",
        "title": title,
        "library": {"path": str(library), "sha256": sha256(library)},
        "decay": {"primary": str(DECAY)},
        "material": {
            "mass_g": 1.0,
            "basis": "atoms_per_g",
            "composition": {"U235": history["initial_u235_atoms"]},
        },
        "spectrum": {
            "structure": "custom",
            "boundaries_eV": [0.0, 1.0],
            "flux_per_group": [1.0],
            "total": history["flux_n_cm2_s"],
            "descending": False,
        },
        "schedule": [
            {"dt": f"{history['irradiation_s']:.17e} s", "flux": 1.0},
            *cooling,
        ],
        "options": {
            # The benchmark definition is an ideal constant fission-rate source.  Trace mode
            # keeps that source constant; the scalar cross section cancels in normalization.
            "mode": "trace",
            "prune": "none",
            "bmin_atoms_per_g": 0.0,
            "temperature_K": 293.6,
            "outputs": ["inventory", "heat", "ledger", "certificate"],
        },
        "fission_yields": {
            "files": [{"path": str(U235_NFPY), "sha256": sha256(U235_NFPY)}],
            "energy": "fixed",
            "fixed_energy_eV": THERMAL_ENERGY_EV,
        },
    }


def run_family(work: Path, name: str, specification_value: dict) -> dict:
    spec_path, result_path = work / f"{name}.json", work / f"{name}.result.json"
    write_json(spec_path, specification_value)
    command([BIN, "run", spec_path, result_path], timeout=600.0)
    return json.loads(result_path.read_text())


def normalized_heats(
    result: dict, history: dict, normalization: str, sigma_fission_b: float
) -> tuple[list[dict], dict]:
    ledger = result["ledger"]
    balance = ledger["fission_yield_balance"]["92235_0"]
    rate_per_parent = balance["fission_rate_per_parent_s"]
    independent_rate = sigma_fission_b * history["flux_n_cm2_s"] * 1.0e-24
    initial_rate = history["initial_u235_atoms"] * rate_per_parent
    integrated_fissions = initial_rate * history["irradiation_s"]
    divisor = integrated_fissions if normalization == "integrated_fissions" else initial_rate
    background = ledger["bulk_background_heat_W_per_g"]
    rows = []
    for step in result["steps"][1:]:
        heat = dict(step["heat_W_per_g"])
        heat["alpha"] = max(0.0, heat["alpha"] - background)
        heat["total"] = max(0.0, heat["total"] - background)
        rows.append(
            {
                channel: heat[channel] / divisor / JOULE_PER_MEV
                for channel in ("beta", "gamma", "total")
            }
        )
    return rows, {
        "kind": normalization,
        "fission_rate_per_parent_s_from_ledger": rate_per_parent,
        "fission_rate_per_parent_s_independent": independent_rate,
        "initial_fission_rate_per_s": initial_rate,
        "integrated_fissions": integrated_fissions,
        "divisor": divisor,
        "relative_rate_closure": relative(rate_per_parent, independent_rate),
        "bulk_parent_background_subtracted_W_per_g": background,
    }


def compare(
    measurements: list[dict], calculated: list[dict], *, pulse_time_weighted: bool
) -> tuple[list[dict], dict]:
    if len(measurements) != len(calculated):
        raise RuntimeError(f"measurement/calculation row count mismatch: {len(measurements)}/{len(calculated)}")
    rows = []
    by_channel = {channel: [] for channel in ("beta", "gamma", "total")}
    for measured, model in zip(measurements, calculated):
        for channel in by_channel:
            experiment = measured[channel]
            if experiment is None or not math.isfinite(experiment):
                continue
            calculation = model[channel]
            uncertainty = measured[f"{channel}_uncertainty"]
            scale = 1.0 / measured["cooling_time_s"] if pulse_time_weighted else 1.0
            archive_experiment, archive_uncertainty = experiment, uncertainty
            experiment *= scale
            uncertainty *= scale
            ce = calculation / experiment
            entry = {
                "cooling_time_s": measured["cooling_time_s"],
                "channel": channel,
                "calculated_MeV_per_fission_s": calculation,
                "experimental_MeV_per_fission_s": experiment,
                "experimental_uncertainty_MeV_per_fission_s": uncertainty,
                "archive_value": archive_experiment,
                "archive_uncertainty": archive_uncertainty,
                "archive_conversion": (
                    "divide time-weighted pulse value by cooling_time_s"
                    if pulse_time_weighted
                    else "none (finite-irradiation power/fission-rate value is MeV/fission)"
                ),
                "experimental_relative_uncertainty": uncertainty / experiment,
                "C_over_E": ce,
                "log_C_over_E": math.log(ce),
                "residual_over_experimental_sigma": (calculation - experiment) / uncertainty,
            }
            rows.append(entry)
            by_channel[channel].append(entry)
    aggregate = {}
    for channel, channel_rows in by_channel.items():
        if not channel_rows:
            continue
        logs = [row["log_C_over_E"] for row in channel_rows]
        aggregate[channel] = {
            "points": len(channel_rows),
            "geometric_mean_C_over_E": math.exp(math.fsum(logs) / len(logs)),
            "maximum_absolute_log_C_over_E": max(abs(value) for value in logs),
            "minimum_C_over_E": min(row["C_over_E"] for row in channel_rows),
            "maximum_C_over_E": max(row["C_over_E"] for row in channel_rows),
            "rms_experimental_sigma": math.sqrt(
                math.fsum(row["residual_over_experimental_sigma"] ** 2 for row in channel_rows)
                / len(channel_rows)
            ),
        }
    return rows, aggregate


def certificate_hashes(result: dict) -> dict:
    inputs = result["certificate"]["inputs"]
    flat = []
    for key in ("library", "library_index", "decay_primary", "decay_fallback", "photon_response"):
        if inputs.get(key):
            flat.append((key, inputs[key]))
    flat.extend(("fission_yield", item) for item in inputs["fission_yields"])
    return {
        f"{name}:{item['path']}": {
            "recorded": item["sha256"],
            "recomputed": sha256(Path(item["path"])),
            "match": item["sha256"] == sha256(Path(item["path"])),
        }
        for name, item in flat
    }


def pre_p9_regression(work: Path) -> dict:
    baseline_path = ROOT / "results" / "fns_spec" / "Fe_1996exp_5min.json"
    baseline = json.loads(baseline_path.read_text())
    spec_path, result_path = work / "pre-p9-regression.json", work / "pre-p9-regression.result.json"
    write_json(spec_path, baseline["spec"])
    command([BIN, "run", spec_path, result_path], timeout=600.0)
    current = json.loads(result_path.read_text())
    differences = []
    for key in ("mode", "pruned_states", "total_states"):
        if current[key] != baseline[key]:
            differences.append([key, baseline[key], current[key]])
    scalar_fields = (
        "t_s",
        "total_atoms_per_g",
        "numerical_floor_atoms_per_g",
        "n_states_below_floor",
        "heat_bound_from_below_floor_W_per_g",
        "leakage_atoms_per_g",
        "negative_atoms_zeroed",
        "inventory",
        "activity_Bq_per_g",
    )
    if len(current["steps"]) != len(baseline["steps"]):
        differences.append(["steps.length", len(baseline["steps"]), len(current["steps"])])
    for index, (now, then) in enumerate(zip(current["steps"], baseline["steps"])):
        if len(now["inventory"]) != then["n_inventory"]:
            differences.append(
                [f"steps[{index}].n_inventory", then["n_inventory"], len(now["inventory"])]
            )
        for field in scalar_fields:
            if now[field] != then[field]:
                differences.append([f"steps[{index}].{field}", "changed"])
    for index, (now, then) in enumerate(zip(current["steps"][1:], baseline["heat_split_uW_g"])):
        converted = {key: value * 1.0e6 for key, value in now["heat_W_per_g"].items()}
        if converted != then:
            differences.append([f"cooling_heat[{index}]", then, converted])
    return {
        "baseline": str(baseline_path.relative_to(ROOT)),
        "pre_p9_deterministic_fields_compared": scalar_fields,
        "new_p9_additive_fields_excluded": [
            "steps[].flux",
            "steps[].flux_weighted_time_s",
            "steps[].fluence_n_cm2",
            "fission-specific ledger/certificate fields",
        ],
        "differences": len(differences),
        "examples": differences[:20],
    }


def quality(arguments: list[str]) -> dict:
    cargo = Path(
        os.environ.get(
            "CARGO",
            "/home/connoravila/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/cargo",
        )
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{cargo.parent}:/usr/local/bin:/usr/bin:/bin"

    def limits() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (12_000_000_000, 12_000_000_000))

    result = subprocess.run(
        [str(cargo), *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=limits,
    )
    return {
        "command": ["cargo", *arguments],
        "returncode": result.returncode,
        "output_tail": result.stdout[-4000:],
    }


def main() -> None:
    all_paths = paths()
    root = Path(os.environ.get("ACTINV_P9_WORK", tempfile.mkdtemp(prefix="actinv-p9-g6-")))
    work = root / "g6"
    work.mkdir(parents=True, exist_ok=True)

    xs = openmc_thermal_fission_xs(all_paths["u235_h5"])
    library, index = activation_library(work, xs["sigma_fission_b"])
    pulse_history = fispact_history(all_paths["pulse_input"])
    constant_history = fispact_history(all_paths["constant_input"])
    dickens_metadata, dickens = measurement_file(all_paths["dickens"])
    yarnell_metadata, yarnell = measurement_file(all_paths["yarnell"])

    pulse = run_family(
        work,
        "dickens-pulse",
        specification("CoNDERC Dickens U-235 thermal pulse", library, pulse_history, dickens),
    )
    constant = run_family(
        work,
        "yarnell-20000s",
        specification(
            "CoNDERC Yarnell U-235 thermal 20000 s irradiation",
            library,
            constant_history,
            yarnell,
        ),
    )
    pulse_heats, pulse_normalization = normalized_heats(
        pulse, pulse_history, "integrated_fissions", xs["sigma_fission_b"]
    )
    constant_heats, constant_normalization = normalized_heats(
        constant, constant_history, "irradiation_fission_rate", xs["sigma_fission_b"]
    )
    pulse_rows, pulse_aggregate = compare(dickens, pulse_heats, pulse_time_weighted=True)
    constant_rows, constant_aggregate = compare(
        yarnell, constant_heats, pulse_time_weighted=False
    )

    pulse_hashes, constant_hashes = certificate_hashes(pulse), certificate_hashes(constant)
    regression = pre_p9_regression(work)
    tests = quality(["test", "--workspace"])
    clippy = quality(
        ["clippy", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"]
    )
    fmt = quality(["fmt", "--all", "--", "--check"])
    prior_verdicts = {
        phase: json.loads((ROOT / "results" / f"verdict_{phase.lower()}.json").read_text())[
            "verdict"
        ]
        for phase in ("P5", "P6", "P7", "P8")
    }

    thermal_tokens = all_paths["thermal_flux"].read_text().split()
    thermal_numeric = []
    for value in thermal_tokens:
        try:
            thermal_numeric.append(float(value))
        except ValueError:
            pass
    if len(thermal_numeric) != 710:
        raise RuntimeError(
            f"expected 709 group values plus one normalization in fluxes_therm, got {len(thermal_numeric)}"
        )
    thermal_flux_values = thermal_numeric[:709]
    actual_hashes = {name: sha256(path) for name, path in all_paths.items()}
    expected_coverage = {
        "dickens": {
            "rows": len(dickens),
            "finite_beta": sum(row["beta"] is not None for row in dickens),
            "finite_gamma": sum(row["gamma"] is not None for row in dickens),
            "finite_total": sum(row["total"] is not None for row in dickens),
        },
        "yarnell": {
            "rows": len(yarnell),
            "finite_beta": sum(row["beta"] is not None for row in yarnell),
            "finite_gamma": sum(row["gamma"] is not None for row in yarnell),
            "finite_total": sum(row["total"] is not None for row in yarnell),
        },
    }
    output = {
        "model": {
            "thermal_energy_eV": THERMAL_ENERGY_EV,
            "activation_cross_section": xs,
            "activation_cross_section_note": (
                "ENDF/B-VII.1 OpenMC HDF5 supplies the scalar U-235 MT=18 rate; the trace-mode "
                "constant-source per-fission normalization cancels this scalar. Yields and decay are "
                "ENDF/B-VIII.0."
            ),
            "library": str(library),
            "library_sha256": sha256(library),
            "index": str(index),
            "index_sha256": sha256(index),
            "fission_yield_selection_pulse": pulse["certificate"]["fission_yields"],
            "fission_yield_selection_constant": constant["certificate"]["fission_yields"],
        },
        "histories": {"pulse": pulse_history, "constant_20000s": constant_history},
        "thermal_flux_file": {
            "groups": len(thermal_flux_values),
            "sum": math.fsum(thermal_flux_values),
            "trailing_normalization": thermal_numeric[709],
            "trailing_title": "Thermal neutron",
            "nonzero": [
                {"zero_based_group": index, "value": value}
                for index, value in enumerate(thermal_flux_values)
                if value != 0.0
            ],
        },
        "measurement_metadata": {
            "dickens": dickens_metadata,
            "yarnell": yarnell_metadata,
            "archive_anomalies": [
                "Dickens_pulse.csv labels values MeV/f/s, but UKAEA-R(18)003 defines the plotted pulse quantity as cooling-time times power/fission; values and uncertainties are divided by cooling time for the protocol's MeV s^-1 fission^-1 C/E.",
                "Yarnell_20000.csv says Author=Akiyama although its filename and UKAEA-R(18)003 attribution are Yarnell.",
                "Yarnell_20000.csv says Irradiation=Pulse although U2352E4s.i and UKAEA-R(18)003 specify 20,000 s.",
                "The pinned archive is not altered; filename, paired FISPACT input, and report define the benchmark identity/history."
            ],
        },
        "coverage": expected_coverage,
        "dickens_pulse": {
            "normalization": pulse_normalization,
            "rows": pulse_rows,
            "aggregate": pulse_aggregate,
            "certificate_hashes": pulse_hashes,
            "numerical_floor_atoms_per_g_max": max(
                step["numerical_floor_atoms_per_g"] for step in pulse["steps"]
            ),
        },
        "yarnell_20000s": {
            "normalization": constant_normalization,
            "rows": constant_rows,
            "aggregate": constant_aggregate,
            "certificate_hashes": constant_hashes,
            "numerical_floor_atoms_per_g_max": max(
                step["numerical_floor_atoms_per_g"] for step in constant["steps"]
            ),
        },
        "reference_context": {
            "fispact_ii": {
                "report": "UKAEA-R(18)003, Validation of FISPACT-II Decay Heat and Inventory Predictions for Fission Events",
                "sha256": actual_hashes["ukaea_report"],
                "matching_results": [
                    "Figures 9 and 10: U-235 thermal pulse, total/gamma and total/beta, including Dickens data.",
                    "Figure 15: U-235 thermal 20,000 s irradiation, total heat, including Yarnell data.",
                    "The report plots paired ENDF/B-VIII.0 nFY+DD, JENDL, JEFF, and GEFY/ENDF-B-VII.1 curves."
                ],
            },
            "origen": {
                "report": "Gauld 2019 CoNDERC summary report TAL-NAPC20190311-001",
                "sha256": actual_hashes["origen_report"],
                "code_and_data": (
                    "ORIGEN from SCALE 6.1.3; ENDF/B-VII.0 yields and ENDF/B-VII.1 decay, "
                    "so these are context rather than code/data identity."
                ),
                "matching_20000s_table": {
                    "source": "Table 5, U-235 20,000 s irradiation (Friesenhahn et al. 1979, not Yarnell)",
                    "calculated_total_anchors_MeV_per_fission": [
                        {"cooling_time_s": 0.95, "ORIGEN": 10.31, "C_over_M": 0.934},
                        {"cooling_time_s": 19586.19, "ORIGEN": 0.2906, "C_over_M": 1.000},
                        {"cooling_time_s": 104933.8, "ORIGEN": 0.04314, "C_over_M": 1.037},
                    ],
                },
                "pulse_note": "The 2019 summary lists Dickens in the wider CoNDERC corpus but does not tabulate the Dickens pulse calculation."
            },
        },
        "provenance": {
            "external_paths": {name: str(path) for name, path in all_paths.items()},
            "external_hashes": actual_hashes,
            "all_expected_hashes_match": actual_hashes == EXPECTED_HASHES,
            "all_certificate_hashes_rematch": all(
                row["match"] for row in [*pulse_hashes.values(), *constant_hashes.values()]
            ),
        },
        "pre_p9_regression": regression,
        "quality": {"workspace_tests": tests, "strict_clippy": clippy, "rustfmt": fmt},
        "retained_verdicts": prior_verdicts,
    }
    finite_expected = sum(
        row[key] for row in expected_coverage.values() for key in ("finite_beta", "finite_gamma", "finite_total")
    )
    all_ce_rows = pulse_rows + constant_rows
    output["pass"] = bool(
        expected_coverage
        == {
            "dickens": {"rows": 32, "finite_beta": 32, "finite_gamma": 32, "finite_total": 32},
            "yarnell": {"rows": 79, "finite_beta": 0, "finite_gamma": 0, "finite_total": 79},
        }
        and len(all_ce_rows) == finite_expected == 175
        and all(
            math.isfinite(row["C_over_E"]) and row["C_over_E"] > 0.0 and row["experimental_uncertainty_MeV_per_fission_s"] > 0.0
            for row in all_ce_rows
        )
        and pulse_normalization["relative_rate_closure"] <= 1.0e-6
        and constant_normalization["relative_rate_closure"] <= 1.0e-6
        and output["provenance"]["all_expected_hashes_match"]
        and output["provenance"]["all_certificate_hashes_rematch"]
        and regression["differences"] == 0
        and tests["returncode"] == 0
        and clippy["returncode"] == 0
        and fmt["returncode"] == 0
        and prior_verdicts
        == {"P5": "P5-PASS", "P6": "P6-CONDITIONAL", "P7": "P7-CONDITIONAL", "P8": "P8-CONDITIONAL"}
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g6_p9_conderc.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
