#!/usr/bin/env python3
"""Control for the national clearance tables (Germany, United Kingdom, Canada). Pure Python, no binary.

The three bundled tables

  crates/actinv-core/data/regulatory/de-strlschv-2018-anlage-4.json
  crates/actinv-core/data/regulatory/uk-epr-2016-sch23-pt3-table-2.json
  crates/actinv-core/data/regulatory/ca-nsrdr-2000-207-schedule-2.json

are converted by scripts/regulatory/convert_national_clearance.py (ElementTree) from the official XML archived in
controls/fixtures/clearance_sources/. This checker re-reads the same XML with a second, independent reader (regular
expressions over the raw text) and checks:

  S1  Each archived XML file has the pinned SHA-256, and the table's source.source_sha256 equals it.
  S2  Every shipped coefficient equals the printed value x 1000 exactly (as a decimal, not just a float), the set of
      nuclides of every response equals the set the independent reader derives from the source columns, and no
      source value is missing or extra.
  S2b Where a nuclide is printed in several forms the shipped limit is the smallest of them and the coverage
      follows the chosen form (plain covers nothing), checked from the source.
  S3  The covered_by_parent map of every response equals the one derived from the source's progeny lists (German
      Tabelle 2, UK Table 3, Canadian footnote a) for the parents the response gives a coefficient.
  S4  The sidecar (*.rows.json) records the table's SHA-256 and carries every source row with the printed text.
  S5  The provenance block is complete: citation, edition, URL, retrieval date, licence/attribution text, and the
      "not an official version" statement.
  S6  Re-running the converter reproduces the committed tables and sidecars byte for byte.

Usage: check_jurisdiction_tables.py
       check_jurisdiction_tables.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "crates" / "actinv-core" / "data" / "regulatory"
SOURCES = ROOT / "controls" / "fixtures" / "clearance_sources"
CONVERTER = ROOT / "scripts" / "regulatory" / "convert_national_clearance.py"

PINNED = {
    "de/BJNR203600018.xml": "aeb25a919ed28af9848623e4c7618ceb498655945efcfd5aa2e93d93d2ab0925",
    "uk/data.xml": "3a2fb528b1f80e45438adb2ab6a3d5dafdd5be011bf4fcf599f54eb85902508d",
    "ca/SOR-2000-207.xml": "55d498a0daf3b4ab736fd416510c898519b4d002cea648301916e69ba5e211ef",
}
NAMES = {
    "de": "de-strlschv-2018-anlage-4",
    "uk": "uk-epr-2016-sch23-pt3-table-2",
    "ca": "ca-nsrdr-2000-207-schedule-2",
}
SOURCE_FILES = {"de": "de/BJNR203600018.xml", "uk": "uk/data.xml", "ca": "ca/SOR-2000-207.xml"}
# The UK fixture is the official data.xml with `key-<32 hex>` ids rewritten to `ref-<32 hex>` (secret scanning reads them
# as Mailgun keys); reversing the rewrite must give the official file.
UK_OFFICIAL_SHA256 = "da9dbf2e66cd043408c3f237d3d996c2d878bd7b8eda4cfeba5de5a96c816702"
# German response id -> column of Anlage 4 Tabelle 1 (column 4, the HRQ activity, is never used)
DE_COLUMNS = {
    "unrestricted-clearance": 3,
    "rubble-gt-1000-mg-a": 6,
    "soil": 7,
    "landfill-le-100-mg-a": 8,
    "incineration-le-100-mg-a": 9,
    "landfill-le-1000-mg-a": 10,
    "incineration-le-1000-mg-a": 11,
    "scrap-metal-recycling": 14,
}
UK_RESPONSE = "table-2-solid-or-relevant-liquid"
CA_RESPONSE = "unconditional-clearance"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------- independent reader


def plain(fragment: str) -> str:
    """Raw XML fragment -> text: superscripts as ^(x), other tags dropped, entities decoded."""
    fragment = re.sub(r"<(?:Sup|Superior)\b[^>]*>(.*?)</(?:Sup|Superior)>", lambda m: "^(" + m.group(1).strip() + ")", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    text = html.unescape(fragment).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\^\(\s*([^)]*?)\s*\)", r"^(\1)", text)


def value_of(text: str) -> Decimal:
    s = text.replace("–", "-").replace("−", "-").strip()
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?) ?E ?([+-]?\d+)", s)
    if m:
        return Decimal(m.group(1).replace(",", ".")) * (Decimal(10) ** int(m.group(2)))
    m = re.fullmatch(r"(?:(\d+(?:[.,]\d+)?) ?[×x] ?)?10\^\(([+-]?\d+)\)", s)
    if m:
        return Decimal((m.group(1) or "1").replace(",", ".")) * (Decimal(10) ** int(m.group(2)))
    if re.fullmatch(r"\d+(?:[.,]\d+)?", s):
        return Decimal(s.replace(",", "."))
    raise ValueError(f"unreadable value {text!r}")


def canon(printed: str, german: bool = False) -> str | None:
    s = re.sub(r"(\+|sec)$", "", printed.strip()).strip()
    m = re.fullmatch(r"([A-Z][a-z]?)-(\d+)(m\d?|n)?", s)
    if not m:
        return None
    element, mass, iso = m.groups()
    if iso is None:
        return f"{element}{mass}"
    if iso == "n" and german:
        return f"{element}{mass}m2"
    if iso == "m":
        return f"{element}{mass}m1"
    return f"{element}{mass}{iso}"


def tokens(text: str, german: bool = False) -> list[str]:
    out = []
    for token in re.findall(r"[A-Z][a-z]?-\d+(?:m\d?|n)?", text):
        key = canon(token, german)
        if key and key not in out:
            out.append(key)
    return out


def invert(parents: dict[str, list[str]], available: set[str]) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {}
    for parent, daughters in parents.items():
        if parent in available:
            for d in daughters:
                if d != parent:
                    out.setdefault(d, set()).add(parent)
    return {k: sorted(out[k]) for k in sorted(out)}


def read_de(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    start = text.index("<enbez>Anlage 4</enbez>")
    end = text.index("</norm>", start)
    segment = text[start:end]
    tables = re.findall(r"<table\b.*?</table>", segment, flags=re.S)
    first = next(t for t in tables if 'tabstyle="Tabelle1"' in t[:400])
    rows = []
    for position, body in enumerate(re.findall(r"<row\b[^>]*>(.*?)</row>", first, flags=re.S), start=1):
        cells = {}
        for attrs, inner in re.findall(r"<entry\b([^>]*?)(?:/>|>(.*?)</entry>)", body, flags=re.S):
            column = re.search(r'colname="col(\d+)"', attrs)
            if column:
                cells[int(column.group(1))] = plain(inner)
        match = re.match(r"^([A-Z][a-z]?-\d+[mn]?)(\+?)\s*(.*)$", cells.get(1, ""))
        if match:
            rows.append({
                "source_row": position,
                "printed": match.group(1) + match.group(2),
                "key": canon(match.group(1), True),
                "plus": bool(match.group(2)),
                "qualifier": match.group(3) or None,
                "cells": cells,
            })
    last = tables[-1]
    table2 = []
    for body in re.findall(r"<row\b[^>]*>(.*?)</row>", last, flags=re.S):
        entries = [plain(inner) for _, inner in re.findall(r"<entry\b([^>]*?)(?:/>|>(.*?)</entry>)", body, flags=re.S)]
        table2.append(entries)
    if table2[0] != ["Mutternuklid", "Tochternuklide"]:
        raise SystemExit(f"unexpected Tabelle 2 header {table2[0]!r}")
    return {"rows": rows, "table2": table2[1:]}


def expected_de(src: dict) -> dict:
    """response id -> (coefficients Bq/kg as Decimal, covered_by_parent)."""
    plain_rows = [r for r in src["rows"] if not r["qualifier"]]
    parents = {}
    for parent_text, daughters_text in src["table2"]:
        parents[canon(parent_text, True)] = tokens(daughters_text, True)
    out = {}
    for response, column in DE_COLUMNS.items():
        forms: dict[str, list[tuple[Decimal, bool]]] = {}
        for row in plain_rows:
            text = row["cells"].get(column, "")
            if text:
                forms.setdefault(row["key"], []).append((value_of(text) * 1000, row["plus"]))
        # strictest limit; on a tie the plain row (it covers no progeny)
        chosen = {k: min(v) for k, v in forms.items()}
        coefficients = {k: v[0] for k, v in chosen.items()}
        covering = {k: parents[k] for k, v in chosen.items() if v[1] and k in parents}
        out[response] = (coefficients, invert(covering, set(coefficients)))
    return out


def read_uk(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")

    def table(number: str) -> list[list[str]]:
        start = text.index(f"<Number>{number}</Number>")
        end = re.search(r"</(?:xhtml:)?table>", text[start:]).end() + start
        rows = []
        for body in re.findall(r"<(?:xhtml:)?tr\b[^>]*>(.*?)</(?:xhtml:)?tr>", text[start:end], flags=re.S):
            cells = re.findall(r"<(?:xhtml:)?t[dh]\b[^>]*>(.*?)</(?:xhtml:)?t[dh]>", body, flags=re.S)
            rows.append([plain(c) for c in cells])
        return rows[1:]  # header row

    return {"table2": table("Table 2"), "table3": table("Table 3")}


def expected_uk(src: dict) -> dict:
    priority = {"plain": 0, "plus": 1, "sec": 2}
    chosen: dict[str, tuple[Decimal, int, str]] = {}
    rows = []
    for cells in src["table2"]:
        # the catch-all "Any other ... radionuclide" row is split over two rows; the first has a blank name cell
        if cells == ["0.01"] or cells[0].startswith("Any other"):
            continue
        name, value = cells
        suffix = "sec" if name.endswith("sec") else "plus" if name.endswith("+") else "plain"
        rows.append((canon(name), name, suffix, value_of(value) * 1000))
    for key, name, suffix, limit in rows:
        rank = (limit, priority[suffix], name)
        if key not in chosen or rank < chosen[key]:
            chosen[key] = rank
    coefficients = {k: v[0] for k, v in chosen.items()}
    kept = {}
    for name, daughters in src["table3"]:
        key = canon(name)
        if key in chosen and chosen[key][2] == name and name != key:
            kept[key] = tokens(daughters)
    return {UK_RESPONSE: (coefficients, invert(kept, set(coefficients)))}


def read_ca(path: Path, symbols: dict[str, str]) -> dict:
    text = path.read_text(encoding="utf-8")
    start = text.index("<Label>SCHEDULE 2</Label>")
    end = text.index("</Schedule>", start)
    segment = text[start:end]
    body = segment[segment.index("<tbody"):segment.index("</tbody>")]
    rows = []
    for row in re.findall(r"<row>(.*?)</row>", body, flags=re.S):
        entries = re.findall(r"<entry\b[^>]*>(.*?)</entry>", row, flags=re.S)
        name = plain(re.sub(r"<FootnoteRef\b.*?</FootnoteRef>", "", entries[0], flags=re.S))
        rows.append({"name": name, "plus": "<FootnoteRef" in entries[0], "value": plain(entries[1])})
    parents = {}
    for label, item in re.findall(r"<Item><Label>(.*?)</Label><Text>(.*?)</Text></Item>", segment, flags=re.S):
        parents[plain(label)] = plain(item)
    out = []
    for row in rows:
        m = re.fullmatch(r"([A-Z][a-z]+) (\d+m?\d?)", row["name"])
        key = canon(f"{symbols[m.group(1)]}-{m.group(2)}") if m else None
        out.append(dict(row, key=key))
    return {"rows": out, "parents": parents}


def expected_ca(src: dict) -> dict:
    coefficients = {r["key"]: value_of(r["value"]) * 1000 for r in src["rows"] if r["key"]}
    plus = {r["key"] for r in src["rows"] if r["key"] and r["plus"]}
    parents = {}
    for label, daughters in src["parents"].items():
        key = canon(label)
        if key:
            if key not in plus:
                raise SystemExit(f"footnote a parent {label} is not marked in the table")
            parents[key] = tokens(daughters)
    return {CA_RESPONSE: (coefficients, invert(parents, set(coefficients)))}


# ---------------------------------------------------------------------------------------------- checks


def load_converter():
    spec = importlib.util.spec_from_file_location("convert_national_clearance", CONVERTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_one(code: str, table: dict, sidecar: dict, table_bytes: bytes, expected: dict, findings: list[str]) -> dict:
    def fail(message: str) -> None:
        findings.append(f"{code}: {message}")

    source = table.get("source", {})
    # S1
    digest = sha256_of(SOURCES / SOURCE_FILES[code])
    if digest != PINNED[SOURCE_FILES[code]]:
        fail(f"archived source has sha256 {digest}, pinned {PINNED[SOURCE_FILES[code]]}")
    if source.get("source_sha256") != PINNED[SOURCE_FILES[code]]:
        fail("source.source_sha256 differs from the pinned source hash")
    if code == "uk":
        restored = re.sub(rb"ref-([0-9a-f]{32})", rb"key-\1", (SOURCES / SOURCE_FILES[code]).read_bytes())
        if hashlib.sha256(restored).hexdigest() != UK_OFFICIAL_SHA256:
            fail("the UK fixture does not reverse to the official legislation.gov.uk data.xml")
    if source.get("source_file") != f"controls/fixtures/clearance_sources/{SOURCE_FILES[code]}":
        fail(f"source.source_file is {source.get('source_file')!r}")
    # S2, S3
    if table.get("format") != "actinv-radiological-table-1":
        fail("format is not actinv-radiological-table-1")
    responses = {r["id"]: r for r in table.get("responses", [])}
    if set(responses) != set(expected):
        fail(f"response ids {sorted(responses)} differ from the expected {sorted(expected)}")
    checked = 0
    for response_id, (coefficients, covered) in expected.items():
        response = responses.get(response_id)
        if response is None:
            continue
        if response.get("kind") != "clearance_index":
            fail(f"{response_id}: kind {response.get('kind')!r}")
        shipped = response.get("coefficients", {})
        if set(shipped) != set(coefficients):
            missing = sorted(set(coefficients) - set(shipped))[:5]
            extra = sorted(set(shipped) - set(coefficients))[:5]
            fail(f"{response_id}: nuclide sets differ (missing {missing}, extra {extra})")
        for key, want in coefficients.items():
            got = shipped.get(key)
            if got is None:
                continue
            checked += 1
            if not isinstance(got, float) or Decimal(repr(got)) != want:
                fail(f"{response_id}: {key} ships {got!r}, source gives {want} Bq/kg")
        if response.get("covered_by_parent", {}) != covered:
            fail(f"{response_id}: covered_by_parent differs from the source progeny lists")
    # S4
    if sidecar.get("tables", {}).get(f"{NAMES[code]}.json") != hashlib.sha256(table_bytes).hexdigest():
        fail("sidecar does not record the table's SHA-256")
    # S5
    for field in ("citation", "edition", "url", "jurisdiction", "scope", "retrieved", "converter", "licence"):
        if not source.get(field):
            fail(f"source.{field} is empty")
    licence = source.get("licence", "")
    required = {
        "de": ["5(1)", "UrhG", "Not an official version"],
        "uk": ["Open Government Licence v3.0"],
        "ca": ["SI/97-5", "not an official version"],
    }[code]
    for phrase in required:
        if phrase not in licence:
            fail(f"licence text lacks {phrase!r}")
    return {"coefficients_checked": checked, "responses": sorted(responses)}


def check_sidecar_rows(code: str, sidecar: dict, source: dict, findings: list[str]) -> int:
    rows = sidecar.get("rows", [])
    if code == "de":
        want = [r for r in source["rows"] if not r["qualifier"]]
        if len(rows) != len(want):
            findings.append(f"de: sidecar has {len(rows)} rows, the source has {len(want)}")
            return 0
        for got, ref in zip(rows, want):
            if got["printed"] != ref["printed"] or got["source_row"] != ref["source_row"]:
                findings.append(f"de: sidecar row {got['source_row']} {got['printed']} differs from the source")
                continue
            for column in (2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14):
                text = ref["cells"].get(column, "")
                entry = got["values"].get(f"col{column}")
                if (entry["text"] if entry else "") != text:
                    findings.append(f"de: {ref['printed']} col{column} sidecar {entry!r} vs source {text!r}")
            if "col4" in got["values"] or "col15" in got["values"]:
                findings.append(f"de: {ref['printed']} sidecar carries the HRQ or half-life column")
    elif code == "uk":
        want = [r for r in source["table2"] if len(r) == 2 and not r[0].startswith("Any other")]
        if [r["printed"] for r in rows] != [r[0] for r in want]:
            findings.append("uk: sidecar rows differ from the source Table 2 rows")
        else:
            for got, ref in zip(rows, want):
                if got["values"]["concentration_Bq_per_g"]["text"] != ref[1]:
                    findings.append(f"uk: {ref[0]} sidecar text differs from the source")
    else:
        want = source["rows"]
        if [r["printed"] for r in rows] != [r["name"] for r in want]:
            findings.append("ca: sidecar rows differ from the source Schedule 2 rows")
        else:
            for got, ref in zip(rows, want):
                if got["values"]["activity_concentration_Bq_per_g"]["text"] != ref["value"]:
                    findings.append(f"ca: {ref['name']} sidecar text differs from the source")
    # form_choice: exactly the nuclides printed in several forms, with the stated rule
    if code == "de":
        counts = {}
        for r in source["rows"]:
            if not r["qualifier"]:
                counts[r["key"]] = counts.get(r["key"], 0) + 1
    elif code == "uk":
        counts = {}
        for r in source["table2"]:
            if len(r) == 2 and not r[0].startswith("Any other"):
                counts[canon(r[0])] = counts.get(canon(r[0]), 0) + 1
    else:
        counts = {}
    duplicated = {k for k, n in counts.items() if n > 1}
    choice = sidecar.get("form_choice", {})
    if set(choice) != duplicated:
        findings.append(f"{code}: form_choice nuclides {sorted(choice)} differ from the duplicated {sorted(duplicated)}")
    for key, entry in choice.items():
        if entry.get("rule") != "strictest limit; progeny coverage follows the chosen row":
            findings.append(f"{code}: form_choice rule of {key} is {entry.get('rule')!r}")
    return len(rows)


def run_checks(data_dir: Path = DATA) -> tuple[list[str], dict]:
    findings: list[str] = []
    converter = load_converter()
    symbols = converter.CA_ELEMENTS
    readers = {
        "de": (read_de, expected_de),
        "uk": (read_uk, expected_uk),
        "ca": (lambda p: read_ca(p, symbols), expected_ca),
    }
    summary = {}
    for code, (reader, expecter) in readers.items():
        name = NAMES[code]
        table_path = data_dir / f"{name}.json"
        table_bytes = table_path.read_bytes()
        table = json.loads(table_bytes)
        sidecar = json.loads((data_dir / f"{name}.rows.json").read_text(encoding="utf-8"))
        source = reader(SOURCES / SOURCE_FILES[code])
        summary[code] = check_one(code, table, sidecar, table_bytes, expecter(source), findings)
        summary[code]["sidecar_rows"] = check_sidecar_rows(code, sidecar, source, findings)
    # S6
    with tempfile.TemporaryDirectory() as out:
        if converter.main([str(SOURCES), out]) != 0:
            findings.append("converter failed")
        else:
            for name in NAMES.values():
                for suffix in (".json", ".rows.json"):
                    if (Path(out) / f"{name}{suffix}").read_bytes() != (data_dir / f"{name}{suffix}").read_bytes():
                        findings.append(f"{name}{suffix} differs from a fresh converter run")
    return findings, summary


def selftest() -> None:
    """Corrupt one shipped value in a copy of the data and require the checker to notice."""
    with tempfile.TemporaryDirectory() as tmp:
        copy_dir = Path(tmp)
        for path in DATA.glob("*"):
            if any(path.name.startswith(n) for n in NAMES.values()):
                (copy_dir / path.name).write_bytes(path.read_bytes())
        clean, _ = run_checks(copy_dir)
        if clean:
            raise SystemExit(f"selftest failed: clean copy reports {clean[:3]}")
        target = copy_dir / f"{NAMES['de']}.json"
        table = json.loads(target.read_text(encoding="utf-8"))
        table["responses"][0]["coefficients"]["Co60"] *= 1.0000001
        target.write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        found, _ = run_checks(copy_dir)
        if not any("Co60" in f for f in found):
            raise SystemExit("selftest failed: a changed German coefficient was not caught")
        table["responses"][0]["coefficients"]["Co60"] /= 1.0000001
        del table["responses"][0]["coefficients"]["Cs137"]
        target.write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        found, _ = run_checks(copy_dir)
        if not any("nuclide sets differ" in f for f in found):
            raise SystemExit("selftest failed: a removed nuclide was not caught")
    print("jurisdiction tables selftest: PASS")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    findings, summary = run_checks()
    print(json.dumps(summary, indent=1))
    if findings:
        for finding in findings[:50]:
            print("FAIL", finding)
        print(f"jurisdiction tables: FAIL ({len(findings)} findings)")
        return 3
    print("jurisdiction tables: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
