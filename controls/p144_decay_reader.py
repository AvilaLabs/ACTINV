#!/usr/bin/env python3
"""P144 V2: an independent reader for an ENDF-6 radioactive-decay sublibrary (File 8, MT=457).

Written from the ENDF-6 Formats Manual (ENDF-102), not from ACTINV's Rust reader, so that a comparison of the two
checks the parsing and not just the shared code. Standard library only.

Conventions used (ENDF-6 Formats Manual, Chapter 8 "File 8: Radioactive Decay and Fission Product Yield Data",
the MT=457 description; the chapter's section number was not checked offline):

  * Records are 80-column lines: columns 1-66 hold six 11-character fields, 67-70 the MAT number, 71-72 the MF
    number, 73-75 the MT number, 76-80 the line sequence number.
  * A float field is a decimal with the ENDF short exponent: `1.234567+5`, `-3.2-10`, and the JEFF form
    `1.00000+00`; an ordinary `1.5E+3` also parses. A blank field is zero. Integer fields are written as integers.
  * MT=457 of one material is: a HEAD record [ZA, AWR, LIS, LISO, NST, NSP]; a LIST record
    [T1/2, dT1/2, 0, 0, N1, 0] followed by N1 words of average decay energies; and a LIST record
    [SPI, PAR, 0, 0, N1, NDK] followed by N1 words that hold NDK decay-mode entries of six words
    [RTYP, RFS, Q, dQ, BR, dBR]. The spectrum blocks that follow are not read. NST=1 marks a stable nuclide.
  * ZA = 1000 Z + A and LISO is the isomeric state number (0 ground, 1 first metastable, ...).

RTYP convention for alpha emission. RTYP is the decay-mode code: 0 gamma, 1 beta-, 2 electron capture and/or beta+,
3 isomeric transition, 4 alpha, 5 neutron, 6 spontaneous fission, 7 proton, and so on. A non-integer RTYP lists the
successive processes of one mode as decimal digits after the point: 1.4 is a beta- decay followed by alpha emission,
6.4 a spontaneous-fission branch with an alpha. This reader therefore writes the RTYP as the decimal string it holds
in the file (all digits written, trailing zeros removed) and a mode is alpha-emitting when the digit "4" occurs in the
integer part or in any fractional digit, and its branching ratio BR is greater than zero.
"""
from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path

WIDTH = 11


def endf_float_text(field: str) -> str:
    """The field as plain `mantissa` or `mantissaEexp` text that float() and Decimal() accept."""
    text = field.strip()
    if not text:
        return "0"
    if "e" in text.lower():
        return text
    # A sign after the first character that is not part of a leading sign starts the short exponent.
    for index in range(len(text) - 1, 0, -1):
        if text[index] in "+-" and text[index - 1] not in "eE":
            return f"{text[:index]}E{text[index:]}"
    return text


def endf_float(field: str) -> float:
    return float(endf_float_text(field))


def endf_int(field: str) -> int:
    value = endf_float(field)
    if value != int(value):
        raise ValueError(f"integer field holds {field!r}")
    return int(value)


def rtyp_digits(field: str) -> str:
    """The RTYP of a mode as the decimal string written in the file, without an exponent or trailing zeros."""
    return format(Decimal(endf_float_text(field)).normalize(), "f")


def alpha_emitting(modes: list[tuple[float, float]]) -> bool:
    """Any mode with a positive branching ratio whose RTYP decimal digits include 4 (see the module docstring)."""
    return any(br > 0.0 and "4" in rtyp_digits(repr(rtyp)) for rtyp, br in modes)


def z(za: int) -> int:
    return za // 1000


def _words(lines: list[str], count: int) -> list[str]:
    """The first `count` 11-character words of the data lines of a LIST record."""
    out = []
    for line in lines:
        out.extend(line[start:start + WIDTH] for start in range(0, 66, WIDTH))
    return out[:count]


def _material(lines: list[str]) -> tuple[tuple[int, int], dict]:
    head = lines[0]
    za, liso, nst = endf_int(head[0:11]), endf_int(head[33:44]), endf_int(head[44:55])
    if nst == 1:
        return (za, liso), {"half_life_s": math.inf, "modes": []}
    half_life = endf_float(lines[1][0:11])
    n1 = endf_int(lines[1][44:55])
    cursor = 2 + -(-n1 // 6)
    modes: list[tuple[float, float]] = []
    if cursor < len(lines):
        n1, ndk = endf_int(lines[cursor][44:55]), endf_int(lines[cursor][55:66])
        if n1 < 6 * ndk:
            raise ValueError(f"ZA {za} LISO {liso}: {ndk} decay modes need {6 * ndk} words, record has {n1}")
        words = _words(lines[cursor + 1:cursor + 1 + -(-n1 // 6)], 6 * ndk)
        if len(words) != 6 * ndk:
            raise ValueError(f"ZA {za} LISO {liso}: decay-mode record is truncated")
        # The RTYP is kept as the float the field denotes; rtyp_digits() re-derives its decimal digits.
        modes = [(endf_float(words[i]), endf_float(words[i + 4])) for i in range(0, 6 * ndk, 6)]
    return (za, liso), {"half_life_s": half_life, "modes": modes}


def read(path: str | Path) -> dict[tuple[int, int], dict]:
    """Every MF=8 MT=457 material of one or more concatenated ENDF-6 files: (za, liso) -> half life and modes."""
    result: dict[tuple[int, int], dict] = {}
    section: list[str] = []
    key = None

    def flush() -> None:
        if section:
            ident, record = _material(section)
            if ident in result:
                raise ValueError(f"{path}: duplicate material ZA {ident[0]} LISO {ident[1]}")
            result[ident] = record
            section.clear()

    with open(path, encoding="ascii", errors="replace") as stream:
        for raw in stream:
            line = raw.rstrip("\r\n").ljust(80)
            mat, mf, mt = line[66:70].strip(), line[70:72].strip(), line[72:75].strip()
            if mf == "8" and mt == "457" and mat != "0":
                if key is not None and key != mat:
                    flush()
                key = mat
                section.append(line)
            else:
                flush()
                key = None
        flush()
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("usage: p144_decay_reader.py DECAY_FILE")
    table = read(sys.argv[1])
    print(f"{len(table)} materials, {sum(1 for v in table.values() if math.isinf(v['half_life_s']))} stable")
