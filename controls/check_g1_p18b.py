#!/usr/bin/env python3
"""Independent checker for committed P18b-G1 decimal and IAEA evidence."""

from __future__ import annotations

import argparse
import bisect
import copy
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import struct
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "results/g1_p18b_decimal_oracle.json"
OUTPUT = ROOT / "results/g1_p18b_check.json"
CONTROL = ROOT / "controls/g1_p18b_decimal_oracle.py"
FIXTURES = ROOT / "controls/fixtures/p18b_g1_oracle.json"
PROBE_SOURCE = ROOT / "crates/actinv-data/src/bin/p18b_oracle_probe.rs"
PROBE = Path(os.environ.get("ACTINV_P18B_PROBE", ROOT / "target/debug/p18b_oracle_probe"))
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
G0 = ROOT / "results/g0_p18b_seal.json"
G0_CHECK = ROOT / "results/g0_p18b_check.json"

EXPECTED_EVIDENCE_SHA256 = "e258ae73302fce5ef63419e8bc24507e42707d14ca5bb1d4b60a0515197d3adc"
EXPECTED_CONTROL_SHA256 = "76c82ea2760666c9ab6187cf650f42e11df92586e23efd7da7d09f8ee516640a"
EXPECTED_FIXTURE_SHA256 = "afea329ec685ee7738fb31b40218e7b1d526a71292a8c0c204cb27be6c82d25b"
EXPECTED_PROBE_SHA256 = "558b6ed1e6c1689196b0fe8b1467bd122a44113869e0754614f286b0d38bb66a"
EXPECTED_PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
EXPECTED_G0_SHA256 = "99648da5dc4d4209e2607ea16ca9d4e34127c64ce7430f9194c48370562271ad"
EXPECTED_G0_CHECK_SHA256 = "5f15c85a3460648514ee24e39954c8a0f9e10c274fac04074a3b5e552129551d"
EXPECTED_ORACLE_120_SHA256 = "6a7cbf8189bd5bb650ecae9e983b730f0e6acc61af247946f0088075660bfccb"
EXPECTED_IMPLEMENTATION = {
    "endf.rs": "ebd727f6a4fa2bde6bc32da34e72c4af4f1f12f50dea381a112b16db6c26b6c7",
    "groups.rs": "cce5586c142b252ba3d845295f0727a2920992f94d305f20da036334ccb8179e",
    "p18b_oracle_probe.rs": EXPECTED_PROBE_SHA256,
}
EXPECTED_G0_WORKFLOW = {
    "commit": "8745787c4e7ab8dad4fdeaed5f8cc0309735bb55",
    "workflow_run": 33_259_962_367,
    "job": 99_120_092_690,
    "conclusion": "success",
}
EXPECTED_OFFICIAL_BUILD = {
    "compiler": "GNU Fortran (Ubuntu 15.2.0-16ubuntu1) 15.2.0",
    "flags": ["-B<SYSTEM_GCC_15_PREFIX>", "-std=legacy", "-O2"],
    "sources_commit": "c2a6718bd831b5c8a6e975beb1946954b1d73c40",
    "source_sha256": {
        "checkr/checkr.f": "739169c525663a3a80d62f8047243b6d3a0d2b36e05cf95a7336ae58363d684e",
        "fizcon/fizcon.f": "15eac8dbcc1f1c0b8825d9e2a487d7e26f4717ccacad373f226a01c721e7527e",
    },
    "binary_sha256": {
        "checkr": "c9ced044a3b40c2137defaf124a6913f95507a0c2c0b1c231f91946d90dad905",
        "fizcon": "7d92a2ced90af043e406e91735d6eaa925e5cce8397700a6056d92de0ac24c68",
    },
}
CHECKR_INPUT_SHA256 = "e1b8c9b1acd667da307e9067108fa25ade16600958d2a67c2b56561c781fb38d"
FIZCON_INPUT_SHA256 = "ed4e41d35e84cee2198accb30700f303b7d89067155164d92f8f5d1418a34d20"
EXPECTED_OFFICIAL_CASES = {
    "mf9_inside_standard": {
        "mf": 9,
        "message": False,
        "tape": "7ef58a896b4f8d0c70cf4628568e86e341920f65a999357d7ccaa6447bb8bd92",
        "checkr": "61af58e2c63c7f0df3798602b6a394dc4d4cfa504e55f560b0c527b7eb5219b0",
        "fizcon": "4cf181d9db0116d55afd4845d51c4498f602963e4b7867c60e08cfbf904e701e",
    },
    "mf9_outside_standard": {
        "mf": 9,
        "message": True,
        "tape": "147665afd77606a2043e27e996f53ac8ea277fab3b844116fa776940396664de",
        "checkr": "61af58e2c63c7f0df3798602b6a394dc4d4cfa504e55f560b0c527b7eb5219b0",
        "fizcon": "156488664cebd070eddefe16e4d887099114e16cfcf80bdc7151eee8b694edb4",
    },
    "mf10_sum_boundary": {
        "mf": 10,
        "message": False,
        "tape": "de630009809a9fc4252c0430a289d0e6986b3ab88d93e7d71031cbe63ec7013d",
        "checkr": "b2c44636e0a5b9ed17c339fbf59a4cca6f73a67c8e8b67bc0a7b6a4a96c3d867",
        "fizcon": "943924bee612c00c8b7e46c22d11efe33b0e43dd8647e92da23f1651842a2fbd",
    },
    "mf10_sum_beyond": {
        "mf": 10,
        "message": True,
        "tape": "91bcc56eef759c27e98ffbac3101713aff173ea4c5f4490ff6bd31cf4d045705",
        "checkr": "b2c44636e0a5b9ed17c339fbf59a4cca6f73a67c8e8b67bc0a7b6a4a96c3d867",
        "fizcon": "02df4d7eb291a717f2a2fceaf09277fac986d5ef6efe3a8d0b6ac1dfaa1f9ce6",
    },
    "mf10_zero_boundary": {
        "mf": 10,
        "message": False,
        "tape": "fd603800643e3592ff7781c7b2a3acc9689cb95bfc5c06faa832ed5d7247c2ae",
        "checkr": "b2c44636e0a5b9ed17c339fbf59a4cca6f73a67c8e8b67bc0a7b6a4a96c3d867",
        "fizcon": "943924bee612c00c8b7e46c22d11efe33b0e43dd8647e92da23f1651842a2fbd",
    },
    "mf10_zero_beyond": {
        "mf": 10,
        "message": True,
        "tape": "785fe02570e286b5c53e216275bce396f9090fec5301723770cfd93327171dd5",
        "checkr": "b2c44636e0a5b9ed17c339fbf59a4cca6f73a67c8e8b67bc0a7b6a4a96c3d867",
        "fizcon": "629b7276bd04635319bc321590d8348994226a1ec9c6205fc03b8a9da0049005",
    },
}
ADDRESS_SPACE_BYTES = 12_000_000 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def float_bits(value: float) -> str:
    return f"{struct.unpack('>Q', struct.pack('>d', value))[0]:016x}"


