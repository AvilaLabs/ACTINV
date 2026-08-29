#!/usr/bin/env python3
"""Authorized P17 held-out parsers and independent response arithmetic.

This module is intentionally separate from :mod:`p17_irdff`, whose hash was
frozen at the G4 unseal checkpoint.  It implements only the notation and
source handling authorized by P17 Amendment 1.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Iterator

import numpy as np

from endf_common import endf_float, fields, interp_eval, read_tab1, sections
from endf_decay import parse_decay_file
from p17_irdff import (
    ProductionLibrary,
    extract_exact,
    fold_histograms,
    histogram_cumulative,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("ACTINV_P17_IRDFF", Path.home() / "nuclear-data" / "p17-irdff"))
PDF = DATA_ROOT / "IRDFF-II_primary_1909.03336.pdf"
GROUP_ARCHIVE = DATA_ROOT / "IRDFF-II_g725.zip"
SPECTRUM_ARCHIVE = DATA_ROOT / "IRDFF-II_sp_g.zip"
POINTWISE_ARCHIVE = DATA_ROOT / "IRDFF-II_ENDF.zip"
DECAY_ARCHIVE = DATA_ROOT / "IRDFF-II_dd_ENDF.zip"
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
    "irdff_pointwise_archive": "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db",
    "irdff_decay_archive": "397f599ef6389ac84931faa31a8e1f7a1bf3ba684b4a22e92d628d4271699bd7",
    "production_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "production_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
}
TABLE_PAGES = {23: 77, 25: 79, 36: 90}
EXPECTED_ROWS = {23: 40, 25: 33, 36: 21}
FIELD_SPECTRUM_MAT = {23: 9014, 25: 9010}
BASELINE_IRRADIATION_S = 16.0 * 60.0

ELEMENT_Z = {
    "Na": 11,
    "Mg": 12,
    "Al": 13,
    "S": 16,
    "Sc": 21,
    "Ti": 22,
    "Mn": 25,
    "Fe": 26,
    "Co": 27,
    "Ni": 28,
    "Cu": 29,
    "Zn": 30,
    "Zr": 40,
    "Nb": 41,
    "Ag": 47,
    "In": 49,
    "La": 57,
    "Ta": 73,
    "W": 74,
    "Au": 79,
    "U": 92,
    "Np": 93,
    "Pu": 94,
}

# Literal atom fractions from Table 22.  They are deliberately not
# renormalized; the paper notes that contaminants can make the printed total
# differ slightly from one.
FOIL_COMPOSITIONS: dict[str, list[tuple[int, float]]] = {
    "enriched_u": [
        (92235, 0.9300),
        (92234, 0.00981),
        (92236, 0.00359),
        (92238, 0.0566),
    ],
    "depleted_u": [
        (92238, 0.9979),
        (92234, 0.00001),
        (92235, 0.00205),
        (92236, 0.00004),
    ],
    "plutonium": [
        (94239, 0.869965),
        (94238, 0.0006798),
        (94240, 0.115968688),
        (94241, 0.010797),
        (94242, 0.00235936),
        (92235, 0.000199946),
        (93237, 0.00002999),
    ],
}
COMPOSITE_LABELS = {
    "U235f": "enriched_u",
    "rmleu": "enriched_u",
    "U238f": "depleted_u",
    "rmldu": "depleted_u",
    "Pu239f": "plutonium",
    "rmlpu": "plutonium",
}
SUPPORTED_COVER = "bare"
KNOWN_UNSUPPORTED_COVERS = {"Cd", "Cdtk", "Cdtk/B4C", "Cdna"}

SPR_OPERATIONS = [
    {"date": "19/01/2006", "operation": 13090, "power_or_energy": "10 kW, 250 s", "comment": "3x3 grid Foils Set #1"},
    {"date": "19/01/2006", "operation": 13091, "power_or_energy": "10 kW", "comment": "235U foil"},
    {"date": "19/01/2006", "operation": 13092, "power_or_energy": "10 kW", "comment": "239Pu foil"},
    {"date": "26/01/2006", "operation": 13117, "power_or_energy": "5 kW, 275 s", "comment": "237Np foil"},
    {"date": "26/01/2006", "operation": 13118, "power_or_energy": None, "comment": "238U foil"},
    {"date": "27/01/2006", "operation": 13127, "power_or_energy": "125 MJ, 16 min", "comment": "Foil Set #2"},
    {"date": "10/02/2006", "operation": 13156, "power_or_energy": "27.5 MJ, 40 min", "comment": None},
    {"date": "14/04/2006", "operation": 13295, "power_or_energy": "27.5 MJ, 138 min", "comment": "Sc in boron ball"},
    {"date": "14/04/2006", "operation": 13296, "power_or_energy": "127.5 MJ, 217 min", "comment": "Mn in boron ball"},
    {"date": "19/05/2006", "operation": 13368, "power_or_energy": "25 MJ, 32 min", "comment": "Fe in boron ball"},
    {"date": "19/05/2006", "operation": 13369, "power_or_energy": None, "comment": "Ag in boron ball"},
    {"date": "30/06/2006", "operation": 13452, "power_or_energy": "25 MJ, 190 min", "comment": "3x3 grid Foils Set #1"},
]
ACRR_OPERATIONS = [
    {"date": "18/09/2013", "operation": 10636, "type": "Steady", "energy_MJ": 172.4},
    {"date": "19/09/2013", "operation": 10638, "type": "Pulse", "energy_MJ": 157.7},
    {"date": "02/10/2013", "operation": 10649, "type": "Pulse", "energy_MJ": 148.0},
    {"date": "19/09/2013", "operation": 10637, "type": "Pulse", "energy_MJ": 151.5},
    {"date": "03/10/2013", "operation": 10650, "type": "Pulse", "energy_MJ": 152.8},
    {"date": "08/04/2014", "operation": 10870, "type": "Steady", "energy_MJ": 150.0, "approximate": True},
    {"date": "08/04/2014", "operation": 10871, "type": "Steady", "energy_MJ": 150.0, "approximate": True},
    {"date": "08/04/2014", "operation": 10872, "type": "Steady", "energy_MJ": 150.0, "approximate": True},
    {"date": "08/04/2014", "operation": 10873, "type": "Steady", "energy_MJ": 150.0, "approximate": True},
]


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
        "irdff_pointwise_archive": POINTWISE_ARCHIVE,
        "irdff_decay_archive": DECAY_ARCHIVE,
        "production_library": PRODUCTION_LIBRARY,
        "production_index": PRODUCTION_INDEX,
    }
    identities: dict[str, str] = {}
    for role, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing P17 held-out input {role}: {path}")
        actual = sha256(path)
        if actual != EXPECTED_HASHES[role]:
            raise RuntimeError(f"P17 held-out input changed for {role}: {actual}")
        identities[role] = actual
    return identities


@contextmanager
def extracted_inputs() -> Iterator[dict[str, Path]]:
    with tempfile.TemporaryDirectory(prefix="actinv-p17-g5-") as temporary:
        work = Path(temporary)
        yield {
            "group": extract_exact(GROUP_ARCHIVE, "IRDFF-II.g725", work),
            "spectrum": extract_exact(SPECTRUM_ARCHIVE, "IRDFF-II_sp.g", work),
            "pointwise": extract_exact(POINTWISE_ARCHIVE, "IRDFF-II.endf", work),
            "decay": extract_exact(DECAY_ARCHIVE, "IRDFF-II_dd.endf", work),
        }


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
        raise RuntimeError(f"pdftotext failed for Table {table}: {completed.stderr}")
    return completed.stdout


def parse_si_table(table: int) -> list[dict[str, Any]]:
    if table not in {23, 25}:
        raise ValueError(f"not an SI table: {table}")
    rows = []
    for line in table_text(table).splitlines():
        parts = line.split()
        if len(parts) != 9 or "-" not in parts[0]:
            continue
        label = parts[0]
        if re.fullmatch(r"[A-Za-z0-9]+-(?:bare|Cd|Cdna|Cdtk|Cdtk/B4C)", label) is None:
            continue
        try:
            values = [float(value) for value in parts[1:]]
        except ValueError:
            continue
        reaction, cover = label.split("-", 1)
        rows.append(
            {
                "table": table,
                "table_row": len(rows) + 1,
                "label": label,
                "reaction_label": reaction,
                "cover": cover,
                "E50_MeV": values[0],
                "measured_EOI_per_atom": values[1],
                "experimental_uncertainty_percent": values[2],
                "published_SACS_uncertainty_percent": values[3],
                "published_spectral_index": values[4],
                "published_spectral_index_uncertainty_percent": values[5],
                "published_SI_C_over_E": values[6],
                "published_SI_C_over_E_uncertainty_percent": values[7],
                "source_line": " ".join(line.split()),
            }
        )
    if len(rows) != EXPECTED_ROWS[table]:
        raise RuntimeError(f"Table {table}: parsed {len(rows)} rows, expected {EXPECTED_ROWS[table]}")
    return rows


def parse_maxwellian_table() -> list[dict[str, Any]]:
    rows = []
    label_pattern = re.compile(r"[A-Z][a-z]?\d+(?:gg|gm|g)")
    for line in table_text(36).splitlines():
        parts = line.split()
        if len(parts) != 12 or label_pattern.fullmatch(parts[0]) is None:
            continue
        if re.fullmatch(r"\[\d+\]", parts[5]) is None or parts[10] != "±" or not parts[11].endswith("%"):
            continue
        try:
            numeric = [float(value) for value in parts[1:5]]
            calculated = [float(value) for value in parts[6:10]]
            ce_uncertainty = float(parts[11][:-1])
        except ValueError:
            continue
        rows.append(
            {
                "table": 36,
                "table_row": len(rows) + 1,
                "label": parts[0],
                "kT_lab_keV": numeric[0],
                "E50_keV": numeric[1],
                "measured_mb": numeric[2],
                "experimental_uncertainty_percent": numeric[3],
                "reference": parts[5],
                "published_calculated_mb": calculated[0],
                "published_cross_section_uncertainty_percent": calculated[1],
                "published_spectrum_uncertainty_percent": calculated[2],
                "published_C_over_E": calculated[3],
                "published_C_over_E_uncertainty_percent": ce_uncertainty,
                "source_line": " ".join(line.split()),
            }
        )
    if len(rows) != EXPECTED_ROWS[36]:
        raise RuntimeError(f"Table 36: parsed {len(rows)} rows, expected {EXPECTED_ROWS[36]}")
    return rows


def validate_support_tables() -> dict[str, Any]:
    pages = []
    for page in (75, 76, 77, 78, 79):
        completed = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"pdftotext failed for support page {page}")
        pages.append(completed.stdout)
    normalized = " ".join(" ".join(pages).split())
    required_fragments = [
        "13090 10 kW, 250 s",
        "13127 125 MJ, 16 min",
        "0.9300",
        "0.869965",
        "10636 Steady",
        "172.4 MJ",
        "10673",  # negative plant: this must remain absent
    ]
    present = {fragment: fragment in normalized for fragment in required_fragments[:-1]}
    if not all(present.values()) or required_fragments[-1] in normalized:
        raise RuntimeError(f"held-out operation/composition source validation failed: {present}")
    return {
        "spr_operations": SPR_OPERATIONS,
        "acrr_baseline": {
            "operation": 10639,
            "date": "19/09/2013",
            "energy_MJ": 153.2,
            "type": "Pulse",
            "normalized_duration_s": BASELINE_IRRADIATION_S,
        },
        "acrr_support_operations": ACRR_OPERATIONS,
        "foil_compositions": {
            name: [{"target_za": za, "atom_fraction": fraction} for za, fraction in values]
            for name, values in FOIL_COMPOSITIONS.items()
        },
        "source_fragments_present": present,
    }


def split_reaction(label: str) -> tuple[str, int, str]:
    for suffix in ("gg", "gm", "nm", "g", "f", "p", "a", "2"):
        if not label.endswith(suffix):
            continue
        match = re.fullmatch(r"([A-Z][a-z]?)(\d+)", label[: -len(suffix)])
        if match is not None:
            return match.group(1), int(match.group(2)), suffix
    raise ValueError(f"unmapped held-out reaction label {label!r}")


def simple_mapping(label: str) -> dict[str, Any]:
    element, mass, suffix = split_reaction(label)
    if element not in ELEMENT_Z:
        raise ValueError(f"unknown held-out element {element!r}")
    z = ELEMENT_Z[element]
    target_za = z * 1000 + mass
    mt_by_suffix = {"g": 102, "gg": 102, "gm": 102, "nm": 4, "f": 18, "p": 103, "a": 107, "2": 16}
    mt = mt_by_suffix[suffix]
    product_lfs: int | None = None
    if suffix == "gg":
        product_lfs = 0
    elif suffix in {"gm", "nm"}:
        product_lfs = 1
    if suffix in {"g", "gg", "gm"}:
        decay_product_za = z * 1000 + mass + 1
    elif suffix == "nm":
        decay_product_za = target_za
    elif suffix == "p":
        decay_product_za = (z - 1) * 1000 + mass
    elif suffix == "a":
        decay_product_za = (z - 2) * 1000 + mass - 3
    elif suffix == "2":
        decay_product_za = z * 1000 + mass - 1
    else:
        decay_product_za = None
    decay_product_liso = 1 if suffix in {"gm", "nm"} else 0
    if label == "Nb932":
        product_lfs = 1
        decay_product_liso = 1
    return {
        "kind": "simple",
        "source_label": label,
        "target_za": target_za,
        "mt": mt,
        "product_za": decay_product_za if product_lfs is not None else None,
        "product_lfs": product_lfs,
        "raw_evaluation_lfs": product_lfs,
        "decay_product_za": decay_product_za,
        "decay_product_liso": decay_product_liso,
        "is_fission": suffix == "f",
        "grammar": "p17-amendment-1-simple",
    }


def reaction_mapping(label: str) -> dict[str, Any]:
    composition = COMPOSITE_LABELS.get(label)
    if composition is None:
        return simple_mapping(label)
    return {
        "kind": "composite_fission_foil",
        "source_label": label,
        "composition": composition,
        "components": [
            {"target_za": za, "atom_fraction": fraction, "mt": 18}
            for za, fraction in FOIL_COMPOSITIONS[composition]
        ],
        "is_fission": True,
        "decay_product_za": None,
        "decay_product_liso": None,
        "grammar": "p17-amendment-1-table-22-composite",
    }


def post_failure_publication_mapping(label: str) -> dict[str, Any]:
    """Return the diagnostic mapping discovered after Amendment 1 froze.

    This mapping is never eligible to repair P17.  It exists so the failed
    protocol still yields useful, explicitly post-failure evidence for P18.
    Table 1 and Fig. 114 identify the abbreviated Table 25/36 ``Ag109g``
    entry as the state-specific Ag-109(n,gamma)Ag-110m response.
    """

    if label != "Ag109g":
        return reaction_mapping(label)
    mapping = simple_mapping("Ag109gm")
    return {
        **mapping,
        "source_label": label,
        "publication_alias": "Ag109gm",
        # ENDF's sparse level identifier is 2 for Ag-110m, while ACTINV's
        # processed product selector uses decay-isomer ordinal 1.
        "raw_evaluation_lfs": 2,
        "grammar": "post-p17-failure-table-1-and-figure-114-alias",
    }


def requested_keys(mappings: list[dict[str, Any]]) -> tuple[set[tuple[int, int]], set[tuple[int, int, int, int]]]:
    mf3: set[tuple[int, int]] = set()
    mf10: set[tuple[int, int, int, int]] = set()
    for mapping in mappings:
        if mapping["kind"] == "composite_fission_foil":
            mf3.update((int(component["target_za"]), 18) for component in mapping["components"])
            continue
        if mapping["product_lfs"] is None:
            mf3.add((int(mapping["target_za"]), int(mapping["mt"])))
        else:
            mf10.add(
                (
                    int(mapping["target_za"]),
                    int(mapping["mt"]),
                    int(mapping["product_za"]),
                    int(mapping["raw_evaluation_lfs"]),
                )
            )
    return mf3, mf10


def header_za(field: str) -> int:
    """Read ZA, including IRDFF's space-padded one-digit exponent form."""

    try:
        return int(endf_float(field))
    except ValueError:
        repaired = re.sub(r"([+-])\s+(\d+)$", r"\1\2", field.strip())
        return int(endf_float(repaired))


