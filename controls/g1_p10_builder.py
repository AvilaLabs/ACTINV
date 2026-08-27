#!/usr/bin/env python3
"""P10-G1: strict Rust parser/builder parity, determinism, cache isolation and rejection plants."""
from __future__ import annotations

import bisect
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time

ADDRESS_SPACE_BYTES = 2 * 1024**3
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))

import numpy as np
from scipy.integrate import quad

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from endf_common import endf_float, fields, read_list, read_tab1, sections  # noqa: E402


RESULT = ROOT / "results" / "g1_p10_builder.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target" / "release" / "dump"))
TENDL_2023 = Path(
    os.environ.get(
        "ACTINV_TENDL2023_DIR", "/home/connoravila/nuclear-data/tendl-2023/files"
    )
)
EAF_2010 = Path(
    os.environ.get(
        "ACTINV_EAF2010_DIR", "/home/connoravila/nuclear-data/eaf-2010/files"
    )
)
LEGACY_EAF = Path(
    os.environ.get(
        "ACTINV_P10_LEGACY_EAF",
        "/home/connoravila/nuclear-data/eaf-2010/actinv_eaf2010_709g.npz",
    )
)
LEGACY_EAF_INDEX = Path(
    os.environ.get(
        "ACTINV_P10_LEGACY_EAF_INDEX",
        "/home/connoravila/nuclear-data/eaf-2010/actinv_eaf2010_709g_index.json",
    )
)

INPUTS = {
    "neutron_fe56": (
        TENDL_2023 / "n_026-Fe-56_2631.dat",
        "e4abb3573a1d3745b5953a5754ad87d128342048a31c4221333693c962c8d53a",
    ),
    "neutron_w186": (
        Path("/home/connoravila/nuclear-data/fendl-3.2c/endf/n_7443_74-W-186.endf"),
        "bf6bf3bb7a1583be49ae8aab865e75d256e0965f969f38a14d63260b3f4a8744",
    ),
    "neutron_ag107": (
        Path("/home/connoravila/nuclear-data/fendl-3.2c/endf/n_4725_47-Ag-107.endf"),
        "0610e15630cb0837a801611d42b6cd401435ddb93dde1126e63000b83ba14185",
    ),
    "neutron_fr226": (
        TENDL_2023 / "n_087-Fr-226_8767.dat",
        "5a2f9fa9b5f53cdf132444694f2502b12fe4f179ca54c06cde0672228df87e67",
    ),
    "neutron_rb94": (
        TENDL_2023 / "n_037-Rb-94_3752.dat",
        "0e25329d3881b7af74419ae3a78495c01470bf304c9f9ecc03a2a91416b693f0",
    ),
    "eaf_fe56": (
        EAF_2010 / "n_2631_26-FE-56.dat",
        "af8e32e7ed025949b65959980d9e2cd5fb6f5ce3c6a7adf2cb4afaac5976d5ab",
    ),
    "proton_fe56_2017": (
        Path(os.environ.get("ACTINV_P10_P2017", "/tmp/p-Fe056.tendl2017")),
        "a817e16d7e5b2bbcc0a8fa4091c9505e4c0364326f26e1727ac72d2c229b6d3a",
    ),
    "deuteron_fe56_2017": (
        Path(os.environ.get("ACTINV_P10_D2017", "/tmp/d-Fe056.tendl2017")),
        "ebb4e2af6ceed337b7355233ddbd29912adb223519fc966b4ea01173acedab9a",
    ),
    "alpha_fe56_2017": (
        Path(os.environ.get("ACTINV_P10_A2017", "/tmp/a-Fe056.tendl2017")),
        "e6a2a93837a279eac7000a97bd4168f9efe91349097c0479f3645e7e8b7bac07",
    ),
    "proton_fe56_2025": (
        Path(os.environ.get("ACTINV_P10_P2025", "/tmp/p-Fe056.tendl2025")),
        "7a505214adb273a2e71fba7ced0ea792dae853875127111af770ee658e01740b",
    ),
    "deuteron_fe56_2025": (
        Path(os.environ.get("ACTINV_P10_D2025", "/tmp/d-Fe056.tendl2025")),
        "cd036d5529c71998d20aa10ae7e8b1d9ae1d7045200b29f46163e5b2faf6ab95",
    ),
    "alpha_fe56_2025": (
        Path(os.environ.get("ACTINV_P10_A2025", "/tmp/a-Fe056.tendl2025")),
        "ff185f3fdf69b6a64a3e9e9eb3964bd8856262b635d674537076c329069a656e",
    ),
}

PINNED = {
    ROOT / "protocols" / "ACTINV-P10_PROTOCOL.md": "74273ec549d113b24367341d1f94f57d0070795d6e679b84a1921d64dbc85b27",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_A.md": "e7fb61dc755f02675c92c57d2f13f6872a6087e24165b0b3fd128dc86df140fd",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_B.md": "36fe887080b03af2851c00a92ebcd5fe93fa4f4bded69c37415ead2626f8cc23",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_C.md": "afa3f1ab58236a36148fe51265cc1d3fe2ae1de31b9b4a9a4a18d0fdd45145de",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_D.md": "5cd79e5ad00ee618b91ddb1b73e795b0cfa4de93c7ebb34c0bce33245e0e5971",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_E.md": "31313e5fb09bd4e969b4cc552beebb7997208197114ceb2b362eabae4de1ffa8",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_F.md": "1746c478a3e31025c0a98446f8567daac67a192eea08b27c76a03503c4a42e49",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_G.md": "390440fa79e3aac05dba6a7de404376f9af89798dba40812703e5dc388e16ac7",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_H.md": "7c2c121ec2007696e824c1aa3ff3b948bf52f79746313b9bc2f6b5661704519a",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_I.md": "84d71f4bcdbf28cc40d4f5e58c12d7f8ed3f1dbe5dc869b13a8ca8db54f3a3c5",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_J.md": "df7bdb47f1ff59d3c58b916a3414aa528c0f0278cca6d1adf67142b51c149dd9",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_K.md": "22a6029aa817206ce52800d943aeadbbd8b9f4e02a9708149f8794860b5733c4",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_L.md": "d2f27d7fdb1765246bc67bacb1199c15dfe43e373fbb96d8691f355b214b2873",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_M.md": "cd6f73ff415a8b2a34049912766f0b1c838519ea3f0deff7e7bc856115ad0596",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_N.md": "6f33ab8d4adc127440c97f5cb7d1393859e417e51716f198f2645eb8b74a15c3",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_O.md": "7be97e0613739765006026dccb2d03c645aaaba246dad60ef82de0ce95a11223",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_P.md": "48482941dd029660e0176f275cb353e967bf52885b24dbb787284c5d0d7f7480",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_Q.md": "c6b1aea580469409ff7890f99e9b6d68b98f5adcd4d66e2783e2893e7ece65d9",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_R.md": "51f524aba1b36ac6d50606bbd107a474a2a2b6311593c72e230d0bf2dd51dc11",
    ROOT / "results" / "g2_p10_rmatrix.json": "5e289aa027fc22373704ba820123ac3cf31a95dfe515a8faed7c4bc62983d81f",
    ROOT / "results" / "g3_p10_unresolved_njoy.json": "4516977dfa48c0f67d30468d4066bd117dc303196d32eff79d8c6e710216850e",
    ROOT / "results" / "g3_p10_unresolved_quadrature.json": "cf99038cda1d0031f5ccb7e75e3799c8caf2eb518131b28feb8e6073505eabba",
    ROOT / "results" / "g4_p10_temperature_narrow.json": "c80ce8b6381267c950c9609ee523a3a07ff72b28aebf2785babfdbe46ec6e509",
    ROOT / "results" / "g5_p10_charged.json": "dc7daf0b45ae028405cbbca6ec977ab8ded11461f2969330ba6ae48bcd704b17",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def limit_address_space() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))


def run_limited(
    arguments: list[object],
    *,
    check: bool = True,
    timed: bool = False,
    address_space_bytes: int = ADDRESS_SPACE_BYTES,
) -> subprocess.CompletedProcess[str]:
    command = [str(value) for value in arguments]
    if timed:
        command = ["/usr/bin/time", "-v", *command]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=lambda: resource.setrlimit(
            resource.RLIMIT_AS, (address_space_bytes, address_space_bytes)
        ),
        check=False,
    )
    if check and completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout[-4000:]}\nstderr:\n{completed.stderr[-4000:]}"
        )
    return completed


def integer(value: str) -> int:
    return int(value) if value.strip() else 0


def cont(line: str) -> tuple[float, float, int, int, int, int]:
    value = fields(line)
    return (
        endf_float(value[0]),
        endf_float(value[1]),
        integer(value[2]),
        integer(value[3]),
        integer(value[4]),
        integer(value[5]),
    )


def table_json(record: tuple) -> dict:
    return {
        "interpolation": [list(value) for value in record[6]],
        "x": list(record[7]),
        "y": list(record[8]),
    }


def read_tab2(lines: list[str], index: int) -> tuple[tuple, int]:
    head = cont(lines[index])
    index += 1
    interpolation = []
    while len(interpolation) < head[4]:
        value = fields(lines[index])
        index += 1
        for offset in range(0, 6, 2):
            if len(interpolation) < head[4]:
                interpolation.append(
                    [integer(value[offset]), integer(value[offset + 1])]
                )
    return (*head, interpolation), index


