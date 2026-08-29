#!/usr/bin/env python3
"""P17 IRDFF-II diagnostic-table parser and independent spectrum folders.

Only the open diagnostic pages (Tables 18--20) are addressed here.  The held-
out table numbers are intentionally absent from this module so importing or
testing it cannot unseal H1--H3.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
import zipfile

import numpy as np

from endf_common import fields, interp_eval, read_tab1, sections


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("ACTINV_P17_IRDFF", Path.home() / "nuclear-data" / "p17-irdff"))
PDF = DATA_ROOT / "IRDFF-II_primary_1909.03336.pdf"
GROUP_ARCHIVE = DATA_ROOT / "IRDFF-II_g725.zip"
SPECTRUM_ARCHIVE = DATA_ROOT / "IRDFF-II_sp_g.zip"
PRODUCTION_LIBRARY = Path(
    os.environ.get(
        "ACTINV_LIBRARY",
        Path.home() / "nuclear-data" / "tendl-2025" / "builds" / "full" / "neutron.n.p10.npz",
    )
)
PRODUCTION_INDEX = PRODUCTION_LIBRARY.with_name(PRODUCTION_LIBRARY.stem + "_index.json")

EXPECTED_HASHES = {
    "pdf": "ba2cd81b9a829368bb4d7a37de26842439ad437b0424586dcbd41074d7552d5f",
    "irdff_group_archive": "6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85",
    "irdff_spectrum_archive": "544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52",
    "production_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "production_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
}
EXPECTED_ROWS = {18: 44, 19: 26, 20: 54}
TABLE_PAGES = {18: 72, 19: 73, 20: 74}
TABLE_SPECTRUM_MAT = {18: 9861, 19: 9861, 20: 9228}

ELEMENT_Z = {
    "H": 1,
    "Li": 3,
    "B": 5,
    "F": 9,
    "Na": 11,
    "Mg": 12,
    "Al": 13,
    "Si": 14,
    "P": 15,
    "S": 16,
    "Ti": 22,
    "V": 23,
    "Mn": 25,
    "Fe": 26,
    "Co": 27,
    "Ni": 28,
    "Cu": 29,
    "Zn": 30,
    "As": 33,
    "Y": 39,
    "Zr": 40,
    "Nb": 41,
    "Mo": 42,
    "Rh": 45,
    "In": 49,
    "I": 53,
    "Tm": 69,
    "Ta": 73,
    "Au": 79,
    "Hg": 80,
    "Pb": 82,
    "Th": 90,
    "U": 92,
    "Np": 93,
    "Pu": 94,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def checked_inputs() -> dict[str, str]:
    paths = {
        "pdf": PDF,
        "irdff_group_archive": GROUP_ARCHIVE,
        "irdff_spectrum_archive": SPECTRUM_ARCHIVE,
        "production_library": PRODUCTION_LIBRARY,
        "production_index": PRODUCTION_INDEX,
    }
    identities = {}
    for role, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing P17 IRDFF input {role}: {path}")
        actual = sha256(path)
        if actual != EXPECTED_HASHES[role]:
            raise RuntimeError(
                f"P17 IRDFF input changed for {role}: {actual}, expected {EXPECTED_HASHES[role]}"
            )
        identities[role] = actual
    return identities


def extract_exact(archive: Path, expected_member: str, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if [member.filename for member in members] != [expected_member]:
            raise RuntimeError(
                f"{archive.name}: expected only {expected_member!r}, got "
                f"{[member.filename for member in members]!r}"
            )
        member = members[0]
        if member.is_dir() or Path(member.filename).is_absolute() or ".." in Path(member.filename).parts:
            raise RuntimeError(f"unsafe archive member {member.filename!r}")
        output = destination / expected_member
        with source.open(member) as incoming, output.open("wb") as outgoing:
            while block := incoming.read(1024 * 1024):
                outgoing.write(block)
    return output


def table_text(table: int) -> str:
    page = TABLE_PAGES[table]
    completed = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"pdftotext failed for page {page}: {completed.stderr}")
    return completed.stdout


def parse_table(table: int) -> list[dict[str, Any]]:
    rows = []
    label_pattern = re.compile(r"^\s*([A-Z][A-Za-z]*\d+[A-Za-z0-9]*|BH3)\s+")
    for line in table_text(table).splitlines():
        match = label_pattern.match(line)
        if match is None:
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            e50 = float(parts[1])
            measured = float(parts[2])
            uncertainty = float(parts[3])
        except ValueError:
            continue
        calculated_match = re.search(r"\]\s*([0-9]+(?:\.[0-9]*)?(?:[Ee][+-]?\d+)?)", line)
        reference_match = re.search(r"\[[^\]]+\]", line)
        if calculated_match is None or reference_match is None:
            raise ValueError(f"Table {table}: cannot parse reference/calculation from {line!r}")
        rows.append(
            {
                "table": table,
                "table_row": len(rows) + 1,
                "label": parts[0],
                "E50_MeV": e50,
                "measured_mb": measured,
                "experimental_uncertainty_percent": uncertainty,
                "reference": reference_match.group(0),
                "published_calculated_mb": float(calculated_match.group(1)),
                "source_line": " ".join(line.split()),
            }
        )
    if len(rows) != EXPECTED_ROWS[table]:
        raise RuntimeError(
            f"Table {table}: parsed {len(rows)} rows, expected {EXPECTED_ROWS[table]}"
        )
    return rows


def reaction_mapping(label: str) -> dict[str, Any]:
    special = {
        "BH3": (5000, 205, None, None),
        "B10H3": (5010, 205, None, None),
        "B10He4": (5010, 207, None, None),
        "Li6He4": (3006, 207, None, None),
        # Table 20 shortens the same IRDFF-II state-specific Nb-92m
        # response written as Nb932m in Tables 18--19 to ``Nb932``.
        "Nb932": (41093, 16, 1, 41092),
    }
    if label in special:
        target_za, mt, state, product_za = special[label]
        return {
            "target_za": target_za,
            "mt": mt,
            "product_lfs": state,
            "product_za": product_za,
            "grammar": "special-light-particle",
        }

    parsed = None
    for suffix in ("gm", "pm", "2m", "nm", "g", "f", "p", "a", "2", "3", "m", "n"):
        if not label.endswith(suffix):
            continue
        prefix = label[: -len(suffix)]
        match = re.fullmatch(r"([A-Z][a-z]?)(\d+)", prefix)
        if match is not None:
            parsed = (*match.groups(), suffix)
            break
    if parsed is None:
        raise ValueError(f"unmapped IRDFF reaction notation {label!r}")
    element, mass_text, suffix = parsed
    if element not in ELEMENT_Z:
        raise ValueError(f"unknown element in IRDFF notation {label!r}")
    z = ELEMENT_Z[element]
    mass = int(mass_text)
    mt_by_suffix = {
        "g": 102,
        "gm": 102,
        "f": 18,
        "p": 103,
        "pm": 103,
        "a": 107,
        "2": 16,
        "2m": 16,
        "3": 17,
        "m": 4,
        "nm": 4,
        "n": 4,
    }
    mt = mt_by_suffix[suffix]
    state = 1 if suffix in {"gm", "pm", "2m", "m", "nm", "n"} else None
    product_za = None
    if state is not None:
        if mt == 102:
            product_za = z * 1000 + mass + 1
        elif mt == 103:
            product_za = (z - 1) * 1000 + mass
        elif mt == 16:
            product_za = z * 1000 + mass - 1
        elif mt == 4:
            product_za = z * 1000 + mass
    return {
        "target_za": z * 1000 + mass,
        "mt": mt,
        "product_lfs": state,
        "product_za": product_za,
        "grammar": "element-mass-reaction-suffix-1",
    }


def endf_catalog(path: Path) -> tuple[dict[tuple[int, int], tuple[Any, ...]], dict[tuple[int, int, int, int], tuple[Any, ...]]]:
    mf3: dict[tuple[int, int], tuple[Any, ...]] = {}
    mf10: dict[tuple[int, int, int, int], tuple[Any, ...]] = {}
    for (_, mf, mt), lines in sections(path):
        if mf not in {3, 10} or mt == 261:
            continue
        target_za = int(float(fields(lines[0])[0]))
        if mf == 3:
            record, _ = read_tab1(lines, 1)
            key = (target_za, mt)
            if key in mf3:
                raise ValueError(f"duplicate IRDFF MF3 key {key}")
            mf3[key] = record
            continue
        subsection_count = int(fields(lines[0])[4])
        offset = 1
        for _ in range(subsection_count):
            record, offset = read_tab1(lines, offset)
            product_za = int(record[2])
            lfs = int(record[3])
            key = (target_za, mt, product_za, lfs)
            if key in mf10:
                raise ValueError(f"duplicate IRDFF MF10 key {key}")
            mf10[key] = record
    return mf3, mf10


def spectrum_catalog(path: Path) -> dict[int, tuple[Any, ...]]:
    spectra = {}
    for (mat, mf, mt), lines in sections(path):
        if mf == 3 and mt == 261 and mat in set(TABLE_SPECTRUM_MAT.values()):
            record, _ = read_tab1(lines, 1)
            spectra[mat] = record
    if set(spectra) != set(TABLE_SPECTRUM_MAT.values()):
        raise RuntimeError(f"missing diagnostic spectra: {sorted(spectra)}")
    return spectra


def fold_histograms(xs_record: tuple[Any, ...], spectrum_record: tuple[Any, ...]) -> float:
    _, _, _, _, _, _, xs_nbt, xs_x, xs_y = xs_record
    _, _, _, _, _, _, spectrum_nbt, spectrum_x, spectrum_y = spectrum_record
    if any(law != 1 for _, law in spectrum_nbt):
        raise ValueError("P17 diagnostic spectra must use histogram interpolation")
    energy = np.asarray(spectrum_x, dtype=float)
    phi = np.asarray(spectrum_y, dtype=float)
    if len(energy) < 2 or len(phi) != len(energy) or not np.all(np.diff(energy) > 0.0):
        raise ValueError("invalid diagnostic spectrum grid")
    sigma = interp_eval(
        np.asarray(xs_x, dtype=float),
        np.asarray(xs_y, dtype=float),
        xs_nbt,
        energy[:-1],
    )
    weights = phi[:-1] * np.diff(energy)
    denominator = float(np.sum(weights))
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise ValueError("diagnostic spectrum has nonpositive normalization")
    return float(np.dot(sigma, weights) / denominator)


def histogram_cumulative(record: tuple[Any, ...], query: np.ndarray) -> np.ndarray:
    _, _, _, _, _, _, nbt, x_values, y_values = record
    if any(law != 1 for _, law in nbt):
        raise ValueError("spectrum rebin requires histogram interpolation")
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    interval = y[:-1] * np.diff(x)
    cumulative = np.concatenate(([0.0], np.cumsum(interval)))
    clipped = np.clip(query, x[0], x[-1])
    index = np.searchsorted(x, clipped, side="right") - 1
    index = np.clip(index, 0, len(x) - 2)
    values = cumulative[index] + y[index] * (clipped - x[index])
    values = np.where(query <= x[0], 0.0, values)
    values = np.where(query >= x[-1], cumulative[-1], values)
    return values


class ProductionLibrary:
    def __init__(self, library: Path, index_path: Path):
        index = json.loads(index_path.read_text(encoding="utf-8"))
        archive = np.load(library, allow_pickle=False)
        self.rows = np.asarray(archive["rows"], dtype=np.int64)
        self.sigma = np.asarray(archive["sig"], dtype=float)
        self.bounds = np.asarray(archive["bounds"], dtype=float)
        if self.rows.shape[0] != self.sigma.shape[0] or self.sigma.shape[1] + 1 != len(self.bounds):
            raise ValueError("production library row/group dimensions disagree")
        if not np.all(np.diff(self.bounds) > 0.0):
            raise ValueError("production library boundaries are not increasing")
        self.target_by_za: dict[int, int] = {}
        for target_index, target in enumerate(index["targets"]):
            if int(target.get("liso", 0)) != 0:
                continue
            za = int(target["za"])
            if za in self.target_by_za:
                raise ValueError(f"duplicate production ground-state target ZA={za}")
            self.target_by_za[za] = target_index

    def spectrum_average(
        self, mapping: dict[str, Any], spectrum: tuple[Any, ...]
    ) -> tuple[float | None, str, dict[str, Any]]:
        target_index = self.target_by_za.get(int(mapping["target_za"]))
        if target_index is None:
            return None, "variant_target_unavailable", {"target_index": None}
        mt = int(mapping["mt"])
        state = mapping["product_lfs"]
        if state is None:
            mask = (
                (self.rows[:, 0] == target_index)
                & (self.rows[:, 1] == mt)
                & (self.rows[:, 2] == -1)
            )
        else:
            mask = (
                (self.rows[:, 0] == target_index)
                & (self.rows[:, 1] == mt)
                & (self.rows[:, 2] == int(mapping["product_za"]))
                & (self.rows[:, 3] == int(state))
            )
        positions = np.flatnonzero(mask)
        if len(positions) != 1:
            return None, "variant_reaction_unavailable", {
                "target_index": target_index,
                "matching_rows": int(len(positions)),
            }
        group_weights = np.diff(histogram_cumulative(spectrum, self.bounds))
        normalization = float(np.sum(group_weights))
        if normalization <= 0.0:
            raise ValueError("rebinned production spectrum has nonpositive normalization")
        position = int(positions[0])
        value = float(np.dot(self.sigma[position], group_weights) / normalization)
        return value, "scored", {
            "target_index": target_index,
            "library_row": [int(value) for value in self.rows[position]],
            "spectrum_integral": normalization,
        }


def diagnostic_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    identities = checked_inputs()
    with tempfile.TemporaryDirectory(prefix="actinv-p17-irdff-g4-") as temporary:
        work = Path(temporary)
        group_file = extract_exact(GROUP_ARCHIVE, "IRDFF-II.g725", work)
        spectrum_file = extract_exact(SPECTRUM_ARCHIVE, "IRDFF-II_sp.g", work)
        mf3, mf10 = endf_catalog(group_file)
        spectra = spectrum_catalog(spectrum_file)
        production = ProductionLibrary(PRODUCTION_LIBRARY, PRODUCTION_INDEX)
        output = []
        for table in sorted(TABLE_PAGES):
            spectrum = spectra[TABLE_SPECTRUM_MAT[table]]
            for source in parse_table(table):
                mapping = reaction_mapping(source["label"])
                state = mapping["product_lfs"]
                if state is None:
                    xs = mf3.get((mapping["target_za"], mapping["mt"]))
                    official_key: list[int] | None = [mapping["target_za"], 3, mapping["mt"]]
                else:
                    key = (
                        mapping["target_za"],
                        mapping["mt"],
                        mapping["product_za"],
                        state,
                    )
                    xs = mf10.get(key)
                    official_key = [mapping["target_za"], 10, mapping["mt"], mapping["product_za"], state]
                # ENDF groupwise cross sections are barns; the publication's
                # SACS columns are millibarns.
                official = 1000.0 * fold_histograms(xs, spectrum) if xs is not None else None
                production_value, production_reason, production_mapping = production.spectrum_average(
                    mapping, spectrum
                )
                if production_value is not None:
                    production_value *= 1000.0
                published_relative = (
                    abs(official - source["published_calculated_mb"])
                    / source["published_calculated_mb"]
                    if official is not None and source["published_calculated_mb"] > 0.0
                    else None
                )
                output.append(
                    {
                        **source,
                        "mapping": mapping,
                        "official_group_key": official_key if xs is not None else None,
                        "official_groupwise_mb": official,
                        "official_vs_published_relative": published_relative,
                        "production_tendl2025_mb": production_value,
                        "production_reason": production_reason,
                        "production_mapping": production_mapping,
                        "spectrum_mat": TABLE_SPECTRUM_MAT[table],
                    }
                )
    published_differences = [
        row["official_vs_published_relative"]
        for row in output
        if row["official_vs_published_relative"] is not None
    ]
    diagnostics = {
        "input_identities": identities,
        "row_counts": {
            str(table): sum(row["table"] == table for row in output)
            for table in sorted(TABLE_PAGES)
        },
        "rows_total": len(output),
        "official_rows_folded": sum(row["official_groupwise_mb"] is not None for row in output),
        "production_rows_folded": sum(row["production_tendl2025_mb"] is not None for row in output),
        "maximum_official_vs_published_relative": max(published_differences),
    }
    return output, diagnostics


if __name__ == "__main__":
    rows, diagnostics = diagnostic_rows()
    print(json.dumps(diagnostics, indent=1, sort_keys=True))
    unavailable = [
        (row["table"], row["label"], row["production_reason"], row["official_group_key"])
        for row in rows
        if row["production_tendl2025_mb"] is None or row["official_groupwise_mb"] is None
    ]
    if unavailable:
        print(json.dumps({"unavailable": unavailable}, indent=1))
