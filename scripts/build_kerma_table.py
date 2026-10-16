#!/usr/bin/env python3
"""Convert NJOY heating kerma into an ACTINV ``actinv-kerma-table-1`` JSON.

Two inputs are understood, both ENDF-6 formatted tapes written by NJOY2016:

* ``--gendf``: GROUPR output (GENDF). MF=3 MT=301 (total heating kerma, written by HEATR into
  the PENDF and group-averaged by GROUPR) and, with ``--damage-energy``, MT=444 (total damage
  energy production). The group structure is the tape's own (MF=1/MT=451 ``EGN``). GROUPR must
  have been run with one Legendre order and one sigma0 (``lord=0``, ``nsigz=1``), one
  temperature per tape.
* ``--pendf``: a HEATR-processed PENDF. MF=3 MT=301 (and MT=444) are pointwise, linearly
  interpolated; they are collapsed onto the group boundaries of ``--boundaries-from`` (any JSON
  with a ``boundaries_eV`` list, e.g. an existing damage table or the activation library's
  group file) with a flat-in-lethargy (1/E) weight, the same weighting ``actinv build-damage``
  uses for MT=444. Outside the section's energy range the group value is zero.

Values are NJOY eV-barns per atom, the unit of the table (``kerma_barn_eV_per_group``). Nothing
is estimated: a material without the requested MT is listed under ``uncovered`` and absent from
``targets``. This script only reformats and collapses data NJOY wrote; it does not run NJOY.

Example (not run by the test suite)::

    build_kerma_table.py fe56_kerma.json --pendf fe56.pendf \\
        --boundaries-from damage_table.json --group-structure fispact-709 \\
        --temperature-K 293.6 --damage-energy
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

FORMAT = "actinv-kerma-table-1"
UNITS = "kerma_barn_eV_per_group"
SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As "
    "Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd "
    "Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am "
    "Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
).split()
EXPONENT = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))([+-]\d+)$")


class TapeError(ValueError):
    """The input is not the ENDF-6 structure this converter understands."""


def endf_float(field: str) -> float:
    """Parse an 11-character ENDF float, including the exponent-without-E form ``1.5+3``."""
    text = field.strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    match = EXPONENT.match(text)
    if not match:
        raise TapeError(f"unparseable ENDF number {field!r}")
    return float(f"{match.group(1)}e{match.group(2)}")


def endf_int(field: str) -> int:
    text = field.strip()
    return int(float(text)) if text else 0


class Section:
    """The data lines (columns 1-66) of one MAT/MF/MT section."""

    def __init__(self, block: int, mat: int, mf: int, mt: int) -> None:
        self.block = block
        self.mat = mat
        self.mf = mf
        self.mt = mt
        self.lines: list[str] = []


def read_tape(text: str) -> list[Section]:
    """Split a tape into sections. A new ``block`` starts at every MF=1/MT=451 section, so a
    tape holding several temperatures of one material keeps them apart."""
    sections: list[Section] = []
    current: Section | None = None
    block = -1
    for number, raw in enumerate(text.splitlines(), start=1):
        if len(raw) < 75:
            continue
        mat, mf, mt = raw[66:70].strip(), raw[70:72].strip(), raw[72:75].strip()
        if not (mat.lstrip("-").isdigit() and mf.isdigit() and mt.isdigit()):
            continue
        mat_i, mf_i, mt_i = int(mat), int(mf), int(mt)
        if mat_i <= 0 or mf_i == 0 or mt_i == 0:
            current = None  # SEND / FEND / MEND / TEND / TPID
            continue
        if current is None or (current.mat, current.mf, current.mt) != (mat_i, mf_i, mt_i):
            if (mf_i, mt_i) == (1, 451):
                block += 1
            current = Section(block, mat_i, mf_i, mt_i)
            sections.append(current)
        current.lines.append(raw[:66].ljust(66))
    return sections


def words(lines: list[str], start: int, count: int) -> tuple[list[str], int]:
    """``count`` 11-character words from consecutive lines at ``start``; returns the next line."""
    out: list[str] = []
    index = start
    while len(out) < count:
        if index >= len(lines):
            raise TapeError("record runs past the end of its section")
        line = lines[index]
        take = min(6, count - len(out))
        out.extend(line[11 * k : 11 * (k + 1)] for k in range(take))
        index += 1
    return out, index


def cont(lines: list[str], index: int) -> tuple[tuple[float, float, int, int, int, int], int]:
    if index >= len(lines):
        raise TapeError("missing CONT record")
    f = [lines[index][11 * k : 11 * (k + 1)] for k in range(6)]
    return (
        endf_float(f[0]),
        endf_float(f[1]),
        endf_int(f[2]),
        endf_int(f[3]),
        endf_int(f[4]),
        endf_int(f[5]),
    ), index + 1


def za_name(za: float, liso: int) -> str:
    za_i = int(round(za))
    z, a = za_i // 1000, za_i % 1000
    if not 1 <= z <= len(SYMBOLS):
        raise TapeError(f"ZA {za_i} has no element symbol")
    symbol = SYMBOLS[z - 1]
    if a == 0:
        return symbol  # natural element
    return f"{symbol}{a}m{liso}" if liso > 0 else f"{symbol}{a}"


def tab1(section: Section) -> tuple[list[tuple[int, int]], list[float], list[float]]:
    """Interpolation table ``[(NBT, INT)]`` and the (x, y) points of a TAB1 section."""
    lines = section.lines
    _, index = cont(lines, 0)  # HEAD: ZA, AWR, ...
    (_, _, _, _, nr, np_), index = cont(lines, index)
    raw, index = words(lines, index, 2 * nr)
    interpolation = [(endf_int(raw[2 * k]), endf_int(raw[2 * k + 1])) for k in range(nr)]
    raw, index = words(lines, index, 2 * np_)
    values = [endf_float(w) for w in raw]
    return interpolation, values[0::2], values[1::2]


def collapse_linlin(
    boundaries: list[float], interpolation: list[tuple[int, int]], x: list[float], y: list[float]
) -> list[float]:
    """Flat-in-lethargy group averages of a lin-lin tabulated function; zero outside its range."""
    if any(law != 2 for _, law in interpolation):
        raise TapeError(
            "only linear-linear (INT=2) pointwise sections are collapsed; "
            f"found interpolation laws {sorted({law for _, law in interpolation})}"
        )
    if any(b < a for a, b in zip(x, x[1:])):
        raise TapeError("pointwise abscissae are not nondecreasing")
    out: list[float] = []
    for low, high in zip(boundaries, boundaries[1:]):
        total = 0.0
        for k in range(len(x) - 1):
            x1, x2 = x[k], x[k + 1]
            if x2 <= x1 or x2 <= low or x1 >= high:
                continue
            lo, hi = max(low, x1), min(high, x2)
            if hi <= lo:
                continue
            slope = (y[k + 1] - y[k]) / (x2 - x1)
            intercept = y[k] - slope * x1
            total += intercept * math.log1p((hi - lo) / lo) + slope * (hi - lo)
        out.append(total / math.log1p((high - low) / low))
    return out


class Material:
    def __init__(self, name: str, temperature: float | None) -> None:
        self.name = name
        self.temperature = temperature
        self.mt: dict[int, list[float]] = {}


def pendf_materials(
    sections: list[Section], boundaries: list[float], mts: tuple[int, ...], temperature: float | None
) -> tuple[list[Material], list[str]]:
    by_block: dict[int, dict[tuple[int, int], Section]] = {}
    for section in sections:
        by_block.setdefault(section.block, {})[(section.mf, section.mt)] = section
    materials: list[Material] = []
    uncovered: list[str] = []
    for block in sorted(by_block):
        head = by_block[block].get((1, 451))
        if head is None:
            continue
        (za, _, _, _, _, _), index = cont(head.lines, 0)
        (_, _, _, liso, _, _), index = cont(head.lines, index)  # ELIS, STA, LIS, LISO, 0, NFOR
        _, index = cont(head.lines, index)  # AWI, EMAX, LREL, 0, NSUB, NVER
        (block_temperature, *_), index = cont(head.lines, index)  # TEMP, 0, LDRV, 0, NWD, NXC
        name = za_name(za, liso)
        if temperature is not None and not math.isclose(
            block_temperature, temperature, rel_tol=1e-9, abs_tol=1e-9
        ):
            continue
        material = Material(name, block_temperature)
        for mt in mts:
            section = by_block[block].get((3, mt))
            if section is None:
                continue
            interpolation, x, y = tab1(section)
            material.mt[mt] = collapse_linlin(boundaries, interpolation, x, y)
        if 301 in material.mt:
            materials.append(material)
        else:
            uncovered.append(name)
    return materials, uncovered


def gendf_materials(
    sections: list[Section], mts: tuple[int, ...], temperature: float | None
) -> tuple[list[float], list[Material], list[str]]:
    by_block: dict[int, dict[tuple[int, int], Section]] = {}
    for section in sections:
        by_block.setdefault(section.block, {})[(section.mf, section.mt)] = section
    boundaries: list[float] | None = None
    materials: list[Material] = []
    uncovered: list[str] = []
    for block in sorted(by_block):
        head = by_block[block].get((1, 451))
        if head is None:
            continue
        # HEAD: ZA, AWR, 0, NZ, -1, NTW ; LIST: TEMP, 0, NGN, NGG, NW, 0 then
        # TITLE (NTW words), SIGZ (NZ), EGN (NGN+1), EGG (NGG+1)
        (za, _, _, nz, _, ntw), index = cont(head.lines, 0)
        (block_temperature, _, ngn, ngg, nw, _), index = cont(head.lines, index)
        raw, _ = words(head.lines, index, nw)
        if nw < ntw + nz + ngn + 1 + ngg + 1 - (1 if ngg == 0 else 0):
            raise TapeError("MF=1/MT=451 list is shorter than its declared contents")
        egn = [endf_float(w) for w in raw[ntw + nz : ntw + nz + ngn + 1]]
        if any(not math.isfinite(e) or e <= 0.0 for e in egn) or any(
            b <= a for a, b in zip(egn, egn[1:])
        ):
            raise TapeError("group boundaries (EGN) are not positive and strictly ascending")
        if boundaries is not None and boundaries != egn:
            raise TapeError("materials on one tape use different group structures")
        boundaries = egn
        if nz != 1:
            raise TapeError(f"GENDF carries {nz} sigma0 values; rerun GROUPR with nsigz=1")
        if temperature is not None and not math.isclose(
            block_temperature, temperature, rel_tol=1e-9, abs_tol=1e-9
        ):
            continue
        name = za_name(za, 0)  # GENDF carries no isomer designation
        material = Material(name, block_temperature)
        for mt in mts:
            section = by_block[block].get((3, mt))
            if section is None:
                continue
            material.mt[mt] = gendf_groups(section, ngn)
        if 301 in material.mt:
            materials.append(material)
        else:
            uncovered.append(name)
    if boundaries is None:
        raise TapeError("no MF=1/MT=451 section: this is not a GENDF tape")
    return boundaries, materials, uncovered


def gendf_groups(section: Section, ngn: int) -> list[float]:
    """Group-averaged cross sections of one GENDF MF=3 section. Groups GROUPR did not write are
    zero by its own convention (it skips all-zero groups except the last)."""
    lines = section.lines
    (_, _, nl, nz, _, ngi), index = cont(lines, 0)  # ZA, izam, NL, NZ, LRFLAG, NGI
    if nl != 1 or nz != 1:
        raise TapeError(f"MF=3 MT={section.mt} has NL={nl}, NZ={nz}; rerun GROUPR with lord=0, nsigz=1")
    values = [0.0] * ngn
    seen: set[int] = set()
    while index < len(lines):
        (_, _, ng2, _, lim, group), index = cont(lines, index)  # TEMP, 0, NG2, IG2LO, LIM, IG
        raw, index = words(lines, index, lim)
        if ng2 != 2 or lim != 2:
            raise TapeError(f"MF=3 MT={section.mt} group {group} has NG2={ng2}, expected 2")
        if not 1 <= group <= ngn or group in seen:
            raise TapeError(f"MF=3 MT={section.mt} has bad or repeated group index {group}")
        seen.add(group)
        values[group - 1] = endf_float(raw[1])  # [flux, cross section]
    return values


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_table(
    materials: list[Material],
    boundaries: list[float],
    uncovered: list[str],
    args: argparse.Namespace,
    files: list[dict[str, str]],
    temperature: float | None,
) -> dict:
    targets: dict[str, list[float]] = {}
    damage: dict[str, list[float]] = {}
    for material in materials:
        if material.name in targets:
            raise TapeError(f"two materials map to the target {material.name}")
        targets[material.name] = material.mt[301]
        if 444 in material.mt:
            damage[material.name] = material.mt[444]
    for name, row in targets.items():
        if len(row) != len(boundaries) - 1 or any(not math.isfinite(v) for v in row):
            raise TapeError(f"target {name} has a missing or nonfinite group value")
        if any(v < 0.0 for v in row):
            print(f"warning: {name} MT=301 has negative group values", file=sys.stderr)
    for name, row in damage.items():
        if any(not math.isfinite(v) or v < 0.0 for v in row):
            raise TapeError(f"target {name} MT=444 has a negative or nonfinite group value")
    if damage and len(damage) < len(targets):
        missing = sorted(set(targets) - set(damage))
        print(
            f"warning: {len(missing)} of {len(targets)} targets have no MT=444 damage-energy row "
            f"and will add nothing to damage energy: {', '.join(missing)}",
            file=sys.stderr,
        )
    table: dict = {
        "format": FORMAT,
        "source": {
            "citation": args.citation,
            "edition": args.edition,
            "url": args.url,
        },
        "projectile": args.projectile,
        "group_structure": args.group_structure,
        "boundaries_eV": boundaries,
        "units": UNITS,
        "targets": dict(sorted(targets.items())),
    }
    if damage:
        table["damage_energy_targets"] = dict(sorted(damage.items()))
    if temperature is not None:
        table["temperature_K"] = temperature
    table["files"] = files
    table["uncovered"] = sorted(uncovered)
    return table


def load_boundaries(path: Path) -> list[float]:
    boundaries = json.loads(path.read_text())["boundaries_eV"]
    if (
        len(boundaries) < 2
        or any(not isinstance(b, (int, float)) or b <= 0 for b in boundaries)
        or any(b <= a for a, b in zip(boundaries, boundaries[1:]))
    ):
        raise TapeError(f"{path}: boundaries_eV must be positive and strictly ascending")
    return [float(b) for b in boundaries]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("output", type=Path, help="kerma table JSON to create")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--gendf", type=Path, nargs="+", help="GROUPR output tape(s)")
    source.add_argument("--pendf", type=Path, nargs="+", help="HEATR-processed PENDF tape(s)")
    parser.add_argument(
        "--boundaries-from",
        type=Path,
        help="JSON with boundaries_eV to collapse a PENDF onto (required with --pendf)",
    )
    parser.add_argument("--group-structure", required=True, help="name recorded in the table")
    parser.add_argument("--projectile", default="neutron")
    parser.add_argument(
        "--temperature-K",
        type=float,
        help="select this temperature when a tape holds several; recorded in the table",
    )
    parser.add_argument("--damage-energy", action="store_true", help="also convert MT=444")
    parser.add_argument(
        "--citation",
        default="NJOY2016 HEATR total heating kerma (MT=301) and damage energy production (MT=444), eV-barn",
    )
    parser.add_argument("--edition", help="evaluation or processing label (default: input file names)")
    parser.add_argument("--url", help="where the processed tapes came from (default: input paths)")
    args = parser.parse_args(argv)

    inputs: list[Path] = args.gendf or args.pendf
    mts = (301, 444) if args.damage_energy else (301,)
    args.edition = args.edition or ", ".join(p.name for p in inputs)
    args.url = args.url or ", ".join(str(p) for p in inputs)
    boundaries: list[float] | None = None
    if args.pendf:
        if not args.boundaries_from:
            parser.error("--pendf needs --boundaries-from")
        boundaries = load_boundaries(args.boundaries_from)

    materials: list[Material] = []
    uncovered: list[str] = []
    files: list[dict[str, str]] = []
    try:
        for path in inputs:
            sections = read_tape(path.read_text())
            if args.gendf:
                tape_boundaries, found, missing = gendf_materials(sections, mts, args.temperature_K)
                if boundaries is not None and boundaries != tape_boundaries:
                    raise TapeError(f"{path}: group structure differs from the other tapes")
                boundaries = tape_boundaries
            else:
                assert boundaries is not None
                found, missing = pendf_materials(sections, boundaries, mts, args.temperature_K)
            materials.extend(found)
            uncovered.extend(missing)
            files.append({"path": path.name, "sha256": sha256_file(path)})
        if not materials:
            raise TapeError("no material carried MF=3/MT=301 at the requested temperature")
        temperatures = {m.temperature for m in materials}
        temperature = args.temperature_K
        if temperature is None and len(temperatures) == 1:
            (only,) = temperatures
            temperature = only
        table = build_table(materials, boundaries, uncovered, args, files, temperature)
    except TapeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(table, indent=1) + "\n")
    print(
        f"wrote {args.output}: {len(table['targets'])} targets, "
        f"{len(table.get('damage_energy_targets', {}))} damage-energy targets, "
        f"{len(table['uncovered'])} uncovered; sha256 {sha256_file(args.output)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
