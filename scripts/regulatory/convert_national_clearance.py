#!/usr/bin/env python3
"""Convert national clearance and out-of-scope concentration tables into ACTINV radiological tables.

Inputs are the official XML of three instruments, archived under
`controls/fixtures/clearance_sources/` (pinned by SHA-256):

  de/BJNR203600018.xml   Strahlenschutzverordnung (StrlSchV 2018), Anlage 4 Tabelle 1 and Tabelle 2
  uk/data.xml            Environmental Permitting (England and Wales) Regulations 2016,
                         Schedule 23 Part 3 (Tables 2 and 3)
  ca/SOR-2000-207.xml    Nuclear Substances and Radiation Devices Regulations, Schedule 2
                         (Unconditional Clearance Levels)

Outputs, in the existing `actinv-radiological-table-1` format (response kind `clearance_index`,
coefficients in Bq/kg converted from the printed Bq/g by x1000):

  de-strlschv-2018-anlage-4.json         one response per Bq/g route: unrestricted clearance
                                         (column 3, which is also the Freigrenze as specific
                                         activity) and the specific-clearance columns 6-11 and 14
  uk-epr-2016-sch23-pt3-table-2.json     Table 2 "Concentration of radionuclides" (column 2, Bq/g)
  ca-nsrdr-2000-207-schedule-2.json      Schedule 2 "Unconditional Clearance Levels" (Bq/g)
  <name>.rows.json                       per-row source text, markers, footnotes and the reason a
                                         source row is not in a table

Progeny markers.  Germany: "+" marks a parent whose Tabelle 2 daughters are fully covered by the
parent's value.  UK: the suffix "+" (or "sec") means the parent is in secular equilibrium with the
daughters named in Table 3, and the value is for the parent alone but already takes the daughters
into account.  Canada: footnote a lists parents "and their progeny included in secular
equilibrium".  In every table the progeny of a parent with a coefficient are written to the
response's `covered_by_parent` map, exactly as the Directive 2013/59/Euratom tables do.

Usage:
  python3 -I scripts/regulatory/convert_national_clearance.py SOURCES_DIR OUT_DIR

Every number is read from the XML; nothing is typed in.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path


def load_common():
    path = Path(__file__).resolve().parent / "regulatory_common.py"
    spec = importlib.util.spec_from_file_location("regulatory_common", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_common()

CONVERTER = "scripts/regulatory/convert_national_clearance.py"
FORM_RULE = "strictest limit; progeny coverage follows the chosen row"
RETRIEVED = "2026-10-30"
SOURCE_DIR_REL = "controls/fixtures/clearance_sources"

DE_FILE = "de/BJNR203600018.xml"
UK_FILE = "uk/data.xml"
CA_FILE = "ca/SOR-2000-207.xml"
PINNED_SHA256 = {
    DE_FILE: "aeb25a919ed28af9848623e4c7618ceb498655945efcfd5aa2e93d93d2ab0925",
    UK_FILE: "3a2fb528b1f80e45438adb2ab6a3d5dafdd5be011bf4fcf599f54eb85902508d",
    CA_FILE: "55d498a0daf3b4ab736fd416510c898519b4d002cea648301916e69ba5e211ef",
}
# The archive the German XML was extracted from (not committed): recorded for provenance only.
DE_ARCHIVE_SHA256 = "683ba2d0263a448ea5a1be78dc1ac00d59a019eff2d812510bad92003f059d8a"
# The UK fixture is legislation.gov.uk's data.xml with every `key-<32 hex>` cross-reference id (CommentaryRef, EffectId,
# Commentary ids) rewritten to `ref-<32 hex>`. GitHub secret scanning reads those ids as Mailgun API keys and refuses the
# push. The rewrite is exact and reversible (`ref-` back to `key-` gives the official file), and no table cell uses the ids.
UK_OFFICIAL_SHA256 = "da9dbf2e66cd043408c3f237d3d996c2d878bd7b8eda4cfeba5de5a96c816702"

DE_NAME = "de-strlschv-2018-anlage-4"
UK_NAME = "uk-epr-2016-sch23-pt3-table-2"
CA_NAME = "ca-nsrdr-2000-207-schedule-2"

DE_LICENCE = (
    "German federal legal text. Official works (amtliche Werke) of this kind have no copyright "
    "protection under section 5(1) of the Urheberrechtsgesetz (UrhG). Published by the "
    "Bundesministerium der Justiz at gesetze-im-internet.de. Not an official version: only the "
    "Bundesgesetzblatt is authentic."
)
UK_LICENCE = (
    "Contains public sector information licensed under the Open Government Licence v3.0 "
    "(https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). Source: "
    "legislation.gov.uk, SI 2016/1154, Crown copyright. Not an official version of the law."
)
CA_LICENCE = (
    "Reproduced from the Nuclear Substances and Radiation Devices Regulations (SOR/2000-207), "
    "Justice Laws Website, Government of Canada, under the Reproduction of Federal Law Order "
    "(SI/97-5). This is not an official version: the Canada Gazette and the official "
    "consolidation govern."
)

# Routes of the German table: (response id, column, short label). Surface columns (Bq/cm2), the total
# activity Freigrenze (column 2) and the half-life are not Bq/g concentrations and are not converted;
# column 4 (HRQ) is not read at all.
DE_ROUTES = [
    ("unrestricted-clearance", 3, "unrestricted clearance of solid and liquid material (also the Freigrenze as specific activity)"),
    ("rubble-gt-1000-mg-a", 6, "specific clearance of building rubble of more than 1000 Mg/a"),
    ("soil", 7, "specific clearance of soil areas"),
    ("landfill-le-100-mg-a", 8, "specific clearance of solid material up to 100 Mg/a for disposal in landfills"),
    ("incineration-le-100-mg-a", 9, "specific clearance of material up to 100 Mg/a for disposal in incineration plants"),
    ("landfill-le-1000-mg-a", 10, "specific clearance of solid material up to 1000 Mg/a for disposal in landfills"),
    ("incineration-le-1000-mg-a", 11, "specific clearance of material up to 1000 Mg/a for disposal in incineration plants"),
    ("scrap-metal-recycling", 14, "specific clearance of scrap metal for recycling"),
]
DE_SIDECAR_COLUMNS = [2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

# Element names as the Canadian regulation prints them, mapped to symbols.
CA_ELEMENTS = {
    "Hydrogen": "H", "Beryllium": "Be", "Carbon": "C", "Nitrogen": "N", "Oxygen": "O", "Fluorine": "F",
    "Sodium": "Na", "Magnesium": "Mg", "Aluminum": "Al", "Aluminium": "Al", "Silicon": "Si",
    "Phosphorous": "P", "Phosphorus": "P", "Sulphur": "S", "Chlorine": "Cl", "Argon": "Ar",
    "Potassium": "K", "Calcium": "Ca", "Scandium": "Sc", "Titanium": "Ti", "Vanadium": "V",
    "Chromium": "Cr", "Manganese": "Mn", "Iron": "Fe", "Cobalt": "Co", "Nickel": "Ni", "Copper": "Cu",
    "Zinc": "Zn", "Gallium": "Ga", "Germanium": "Ge", "Arsenic": "As", "Selenium": "Se",
    "Bromine": "Br", "Krypton": "Kr", "Rubidium": "Rb", "Strontium": "Sr", "Yttrium": "Y",
    "Zirconium": "Zr", "Niobium": "Nb", "Molybdenum": "Mo", "Technetium": "Tc", "Ruthenium": "Ru",
    "Rhodium": "Rh", "Palladium": "Pd", "Silver": "Ag", "Cadmium": "Cd", "Indium": "In", "Tin": "Sn",
    "Antimony": "Sb", "Tellurium": "Te", "Iodine": "I", "Xenon": "Xe", "Cesium": "Cs", "Barium": "Ba",
    "Lanthanum": "La", "Cerium": "Ce", "Praseodymium": "Pr", "Neodymium": "Nd", "Promethium": "Pm",
    "Samarium": "Sm", "Europium": "Eu", "Gadolinium": "Gd", "Terbium": "Tb", "Dysprosium": "Dy",
    "Holmium": "Ho", "Erbium": "Er", "Thulium": "Tm", "Ytterbium": "Yb", "Lutetium": "Lu",
    "Hafnium": "Hf", "Tantalum": "Ta", "Tungsten": "W", "Rhenium": "Re", "Osmium": "Os",
    "Iridium": "Ir", "Platinum": "Pt", "Gold": "Au", "Mercury": "Hg", "Thallium": "Tl", "Lead": "Pb",
    "Bismuth": "Bi", "Polonium": "Po", "Astatine": "At", "Radon": "Rn", "Francium": "Fr",
    "Radium": "Ra", "Actinium": "Ac", "Thorium": "Th", "Protactinium": "Pa", "Uranium": "U",
    "Neptunium": "Np", "Plutonium": "Pu", "Americium": "Am", "Curium": "Cm", "Berkelium": "Bk",
    "Californium": "Cf", "Einsteinium": "Es", "Fermium": "Fm",
}


# ---------------------------------------------------------------------------
# Text and number helpers
# ---------------------------------------------------------------------------


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def squeeze(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return re.sub(r"\^\(\s*([^)]*?)\s*\)", r"^(\1)", text)


def cell_text(element: ET.Element, refs: list[str] | None = None) -> str:
    """Text of a table cell; superscripts are written ^(x), footnote references are collected in refs."""
    parts = [element.text or ""]
    for child in element:
        name = local(child.tag).lower()
        if name in ("footnoteref", "commentaryref"):
            if refs is not None:
                refs.append((child.text or "").strip() or child.get("Ref", ""))
        elif name in ("sup", "superior"):
            parts.append("^(" + cell_text(child, refs).strip() + ")")
        elif name == "br":
            parts.append(" ")
        else:
            parts.append(cell_text(child, refs))
        parts.append(child.tail or "")
    return squeeze("".join(parts))


def decimal_of(text: str) -> Decimal:
    """The exact decimal of a printed value: '1 E+9', '1,6 E+6', '10^(2)', '1 × 10^(–1)', '0.1', '3'."""
    s = text.replace("–", "-").replace("−", "-").replace("\xa0", " ").strip()
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*E\s*([+-]?\d+)", s)
    if match:
        return Decimal(match.group(1).replace(",", ".")).scaleb(int(match.group(2)))
    match = re.fullmatch(r"(?:(\d+(?:[.,]\d+)?)\s*[×x]\s*)?10\^\(([+-]?\d+)\)", s)
    if match:
        return Decimal((match.group(1) or "1").replace(",", ".")).scaleb(int(match.group(2)))
    if re.fullmatch(r"\d+(?:[.,]\d+)?", s):
        return Decimal(s.replace(",", "."))
    raise ValueError(f"unreadable value {text!r}")


def bq_per_kg(bq_per_g: Decimal) -> float:
    """Bq/g -> Bq/kg; the decimal product is rounded once, to the nearest double."""
    return float(bq_per_g * 1000)


def key_of(printed: str, german: bool = False) -> str | None:
    """ACTINV key of a printed nuclide ('Ag-108m+' -> Ag108m1; German 'Hf-178n' -> Hf178m2)."""
    name = printed.strip()
    name = re.sub(r"(\+|sec)$", "", name).strip()
    if german:
        name = re.sub(r"(\d)n$", r"\1m2", name)
    parsed = C.parse_nuclide(name)
    return parsed[0] if parsed else None


def progeny_of(text: str, german: bool = False) -> tuple[list[str], list[str]]:
    pieces = [p.strip() for p in text.split(",") if p.strip()]
    pieces = [re.sub(r"(\+|sec)$", "", p).strip() for p in pieces]
    if german:
        pieces = [re.sub(r"(\d)n$", r"\1m2", p) for p in pieces]
    return C.progeny_keys(pieces)


def number_entry(text: str) -> dict:
    return {"text": text, "number": float(decimal_of(text))}


# ---------------------------------------------------------------------------
# Germany
# ---------------------------------------------------------------------------

DE_NUCLIDE = re.compile(r"^([A-Z][a-z]?-\d+[mn]?)(\+?)\s*(.*)$")


def parse_de(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(r"<!DOCTYPE[^>]*>", "", raw)
    root = ET.fromstring(raw.encode("utf-8"))
    norm = next(n for n in root.iter("norm") if n.findtext("./metadaten/enbez") == "Anlage 4")
    tables = list(norm.iter("table"))
    table1 = next(t for t in tables if t.get("tabstyle") == "Tabelle1")

    def cells(row) -> dict[int, str]:
        out = {}
        for entry in row.findall("entry"):
            name = entry.get("colname") or ""
            if name.startswith("col"):
                out[int(name[3:])] = cell_text(entry)
        return out

    headers: dict[int, str] = {}
    rows = []
    qualified = []
    for position, row in enumerate(table1.findall(".//row"), start=1):
        c = cells(row)
        match = DE_NUCLIDE.match(c.get(1, ""))
        if not match:
            for number, text in c.items():
                if text and not text.isdigit():
                    headers[number] = text if number not in headers else headers[number] + " " + text
            continue
        printed, plus, qualifier = match.group(1), match.group(2), match.group(3)
        record = {
            "source_row": position,
            "printed": printed + plus,
            "nuclide": key_of(printed, german=True),
            "with_progeny": bool(plus),
            "qualifier": qualifier or None,
            "values": {number: c.get(number, "") for number in DE_SIDECAR_COLUMNS},
            "cells": c,
        }
        (qualified if qualifier else rows).append(record)

    last = tables[-1]
    table2 = []
    for row in last.findall(".//row"):
        texts = [cell_text(e) for e in row.findall("entry")]
        table2.append(texts)
    if table2[0] != ["Mutternuklid", "Tochternuklide"]:
        raise ValueError(f"unexpected Anlage 4 Tabelle 2 header {table2[0]!r}")
    explanations = []
    for row in tables[0].findall(".//row"):
        texts = [t for t in (cell_text(e) for e in row.findall("entry")) if t]
        if texts:
            explanations.append(" | ".join(texts))

    meta = root.find("norm/metadaten")
    return {
        "headers": headers,
        "rows": rows,
        "qualified": qualified,
        "table2": table2[1:],
        "explanations": explanations,
        "footnotes": [cell_text(f) for f in norm.iter("fussnoten")],
        "version": {
            "ausfertigung_datum": meta.findtext("ausfertigung-datum"),
            "fundstelle": meta.findtext("fundstelle/zitstelle"),
            "stand": [[s.findtext("standtyp"), s.findtext("standkommentar")] for s in meta.findall("standangabe")],
            "xml_builddate": root.get("builddate"),
        },
    }


def build_de(src: dict, digest: str) -> tuple[dict, dict]:
    headers = src["headers"]
    rows = src["rows"]
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        if row["nuclide"] is None:
            raise ValueError(f"German name {row['printed']!r} does not map to a nuclide")
        by_key.setdefault(row["nuclide"], []).append(row)
    anomalies = []
    form_choice: dict[str, dict] = {}
    for key, group in by_key.items():
        if len(group) > 1:
            if sorted(r["with_progeny"] for r in group) != [False, True]:
                raise ValueError(f"German duplicate nuclide {key} is not a plain/'+' pair")
            form_choice[key] = {"rule": FORM_RULE, "forms": [r["printed"] for r in group], "per_route": {}}
            anomalies.append(
                f"{key} is printed as {' and '.join(r['printed'] for r in group)}; per column the row with the "
                "smallest limit is kept (a tie keeps the plain row) and progeny coverage follows the chosen row"
            )

    parents_all: dict[str, list[str]] = {}
    skipped_progeny = []
    plus_parents = {r["nuclide"] for r in rows if r["with_progeny"]}
    table2_parents = set()
    for parent_text, daughters_text in src["table2"]:
        parent = key_of(parent_text, german=True)
        keys, skipped = progeny_of(daughters_text, german=True)
        skipped_progeny += skipped
        if parent is None:
            raise ValueError(f"Tabelle 2 parent {parent_text!r} is not a nuclide")
        table2_parents.add(parent)
        parents_all[parent] = keys

    responses = []
    counts = {}
    for response_id, column, label in DE_ROUTES:
        coefficients = {}
        covering = {}
        for key, group in by_key.items():
            valued = [r for r in group if r["cells"].get(column, "")]
            if valued:
                row = min(valued, key=lambda r: (decimal_of(r["cells"][column]), r["with_progeny"]))
                coefficients[key] = bq_per_kg(decimal_of(row["cells"][column]))
                if row["with_progeny"] and key in parents_all:
                    covering[key] = parents_all[key]
                if key in form_choice:
                    form_choice[key]["per_route"][response_id] = {
                        "chosen": row["printed"],
                        "limits_Bq_per_g": {r["printed"]: r["cells"][column] for r in valued},
                    }
        covered = C.covered_by_parent(covering, set(coefficients))
        C.check_acyclic(covered, f"{DE_NAME} {response_id}")
        counts[response_id] = len(coefficients)
        header = headers.get(column, "")
        basis = (
            f"StrlSchV 2018 Anlage 4 Tabelle 1 Spalte {column} ('{header}'): {label}. Bq/g converted to "
            "Bq/kg (x1000). A blank cell means the table gives no value for that nuclide in this column, "
            "so the nuclide has no coefficient in this response. A parent marked '+' includes the daughters "
            "listed in Tabelle 2; covered_by_parent lists them (zero contribution while the parent is present)."
        )
        if column == 3:
            basis += (
                " Column 3 is the Freigrenze as specific activity and also the level for unrestricted "
                "clearance of solid and liquid material (Freigabe, sections 31 to 42 and Anlage 8)."
            )
        response = {"id": response_id, "kind": "clearance_index", "basis": basis, "coefficients": coefficients}
        if covered:
            response["covered_by_parent"] = covered
        responses.append(response)

    source = C.source_block(
        citation="Strahlenschutzverordnung (StrlSchV 2018), Anlage 4 Tabelle 1 (Freigrenzen, Freigabewerte, Werte der Oberflächenkontamination) and Tabelle 2 (Tochternuklide)",
        edition=(
            "StrlSchV of 29 Nov 2018, BGBl. I 2018, 2034; "
            + "; ".join(f"{k}: {v}" for k, v in src["version"]["stand"] if k == "Stand")
            + "; Anlage 4 as published by gesetze-im-internet.de (XML build "
            + f"{src['version']['xml_builddate']}); HRQ and half-life columns not read"
        ),
        url="https://www.gesetze-im-internet.de/strlschv_2018/anlage_4.html",
        jurisdiction="Germany (Federal Republic; the StrlSchV applies in all Länder)",
        scope=(
            "Activity concentration in Bq/g for unrestricted clearance of solid and liquid material "
            "(Freigabe, column 3) and for specific clearance routes (columns 6-11 and 14: rubble over 1000 Mg/a, "
            "soil, landfill and incineration up to 100 and 1000 Mg/a, scrap metal for recycling). Surface "
            "values (Bq/cm2), the total-activity Freigrenze and the HRQ and half-life columns are not converted. "
            "Choosing a table and a route is a regulatory decision for the analyst; a clearance decision "
            "also needs the conditions of sections 31 to 42 and Anlage 8."
        ),
        retrieved=RETRIEVED,
        source_file=f"{SOURCE_DIR_REL}/{DE_FILE}",
        source_sha256=digest,
        converter=CONVERTER,
        licence=DE_LICENCE,
        row_provenance=f"{DE_NAME}.rows.json",
    )
    table = {
        "format": C.TABLE_FORMAT,
        "title": "StrlSchV 2018 Anlage 4 Tabelle 1: clearance (Freigabe) values in Bq/g",
        "source": source,
        "responses": responses,
    }

    sidecar_rows = []
    for row in rows:
        values = {f"col{n}": number_entry(t) for n, t in row["values"].items() if t}
        flags = []
        if not row["values"][3]:
            flags.append("no column 3 value: not in the unrestricted-clearance response")
        if row["with_progeny"] and row["nuclide"] not in table2_parents:
            anomalies.append(f"{row['printed']} is marked '+' but has no Tabelle 2 row")
        sidecar_rows.append({
            "table": "Anlage 4 Tabelle 1",
            "source_row": row["source_row"],
            "row_text": " | ".join(row["cells"].get(n, "") for n in [1] + DE_SIDECAR_COLUMNS),
            "printed": row["printed"],
            "nuclide": row["nuclide"],
            "with_progeny": row["with_progeny"],
            "flags": flags,
            "values": values,
        })
    for parent in sorted(table2_parents - plus_parents):
        anomalies.append(f"Tabelle 2 lists {parent} as parent but the Tabelle 1 row is not marked '+' (or is absent)")
    qualified_rows = [
        {
            "printed": row["printed"],
            "qualifier": row["qualifier"],
            "nuclide": row["nuclide"],
            "row_text": " | ".join(row["cells"].get(n, "") for n in [1] + DE_SIDECAR_COLUMNS),
            "values": {f"col{n}": number_entry(t) for n, t in row["values"].items() if t},
            "reason_not_in_table": "chemical-form qualifier; the unqualified row of the same nuclide is the one converted",
        }
        for row in src["qualified"]
    ]
    sidecar = {
        "format": C.ROWS_FORMAT,
        "about": (
            "Per-row source text for the table built from StrlSchV 2018 Anlage 4. ACTINV does not read this file; "
            "it records what a coefficient table cannot express. Values are in the printed units (Bq, Bq/g, Bq/cm2). "
            "Column 4 (HRQ) and column 15 (half-life) are deliberately not recorded."
        ),
        "source": dict(
            source,
            xml_builddate=src["version"]["xml_builddate"],
            upstream_archive="https://www.gesetze-im-internet.de/strlschv_2018/xml.zip",
            upstream_archive_sha256=DE_ARCHIVE_SHA256,
        ),
        "version_statement": src["version"],
        "columns": {f"col{n}": headers.get(n, "") for n in [1] + DE_SIDECAR_COLUMNS},
        "routes": {response_id: f"col{column}" for response_id, column, _ in DE_ROUTES},
        "notes": {
            "explanations": src["explanations"],
            "footnotes": src["footnotes"],
            "plus_marker": "'+' marks parents whose Tabelle 2 daughters are fully covered by the parent; the exposures from these daughters are already included in the Freigrenzen, Freigabewerte and surface-contamination values (Erläuterung zu Spalte 1).",
            "summation": "Several nuclides: sum of Ci/limit_i must not exceed 1; nuclides may be left out of the sum when their share of the sum of all ratios does not exceed 10 percent (Erläuterung zu den Spalten 2 und 3). ACTINV sums the listed nuclides and reports the others in not_listed; it does not apply the 10 percent allowance.",
            "isomer_naming": "German 'n' is the second metastable state and maps to ACTINV 'm2' (Hf-178n -> Hf178m2); German 'm' maps to 'm1'. The mapping is by convention and is not stated in the source.",
        },
        "progeny_lists": {
            parent: {"progeny": keys} for parent, keys in sorted(parents_all.items())
        },
        "skipped_progeny": skipped_progeny,
        "form_choice": form_choice,
        "source_anomalies": anomalies,
        "qualified_rows": qualified_rows,
        "rows": sidecar_rows,
    }
    return table, sidecar


# ---------------------------------------------------------------------------
# United Kingdom
# ---------------------------------------------------------------------------


def parse_uk(path: Path) -> dict:
    root = ET.parse(path).getroot()
    tabulars = {}
    for tab in root.iter():
        if local(tab.tag) != "Tabular":
            continue
        number = next(squeeze("".join(c.itertext())) for c in tab if local(c.tag) == "Number")
        table = next(c for c in tab if local(c.tag) == "table")
        header, body = [], []
        for tr in table.iter():
            if local(tr.tag) != "tr":
                continue
            cells = [cell_text(c) for c in tr if local(c.tag) in ("td", "th")]
            (header if any(local(c.tag) == "th" for c in tr) else body).append(cells)
        tabulars[number] = (header, body)
    metadata = next(e for e in root.iter() if local(e.tag) == "Metadata")
    valid = next(e.text for e in metadata.iter() if local(e.tag) == "valid")
    modified = next(e.text for e in metadata.iter() if local(e.tag) == "modified")
    texts = [squeeze("".join(e.itertext())) for e in root.iter() if local(e.tag) == "Text"]
    commentaries = {
        c.get("id"): squeeze("".join(c.itertext())) for c in root.iter() if local(c.tag) == "Commentary"
    }
    return {
        "table2": tabulars["Table 2"],
        "table3": tabulars["Table 3"],
        "valid": valid,
        "modified": modified,
        "root_restrict_start": root.get("RestrictStartDate"),
        "paragraph_texts": [t for t in texts if "Table" in t or "radionuclide" in t][:20],
        "commentaries": commentaries,
    }


def build_uk(src: dict, digest: str) -> tuple[dict, dict]:
    header, body = src["table2"]
    if len(header) != 1 or len(header[0]) != 2:
        raise ValueError(f"unexpected UK Table 2 header {header!r}")
    catch_all = None
    rows = []
    index = 0
    while index < len(body):
        cells = body[index]
        if cells[0] == "":
            nxt = body[index + 1]
            if not nxt[0].startswith("Any other"):
                raise ValueError("unexpected blank-name row in UK Table 2")
            catch_all = {
                "printed": nxt[0],
                "value": cells[1],
                "number": float(decimal_of(cells[1])),
                "alternative": nxt[1],
            }
            index += 2
            continue
        rows.append(cells)
        index += 1
    if catch_all is None:
        raise ValueError("UK Table 2 catch-all row not found")

    entries = []
    for cells in rows:
        printed = cells[0]
        suffix = "sec" if printed.endswith("sec") else "plus" if printed.endswith("+") else "plain"
        key = key_of(printed)
        if key is None:
            raise ValueError(f"UK name {printed!r} does not map to a nuclide")
        entries.append({"printed": printed, "key": key, "suffix": suffix, "text": cells[1]})

    # A parent can be printed in several forms (Th-232, Th-232+, Th-232sec). One coefficient per key: the
    # smallest limit; on a tie the plain form (it covers no progeny), then '+', then 'sec'.
    priority = {"plain": 0, "plus": 1, "sec": 2}
    chosen: dict[str, dict] = {}
    anomalies = []
    form_choice: dict[str, dict] = {}
    for entry in entries:
        current = chosen.get(entry["key"])
        rank = (decimal_of(entry["text"]), priority[entry["suffix"]])
        if current is None or rank < (decimal_of(current["text"]), priority[current["suffix"]]):
            chosen[entry["key"]] = entry
    for key, entry in chosen.items():
        forms = [e for e in entries if e["key"] == key]
        if len(forms) > 1:
            form_choice[key] = {
                "rule": FORM_RULE,
                "chosen": entry["printed"],
                "limits_Bq_per_g": {e["printed"]: e["text"] for e in forms},
            }
            anomalies.append(
                f"{key} is printed as {', '.join(e['printed'] for e in forms)}; the table keeps "
                f"{entry['printed']} ({entry['text']} Bq/g), the smallest limit"
            )

    coefficients = {key: bq_per_kg(decimal_of(e["text"])) for key, e in chosen.items()}

    chosen_printed = {e["printed"] for e in chosen.values() if e["suffix"] != "plain"}
    parents: dict[str, list[str]] = {}
    skipped = []
    t3_header, t3_body = src["table3"]
    for cells in t3_body:
        if cells[0] not in chosen_printed:
            continue
        keys, skip = progeny_of(cells[1])
        skipped += skip
        parents[key_of(cells[0])] = keys
    covered = C.covered_by_parent(parents, set(coefficients))
    C.check_acyclic(covered, UK_NAME)

    basis = (
        "The Environmental Permitting (England and Wales) Regulations 2016, Schedule 23 Part 3, Table 2 "
        "'Concentration of radionuclides', concentration in becquerels per gram (Bq/g), for a solid or "
        "relevant liquid. Part 3 states that this Table 2 is the one referred to in paragraph 5 of Part 2 "
        "(processed radionuclides of natural terrestrial or cosmic origin) and paragraph 6 of that Part "
        "(radionuclides not of natural terrestrial or cosmic origin); the regulation does not call it a "
        "clearance table. Its summation rule: the sum of A/B, A the concentration of each listed radionuclide "
        "and B its column 2 value. Bq/g converted to Bq/kg (x1000). The suffix '+' (or 'sec') means the "
        "parent is in secular equilibrium with the daughters in Table 3 and its value is for the parent "
        "alone but already takes the daughters into account; covered_by_parent lists the Table 3 daughters. "
        "Where a parent is printed in several forms the form with the smallest limit is kept and progeny coverage follows that form (see the sidecar)."
    )
    source = C.source_block(
        citation="The Environmental Permitting (England and Wales) Regulations 2016 (SI 2016/1154), Schedule 23 Part 3, Table 2 (Concentration of radionuclides) with Table 3 (secular-equilibrium daughters)",
        edition=(
            f"legislation.gov.uk data.xml, in force from {src['valid']}, modified {src['modified']}; "
            "Tables 1 and 2 substituted 2018-05-02 by SI 2018/428"
        ),
        url="https://www.legislation.gov.uk/uksi/2016/1154/schedule/23/part/3",
        jurisdiction="United Kingdom, England and Wales (Environmental Permitting Regulations)",
        scope=(
            "Activity concentration in Bq/g of radionuclides in a solid or relevant liquid, below which the "
            "substance is outside the radioactive-substances regime as referred to in paragraphs 5 and 6 of "
            "Schedule 23 Part 2; solid-material concentration only (Table 1, the NORM table, and the "
            "catch-all row for other radionuclides are not converted). Choosing this table is a regulatory "
            "decision for the analyst."
        ),
        retrieved=RETRIEVED,
        source_file=f"{SOURCE_DIR_REL}/{UK_FILE}",
        source_sha256=digest,
        converter=CONVERTER,
        licence=UK_LICENCE,
        row_provenance=f"{UK_NAME}.rows.json",
    )
    response = {"id": "table-2-solid-or-relevant-liquid", "kind": "clearance_index", "basis": basis, "coefficients": coefficients}
    if covered:
        response["covered_by_parent"] = covered
    table = {
        "format": C.TABLE_FORMAT,
        "title": "EPR 2016 Schedule 23 Part 3 Table 2: concentration of radionuclides, Bq/g",
        "source": source,
        "responses": [response],
    }
    sidecar_rows = []
    for position, entry in enumerate(entries, start=1):
        flags = []
        if chosen[entry["key"]] is not entry:
            flags.append("printed in several forms; see form_choice for the form kept")
        sidecar_rows.append({
            "table": "Schedule 23 Part 3 Table 2",
            "row": position,
            "row_text": f"{entry['printed']} | {entry['text']}",
            "printed": entry["printed"],
            "nuclide": entry["key"],
            "suffix": entry["suffix"],
            "flags": flags,
            "values": {"concentration_Bq_per_g": number_entry(entry["text"])},
        })
    sidecar = {
        "format": C.ROWS_FORMAT,
        "about": (
            "Per-row source text for the table built from EPR 2016 Schedule 23 Part 3 Table 2. ACTINV does not read "
            "this file. Superscripts in row_text are written ^(...)."
        ),
        "source": dict(source, dct_valid=src["valid"], dc_modified=src["modified"], root_restrict_start_date=src["root_restrict_start"]),
        "paragraph_texts": src["paragraph_texts"],
        "commentaries": src["commentaries"],
        "catch_all_row": dict(
            catch_all,
            reason_not_in_table="not a nuclide; the regulation gives 0.01 Bq/g or the concentration that gives 10 microsieverts a year by IAEA RS-G-1.7 for any other artificial radionuclide. ACTINV does not apply it: such nuclides are reported in not_listed.",
        ),
        "progeny_lists": {key: {"progeny": keys} for key, keys in sorted(parents.items())},
        "skipped_progeny": skipped,
        "form_choice": form_choice,
        "source_anomalies": anomalies,
        "rows": sidecar_rows,
    }
    return table, sidecar


# ---------------------------------------------------------------------------
# Canada
# ---------------------------------------------------------------------------

CA_NAME_PATTERN = re.compile(r"^([A-Z][a-z]+) (\d+m?\d?|natural)$")


def parse_ca(path: Path) -> dict:
    root = ET.parse(path).getroot()
    schedule = next(
        s for s in root.iter("Schedule") if s.findtext("ScheduleFormHeading/Label") == "SCHEDULE 2"
    )
    title = schedule.findtext(".//table/title")
    body = schedule.find(".//table/tgroup/tbody")
    rows = []
    for position, row in enumerate(body.findall("row"), start=1):
        entries = row.findall("entry")
        refs: list[str] = []
        name = cell_text(entries[0], refs)
        value = cell_text(entries[1])
        rows.append({"row": position, "name": name, "value": value, "refs": refs})
    footnote = schedule.find("Footnote")
    parents = {}
    for item in footnote.iter("Item"):
        parents[squeeze(item.findtext("Label") or "")] = squeeze(item.findtext("Text") or "")
    text_all = re.sub(r"<[^>]+>", "", path.read_text(encoding="utf-8"))
    definition = re.search(r"unconditional clearance level, in respect of a bulk quantity.*?\(niveau de lib", text_all)
    return {
        "title": title,
        "rows": rows,
        "footnote_text": squeeze(footnote.findtext("Text") or ""),
        "parents": parents,
        "definition": squeeze(definition.group(0)) if definition else None,
        "pit_date": root.get("{http://justice.gc.ca/lims}pit-date"),
        "last_amended": root.get("{http://justice.gc.ca/lims}lastAmendedDate"),
        "current_date": root.get("{http://justice.gc.ca/lims}current-date"),
        "consolidation": re.sub(
            r"^(\d{4})(\d{2})(\d{2})$",
            r"\1-\2-\3",
            "".join(root.find("Identification/ConsolidationDate").itertext()).strip(),
        ),
        "historical_note": squeeze("".join(schedule.find("HistoricalNote").itertext())),
        "recent_amendments": [
            (a.findtext("AmendmentCitation"), a.findtext("AmendmentDate"))
            for a in root.iter("Amendment")
        ],
    }


def ca_printed(name: str) -> str | None:
    match = CA_NAME_PATTERN.match(name)
    if not match or match.group(2) == "natural":
        return None
    symbol = CA_ELEMENTS.get(match.group(1))
    return f"{symbol}-{match.group(2)}" if symbol else None


def build_ca(src: dict, digest: str) -> tuple[dict, dict]:
    coefficients = {}
    unmapped = []
    plus = set()
    sidecar_rows = []
    for row in src["rows"]:
        printed = ca_printed(row["name"])
        key = key_of(printed) if printed else None
        flags = []
        if key is None:
            unmapped.append({"name": row["name"], "value": row["value"], "reason": "not a single nuclide (natural mixture)"})
            flags.append("not a single nuclide: not in the table")
        else:
            if key in coefficients:
                raise ValueError(f"Canadian duplicate nuclide {key}")
            coefficients[key] = bq_per_kg(decimal_of(row["value"]))
            if row["refs"]:
                plus.add(key)
        sidecar_rows.append({
            "table": "Schedule 2",
            "row": row["row"],
            "row_text": row["name"] + " | " + row["value"],
            "printed": row["name"],
            "symbol_form": printed,
            "nuclide": key,
            "with_progeny": bool(row["refs"]),
            "footnotes": row["refs"],
            "flags": flags,
            "values": {"activity_concentration_Bq_per_g": number_entry(row["value"])},
        })
    parents: dict[str, list[str]] = {}
    skipped = []
    for label, text in src["parents"].items():
        key = key_of(label)
        keys, skip = progeny_of(text)
        skipped += skip
        if key is None:
            skipped.append(label)
            continue
        if key not in plus:
            raise ValueError(f"footnote a names parent {label} but its row is not marked")
        parents[key] = keys
    for key in sorted(plus - set(parents)):
        raise ValueError(f"row {key} carries footnote a but the footnote does not list it")
    covered = C.covered_by_parent(parents, set(coefficients))
    C.check_acyclic(covered, CA_NAME)

    basis = (
        "Nuclear Substances and Radiation Devices Regulations (SOR/2000-207), Schedule 2 (Section 1) "
        f"'{src['title']}', column 2 'Activity Concentration (Bq/g)'. Bq/g converted to Bq/kg (x1000). "
        "A row marked a is a parent whose progeny in secular equilibrium are included in its value; "
        "covered_by_parent lists them. The regulation also defines a level for nuclides not in the schedule "
        "(1 Bq/g up to atomic number 81; above 81, 1 Bq/g without alpha emission and 0.1 Bq/g with it); that "
        "rule is not a table row and is not stored here. Schedule 1 (exemption quantities) is a different "
        "table and is not converted."
    )
    source = C.source_block(
        citation="Nuclear Substances and Radiation Devices Regulations (SOR/2000-207), Schedule 2 (Section 1), Unconditional Clearance Levels",
        edition=(
            f"Justice Laws Website XML, consolidation {src['consolidation']}, point in time {src['pit_date']}, "
            f"last amended {src['last_amended']}; Schedule 2 enacted by {src['historical_note']}"
        ),
        url="https://laws-lois.justice.gc.ca/eng/regulations/SOR-2000-207/",
        jurisdiction="Canada (federal; Nuclear Safety and Control Act)",
        scope=(
            "Unconditional clearance levels as activity concentration in Bq/g for a bulk quantity of material, "
            "except surface-contaminated material, in which the nuclide is uniformly distributed. Choosing this "
            "table is a regulatory decision for the analyst."
        ),
        retrieved=RETRIEVED,
        source_file=f"{SOURCE_DIR_REL}/{CA_FILE}",
        source_sha256=digest,
        converter=CONVERTER,
        licence=CA_LICENCE,
        row_provenance=f"{CA_NAME}.rows.json",
    )
    response = {"id": "unconditional-clearance", "kind": "clearance_index", "basis": basis, "coefficients": coefficients}
    if covered:
        response["covered_by_parent"] = covered
    table = {
        "format": C.TABLE_FORMAT,
        "title": "NSRDR Schedule 2: unconditional clearance levels, Bq/g",
        "source": source,
        "responses": [response],
    }
    sidecar = {
        "format": C.ROWS_FORMAT,
        "about": (
            "Per-row source text for the table built from NSRDR Schedule 2. ACTINV does not read this file. "
            "Superscripts in row_text are written ^(...)."
        ),
        "source": dict(source, xml_current_date=src["current_date"], recent_amendments=src["recent_amendments"]),
        "notes": {
            "footnote_a": src["footnote_text"],
            "unconditional_clearance_level_definition": src["definition"],
            "default_rule": "Definition paragraph (b): a nuclide not in column 1 has 1 Bq/g if its atomic number is 81 or less, 1 Bq/g above 81 without alpha emission, 0.1 Bq/g above 81 with alpha emission (itself or short-lived progeny). Paragraph (c): for several nuclides the criterion is the sum of concentration over limit.",
        },
        "progeny_lists": {key: {"progeny": keys} for key, keys in sorted(parents.items())},
        "skipped_progeny": skipped,
        "unmapped_rows": unmapped,
        "source_anomalies": [],
        "rows": sidecar_rows,
    }
    return table, sidecar


# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sources", type=Path, help="directory with de/, uk/ and ca/ (controls/fixtures/clearance_sources)")
    parser.add_argument("out_dir", type=Path, help="output directory")
    args = parser.parse_args(argv)

    digests = {}
    for rel, pinned in PINNED_SHA256.items():
        digests[rel] = C.sha256_file(args.sources / rel)
        if digests[rel] != pinned:
            print(f"error: {rel} sha256 {digests[rel]} differs from the pinned {pinned}", file=sys.stderr)
            return 1

    built = {
        DE_NAME: build_de(parse_de(args.sources / DE_FILE), digests[DE_FILE]),
        UK_NAME: build_uk(parse_uk(args.sources / UK_FILE), digests[UK_FILE]),
        CA_NAME: build_ca(parse_ca(args.sources / CA_FILE), digests[CA_FILE]),
    }
    for name, (table, sidecar) in built.items():
        sha = C.dump_json(args.out_dir / f"{name}.json", table)
        sidecar["tables"] = {f"{name}.json": sha}
        C.dump_json(args.out_dir / f"{name}.rows.json", sidecar)
        counts = {r["id"]: len(r["coefficients"]) for r in table["responses"]}
        covered = {r["id"]: len(r.get("covered_by_parent", {})) for r in table["responses"]}
        print(f"{name}: coefficients {counts}; covered_by_parent {covered}; sha256 {sha}")
        for key in ("source_anomalies", "skipped_progeny", "unmapped_rows"):
            if sidecar.get(key):
                print(f"  {key}: {sidecar[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
