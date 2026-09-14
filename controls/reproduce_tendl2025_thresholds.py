#!/usr/bin/env python3
"""Read-only reproducer for four pinned TENDL-2025 MF3/MF10 discrepancies.

Python standard library only; no ACTINV imports, interpolation, or downloads.
Usage: python3 reproduce_tendl2025_thresholds.py DIRECTORY_WITH_FOUR_FILES
Exit 0: all four pinned discrepancies reproduced; exit 1: input/check failure.
JSON, including raw records and physical line numbers, is printed to stdout.
"""

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys


CASES = (
    ("n-Fe053m.tendl", 26052, "7791974", "107582.0",
     "7d3a58320465837055b854a5a61860584f9b0bc8bd2f8d264a659a1ef2aa095b"),
    ("n-Cl035.tendl", 17034, "13009500", "24.59622",
     "c0ac6b92bad9851493b10a42bd81700eb3a7bbe8915b2627d2ce6454d3d08adf"),
    ("n-Zr088.tendl", 40087, "12494780", "70.11846",
     "148382919d34670d07f3ae3c121b06e05f8d1c8b763c6fba30a9401edfeb40a3"),
    ("n-Y088.tendl", 39087, "9459262", "26.05538",
     "f6c69655d0991faddb90e8b1a620b9ccd4d0e68a56ce73f3ae52955295cfaedc"),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def real(field):
    return Decimal(re.sub(r"(?<=\d)([+-]\d+)$", r"E\1", field.strip()))


def fields(record):
    return [record[i:i + 11] for i in range(0, 66, 11)]


def tab1(records, index):
    header = fields(records[index][1])
    nr, np = int(header[4]), int(header[5])
    require(nr > 0 and np > 0, "Invalid TAB1 dimensions")
    offset = index + 1
    interpolation = []
    for _ in range((2 * nr + 5) // 6):
        interpolation.extend(fields(records[offset][1]))
        offset += 1
    interpolation = list(map(int, interpolation[:2 * nr]))
    require(interpolation[-2] == np, "Invalid interpolation endpoint")
    points = []
    for _ in range((2 * np + 5) // 6):
        line_number, line = records[offset]
        for start in range(0, 66, 22):
            if len(points) < np:
                points.append((real(line[start:start + 11]),
                               real(line[start + 11:start + 22]),
                               line_number, line))
        offset += 1
    require(all(a[0] <= b[0] for a, b in zip(points, points[1:])),
            "Decreasing energy grid")
    return header, points, offset


def inspect(directory, case):
    name, zap, energy, expected, digest = case
    raw = (directory / name).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    require(actual == digest, f"{name}: source hash mismatch: {actual}")
    sections = {}
    for number, line in enumerate(raw.decode("ascii").splitlines(), 1):
        require(len(line) == 80, f"{name}:{number}: expected 80 columns")
        mat, mf, mt = int(line[66:70]), int(line[70:72]), int(line[72:75])
        if mf in (3, 10) and mt == 16:
            sections.setdefault((mat, mf), []).append((number, line))
    mats = {mat for mat, _ in sections}
    require(len(mats) == 1, "Expected one material")
    mat = next(iter(mats))
    evidence = {}
    for mf in (3, 10):
        records = sections[(mat, mf)]
        count = 1 if mf == 3 else int(records[0][1][44:55])
        index = 1
        matches = []
        for _ in range(count):
            header, points, index = tab1(records, index)
            if mf == 10 and (int(header[2]), int(header[3])) != (zap, 0):
                continue
            require(real(header[1]) < 0, "Expected negative reaction Q")
            require(points[0][0] == Decimal(energy), "Unexpected threshold")
            matches.extend(p for p in points if p[0] == Decimal(energy))
        require(index == len(records), "Unconsumed section records")
        require(len(matches) == 1, "Expected one explicit energy record")
        e, value, number, line = matches[0]
        evidence[str(mf)] = {"value_b": str(value), "line_number": number,
                             "raw_record": line}
    require(Decimal(evidence["3"]["value_b"]) == 0, "MF3 is not zero")
    require(Decimal(evidence["10"]["value_b"]) == Decimal(expected),
            "MF10 differs from expected nonzero product")
    return {"file": name, "sha256": actual, "mat": mat, "mt": 16,
            "zap": zap, "lfs": 0, "energy_eV": energy,
            "records": evidence, "contradiction_reproduced": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        cases = [inspect(args.directory, case) for case in CASES]
    except (OSError, ValueError, IndexError, KeyError, ArithmeticError) as exc:
        print(f"Reproduction failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"scope": "four pinned source files; explicit ordinates only",
                      "cases": cases, "all_reproduced": True}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
