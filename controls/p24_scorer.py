#!/usr/bin/env python3
"""P24 frozen held-out scorer.

Implements exactly the frozen G1 definitions (``controls/p24_definitions.py``)
and the frozen P17 metric/row semantics (``controls/p17_scoring.py``) for the
sealed fresh partition.  Written and audited at G2 before the partition is
unsealed; the G4 run executes it once.

Parser contract: each fresh table has a frozen grammar below, derived from
the publication's captions and the consumed-table conventions.  A row line
that fails its grammar raises; a parsed row whose printed C/E is
inconsistent with its printed measured/calculated pair (internal
consistency check) is ledgered ``internal_consistency_failure`` — never
silently trusted.  ``source_line`` is preserved for every row so the G5
checker can re-derive every value independently.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from p24_definitions import (
    COMPOSITE_LABELS,
    DILUTE_VERIFIED_FIELDS,
    FOIL_COMPOSITIONS,
    MONITOR_LABEL,
    SUFFIX_CLASS,
    SUFFIX_MT,
    decay_half_life_s,
    eoi_spectral_index_finite,
    eoi_spectral_index_pulse,
)
from p17_scoring import (  # unchanged P17 semantics
    CALCULATION_REASONS,
    family_metrics,
    all_family_metrics,
    make_row,
    score_calculation,
    unscored_calculation,
)

# ---------------------------------------------------------------------------
# Frozen table contracts (from the sealed partition + captions only)
# ---------------------------------------------------------------------------

PDF = Path.home() / "nuclear-data/p17-irdff/IRDFF-II_primary_1909.03336.pdf"

TABLE_SPECS: dict[int, dict[str, Any]] = {
    26: {"pages": (79,), "family": "F-MOLBR1", "field": "Mol-BR1 Mark-III",
         "kind": "si_direct", "spectrum_mats": (9020,)},
    27: {"pages": (81,), "family": "F-TRIGA", "field": "TRIGA-JSI filtered channels",
         "kind": "rate_ratio", "spectrum_mats": (9041, 9042, 9043, 9044)},
    28: {"pages": (82,), "family": "F-HMF001", "field": "HMF001 Godiva",
         "kind": "si_direct", "spectrum_mats": (9101,)},
    29: {"pages": (83,), "family": "F-HMF028", "field": "HMF028 Flattop-25",
         "kind": "si_direct", "spectrum_mats": (9102,)},
    30: {"pages": (84,), "family": "F-IMF007", "field": "IMF007 Big-Ten",
         "kind": "si_direct", "spectrum_mats": (9103,)},
    31: {"pages": (85,), "family": "F-PMF", "field": "PMF multi-assembly",
         "kind": "si_direct", "spectrum_mats": (9104, 9106, 9107)},
    32: {"pages": (85,), "family": "F-FMR001", "field": "FMR001 IPPE-BR1",
         "kind": "si_direct", "spectrum_mats": (9110,)},
    # Table 33 continues on PDF p.88 (caption p.87, "TABLE 33: continued" p.88);
    # the sealed page list records the caption page only.
    33: {"pages": (87, 88), "family": "F-LEGACY", "field": "legacy reactor fields",
         "kind": "sacs_or_si", "spectrum_mats": (9004, 9005, 9007)},
    34: {"pages": (88,), "family": "F-THERMAL-XS", "field": "thermal Maxwellian",
         "kind": "sigma0", "spectrum_mats": ()},
    35: {"pages": (89,), "family": "F-THERMAL-I0", "field": "epithermal",
         "kind": "resonance_integral", "spectrum_mats": ()},
    41: {"pages": (96,), "family": "F-ACRR-LB44", "field": "ACRR central cavity",
         "kind": "eoi_si", "spectrum_mats": (9013,)},
    42: {"pages": (97,), "family": "F-ACRR-PLG", "field": "ACRR central cavity",
         "kind": "eoi_si", "spectrum_mats": (9012,)},
    43: {"pages": (98,), "family": "F-ACRR-CDPOLY", "field": "ACRR central cavity",
         "kind": "eoi_si", "spectrum_mats": (9011,)},
    44: {"pages": (99,), "family": "F-ACRR-FREC2", "field": "FREC-II external cavity",
         "kind": "eoi_si", "spectrum_mats": (9015,)},
    45: {"pages": (100,), "family": "F-BEDN", "field": "9Be(d,n) Ed=16 MeV",
         "kind": "be_production", "spectrum_mats": (9408,)},
    46: {"pages": (100,), "family": "F-BEDN", "field": "9Be(d,n) Ed=40 MeV",
         "kind": "be_production", "spectrum_mats": (9409,)},
}

# P24 frozen inclusion vocabulary = P17's set plus the P24-corrected
# definitions' named outcomes (frozen at G1/G2).
P24_INCLUSION_REASONS = frozenset({
    "scored",
    "nonpositive_experimental_value",
    "nonfinite_experimental_value",
    "nonpositive_measurement_time",
    "no_cooling_step_within_2_percent",
    "non_neutron_incident_particle",
    "unmapped_target_reaction_product",
    "insufficient_spectrum_or_history",
    "unsupported_self_shielding",
    # P24 additions (frozen):
    "undefined_state_alias",
    "undefined_eoi_history",
    "undefined_monitor",
    "internal_consistency_failure",
    "monitor_identity_not_predictive",
    "unsupported_observable_kind",
})

ELEMENT_Z = {
    e: z for z, e in enumerate(
        "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe "
        "Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In "
        "Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf "
        "Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am "
        "Cm Bk Cf".split(), start=1)
}

MONITOR_HALF_LIFE_S = 6122300.0  # Co-58, pinned decay archive MAT 2722

# The thermal (2200 m/s) convention: sigma0 = sigma(E = 0.0253 eV).
SIGMA0_EV = 0.0253
# Kayzero k0-convention resonance integral: integral of sigma(E)/E above
# the cadmium cutoff, 0.55 eV.
I0_LOW_EV = 0.55
# Kayzero I0 upper bound stated in the Table 35 caption (2 MeV).
I0_HIGH_EV = 2.0e6

# Spectrum-archive MF=1 header attributions (metadata, verified at seal).
FIELD_MAT = {
    "ISNF": 9004, "CFRMF": 9005, "Sigma-Sigma": 9007,
    "Mol-BR1": 9020, "Godiva": 9101, "Flattop-25": 9102, "Big-Ten": 9103,
    "Jezebel": 9104, "Flattop-Pu": 9106, "Thor": 9107, "IPPE-BR1": 9110,
    "LB44": 9013, "PLG": 9012, "CdPoly": 9011, "FREC-II": 9015,
    "TRIGA-PT": 9041, "TRIGA-BN": 9042, "TRIGA-B4C": 9043, "TRIGA-10B4C": 9044,
}
# Table 33 interleaved sub-field headers (grammar metadata, not values).
T33_FIELD_HEADERS = [
    ("Intermediate Energy Standard Neutron Field", "ISNF"),
    ("Secondary Intermediate-Energy Standard Neutron Field", "Sigma-Sigma"),
    ("Coupled Fast Reactivity Measurement Facility", "CFRMF"),
]
# Table 27 column groups: each measured ratio is the filtered-channel
# rate over the unfiltered pneumatic-tube rate (MAT 9041).
T27_CHANNELS = [("TRIGA-BN", 9042), ("TRIGA-B4C", 9043), ("TRIGA-10B4C", 9044)]

# Tokens that name a fresh-partition field inside a row or an interleaved
# sub-header line (publication labels and ICSBEP identifiers -> FIELD_MAT key).
FIELD_TOKENS = {
    "ISNF": "ISNF", "SIGMA-SIGMA": "Sigma-Sigma", "SIGMASIGMA": "Sigma-Sigma",
    "CFRMF": "CFRMF",
    "JEZEBEL": "Jezebel", "PMF001": "Jezebel",
    "FLATTOP-PU": "Flattop-Pu", "FLATTOPPU": "Flattop-Pu", "PMF006": "Flattop-Pu",
    "THOR": "Thor", "PMF008": "Thor",
    "GODIVA": "Godiva", "HMF001": "Godiva",
    "FLATTOP-25": "Flattop-25", "FLATTOP25": "Flattop-25", "HMF028": "Flattop-25",
    "BIG-TEN": "Big-Ten", "BIGTEN": "Big-Ten", "IMF007": "Big-Ten",
    "IPPE-BR1": "IPPE-BR1", "FMR001": "IPPE-BR1",
    "MOL-BR1": "Mol-BR1", "MOLBR1": "Mol-BR1",
    "LB44": "LB44", "PLG": "PLG", "CDPOLY": "CdPoly",
    "FREC-II": "FREC-II", "FREC": "FREC-II",
}


def detect_field(parts: list[str]) -> str | None:
    """Scan a line's tokens for a named fresh-partition field."""
    for token in parts:
        key = token.strip("(),[];:.").upper()
        if key in FIELD_TOKENS:
            return FIELD_TOKENS[key]
    return None


