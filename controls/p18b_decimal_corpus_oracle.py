#!/usr/bin/env python3
"""Independent exact-decimal oracle for retained P18b corpus boundaries.

This module deliberately imports no ACTINV parser, collapse, or classifier. It reads the original ENDF 11-character
fields, evaluates the lin-lin corpus tables with ``Decimal``, and repeats every retained source boundary at 80 and
120 digits. Large checkpoints and nuclear-data inputs remain outside Git.
"""

from __future__ import annotations

import bisect
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, getcontext, localcontext
import hashlib
import json
from pathlib import Path
import struct
from typing import Any


@dataclass(frozen=True)
class ExactValue:
    value: Decimal
    quantum: Decimal

    def ordinate(self, bound: str) -> Decimal:
        if bound == "value":
            return self.value
        if bound == "lower":
            return max(Decimal(0), self.value - self.quantum / 2)
        if bound == "upper":
            return self.value + self.quantum / 2
        raise ValueError(f"unknown ordinate bound {bound!r}")


@dataclass(frozen=True)
class ExactTable:
    interpolation: tuple[tuple[int, int], ...]
    x: tuple[Decimal, ...]
    y: tuple[ExactValue, ...]


@dataclass(frozen=True)
class ExactProduct:
    zap: int
    lfs: int
    qi: ExactValue
    table: ExactTable


@dataclass(frozen=True)
class ExactEvaluation:
    awr: ExactValue
    awi: ExactValue
    mf3: dict[int, ExactTable]
    mf9: dict[int, tuple[ExactProduct, ...]]
    mf10: dict[int, tuple[ExactProduct, ...]]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def float_bits(value: float) -> str:
    return f"{struct.unpack('>Q', struct.pack('>d', value))[0]:016x}"


def split_real(field: str) -> tuple[str, int]:
    text = field.strip()
    explicit = next((index for index, char in enumerate(text) if char in "eE"), None)
    implicit = next(
        (
            index
            for index in range(1, len(text))
            if text[index] in "+-" and text[index - 1] not in "eE"
        ),
        None,
    )
    split = explicit if explicit is not None else implicit
    if split is None:
        return text, 0
    exponent_text = text[split + 1 :] if text[split] in "eE" else text[split:]
    if not exponent_text or exponent_text in {"+", "-"}:
        raise ValueError(f"invalid exponent in {field!r}")
    return text[:split], int(exponent_text)


def parse_exact(field: str) -> ExactValue:
    require(len(field) == 11 and field.isascii(), "ENDF real field width/encoding")
    if not field.strip():
        return ExactValue(Decimal(0), Decimal(0))
    mantissa, exponent = split_real(field)
    try:
        value = Decimal(mantissa).scaleb(exponent)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"invalid ENDF real {field!r}") from error
    require(value.is_finite(), f"nonfinite ENDF real {field!r}")
    decimals = len(mantissa.partition(".")[2]) if "." in mantissa else 0
    return ExactValue(value, Decimal(1).scaleb(exponent - decimals))


def fields(line: str) -> tuple[str, ...]:
    require(len(line) >= 66 and line.isascii(), "truncated or non-ASCII ENDF record")
    return tuple(line[index : index + 11] for index in range(0, 66, 11))


def integer(field: str) -> int:
    return int(field.strip() or "0")


def sections(text: str, mat: int) -> dict[tuple[int, int], tuple[str, ...]]:
    output: dict[tuple[int, int], list[str]] = {}
    active: tuple[int, int] | None = None
    for line_number, line in enumerate(text.splitlines(), 1):
        require(len(line) == 80, f"record {line_number} is not 80 columns")
        try:
            record_mat = int(line[66:70])
            mf = int(line[70:72])
            mt = int(line[72:75])
        except ValueError as error:
            raise AssertionError(f"record {line_number} has malformed identifiers") from error
        if record_mat == mat and mf > 0 and mt > 0:
            key = (mf, mt)
            if key != active:
                require(key not in output, f"duplicate MAT={mat}/MF={mf}/MT={mt}")
                output[key] = []
                active = key
            output[key].append(line)
        elif record_mat == mat and mt == 0:
            active = None
    return {key: tuple(value) for key, value in output.items()}


def read_tab1(
    lines: tuple[str, ...], index: int
) -> tuple[ExactValue, ExactValue, int, int, ExactTable, int]:
    head = fields(lines[index])
    c1, c2 = parse_exact(head[0]), parse_exact(head[1])
    l1, l2 = integer(head[2]), integer(head[3])
    nr, np = integer(head[4]), integer(head[5])
    require(nr > 0 and np > 0, "TAB1 NR/NP must be positive")
    next_index = index + 1
    raw_interpolation: list[int] = []
    while len(raw_interpolation) < nr * 2:
        require(next_index < len(lines), "truncated TAB1 interpolation")
        for field in fields(lines[next_index]):
            if len(raw_interpolation) == nr * 2:
                break
            raw_interpolation.append(integer(field))
        next_index += 1
    interpolation = tuple(
        (raw_interpolation[index], raw_interpolation[index + 1])
        for index in range(0, len(raw_interpolation), 2)
    )
    raw_points: list[ExactValue] = []
    while len(raw_points) < np * 2:
        require(next_index < len(lines), "truncated TAB1 points")
        for field in fields(lines[next_index]):
            if len(raw_points) == np * 2:
                break
            raw_points.append(parse_exact(field))
        next_index += 1
    x = tuple(raw_points[index].value for index in range(0, len(raw_points), 2))
    y = tuple(raw_points[index] for index in range(1, len(raw_points), 2))
    require(all(left <= right for left, right in zip(x, x[1:])), "decreasing TAB1 grid")
    require(interpolation[-1][0] == len(x), "TAB1 interpolation endpoint")
    require(all(law == 2 for _, law in interpolation), "P18b corpus oracle expects lin-lin")
    return c1, c2, l1, l2, ExactTable(interpolation, x, y), next_index


