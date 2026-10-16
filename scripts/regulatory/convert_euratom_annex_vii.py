#!/usr/bin/env python3
"""Convert Annex VII of Council Directive 2013/59/Euratom into ACTINV radiological tables.

Input: the Publications Office XHTML of the Directive (OJ L 13, 17.1.2014).

Outputs, in the existing `actinv-radiological-table-1` format:
  euratom-2013-59-annex-vii-a.json
      Table A Part 1, artificial radionuclides: activity concentration for exemption
      AND clearance of solid material, kBq/kg converted to Bq/kg.
  euratom-2013-59-annex-vii-b-concentration.json
      Table B column 2: exemption concentration for moderate amounts, kBq/kg -> Bq/kg.
  euratom-2013-59-annex-vii-b-activity.json
      Table B column 3: total activity for exemption, Bq.  The coefficient is an
      activity (Bq), not a concentration, and ACTINV evaluates it per gram of
      material (activity input is Bq/g), so the index is the fraction of the limit
      per gram; multiply by the mass in grams.
  euratom-2013-59-annex-vii.rows.json
      Every row of Tables A (Parts 1 and 2) and B with the original text, the
      footnote markers, the parent/progeny lists and anomalies of the source.

Footnote markers.  In Table A Part 1 "(1)" marks a parent whose listed progeny are
already covered by the parent's value; in Table B "(1)" is the potassium-salts note
and "(2)" is the parent/progeny marker.  Parent rows keep their value in the table, and the
progeny of the progeny lists are written to the response's `covered_by_parent` map
(progeny -> parents): while the parent has activity the progeny contributes nothing,
because the parent's value already includes it.  A progeny with its own row (Y-90 under
Sr-90) is covered the same way, so a decay chain holding both is not counted twice.  A
parent named in a progeny list but not marked in the data table (Ce-144 in Table A,
Ag-108m and U-230 in Table B) is treated as a parent and flagged in the sidecar.  The U-240
row used in Table B is the nuclide-alone one, so it covers no progeny.

Table A Part 2 (natural U-238 / Th-232 series, K-40) is not nuclide-keyed and is
outside the scope of "artificial radionuclides"; it is recorded in the sidecar only.

Usage:
  python3 -I scripts/regulatory/convert_euratom_annex_vii.py DIRECTIVE.xhtml OUT_DIR

Every number is read from the XHTML; nothing is typed in.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


def load_common():
    path = Path(__file__).resolve().parent / "regulatory_common.py"
    spec = importlib.util.spec_from_file_location("regulatory_common", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_common()

CONVERTER = "scripts/regulatory/convert_euratom_annex_vii.py"
PINNED_SHA256 = "9f403f9016f0e7dcaa133d0680fc2d4733701ea057fad78981ad1494bf2d89a3"
RETRIEVED = "2026-10-15"
URL = "http://publications.europa.eu/resource/celex/32013L0059"
LICENCE = (
    "EU legal act; reuse with attribution to the source (Commission Decision 2011/833/EU). "
    "Source: Publications Office of the European Union, OJ L 13, 17.1.2014. The values "
    "originate with the IAEA basic safety standards (exemption and clearance levels)."
)
SIDECAR = "euratom-2013-59-annex-vii.rows.json"

SECTION_TITLES = {
    "TABLE A PART 1": "A1",
    "TABLE A PART 2": "A2",
    "TABLE B": "B",
}


class AnnexParser(HTMLParser):
    """Collect, in document order, paragraphs and `oj-table` rows of Annex VII only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, object]] = []
        self.header: list[str] = []
        self._in_annex = False
        self._done = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._para: list[str] | None = None
        self._super_depth: list[bool] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and a.get("id") == "anx_VII":
            self._in_annex = True
        if tag == "p" and a.get("class") in ("oj-doc-ti", "oj-hd-date") and not self._in_annex:
            self._para = []
        if not self._in_annex or self._done:
            return
        if tag == "table" and a.get("class") == "oj-table":
            self.items.append(("TABLE", None))
        elif tag == "tr" and a.get("class") == "oj-table":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []
        elif tag == "p":
            self._para = []
        elif tag == "span":
            is_super = a.get("class") == "oj-super"
            self._super_depth.append(is_super)
            if is_super:
                self._emit("^{")

    def handle_endtag(self, tag):
        if tag == "p" and self._para is not None and not self._in_annex:
            self.header.append("".join(self._para).strip())
            self._para = None
            return
        if not self._in_annex or self._done:
            return
        if tag == "span" and self._super_depth:
            if self._super_depth.pop():
                self._emit("}")
        elif tag == "td" and self._cell is not None:
            self._row.append(tidy("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.items.append(("ROW", self._row))
            self._row = None
        elif tag == "p" and self._para is not None:
            text = tidy("".join(self._para))
            self._para = None
            if self._cell is None and self._row is None and text:
                if text == "ANNEX VIII":
                    self._done = True
                else:
                    self.items.append(("P", text))

    def _emit(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        if self._para is not None:
            self._para.append(text)

    def handle_data(self, data):
        self._emit(data)


def tidy(text: str) -> str:
    """Collapse whitespace and write the footnote marker '(^{1})' as '(1)'."""
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return re.sub(r"\(\^\{(\d)\}\)", r"(\1)", text)


def parse_number(text: str, where: str) -> tuple[str, int]:
    """(mantissa text, exponent) from '0,1', '1 000', '1 × 10^{3}'."""
    cleaned = re.sub(r"[\s  ]+", "", text.replace("\xa0", " ")).replace(",", ".")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:×10\^\{(-?\d+)\})?", cleaned)
    if not match:
        raise ValueError(f"{where}: cannot parse number {text!r}")
    return match.group(1), int(match.group(2) or 0)


def parse_name(raw: str, where: str) -> tuple[str, str | None, str | None]:
    """'Ag-110 m (1)' -> (canonical key 'Ag110m1', footnote marker '1', label with the source spelling)."""
    match = re.fullmatch(r"(.*?)\s*(?:\((\d)\))?", raw)
    base, marker = match.group(1), match.group(2)
    nuclide = C.parse_nuclide(base)
    if nuclide is None:
        raise ValueError(f"{where}: {raw!r} is not a nuclide")
    return nuclide[0], marker, base


def split_tables(items):
    """Group rows into data tables by section and progeny tables by the note that precedes them."""
    section = None
    note = None
    data = {"A1": [], "A2": [], "B": []}
    notes = {}  # note paragraph text -> progeny rows
    current = None
    for kind, payload in items:
        if kind == "P":
            if payload in SECTION_TITLES:
                section = SECTION_TITLES[payload]
            elif re.match(r"^\(\d\)\s", payload):
                note = payload
        elif kind == "TABLE":
            current = None
        elif kind == "ROW":
            if current is None:
                # Table A Part 2 has no header row; every other table starts with one.
                if payload[0] == "Parent radionuclide":
                    current = ("progeny", note)
                    notes.setdefault(note, [])
                    continue
                current = ("data", section)
                if payload[0] == "Radionuclide":
                    continue
            if current[0] == "progeny":
                notes[current[1]].append(payload)
            else:
                data[current[1]].append(payload)
    return data, notes


def progeny_list(cell: str) -> list[str]:
    return [piece.strip() for piece in re.split(r",\s*(?![^()]*\))", cell.strip().rstrip(",")) if piece.strip()]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("xhtml", type=Path, help="Directive_2013_59_Euratom_OJ_L13_2014.xhtml")
    parser.add_argument("out_dir", type=Path, help="output directory")
    parser.add_argument("--allow-unpinned-source", action="store_true")
    args = parser.parse_args(argv)

    digest = C.sha256_file(args.xhtml)
    if digest != PINNED_SHA256 and not args.allow_unpinned_source:
        print(f"error: sha256 {digest} differs from the pinned {PINNED_SHA256}", file=sys.stderr)
        return 1

    html = AnnexParser()
    html.feed(args.xhtml.read_text(encoding="utf-8"))
    if not html._done:
        raise ValueError("Annex VII end (ANNEX VIII) not found")
    title_lines = [line for line in html.header if line]
    date_line = next(line for line in title_lines if re.fullmatch(r"\d+\.\d+\.\d{4}", line))
    data, notes = split_tables(html.items)

    note_a = next(text for text in notes if text.startswith("(1)") and "Parent" in text)
    note_b = next(text for text in notes if text.startswith("(2)") and "Parent" in text)
    potassium = next(
        payload for kind, payload in html.items
        if kind == "P" and payload.startswith("(1)") and "Potassium" in payload
    )
    gap_text = next(
        payload for kind, payload in html.items
        if kind == "P" and payload.startswith("For radionuclides not listed in Table A")
    )

    sidecar_rows = []
    anomalies = []

    def progeny_map(table_name: str, rows) -> dict[str, dict]:
        out = {}
        for parent_cell, progeny_cell in rows:
            key, _marker, label = parse_name(parent_cell, f"{table_name} progeny")
            if key in out:
                raise ValueError(f"{table_name}: duplicate progeny-list parent {key}")
            out[key] = {"source_parent": parent_cell, "progeny": progeny_list(progeny_cell)}
            if not re.fullmatch(r"[A-Z][a-z]?-\d+( m)?", label):
                anomalies.append(
                    f"{table_name} progeny list spells parent {parent_cell!r} without the hyphen/space of the data table"
                )
        return out

    progeny_a = progeny_map("Table A Part 1", notes[note_a])
    progeny_b = progeny_map("Table B", notes[note_b])

    def read_table(name, rows, columns, progeny, marker_parent, extra_notes):
        out = {}
        marked = set()
        for position, cells in enumerate(rows, start=1):
            where = f"{name} row {position} ({cells[0]!r})"
            if len(cells) != len(columns) + 1:
                raise ValueError(f"{where}: expected {len(columns) + 1} cells, found {len(cells)}")
            key, marker, label = parse_name(cells[0], where)
            values = {column: parse_number(text, where) for column, text in zip(columns, cells[1:])}
            record = {
                "table": name,
                "row": position,
                "row_text": " | ".join(cells),
                "nuclide": key,
                "footnotes": [marker] if marker else [],
                "flags": [],
                "values": {
                    column: {"text": text, "number": C.scaled_float(*values[column])}
                    for column, text in zip(columns, cells[1:])
                },
            }
            if marker == marker_parent:
                marked.add(key)
                record["flags"].append("parent_with_progeny_included")
                if key in progeny:
                    record["progeny_included"] = progeny[key]["progeny"]
                else:
                    record["flags"].append("marked_parent_missing_from_progeny_list")
                    anomalies.append(f"{name}: {cells[0]!r} carries ({marker}) but is not in the progeny list")
            elif marker:
                record["flags"].append(extra_notes[marker])
            if key in out:
                # Table B lists U-240 twice: alone, and as a parent with its progeny (2).
                # The nuclide-alone row goes in the table, the other stays in the sidecar.
                kept, _ = out[key]
                if (marker == marker_parent) == ("parent_with_progeny_included" in kept["flags"]):
                    raise ValueError(f"{where}: duplicate nuclide {key} that cannot be told apart")
                if marker == marker_parent:
                    record["flags"].append("duplicate_nuclide_row_not_in_table")
                    kept["flags"].append("duplicate_nuclide_alternative_row_in_sidecar")
                    anomalies.append(f"{name}: {cells[0]!r} duplicates {key}; the nuclide-alone row is used")
                    sidecar_rows.append(record)
                    continue
                kept["flags"].append("duplicate_nuclide_row_not_in_table")
                record["flags"].append("duplicate_nuclide_alternative_row_in_sidecar")
                anomalies.append(f"{name}: {key} appears twice; the nuclide-alone row is used")
            out[key] = (record, values)
            sidecar_rows.append(record)
        for key in [k for k in progeny if k not in marked]:
            anomalies.append(f"{name}: progeny list names {progeny[key]['source_parent']!r} but the data row is not marked")
            if key in out:
                record = out[key][0]
                record["progeny_included"] = progeny[key]["progeny"]
                record["flags"].append("progeny_list_parent_not_marked")
        return out

    def covered_for(name, table):
        """progeny -> parents for the parent rows kept in `table`; non-nuclide progeny go to the anomalies."""
        parents = {}
        for key, (record, _values) in table.items():
            if "progeny_included" not in record:
                continue
            keys, skipped = C.progeny_keys(record["progeny_included"])
            parents[key] = keys
            for piece in skipped:
                anomalies.append(f"{name}: progeny {piece!r} of {key} is not a single nuclide and is not in covered_by_parent")
        covered = C.covered_by_parent(parents, set(table))
        C.check_acyclic(covered, name)
        return covered

    table_a = read_table(
        "Table A Part 1", data["A1"], ["activity_concentration_kBq_per_kg"], progeny_a, "1", {}
    )
    table_b = read_table(
        "Table B",
        data["B"],
        ["activity_concentration_kBq_per_kg", "activity_Bq"],
        progeny_b,
        "2",
        {"1": "potassium_salts_below_1000_kg_exempted"},
    )
    for position, cells in enumerate(data["A2"], start=1):
        sidecar_rows.append(
            {
                "table": "Table A Part 2",
                "row": position,
                "row_text": " | ".join(cells),
                "nuclide": None,
                "flags": ["natural_series_entry_not_nuclide_keyed"],
            }
        )

    source_common = dict(
        edition=(
            f"Council Directive 2013/59/Euratom of 5 December 2013, OJ L 13, {date_line}, "
            "text as published in the Official Journal (later corrigenda not checked)"
        ),
        url=URL,
        jurisdiction="European Union (Euratom member states; national transposition may differ)",
        retrieved=RETRIEVED,
        source_file=args.xhtml.name,
        source_sha256=digest,
        converter=CONVERTER,
        licence=LICENCE,
        row_provenance=SIDECAR,
    )

    covered_a = covered_for("Table A Part 1", table_a)
    covered_b = covered_for("Table B", table_b)

    def table_doc(title, citation, scope, kind, basis, coefficients, covered):
        response = {"id": basis[0], "kind": kind, "basis": basis[1], "coefficients": coefficients}
        if covered:
            response["covered_by_parent"] = covered
        return {
            "format": C.TABLE_FORMAT,
            "title": title,
            "source": C.source_block(citation=citation, scope=scope, **source_common),
            "responses": [response],
        }

    docs = {
        "euratom-2013-59-annex-vii-a.json": table_doc(
            "Directive 2013/59/Euratom Annex VII Table A Part 1 (exemption and clearance, bulk solids)",
            "Council Directive 2013/59/Euratom, Annex VII, Table A Part 1 (artificial radionuclides)",
            "Activity concentration for exemption or clearance of solid material of any amount; artificial "
            "radionuclides only. Choosing this table is a regulatory decision for the analyst.",
            "clearance_index",
            (
                "annex-vii-table-a-part1",
                "Activity concentration for exemption or clearance of solid material, kBq/kg converted to Bq/kg "
                "(x1000). Parent rows marked (1) include listed short-lived progeny, which covered_by_parent lists "
                "(zero contribution while the parent is present).",
            ),
            {key: C.scaled_float(values["activity_concentration_kBq_per_kg"][0],
                                 values["activity_concentration_kBq_per_kg"][1], 3)
             for key, (_record, values) in table_a.items()},
            covered_a,
        ),
        "euratom-2013-59-annex-vii-b-concentration.json": table_doc(
            "Directive 2013/59/Euratom Annex VII Table B column 2 (exemption, moderate amounts)",
            "Council Directive 2013/59/Euratom, Annex VII, Table B column 2",
            "Exemption concentration for moderate amounts of any type of material, as specified by Member "
            "States. Choosing this table is a regulatory decision for the analyst.",
            "clearance_index",
            (
                "annex-vii-table-b-concentration",
                "Exemption activity concentration for moderate amounts of material, kBq/kg converted to Bq/kg "
                "(x1000). Parent rows marked (2) include listed progeny, which covered_by_parent lists (zero contribution "
                "while the parent is present); K-40 carries the potassium-salts note. U-240 is listed twice in the source; the nuclide-alone row is used.",
            ),
            {key: C.scaled_float(values["activity_concentration_kBq_per_kg"][0],
                                 values["activity_concentration_kBq_per_kg"][1], 3)
             for key, (_record, values) in table_b.items()},
            covered_b,
        ),
        "euratom-2013-59-annex-vii-b-activity.json": table_doc(
            "Directive 2013/59/Euratom Annex VII Table B column 3 (exemption, total activity)",
            "Council Directive 2013/59/Euratom, Annex VII, Table B column 3",
            "Total activity for exemption of a practice (the whole inventory), Bq. Choosing this table is a "
            "regulatory decision for the analyst.",
            "activity_limit",
            (
                "annex-vii-table-b-activity",
                "Total activity value for exemption, Bq. The index is the fraction of the limit per gram of "
                "material, so multiply by the mass in grams. Parent rows marked (2) include listed progeny, which covered_by_parent lists "
                "(zero contribution while the parent is present). U-240 is listed twice in the source; the nuclide-alone row is used.",
            ),
            {key: C.scaled_float(values["activity_Bq"][0], values["activity_Bq"][1])
             for key, (_record, values) in table_b.items()},
            covered_b,
        ),
    }
    hashes = {}
    for name, doc in docs.items():
        hashes[name] = C.dump_json(args.out_dir / name, doc)
        print(
            f"{name}: sha256 {hashes[name]} coefficients {len(doc['responses'][0]['coefficients'])} "
            f"covered_by_parent {len(doc['responses'][0].get('covered_by_parent', {}))}"
        )

    sidecar = {
        "format": C.ROWS_FORMAT,
        "about": (
            "Per-row source text for the tables built from Annex VII. ACTINV does not read this file; it "
            "records what a coefficient table cannot express. Superscripts in row_text are written ^{...}."
        ),
        "source": C.source_block(
            citation="Council Directive 2013/59/Euratom, Annex VII",
            scope="Tables A (Parts 1 and 2) and B",
            **{k: v for k, v in source_common.items() if k != "row_provenance"},
        ),
        "tables": hashes,
        "notes": {
            "table_a_part1_note_1": note_a,
            "table_b_note_1": potassium,
            "table_b_note_2": note_b,
            "table_a_part1_gap_rule": gap_text,
        },
        "progeny_lists": {"Table A Part 1": progeny_a, "Table B": progeny_b},
        "source_anomalies": anomalies,
        "rows": sidecar_rows,
    }
    C.dump_json(args.out_dir / SIDECAR, sidecar)
    print(
        f"Table A Part 1 rows {len(table_a)}, Table A Part 2 rows {len(data['A2'])}, "
        f"Table B rows {len(table_b)}; anomalies {len(anomalies)}"
    )
    for line in anomalies:
        print(f"  anomaly: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