# ---------------------------------------------------------------------------
# Page extraction and table segmentation
# ---------------------------------------------------------------------------

def page_text(page: int) -> str:
    completed = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        raise RuntimeError(f"pdftotext failed for page {page}: {completed.stderr}")
    return completed.stdout


def table_text(table: int) -> str:
    """Concatenate the table's caption-bounded regions across its pages."""
    spec = TABLE_SPECS[table]
    caption = re.compile(rf"^\s*TABLE\s+{table}\b", re.IGNORECASE)
    next_caption = re.compile(r"^\s*TABLE\s+\d+\b", re.IGNORECASE)
    chunks = []
    for page in spec["pages"]:
        collecting = False
        for line in page_text(page).splitlines():
            if caption.match(line):
                collecting = True
                continue
            if collecting and next_caption.match(line):
                collecting = False
            if collecting:
                chunks.append(line)
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Label grammar (frozen)
# ---------------------------------------------------------------------------

def split_label(label: str) -> tuple[int, int, str]:
    """Publication label -> (target_za, mt, suffix_class)."""
    if label in COMPOSITE_LABELS:
        return -1, 18, "composite"
    for suffix in ("gg", "gm", "nm", "2m", "pm", "g", "f", "p", "a", "2", "3", "m", "n"):
        if not label.endswith(suffix):
            continue
        match = re.fullmatch(r"([A-Z][a-z]?)(\d+)", label[: -len(suffix)])
        if match is not None:
            element, mass_text = match.groups()
            if element not in ELEMENT_Z:
                raise ValueError(f"unknown element in label {label!r}")
            return ELEMENT_Z[element] * 1000 + int(mass_text), SUFFIX_MT[suffix], SUFFIX_CLASS[suffix]
    raise ValueError(f"unmapped reaction label {label!r}")


def load_alias_index(definitions_record: dict) -> dict[tuple[int, int, str], dict]:
    return {
        (e["target_za"], e["mt"], e["interpreted_suffix"]): e["binding"]
        for e in definitions_record["alias_table"]
    }


# ---------------------------------------------------------------------------
# Parsers — one per frozen grammar kind; every label-like line must parse
# ---------------------------------------------------------------------------

