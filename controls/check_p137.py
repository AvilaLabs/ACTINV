#!/usr/bin/env python3
"""P137 checker: `actinv report` renders an existing result as FISPACT-style tables.

G0, G1 and G5 are external (protocol hash registry, fmt/clippy/tests, CI); they are recorded as external and do not
enter the verdict. This checker evaluates G2-G4.

The candidate first runs an FNS Fe problem (examples/fns_fe_5min.json with a 2.5 g, 7.874 g/cm3 material, a
`summary` block, photon output (on by default) and a synthetic `radiological` table written into the work
directory: ingestion, inhalation and clearance responses with made-up coefficients, only to give tab3 something to
render). `actinv report` is then run on that result in every format, with `--out` into the work directory. Every
expected table below is built by this script from the result JSON alone (a second implementation of the table
definitions in docs/guide/cli.md, not a reuse of report.rs).

G2  Exact values. Per table, the CSV has the expected header, the expected rows in the expected order (largest
    population or activity first, ties by name), and every cell parses back to the corresponding JSON value:
    numbers bitwise equal as IEEE doubles (a +0/-0 pair is counted and accepted), integers and text equal. Derived
    columns are formed here exactly as documented (heat = alpha + beta + gamma; absolute columns are the per-gram
    value times `material.mass_g`; the activity total is the sequential sum over the activity map). Coverage: the row
    counts equal independent counts taken from the JSON (summary.nuclides entries, nuclides with activity, photon
    groups, dominant entries, elements, steps, Sv-unit radiological responses). The same check is repeated for
    `--step 2 --nuclides ...`. The shortest round-trip `{:e}` spelling of each number is compared too and counted
    (`string_form_mismatches`); only the parsed value gates.
G3  Formats agree. For every table TSV has the same cells as CSV (tabs for commas; quoted CSV cells are compared
    parsed), and the markdown and text renderings hold, in every numeric cell, the CSV value rounded to six
    significant digits (`{:.5e}`); integers and text are identical. The number of significant digits actually used is
    measured (6 or 7) and reported: report.rs formats with `{:.6e}`, which has seven.
G4  Missing sections. The result is edited (copies in the work directory) to drop `summary` from every step, then
    `photon_source`, then `radiological`, then all three. For each variant `report` is run in csv (with `--out`),
    and in markdown and text to standard output: the available tables equal the tables rebuilt from the edited JSON
    (base columns only, values bitwise), the files written are exactly the available tables, no omitted column is a
    header, stderr names every omitted column under its table and every unavailable table with a reason, markdown
    and text carry the same notes/unavailable lines, and the exit status is 0. Exit statuses of the failure paths
    follow report.rs and docs/guide/cli.md: 1 when no requested table can be built, when the file is missing,
    unreadable as JSON, has no steps or an unknown step or nuclide is requested; 2 for a malformed command line.

Usage: check_p137.py --candidate CAND [--reference REF] [--workdir target/p137-work] [--out results/p137_report.json]
"""
import argparse
import copy
import csv
import decimal
import hashlib
import io
import json
import math
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P137_PROTOCOL.md"

ALL_TABLES = ["tab1", "tab2", "tab3", "tab4", "totals", "dominant", "elements", "timeseries"]
FORMATS = {"csv": "csv", "tsv": "tsv", "markdown": "md", "text": "txt"}
MASS_G = 2.5
DENSITY = 7.874
LIST_LIMIT = 12
SIGNIFICANT_DIGITS = 6
FILTER_STEP = 2
FILTER_NUCLIDES = ["Mn54", "Mn56"]

# The synthetic radiological table: coefficients are placeholders (not data) so that tab3 has rows. Co and Fe55
# are left out on purpose so that `missing_active_nuclides` is non-empty.
RADIOLOGICAL_TABLE = {
    "format": "actinv-radiological-table-1",
    "title": "P137 checker fixture (synthetic coefficients, not data)",
    "source": {"citation": "synthetic fixture written by controls/check_p137.py", "edition": "1",
               "url": "https://example.invalid/p137", "jurisdiction": "none"},
    "responses": [
        {"id": "p137-ingestion", "kind": "ingestion_dose", "basis": "synthetic",
         "coefficients": {"Mn56": 2.5e-10, "Mn54": 7.0e-10, "Cr51": 3.8e-11}},
        {"id": "p137-inhalation", "kind": "inhalation_dose", "basis": "synthetic",
         "coefficients": {"Mn56": 1.5e-10, "Mn54": 1.5e-9}},
        {"id": "p137-clearance", "kind": "clearance_index", "basis": "synthetic",
         "coefficients": {"Mn56": 10.0, "Mn54": 0.1, "Cr51": 100.0}},
    ],
}


class GateError(Exception):
    """A gate could not be evaluated (as opposed to evaluating to a mismatch)."""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------------------ JSON access (as report.rs reads it)


