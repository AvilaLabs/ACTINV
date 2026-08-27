#!/usr/bin/env python3
"""P10-G7 exhaustive, bounded-memory EAF-2010 regression and independent collapse."""
from __future__ import annotations

import bisect
from collections import Counter
from decimal import localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterable

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
from scipy.integrate import quad


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import g1_p10_builder as g1  # noqa: E402
import g7_p10_eaf_product as high_precision  # noqa: E402


EAF = Path(
    os.environ.get("ACTINV_EAF2010_DIR", "/home/connoravila/nuclear-data/eaf-2010/files")
)
LEGACY_LIBRARY = Path(
    os.environ.get(
        "ACTINV_P10_LEGACY_EAF",
        "/home/connoravila/nuclear-data/eaf-2010/actinv_eaf2010_709g.npz",
    )
)
LEGACY_INDEX = Path(
    os.environ.get(
        "ACTINV_P10_LEGACY_EAF_INDEX",
        "/home/connoravila/nuclear-data/eaf-2010/actinv_eaf2010_709g_index.json",
    )
)
CURRENT_LIBRARY = Path(
    os.environ.get(
        "ACTINV_P10_EAF_LIBRARY",
        "/home/connoravila/nuclear-data/tendl-2025/builds/full/eaf.n.p10.npz",
    )
)
CURRENT_INDEX = Path(
    os.environ.get(
        "ACTINV_P10_EAF_INDEX",
        str(CURRENT_LIBRARY.with_name(f"{CURRENT_LIBRARY.stem}_index.json")),
    )
)
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target/release/dump"))
PRODUCT_RESULT = Path(
    os.environ.get(
        "ACTINV_P10_EAF_PRODUCT_RESULT", ROOT / "results/g7_p10_eaf_product.json"
    )
)
AMENDMENT_I = ROOT / "protocols/ACTINV-P10_AMENDMENT_I.md"
AMENDMENT_I_SHA256 = "84d71f4bcdbf28cc40d4f5e58c12d7f8ed3f1dbe5dc869b13a8ca8db54f3a3c5"
AMENDMENT_J = ROOT / "protocols/ACTINV-P10_AMENDMENT_J.md"
AMENDMENT_J_SHA256 = "df7bdb47f1ff59d3c58b916a3414aa528c0f0278cca6d1adf67142b51c149dd9"
AMENDMENT_K = ROOT / "protocols/ACTINV-P10_AMENDMENT_K.md"
AMENDMENT_K_SHA256 = "22a6029aa817206ce52800d943aeadbbd8b9f4e02a9708149f8794860b5733c4"
AMENDMENT_L = ROOT / "protocols/ACTINV-P10_AMENDMENT_L.md"
AMENDMENT_L_SHA256 = "d2f27d7fdb1765246bc67bacb1199c15dfe43e373fbb96d8691f355b214b2873"
AMENDMENT_M = ROOT / "protocols/ACTINV-P10_AMENDMENT_M.md"
AMENDMENT_M_SHA256 = "cd6f73ff415a8b2a34049912766f0b1c838519ea3f0deff7e7bc856115ad0596"
RESULT = Path(
    os.environ.get(
        "ACTINV_P10_EAF_REGRESSION_RESULT", ROOT / "results/g7_p10_eaf_regression.json"
    )
)
BATCH_SIZE = int(os.environ.get("ACTINV_P10_EAF_BATCH_SIZE", "32"))
TARGET_LIMIT = int(os.environ.get("ACTINV_P10_EAF_TARGET_LIMIT", "0"))
TARGET_SELECTION = os.environ.get("ACTINV_P10_EAF_TARGETS", "").strip()

