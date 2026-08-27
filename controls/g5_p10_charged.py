#!/usr/bin/env python3
"""P10-G5: charged residual production vs TENDL-2025 and FISPACT TENDL-2017 references."""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tempfile

import numpy as np
from scipy.integrate import quad

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from endf_common import endf_float, fields, read_list, read_tab1, sections  # noqa: E402


RESULT = ROOT / "results" / "g5_p10_charged.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target" / "release" / "dump"))
WORK = Path(os.environ.get("ACTINV_P10_WORK", "/tmp/actinv-p10")) / "g5"
ARCHIVE = Path(
    os.environ.get(
        "ACTINV_P10_TENDL2017_ARCHIVE",
        "/tmp/actinv-p10-TENDL2017data.tar.bz2",
    )
)
EBINS_ARCHIVE = Path(
    os.environ.get("ACTINV_P10_EBINS_ARCHIVE", "/tmp/actinv-p10-ebins.tar.bz2")
)
EBINS_162 = Path(
    os.environ.get("ACTINV_P10_EBINS_162", "/tmp/actinv-p10-ebins/ebins_162")
)
EXTRACTED = Path(
    os.environ.get("ACTINV_P10_FISPACT_EXTRACT", "/tmp/actinv-p10-fispact")
) / "TENDL2017data"

POINT_ENERGIES_MEV = (35.0, 50.0, 100.0, 200.0)
POINT_TOLERANCE = 2.0e-6
PROCESSED_ROW_TOLERANCE = 2.5e-3  # P10 Amendment B
PROCESSED_SPECTRUM_TOLERANCE = 2.0e-3
INDEPENDENT_RATE_TOLERANCE = 1.0e-12
ADDRESS_SPACE_BYTES = 2 * 1024**3

CASES = (
    {
        "tag": "p",
        "projectile": "proton",
        "projectile_za": (1, 1),
        "zap": 27055,
        "lfs": 0,
        "raw_2017": Path(os.environ.get("ACTINV_P10_P2017", "/tmp/p-Fe056.tendl2017")),
        "raw_2025": Path(os.environ.get("ACTINV_P10_P2025", "/tmp/p-Fe056.tendl2025")),
        "processed": EXTRACTED / "tal2017-p" / "gxs-162" / "Fe056g.asc",
        "residual": Path(
            os.environ.get("ACTINV_P10_P_RESIDUAL", "/tmp/p-Fe056-Co055.tot2025")
        ),
        "raw_2017_sha256": "a817e16d7e5b2bbcc0a8fa4091c9505e4c0364326f26e1727ac72d2c229b6d3a",
        "raw_2025_sha256": "7a505214adb273a2e71fba7ced0ea792dae853875127111af770ee658e01740b",
        "processed_sha256": "d21f41bb5d6f6667145511ae89affdf62ad1cc4cc6c2d054c7b74aafa2b66604",
        "residual_sha256": "7dd9940588e92d80e0120a2fb846010667a5284c1c5feb612bd33b4f9b6e2065",
    },
    {
        "tag": "d",
        "projectile": "deuteron",
        "projectile_za": (1, 2),
        "zap": 27057,
        "lfs": 0,
        "raw_2017": Path(os.environ.get("ACTINV_P10_D2017", "/tmp/d-Fe056.tendl2017")),
        "raw_2025": Path(os.environ.get("ACTINV_P10_D2025", "/tmp/d-Fe056.tendl2025")),
        "processed": EXTRACTED / "tal2017-d" / "gxs-162" / "Fe056g.asc",
        "residual": Path(
            os.environ.get("ACTINV_P10_D_RESIDUAL", "/tmp/d-Fe056-Co057.tot2025")
        ),
        "raw_2017_sha256": "ebb4e2af6ceed337b7355233ddbd29912adb223519fc966b4ea01173acedab9a",
        "raw_2025_sha256": "cd036d5529c71998d20aa10ae7e8b1d9ae1d7045200b29f46163e5b2faf6ab95",
        "processed_sha256": "9d136c2caa3f6ae8479321234bb05aaae9a34efefdebe440ab653ababf2df2b0",
        "residual_sha256": "40f2e3118fded4bba9036911b3b159cdced4341f9b76d5d8619a9b81b5ce3365",
    },
    {
        "tag": "a",
        "projectile": "alpha",
        "projectile_za": (2, 4),
        "zap": 28059,
        "lfs": 0,
        "raw_2017": Path(os.environ.get("ACTINV_P10_A2017", "/tmp/a-Fe056.tendl2017")),
        "raw_2025": Path(os.environ.get("ACTINV_P10_A2025", "/tmp/a-Fe056.tendl2025")),
        "processed": EXTRACTED / "tal2017-a" / "gxs-162" / "Fe056g.asc",
        "residual": Path(
            os.environ.get("ACTINV_P10_A_RESIDUAL", "/tmp/a-Fe056-Ni059.tot2025")
        ),
        "raw_2017_sha256": "e6a2a93837a279eac7000a97bd4168f9efe91349097c0479f3645e7e8b7bac07",
        "raw_2025_sha256": "ff185f3fdf69b6a64a3e9e9eb3964bd8856262b635d674537076c329069a656e",
        "processed_sha256": "f8c60a8983dc568c2e1f356318985c8cad0d1395fb8f80683a0ee064e73c8c30",
        "residual_sha256": "12a4344a8c6b11c3d754a5962ede1d6ce3cebfd1b0556bf0d0ab541224f66709",
    },
)