def parse_rml_extension(lines: list[str], index: int) -> tuple[dict, int]:
    _, _, channel, law, _, _ = cont(lines[index])
    index += 1
    real = imaginary = None
    parameters = []
    if law == 1:
        record, index = read_tab1(lines, index)
        real = table_json(record)
        record, index = read_tab1(lines, index)
        imaginary = table_json(record)
    elif law in (2, 3):
        record, index = read_list(lines, index)
        parameters = list(record[6])
    elif law != 0:
        raise ValueError(f"unsupported independent RML extension law {law}")
    return {
        "channel": channel,
        "law": law,
        "real": real,
        "imaginary": imaginary,
        "parameters": parameters,
    }, index


def parse_rmatrix_limited(lines: list[str], index: int) -> tuple[dict, int]:
    _, _, ifg, krm, spin_group_count, _ = cont(lines[index])
    index += 1
    pair_record, index = read_list(lines, index)
    pair_count = pair_record[2]
    if pair_record[4] != 12 * pair_count or pair_record[5] != 2 * pair_count:
        raise ValueError("independent RML pair counts disagree")
    pairs = []
    for offset in range(0, len(pair_record[6]), 12):
        value = pair_record[6][offset : offset + 12]
        pairs.append(
            {
                "mass_a": value[0],
                "mass_b": value[1],
                "za": round(value[2]),
                "zb": round(value[3]),
                "spin_a": value[4],
                "spin_b": value[5],
                "q_value": value[6],
                "penetrability": round(value[7]),
                "shift": round(value[8]),
                "mt": round(value[9]),
                "parity_a": round(value[10]),
                "parity_b": round(value[11]),
            }
        )
    spin_groups = []
    for _ in range(spin_group_count):
        channel_record, index = read_list(lines, index)
        channel_count = channel_record[5]
        if channel_record[4] != 6 * channel_count:
            raise ValueError("independent RML channel counts disagree")
        channels = []
        for offset in range(0, len(channel_record[6]), 6):
            value = channel_record[6][offset : offset + 6]
            channels.append(
                {
                    "pair": round(value[0]) - 1,
                    "l": round(value[1]),
                    "spin": value[2],
                    "boundary": value[3],
                    "effective_radius": value[4],
                    "true_radius": value[5],
                }
            )
        resonance_record, index = read_list(lines, index)
        resonance_count = resonance_record[3]
        values_per = 6 * math.ceil((channel_count + 1) / 6)
        if (
            resonance_record[5] != resonance_count
            or resonance_record[4] != values_per * resonance_count
        ):
            raise ValueError("independent RML resonance counts disagree")
        resonances = []
        for offset in range(0, len(resonance_record[6]), values_per):
            value = resonance_record[6][offset : offset + values_per]
            resonances.append(
                {"energy": value[0], "widths": value[1 : channel_count + 1]}
            )
        backgrounds = []
        for _ in range(channel_record[2]):
            value, index = parse_rml_extension(lines, index)
            backgrounds.append(value)
        phase_shifts = []
        for _ in range(channel_record[3]):
            value, index = parse_rml_extension(lines, index)
            phase_shifts.append(value)
        spin_groups.append(
            {
                "spin": channel_record[0],
                "parity": channel_record[1],
                "channels": channels,
                "resonances": resonances,
                "backgrounds": backgrounds,
                "phase_shifts": phase_shifts,
            }
        )
    return {
        "reduced_widths": ifg == 1,
        "krm": krm,
        "particle_pairs": pairs,
        "spin_groups": spin_groups,
    }, index


UNRESR_MANTISSAS = (1.0, 1.25, 1.5, 1.7, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.2, 8.5)
UNRESR_DECADES = (10.0, 100.0, 1e3, 1e4, 1e5, 1e6)


def unresolved_mesh(low: float, high: float, case: str, energies: list[float]) -> list[float]:
    source = sorted([*energies, low, high])
    unique = []
    for value in source:
        if not unique or value != unique[-1]:
            unique.append(value)
    mesh = [unique[0]]
    for upper in unique[1:]:
        lower = mesh[-1]
        if case == "A" or upper >= 1.26 * lower:
            cursor = lower
            for decade in UNRESR_DECADES:
                for mantissa in UNRESR_MANTISSAS:
                    candidate = mantissa * decade
                    if candidate > 1.01 * cursor and candidate < upper:
                        mesh.append(candidate)
                        cursor = candidate
        mesh.append(upper)
    return mesh


def parse_unresolved(
    lines: list[str], index: int, lrf: int, fission_widths: bool, low: float, high: float
) -> tuple[dict, int]:
    case = "C" if lrf == 2 else ("B" if fission_widths else "A")
    if case == "B":
        control, index = read_list(lines, index)
    else:
        head = cont(lines[index])
        index += 1
        control = (*head, [])
    spin, ap, lssf = control[0], control[1], control[2]
    sequences = []
    if case == "A":
        for _ in range(control[4]):
            record, index = read_list(lines, index)
            for offset in range(0, len(record[6]), 6):
                value = record[6][offset : offset + 6]
                sequences.append(
                    {
                        "awri": record[0],
                        "l": record[2],
                        "spin": value[1],
                        "interpolation": 2,
                        "competitive_dof": 0,
                        "neutron_dof": round(value[2]),
                        "fission_dof": 0,
                        "points": [
                            {
                                "energy": None,
                                "spacing": value[0],
                                "competitive": 0.0,
                                "neutron": value[3],
                                "capture": value[4],
                                "fission": 0.0,
                            }
                        ],
                    }
                )
    elif case == "B":
        energies = control[6]
        for _ in range(control[5]):
            l_record = cont(lines[index])
            index += 1
            for _ in range(l_record[4]):
                record, index = read_list(lines, index)
                points = []
                for energy, fission in zip(energies, record[6][6:]):
                    points.append(
                        {
                            "energy": energy,
                            "spacing": record[6][1],
                            "competitive": 0.0,
                            "neutron": record[6][4],
                            "capture": record[6][5],
                            "fission": fission,
                        }
                    )
                sequences.append(
                    {
                        "awri": l_record[0],
                        "l": l_record[2],
                        "spin": record[6][2],
                        "interpolation": 2,
                        "competitive_dof": 0,
                        "neutron_dof": round(record[6][3]),
                        "fission_dof": round(record[6][0]),
                        "points": points,
                    }
                )
    else:
        for _ in range(control[4]):
            l_record = cont(lines[index])
            index += 1
            for _ in range(l_record[4]):
                record, index = read_list(lines, index)
                dof = record[6][:6]
                points = []
                for offset in range(6, len(record[6]), 6):
                    value = record[6][offset : offset + 6]
                    points.append(
                        {
                            "energy": value[0],
                            "spacing": value[1],
                            "competitive": value[2],
                            "neutron": value[3],
                            "capture": value[4],
                            "fission": value[5],
                        }
                    )
                sequences.append(
                    {
                        "awri": l_record[0],
                        "l": l_record[2],
                        "spin": record[0],
                        "interpolation": record[2],
                        "competitive_dof": round(dof[2]),
                        "neutron_dof": round(dof[3]),
                        "fission_dof": round(dof[5]),
                        "points": points,
                    }
                )
    parameter_energies = [
        point["energy"]
        for sequence in sequences
        for point in sequence["points"]
        if point["energy"] is not None
    ]
    return {
        "spin": spin,
        "ap": ap,
        "add_to_background": lssf == 0,
        "case": case,
        "sequences": sequences,
        "interpolation_energies": unresolved_mesh(
            low, high, case, parameter_energies
        ),
    }, index


def parse_mf2(lines: list[str]) -> dict:
    head = cont(lines[0])
    index = 1
    isotopes = []
    for _ in range(head[4]):
        isotope = cont(lines[index])
        index += 1
        ranges = []
        for _ in range(isotope[4]):
            range_head = cont(lines[index])
            index += 1
            low, high, lru, lrf, nro, naps = range_head
            scattering_radius = None
            if nro == 1:
                record, index = read_tab1(lines, index)
                scattering_radius = table_json(record)
            if (lru, lrf) == (0, 0):
                control = cont(lines[index])
                index += 1
                data = {"ScatteringOnly": {"spin": control[0], "ap": control[1]}}
            elif lru == 1 and lrf in (1, 2, 3):
                control = cont(lines[index])
                index += 1
                groups = []
                for _ in range(control[4]):
                    record, index = read_list(lines, index)
                    resonances = []
                    for offset in range(0, len(record[6]), 6):
                        value = record[6][offset : offset + 6]
                        if lrf <= 2:
                            resonances.append(
                                {
                                    "energy": value[0],
                                    "spin": value[1],
                                    "total": value[2],
                                    "neutron": value[3],
                                    "capture": value[4],
                                    "fission_a": value[5],
                                    "fission_b": 0.0,
                                }
                            )
                        else:
                            resonances.append(
                                {
                                    "energy": value[0],
                                    "spin": value[1],
                                    "total": 0.0,
                                    "neutron": value[2],
                                    "capture": value[3],
                                    "fission_a": value[4],
                                    "fission_b": value[5],
                                }
                            )
                    groups.append(
                        {
                            "awri": record[0],
                            "apl": record[1] if lrf == 3 else 0.0,
                            "qx": record[1] if lrf <= 2 else 0.0,
                            "l": record[2],
                            "lrx": record[3] if lrf <= 2 else 0,
                            "resonances": resonances,
                        }
                    )
                value = {"spin": control[0], "ap": control[1], "groups": groups}
                data = {"ReichMoore" if lrf == 3 else "BreitWigner": value}
            elif (lru, lrf) == (1, 7):
                value, index = parse_rmatrix_limited(lines, index)
                data = {"RMatrixLimited": value}
            elif lru == 2 and lrf in (1, 2):
                value, index = parse_unresolved(
                    lines, index, lrf, isotope[3] == 1, low, high
                )
                data = {"Unresolved": value}
            else:
                raise ValueError(f"unsupported independent MF2 LRU={lru}/LRF={lrf}")
            ranges.append(
                {
                    "energy_min": low,
                    "energy_max": high,
                    "lru": lru,
                    "lrf": lrf,
                    "naps": naps,
                    "scattering_radius": scattering_radius,
                    "data": data,
                }
            )
        isotopes.append(
            {
                "zai": round(isotope[0]),
                "abundance": isotope[1],
                "fission_widths": isotope[3] == 1,
                "ranges": ranges,
            }
        )
    if index != len(lines):
        raise ValueError(f"independent MF2 left {len(lines) - index} records")
    return {"za": round(head[0]), "awr": head[1], "isotopes": isotopes}


