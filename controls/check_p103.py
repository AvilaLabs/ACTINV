#!/usr/bin/env python3
"""P103 G0: independently check the frozen Part 61.55 source pack and seal."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P103_PROTOCOL.md"
SOURCE_REVIEW = ROOT / "docs/maintainers/WASTE_RULE_SOURCE_REVIEW.md"
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"
MIRROR = ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json"
XML = ROOT / "controls/fixtures/p103/61.55_2025.xml"
RESULT = ROOT / "results/g0_p103_seals.json"

# These identities are the review inputs approved for the G0 source check. A
# changed input requires a fresh source review and protocol seal.
SEALED_INPUTS = {
    "protocol_sha256": "22bbaada0e757b0c1ffd5a914ca4835c12ebc6af2c61eb971c48f6e36150983e",
    "pack_sha256": "890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7",
    "core_mirror_sha256": "890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7",
    "source_xml_sha256": "7c08a6cb64d21e082ecf62a94fc3ef0be42c64fb68443658eff393ddc0bc0864",
    "source_review_sha256": "4d316aacee158dbcc36daf4bd191d84ee7c511d471555ec3084115dabaeefd64",
}

# Independent transcription of the official CFR table. XML footnote markers
# are parsed separately and represented as absent limits, never as zero.
SOURCE_ROWS = [
    (1, "C-14", "C-14", "general", "Ci/m3", [8]),
    (1, "C-14 in activated metal", "C-14", "activated_metal", "Ci/m3", [80]),
    (1, "Ni-59 in activated metal", "Ni-59", "activated_metal", "Ci/m3", [220]),
    (1, "Nb-94 in activated metal", "Nb-94", "activated_metal", "Ci/m3", [0.2]),
    (1, "Tc-99", "Tc-99", "all", "Ci/m3", [3]),
    (1, "I-129", "I-129", "all", "Ci/m3", [0.08]),
    (1, "Alpha emitting transuranic nuclides with half-life greater than 5 years", "alpha_transuranic_gt5y", "all", "nCi/g", [100]),
    (1, "Pu-241", "Pu-241", "all", "nCi/g", [3500]),
    (1, "Cm-242", "Cm-242", "all", "nCi/g", [20000]),
    (2, "Total of all nuclides with less than 5 year half-life", "half_life_lt5y", "all", "Ci/m3", [700, None, None]),
    (2, "H-3", "H-3", "all", "Ci/m3", [40, None, None]),
    (2, "Co-60", "Co-60", "all", "Ci/m3", [700, None, None]),
    (2, "Ni-63", "Ni-63", "general", "Ci/m3", [3.5, 70, 700]),
    (2, "Ni-63 in activated metal", "Ni-63", "activated_metal", "Ci/m3", [35, 700, 7000]),
    (2, "Sr-90", "Sr-90", "all", "Ci/m3", [0.04, 150, 7000]),
    (2, "Cs-137", "Cs-137", "all", "Ci/m3", [1, 44, 4600]),
]

DECISIONS = {
    "single_component_volume_basis": "declared single component under (a)(8); it does not qualify full CA BTP conformity",
    "metal_variants_replace_general": "Metal variants replace their general rows",
    "pu241_dedicated_precedence": "A dedicated nuclide row takes precedence over a generic category",
    "cm242_table2_aggregate": "including Cm-242 even though it has a Table 1 row",
    "short_lived_named_row_precedence": "without a dedicated Table 2 row",
    "equal_five_year_boundary": "exactly five is neither",
    "unknown_metadata_fails_closed": "Missing metadata on any active nuclide yields an unknown class",
    "external_tritium_no_implicit_zero": "No implicit zero",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).replace("\u2009", " ").split())


def cell_limit(cell: ET.Element) -> float | None:
    # SU elements identify CFR footnotes. A standalone (1) is a no-limit
    # marker; numeric limits in footnoted Table 1 cells remain recoverable.
    def text_without_superscripts(node: ET.Element) -> str:
        pieces = [node.text or ""]
        for child in node:
            if child.tag != "SU":
                pieces.append(text_without_superscripts(child))
            pieces.append(child.tail or "")
        return "".join(pieces)

    value_text = text_without_superscripts(cell)
    value_text = re.sub(r"\([0-9]+\)", " ", value_text)
    value_text = value_text.replace(",", "")
    tokens = re.findall(r"(?<![A-Za-z])(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?", value_text)
    if not tokens:
        return None
    if len(tokens) != 1:
        raise ValueError(f"expected one numeric limit in {normalized_text(cell)!r}")
    return float(tokens[0])


def parse_source_tables() -> tuple[list[tuple[int, str, list[float | None]]], dict]:
    root = ET.parse(XML).getroot()
    tables = {}
    for table in root.iter("GPOTABLE"):
        title = next((normalized_text(item) for item in table if item.tag == "TTITLE"), "")
        if title in {"Table 1", "Table 2"}:
            tables[title] = table
    if set(tables) != {"Table 1", "Table 2"}:
        raise ValueError("official CFR XML does not contain exactly the expected Tables 1 and 2")

    parsed = []
    footnotes = {}
    for number, title in ((1, "Table 1"), (2, "Table 2")):
        table = tables[title]
        expected_cells = 2 if number == 1 else 4
        for row in table:
            if row.tag != "ROW":
                continue
            cells = [item for item in row if item.tag == "ENT"]
            if len(cells) != expected_cells:
                raise ValueError(f"unexpected cell count in {title}: {len(cells)}")
            label = normalized_text(cells[0])
            limits = [cell_limit(cell) for cell in cells[1:]]
            parsed.append((number, label, limits))
            if number == 2 and label in {
                "Total of all nuclides with less than 5 year half-life", "H-3", "Co-60"
            }:
                footnotes[label] = [
                    any(descendant.tag == "SU" and normalized_text(descendant) == "1" for descendant in cell.iter())
                    for cell in cells[2:]
                ]
    footnotes["table1_note"] = next(
        (normalized_text(note) for note in tables["Table 1"].iter("TNOTE")), ""
    )
    footnotes["table2_note"] = next(
        (normalized_text(note) for note in tables["Table 2"].iter("TNOTE")), ""
    )
    return parsed, footnotes


def expected_pack_rows() -> list[dict]:
    return [
        {
            "table": table,
            "id": selector if applicability in {"all", "general"} else f"{selector}_activated_metal",
            "selector": selector,
            "applicability": applicability,
            "unit": unit,
            "limits": limits,
        }
        for table, _label, selector, applicability, unit, limits in SOURCE_ROWS
    ]


def main() -> int:
    checks: dict[str, object] = {}
    identities = {
        "protocol_sha256": sha256(PROTOCOL),
        "pack_sha256": sha256(PACK),
        "core_mirror_sha256": sha256(MIRROR),
        "source_xml_sha256": sha256(XML),
        "source_review_sha256": sha256(SOURCE_REVIEW),
    }
    checks["approved_source_identities"] = {
        "expected": SEALED_INPUTS,
        "actual": identities,
        "pass": identities == SEALED_INPUTS,
    }
    mirror_hash = sha256(MIRROR)
    checks["core_mirror_bytes"] = {
        "identical": PACK.read_bytes() == MIRROR.read_bytes(),
        "sha256": mirror_hash,
        "pass": PACK.read_bytes() == MIRROR.read_bytes() and mirror_hash == identities["pack_sha256"],
    }

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    checks["pack_identity_and_units"] = {
        "schema": pack.get("schema"),
        "id": pack.get("id"),
        "source_url": pack.get("annual_source_url"),
        "source_sha256_matches_xml": pack.get("annual_source_sha256") == identities["source_xml_sha256"],
        "ci_bq": pack.get("ci_bq"),
        "year_s": pack.get("year_s"),
        "pass": pack.get("schema") == "actinv-waste-rules-1"
        and pack.get("id") == "us-nrc-10cfr61.55-v1"
        and pack.get("annual_source_url") == "https://www.govinfo.gov/content/pkg/CFR-2025-title10-vol2/xml/CFR-2025-title10-vol2-sec61-55.xml"
        and pack.get("annual_source_sha256") == identities["source_xml_sha256"]
        and pack.get("ci_bq") == 37_000_000_000
        and pack.get("year_s") == 31_557_600,
    }

    source_rows, footnotes = parse_source_tables()
    expected_rows = expected_pack_rows()
    source_exact = len(source_rows) == len(SOURCE_ROWS) and all(
        table == expected_table and label == expected_label and limits == expected_limits
        for (table, label, limits), (expected_table, expected_label, _selector, _app, _unit, expected_limits)
        in zip(source_rows, SOURCE_ROWS)
    )
    pack_rows = pack.get("rows")
    pack_exact = pack_rows == expected_rows
    # Compare parsed source values to independent transcription and separately
    # compare pack values to the transcription, preventing pack/source circularity.
    checks["source_table_rows"] = {
        "source_row_count": len(source_rows),
        "expected_row_count": len(SOURCE_ROWS),
        "rows_match_independent_transcription": source_exact,
        "pack_matches_independent_transcription": pack_exact,
        "pass": source_exact and pack_exact,
    }
    footnote1_units = "Units are nanocuries per gram." in footnotes["table1_note"]
    footnote1_unbounded = "There are no limits established for these radionuclides in Class B or C wastes." in footnotes["table2_note"]
    footnote1_class_b = "These wastes shall be Class B unless the concentrations of other nuclides in Table 2 determine the waste to be Class C independent of these nuclides." in footnotes["table2_note"]
    no_limit_markers = all(footnotes.get(label) == [True, True] for label in (
        "Total of all nuclides with less than 5 year half-life", "H-3", "Co-60"
    ))
    checks["source_footnotes"] = {
        "table1_units_note": footnotes["table1_note"],
        "table2_no_limit_note": footnotes["table2_note"],
        "table2_unbounded_rows_have_footnote_markers": no_limit_markers,
        "table1_nci_g_note_present": footnote1_units,
        "table2_no_class_b_c_limit_note_present": footnote1_unbounded,
        "table2_default_class_b_note_present": footnote1_class_b,
        "pass": no_limit_markers and footnote1_units and footnote1_unbounded and footnote1_class_b,
    }
    protocol_text = " ".join(PROTOCOL.read_text(encoding="utf-8").split())
    checks["source_review_decisions"] = {
        "decisions": DECISIONS,
        "protocol_anchors_present": {
            key: " ".join(phrase.split()) in protocol_text for key, phrase in DECISIONS.items()
        },
        "pass": all(" ".join(phrase.split()) in protocol_text for phrase in DECISIONS.values()),
    }

    # A result may be written for review even when an expected source entry is
    # wrong; only all-green G0 checks establish the seal.
    passed = all(value.get("pass", False) for value in checks.values())
    result = {
        "schema": "actinv-p103-g0-seal-1",
        "protocol": "ACTINV-P103",
        "input_sha256": identities,
        "checks": checks,
        "sealed": passed,
        "verdict": "P103-G0-SEALED" if passed else "P103-G0-FAIL",
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--g0-only"]):
        raise SystemExit("usage: python controls/check_p103.py --g0-only")
    raise SystemExit(main())