LABEL_LINE = re.compile(r"^\s*([A-Z][A-Za-z]*\d+[A-Za-z0-9]*(?:[-/][A-Za-z0-9/.]+)*)\s+")


def _float_list(parts: list[str]) -> list[float] | None:
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


REACTION_TOKEN = re.compile(r"^[A-Za-z]+\d+[A-Za-z0-9]*$")


def _label_and_monitor(parts: list[str]) -> tuple[str, str | None, int]:
    """Extract (reaction_label, monitor_label, first_numeric_index).

    The publication prints a fused ``reaction/monitor`` token or a
    separate monitor token after the reaction label ("Label and
    monitor" column).  Neither form is guessed: an unparseable monitor
    slot yields ``monitor=None`` -> ``undefined_monitor`` at scoring.
    """
    label = parts[0]
    if "/" in label:
        reaction, monitor = label.split("/", 1)
        return reaction, monitor or None, 1
    if len(parts) > 1 and REACTION_TOKEN.match(parts[1]):
        return label, parts[1], 2
    return label, None, 1


def _degraded_row(table: int, rows: list, parts: list[str], note: str) -> dict:
    """A label-bearing line that failed its frozen grammar.  Emitted so the
    source row is never dropped; it ledgers via internal_consistency_failure."""
    row = {
        "table": table, "table_row": len(rows) + 1,
        "label": parts[0] if parts else "<unlabeled>",
        "reaction_label": None,
        "grammar_failure": note,
        "numeric_tokens": [float(p) for p in parts if _is_number(p)] or [],
        "source_line": " ".join(parts),
    }
    rows.append(row)
    return row


def parse_si_direct(table: int, text: str) -> list[dict]:
    """'label monitor? E50 measSI unc calcSI unc CE unc' — internal C/E check."""
    rows = []
    for line in text.splitlines():
        m = LABEL_LINE.match(line)
        if m is None:
            continue
        parts = line.split()
        reaction, monitor, start = _label_and_monitor(parts)
        nums = _float_list(parts[start:])
        if nums is None or len(nums) < 7:
            _degraded_row(table, rows, parts, "si_direct grammar")
            continue
        rows.append({
            "table": table, "table_row": len(rows) + 1,
            "label": parts[0], "reaction_label": reaction,
            "monitor_label": monitor, "E50_MeV": nums[0],
            "measured_si": nums[1], "measured_si_uncertainty_percent": nums[2],
            "published_calculated_si": nums[3], "published_calc_uncertainty_percent": nums[4],
            "published_C_over_E": nums[5], "published_CE_uncertainty_percent": nums[6],
            "source_line": " ".join(parts),
        })
    return rows


def parse_eoi_si(table: int, text: str) -> list[dict]:
    """T25-style 9-column EOI grammar."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 9 or "-" not in parts[0]:
            if LABEL_LINE.match(line):
                _degraded_row(table, rows, parts, "eoi_si grammar")
            continue
        if re.fullmatch(r"[A-Za-z0-9]+-(?:bare|Cd|Cdna|Cdtk|Cdtk/B4C)", parts[0]) is None:
            continue
        nums = _float_list(parts[1:])
        if nums is None:
            _degraded_row(table, rows, parts, "eoi_si nonnumeric")
            continue
        reaction, cover = parts[0].split("-", 1)
        rows.append({
            "table": table, "table_row": len(rows) + 1,
            "label": parts[0], "reaction_label": reaction, "cover": cover,
            "E50_MeV": nums[0],
            "measured_EOI_per_atom": nums[1],
            "experimental_uncertainty_percent": nums[2],
            "published_SACS_uncertainty_percent": nums[3],
            "published_spectral_index": nums[4],
            "published_spectral_index_uncertainty_percent": nums[5],
            "published_SI_C_over_E": nums[6],
            "published_SI_C_over_E_uncertainty_percent": nums[7],
            "source_line": " ".join(parts),
        })
    return rows


def parse_si_direct_field(table: int, text: str) -> list[dict]:
    """si_direct rows plus interleaved/per-row field markers (T31
    multi-assembly).  A row's field comes from the nearest preceding
    header line or a token inside the row itself."""
    rows = []
    field = None
    for line in text.splitlines():
        header_field = detect_field(line.split())
        m = LABEL_LINE.match(line)
        if m is None:
            if header_field is not None:
                field = header_field
            continue
        parts = line.split()
        row_field = detect_field(parts) or field
        # field tokens are not data: strip them before label/monitor
        # resolution so "PMF001" is never misread as a monitor label
        data_parts = [p for p in parts
                      if FIELD_TOKENS.get(p.strip("(),[];:.").upper()) is None]
        if not data_parts:
            continue
        reaction, monitor, start = _label_and_monitor(data_parts)
        nums = _float_list(data_parts[start:])
        if nums is None or len(nums) < 7:
            _degraded_row(table, rows, parts, "si_direct_field grammar")
            continue
        rows.append({
            "table": table, "table_row": len(rows) + 1,
            "label": parts[0], "reaction_label": reaction,
            "monitor_label": monitor, "field_label": row_field,
            "E50_MeV": nums[0],
            "measured_si": nums[1], "measured_si_uncertainty_percent": nums[2],
            "published_calculated_si": nums[3], "published_calc_uncertainty_percent": nums[4],
            "published_C_over_E": nums[5], "published_CE_uncertainty_percent": nums[6],
            "source_line": " ".join(parts),
        })
    return rows


VALUE_UNC = re.compile(r"^([+-]?[0-9]*\.?[0-9]+(?:[Ee][+-]?\d+)?)\s*±\s*([0-9]*\.?[0-9]+(?:[Ee][+-]?\d+)?)%?$")


def parse_rate_ratio(table: int, text: str) -> list[dict]:
    """T27 grammar: 'mass Elem' label, then per channel group
    'exp±unc% calc±unc% diff%'.  Each channel group is one emitted row:
    measured value = filtered/unfiltered rate ratio."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not re.fullmatch(r"\d+", parts[0]) or parts[1] not in ELEMENT_Z:
            continue
        element, mass = parts[1], parts[0]
        groups = parts[2:]
        if len(groups) != 3 * len(T27_CHANNELS):
            _degraded_row(table, rows, parts, "rate_ratio arity")
            continue
        for index, (channel, mat) in enumerate(T27_CHANNELS):
            exp_tok, calc_tok, diff_tok = groups[3 * index: 3 * index + 3]
            exp_m, calc_m = VALUE_UNC.match(exp_tok), VALUE_UNC.match(calc_tok)
            row = {
                "table": table, "table_row": len(rows) + 1,
                "label": f"{element}{mass}g:{channel}",
                "reaction_label": f"{element}{mass}g",
                "field_label": channel,
                "spectrum_pair": (mat, 9041),
                "measured_value": float(exp_m.group(1)) if exp_m else None,
                "measured_uncertainty_percent": float(exp_m.group(2)) if exp_m else None,
                "published_calculated": float(calc_m.group(1)) if calc_m else None,
                "published_calc_uncertainty_percent": float(calc_m.group(2)) if calc_m else None,
                "published_diff_percent": float(diff_tok) if _is_number(diff_tok) else None,
                "source_line": " ".join(parts),
            }
            if row["measured_value"] is None:
                row["grammar_failure"] = "rate cell"
            rows.append(row)
    return rows


