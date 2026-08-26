#!/usr/bin/env python3
"""Independent Python MF=8/MT=457 spectrum reader and P7 integration controls.

This intentionally does not call either Rust parser or OpenMC's decay reader. It is the
second implementation used by P7 G1/G2/G4.
"""
from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections import Counter

_ENDF = re.compile(r"^\s*([+-]?\d*\.?\d*)([+-]\d+)\s*$")


def endf_float(value: str) -> float:
    value = value.strip()
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        match = _ENDF.match(value)
        if not match:
            raise
        return float(match.group(1) + "e" + match.group(2))


def fields(line: str) -> list[str]:
    return [line[index * 11 : (index + 1) * 11] for index in range(6)]


def read_list(lines: list[str], index: int) -> tuple[tuple, int]:
    head = fields(lines[index])
    c1, c2 = endf_float(head[0]), endf_float(head[1])
    l1, l2, n1, n2 = map(int, head[2:6])
    index += 1
    values: list[float] = []
    while len(values) < n1:
        for value in fields(lines[index]):
            if len(values) == n1:
                break
            values.append(endf_float(value))
        index += 1
    return (c1, c2, l1, l2, n1, n2, values), index


def read_tab1(lines: list[str], index: int) -> tuple[tuple, int]:
    head = fields(lines[index])
    c1, c2 = endf_float(head[0]), endf_float(head[1])
    l1, l2, nr, np = map(int, head[2:6])
    index += 1
    ranges: list[tuple[int, int]] = []
    while len(ranges) < nr:
        row = fields(lines[index])
        for k in range(0, 6, 2):
            if len(ranges) < nr:
                ranges.append((int(row[k]), int(row[k + 1])))
        index += 1
    points: list[tuple[float, float]] = []
    while len(points) < np:
        row = fields(lines[index])
        for k in range(0, 6, 2):
            if len(points) < np:
                points.append((endf_float(row[k]), endf_float(row[k + 1])))
        index += 1
    return (c1, c2, l1, l2, ranges, points), index


def sections(path: str):
    current = None
    buffered: list[str] = []
    with open(path, errors="replace") as stream:
        for raw in stream:
            line = raw.rstrip("\n")
            if len(line) < 75:
                continue
            try:
                tail = (int(line[66:70]), int(line[70:72]), int(line[72:75]))
            except ValueError:
                continue
            if tail[1:] == (8, 457):
                if tail != current:
                    if current is not None and buffered:
                        yield current[0], buffered
                    current, buffered = tail, []
                buffered.append(line)
            elif current is not None and buffered:
                yield current[0], buffered
                current, buffered = None, []
    if current is not None and buffered:
        yield current[0], buffered


def parse_section(mat: int, lines: list[str]) -> dict:
    head = fields(lines[0])
    za = round(endf_float(head[0]))
    liso, nst, nsp = map(int, head[3:6])
    (half_life, d_half_life, _, _, n_energy, _, energy_values), index = read_list(lines, 1)
    (_, _, _, _, _, n_modes, mode_values), index = read_list(lines, index)
    modes = [
        {
            "rtyp": mode_values[6 * k],
            "rfs": mode_values[6 * k + 1],
            "q": mode_values[6 * k + 2],
            "dq": mode_values[6 * k + 3],
            "br": mode_values[6 * k + 4],
            "dbr": mode_values[6 * k + 5],
        }
        for k in range(n_modes)
    ]
    spectra = []
    for _ in range(nsp):
        (_, styp, lcon, lcov, _, n_discrete, norm), index = read_list(lines, index)
        discrete = []
        if lcon != 1:
            for _ in range(n_discrete):
                (energy, d_energy, _, _, _, _, values), index = read_list(lines, index)
                values = values + [0.0] * (12 - len(values))
                discrete.append(
                    {
                        "energy": energy,
                        "d_energy": d_energy,
                        "fields": values[:12],
                    }
                )
        continuous = None
        if lcon != 0:
            (rtyp, _, _, _, ranges, points), index = read_tab1(lines, index)
            continuous = {"rtyp": rtyp, "ranges": ranges, "points": points}
        if lcov in (1, 3) and lcon != 0:
            _, index = read_list(lines, index)
        if lcov in (2, 3):
            _, index = read_list(lines, index)
        norm = norm + [0.0] * (6 - len(norm))
        spectra.append(
            {
                "styp": styp,
                "lcon": lcon,
                "lcov": lcov,
                "norm": norm[:6],
                "discrete": discrete,
                "continuous": continuous,
            }
        )
    if index != len(lines):
        raise ValueError(f"MAT {mat}: consumed {index} of {len(lines)} MF=8/MT=457 records")
    return {
        "mat": mat,
        "za": za,
        "liso": liso,
        "nst": nst,
        "half_life": half_life,
        "d_half_life": d_half_life,
        "energies": energy_values[:n_energy],
        "modes": modes,
        "spectra": spectra,
    }


def parse_file(path: str) -> dict[tuple[int, int], dict]:
    records = {}
    for mat, lines in sections(path):
        record = parse_section(mat, lines)
        key = (record["za"], record["liso"])
        if key in records:
            raise ValueError(f"duplicate decay record {key}")
        records[key] = record
    return records


def audit(records: dict[tuple[int, int], dict]) -> dict:
    counts = Counter()
    spectra = 0
    for record in records.values():
        spectra += len(record["spectra"])
        counts.update((round(spectrum["styp"]), spectrum["lcon"]) for spectrum in record["spectra"])
    return {
        "sections": len(records),
        "spectra": spectra,
        "styp_lcon": {f"{styp}:{lcon}": count for (styp, lcon), count in sorted(counts.items())},
    }