def consume_mf6_law(lines: list[str], index: int, law: int) -> int:
    if law in (0, 3, 4):
        return index
    if law in (1, 2, 5):
        tab2, index = read_tab2(lines, index)
        for _ in range(tab2[5]):
            _, index = read_list(lines, index)
        return index
    if law == 6:
        return index + 1
    if law == 7:
        energies, index = read_tab2(lines, index)
        for _ in range(energies[5]):
            angles, index = read_tab2(lines, index)
            for _ in range(angles[5]):
                _, index = read_tab1(lines, index)
        return index
    raise ValueError(f"unsupported independent MF6 LAW={law}")


def parse_mf6(lines: list[str]) -> list[dict]:
    head = cont(lines[0])
    index = 1
    products = []
    for _ in range(head[4]):
        record, next_index = read_tab1(lines, index)
        law = record[3]
        products.append(
            {
                "zap": round(record[0]),
                "awp": record[1],
                "law": law,
                "yield_table": table_json(record),
            }
        )
        index = consume_mf6_law(lines, next_index, law)
    if index != len(lines):
        raise ValueError(f"independent MF6 left {len(lines) - index} records")
    return products


def parse_mf8(lines: list[str]) -> list[dict]:
    head = cont(lines[0])
    index = 1
    products = []
    for _ in range(head[4]):
        if head[5] == 0:
            record, index = read_list(lines, index)
            product_head = record[:6]
        else:
            product_head = cont(lines[index])
            index += 1
        products.append(
            {
                "zap": -1 if product_head[0] == -1.0 else round(product_head[0]),
                "lfs": product_head[3],
                "lmf": product_head[2],
            }
        )
    if index != len(lines):
        raise ValueError(f"independent MF8 left {len(lines) - index} records")
    return products


def parse_product_tables(lines: list[str]) -> list[dict]:
    head = cont(lines[0])
    index = 1
    products = []
    for _ in range(head[4]):
        record, index = read_tab1(lines, index)
        products.append(
            {
                "zap": record[2],
                "lfs": record[3],
                "table": table_json(record),
            }
        )
    if index != len(lines):
        raise ValueError(f"independent product section left {len(lines) - index} records")
    return products


def parse_evaluations(path: Path) -> list[dict]:
    by_mat: dict[int, list[tuple[int, int, list[str]]]] = {}
    for (mat, mf, mt), lines in sections(path):
        by_mat.setdefault(mat, []).append((mf, mt, lines))
    evaluations = []
    projectile_names = {10: "neutron", 10010: "proton", 10020: "deuteron", 20040: "alpha"}
    for mat in sorted(by_mat):
        material = by_mat[mat]
        directory = next(lines for mf, mt, lines in material if (mf, mt) == (1, 451))
        target = cont(directory[1])
        incident = cont(directory[2])
        processing = cont(directory[3])
        metadata = {
            "mat": mat,
            "za": round(cont(directory[0])[0]),
            "awr": cont(directory[0])[1],
            "liso": target[3],
            "awi": incident[0],
            "nsub": incident[4],
            "projectile": projectile_names[incident[4]],
            "evaluation_temperature_k": processing[0],
        }
        result = {
            "metadata": metadata,
            "mf2_sections": [],
            "resonance": None,
            "mf3": {},
            "mf6": {},
            "mf8": {},
            "mf9": {},
            "mf10": {},
        }
        for mf, mt, lines in material:
            key = str(mt)
            if mf == 2:
                result["mf2_sections"].append(mt)
                if mt == 151:
                    result["resonance"] = parse_mf2(lines)
            elif mf == 3:
                record, index = read_tab1(lines, 1)
                if index != len(lines):
                    raise ValueError("independent MF3 left records")
                result["mf3"][key] = table_json(record)
            elif mf == 6:
                result["mf6"][key] = parse_mf6(lines)
            elif mf == 8 and mt not in (454, 457, 459):
                result["mf8"][key] = parse_mf8(lines)
            elif mf == 9:
                result["mf9"][key] = parse_product_tables(lines)
            elif mf == 10:
                result["mf10"][key] = parse_product_tables(lines)
        result["mf2_sections"].sort()
        evaluations.append(result)
    return evaluations


def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, float):
        return format(value, ".10g")
    return value