def parse_sacs_or_si(table: int, text: str) -> list[dict]:
    """T33 grammar: interleaved sub-field headers, rows
    'reaction[-or-SI-pair] E50 meas unc ref calc sigma-unc spect-unc CE'.
    A label containing '/' is an SI pair (monitor = denominator); a
    single reaction label is a SACS row measured in mb."""
    rows = []
    field = None
    for line in text.splitlines():
        for phrase, name in T33_FIELD_HEADERS:
            if phrase.lower() in line.lower():
                field = name
        m = LABEL_LINE.match(line)
        if m is None:
            continue
        parts = line.split()
        reaction, monitor, start = _label_and_monitor(parts)
        nums = [float(p) for p in parts[start:] if _is_number(p)]
        if len(nums) < 7:
            _degraded_row(table, rows, parts, "sacs_or_si grammar")
            continue
        rows.append({
            "table": table, "table_row": len(rows) + 1,
            "label": parts[0], "reaction_label": reaction,
            "monitor_label": monitor, "field_label": field,
            "E50_MeV": nums[0],
            "measured_value": nums[1],
            "measured_uncertainty_percent": nums[2],
            "measured_unit": "dimensionless" if monitor else "mb",
            "published_calculated": nums[3],
            "published_sacs_uncertainty_percent": nums[4],
            "published_spectrum_uncertainty_percent": nums[5],
            "published_C_over_E": nums[6],
            "source_line": " ".join(parts),
        })
    return rows


def parse_isotope_pair(table: int, text: str) -> list[dict]:
    """T34/T35 grammar: target/product isotope masses print on separate
    lines, then a 'Elem Elem <numerics>' line.  Reaction label derives
    from the mass/charge delta: same element A+1 -> capture 'g',
    Z-1 same A -> 'p', Z-2 A-3 -> 'a'; anything else stays unmapped."""
    rows = []
    pending: list[int] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if all(re.fullmatch(r"\d+", p) for p in parts):
            pending.extend(int(p) for p in parts)
            continue
        if len(parts) < 3 or parts[0] not in ELEMENT_Z or parts[1] not in ELEMENT_Z:
            pending.clear()
            continue
        nums = _float_list([p for p in parts[2:] if _is_number(p)])
        if not nums or len(pending) < 2:
            pending.clear()
            continue
        mass_t, mass_p = pending[-2], pending[-1]
        pending.clear()
        z_t, z_p = ELEMENT_Z[parts[0]], ELEMENT_Z[parts[1]]
        suffix = None
        if z_p == z_t and mass_p == mass_t + 1:
            suffix = "g"
        elif z_p == z_t - 1 and mass_p == mass_t:
            suffix = "p"
        elif z_p == z_t - 2 and mass_p == mass_t - 3:
            suffix = "a"
        reaction = f"{parts[0]}{mass_t}{suffix}" if suffix else None
        rows.append({
            "table": table, "table_row": len(rows) + 1,
            "label": f"{parts[0]}{mass_t}->{parts[1]}{mass_p}",
            "reaction_label": reaction,
            "measured_value": nums[0],
            "measured_uncertainty_percent": nums[1] if len(nums) > 1 else None,
            "numeric_tokens": nums,
            "source_line": " ".join(parts),
        })
    return rows


def parse_be_production(table: int, text: str) -> list[dict]:
    """T45/46 grammar: every row is a charged-particle production rate —
    parsed for the ledger only; scoring is denied at the predicate.
    A data line carries >=2 numeric tokens; label fragments (element or
    mass lines) accumulate into the next row's label."""
    rows = []
    pending: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        nums = [float(p) for p in parts if _is_number(p)]
        if len(nums) >= 2:
            rows.append({
                "table": table, "table_row": len(rows) + 1,
                "label": "-".join(pending[-2:] + [parts[0]]),
                "reaction_label": None,
                "numeric_tokens": nums,
                "source_line": " ".join(parts),
            })
            pending.clear()
        else:
            pending.extend(parts)
    return rows