def segment_moments(x0: float, y0: float, x1: float, y1: float, law: int, a: float, b: float):
    """Independent analytic integral of y and E*y over one ENDF interpolation segment."""
    if law == 1:
        return y0 * (b - a), 0.5 * y0 * (b * b - a * a)
    if law == 2:
        slope = (y1 - y0) / (x1 - x0)
        intercept = y0 - slope * x0
        return (
            0.5 * slope * (b * b - a * a) + intercept * (b - a),
            slope / 3.0 * (b**3 - a**3) + 0.5 * intercept * (b * b - a * a),
        )
    if law == 3:
        q = (y1 - y0) / math.log(x1 / x0)
        p = y0 - q * math.log(x0)
        f0 = lambda x: p * x + q * (x * math.log(x) - x)
        f1 = lambda x: 0.5 * p * x * x + q * (0.5 * x * x * math.log(x) - 0.25 * x * x)
        return f0(b) - f0(a), f1(b) - f1(a)
    if law == 4:
        k = math.log(y1 / y0) / (x1 - x0)
        ya, width = y0 * math.exp(k * (a - x0)), b - a
        if abs(k) < 1e-14:
            return ya * width, 0.5 * ya * (b * b - a * a)
        exp_width = math.exp(k * width)
        count = ya * (exp_width - 1.0) / k
        local_moment = ya * (exp_width * (k * width - 1.0) + 1.0) / (k * k)
        return count, a * count + local_moment
    if law == 5:
        power = math.log(y1 / y0) / math.log(x1 / x0)
        ya, ratio = y0 * (a / x0) ** power, b / a
        integral = lambda exponent: math.log(ratio) if abs(exponent) < 1e-12 else (ratio**exponent - 1.0) / exponent
        return ya * a * integral(power + 1.0), ya * a * a * integral(power + 2.0)
    raise ValueError(f"unsupported interpolation law {law}")


def raw_photon_moments(record: dict, styp=(0, 9)) -> tuple[float, float]:
    count = energy = 0.0
    for spectrum in record["spectra"]:
        if round(spectrum["styp"]) not in styp:
            continue
        fd, fc = spectrum["norm"][0], spectrum["norm"][4]
        for line in spectrum["discrete"]:
            photons = fd * line["fields"][2]
            count += photons
            energy += photons * line["energy"]
        continuum = spectrum["continuous"]
        if continuum:
            ranges = continuum["ranges"]
            for index, ((x0, y0), (x1, y1)) in enumerate(zip(continuum["points"], continuum["points"][1:])):
                law = next(law for nbt, law in ranges if index + 2 <= nbt)
                photons, moment = segment_moments(x0, y0, x1, y1, law, x0, x1)
                count += fc * photons
                energy += fc * moment
    return count, energy


def photon_shape(record: dict, boundaries: list[float], styp=(0, 9)) -> dict:
    """Independently collapse evaluated photons, then apply the P7 E_EM normalization."""
    group_count = [0.0] * (len(boundaries) - 1)
    group_moment = [0.0] * (len(boundaries) - 1)
    under_count = under_moment = over_count = over_moment = 0.0
    raw_count = raw_moment = 0.0

    def accumulate(a: float, b: float, count: float, moment: float):
        nonlocal under_count, under_moment, over_count, over_moment
        midpoint = 0.5 * (a + b)
        if b <= boundaries[0]:
            under_count += count
            under_moment += moment
        elif a >= boundaries[-1]:
            over_count += count
            over_moment += moment
        else:
            group = min(bisect_right(boundaries, midpoint) - 1, len(group_count) - 1)
            group_count[group] += count
            group_moment[group] += moment

    for spectrum in record["spectra"]:
        if round(spectrum["styp"]) not in styp:
            continue
        fd, fc = spectrum["norm"][0], spectrum["norm"][4]
        for line in spectrum["discrete"]:
            photons = fd * line["fields"][2]
            moment = photons * line["energy"]
            raw_count += photons
            raw_moment += moment
            if line["energy"] < boundaries[0]:
                under_count += photons
                under_moment += moment
            elif line["energy"] > boundaries[-1]:
                over_count += photons
                over_moment += moment
            else:
                group = min(bisect_right(boundaries, line["energy"]) - 1, len(group_count) - 1)
                group_count[group] += photons
                group_moment[group] += moment
        continuum = spectrum["continuous"]
        if not continuum:
            continue
        ranges = continuum["ranges"]
        for index, ((x0, y0), (x1, y1)) in enumerate(zip(continuum["points"], continuum["points"][1:])):
            law = next(law for nbt, law in ranges if index + 2 <= nbt)
            photons, moment = segment_moments(x0, y0, x1, y1, law, x0, x1)
            raw_count += fc * photons
            raw_moment += fc * moment
            cuts = [x0, *(edge for edge in boundaries if x0 < edge < x1), x1]
            for a, b in zip(cuts, cuts[1:]):
                part_count, part_moment = segment_moments(x0, y0, x1, y1, law, a, b)
                accumulate(a, b, fc * part_count, fc * part_moment)

    e_em = record["energies"][2] if len(record["energies"]) > 2 else 0.0
    scale = e_em / raw_moment if e_em > 0.0 and raw_moment > 0.0 else 1.0
    return {
        "raw_count": raw_count,
        "raw_moment_eV": raw_moment,
        "scale": scale,
        "source_count": raw_count * scale,
        "source_moment_eV": raw_moment * scale,
        "group_count": [value * scale for value in group_count],
        "group_moment_eV": [value * scale for value in group_moment],
        "under_count": under_count * scale,
        "under_moment_eV": under_moment * scale,
        "over_count": over_count * scale,
        "over_moment_eV": over_moment * scale,
    }
