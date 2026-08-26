#!/usr/bin/env python3
"""P7-G1: independent Python/Rust decay-spectrum record agreement and all-file audit."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g1_p7_spectrum_reader.json"
sys.path.insert(0, str(ROOT / "controls"))
from p7_spectra import audit, parse_file

CASES = [(27060, 0), (55137, 0), (56137, 1), (25068, 0)]


def dump_path() -> Path:
    supplied = os.environ.get("ACTINV_DUMP")
    if supplied:
        return Path(supplied)
    for profile in ("debug", "release"):
        candidate = ROOT / "target" / profile / "dump"
        if candidate.exists():
            return candidate
    raise RuntimeError("build actinv-data's dump binary before running P7 G1")


def expected_lines(records: dict) -> list[list[float | int | str]]:
    out: list[list[float | int | str]] = [[len(CASES)]]
    for key in sorted(CASES):
        record = records[key]
        out.append(["N", key[0], key[1], len(record["spectra"])])
        for si, spectrum in enumerate(record["spectra"]):
            norm = spectrum["norm"]
            out.append(
                [
                    "S", si, spectrum["styp"], spectrum["lcon"], spectrum["lcov"],
                    *norm, len(spectrum["discrete"]), int(spectrum["continuous"] is not None),
                ]
            )
            for line in spectrum["discrete"]:
                out.append(["D", si, line["energy"], line["d_energy"], *line["fields"]])
            continuum = spectrum["continuous"]
            if continuum:
                out.append(["C", si, continuum["rtyp"], len(continuum["ranges"]), len(continuum["points"])])
                for nbt, law in continuum["ranges"]:
                    out.append(["R", si, nbt, law])
                for energy, probability in continuum["points"]:
                    out.append(["P", si, energy, probability])
    return out


def compare(expected: list[list], actual_text: str) -> tuple[int, float, list]:
    actual = [line.split() for line in actual_text.splitlines()]
    mismatches = []
    max_rel = 0.0
    if len(actual) != len(expected):
        mismatches.append(["line_count", len(expected), len(actual)])
    for line_no, (want, got) in enumerate(zip(expected, actual), 1):
        if len(want) != len(got):
            mismatches.append([line_no, "field_count", len(want), len(got)])
            continue
        for field_no, (left, right) in enumerate(zip(want, got)):
            if isinstance(left, str):
                if left != right:
                    mismatches.append([line_no, field_no, left, right])
            elif isinstance(left, int):
                if left != int(right):
                    mismatches.append([line_no, field_no, left, right])
            else:
                value = float(right)
                rel = abs(left - value) / max(abs(left), abs(value), 1e-300)
                max_rel = max(max_rel, rel)
                if rel > 1e-12:
                    mismatches.append([line_no, field_no, left, value, rel])
    return len(actual), max_rel, mismatches[:50]


def parse_summary(text: str) -> dict:
    lines = text.splitlines()
    sections, spectra = map(int, lines[0].split())
    counts = {f"{styp}:{lcon}": int(count) for _, styp, lcon, count in map(str.split, lines[1:])}
    return {"sections": sections, "spectra": spectra, "styp_lcon": counts}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: g1_p7_spectrum_reader.py DECAY.endf")
    decay_path = sys.argv[1]
    records = parse_file(decay_path)
    python_audit = audit(records)
    executable = dump_path()
    selected = subprocess.run(
        [str(executable), "spectra", decay_path, *(f"{za}:{liso}" for za, liso in CASES)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    actual_lines, max_rel, mismatches = compare(expected_lines(records), selected)
    rust_summary = parse_summary(
        subprocess.run(
            [str(executable), "spectra-summary", decay_path], check=True, capture_output=True, text=True
        ).stdout
    )
    passed = not mismatches and max_rel <= 1e-12 and rust_summary == python_audit and python_audit["sections"] == 3821
    result = {
        "cases": [f"{za}:{liso}" for za, liso in CASES],
        "selected_dump_lines": actual_lines,
        "max_relative_field_difference": max_rel,
        "mismatches": mismatches,
        "python_audit": python_audit,
        "rust_audit": rust_summary,
        "pass": passed,
    }
    RESULT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
