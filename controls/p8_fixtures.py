#!/usr/bin/env python3
"""Independent deterministic fixtures and helpers for the P8 controls."""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
PROBE = Path(os.environ.get("ACTINV_FLUX_PROBE", ROOT / "target" / "release" / "flux_probe"))
SOURCE_RATE = 1024.0
BOUNDARIES_EV = [0.1, 1.0, 10.0, 100.0, 1000.0]
SOURCE_BOUNDARIES_EV = [0.0, 1.0, 10.0, 100.0, 1000.0]
PHYSICAL = np.asarray(
    [
        [1.0, 2.0, 3.0, 4.0],
        [5.0, 7.0, 11.0, 13.0],
        [17.0, 19.0, 23.0, 29.0],
        [31.0, 37.0, 41.0, 43.0],
    ],
    dtype=np.float64,
)
RELERR = np.arange(1, 17, dtype=np.float64).reshape((4, 4)) / 32.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def command(arguments: list[str | Path], *, ok: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(item) for item in arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if ok and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(map(str, arguments))}\n{result.stdout}{result.stderr}"
        )
    if not ok and result.returncode == 0:
        raise RuntimeError(f"command unexpectedly succeeded: {' '.join(map(str, arguments))}")
    return result


def read_ndjson(path: Path) -> tuple[dict, list[dict], dict]:
    records = [json.loads(line) for line in path.read_text().splitlines()]
    if len(records) < 3 or records[0].get("record") != "header" or records[-1].get("record") != "footer":
        raise AssertionError(f"{path} is not a closed canonical stream")
    cells = records[1:-1]
    if any(cell.get("record") != "cell" or cell.get("ordinal") != i for i, cell in enumerate(cells)):
        raise AssertionError(f"{path} has unordered cell records")
    if len(cells) != records[0]["cell_count"] or len(cells) != records[-1]["cell_count"]:
        raise AssertionError(f"{path} cell counts do not close")
    total = math.fsum(cell["flux_total"] for cell in cells)
    if not math.isclose(total, records[-1]["flux_sum_over_cells"], rel_tol=1e-12):
        raise AssertionError(f"{path} flux footer does not close")
    return records[0], cells, records[-1]


def _mesh(order: tuple[str, str], rectilinear: bool) -> tuple[np.ndarray, list[np.ndarray], str]:
    if rectilinear:
        grids = [
            np.asarray([0.0, 1.0, 3.0]),
            np.asarray([0.0, 2.0, 5.0]),
            np.asarray([0.0, 4.0]),
        ]
        kind = "rectilinear"
    else:
        grids = [
            np.asarray([0.0, 1.0, 2.0]),
            np.asarray([0.0, 1.0, 2.0]),
            np.asarray([0.0, 1.0]),
        ]
        kind = "regular"
    volumes = []
    for k in range(1):
        for j in range(2):
            for i in range(2):
                volumes.append(
                    (grids[0][i + 1] - grids[0][i])
                    * (grids[1][j + 1] - grids[1][j])
                    * (grids[2][k + 1] - grids[2][k])
                )
    return np.asarray(volumes), grids, kind


