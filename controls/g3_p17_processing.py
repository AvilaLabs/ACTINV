#!/usr/bin/env python3
"""P17-G3: fresh ACTINV/NJOY raw-evaluation processing differential.

Six hash-pinned FENDL-3.2c neutron evaluations are independently processed by
ACTINV 1.0.1 and NJOY2016.79 at the protocol temperature and reconstruction
tolerance.  The resulting capture and threshold reactions are collapsed onto
the same FISPACT-709 boundaries.  Compact scalar evidence is committed; raw
evaluations, PENDF tapes, and generated libraries remain external or temporary.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from endf_common import endf_float, fields, read_list, read_tab1, sections


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g3_p17_processing.json"
PROTOCOL_SHA256 = "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f"
TEMPERATURE_K = 293.6
RECONSTRUCTION_TOLERANCE = 2.0e-4
GROUP_RELATIVE_TOLERANCE = 2.0e-2
ONE_GROUP_RELATIVE_TOLERANCE = 5.0e-3
ADDRESS_SPACE_LIMIT_BYTES = 12 * 1024**3
PROFILE_WALL_CEILING_S = 300.0

FENDL_DIR = Path(
    os.environ.get(
        "ACTINV_FENDL", Path.home() / "nuclear-data" / "fendl-3.2c" / "endf"
    )
)
NJOY = Path(
    os.environ.get(
        "ACTINV_P17_NJOY", Path.home() / "nuclear-data" / "njoy2016.79-build" / "njoy"
    )
)
NJOY_SOURCE = Path(
    os.environ.get(
        "ACTINV_P17_NJOY_SOURCE", Path.home() / "nuclear-data" / "njoy2016.79"
    )
)
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
ACTINV_PROCESSING_SOURCE = ROOT / "crates" / "actinv-data" / "src" / "processing.rs"

NJOY_COMMIT = "ac5adf5f33d893e42f2eed7fb286b0d51c7580da"
NJOY_FILES = {
    "reconr": (
        "src/reconr.f90",
        "054ede7a59e1c39cf3e72105d8a0b95a0fb1d8df0882eca6b949e765b62bf5db",
    ),
    "broadr": (
        "src/broadr.f90",
        "b2dc071a0f63975cfe702f84441539cfaecbdeb7dfd74c5be70578b72184744e",
    ),
    "unresr": (
        "src/unresr.f90",
        "57a3a975566d45a8f2d0db67fed121b908e50039d9aafb25ea27f628c745d650",
    ),
    "groupr": (
        "src/groupr.f90",
        "0b7b7237f897071552b81a59eb0c3bcccae36aa3dbc585a4d55f0e103e9f6d31",
    ),
    "license": (
        "LICENSE",
        "08dc30ca5b19bfa904168f5194b646bb13a661e3591c4e2d000e9a514554b76c",
    ),
}

# The order is frozen: the representative Fe-56 profile must pass before the
# broader target expansion runs.  LSSF is represented as NJOY's ENDF value;
# None means that the evaluation has no unresolved range.
TARGETS = [
    {
        "name": "Fe56",
        "filename": "n_2631_26-Fe-56.endf",
        "sha256": "24a45021fb38262dd8fb598c520a807f342bd07e137a36e88d7ae97a0f38715e",
        "mat": 2631,
        "za": 26056,
        "resolved_lrf": [3],
        "unresolved_lssf": None,
        "threshold_mt": 16,
    },
    {
        "name": "Ag107",
        "filename": "n_4725_47-Ag-107.endf",
        "sha256": "0610e15630cb0837a801611d42b6cd401435ddb93dde1126e63000b83ba14185",
        "mat": 4725,
        "za": 47107,
        "resolved_lrf": [2],
        "unresolved_lssf": 0,
        "threshold_mt": 107,
    },
    {
        "name": "W186",
        "filename": "n_7443_74-W-186.endf",
        "sha256": "bf6bf3bb7a1583be49ae8aab865e75d256e0965f969f38a14d63260b3f4a8744",
        "mat": 7443,
        "za": 74186,
        "resolved_lrf": [7],
        "unresolved_lssf": 1,
        "threshold_mt": 16,
    },
    {
        "name": "Au197",
        "filename": "n_7925_79-Au-197.endf",
        "sha256": "fb7897fdde04b68b79cfc2a44e90a7c3aba77397815a5be342648af013f39f6d",
        "mat": 7925,
        "za": 79197,
        "resolved_lrf": [2],
        "unresolved_lssf": None,
        "threshold_mt": 107,
    },
    {
        "name": "Co59",
        "filename": "n_2725_27-Co-59.endf",
        "sha256": "a4c6480e200b9474ed04900e4d17d018577d6235d57f31609b75322ae9a3b75d",
        "mat": 2725,
        "za": 27059,
        "resolved_lrf": [3],
        "unresolved_lssf": None,
        "threshold_mt": 103,
    },
    {
        "name": "Ni58",
        "filename": "n_2825_28-Ni-58.endf",
        "sha256": "312f5a069dbda4e0abd662a258710ea332dd749191a9bad2a0c70567644af4f4",
        "mat": 2825,
        "za": 28058,
        "resolved_lrf": [3],
        "unresolved_lssf": None,
        "threshold_mt": 103,
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_array_sha256(*arrays: np.ndarray) -> str:
    digest = hashlib.sha256()
    for array in arrays:
        canonical = np.ascontiguousarray(array, dtype="<f8")
        digest.update(np.asarray(canonical.shape, dtype="<u8").tobytes())
        digest.update(canonical.tobytes())
    return digest.hexdigest()


def limit_address_space() -> None:
    resource.setrlimit(
        resource.RLIMIT_AS, (ADDRESS_SPACE_LIMIT_BYTES, ADDRESS_SPACE_LIMIT_BYTES)
    )


def checked_run(
    arguments: list[str | Path],
    *,
    cwd: Path,
    input_text: str | None = None,
    timeout: float = 1800.0,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        preexec_fn=limit_address_space,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(argument) for argument in arguments)}\n"
            f"stdout:\n{completed.stdout[-12000:]}\n"
            f"stderr:\n{completed.stderr[-12000:]}"
        )
    return completed


def checked_inputs() -> dict[str, Any]:
    required = [FENDL_DIR, NJOY, NJOY_SOURCE, ACTINV, ACTINV_PROCESSING_SOURCE]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing P17 G3 input: " + ", ".join(missing))

    evaluations = {}
    for target in TARGETS:
        path = FENDL_DIR / target["filename"]
        actual = sha256(path) if path.is_file() else None
        if actual != target["sha256"]:
            raise RuntimeError(
                f"pinned {target['name']} evaluation changed: {actual}, "
                f"expected {target['sha256']}"
            )
        evaluations[target["name"]] = {
            "filename": target["filename"],
            "sha256": actual,
            "bytes": path.stat().st_size,
        }

    source_hashes = {}
    for name, (relative_path, expected) in NJOY_FILES.items():
        path = NJOY_SOURCE / relative_path
        actual = sha256(path) if path.is_file() else None
        if actual != expected:
            raise RuntimeError(
                f"pinned NJOY {name} source changed: {actual}, expected {expected}"
            )
        source_hashes[name] = actual
    commit = subprocess.run(
        ["git", "-C", str(NJOY_SOURCE), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    if commit != NJOY_COMMIT:
        raise RuntimeError(f"pinned NJOY commit changed: {commit}")
    with tempfile.TemporaryDirectory(prefix="actinv-p17-njoy-version-") as temporary:
        banner = checked_run(
            [NJOY], cwd=Path(temporary), input_text="stop\n", timeout=60.0
        ).stdout
    if "njoy 2016.79" not in banner.lower():
        raise RuntimeError("NJOY executable did not identify as 2016.79")
    version = checked_run([ACTINV, "--version"], cwd=ROOT, timeout=60.0).stdout.strip()
    if version != "actinv 1.0.1":
        raise RuntimeError(f"unexpected ACTINV version: {version}")
    processing_source = ACTINV_PROCESSING_SOURCE.read_text()
    tolerance_declaration = "const LINEARIZATION_TOLERANCE: f64 = 2e-4;"
    if tolerance_declaration not in processing_source:
        raise RuntimeError(
            "ACTINV processing source no longer declares the frozen 2e-4 tolerance"
        )
    return {
        "evaluations": evaluations,
        "njoy": {
            "version": "2016.79",
            "commit": commit,
            "binary_sha256": sha256(NJOY),
            "source_hashes": source_hashes,
        },
        "actinv": {
            "version": version,
            "binary_sha256": sha256(ACTINV),
            "processing_source_sha256": sha256(ACTINV_PROCESSING_SOURCE),
            "linearization_tolerance_declaration": tolerance_declaration,
        },
    }


def njoy_deck(target: dict[str, Any]) -> str:
    mat = target["mat"]
    lines = [
        "moder",
        " 20 -21/",
        "reconr",
        " -21 -22/",
        f" 'ACTINV P17 {target['name']} NJOY2016.79 reference'/",
        f" {mat} 2/",
        f" {RECONSTRUCTION_TOLERANCE:.8g} 0.0 {RECONSTRUCTION_TOLERANCE:.8g}/",
        f" 'FENDL-3.2c {target['name']}'/",
        " 'P17 identical-temperature processing differential'/",
        " 0/",
        "broadr",
        " -21 -22 -23/",
        f" {mat} 1 0 0 0.0/",
        f" {RECONSTRUCTION_TOLERANCE:.8g} 0.0 {RECONSTRUCTION_TOLERANCE:.8g}/",
        f" {TEMPERATURE_K:.8g}/",
        " 0/",
    ]
    if target["unresolved_lssf"] is not None:
        lines.extend(
            [
                "unresr",
                " -21 -23 -24/",
                f" {mat} 1 1 1/",
                f" {TEMPERATURE_K:.8g}/",
                " 1.0e10/",
                " 0/",
                "moder",
                " -24 25/",
            ]
        )
    else:
        lines.extend(["moder", " -23 25/"])
    lines.append("stop")
    return "\n".join(lines) + "\n"


def parse_mf3(path: Path, mat: int, mt: int) -> dict[str, Any]:
    matching = [
        body
        for (section_mat, mf, section_mt), body in sections(path)
        if section_mat == mat and mf == 3 and section_mt == mt
    ]
    if len(matching) != 1:
        raise RuntimeError(f"expected one MAT={mat} MF=3 MT={mt}, found {len(matching)}")
    record, next_index = read_tab1(matching[0], 1)
    if next_index != len(matching[0]):
        raise RuntimeError(f"MAT={mat} MF=3 MT={mt} TAB1 did not consume its section")
    _, _, _, _, _, points, interpolation, energy, sigma = record
    if points != len(energy) or any(law != 2 for _, law in interpolation):
        raise RuntimeError(
            f"unexpected processed MAT={mat} MT={mt} interpolation: {interpolation}"
        )
    return {
        "energy": np.asarray(energy, dtype=float),
        "sigma": np.asarray(sigma, dtype=float),
        "interpolation": interpolation,
    }


def parse_unresr(path: Path, mat: int) -> dict[str, Any]:
    matching = [
        body
        for (section_mat, mf, mt), body in sections(path)
        if section_mat == mat and mf == 2 and mt == 152
    ]
    if len(matching) != 1:
        raise RuntimeError(
            f"expected one MAT={mat} MF=2 MT=152, found {len(matching)}"
        )
    values = fields(matching[0][0])
    lssf = int(values[2])
    record, next_index = read_list(matching[0], 1)
    if next_index != len(matching[0]):
        raise RuntimeError(f"MAT={mat} MF=2 MT=152 LIST did not consume its section")
    temperature, _, columns, dilutions, count, energies, flat = record
    if columns != 5 or dilutions != 1 or count != 1 + 6 * energies:
        raise RuntimeError(
            f"unexpected MAT={mat} UNRESR table shape: "
            f"columns={columns}, dilutions={dilutions}, count={count}, energies={energies}"
        )
    rows = np.asarray(flat[1:], dtype=float).reshape(energies, 6)
    return {
        "lssf": lssf,
        "temperature_K": temperature,
        "dilution_b": flat[0],
        "energy": rows[:, 0],
        "total": rows[:, 1],
        "elastic": rows[:, 2],
        "fission": rows[:, 3],
        "capture": rows[:, 4],
        "transport": rows[:, 5],
    }


def reference_curve(
    pendf: Path, target: dict[str, Any], mt: int, unresolved: dict[str, Any] | None
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    table = parse_mf3(pendf, target["mat"], mt)
    energy = table["energy"]
    sigma = table["sigma"]
    source = "NJOY BROADR MF=3"
    if mt == 102 and unresolved is not None and unresolved["lssf"] == 0:
        low = float(unresolved["energy"][0])
        high = float(unresolved["energy"][-1])
        left = energy <= low
        right = energy >= high
        energy = np.concatenate(
            [energy[left], unresolved["energy"], energy[right]]
        )
        sigma = np.concatenate([sigma[left], unresolved["capture"], sigma[right]])
        source = "NJOY BROADR MF=3 plus infinite-dilution UNRESR MF=2/MT=152"
    if np.any(np.diff(energy) < 0.0) or np.any(~np.isfinite(sigma)):
        raise RuntimeError(f"non-monotone or nonfinite NJOY MAT={target['mat']} MT={mt}")
    return energy, sigma, {
        "source": source,
        "points": int(len(energy)),
        "energy_min_ev": float(energy[0]),
        "energy_max_ev": float(energy[-1]),
    }


def lethargy_integral(
    energy: np.ndarray, sigma: np.ndarray, low: float, high: float
) -> float:
    total = 0.0
    first = max(0, int(np.searchsorted(energy, low, side="right")) - 1)
    last = min(len(energy) - 1, int(np.searchsorted(energy, high, side="left")) + 1)
    for index in range(first, last):
        x1, x2 = energy[index], energy[index + 1]
        if x2 <= low or x1 >= high or x2 <= x1:
            continue
        a = max(low, x1)
        b = min(high, x2)
        if b <= a:
            continue
        slope = (sigma[index + 1] - sigma[index]) / (x2 - x1)
        value_a = sigma[index] + slope * (a - x1)
        ratio_minus_one = (b - a) / a
        logarithm = math.log1p(ratio_minus_one)
        total += value_a * logarithm + slope * a * (ratio_minus_one - logarithm)
    return total


def collapse_reference(
    energy: np.ndarray, sigma: np.ndarray, bounds: np.ndarray
) -> np.ndarray:
    return np.asarray(
        [
            lethargy_integral(energy, sigma, low, high) / math.log(high / low)
            for low, high in zip(bounds[:-1], bounds[1:], strict=True)
        ]
    )


def actinv_library(
    source: Path, output: Path, reactions: list[int]
) -> tuple[np.ndarray, dict[int, np.ndarray], dict[str, Any]]:
    workers = str(min(4, os.cpu_count() or 1))
    run = checked_run(
        [
            ACTINV,
            "build-library",
            source,
            output,
            "--format",
            "tendl",
            "--projectile",
            "neutron",
            "--groups",
            "fispact-709",
            "--temperature-K",
            f"{TEMPERATURE_K:.8g}",
            "--workers",
            workers,
            "--grid-density",
            "1",
        ],
        cwd=ROOT,
    )
    with np.load(output) as library:
        rows = np.asarray(library["rows"])
        sigma = np.asarray(library["sig"])
        bounds = np.asarray(library["bounds"])
    selected = {}
    for mt in reactions:
        matches = np.flatnonzero(
            (rows[:, 0] == 0)
            & (rows[:, 1] == mt)
            & (rows[:, 2] == -1)
            & (rows[:, 3] == -1)
            & (rows[:, 4] == 0)
        )
        if len(matches) != 1:
            raise RuntimeError(f"expected one ACTINV MT={mt} loss row, found {len(matches)}")
        selected[mt] = sigma[matches[0]]
    if len(bounds) != 710 or np.any(np.diff(bounds) <= 0.0):
        raise RuntimeError(f"unexpected ACTINV group structure: {bounds.shape}")
    return bounds, selected, {
        "library_sha256": sha256(output),
        "rows": int(len(rows)),
        "groups": int(len(bounds) - 1),
        "completed": run.returncode == 0,
    }


def group_summary(
    actual: np.ndarray, reference: np.ndarray, bounds: np.ndarray
) -> tuple[dict[str, Any], np.ndarray]:
    peak = float(np.max(np.abs(reference)))
    cutoff = max(1.0e-12, 1.0e-8 * peak)
    keep = np.abs(reference) >= cutoff
    relative = np.abs(actual[keep] - reference[keep]) / np.abs(reference[keep])
    if not len(relative):
        raise RuntimeError("no non-negligible groups survived the frozen cutoff")
    kept_indices = np.flatnonzero(keep)
    worst_local = int(np.argmax(relative))
    worst = int(kept_indices[worst_local])
    summary = {
        "reference_peak_b": peak,
        "non_negligible_cutoff_b": cutoff,
        "groups_total": int(len(reference)),
        "groups_compared": int(np.count_nonzero(keep)),
        "groups_below_cutoff": int(np.count_nonzero(~keep)),
        "relative_p50": float(np.percentile(relative, 50.0)),
        "relative_p90": float(np.percentile(relative, 90.0)),
        "relative_p99": float(np.percentile(relative, 99.0)),
        "maximum_relative": float(relative[worst_local]),
        "worst_group": worst,
        "worst_group_low_ev": float(bounds[worst]),
        "worst_group_high_ev": float(bounds[worst + 1]),
        "worst_actinv_b": float(actual[worst]),
        "worst_njoy_b": float(reference[worst]),
        "pass": bool(np.max(relative) <= GROUP_RELATIVE_TOLERANCE),
    }
    return summary, relative


def spectrum_value(name: str, energy_ev: np.ndarray) -> np.ndarray:
    if name == "thermal":
        kt_ev = 0.0253
        return energy_ev * np.exp(-energy_ev / kt_ev)
    if name == "one_over_e":
        return 1.0 / energy_ev
    energy_mev = energy_ev / 1.0e6
    if name == "fission_like":
        return np.exp(-energy_mev / 0.988) * np.sinh(np.sqrt(2.249 * energy_mev))
    if name == "fusion_like":
        return np.exp(-0.5 * ((energy_mev - 14.1) / 0.25) ** 2)
    raise ValueError(f"unknown spectrum {name}")


def spectrum_weights(name: str, bounds: np.ndarray) -> np.ndarray:
    nodes, quadrature = np.polynomial.legendre.leggauss(24)
    result = np.zeros(len(bounds) - 1)
    for index, (low, high) in enumerate(zip(bounds[:-1], bounds[1:], strict=True)):
        if low >= 20.0e6:
            continue
        clipped_high = min(high, 20.0e6)
        midpoint = 0.5 * (low + clipped_high)
        half_width = 0.5 * (clipped_high - low)
        energies = midpoint + half_width * nodes
        result[index] = half_width * float(
            np.dot(quadrature, spectrum_value(name, energies))
        )
    total = float(np.sum(result))
    if not math.isfinite(total) or total <= 0.0:
        raise RuntimeError(f"spectrum {name} has invalid group-integrated strength {total}")
    return result / total


def one_group_summary(
    actual: np.ndarray,
    reference: np.ndarray,
    weights: dict[str, np.ndarray],
    names: list[str],
) -> list[dict[str, Any]]:
    rows = []
    for name in names:
        actual_value = float(np.dot(actual, weights[name]))
        reference_value = float(np.dot(reference, weights[name]))
        if abs(reference_value) < 1.0e-20:
            raise RuntimeError(f"reference {name} one-group value is numerically zero")
        relative = abs(actual_value - reference_value) / abs(reference_value)
        rows.append(
            {
                "spectrum": name,
                "actinv_b": actual_value,
                "njoy_b": reference_value,
                "relative": relative,
                "pass": relative <= ONE_GROUP_RELATIVE_TOLERANCE,
            }
        )
    return rows


def target_run(
    target: dict[str, Any], work: Path, expected_bounds: np.ndarray | None
) -> tuple[dict[str, Any], np.ndarray, list[dict[str, Any]], np.ndarray]:
    target_work = work / target["name"]
    target_work.mkdir()
    source = FENDL_DIR / target["filename"]
    os.symlink(source, target_work / "tape20")
    deck = njoy_deck(target)
    input_path = target_work / "njoy.inp"
    input_path.write_text(deck)
    njoy_run = checked_run([NJOY], cwd=target_work, input_text=deck)
    if "njoy 2016.79" not in njoy_run.stdout.lower():
        raise RuntimeError(f"{target['name']} fresh run did not identify NJOY2016.79")
    pendf = target_work / "tape25"
    if not pendf.is_file():
        raise RuntimeError(f"{target['name']} NJOY run did not produce tape25")

    unresolved = None
    if target["unresolved_lssf"] is not None:
        unresolved = parse_unresr(pendf, target["mat"])
        if unresolved["lssf"] != target["unresolved_lssf"]:
            raise RuntimeError(
                f"{target['name']} LSSF changed: {unresolved['lssf']}, "
                f"expected {target['unresolved_lssf']}"
            )
        if not math.isclose(unresolved["temperature_K"], TEMPERATURE_K):
            raise RuntimeError(f"{target['name']} UNRESR temperature changed")
        if unresolved["dilution_b"] < 1.0e9:
            raise RuntimeError(f"{target['name']} UNRESR dilution is not infinite-like")

    reactions = [102, target["threshold_mt"]]
    library_path = target_work / f"{target['name'].lower()}.npz"
    bounds, actinv, library = actinv_library(source, library_path, reactions)
    if expected_bounds is not None and not np.array_equal(bounds, expected_bounds):
        raise RuntimeError(f"{target['name']} ACTINV boundaries differ from Fe56")
    weights = {
        name: spectrum_weights(name, bounds)
        for name in ("thermal", "one_over_e", "fission_like", "fusion_like")
    }

    reactions_result = []
    relative_populations = []
    one_group_rows = []
    compact_arrays = []
    for mt, kind in ((102, "capture"), (target["threshold_mt"], "threshold")):
        energy, njoy_sigma, curve = reference_curve(pendf, target, mt, unresolved)
        reference = collapse_reference(energy, njoy_sigma, bounds)
        actual = actinv[mt]
        summary, relatives = group_summary(actual, reference, bounds)
        spectra = (
            ["thermal", "one_over_e", "fission_like", "fusion_like"]
            if kind == "capture"
            else ["fission_like", "fusion_like"]
        )
        one_group = one_group_summary(actual, reference, weights, spectra)
        one_group_rows.extend(
            [{"target": target["name"], "mt": mt, **row} for row in one_group]
        )
        relative_populations.append(relatives)
        compact_arrays.extend([actual, reference])
        reactions_result.append(
            {
                "kind": kind,
                "mt": mt,
                "njoy_curve": curve,
                "group_comparison": summary,
                "one_group": one_group,
                "compact_arrays_sha256": canonical_array_sha256(bounds, actual, reference),
                "pass": bool(summary["pass"] and all(row["pass"] for row in one_group)),
            }
        )

    unresolved_evidence = None
    if unresolved is not None:
        mf3 = parse_mf3(pendf, target["mat"], 102)
        mf3_at_table = np.interp(unresolved["energy"], mf3["energy"], mf3["sigma"])
        relative = np.abs(mf3_at_table - unresolved["capture"]) / np.maximum(
            np.abs(unresolved["capture"]), 1.0e-30
        )
        unresolved_evidence = {
            "lssf": unresolved["lssf"],
            "temperature_K": unresolved["temperature_K"],
            "dilution_b": unresolved["dilution_b"],
            "table_points": int(len(unresolved["energy"])),
            "energy_min_ev": float(unresolved["energy"][0]),
            "energy_max_ev": float(unresolved["energy"][-1]),
            "mf3_vs_unresr_capture_max_relative": float(np.max(relative)),
            "interpretation": (
                "UNRESR replaces MF=3 in the LSSF=0 interval"
                if unresolved["lssf"] == 0
                else "MF=3 is authoritative in the LSSF=1 interval"
            ),
        }

    result = {
        "name": target["name"],
        "mat": target["mat"],
        "za": target["za"],
        "resolved_lrf": target["resolved_lrf"],
        "unresolved": unresolved_evidence,
        "threshold_mt": target["threshold_mt"],
        "njoy": {
            "fresh_run": True,
            "deck_sha256": sha256(input_path),
            "pendf_sha256": sha256(pendf),
            "pendf_bytes": pendf.stat().st_size,
        },
        "actinv": library,
        "reactions": reactions_result,
        "all_compared_arrays_sha256": canonical_array_sha256(bounds, *compact_arrays),
        "pass": all(reaction["pass"] for reaction in reactions_result),
    }
    return result, np.concatenate(relative_populations), one_group_rows, bounds


def global_summary(
    targets: list[dict[str, Any]],
    relatives: np.ndarray,
    one_group_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    group_worst = max(
        (
            (reaction["group_comparison"]["maximum_relative"], target, reaction)
            for target in targets
            for reaction in target["reactions"]
        ),
        key=lambda item: item[0],
    )
    one_group_worst = max(one_group_rows, key=lambda row: row["relative"])
    return {
        "group_relative_p50": float(np.percentile(relatives, 50.0)),
        "group_relative_p90": float(np.percentile(relatives, 90.0)),
        "group_relative_p99": float(np.percentile(relatives, 99.0)),
        "maximum_group_relative": group_worst[0],
        "maximum_group_target": group_worst[1]["name"],
        "maximum_group_mt": group_worst[2]["mt"],
        "maximum_group_index": group_worst[2]["group_comparison"]["worst_group"],
        "maximum_group_low_ev": group_worst[2]["group_comparison"][
            "worst_group_low_ev"
        ],
        "maximum_group_high_ev": group_worst[2]["group_comparison"][
            "worst_group_high_ev"
        ],
        "maximum_one_group_relative": one_group_worst["relative"],
        "maximum_one_group_target": one_group_worst["target"],
        "maximum_one_group_mt": one_group_worst["mt"],
        "maximum_one_group_spectrum": one_group_worst["spectrum"],
        "group_pass": bool(group_worst[0] <= GROUP_RELATIVE_TOLERANCE),
        "one_group_pass": bool(
            one_group_worst["relative"] <= ONE_GROUP_RELATIVE_TOLERANCE
        ),
    }


def main() -> None:
    provenance = checked_inputs()
    root = Path(
        os.environ.get("ACTINV_P17_WORK", tempfile.mkdtemp(prefix="actinv-p17-g3-"))
    )
    work = root / "g3"
    work.mkdir(parents=True, exist_ok=True)

    target_results = []
    relative_populations = []
    one_group_rows = []
    common_bounds = None
    representative_passed = False
    for index, target in enumerate(TARGETS):
        before = time.monotonic()
        result, relatives, rows, bounds = target_run(target, work, common_bounds)
        elapsed = time.monotonic() - before
        if index == 0:
            representative_passed = bool(
                result["pass"] and elapsed <= PROFILE_WALL_CEILING_S
            )
            result["representative_profile"] = {
                "profiled_before_expansion": True,
                "wall_time_ceiling_s": PROFILE_WALL_CEILING_S,
                "completed_within_wall_time_ceiling": elapsed <= PROFILE_WALL_CEILING_S,
                "address_space_ceiling_bytes": ADDRESS_SPACE_LIMIT_BYTES,
                "completed_within_address_space_ceiling": True,
                "authorized_expansion": representative_passed,
            }
            if not representative_passed:
                raise RuntimeError("representative Fe56 profile failed; expansion is unauthorized")
            common_bounds = bounds
        target_results.append(result)
        relative_populations.append(relatives)
        one_group_rows.extend(rows)

    coverage = {
        "targets": len(target_results),
        "capture_reactions": sum(
            reaction["kind"] == "capture"
            for target in target_results
            for reaction in target["reactions"]
        ),
        "threshold_reactions": sum(
            reaction["kind"] == "threshold"
            for target in target_results
            for reaction in target["reactions"]
        ),
        "resolved_lrf": sorted(
            {
                lrf
                for target in target_results
                for lrf in target["resolved_lrf"]
            }
        ),
        "unresolved_lssf": sorted(
            {
                target["unresolved"]["lssf"]
                for target in target_results
                if target["unresolved"] is not None
            }
        ),
        "spectra": ["thermal", "one_over_e", "fission_like", "fusion_like"],
        "representative_profile_passed_before_expansion": representative_passed,
    }
    summary = global_summary(
        target_results, np.concatenate(relative_populations), one_group_rows
    )
    result = {
        "schema": "actinv-p17-g3-processing-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "provenance": provenance,
        "configuration": {
            "temperature_K": TEMPERATURE_K,
            "reconstruction_relative_tolerance": RECONSTRUCTION_TOLERANCE,
            "groups": "FISPACT-709 ascending-energy boundaries",
            "group_weight": "1/E within each group",
            "spectrum_definitions": {
                "thermal": "E * exp(-E / 0.0253 eV)",
                "one_over_e": "1/E",
                "fission_like": "Watt exp(-E/0.988 MeV) sinh(sqrt(2.249 E/MeV))",
                "fusion_like": "Gaussian mean 14.1 MeV, sigma 0.25 MeV",
            },
            "spectrum_energy_max_ev": 20.0e6,
            "non_negligible_cutoff": "max(1e-12 barn, 1e-8 * reference_peak)",
            "group_relative_tolerance": GROUP_RELATIVE_TOLERANCE,
            "one_group_relative_tolerance": ONE_GROUP_RELATIVE_TOLERANCE,
        },
        "coverage": coverage,
        "targets": target_results,
        "summary": summary,
    }
    result["pass"] = bool(
        coverage["targets"] == 6
        and coverage["capture_reactions"] == 6
        and coverage["threshold_reactions"] == 6
        and coverage["resolved_lrf"] == [2, 3, 7]
        and coverage["unresolved_lssf"] == [0, 1]
        and representative_passed
        and all(target["pass"] for target in target_results)
        and summary["group_pass"]
        and summary["one_group_pass"]
    )
    RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "targets": coverage["targets"],
                "resolved_lrf": coverage["resolved_lrf"],
                "unresolved_lssf": coverage["unresolved_lssf"],
                "group_relative_p50": summary["group_relative_p50"],
                "group_relative_p90": summary["group_relative_p90"],
                "group_relative_p99": summary["group_relative_p99"],
                "maximum_group_relative": summary["maximum_group_relative"],
                "maximum_one_group_relative": summary["maximum_one_group_relative"],
                "pass": result["pass"],
            },
            indent=1,
        )
    )
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
