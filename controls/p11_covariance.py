#!/usr/bin/env python3
"""Independent P11 ENDF-6 MF=33 parser and dense collapse reference.

This control code does not import ACTINV. It intentionally operates on ENDF records and NumPy
arrays so the production Rust parser/collapse can be compared field-for-field and matrix-for-matrix.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from endf_common import endf_float, fields, read_list, sections


KINDS = {0: "Absolute", 1: "Relative", 8: "ShortRange8", 9: "ShortRange9"}


def _head(line: str) -> tuple[float, float, int, int, int, int]:
    value = fields(line)
    return (
        endf_float(value[0]),
        endf_float(value[1]),
        int(value[2]),
        int(value[3]),
        int(value[4]),
        int(value[5]),
    )


def _grid(values: list[float], context: str) -> list[float]:
    if len(values) < 2 or any(not np.isfinite(x) or x < 0.0 for x in values):
        raise ValueError(f"{context}: invalid covariance grid")
    if any(right <= left for left, right in zip(values, values[1:])):
        raise ValueError(f"{context}: covariance grid is not strictly increasing")
    return values


def _pairs(values: list[float], count: int, context: str) -> tuple[list[float], list[float]]:
    if len(values) != 2 * count:
        raise ValueError(f"{context}: malformed pair table")
    energies = values[0::2]
    factors = values[1::2]
    if factors[-1] != 0.0:
        raise ValueError(f"{context}: final F must be zero")
    return _grid(energies, context), factors[:-1]


def _outer(left: list[float], right: list[float]) -> list[float]:
    return [a * b for a in left for b in right]


def _component(mat: int, mt: int, mt1: int, record) -> dict:
    _, _, l1, lb, nt, ne = record[:6]
    payload = list(record[6])
    context = f"MAT={mat}/MF=33/MT={mt}/MT1={mt1}/LB={lb}"
    if len(payload) != nt or any(not np.isfinite(x) for x in payload):
        raise ValueError(f"{context}: malformed or nonfinite LIST")
    kind = "Relative"
    if 0 <= lb <= 4:
        lt, npairs = l1, ne
        if nt != 2 * npairs or lt < 0 or lt >= npairs:
            raise ValueError(f"{context}: invalid NT/NP/LT")
        if lb <= 2 and lt != 0:
            raise ValueError(f"{context}: LT must be zero")
        if lb >= 3 and lt == 0:
            raise ValueError(f"{context}: second table is absent")
        first_count = npairs - lt
        first_grid, first_factor = _pairs(payload[: 2 * first_count], first_count, context)
        if lb in (0, 1):
            row_grid = column_grid = first_grid
            size = len(first_factor)
            values = [0.0] * (size * size)
            for index, factor in enumerate(first_factor):
                values[index * size + index] = factor
        elif lb == 2:
            row_grid = column_grid = first_grid
            values = _outer(first_factor, first_factor)
        else:
            second_grid, second_factor = _pairs(payload[2 * first_count :], lt, context)
            if lb == 3:
                row_grid, column_grid = first_grid, second_grid
                values = _outer(first_factor, second_factor)
            else:
                row_grid = sorted(set(first_grid + second_grid))
                column_grid = row_grid
                size = len(row_grid) - 1
                values = [0.0] * (size * size)
                for row in range(size):
                    row_energy = row_grid[row] + (row_grid[row + 1] - row_grid[row]) / 2.0
                    coarse_row = np.searchsorted(first_grid, row_energy, side="right") - 1
                    fine_row = np.searchsorted(second_grid, row_energy, side="right") - 1
                    for column in range(size):
                        column_energy = column_grid[column] + (
                            column_grid[column + 1] - column_grid[column]
                        ) / 2.0
                        coarse_column = np.searchsorted(first_grid, column_energy, side="right") - 1
                        fine_column = np.searchsorted(second_grid, column_energy, side="right") - 1
                        inside = (
                            0 <= coarse_row < len(first_factor)
                            and 0 <= fine_row < len(second_factor)
                            and 0 <= coarse_column < len(first_factor)
                            and 0 <= fine_column < len(second_factor)
                        )
                        if inside and coarse_row == coarse_column:
                            values[row * size + column] = (
                                first_factor[coarse_row]
                                * second_factor[fine_row]
                                * second_factor[fine_column]
                            )
        if lb == 0:
            kind = "Absolute"
    elif lb == 5:
        intervals, ls = ne - 1, l1
        if intervals < 1 or ls not in (0, 1):
            raise ValueError(f"{context}: invalid NE/LS")
        matrix_count = intervals * intervals if ls == 0 else ne * intervals // 2
        if nt != ne + matrix_count:
            raise ValueError(f"{context}: invalid packed size")
        row_grid = column_grid = _grid(payload[:ne], context)
        packed = payload[ne:]
        if ls == 0:
            values = packed
        else:
            values = [0.0] * (intervals * intervals)
            cursor = 0
            for row in range(intervals):
                for column in range(row, intervals):
                    values[row * intervals + column] = packed[cursor]
                    values[column * intervals + row] = packed[cursor]
                    cursor += 1
    elif lb == 6:
        ner = ne
        if l1 != 0 or ner < 2 or (nt - 1) % ner:
            raise ValueError(f"{context}: invalid LB=6 dimensions")
        nec = (nt - 1) // ner
        if nec < 2 or ner + nec + (ner - 1) * (nec - 1) != nt:
            raise ValueError(f"{context}: inconsistent LB=6 dimensions")
        row_grid = _grid(payload[:ner], context)
        column_grid = _grid(payload[ner : ner + nec], context)
        values = payload[ner + nec :]
    elif lb in (8, 9):
        if l1 != 0 or nt != 2 * ne or mt != mt1:
            raise ValueError(f"{context}: invalid short-range covariance")
        row_grid, values = _pairs(payload, ne, context)
        column_grid = row_grid
        kind = "ShortRange8" if lb == 8 else "ShortRange9"
    else:
        raise ValueError(f"{context}: unsupported LB")
    return {
        "mat": mat,
        "mt": mt,
        "mt1": mt1,
        "lb": lb,
        "kind": kind,
        "row_grid": row_grid,
        "column_grid": column_grid,
        "values": values,
    }


def parse_mf33(path: Path) -> list[dict]:
    output: list[dict] = []
    for (mat, mf, mt), lines in sections(path):
        if mf != 33:
            continue
        _, _, l1, mtl, n1, nl = _head(lines[0])
        if l1 != 0 or n1 != 0 or mtl != 0:
            raise ValueError(f"MAT={mat}/MF=33/MT={mt}: invalid HEAD")
        cursor = 1
        for subsection in range(nl):
            c1, c2, mat1, mt1, nc, ni = _head(lines[cursor])
            cursor += 1
            context = f"MAT={mat}/MF=33/MT={mt}/subsection={subsection + 1}"
            if c1 != 0.0 or c2 != 0.0 or mat1 not in (0, mat) or mt1 <= 0 or nc != 0:
                raise ValueError(f"{context}: unsupported reference or NC component")
            for _ in range(ni):
                record, cursor = read_list(lines, cursor)
                output.append(_component(mat, mt, mt1, record))
        if cursor != len(lines):
            raise ValueError(f"MAT={mat}/MF=33/MT={mt}: unconsumed records")
    return output


def read_sidecar(path: Path, target: int | None = None) -> list[dict]:
    with np.load(path, allow_pickle=False) as archive:
        descriptors = np.asarray(archive["components"], dtype=np.int64)
        offsets = np.asarray(archive["grid_offsets"], dtype=np.int64)
        grid_values = np.asarray(archive["grid_values"], dtype=np.float64)
        stored_values = np.asarray(archive["values"], dtype=np.float64)
    grids = [grid_values[left:right].tolist() for left, right in zip(offsets, offsets[1:])]
    output = []
    for descriptor in descriptors:
        stored_target, mt, mt1, lb, kind, row_grid, column_grid, offset, length = map(int, descriptor)
        if target is not None and stored_target != target:
            continue
        output.append(
            {
                "target": stored_target,
                "mt": mt,
                "mt1": mt1,
                "lb": lb,
                "kind": KINDS[kind],
                "row_grid": grids[row_grid],
                "column_grid": grids[column_grid],
                "values": stored_values[offset : offset + length].tolist(),
            }
        )
    return output


def ordered_float(value: float) -> int:
    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    return (~bits & ((1 << 64) - 1)) if bits >> 63 else bits | (1 << 63)


def compare_components(reference: list[dict], observed: list[dict]) -> dict:
    if len(reference) != len(observed):
        return {"components": len(reference), "observed": len(observed), "pass": False}
    fields_compared = 0
    maximum_ulp = 0
    structural_mismatches = []
    for index, (left, right) in enumerate(zip(reference, observed)):
        for name in ("mat", "mt", "mt1", "lb", "kind"):
            if name == "mat" and name not in right:
                continue
            if left[name] != right[name]:
                structural_mismatches.append([index, name, left[name], right[name]])
        for name in ("row_grid", "column_grid", "values"):
            if len(left[name]) != len(right[name]):
                structural_mismatches.append([index, name, len(left[name]), len(right[name])])
                continue
            for expected, actual in zip(left[name], right[name]):
                fields_compared += 1
                maximum_ulp = max(maximum_ulp, abs(ordered_float(expected) - ordered_float(actual)))
    return {
        "components": len(reference),
        "fields_compared": fields_compared,
        "maximum_ulp_distance": maximum_ulp,
        "structural_mismatches": structural_mismatches[:20],
        "pass": not structural_mismatches and maximum_ulp == 0,
    }


def load_activation(path: Path) -> dict:
    with np.load(path, allow_pickle=False) as archive:
        return {
            "rows": np.asarray(archive["rows"], dtype=np.int64),
            "sig": np.asarray(archive["sig"], dtype=np.float64),
            "bounds": np.asarray(archive["bounds"], dtype=np.float64),
        }


def collapse(sidecar: list[dict], activation: dict, flux: np.ndarray, selected: list[int]) -> dict:
    rows, sigma, bounds = activation["rows"], activation["sig"], activation["bounds"]
    total_flux = float(np.sum(flux))
    self_covered = {(item["target"], item["mt"]) for item in sidecar if item["mt"] == item["mt1"]}
    covered = [row for row in sorted(set(selected)) if rows[row, 4] != 10 and (rows[row, 0], rows[row, 1]) in self_covered]
    uncovered = [row for row in sorted(set(selected)) if row not in covered]
    base = {(int(row[0]), int(row[1])): index for index, row in enumerate(rows) if row[2] == -1}
    size = len(covered)
    matrix = np.zeros((size, size), dtype=np.float64)
    positions: dict[tuple[int, int], list[int]] = {}
    for parameter, row_index in enumerate(covered):
        row = rows[row_index]
        positions.setdefault((int(row[0]), int(row[1])), []).append(parameter)

    def vector(row_index: int, base_index: int, grid: list[float], relative: bool) -> np.ndarray:
        result = np.zeros(len(grid) - 1)
        if total_flux == 0.0:
            return result
        for group, group_flux in enumerate(flux):
            if group_flux == 0.0:
                continue
            low, high = bounds[group : group + 2]
            multiplier = sigma[row_index, group] if relative else (
                1.0 if row_index == base_index else sigma[row_index, group] / sigma[base_index, group]
            )
            for interval, (left, right) in enumerate(zip(grid, grid[1:])):
                width = max(0.0, min(high, right) - max(low, left))
                result[interval] += group_flux / (high - low) / total_flux * width * multiplier
        return result

    represented = {
        (item["target"], min(item["mt"], item["mt1"]), max(item["mt"], item["mt1"]))
        for item in sidecar
    }
    for item in sidecar:
        left_positions = positions.get((item["target"], item["mt"]), [])
        right_positions = positions.get((item["target"], item["mt1"]), [])
        for left_position in left_positions:
            left_row = covered[left_position]
            left_base = base[(item["target"], item["mt"])]
            for right_position in right_positions:
                right_row = covered[right_position]
                right_base = base[(item["target"], item["mt1"])]
                if item["kind"] in ("Absolute", "Relative"):
                    left = vector(left_row, left_base, item["row_grid"], item["kind"] == "Relative")
                    right = vector(right_row, right_base, item["column_grid"], item["kind"] == "Relative")
                    values = np.asarray(item["values"]).reshape(len(left), len(right))
                    value = float(left @ values @ right)
                else:
                    value = 0.0
                    for group, group_flux in enumerate(flux):
                        if group_flux == 0.0:
                            continue
                        low, high = bounds[group : group + 2]
                        group_width = high - low
                        left_ratio = 1.0 if left_row == left_base else sigma[left_row, group] / sigma[left_base, group]
                        right_ratio = 1.0 if right_row == right_base else sigma[right_row, group] / sigma[right_base, group]
                        for factor, grid_left, grid_right in zip(
                            item["values"], item["row_grid"], item["row_grid"][1:]
                        ):
                            width = max(0.0, min(high, grid_right) - max(low, grid_left))
                            if width == 0.0:
                                continue
                            covariance_width = grid_right - grid_left
                            weight = group_flux * width / group_width / total_flux
                            variance = factor * covariance_width / width if item["kind"] == "ShortRange8" else factor * (1.0 - width / covariance_width)
                            value += weight * weight * left_ratio * right_ratio * variance
                matrix[left_position, right_position] += value
                if item["mt"] != item["mt1"]:
                    matrix[right_position, left_position] += value
    absent = 0
    for left in range(size):
        for right in range(left + 1, size):
            left_row, right_row = rows[covered[left]], rows[covered[right]]
            if left_row[0] != right_row[0] or (
                int(left_row[0]), min(int(left_row[1]), int(right_row[1])), max(int(left_row[1]), int(right_row[1]))
            ) not in represented:
                absent += 1
    return {
        "row_indices": covered,
        "one_group_barns": [float(np.dot(sigma[row], flux) / total_flux) if total_flux else 0.0 for row in covered],
        "covariance_barn2": matrix.ravel().tolist(),
        "uncovered_rows": uncovered,
        "absent_cross_parameter_pairs": absent,
        "maximum_asymmetry_barn2": float(np.max(np.abs(matrix - matrix.T))) if size else 0.0,
    }