def _residual_rows(table: int, text: str, rows: list[dict]) -> None:
    """Anti-drop sweep: any line inside the table region carrying >=2 numeric
    tokens that no emitted row claims is appended as a residual ledger row.
    The G4 report shows these as internal_consistency_failure — silent row
    loss is the protocol violation, a visible residual is not."""
    covered = {row["source_line"] for row in rows}
    for line in text.splitlines():
        normalized = " ".join(line.split())
        if not normalized or normalized in covered:
            continue
        if sum(1 for p in normalized.split() if _is_number(p)) >= 2:
            rows.append({
                "table": table, "table_row": len(rows) + 1,
                "label": normalized.split()[0],
                "reaction_label": None,
                "grammar_failure": "unclaimed numeric line",
                "numeric_tokens": [float(p) for p in normalized.split() if _is_number(p)],
                "source_line": normalized,
            })


def parse_fresh_table(table: int) -> list[dict]:
    spec = TABLE_SPECS[table]
    text = table_text(table)
    kind = spec["kind"]
    if kind == "si_direct":
        parser = parse_si_direct_field if len(spec["spectrum_mats"]) > 1 else parse_si_direct
    elif kind == "eoi_si":
        parser = parse_eoi_si
    elif kind == "rate_ratio":
        parser = parse_rate_ratio
    elif kind == "sacs_or_si":
        parser = parse_sacs_or_si
    elif kind in {"sigma0", "resonance_integral"}:
        parser = parse_isotope_pair
    elif kind == "be_production":
        parser = parse_be_production
    else:
        raise ValueError(f"Table {table}: no frozen grammar for kind {kind!r}")
    rows = parser(table, text)
    _residual_rows(table, text, rows)
    return rows


# ---------------------------------------------------------------------------
# Row construction under the frozen definitions
# ---------------------------------------------------------------------------

def p24_make_row(*, row_id, family, source_id, source_record, observable, unit,
                 experimental_value, experimental_uncertainty,
                 experimental_uncertainty_unit, inclusion_reason, calculations) -> dict:
    """P17's make_row structure with the P24 frozen inclusion vocabulary."""
    from p17_scoring import source_digest
    if not row_id or not family or not source_id or not observable or not unit:
        raise ValueError("row identity, family, source, observable and unit are required")
    if inclusion_reason not in P24_INCLUSION_REASONS:
        raise ValueError(f"inclusion reason {inclusion_reason!r} outside the P24 frozen set")
    status = "scored" if inclusion_reason == "scored" else "unscored"
    if status == "scored" and not (
        isinstance(experimental_value, (int, float))
        and math.isfinite(float(experimental_value)) and float(experimental_value) > 0.0):
        raise ValueError("scored rows require a finite positive experimental value")
    if experimental_uncertainty is not None and (
        not math.isfinite(float(experimental_uncertainty))
        or float(experimental_uncertainty) < 0.0):
        raise ValueError("experimental uncertainty must be finite and nonnegative")
    if not calculations:
        raise ValueError("every row must preserve at least one calculation variant")
    return {
        "row_id": row_id,
        "family": family,
        "source_id": source_id,
        "source_record": source_record,
        "source_record_sha256": source_digest(source_record),
        "observable": observable,
        "unit": unit,
        "experimental": {
            "value": float(experimental_value) if experimental_value is not None else None,
            "uncertainty": (float(experimental_uncertainty)
                            if experimental_uncertainty is not None else None),
            "uncertainty_unit": experimental_uncertainty_unit,
        },
        "inclusion": {"status": status, "reason": inclusion_reason},
        "calculations": dict(sorted(calculations.items())),
    }


def row_binding(label: str, alias_index: dict) -> dict:
    """Map a publication reaction label through the frozen alias table."""
    if label in COMPOSITE_LABELS:
        composition = COMPOSITE_LABELS[label]
        return {
            "kind": "composite_fission_foil", "source_label": label,
            "composition": composition,
            "components": [
                {"target_za": za, "atom_fraction": f, "mt": 18}
                for za, f in FOIL_COMPOSITIONS[composition]],
            "is_fission": True,
            "channel": "mf3_total",
        }
    target_za, mt, suffix_class = split_label(label)
    binding = alias_index.get((target_za, mt, suffix_class))
    if binding is None or binding["status"] != "bound":
        return {"kind": "simple", "source_label": label, "target_za": target_za,
                "mt": mt, "binding": binding,
                "unbound": True}
    out = {
        "kind": "simple", "source_label": label,
        "target_za": target_za, "mt": mt,
        "is_fission": mt == 18,
        "channel": binding["channel"],
        "binding": binding,
    }
    if binding["channel"] == "mf10_partial":
        out["product_za"] = binding["product_za"]
        out["raw_evaluation_lfs"] = binding["raw_evaluation_lfs"]
        leg = binding.get("decay_leg") or {}
        out["decay_product_liso"] = leg.get("decay_product_liso")
        out["decay_leg_status"] = leg.get("status")
        # production-library selector = processed isomer ordinal
        out["product_lfs"] = leg.get("decay_product_liso") if leg.get("status") == "bound" else None
        if leg.get("status") not in ("bound", "no_radioactive_product"):
            out["eoi_blocked"] = leg.get("status")
    else:
        # total channel: product_lfs None -> production mask on -1
        out["product_za"] = None
        out["product_lfs"] = None
        leg = binding.get("decay_leg") or {}
        out["decay_product_liso"] = leg.get("decay_product_liso")
        out["decay_leg_status"] = leg.get("status")
        # decay identity still needed for EOI reconstruction
        from p24_definitions import product_za_for_mt
        out["decay_product_za"] = product_za_for_mt(target_za, mt)
    return out