def as_f64(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def as_u64(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def ptr(obj, pointer):
    cur = obj
    for part in pointer.strip("/").split("/"):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def need(obj, key, kind, context):
    v = obj.get(key) if isinstance(obj, dict) else None
    out = {"f": as_f64, "u": as_u64, "s": lambda x: x if isinstance(x, str) else None}[kind](v)
    if out is None:
        raise GateError(f"{context} lacks a {kind} '{key}'")
    return out


class Step:
    def __init__(self, index, value):
        self.value = value
        self.step = as_u64(value.get("step")) if as_u64(value.get("step")) is not None else index + 1
        self.time_s = as_f64(value.get("t_s"))
        if self.time_s is None:
            raise GateError(f"step {self.step} lacks a numeric t_s")


def select_steps(result, step=None):
    steps = result.get("steps")
    if not isinstance(steps, list) or not steps:
        raise GateError("no steps")
    views = [Step(i, v) for i, v in enumerate(steps)]
    if step is not None:
        views = [v for v in views if v.step == step]
        if not views:
            raise GateError(f"step {step} is not in the result")
    return views


# ------------------------------------------------------------------------------ expected tables
# A cell is ("t", str) | ("i", int) | ("f", float) | ("e",). A table is
# {"headers": [...], "rows": [[cell]], "omitted": [column names]} or an unavailable reason (str).


def T(s):
    return ("t", s)


def I(n):
    return ("i", n)


def F(x):
    return ("f", x)


E = ("e",)


def head(step):
    return [I(step.step), F(step.time_s)]


def inventory_names(step):
    entries = step.value.get("inventory")
    return [e["nuclide"] for e in entries if isinstance(e, dict) and isinstance(e.get("nuclide"), str)] \
        if isinstance(entries, list) else []


def has_summary_nuclides(steps):
    return all(isinstance(ptr(s.value, "/summary/nuclides"), list) for s in steps)


def mass_multipliers(steps):
    out = [as_f64(ptr(s.value, "/summary/totals/absolute/multiplier")) for s in steps]
    return None if any(m is None for m in out) else out


def sort_desc(rows, key_value, key_name):
    # largest first, ties by name (bytewise)
    return sorted(rows, key=lambda r: (-key_value(r), key_name(r).encode()))


def table_tab1(steps, nuclides):
    summary, mult = has_summary_nuclides(steps), mass_multipliers(steps)
    rows_out = []
    for idx, step in enumerate(steps):
        rows = []
        if summary:
            for e in step.value["summary"]["nuclides"]:
                ctx = f"step {step.step} summary.nuclides entry"
                rows.append((need(e, "nuclide", "s", ctx), need(e, "atoms", "f", ctx), need(e, "grams", "f", ctx)))
        else:
            inv = step.value.get("inventory")
            if not isinstance(inv, list):
                return f"step {step.step} has no inventory"
            for e in inv:
                ctx = f"step {step.step} inventory entry"
                rows.append((need(e, "nuclide", "s", ctx), need(e, "atoms_per_g", "f", ctx), None))
        if nuclides:
            rows = [r for r in rows if r[0] in nuclides]
        for name, atoms, grams in sort_desc(rows, lambda r: r[1], lambda r: r[0]):
            cells = head(step) + [T(name), F(atoms)]
            if grams is not None:
                cells.append(F(grams))
            if mult is not None:
                cells.append(F(atoms * mult[idx]))
                if grams is not None:
                    cells.append(F(grams * mult[idx]))
            rows_out.append(cells)
    headers = ["step", "time_s", "nuclide", "atoms_per_g"]
    omitted = []
    if summary:
        headers.append("mass_g_per_g")
    else:
        omitted.append("mass_g_per_g")
    if mult is not None:
        headers.append("atoms")
    else:
        omitted.append("atoms")
    if mult is not None and summary:
        headers.append("mass_g")
    else:
        omitted.append("mass_g")
    return {"headers": headers, "rows": rows_out, "omitted": omitted}


def table_tab2(steps, nuclides):
    summary, mult = has_summary_nuclides(steps), mass_multipliers(steps)
    rows_out = []
    for idx, step in enumerate(steps):
        rows = []
        if summary:
            for e in step.value["summary"]["nuclides"]:
                ctx = f"step {step.step} summary.nuclides entry"
                activity = need(e, "activity_Bq", "f", ctx)
                if activity > 0.0:
                    rows.append({"name": need(e, "nuclide", "s", ctx), "activity": activity,
                                 "heat": [need(e, "alpha_W", "f", ctx), need(e, "beta_W", "f", ctx),
                                          need(e, "gamma_W", "f", ctx)],
                                 "half_life": as_f64(e.get("half_life_s"))})
        else:
            amap = step.value.get("activity_Bq_per_g")
            if not isinstance(amap, dict):
                return f"step {step.step} has no activity_Bq_per_g"
            for name, v in amap.items():
                activity = as_f64(v)
                if activity is None:
                    raise GateError(f"step {step.step} activity for {name} is not a number")
                if activity > 0.0:
                    rows.append({"name": name, "activity": activity, "heat": None, "half_life": None})
        if nuclides:
            rows = [r for r in rows if r["name"] in nuclides]
        for r in sort_desc(rows, lambda r: r["activity"], lambda r: r["name"]):
            cells = head(step) + [T(r["name"]), F(r["activity"])]
            if r["heat"] is not None:
                a, b, g = r["heat"]
                cells += [F(a + b + g), F(a), F(b), F(g), E if r["half_life"] is None else F(r["half_life"])]
            if mult is not None:
                cells.append(F(r["activity"] * mult[idx]))
                if r["heat"] is not None:
                    a, b, g = r["heat"]
                    cells.append(F((a + b + g) * mult[idx]))
            rows_out.append(cells)
    headers = ["step", "time_s", "nuclide", "activity_Bq_per_g"]
    omitted = []
    heat_cols = ["heat_W_per_g", "alpha_W_per_g", "beta_W_per_g", "gamma_W_per_g", "half_life_s"]
    if summary:
        headers += heat_cols
    else:
        omitted += heat_cols
    if mult is not None:
        headers.append("activity_Bq")
    else:
        omitted.append("activity_Bq")
    if mult is not None and summary:
        headers.append("heat_W")
    else:
        omitted.append("heat_W")
    return {"headers": headers, "rows": rows_out, "omitted": omitted}


def table_tab3(steps):
    rows_out, any_responses, units = [], False, set()
    for step in steps:
        responses = ptr(step.value, "/radiological/responses")
        if not isinstance(responses, list):
            continue
        any_responses = True
        ctx = f"step {step.step} radiological response"
        for r in responses:
            unit = need(r, "unit", "s", ctx)
            if not unit.startswith("Sv"):
                units.add(unit)
                continue
            missing = [m for m in (r.get("missing_active_nuclides") or []) if isinstance(m, str)]
            cells = head(step) + [T(need(r, "id", "s", ctx)), F(need(r, "value", "f", ctx)), T(unit)]
            for key in ("covered_activity_Bq_per_g", "missing_activity_Bq_per_g", "activity_coverage_fraction"):
                cells.append(F(need(r, key, "f", ctx)))
            cells.append(I(need(r, "contributing_nuclide_count", "u", ctx)))
            cells.append(T(";".join(missing)))
            rows_out.append(cells)
    if not any_responses:
        return "the result has no radiological responses"
    if not rows_out:
        return "the result has radiological responses but none is an ingestion or inhalation dose"
    return {"headers": ["step", "time_s", "response", "value", "unit", "covered_activity_Bq_per_g",
                        "missing_activity_Bq_per_g", "activity_coverage_fraction", "contributing_nuclides",
                        "missing_active_nuclides"], "rows": rows_out, "omitted": []}


def table_tab4(steps):
    rows_out, without = [], 0
    for step in steps:
        groups = ptr(step.value, "/photon_source/groups")
        if not isinstance(groups, list):
            without += 1
            continue
        ctx = f"step {step.step} photon group"
        for i, g in enumerate(groups):
            cells = head(step) + [I(i + 1)]
            for key in ("low_eV", "high_eV", "centroid_eV", "photons_s_g", "power_W_g", "photons_s", "power_W"):
                cells.append(F(need(g, key, "f", ctx)))
            rows_out.append(cells)
    if without == len(steps):
        return "the result has no photon_source groups"
    return {"headers": ["step", "time_s", "group", "low_eV", "high_eV", "centroid_eV", "photons_s_g", "power_W_g",
                        "photons_s", "power_W"], "rows": rows_out, "omitted": []}


def pointer_column(steps, header, pointer, is_int):
    cells = []
    for s in steps:
        v = ptr(s.value, pointer)
        if is_int:
            v = as_u64(v)
            cells.append(None if v is None else I(v))
        else:
            v = as_f64(v)
            cells.append(None if v is None else F(v))
    return None if any(c is None for c in cells) else (header, cells)


def activity_total_column(steps):
    cells = []
    for s in steps:
        amap = s.value.get("activity_Bq_per_g")
        if not isinstance(amap, dict):
            return None
        total = 0.0
        for v in amap.values():
            f = as_f64(v)
            if f is None:
                return None
            total = total + f
        cells.append(F(total))
    return ("activity_Bq_per_g", cells)


BASE_HEAT = [("heat_total_W_per_g", "/heat_W_per_g/total"), ("heat_alpha_W_per_g", "/heat_W_per_g/alpha"),
             ("heat_beta_W_per_g", "/heat_W_per_g/beta"), ("heat_gamma_W_per_g", "/heat_W_per_g/gamma")]
SUMMARY_TOTALS = [
    ("activity_Ci_per_g", "/summary/totals/activity_Ci", False),
    ("alpha_activity_Bq_per_g", "/summary/totals/alpha_Bq", False),
    ("beta_activity_Bq_per_g", "/summary/totals/beta_Bq", False),
    ("gamma_activity_Bq_per_g", "/summary/totals/gamma_Bq", False),
    ("activity_excl_H3_Bq_per_g", "/summary/totals/activity_excl_H3_Bq", False),
    ("heat_excl_H3_W_per_g", "/summary/totals/heat_excl_H3_W", False),
    ("mass_g_per_g", "/summary/totals/mass_g", False),
    ("n_nuclides", "/summary/totals/n_nuclides", True),
    ("mode_beta_minus_Bq_per_g", "/summary/totals/activity_by_mode/beta_minus_Bq", False),
    ("mode_ec_beta_plus_Bq_per_g", "/summary/totals/activity_by_mode/ec_beta_plus_Bq", False),
    ("mode_isomeric_transition_Bq_per_g", "/summary/totals/activity_by_mode/isomeric_transition_Bq", False),
    ("mode_alpha_Bq_per_g", "/summary/totals/activity_by_mode/alpha_Bq", False),
    ("mode_neutron_Bq_per_g", "/summary/totals/activity_by_mode/neutron_Bq", False),
    ("mode_spontaneous_fission_Bq_per_g", "/summary/totals/activity_by_mode/spontaneous_fission_Bq", False),
    ("mode_proton_Bq_per_g", "/summary/totals/activity_by_mode/proton_Bq", False),
    ("mode_other_Bq_per_g", "/summary/totals/activity_by_mode/other_Bq", False),
    ("material_mass_g", "/summary/totals/absolute/multiplier", False),
    ("activity_Bq", "/summary/totals/absolute/activity_Bq", False),
]
SUMMARY_SCALED = [("heat_W", "/summary/totals/absolute/heat_W"), ("density_g_cm3", "/summary/totals/per_cm3/multiplier"),
                  ("activity_Bq_per_cm3", "/summary/totals/per_cm3/activity_Bq"),
                  ("heat_W_per_cm3", "/summary/totals/per_cm3/heat_W")]


def per_step_table(steps, columns, omitted):
    headers = ["step", "time_s"] + [h for h, _ in columns]
    rows = [head(step) + [cells[i] for _, cells in columns] for i, step in enumerate(steps)]
    return {"headers": headers, "rows": rows, "omitted": omitted}


def base_total_columns(steps, columns, omitted):
    col = activity_total_column(steps)
    if col is None:
        omitted.append("activity_Bq_per_g")
    else:
        columns.append(col)
    for header, pointer in BASE_HEAT:
        col = pointer_column(steps, header, pointer, False)
        if col is None:
            omitted.append(header)
        else:
            columns.append(col)


def table_totals(steps):
    columns, omitted = [], []
    base_total_columns(steps, columns, omitted)
    for header, pointer, is_int in SUMMARY_TOTALS + [(h, p, False) for h, p in SUMMARY_SCALED]:
        col = pointer_column(steps, header, pointer, is_int)
        if col is None:
            omitted.append(header)
        else:
            columns.append(col)
    summary_present = all(ptr(s.value, "/summary/totals") is not None for s in steps)
    if summary_present:
        omitted = [h for h in omitted if h not in ("density_g_cm3", "activity_Bq_per_cm3", "heat_W_per_cm3")]
    return per_step_table(steps, columns, omitted)


def table_timeseries(steps, nuclides):
    columns, omitted = [], []
    base_total_columns(steps, columns, omitted)
    for header, pointer in (("contact_gamma_air_dose_proxy_Gy_h", "/photon_source/contact_gamma_air_dose_proxy_Gy_h"),
                            ("point_source_gamma_air_dose_Sv_h",
                             "/photon_source/point_source_dose/gamma_air_dose_Sv_h")):
        cells = [E if as_f64(ptr(s.value, pointer)) is None else F(as_f64(ptr(s.value, pointer))) for s in steps]
        if any(c != E for c in cells):
            columns.append((header, cells))
    responses = []
    for s in steps:
        for r in (ptr(s.value, "/radiological/responses") or []):
            if isinstance(r.get("id"), str) and isinstance(r.get("unit"), str) \
                    and r["id"] not in [known for known, _ in responses]:
                responses.append((r["id"], r["unit"]))
    for rid, unit in responses:
        cells = []
        for s in steps:
            found = next((r for r in (ptr(s.value, "/radiological/responses") or []) if r.get("id") == rid), None)
            v = as_f64(found.get("value")) if found else None
            cells.append(E if v is None else F(v))
        columns.append((f"radiological.{rid}[{unit}]", cells))
    for nuclide in nuclides:
        atoms, activity = [], []
        for s in steps:
            inv = s.value.get("inventory")
            v = next((as_f64(e.get("atoms_per_g")) for e in (inv or []) if e.get("nuclide") == nuclide), None)
            atoms.append(F(0.0 if v is None else v))
            amap = s.value.get("activity_Bq_per_g")
            v = as_f64(amap.get(nuclide)) if isinstance(amap, dict) else None
            activity.append(F(0.0 if v is None else v))
        columns.append((f"{nuclide}_atoms_per_g", atoms))
        columns.append((f"{nuclide}_activity_Bq_per_g", activity))
    return per_step_table(steps, columns, omitted)


DOMINANT_LISTS = [("activity", "Bq_per_g"), ("heat", "W_per_g"), ("alpha_heat", "W_per_g"), ("beta_heat", "W_per_g"),
                  ("gamma_heat", "W_per_g")]


def table_dominant(steps):
    rows_out, without = [], 0
    for step in steps:
        block = ptr(step.value, "/summary/dominant")
        if block is None:
            without += 1
            continue
        for quantity, unit in DOMINANT_LISTS:
            ctx = f"step {step.step} summary.dominant.{quantity}"
            lst = block.get(quantity)
            if lst is None:
                raise GateError(f"{ctx} is missing")
            total = need(lst, "total", "f", ctx)
            n90, n99 = need(lst, "n_for_90_percent", "u", ctx), need(lst, "n_for_99_percent", "u", ctx)
            for rank, e in enumerate(lst["top"]):
                rows_out.append(head(step) + [T(quantity), I(rank + 1), T(need(e, "nuclide", "s", ctx)),
                                              F(need(e, "value", "f", ctx)), T(unit),
                                              F(need(e, "percent", "f", ctx)),
                                              F(need(e, "cumulative_percent", "f", ctx)), F(total), I(n90), I(n99)])
    if without == len(steps):
        return "the result has no summary.dominant lists"
    return {"headers": ["step", "time_s", "quantity", "rank", "nuclide", "value", "unit", "percent",
                        "cumulative_percent", "list_total", "n_for_90_percent", "n_for_99_percent"],
            "rows": rows_out, "omitted": []}


def table_elements(steps):
    rows_out, without = [], 0
    for step in steps:
        entries = ptr(step.value, "/summary/elements")
        if not isinstance(entries, list):
            without += 1
            continue
        ctx = f"step {step.step} summary.elements entry"
        for e in entries:
            cells = head(step) + [I(need(e, "Z", "u", ctx)), T(need(e, "symbol", "s", ctx))]
            for key in ("atoms", "grams", "activity_Bq", "alpha_W", "beta_W", "gamma_W"):
                cells.append(F(need(e, key, "f", ctx)))
            rows_out.append(cells)
    if without == len(steps):
        return "the result has no summary.elements table"
    return {"headers": ["step", "time_s", "Z", "element", "atoms_per_g", "mass_g_per_g", "activity_Bq_per_g",
                        "alpha_W_per_g", "beta_W_per_g", "gamma_W_per_g"], "rows": rows_out, "omitted": []}


def expected_tables(result, tables=None, step=None, nuclides=()):
    steps = select_steps(result, step)
    nuclides = list(nuclides)
    for n in nuclides:
        present = any(n in inventory_names(s) or (isinstance(s.value.get("activity_Bq_per_g"), dict)
                                                  and n in s.value["activity_Bq_per_g"]) for s in steps)
        if not present:
            raise GateError(f"nuclide {n} is in no selected step")
    builders = {"tab1": lambda: table_tab1(steps, nuclides), "tab2": lambda: table_tab2(steps, nuclides),
                "tab3": lambda: table_tab3(steps), "tab4": lambda: table_tab4(steps),
                "totals": lambda: table_totals(steps), "dominant": lambda: table_dominant(steps),
                "elements": lambda: table_elements(steps), "timeseries": lambda: table_timeseries(steps, nuclides)}
    return {name: builders[name]() for name in (tables or ALL_TABLES)}


def independent_counts(result):
    """Row counts read straight from the JSON, not through the table builders."""
    steps = result["steps"]
    out = {"steps": len(steps), "tab1": 0, "tab2": 0, "tab3": 0, "tab4": 0, "dominant": 0, "elements": 0}
    for s in steps:
        summary = s.get("summary")
        if summary:
            out["tab1"] += len(summary["nuclides"])
            out["tab2"] += sum(1 for n in summary["nuclides"] if n["activity_Bq"] > 0)
            out["dominant"] += sum(len(summary["dominant"][q]["top"]) for q, _ in DOMINANT_LISTS)
            out["elements"] += len(summary["elements"])
        if s.get("photon_source"):
            out["tab4"] += len(s["photon_source"]["groups"])
        if s.get("radiological"):
            out["tab3"] += sum(1 for r in s["radiological"]["responses"] if r["unit"].startswith("Sv"))
    return out


# ------------------------------------------------------------------------------ number spelling


def bits(x):
    return struct.pack(">d", x)


def rust_exp(x):
    """Rust's `{:e}` of a double: shortest digits that round-trip, `d.ddde-n`, no plus sign, no padding."""
    if x == 0.0:
        return "-0e0" if math.copysign(1.0, x) < 0 else "0e0"
    sign, digits, exp = decimal.Decimal(repr(abs(x))).as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exp += 1
    e10 = len(digits) - 1 + exp
    mantissa = str(digits[0]) + ("." + "".join(map(str, digits[1:])) if len(digits) > 1 else "")
    return f"{'-' if x < 0 else ''}{mantissa}e{e10}"


def rust_fixed_exp(x, places):
    """Rust's `{:.Ne}`: `places` digits after the point, exponent without plus sign or padding."""
    mantissa, exponent = ("%.*e" % (places, x)).split("e")
    return f"{mantissa}e{int(exponent)}"


NUMBER = re.compile(r"^-?\d(\.\d+)?e-?\d+$")
INTEGER = re.compile(r"^\d+$")


# ------------------------------------------------------------------------------ file readers


def read_csv(path, delimiter=","):
    text = Path(path).read_text()
    if delimiter == ",":
        grid = list(csv.reader(io.StringIO(text, newline=""), delimiter=","))
    else:
        grid = [line.split("\t") for line in text.rstrip("\n").split("\n")]
    return grid, text


def read_markdown(path):
    lines = Path(path).read_text().rstrip("\n").split("\n")
    table = [ln for ln in lines if ln.startswith("| ")]
    notes = [ln[len("Note: "):] for ln in lines if ln.startswith("Note: ")]
    grid = []
    for ln in table:
        if not ln.endswith(" |"):
            raise GateError(f"markdown row without closing pipe: {ln[:80]}")
        grid.append(ln[2:-2].replace("\\|", "\x00").split(" | "))
        grid[-1] = [c.replace("\x00", "|") for c in grid[-1]]
    if len(grid) < 2 or set(grid[1]) != {"---"}:
        raise GateError(f"{path}: no markdown header rule")
    return [grid[0]] + grid[2:], notes


def parse_stream_markdown(text):
    """(header, rows, notes) of each `### title` section of a markdown stream, plus unavailable lines."""
    sections = {}
    for block in re.split(r"(?m)^### ", text):
        if not block.strip():
            continue
        title = block.split("\n", 1)[0].strip()
        sections[title] = block
    return sections


def read_text_table(path):
    lines = Path(path).read_text().rstrip("\n").split("\n")
    return parse_text_lines(lines)


def parse_text_lines(lines):
    """Fixed-width table: title, header, rule, rows; notes are `Note: ` lines after the rows."""
    if len(lines) < 3:
        raise GateError("text table shorter than title, header and rule")
    rule = lines[2]
    spans, pos = [], 0
    for part in rule.split("  "):
        spans.append((pos, pos + len(part)))
        pos += len(part) + 2
    notes = [ln[len("Note: "):] for ln in lines[3:] if ln.startswith("Note: ")]
    body = [ln for ln in lines[3:] if not ln.startswith("Note: ")]

    def cells(line):
        return [line[a:b].strip() for a, b in spans]

    return [cells(lines[1])] + [cells(ln) for ln in body], notes, lines[0]


# ------------------------------------------------------------------------------ comparisons


class Tally:
    def __init__(self):
        self.cells = 0
        self.bitwise_equal = 0
        self.signed_zero_pairs = 0
        self.string_form_mismatches = 0
        self.mismatches = []
        self.n_mismatches = 0

    def miss(self, **item):
        self.n_mismatches += 1
        if len(self.mismatches) < LIST_LIMIT:
            self.mismatches.append(item)


def compare_csv_to_expected(name, grid, expected, tally):
    """CSV grid (header + rows of strings) against an expected table, bitwise for numbers."""
    if not grid:
        tally.miss(table=name, problem="empty file")
        return
    if grid[0] != expected["headers"]:
        tally.miss(table=name, problem="header", found=grid[0], expected=expected["headers"])
        return
    rows = grid[1:]
    if len(rows) != len(expected["rows"]):
        tally.miss(table=name, problem="row count", found=len(rows), expected=len(expected["rows"]))
        return
    for r, (found_row, want_row) in enumerate(zip(rows, expected["rows"])):
        if len(found_row) != len(want_row):
            tally.miss(table=name, row=r + 1, problem="cell count", found=len(found_row), expected=len(want_row))
            continue
        for c, (text, cell) in enumerate(zip(found_row, want_row)):
            tally.cells += 1
            where = {"table": name, "row": r + 1, "column": expected["headers"][c]}
            kind = cell[0]
            if kind == "t":
                if text != cell[1]:
                    tally.miss(found=text, expected=cell[1], **where)
                else:
                    tally.bitwise_equal += 1
            elif kind == "i":
                if text != str(cell[1]):
                    tally.miss(found=text, expected=cell[1], **where)
                else:
                    tally.bitwise_equal += 1
            elif kind == "e":
                if text != "":
                    tally.miss(found=text, expected="empty", **where)
                else:
                    tally.bitwise_equal += 1
            else:
                try:
                    value = float(text)
                except ValueError:
                    tally.miss(found=text, expected=cell[1], problem="not a number", **where)
                    continue
                if bits(value) == bits(cell[1]):
                    tally.bitwise_equal += 1
                elif value == 0.0 and cell[1] == 0.0:
                    tally.bitwise_equal += 1
                    tally.signed_zero_pairs += 1
                else:
                    tally.miss(found=text, expected=cell[1], problem="value differs", **where)
                    continue
                if text != rust_exp(cell[1]):
                    tally.string_form_mismatches += 1


def summarise(tally, **extra):
    out = {"cells": tally.cells, "bitwise_equal": tally.bitwise_equal, "signed_zero_pairs": tally.signed_zero_pairs,
           "string_form_mismatches": tally.string_form_mismatches, "n_mismatches": tally.n_mismatches,
           "mismatches": tally.mismatches}
    out.update(extra)
    return out


# ------------------------------------------------------------------------------ running


def run_candidate(binary, spec, out, timeout):
    proc = subprocess.run([binary, "run", str(spec), str(out)], cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode != 0:
        raise GateError(f"{binary} failed on {spec}: {proc.stderr.strip()[-400:]}")
    return json.loads(Path(out).read_text())


def report(binary, args, timeout):
    proc = subprocess.run([binary, "report"] + [str(a) for a in args], cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def make_spec(base_spec, table_path):
    spec = copy.deepcopy(base_spec)
    spec["library"]["path"] = str(ROOT / spec["library"]["path"])
    for key in ("primary", "fallback"):
        if spec.get("decay", {}).get(key):
            spec["decay"][key] = str(ROOT / spec["decay"][key])
    spec["material"] = dict(spec["material"], mass_g=MASS_G, density_g_cm3=DENSITY)
    spec["summary"] = {}
    spec["radiological"] = {"table": {"path": str(table_path), "sha256": sha256(table_path)}}
    spec["title"] = f"{spec.get('title', 'P137')} [P137 report fixture]"
    return spec


def strip_sections(result, summary=False, photon=False, radiological=False):
    out = copy.deepcopy(result)
    for step in out["steps"]:
        if summary:
            step.pop("summary", None)
        if photon:
            step.pop("photon_source", None)
        if radiological:
            step.pop("radiological", None)
    return out


# ------------------------------------------------------------------------------ gates


def gate_g2(binary, work, result, result_path, timeout):
    out_dir = work / "csv"
    code, _stdout, stderr = report(binary, [result_path, "--format", "csv", "--out", out_dir], timeout)
    if code != 0:
        raise GateError(f"report --format csv exited {code}: {stderr.strip()[-300:]}")
    tally = Tally()
    expected = expected_tables(result)
    unavailable = {n: t for n, t in expected.items() if isinstance(t, str)}
    if unavailable:
        raise GateError(f"the fixture result cannot supply {sorted(unavailable)}: {unavailable}")
    per_table = {}
    for name in ALL_TABLES:
        path = out_dir / f"{name}.csv"
        if not path.exists():
            tally.miss(table=name, problem="file not written")
            continue
        grid, _ = read_csv(path)
        before = tally.n_mismatches
        compare_csv_to_expected(name, grid, expected[name], tally)
        per_table[name] = {"rows": len(expected[name]["rows"]), "columns": len(expected[name]["headers"]),
                           "mismatches": tally.n_mismatches - before}
    counts = independent_counts(result)
    coverage = {"steps": counts["steps"]}
    coverage_ok = True
    for name in ("tab1", "tab2", "tab3", "tab4", "dominant", "elements"):
        coverage[name] = {"json_values": counts[name], "table_rows": per_table.get(name, {}).get("rows")}
        coverage_ok = coverage_ok and counts[name] == per_table.get(name, {}).get("rows") and counts[name] > 0
    for name in ("totals", "timeseries"):
        coverage[name] = {"json_values": counts["steps"], "table_rows": per_table.get(name, {}).get("rows")}
        coverage_ok = coverage_ok and per_table.get(name, {}).get("rows") == counts["steps"]
    # filters: one step, two nuclides
    filter_dir = work / "csv_filtered"
    args = [result_path, "--format", "csv", "--out", filter_dir, "--step", FILTER_STEP,
            "--nuclides", ",".join(FILTER_NUCLIDES), "--tables", "tab1,tab2,timeseries,totals"]
    code, _out, stderr = report(binary, args, timeout)
    filt = Tally()
    if code != 0:
        filt.miss(problem=f"filtered report exited {code}", stderr=stderr.strip()[-300:])
    else:
        want = expected_tables(result, ["tab1", "tab2", "timeseries", "totals"], FILTER_STEP, FILTER_NUCLIDES)
        for name, table in want.items():
            path = filter_dir / f"{name}.csv"
            if not path.exists():
                filt.miss(table=name, problem="file not written")
                continue
            compare_csv_to_expected(name, read_csv(path)[0], table, filt)
    ok = tally.n_mismatches == 0 and tally.cells > 0 and coverage_ok and filt.n_mismatches == 0 and filt.cells > 0
    return {"pass": bool(ok), **summarise(tally, tables=per_table, coverage=coverage, coverage_ok=coverage_ok,
                                          filtered=summarise(filt, step=FILTER_STEP, nuclides=FILTER_NUCLIDES))}


def classify(text):
    if text == "":
        return "empty"
    if INTEGER.match(text):
        return "int"
    if NUMBER.match(text):
        return "num"
    return "text"


def gate_g3(binary, work, result_path, timeout):
    dirs = {}
    for fmt in FORMATS:
        dirs[fmt] = work / fmt
        if fmt == "csv":
            dirs[fmt] = work / "csv"
            if not (work / "csv" / "tab1.csv").exists():
                code, _o, stderr = report(binary, [result_path, "--format", "csv", "--out", dirs[fmt]], timeout)
                if code != 0:
                    raise GateError(f"report csv exited {code}: {stderr.strip()[-300:]}")
            continue
        code, _o, stderr = report(binary, [result_path, "--format", fmt, "--out", dirs[fmt]], timeout)
        if code != 0:
            raise GateError(f"report --format {fmt} exited {code}: {stderr.strip()[-300:]}")
    problems, n_tsv, n_num, n_int_text = [], 0, 0, 0
    digits_seen = {}
    quoted_csv_cells = 0

    def miss(**item):
        problems.append(item)

    expected_places = SIGNIFICANT_DIGITS - 1
    for name in ALL_TABLES:
        csv_grid, csv_text = read_csv(dirs["csv"] / f"{name}.csv")
        tsv_grid, tsv_text = read_csv(dirs["tsv"] / f"{name}.tsv", "\t")
        quoted_csv_cells += csv_text.count('"')
        if tsv_grid != csv_grid:
            miss(table=name, format="tsv", problem="cells differ from csv")
        elif '"' not in csv_text and csv_text.replace(",", "\t") != tsv_text:
            miss(table=name, format="tsv", problem="not csv with the separator changed")
        else:
            n_tsv += 1
        md_grid, md_notes = read_markdown(dirs["markdown"] / f"{name}.md")
        tx_grid, tx_notes, _title = read_text_table(dirs["text"] / f"{name}.txt")
        for fmt, grid in (("markdown", md_grid), ("text", tx_grid)):
            if len(grid) != len(csv_grid) or grid[0] != csv_grid[0]:
                miss(table=name, format=fmt, problem="header or row count differs from csv", found=len(grid),
                     expected=len(csv_grid))
                continue
            for r, (row_c, row_f) in enumerate(zip(csv_grid[1:], grid[1:])):
                if len(row_c) != len(row_f):
                    miss(table=name, format=fmt, row=r + 1, problem="cell count")
                    continue
                for c, (cv, fv) in enumerate(zip(row_c, row_f)):
                    kind = classify(cv)
                    where = {"table": name, "format": fmt, "row": r + 1, "column": csv_grid[0][c]}
                    if kind == "num":
                        n_num += 1
                        value = float(cv)
                        want = rust_fixed_exp(value, expected_places)
                        alt = rust_fixed_exp(value, expected_places + 1)
                        if fv == alt:
                            digits_seen["7_significant"] = digits_seen.get("7_significant", 0) + 1
                        elif fv == want:
                            digits_seen["6_significant"] = digits_seen.get("6_significant", 0) + 1
                        else:
                            digits_seen["other"] = digits_seen.get("other", 0) + 1
                        if fv != want:
                            miss(found=fv, csv=cv, expected=want, **where)
                    else:
                        n_int_text += 1
                        if fv != cv:
                            miss(found=fv, csv=cv, **where)
        if sorted(md_notes) != sorted(tx_notes):
            miss(table=name, problem="markdown and text notes differ", markdown=md_notes[:3], text=tx_notes[:3])
    ok = not problems and n_tsv == len(ALL_TABLES) and n_num > 0
    return {"pass": bool(ok), "tsv_tables_equal_csv": n_tsv, "numeric_cells_per_format_compared": n_num // 2,
            "integer_text_empty_cells_compared": n_int_text // 2, "digits_observed": digits_seen,
            "required_significant_digits": SIGNIFICANT_DIGITS, "quoted_csv_cells": quoted_csv_cells,
            "n_problems": len(problems), "problems": problems[:LIST_LIMIT],
            "note": "report.rs renders markdown and text with sci() = format!(\"{:.6e}\"), which has seven "
                    "significant digits; the protocol and docs/guide/cli.md say six"}


VARIANTS = {"no_summary": dict(summary=True), "no_photon": dict(photon=True),
            "no_radiological": dict(radiological=True), "base_only": dict(summary=True, photon=True,
                                                                           radiological=True)}


def note_lines(stderr):
    return [ln for ln in stderr.splitlines() if ln.startswith("note: ")]


def gate_g4(binary, work, result, timeout):
    cases = {}
    for label, strip in VARIANTS.items():
        def one(label=label, strip=strip):
            edited = strip_sections(result, **strip)
            path = work / f"{label}.json"
            path.write_text(json.dumps(edited))
            expected = expected_tables(edited)
            available = {n: t for n, t in expected.items() if isinstance(t, dict)}
            unavailable = {n: t for n, t in expected.items() if isinstance(t, str)}
            problems = []
            out_dir = work / f"{label}_csv"
            code, stdout, stderr = report(binary, [path, "--format", "csv", "--out", out_dir], timeout)
            if code != 0:
                raise GateError(f"report exited {code} (expected 0): {stderr.strip()[-300:]}")
            written = sorted(p.name for p in out_dir.iterdir()) if out_dir.exists() else []
            if written != sorted(f"{n}.csv" for n in available):
                problems.append({"problem": "files written differ from the available tables", "written": written,
                                 "expected": sorted(f"{n}.csv" for n in available)})
            tally = Tally()
            for name, table in available.items():
                path_csv = out_dir / f"{name}.csv"
                if path_csv.exists():
                    grid = read_csv(path_csv)[0]
                    compare_csv_to_expected(name, grid, table, tally)
                    for column in table["omitted"]:
                        if column in grid[0]:
                            problems.append({"problem": "omitted column is present", "table": name,
                                             "column": column})
            notes = note_lines(stderr)
            for name, table in available.items():
                if table["omitted"]:
                    line = next((ln for ln in notes if ln.startswith(f"note: {name}:") and "omitted" in ln), None)
                    if line is None:
                        problems.append({"problem": "no omitted-columns note on stderr", "table": name})
                    elif not line.endswith(", ".join(table["omitted"])):
                        problems.append({"problem": "omitted columns not named (in order) on stderr",
                                         "table": name, "columns": table["omitted"], "line": line[-200:]})
            for name in unavailable:
                if not any(ln.startswith(f"note: {name}: unavailable:") and len(ln.split("unavailable:", 1)[1].strip()) > 3
                           for ln in notes):
                    problems.append({"problem": "unavailable table not named with a reason on stderr", "table": name})
            for name in available:
                if any(ln.startswith(f"note: {name}: unavailable") for ln in notes):
                    problems.append({"problem": "available table reported unavailable", "table": name})
            # markdown and text streams (no --out): unavailable tables are announced, omitted columns noted
            streams = {}
            for fmt in ("markdown", "text"):
                code, stdout, err = report(binary, [path, "--format", fmt], timeout)
                if code != 0:
                    problems.append({"problem": f"{fmt} stream exited {code}"})
                    continue
                streams[fmt] = stdout
                for name, reason in unavailable.items():
                    marker = "Unavailable:" if fmt == "markdown" else "unavailable:"
                    title = {"tab3": "TAB3", "tab4": "TAB4", "dominant": "Dominant", "elements": "Elements"}[name]
                    if title not in stdout or marker not in stdout:
                        problems.append({"problem": "unavailable table not announced in stream", "format": fmt,
                                         "table": name})
                note_text = [ln for ln in stdout.splitlines() if ln.startswith("Note: columns omitted")]
                for name, table in available.items():
                    if table["omitted"] and not any(ln.endswith(", ".join(table["omitted"])) for ln in note_text):
                        problems.append({"problem": "omitted columns not named in stream", "format": fmt,
                                         "table": name, "columns": table["omitted"]})
            return {"pass": not problems and tally.n_mismatches == 0 and tally.cells > 0,
                    "available": sorted(available), "unavailable": {k: v for k, v in unavailable.items()},
                    "omitted_columns": {n: t["omitted"] for n, t in available.items() if t["omitted"]},
                    "n_problems": len(problems), "problems": problems[:LIST_LIMIT], **summarise(tally)}
        try:
            cases[label] = one()
        except (GateError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
            cases[label] = {"pass": False, "error": f"{type(error).__name__}: {error}"}

    # exit statuses of the failure paths
    exits, bad = {}, []
    summary_free = work / "base_only.json"
    full = work / "result.json"
    checks = [
        ("no table buildable (tab4, tab3, dominant, elements on a base-only result)",
         [summary_free, "--tables", "tab3,tab4,dominant,elements"], 1),
        ("missing file", [work / "does-not-exist.json"], 1),
        ("not JSON", [ROOT / "protocols" / "ACTINV-P137_PROTOCOL.md"], 1),
        ("step beyond the result", [full, "--step", "9999"], 1),
        ("nuclide in no step", [full, "--nuclides", "Zz999"], 1),
        ("unknown table", [full, "--tables", "tab9"], 2),
        ("unknown option", [full, "--bogus", "1"], 2),
        ("bad format", [full, "--format", "xml"], 2),
        ("no result file", [], 2),
        ("step zero", [full, "--step", "0"], 2),
        ("one buildable table is enough", [summary_free, "--tables", "tab4,tab1"], 0),
    ]
    for label, args, want in checks:
        code, _o, stderr = report(binary, args, timeout)
        exits[label] = {"exit": code, "expected": want, "stderr": stderr.strip()[:160]}
        if code != want:
            bad.append(label)
        elif want != 0 and not stderr.strip():
            bad.append(label + " (no message on stderr)")
    cases["exit_statuses"] = {"pass": not bad, "cases": exits, "failed": bad}
    return {"pass": all(c.get("pass") for c in cases.values()), "cases": cases}


# ------------------------------------------------------------------------------ main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--reference", help="recorded in the result file only; the report command is not in the reference")
    ap.add_argument("--workdir", default="target/p137-work")
    ap.add_argument("--out", default="results/p137_report.json")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)
    cand = str(Path(args.candidate).resolve())
    work = (ROOT / args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    table_path = work / "radiological_fixture.json"
    table_path.write_text(json.dumps(RADIOLOGICAL_TABLE, indent=1) + "\n")
    spec = make_spec(json.loads(GATE_SPEC.read_text()), table_path)
    spec_path = work / "fixture.spec.json"
    spec_path.write_text(json.dumps(spec))
    result_path = work / "result.json"

    gates = {g: {"pass": None, "external": True} for g in ("G0", "G1", "G5")}
    gates["G0"]["note"] = "protocol SHA-256 in protocols/protocol_hash.txt (external)"
    gates["G1"]["note"] = "fmt, clippy, workspace tests including report (external)"
    gates["G5"]["note"] = "CI green on the pushed commit (external)"
    result = None
    try:
        result = run_candidate(cand, spec_path, result_path, args.timeout)
    except (GateError, subprocess.TimeoutExpired) as error:
        for g in ("G2", "G3", "G4"):
            gates[g] = {"pass": False, "error": f"fixture run failed: {error}"}

    def guarded(fn, *a):
        try:
            return fn(*a)
        except (GateError, KeyError, ValueError, OSError, subprocess.TimeoutExpired) as error:
            return {"pass": False, "error": f"{type(error).__name__}: {error}"}

    if result is not None:
        gates["G2"] = guarded(gate_g2, cand, work, result, result_path, args.timeout)
        gates["G3"] = guarded(gate_g3, cand, work, result_path, args.timeout)
        gates["G4"] = guarded(gate_g4, cand, work, result, args.timeout)

    judged = ("G2", "G3", "G4")
    verdict = all(gates[g].get("pass") is True for g in judged)
    record = {"protocol": "protocols/ACTINV-P137_PROTOCOL.md", "protocol_sha256": sha256(PROTOCOL),
              "reference_sha256": sha256(Path(args.reference).resolve()) if args.reference else None,
              "candidate_sha256": sha256(cand), "checker_sha256": sha256(__file__),
              "fixture": {"spec": str(spec_path), "spec_sha256": sha256(spec_path),
                          "radiological_table_sha256": sha256(table_path), "mass_g": MASS_G,
                          "density_g_cm3": DENSITY},
              "gates": gates, "verdict": "PASS" if verdict else "FAIL"}
    out = ROOT / args.out
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for g in judged:
        gate = gates[g]
        print(f"P137 {g}: {'PASS' if gate.get('pass') else 'FAIL'}")
        if "error" in gate:
            print(f"  {g}: ERROR {gate['error']}")
        if g == "G2" and "cells" in gate:
            print(f"  G2: {gate['cells']} cells, {gate['bitwise_equal']} equal, {gate['n_mismatches']} mismatches, "
                  f"coverage {'ok' if gate['coverage_ok'] else 'FAIL'}, filtered {gate['filtered']['cells']} cells "
                  f"{gate['filtered']['n_mismatches']} mismatches")
        if g == "G3" and "n_problems" in gate:
            print(f"  G3: {gate['tsv_tables_equal_csv']} tables tsv==csv, "
                  f"{gate['numeric_cells_per_format_compared']} numeric cells per format, "
                  f"{gate['n_problems']} problems, digits {gate['digits_observed']}")
        if g == "G4":
            for label, case in (gate.get("cases") or {}).items():
                detail = case.get("error") or (f"{case['n_problems']} problems" if "n_problems" in case
                                               else f"failed {case.get('failed')}")
                print(f"  G4/{label}: {'PASS' if case.get('pass') else 'FAIL'} ({detail})")
    print("P137 G0, G1, G5: external (not evaluated here)")
    print(f"P137: {record['verdict']} ({out})")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