def parse_evaluation(path: Path, mat: int) -> ExactEvaluation:
    text = path.read_text(encoding="ascii")
    source = sections(text, mat)
    mf3: dict[int, ExactTable] = {}
    products: dict[int, dict[int, tuple[ExactProduct, ...]]] = {9: {}, 10: {}}
    awr: ExactValue | None = None
    awi: ExactValue | None = None
    for (mf, mt), lines in source.items():
        if mf == 1 and mt == 451:
            awr = parse_exact(fields(lines[0])[1])
            require(len(lines) >= 3, "truncated MF=1/MT=451 metadata")
            awi = parse_exact(fields(lines[2])[0])
        elif mf == 3:
            _, _, _, _, table, next_index = read_tab1(lines, 1)
            require(next_index == len(lines), f"MF=3/MT={mt} trailing records")
            mf3[mt] = table
        elif mf in {9, 10}:
            count = integer(fields(lines[0])[4])
            next_index = 1
            parsed: list[ExactProduct] = []
            for _ in range(count):
                _, qi, zap, lfs, table, next_index = read_tab1(lines, next_index)
                parsed.append(ExactProduct(zap, lfs, qi, table))
            require(next_index == len(lines), f"MF={mf}/MT={mt} trailing records")
            unique: dict[tuple[int, int], ExactProduct] = {}
            for product in parsed:
                key = (product.zap, product.lfs)
                if key in unique:
                    require(unique[key] == product, f"conflicting duplicate MF={mf}/MT={mt} {key}")
                else:
                    unique[key] = product
            products[mf][mt] = tuple(unique.values())
    require(awr is not None and awi is not None, "missing MF=1 mass metadata")
    return ExactEvaluation(awr, awi, mf3, products[9], products[10])


def ordinates(table: ExactTable, bound: str) -> tuple[Decimal, ...]:
    return tuple(value.ordinate(bound) for value in table.y)


def table_value(table: ExactTable, query: Decimal, side: str, bound: str) -> Decimal:
    if side == "right":
        if query < table.x[0] or query > table.x[-1]:
            return Decimal(0)
        if query == table.x[-1]:
            return table.y[-1].ordinate(bound)
        segment = min(max(bisect.bisect_right(table.x, query) - 1, 0), len(table.x) - 2)
    elif side == "left":
        if query <= table.x[0] or query > table.x[-1]:
            return Decimal(0)
        segment = bisect.bisect_left(table.x, query) - 1
    else:
        raise ValueError(f"unsupported side {side!r}")
    x1, x2 = table.x[segment], table.x[segment + 1]
    if x2 == x1:
        return table.y[segment + 1].ordinate(bound)
    y1 = table.y[segment].ordinate(bound)
    y2 = table.y[segment + 1].ordinate(bound)
    return y1 + (query - x1) / (x2 - x1) * (y2 - y1)


def query_energy(total: ExactTable, products: tuple[ExactProduct, ...], bits: str) -> Decimal:
    candidates = {
        value
        for value in (
            *total.x,
            *(point for product in products for point in product.table.x),
        )
        if float_bits(float(value)) == bits
    }
    require(len(candidates) == 1, f"energy bits {bits} do not identify one exact source value")
    return next(iter(candidates))


def contract_class(
    mf: int,
    total: ExactTable,
    products: tuple[ExactProduct, ...],
    awr: ExactValue,
    awi: ExactValue,
) -> str | None:
    total_grid = set(total.x)
    threshold = False
    for product in products:
        if any(value not in total_grid for value in product.table.x):
            return "missing_total_or_grid_contract"
        if mf == 10 and product.qi.value < 0:
            awr_low = awr.value - awr.quantum / 2
            awr_high = awr.value + awr.quantum / 2
            awi_low = awi.value - awi.quantum / 2
            awi_high = awi.value + awi.quantum / 2
            q_low = max(Decimal(0), -product.qi.value - product.qi.quantum / 2)
            q_high = -product.qi.value + product.qi.quantum / 2
            threshold_low = q_low * (1 + awi_low / awr_high)
            threshold_high = q_high * (1 + awi_high / awr_low)
            if not (
                threshold_low <= product.table.x[0] <= threshold_high
                and product.table.y[0].value == 0
            ):
                threshold = True
    return "threshold_contract" if threshold else None