def selected_endf_catalog(
    path: Path, mappings: list[dict[str, Any]]
) -> tuple[dict[tuple[int, int], tuple[Any, ...]], dict[tuple[int, int, int, int], tuple[Any, ...]]]:
    wanted_mf3, wanted_mf10 = requested_keys(mappings)
    mf3: dict[tuple[int, int], tuple[Any, ...]] = {}
    mf10: dict[tuple[int, int, int, int], tuple[Any, ...]] = {}
    wanted_prefix = {(key[0], key[1]) for key in wanted_mf10}
    for (_, mf, mt), lines in sections(path):
        if mf not in {3, 10} or mt == 261:
            continue
        target_za = header_za(fields(lines[0])[0])
        if mf == 3:
            key3 = (target_za, mt)
            if key3 in wanted_mf3:
                if key3 in mf3:
                    raise ValueError(f"duplicate selected MF3 key {key3}")
                mf3[key3], _ = read_tab1(lines, 1)
            continue
        if (target_za, mt) not in wanted_prefix:
            continue
        subsection_count = int(fields(lines[0])[4])
        offset = 1
        for _ in range(subsection_count):
            record, offset = read_tab1(lines, offset)
            key10 = (target_za, mt, int(record[2]), int(record[3]))
            if key10 in wanted_mf10:
                if key10 in mf10:
                    raise ValueError(f"duplicate selected MF10 key {key10}")
                mf10[key10] = record
    return mf3, mf10