# ---------------------------------------------------------------------------
# Folding helpers — reuse the unchanged P17 machinery
# ---------------------------------------------------------------------------

def fold_keyset(binding: dict) -> dict:
    """The official-fold key for a bound simple mapping."""
    if binding.get("channel") == "mf10_partial":
        return {"mf": 10, "key": [int(binding["target_za"]), int(binding["mt"]),
                                  int(binding["product_za"]), int(binding["raw_evaluation_lfs"])]}
    return {"mf": 3, "key": [int(binding["target_za"]), int(binding["mt"])]}


def _is_number(token: str) -> bool:
    return re.fullmatch(r"[+-]?[0-9]*\.?[0-9]+(?:[Ee][+-]?\d+)?", token) is not None


# ---------------------------------------------------------------------------
# Row scoring under the frozen definitions
# ---------------------------------------------------------------------------

def row_spectrum_mat(row: dict, spec: dict) -> int | None:
    """Resolve the row's spectrum MAT: single-MAT tables are unambiguous;
    multi-MAT tables require the row's parsed field_label to name one of
    the spec's MATs."""
    mats = spec["spectrum_mats"]
    if len(mats) == 1:
        return mats[0]
    field = row.get("field_label")
    if field is not None and FIELD_MAT.get(field) in mats:
        return FIELD_MAT[field]
    return None


def inclusion_predicate(row: dict, spec: dict, binding: dict | None) -> str:
    """The frozen inclusion predicate, evaluated before any folding."""
    kind = spec["kind"]
    if kind == "be_production":
        return "non_neutron_incident_particle"
    from p24_definitions import cover_outcome, RESONANCE_STRUCTURED_MT
    # cover is the outermost physical gate: a covered row is unsupported
    # regardless of whether its label could be bound
    reason = cover_outcome(row.get("cover", "bare"))
    if reason is not None:
        return reason
    if kind in {"sigma0", "resonance_integral"}:
        # element-aggregate observables are outside the isotopic scope
        # unless the row prints an explicit reaction label
        if binding is None or binding.get("unbound") or binding.get("kind") != "simple":
            return "unmapped_target_reaction_product"
        return "scored"
    if binding is None:
        return "unmapped_target_reaction_product"
    if binding.get("unbound"):
        return "undefined_state_alias"
    if kind in {"si_direct", "sacs_or_si", "eoi_si", "rate_ratio"}:
        # dilute validity precedes monitor resolution: physical support
        # is the more fundamental predicate
        if int(binding.get("mt", 0)) in RESONANCE_STRUCTURED_MT:
            if spec["field"] not in DILUTE_VERIFIED_FIELDS:
                return "unsupported_self_shielding"
        if kind == "si_direct" and not row.get("monitor_label"):
            return "undefined_monitor"
    return "scored"


def experimental_value(row: dict, spec: dict, binding: dict,
                       monitor_row: dict | None,
                       decay_by_id: dict) -> tuple[float | None, str]:
    """The measured-side value under the corrected definitions.

    Returns (value, reason).  reason "scored" means the value is usable.
    """
    if row.get("grammar_failure"):
        return None, "internal_consistency_failure"
    kind = spec["kind"]
    if kind == "si_direct":
        return row["measured_si"], "scored"
    if kind in {"sacs_or_si", "rate_ratio", "sigma0", "resonance_integral"}:
        value = row.get("measured_value")
        if value is None:
            return None, "internal_consistency_failure"
        return value, "scored"
    if kind == "eoi_si":
        if monitor_row is None:
            return None, "undefined_monitor"
        if binding is None or binding.get("unbound"):
            return None, "undefined_state_alias"
        am = monitor_row.get("measured_EOI_per_atom")
        if not am or am <= 0:
            return None, "undefined_monitor"
        ratio = row["measured_EOI_per_atom"] / am
        if binding.get("is_fission"):
            return eoi_spectral_index_pulse(ratio, None, MONITOR_HALF_LIFE_S, is_fission=True), "scored"
        # product half-life via the frozen decay identity
        pza = binding.get("decay_product_za") or binding.get("product_za")
        liso = binding.get("decay_product_liso")
        if binding.get("decay_leg_status") not in ("bound", "no_radioactive_product", None) or pza is None:
            return None, "undefined_state_alias"
        try:
            hl = decay_half_life_s(pza, liso or 0, decay_by_id)
        except KeyError:
            return None, "undefined_eoi_history"
        return eoi_spectral_index_pulse(ratio, hl, monitor_hl_of(decay_by_id)), "scored"
    return None, "unsupported_observable_kind"


def monitor_hl_of(decay_by_id: dict) -> float:
    return decay_half_life_s(27058, 0, decay_by_id)


# Amendment 1 R2: the fold-level outcomes the fresh partition legitimately
# produces extend the frozen P17 calculation vocabulary.  The P17 module
# stays byte-identical; the P24 builder validates against this union.
P24_CALCULATION_REASONS = CALCULATION_REASONS | {
    "undefined_monitor",
    "insufficient_spectrum_or_history",
    "non_neutron_incident_particle",
}