def selected_products(
    evaluation: ExactEvaluation, example: dict[str, Any]
) -> tuple[ExactTable, tuple[ExactProduct, ...]]:
    mf = int(example["mf"])
    mt = int(example["mt"])
    total = evaluation.mf3[mt]
    available = evaluation.mf9[mt] if mf == 9 else evaluation.mf10[mt]
    selected = tuple(
        product
        for product in available
        if product.zap == int(example["zap"])
        and (
            example["raw_lfs"] is None
            or product.lfs == int(example["raw_lfs"])
        )
    )
    require(bool(selected), f"no selected MF={mf}/MT={mt} products")
    if example["raw_lfs"] is not None:
        require(len(selected) == 1, "individual source boundary is not unique")
    return total, selected


def multiply_polynomials(left: list[Decimal], right: list[Decimal]) -> list[Decimal]:
    output = [Decimal(0)] * (len(left) + len(right) - 1)
    for left_index, left_value in enumerate(left):
        for right_index, right_value in enumerate(right):
            output[left_index + right_index] += left_value * right_value
    return output


def linear_terms(table: ExactTable, low: Decimal, high: Decimal, bound: str) -> list[Decimal]:
    middle = low + (high - low) / 2
    if middle < table.x[0] or middle > table.x[-1]:
        return [Decimal(0), Decimal(0)]
    segment = min(max(bisect.bisect_right(table.x, middle) - 1, 0), len(table.x) - 2)
    x1, x2 = table.x[segment], table.x[segment + 1]
    require(x2 > x1, "collapse interval selected repeated source energy")
    y1 = table.y[segment].ordinate(bound)
    y2 = table.y[segment + 1].ordinate(bound)
    slope = (y2 - y1) / (x2 - x1)
    return [y1 - slope * x1, slope]


def collapse_product(
    tables: tuple[ExactTable, ...], low: Decimal, high: Decimal, bound: str
) -> Decimal:
    breaks = {low, high}
    for table in tables:
        first = bisect.bisect_right(table.x, low)
        final = bisect.bisect_left(table.x, high)
        breaks.update(table.x[first:final])
    ordered = sorted(breaks)
    integral = Decimal(0)
    for left, right in zip(ordered, ordered[1:]):
        if right <= left:
            continue
        polynomial = [Decimal(1)]
        for table in tables:
            polynomial = multiply_polynomials(polynomial, linear_terms(table, left, right, bound))
        value = polynomial[0] * (right / left).ln()
        for power, coefficient in enumerate(polynomial[1:], 1):
            value += coefficient * (right**power - left**power) / power
        integral += value
    return integral / (high / low).ln()


def exact_values_for_example(
    evaluation: ExactEvaluation,
    group_bounds: tuple[Decimal, ...],
    example: dict[str, Any],
) -> tuple[Decimal, Decimal, Decimal, Decimal, str | None]:
    total, products = selected_products(evaluation, example)
    mf = int(example["mf"])
    contract = contract_class(mf, total, products, evaluation.awr, evaluation.awi)
    if example["scope"] in {"pointwise", "multiplicity"}:
        energy = query_energy(total, products, example["energy_bits"])
        side = str(example["side"])
        if example["scope"] == "multiplicity":
            partial = sum(
                (table_value(product.table, energy, side, "value") for product in products),
                Decimal(0),
            )
            partial_low = sum(
                (table_value(product.table, energy, side, "lower") for product in products),
                Decimal(0),
            )
            return partial, Decimal(1), partial_low, Decimal(1), contract
        total_value = table_value(total, energy, side, "value")
        total_low = table_value(total, energy, side, "lower")
        total_high = table_value(total, energy, side, "upper")
        if mf == 9:
            partial = sum(
                (
                    total_value * table_value(product.table, energy, side, "value")
                    for product in products
                ),
                Decimal(0),
            )
            partial_low = sum(
                (
                    total_low * table_value(product.table, energy, side, "lower")
                    for product in products
                ),
                Decimal(0),
            )
        else:
            partial = sum(
                (table_value(product.table, energy, side, "value") for product in products),
                Decimal(0),
            )
            partial_low = sum(
                (table_value(product.table, energy, side, "lower") for product in products),
                Decimal(0),
            )
        return partial, total_value, partial_low, total_high, contract
    require(example["scope"] == "collapsed", "unknown exact source scope")
    group = int(example["group"])
    low, high = group_bounds[group], group_bounds[group + 1]
    total_value = collapse_product((total,), low, high, "value")
    total_high = collapse_product((total,), low, high, "upper")
    if mf == 9:
        partial = sum(
            (
                collapse_product((total, product.table), low, high, "value")
                for product in products
            ),
            Decimal(0),
        )
        partial_low = sum(
            (
                collapse_product((total, product.table), low, high, "lower")
                for product in products
            ),
            Decimal(0),
        )
    else:
        partial = sum(
            (
                collapse_product((product.table,), low, high, "value")
                for product in products
            ),
            Decimal(0),
        )
        partial_low = sum(
            (
                collapse_product((product.table,), low, high, "lower")
                for product in products
            ),
            Decimal(0),
        )
    return partial, total_value, partial_low, total_high, contract