RELATIVE_TOLERANCE = 2e-12
ABSOLUTE_TOLERANCE_B = 1e-14
SCORE_FLOOR_B = 1e-12
MT_PRODUCTS = json.loads((ROOT / "data/mt_products.json").read_text())["table"]
ROUNDOFF_PROBE = ("n_6329_63-EU-152M.dat", (102, -1, -1, 0), 450)
EXPECTED_MF9_LAW_CENSUS = {
    "MF3_INT_2__MF9_INT_2": 142,
    "MF3_INT_2_5__MF9_INT_2": 283,
    "MF3_INT_5__MF9_INT_2": 57,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def target_key(target: dict) -> tuple[str, int, int]:
    return target["file"], int(target["za"]), int(target["liso"])


def chunks(values: list, size: int) -> Iterable[list]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def dump_targets(library: Path, targets: list[int], prefix: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    completed = subprocess.run(
        [
            str(DUMP),
            "library-targets",
            str(library),
            ",".join(str(value) for value in targets),
            str(prefix),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"bounded target extraction failed for {library}: {completed.stderr[-2000:]}"
        )
    row_count, group_count = (int(value) for value in completed.stdout.split())
    rows = np.fromfile(f"{prefix}.rows", dtype="<i8").reshape(row_count, 5)
    sigma = np.fromfile(f"{prefix}.sig", dtype="<f8").reshape(row_count, group_count)
    bounds = np.fromfile(f"{prefix}.bounds", dtype="<f8")
    return rows, sigma, bounds


def table_value(table: dict, energy: float) -> float:
    x = table["x"]
    if energy < x[0] or energy > x[-1]:
        return 0.0
    if energy == x[-1]:
        return float(table["y"][-1])
    segment = min(max(bisect.bisect_right(x, energy) - 1, 0), len(x) - 2)
    return g1.segment_value(table, segment, energy, g1.table_analysis(table)[0][segment])


def interval_power_terms(table: dict, low: float, high: float) -> list[tuple[float, float]] | None:
    middle = low + 0.5 * (high - low)
    x = table["x"]
    if middle < x[0] or middle > x[-1]:
        return [(0.0, 0.0)]
    segment = min(max(bisect.bisect_right(x, middle) - 1, 0), len(x) - 2)
    x1, x2 = x[segment : segment + 2]
    if x2 <= x1:
        raise ValueError("product interval selected a zero-width source segment")
    y1, y2 = table["y"][segment : segment + 2]
    law = g1.table_analysis(table)[0][segment]
    if law == 1:
        return [(float(y1), 0.0)]
    if law == 2:
        slope = (y2 - y1) / (x2 - x1)
        value = y1 + slope * (low - x1)
        scaled_slope = slope * low
        return [(value - scaled_slope, 0.0), (scaled_slope, 1.0)]
    if law == 5:
        power = math.log1p((y2 - y1) / y1) / math.log1p((x2 - x1) / x1)
        value = y1 * math.exp(power * math.log1p((low - x1) / x1))
        return [(value, power)]
    return None


def interval_product_integral(tables: tuple[dict, ...], low: float, high: float) -> float:
    terms = [(1.0, 0.0)]
    for table in tables:
        factors = interval_power_terms(table, low, high)
        if factors is None:
            log_low, log_high = math.log(low), math.log(high)
            value, error = quad(
                lambda log_energy: math.prod(
                    table_value(source, math.exp(log_energy)) for source in tables
                ),
                log_low,
                log_high,
                epsabs=1e-300,
                epsrel=5e-14,
                limit=100,
            )
            if error > max(2e-12 * abs(value), 1e-280):
                raise ArithmeticError(
                    f"independent product quadrature uncertainty {error} for {value}"
                )
            return value
        terms = [
            (left_coefficient * right_coefficient, left_power + right_power)
            for left_coefficient, left_power in terms
            for right_coefficient, right_power in factors
        ]
    log_ratio = math.log1p((high - low) / low)
    return math.fsum(
        coefficient * log_ratio * g1.expm1_over_x(power * log_ratio)
        for coefficient, power in terms
    )


def collapse_product_binary64(tables: tuple[dict, ...], bounds: np.ndarray) -> np.ndarray:
    collapsed = np.zeros(len(bounds) - 1)
    for group, (group_low, group_high) in enumerate(zip(bounds[:-1], bounds[1:])):
        low, high = float(group_low), float(group_high)
        breaks = [low, high]
        for table in tables:
            begin = bisect.bisect_right(table["x"], low)
            end = bisect.bisect_left(table["x"], high)
            breaks.extend(table["x"][begin:end])
        breaks = sorted(set(breaks))
        collapsed[group] = math.fsum(
            interval_product_integral(tables, left, right)
            for left, right in zip(breaks[:-1], breaks[1:])
        ) / math.log1p((high - low) / low)
    return collapsed


def declared_products(evaluation: dict, mt: int, lmf: int) -> set[tuple[int, int]]:
    return {
        (int(product["zap"]), int(product["lfs"]))
        for product in evaluation["mf8"].get(str(mt), [])
        if product["lmf"] == lmf and product["zap"] >= 0
    }


def mf9_law_key(reaction: dict, product: dict) -> str:
    def encoded(table: dict) -> str:
        return "_".join(str(value) for value in sorted(set(g1.table_laws(table))))

    return f"MF3_INT_{encoded(reaction)}__MF9_INT_{encoded(product)}"


def validate_declaration(evaluation: dict, mt: int, lmf: int, actual: set[tuple[int, int]]) -> None:
    declared = declared_products(evaluation, mt, lmf)
    if declared and declared != actual:
        raise ValueError(f"independent MF=8/LMF={lmf} declaration mismatch at MT{mt}")


def independent_rows(evaluation: dict, bounds: np.ndarray) -> dict[tuple[int, int, int, int], dict]:
    """Construct every current EAF row from source fields without invoking production Rust."""
    if evaluation["metadata"]["projectile"] != "neutron" or evaluation["mf6"]:
        raise ValueError("EAF oracle requires a neutron evaluation without MF=6")
    table_cache: dict[int, np.ndarray] = {}
    product_cache: dict[tuple[int, ...], tuple[np.ndarray, np.ndarray]] = {}

    def collapse(table: dict) -> np.ndarray:
        key = id(table)
        if key not in table_cache:
            table_cache[key] = g1.collapse_table(table, bounds)
        return table_cache[key]

    def product(tables: tuple[dict, ...]) -> tuple[np.ndarray, np.ndarray]:
        key = tuple(id(table) for table in tables)
        if key not in product_cache:
            binary64 = collapse_product_binary64(tables, bounds)
            with localcontext() as context:
                context.prec = 80
                decimal_values = high_precision.collapse_product(list(tables), bounds)
            product_cache[key] = (
                np.asarray([float(value) for value in decimal_values]),
                binary64,
            )
        return product_cache[key]

    records: list[dict] = []
    mts = sorted({*map(int, evaluation["mf3"]), *map(int, evaluation["mf10"])})
    for mt in mts:
        descriptors = evaluation["mf8"].get(str(mt), [])
        if g1.skip_mt(mt, "neutron"):
            continue
        if g1.inelastic(mt):
            products = evaluation["mf10"].get(str(mt), [])
            if any(item["zap"] < 0 for item in products):
                raise ValueError(f"MT{mt}/MF=10 contains a negative inelastic product")
            actual = {(int(item["zap"]), int(item["lfs"])) for item in products}
            if len(actual) != len(products):
                raise ValueError(f"MT{mt}/MF=10 contains duplicate products")
            validate_declaration(evaluation, mt, 10, actual)
            metastable = [item for item in products if item["lfs"] > 0]
            if not metastable:
                continue
            values = [(item, collapse(item["table"])) for item in metastable]
            total = np.zeros(len(bounds) - 1)
            for _, value in values:
                total += value
            records.append(
                {
                    "key": [mt, -1, -1, 0],
                    "values": total,
                    "direct": None,
                    "fixed_reason": "inelastic_semantics",
                }
            )
            for item, value in values:
                records.append(
                    {
                        "key": [mt, int(item["zap"]), int(item["lfs"]), 10],
                        "values": value,
                        "direct": (item["table"],),
                        "fixed_reason": "inelastic_semantics",
                    }
                )
            continue

        total_source = evaluation["mf3"].get(str(mt))
        if total_source is not None:
            total = collapse(total_source)
        else:
            products = evaluation["mf10"].get(str(mt), [])
            sentinel = next((item for item in products if item["zap"] == -1), None)
            if sentinel is not None:
                total_source = sentinel["table"]
                total = collapse(total_source)
            else:
                total = np.zeros(len(bounds) - 1)
                for item in products:
                    if item["zap"] >= 0:
                        total += collapse(item["table"])
        records.append(
            {
                "key": [mt, -1, -1, 0],
                "values": total,
                "direct": (total_source,) if total_source is not None else None,
                "fixed_reason": None if total_source is not None else "no_single_direct_source",
            }
        )
        done: set[tuple[int, int]] = set()
        mf10 = evaluation["mf10"].get(str(mt), [])
        nonnegative = [item for item in mf10 if item["zap"] >= 0]
        actual_mf10 = {(int(item["zap"]), int(item["lfs"])) for item in nonnegative}
        if len(actual_mf10) + sum(item["zap"] < 0 for item in mf10) != len(mf10):
            raise ValueError(f"MT{mt}/MF=10 contains duplicate products")
        validate_declaration(evaluation, mt, 10, actual_mf10)
        for item in nonnegative:
            identity = (int(item["zap"]), int(item["lfs"]))
            if identity in done:
                raise ValueError(f"conflicting independent product at MT{mt}/{identity}")
            done.add(identity)
            records.append(
                {
                    "key": [mt, *identity, 10],
                    "values": collapse(item["table"]),
                    "direct": (item["table"],),
                    "fixed_reason": None,
                }
            )

        mf9 = evaluation["mf9"].get(str(mt), [])
        actual_mf9 = {(int(item["zap"]), int(item["lfs"])) for item in mf9}
        if len(actual_mf9) != len(mf9):
            raise ValueError(f"MT{mt}/MF=9 contains duplicate products")
        validate_declaration(evaluation, mt, 9, actual_mf9)
        if mf9 and total_source is None:
            raise ValueError(f"MT{mt}/MF=9 has no matching MF=3 table")
        for item in mf9:
            identity = (int(item["zap"]), int(item["lfs"]))
            if identity in done:
                raise ValueError(f"conflicting independent product at MT{mt}/{identity}")
            done.add(identity)
            tables = (total_source, item["table"])
            exact_values, binary64_values = product(tables)
            records.append(
                {
                    "key": [mt, *identity, 9],
                    "values": exact_values,
                    "binary64_values": binary64_values,
                    "direct": tables,
                    "fixed_reason": None,
                }
            )

        for descriptor in (item for item in descriptors if item["lmf"] == 3):
            identity = (int(descriptor["zap"]), int(descriptor["lfs"]))
            if identity in done:
                raise ValueError(f"conflicting independent product at MT{mt}/{identity}")
            done.add(identity)
            records.append(
                {
                    "key": [mt, *identity, 3],
                    "values": total.copy(),
                    "direct": (total_source,) if total_source is not None else None,
                    "fixed_reason": None if total_source is not None else "no_single_direct_source",
                }
            )
        if not done:
            if mt == 18:
                product_za, lmf = 0, 0
            elif str(mt) in MT_PRODUCTS:
                product_za = g1.neutron_residual(
                    evaluation["metadata"]["za"], MT_PRODUCTS[str(mt)]
                )
                product_za, lmf = (product_za, -1) if product_za is not None else (0, -2)
            else:
                product_za, lmf = 0, -2
            records.append(
                {
                    "key": [mt, product_za, 0, lmf],
                    "values": total.copy(),
                    "direct": (total_source,) if total_source is not None else None,
                    "fixed_reason": None if total_source is not None else "no_single_direct_source",
                }
            )

    levels: dict[tuple[int, int], set[int]] = {}
    for record in records:
        mt, zap, lfs, _ = record["key"]
        if lfs > 0:
            levels.setdefault((mt, zap), set()).add(lfs)
    for (mt, zap), original in levels.items():
        mapping = {value: index + 1 for index, value in enumerate(sorted(original))}
        for record in records:
            if record["key"][0] == mt and record["key"][1] == zap and record["key"][2] > 0:
                record["key"][2] = mapping[record["key"][2]]

    result = {}
    for record in records:
        identity = tuple(record.pop("key"))
        if identity in result:
            raise ValueError(f"duplicate independent row identity {identity}")
        result[identity] = record
    return result


def product_analysis(tables: tuple[dict, ...], bounds: np.ndarray) -> tuple[dict[str, np.ndarray], np.ndarray]:
    groups = len(bounds) - 1
    masks = {
        "source_edge": np.zeros(groups, dtype=bool),
        "no_source_segment": np.zeros(groups, dtype=bool),
        "nonlinear_mf9_product": np.zeros(groups, dtype=bool),
        "value_changing_duplicate": np.zeros(groups, dtype=bool),
    }
    roundoff = np.zeros(groups)
    support_low = max(table["x"][0] for table in tables)
    support_high = min(table["x"][-1] for table in tables)
    laws = [g1.table_laws(table) for table in tables]
    duplicates = [
        {
            table["x"][index]
            for index in range(len(table["x"]) - 1)
            if table["x"][index] == table["x"][index + 1]
            and table["y"][index] != table["y"][index + 1]
        }
        for table in tables
    ]
    epsilon = np.finfo(float).eps
    for group, (low_value, high_value) in enumerate(zip(bounds[:-1], bounds[1:])):
        low, high = float(low_value), float(high_value)
        if low < support_low or high > support_high:
            masks["source_edge"][group] = True
        if any(any(low <= value <= high for value in values) for values in duplicates):
            masks["value_changing_duplicate"][group] = True
        breaks = [low, high]
        for table in tables:
            breaks.extend(
                value for value in table["x"] if low < value < high
            )
        breaks = sorted(set(breaks))
        terms = []
        intercepts = []
        contributed = False
        nonlinear = False
        for left, right in zip(breaks[:-1], breaks[1:]):
            middle = left + 0.5 * (right - left)
            if not all(table["x"][0] <= middle <= table["x"][-1] for table in tables):
                continue
            contributed = True
            segments = [
                min(max(bisect.bisect_right(table["x"], middle) - 1, 0), len(table["x"]) - 2)
                for table in tables
            ]
            interval_laws = [law[segment] for law, segment in zip(laws, segments)]
            constant = [
                interval_law == 1
                or table["y"][segment] == table["y"][segment + 1]
                for table, segment, interval_law in zip(tables, segments, interval_laws)
            ]
            if any(value not in (1, 2) for value in interval_laws) or not any(constant):
                nonlinear = True
                continue
            value_left = math.prod(table_value(table, left) for table in tables)
            value_right = math.prod(table_value(table, right) for table in tables)
            slope = (value_right - value_left) / (right - left)
            intercept = value_left - slope * left
            intercepts.append(intercept)
            terms.extend((intercept * math.log(right / left), slope * (right - left)))
        if not contributed:
            masks["no_source_segment"][group] = True
        if nonlinear:
            masks["nonlinear_mf9_product"][group] = True
        operations = 32 * max(len(terms) // 2, 1) + 64
        gamma = operations * epsilon / (1.0 - operations * epsilon)
        group_log = math.log1p((high - low) / low)
        log_allowance = 8.0 * epsilon
        if group_log <= log_allowance:
            roundoff[group] = math.inf
            continue
        absolute_terms = math.fsum(abs(value) for value in terms)
        numerator_bound = gamma * absolute_terms + log_allowance * math.fsum(
            abs(value) for value in intercepts
        )
        denominator = group_log - log_allowance
        roundoff[group] = numerator_bound / denominator + (
            (absolute_terms + numerator_bound)
            * log_allowance
            / (group_log * denominator)
        )
    return masks, roundoff


def single_analysis(table: dict, bounds: np.ndarray) -> tuple[dict[str, np.ndarray], np.ndarray]:
    groups = len(bounds) - 1
    names = ("source_edge", "no_source_segment", "non_lin_lin", "value_changing_duplicate")
    masks = {name: np.zeros(groups, dtype=bool) for name in names}
    roundoff = np.zeros(groups)
    for group, (low, high) in enumerate(zip(bounds[:-1], bounds[1:])):
        reasons = g1.unchanged_group_reasons(table, float(low), float(high))
        for reason in reasons:
            masks[reason][group] = True
        if not reasons:
            roundoff[group] = g1.legacy_linlin_roundoff_bound(table, float(low), float(high))
    return masks, roundoff


def pre_amendment_i_bound(table: dict, low: float, high: float) -> float:
    x = table["x"]
    start = bisect.bisect_right(x, low)
    stop = bisect.bisect_left(x, high)
    grid = [low, *x[start:stop], high]
    terms = []
    for left, right in zip(grid[:-1], grid[1:]):
        segment = min(max(bisect.bisect_right(x, left) - 1, 0), len(x) - 2)
        value_left = g1.segment_value(table, segment, left, 2)
        value_right = g1.segment_value(table, segment, right, 2)
        slope = (value_right - value_left) / (right - left)
        intercept = value_left - slope * left
        terms.extend(
            (intercept * math.log(right / left), slope * (right - left))
        )
    operations = 32 * max(len(grid) - 1, 1) + 64
    epsilon = np.finfo(float).eps
    gamma = operations * epsilon / (1.0 - operations * epsilon)
    return gamma * math.fsum(abs(value) for value in terms) / math.log(high / low)


def fresh_score() -> dict:
    return {
        "points": 0,
        "above_score_floor": 0,
        "maximum_absolute_b": 0.0,
        "maximum_relative": 0.0,
        "maximum_tolerance_fraction": 0.0,
        "worst": None,
        "failure_examples": [],
    }


def update_score(score: dict, left: np.ndarray, right: np.ndarray, identity: dict, mask: np.ndarray | None = None) -> None:
    if left.shape != right.shape:
        raise ValueError(f"cannot compare shapes {left.shape} and {right.shape}")
    selected = np.ones(left.shape, dtype=bool) if mask is None else mask.copy()
    score["points"] += int(np.count_nonzero(selected))
    finite = np.isfinite(left) & np.isfinite(right)
    magnitude = np.maximum(np.abs(left), np.abs(right))
    score["above_score_floor"] += int(np.count_nonzero(selected & (magnitude >= SCORE_FLOOR_B)))
    absolute = np.abs(left - right)
    tolerance = np.maximum(ABSOLUTE_TOLERANCE_B, RELATIVE_TOLERANCE * magnitude)
    fraction = np.divide(absolute, tolerance, out=np.full_like(absolute, np.inf), where=tolerance > 0)
    relative = np.divide(absolute, magnitude, out=np.zeros_like(absolute), where=magnitude > 0)
    valid = selected & finite
    if np.any(valid):
        local = np.where(valid, fraction, -1.0)
        group = int(np.argmax(local))
        if fraction[group] > score["maximum_tolerance_fraction"]:
            score["maximum_tolerance_fraction"] = float(fraction[group])
            score["worst"] = {
                **identity,
                "group": group,
                "left_b": float(left[group]),
                "right_b": float(right[group]),
                "absolute_b": float(absolute[group]),
                "relative": float(relative[group]),
                "tolerance_b": float(tolerance[group]),
            }
        score["maximum_absolute_b"] = max(
            score["maximum_absolute_b"], float(np.max(absolute[valid]))
        )
        score["maximum_relative"] = max(
            score["maximum_relative"], float(np.max(relative[valid]))
        )
    failures = selected & (~finite | (absolute > tolerance))
    for group in np.flatnonzero(failures):
        if len(score["failure_examples"]) >= 10:
            break
        score["failure_examples"].append(
            {
                **identity,
                "group": int(group),
                "left_b": float(left[group]),
                "right_b": float(right[group]),
                "absolute_b": float(absolute[group]),
                "tolerance_b": float(tolerance[group]),
            }
        )


def finish_score(score: dict) -> dict:
    score["pass"] = not score["failure_examples"]
    return score


def classify_changes(
    old_identities: set[tuple[int, int, int, int]],
    new_identities: set[tuple[int, int, int, int]],
) -> tuple[list[dict], bool]:
    changes = []
    passed = True
    old_only = sorted(old_identities - new_identities)
    new_only = sorted(new_identities - old_identities)
    for identity in old_only:
        mt, zap, lfs, lmf = identity
        reason = None
        if g1.inelastic(mt) and (zap, lfs, lmf) == (-1, -1, 0):
            reason = "inelastic_without_metastable_omitted"
        elif g1.inelastic(mt) and lfs == 0 and lmf in (3, 10):
            reason = "inelastic_ground_state_omitted"
        elif g1.inelastic(mt) and lfs > 0 and any(
            candidate[0] == mt
            and candidate[1] == zap
            and candidate[2] > 0
            and candidate[3] == lmf
            for candidate in new_only
        ):
            reason = "decay_isomer_level_remap"
        else:
            passed = False
        changes.append({"side": "legacy_only", "identity": list(identity), "reason": reason})
    for identity in new_only:
        mt, zap, lfs, lmf = identity
        reason = None
        if g1.inelastic(mt) and lfs > 0 and any(
            candidate[0] == mt
            and candidate[1] == zap
            and candidate[2] > 0
            and candidate[3] == lmf
            for candidate in old_only
        ):
            reason = "decay_isomer_level_remap"
        else:
            passed = False
        changes.append({"side": "current_only", "identity": list(identity), "reason": reason})
    return changes, passed


def main() -> None:
    if BATCH_SIZE < 1:
        raise ValueError("ACTINV_P10_EAF_BATCH_SIZE must be positive")
    legacy_index = json.loads(LEGACY_INDEX.read_text())
    current_index = json.loads(CURRENT_INDEX.read_text())
    old_by_key = {
        target_key(target): index for index, target in enumerate(legacy_index["targets"])
    }
    new_by_key = {
        target_key(target): index for index, target in enumerate(current_index["targets"])
    }
    if set(old_by_key) != set(new_by_key):
        raise ValueError("legacy/current EAF target inventories differ")
    selected_keys = sorted(new_by_key, key=lambda key: new_by_key[key])
    if TARGET_SELECTION:
        requested = [int(value) for value in TARGET_SELECTION.split(",")]
        if len(set(requested)) != len(requested) or any(
            value < 0 or value >= len(selected_keys) for value in requested
        ):
            raise ValueError("ACTINV_P10_EAF_TARGETS contains an invalid target index")
        selected_keys = [selected_keys[value] for value in requested]
    if TARGET_LIMIT:
        if TARGET_SELECTION:
            raise ValueError("target selection and target limit are mutually exclusive")
        selected_keys = selected_keys[:TARGET_LIMIT]
    complete_corpus = len(selected_keys) == len(new_by_key) == 816

    independent_score = fresh_score()
    legacy_score = fresh_score()
    binary64_product_score = fresh_score()
    reason_counts: dict[str, int] = {}
    structural_changes = []
    structural_pass = True
    rows_checked = 0
    peak_materialized_bytes = 0
    mf9_rows = 0
    mf9_files = 0
    mf9_law_census: Counter[str] = Counter()
    roundoff_probe = None
    analysis_cache: dict[tuple[int, ...], tuple[dict[str, np.ndarray], np.ndarray]] = {}

    with tempfile.TemporaryDirectory(
        prefix="actinv-p10-eaf-regression-", dir=CURRENT_LIBRARY.parent
    ) as raw:
        work = Path(raw)
        expected_bounds = None
        for batch_number, batch_keys in enumerate(chunks(selected_keys, BATCH_SIZE)):
            old_targets = [old_by_key[key] for key in batch_keys]
            new_targets = [new_by_key[key] for key in batch_keys]
            old_prefix = work / f"old-{batch_number}"
            new_prefix = work / f"new-{batch_number}"
            old_rows, old_sigma, old_bounds = dump_targets(
                LEGACY_LIBRARY, old_targets, old_prefix
            )
            new_rows, new_sigma, new_bounds = dump_targets(
                CURRENT_LIBRARY, new_targets, new_prefix
            )
            peak_materialized_bytes = max(
                peak_materialized_bytes,
                sum(
                    Path(f"{prefix}.{suffix}").stat().st_size
                    for prefix in (old_prefix, new_prefix)
                    for suffix in ("rows", "sig", "bounds")
                ),
            )
            if not np.array_equal(old_bounds, new_bounds):
                raise ValueError("legacy/current EAF group boundaries differ")
            if expected_bounds is None:
                expected_bounds = new_bounds.copy()
            elif not np.array_equal(expected_bounds, new_bounds):
                raise ValueError("batch group boundaries changed")

            for key in batch_keys:
                old_target = old_by_key[key]
                new_target = new_by_key[key]
                old_mask = old_rows[:, 0] == old_target
                new_mask = new_rows[:, 0] == new_target
                old_target_rows = old_rows[old_mask, 1:]
                new_target_rows = new_rows[new_mask, 1:]
                old_values = old_sigma[old_mask]
                new_values = new_sigma[new_mask]
                old_map = {
                    tuple(int(value) for value in row): number
                    for number, row in enumerate(old_target_rows)
                }
                new_map = {
                    tuple(int(value) for value in row): number
                    for number, row in enumerate(new_target_rows)
                }
                if len(old_map) != len(old_target_rows) or len(new_map) != len(new_target_rows):
                    raise ValueError(f"duplicate row identity in {key[0]}")

                source = EAF / key[0]
                current_target = current_index["targets"][new_target]
                if sha256(source) != current_target["source_sha256"]:
                    raise ValueError(f"source hash mismatch for {key[0]}")
                evaluations = g1.parse_evaluations(source)
                if len(evaluations) != 1:
                    raise ValueError(f"expected one material in {key[0]}")
                evaluation = evaluations[0]
                if (
                    evaluation["metadata"]["za"] != key[1]
                    or evaluation["metadata"]["liso"] != key[2]
                ):
                    raise ValueError(f"source metadata mismatch for {key[0]}")
                if evaluation["mf9"]:
                    mf9_files += 1
                    for mt, products in evaluation["mf9"].items():
                        reaction = evaluation["mf3"][mt]
                        for product in products:
                            mf9_law_census[mf9_law_key(reaction, product["table"])] += 1
                g1.TABLE_ANALYSIS_CACHE.clear()
                analysis_cache.clear()
                expected = independent_rows(evaluation, new_bounds)
                expected_identities = set(expected)
                new_identities = set(new_map)
                target_structural_pass = expected_identities == new_identities
                structural_pass &= target_structural_pass
                changes, changes_pass = classify_changes(set(old_map), new_identities)
                structural_pass &= changes_pass
                if changes or not target_structural_pass:
                    structural_changes.append(
                        {
                            "file": key[0],
                            "target_za": key[1],
                            "target_liso": key[2],
                            "changes": changes,
                            "independent_only": [
                                list(value) for value in sorted(expected_identities - new_identities)
                            ],
                            "current_only_vs_independent": [
                                list(value) for value in sorted(new_identities - expected_identities)
                            ],
                            "pass": target_structural_pass and changes_pass,
                        }
                    )

                for identity in sorted(expected_identities & new_identities):
                    oracle = expected[identity]
                    current = new_values[new_map[identity]]
                    label = {"file": key[0], "identity": list(identity)}
                    update_score(independent_score, oracle["values"], current, label)
                    if "binary64_values" in oracle:
                        update_score(
                            binary64_product_score,
                            oracle["binary64_values"],
                            oracle["values"],
                            label,
                        )
                    rows_checked += 1
                    if identity[3] == 9:
                        mf9_rows += 1
                    if identity not in old_map:
                        continue
                    legacy = old_values[old_map[identity]]
                    magnitude = np.maximum(np.abs(legacy), np.abs(current))
                    scored = magnitude >= SCORE_FLOOR_B
                    if oracle["fixed_reason"]:
                        masks = {
                            oracle["fixed_reason"]: np.ones(len(new_bounds) - 1, dtype=bool)
                        }
                        roundoff = np.zeros(len(new_bounds) - 1)
                    elif oracle["direct"] is None:
                        masks = {
                            "no_single_direct_source": np.ones(len(new_bounds) - 1, dtype=bool)
                        }
                        roundoff = np.zeros(len(new_bounds) - 1)
                    else:
                        cache_key = tuple(id(table) for table in oracle["direct"])
                        if cache_key not in analysis_cache:
                            analysis_cache[cache_key] = (
                                single_analysis(oracle["direct"][0], new_bounds)
                                if len(oracle["direct"]) == 1
                                else product_analysis(oracle["direct"], new_bounds)
                            )
                        masks, roundoff = analysis_cache[cache_key]
                    excluded = np.zeros(len(new_bounds) - 1, dtype=bool)
                    for reason, reason_mask in masks.items():
                        active = scored & reason_mask
                        reason_counts[reason] = reason_counts.get(reason, 0) + int(
                            np.count_nonzero(active)
                        )
                        excluded |= reason_mask
                    tolerance = np.maximum(
                        ABSOLUTE_TOLERANCE_B,
                        RELATIVE_TOLERANCE * magnitude,
                    )
                    roundoff_mask = ~excluded & (roundoff > tolerance)
                    reason_counts["legacy_roundoff_bound"] = reason_counts.get(
                        "legacy_roundoff_bound", 0
                    ) + int(np.count_nonzero(scored & roundoff_mask))
                    excluded |= roundoff_mask
                    eligible = scored & ~excluded
                    update_score(legacy_score, legacy, current, label, eligible)
                    if (key[0], identity, ROUNDOFF_PROBE[2]) == ROUNDOFF_PROBE:
                        group = ROUNDOFF_PROBE[2]
                        observed = abs(float(legacy[group]) - float(current[group]))
                        independent_error = abs(
                            float(oracle["values"][group]) - float(current[group])
                        )
                        old_bound = pre_amendment_i_bound(
                            oracle["direct"][0],
                            float(new_bounds[group]),
                            float(new_bounds[group + 1]),
                        )
                        roundoff_probe = {
                            "file": key[0],
                            "source_sha256": current_target["source_sha256"],
                            "identity": list(identity),
                            "group": group,
                            "bounds_eV": new_bounds[group : group + 2].tolist(),
                            "legacy_b": float(legacy[group]),
                            "current_b": float(current[group]),
                            "independent_b": float(oracle["values"][group]),
                            "observed_legacy_deviation_b": observed,
                            "pre_amendment_i_bound_b": float(old_bound),
                            "corrected_bound_b": float(roundoff[group]),
                            "tolerance_b": float(tolerance[group]),
                            "pass": bool(
                                old_bound < observed
                                and observed > tolerance[group]
                                and roundoff[group] >= observed
                                and independent_error <= tolerance[group]
                            ),
                        }

            print(
                f"batch {batch_number + 1}: {min((batch_number + 1) * BATCH_SIZE, len(selected_keys))}/"
                f"{len(selected_keys)} targets, {rows_checked} current rows",
                flush=True,
            )

    independent = finish_score(independent_score)
    unchanged = finish_score(legacy_score)
    binary64_product = finish_score(binary64_product_score)
    product_control = json.loads(PRODUCT_RESULT.read_text())
    product_fingerprints = {
        case["builder_fingerprint"] for case in product_control["cases"].values()
    }
    product_summary = {
        "result_sha256": sha256(PRODUCT_RESULT),
        "rows": sum(case["product_rows"] for case in product_control["cases"].values()),
        "builder_fingerprints": sorted(product_fingerprints),
        "matches_current_builder": product_fingerprints
        == {current_index["builder_fingerprint"]},
        "pass": product_control["pass"]
        and product_fingerprints == {current_index["builder_fingerprint"]},
    }
    output = {
        "schema": "actinv-p10-g7-eaf-regression-1",
        "gate": "P10-G7",
        "relative_tolerance": RELATIVE_TOLERANCE,
        "absolute_tolerance_b": ABSOLUTE_TOLERANCE_B,
        "score_floor_b": SCORE_FLOOR_B,
        "inputs": {
            "legacy_library_sha256": sha256(LEGACY_LIBRARY),
            "legacy_index_sha256": sha256(LEGACY_INDEX),
            "current_library_sha256": sha256(CURRENT_LIBRARY),
            "current_index_sha256": sha256(CURRENT_INDEX),
            "current_builder_fingerprint": current_index["builder_fingerprint"],
        },
        "complete_corpus": complete_corpus,
        "targets_checked": len(selected_keys),
        "current_rows_checked": rows_checked,
        "mf9_product_rows_checked": mf9_rows,
        "mf9_high_precision_census": {
            "precision_decimal_digits": 80,
            "files": mf9_files,
            "rows": mf9_rows,
            "law_pairs": dict(sorted(mf9_law_census.items())),
            "pass": mf9_files == 230
            and mf9_rows == 482
            and dict(mf9_law_census) == EXPECTED_MF9_LAW_CENSUS,
        },
        "bounded_extraction": {
            "batch_size": BATCH_SIZE,
            "peak_materialized_bytes": peak_materialized_bytes,
            "full_sigma_arrays_loaded_in_python": False,
            "pass": peak_materialized_bytes < 512 * 1024**2,
        },
        "structural": {
            "legacy_rows": legacy_index["n_rows"],
            "current_rows": current_index["n_rows"],
            "enumerated_target_changes": structural_changes,
            "pass": structural_pass,
        },
        "unchanged_legacy_domain": {
            **unchanged,
            "excluded_scored_groups_by_reason": dict(sorted(reason_counts.items())),
        },
        "independent_current_collapse": independent,
        "binary64_product_conditioning_diagnostic": binary64_product,
        "high_precision_product_control": product_summary,
        "ratio_log_roundoff_probe": roundoff_probe,
        "amendment_i_sha256": sha256(AMENDMENT_I),
        "amendment_j_sha256": sha256(AMENDMENT_J),
        "amendment_k_sha256": sha256(AMENDMENT_K),
        "amendment_l_sha256": sha256(AMENDMENT_L),
        "amendment_m_sha256": sha256(AMENDMENT_M),
        "pass": complete_corpus
        and rows_checked == current_index["n_rows"]
        and structural_pass
        and unchanged["pass"]
        and independent["pass"]
        and mf9_files == 230
        and mf9_rows == 482
        and dict(mf9_law_census) == EXPECTED_MF9_LAW_CENSUS
        and product_summary["pass"]
        and roundoff_probe is not None
        and roundoff_probe["pass"]
        and sha256(AMENDMENT_I) == AMENDMENT_I_SHA256
        and sha256(AMENDMENT_J) == AMENDMENT_J_SHA256
        and sha256(AMENDMENT_K) == AMENDMENT_K_SHA256
        and sha256(AMENDMENT_L) == AMENDMENT_L_SHA256
        and sha256(AMENDMENT_M) == AMENDMENT_M_SHA256
        and peak_materialized_bytes < 512 * 1024**2,
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
