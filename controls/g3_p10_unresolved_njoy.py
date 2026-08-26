#!/usr/bin/env python3
"""P10 G3 actual-data control: Ag-107 infinite-dilution unresolved averages vs NJOY2016.79."""
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

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from endf_common import endf_float, fields, read_list, read_tab1, sections


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g3_p10_unresolved_njoy.json"
ENDF = Path(
    os.environ.get(
        "ACTINV_P10_AG107_ENDF",
        "~/nuclear-data/fendl-3.2c/endf/n_4725_47-Ag-107.endf",
    )
).expanduser()
NJOY = Path(
    os.environ.get("ACTINV_P10_NJOY", "/tmp/actinv-njoy2016-79/build/njoy")
).expanduser()
NJOY_SOURCE = Path(
    os.environ.get("ACTINV_P10_NJOY_SOURCE", "/tmp/actinv-njoy2016-79")
).expanduser()
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target/release/dump"))

EXPECTED = {
    "ag107_endf": "0610e15630cb0837a801611d42b6cd401435ddb93dde1126e63000b83ba14185",
    "njoy_commit": "ac5adf5f33d893e42f2eed7fb286b0d51c7580da",
    "reconr": "054ede7a59e1c39cf3e72105d8a0b95a0fb1d8df0882eca6b949e765b62bf5db",
    "unresr": "57a3a975566d45a8f2d0db67fed121b908e50039d9aafb25ea27f628c745d650",
    "license": "08dc30ca5b19bfa904168f5194b646bb13a661e3591c4e2d000e9a514554b76c",
}