def p24_unscored_calculation(*, input_set_id: str, reason: str,
                             interpretation: str, value: float | None = None) -> dict:
    """P17's unscored_calculation record shape under the P24 vocabulary."""
    if not input_set_id:
        raise ValueError("every calculation must reference an input set")
    if reason not in P24_CALCULATION_REASONS or reason == "scored":
        raise ValueError(f"invalid unscored calculation reason {reason!r}")
    if value is not None and not math.isfinite(float(value)):
        raise ValueError("preserved unscored values must be finite")
    return {
        "status": "context" if reason == "different_data_context" else "unscored",
        "reason": reason,
        "value": float(value) if value is not None else None,
        "ratio_C_over_E": None,
        "signed_log_C_over_E": None,
        "material_mismatch": False,
        "input_set_id": input_set_id,
        "interpretation": interpretation,
    }


def find_monitor_row(rows: list[dict]) -> dict | None:
    for row in rows:
        if (row.get("reaction_label") or "").startswith(MONITOR_LABEL) or \
           (row.get("label") or "").startswith(MONITOR_LABEL):
            return row
    return None


def monitor_self_reason(row: dict, monitor_row: dict | None, kind: str) -> str | None:
    """A row whose ratio is 1.0 by construction carries no predictive
    information and is ledgered, never scored: the eoi_si table monitor
    itself, and any si row whose denominator equals its own reaction."""
    if kind == "eoi_si" and monitor_row is not None and row is monitor_row:
        return "monitor_identity_not_predictive"
    if kind in {"si_direct", "sacs_or_si"} and row.get("monitor_label") \
            and row.get("monitor_label") == row.get("reaction_label"):
        return "monitor_identity_not_predictive"
    return None


def internal_consistency(row: dict) -> bool:
    """Where a table prints measured, calculated and C/E (or Diff%) together,
    the printed comparator must match within the printed rounding."""
    meas = row.get("measured_si")
    if meas is None:
        meas = row.get("measured_value")
    calc = row.get("published_calculated_si")
    if calc is None:
        calc = row.get("published_calculated")
    ce = row.get("published_C_over_E")
    if meas is not None and calc is not None and ce not in (None, 0):
        if meas > 0 and calc > 0 and abs(calc / meas - ce) / abs(ce) >= 0.02:
            return False
    diff = row.get("published_diff_percent")
    if meas not in (None, 0) and calc is not None and diff is not None:
        implied = (calc - meas) / meas * 100.0
        # the printed sign convention for Diff% is not frozen: a row fails
        # only when neither sign convention reproduces the printed value
        if min(abs(implied - diff), abs(implied + diff)) > max(0.15, abs(diff) * 0.02):
            return False
    # T34/35: two comparator columns each print value + Diff% against the
    # measured Kayzero column (layout: kz kz-unc mug mug-unc mug-diff
    # irdff irdff-unc irdff-diff)
    nums = row.get("numeric_tokens")
    if row.get("table") in (34, 35) and nums and len(nums) >= 8 and meas not in (None, 0):
        for val_i, diff_i in ((2, 4), (5, 7)):
            printed = nums[diff_i]
            implied = (nums[val_i] - meas) / meas * 100.0
            if min(abs(implied - printed), abs(implied + printed)) > max(0.15, abs(printed) * 0.02):
                return False
    return True


def score_fresh_partition(definitions_record: dict, *, context,
                          parsed_tables: dict | None = None) -> dict:
    """Parse every fresh table once and build the row ledger.

    ``context`` supplies: ``alias_index``, ``decay_by_id``, ``spectra``
    (MAT -> TAB1 record), ``fold_response(binding, spectrum)`` for the
    official fold, ``production_response(lib, binding, spectrum)`` per
    variant library, ``thermal_response``/``production_thermal`` for the
    spectrum-free observables, ``monitor_bindings``, ``libraries``,
    ``input_set_ids`` and ``interpretations``.  Folding callables are
    injected so this module carries no data-acquisition code.

    ``parsed_tables`` lets the driver supply its one-time parse so the
    catalog selection and the scoring share the same parsed rows — the
    partition is never parsed twice.
    """
    alias_index = context["alias_index"]
    decay_by_id = context["decay_by_id"]
    if parsed_tables is None:
        parsed_tables = {t: parse_fresh_table(t) for t in sorted(TABLE_SPECS)}
    rows_out = []
    for table in sorted(TABLE_SPECS):
        spec = TABLE_SPECS[table]
        parsed = parsed_tables[table]
        monitor_row = find_monitor_row(parsed) if spec["kind"] == "eoi_si" else None
        for row in parsed:
            reaction = row.get("reaction_label") or row.get("label", "")
            binding = None
            try:
                binding = row_binding(reaction, alias_index)
            except ValueError:
                pass
            reason = monitor_self_reason(row, monitor_row, spec["kind"]) \
                or inclusion_predicate(row, spec, binding)
            if not internal_consistency(row):
                reason = "internal_consistency_failure"
            exp, exp_reason = experimental_value(row, spec, binding or {}, monitor_row, decay_by_id)
            if reason == "scored" and exp_reason != "scored":
                reason = exp_reason
            if reason == "scored":
                if exp is None or (isinstance(exp, float) and not math.isfinite(exp)):
                    reason = "nonfinite_experimental_value"
                elif not (exp > 0):
                    reason = "nonpositive_experimental_value"

            calculations = {}
            if reason == "scored":
                for variant, lib in context["libraries"].items():
                    calc, keys, calc_reason = fold_variant(variant, lib, row, spec, binding, context)
                    calculations[variant] = score_calculation(
                        exp, calc, input_set_id=context["input_set_ids"][variant],
                        interpretation=context["interpretations"][variant],
                    ) if calc_reason == "scored" else p24_unscored_calculation(
                        input_set_id=context["input_set_ids"][variant],
                        reason=calc_reason, interpretation=context["interpretations"][variant],
                        value=calc)
            else:
                for variant in context["libraries"]:
                    calculations[variant] = p24_unscored_calculation(
                        input_set_id=context["input_set_ids"][variant],
                        reason="not_applicable",
                        interpretation=context["interpretations"][variant])
            unc = row.get("experimental_uncertainty_percent")
            if unc is None:
                unc = row.get("measured_si_uncertainty_percent")
            if unc is None:
                unc = row.get("measured_uncertainty_percent")
            # a malformed printed uncertainty never crashes the one-time
            # read; the raw token remains in source_record for audit
            if unc is not None and not (
                    isinstance(unc, (int, float)) and math.isfinite(float(unc))
                    and float(unc) >= 0.0):
                unc = None
            rows_out.append(p24_make_row(
                row_id=f"p24-{spec['family']}-t{table}-r{row['table_row']:03d}",
                family=spec["family"],
                source_id=f"IRDFF-II:Table-{table}:row-{row['table_row']:03d}",
                source_record=row, observable=spec["kind"],
                unit=row.get("measured_unit") or KIND_UNIT.get(spec["kind"], "dimensionless"),
                experimental_value=exp,
                experimental_uncertainty=unc,
                experimental_uncertainty_unit="percent",
                inclusion_reason=reason, calculations=calculations))
    return {"rows": rows_out, "family_metrics": all_family_metrics(rows_out)}