def structure_hash(value) -> str:
    encoded = json.dumps(normalize(value), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def compare_structures(independent, rust) -> dict:
    fields_compared = 0
    float_fields = 0
    binary_differences = 0
    maximum_ulp = 0.0
    mismatches = []

    def visit(left, right, path: str) -> None:
        nonlocal fields_compared, float_fields, binary_differences, maximum_ulp
        if isinstance(left, dict) and isinstance(right, dict):
            if set(left) != set(right):
                mismatches.append(f"{path}: keys {sorted(left)}/{sorted(right)}")
                return
            for key in sorted(left):
                visit(left[key], right[key], f"{path}/{key}")
            return
        if isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                mismatches.append(f"{path}: lengths {len(left)}/{len(right)}")
                return
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                visit(left_item, right_item, f"{path}/{index}")
            return
        fields_compared += 1
        if isinstance(left, float) and isinstance(right, float):
            float_fields += 1
            if left != right:
                binary_differences += 1
                maximum_ulp = max(
                    maximum_ulp,
                    abs(left - right) / max(math.ulp(left), math.ulp(right)),
                )
            if format(left, ".10g") != format(right, ".10g"):
                mismatches.append(f"{path}: {left!r}/{right!r}")
        elif left != right:
            mismatches.append(f"{path}: {left!r}/{right!r}")

    visit(independent, rust, "")
    return {
        "fields_compared": fields_compared,
        "float_fields": float_fields,
        "binary_representation_differences": binary_differences,
        "maximum_ulp_distance": maximum_ulp,
        "source_decimal_mismatches": len(mismatches),
        "mismatch_examples": mismatches[:10],
        "exact_binary64": binary_differences == 0,
        "exact_at_endf_source_precision": not mismatches,
    }


RELATIVE_TOLERANCE = 2e-12
ABSOLUTE_TOLERANCE_B = 1e-14
SCORE_FLOOR_B = 1e-12


def x_minus_ln_1p(value: float) -> float:
    """Stable x - log(1+x), independently matching the analytic lin-lin integral."""
    if abs(value) >= 1e-3:
        return value - math.log1p(value)
    power = value * value
    total = 0.5 * power
    for denominator in range(3, 13):
        power *= value
        term = power / denominator
        total += term if denominator % 2 == 0 else -term
    return total


def expm1_over_x(value: float) -> float:
    if abs(value) < 1e-8:
        return 1.0 + value * (0.5 + value * (1.0 / 6.0 + value / 24.0))
    return math.expm1(value) / value


def table_laws(table: dict) -> list[int]:
    laws = []
    region = 0
    for segment in range(len(table["x"]) - 1):
        endpoint = segment + 2
        while endpoint > table["interpolation"][region][0]:
            region += 1
        laws.append(table["interpolation"][region][1])
    return laws


def segment_value(table: dict, segment: int, energy: float, law: int) -> float:
    x1, x2 = table["x"][segment : segment + 2]
    y1, y2 = table["y"][segment : segment + 2]
    if x2 == x1:
        return y2
    linear = (energy - x1) / (x2 - x1)
    if law == 1:
        return y1
    if law == 2:
        return y1 + linear * (y2 - y1)
    if law == 3:
        return y1 + math.log(energy / x1) / math.log(x2 / x1) * (y2 - y1)
    if law == 4:
        return y1 * (y2 / y1) ** linear
    if law == 5:
        return y1 * (y2 / y1) ** (math.log(energy / x1) / math.log(x2 / x1))
    raise ValueError(f"unsupported independent TAB1 interpolation INT={law}")


def segment_lethargy_integral(
    table: dict, segment: int, low: float, high: float, law: int
) -> float:
    x1, x2 = table["x"][segment : segment + 2]
    y1, y2 = table["y"][segment : segment + 2]
    ratio_minus_one = (high - low) / low
    log_ratio = math.log1p(ratio_minus_one)
    if law == 1:
        return y1 * log_ratio
    if law == 2:
        slope = (y2 - y1) / (x2 - x1)
        value_at_low = segment_value(table, segment, low, law)
        return value_at_low * log_ratio + slope * low * x_minus_ln_1p(
            ratio_minus_one
        )
    if law == 3:
        return (
            0.5
            * (
                segment_value(table, segment, low, law)
                + segment_value(table, segment, high, law)
            )
            * log_ratio
        )
    if law == 4:
        # Integrating in log-energy makes the lethargy measure exactly dt.  QUADPACK is an implementation-independent
        # oracle for Rust's adaptive Simpson path.
        value, error = quad(
            lambda log_energy: segment_value(
                table, segment, math.exp(log_energy), law
            ),
            math.log(low),
            math.log(high),
            epsabs=1e-300,
            epsrel=5e-14,
            limit=100,
        )
        if error > max(1e-13 * abs(value), 1e-280):
            raise ArithmeticError(
                f"independent INT=4 quadrature uncertainty {error} for integral {value}"
            )
        return value
    if law == 5:
        power = math.log(y2 / y1) / math.log(x2 / x1)
        value_at_low = segment_value(table, segment, low, law)
        return value_at_low * log_ratio * expm1_over_x(power * log_ratio)
    raise ValueError(f"unsupported independent TAB1 interpolation INT={law}")


def collapse_table(table: dict, bounds: np.ndarray) -> np.ndarray:
    """Exact flat-lethargy group collapse, with finite support and ENDF discontinuities."""
    x = table["x"]
    laws = table_laws(table)
    collapsed = np.zeros(len(bounds) - 1)
    for group, (group_low, group_high) in enumerate(zip(bounds[:-1], bounds[1:])):
        low = max(float(group_low), x[0])
        high = min(float(group_high), x[-1])
        if high <= low:
            continue
        start = min(max(bisect.bisect_right(x, low) - 1, 0), len(x) - 2)
        pieces = []
        for segment in range(start, len(x) - 1):
            x1, x2 = x[segment : segment + 2]
            if x1 >= high:
                break
            if x2 <= low or x2 <= x1:
                continue
            a = max(low, x1)
            b = min(high, x2)
            if b > a:
                pieces.append(
                    segment_lethargy_integral(table, segment, a, b, laws[segment])
                )
        collapsed[group] = math.fsum(pieces) / math.log(group_high / group_low)
    return collapsed


def skip_mt(mt: int, projectile: str, has_mf6: bool = False) -> bool:
    return (
        mt in (1, 2, 3, 27, 101, 444, 19, 20, 21, 38)
        or (mt == 5 and not (projectile != "neutron" and has_mf6))
        or 201 <= mt <= 207
        or 600 <= mt <= 849
        or mt >= 1000
    )


def inelastic(mt: int) -> bool:
    return mt == 4 or 51 <= mt <= 91


def neutron_residual(target_za: int, delta: list[int]) -> int | None:
    z, a = divmod(target_za, 1000)
    z += delta[0]
    a += delta[1]
    return z * 1000 + a if z > 0 and a > 0 and a >= z else None


def independent_eaf_rows(
    evaluation: dict, bounds: np.ndarray
) -> tuple[dict[tuple[int, int, int, int], np.ndarray], dict[tuple[int, int, int, int], dict | None]]:
    """Build the pinned EAF target without invoking Rust or the legacy Python builder."""
    if evaluation["metadata"]["projectile"] != "neutron":
        raise ValueError("independent EAF oracle requires a neutron evaluation")
    if evaluation["mf6"] or evaluation["mf9"]:
        raise ValueError("pinned EAF oracle unexpectedly requires MF=6 or nonlinear MF=9")
    mt_products = json.loads(
        (ROOT / "crates" / "actinv-data" / "data" / "mt_products.json").read_text()
    )["table"]
    records = []
    for mt in sorted({*map(int, evaluation["mf3"]), *map(int, evaluation["mf10"])}):
        descriptors = evaluation["mf8"].get(str(mt), [])
        if skip_mt(mt, "neutron"):
            continue
        if inelastic(mt):
            products = evaluation["mf10"].get(str(mt), [])
            metastable = [product for product in products if product["lfs"] > 0]
            if not metastable:
                continue
            values = [(product, collapse_table(product["table"], bounds)) for product in metastable]
            total = np.sum([value for _, value in values], axis=0)
            records.append(([mt, -1, -1, 0], total, None))
            records.extend(
                ([mt, product["zap"], product["lfs"], 10], value, product["table"])
                for product, value in values
            )
            continue
        total_source = evaluation["mf3"].get(str(mt))
        if total_source is not None:
            total = collapse_table(total_source, bounds)
        else:
            products = evaluation["mf10"].get(str(mt), [])
            sentinel = next((product for product in products if product["zap"] == -1), None)
            if sentinel is not None:
                total_source = sentinel["table"]
                total = collapse_table(total_source, bounds)
            else:
                components = [
                    collapse_table(product["table"], bounds)
                    for product in products
                    if product["zap"] >= 0
                ]
                total = np.sum(components, axis=0)
        records.append(([mt, -1, -1, 0], total, total_source))
        done = set()
        for product in evaluation["mf10"].get(str(mt), []):
            if product["zap"] < 0:
                continue
            identity = (product["zap"], product["lfs"])
            if identity in done:
                raise ValueError(f"duplicate independent EAF product MT{mt}/{identity}")
            done.add(identity)
            records.append(
                (
                    [mt, product["zap"], product["lfs"], 10],
                    collapse_table(product["table"], bounds),
                    product["table"],
                )
            )
        for descriptor in (value for value in descriptors if value["lmf"] == 3):
            identity = (descriptor["zap"], descriptor["lfs"])
            if identity in done:
                raise ValueError(f"conflicting independent EAF product MT{mt}/{identity}")
            done.add(identity)
            records.append(
                ([mt, descriptor["zap"], descriptor["lfs"], 3], total.copy(), total_source)
            )
        if not done:
            if mt == 18:
                product, lmf = 0, 0
            elif str(mt) in mt_products:
                product = neutron_residual(
                    evaluation["metadata"]["za"], mt_products[str(mt)]
                )
                product, lmf = (product, -1) if product is not None else (0, -2)
            else:
                product, lmf = 0, -2
            records.append(([mt, product, 0, lmf], total.copy(), total_source))

    # Positive LFS identifiers are decay-isomer ordinals, not necessarily the sparse ENDF level numbers.
    for mt, zap in sorted({(key[0], key[1]) for key, _, _ in records}):
        levels = sorted(
            {key[2] for key, _, _ in records if key[0] == mt and key[1] == zap and key[2] > 0}
        )
        remap = {level: index + 1 for index, level in enumerate(levels)}
        for key, _, _ in records:
            if key[0] == mt and key[1] == zap and key[2] > 0:
                key[2] = remap[key[2]]
    rows = {}
    sources = {}
    for key, values, source in records:
        identity = tuple(key)
        if identity in rows:
            raise ValueError(f"duplicate independent row identity {identity}")
        rows[identity] = values
        sources[identity] = source
    return rows, sources


def target_number(index_path: Path, za: int, liso: int) -> int:
    index = json.loads(index_path.read_text())
    matches = [
        number
        for number, target in enumerate(index["targets"])
        if target["za"] == za and target["liso"] == liso
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one ZA={za}/LISO={liso} target in {index_path}")
    return matches[0]


def extract_target(library: Path, target: int, prefix: Path) -> None:
    run_limited([DUMP, "library-target", library, target, prefix])


def read_target(prefix: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = np.fromfile(f"{prefix}.rows", dtype="<i8").reshape(-1, 4)
    bounds = np.fromfile(f"{prefix}.bounds", dtype="<f8")
    sigma = np.fromfile(f"{prefix}.sig", dtype="<f8").reshape(len(rows), len(bounds) - 1)
    return rows, sigma, bounds


def score_points(points) -> dict:
    scored = 0
    failures = []
    maximum_absolute = 0.0
    maximum_relative = 0.0
    maximum_tolerance_fraction = 0.0
    worst = None
    for a, b, identity, group in points:
        magnitude = max(abs(float(a)), abs(float(b)))
        if magnitude < SCORE_FLOOR_B:
            continue
        scored += 1
        absolute = abs(float(a) - float(b))
        relative = absolute / magnitude
        tolerance = max(ABSOLUTE_TOLERANCE_B, RELATIVE_TOLERANCE * magnitude)
        fraction = absolute / tolerance
        current = {
            "identity": list(identity),
            "group": group,
            "left_b": float(a),
            "right_b": float(b),
            "absolute_b": absolute,
            "relative": relative,
            "tolerance_b": tolerance,
        }
        maximum_absolute = max(maximum_absolute, absolute)
        maximum_relative = max(maximum_relative, relative)
        if fraction > maximum_tolerance_fraction:
            maximum_tolerance_fraction = fraction
            worst = current
        if absolute > tolerance and len(failures) < 10:
            failures.append(current)
    return {
        "scored_groups": scored,
        "maximum_absolute_b": maximum_absolute,
        "maximum_relative": maximum_relative,
        "worst_tolerance_fraction": maximum_tolerance_fraction,
        "worst": worst,
        "failure_examples": failures,
        "pass": not failures,
    }


def score_arrays(left: np.ndarray, right: np.ndarray, identities: list[tuple]) -> dict:
    if left.shape != right.shape:
        raise ValueError(f"cannot score shapes {left.shape} and {right.shape}")
    return score_points(
        (a, b, identity, group)
        for row, identity in enumerate(identities)
        for group, (a, b) in enumerate(zip(left[row], right[row]))
    )


TABLE_ANALYSIS_CACHE: dict[int, tuple[list[int], list[float]]] = {}


def table_analysis(table: dict) -> tuple[list[int], list[float]]:
    key = id(table)
    if key not in TABLE_ANALYSIS_CACHE:
        TABLE_ANALYSIS_CACHE[key] = (
            table_laws(table),
            [
                table["x"][index]
                for index in range(len(table["x"]) - 1)
                if table["x"][index] == table["x"][index + 1]
                and table["y"][index] != table["y"][index + 1]
            ],
        )
    return TABLE_ANALYSIS_CACHE[key]


def unchanged_group_reasons(table: dict | None, low: float, high: float) -> set[str]:
    if table is None:
        return {"no_single_direct_source"}
    x = table["x"]
    reasons = set()
    if low < x[0] or high > x[-1]:
        reasons.add("source_edge")
    laws, duplicates = table_analysis(table)
    start = min(max(bisect.bisect_right(x, low) - 1, 0), len(x) - 2)
    stop = min(bisect.bisect_left(x, high), len(x) - 1)
    overlapping = [
        segment
        for segment in range(start, stop)
        if x[segment + 1] > low
        and x[segment] < high
        and x[segment + 1] > x[segment]
    ]
    if not overlapping:
        reasons.add("no_source_segment")
    elif any(laws[segment] != 2 for segment in overlapping):
        reasons.add("non_lin_lin")
    if any(low <= value <= high for value in duplicates):
        reasons.add("value_changing_duplicate")
    return reasons


def legacy_linlin_roundoff_bound(table: dict, low: float, high: float) -> float:
    """Conservative P2 primitive bound, including ratio-then-log input rounding."""
    x = table["x"]
    start = bisect.bisect_right(x, low)
    stop = bisect.bisect_left(x, high)
    grid = [low, *x[start:stop], high]
    terms = []
    intercepts = []
    for a, b in zip(grid[:-1], grid[1:]):
        segment = min(max(bisect.bisect_right(x, a) - 1, 0), len(x) - 2)
        y_a = segment_value(table, segment, a, 2)
        y_b = segment_value(table, segment, b, 2)
        slope = (y_b - y_a) / (b - a)
        intercept = y_a - slope * a
        intercepts.append(intercept)
        terms.extend((intercept * math.log(b / a), slope * (b - a)))
    operations = 32 * max(len(grid) - 1, 1) + 64
    epsilon = np.finfo(float).eps
    gamma = operations * epsilon / (1.0 - operations * epsilon)
    group_log = math.log1p((high - low) / low)
    log_allowance = 8.0 * epsilon
    if group_log <= log_allowance:
        return math.inf
    absolute_terms = math.fsum(abs(value) for value in terms)
    numerator_bound = gamma * absolute_terms + log_allowance * math.fsum(
        abs(value) for value in intercepts
    )
    denominator = group_log - log_allowance
    return numerator_bound / denominator + (
        (absolute_terms + numerator_bound)
        * log_allowance
        / (group_log * denominator)
    )


def compare_legacy_unchanged_domain(
    old_rows: np.ndarray,
    old_sigma: np.ndarray,
    new_rows: np.ndarray,
    new_sigma: np.ndarray,
    bounds: np.ndarray,
    sources: dict[tuple[int, int, int, int], dict | None],
) -> tuple[dict, dict]:
    old_map = {tuple(row): index for index, row in enumerate(old_rows.tolist())}
    new_map = {tuple(row): index for index, row in enumerate(new_rows.tolist())}
    if len(old_map) != len(old_rows) or len(new_map) != len(new_rows):
        raise ValueError("bounded EAF slice contains duplicate row identities")
    old_only = sorted(set(old_map) - set(new_map))
    new_only = sorted(set(new_map) - set(old_map))
    structural = {
        "legacy_rows": len(old_rows),
        "rust_rows": len(new_rows),
        "old_only": [list(value) for value in old_only],
        "new_only": [list(value) for value in new_only],
        "enumerated_exceptions": [],
        "pass": not old_only and not new_only,
    }
    reasons: dict[str, int] = {}
    points = []
    eligible_rows = set()
    scored_common = 0
    excluded = 0
    for identity in sorted(set(old_map) & set(new_map)):
        old = old_sigma[old_map[identity]]
        new = new_sigma[new_map[identity]]
        table = sources.get(identity)
        for group, (old_value, new_value) in enumerate(zip(old, new)):
            if max(abs(float(old_value)), abs(float(new_value))) < SCORE_FLOOR_B:
                continue
            scored_common += 1
            group_reasons = unchanged_group_reasons(
                table, float(bounds[group]), float(bounds[group + 1])
            )
            tolerance = max(
                ABSOLUTE_TOLERANCE_B,
                RELATIVE_TOLERANCE * max(abs(float(old_value)), abs(float(new_value))),
            )
            if not group_reasons and legacy_linlin_roundoff_bound(
                table, float(bounds[group]), float(bounds[group + 1])
            ) > tolerance:
                group_reasons.add("legacy_roundoff_bound")
            if group_reasons:
                excluded += 1
                for reason in group_reasons:
                    reasons[reason] = reasons.get(reason, 0) + 1
                continue
            eligible_rows.add(identity)
            points.append((old_value, new_value, identity, group))
    scored = score_points(points)
    scored.update(
        {
            "common_rows": len(set(old_map) & set(new_map)),
            "common_scored_groups": scored_common,
            "eligible_rows": len(eligible_rows),
            "eligible_groups": len(points),
            "excluded_scored_groups": excluded,
            "excluded_by_reason": dict(sorted(reasons.items())),
            "pass": structural["pass"] and scored["pass"],
        }
    )
    return structural, scored


BUILD_ADDRESS_SPACE_BYTES = 1024**3


def index_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_index.json")


def build_arguments(
    source: Path,
    output: Path,
    *,
    format_name: str,
    projectile: str,
    groups: str,
    temperature: float,
    workers: int,
    cache: Path | None = None,
    grid_density: float = 1.0,
) -> list[object]:
    arguments: list[object] = [
        ACTINV,
        "build-library",
        source,
        output,
        "--format",
        format_name,
        "--projectile",
        projectile,
        "--groups",
        groups,
        "--temperature-K",
        temperature,
        "--workers",
        workers,
        "--grid-density",
        grid_density,
    ]
    if cache is not None:
        arguments.extend(["--cache", cache])
    return arguments


def parse_cache_hits(stdout: str) -> int:
    match = re.search(r"\b(\d+) cache hits\b", stdout)
    if not match:
        raise ValueError(f"build summary has no cache-hit count: {stdout[-500:]}")
    return int(match.group(1))


def parse_maximum_rss(stderr: str) -> int:
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", stderr)
    if not match:
        raise ValueError("/usr/bin/time output has no maximum RSS")
    return int(match.group(1))


def artifact_summary(output: Path, completed: subprocess.CompletedProcess[str]) -> dict:
    index = index_path(output)
    if not output.is_file() or not index.is_file():
        raise ValueError(f"successful build did not publish {output.name} and its index")
    document = json.loads(index.read_text())
    return {
        "npz_sha256": sha256(output),
        "index_sha256": sha256(index),
        "cache_hits": parse_cache_hits(completed.stdout),
        "targets": len(document["targets"]),
        "rows": document["n_rows"],
        "builder_fingerprint": document["builder_fingerprint"],
        "group_boundary_sha256": document["group_boundary_sha256"],
    }


def mutate_ignored_mf1_text(path: Path) -> None:
    lines = path.read_bytes().splitlines(keepends=True)
    seen = 0
    for line_number, raw in enumerate(lines):
        record = raw.rstrip(b"\r\n")
        if len(record) < 75:
            continue
        try:
            mf = int(record[70:72])
            mt = int(record[72:75])
        except ValueError:
            continue
        if (mf, mt) != (1, 451):
            continue
        seen += 1
        if seen <= 4:
            continue
        changed = bytearray(raw)
        for offset, value in enumerate(changed[:66]):
            if 65 <= value <= 90 or 97 <= value <= 122:
                changed[offset] = value ^ 32
                lines[line_number] = bytes(changed)
                path.write_bytes(b"".join(lines))
                return
    raise ValueError(f"could not find ignored MF=1/MT=451 text in {path}")


def same_artifacts(*summaries: dict) -> bool:
    return len({value["npz_sha256"] for value in summaries}) == 1 and len(
        {value["index_sha256"] for value in summaries}
    ) == 1


def cache_files(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir() if path.is_file())


def run_determinism_and_cache(work: Path) -> dict:
    work.mkdir(parents=True)
    inputs = work / "determinism-inputs"
    inputs.mkdir()
    selected = ["neutron_w186", "neutron_ag107", "neutron_fr226", "neutron_rb94"]
    for name in selected:
        shutil.copy2(INPUTS[name][0], inputs / INPUTS[name][0].name)
    cache_a, cache_b = work / "cache-a", work / "cache-b"

    one_fresh_out = work / "one-fresh.npz"
    one_fresh_run = run_limited(
        build_arguments(
            inputs,
            one_fresh_out,
            format_name="tendl",
            projectile="neutron",
            groups="fispact-709",
            temperature=293.6,
            workers=1,
            cache=cache_a,
        ),
        address_space_bytes=BUILD_ADDRESS_SPACE_BYTES,
    )
    one_fresh = artifact_summary(one_fresh_out, one_fresh_run)
    cache_a_initial = cache_files(cache_a)

    four_cached_out = work / "four-cached.npz"
    four_cached_run = run_limited(
        build_arguments(
            inputs,
            four_cached_out,
            format_name="tendl",
            projectile="neutron",
            groups="fispact-709",
            temperature=293.6,
            workers=4,
            cache=cache_a,
        ),
        address_space_bytes=BUILD_ADDRESS_SPACE_BYTES,
    )
    four_cached = artifact_summary(four_cached_out, four_cached_run)

    four_fresh_out = work / "four-fresh.npz"
    four_fresh_run = run_limited(
        build_arguments(
            inputs,
            four_fresh_out,
            format_name="tendl",
            projectile="neutron",
            groups="fispact-709",
            temperature=293.6,
            workers=4,
            cache=cache_b,
        ),
        timed=True,
        address_space_bytes=BUILD_ADDRESS_SPACE_BYTES,
    )
    four_fresh = artifact_summary(four_fresh_out, four_fresh_run)
    maximum_rss_kib = parse_maximum_rss(four_fresh_run.stderr)

    one_cached_out = work / "one-cached.npz"
    one_cached_run = run_limited(
        build_arguments(
            inputs,
            one_cached_out,
            format_name="tendl",
            projectile="neutron",
            groups="fispact-709",
            temperature=293.6,
            workers=1,
            cache=cache_b,
        ),
        address_space_bytes=BUILD_ADDRESS_SPACE_BYTES,
    )
    one_cached = artifact_summary(one_cached_out, one_cached_run)

    baseline_index = json.loads(index_path(one_fresh_out).read_text())
    rb_copy = inputs / INPUTS["neutron_rb94"][0].name
    original_rb_hash = sha256(rb_copy)
    mutate_ignored_mf1_text(rb_copy)
    mutated_rb_hash = sha256(rb_copy)
    mutated_out = work / "source-mutated.npz"
    mutated_run = run_limited(
        build_arguments(
            inputs,
            mutated_out,
            format_name="tendl",
            projectile="neutron",
            groups="fispact-709",
            temperature=293.6,
            workers=4,
            cache=cache_a,
        ),
        address_space_bytes=BUILD_ADDRESS_SPACE_BYTES,
    )
    mutated = artifact_summary(mutated_out, mutated_run)
    mutated_index = json.loads(index_path(mutated_out).read_text())
    baseline_targets = {
        (target["za"], target["liso"]): target for target in baseline_index["targets"]
    }
    mutated_targets = {
        (target["za"], target["liso"]): target for target in mutated_index["targets"]
    }
    source_hash_changes = [
        [*identity, baseline_targets[identity]["source_sha256"], target["source_sha256"]]
        for identity, target in mutated_targets.items()
        if target["source_sha256"] != baseline_targets[identity]["source_sha256"]
    ]
    scrubbed_mutated = json.loads(json.dumps(mutated_index))
    for target in scrubbed_mutated["targets"]:
        identity = (target["za"], target["liso"])
        target["source_sha256"] = baseline_targets[identity]["source_sha256"]
    cache_a_after_mutation = cache_files(cache_a)

    all_runs = [one_fresh, four_cached, four_fresh, one_cached]
    return {
        "inputs": {name: INPUTS[name][1] for name in selected},
        "runs": {
            "one_worker_fresh": one_fresh,
            "four_worker_cached": four_cached,
            "four_worker_fresh": four_fresh,
            "one_worker_cached": one_cached,
        },
        "byte_identical": same_artifacts(*all_runs),
        "cache_pairs_after_first_fresh": len(cache_a_initial) == 2 * len(selected),
        "fresh_cache_hits": [one_fresh["cache_hits"], four_fresh["cache_hits"]],
        "cached_cache_hits": [four_cached["cache_hits"], one_cached["cache_hits"]],
        "profile": {
            "workers": 4,
            "maximum_rss_kib": maximum_rss_kib,
            "maximum_rss_bytes": maximum_rss_kib * 1024,
            "address_space_limit_bytes": BUILD_ADDRESS_SPACE_BYTES,
            "below_two_gib_per_process": maximum_rss_kib * 1024 < ADDRESS_SPACE_BYTES,
            "single_allocation_below_one_gib_proven_by_total_address_space_cap": True,
        },
        "source_invalidation": {
            "original_sha256": original_rb_hash,
            "mutated_sha256": mutated_rb_hash,
            "cache_hits": mutated["cache_hits"],
            "cache_files_before": len(cache_a_initial),
            "cache_files_after": len(cache_a_after_mutation),
            "source_hash_changes": source_hash_changes,
            "npz_unchanged": mutated["npz_sha256"] == one_fresh["npz_sha256"],
            "index_differs_only_in_source_hash": scrubbed_mutated == baseline_index,
            "pass": (
                original_rb_hash != mutated_rb_hash
                and mutated["cache_hits"] == len(selected) - 1
                and len(cache_a_after_mutation) == len(cache_a_initial) + 2
                and len(source_hash_changes) == 1
                and mutated["npz_sha256"] == one_fresh["npz_sha256"]
                and scrubbed_mutated == baseline_index
            ),
        },
        "pass": (
            same_artifacts(*all_runs)
            and one_fresh["cache_hits"] == 0
            and four_fresh["cache_hits"] == 0
            and four_cached["cache_hits"] == len(selected)
            and one_cached["cache_hits"] == len(selected)
            and len(cache_a_initial) == 2 * len(selected)
            and maximum_rss_kib * 1024 < ADDRESS_SPACE_BYTES
            and original_rb_hash != mutated_rb_hash
            and mutated["cache_hits"] == len(selected) - 1
            and len(cache_a_after_mutation) == len(cache_a_initial) + 2
            and len(source_hash_changes) == 1
            and mutated["npz_sha256"] == one_fresh["npz_sha256"]
            and scrubbed_mutated == baseline_index
        ),
    }


def run_option_invalidation(work: Path) -> dict:
    work.mkdir(parents=True)
    source = work / INPUTS["eaf_fe56"][0].name
    shutil.copy2(INPUTS["eaf_fe56"][0], source)
    cache = work / "option-cache"
    summaries = []
    for label, density in (("density_1", 1.0), ("density_2_fresh", 2.0), ("density_2_cached", 2.0)):
        output = work / f"{label}.npz"
        completed = run_limited(
            build_arguments(
                source,
                output,
                format_name="eaf",
                projectile="neutron",
                groups="fispact-709",
                temperature=293.6,
                workers=1,
                cache=cache,
                grid_density=density,
            ),
            address_space_bytes=BUILD_ADDRESS_SPACE_BYTES,
        )
        summaries.append((label, density, artifact_summary(output, completed)))
    first, second, third = (value[2] for value in summaries)
    files = cache_files(cache)
    return {
        "runs": {
            label: {"grid_density": density, **summary}
            for label, density, summary in summaries
        },
        "cache_files": len(files),
        "different_option_invalidated": first["cache_hits"] == 0
        and second["cache_hits"] == 0,
        "same_option_reused": third["cache_hits"] == 1,
        "density_2_repeat_byte_identical": same_artifacts(second, third),
        "physics_unchanged_for_processed_eaf": first["npz_sha256"]
        == second["npz_sha256"],
        "pass": (
            len(files) == 4
            and first["cache_hits"] == 0
            and second["cache_hits"] == 0
            and third["cache_hits"] == 1
            and same_artifacts(second, third)
            and first["npz_sha256"] == second["npz_sha256"]
        ),
    }


def record_tail(line: str) -> tuple[int, int, int] | None:
    if len(line) < 75:
        return None
    try:
        return int(line[66:70]), int(line[70:72]), int(line[72:75])
    except ValueError:
        return None


def section_line_numbers(lines: list[str], mf: int, mt: int) -> list[int]:
    return [
        number
        for number, line in enumerate(lines)
        if record_tail(line) is not None and record_tail(line)[1:] == (mf, mt)
    ]


def replace_field(line: str, field: int, value: str) -> str:
    if not 0 <= field < 6 or len(value) > 11:
        raise ValueError("invalid ENDF field replacement")
    start = 11 * field
    return f"{line[:start]}{value:>11}{line[start + 11:]}"


def replace_tail_mt(line: str, mt: int) -> str:
    if len(line) < 75:
        raise ValueError("cannot replace the tail of a truncated record")
    return f"{line[:72]}{mt:>3}{line[75:]}"


def mutated_file(source: Path, destination: Path, mutator) -> None:
    lines = source.read_text().splitlines()
    mutator(lines)
    destination.write_text("\n".join(lines) + "\n")


def plant_result(
    name: str,
    source: Path,
    work: Path,
    expected: list[str],
    *,
    format_name: str = "eaf",
) -> dict:
    output = work / f"{name}.npz"
    completed = run_limited(
        build_arguments(
            source,
            output,
            format_name=format_name,
            projectile="neutron",
            groups="fispact-709",
            temperature=293.6,
            workers=1,
        ),
        check=False,
        address_space_bytes=BUILD_ADDRESS_SPACE_BYTES,
    )
    message = f"{completed.stdout}\n{completed.stderr}".replace(str(work), "<WORK>")
    published = output.exists() or index_path(output).exists()
    checks = {token: token in message for token in expected}
    return {
        "returncode": completed.returncode,
        "expected_context": checks,
        "published_final_pair": published,
        "message_tail": message[-1200:],
        "pass": completed.returncode != 0 and all(checks.values()) and not published,
    }


def run_rejection_plants(work: Path) -> dict:
    work.mkdir(parents=True)
    source = INPUTS["eaf_fe56"][0]
    plants = {}

    invalid_numeric = work / "invalid_numeric.dat"
    mutated_file(
        source,
        invalid_numeric,
        lambda lines: lines.__setitem__(
            section_line_numbers(lines, 3, 102)[1],
            replace_field(lines[section_line_numbers(lines, 3, 102)[1]], 0, "NOTNUMBER"),
        ),
    )
    plants["invalid_numeric_field"] = plant_result(
        "invalid_numeric_field",
        invalid_numeric,
        work,
        [invalid_numeric.name, "MF=3/MT=102", "invalid ENDF number"],
    )

    invalid_count = work / "invalid_count.dat"
    mutated_file(
        source,
        invalid_count,
        lambda lines: lines.__setitem__(
            section_line_numbers(lines, 3, 102)[1],
            replace_field(lines[section_line_numbers(lines, 3, 102)[1]], 5, "99999"),
        ),
    )
    plants["invalid_count"] = plant_result(
        "invalid_count",
        invalid_count,
        work,
        [invalid_count.name, "MF=3/MT=102", "expected NP=99999"],
    )

    invalid_law = work / "invalid_law.dat"
    mutated_file(
        source,
        invalid_law,
        lambda lines: lines.__setitem__(
            section_line_numbers(lines, 3, 102)[2],
            replace_field(lines[section_line_numbers(lines, 3, 102)[2]], 1, "6"),
        ),
    )
    plants["invalid_interpolation_law"] = plant_result(
        "invalid_interpolation_law",
        invalid_law,
        work,
        [invalid_law.name, "MF=3/MT=102", "unsupported TAB1 interpolation INT=6"],
    )

    invalid_tail = work / "invalid_tail.dat"
    mutated_file(
        source,
        invalid_tail,
        lambda lines: lines.__setitem__(
            section_line_numbers(lines, 3, 102)[2],
            replace_tail_mt(lines[section_line_numbers(lines, 3, 102)[2]], 103),
        ),
    )
    plants["invalid_tail"] = plant_result(
        "invalid_tail",
        invalid_tail,
        work,
        [invalid_tail.name, "MF=3/MT=102", "without SEND"],
    )

    truncated = work / "truncated.dat"
    mutated_file(
        source,
        truncated,
        lambda lines: lines.__setitem__(
            section_line_numbers(lines, 3, 102)[1],
            lines[section_line_numbers(lines, 3, 102)[1]][:60],
        ),
    )
    plants["truncated_record"] = plant_result(
        "truncated_record",
        truncated,
        work,
        [truncated.name, "truncated ENDF record"],
    )

    duplicate_section = work / "duplicate_section.dat"

    def duplicate_mf3(lines: list[str]) -> None:
        section = section_line_numbers(lines, 3, 102)
        start, last = section[0], section[-1]
        send = next(
            number
            for number in range(last + 1, len(lines))
            if record_tail(lines[number]) is not None
            and record_tail(lines[number])[1:] == (3, 0)
        )
        lines[send + 1 : send + 1] = lines[start : send + 1]

    mutated_file(source, duplicate_section, duplicate_mf3)
    plants["duplicate_section"] = plant_result(
        "duplicate_section",
        duplicate_section,
        work,
        [duplicate_section.name, "duplicate MAT=", "MF=3/MT=102"],
    )

    invalid_nsub = work / "invalid_nsub.dat"
    mutated_file(
        source,
        invalid_nsub,
        lambda lines: lines.__setitem__(
            section_line_numbers(lines, 1, 451)[2],
            replace_field(lines[section_line_numbers(lines, 1, 451)[2]], 4, "42"),
        ),
    )
    plants["invalid_nsub"] = plant_result(
        "invalid_nsub",
        invalid_nsub,
        work,
        [invalid_nsub.name, "MF=1/MT=451", "unsupported incident-particle NSUB=42"],
    )

    unsupported_section = work / "unsupported_mf2.dat"

    def change_mf2_mt(lines: list[str]) -> None:
        for number in section_line_numbers(lines, 2, 151):
            lines[number] = replace_tail_mt(lines[number], 152)

    mutated_file(INPUTS["neutron_fe56"][0], unsupported_section, change_mf2_mt)
    plants["unsupported_section"] = plant_result(
        "unsupported_section",
        unsupported_section,
        work,
        [unsupported_section.name, "unsupported MF=2 sections {152}"],
        format_name="tendl",
    )

    duplicate_directory = work / "duplicate-target-input"
    duplicate_directory.mkdir()
    shutil.copy2(source, duplicate_directory / "first.dat")
    shutil.copy2(source, duplicate_directory / "second.dat")
    plants["duplicate_target"] = plant_result(
        "duplicate_target",
        duplicate_directory,
        work,
        ["duplicate target ZA=26056/LISO=0", "first.dat", "second.dat"],
    )

    race_source = work / "source_mutation.endf"
    shutil.copy2(INPUTS["neutron_w186"][0], race_source)
    race_output = work / "source_mutation.npz"
    race_command = [
        str(value)
        for value in build_arguments(
            race_source,
            race_output,
            format_name="tendl",
            projectile="neutron",
            groups="fispact-709",
            temperature=293.6,
            workers=1,
        )
    ]
    process = subprocess.Popen(
        race_command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=lambda: resource.setrlimit(
            resource.RLIMIT_AS, (BUILD_ADDRESS_SPACE_BYTES, BUILD_ADDRESS_SPACE_BYTES)
        ),
    )
    time.sleep(0.25)
    mutate_ignored_mf1_text(race_source)
    stdout, stderr = process.communicate(timeout=300)
    race_message = f"{stdout}\n{stderr}".replace(str(work), "<WORK>")
    race_checks = {
        race_source.name: race_source.name in race_message,
        "changed during the library build": "changed during the library build"
        in race_message,
    }
    race_published = race_output.exists() or index_path(race_output).exists()
    plants["source_mutation"] = {
        "returncode": process.returncode,
        "expected_context": race_checks,
        "published_final_pair": race_published,
        "message_tail": race_message[-1200:],
        "pass": process.returncode != 0
        and all(race_checks.values())
        and not race_published,
    }

    return {
        "plants": plants,
        "count": len(plants),
        "passed": sum(value["pass"] for value in plants.values()),
        "pass": all(value["pass"] for value in plants.values()),
    }


def run_parser_parity() -> dict:
    comparisons = {}
    totals = {
        "evaluations": 0,
        "fields_compared": 0,
        "float_fields": 0,
        "binary_representation_differences": 0,
        "maximum_ulp_distance": 0.0,
        "source_decimal_mismatches": 0,
    }
    for name, (path, expected_hash) in INPUTS.items():
        print(f"parser parity: {name}", flush=True)
        actual_hash = sha256(path)
        independent = parse_evaluations(path)
        rust = json.loads(run_limited([DUMP, "activation-json", path]).stdout)
        comparison = compare_structures(independent, rust)
        comparisons[name] = {
            "source_file": path.name,
            "source_sha256": actual_hash,
            "expected_source_sha256": expected_hash,
            "evaluations": len(independent),
            "independent_structure_sha256": structure_hash(independent),
            "rust_structure_sha256": structure_hash(rust),
            **comparison,
            "pass": actual_hash == expected_hash
            and structure_hash(independent) == structure_hash(rust)
            and comparison["exact_binary64"]
            and comparison["exact_at_endf_source_precision"],
        }
        totals["evaluations"] += len(independent)
        for key in (
            "fields_compared",
            "float_fields",
            "binary_representation_differences",
            "source_decimal_mismatches",
        ):
            totals[key] += comparison[key]
        totals["maximum_ulp_distance"] = max(
            totals["maximum_ulp_distance"], comparison["maximum_ulp_distance"]
        )
        del independent, rust
        gc.collect()
    totals["pass"] = all(value["pass"] for value in comparisons.values())
    return {"inputs": comparisons, "totals": totals, "pass": totals["pass"]}


def outside_support_check(
    identities: list[tuple[int, int, int, int]],
    sigma: np.ndarray,
    bounds: np.ndarray,
    sources: dict[tuple[int, int, int, int], dict | None],
) -> dict:
    checked = 0
    nonzero = []
    for row, identity in enumerate(identities):
        table = sources.get(identity)
        if table is None:
            continue
        for group, value in enumerate(sigma[row]):
            if bounds[group + 1] <= table["x"][0] or bounds[group] >= table["x"][-1]:
                checked += 1
                if value != 0.0 and len(nonzero) < 10:
                    nonzero.append(
                        {
                            "identity": list(identity),
                            "group": group,
                            "value_b": float(value),
                        }
                    )
    return {
        "groups_checked": checked,
        "nonzero_examples": nonzero,
        "pass": not nonzero,
    }


def run_eaf_numerical(work: Path) -> dict:
    work.mkdir(parents=True)
    output = work / "eaf-fe56.npz"
    completed = run_limited(
        build_arguments(
            INPUTS["eaf_fe56"][0],
            output,
            format_name="eaf",
            projectile="neutron",
            groups="fispact-709",
            temperature=293.6,
            workers=1,
            cache=work / "eaf-cache",
        ),
        address_space_bytes=BUILD_ADDRESS_SPACE_BYTES,
    )
    build = artifact_summary(output, completed)
    old_target = target_number(LEGACY_EAF_INDEX, 26056, 0)
    new_target = target_number(index_path(output), 26056, 0)
    old_prefix, new_prefix = work / "legacy-fe56", work / "rust-fe56"
    extract_target(LEGACY_EAF, old_target, old_prefix)
    extract_target(output, new_target, new_prefix)
    old_rows, old_sigma, old_bounds = read_target(old_prefix)
    new_rows, new_sigma, new_bounds = read_target(new_prefix)
    bounds_identical = np.array_equal(old_bounds, new_bounds)
    if not bounds_identical:
        raise ValueError("legacy and Rust EAF boundaries differ")

    evaluation = parse_evaluations(INPUTS["eaf_fe56"][0])[0]
    independent, sources = independent_eaf_rows(evaluation, new_bounds)
    identities = [tuple(value) for value in new_rows.tolist()]
    expected_only = sorted(set(independent) - set(identities))
    rust_only = sorted(set(identities) - set(independent))
    independent_sigma = np.stack([independent[identity] for identity in identities])
    exact = score_arrays(independent_sigma, new_sigma, identities)
    exact.update(
        {
            "independent_rows": len(independent),
            "rust_rows": len(identities),
            "independent_only": [list(value) for value in expected_only],
            "rust_only": [list(value) for value in rust_only],
            "pass": not expected_only and not rust_only and exact["pass"],
        }
    )
    finite_support = outside_support_check(
        identities, new_sigma, new_bounds, sources
    )
    structural, unchanged = compare_legacy_unchanged_domain(
        old_rows, old_sigma, new_rows, new_sigma, new_bounds, sources
    )

    mt32 = (32, -1, -1, 0)
    terminal_group = next(
        group
        for group, (low, high) in enumerate(zip(new_bounds[:-1], new_bounds[1:]))
        if low == 60_000_000.0 and high == 65_000_000.0
    )
    old_map = {tuple(row): number for number, row in enumerate(old_rows.tolist())}
    new_map = {tuple(row): number for number, row in enumerate(new_rows.tolist())}
    terminal_proof = {
        "identity": list(mt32),
        "source_support_max_eV": evaluation["mf3"]["32"]["x"][-1],
        "source_endpoint_b": evaluation["mf3"]["32"]["y"][-1],
        "group": terminal_group,
        "bounds_eV": new_bounds[terminal_group : terminal_group + 2].tolist(),
        "legacy_b": float(old_sigma[old_map[mt32], terminal_group]),
        "independent_exact_b": float(independent[mt32][terminal_group]),
        "rust_b": float(new_sigma[new_map[mt32], terminal_group]),
    }
    terminal_proof["pass"] = (
        terminal_proof["legacy_b"] > 0.0
        and terminal_proof["independent_exact_b"] == 0.0
        and terminal_proof["rust_b"] == 0.0
    )
    bounded_bytes = sum(
        Path(f"{prefix}.{suffix}").stat().st_size
        for prefix in (old_prefix, new_prefix)
        for suffix in ("rows", "sig", "bounds")
    )
    g4 = json.loads((ROOT / "results" / "g4_p10_temperature_narrow.json").read_text())
    reused = g4["seeded_p4_regression"]
    reused_summary = {
        "result_sha256": sha256(ROOT / "results" / "g4_p10_temperature_narrow.json"),
        "row_count": reused["row_count"],
        "structural": reused["structural"],
        "unchanged_domain": reused["unchanged_domain"],
        "pass": reused["pass"]
        and reused["structural"]["pass"]
        and reused["unchanged_domain"]["pass"],
    }
    return {
        "build": build,
        "legacy_library_sha256": sha256(LEGACY_EAF),
        "legacy_index_sha256": sha256(LEGACY_EAF_INDEX),
        "legacy_target": old_target,
        "rust_target": new_target,
        "bounded_materialized_bytes": bounded_bytes,
        "full_legacy_npz_loaded_in_python": False,
        "bounds_identical": bounds_identical,
        "structural": structural,
        "unchanged_domain": unchanged,
        "independent_exact": exact,
        "finite_support": finite_support,
        "terminal_support_proof": terminal_proof,
        "reused_seeded_neutron_control": reused_summary,
        "pass": bounds_identical
        and structural["pass"]
        and unchanged["pass"]
        and exact["pass"]
        and finite_support["pass"]
        and terminal_proof["pass"]
        and reused_summary["pass"],
    }


def prerequisite_results() -> dict:
    reports = {}
    for path, expected in PINNED.items():
        actual = sha256(path)
        entry = {"sha256": actual, "expected_sha256": expected, "hash_matches": actual == expected}
        if path.parent.name == "results":
            document = json.loads(path.read_text())
            entry["reported_pass"] = document.get("pass") is True
            entry["pass"] = entry["hash_matches"] and entry["reported_pass"]
        else:
            entry["pass"] = entry["hash_matches"]
        reports[path.name] = entry
    return {"files": reports, "pass": all(value["pass"] for value in reports.values())}


def verify_inputs_and_tools() -> dict:
    files = {}
    for name, (path, expected) in INPUTS.items():
        actual = sha256(path) if path.is_file() else None
        files[name] = {
            "file": path.name,
            "size_bytes": path.stat().st_size if path.is_file() else None,
            "sha256": actual,
            "expected_sha256": expected,
            "pass": actual == expected,
        }
    tools = {}
    for name, path in (("actinv", ACTINV), ("dump", DUMP)):
        tools[name] = {
            "file": path.name,
            "sha256": sha256(path) if path.is_file() else None,
            "pass": path.is_file(),
        }
    legacy = {
        "library": {
            "file": LEGACY_EAF.name,
            "size_bytes": LEGACY_EAF.stat().st_size if LEGACY_EAF.is_file() else None,
            "pass": LEGACY_EAF.is_file(),
        },
        "index": {
            "file": LEGACY_EAF_INDEX.name,
            "size_bytes": LEGACY_EAF_INDEX.stat().st_size
            if LEGACY_EAF_INDEX.is_file()
            else None,
            "pass": LEGACY_EAF_INDEX.is_file(),
        },
    }
    return {
        "files": files,
        "tools": tools,
        "legacy_eaf": legacy,
        "pass": all(value["pass"] for value in files.values())
        and all(value["pass"] for value in tools.values())
        and all(value["pass"] for value in legacy.values()),
    }


def main() -> None:
    prerequisites = prerequisite_results()
    inputs = verify_inputs_and_tools()
    if not prerequisites["pass"] or not inputs["pass"]:
        raise SystemExit(
            f"P10-G1 prerequisites failed: protocols/results={prerequisites['pass']}, inputs={inputs['pass']}"
        )
    parser = run_parser_parity()
    with tempfile.TemporaryDirectory(prefix="actinv-p10-g1-") as directory:
        work = Path(directory)
        print("exact and legacy EAF collapse", flush=True)
        numerical = run_eaf_numerical(work / "numerical")
        print("fresh/cached one/four-worker determinism", flush=True)
        determinism = run_determinism_and_cache(work / "determinism")
        print("option checkpoint invalidation", flush=True)
        option_invalidation = run_option_invalidation(work / "options")
        print("fail-closed rejection plants", flush=True)
        plants = run_rejection_plants(work / "plants")
    result = {
        "schema": "actinv-p10-g1-builder-1",
        "gate": "P10-G1",
        "generated": "2026-08-27",
        "address_space_limit_bytes": ADDRESS_SPACE_BYTES,
        "build_address_space_limit_bytes": BUILD_ADDRESS_SPACE_BYTES,
        "control_sha256": sha256(Path(__file__)),
        "inputs": inputs,
        "prerequisites": prerequisites,
        "parser_parity": parser,
        "numerical_parity": numerical,
        "determinism_and_cache": determinism,
        "option_invalidation": option_invalidation,
        "rejection_plants": plants,
    }
    result["pass"] = all(
        value["pass"]
        for value in (
            inputs,
            prerequisites,
            parser,
            numerical,
            determinism,
            option_invalidation,
            plants,
        )
    )
    RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