def parse_number(field: str) -> tuple[Fraction, Fraction, int | None]:
    require(len(field) == 11 and field.isascii(), "fixed-width real")
    text = field.strip()
    if not text:
        return Fraction(0), Fraction(0), None
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
        mantissa, exponent = text, 0
    else:
        mantissa = text[:split]
        exponent_text = text[split + 1 :] if text[split] in "eE" else text[split:]
        exponent = int(exponent_text)
    decimal_value = Decimal(mantissa).scaleb(exponent)
    require(decimal_value.is_finite() and math.isfinite(float(decimal_value)), "finite real")
    decimals = len(mantissa.partition(".")[2]) if "." in mantissa else 0
    quantum_power = exponent - decimals
    value = Fraction(decimal_value)
    quantum = (
        Fraction(10**quantum_power)
        if quantum_power >= 0
        else Fraction(1, 10 ** (-quantum_power))
    )
    return value, quantum, quantum_power


def independent_fields(fixtures: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for case in fixtures["field_cases"]:
        try:
            value, _, power = parse_number(case["field"])
            parsed = True
            decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
            value_text = str(decimal_value)
            binary = -0.0 if value == 0 and case["field"].strip().startswith("-") else float(value)
            bits = float_bits(binary)
        except (AssertionError, ArithmeticError, ValueError):
            parsed = False
            value_text = None
            bits = None
            power = None
        rows.append(
            {
                "id": case["id"],
                "declared_valid": case["valid"],
                "parsed": parsed,
                "value": value_text,
                "value_bits": bits,
                "quantum_power_10": power,
            }
        )
    return rows


def decimal_value(field: str) -> Decimal:
    value, _, _ = parse_number(field)
    return Decimal(value.numerator) / Decimal(value.denominator)


def table_query(table: dict[str, Any], query: dict[str, str]) -> Decimal:
    x = [decimal_value(field) for field in table["x"]]
    y = [decimal_value(field) for field in table["y"]]
    point = decimal_value(query["x"])
    if query["side"] == "right":
        if point < x[0] or point > x[-1]:
            return Decimal(0)
        if point == x[-1]:
            return y[-1]
        segment = min(max(bisect.bisect_right(x, point) - 1, 0), len(x) - 2)
    else:
        require(query["side"] == "left", "query side")
        if point <= x[0] or point > x[-1]:
            return Decimal(0)
        segment = bisect.bisect_left(x, point) - 1
    x1, x2 = x[segment], x[segment + 1]
    y1, y2 = y[segment], y[segment + 1]
    if x1 == x2:
        return y2
    endpoint = segment + 2
    law = next(law for nbt, law in table["interpolation"] if endpoint <= nbt)
    fraction = (point - x1) / (x2 - x1)
    if law == 1:
        return y1
    if law == 2:
        return y1 + fraction * (y2 - y1)
    if law == 3:
        return y1 + (point / x1).ln() / (x2 / x1).ln() * (y2 - y1)
    if law == 4:
        return y1 * ((y2 / y1).ln() * fraction).exp()
    require(law == 5, "interpolation law")
    return y1 * ((y2 / y1).ln() * (point / x1).ln() / (x2 / x1).ln()).exp()


def independent_tables(fixtures: dict[str, Any]) -> dict[tuple[str, str, str], Decimal]:
    with localcontext() as context:
        context.prec = 100
        return {
            (table["id"], query["id"], query["side"]): +table_query(table, query)
            for table in fixtures["tables"]
            for query in table["queries"]
        }


def source_class(
    total: tuple[Fraction, Fraction, int | None],
    partials: list[tuple[Fraction, Fraction, int | None]],
) -> str:
    total_value, total_quantum, _ = total
    if total_value < 0 or any(value < 0 for value, _, _ in partials):
        return "malformed_or_nonfinite"
    summed = sum((value for value, _, _ in partials), Fraction(0))
    if summed <= total_value:
        return "source_conformant"
    partial_low = sum(
        (max(Fraction(0), value - quantum / 2) for value, quantum, _ in partials),
        Fraction(0),
    )
    if partial_low > total_value + total_quantum / 2:
        return "definite_source_excess"
    return "printing_envelope_excess"


def independent_comparisons(fixtures: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for case in fixtures["comparisons"]:
        if case["total"] is None:
            rows.append(
                {
                    "id": case["id"],
                    "mf": case["mf"],
                    "individual_classes": [],
                    "sum_class": "missing_total_or_grid_contract",
                    "sum": None,
                    "total": None,
                    "p18_sum_violation": None,
                    "standard_compatible": None,
                }
            )
            continue
        total = parse_number(case["total"])
        partials = [parse_number(field) for field in case["partials"]]
        total_value = total[0]
        summed = sum((partial[0] for partial in partials), Fraction(0))
        p18_tolerance = max(Fraction(1, 10**12), Fraction(5, 10**10) * max(total_value, 0))
        compatible = (
            True
            if summed <= total_value
            else (
                (summed - total_value) / total_value <= Fraction(1, 1000)
                if total_value > 0
                else summed <= Fraction(1, 1000)
            )
        )
        rows.append(
            {
                "id": case["id"],
                "mf": case["mf"],
                "individual_classes": [source_class(total, [partial]) for partial in partials],
                "sum_class": source_class(total, partials),
                "sum": summed,
                "total": total_value,
                "p18_sum_violation": summed > total_value + p18_tolerance,
                "standard_compatible": compatible,
            }
        )
    return rows


def tolerance(left: Fraction, right: Fraction) -> Fraction:
    return max(Fraction(1), Fraction(5, 10**6) * max(abs(left), abs(right)))


def independent_thresholds(fixtures: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for case in fixtures["threshold_cases"]:
        q = parse_number(case["q"])[0]
        threshold = parse_number(case["threshold"])[0]
        energy = parse_number(case["first_energy"])[0]
        value = parse_number(case["first_value"])[0]
        rows.append(
            {
                "id": case["id"],
                "decision": (
                    "source_conformant"
                    if q >= 0 or (energy == threshold and value == 0)
                    else "threshold_contract"
                ),
            }
        )
    return rows


def independent_excitations(fixtures: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for case in fixtures["excitation_cases"]:
        elfs = parse_number(case["elfs"])[0]
        qm = parse_number(case["qm"])[0]
        qi = parse_number(case["qi"])[0]
        catalog = parse_number(case["catalog"])[0]
        derived = qm - qi
        bound = tolerance(elfs, derived)
        if derived < -tolerance(Fraction(0), derived):
            decision = "negative_q_excitation_conflict"
        elif abs(elfs - derived) > bound:
            decision = "mf8_q_excitation_conflict"
        elif abs(elfs - catalog) <= tolerance(elfs, catalog):
            decision = "catalog_excitation_match"
        else:
            decision = "no_catalog_excitation_match_to_leakage"
        rows.append(
            {
                "id": case["id"],
                "derived": derived,
                "tolerance": bound,
                "decision": decision,
            }
        )
    return rows


def record(values: list[str | int], mat: int, mf: int, mt: int, sequence: int) -> str:
    require(len(values) == 6, "record fields")
    data = "".join(f"{str(value):>11}" for value in values)
    output = data + f"{mat:>4}{mf:>2}{mt:>3}{sequence:>5}"
    require(len(output) == 80, "record width")
    return output


def text_record(text: str, mat: int, sequence: int) -> str:
    output = f"{text:<66}{mat:>4}{1:>2}{451:>3}{sequence:>5}"
    require(len(output) == 80, "text record width")
    return output


def send(mat: int, mf: int) -> str:
    return record(["", "", "", "", "", ""], mat, mf, 0, 99_999)


def tab1(
    mat: int,
    mf: int,
    mt: int,
    sequence: int,
    head: list[str | int],
    value: str,
) -> list[str]:
    return [
        record([*head, 1, 2], mat, mf, mt, sequence),
        record([2, 2, "", "", "", ""], mat, mf, mt, sequence + 1),
        record([" 1.000000-5", value, " 2.000000+7", value, "", ""], mat, mf, mt, sequence + 2),
    ]


def independent_tape(case: dict[str, Any]) -> bytes:
    mat, mt, product, state_mf = 2631, 102, 26057, case["mf"]
    total = case["total"]
    require(total is not None and len(case["partials"]) == 2, "official case")
    mf3 = [
        record([" 2.605600+4", " 5.545000+1", 0, 0, 0, 0], mat, 3, mt, 1),
        *tab1(mat, 3, mt, 2, [" 0.000000+0", " 0.000000+0", 0, 0], total),
        send(mat, 3),
    ]
    mf8 = [
        record([" 2.605600+4", " 5.545000+1", 0, 0, 2, 1], mat, 8, mt, 1),
        record([" 2.605700+4", " 0.000000+0", state_mf, 0, 0, 0], mat, 8, mt, 2),
        record([" 2.605700+4", " 1.000000+5", state_mf, 1, 0, 0], mat, 8, mt, 3),
        send(mat, 8),
    ]
    state = [
        record([" 2.605600+4", " 5.545000+1", 0, 0, 2, 0], mat, state_mf, mt, 1),
        *tab1(mat, state_mf, mt, 2, [" 0.000000+0", " 0.000000+0", product, 0], case["partials"][0]),
        *tab1(mat, state_mf, mt, 5, [" 0.000000+0", "-1.000000+5", product, 1], case["partials"][1]),
        send(mat, state_mf),
    ]
    mf1 = [
        record([" 2.605600+4", " 5.545000+1", 0, 0, 0, 0], mat, 1, 451, 1),
        record([" 0.000000+0", " 0.000000+0", 0, 0, 0, 6], mat, 1, 451, 2),
        record([" 1.000000+0", " 2.000000+7", 0, 0, 10, 2025], mat, 1, 451, 3),
        record([" 0.000000+0", " 0.000000+0", 0, 0, 5, 4], mat, 1, 451, 4),
        text_record("ACTINV P18b generated IAEA compatibility fixture", mat, 5),
        text_record("Data-independent fixed-width MF9/MF10 sum test", mat, 6),
        text_record("No evaluated nuclear-data values are present", mat, 7),
        text_record("Generated under ACTINV-P18b_PROTOCOL", mat, 8),
        text_record("ENDF-6 FORMAT", mat, 9),
        record(["", "", 1, 451, 14, 0], mat, 1, 451, 10),
        record(["", "", 3, mt, len(mf3), 0], mat, 1, 451, 11),
        record(["", "", 8, mt, len(mf8), 0], mat, 1, 451, 12),
        record(["", "", state_mf, mt, len(state), 0], mat, 1, 451, 13),
        send(mat, 1),
    ]
    lines = [
        record(["", "", "", "", "", ""], 1, 0, 0, 0),
        *mf1,
        *mf3,
        *mf8,
        *state,
        record(["", "", "", "", "", ""], mat, 0, 0, 0),
        record(["", "", "", "", "", ""], 0, 0, 0, 0),
        record(["", "", "", "", "", ""], -1, 0, 0, 0),
    ]
    return ("\n".join(lines) + "\n").encode()


def run_probe() -> dict[str, Any]:
    require(PROBE.is_file(), f"missing Rust G1 probe {PROBE}")
    completed = subprocess.run(
        [str(PROBE), str(FIXTURES)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
        preexec_fn=lambda: resource.setrlimit(
            resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES)
        ),
    )
    require(completed.returncode == 0, f"Rust G1 probe failed: {completed.stderr[-2000:]}")
    return json.loads(completed.stdout)


def rows_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows}


def strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def validate_evidence(
    evidence: dict[str, Any], fixtures: dict[str, Any], current_rust: dict[str, Any]
) -> None:
    require(evidence["schema"] == "actinv-p18b-g1-decimal-checker-oracle-1", "schema")
    require(evidence["gate"] == "P18b-G1" and evidence["pass"] is True, "verdict")
    require(evidence["protocol_sha256"] == EXPECTED_PROTOCOL_SHA256, "protocol")
    require(evidence["g0_evidence_sha256"] == EXPECTED_G0_SHA256, "G0 evidence")
    require(evidence["g0_check_sha256"] == EXPECTED_G0_CHECK_SHA256, "G0 check")
    require(evidence["g0_workflow"] == EXPECTED_G0_WORKFLOW, "G0 workflow")
    require(evidence["control_source_sha256"] == EXPECTED_CONTROL_SHA256, "control")
    require(evidence["implementation_source_sha256"] == EXPECTED_IMPLEMENTATION, "implementation")
    require(
        evidence["fixture"]
        == {
            "path": "controls/fixtures/p18b_g1_oracle.json",
            "sha256": EXPECTED_FIXTURE_SHA256,
            "field_cases": 18,
            "tables": 7,
            "queries": 12,
            "comparisons": 12,
            "threshold_cases": 4,
            "excitation_cases": 4,
        },
        "fixture inventory",
    )
    require(evidence["decimal_oracle_120_sha256"] == EXPECTED_ORACLE_120_SHA256, "120-digit oracle")
    require(Decimal(evidence["maximum_80_120_relative_delta"]) <= Decimal("1e-75"), "precision delta")
    require(evidence["maximum_rust_interpolation_ulps"] <= 8, "Rust interpolation ULPs")
    require(all(evidence["checks"].values()), "G1 checks")
    require(all(evidence["rust_agreement"].values()), "Rust agreement")
    require(all(evidence["mutation_plants"].values()), "producer mutation plants")
    require(evidence["rust_probe"] == current_rust, "fresh Rust probe")
    require(
        not any(value.startswith("/") or "/tmp/" in value for value in strings(evidence)),
        "absolute path leaked into evidence",
    )

    oracle = evidence["decimal_oracle_80"]
    expected_fields = rows_by_id(independent_fields(fixtures))
    observed_fields = rows_by_id(oracle["fields"])
    require(set(expected_fields) == set(observed_fields), "field inventory")
    for name, expected in expected_fields.items():
        observed = observed_fields[name]
        for field in ("declared_valid", "parsed", "value_bits", "quantum_power_10"):
            require(observed[field] == expected[field], f"field {name} {field}")
        if expected["parsed"]:
            require(Fraction(Decimal(observed["value"])) == Fraction(Decimal(expected["value"])), f"field {name} value")

    expected_tables = independent_tables(fixtures)
    observed_tables = {
        (table["id"], query["id"], query["side"]): Decimal(query["value"])
        for table in oracle["tables"]
        for query in table["queries"]
    }
    require(set(expected_tables) == set(observed_tables), "table query inventory")
    for key, expected in expected_tables.items():
        delta = abs(observed_tables[key] - expected) / max(Decimal(1), abs(expected))
        require(delta <= Decimal("1e-75"), f"table query {key}")

    expected_comparisons = rows_by_id(independent_comparisons(fixtures))
    observed_comparisons = rows_by_id(oracle["comparisons"])
    require(set(expected_comparisons) == set(observed_comparisons), "comparison inventory")
    for name, expected in expected_comparisons.items():
        observed = observed_comparisons[name]
        for field in (
            "mf",
            "individual_classes",
            "sum_class",
            "p18_sum_violation",
            "standard_compatible",
        ):
            require(observed[field] == expected[field], f"comparison {name} {field}")
        if expected["sum"] is not None:
            require(Fraction(Decimal(observed["sum"])) == expected["sum"], f"comparison {name} sum")
            require(Fraction(Decimal(observed["total"])) == expected["total"], f"comparison {name} total")

    require(oracle["thresholds"] == independent_thresholds(fixtures), "threshold decisions")
    expected_excitations = rows_by_id(independent_excitations(fixtures))
    observed_excitations = rows_by_id(oracle["excitations"])
    require(set(expected_excitations) == set(observed_excitations), "excitation inventory")
    for name, expected in expected_excitations.items():
        observed = observed_excitations[name]
        require(observed["decision"] == expected["decision"], f"excitation {name} decision")
        require(Fraction(Decimal(observed["derived_ev"])) == expected["derived"], f"excitation {name} derived")
        require(Fraction(Decimal(observed["tolerance_ev"])) == expected["tolerance"], f"excitation {name} tolerance")

    require(evidence["official_build"] == EXPECTED_OFFICIAL_BUILD, "official build")
    comparisons = rows_by_id(fixtures["comparisons"])
    require(set(evidence["official_cases"]) == set(EXPECTED_OFFICIAL_CASES), "official cases")
    for name, expected in EXPECTED_OFFICIAL_CASES.items():
        observed = evidence["official_cases"][name]
        tape = independent_tape(comparisons[name])
        require(len(tape) == 2835 and sha256_bytes(tape) == expected["tape"], f"official tape {name}")
        require(
            observed
            == {
                "mf": expected["mf"],
                "tape_bytes": 2835,
                "tape_sha256": expected["tape"],
                "checkr_input_sha256": CHECKR_INPUT_SHA256,
                "checkr_normalized_sha256": expected["checkr"],
                "checkr_read_structure_ok": True,
                "fizcon_input_sha256": FIZCON_INPUT_SHA256,
                "fizcon_normalized_sha256": expected["fizcon"],
                "sum_excess_message": expected["message"],
                "expected_sum_excess_message": expected["message"],
                "agreement": True,
            },
            f"official result {name}",
        )


def mutation_plants(
    evidence: dict[str, Any], fixtures: dict[str, Any], current_rust: dict[str, Any]
) -> dict[str, bool]:
    cases = {}
    mutant = copy.deepcopy(evidence)
    mutant["decimal_oracle_80"]["comparisons"][5]["standard_compatible"] = False
    cases["boundary"] = mutant
    mutant = copy.deepcopy(evidence)
    mutant["decimal_oracle_80"]["fields"][7]["value_bits"] = "0000000000000000"
    cases["digit"] = mutant
    mutant = copy.deepcopy(evidence)
    mutant["decimal_oracle_80"]["fields"][8]["quantum_power_10"] = -8
    cases["exponent"] = mutant
    mutant = copy.deepcopy(evidence)
    mutant["rust_probe"]["tables"][1]["queries"][0]["value"] = 4.0
    cases["interpolation"] = mutant
    mutant = copy.deepcopy(evidence)
    mutant["rust_probe"]["tables"][5]["queries"][0]["side"] = "right"
    cases["side"] = mutant
    mutant = copy.deepcopy(evidence)
    mutant["fixture"]["sha256"] = "0" * 64
    cases["grid"] = mutant
    mutant = copy.deepcopy(evidence)
    mutant["official_cases"]["mf10_sum_boundary"]["expected_sum_excess_message"] = True
    cases["tolerance"] = mutant
    output = {}
    for name, value in cases.items():
        try:
            validate_evidence(value, fixtures, current_rust)
        except AssertionError:
            output[name] = True
        else:
            output[name] = False
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    require(sha256(EVIDENCE) == EXPECTED_EVIDENCE_SHA256, "G1 evidence bytes changed")
    require(sha256(CONTROL) == EXPECTED_CONTROL_SHA256, "G1 control bytes changed")
    require(sha256(FIXTURES) == EXPECTED_FIXTURE_SHA256, "G1 fixture bytes changed")
    require(sha256(PROBE_SOURCE) == EXPECTED_PROBE_SHA256, "G1 Rust probe bytes changed")
    require(sha256(PROTOCOL) == EXPECTED_PROTOCOL_SHA256, "P18b protocol bytes changed")
    require(sha256(G0) == EXPECTED_G0_SHA256, "G0 evidence bytes changed")
    require(sha256(G0_CHECK) == EXPECTED_G0_CHECK_SHA256, "G0 checker bytes changed")
    fixtures = json.loads(FIXTURES.read_text())
    evidence = json.loads(EVIDENCE.read_text())
    current_rust = run_probe()
    validate_evidence(evidence, fixtures, current_rust)
    plants = mutation_plants(evidence, fixtures, current_rust)
    checks = {
        "frozen_predecessor": True,
        "fixed_width_decimal": True,
        "printed_quantum": True,
        "interpolation_laws": True,
        "one_sided_repeated_energy": True,
        "source_classes": True,
        "standard_envelope": True,
        "threshold_contract": True,
        "excitation_boundary": True,
        "fresh_rust_probe": True,
        "official_tapes_regenerated": True,
        "official_checker_decisions": True,
        "portable_evidence": True,
    }
    result = {
        "schema": "actinv-p18b-g1-independent-check-1",
        "gate": "P18b-G1",
        "evidence_sha256": EXPECTED_EVIDENCE_SHA256,
        "control_sha256": EXPECTED_CONTROL_SHA256,
        "fixture_sha256": EXPECTED_FIXTURE_SHA256,
        "probe_sha256": EXPECTED_PROBE_SHA256,
        "checker_source_sha256": sha256(Path(__file__)),
        "checks": checks,
        "mutation_plants": plants,
    }
    result["pass"] = all(checks.values()) and all(plants.values())
    if arguments.no_write:
        require(OUTPUT.is_file(), f"missing committed checker result {OUTPUT}")
        require(json.loads(OUTPUT.read_text()) == result, "committed G1 checker differs")
    else:
        OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