def spectrum_catalog(path: Path) -> dict[int, tuple[Any, ...]]:
    expected = set(FIELD_SPECTRUM_MAT.values())
    output = {}
    for (mat, mf, mt), lines in sections(path):
        if mat in expected and mf == 3 and mt == 261:
            output[mat], _ = read_tab1(lines, 1)
    if set(output) != expected:
        raise RuntimeError(f"missing held-out spectra: {sorted(expected - set(output))}")
    return output


def catalog_record(
    mapping: dict[str, Any],
    mf3: dict[tuple[int, int], tuple[Any, ...]],
    mf10: dict[tuple[int, int, int, int], tuple[Any, ...]],
) -> tuple[Any, ...] | None:
    if mapping["kind"] != "simple":
        raise ValueError("catalog_record requires a simple response")
    if mapping["product_lfs"] is None:
        return mf3.get((int(mapping["target_za"]), int(mapping["mt"])))
    return mf10.get(
        (
            int(mapping["target_za"]),
            int(mapping["mt"]),
            int(mapping["product_za"]),
            int(mapping["raw_evaluation_lfs"]),
        )
    )


def fold_group_response(
    mapping: dict[str, Any],
    mf3: dict[tuple[int, int], tuple[Any, ...]],
    mf10: dict[tuple[int, int, int, int], tuple[Any, ...]],
    spectrum: tuple[Any, ...],
) -> tuple[float | None, list[list[int]]]:
    if mapping["kind"] == "composite_fission_foil":
        total = 0.0
        keys = []
        for component in mapping["components"]:
            key = (int(component["target_za"]), 18)
            record = mf3.get(key)
            if record is None:
                return None, keys
            total += float(component["atom_fraction"]) * fold_histograms(record, spectrum)
            keys.append([key[0], 3, key[1]])
        return total, keys
    record = catalog_record(mapping, mf3, mf10)
    if record is None:
        return None, []
    if mapping["product_lfs"] is None:
        key = [int(mapping["target_za"]), 3, int(mapping["mt"])]
    else:
        key = [
            int(mapping["target_za"]),
            10,
            int(mapping["mt"]),
            int(mapping["product_za"]),
            int(mapping["raw_evaluation_lfs"]),
        ]
    return fold_histograms(record, spectrum), [key]


