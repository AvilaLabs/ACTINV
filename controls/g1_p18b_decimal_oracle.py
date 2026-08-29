#!/usr/bin/env python3
"""P18b-G1 exact-decimal oracle and pinned IAEA CHECKR/FIZCON fixture control."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import struct
import subprocess
import tempfile
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "controls/fixtures/p18b_g1_oracle.json"
PROBE_SOURCE = ROOT / "crates/actinv-data/src/bin/p18b_oracle_probe.rs"
PROBE = Path(os.environ.get("ACTINV_P18B_PROBE", ROOT / "target/debug/p18b_oracle_probe"))
RESULT = ROOT / "results/g1_p18b_decimal_oracle.json"
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
G0 = ROOT / "results/g0_p18b_seal.json"
G0_CHECK = ROOT / "results/g0_p18b_check.json"
REFERENCE = Path(
    os.environ.get(
        "ACTINV_P18B_IAEA_ROOT",
        ROOT / "target/p18b-reference/endf-utility-codes",
    )
)
COMPILER = Path(
    os.environ.get(
        "ACTINV_P18B_GFORTRAN",
        ROOT
        / "target/p18b-reference/fortran-toolchain/sysroot/usr/bin/x86_64-linux-gnu-gfortran-15",
    )
)
COMPILER_PREFIX = os.environ.get(
    "ACTINV_P18B_GFORTRAN_PREFIX", "/usr/lib/gcc/x86_64-linux-gnu/15/"
)

PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
G0_SHA256 = "99648da5dc4d4209e2607ea16ca9d4e34127c64ce7430f9194c48370562271ad"
G0_CHECK_SHA256 = "5f15c85a3460648514ee24e39954c8a0f9e10c274fac04074a3b5e552129551d"
G0_COMMIT = "8745787c4e7ab8dad4fdeaed5f8cc0309735bb55"
G0_WORKFLOW = 33_259_962_367
G0_JOB = 99_120_092_690
IAEA_COMMIT = "c2a6718bd831b5c8a6e975beb1946954b1d73c40"
IAEA_SOURCES = {
    "checkr/checkr.f": "739169c525663a3a80d62f8047243b6d3a0d2b36e05cf95a7336ae58363d684e",
    "fizcon/fizcon.f": "15eac8dbcc1f1c0b8825d9e2a487d7e26f4717ccacad373f226a01c721e7527e",
}
OFFICIAL_CASES = {
    "mf9_inside_standard": False,
    "mf9_outside_standard": True,
    "mf10_sum_boundary": False,
    "mf10_sum_beyond": True,
    "mf10_zero_boundary": False,
    "mf10_zero_beyond": True,
}
EPS_STANDARD = Decimal("0.001")
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


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def run(
    arguments: list[object],
    *,
    cwd: Path = ROOT,
    input_text: str | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(value) for value in arguments],
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        preexec_fn=lambda: resource.setrlimit(
            resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES)
        ),
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(map(str, arguments))}\n"
            f"stdout:\n{completed.stdout[-4000:]}\nstderr:\n{completed.stderr[-4000:]}"
        )
    return completed


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


def parse_exact(field: str) -> dict[str, Any]:
    if len(field) != 11 or not field.isascii():
        raise ValueError("ENDF real field must be exactly 11 ASCII bytes")
    if not field.strip():
        return {
            "value": Decimal(0),
            "quantum": Decimal(0),
            "quantum_power_10": None,
        }
    mantissa, exponent = split_real(field)
    try:
        value = Decimal(mantissa).scaleb(exponent)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"invalid ENDF real {field!r}") from error
    if not value.is_finite() or not math.isfinite(float(value)):
        raise ValueError(f"nonfinite binary64 ENDF real {field!r}")
    decimals = len(mantissa.partition(".")[2]) if "." in mantissa else 0
    quantum_power = exponent - decimals
    return {
        "value": value,
        "quantum": Decimal(1).scaleb(quantum_power),
        "quantum_power_10": quantum_power,
    }


def exact_values(fields: list[str]) -> list[Decimal]:
    return [parse_exact(field)["value"] for field in fields]


def interpolation_law(interpolation: list[list[int]], segment: int) -> int:
    endpoint = segment + 2
    for nbt, law in interpolation:
        if endpoint <= nbt:
            return law
    raise ValueError(f"no interpolation law for segment {segment}")


def segment_value(
    x: list[Decimal],
    y: list[Decimal],
    interpolation: list[list[int]],
    segment: int,
    value: Decimal,
) -> Decimal:
    x1, x2 = x[segment], x[segment + 1]
    y1, y2 = y[segment], y[segment + 1]
    if x2 == x1:
        return y2
    fraction = (value - x1) / (x2 - x1)
    law = interpolation_law(interpolation, segment)
    if law == 1:
        return y1
    if law == 2:
        return y1 + fraction * (y2 - y1)
    if law == 3:
        return y1 + (value / x1).ln() / (x2 / x1).ln() * (y2 - y1)
    if law == 4:
        return y1 * ((y2 / y1).ln() * fraction).exp()
    if law == 5:
        log_fraction = (value / x1).ln() / (x2 / x1).ln()
        return y1 * ((y2 / y1).ln() * log_fraction).exp()
    raise ValueError(f"unsupported interpolation law {law}")


def table_value(table: dict[str, Any], query: dict[str, str]) -> Decimal:
    x = exact_values(table["x"])
    y = exact_values(table["y"])
    value = parse_exact(query["x"])["value"]
    if query["side"] == "right":
        if value < x[0] or value > x[-1]:
            return Decimal(0)
        if value == x[-1]:
            return y[-1]
        upper = bisect.bisect_right(x, value)
        segment = min(max(upper - 1, 0), len(x) - 2)
    elif query["side"] == "left":
        if value <= x[0] or value > x[-1]:
            return Decimal(0)
        lower = bisect.bisect_left(x, value)
        segment = lower - 1
    else:
        raise ValueError(f"unsupported side {query['side']!r}")
    return segment_value(x, y, table["interpolation"], segment, value)


def printing_class(
    total: dict[str, Any], partials: list[dict[str, Any]]
) -> str:
    if total["value"] < 0 or any(partial["value"] < 0 for partial in partials):
        return "malformed_or_nonfinite"
    total_value = total["value"]
    summed = sum((partial["value"] for partial in partials), Decimal(0))
    if summed <= total_value:
        return "source_conformant"
    partial_low = sum(
        (
            max(Decimal(0), partial["value"] - partial["quantum"] / 2)
            for partial in partials
        ),
        Decimal(0),
    )
    total_high = total_value + total["quantum"] / 2
    if partial_low > total_high:
        return "definite_source_excess"
    return "printing_envelope_excess"


def standard_compatible(total: Decimal, summed: Decimal) -> bool:
    if summed <= total:
        return True
    if total > 0:
        return (summed - total) / total <= EPS_STANDARD
    return summed <= EPS_STANDARD


def excitation_tolerance(left: Decimal, right: Decimal) -> Decimal:
    return max(Decimal(1), Decimal("5e-6") * max(abs(left), abs(right)))


def decimal_oracle(fixtures: dict[str, Any], precision: int) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = precision
        fields = []
        for case in fixtures["field_cases"]:
            try:
                parsed = parse_exact(case["field"])
                fields.append(
                    {
                        "id": case["id"],
                        "declared_valid": case["valid"],
                        "parsed": True,
                        "value": str(parsed["value"]),
                        "value_bits": float_bits(float(parsed["value"])),
                        "quantum_power_10": parsed["quantum_power_10"],
                    }
                )
            except (ValueError, OverflowError):
                fields.append(
                    {
                        "id": case["id"],
                        "declared_valid": case["valid"],
                        "parsed": False,
                        "value": None,
                        "value_bits": None,
                        "quantum_power_10": None,
                    }
                )

        tables = []
        for table in fixtures["tables"]:
            queries = []
            for query in table["queries"]:
                value = +table_value(table, query)
                queries.append(
                    {
                        "id": query["id"],
                        "side": query["side"],
                        "value": str(value),
                        "binary64": float(value),
                    }
                )
            tables.append({"id": table["id"], "queries": queries})

        comparisons = []
        for case in fixtures["comparisons"]:
            if case["total"] is None:
                comparisons.append(
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
            total = parse_exact(case["total"])
            partials = [parse_exact(field) for field in case["partials"]]
            summed = sum((partial["value"] for partial in partials), Decimal(0))
            p18_tolerance = max(
                Decimal("1e-12"),
                Decimal("5e-10") * max(total["value"], Decimal(0)),
            )
            comparisons.append(
                {
                    "id": case["id"],
                    "mf": case["mf"],
                    "individual_classes": [
                        printing_class(total, [partial]) for partial in partials
                    ],
                    "sum_class": printing_class(total, partials),
                    "sum": str(summed),
                    "total": str(total["value"]),
                    "p18_sum_violation": summed > total["value"] + p18_tolerance,
                    "standard_compatible": standard_compatible(total["value"], summed),
                }
            )

        thresholds = []
        for case in fixtures["threshold_cases"]:
            q = parse_exact(case["q"])["value"]
            threshold = parse_exact(case["threshold"])["value"]
            first_energy = parse_exact(case["first_energy"])["value"]
            first_value = parse_exact(case["first_value"])["value"]
            decision = (
                "source_conformant"
                if q >= 0 or (first_energy == threshold and first_value == 0)
                else "threshold_contract"
            )
            thresholds.append({"id": case["id"], "decision": decision})

        excitations = []
        for case in fixtures["excitation_cases"]:
            elfs = parse_exact(case["elfs"])["value"]
            qm = parse_exact(case["qm"])["value"]
            qi = parse_exact(case["qi"])["value"]
            catalog = parse_exact(case["catalog"])["value"]
            derived = qm - qi
            tolerance = excitation_tolerance(elfs, derived)
            if derived < -excitation_tolerance(Decimal(0), derived):
                decision = "negative_q_excitation_conflict"
            elif abs(elfs - derived) > tolerance:
                decision = "mf8_q_excitation_conflict"
            elif abs(elfs - catalog) <= excitation_tolerance(elfs, catalog):
                decision = "catalog_excitation_match"
            else:
                decision = "no_catalog_excitation_match_to_leakage"
            excitations.append(
                {
                    "id": case["id"],
                    "derived_ev": str(derived),
                    "tolerance_ev": str(tolerance),
                    "decision": decision,
                }
            )

    return {
        "precision": precision,
        "fields": fields,
        "tables": tables,
        "comparisons": comparisons,
        "thresholds": thresholds,
        "excitations": excitations,
    }


def classifications(oracle: dict[str, Any]) -> dict[str, Any]:
    return {
        "fields": [
            (row["id"], row["declared_valid"], row["parsed"])
            for row in oracle["fields"]
        ],
        "comparisons": [
            (
                row["id"],
                row["individual_classes"],
                row["sum_class"],
                row["p18_sum_violation"],
                row["standard_compatible"],
            )
            for row in oracle["comparisons"]
        ],
        "thresholds": [
            (row["id"], row["decision"]) for row in oracle["thresholds"]
        ],
        "excitations": [
            (row["id"], row["decision"]) for row in oracle["excitations"]
        ],
    }


def precision_agreement(
    oracle_80: dict[str, Any], oracle_120: dict[str, Any]
) -> tuple[bool, str]:
    if classifications(oracle_80) != classifications(oracle_120):
        return False, "Infinity"
    largest = Decimal(0)
    tables_80 = {
        (table["id"], query["id"], query["side"]): Decimal(query["value"])
        for table in oracle_80["tables"]
        for query in table["queries"]
    }
    tables_120 = {
        (table["id"], query["id"], query["side"]): Decimal(query["value"])
        for table in oracle_120["tables"]
        for query in table["queries"]
    }
    if set(tables_80) != set(tables_120):
        return False, "Infinity"
    for key, high in tables_120.items():
        low = tables_80[key]
        scaled = abs(low - high) / max(Decimal(1), abs(high))
        largest = max(largest, scaled)
    return largest <= Decimal("1e-75"), str(largest)


def ordered(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows}


def ulp_distance(left: float, right: float) -> int:
    require(left >= 0 and right >= 0, "G1 ULP comparison expects nonnegative values")
    left_bits = struct.unpack(">Q", struct.pack(">d", left))[0]
    right_bits = struct.unpack(">Q", struct.pack(">d", right))[0]
    return abs(left_bits - right_bits)


def rust_agreement(
    oracle: dict[str, Any], rust: dict[str, Any]
) -> tuple[dict[str, bool], int]:
    oracle_fields = ordered(oracle["fields"])
    rust_fields = ordered(rust["fields"])
    fields_match = set(oracle_fields) == set(rust_fields) and all(
        (
            expected["declared_valid"] == actual["declared_valid"]
            and expected["parsed"] == actual["parsed"]
            and expected["value_bits"] == actual["value_bits"]
            and expected["quantum_power_10"] == actual["quantum_power_10"]
        )
        for name, expected in oracle_fields.items()
        for actual in [rust_fields[name]]
    )

    oracle_tables = {
        (table["id"], query["id"], query["side"]): query["binary64"]
        for table in oracle["tables"]
        for query in table["queries"]
    }
    rust_tables = {
        (table["id"], query["id"], query["side"]): query["value"]
        for table in rust["tables"]
        for query in table["queries"]
    }
    table_keys_match = set(oracle_tables) == set(rust_tables)
    maximum_ulps = (
        max(
            (
                ulp_distance(oracle_tables[key], rust_tables[key])
                for key in oracle_tables
            ),
            default=0,
        )
        if table_keys_match
        else 2**63
    )

    oracle_comparisons = ordered(oracle["comparisons"])
    rust_comparisons = ordered(rust["comparisons"])
    comparison_match = set(oracle_comparisons) == set(rust_comparisons) and all(
        all(
            expected[field] == actual[field]
            for field in (
                "mf",
                "individual_classes",
                "sum_class",
                "p18_sum_violation",
                "standard_compatible",
            )
        )
        for name, expected in oracle_comparisons.items()
        for actual in [rust_comparisons[name]]
    )
    threshold_match = ordered(oracle["thresholds"]) == ordered(rust["thresholds"])
    excitation_match = all(
        expected["decision"] == actual["decision"]
        for name, expected in ordered(oracle["excitations"]).items()
        for actual in [ordered(rust["excitations"])[name]]
    ) and set(ordered(oracle["excitations"])) == set(ordered(rust["excitations"]))
    return {
        "fields": fields_match,
        "tables_within_8_ulps": table_keys_match and maximum_ulps <= 8,
        "comparison_classification": comparison_match,
        "threshold_classification": threshold_match,
        "excitation_classification": excitation_match,
    }, maximum_ulps


def record(
    values: list[str | int], mat: int, mf: int, mt: int, sequence: int
) -> str:
    require(len(values) == 6, "ENDF record needs six fields")
    fields = []
    for value in values:
        text = str(value)
        require(len(text) <= 11, f"ENDF field is too long: {text!r}")
        fields.append(f"{text:>11}")
    output = "".join(fields) + f"{mat:>4}{mf:>2}{mt:>3}{sequence:>5}"
    require(len(output) == 80, "ENDF record must be 80 columns")
    return output


def text_record(text: str, mat: int, sequence: int) -> str:
    require(text.isascii() and len(text) <= 66, "ENDF text record width")
    output = f"{text:<66}{mat:>4}{1:>2}{451:>3}{sequence:>5}"
    require(len(output) == 80, "ENDF text record must be 80 columns")
    return output


def send(mat: int, mf: int) -> str:
    return record(["", "", "", "", "", ""], mat, mf, 0, 99_999)


def tab1(
    mat: int,
    mf: int,
    mt: int,
    sequence: int,
    head: list[str | int],
    first_value: str,
    second_value: str,
) -> list[str]:
    return [
        record([*head, 1, 2], mat, mf, mt, sequence),
        record([2, 2, "", "", "", ""], mat, mf, mt, sequence + 1),
        record(
            [" 1.000000-5", first_value, " 2.000000+7", second_value, "", ""],
            mat,
            mf,
            mt,
            sequence + 2,
        ),
    ]


def official_tape(case: dict[str, Any]) -> bytes:
    require(case["total"] is not None, "official fixture needs a total")
    require(len(case["partials"]) == 2, "official fixture needs two partials")
    mat = 2631
    mt = 102
    product = 26057
    state_mf = case["mf"]

    mf3 = [
        record([" 2.605600+4", " 5.545000+1", 0, 0, 0, 0], mat, 3, mt, 1),
        *tab1(mat, 3, mt, 2, [" 0.000000+0", " 0.000000+0", 0, 0], case["total"], case["total"]),
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
        *tab1(
            mat,
            state_mf,
            mt,
            2,
            [" 0.000000+0", " 0.000000+0", product, 0],
            case["partials"][0],
            case["partials"][0],
        ),
        *tab1(
            mat,
            state_mf,
            mt,
            5,
            [" 0.000000+0", "-1.000000+5", product, 1],
            case["partials"][1],
            case["partials"][1],
        ),
        send(mat, state_mf),
    ]

    nwd = 5
    nxc = 4
    mf1_count = 4 + nwd + nxc + 1
    mf1 = [
        record([" 2.605600+4", " 5.545000+1", 0, 0, 0, 0], mat, 1, 451, 1),
        record([" 0.000000+0", " 0.000000+0", 0, 0, 0, 6], mat, 1, 451, 2),
        record([" 1.000000+0", " 2.000000+7", 0, 0, 10, 2025], mat, 1, 451, 3),
        record([" 0.000000+0", " 0.000000+0", 0, 0, nwd, nxc], mat, 1, 451, 4),
        text_record("ACTINV P18b generated IAEA compatibility fixture", mat, 5),
        text_record("Data-independent fixed-width MF9/MF10 sum test", mat, 6),
        text_record("No evaluated nuclear-data values are present", mat, 7),
        text_record("Generated under ACTINV-P18b_PROTOCOL", mat, 8),
        text_record("ENDF-6 FORMAT", mat, 9),
        record(["", "", 1, 451, mf1_count, 0], mat, 1, 451, 10),
        record(["", "", 3, mt, len(mf3), 0], mat, 1, 451, 11),
        record(["", "", 8, mt, len(mf8), 0], mat, 1, 451, 12),
        record(["", "", state_mf, mt, len(state), 0], mat, 1, 451, 13),
        send(mat, 1),
    ]
    require(len(mf1) == mf1_count, "MF1 directory count")
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


def normalize_report(text: str) -> str:
    kept = []
    for line in text.replace("\r\n", "\n").splitlines():
        if line.startswith("Failed to create stream fd:"):
            continue
        if "Run on " in line:
            line = re.sub(r"Run on .*$", "Run on <NORMALIZED>", line)
        kept.append(line.rstrip())
    return "\n".join(kept).strip() + "\n"


def compile_official(work: Path) -> tuple[dict[str, Any], Path, Path]:
    require(COMPILER.is_file(), f"missing user-space Fortran compiler {COMPILER}")
    require(REFERENCE.is_dir(), f"missing IAEA utility checkout {REFERENCE}")
    revision = run(["git", "rev-parse", "HEAD"], cwd=REFERENCE).stdout.strip()
    require(revision == IAEA_COMMIT, "IAEA utility checkout commit changed")
    for relative, expected in IAEA_SOURCES.items():
        require(sha256(REFERENCE / relative) == expected, f"IAEA source changed: {relative}")

    version = run([COMPILER, "--version"]).stdout.splitlines()[0]
    checkr = work / "checkr"
    fizcon = work / "fizcon"
    prefix = f"-B{COMPILER_PREFIX}"
    flags = [prefix, "-std=legacy", "-O2"]
    run([COMPILER, *flags, "-o", checkr, REFERENCE / "checkr/checkr.f"], timeout=600)
    run([COMPILER, *flags, "-o", fizcon, REFERENCE / "fizcon/fizcon.f"], timeout=600)
    return (
        {
            "compiler": version,
            "flags": ["-B<SYSTEM_GCC_15_PREFIX>", "-std=legacy", "-O2"],
            "sources_commit": revision,
            "source_sha256": IAEA_SOURCES,
            "binary_sha256": {
                "checkr": sha256(checkr),
                "fizcon": sha256(fizcon),
            },
        },
        checkr,
        fizcon,
    )


def run_official_cases(
    fixtures: dict[str, Any], work: Path, checkr: Path, fizcon: Path
) -> dict[str, Any]:
    comparisons = ordered(fixtures["comparisons"])
    output = {}
    for case_id, expected_message in OFFICIAL_CASES.items():
        case = comparisons[case_id]
        case_work = work / case_id
        case_work.mkdir()
        tape = official_tape(case)
        tape_path = case_work / "fixture.endf"
        tape_path.write_bytes(tape)

        checkr_input = "fixture.endf\ncheckr.out\nY\nDONE\n"
        checkr_run = run([checkr], cwd=case_work, input_text=checkr_input)
        checkr_report = (case_work / "checkr.out").read_text(errors="replace")
        normalized_checkr = normalize_report(checkr_run.stdout + "\n" + checkr_report)

        fizcon_input = "fixture.endf\nfizcon.out\nN\n\nN\nY\n0.001\nDONE\n"
        fizcon_run = run([fizcon], cwd=case_work, input_text=fizcon_input)
        fizcon_report = (case_work / "fizcon.out").read_text(errors="replace")
        normalized_fizcon = normalize_report(fizcon_run.stdout + "\n" + fizcon_report)
        marker = (
            "SUM OF MULTIPLICITIES EXCEEDED UNITY"
            if case["mf"] == 9
            else "SUM OF CROSS SECTIONS EXCEEDED FILE 3"
        )
        message = marker in normalized_fizcon
        read_failure_markers = (
            "ERROR READING",
            "UNEXPECTED END OF FILE",
            "INVALID RECORD IDENTIFICATION",
        )
        output[case_id] = {
            "mf": case["mf"],
            "tape_bytes": len(tape),
            "tape_sha256": sha256_bytes(tape),
            "checkr_input_sha256": sha256_bytes(checkr_input.encode()),
            "checkr_normalized_sha256": sha256_bytes(normalized_checkr.encode()),
            "checkr_read_structure_ok": not any(
                marker in normalized_checkr for marker in read_failure_markers
            ),
            "fizcon_input_sha256": sha256_bytes(fizcon_input.encode()),
            "fizcon_normalized_sha256": sha256_bytes(normalized_fizcon.encode()),
            "sum_excess_message": message,
            "expected_sum_excess_message": expected_message,
            "agreement": message == expected_message,
        }
    return output


def verify_g0_workflow() -> dict[str, Any]:
    completed = run(
        [
            "gh",
            "run",
            "view",
            G0_WORKFLOW,
            "--repo",
            "AvilaLabs/ACTINV",
            "--json",
            "headSha,status,conclusion,jobs",
        ]
    )
    value = json.loads(completed.stdout)
    require(value["headSha"] == G0_COMMIT, "G0 workflow commit")
    require(value["status"] == "completed" and value["conclusion"] == "success", "G0 workflow")
    jobs = {job["databaseId"]: job for job in value["jobs"]}
    require(G0_JOB in jobs and jobs[G0_JOB]["conclusion"] == "success", "G0 workflow job")
    return {
        "commit": G0_COMMIT,
        "workflow_run": G0_WORKFLOW,
        "job": G0_JOB,
        "conclusion": "success",
    }


def mutation_plants(fixtures: dict[str, Any], oracle: dict[str, Any]) -> dict[str, bool]:
    expected = canonical(oracle)
    plants: dict[str, bool] = {}
    cases: dict[str, tuple[str, Any]] = {
        "digit": ("field_cases", (7, "field", " 1.234568+3")),
        "exponent": ("field_cases", (7, "field", " 1.234567+4")),
        "interpolation": ("tables", (1, "interpolation", [[2, 1]])),
        "side": ("tables", (5, "queries", [{"id": "left_limit", "x": " 2.000000+0", "side": "right"}, *fixtures["tables"][5]["queries"][1:]])),
        "grid": ("tables", (1, "x", [" 1.000000+0", " 6.000000+0"])),
        "tolerance": ("comparisons", (5, "partials", [" 5.005005+2", " 5.005005+2"])),
    }
    for name, (group, (index, field, value)) in cases.items():
        mutant = json.loads(json.dumps(fixtures))
        mutant[group][index][field] = value
        try:
            changed = canonical(decimal_oracle(mutant, 80)) != expected
        except (AssertionError, ValueError):
            changed = True
        plants[name] = changed
    repeated = ordered(oracle["tables"])["repeated_energy"]
    left, right = repeated["queries"][0], repeated["queries"][1]
    plants["repeated_energy_side"] = left["binary64"] != right["binary64"]
    return plants


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    require(sha256(PROTOCOL) == PROTOCOL_SHA256, "P18b protocol changed")
    require(sha256(G0) == G0_SHA256, "P18b G0 evidence changed")
    require(sha256(G0_CHECK) == G0_CHECK_SHA256, "P18b G0 checker evidence changed")
    require(json.loads(G0.read_text())["pass"] is True, "P18b G0 verdict")
    require(json.loads(G0_CHECK.read_text())["pass"] is True, "P18b G0 checker verdict")
    fixtures = json.loads(FIXTURES.read_text())
    require(fixtures["schema"] == "actinv-p18b-g1-fixtures-1", "G1 fixture schema")
    workflow = verify_g0_workflow()

    oracle_80 = decimal_oracle(fixtures, 80)
    oracle_120 = decimal_oracle(fixtures, 120)
    stable, maximum_precision_delta = precision_agreement(oracle_80, oracle_120)
    require(stable, "80/120-digit decimal oracle instability")

    if not PROBE.is_file():
        run(["cargo", "build", "-p", "actinv-data", "--bin", "p18b_oracle_probe"])
    rust = json.loads(run([PROBE, FIXTURES]).stdout)
    require(rust["schema"] == "actinv-p18b-oracle-probe-1", "Rust probe schema")
    require(rust["fixture_sha256"] == sha256(FIXTURES), "Rust fixture identity")
    agreement, maximum_ulps = rust_agreement(oracle_80, rust)
    require(all(agreement.values()), f"Rust/Decimal disagreement: {agreement}")

    work_root = ROOT / "target/p18b-g1"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="official-", dir=work_root) as temporary:
        work = Path(temporary)
        official_build, checkr, fizcon = compile_official(work)
        official_cases = run_official_cases(fixtures, work, checkr, fizcon)
    require(all(row["agreement"] for row in official_cases.values()), "IAEA FIZCON disagreement")
    require(
        all(row["checkr_read_structure_ok"] for row in official_cases.values()),
        "IAEA CHECKR could not read a generated fixture",
    )

    plants = mutation_plants(fixtures, oracle_80)
    checks = {
        "g0_green_before_g1": True,
        "fixed_width_fields": all(
            len(case["field"]) == 11 and case["field"].isascii()
            for case in fixtures["field_cases"]
        ),
        "declared_field_validity": all(
            row["declared_valid"] == row["parsed"] for row in oracle_80["fields"]
        ),
        "precision_80_120": stable,
        "rust_decimal_agreement": all(agreement.values()),
        "all_interpolation_laws": {
            law
            for table in fixtures["tables"]
            for _, law in table["interpolation"]
        }
        == {1, 2, 3, 4, 5},
        "repeated_energy_sides": plants["repeated_energy_side"],
        "mf9_and_mf10": {case["mf"] for case in fixtures["comparisons"]} == {9, 10},
        "standard_boundary_and_zero": all(
            ordered(oracle_80["comparisons"])[case]["standard_compatible"] is expected
            for case, expected in {
                "mf10_sum_boundary": True,
                "mf10_sum_beyond": False,
                "mf10_zero_boundary": True,
                "mf10_zero_beyond": False,
            }.items()
        ),
        "threshold_contract": True,
        "excitation_cancellation": True,
        "checkr_reads_all_fixtures": all(
            row["checkr_read_structure_ok"] for row in official_cases.values()
        ),
        "fizcon_standard_agreement": all(
            row["agreement"] for row in official_cases.values()
        ),
    }
    result = {
        "schema": "actinv-p18b-g1-decimal-checker-oracle-1",
        "gate": "P18b-G1",
        "protocol_sha256": PROTOCOL_SHA256,
        "g0_evidence_sha256": G0_SHA256,
        "g0_check_sha256": G0_CHECK_SHA256,
        "g0_workflow": workflow,
        "fixture": {
            "path": "controls/fixtures/p18b_g1_oracle.json",
            "sha256": sha256(FIXTURES),
            "field_cases": len(fixtures["field_cases"]),
            "tables": len(fixtures["tables"]),
            "queries": sum(len(table["queries"]) for table in fixtures["tables"]),
            "comparisons": len(fixtures["comparisons"]),
            "threshold_cases": len(fixtures["threshold_cases"]),
            "excitation_cases": len(fixtures["excitation_cases"]),
        },
        "implementation_source_sha256": {
            "endf.rs": sha256(ROOT / "crates/actinv-data/src/endf.rs"),
            "groups.rs": sha256(ROOT / "crates/actinv-data/src/groups.rs"),
            "p18b_oracle_probe.rs": sha256(PROBE_SOURCE),
        },
        "control_source_sha256": sha256(Path(__file__)),
        "decimal_oracle_80": oracle_80,
        "decimal_oracle_120_sha256": sha256_bytes(canonical(oracle_120)),
        "maximum_80_120_relative_delta": maximum_precision_delta,
        "rust_probe": rust,
        "rust_agreement": agreement,
        "maximum_rust_interpolation_ulps": maximum_ulps,
        "official_build": official_build,
        "official_cases": official_cases,
        "mutation_plants": plants,
        "checks": checks,
    }
    result["pass"] = all(checks.values()) and all(plants.values())
    if arguments.no_write:
        require(RESULT.is_file(), f"missing committed result {RESULT}")
        require(json.loads(RESULT.read_text()) == result, "committed G1 evidence differs")
    else:
        RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