def make_openmc(
    path: Path,
    *,
    order: tuple[str, str] = ("mesh", "energy"),
    rectilinear: bool = False,
    score: str = "flux",
    nuclide: str = "total",
    version: tuple[int, int] = (18, 2),
    extra_filter: bool = False,
    mesh_type: str | None = None,
    padding_mb: int = 0,
) -> None:
    volumes, grids, kind = _mesh(order, rectilinear)
    means = PHYSICAL * volumes[:, None] / SOURCE_RATE
    realizations = 8.0
    sums = means * realizations
    sum_sq = realizations * (means**2 + (means * RELERR) ** 2 * (realizations - 1.0))
    rows = []
    if order == ("mesh", "energy"):
        for cell in range(4):
            for group in range(4):
                rows.append((sums[cell, group], sum_sq[cell, group]))
        filter_ids = [11, 12]
    elif order == ("energy", "mesh"):
        for group in range(4):
            for cell in range(4):
                rows.append((sums[cell, group], sum_sq[cell, group]))
        filter_ids = [12, 11]
    else:
        raise ValueError(order)
    if extra_filter:
        filter_ids.append(13)

    with h5py.File(path, "w", libver="latest") as handle:
        handle.attrs["filetype"] = np.bytes_("statepoint")
        handle.attrs["version"] = np.asarray(version, dtype=np.int32)
        handle.attrs["openmc_version"] = np.asarray([0, 15, 3], dtype=np.int32)
        handle.attrs["tallies_present"] = np.int32(1)
        tallies = handle.create_group("tallies")
        tallies.attrs["n_tallies"] = np.int32(1)
        tallies.attrs["ids"] = np.asarray([7], dtype=np.int32)
        meshes = tallies.create_group("meshes")
        meshes.attrs["n_meshes"] = np.int32(1)
        meshes.attrs["ids"] = np.asarray([5], dtype=np.int32)
        mesh = meshes.create_group("mesh 5")
        mesh.attrs["id"] = np.int32(5)
        mesh["dimension"] = np.asarray([2, 2, 1], dtype=np.int32)
        selected_mesh_type = mesh_type or kind
        mesh["type"] = np.bytes_(selected_mesh_type)
        if selected_mesh_type == "rectilinear":
            mesh["x_grid"], mesh["y_grid"], mesh["z_grid"] = grids
        elif selected_mesh_type == "regular":
            lower = np.asarray([grid[0] for grid in grids])
            upper = np.asarray([grid[-1] for grid in grids])
            mesh["lower_left"] = lower
            mesh["upper_right"] = upper
            mesh["width"] = (upper - lower) / np.asarray([2.0, 2.0, 1.0])

        filters = tallies.create_group("filters")
        filters.attrs["n_filters"] = np.int32(len(filter_ids))
        filters.attrs["ids"] = np.asarray(filter_ids, dtype=np.int32)
        mesh_filter = filters.create_group("filter 11")
        mesh_filter["type"] = np.bytes_("mesh")
        mesh_filter["n_bins"] = np.int32(4)
        mesh_filter["bins"] = np.int32(5)
        energy_filter = filters.create_group("filter 12")
        energy_filter["type"] = np.bytes_("energy")
        energy_filter["n_bins"] = np.int32(4)
        energy_filter["bins"] = np.asarray(SOURCE_BOUNDARIES_EV)
        if extra_filter:
            other = filters.create_group("filter 13")
            other["type"] = np.bytes_("cell")
            other["n_bins"] = np.int32(1)
            other["bins"] = np.asarray([99], dtype=np.int32)

        tally = tallies.create_group("tally 7")
        tally.attrs["internal"] = np.int32(0)
        tally["n_realizations"] = np.int32(realizations)
        tally["estimator"] = np.bytes_("tracklength")
        tally["n_filters"] = np.int32(len(filter_ids))
        tally["filters"] = np.asarray(filter_ids, dtype=np.int32)
        tally["nuclides"] = np.asarray([nuclide.encode()], dtype=f"S{len(nuclide)}")
        tally["n_score_bins"] = np.int32(1)
        tally["score_bins"] = np.asarray([score.encode()], dtype=f"S{len(score)}")
        tally.create_dataset(
            "results",
            data=np.asarray(rows).reshape((16, 1, 2)),
            chunks=(3, 1, 2),
        )
        if padding_mb:
            size = padding_mb * 1024 * 1024
            padding = handle.create_dataset("unused_padding", shape=(size,), dtype="u1", chunks=(1024 * 1024,))
            block = np.arange(1024 * 1024, dtype=np.uint8)
            for start in range(0, size, len(block)):
                padding[start : start + len(block)] = block


def make_meshtal(path: Path, *, bad_total: bool = False, multiplier: bool = False, truncate: bool = False) -> None:
    lines = [
        " Mesh Tally Number        24",
        " neutron mesh tally.",
        "",
        " Tally bin boundaries:",
        "    X direction: 0.0 1.0 2.0",
        "    Y direction: 0.0 1.0 2.0",
        "    Z direction: 0.0 1.0",
        "    Energy bin boundaries: 0.0 1.0E-6 1.0E-5 1.0E-4 1.0E-3",
    ]
    if multiplier:
        lines.append(" Dose response function / tally multiplier")
    lines.extend(["", "   Energy X Y Z Result Rel Error"])
    for group in range(4):
        upper_mev = SOURCE_BOUNDARIES_EV[group + 1] * 1e-6
        for i in range(2):
            for j in range(2):
                cell = i + 2 * j
                lines.append(
                    f" {upper_mev:.16E} {i + 0.5:.1f} {j + 0.5:.1f} 0.5 "
                    f"{PHYSICAL[cell, group] / SOURCE_RATE:.17E} {RELERR[cell, group]:.17E}"
                )
    for i in range(2):
        for j in range(2):
            cell = i + 2 * j
            total = math.fsum(PHYSICAL[cell]) / SOURCE_RATE
            if bad_total and cell == 2:
                total += 1.0 / SOURCE_RATE
            lines.append(
                f" Total {i + 0.5:.1f} {j + 0.5:.1f} 0.5 {total:.17E} {RELERR[cell, 0]:.17E}"
            )
    if truncate:
        lines.pop()
    path.write_text("\n".join(lines) + "\n")