def production_spectrum_response(
    library: ProductionLibrary, mapping: dict[str, Any], spectrum: tuple[Any, ...]
) -> tuple[float | None, str, list[dict[str, Any]]]:
    if mapping["kind"] == "simple":
        value, reason, evidence = library.spectrum_average(mapping, spectrum)
        return value, reason, [evidence]
    total = 0.0
    evidence = []
    for component in mapping["components"]:
        simple = {
            "target_za": int(component["target_za"]),
            "mt": 18,
            "product_lfs": None,
            "product_za": None,
        }
        value, reason, detail = library.spectrum_average(simple, spectrum)
        evidence.append({"atom_fraction": component["atom_fraction"], **detail})
        if value is None:
            return None, reason, evidence
        total += float(component["atom_fraction"]) * value
    return total, "scored", evidence


def _production_row(library: ProductionLibrary, mapping: dict[str, Any]) -> tuple[np.ndarray | None, dict[str, Any]]:
    target_index = library.target_by_za.get(int(mapping["target_za"]))
    if target_index is None:
        return None, {"reason": "variant_target_unavailable", "target_index": None}
    if mapping["product_lfs"] is None:
        mask = (
            (library.rows[:, 0] == target_index)
            & (library.rows[:, 1] == int(mapping["mt"]))
            & (library.rows[:, 2] == -1)
        )
    else:
        mask = (
            (library.rows[:, 0] == target_index)
            & (library.rows[:, 1] == int(mapping["mt"]))
            & (library.rows[:, 2] == int(mapping["product_za"]))
            & (library.rows[:, 3] == int(mapping["product_lfs"]))
        )
    positions = np.flatnonzero(mask)
    if len(positions) != 1:
        return None, {
            "reason": "variant_reaction_unavailable",
            "target_index": target_index,
            "matching_rows": int(len(positions)),
        }
    position = int(positions[0])
    return library.sigma[position], {
        "reason": "scored",
        "target_index": target_index,
        "library_row": [int(value) for value in library.rows[position]],
    }