def classify_exact(
    values: tuple[Decimal, Decimal, Decimal, Decimal, str | None],
    p18_violation: bool,
) -> str:
    partial, total, partial_low, total_high, contract = values
    if contract is not None:
        return contract
    if partial > total and partial_low > total_high:
        return "definite_source_excess"
    if partial > total:
        return "printing_envelope_excess"
    if p18_violation:
        return "binary_only_excess"
    return "source_conformant"


PRIMARY_CODE = {
    "missing_total_or_grid_contract": 1,
    "threshold_contract": 2,
    "definite_source_excess": 3,
    "printing_envelope_excess": 4,
    "binary_only_excess": 5,
    "source_conformant": 6,
}


class FrozenFlags:
    def __init__(self, encoded: str, expected: int) -> None:
        require(len(encoded) == 2 * ((expected + 7) // 8), "P18 bitmap byte length")
        try:
            self.data = bytes.fromhex(encoded)
        except ValueError as error:
            raise AssertionError("P18 bitmap is not lowercase hexadecimal") from error
        require(encoded == self.data.hex(), "P18 bitmap canonical hexadecimal")
        if expected % 8 and self.data:
            require(
                self.data[-1] >> (expected % 8) == 0,
                "P18 bitmap has nonzero padding bits",
            )
        self.expected = expected
        self.index = 0

    def take(self) -> bool:
        require(self.index < self.expected, "P18 bitmap exhausted early")
        value = bool(self.data[self.index // 8] & (1 << (self.index % 8)))
        self.index += 1
        return value

    def finish(self) -> None:
        require(self.index == self.expected, "P18 bitmap has unconsumed decisions")


class ClassificationRecorder:
    def __init__(self, flags: FrozenFlags) -> None:
        self.flags = flags
        self.primary: Counter[str] = Counter()
        self.p18_primary: Counter[str] = Counter()
        self.p18_violations = 0
        self.exact_excesses = 0
        self.standard_compatible_excesses = 0
        self.codes = bytearray()

    def observe(
        self,
        *,
        excess_difference: Decimal | None,
        total: Decimal | None,
        envelope_difference: Decimal | None,
        contract: str | None,
    ) -> None:
        p18_violation = self.flags.take()
        if contract is not None:
            primary = contract
        else:
            require(
                all(
                    value is not None and value.is_finite() and value >= 0
                    for value in (total,)
                ),
                "exact source total is nonfinite or negative",
            )
            require(
                all(
                    value is not None and value.is_finite()
                    for value in (excess_difference, envelope_difference)
                ),
                "exact source difference is nonfinite",
            )
            assert excess_difference is not None
            assert total is not None
            assert envelope_difference is not None
            exact_excess = excess_difference > 0
            if exact_excess and envelope_difference > 0:
                primary = "definite_source_excess"
            elif exact_excess:
                primary = "printing_envelope_excess"
            elif p18_violation:
                primary = "binary_only_excess"
            else:
                primary = "source_conformant"
            self.exact_excesses += int(exact_excess)
            self.standard_compatible_excesses += int(
                exact_excess
                and (
                    excess_difference <= Decimal("0.001") * total
                    if total > 0
                    else excess_difference <= Decimal("0.001")
                )
            )
        self.primary[primary] += 1
        if p18_violation:
            self.p18_violations += 1
            self.p18_primary[primary] += 1
        self.codes.append(PRIMARY_CODE[primary])

    def finish(self) -> dict[str, Any]:
        self.flags.finish()
        return {
            "comparisons": len(self.codes),
            "primary_counts": dict(sorted(self.primary.items())),
            "p18_violations": self.p18_violations,
            "p18_violations_by_primary": dict(sorted(self.p18_primary.items())),
            "exact_excesses_outside_contracts": self.exact_excesses,
            "standard_compatible_excesses_outside_contracts": self.standard_compatible_excesses,
            "classification_sha256": hashlib.sha256(self.codes).hexdigest(),
            "_codes": bytes(self.codes),
        }


def exact_energy_union(
    total: ExactTable, products: tuple[ExactProduct, ...]
) -> tuple[Decimal, ...]:
    by_bits: dict[str, Decimal] = {}
    for value in (*total.x, *(point for product in products for point in product.table.x)):
        bits = float_bits(float(value))
        previous = by_bits.setdefault(bits, value)
        require(previous == value, f"distinct printed energies share binary64 bits {bits}")
    return tuple(sorted(by_bits.values()))


def merged_contract(
    mf: int,
    total: ExactTable,
    products: tuple[ExactProduct, ...],
    awr: ExactValue,
    awi: ExactValue,
) -> str | None:
    return contract_class(mf, total, products, awr, awi)


def collapse_groups(
    tables: tuple[ExactTable, ...],
    group_bounds: tuple[Decimal, ...],
    bound: str,
) -> tuple[Decimal, ...]:
    return tuple(
        collapse_product(tables, low, high, bound)
        for low, high in zip(group_bounds, group_bounds[1:])
    )


def normalized_linear_terms(
    table: ExactTable, low: Decimal, high: Decimal, bound: str
) -> list[Decimal]:
    middle = low + (high - low) / 2
    if middle < table.x[0] or middle > table.x[-1]:
        return [Decimal(0), Decimal(0)]
    segment = min(
        max(bisect.bisect_right(table.x, middle) - 1, 0), len(table.x) - 2
    )
    x1, x2 = table.x[segment], table.x[segment + 1]
    require(x2 > x1, "combination interval selected repeated source energy")
    y1 = table.y[segment].ordinate(bound)
    y2 = table.y[segment + 1].ordinate(bound)
    slope = (y2 - y1) / (x2 - x1)
    value_at_low = y1 + (low - x1) * slope
    scaled_slope = low * slope
    return [value_at_low - scaled_slope, scaled_slope]


def collapse_combination(
    terms: tuple[tuple[Decimal, tuple[ExactTable, ...], str], ...],
    low: Decimal,
    high: Decimal,
) -> Decimal:
    breaks = {low, high}
    for _, tables, _ in terms:
        for table in tables:
            first = bisect.bisect_right(table.x, low)
            final = bisect.bisect_left(table.x, high)
            breaks.update(table.x[first:final])
    integral = Decimal(0)
    ordered = sorted(breaks)
    for left, right in zip(ordered, ordered[1:]):
        if right <= left:
            continue
        combined: list[Decimal] = []
        for coefficient, tables, bound in terms:
            polynomial = [Decimal(1)]
            for table in tables:
                polynomial = multiply_polynomials(
                    polynomial, normalized_linear_terms(table, left, right, bound)
                )
            if len(combined) < len(polynomial):
                combined.extend([Decimal(0)] * (len(polynomial) - len(combined)))
            for power, value in enumerate(polynomial):
                combined[power] += coefficient * value
        ratio = right / left
        value = combined[0] * ratio.ln()
        for power, coefficient in enumerate(combined[1:], 1):
            value += coefficient * (ratio**power - 1) / power
        integral += value
    return integral / (high / low).ln()


def needs_stable_difference(left: Decimal, right: Decimal) -> bool:
    difference = abs(left - right)
    scale = max(Decimal(1), abs(left), abs(right))
    guard_digits = max(getcontext().prec - 20, 1)
    return difference <= scale * Decimal(1).scaleb(-guard_digits)


def audit_exact_source_section(
    *,
    mf: int,
    total: ExactTable,
    products: tuple[ExactProduct, ...],
    awr: ExactValue,
    awi: ExactValue,
    group_bounds: tuple[Decimal, ...],
    source: ClassificationRecorder,
) -> None:
    contracts = tuple(
        contract_class(mf, total, (product,), awr, awi) for product in products
    )
    energies = exact_energy_union(total, products)
    zaps = tuple(sorted({product.zap for product in products}))
    selected_by_zap = {
        zap: tuple(index for index, product in enumerate(products) if product.zap == zap)
        for zap in zaps
    }

    for energy in energies:
        for side in ("right", "left"):
            total_value = table_value(total, energy, side, "value")
            total_low = table_value(total, energy, side, "lower")
            total_high = table_value(total, energy, side, "upper")
            product_values: dict[int, Decimal] = {}
            product_lows: dict[int, Decimal] = {}
            for index, product in enumerate(products):
                contract = contracts[index]
                if contract is None:
                    value = table_value(product.table, energy, side, "value")
                    lower = table_value(product.table, energy, side, "lower")
                    product_values[index] = total_value * value if mf == 9 else value
                    product_lows[index] = total_low * lower if mf == 9 else lower
                source.observe(
                    excess_difference=(
                        product_values[index] - total_value
                        if contract is None
                        else None
                    ),
                    total=total_value if contract is None else None,
                    envelope_difference=(
                        product_lows[index] - total_high
                        if contract is None
                        else None
                    ),
                    contract=contract,
                )
            for zap in zaps:
                selected = selected_by_zap[zap]
                selected_products = tuple(products[index] for index in selected)
                contract = merged_contract(mf, total, selected_products, awr, awi)
                partial = (
                    sum((product_values[index] for index in selected), Decimal(0))
                    if contract is None
                    else None
                )
                partial_low = (
                    sum((product_lows[index] for index in selected), Decimal(0))
                    if contract is None
                    else None
                )
                source.observe(
                    excess_difference=(
                        partial - total_value if partial is not None else None
                    ),
                    total=total_value if contract is None else None,
                    envelope_difference=(
                        partial_low - total_high if partial_low is not None else None
                    ),
                    contract=contract,
                )

    total_values = collapse_groups((total,), group_bounds, "value")
    total_highs = collapse_groups((total,), group_bounds, "upper")
    product_values: dict[int, tuple[Decimal, ...]] = {}
    product_lows: dict[int, tuple[Decimal, ...]] = {}
    for index, product in enumerate(products):
        if contracts[index] is None:
            tables = (total, product.table) if mf == 9 else (product.table,)
            product_values[index] = collapse_groups(tables, group_bounds, "value")
            product_lows[index] = collapse_groups(tables, group_bounds, "lower")
        for group in range(len(group_bounds) - 1):
            contract = contracts[index]
            excess_difference = None
            envelope_difference = None
            if contract is None:
                partial = product_values[index][group]
                partial_low = product_lows[index][group]
                excess_difference = partial - total_values[group]
                envelope_difference = partial_low - total_highs[group]
                low, high = group_bounds[group], group_bounds[group + 1]
                if needs_stable_difference(partial, total_values[group]):
                    value_terms = (
                        (
                            Decimal(1),
                            (total, product.table) if mf == 9 else (product.table,),
                            "value",
                        ),
                        (Decimal(-1), (total,), "value"),
                    )
                    excess_difference = collapse_combination(value_terms, low, high)
                if needs_stable_difference(partial_low, total_highs[group]):
                    lower_terms = (
                        (
                            Decimal(1),
                            (total, product.table) if mf == 9 else (product.table,),
                            "lower",
                        ),
                        (Decimal(-1), (total,), "upper"),
                    )
                    envelope_difference = collapse_combination(lower_terms, low, high)
            source.observe(
                excess_difference=excess_difference,
                total=total_values[group] if contract is None else None,
                envelope_difference=envelope_difference,
                contract=contract,
            )
    for zap in zaps:
        selected = selected_by_zap[zap]
        selected_products = tuple(products[index] for index in selected)
        contract = merged_contract(mf, total, selected_products, awr, awi)
        for group in range(len(group_bounds) - 1):
            partial = (
                sum((product_values[index][group] for index in selected), Decimal(0))
                if contract is None
                else None
            )
            partial_low = (
                sum((product_lows[index][group] for index in selected), Decimal(0))
                if contract is None
                else None
            )
            excess_difference = (
                partial - total_values[group] if partial is not None else None
            )
            envelope_difference = (
                partial_low - total_highs[group] if partial_low is not None else None
            )
            if contract is None:
                assert partial is not None
                assert partial_low is not None
                low, high = group_bounds[group], group_bounds[group + 1]
                if needs_stable_difference(partial, total_values[group]):
                    value_terms = tuple(
                        (
                            Decimal(1),
                            (total, products[index].table)
                            if mf == 9
                            else (products[index].table,),
                            "value",
                        )
                        for index in selected
                    ) + ((Decimal(-1), (total,), "value"),)
                    excess_difference = collapse_combination(value_terms, low, high)
                if needs_stable_difference(partial_low, total_highs[group]):
                    lower_terms = tuple(
                        (
                            Decimal(1),
                            (total, products[index].table)
                            if mf == 9
                            else (products[index].table,),
                            "lower",
                        )
                        for index in selected
                    ) + ((Decimal(-1), (total,), "upper"),)
                    envelope_difference = collapse_combination(lower_terms, low, high)
            source.observe(
                excess_difference=excess_difference,
                total=total_values[group] if contract is None else None,
                envelope_difference=envelope_difference,
                contract=contract,
            )


def audit_exact_fraction_section(
    *,
    total: ExactTable,
    products: tuple[ExactProduct, ...],
    awr: ExactValue,
    awi: ExactValue,
    fractions: ClassificationRecorder,
) -> None:
    contracts = tuple(
        contract_class(9, total, (product,), awr, awi) for product in products
    )
    energies = exact_energy_union(total, products)
    zaps = tuple(sorted({product.zap for product in products}))
    selected_by_zap = {
        zap: tuple(index for index, product in enumerate(products) if product.zap == zap)
        for zap in zaps
    }
    for energy in energies:
        for side in ("right", "left"):
            values: dict[int, Decimal] = {}
            lows: dict[int, Decimal] = {}
            for index, product in enumerate(products):
                contract = contracts[index]
                if contract is None:
                    values[index] = table_value(product.table, energy, side, "value")
                    lows[index] = table_value(product.table, energy, side, "lower")
                fractions.observe(
                    excess_difference=(
                        values[index] - 1 if contract is None else None
                    ),
                    total=Decimal(1) if contract is None else None,
                    envelope_difference=(
                        lows[index] - 1 if contract is None else None
                    ),
                    contract=contract,
                )
            for zap in zaps:
                selected = selected_by_zap[zap]
                selected_products = tuple(products[index] for index in selected)
                contract = merged_contract(9, total, selected_products, awr, awi)
                partial = (
                    sum((values[index] for index in selected), Decimal(0))
                    if contract is None
                    else None
                )
                partial_low = (
                    sum((lows[index] for index in selected), Decimal(0))
                    if contract is None
                    else None
                )
                fractions.observe(
                    excess_difference=partial - 1 if partial is not None else None,
                    total=Decimal(1) if contract is None else None,
                    envelope_difference=(
                        partial_low - 1 if partial_low is not None else None
                    ),
                    contract=contract,
                )


def audit_exact_evaluation(
    evaluation: ExactEvaluation,
    group_bounds: tuple[Decimal, ...],
    *,
    source_bitmap_hex: str,
    source_comparisons: int,
    fraction_bitmap_hex: str,
    fraction_comparisons: int,
    precision: int,
) -> dict[str, Any]:
    source = ClassificationRecorder(FrozenFlags(source_bitmap_hex, source_comparisons))
    fractions = ClassificationRecorder(
        FrozenFlags(fraction_bitmap_hex, fraction_comparisons)
    )
    with localcontext() as context:
        context.prec = precision
        for mf, available in ((9, evaluation.mf9), (10, evaluation.mf10)):
            for mt in sorted(available):
                products = tuple(
                    sorted(
                        (product for product in available[mt] if product.zap >= 0),
                        key=lambda product: (product.zap, product.lfs),
                    )
                )
                if not products:
                    continue
                total = evaluation.mf3.get(mt)
                if total is None:
                    continue
                audit_exact_source_section(
                    mf=mf,
                    total=total,
                    products=products,
                    awr=evaluation.awr,
                    awi=evaluation.awi,
                    group_bounds=group_bounds,
                    source=source,
                )
                if mf == 9:
                    audit_exact_fraction_section(
                        total=total,
                        products=products,
                        awr=evaluation.awr,
                        awi=evaluation.awi,
                        fractions=fractions,
                    )
    return {
        "precision": precision,
        "source": source.finish(),
        "mf9_fractions": fractions.finish(),
    }


def compare_precision_audits(low: dict[str, Any], high: dict[str, Any]) -> dict[str, Any]:
    require(low["precision"] == 80 and high["precision"] == 120, "oracle precisions")
    output: dict[str, Any] = {"precision_digits": [80, 120]}
    for name in ("source", "mf9_fractions"):
        low_values = low[name]
        high_values = high[name]
        require(low_values["_codes"] == high_values["_codes"], f"{name} 80/120 class disagreement")
        for field in (
            "comparisons",
            "primary_counts",
            "p18_violations",
            "p18_violations_by_primary",
            "exact_excesses_outside_contracts",
            "standard_compatible_excesses_outside_contracts",
            "classification_sha256",
        ):
            require(low_values[field] == high_values[field], f"{name} 80/120 {field}")
        output[name] = {
            field: value
            for field, value in high_values.items()
            if field != "_codes"
        }
    output["pass"] = True
    return output


def group_boundaries(path: Path) -> tuple[Decimal, ...]:
    payload = json.loads(path.read_text(), parse_float=Decimal, parse_int=Decimal)
    values = [Decimal.from_float(float(value)) for value in payload["boundaries_eV"]]
    if values[0] > values[-1]:
        values.reverse()
    require(all(left < right for left, right in zip(values, values[1:])), "group ordering")
    return tuple(values)


def scaled_difference(left: Decimal, right: Decimal) -> Decimal:
    return abs(left - right) / max(Decimal(1), abs(right))


def verify_examples(
    *,
    data_directory: Path,
    group_path: Path,
    source_examples: list[dict[str, Any]],
    fraction_examples: list[dict[str, Any]],
    contract_examples: list[dict[str, Any]],
) -> dict[str, Any]:
    bounds = group_boundaries(group_path)
    cache: dict[tuple[str, int], ExactEvaluation] = {}

    def evaluation_for(key: tuple[str, int]) -> ExactEvaluation:
        if key not in cache:
            cache[key] = parse_evaluation(data_directory / key[0], key[1])
        return cache[key]

    accounting: list[dict[str, Any]] = []
    classes: Counter[str] = Counter()
    maximum_scaled = Decimal(0)
    for kind, examples in (("source", source_examples), ("mf9_fraction", fraction_examples)):
        for example in examples:
            key = (example["file"], int(example["mat"]))
            evaluation = evaluation_for(key)
            results = []
            for precision in (80, 120):
                with localcontext() as context:
                    context.prec = precision
                    values = tuple(+value if isinstance(value, Decimal) else value for value in exact_values_for_example(
                        evaluation, bounds, example
                    ))
                    result_class = classify_exact(values, bool(example["p18_violation"]))
                    results.append((values, result_class))
            identity = (
                f"{kind} {example['file']} MAT={example['mat']} MF={example['mf']} "
                f"MT={example['mt']} scope={example['scope']} side={example['side']} "
                f"group={example['group']} ZAP={example['zap']} LFS={example['raw_lfs']}"
            )
            require(
                results[0][1] == results[1][1],
                f"80/120-digit class disagreement: {identity}",
            )
            require(
                results[1][1] == example["primary_class"],
                f"Rust/exact-decimal class disagreement: {identity}; "
                f"Rust={example['primary_class']} exact={results[1][1]}",
            )
            for low, high in zip(results[0][0][:4], results[1][0][:4]):
                maximum_scaled = max(maximum_scaled, scaled_difference(low, high))
            require(maximum_scaled <= Decimal("1e-75"), "80/120-digit value disagreement")
            classes[results[1][1]] += 1
            accounting.append(
                {
                    "kind": kind,
                    "file": example["file"],
                    "source_sha256": example["source_sha256"],
                    "mat": example["mat"],
                    "mf": example["mf"],
                    "mt": example["mt"],
                    "scope": example["scope"],
                    "side": example["side"],
                    "energy_bits": example["energy_bits"],
                    "group": example["group"],
                    "zap": example["zap"],
                    "raw_lfs": example["raw_lfs"],
                    "summed": example["summed"],
                    "class": results[1][1],
                    "values_sha256": hashlib.sha256(
                        canonical([str(value) for value in results[1][0][:4]])
                    ).hexdigest(),
                }
            )
    for example in contract_examples:
        key = (example["file"], int(example["mat"]))
        evaluation = evaluation_for(key)
        mf, mt = int(example["mf"]), int(example["mt"])
        total = evaluation.mf3[mt]
        available = evaluation.mf9[mt] if mf == 9 else evaluation.mf10[mt]
        selected = tuple(
            product
            for product in available
            if product.zap == int(example["zap"])
            and product.lfs == int(example["raw_lfs"])
        )
        require(len(selected) == 1, "contract example product identity")
        decision = contract_class(
            mf, total, selected, evaluation.awr, evaluation.awi
        )
        require(decision == example["decision"], "exact-decimal contract disagreement")
        accounting.append(
            {
                "kind": "contract",
                "file": example["file"],
                "source_sha256": example["source_sha256"],
                "mat": example["mat"],
                "mf": mf,
                "mt": mt,
                "zap": example["zap"],
                "raw_lfs": example["raw_lfs"],
                "class": decision,
            }
        )
    accounting.sort(key=lambda row: canonical(row))
    return {
        "source_examples": len(source_examples),
        "mf9_fraction_examples": len(fraction_examples),
        "contract_examples": len(contract_examples),
        "precision_digits": [80, 120],
        "maximum_scaled_80_120_difference": str(maximum_scaled),
        "primary_counts": dict(sorted(classes.items())),
        "accounting_sha256": hashlib.sha256(b"\n".join(map(canonical, accounting))).hexdigest(),
        "imports_production": False,
        "class_agreement": True,
        "precision_agreement": True,
        "pass": True,
    }


def corpus_audit(
    *,
    data_directory: Path,
    group_path: Path,
    probe_checkpoint: Path,
    output_path: Path,
) -> None:
    """Resumable exhaustive 80/120-digit audit over a v2 probe checkpoint.

    The probe's frozen P18 bitmaps are consumed bit-by-bit in the probe's own
    comparison order, so every frozen decision is reproduced exactly — any
    disagreement raises inside ``FrozenFlags.take``. The emitted row per file
    carries the exact classes that own the report's classification counts.
    """
    bounds = group_boundaries(group_path)
    done: set[str] = set()
    has_header = False
    mode = "w"
    if output_path.is_file() and output_path.stat().st_size:
        # A killed run can leave a truncated trailing row; keep only the rows
        # that parsed and truncate back to them before appending.
        raw = output_path.read_bytes()
        rows = raw.split(b"\n")
        if rows and not rows[-1]:
            rows.pop()
        valid_end = 0
        for index, line in enumerate(rows):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                break
            if row.get("kind") == "header":
                has_header = True
            if row.get("kind") == "file":
                require(row.get("pass"), f"checkpoint records failing audit for {row.get('file')}")
                done.add(row["file"])
            valid_end = index + 1
        if valid_end < len(rows):
            with output_path.open("wb") as stream:
                for line in rows[:valid_end]:
                    stream.write(line + b"\n")
        mode = "a"
    with output_path.open(mode, encoding="utf-8") as output:
        if not has_header:
            output.write(
                json.dumps(
                    {
                        "kind": "header",
                        "schema": "actinv-p18b-decimal-corpus-1",
                        "probe_checkpoint_sha256": _sha256_file(probe_checkpoint),
                        "group_boundaries_sha256": _sha256_file(group_path),
                        "precision_digits": [80, 120],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            output.flush()
        with probe_checkpoint.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("kind") != "file":
                    continue
                name = row["file"]
                if name in done:
                    continue
                path = data_directory / name
                targets: list[dict[str, Any]] = []
                for target in row["targets"]:
                    evaluation = parse_evaluation(path, int(target["mat"]))
                    source = target["source"]
                    fractions = source["mf9_fractions"]
                    audits = [
                        audit_exact_evaluation(
                            evaluation,
                            bounds,
                            source_bitmap_hex=source["p18_violation_bitmap_hex"],
                            source_comparisons=source["comparisons"],
                            fraction_bitmap_hex=fractions["p18_violation_bitmap_hex"],
                            fraction_comparisons=fractions["comparisons"],
                            precision=precision,
                        )
                        for precision in (80, 120)
                    ]
                    merged = compare_precision_audits(audits[0], audits[1])
                    targets.append(
                        {
                            "mat": target["mat"],
                            "source": merged["source"],
                            "mf9_fractions": merged["mf9_fractions"],
                            "precision_digits": merged["precision_digits"],
                            "pass": merged["pass"],
                        }
                    )
                output.write(
                    json.dumps(
                        {
                            "kind": "file",
                            "file": name,
                            "source_sha256": row["source_sha256"],
                            "targets": targets,
                            "pass": all(target["pass"] for target in targets),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                output.flush()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("data_directory", type=Path)
    parser.add_argument("group_boundaries", type=Path)
    parser.add_argument("probe_checkpoint", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    corpus_audit(
        data_directory=arguments.data_directory,
        group_path=arguments.group_boundaries,
        probe_checkpoint=arguments.probe_checkpoint,
        output_path=arguments.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