NJOY_INPUT = """moder
 20 -21/
reconr
 -21 -22/
 'ACTINV P10 Ag-107 NJOY2016.79 infinite-dilution reference'/
 4725 2/
 0.001 0.0 0.002/
 '47-Ag-107 FENDL-3.2c'/
 'Processed by pinned NJOY2016.79'/
 0/
broadr
 -21 -22 -23/
 4725 1 0 0 0.0/
 0.001 0.0 0.002/
 293.6/
 0/
unresr
 -21 -23 -24/
 4725 1 1 1/
 293.6/
 1.0e10/
 0/
moder
 -24 25/
stop
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def limit_address_space() -> None:
    limit = 2 * 1024**3
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def checked_run(arguments: list[str], *, cwd: Path, stdin=None) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        stdin=stdin,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=limit_address_space,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def cont(line: str) -> tuple[float, float, int, int, int, int]:
    values = fields(line)
    return (
        endf_float(values[0]),
        endf_float(values[1]),
        int(values[2]),
        int(values[3]),
        int(values[4]),
        int(values[5]),
    )


def parse_case_c(path: Path) -> dict:
    lines = next(
        body for (_, mf, mt), body in sections(path) if mf == 2 and mt == 151
    )
    _, _, _, _, nis, _ = cont(lines[0])
    index = 1
    unresolved = None
    for _ in range(nis):
        zai, abundance, _, lfw, ner, _ = cont(lines[index])
        index += 1
        for _ in range(ner):
            low, high, lru, lrf, nro, naps = cont(lines[index])
            index += 1
            if nro:
                _, index = read_tab1(lines, index)
            if lru == 1 and lrf in (1, 2, 3):
                _, _, _, _, nls, _ = cont(lines[index])
                index += 1
                for _ in range(nls):
                    _, index = read_list(lines, index)
                continue
            if (lru, lrf) != (2, 2):
                raise ValueError(f"unsupported independent-control range LRU={lru}/LRF={lrf}")
            spin, ap, lssf, _, nls, _ = cont(lines[index])
            index += 1
            sequences = []
            for _ in range(nls):
                awri, _, l_value, _, njs, _ = cont(lines[index])
                index += 1
                for _ in range(njs):
                    (aj, _, interpolation, _, npl, ne, values), index = read_list(
                        lines, index
                    )
                    if npl != 6 * (ne + 1):
                        raise ValueError("case-C LIST count mismatch")
                    dof = values[:6]
                    points = [values[offset : offset + 6] for offset in range(6, npl, 6)]
                    sequences.append(
                        {
                            "awri": awri,
                            "l": l_value,
                            "spin": aj,
                            "interpolation": interpolation,
                            "competitive_dof": int(dof[2]),
                            "neutron_dof": int(dof[3]),
                            "fission_dof": int(dof[5]),
                            "points": points,
                        }
                    )
            unresolved = {
                "zai": int(zai),
                "abundance": abundance,
                "lfw": lfw,
                "energy_min_ev": low,
                "energy_max_ev": high,
                "naps": naps,
                "spin": spin,
                "ap": ap,
                "lssf": lssf,
                "sequences": sequences,
            }
    if unresolved is None:
        raise ValueError("Ag-107 evaluation has no case-C unresolved range")
    return unresolved


def unresr_table(path: Path) -> dict[str, np.ndarray | float]:
    lines = next(
        body for (_, mf, mt), body in sections(path) if mf == 2 and mt == 152
    )
    (temperature, _, columns, dilutions, count, energies, values), next_index = (
        read_list(lines, 1)
    )
    if next_index != len(lines):
        raise ValueError("MF=2/MT=152 LIST did not consume its section")
    if columns != 5 or dilutions != 1 or count != 1 + 6 * energies:
        raise ValueError(
            f"unexpected UNRESR table shape: columns={columns}, "
            f"dilutions={dilutions}, count={count}, energies={energies}"
        )
    rows = np.asarray(values[1:], dtype=float).reshape(energies, 6)
    return {
        "temperature_K": temperature,
        "dilution_b": values[0],
        "energy": rows[:, 0],
        "total": rows[:, 1],
        "elastic": rows[:, 2],
        "fission": rows[:, 3],
        "capture": rows[:, 4],
        "transport": rows[:, 5],
    }


def rust_points(mt: int, energies: np.ndarray) -> np.ndarray:
    command = [
        str(DUMP),
        "processed-xs",
        str(ENDF),
        str(mt),
        "293.6",
        *(f"{energy:.17e}" for energy in energies),
    ]
    completed = checked_run(command, cwd=ROOT)
    values = [float(line.split()[2]) for line in completed.stdout.splitlines() if line.startswith("X ")]
    if len(values) != len(energies):
        raise ValueError(f"Rust MT={mt} returned {len(values)} points, expected {len(energies)}")
    return np.asarray(values)


def rust_resonance_points(energies: np.ndarray) -> np.ndarray:
    completed = checked_run(
        [
            str(DUMP),
            "resonance-xs",
            str(ENDF),
            *(f"{energy:.17e}" for energy in energies),
        ],
        cwd=ROOT,
    )
    values = [
        [float(value) for value in line.split()[2:6]]
        for line in completed.stdout.splitlines()
        if line.startswith("X ")
    ]
    if len(values) != len(energies):
        raise ValueError(
            f"Rust resonance control returned {len(values)} points, expected {len(energies)}"
        )
    return np.asarray(values)


def relative_summary(
    actual: np.ndarray,
    reference: np.ndarray,
    threshold: float,
    coordinates: np.ndarray | None = None,
) -> dict:
    keep = np.maximum(np.abs(actual), np.abs(reference)) >= threshold
    if not np.any(keep):
        return {"compared": 0, "max_relative": 0.0}
    relative = np.abs(actual[keep] - reference[keep]) / np.maximum(
        np.abs(actual[keep]), np.abs(reference[keep])
    )
    worst = int(np.argmax(relative))
    kept_indices = np.flatnonzero(keep)
    summary = {
        "compared": int(keep.sum()),
        "max_relative": float(relative[worst]),
        "worst_input_index": int(kept_indices[worst]),
        "actual": float(actual[kept_indices[worst]]),
        "reference": float(reference[kept_indices[worst]]),
    }
    if coordinates is not None:
        summary["worst_energy_ev"] = float(coordinates[kept_indices[worst]])
    return summary


def lethargy_integral(x: np.ndarray, y: np.ndarray, low: float, high: float) -> float:
    total = 0.0
    first = max(0, int(np.searchsorted(x, low, side="right")) - 1)
    last = min(len(x) - 1, int(np.searchsorted(x, high, side="left")) + 1)
    for index in range(first, last):
        x1, x2 = x[index], x[index + 1]
        if x2 <= low or x1 >= high or x2 <= x1:
            continue
        a, b = max(low, x1), min(high, x2)
        if b <= a:
            continue
        slope = (y[index + 1] - y[index]) / (x2 - x1)
        value_a = y[index] + slope * (a - x1)
        ratio_minus_one = (b - a) / a
        log_ratio = math.log1p(ratio_minus_one)
        total += value_a * log_ratio + slope * a * (ratio_minus_one - log_ratio)
    return total


def group_capture(reference_table: dict, library_path: Path, low: float, high: float) -> dict:
    x = reference_table["energy"]
    y = reference_table["capture"]
    with np.load(library_path) as library:
        rows = np.asarray(library["rows"])
        sigma = np.asarray(library["sig"])
        bounds = np.asarray(library["bounds"])
    matches = np.flatnonzero(
        (rows[:, 0] == 0)
        & (rows[:, 1] == 102)
        & (rows[:, 2] == -1)
        & (rows[:, 3] == -1)
        & (rows[:, 4] == 0)
    )
    if len(matches) != 1:
        raise ValueError(f"expected one Ag-107 MT102 loss row, found {len(matches)}")
    actual = sigma[matches[0]]
    groups = np.flatnonzero((bounds[:-1] >= low) & (bounds[1:] <= high))
    reference = np.asarray(
        [
            lethargy_integral(x, y, bounds[group], bounds[group + 1])
            / math.log(bounds[group + 1] / bounds[group])
            for group in groups
        ]
    )
    summary = relative_summary(actual[groups], reference, 1e-8)
    summary["groups"] = int(len(groups))
    summary["worst_group"] = int(groups[summary.pop("worst_input_index")])
    return summary


def main() -> None:
    for path in (ENDF, NJOY, NJOY_SOURCE, ACTINV, DUMP):
        if not path.exists():
            raise SystemExit(f"missing P10 G3 input: {path}")
    hashes = {
        "ag107_endf": sha256(ENDF),
        "reconr": sha256(NJOY_SOURCE / "src/reconr.f90"),
        "unresr": sha256(NJOY_SOURCE / "src/unresr.f90"),
        "license": sha256(NJOY_SOURCE / "LICENSE"),
    }
    commit = subprocess.run(
        ["git", "-C", str(NJOY_SOURCE), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if hashes != {key: EXPECTED[key] for key in hashes} or commit != EXPECTED["njoy_commit"]:
        raise SystemExit(f"P10 G3 source pin mismatch: hashes={hashes}, commit={commit}")

    unresolved = parse_case_c(ENDF)
    parameter_energies = sorted(
        {point[0] for sequence in unresolved["sequences"] for point in sequence["points"]}
    )
    interior = parameter_energies[1:-1]
    one_sided = np.asarray(
        [side for energy in interior for side in (math.nextafter(energy, -math.inf), math.nextafter(energy, math.inf))]
    )
    maximum_competitive_width = max(
        point[2] for sequence in unresolved["sequences"] for point in sequence["points"]
    )
    maximum_fission_width = max(
        point[5] for sequence in unresolved["sequences"] for point in sequence["points"]
    )

    with tempfile.TemporaryDirectory(prefix="actinv-p10-g3-", dir="/tmp") as temporary:
        work = Path(temporary)
        os.symlink(ENDF, work / "tape20")
        input_path = work / "input"
        input_path.write_text(NJOY_INPUT)
        with input_path.open() as stdin:
            njoy_run = checked_run([str(NJOY)], cwd=work, stdin=stdin)
        if "njoy 2016.79" not in njoy_run.stdout.lower():
            raise RuntimeError("fresh reference did not identify as NJOY 2016.79")
        pendf = work / "tape25"
        if not pendf.exists():
            raise RuntimeError("NJOY did not produce the requested PENDF tape25")

        library_path = work / "ag107.npz"
        workers = str(min(8, os.cpu_count() or 1))
        checked_run(
            [
                str(ACTINV),
                "build-library",
                str(ENDF),
                str(library_path),
                "--format",
                "tendl",
                "--projectile",
                "neutron",
                "--groups",
                "fispact-709",
                "--temperature-K",
                "293.6",
                "--workers",
                workers,
                "--grid-density",
                "1",
            ],
            cwd=ROOT,
        )

        pointwise = {}
        reference_table = unresr_table(pendf)
        for mt, channel in ((2, "elastic"), (102, "capture")):
            rust = rust_points(mt, one_sided)
            reference = np.interp(
                one_sided, reference_table["energy"], reference_table[channel]
            )
            pointwise[str(mt)] = relative_summary(
                rust, reference, 1e-8, one_sided
            )
        direct = rust_resonance_points(one_sided)
        direct_channels = {
            channel: {
                "minimum_b": float(np.min(direct[:, index])),
                "maximum_b": float(np.max(direct[:, index])),
            }
            for index, channel in enumerate(
                ("elastic", "capture", "fission", "competitive")
            )
        }
        capture_groups = group_capture(
            reference_table,
            library_path,
            unresolved["energy_min_ev"],
            unresolved["energy_max_ev"],
        )
        generated = {
            "njoy_input_sha256": sha256(input_path),
            "njoy_pendf_sha256": sha256(pendf),
            "rust_library_sha256": sha256(library_path),
            "rust_index_sha256": sha256(work / "ag107_index.json"),
        }

    direct_is_finite_and_nonnegative = bool(
        np.all(np.isfinite(direct)) and np.all(direct >= 0.0)
    )
    actual_reference_pass = bool(
        all(value["max_relative"] <= 2e-4 for value in pointwise.values())
        and capture_groups["max_relative"] <= 5e-4
        and math.isclose(reference_table["temperature_K"], 293.6)
        and reference_table["dilution_b"] >= 1e9
        and direct_is_finite_and_nonnegative
        and direct_channels["fission"]["maximum_b"] == 0.0
        and direct_channels["competitive"]["maximum_b"] > 0.0
    )
    output = {
        "schema": "actinv-p10-g3-unresolved-njoy-1",
        "inputs": {**hashes, "njoy_commit": commit},
        "generated": generated,
        "njoy": {
            "version": "2016.79",
            "fresh_run": True,
            "temperature_K": reference_table["temperature_K"],
            "dilution_b": reference_table["dilution_b"],
            "table_points": len(reference_table["energy"]),
            "maximum_fission_b": float(np.max(reference_table["fission"])),
            "mt152_channels": ["total", "elastic", "fission", "capture", "transport"],
            "competitive_reference": "separate independent high-order quadrature control",
        },
        "unresolved": {
            "case": "C",
            "lssf": unresolved["lssf"],
            "energy_min_ev": unresolved["energy_min_ev"],
            "energy_max_ev": unresolved["energy_max_ev"],
            "sequences": len(unresolved["sequences"]),
            "points": sum(len(sequence["points"]) for sequence in unresolved["sequences"]),
            "unique_parameter_energies": len(parameter_energies),
            "one_sided_interior_points": len(one_sided),
            "maximum_competitive_width_ev": maximum_competitive_width,
            "maximum_fission_width_ev": maximum_fission_width,
        },
        "rust_resonance_channels": direct_channels,
        "pointwise": pointwise,
        "capture_groups": capture_groups,
        "actual_reference_pass": actual_reference_pass,
        "pass": actual_reference_pass,
    }
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if actual_reference_pass else 2)


if __name__ == "__main__":
    main()