def maxwellian_group_weights(
    bounds_eV: np.ndarray, kt_keV: float, *, stellar_normalization: bool = True
) -> np.ndarray:
    kt_eV = float(kt_keV) * 1000.0
    scaled = np.asarray(bounds_eV, dtype=float) / kt_eV
    primitive = (1.0 + scaled) * np.exp(-scaled)
    scale = 2.0 / math.sqrt(math.pi) if stellar_normalization else 1.0
    return scale * (primitive[:-1] - primitive[1:])


def production_macs(
    library: ProductionLibrary,
    mapping: dict[str, Any],
    kt_keV: float,
    *,
    stellar_normalization: bool = True,
) -> tuple[float | None, str, list[dict[str, Any]]]:
    weights = maxwellian_group_weights(
        library.bounds, kt_keV, stellar_normalization=stellar_normalization
    )
    if mapping["kind"] == "simple":
        sigma, evidence = _production_row(library, mapping)
        if sigma is None:
            return None, str(evidence["reason"]), [evidence]
        return float(np.dot(sigma, weights)), "scored", [evidence]
    total = 0.0
    evidence = []
    for component in mapping["components"]:
        simple = {
            "target_za": int(component["target_za"]),
            "mt": 18,
            "product_lfs": None,
            "product_za": None,
        }
        sigma, detail = _production_row(library, simple)
        evidence.append({"atom_fraction": component["atom_fraction"], **detail})
        if sigma is None:
            return None, str(detail["reason"]), evidence
        total += float(component["atom_fraction"]) * float(np.dot(sigma, weights))
    return total, "scored", evidence


