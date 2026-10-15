#!/usr/bin/env python3
"""Shared helpers for the regulatory radiological-table converters.

The converters are run with `python3 -I`, which keeps the script directory off
`sys.path`; they load this module by file path (see `load_common`).  Nothing
here reads a network or executes downloaded content.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

TABLE_FORMAT = "actinv-radiological-table-1"
ROWS_FORMAT = "actinv-regulatory-rows-1"
BLOCK_BYTES = 1024 * 1024

ELEMENTS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
    "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
    "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl "
    "Mc Lv Ts Og"
).split()
Z_OF = {symbol: index + 1 for index, symbol in enumerate(ELEMENTS)}

# "Co-60", "Ag-108m", "Mn-52 m", "Te132", "Np237": the spellings the sources use.
_NUCLIDE = re.compile(r"^([A-Z][a-z]?)\s*-?\s*(\d{1,3})\s*(m\d*)?$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def parse_nuclide(raw: str) -> tuple[str, int, int, int] | None:
    """Return (canonical ACTINV key, Z, A, isomer state) or None if `raw` is not a nuclide.

    ACTINV keys are `Co60`, `Ag108m1`; a bare `m` is the first isomer, `m2` the second.
    """
    match = _NUCLIDE.match(raw.strip())
    if not match:
        return None
    symbol, mass, meta = match.groups()
    if symbol not in Z_OF:
        return None
    mass_number = int(mass)
    if not 1 <= mass_number <= 999:
        return None
    state = 0
    if meta:
        state = int(meta[1:]) if len(meta) > 1 else 1
    canonical = f"{symbol}{mass_number}" + (f"m{state}" if state else "")
    return canonical, Z_OF[symbol], mass_number, state


def za_key(z: int, a: int, state: int) -> int:
    """FISPACT-style identifier: ZZAAAS (S = isomer state)."""
    return z * 10000 + a * 10 + state


def scaled_float(mantissa: str, exponent: int, shift: int = 0) -> float:
    """The correctly rounded double nearest `mantissa x 10^(exponent+shift)`.

    The decimal string is handed to the float parser so no intermediate
    multiplication rounds the value.
    """
    return float(f"{mantissa}e{exponent + shift}")


def dump_json(path: Path, document: object) -> str:
    """Write `document` deterministically (insertion order kept) and return its SHA-256."""
    text = json.dumps(document, indent=1, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return sha256_file(path)


def source_block(
    *,
    citation: str,
    edition: str,
    url: str,
    jurisdiction: str,
    scope: str,
    retrieved: str,
    source_file: str,
    source_sha256: str,
    converter: str,
    licence: str,
    row_provenance: str | None = None,
) -> dict[str, str]:
    block = {
        "citation": citation,
        "edition": edition,
        "url": url,
        "jurisdiction": jurisdiction,
        "scope": scope,
        "retrieved": retrieved,
        "source_file": source_file,
        "source_sha256": source_sha256,
        "converter": converter,
        "licence": licence,
    }
    if row_provenance:
        block["row_provenance"] = row_provenance
    return block
