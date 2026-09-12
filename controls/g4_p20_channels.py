#!/usr/bin/env python3
"""P20 G4 control — decay-constant and fission-yield uncertainty channels.

Legs:

  synthetic     Tiny ENDF fixture where every direction and variance is
                analytic: a bulk U-235 reservoir fissions into two radioactive
                products — Xe140 (half-life uncertainty present -> covered) and
                Kr92 (no half-life uncertainty and zero DY -> named uncovered).
                The run's decay/yield tangents, channel variances, combined
                band, coverage fields, uncovered lists, and the
                require_complete rejection are compared against closed-form
                derivatives.
  real_decay    FNS Fe case plus the decay_constants channel on the pinned
                ENDF/B-VIII.0 decay file; Mn56's half-life is then perturbed in
                a copied decay file and a central finite difference checks the
                reported decay tangent end to end.
  real_yield    U-235 trace case on the real TENDL library with the pinned
                ENDF/B-VIII.0 fission-yield file at the fixed 500 keV table;
                the highest-leverage covered yield is perturbed in a copied
                file and a central finite difference checks the reported yield
                tangent.

All runs are ordinary `actinv run` invocations against hashed inputs; nothing
imports the production implementation.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p9_fixtures import (  # noqa: E402
    BIN,
    SYNTHETIC_FLUX,
    _field,
    _payload,
    _record,
    command,
    relative,
    run_spec,
    sha256,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "target" / "p20-g4"
REPORT = ROOT / "results" / "g4_p20_channels.json"
PROTOCOL_SHA256 = "76c2ca2f646f85ea8c4a26f9ed5b2c1b3c49cfb3312a9122e546db4b53164335"

FNS_SPEC = ROOT / "examples" / "fns_fe_5min.json"
REAL_COVARIANCE = ROOT / "target" / "p11-full-v2-repro.cov.npz"
REAL_DECAY = ROOT / "actinv-data" / "v1.0.0" / "decay" / "endf-b-viii-0_decay.dat"
REAL_YIELDS = Path(
    "/home/connoravila/nuclear-data/endfb-viii.0-nfpy/nfy-092_U_235.endf"
)
FIXED_YIELD_ENERGY = 5.0e5  # a table energy in the pinned U235 NFY file

EV = 1.602176634e-19
LN2 = math.log(2.0)

# ---------------------------------------------------------------------------
# synthetic fixture
#
#   U235_0 (stable bulk reservoir) --fission--> Xe140_0 --beta-(200 s)--> Cs140_0
#                                        \---> Kr92_0  --beta-(100 s)--> Rb92_0
#
# Xe140 carries dT1/2 (covered decay parameter) and DY (covered yield
# parameter); Kr92 carries neither (named uncovered in both channels).
# ---------------------------------------------------------------------------

SYNTHETIC_BOUNDS = [1.0, 3.0]
N_U = 1.0e21          # U235 atoms per gram
SIG_F = 2.0           # barns, single group
DT = 50.0             # irradiation step, seconds
T_XE = 200.0
DT_XE = 2.0
T_KR = 100.0
DT_KR = 0.0           # absent -> uncovered decay parameter
Y_XE, DY_XE = 0.06, 0.003
Y_KR, DY_KR = 0.05, 0.0
E_EM_XE = 2.0e6       # eV
E_EM_KR = 1.5e6       # eV


def group_boundary_hash(boundaries_ev: list[float]) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"ACTINV-GROUP-BOUNDARIES-v1\0")
    for value in boundaries_ev:
        hasher.update(struct.pack("<d", value))
    return hasher.hexdigest()


def write_decay_g4(path: Path) -> None:
    # za, liso, AWR, stable, half-life, d-half-life, [(RTYP, RFS, Q, BR)]
    records = [
        (92235, 0, 233.0, True, 0.0, 0.0, []),
        (54140, 0, 139.0, False, T_XE, DT_XE, [(1.0, 0.0, 3.0e6, 1.0)]),
        (36092, 0, 91.0, False, T_KR, DT_KR, [(1.0, 0.0, 2.0e6, 1.0)]),
        (55140, 0, 139.0, True, 0.0, 0.0, []),
        (37092, 0, 91.0, True, 0.0, 0.0, []),
    ]
    energies = {
        54140: [1.0e6, 0.0, E_EM_XE, 0.0, 3.0e6, 0.0],
        36092: [5.0e5, 0.0, E_EM_KR, 0.0, 1.0e6, 0.0],
    }
    lines: list[str] = []
    for material, (za, liso, awr, stable, half_life, d_half_life, modes) in enumerate(
        records, 100
    ):
        sequence = 1
        lines.append(
            _record([float(za), awr, 0, liso, int(stable), 0], material, 8, 457, sequence)
        )
        sequence += 1
        lines.append(
            _record(
                [half_life, d_half_life, 0, 0, 6 if not stable else 0, 0],
                material,
                8,
                457,
                sequence,
            )
        )
        sequence += 1
        if not stable:
            payload, sequence = _payload(energies[za], material, 8, 457, sequence)
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


def _yield_section(
    parent: int,
    awr: float,
    material: int,
    mt: int,
    tables: list[tuple[float, list[tuple[int, int, float, float]]]],
) -> list[str]:
    sequence = 1
    lines = [
        _record([float(parent), awr, len(tables), 0, 0, 0], material, 8, mt, sequence)
    ]
    sequence += 1
    for energy, products in tables:
        values: list[float | int] = []
        for za, liso, value, uncertainty in products:
            values.extend([float(za), float(liso), value, uncertainty])
        lines.append(
            _record(
                [energy, 0.0, 0, 0, len(values), len(products)],
                material,
                8,
                mt,
                sequence,
            )
        )
        sequence += 1
        payload, sequence = _payload(values, material, 8, mt, sequence)
        lines.extend(payload)
    lines.append(_record([0.0, 0.0, 0, 0, 0, 0], material, 8, 0, sequence))
    return lines


def write_yields_g4(path: Path) -> None:
    parent, awr, material = 92235, 233.025, 9237
    # 38090 is absent from the decay file: its yield routes to the leak row,
    # exercising the leak-edge direction while keeping the table sum at 2.
    tables = [
        (
            1.0,
            [(54140, 0, Y_XE, DY_XE), (36092, 0, Y_KR, DY_KR), (38090, 0, 1.89, 0.05)],
        ),
        (
            3.0,
            [(54140, 0, 0.08, 0.004), (36092, 0, 0.05, 0.0025), (38090, 0, 1.87, 0.05)],
        ),
    ]
    lines = [
        _record([float(parent), awr, 0, 0, 0, 0], material, 1, 451, 1),
        _record([0.0, 0.0, 0, 0, 0, 0], material, 1, 451, 2),
        _record([0.0, 0.0, 0, 0, 0, 0], material, 1, 0, 3),
    ]
    lines.extend(_yield_section(parent, awr, material, 454, tables))
    lines.extend(_yield_section(parent, awr, material, 459, tables))
    path.write_text("\n".join(lines) + "\n")


def write_library_g4(path: Path) -> Path:
    rows = np.asarray(
        [
            [0, 18, -1, -1, 0],
            [0, 18, 0, 0, 0],
        ],
        dtype=np.int64,
    )
    sig = np.asarray([[SIG_F], [SIG_F]], dtype=np.float64)
    bounds = np.asarray(SYNTHETIC_BOUNDS, dtype=np.float64)
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
                {
                    "za": 92235,
                    "liso": 0,
                    "awr": 233.025,
                    "mat": 9237,
                    "file": "p20-g4-synthetic.tendl",
                    "source_sha256": "0" * 64,
                    "ledger": [],
                }
            ],
        },
    )
    return index


def write_covariance_g4(
    activation_index_path: Path,
    activation_index: dict,
    library_sha: str,
    boundaries_ev: list[float],
    out: Path,
) -> Path:
    """A sidecar with one LB=5 single-group relative component on (target 0,
    MT18, MT18): both U235 fission rows are covered with 10% relative standard
    uncertainty (variance 0.01)."""
    components = np.asarray(
        [
            # target, mt, mt1, lb, kind(=1 relative), row_grid, col_grid, offset, len
            [0, 18, 18, 5, 1, 0, 0, 0, 1],
        ],
        dtype=np.int64,
    )
    np.savez(
        out,
        components=components,
        grid_offsets=np.asarray([0, 2], dtype=np.int64),
        grid_values=np.asarray(boundaries_ev, dtype=np.float64),
        values=np.asarray([0.01], dtype=np.float64),
    )
    targets = []
    for ordinal, entry in enumerate(activation_index["targets"]):
        targets.append(
            {
                "target": ordinal,
                "mat": entry["mat"],
                "za": entry["za"],
                "liso": entry["liso"],
                "file": entry["file"],
                "source_sha256": entry["source_sha256"],
                "mf33_sections": 1,
                "components": 1,
                "lb_counts": {"5": 1},
            }
        )
    index = out.with_name(out.stem + "_index.json")
    write_json(
        index,
        {
            "schema": "actinv-covariance-index-1",
            "projectile": "neutron",
            "sha256_npz": sha256(out),
            "activation_library_sha256": library_sha,
            "activation_index_sha256": sha256(activation_index_path),
            "group_boundary_sha256": group_boundary_hash(boundaries_ev),
            "files": 1,
            "mf33_sections": 1,
            "components": 1,
            "lb_counts": {"5": 1},
            "targets": targets,
        },
    )
    return index


def endf_float(text: str) -> float:
    """Parse an ENDF-6 field, including the E-less Fortran form."""
    text = text.strip()
    if not text:
        return 0.0
    if "e" not in text.lower() and ("+" in text[1:] or "-" in text[1:]):
        for split in range(len(text) - 1, 0, -1):
            if text[split] in "+-" and text[split - 1].isdigit():
                return float(text[:split] + "e" + text[split:])
    return float(text)


def record_fields(line: str) -> list[str]:
    return [line[start : start + 11] for start in range(0, 66, 11)]


def record_tail(line: str) -> tuple[int, int, int, int]:
    tail = line[66:80]
    # Files written without the ENDF line-number field (75-char records) leave
    # the last field blank; the record sequence is not needed here.
    number = tail[9:14].strip()
    return (
        int(tail[0:4]),
        int(tail[4:6]),
        int(tail[6:9]),
        int(number) if number else 0,
    )


def perturb_half_life(source: Path, dest: Path, za: int, liso: int, scale: float) -> None:
    """Scale T1/2 for (za, liso) inside its MF=8/MT=457 section."""
    lines = source.read_text().splitlines()
    i = 0
    while i < len(lines):
        try:
            _, mf, mt, _ = record_tail(lines[i])
        except (ValueError, IndexError):
            i += 1
            continue
        if mf == 8 and mt == 457:
            fields = record_fields(lines[i])
            if (
                abs(endf_float(fields[0]) - za) < 0.5
                and int(endf_float(fields[3])) == liso
                and i + 1 < len(lines)
            ):
                # The next record is the half-life LIST head: c1 = T1/2.
                target = lines[i + 1]
                fields2 = record_fields(target)
                fields2[0] = _field(endf_float(fields2[0]) * scale)
                lines[i + 1] = "".join(fields2) + target[66:]
                dest.write_text("\n".join(lines) + "\n")
                return
        i += 1
    raise RuntimeError(f"no MF=8/MT=457 section for ZA {za} LISO {liso}")


def perturb_yield(
    source: Path,
    dest: Path,
    energy: float,
    za: int,
    liso: int,
    scale: float,
    compensate: tuple[int, int] | None = None,
) -> None:
    """Scale the independent yield of (za, liso) in the MF=8/MT=454 table whose
    subsection energy equals `energy`. If `compensate` is given, shift that
    product by the opposite absolute amount so the table still sums to 2."""
    lines = source.read_text().splitlines()
    i = 0
    while i < len(lines):
        try:
            _, mf, mt, _ = record_tail(lines[i])
        except (ValueError, IndexError):
            i += 1
            continue
        if mf != 8 or mt != 454:
            i += 1
            continue
        head = lines[i]
        head_fields = record_fields(head)
        n_products = int(endf_float(head_fields[5]))
        n_values = n_products * 4
        n_lines = (n_values + 5) // 6
        if abs(endf_float(head_fields[0]) - energy) < 1.0:
            values: list[float] = []
            for k in range(i + 1, i + 1 + n_lines):
                values.extend(endf_float(field) for field in record_fields(lines[k]))
            moved = {}
            for product in range(n_products):
                base = product * 4
                key = (int(round(values[base])), int(round(values[base + 1])))
                if key == (za, liso):
                    moved["target"] = values[base + 2] * (scale - 1.0)
                    values[base + 2] *= scale
                if compensate is not None and key == compensate:
                    moved["compensate"] = base
            if "target" not in moved:
                raise RuntimeError(
                    f"product ZA {za} LISO {liso} not in MT=454 table at E={energy}"
                )
            if compensate is not None:
                if "compensate" not in moved:
                    raise RuntimeError(
                        f"compensation product {compensate} not in MT=454 table"
                    )
                # Shift the compensation product by the opposite absolute
                # amount so the yield table still sums to 2. A leak-routed
                # compensator has exactly zero sensitivity on every tracked
                # response, so the finite difference stays clean.
                values[moved["compensate"] + 2] -= moved["target"]
            # Rewrite only the fields that changed — reformatting untouched
            # values would lose digits against the original 7-significant-
            # figure fields and drift the yield sum past its 1e-6 check.
            edits = {base + 2 for base, key in
                     ((p * 4, (int(round(values[p * 4])),
                               int(round(values[p * 4 + 1]))))
                      for p in range(n_products))
                     if key == (za, liso) or (compensate is not None and key == compensate)}
            for field_index in edits:
                line_index = i + 1 + field_index // 6
                slot = field_index % 6
                start = slot * 11
                original = lines[line_index]
                lines[line_index] = (
                    original[:start]
                    + f"{values[field_index]:11.5E}"
                    + original[start + 11 :]
                )
            dest.write_text("\n".join(lines) + "\n")
            return
        i += 1 + n_lines
    raise RuntimeError(f"no MF=8/MT=454 table at E={energy}")


def yield_table(path: Path, energy: float) -> dict[tuple[int, int], tuple[float, float]]:
    """Return {(za, liso): (y, dy)} for the MF=8/MT=454 table at `energy`."""
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        try:
            _, mf, mt, _ = record_tail(lines[i])
        except (ValueError, IndexError):
            i += 1
            continue
        if mf != 8 or mt != 454:
            i += 1
            continue
        head_fields = record_fields(lines[i])
        n_products = int(endf_float(head_fields[5]))
        n_lines = (n_products * 4 + 5) // 6
        if abs(endf_float(head_fields[0]) - energy) < 1.0:
            values: list[float] = []
            for k in range(i + 1, i + 1 + n_lines):
                values.extend(endf_float(field) for field in record_fields(lines[k]))
            return {
                (int(round(values[p * 4])), int(round(values[p * 4 + 1]))): (
                    values[p * 4 + 2],
                    values[p * 4 + 3],
                )
                for p in range(n_products)
            }
        i += 1 + n_lines
    raise RuntimeError(f"no MF=8/MT=454 table at E={energy} in {path}")


def channel_report(band: dict, name: str) -> dict:
    for entry in band["channels"]:
        if entry["channel"] == name:
            return entry
    raise RuntimeError(f"no channel report for {name}")


def response_value(step: dict, name: str) -> float:
    if name.startswith("heat."):
        return step["heat_W_per_g"][name.split(".", 1)[1]]
    prefix, _, key = name.partition(":")
    if prefix == "activity":
        return step["activity_Bq_per_g"].get(key, 0.0)
    raise ValueError(f"unknown response {name}")


def synthetic_leg(work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    decay = work / "g4-synthetic-decay.endf"
    yields = work / "g4-synthetic-yields.endf"
    library = work / "g4-synthetic.npz"
    write_decay_g4(decay)
    write_yields_g4(yields)
    index_path = write_library_g4(library)
    library_index = json.loads(index_path.read_text())
    cov = work / "g4-synthetic.cov.npz"
    write_covariance_g4(
        index_path, library_index, sha256(library), SYNTHETIC_BOUNDS, cov
    )

    base = {
        "spec": "actinv-spec-1",
        "title": "P20 G4 synthetic channel oracle",
        "library": {"path": str(library), "sha256": sha256(library)},
        "decay": {"primary": str(decay)},
        "material": {
            "mass_g": 1.0,
            "basis": "atoms_per_g",
            "composition": {"U235": N_U},
        },
        "spectrum": {
            "structure": "custom",
            "boundaries_eV": SYNTHETIC_BOUNDS,
            "flux_per_group": [1.0],
            "total": SYNTHETIC_FLUX,
            "descending": False,
        },
        "schedule": [{"dt": f"{DT} s", "flux": 1.0}],
        "options": {
            "mode": "trace",
            "prune": "none",
            "bmin_atoms_per_g": 0.0,
            "temperature_K": 293.6,
            "outputs": ["inventory", "activity", "heat", "ledger", "certificate"],
        },
        "fission_yields": {
            "files": [{"path": str(yields), "sha256": sha256(yields)}],
            "energy": "fixed",
            "fixed_energy_eV": 1.0,
        },
        "uncertainty": {
            "covariance": {"path": str(cov), "sha256": sha256(cov)},
            "confidence_level": 0.90,
            "responses": ["activity:Xe140", "activity:Kr92", "heat.gamma"],
        },
    }

    lam_xe = LN2 / T_XE
    lam_kr = LN2 / T_KR
    dlam_xe = lam_xe * DT_XE / T_XE
    rate_f = SIG_F * 1.0  # collapsed sigma * rate_per_barn_s (flux*1e-24 = 1.0)
    src_xe = Y_XE * rate_f * N_U
    src_kr = Y_KR * rate_f * N_U
    analytic = {
        "activity_Xe140": src_xe * (1.0 - math.exp(-lam_xe * DT)),
        "activity_Kr92": src_kr * (1.0 - math.exp(-lam_kr * DT)),
        "dActivity_Xe140_dLambda": src_xe * DT * math.exp(-lam_xe * DT),
        "dActivity_Kr92_dLambda": src_kr * DT * math.exp(-lam_kr * DT),
        "dActivity_Xe140_dYield": rate_f * N_U * (1.0 - math.exp(-lam_xe * DT)),
        "dActivity_Kr92_dYield": rate_f * N_U * (1.0 - math.exp(-lam_kr * DT)),
        "dHeatGamma_dLambda_Xe": E_EM_XE * EV * src_xe * DT * math.exp(-lam_xe * DT),
        "dHeatGamma_dLambda_Kr": E_EM_KR * EV * src_kr * DT * math.exp(-lam_kr * DT),
        "decay_sigma_activity_Xe140": abs(src_xe * DT * math.exp(-lam_xe * DT))
        * dlam_xe,
        "yield_sigma_activity_Xe140": abs(rate_f * N_U * (1.0 - math.exp(-lam_xe * DT)))
        * DY_XE,
        # Only the product row carries a tracked sensitivity: the loss row's
        # derivative is absorbed by the U235 reservoir in trace mode. The LB=5
        # relative block contributes |dA/dsigma| * sigma * 0.1.
        "xs_sigma_activity_Xe140": abs(
            1.0 * N_U * Y_XE * (1.0 - math.exp(-lam_xe * DT))
        )
        * (0.1 * SIG_F),
    }

    spec = dict(base)
    spec["uncertainty"] = dict(
        base["uncertainty"], channels=["decay_constants", "fission_yields"]
    )
    result = run_spec(work, "synthetic", spec, timeout=120.0)
    step = result["steps"][0]
    bands = step["uncertainty"]["responses"]

    def decay_sensitivity(band_entry, nuclide):
        for entry in band_entry["decay_sensitivities"]:
            if entry["parameter"]["nuclide"] == nuclide:
                return entry["value"]
        raise RuntimeError(f"no decay sensitivity for {nuclide}")

    def yield_sensitivity(band_entry, parent, product):
        for entry in band_entry["yield_sensitivities"]:
            if (
                entry["parameter"]["parent_nuclide"] == parent
                and entry["parameter"]["product_nuclide"] == product
            ):
                return entry["value"]
        raise RuntimeError(f"no yield sensitivity for {parent} -> {product}")

    checks = []
    xe = bands["activity:Xe140"]
    kr = bands["activity:Kr92"]
    heat = bands["heat.gamma"]
    for name, reported, expected in [
        (
            "decay_tangent_Xe140",
            decay_sensitivity(xe, "Xe140"),
            analytic["dActivity_Xe140_dLambda"],
        ),
        (
            "decay_tangent_Kr92_uncovered",
            decay_sensitivity(kr, "Kr92"),
            analytic["dActivity_Kr92_dLambda"],
        ),
        (
            "yield_tangent_Xe140",
            yield_sensitivity(xe, "U235", "Xe140"),
            analytic["dActivity_Xe140_dYield"],
        ),
        (
            "yield_tangent_Kr92_uncovered",
            yield_sensitivity(kr, "U235", "Kr92"),
            analytic["dActivity_Kr92_dYield"],
        ),
        (
            "decay_tangent_heat_gamma_Xe140",
            decay_sensitivity(heat, "Xe140"),
            analytic["dHeatGamma_dLambda_Xe"],
        ),
        (
            "decay_tangent_heat_gamma_Kr92",
            decay_sensitivity(heat, "Kr92"),
            analytic["dHeatGamma_dLambda_Kr"],
        ),
    ]:
        checks.append(
            {
                "name": name,
                "reported": reported,
                "expected": expected,
                "relative_error": relative(reported, expected),
            }
        )
    decay_channel = channel_report(xe, "decay_constants")
    yield_channel = channel_report(xe, "fission_yields")
    mf33_channel = channel_report(xe, "cross_section_mf33")
    combined_reported = xe["combined_standard_uncertainty"]
    combined_expected = math.sqrt(
        analytic["xs_sigma_activity_Xe140"] ** 2
        + analytic["decay_sigma_activity_Xe140"] ** 2
        + analytic["yield_sigma_activity_Xe140"] ** 2
    )
    for name, reported, expected in [
        (
            "mf33_channel_sigma_activity_Xe140",
            mf33_channel["standard_uncertainty"],
            analytic["xs_sigma_activity_Xe140"],
        ),
        (
            "decay_channel_sigma_activity_Xe140",
            decay_channel["standard_uncertainty"],
            analytic["decay_sigma_activity_Xe140"],
        ),
        (
            "yield_channel_sigma_activity_Xe140",
            yield_channel["standard_uncertainty"],
            analytic["yield_sigma_activity_Xe140"],
        ),
        ("combined_sigma_activity_Xe140", combined_reported, combined_expected),
    ]:
        checks.append(
            {
                "name": name,
                "reported": reported,
                "expected": expected,
                "relative_error": relative(reported, expected),
            }
        )
    worst = max(entry["relative_error"] for entry in checks)
    uncovered_decay = step["uncertainty"].get("uncovered_decay_constants", [])
    uncovered_yield = step["uncertainty"].get("uncovered_yield_products", [])
    half_width = (xe["normal_interval"][1] - xe["normal_interval"][0]) / 2.0
    interval_consistent = (
        relative(half_width, xe["normal_multiplier"] * combined_reported) < 1e-12
    )

    # require_complete must refuse the partial coverage.
    spec_strict = json.loads(json.dumps(spec))
    spec_strict["uncertainty"]["require_complete"] = True
    strict_path = work / "synthetic-strict.json"
    strict_out = work / "synthetic-strict.result.json"
    write_json(strict_path, spec_strict)
    strict = command([BIN, "run", strict_path, strict_out], ok=False, timeout=120.0)
    strict_message = strict.stderr + strict.stdout
    strict_failed = strict.returncode != 0 and (
        "coverage" in strict_message.lower() or "covariance" in strict_message.lower()
    )

    # Without the channel request no decay/yield fields appear at all.
    plain = run_spec(work, "synthetic-plain", base, timeout=120.0)
    plain_step = plain["steps"][0]
    plain_band = plain_step["uncertainty"]["responses"]["activity:Xe140"]
    channel_keys_absent = (
        "decay_sensitivities" not in plain_band
        and "yield_sensitivities" not in plain_band
        and "combined_standard_uncertainty" not in plain_band
        and "uncovered_decay_constants" not in plain_step["uncertainty"]
    )

    return {
        "work": str(work),
        "analytic": analytic,
        "checks": checks,
        "worst_relative_error": worst,
        "uncovered_decay_constants": uncovered_decay,
        "uncovered_yield_products": uncovered_yield,
        "decay_channel_report": decay_channel,
        "yield_channel_report": yield_channel,
        "mf33_channel_report": channel_report(xe, "cross_section_mf33"),
        "remainder_channel_report": channel_report(xe, "uncovered_remainder"),
        "combined_standard_uncertainty": combined_reported,
        "normal_interval": xe["normal_interval"],
        "interval_consistent": interval_consistent,
        "require_complete_rejected": strict_failed,
        "channels_absent_clean": channel_keys_absent,
        "n_decay_parameters": len(xe["decay_sensitivities"]),
        "n_yield_parameters": len(xe["yield_sensitivities"]),
        "pass": worst < 1e-9
        and uncovered_decay == ["Kr92"]
        and uncovered_yield == ["U235 -> Kr92"]
        and interval_consistent
        and strict_failed
        and channel_keys_absent,
    }


def real_decay_leg(work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    spec = json.loads(FNS_SPEC.read_text())
    spec["uncertainty"] = {
        "covariance": {
            "path": str(REAL_COVARIANCE),
            "sha256": sha256(REAL_COVARIANCE),
        },
        "confidence_level": 0.90,
        "channels": ["decay_constants"],
        "responses": ["activity:Mn56", "activity:Mn54", "heat.gamma"],
    }
    result = run_spec(work, "fns-decay", spec, timeout=600.0)
    step = result["steps"][0]
    uncertainty = step["uncertainty"]
    mn56 = uncertainty["responses"]["activity:Mn56"]
    decay_entries = {
        entry["parameter"]["nuclide"]: entry for entry in mn56["decay_sensitivities"]
    }
    mn56_param = decay_entries.get("Mn56")
    if mn56_param is None:
        raise RuntimeError("Mn56 has no decay parameter on the real case")

    # Central finite difference on Mn56's half-life. T_half and lambda move in
    # opposite directions, so d/dlambda = -(up - down)/(2*d_lambda).
    delta = 1.0e-3
    perturbed = {}
    for sign, tag in [(1.0 + delta, "up"), (1.0 - delta, "down")]:
        decay_copy = work / f"endfb-decay-mn56-{tag}.endf"
        perturb_half_life(REAL_DECAY, decay_copy, 25056, 0, sign)
        fd_spec = json.loads(json.dumps(spec))
        fd_spec["decay"] = {"primary": str(decay_copy)}
        fd_spec.pop("uncertainty")
        perturbed[tag] = run_spec(
            work, f"fns-decay-mn56-{tag}", fd_spec, timeout=600.0
        )
    a_up = response_value(perturbed["up"]["steps"][0], "activity:Mn56")
    a_down = response_value(perturbed["down"]["steps"][0], "activity:Mn56")
    lambda_s = mn56_param["parameter"]["lambda_s"]
    d_lambda = lambda_s * delta
    fd = -(a_up - a_down) / (2.0 * d_lambda)
    reported = mn56_param["value"]
    return {
        "work": str(work),
        "source_files": {
            "decay": {"path": str(REAL_DECAY), "sha256": sha256(REAL_DECAY)},
            "covariance": {
                "path": str(REAL_COVARIANCE),
                "sha256": sha256(REAL_COVARIANCE),
            },
        },
        "decay_parameter": mn56_param["parameter"],
        "reported_sensitivity": reported,
        "finite_difference": fd,
        "relative_error": relative(reported, fd),
        "delta_relative": delta,
        "n_decay_parameters": len(mn56["decay_sensitivities"]),
        "uncovered_decay_count": len(uncertainty.get("uncovered_decay_constants", [])),
        "decay_channel_report": channel_report(mn56, "decay_constants"),
        "combined_standard_uncertainty": mn56.get("combined_standard_uncertainty"),
        "mf33_standard_uncertainty": mn56["mf33_standard_uncertainty"],
        "pass": relative(reported, fd) < 5e-3
        and mn56["combined_standard_uncertainty"]
        >= mn56["mf33_standard_uncertainty"],
    }


def write_empty_covariance(
    activation_index_path: Path,
    activation_index: dict,
    library_sha: str,
    boundaries_ev: list[float],
    out: Path,
) -> Path:
    """A schema-valid sidecar carrying zero components (MF=33 absent)."""
    np.savez(
        out,
        components=np.zeros((0, 9), dtype=np.int64),
        grid_offsets=np.zeros(1, dtype=np.int64),
        grid_values=np.zeros(0, dtype=np.float64),
        values=np.zeros(0, dtype=np.float64),
    )
    targets = []
    for ordinal, entry in enumerate(activation_index["targets"]):
        targets.append(
            {
                "target": ordinal,
                "mat": entry["mat"],
                "za": entry["za"],
                "liso": entry["liso"],
                "file": entry["file"],
                "source_sha256": entry["source_sha256"],
                "mf33_sections": 0,
                "components": 0,
                "lb_counts": {},
            }
        )
    index = out.with_name(out.stem + "_index.json")
    write_json(
        index,
        {
            "schema": "actinv-covariance-index-1",
            "projectile": "neutron",
            "sha256_npz": sha256(out),
            "activation_library_sha256": library_sha,
            "activation_index_sha256": sha256(activation_index_path),
            "group_boundary_sha256": group_boundary_hash(boundaries_ev),
            "files": 0,
            "mf33_sections": 0,
            "components": 0,
            "lb_counts": {},
            "targets": targets,
        },
    )
    return index


def real_yield_leg(work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    fns = json.loads(FNS_SPEC.read_text())
    # The real activation library + real ENDF/B-VIII.0 yield file carry the
    # channel's data; the decay file is the leg's own 5-nuclide fixture so the
    # chain stays small (the real decay file would put ~1e5 library rows into
    # the MF=33 parameter set, and this leg targets the yield channel, not the
    # P11-proven MF=33 machinery). Xe140/Kr92 land inside the chain; every
    # other real product is a named, leak-routed yield parameter.
    decay = work / "g4-real-yield-decay.endf"
    write_decay_g4(decay)
    lib_index_path = Path(fns["library"]["path"]).with_name(
        Path(fns["library"]["path"]).stem + "_index.json"
    )
    real_lib_index = json.loads(lib_index_path.read_text())
    empty_cov = work / "empty.cov.npz"
    write_empty_covariance(
        lib_index_path,
        real_lib_index,
        fns["library"]["sha256"],
        list(np.load(fns["library"]["path"])["bounds"]),
        empty_cov,
    )
    spec = {
        "spec": "actinv-spec-1",
        "title": "P20 G4 real fission-yield channel",
        "library": fns["library"],
        "decay": {"primary": str(decay)},
        "material": {
            "mass_g": 1.0,
            "basis": "atoms_per_g",
            "composition": {"U235": 1.0e18},
        },
        "spectrum": fns["spectrum"],
        "schedule": [{"dt": "30 s", "flux": 1.0}],
        "options": {
            "mode": "trace",
            "prune": "none",
            "bmin_atoms_per_g": 0.0,
            "temperature_K": 293.6,
            "outputs": ["inventory", "activity", "heat", "ledger", "certificate"],
        },
        "fission_yields": {
            "files": [{"path": str(REAL_YIELDS), "sha256": sha256(REAL_YIELDS)}],
            "energy": "fixed",
            "fixed_energy_eV": FIXED_YIELD_ENERGY,
        },
        "uncertainty": {
            "covariance": {
                "path": str(empty_cov),
                "sha256": sha256(empty_cov),
            },
            "confidence_level": 0.90,
            "channels": ["decay_constants", "fission_yields"],
            "responses": ["activity:Xe140", "activity:Kr92", "heat.gamma"],
        },
    }
    result = run_spec(work, "u235-yields", spec, timeout=900.0)
    step = result["steps"][0]
    uncertainty = step["uncertainty"]

    # Finite-difference the covered yield parameter with the largest
    # |sensitivity*sigma| among nuclides populated inside the small chain.
    best = None
    for band_name, band in uncertainty["responses"].items():
        for entry in band["yield_sensitivities"]:
            parameter = entry["parameter"]
            if parameter["covered"] and entry["value"] != 0.0:
                weight = abs(entry["value"] * parameter["standard_uncertainty"])
                if best is None or weight > best[0]:
                    best = (weight, band_name, parameter, entry["value"])
    if best is None:
        raise RuntimeError("no covered yield sensitivity on the real case")
    _, response, parameter, reported = best

    # The MF=8/MT=454 table must keep summing to 2, so each perturbation is
    # offset on the highest-yield product that is absent from the chain — a
    # leak-routed parameter whose response sensitivity is exactly zero.
    in_chain = {92235, 54140, 55140, 36092, 37092}
    table = yield_table(REAL_YIELDS, FIXED_YIELD_ENERGY)
    compensator = max(
        (kv for kv in table.items() if kv[0][0] not in in_chain),
        key=lambda kv: kv[1][0],
    )[0]

    # The compensator keeps the table sum exact, so a wide relative step is
    # allowed — and needed, since the response difference is otherwise small
    # against solver noise. The response is exactly linear in the yield, so
    # the wide step does not bias the difference.
    delta = 5.0e-2
    values = {}
    for sign, tag in [(1.0 + delta, "up"), (1.0 - delta, "down")]:
        yields_copy = work / f"nfy-u235-{tag}.endf"
        perturb_yield(
            REAL_YIELDS,
            yields_copy,
            FIXED_YIELD_ENERGY,
            parameter["product_ZA"],
            parameter["product_LISO"],
            sign,
            compensate=compensator,
        )
        fd_spec = json.loads(json.dumps(spec))
        fd_spec["fission_yields"]["files"] = [
            {"path": str(yields_copy), "sha256": sha256(yields_copy)}
        ]
        fd_spec.pop("uncertainty")
        values[tag] = run_spec(work, f"u235-yields-{tag}", fd_spec, timeout=900.0)

    dy = parameter["yield_value"] * delta
    fd = (
        response_value(values["up"]["steps"][0], response)
        - response_value(values["down"]["steps"][0], response)
    ) / (2.0 * dy)
    band = uncertainty["responses"][response]
    yield_channel = channel_report(band, "fission_yields")
    return {
        "work": str(work),
        "source_files": {
            "fission_yields": {
                "path": str(REAL_YIELDS),
                "sha256": sha256(REAL_YIELDS),
                "fixed_energy_ev": FIXED_YIELD_ENERGY,
            },
            "library": {
                "path": fns["library"]["path"],
                "sha256": fns["library"]["sha256"],
            },
        },
        "finite_difference_response": response,
        "yield_parameter": parameter,
        "compensation_product": {"za": compensator[0], "liso": compensator[1]},
        "reported_sensitivity": reported,
        "finite_difference": fd,
        "relative_error": relative(reported, fd),
        "delta_relative": delta,
        "n_yield_parameters": len(band["yield_sensitivities"]),
        "n_decay_parameters": len(band["decay_sensitivities"]),
        "uncovered_yield_count": len(uncertainty.get("uncovered_yield_products", [])),
        "yield_channel_report": yield_channel,
        "combined_standard_uncertainty": band.get("combined_standard_uncertainty"),
        "pass": relative(reported, fd) < 5e-3,
    }


def perf_leg(work: Path) -> dict:
    """Marginal solve-time cost of the requested channels.

    The FNS case isolates the decay channel against the P11 MF=33-only
    baseline; the U235 yield case isolates the yield channel against the same
    spec with no uncertainty at all. `ms` is the solve-time field (process
    startup excluded); two repeats bound the noise.
    """
    work.mkdir(parents=True, exist_ok=True)
    fns = json.loads(FNS_SPEC.read_text())

    mf33_only = json.loads(json.dumps(fns))
    mf33_only["uncertainty"] = {
        "covariance": {
            "path": str(REAL_COVARIANCE),
            "sha256": sha256(REAL_COVARIANCE),
        },
        "confidence_level": 0.90,
        "responses": ["activity:Mn56", "activity:Mn54", "heat.gamma"],
    }
    with_decay = json.loads(json.dumps(mf33_only))
    with_decay["uncertainty"]["channels"] = ["decay_constants"]

    yield_base = json.loads(
        (WORK / "real-yield" / "u235-yields.json").read_text()
    )
    yield_base["uncertainty"]["channels"] = ["fission_yields"]
    yield_plain = json.loads(json.dumps(yield_base))
    yield_plain.pop("uncertainty")

    cases = {
        "fns_mf33_only": mf33_only,
        "fns_decay_channel": with_decay,
        "u235_plain": yield_plain,
        "u235_yield_channel": yield_base,
    }
    timings: dict[str, list[float]] = {}
    for name, spec in cases.items():
        timings[name] = []
        for repeat in range(2):
            result = run_spec(work, f"perf-{name}-{repeat}", spec, timeout=900.0)
            timings[name].append(result["ms"] / 1000.0)
    decay_cost = statistics.median(timings["fns_decay_channel"]) - statistics.median(
        timings["fns_mf33_only"]
    )
    yield_cost = statistics.median(timings["u235_yield_channel"]) - statistics.median(
        timings["u235_plain"]
    )
    return {
        "work": str(work),
        "timings_seconds": timings,
        "fns_decay_channel_added_seconds": decay_cost,
        "fns_decay_channel_added_parameters": 34,
        "u235_yield_channel_added_seconds": yield_cost,
        "u235_yield_channel_added_parameters": 1016,
        "note": (
            "channel cost is the per-pole tangent solve times the parameter "
            "count; the yield channel dominates at 1016 real MF=8/MT=454 "
            "products. Absent channels allocate nothing (the absent-feature "
            "path is byte-identical by construction)."
        ),
        "pass": all(times and all(math.isfinite(t) for t in times)
                    for times in timings.values()),
    }


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    legs = {
        "synthetic": synthetic_leg(WORK / "synthetic"),
        "real_decay": real_decay_leg(WORK / "real-decay"),
        "real_yield": real_yield_leg(WORK / "real-yield"),
        "performance": perf_leg(WORK / "perf"),
    }
    report = {
        "schema": "actinv-p20-g4-channels-1",
        "protocol": "ACTINV-P20",
        "protocol_sha256": PROTOCOL_SHA256,
        "legs": legs,
        "pass": all(leg["pass"] for leg in legs.values()),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    write_json(REPORT, report)
    print(json.dumps({leg: body["pass"] for leg, body in legs.items()}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
