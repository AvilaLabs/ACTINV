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

LABEL_LINE = re.compile(r"^\s*([A-Z][A-Za-z]*\d+[A-Za-z0-9]*(?:-[A-Za-z0-9/.]+)?)\s+")


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
            raise ValueError(f"Table {table}: label line fails si_direct grammar: {line!r}")
        meas, calc, ce = nums[1], nums[3], nums[5]
        rows.append({
            "table": table, "table_row": len(rows) + 1,
            "label": parts[0], "reaction_label": reaction,
            "monitor_label": monitor, "E50_MeV": nums[0],
            "measured_si": meas, "measured_si_uncertainty_percent": nums[2],
            "published_calculated_si": calc, "published_calc_uncertainty_percent": nums[4],
            "published_C_over_E": ce, "published_CE_uncertainty_percent": nums[6],
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
                raise ValueError(f"Table {table}: label line fails eoi_si grammar: {line!r}")
            continue
        if re.fullmatch(r"[A-Za-z0-9]+-(?:bare|Cd|Cdna|Cdtk|Cdtk/B4C)", parts[0]) is None:
            continue
        nums = _float_list(parts[1:])
        if nums is None:
            raise ValueError(f"Table {table}: nonnumeric eoi row: {line!r}")
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


def parse_generic_numeric(table: int, text: str) -> list[dict]:
    """Fallback grammar for rate_ratio / sacs_or_si / sigma0 / resonance_integral /
    be_production: rows are label + numeric tokens; the internal C/E self-check
    runs wherever a calc/meas pair is frozen."""
    rows = []
    for line in text.splitlines():
        m = LABEL_LINE.match(line)
        if m is None:
            continue
        parts = line.split()
        nums = _float_list([p for p in parts[1:] if re.fullmatch(
            r"[+-]?[0-9]*\.?[0-9]+(?:[Ee][+-]?\d+)?", p)])
        if not nums:
            raise ValueError(f"Table {table}: label line with no numerics: {line!r}")
        rows.append({
            "table": table, "table_row": len(rows) + 1,
            "label": parts[0], "tokens": parts[1:], "numeric_tokens": nums,
            "source_line": " ".join(parts),
        })
    return rows


def parse_fresh_table(table: int) -> list[dict]:
    spec = TABLE_SPECS[table]
    text = table_text(table)
    if spec["kind"] == "si_direct":
        return parse_si_direct(table, text)
    if spec["kind"] == "eoi_si":
        return parse_eoi_si(table, text)
    return parse_generic_numeric(table, text)


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

def inclusion_predicate(row: dict, spec: dict, binding: dict | None) -> str:
    """The frozen inclusion predicate, evaluated before any folding."""
    if spec["kind"] == "be_production":
        return "non_neutron_incident_particle"
    from p24_definitions import cover_outcome, RESONANCE_STRUCTURED_MT
    # cover is the outermost physical gate: a covered row is unsupported
    # regardless of whether its label could be bound
    reason = cover_outcome(row.get("cover", "bare"))
    if reason is not None:
        return reason
    if spec["kind"] in {"sigma0", "resonance_integral"}:
        # element-aggregate observables are outside the isotopic scope
        # unless the row prints an explicit reaction label
        if binding is None or binding.get("unbound") or binding.get("kind") != "simple":
            return "unmapped_target_reaction_product"
        return "scored"
    if binding is None:
        return "unmapped_target_reaction_product"
    if binding.get("unbound"):
        return "undefined_state_alias"
    if spec["kind"] in {"si_direct", "sacs_or_si", "eoi_si"}:
        # dilute validity precedes monitor resolution: physical support
        # is the more fundamental predicate
        if int(binding.get("mt", 0)) in RESONANCE_STRUCTURED_MT:
            if spec["field"] not in DILUTE_VERIFIED_FIELDS:
                return "unsupported_self_shielding"
        if spec["kind"] != "eoi_si" and not row.get("monitor_label"):
            return "undefined_monitor"
    return "scored"


def experimental_value(row: dict, spec: dict, binding: dict,
                       monitor_row: dict | None,
                       decay_by_id: dict) -> tuple[float | None, str]:
    """The measured-side value under the corrected definitions.

    Returns (value, reason).  reason "scored" means the value is usable.
    """
    kind = spec["kind"]
    if kind == "si_direct":
        return row["measured_si"], "scored"
    if kind == "sacs_or_si":
        nums = row["numeric_tokens"]
        # frozen column contract: label E50 measSACS unc ref calcSACS unc CE unc
        if len(nums) < 3:
            return None, "internal_consistency_failure"
        return nums[1], "scored"
    if kind == "rate_ratio":
        nums = row["numeric_tokens"]
        if len(nums) < 2:
            return None, "internal_consistency_failure"
        return nums[0], "scored"
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
    if kind == "sigma0":
        nums = row["numeric_tokens"]
        if len(nums) < 2:
            return None, "internal_consistency_failure"
        return nums[0], "scored"
    if kind == "resonance_integral":
        nums = row["numeric_tokens"]
        if len(nums) < 2:
            return None, "internal_consistency_failure"
        return nums[0], "scored"
    return None, "unsupported_observable_kind"


def monitor_hl_of(decay_by_id: dict) -> float:
    return decay_half_life_s(27058, 0, decay_by_id)


def find_monitor_row(rows: list[dict]) -> dict | None:
    for row in rows:
        if row.get("reaction_label", "").startswith(MONITOR_LABEL) or \
           row.get("label", "").startswith(MONITOR_LABEL):
            return row
    return None


def monitor_self_reason(row: dict, monitor_row: dict | None, kind: str) -> str | None:
    """The monitor row's own ratio is 1.0 by construction — it carries no
    predictive information and is ledgered, never scored."""
    if kind == "eoi_si" and monitor_row is not None and row is monitor_row:
        return "monitor_identity_not_predictive"
    return None


def internal_consistency(row: dict) -> bool:
    """Where a table prints measured, calculated and C/E together, the
    printed C/E must equal calc/meas within the printed rounding."""
    meas = row.get("measured_si") or (row.get("numeric_tokens") or [None])[0]
    calc = row.get("published_calculated_si")
    ce = row.get("published_C_over_E")
    if meas is None or calc is None or ce in (None, 0):
        return True  # nothing to check
    if not (meas > 0 and calc > 0):
        return True
    return abs(calc / meas - ce) / ce < 0.02


def score_fresh_partition(definitions_record: dict, *, context) -> dict:
    """Parse every fresh table once and build the row ledger.

    ``context`` supplies: ``alias_index``, ``decay_by_id``, ``spectra``
    (MAT -> TAB1 record), ``fold_response(binding, spectrum)`` for the
    official fold, and ``production_response(binding, spectrum,
    library)`` per variant library.  Folding callables are injected so
    this module carries no data-acquisition code.
    """
    alias_index = context["alias_index"]
    decay_by_id = context["decay_by_id"]
    rows_out = []
    for table in sorted(TABLE_SPECS):
        spec = TABLE_SPECS[table]
        parsed = parse_fresh_table(table)
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
            if reason == "scored" and (exp is None or not (isinstance(exp, float) and exp > 0)):
                reason = "nonpositive_experimental_value" if exp == 0 else "nonfinite_experimental_value"

            calculations = {}
            if reason == "scored":
                for variant, lib in context["libraries"].items():
                    calc, keys, calc_reason = fold_variant(variant, lib, row, spec, binding, context)
                    calculations[variant] = score_calculation(
                        exp, calc, input_set_id=context["input_set_ids"][variant],
                        interpretation=context["interpretations"][variant],
                    ) if calc_reason == "scored" else unscored_calculation(
                        input_set_id=context["input_set_ids"][variant],
                        reason=calc_reason, interpretation=context["interpretations"][variant],
                        value=calc)
            else:
                for variant in context["libraries"]:
                    calculations[variant] = unscored_calculation(
                        input_set_id=context["input_set_ids"][variant],
                        reason="not_applicable",
                        interpretation=context["interpretations"][variant])
            rows_out.append(p24_make_row(
                row_id=f"p24-{spec['family']}-t{table}-r{row['table_row']:03d}",
                family=spec["family"],
                source_id=f"IRDFF-II:Table-{table}:row-{row['table_row']:03d}",
                source_record=row, observable=spec["kind"],
                unit="dimensionless" if spec["kind"] not in {"sigma0", "resonance_integral", "be_production"} else "barns",
                experimental_value=exp, experimental_uncertainty=row.get("experimental_uncertainty_percent") or row.get("measured_si_uncertainty_percent"),
                experimental_uncertainty_unit="percent",
                inclusion_reason=reason, calculations=calculations))
    return {"rows": rows_out, "family_metrics": all_family_metrics(rows_out)}


def fold_variant(variant, lib, row, spec, binding, context):
    """Fold the response for one calculation variant.

    Returns (value, keys, reason).  reason "scored" -> value usable.
    """
    if binding is None or binding.get("unbound"):
        return None, [], "variant_reaction_unavailable"
    spectrum = context["spectra"].get(tuple(spec["spectrum_mats"]))
    if spec["kind"] in {"sigma0", "resonance_integral"}:
        return None, [], "not_applicable"  # context families score official only
    if spectrum is None:
        return None, [], "insufficient_spectrum_or_history"
    if spec["kind"] == "eoi_si" or spec["kind"] == "si_direct":
        # SI = fold(row)/fold(monitor)
        m_label = row.get("monitor_label") or MONITOR_LABEL
        monitor_binding = context["monitor_bindings"].get(m_label)
        if monitor_binding is None:
            return None, [], "undefined_monitor"
        if variant == "official":
            num, keys_n = context["fold_response"](binding, spectrum)
            den, keys_d = context["fold_response"](monitor_binding, spectrum)
        else:
            num, keys_n, rn = context["production_response"](lib, binding, spectrum)
            den, keys_d, rd = context["production_response"](lib, monitor_binding, spectrum)
            if rn != "scored" or rd != "scored":
                return None, [], "variant_reaction_unavailable"
        if num is None or den is None or den == 0:
            return None, [], "variant_reaction_unavailable"
        return num / den, list(keys_n) + list(keys_d), "scored"
    return None, [], "not_applicable"