_GAUSS_NODES, _GAUSS_WEIGHTS = np.polynomial.legendre.leggauss(16)


def pointwise_macs(
    record: tuple[Any, ...], kt_keV: float, *, stellar_normalization: bool = True
) -> float:
    """Fold one ENDF TAB1 response with the normalized stellar Maxwellian."""

    _, _, _, _, _, _, nbt, x_values, y_values = record
    energy = np.asarray(x_values, dtype=float)
    differences = np.diff(energy)
    if len(energy) < 2 or np.any(differences < 0.0) or not np.any(differences > 0.0):
        raise ValueError("pointwise response energy grid is not nondecreasing")
    lower_all = energy[:-1][differences > 0.0]
    upper_all = energy[1:][differences > 0.0]
    kt_eV = float(kt_keV) * 1000.0
    total = 0.0
    for start in range(0, len(lower_all), 20_000):
        stop = min(start + 20_000, len(lower_all))
        lower = lower_all[start:stop]
        upper = upper_all[start:stop]
        midpoint = 0.5 * (lower + upper)
        half_width = 0.5 * (upper - lower)
        nodes = midpoint[:, None] + half_width[:, None] * _GAUSS_NODES[None, :]
        sigma = interp_eval(
            energy,
            np.asarray(y_values, dtype=float),
            nbt,
            nodes.ravel(),
        ).reshape(nodes.shape)
        scale = 2.0 / math.sqrt(math.pi) if stellar_normalization else 1.0
        spectrum = scale * nodes * np.exp(-nodes / kt_eV) / (kt_eV**2)
        total += float(np.sum(half_width[:, None] * _GAUSS_WEIGHTS[None, :] * sigma * spectrum))
    return total


def pointwise_response_macs(
    mapping: dict[str, Any],
    mf3: dict[tuple[int, int], tuple[Any, ...]],
    mf10: dict[tuple[int, int, int, int], tuple[Any, ...]],
    kt_keV: float,
    *,
    stellar_normalization: bool = True,
) -> tuple[float | None, list[list[int]]]:
    if mapping["kind"] == "composite_fission_foil":
        total = 0.0
        keys = []
        for component in mapping["components"]:
            key = (int(component["target_za"]), 18)
            record = mf3.get(key)
            if record is None:
                return None, keys
            total += float(component["atom_fraction"]) * pointwise_macs(
                record, kt_keV, stellar_normalization=stellar_normalization
            )
            keys.append([key[0], 3, key[1]])
        return total, keys
    record = catalog_record(mapping, mf3, mf10)
    if record is None:
        return None, []
    if mapping["product_lfs"] is None:
        key = [int(mapping["target_za"]), 3, int(mapping["mt"])]
    else:
        key = [
            int(mapping["target_za"]),
            10,
            int(mapping["mt"]),
            int(mapping["product_za"]),
            int(mapping["raw_evaluation_lfs"]),
        ]
    return pointwise_macs(
        record, kt_keV, stellar_normalization=stellar_normalization
    ), [key]


