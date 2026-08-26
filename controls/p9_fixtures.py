#!/usr/bin/env python3
"""Deterministic fixtures and shared helpers for the P9 controls.

The generated nuclear data are intentionally tiny. They are not physics data: they make every
matrix entry analytically visible while exercising the same strict readers and solver path used
for evaluated files. Evaluated ENDF/B, CoNDERC, OpenMC and ALARA inputs remain external.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target" / "release" / "dump"))
FISSION_PROBE = Path(
    os.environ.get("ACTINV_FISSION_PROBE", ROOT / "target" / "release" / "fission_probe")
)
OPENMC_PYTHON = Path(
    os.environ.get("ACTINV_OPENMC_PYTHON", "/home/connoravila/.venvs/w003env/bin/python")
)
U235_NFPY = Path(
    os.environ.get(
        "ACTINV_P9_U235_NFPY",
        str(Path.home() / "nuclear-data" / "endfb-viii.0-nfpy" / "nfy-092_U_235.endf"),
    )
)
CONDERC_ROOT = Path(
    os.environ.get("ACTINV_P9_CONDERC", str(Path.home() / "nuclear-data" / "conderc-fission"))
)

NEUTRON_MASS_U = 1.00866491595
AVOGADRO = 6.02214076e23
SYNTHETIC_BOUNDS_EV = [1.0, 3.0]
SYNTHETIC_FLUX = 1.0e24


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def command(
    arguments: list[str | Path],
    *,
    ok: bool = True,
    cwd: Path | None = None,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if env:
        environment.update(env)
    result = subprocess.run(
        [str(item) for item in arguments],
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if ok and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(map(str, arguments))}\n"
            f"{result.stdout}{result.stderr}"
        )
    if not ok and result.returncode == 0:
        raise RuntimeError(f"command unexpectedly succeeded: {' '.join(map(str, arguments))}")
    return result


def _field(value: float | int) -> str:
    if isinstance(value, int):
        return f"{value:11d}"
    text = f"{value:11.4E}"
    if len(text) != 11:
        raise ValueError(f"cannot encode {value} in one synthetic ENDF field")
    return text


def _record(
    values: list[float | int], mat: int, mf: int, mt: int, sequence: int
) -> str:
    if len(values) != 6:
        raise ValueError("an ENDF record needs six fields")
    return "".join(_field(value) for value in values) + f"{mat:4d}{mf:2d}{mt:3d}{sequence:5d}"


def _payload(
    values: list[float | int], mat: int, mf: int, mt: int, sequence: int
) -> tuple[list[str], int]:
    lines = []
    for start in range(0, len(values), 6):
        fields = values[start : start + 6]
        fields.extend([0.0] * (6 - len(fields)))
        lines.append(_record(fields, mat, mf, mt, sequence))
        sequence += 1
    return lines, sequence


def write_decay(path: Path) -> None:
    # za, liso, AWR, stable, half-life, [(RTYP, RFS, Q, BR)]
    records = [
        (92235, 0, 233.0, True, 0.0, []),
        (92236, 0, 234.0, True, 0.0, []),
        (36092, 0, 91.0, True, 0.0, []),
        (56141, 0, 140.0, True, 0.0, []),
        (38100, 0, 99.0, True, 0.0, []),
        (26056, 0, 55.454, True, 0.0, []),
        (25056, 0, 55.460, False, 100.0, [(1.0, 0.0, 3.0e6, 1.0)]),
        (56137, 0, 135.75, True, 0.0, []),
        (56137, 1, 135.76, True, 0.0, []),
    ]
    lines: list[str] = []
    for material, (za, liso, awr, stable, half_life, modes) in enumerate(records, 100):
        sequence = 1
        lines.append(
            _record([float(za), awr, 0, liso, int(stable), 0], material, 8, 457, sequence)
        )
        sequence += 1
        lines.append(
            _record([half_life, 0.0, 0, 0, 6 if not stable else 0, 0], material, 8, 457, sequence)
        )
        sequence += 1
        if not stable:
            # light-particle, uncertainty, EM, uncertainty, heavy, uncertainty
            payload, sequence = _payload(
                [1.0e6, 0.0, 2.0e6, 0.0, 0.0, 0.0], material, 8, 457, sequence
            )
            lines.extend(payload)
        mode_values: list[float] = []
        for rtyp, rfs, q_value, branching in modes:
            mode_values.extend([rtyp, rfs, q_value, 0.0, branching, 0.0])
        lines.append(
            _record(
                [0.0, 0.0, 0, 0, len(mode_values), len(modes)],
                material,
                8,
                457,
                sequence,
            )
        )
        sequence += 1
        payload, sequence = _payload(mode_values, material, 8, 457, sequence)
        lines.extend(payload)
        lines.append(_record([0.0, 0.0, 0, 0, 0, 0], material, 8, 0, sequence))
    path.write_text("\n".join(lines) + "\n")


INDEPENDENT_TABLES = [
    (1.0, [(36092, 0, 0.8, 0.01), (56141, 0, 0.9, 0.02), (54140, 0, 0.3, 0.03)]),
    (3.0, [(36092, 0, 0.4, 0.01), (38100, 0, 1.0, 0.02), (54140, 0, 0.6, 0.03)]),
]


def _yield_section(
    parent: int,
    awr: float,
    material: int,
    mt: int,
    tables: list[tuple[float, list[tuple[int, int, float, float]]]],
) -> list[str]:
    sequence = 1
    lines = [_record([float(parent), awr, len(tables), 0, 0, 0], material, 8, mt, sequence)]
    sequence += 1
    for energy, products in tables:
        values: list[float | int] = []
        for za, liso, value, uncertainty in products:
            values.extend([float(za), float(liso), value, uncertainty])
        lines.append(
            _record([energy, 0.0, 0, 0, len(values), len(products)], material, 8, mt, sequence)
        )
        sequence += 1
        payload, sequence = _payload(values, material, 8, mt, sequence)
        lines.extend(payload)
    lines.append(_record([0.0, 0.0, 0, 0, 0, 0], material, 8, 0, sequence))
    return lines


def write_fission_yields(path: Path, *, cumulative_variant: float = 1.0) -> None:
    parent, awr, material = 92235, 233.025, 9237
    lines = [
        _record([float(parent), awr, 0, 0, 0, 0], material, 1, 451, 1),
        _record([0.0, 0.0, 0, 0, 0, 0], material, 1, 451, 2),
        _record([0.0, 0.0, 0, 0, 0, 0], material, 1, 0, 3),
    ]
    lines.extend(_yield_section(parent, awr, material, 454, INDEPENDENT_TABLES))
    cumulative = [
        (
            energy,
            [
                (za, liso, value * cumulative_variant + 0.123, uncertainty)
                for za, liso, value, uncertainty in products
            ],
        )
        for energy, products in INDEPENDENT_TABLES
    ]
    lines.extend(_yield_section(parent, awr, material, 459, cumulative))
    path.write_text("\n".join(lines) + "\n")


def write_activation_library(path: Path) -> Path:
    rows = np.asarray(
        [
            [0, 18, -1, -1, 0],
            [0, 18, 0, 0, 0],
            [1, 18, -1, -1, 0],
            [1, 18, 0, 0, 0],
            [2, 103, -1, -1, 0],
            [2, 103, 25056, 0, -1],
        ],
        dtype=np.int64,
    )
    sig = np.asarray([[2.0], [2.0], [0.5], [0.5], [4.0], [4.0]], dtype=np.float64)
    bounds = np.asarray(SYNTHETIC_BOUNDS_EV, dtype=np.float64)
    np.savez(path, rows=rows, sig=sig, bounds=bounds)
    index = path.with_name(path.stem + "_index.json")
    write_json(
        index,
        {
            "groups": 1,
            "n_rows": len(rows),
            "temperature_K": 293.6,
            "sha256_npz": sha256(path),
            "targets": [
                {"za": 92235, "liso": 0, "awr": 233.025, "ledger": []},
                {"za": 92236, "liso": 0, "awr": 234.015, "ledger": []},
                {"za": 26056, "liso": 0, "awr": 55.454, "ledger": []},
            ],
        },
    )
    return index


def make_fixture(work: Path) -> dict[str, Path]:
    work.mkdir(parents=True, exist_ok=True)
    decay = work / "p9-synthetic-decay.endf"
    yields = work / "p9-synthetic-yields.endf"
    yields_cumulative_changed = work / "p9-synthetic-yields-cumulative-changed.endf"
    library = work / "p9-synthetic.npz"
    write_decay(decay)
    write_fission_yields(yields)
    write_fission_yields(yields_cumulative_changed, cumulative_variant=7.0)
    index = write_activation_library(library)
    return {
        "decay": decay,
        "yields": yields,
        "yields_cumulative_changed": yields_cumulative_changed,
        "library": library,
        "index": index,
    }


def base_spec(
    fixture: dict[str, Path],
    *,
    composition: dict[str, float],
    basis: str = "atoms_per_g",
    schedule: list[dict] | None = None,
    mode: str = "auto",
    total_flux: float = SYNTHETIC_FLUX,
    yields: Path | None = None,
    fixed_energy_ev: float | None = 2.0,
) -> dict:
    selected_yields = yields if yields is not None else fixture["yields"]
    fission = (
        {
            "files": [{"path": str(selected_yields), "sha256": sha256(selected_yields)}],
            "energy": "fixed",
            "fixed_energy_eV": fixed_energy_ev,
        }
        if selected_yields
        else {"files": [], "energy": "spectrum_average"}
    )
    return {
        "spec": "actinv-spec-1",
        "title": "P9 deterministic synthetic fixture",
        "library": {"path": str(fixture["library"]), "sha256": sha256(fixture["library"])},
        "decay": {"primary": str(fixture["decay"])},
        "material": {"mass_g": 1.0, "basis": basis, "composition": composition},
        "spectrum": {
            "structure": "custom",
            "boundaries_eV": SYNTHETIC_BOUNDS_EV,
            "flux_per_group": [1.0],
            "total": total_flux,
            "descending": False,
        },
        "schedule": schedule or [{"dt": "1 s", "flux": 1.0}],
        "options": {
            "mode": mode,
            "prune": "none",
            "bmin_atoms_per_g": 0.0,
            "temperature_K": 293.6,
            "outputs": ["inventory", "activity", "heat", "ledger", "certificate"],
        },
        "fission_yields": fission,
    }


def run_spec(work: Path, name: str, specification: dict, *, timeout: float = 120.0) -> dict:
    specification_path = work / f"{name}.json"
    result_path = work / f"{name}.result.json"
    write_json(specification_path, specification)
    command([BIN, "run", specification_path, result_path], timeout=timeout)
    return json.loads(result_path.read_text())


def inventory(step: dict) -> dict[str, float]:
    return {entry["nuclide"]: entry["atoms_per_g"] for entry in step["inventory"]}


def relative(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0e-300)


def log_mean_energy(low: float, high: float) -> float:
    return (high - low) / math.log(high / low)
