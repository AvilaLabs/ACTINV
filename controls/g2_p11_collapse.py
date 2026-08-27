#!/usr/bin/env python3
"""P11-G2: every MF=33 representation against an independent dense collapse, plus Fe-56."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from p11_covariance import collapse, compare_components, load_activation, parse_mf33, read_sidecar


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g2_p11_collapse.json"
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target" / "release" / "dump"))
FULL_ACTIVATION = Path(
    os.environ.get(
        "ACTINV_P11_FULL_ACTIVATION",
        Path.home() / "nuclear-data" / "tendl-2025" / "builds" / "full" / "neutron.n.p10.npz",
    )
)
FULL_COVARIANCE = Path(
    os.environ.get("ACTINV_P11_FULL_COVARIANCE", ROOT / "target" / "p11-full-v2.cov.npz")
)
FE56 = Path(
    os.environ.get(
        "ACTINV_P11_FE56",
        Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n-working" / "n-Fe056.tendl",
    )
)
NJOY = Path(os.environ.get("ACTINV_P11_NJOY", "/tmp/actinv-njoy2016-79/build/njoy"))
NJOY_SOURCE = Path(os.environ.get("ACTINV_P11_NJOY_SOURCE", "/tmp/actinv-njoy2016-79"))
GROUPS = ROOT / "data" / "fispact_709_groups.json"
EXPECTED = {
    "fe56": "f33f867a4f9c4579a62954fe31dc6e70768ab2424dc8f282a122a93f156d2e1e",
    "errorr": "4fd380f6a8b8c55ea3282bc5aed0e3755bee9361474423981200aa82800b956d",
    "license": "08dc30ca5b19bfa904168f5194b646bb13a661e3591c4e2d000e9a514554b76c",
    "commit": "ac5adf5f33d893e42f2eed7fb286b0d51c7580da",
}


def run(arguments, timeout=180):
    result = subprocess.run(
        [str(value) for value in arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"command failed: {' '.join(map(str, arguments))}\n{result.stderr}")
    return result.stdout


def field(value) -> str:
    return f"{value:11d}" if isinstance(value, int) else f"{float(value):11.4E}"


def record(values, mat, mf, mt, sequence):
    return "".join(field(value) for value in values) + f"{mat:4d}{mf:2d}{mt:3d}{sequence:5d}"


def write_component(path: Path, case: dict) -> None:
    mat, mt, mt1 = 2631, case["mt"], case["mt1"]
    lines = [
        record([26056.0, 55.45, 0, 0, 0, 1], mat, 33, mt, 1),
        record([0.0, 0.0, 0, mt1, 0, 1], mat, 33, mt, 2),
        record([0.0, 0.0, case["l1"], case["lb"], len(case["payload"]), case["n2"]], mat, 33, mt, 3),
    ]
    sequence = 4
    for start in range(0, len(case["payload"]), 6):
        values = list(case["payload"][start : start + 6])
        values.extend([0.0] * (6 - len(values)))
        lines.append(record(values, mat, 33, mt, sequence))
        sequence += 1
    lines.extend([record([0.0] * 6, mat, 33, 0, sequence), record([0.0] * 6, 0, 0, 0, 0)])
    path.write_text("\n".join(lines) + "\n")


def write_sidecar(path: Path, components: list[dict]) -> None:
    kinds = {"Absolute": 0, "Relative": 1, "ShortRange8": 8, "ShortRange9": 9}
    grids: list[list[float]] = []

    def intern(grid):
        for index, known in enumerate(grids):
            if all(np.float64(left).view(np.uint64) == np.float64(right).view(np.uint64) for left, right in zip(grid, known)) and len(grid) == len(known):
                return index
        grids.append(list(grid))
        return len(grids) - 1

    descriptors, values = [], []
    for item in components:
        row_grid, column_grid = intern(item["row_grid"]), intern(item["column_grid"])
        offset = len(values)
        values.extend(item["values"])
        descriptors.append(
            [0, item["mt"], item["mt1"], item["lb"], kinds[item["kind"]], row_grid, column_grid, offset, len(item["values"])]
        )
    offsets, grid_values = [0], []
    for grid in grids:
        grid_values.extend(grid)
        offsets.append(len(grid_values))
    np.savez(
        path,
        components=np.asarray(descriptors, dtype=np.int64),
        grid_offsets=np.asarray(offsets, dtype=np.int64),
        grid_values=np.asarray(grid_values, dtype=np.float64),
        values=np.asarray(values, dtype=np.float64),
    )


def write_activation(path: Path, mt: int, mt1: int, include_lmf10=False) -> list[int]:
    mts = sorted({mt, mt1})
    rows, sigma = [], []
    for offset, reaction in enumerate(mts):
        rows.extend([[0, reaction, -1, -1, 0], [0, reaction, 25056 + offset, 0, 3]])
        sigma.extend([[2.0 + offset, 4.0 + offset], [1.0 + offset, 3.0 + offset]])
    if include_lmf10:
        rows.append([0, mts[0], 25099, 0, 10])
        sigma.append([0.5, 0.75])
    np.savez(
        path,
        rows=np.asarray(rows, dtype=np.int64),
        sig=np.asarray(sigma, dtype=np.float64),
        bounds=np.asarray([1.0, 2.0, 5.0], dtype=np.float64),
    )
    return list(range(len(rows)))


def maximum_error(reference: dict, observed: dict) -> tuple[float, float]:
    absolute = relative = 0.0
    for name in ("one_group_barns", "covariance_barn2"):
        for left, right in zip(reference[name], observed[name]):
            difference = abs(left - right)
            absolute = max(absolute, difference)
            relative = max(relative, difference / max(abs(left), abs(right), 1.0e-300))
    return absolute, relative


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def endf_float(field_value: str) -> float:
    value = field_value.strip()
    if not value:
        return 0.0
    if "e" not in value.lower():
        for index in range(len(value) - 1, 0, -1):
            if value[index] in "+-" and any(character.isdigit() for character in value[:index]):
                value = value[:index] + "e" + value[index:]
                break
    return float(value)


def gendf_fields(line: str) -> list[float]:
    return [endf_float(line[start : start + 11]) for start in range(0, 66, 11)]


def read_errorr_mf33(path: Path) -> tuple[np.ndarray, dict[tuple[int, int], np.ndarray]]:
    """Read absolute ERRORR MF=33 matrices from its formatted GENDF output."""
    lines = path.read_text().splitlines()
    boundaries: np.ndarray | None = None
    matrices: dict[tuple[int, int], np.ndarray] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if len(line) < 75:
            index += 1
            continue
        mf, mt = int(line[70:72] or 0), int(line[72:75] or 0)
        if mf == 1 and mt == 451:
            if index + 1 >= len(lines):
                raise RuntimeError("truncated ERRORR MF=1 group header")
            group_header = gendf_fields(lines[index + 1])
            groups, count = int(group_header[2]), int(group_header[4])
            values: list[float] = []
            index += 2
            while len(values) < count:
                values.extend(gendf_fields(lines[index]))
                index += 1
            boundaries = np.asarray(values[:count], dtype=np.float64)
            if count != groups + 1 or not np.all(np.diff(boundaries) > 0.0):
                raise RuntimeError("invalid ERRORR group-boundary record")
            continue
        if mf != 33 or mt == 0:
            index += 1
            continue
        fields = gendf_fields(line)
        if boundaries is None:
            raise RuntimeError("ERRORR MF=33 appeared before its group structure")
        subsections = int(fields[5])
        index += 1
        for _ in range(subsections):
            subsection = gendf_fields(lines[index])
            mt1, groups = int(subsection[3]), int(subsection[5])
            if groups != len(boundaries) - 1:
                raise RuntimeError(f"ERRORR MT={mt}/MT1={mt1} group count mismatch")
            index += 1
            matrix = np.zeros((groups, groups), dtype=np.float64)
            seen_rows: set[int] = set()
            last_row = 0
            while last_row < groups:
                header = gendf_fields(lines[index])
                first, count, row = int(header[3]), int(header[4]), int(header[5])
                valid_first = (count == 0 and 0 <= first <= groups + 1) or (count > 0 and 1 <= first <= groups)
                if count != int(header[2]) or not valid_first or not (last_row < row <= groups):
                    raise RuntimeError(f"invalid ERRORR LIST header for MT={mt}/MT1={mt1}")
                index += 1
                values: list[float] = []
                while len(values) < count:
                    values.extend(gendf_fields(lines[index]))
                    index += 1
                if (count > 0 and first - 1 + count > groups) or row in seen_rows:
                    raise RuntimeError(f"invalid ERRORR sparse row for MT={mt}/MT1={mt1}")
                if count > 0:
                    matrix[row - 1, first - 1 : first - 1 + count] = values[:count]
                seen_rows.add(row)
                last_row = row
            if groups not in seen_rows or not np.all(np.isfinite(matrix)):
                raise RuntimeError(f"incomplete ERRORR matrix for MT={mt}/MT1={mt1}")
            matrices[(mt, mt1)] = matrix
    if boundaries is None or not matrices:
        raise RuntimeError("ERRORR output contained no MF=33 matrices")
    return boundaries, matrices


def errorr_deck(boundaries: np.ndarray) -> str:
    boundary_lines = [
        " ".join(f"{value:.17E}" for value in boundaries[start : start + 6])
        for start in range(0, len(boundaries), 6)
    ]
    return "\n".join(
        [
            "moder",
            "20 -21 /",
            "reconr",
            "-21 -22 /",
            "'ACTINV P11 Fe-56 covariance control' /",
            "2631 0 0 /",
            "0.001 /",
            "0 /",
            "broadr",
            "-21 -22 -23 /",
            "2631 1 0 0 0 /",
            "0.001 /",
            "293.6 /",
            "0 /",
            "groupr",
            "-21 -23 0 -24 /",
            "2631 1 0 2 1 1 1 0 /",
            "'ACTINV P11 Fe-56 group-constant covariance control' /",
            "293.6 /",
            "1.0E10 /",
            f"{len(boundaries) - 1} /",
            *boundary_lines,
            "3 /",
            "3 102 'capture' /",
            "3 103 'proton production' /",
            "3 107 'alpha production' /",
            "0 /",
            "0 /",
            "errorr",
            "-21 0 -24 34 0 0 /",
            "2631 1 2 0 0 /",
            "0 293.6 /",
            "0 33 0 1 -1 /",
            f"{len(boundaries) - 1} /",
            *boundary_lines,
            "stop",
            "",
        ]
    )


def evaluation_emax(path: Path) -> float:
    records = []
    for line in path.read_text().splitlines():
        if len(line) < 75:
            continue
        mat, mf, mt = int(line[66:70] or 0), int(line[70:72] or 0), int(line[72:75] or 0)
        if mat == 2631 and mf == 1 and mt == 451:
            records.append(line)
        elif records:
            break
    if len(records) < 3:
        raise RuntimeError("Fe-56 evaluation has no complete MF=1/MT=451 header")
    emax = gendf_fields(records[2])[1]
    if not np.isfinite(emax) or emax <= 0.0:
        raise RuntimeError("Fe-56 evaluation declares an invalid maximum energy")
    return emax


def write_selected_mf33_evaluation(source: Path, destination: Path) -> dict:
    """Retain selected MF=33 and remove MF=32 for a bounded P11 ERRORR control."""
    lines = source.read_text().splitlines(keepends=True)
    selected = {102, 103, 107}

    def identifiers(line: str) -> tuple[int, int, int]:
        if len(line) < 75:
            return 0, 0, 0
        return int(line[66:70] or 0), int(line[70:72] or 0), int(line[72:75] or 0)

    mf1_start = next(
        index for index, line in enumerate(lines) if identifiers(line) == (2631, 1, 451)
    )
    header = mf1_start + 3
    nwd, nxc = int(lines[header][44:55]), int(lines[header][55:66])
    directory_start = header + 1 + nwd
    directory = range(directory_start, directory_start + nxc)
    removed_directory = {
        index
        for index in directory
        if int(lines[index][22:33] or 0) == 32
        or (
            int(lines[index][22:33] or 0) == 33
            and int(lines[index][33:44] or 0) not in selected
        )
    }
    if not removed_directory:
        raise RuntimeError("Fe-56 MF=1 directory does not declare MF=32")
    adjusted_header = (
        lines[header][:55] + f"{nxc - len(removed_directory):11d}" + lines[header][66:]
    )

    output = []
    removed_mf32_records = 0
    removed_mf33_records = 0
    awaiting_fend = False
    keep_mf33_section = False
    for index, line in enumerate(lines):
        mat, mf, mt = identifiers(line)
        if mf == 32:
            removed_mf32_records += 1
            awaiting_fend = True
            continue
        if awaiting_fend and mat == 2631 and mf == 0:
            removed_mf32_records += 1
            awaiting_fend = False
            continue
        if mf == 33:
            if mt > 0:
                keep_mf33_section = mt in selected
            if not keep_mf33_section:
                removed_mf33_records += 1
                continue
            if mt == 0:
                keep_mf33_section = False
        if index in removed_directory:
            continue
        output.append(adjusted_header if index == header else line)
    destination.write_text("".join(output))
    retained_mf33 = {
        identifiers(line)[2] for line in output if identifiers(line)[1] == 33 and identifiers(line)[2] > 0
    }
    if (
        removed_mf32_records == 0
        or retained_mf33 != selected
        or any(identifiers(line)[1] == 32 for line in output)
    ):
        raise RuntimeError("failed to produce the selected MF=33-only ERRORR control evaluation")
    return {
        "retained_mf33_reactions": sorted(selected),
        "removed_mf32_records_including_fend": removed_mf32_records,
        "removed_unselected_mf33_records": removed_mf33_records,
        "removed_mf1_directory_entries": len(removed_directory),
        "sha256": sha256(destination),
    }


def njoy_errorr_control(work: Path, spectra: dict[str, np.ndarray]) -> dict:
    required = [FE56, NJOY, NJOY_SOURCE / "src" / "errorr.f90", NJOY_SOURCE / "LICENSE", GROUPS]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        return {"pass": False, "error": "missing pinned input", "missing": missing}
    hashes = {
        "fe56": sha256(FE56),
        "errorr": sha256(NJOY_SOURCE / "src" / "errorr.f90"),
        "license": sha256(NJOY_SOURCE / "LICENSE"),
    }
    commit = run(["git", "-C", NJOY_SOURCE, "rev-parse", "HEAD"]).strip()
    if hashes != {key: EXPECTED[key] for key in hashes} or commit != EXPECTED["commit"]:
        return {"pass": False, "error": "pinned NJOY or Fe-56 input hash mismatch", "hashes": hashes, "commit": commit}

    activation = load_activation(FULL_ACTIVATION)
    boundaries = activation["bounds"]
    published = np.asarray(json.loads(GROUPS.read_text())["boundaries_eV"][::-1], dtype=np.float64)
    if not np.array_equal(boundaries, published):
        return {"pass": False, "error": "activation and pinned 709-group boundaries differ"}
    emax = evaluation_emax(FE56)
    matches = np.flatnonzero(boundaries == emax)
    if len(matches) != 1:
        return {"pass": False, "error": "evaluation maximum is not an exact 709-group boundary", "emax_eV": emax}
    active_groups = int(matches[0])
    active_boundaries = boundaries[: active_groups + 1]
    deck = errorr_deck(active_boundaries)
    deck_path = work / "errorr.inp"
    deck_path.write_text(deck)
    mf33_only = write_selected_mf33_evaluation(FE56, work / "tape20")
    completed = subprocess.run(
        [str(NJOY)],
        cwd=work,
        input=deck,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=1200,
    )
    if completed.returncode:
        raise RuntimeError(f"NJOY ERRORR failed\n{completed.stdout[-4000:]}\n{completed.stderr[-4000:]}")
    if "njoy 2016.79" not in completed.stdout.lower():
        raise RuntimeError("fresh ERRORR run did not identify NJOY2016.79")
    output_path = work / "tape34"
    if not output_path.exists():
        raise RuntimeError("NJOY ERRORR did not publish formatted tape34")
    njoy_bounds, raw_matrices = read_errorr_mf33(output_path)
    if len(njoy_bounds) != len(active_boundaries):
        raise RuntimeError("NJOY ERRORR output group count differs on evaluated CCFE-709 support")
    boundary_relative = float(
        np.max(np.abs(njoy_bounds - active_boundaries) / np.maximum(np.abs(active_boundaries), 1.0e-300))
    )
    # Formatted GENDF retains six significant digits in an 11-column ENDF field.
    if boundary_relative > 5.0e-6:
        raise RuntimeError(
            f"NJOY ERRORR formatted group boundaries differ by {boundary_relative:.3e} relative"
        )

    reactions = (102, 103, 107)
    activation_index = json.loads(FULL_ACTIVATION.with_name(FULL_ACTIVATION.stem + "_index.json").read_text())
    target = next(index for index, value in enumerate(activation_index["targets"]) if value["za"] == 26056 and value["liso"] == 0)
    covariance = read_sidecar(FULL_COVARIANCE, target=target)
    represented = {
        (item["mt"], item["mt1"])
        for item in covariance
        if item["target"] == target and item["mt"] in reactions and item["mt1"] in reactions
    }
    required_pairs = represented | {(right, left) for left, right in represented}
    matrices: dict[tuple[int, int], np.ndarray] = {}
    for pair, matrix in raw_matrices.items():
        if pair[0] in reactions and pair[1] in reactions:
            padded = np.zeros((len(boundaries) - 1, len(boundaries) - 1))
            padded[:active_groups, :active_groups] = matrix
            matrices[pair] = padded
            matrices[(pair[1], pair[0])] = padded.T
    missing_pairs = sorted(pair for pair in required_pairs if pair not in matrices)
    for pair in missing_pairs:
        matrices[pair] = np.zeros((len(boundaries) - 1, len(boundaries) - 1))
    absent_pairs = []
    for left in reactions:
        for right in reactions:
            if (left, right) not in matrices and (left, right) not in required_pairs:
                matrices[(left, right)] = np.zeros((len(boundaries) - 1, len(boundaries) - 1))
                absent_pairs.append((left, right))
    comparisons = {}
    selected = [
        index
        for index, row in enumerate(activation["rows"])
        if int(row[0]) == target and int(row[2]) == -1 and int(row[1]) in reactions
    ]
    selected_reactions = [int(activation["rows"][row, 1]) for row in selected]
    zero_tail = bool(np.all(activation["sig"][selected, active_groups:] == 0.0))
    for name, flux in spectra.items():
        actinv = np.asarray(collapse(covariance, activation, flux, selected)["covariance_barn2"]).reshape(3, 3)
        weights = flux / np.sum(flux)
        njoy = np.asarray(
            [[weights @ matrices[(left, right)] @ weights for right in selected_reactions] for left in selected_reactions]
        )
        difference = np.abs(actinv - njoy)
        scale = np.maximum(np.maximum(np.abs(actinv), np.abs(njoy)), 1.0e-300)
        relative = difference / scale
        compared = (np.abs(actinv) > 1.0e-14) | (np.abs(njoy) > 1.0e-14)
        passing = (difference <= 1.0e-14) | (relative <= 5.0e-3)
        comparisons[name] = {
            "actinv_barn2": actinv.ravel().tolist(),
            "njoy_barn2": njoy.ravel().tolist(),
            "maximum_absolute_barn2": float(np.max(difference)),
            "maximum_relative_compared": float(np.max(relative[compared])) if np.any(compared) else 0.0,
            "pass": bool(np.all(passing)),
        }
    return {
        "njoy_commit": commit,
        "hashes": {
            **hashes,
            "groups": sha256(GROUPS),
            "deck": sha256(deck_path),
            "output": sha256(output_path),
        },
        "mf33_only_control_evaluation": mf33_only,
        "groups": len(boundaries) - 1,
        "errorr_groups_on_evaluated_support": active_groups,
        "evaluation_emax_eV": emax,
        "formatted_boundary_maximum_relative": boundary_relative,
        "activation_selected_rows_zero_above_emax": zero_tail,
        "requested_reactions": list(reactions),
        "reported_pairs": [list(pair) for pair in sorted(raw_matrices) if pair[0] in reactions or pair[1] in reactions],
        "missing_selected_pairs": [list(pair) for pair in missing_pairs],
        "evaluated_absent_pairs_treated_as_zero": [list(pair) for pair in absent_pairs],
        "spectra": comparisons,
        "pass": zero_tail and not missing_pairs and all(item["pass"] for item in comparisons.values()),
    }


CASES = [
    {"name": "lb0", "lb": 0, "l1": 0, "n2": 3, "payload": [1.0, 0.2, 3.0, 0.3, 5.0, 0.0], "mt": 102, "mt1": 102},
    {"name": "lb1", "lb": 1, "l1": 0, "n2": 3, "payload": [1.0, 0.02, 3.0, 0.03, 5.0, 0.0], "mt": 103, "mt1": 103},
    {"name": "lb2", "lb": 2, "l1": 0, "n2": 3, "payload": [1.0, 0.2, 3.0, 0.1, 5.0, 0.0], "mt": 104, "mt1": 104},
    {"name": "lb3", "lb": 3, "l1": 3, "n2": 6, "payload": [1.0, 0.2, 3.0, 0.1, 5.0, 0.0, 1.0, 0.3, 2.0, 0.4, 5.0, 0.0], "mt": 105, "mt1": 106},
    {"name": "lb4", "lb": 4, "l1": 3, "n2": 6, "payload": [1.0, 0.2, 3.0, 0.1, 5.0, 0.0, 1.0, 0.3, 2.0, 0.4, 5.0, 0.0], "mt": 107, "mt1": 107},
    {"name": "lb5_full", "lb": 5, "l1": 0, "n2": 3, "payload": [1.0, 3.0, 5.0, 0.04, 0.01, 0.02, 0.09], "mt": 108, "mt1": 108},
    {"name": "lb5_symmetric", "lb": 5, "l1": 1, "n2": 3, "payload": [1.0, 3.0, 5.0, 0.04, 0.01, 0.09], "mt": 109, "mt1": 109},
    {"name": "lb6", "lb": 6, "l1": 0, "n2": 3, "payload": [1.0, 3.0, 5.0, 1.0, 2.0, 5.0, 0.04, 0.01, 0.02, 0.09], "mt": 110, "mt1": 111},
    {"name": "lb8", "lb": 8, "l1": 0, "n2": 3, "payload": [1.0, 0.4, 3.0, 0.2, 5.0, 0.0], "mt": 112, "mt1": 112},
    {"name": "lb9", "lb": 9, "l1": 0, "n2": 3, "payload": [1.0, 0.4, 3.0, 0.2, 5.0, 0.0], "mt": 113, "mt1": 113},
]


def fixed_spectra(groups: int) -> dict[str, np.ndarray]:
    return {
        "flat": np.ones(groups),
        "ramp": np.linspace(0.0, 2.0, groups),
        "fast_window": np.r_[np.zeros(groups - 100), np.linspace(0.1, 1.0, 100)],
    }


def synthetic_controls(work: Path) -> dict:
    output = {}
    maximum_absolute = maximum_relative = 0.0
    for case_index, case in enumerate(CASES):
        endf = work / f"{case['name']}.endf"
        write_component(endf, case)
        reference_components = parse_mf33(endf)
        rust_components = json.loads(run([DUMP, "covariance", endf]))
        parser = compare_components(reference_components, rust_components)
        components = [dict(reference_components[0], target=0)]
        if case["mt"] != case["mt1"]:
            for reaction in (case["mt"], case["mt1"]):
                components.append(
                    {
                        "target": 0,
                        "mt": reaction,
                        "mt1": reaction,
                        "lb": 5,
                        "kind": "Relative",
                        "row_grid": [1.0, 5.0],
                        "column_grid": [1.0, 5.0],
                        "values": [0.0],
                    }
                )
        sidecar_path = work / f"{case['name']}.npz"
        activation_path = work / f"{case['name']}.activation.npz"
        write_sidecar(sidecar_path, components)
        selected = write_activation(
            activation_path, case["mt"], case["mt1"], include_lmf10=case_index == 0
        )
        flux_path = work / f"{case['name']}.flux.json"
        flux = np.asarray([1.0, 3.0])
        flux_path.write_text(json.dumps(flux.tolist()))
        reference = collapse(read_sidecar(sidecar_path), load_activation(activation_path), flux, selected)
        observed = json.loads(
            run(
                [
                    DUMP,
                    "covariance-collapse",
                    sidecar_path,
                    activation_path,
                    flux_path,
                    ",".join(map(str, selected)),
                ]
            )
        )
        absolute, relative = maximum_error(reference, observed)
        maximum_absolute, maximum_relative = max(maximum_absolute, absolute), max(maximum_relative, relative)
        structural = all(reference[name] == observed[name] for name in ("row_indices", "uncovered_rows", "absent_cross_parameter_pairs"))
        output[case["name"]] = {
            "parser": parser,
            "maximum_absolute_barn2": absolute,
            "maximum_relative": relative,
            "structural_identity": structural,
            "pass": parser["pass"] and structural and (relative <= 2e-12 or absolute <= 1e-16),
        }
    # A separate exact zero-flux plant exercises the early return.
    case = CASES[5]
    sidecar_path = work / f"{case['name']}.npz"
    activation_path = work / f"{case['name']}.activation.npz"
    zero_path = work / "zero-flux.json"
    zero_path.write_text("[0.0, 0.0]")
    selected = list(range(len(load_activation(activation_path)["rows"])))
    zero = json.loads(
        run([DUMP, "covariance-collapse", sidecar_path, activation_path, zero_path, ",".join(map(str, selected))])
    )
    zero_pass = all(value == 0.0 for value in zero["one_group_barns"] + zero["covariance_barn2"])
    return {
        "cases": output,
        "maximum_absolute_barn2": maximum_absolute,
        "maximum_relative": maximum_relative,
        "zero_flux_pass": zero_pass,
        "pass": all(item["pass"] for item in output.values()) and zero_pass,
    }


def real_fe_controls(work: Path) -> dict:
    if not FULL_ACTIVATION.exists() or not FULL_COVARIANCE.exists():
        return {"pass": False, "error": "full activation library or covariance sidecar is absent"}
    activation_index = json.loads(
        FULL_ACTIVATION.with_name(FULL_ACTIVATION.stem + "_index.json").read_text()
    )
    target = next(index for index, value in enumerate(activation_index["targets"]) if value["za"] == 26056 and value["liso"] == 0)
    activation = load_activation(FULL_ACTIVATION)
    selected = [
        index
        for index, row in enumerate(activation["rows"])
        if int(row[0]) == target and int(row[2]) == -1 and int(row[1]) in (102, 103, 107)
    ]
    covariance = read_sidecar(FULL_COVARIANCE, target=target)
    groups = activation["sig"].shape[1]
    spectra = fixed_spectra(groups)
    comparisons = {}
    for name, flux in spectra.items():
        path = work / f"fe-{name}.json"
        path.write_text(json.dumps(flux.tolist()))
        reference = collapse(covariance, activation, flux, selected)
        observed = json.loads(
            run(
                [
                    DUMP,
                    "covariance-collapse",
                    FULL_COVARIANCE,
                    FULL_ACTIVATION,
                    path,
                    ",".join(map(str, selected)),
                ],
                timeout=300,
            )
        )
        absolute, relative = maximum_error(reference, observed)
        comparisons[name] = {
            "maximum_absolute_barn2": absolute,
            "maximum_relative": relative,
            "maximum_asymmetry_barn2": observed["maximum_asymmetry_barn2"],
            "uncovered_rows": observed["uncovered_rows"],
            "pass": relative <= 2e-12 or absolute <= 1e-16,
        }
    return {
        "target": target,
        "selected_rows": selected,
        "spectra": comparisons,
        "source_sha256": activation_index["targets"][target]["source_sha256"],
        "pass": len(selected) == 3 and all(item["pass"] for item in comparisons.values()),
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="actinv-p11-g2-") as directory:
        work = Path(directory)
        synthetic = synthetic_controls(work)
        real = real_fe_controls(work)
        groups = load_activation(FULL_ACTIVATION)["sig"].shape[1] if FULL_ACTIVATION.exists() else 709
        njoy = njoy_errorr_control(work, fixed_spectra(groups))
        output = {
            "synthetic": synthetic,
            "tendl_2025_fe56": real,
            "njoy_errorr": njoy,
        }
        output["pass"] = synthetic["pass"] and real["pass"] and output["njoy_errorr"]["pass"]
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