def decay_by_identity(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    records = parse_decay_file(str(path))
    candidates: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for record in records.values():
        key = (int(round(record["za"])), int(record["liso"]))
        candidates.setdefault(key, []).append(record)
    # IRDFF-II carries older IRDF-2012 and newer IRDF-30 copies for some
    # products.  The larger MAT is the later distributed record.  Preserve
    # every candidate identity in the selected record for independent audit.
    output = {}
    for key, values in candidates.items():
        selected = max(values, key=lambda value: int(value["mat"]))
        output[key] = {
            **selected,
            "candidate_mats": sorted(int(value["mat"]) for value in values),
            "candidate_half_lives_s": [
                float(value["half_life"]) for value in sorted(values, key=lambda value: int(value["mat"]))
            ],
        }
    return output


def experimental_spectral_index(
    row: dict[str, Any], monitor: dict[str, Any], mapping: dict[str, Any], decays: dict[tuple[int, int], dict[str, Any]]
) -> dict[str, Any]:
    monitor_mapping = simple_mapping("Ni58p")
    monitor_key = (
        int(monitor_mapping["decay_product_za"]),
        int(monitor_mapping["decay_product_liso"]),
    )
    monitor_decay = decays.get(monitor_key)
    if monitor_decay is None or monitor_decay["half_life"] <= 0.0:
        raise RuntimeError(f"missing radioactive monitor decay {monitor_key}")
    monitor_lambda = math.log(2.0) / float(monitor_decay["half_life"])
    monitor_saturation = -math.expm1(-monitor_lambda * BASELINE_IRRADIATION_S)
    if mapping["is_fission"]:
        value = (
            float(row["measured_EOI_per_atom"])
            / float(monitor["measured_EOI_per_atom"])
            * monitor_saturation
            / BASELINE_IRRADIATION_S
        )
        product_key = None
        product_half_life = None
        product_saturation = None
    else:
        product_key = (int(mapping["decay_product_za"]), int(mapping["decay_product_liso"]))
        product_decay = decays.get(product_key)
        if product_decay is None or float(product_decay["half_life"]) <= 0.0:
            raise RuntimeError(f"missing radioactive activation-product decay {product_key} for {row['label']}")
        product_half_life = float(product_decay["half_life"])
        product_lambda = math.log(2.0) / product_half_life
        product_saturation = -math.expm1(-product_lambda * BASELINE_IRRADIATION_S)
        value = (
            float(row["measured_EOI_per_atom"])
            / float(monitor["measured_EOI_per_atom"])
            * monitor_saturation
            / product_saturation
        )
    published_inferred = float(row["published_spectral_index"]) / float(row["published_SI_C_over_E"])
    relative = abs(value - published_inferred) / published_inferred
    return {
        "value": value,
        "published_inferred_value": published_inferred,
        "relative_to_published_inferred": relative,
        "baseline_irradiation_s": BASELINE_IRRADIATION_S,
        "monitor_label": monitor["label"],
        "monitor_decay_product": {"za": monitor_key[0], "liso": monitor_key[1]},
        "monitor_half_life_s": float(monitor_decay["half_life"]),
        "monitor_decay_mat": int(monitor_decay["mat"]),
        "monitor_saturation": monitor_saturation,
        "product_decay_identity": (
            None if product_key is None else {"za": product_key[0], "liso": product_key[1]}
        ),
        "product_half_life_s": product_half_life,
        "product_decay_mat": None if product_key is None else int(product_decay["mat"]),
        "product_saturation": product_saturation,
        "observable_kind": "fissions_per_atom" if mapping["is_fission"] else "activity_Bq_per_atom",
    }


def pulse_limit_spectral_index(
    row: dict[str, Any], monitor: dict[str, Any], mapping: dict[str, Any], decays: dict[tuple[int, int], dict[str, Any]]
) -> dict[str, Any]:
    """Reconstruct the EOI SI in the instantaneous-pulse limit.

    This is post-failure diagnostic arithmetic.  It is not a correction to
    Amendment 1's frozen 960 s uniform-power formula.
    """

    monitor_mapping = simple_mapping("Ni58p")
    monitor_key = (int(monitor_mapping["decay_product_za"]), 0)
    monitor_decay = decays[monitor_key]
    monitor_lambda = math.log(2.0) / float(monitor_decay["half_life"])
    if mapping["is_fission"]:
        value = (
            float(row["measured_EOI_per_atom"])
            / float(monitor["measured_EOI_per_atom"])
            * monitor_lambda
        )
        product_key = None
        product_decay = None
    else:
        product_key = (int(mapping["decay_product_za"]), int(mapping["decay_product_liso"]))
        product_decay = decays[product_key]
        product_lambda = math.log(2.0) / float(product_decay["half_life"])
        value = (
            float(row["measured_EOI_per_atom"])
            / float(monitor["measured_EOI_per_atom"])
            * monitor_lambda
            / product_lambda
        )
    published_inferred = float(row["published_spectral_index"]) / float(row["published_SI_C_over_E"])
    return {
        "value": value,
        "published_inferred_value": published_inferred,
        "relative_to_published_inferred": abs(value - published_inferred) / published_inferred,
        "limit": "instantaneous_pulse",
        "monitor_decay_product": {"za": monitor_key[0], "liso": monitor_key[1]},
        "monitor_half_life_s": float(monitor_decay["half_life"]),
        "monitor_decay_mat": int(monitor_decay["mat"]),
        "product_decay_identity": (
            None if product_key is None else {"za": product_key[0], "liso": product_key[1]}
        ),
        "product_half_life_s": (
            None if product_decay is None else float(product_decay["half_life"])
        ),
        "product_decay_mat": None if product_decay is None else int(product_decay["mat"]),
    }


def derived_si_rows(decays: dict[tuple[int, int], dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output = []
    monitor_by_table = {23: "Ni58p-Cd", 25: "Ni58p-bare"}
    for table in (23, 25):
        source_rows = parse_si_table(table)
        monitor = next(row for row in source_rows if row["label"] == monitor_by_table[table])
        for source in source_rows:
            mapping = reaction_mapping(source["reaction_label"])
            derivation = experimental_spectral_index(source, monitor, mapping, decays)
            diagnostic_mapping = post_failure_publication_mapping(source["reaction_label"])
            pulse_derivation = pulse_limit_spectral_index(
                source, monitor, diagnostic_mapping, decays
            )
            if table == 23:
                eligibility = "unsupported_self_shielding"
            elif source["label"] == monitor["label"]:
                eligibility = "monitor_identity_not_predictive"
            elif source["cover"] == SUPPORTED_COVER:
                eligibility = "scored"
            elif source["cover"] in KNOWN_UNSUPPORTED_COVERS:
                eligibility = "unsupported_self_shielding"
            else:
                raise ValueError(f"unclassified held-out cover {source['cover']!r}")
            output.append(
                {
                    **source,
                    "mapping": mapping,
                    "experimental_spectral_index": derivation,
                    "post_failure_mapping": diagnostic_mapping,
                    "post_failure_pulse_spectral_index": pulse_derivation,
                    "production_eligibility": eligibility,
                }
            )
    relative = [
        float(row["experimental_spectral_index"]["relative_to_published_inferred"])
        for row in output
    ]
    diagnostics = {
        "rows_total": len(output),
        "rows_by_table": {
            str(table): sum(row["table"] == table for row in output) for table in (23, 25)
        },
        "maximum_EOI_reconstruction_relative": max(relative),
        "maximum_post_failure_pulse_reconstruction_relative": max(
            float(row["post_failure_pulse_spectral_index"]["relative_to_published_inferred"])
            for row in output
        ),
        "production_eligibility": {
            reason: sum(row["production_eligibility"] == reason for row in output)
            for reason in sorted({row["production_eligibility"] for row in output})
        },
    }
    return output, diagnostics


if __name__ == "__main__":
    with extracted_inputs() as paths:
        si_rows, si_diagnostics = derived_si_rows(decay_by_identity(paths["decay"]))
    print(
        {
            "inputs": checked_inputs(),
            "rows": {table: len(parse_si_table(table)) for table in (23, 25)},
            "h3": len(parse_maxwellian_table()),
            "support": validate_support_tables(),
            "si_diagnostics": si_diagnostics,
            "worst_si_rows": sorted(
                (
                    (
                        row["label"],
                        row["experimental_spectral_index"]["relative_to_published_inferred"],
                    )
                    for row in si_rows
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:5],
        }
    )
