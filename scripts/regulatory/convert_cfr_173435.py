#!/usr/bin/env python3
"""Convert the eCFR XML of 49 CFR 173.435 / 173.436 into ACTINV radiological tables.

Inputs (public-domain US Government text, mirror of IAEA SSR-6 Rev.1 Table 2):
  49CFR173.435.xml  A1 and A2 (TBq) per radionuclide
  49CFR173.436.xml  exempt activity concentration (Bq/g) and exempt consignment activity (Bq)

Outputs, in the existing `actinv-radiological-table-1` format:
  us-49cfr173-a1.json                    A1, coefficient = activity in Bq
  us-49cfr173-a2.json                    A2, coefficient = activity in Bq
  us-49cfr173-exempt-concentration.json  coefficient = Bq/kg (1000 x the Bq/g value)
  us-49cfr173-exempt-consignment.json    coefficient = activity in Bq
  us-49cfr173-435.rows.json, us-49cfr173-436.rows.json
      one record per source row: the original cell text, footnotes, flags, and the
      reason a row is absent from the tables.  The table format has no per-row
      field, so everything a coefficient cannot say is kept here.

Uranium rows depend on lung-absorption type (fast/medium/slow).  ACTINV never picks
one: each table carries three responses (`...-u-fast`, `-u-medium`, `-u-slow`) that
differ only in their uranium coefficients.  Rows with no finite limit ("Unlimited") are
not coefficients: each response lists them under `unlimited`, and ACTINV counts their
activity as covered with zero contribution (an unlimited A1/A2 limit contributes nothing
to the sum).  Element-level rows ("U (nat)", "Rb(nat)") and anything that is not a single
nuclide cannot be a coefficient; they are listed in the rows sidecar with the reason and
ACTINV reports their activity as uncovered, never as zero.

Progeny.  A parent row that includes its short-lived progeny is described by the response's
`covered_by_parent` map (progeny -> parents): while the parent has activity, the progeny
contributes nothing, because the parent's value already includes it.  173.436 footnote (b)
lists the parents and progeny in the CFR text.  173.435 footnote (a) only refers to the
IAEA SSR-6 list, which the CFR does not reproduce; for 173.435 the converter uses the
173.436 footnote (b) lists for the parents carrying footnote (a), and each such row says
so in the rows sidecar (`progeny_from_173_436_footnote_b`).

Usage:
  python3 -I scripts/regulatory/convert_cfr_173435.py XML_DIR OUT_DIR

Every number is read from the XML.  The only constants are the source-file SHA-256
pins, the isomer mapping of three lifetime-labelled rows, and text labels.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def load_common():
    path = Path(__file__).resolve().parent / "regulatory_common.py"
    spec = importlib.util.spec_from_file_location("regulatory_common", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_common()

CONVERTER = "scripts/regulatory/convert_cfr_173435.py"
PINNED_SHA256 = {
    "49CFR173.435.xml": "0182c6bd4af6b69f491364d75f761b4016d890c15e962856613050bd897ba6b6",
    "49CFR173.436.xml": "f32bb3a7a3fb9f298997bba6f9a80712f6084d64ced30a1cbf34f46bb718969b",
}
ECFR_DATE = "2026-10-13"
RETRIEVED = "2026-10-15"
ECFR_URL = (
    "https://www.ecfr.gov/api/versioner/v1/full/" + ECFR_DATE + "/title-49.xml"
    "?part=173&section={section}"
)
LICENCE = (
    "US Government work (17 U.S.C. 105), public domain. The values mirror IAEA SSR-6 "
    "(Rev.1) Table 2 as published in 49 CFR."
)
VARIANTS = ("fast", "medium", "slow")
LUNG = {
    "fast lung absorption": "fast",
    "medium lung absorption": "medium",
    "slow lung absorption": "slow",
    "all lung absorption types": "all",
}
# The CFR labels three rows by lifetime instead of by isomer.  The mapping to ACTINV's
# isomer state is fixed by the half-lives in the ENDF/B-VIII.0 decay sublibrary
# (endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat, MF8/MT457), read when this
# converter was written:
#   Eu-150  state 0: 1.16448e9 s (36.9 y)    state 1: 46080 s (12.8 h)
#   Np-236  state 0: 4.82831e12 s (1.5e5 y)  state 1: 81000 s (22.5 h)
#   Ta-178  state 0: 558.6 s (9.3 min)       state 1: 8496 s (2.36 h)
ISOMER_LABELS = {
    ("Eu150", "short"): "Eu150m1",
    ("Eu150", "long"): "Eu150",
    ("Np236", "short"): "Np236m1",
    ("Np236", "long"): "Np236",
    ("Ta178", "long"): "Ta178m1",
}

FOOTNOTE_MARK = re.compile(r"\(([a-z])\)")
VALUE = re.compile(
    r"^(\d+(?:\.\d+)?)(?:\s*×\s*10\s*([+-])?\s*\^\{\s*([+-])?\s*(\d+)\s*\})?$"
)
# Ci per TBq (and Ci per Bq, up to a power of ten): the Ci columns are approximate.
CI_PER_TBQ = 27.027
CI_PER_BQ = 2.7027e-11

SECTIONS = {
    "173.435": {
        "xml": "49CFR173.435.xml",
        "columns": [
            "a1_TBq", "a1_Ci", "a2_TBq", "a2_Ci",
            "specific_activity_TBq_per_g", "specific_activity_Ci_per_g",
        ],
        "cross_checks": [
            ("a1_TBq", "a1_Ci", CI_PER_TBQ),
            ("a2_TBq", "a2_Ci", CI_PER_TBQ),
            ("specific_activity_TBq_per_g", "specific_activity_Ci_per_g", CI_PER_TBQ),
        ],
        "tables": [
            {
                "file": "us-49cfr173-a1.json",
                "id": "a1",
                "column": "a1_TBq",
                "kind": "activity_limit",
                "shift": 12,
                "title": "49 CFR 173.435 A1 values (IAEA SSR-6 Rev.1 Table 2)",
                "basis": (
                    "A1 for special form radioactive material, TBq converted to Bq. The index is the number of "
                    "A1 per gram of material, so multiply by the consignment mass in grams. Uranium rows use the {variant} "
                    "lung-absorption values; unlimited rows are listed under unlimited (zero contribution) and "
                    "element-level rows are omitted. covered_by_parent lists progeny included in a parent's value "
                    "(from the 173.436 footnote (b) lists)."
                ),
            },
            {
                "file": "us-49cfr173-a2.json",
                "id": "a2",
                "column": "a2_TBq",
                "kind": "a2",
                "shift": 12,
                "title": "49 CFR 173.435 A2 values (IAEA SSR-6 Rev.1 Table 2)",
                "basis": (
                    "A2 for radioactive material other than special form, TBq converted to Bq. The index is the "
                    "number of A2 per gram of material (FISPACT-II ATWO), so multiply by the consignment mass in "
                    "grams. Uranium rows use the {variant} "
                    "lung-absorption values; unlimited rows are listed under unlimited (zero contribution) and "
                    "element-level rows are omitted. covered_by_parent lists progeny included in a parent's value "
                    "(from the 173.436 footnote (b) lists)."
                ),
            },
        ],
    },
    "173.436": {
        "xml": "49CFR173.436.xml",
        "columns": [
            "exempt_concentration_Bq_per_g", "exempt_concentration_Ci_per_g",
            "exempt_consignment_Bq", "exempt_consignment_Ci",
        ],
        "cross_checks": [
            ("exempt_concentration_Bq_per_g", "exempt_concentration_Ci_per_g", CI_PER_BQ),
            ("exempt_consignment_Bq", "exempt_consignment_Ci", CI_PER_BQ),
        ],
        "tables": [
            {
                "file": "us-49cfr173-exempt-concentration.json",
                "id": "exempt-concentration",
                "column": "exempt_concentration_Bq_per_g",
                "kind": "clearance_index",
                "shift": 3,
                "title": "49 CFR 173.436 exempt material activity concentration (IAEA SSR-6 Rev.1 Table 2)",
                "basis": (
                    "Activity concentration for exempt material, Bq/g converted to Bq/kg (x1000). Uranium rows "
                    "use the {variant} lung-absorption values; element-level rows are omitted. Parent rows "
                    "marked (b) include their listed progeny, which covered_by_parent lists (zero contribution while the parent is present)."
                ),
            },
            {
                "file": "us-49cfr173-exempt-consignment.json",
                "id": "exempt-consignment",
                "column": "exempt_consignment_Bq",
                "kind": "activity_limit",
                "shift": 0,
                "title": "49 CFR 173.436 exempt consignment activity limit (IAEA SSR-6 Rev.1 Table 2)",
                "basis": (
                    "Activity limit for an exempt consignment, Bq. The index is the fraction of that limit per "
                    "gram of material, so multiply by the consignment mass in grams. Uranium rows use the {variant} lung-absorption values; "
                    "element-level rows are omitted. Parent rows marked (b) include their listed progeny, which covered_by_parent lists (zero contribution while the parent is present)."
                ),
            },
        ],
    },
}


def cell_text(element: ET.Element) -> str:
    """Cell text with superscripts kept as `^{...}` so `10^0` and `100` stay distinct."""
    parts = [element.text or ""]
    for child in element:
        if child.tag == "sup":
            parts.append("^{" + "".join(child.itertext()).strip() + "}")
        elif child.tag == "br":
            parts.append(" ")
        else:
            parts.append(cell_text(child))
        parts.append(child.tail or "")
    text = "".join(parts).replace("−", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_value(text: str, where: str) -> tuple[str, int] | None:
    """(mantissa text, exponent) of a numeric cell; None for Unlimited or a cross-reference."""
    if text == "Unlimited" or text.startswith("see §"):
        return None
    match = VALUE.match(text)
    if not match:
        raise ValueError(f"{where}: cannot parse numeric cell {text!r}")
    mantissa, sign_outer, sign_inner, digits = match.groups()
    exponent = 0
    if digits is not None:
        if sign_outer and sign_inner and sign_outer != sign_inner:
            raise ValueError(f"{where}: conflicting exponent signs in {text!r}")
        negative = "-" in (sign_outer, sign_inner)
        exponent = -int(digits) if negative else int(digits)
    return mantissa, exponent


def parse_label(label: str) -> dict:
    """Classify a row label; an unknown shape raises instead of being dropped."""
    footnotes = FOOTNOTE_MARK.findall(label)
    rest = re.sub(r"[\s,]+$", "", FOOTNOTE_MARK.sub("", label)).strip()
    qualified = re.match(r"^(\S+?)\s*\((.+)\)$", rest)
    name, qualifier = (qualified.group(1), qualified.group(2)) if qualified else (rest, None)

    record: dict = {"label": label, "footnotes": footnotes, "flags": []}
    nuclide = C.parse_nuclide(name)
    if qualifier is None and nuclide:
        record.update(kind="nuclide", nuclide=nuclide[0])
    elif nuclide and qualifier in LUNG:
        record.update(kind="lung", nuclide=nuclide[0], variant=LUNG[qualifier])
        record["flags"].append(f"lung_absorption:{LUNG[qualifier]}")
    elif nuclide and qualifier and re.fullmatch(r"(short|long)[ -]lived", qualifier):
        lifetime = re.split(r"[ -]", qualifier)[0]
        key = (nuclide[0], lifetime)
        if key not in ISOMER_LABELS:
            raise ValueError(f"no isomer mapping for {label!r}")
        record.update(kind="nuclide", nuclide=ISOMER_LABELS[key])
        record["flags"].append(f"isomer_label_mapped:{qualifier}->{ISOMER_LABELS[key]}")
    elif name == "T" and qualifier == "H-3":
        record.update(kind="nuclide", nuclide="H3")
        record["flags"].append("label_mapped:T(H-3)->H3")
    elif name in C.Z_OF and qualifier and (
        qualifier in ("nat", "dep") or qualifier.startswith("enriched")
    ):
        record.update(kind="element", element=name, material=qualifier)
        record["flags"].append("element_level_entry")
    else:
        raise ValueError(f"unrecognised row label {label!r}")
    return record


def read_section(xml_path: Path):
    """(body rows as cell-text lists, footnotes as marker -> lines, CFR history citation)."""
    root = ET.parse(xml_path).getroot()
    rows = [[cell_text(td) for td in tr] for tr in root.find(".//TBODY")]
    footnotes: dict[str, list[str]] = {}
    current = None
    for tr in root.find(".//TFOOT"):
        text = cell_text(tr[0])
        marked = re.match(r"^\^\{([a-z])\}\s*(.*)$", text)
        if marked:
            current = marked.group(1)
            footnotes[current] = [marked.group(2)]
        elif current is not None:
            footnotes[current].append(text)
        else:
            raise ValueError(f"{xml_path.name}: footnote text before any marker: {text!r}")
    cita = root.find(".//CITA")
    history = re.sub(r"\s+", " ", "".join(cita.itertext())).strip() if cita is not None else ""
    return rows, footnotes, history


def parse_progeny(lines: list[str]) -> dict[str, dict]:
    """173.436 footnote (b): lines of 'Parent progeny, progeny (branching fraction)'."""
    parents = {}
    for line in lines[1:]:
        parent, _, rest = line.partition(" ")
        rest = rest.strip().rstrip(",")
        if not rest:
            raise ValueError(f"progeny line without progeny: {line!r}")
        parents[parent] = {
            "raw": line,
            "progeny": [piece.strip() for piece in re.split(r",\s*(?![^()]*\))", rest)],
        }
    return parents


def build_records(section: str, spec: dict, rows, footnotes) -> list[dict]:
    columns = spec["columns"]
    table_columns = [table["column"] for table in spec["tables"]]
    records = []
    for index, cells in enumerate(rows, start=1):
        where = f"{section} row {index} ({cells[0]!r})"
        if len(cells) != len(columns) + 2:
            raise ValueError(f"{where}: expected {len(columns) + 2} cells, found {len(cells)}")
        record = parse_label(cells[0])
        record["row"] = index
        record["cells"] = {"element": cells[1], **dict(zip(columns, cells[2:]))}
        record["values"] = {}
        record["_decimal"] = {}
        for column, text in zip(columns, cells[2:]):
            parsed = parse_value(text, where)
            if parsed is None:
                record["values"][column] = {"text": text, "number": None}
                if column in table_columns:
                    record["flags"].append(
                        f"unlimited:{column}" if text == "Unlimited" else f"reference_only:{column}"
                    )
            else:
                record["values"][column] = {
                    "text": text,
                    "number": C.scaled_float(parsed[0], parsed[1]),
                }
                record["_decimal"][column] = parsed
        for first, second, ratio in spec["cross_checks"]:
            a = record["values"][first]["number"]
            b = record["values"][second]["number"]
            if a and b and not 0.94 < (b / a) / ratio < 1.06:
                record["flags"].append(f"unit_cross_check_off:{first}")
        for mark in record["footnotes"]:
            if mark not in footnotes:
                raise ValueError(f"{where}: cites unknown footnote ({mark})")
            record["flags"].append(f"footnote_{mark}")
        omitted = {}
        for column in table_columns:
            if record["values"][column]["number"] is None:
                omitted[column] = (
                    "unlimited" if record["values"][column]["text"] == "Unlimited" else "reference_only"
                )
        if record["kind"] == "element":
            omitted["all_columns"] = "element-level entry; ACTINV coefficient keys are nuclides"
        record["omitted_from_tables"] = omitted or None
        records.append(record)
    return records


def parent_key(parent: str) -> str | None:
    """ACTINV key of a progeny-list parent such as 'Ag-108m'; None for 'Th-nat' style entries."""
    parsed = C.parse_nuclide(parent)
    return parsed[0] if parsed else None


def annotate_173_435(records, footnotes, progeny_436: dict[str, dict]) -> list[str]:
    """Flag footnote (a) parents and attach the 173.436 footnote (b) progeny of the same parent.

    Returns the 173.436 progeny-list parents with no footnote (a) row here.
    """
    by_key = {parent_key(name): entry for name, entry in progeny_436.items() if parent_key(name)}
    matched = set()
    for record in records:
        if "a" in record["footnotes"]:
            record["flags"].append("parent_with_progeny_not_listed_in_cfr")
            entry = by_key.get(record.get("nuclide"))
            if entry is not None and record["kind"] != "element":
                record["progeny_included"] = entry["progeny"]
                record["flags"].append("progeny_from_173_436_footnote_b")
                matched.add(record["nuclide"])
            else:
                record["flags"].append("parent_with_progeny_not_in_173_436_list")
        if "i" in record["footnotes"]:
            note = " ".join(footnotes["i"])
            value = re.search(r"A2\s*=\s*([0-9.]+)\s*TBq", note)
            if not value:
                raise ValueError(f"cannot read the domestic A2 value from {note!r}")
            record["alternative_values"] = {
                "a2_TBq_domestic_use": float(value.group(1)),
                "source_text": note,
            }
    return sorted(name for name, _ in progeny_436.items() if parent_key(name) not in matched)


def annotate_173_436(records, footnotes) -> list[str]:
    """Attach footnote (b) progeny lists; return progeny parents that no row cites."""
    progeny = parse_progeny(footnotes["b"])
    matched = set()
    for record in records:
        if "b" not in record["footnotes"]:
            continue
        if record["kind"] == "element" and record["material"] == "nat":
            key = f"{record['element']}-nat"
        else:
            key = re.match(r"[A-Z][a-z]?-\d+m?", record["label"]).group(0)
        if key in progeny:
            record["progeny_included"] = progeny[key]["progeny"]
            matched.add(key)
        else:
            record["flags"].append("footnote_b_parent_not_in_progeny_list")
    return sorted(set(progeny) - matched)


def coefficients(records, column: str, variant: str, shift: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for record in records:
        if record["kind"] == "element" or column not in record["_decimal"]:
            continue
        if record["kind"] == "lung" and record["variant"] not in (variant, "all"):
            continue
        if record["nuclide"] in out:
            raise ValueError(f"duplicate nuclide {record['nuclide']} in column {column}")
        mantissa, exponent = record["_decimal"][column]
        out[record["nuclide"]] = C.scaled_float(mantissa, exponent, shift)
    return out


def unlimited_nuclides(records, column: str, variant: str) -> list[str]:
    """Nuclides whose `column` cell reads "Unlimited" for this uranium variant."""
    out = []
    for record in records:
        if record["kind"] == "element" or record["values"][column]["text"] != "Unlimited":
            continue
        if record["kind"] == "lung" and record["variant"] not in (variant, "all"):
            continue
        out.append(record["nuclide"])
    return sorted(out)


def progeny_of_parents(records, variant: str) -> dict[str, list[str]]:
    """Parent key -> progeny keys from the rows that carry a progeny list, for this variant."""
    out: dict[str, list[str]] = {}
    for record in records:
        if record["kind"] == "element" or "progeny_included" not in record:
            continue
        if record["kind"] == "lung" and record["variant"] not in (variant, "all"):
            continue
        keys, _skipped = C.progeny_keys(record["progeny_included"])
        out[record["nuclide"]] = keys
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("xml_dir", type=Path, help="directory with 49CFR173.435.xml and 49CFR173.436.xml")
    parser.add_argument("out_dir", type=Path, help="output directory for the tables and sidecars")
    parser.add_argument(
        "--allow-unpinned-source",
        action="store_true",
        help="accept source files whose SHA-256 differs from the pins in this script",
    )
    args = parser.parse_args(argv)

    shas = {}
    for name, pin in PINNED_SHA256.items():
        digest = C.sha256_file(args.xml_dir / name)
        if digest != pin and not args.allow_unpinned_source:
            print(f"error: {name} sha256 {digest} differs from the pinned {pin}", file=sys.stderr)
            return 1
        shas[name] = digest

    # The 173.436 progeny lists also describe the 173.435 parents, so read that section first.
    _rows, footnotes_436, _history = read_section(args.xml_dir / SECTIONS["173.436"]["xml"])
    progeny_436 = parse_progeny(footnotes_436["b"])

    for section, spec in SECTIONS.items():
        rows, footnotes, history = read_section(args.xml_dir / spec["xml"])
        records = build_records(section, spec, rows, footnotes)
        orphan_parents = []
        if section == "173.435":
            orphan_parents = annotate_173_435(records, footnotes, progeny_436)
        else:
            orphan_parents = annotate_173_436(records, footnotes)

        sidecar_name = f"us-49cfr173-{section[-3:]}.rows.json"
        source = dict(
            citation=f"49 CFR {section} (US Code of Federal Regulations, Title 49)",
            edition=(
                f"eCFR text current as of {ECFR_DATE}; CFR history {history}; values are those of "
                "IAEA SSR-6 (Rev.1), 2018 edition, as incorporated by 49 CFR 171.7"
            ),
            url=ECFR_URL.format(section=section),
            jurisdiction="United States (49 CFR Part 173, transport of radioactive material)",
            scope=(
                "Transport of radioactive material. A1/A2 and exempt values are not clearance, waste "
                "classification or dose limits; choosing this table is a regulatory decision for the analyst."
            ),
            retrieved=RETRIEVED,
            source_file=spec["xml"],
            source_sha256=shas[spec["xml"]],
            converter=CONVERTER,
            licence=LICENCE,
        )
        table_hashes = {}
        for table in spec["tables"]:
            responses = []
            for variant in VARIANTS:
                values = coefficients(records, table["column"], variant, table["shift"])
                unlimited = unlimited_nuclides(records, table["column"], variant)
                if set(unlimited) & set(values):
                    raise ValueError(f"{table['file']}: nuclide both finite and unlimited in {variant}")
                parents = progeny_of_parents(records, variant)
                covered = C.covered_by_parent(parents, set(values) | set(unlimited))
                C.check_acyclic(covered, f"{table['file']} {variant}")
                response = {
                    "id": f"{table['id']}-u-{variant}",
                    "kind": table["kind"],
                    "basis": table["basis"].format(variant=variant),
                    "coefficients": values,
                }
                if unlimited:
                    response["unlimited"] = unlimited
                if covered:
                    response["covered_by_parent"] = covered
                responses.append(response)
            document = {
                "format": C.TABLE_FORMAT,
                "title": table["title"],
                "source": C.source_block(row_provenance=sidecar_name, **source),
                "responses": responses,
            }
            table_hashes[table["file"]] = C.dump_json(args.out_dir / table["file"], document)
            counts = [len(response["coefficients"]) for response in responses]
            extras = [
                (len(response.get("unlimited", [])), len(response.get("covered_by_parent", {})))
                for response in responses
            ]
            print(
                f"{table['file']}: sha256 {table_hashes[table['file']]} coefficients {counts} "
                f"(unlimited, covered_by_parent) {extras}"
            )

        sidecar = {
            "format": C.ROWS_FORMAT,
            "about": (
                "Per-row source text and flags for the tables built from this section. ACTINV does not read "
                "this file; it records what a coefficient table cannot express. Superscripts in cell text are written ^{...}."
            ),
            "source": source,
            "tables": table_hashes,
            **(
                {
                    "progeny_provenance": (
                        "49 CFR 173.435 footnote (a) refers to the IAEA SSR-6 list of parents whose A1/A2 include "
                        "daughter nuclides with half-lives under 10 days; the CFR does not reproduce that list. The "
                        "tables' covered_by_parent maps use the 49 CFR 173.436 footnote (b) progeny lists for the parents "
                        "that carry footnote (a); rows flagged progeny_from_173_436_footnote_b are those, and rows flagged "
                        "parent_with_progeny_not_in_173_436_list have progeny that this converter does not know."
                    )
                }
                if section == "173.435"
                else {}
            ),
            "footnotes": footnotes,
            "rows": [
                {key: value for key, value in record.items() if not key.startswith("_")}
                for record in records
            ],
        }
        C.dump_json(args.out_dir / sidecar_name, sidecar)
        flag_counts: dict[str, int] = {}
        for record in records:
            for flag in record["flags"]:
                kind = flag.split(":")[0]
                flag_counts[kind] = flag_counts.get(kind, 0) + 1
        print(f"{section}: {len(records)} source rows; flags {dict(sorted(flag_counts.items()))}")
        if orphan_parents:
            print(f"{section}: progeny-list parents with no flagged row: {orphan_parents}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