def make_mctal(
    path: Path,
    *,
    tally_id: int = 4,
    bad_total: bool = False,
    extra_dimension: bool = False,
    truncate: bool = False,
) -> None:
    lines = [
        f"tally {tally_id} -1 0 0",
        " 1 0 0",
        "f 4",
        " 1 2 3 4",
        "d 1",
        "u 2" if extra_dimension else "u 0",
        "s 0",
        "m 0",
        "c 0",
        "et 5",
        " 1.0E-6 1.0E-5 1.0E-4 1.0E-3",
        "t 0",
        "vals",
    ]
    values = []
    for cell in range(4):
        for group in range(4):
            values.extend([PHYSICAL[cell, group] / SOURCE_RATE, RELERR[cell, group]])
        total = math.fsum(PHYSICAL[cell]) / SOURCE_RATE
        if bad_total and cell == 1:
            total += 1.0 / SOURCE_RATE
        values.extend([total, RELERR[cell, 0]])
    if truncate:
        values.pop()
    for start in range(0, len(values), 8):
        lines.append(" " + " ".join(f"{value:.17E}" for value in values[start : start + 8]))
    lines.append("tfc 0 1 1 1 1 1 1 3 1")
    path.write_text("\n".join(lines) + "\n")


def make_fispact(path: Path, groups_path: Path) -> None:
    write_json(groups_path, {"boundaries_eV": list(reversed(BOUNDARIES_EV))})
    path.write_text(
        " ".join(f"{value:.17E}" for value in reversed(PHYSICAL[0]))
        + "\n0.0\nACTINV P8 deterministic identity spectrum\n"
    )


def make_all(directory: Path, *, padding_mb: int = 0) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "openmc_mesh": directory / "openmc_mesh_first.h5",
        "openmc_energy": directory / "openmc_energy_first.h5",
        "meshtal": directory / "meshtal.txt",
        "mctal": directory / "mctal.txt",
        "fispact": directory / "fluxes",
        "groups": directory / "groups.json",
    }
    make_openmc(paths["openmc_mesh"], padding_mb=padding_mb)
    make_openmc(paths["openmc_energy"], order=("energy", "mesh"), rectilinear=True)
    make_meshtal(paths["meshtal"])
    make_mctal(paths["mctal"])
    make_fispact(paths["fispact"], paths["groups"])
    return paths


def import_arguments(kind: str, source: Path, output: Path, groups: Path | None = None) -> list[str | Path]:
    base: list[str | Path] = [BIN, "import-flux", kind, source, output]
    if kind == "openmc":
        return base + ["--tally", "7", "--source-rate", str(SOURCE_RATE), "--energy-floor-eV", "0.1", "--window-rows", "5"]
    if kind == "meshtal":
        return base + ["--tally", "24", "--source-rate", str(SOURCE_RATE), "--energy-floor-eV", "0.1"]
    if kind == "mctal":
        return base + ["--tally", "4", "--source-rate", str(SOURCE_RATE), "--energy-floor-eV", "0.1"]
    if kind == "fispact":
        if groups is None:
            raise ValueError("FISPACT import needs group boundaries")
        return base + ["--groups", groups]
    raise ValueError(kind)


def ensure_ci_library(work: Path) -> tuple[Path, Path]:
    data = Path(os.environ.get("ACTINV_CI_DATA", Path.home() / "actinv-ci-data"))
    explicit = os.environ.get("ACTINV_CI_OUT")
    if explicit:
        output = Path(explicit)
    elif (Path("/tmp/p7-ci") / "ci_fe.npz").exists():
        output = Path("/tmp/p7-ci")
    else:
        output = work / "ci-library"
    output.mkdir(parents=True, exist_ok=True)
    library = output / "ci_fe.npz"
    if not library.exists():
        command(
            [
                os.environ.get("PYTHON", "python3"),
                ROOT / "controls" / "tendl_build.py",
                data / "tendl",
                output,
                "--workers",
                "4",
                "--dense",
                "1",
                "--name",
                "ci_fe",
            ]
        )
    decay = data / "decay" / "endf-b-viii-0_decay.dat"
    if not decay.exists():
        raise FileNotFoundError(f"P8 controls need the pinned CI decay file at {decay}")
    return library, decay