KIND_UNIT = {
    "si_direct": "dimensionless", "eoi_si": "dimensionless",
    "rate_ratio": "dimensionless",
    "sigma0": "barns", "resonance_integral": "barns",
    "be_production": "at/at-s",
}


def _ratio_fold(variant, lib, binding, monitor_binding, spectrum, context):
    """fold(binding)/fold(monitor_binding) over one spectrum."""
    if variant == "official":
        num, keys_n = context["fold_response"](binding, spectrum)
        den, keys_d = context["fold_response"](monitor_binding, spectrum)
    else:
        num, keys_n, rn = context["production_response"](lib, binding, spectrum)
        den, keys_d, rd = context["production_response"](lib, monitor_binding, spectrum)
        if rn != "scored" or rd != "scored":
            return None, [], "variant_reaction_unavailable"
    if num is None or den in (None, 0):
        return None, [], "variant_reaction_unavailable"
    return num / den, list(keys_n) + list(keys_d), "scored"


def _direct_fold(variant, lib, binding, spectrum, context):
    if variant == "official":
        value, keys = context["fold_response"](binding, spectrum)
        return (value, list(keys), "scored") if value is not None else (None, [], "variant_reaction_unavailable")
    value, keys, reason = context["production_response"](lib, binding, spectrum)
    if reason != "scored" or value is None:
        return None, [], "variant_reaction_unavailable"
    return value, list(keys), "scored"


def fold_variant(variant, lib, row, spec, binding, context):
    """Fold the response for one calculation variant.

    Returns (value, keys, reason).  reason "scored" -> value usable.
    """
    if binding is None or binding.get("unbound"):
        return None, [], "variant_reaction_unavailable"
    kind = spec["kind"]
    if kind == "be_production":
        return None, [], "non_neutron_incident_particle"
    if kind in {"sigma0", "resonance_integral"}:
        # pure-XS observables: no spectrum is needed
        if variant == "official":
            value, keys, reason = context["thermal_response"](binding, kind)
        else:
            value, keys, reason = context["production_thermal"](lib, binding, kind)
        if reason != "scored" or value is None:
            return None, [], reason if reason != "scored" else "variant_reaction_unavailable"
        return value, list(keys), "scored"
    if kind == "rate_ratio":
        pair = row.get("spectrum_pair")
        if not pair:
            return None, [], "insufficient_spectrum_or_history"
        s_num = context["spectra"].get(pair[0])
        s_den = context["spectra"].get(pair[1])
        if s_num is None or s_den is None:
            return None, [], "insufficient_spectrum_or_history"
        if variant == "official":
            num, keys_n = context["fold_response"](binding, s_num)
            den, keys_d = context["fold_response"](binding, s_den)
        else:
            num, keys_n, rn = context["production_response"](lib, binding, s_num)
            den, keys_d, rd = context["production_response"](lib, binding, s_den)
            if rn != "scored" or rd != "scored":
                return None, [], "variant_reaction_unavailable"
        if num is None or den in (None, 0):
            return None, [], "variant_reaction_unavailable"
        return num / den, list(keys_n) + list(keys_d), "scored"
    mat = row_spectrum_mat(row, spec)
    spectrum = context["spectra"].get(mat) if mat is not None else None
    if spectrum is None:
        return None, [], "insufficient_spectrum_or_history"
    if kind in {"eoi_si", "si_direct"}:
        # SI = fold(row)/fold(monitor)
        m_label = row.get("monitor_label") or MONITOR_LABEL
        monitor_binding = context["monitor_bindings"].get(m_label)
        if monitor_binding is None:
            return None, [], "undefined_monitor"
        return _ratio_fold(variant, lib, binding, monitor_binding, spectrum, context)
    if kind == "sacs_or_si":
        m_label = row.get("monitor_label")
        if m_label:
            monitor_binding = context["monitor_bindings"].get(m_label)
            if monitor_binding is None:
                return None, [], "undefined_monitor"
            return _ratio_fold(variant, lib, binding, monitor_binding, spectrum, context)
        # SACS row: direct fold; the published unit is mb, folds are barns
        value, keys, reason = _direct_fold(variant, lib, binding, spectrum, context)
        if reason != "scored":
            return None, keys, reason
        return (value * 1e3 if row.get("measured_unit") == "mb" else value), keys, "scored"
    return None, [], "not_applicable"