EXPECTED_COMMON = {
    ARCHIVE: "7f305df2277f71a7d7d6d1e1ebfec8dea9415d813e283990c1fb65804b05bec8",
    EBINS_ARCHIVE: "fb612c2df07269389b44e15dc101166e675d53269f4078174999650a68e1b63a",
    EBINS_162: "4b1ba7ec855aa305b3312cb57d75cbcd6be41b4e67e93070df104bd62b500b0e",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_B.md": "36fe887080b03af2851c00a92ebcd5fe93fa4f4bded69c37415ead2626f8cc23",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def limit_address_space() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))


def run_limited(arguments: list[object], *, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        [str(value) for value in arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=limit_address_space,
        check=False,
    )
    if check and completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(map(str, arguments))}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
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


def read_tab2(lines: list[str], index: int) -> tuple[dict, int]:
    c1, c2, l1, l2, nr, ne = cont(lines[index])
    index += 1
    interpolation = []
    while len(interpolation) < nr:
        value = fields(lines[index])
        index += 1
        for offset in range(0, 6, 2):
            if len(interpolation) < nr:
                interpolation.append((integer(value[offset]), integer(value[offset + 1])))
    return {
        "c1": c1,
        "c2": c2,
        "l1": l1,
        "l2": l2,
        "ne": ne,
        "interpolation": interpolation,
    }, index


def consume_mf6_law(lines: list[str], index: int, law: int) -> int:
    if law in (0, 3, 4):
        return index
    if law in (1, 2, 5):
        tab2, index = read_tab2(lines, index)
        for _ in range(tab2["ne"]):
            _, index = read_list(lines, index)
        return index
    if law == 6:
        cont(lines[index])
        return index + 1
    if law == 7:
        energies, index = read_tab2(lines, index)
        for _ in range(energies["ne"]):
            angles, index = read_tab2(lines, index)
            for _ in range(angles["ne"]):
                _, index = read_tab1(lines, index)
        return index
    raise ValueError(f"unsupported independent MF=6 LAW={law}")


def table(record: tuple) -> dict:
    return {
        "nbt": tuple((int(nbt), int(law)) for nbt, law in record[6]),
        "x": tuple(float(value) for value in record[7]),
        "y": tuple(float(value) for value in record[8]),
    }


def parse_independent(path: Path) -> dict:
    result = {
        "za": None,
        "awi": None,
        "nsub": None,
        "mf3": {},
        "mf6": {},
        "mf8": {},
        "mf9": {},
        "mf10": {},
        "mf6_laws": set(),
    }
    for (_, mf, mt), lines in sections(path):
        if mf == 1 and mt == 451:
            head = cont(lines[0])
            incident = cont(lines[2])
            result["za"] = int(round(head[0]))
            result["awi"] = incident[0]
            result["nsub"] = incident[4]
        elif mf == 3:
            record, next_index = read_tab1(lines, 1)
            if next_index != len(lines):
                raise ValueError(f"{path.name}: MF=3/MT={mt} was not consumed")
            result["mf3"][mt] = table(record)
        elif mf == 6:
            count = cont(lines[0])[4]
            index = 1
            products = []
            for _ in range(count):
                record, after_yield = read_tab1(lines, index)
                law = int(record[3])
                products.append(
                    {
                        "zap": int(round(record[0])),
                        "awp": float(record[1]),
                        "law": law,
                        "table": table(record),
                    }
                )
                result["mf6_laws"].add(law)
                index = consume_mf6_law(lines, after_yield, law)
            if index != len(lines):
                raise ValueError(f"{path.name}: MF=6/MT={mt} was not consumed")
            result["mf6"][mt] = products
        elif mf == 8 and mt not in (454, 457, 459):
            _, _, _, _, count, no = cont(lines[0])
            index = 1
            products = []
            for _ in range(count):
                if no == 0:
                    record, index = read_list(lines, index)
                    zap, _, lmf, lfs = record[:4]
                else:
                    zap, _, lmf, lfs, _, _ = cont(lines[index])
                    index += 1
                products.append(
                    {"zap": int(round(zap)), "lfs": int(lfs), "lmf": int(lmf)}
                )
            if index != len(lines):
                raise ValueError(f"{path.name}: MF=8/MT={mt} was not consumed")
            result["mf8"][mt] = products
        elif mf in (9, 10):
            count = cont(lines[0])[4]
            index = 1
            products = []
            for _ in range(count):
                record, index = read_tab1(lines, index)
                products.append(
                    {
                        "zap": int(record[2]),
                        "lfs": int(record[3]),
                        "table": table(record),
                    }
                )
            if index != len(lines):
                raise ValueError(f"{path.name}: MF={mf}/MT={mt} was not consumed")
            result[f"mf{mf}"][mt] = products
    if result["za"] is None:
        raise ValueError(f"{path.name}: no MF=1/MT=451 metadata")
    result["mf6_laws"] = sorted(result["mf6_laws"])
    return result


def interpolation_law(tab: dict, segment: int) -> int:
    endpoint = segment + 2
    return next(law for nbt, law in tab["nbt"] if endpoint <= nbt)


def evaluate(tab: dict, energy: float) -> float:
    x = tab["x"]
    y = tab["y"]
    if energy < x[0] or energy > x[-1]:
        return 0.0
    if energy == x[-1]:
        return y[-1]
    segment = min(max(bisect.bisect_right(x, energy) - 1, 0), len(x) - 2)
    x1, x2 = x[segment], x[segment + 1]
    y1, y2 = y[segment], y[segment + 1]
    if x2 == x1:
        return y2
    linear = (energy - x1) / (x2 - x1)
    law = interpolation_law(tab, segment)
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
    raise ValueError(f"unsupported independent TAB1 INT={law}")


def production_terms(
    evaluation: dict,
    zap: int,
    lfs: int,
    projectile_za: tuple[int, int],
) -> dict[str, list[tuple[dict, ...]]]:
    terms: dict[str, list[tuple[dict, ...]]] = {
        "mf3": [],
        "mf9": [],
        "mf10": [],
        "mf6": [],
    }
    for mt, descriptors in evaluation["mf8"].items():
        for descriptor in descriptors:
            if (descriptor["zap"], descriptor["lfs"], descriptor["lmf"]) == (zap, lfs, 3):
                terms["mf3"].append((evaluation["mf3"][mt],))
    for mt, products in evaluation["mf9"].items():
        for product in products:
            if (product["zap"], product["lfs"]) == (zap, lfs):
                terms["mf9"].append((evaluation["mf3"][mt], product["table"]))
    for products in evaluation["mf10"].values():
        for product in products:
            if (product["zap"], product["lfs"]) == (zap, lfs):
                terms["mf10"].append((product["table"],))

    if 5 in evaluation["mf3"] and 5 in evaluation["mf6"] and 5 in evaluation["mf8"]:
        yields = evaluation["mf6"][5]
        used = [False] * len(yields)
        for descriptor in (value for value in evaluation["mf8"][5] if value["lmf"] == 6):
            match = next(
                (
                    index
                    for index, product in enumerate(yields)
                    if not used[index] and product["zap"] == descriptor["zap"]
                ),
                None,
            )
            if match is None:
                raise ValueError(
                    f"independent MF=8 product {descriptor['zap']}/{descriptor['lfs']} has no MF=6 yield"
                )
            used[match] = True
            if (descriptor["zap"], descriptor["lfs"]) == (zap, lfs):
                terms["mf6"].append((evaluation["mf3"][5], yields[match]["table"]))
        if any(not flag and product["zap"] != 0 for flag, product in zip(used, yields)):
            raise ValueError("independent MF=6 yield has no MF=8 declaration")

    mt_products = {
        int(mt): tuple(delta)
        for mt, delta in json.loads(
            (ROOT / "crates/actinv-data/data/mt_products.json").read_text()
        )["table"].items()
    }
    target_z, target_a = divmod(evaluation["za"], 1000)
    projectile_z, projectile_a = projectile_za
    skipped = {1, 2, 3, 5, 19, 20, 21, 27, 38, 101, 444}
    for mt, reaction in evaluation["mf3"].items():
        if mt in skipped or 201 <= mt <= 207 or 600 <= mt <= 849 or mt >= 1000:
            continue
        declared = evaluation["mf8"].get(mt, [])
        has_explicit_product = (
            bool(evaluation["mf9"].get(mt))
            or bool(evaluation["mf10"].get(mt))
            or any(product["lmf"] in (3, 6, 9, 10) for product in declared)
        )
        if has_explicit_product or mt not in mt_products or lfs != 0:
            continue
        delta_z, delta_a = mt_products[mt]
        residual = (target_z + delta_z + projectile_z) * 1000 + (
            target_a + delta_a + projectile_a - 1
        )
        if residual == zap:
            terms["mf3"].append((reaction,))
    return terms


def point_components(
    evaluation: dict,
    zap: int,
    lfs: int,
    projectile_za: tuple[int, int],
    energy: float,
) -> dict[str, float]:
    terms = production_terms(evaluation, zap, lfs, projectile_za)
    values = {
        name: math.fsum(
            math.prod(evaluate(tab, energy) for tab in product) for product in products
        )
        for name, products in terms.items()
    }
    values["total"] = math.fsum(values.values())
    return values


def collapse_product(tables: tuple[dict, ...], low: float, high: float) -> float:
    breaks = {low, high}
    for tab in tables:
        breaks.update(value for value in tab["x"] if low < value < high)
    ordered = sorted(breaks)
    integral = 0.0
    for left, right in zip(ordered, ordered[1:]):
        if right <= left:
            continue
        value, _ = quad(
            lambda log_energy: math.prod(
                evaluate(tab, math.exp(log_energy)) for tab in tables
            ),
            math.log(left),
            math.log(right),
            epsabs=1.0e-16,
            epsrel=2.0e-13,
            limit=200,
        )
        integral += value
    return integral / math.log(high / low)


def independent_group_row(
    evaluation: dict,
    zap: int,
    lfs: int,
    projectile_za: tuple[int, int],
    bounds: np.ndarray,
) -> np.ndarray:
    terms = production_terms(evaluation, zap, lfs, projectile_za)
    products = [product for values in terms.values() for product in values]
    return np.array(
        [
            math.fsum(collapse_product(product, low, high) for product in products)
            for low, high in zip(bounds, bounds[1:])
        ],
        dtype=float,
    )


def relative(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0e-300)


def official_residual(path: Path) -> dict[float, float]:
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        energy_mev, millibarns = map(float, line.split()[:2])
        values[energy_mev] = millibarns * 1.0e-3
    return values


def official_bounds() -> np.ndarray:
    lines = EBINS_162.read_text().splitlines()
    if int(lines[1]) != 162:
        raise ValueError("official ebins_162 does not declare 162 groups")
    numbers = re.findall(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)[Ee][+-]?\d+", " ".join(lines[2:]))
    descending = np.array([float(value) for value in numbers], dtype=float)
    if len(descending) != 163 or np.any(np.diff(descending) >= 0.0):
        raise ValueError("official ebins_162 boundaries are incomplete or unordered")
    return descending[::-1].copy()


def rust_probe(case: dict, path: Path) -> dict[float, dict[str, float]]:
    arguments = [DUMP, "activation-product", path, case["zap"], case["lfs"]]
    arguments.extend(int(energy * 1.0e6) for energy in POINT_ENERGIES_MEV)
    lines = run_limited(arguments).stdout.splitlines()
    expected_header = f"E {case['projectile']} 26056 {case['zap']} {case['lfs']}"
    if not lines or lines[0] != expected_header:
        raise ValueError(f"unexpected Rust activation-product header: {lines[:1]}")
    values = {}
    for line in lines[1:]:
        record = line.split()
        if len(record) != 7 or record[0] != "P":
            raise ValueError(f"unexpected Rust activation-product row: {line}")
        energy = float(record[1])
        values[energy] = dict(zip(("mf3", "mf9", "mf10", "mf6", "total"), map(float, record[2:])))
    return values


def build_library(case: dict, year: int) -> dict:
    source = case[f"raw_{year}"]
    with tempfile.TemporaryDirectory(prefix=f"actinv-p10-g5-{year}-{case['tag']}-", dir=WORK) as directory:
        directory = Path(directory)
        output = directory / "fe56.npz"
        completed = run_limited(
            [
                ACTINV,
                "build-library",
                source,
                output,
                "--format",
                "tendl",
                "--projectile",
                case["projectile"],
                "--groups",
                "fispact-162",
                "--temperature-K",
                "0",
                "--workers",
                "1",
                "--cache",
                directory / "cache",
            ]
        )
        index_path = output.with_name("fe56_index.json")
        index = json.loads(index_path.read_text())
        with np.load(output, allow_pickle=False) as library:
            rows = library["rows"].copy()
            sig = library["sig"].copy()
            bounds = library["bounds"].copy()
        return {
            "rows_array": rows,
            "sig_array": sig,
            "bounds_array": bounds,
            "index": index,
            "rows": int(len(rows)),
            "npz_sha256": sha256(output),
            "index_sha256": sha256(index_path),
            "summary": completed.stdout.strip().splitlines()[-1],
        }


def product_row(build: dict, zap: int, lfs: int) -> np.ndarray:
    rows = build["rows_array"]
    selected = np.flatnonzero((rows[:, 2] == zap) & (rows[:, 3] == lfs))
    if not len(selected):
        raise ValueError(f"built library has no product {zap}/{lfs}")
    return np.sum(build["sig_array"][selected], axis=0)


def processed_row(evaluation: dict, case: dict, bounds: np.ndarray) -> np.ndarray:
    terms = production_terms(
        evaluation,
        case["zap"],
        case["lfs"],
        case["projectile_za"],
    )
    tables = [product for products in terms.values() for product in products]
    if not tables:
        raise ValueError(
            f"processed reference has no product {case['zap']}/{case['lfs']}"
        )
    return np.array(
        [
            math.fsum(
                math.prod(evaluate(tab, float(low)) for tab in product)
                for product in tables
            )
            for low in bounds[:-1]
        ],
        dtype=float,
    )


def spectra(bounds: np.ndarray) -> dict[str, np.ndarray]:
    centers = np.sqrt(bounds[:-1] * bounds[1:])
    support = (bounds[:-1] >= 30.0e6) & (bounds[1:] <= 200.0e6)
    values = {
        "flat_30_200_MeV": support.astype(float),
        "soft_40_MeV": support * np.exp(-0.5 * (np.log(centers / 40.0e6) / 0.32) ** 2),
        "hard_140_MeV": support * np.exp(-0.5 * (np.log(centers / 140.0e6) / 0.28) ** 2),
    }
    for name, spectrum in values.items():
        total = math.fsum(map(float, spectrum))
        if total <= 0.0:
            raise ValueError(f"fixed spectrum {name} has no support")
        values[name] = spectrum / total
    return values


def structure_checks(case: dict, evaluation: dict, build: dict) -> dict:
    rows = build["rows_array"]
    mf6_rows = rows[(rows[:, 1] == 5) & (rows[:, 4] == 6)]
    nonfree_descriptors = [
        product for product in evaluation["mf8"][5] if product["lmf"] == 6 and product["zap"] not in (0, 1)
    ]
    if len(mf6_rows) != len(nonfree_descriptors) or len(mf6_rows) < 2:
        raise ValueError("MF=6 multiple-product retention does not match independent descriptors")
    if not any(product["zap"] == 1 for product in evaluation["mf8"][5]):
        raise ValueError("real MT5 control has no emitted-neutron descriptor")
    if np.any((rows[:, 1] == 5) & (rows[:, 2] == 1)):
        raise ValueError("emitted free neutron entered the nuclide inventory")
    ledger = build["index"]["targets"][0]["ledger"]
    if not any("free-neutron MF=6 product omitted" in entry for entry in ledger):
        raise ValueError("emitted-neutron omission is absent from the target ledger")

    levels = {}
    for mt, products in evaluation["mf8"].items():
        for product in products:
            if product["zap"] > 1 and product["lfs"] > 0:
                levels.setdefault((mt, product["zap"]), set()).add(product["lfs"])
    remap = None
    for (mt, zap), raw_values in sorted(levels.items()):
        built_values = sorted(
            int(value)
            for value in set(rows[(rows[:, 1] == mt) & (rows[:, 2] == zap), 3])
            if int(value) != 0
        )
        raw_values = sorted(raw_values)
        canonical = list(range(1, len(raw_values) + 1))
        if built_values and raw_values != canonical and built_values == canonical:
            remap = {"mt": mt, "zap": zap, "raw_lfs": raw_values, "built_liso": built_values}
            break
    if remap is None:
        raise ValueError("no independently checked noncanonical level remap was found")

    mt_products = {
        int(mt): tuple(delta)
        for mt, delta in json.loads(
            (ROOT / "crates/actinv-data/data/mt_products.json").read_text()
        )["table"].items()
    }
    explicit = None
    target_z, target_a = divmod(evaluation["za"], 1000)
    projectile_z, projectile_a = case["projectile_za"]
    skipped = {1, 2, 3, 5, 19, 20, 21, 27, 38, 101, 444}
    for mt in sorted(evaluation["mf3"]):
        if mt in skipped or 201 <= mt <= 207 or 600 <= mt <= 849 or mt >= 1000:
            continue
        if evaluation["mf8"].get(mt) or evaluation["mf9"].get(mt) or evaluation["mf10"].get(mt):
            continue
        if mt not in mt_products:
            continue
        delta_z, delta_a = mt_products[mt]
        expected = (target_z + delta_z + projectile_z) * 1000 + (
            target_a + delta_a + projectile_a - 1
        )
        found = rows[(rows[:, 1] == mt) & (rows[:, 2] == expected) & (rows[:, 4] == -1)]
        if len(found) == 1:
            explicit = {"mt": mt, "expected_zap": expected, "rows": 1}
            break
    if explicit is None:
        raise ValueError("no explicit charged-channel arithmetic row matched independently")
    return {
        "mf6_descriptors_nonfree": len(nonfree_descriptors),
        "mf6_rows_nonfree": len(mf6_rows),
        "emitted_neutron_omitted_and_ledgered": True,
        "level_remap": remap,
        "explicit_channel": explicit,
    }


def tail(line: str) -> tuple[int, int, int] | None:
    if len(line) < 75:
        return None
    try:
        return int(line[66:70]), int(line[70:72]), int(line[72:75])
    except ValueError:
        return None


def section_indices(lines: list[str], mf: int, mt: int) -> list[int]:
    return [index for index, line in enumerate(lines) if (tail(line) or (0, 0, 0))[1:] == (mf, mt)]


def set_field(line: str, field_index: int, value: object) -> str:
    newline = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n").ljust(80)
    replacement = str(value)
    if len(replacement) > 11:
        raise ValueError(f"replacement ENDF field is too wide: {replacement}")
    start = 11 * field_index
    return body[:start] + replacement.rjust(11) + body[start + 11 :] + newline


def planted_file(source: Path, destination: Path, plant: str) -> None:
    lines = source.read_text().splitlines(keepends=True)
    if plant == "bad-nsub":
        indices = section_indices(lines, 1, 451)
        lines[indices[2]] = set_field(lines[indices[2]], 4, 10030)
    elif plant in ("missing-yield", "malformed-law"):
        indices = section_indices(lines, 6, 5)
        field_index, value = (0, 99999) if plant == "missing-yield" else (3, 99)
        lines[indices[1]] = set_field(lines[indices[1]], field_index, value)
    elif plant == "conflicting-product":
        indices = section_indices(lines, 10, 22)
        body = [lines[index].rstrip("\n") for index in indices]
        first, second_start = read_tab1(body, 1)
        lines[indices[second_start]] = set_field(lines[indices[second_start]], 2, first[2])
        lines[indices[second_start]] = set_field(lines[indices[second_start]], 3, first[3])
    else:
        raise ValueError(f"unknown plant {plant}")
    destination.write_text("".join(lines))


def rejection_controls(source: Path) -> dict:
    expected = {
        "wrong-projectile": "expected deuteron",
        "bad-nsub": "unsupported incident-particle NSUB=10030",
        "missing-yield": "no matching MF=6 yield",
        "conflicting-product": "duplicate product identities",
        "malformed-law": "unsupported MF=6 LAW=99",
    }
    results = {}
    with tempfile.TemporaryDirectory(prefix="actinv-p10-g5-plants-", dir=WORK) as directory:
        directory = Path(directory)
        for name, needle in expected.items():
            planted = source
            if name != "wrong-projectile":
                planted = directory / f"{name}.endf"
                planted_file(source, planted, name)
            output = directory / f"{name}.npz"
            projectile = "deuteron" if name == "wrong-projectile" else "proton"
            completed = run_limited(
                [
                    ACTINV,
                    "build-library",
                    planted,
                    output,
                    "--format",
                    "tendl",
                    "--projectile",
                    projectile,
                    "--groups",
                    "fispact-162",
                    "--temperature-K",
                    "0",
                    "--workers",
                    "1",
                ],
                check=False,
            )
            message = completed.stdout + completed.stderr
            index = output.with_name(f"{output.stem}_index.json")
            passed = completed.returncode != 0 and needle in message and not output.exists() and not index.exists()
            if not passed:
                raise ValueError(
                    f"plant {name} did not fail closed with {needle!r}: rc={completed.returncode}, message={message!r}"
                )
            results[name] = {
                "returncode_nonzero": True,
                "context": needle,
                "final_pair_absent": True,
            }
    return results


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    required = [ACTINV, DUMP, *EXPECTED_COMMON]
    for case in CASES:
        required.extend((case["raw_2017"], case["raw_2025"], case["processed"], case["residual"]))
    missing = [str(path) for path in required if not Path(path).is_file()]
    if missing:
        raise FileNotFoundError("missing P10-G5 input(s): " + ", ".join(missing))
    if ARCHIVE.stat().st_size != 2_595_437_294:
        raise ValueError("TENDL2017data archive size does not match the frozen protocol")

    hashes = {path.name: sha256(path) for path in EXPECTED_COMMON}
    for path, expected in EXPECTED_COMMON.items():
        if hashes[path.name] != expected:
            raise ValueError(f"hash mismatch for {path}")
    for case in CASES:
        for field in ("raw_2017", "raw_2025", "processed", "residual"):
            actual = sha256(case[field])
            if actual != case[f"{field}_sha256"]:
                raise ValueError(f"hash mismatch for {case[field]}")

    bounds = official_bounds()
    embedded = np.array(
        json.loads(
            (ROOT / "crates/actinv-data/data/fispact_162_groups.json").read_text()
        )["boundaries_eV"],
        dtype=float,
    )
    if embedded[0] > embedded[-1]:
        embedded = embedded[::-1].copy()
    if not np.array_equal(bounds, embedded):
        raise ValueError("vendored CCFE-162 boundaries differ from official ebins_162")

    parsed = {}
    builds = {}
    for case in CASES:
        tag = case["tag"]
        parsed[tag] = {
            2017: parse_independent(case["raw_2017"]),
            2025: parse_independent(case["raw_2025"]),
            "processed": parse_independent(case["processed"]),
        }
        builds[tag] = {2017: build_library(case, 2017), 2025: build_library(case, 2025)}
        for year in (2017, 2025):
            if not np.array_equal(builds[tag][year]["bounds_array"], bounds):
                raise ValueError(f"{tag}/{year}: Rust output boundaries differ from official CCFE-162")

    point_results = {}
    group_results = {}
    fixed_spectra = spectra(bounds)
    for case in CASES:
        tag = case["tag"]
        rust_points = rust_probe(case, case["raw_2025"])
        official_points = official_residual(case["residual"])
        rows = []
        point_reference_worst = 0.0
        point_independent_worst = 0.0
        for energy_mev in POINT_ENERGIES_MEV:
            energy_ev = energy_mev * 1.0e6
            rust = rust_points[energy_ev]
            independent = point_components(
                parsed[tag][2025],
                case["zap"],
                case["lfs"],
                case["projectile_za"],
                energy_ev,
            )
            reference = official_points[energy_mev]
            reference_deviation = relative(rust["total"], reference)
            independent_deviation = max(relative(rust[name], independent[name]) for name in rust)
            point_reference_worst = max(point_reference_worst, reference_deviation)
            point_independent_worst = max(point_independent_worst, independent_deviation)
            rows.append(
                {
                    "energy_MeV": energy_mev,
                    "rust_b": rust["total"],
                    "independent_b": independent["total"],
                    "official_b": reference,
                    "rust_components_b": {name: rust[name] for name in ("mf3", "mf9", "mf10", "mf6")},
                    "relative_to_official": reference_deviation,
                }
            )
        point_results[case["projectile"]] = {
            "rows": rows,
            "max_relative_to_official": point_reference_worst,
            "max_relative_to_independent": point_independent_worst,
            "pass": point_reference_worst <= POINT_TOLERANCE and point_independent_worst <= INDEPENDENT_RATE_TOLERANCE,
        }

        rust_row = product_row(builds[tag][2017], case["zap"], case["lfs"])
        independent_row = independent_group_row(
            parsed[tag][2017],
            case["zap"],
            case["lfs"],
            case["projectile_za"],
            bounds,
        )
        reference_row = processed_row(parsed[tag]["processed"], case, bounds)
        compare = (bounds[:-1] < 200.0e6) & (
            (np.abs(rust_row) >= 1.0e-12) | (np.abs(reference_row) >= 1.0e-12)
        )
        deviations = np.array(
            [relative(float(left), float(right)) for left, right in zip(rust_row[compare], reference_row[compare])]
        )
        worst_local = int(np.argmax(deviations))
        compared_indices = np.flatnonzero(compare)
        worst_group = int(compared_indices[worst_local])
        rates = {}
        independent_rate_worst = 0.0
        processed_rate_worst = 0.0
        for name, spectrum in fixed_spectra.items():
            rust_rate = float(np.dot(rust_row, spectrum))
            independent_rate = math.fsum(float(value) * float(weight) for value, weight in zip(independent_row, spectrum))
            processed_rate = math.fsum(float(value) * float(weight) for value, weight in zip(reference_row, spectrum))
            independent_deviation = relative(rust_rate, independent_rate)
            processed_deviation = relative(rust_rate, processed_rate)
            independent_rate_worst = max(independent_rate_worst, independent_deviation)
            processed_rate_worst = max(processed_rate_worst, processed_deviation)
            rates[name] = {
                "rust_b": rust_rate,
                "independent_b": independent_rate,
                "processed_b": processed_rate,
                "relative_rust_independent": independent_deviation,
                "relative_rust_processed": processed_deviation,
            }
        above = bounds[:-1] >= 200.0e6
        group_results[case["projectile"]] = {
            "matched_groups": int(np.count_nonzero(compare)),
            "max_row_relative_to_processed": float(deviations[worst_local]),
            "worst_group_eV": [float(bounds[worst_group]), float(bounds[worst_group + 1])],
            "worst_group_rust_b": float(rust_row[worst_group]),
            "worst_group_processed_b": float(reference_row[worst_group]),
            "max_rust_above_200_MeV_b": float(np.max(np.abs(rust_row[above]))),
            "max_processed_extrapolation_above_200_MeV_b": float(np.max(np.abs(reference_row[above]))),
            "fixed_spectra": rates,
            "max_fixed_spectrum_relative_to_processed": processed_rate_worst,
            "max_fixed_spectrum_relative_to_independent": independent_rate_worst,
            "pass": (
                float(deviations[worst_local]) <= PROCESSED_ROW_TOLERANCE
                and processed_rate_worst <= PROCESSED_SPECTRUM_TOLERANCE
                and independent_rate_worst <= INDEPENDENT_RATE_TOLERANCE
                and float(np.max(np.abs(rust_row[above]))) == 0.0
            ),
        }

    structure = structure_checks(CASES[0], parsed["p"][2025], builds["p"][2025])
    rejections = rejection_controls(CASES[0]["raw_2025"])
    build_report = {
        case["projectile"]: {
            str(year): {
                "rows": builds[case["tag"]][year]["rows"],
                "npz_sha256": builds[case["tag"]][year]["npz_sha256"],
                "index_sha256": builds[case["tag"]][year]["index_sha256"],
                "projectile": builds[case["tag"]][year]["index"]["projectile"],
                "temperature_K": builds[case["tag"]][year]["index"]["temperature_K"],
            }
            for year in (2017, 2025)
        }
        for case in CASES
    }
    passed = (
        all(value["pass"] for value in point_results.values())
        and all(value["pass"] for value in group_results.values())
        and len(rejections) == 5
    )
    report = {
        "gate": "P10-G5",
        "protocol_sha256": "74273ec549d113b24367341d1f94f57d0070795d6e679b84a1921d64dbc85b27",
        "amendment_a_sha256": "e7fb61dc755f02675c92c57d2f13f6872a6087e24165b0b3fd128dc86df140fd",
        "amendment_b_sha256": EXPECTED_COMMON[ROOT / "protocols" / "ACTINV-P10_AMENDMENT_B.md"],
        "input_hashes": {
            "TENDL2017data.tar.bz2": hashes[ARCHIVE.name],
            "ebins.tar.bz2": hashes[EBINS_ARCHIVE.name],
            "ebins_162": hashes[EBINS_162.name],
            **{
                f"{case['tag']}_{field}_sha256": case[f"{field}_sha256"]
                for case in CASES
                for field in ("raw_2017", "raw_2025", "processed", "residual")
            },
        },
        "archive_size_bytes": ARCHIVE.stat().st_size,
        "official_groups": 162,
        "point_tolerance": POINT_TOLERANCE,
        "processed_row_tolerance_amended": PROCESSED_ROW_TOLERANCE,
        "processed_fixed_spectrum_tolerance": PROCESSED_SPECTRUM_TOLERANCE,
        "independent_rate_tolerance": INDEPENDENT_RATE_TOLERANCE,
        "pointwise_2025": point_results,
        "processed_2017": group_results,
        "structure": structure,
        "rejections": rejections,
        "builds": build_report,
        "binary_sha256": sha256(ACTINV),
        "dump_sha256": sha256(DUMP),
        "control_sha256": sha256(Path(__file__)),
        "address_space_limit_bytes": ADDRESS_SPACE_BYTES,
        "pass": bool(passed),
    }
    RESULT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
