#!/usr/bin/env python3
"""P105 successor controls, reusing the immutable P103 source and vector seals."""
from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
import argparse
import os
import subprocess
import tempfile
import copy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P103_PROTOCOL.md"
SOURCE_REVIEW = ROOT / "docs/maintainers/WASTE_RULE_SOURCE_REVIEW.md"
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"
MIRROR = ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json"
XML = ROOT / "controls/fixtures/p103/61.55_2025.xml"
RESULT = ROOT / "results/g0_p103_seals.json"
VECTOR_FIXTURE = ROOT / "controls/fixtures/p103/classification_vectors.json"
VECTOR_SEAL = ROOT / "results/g0b_p103_vectors.json"
CLASS_RESULT = ROOT / "results/g1_p105_classes.json"
SUCCESSOR_PROTOCOL = ROOT / "protocols/ACTINV-P105_PROTOCOL.md"
SUCCESSOR_PROTOCOL_SHA256 = "f38b973532010f4a7e4dc644faaffe6c8c420e42aa2e27c898e029ecef035075"
SUCCESSOR_AMENDMENT = ROOT / "protocols/ACTINV-P104_AMENDMENT_A.md"
SUCCESSOR_AMENDMENT_SHA256 = "b83a177adcd2614b771da62726a0e3d941f9e986c62cd218f8baea3a7ec8bf54"
SUCCESSOR_RESULT = ROOT / "results/g0_p105_successor.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
FIVE_YEAR_S = 5.0 * 365.25 * 86400.0
CI_BQ_PER_CM3 = 37_000.0
NCI_BQ_PER_G = 37.0
VECTOR_SHA256 = "bfefb655b2df52da7ccb7a93cfd7c22bdc18762917e900e828ebd97d58b2bb42"
VECTOR_GENERATOR_SHA256 = "89c05549d8a9b71c256e3688f64b56cb62f24b2e59b7d5ab4a7e79ff6adbd284"


def verify_successor_g0(no_write: bool = False) -> int:
    """Bind P105 to its registered protocol and freeze successor control hashes."""
    protocol_hash = sha256(SUCCESSOR_PROTOCOL) if SUCCESSOR_PROTOCOL.is_file() else None
    registration = f"{SUCCESSOR_PROTOCOL_SHA256}  {SUCCESSOR_PROTOCOL.relative_to(ROOT)}"
    registered = registration in (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    amendment_hash = sha256(SUCCESSOR_AMENDMENT) if SUCCESSOR_AMENDMENT.is_file() else None
    amendment_registration = f"{SUCCESSOR_AMENDMENT_SHA256}  {SUCCESSOR_AMENDMENT.relative_to(ROOT)}"
    amendment_registered = amendment_registration in (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    try:
        inherited_source = json.loads(RESULT.read_text(encoding="utf-8"))
        inherited_source_ok = inherited_source.get("sealed") is True and inherited_source.get("verdict") == "P103-G0-SEALED"
    except (OSError, json.JSONDecodeError):
        inherited_source_ok = False
    control_files = {
        "checker_sha256": ROOT / "controls/check_p105.py",
        "budget_helper_sha256": ROOT / "controls/p105_budget_control.py",
        "child_regression_sha256": ROOT / "controls/test_p105_children.py",
    }
    checks = {
        "protocol_registered": {
            "expected_sha256": SUCCESSOR_PROTOCOL_SHA256,
            "actual_sha256": protocol_hash,
            "registered": registered,
            "pass": protocol_hash == SUCCESSOR_PROTOCOL_SHA256 and registered,
        },
        "inherited_p103_source_seal": {
            "result_path": str(RESULT.relative_to(ROOT)),
            "read_only": True,
            "pass": inherited_source_ok,
        },
        "repair_amendment_registered": {
            "expected_sha256": SUCCESSOR_AMENDMENT_SHA256,
            "actual_sha256": amendment_hash,
            "registered": amendment_registered,
            "pass": amendment_hash == SUCCESSOR_AMENDMENT_SHA256 and amendment_registered,
        },
    }
    code_hashes = {name: sha256(path) for name, path in control_files.items() if path.is_file()}
    code_hashes_complete = len(code_hashes) == len(control_files)
    sealed = all(check["pass"] for check in checks.values()) and code_hashes_complete
    result = {
        "schema": "actinv-p105-successor-g0-1",
        "phase": "P105",
        "protocol": "ACTINV-P105",
        "protocol_sha256": protocol_hash,
        "amendment_sha256": amendment_hash,
        "inherited_protocol_sha256": sha256(PROTOCOL),
        "inherited_source_seal_sha256": sha256(RESULT),
        "inherited_vector_fixture_sha256": sha256(VECTOR_FIXTURE),
        "inherited_vector_seal_sha256": sha256(VECTOR_SEAL),
        "control_hashes": code_hashes,
        "checks": checks,
        "sealed": sealed,
        "verdict": "P105-G0-SEALED" if sealed else "P105-G0-FAIL",
    }
    if no_write:
        try:
            persisted = json.loads(SUCCESSOR_RESULT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            result["sealed"] = False
            result["verdict"] = "P105-G0-FAIL"
            result["checks"]["persisted_successor_seal"] = {"pass": False, "error": str(error)}
        else:
            same_result = persisted == result
            result["checks"]["persisted_successor_seal"] = {
                "same_result": same_result,
                "pass": same_result and persisted.get("sealed") is True,
            }
            result["sealed"] = result["sealed"] and same_result and persisted.get("sealed") is True
            result["verdict"] = "P105-G0-SEALED" if result["sealed"] else "P105-G0-FAIL"
    else:
        SUCCESSOR_RESULT.parent.mkdir(parents=True, exist_ok=True)
        SUCCESSOR_RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["sealed"] else 1

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


def _fraction(activity_bq_per_g: float, unit: str, limit: float, mass_g: float, volume_cm3: float) -> float:
    total_bq = activity_bq_per_g * mass_g
    if unit == "Ci/m3":
        return total_bq / (limit * CI_BQ_PER_CM3 * volume_cm3)
    if unit == "nCi/g":
        return total_bq / (limit * NCI_BQ_PER_G * mass_g)
    raise ValueError(f"unrecognized rule unit: {unit!r}")


def _concentration(activity_bq_per_g: float, unit: str, mass_g: float, volume_cm3: float) -> float:
    total_bq = activity_bq_per_g * mass_g
    if unit == "Ci/m3":
        return total_bq / (37_000_000_000.0 * volume_cm3 * 1.0e-6)
    if unit == "nCi/g":
        return total_bq / 37.0 / mass_g
    raise ValueError(f"unrecognized rule unit: {unit!r}")


def independent_classification(case: dict, rows: list[dict]) -> dict:
    """Recompute vector row fractions and class directly from raw Bq/g input."""
    activity = case["activity_Bq_per_g"]
    properties = case["nuclide_properties"]
    mass_g = case["mass_g"]
    volume_cm3 = case["displaced_volume_cm3"]
    waste_type = case["waste_type"]
    active = {name: value for name, value in activity.items() if value > 0.0}
    unknown = sorted(name for name in active if name not in properties)
    t1_rows = [row for row in rows if row["table"] == 1 and row["applicability"] in ("all", waste_type)]
    t2_rows = [row for row in rows if row["table"] == 2 and row["applicability"] in ("all", waste_type)]
    t2_named = {row["selector"].replace("-", "") for row in t2_rows if row["selector"] not in ("half_life_lt5y",)}
    row_fractions: dict[str, float] = {}
    table1_names: set[str] = set()
    t2_by_column: list[list[tuple[str, float]]] = [[], [], []]
    t2_names_by_column: list[set[str]] = [set(), set(), set()]
    row_details: list[dict] = []

    for row in t1_rows:
        selector = row["selector"].replace("-", "")
        if selector == "alpha_transuranic_gt5y":
            names = [
                name for name in active
                if name in properties
                and properties[name]["z"] > 92
                and properties[name]["alpha_emitting"] is True
                and properties[name]["half_life_s"] > FIVE_YEAR_S
                and name != "Pu241"
            ]
        else:
            names = [selector] if selector in active else []
        limit = row["limits"][0]
        if names:
            fractions = [
                _fraction(active[name], row["unit"], limit, mass_g, volume_cm3)
                for name in names
            ]
            row_fractions[f"T1:{row['id']}"] = math.fsum(fractions)
            table1_names.update(names)
            row_details.extend({
                "table": 1, "column": None, "row_id": row["id"], "nuclide": name,
                "unit": row["unit"],
                "concentration": _concentration(active[name], row["unit"], mass_g, volume_cm3),
                "limit": limit, "fraction": fraction,
            } for name, fraction in zip(names, fractions))

    for row in t2_rows:
        selector = row["selector"].replace("-", "")
        if selector == "half_life_lt5y":
            names = [
                name for name in active
                if name in properties
                and 0.0 < properties[name]["half_life_s"] < FIVE_YEAR_S
                and name not in t2_named
            ]
        else:
            names = [selector] if selector in active else []
        for column, limit in enumerate(row["limits"]):
            if limit is None:
                for name in names:
                    row_details.append({
                        "table": 2, "column": column + 1, "row_id": row["id"], "nuclide": name,
                        "unit": row["unit"],
                        "concentration": _concentration(active[name], row["unit"], mass_g, volume_cm3),
                        "limit": None, "fraction": None,
                    })
                continue
            fraction = math.fsum(
                _fraction(active[name], "Ci/m3", limit, mass_g, volume_cm3)
                for name in names
            )
            if names:
                row_fractions[f"T2:{row['id']}:col{column + 1}"] = fraction
                t2_by_column[column].append((row["id"], fraction))
                t2_names_by_column[column].update(names)
                row_details.extend({
                    "table": 2, "column": column + 1, "row_id": row["id"], "nuclide": name,
                    "unit": row["unit"],
                    "concentration": _concentration(active[name], row["unit"], mass_g, volume_cm3),
                    "limit": limit,
                    "fraction": _fraction(active[name], "Ci/m3", limit, mass_g, volume_cm3),
                } for name in names)

    t1_sum = math.fsum(value for key, value in row_fractions.items() if key.startswith("T1:"))
    t1_count = len(table1_names)
    t2_sums = [math.fsum(value for _row_id, value in column) for column in t2_by_column]
    t2_counts = [len(names) for names in t2_names_by_column]

    unknown_external = case.get("external_tritium", {}).get("status") == "required"
    unknown_all = sorted(set(unknown))
    t1_contributor_names = sorted(table1_names)
    constraints = []
    for target, t2_column in (("A", 0), ("B", 1), ("C", 2)):
        t1_threshold = 1.0 if target == "C" else 0.1
        for table, column, source_sum, contributors in (
            (1, None, t1_sum, t1_contributor_names),
            (2, t2_column + 1, t2_sums[t2_column], sorted(t2_names_by_column[t2_column])),
        ):
            normalized_sum = source_sum / t1_threshold if table == 1 else source_sum
            strict = len(contributors) > 1
            passes = normalized_sum < 1.0 if strict else normalized_sum <= 1.0
            constraints.append({
                "target_class": target,
                "table": table,
                "column": column,
                "source_sum_fraction": source_sum,
                "normalized_sum": normalized_sum,
                "normalized_margin": 1.0 - normalized_sum,
                "strict": strict,
                "passes": passes,
                "contributor_count": len(contributors),
                "contributors": contributors,
            })
    calculated_class = next(
        (target for target in ("A", "B", "C") if all(
            constraint["passes"] for constraint in constraints if constraint["target_class"] == target
        )),
        "above_class_c",
    )
    final_class = "unknown" if unknown_all or unknown_external else calculated_class
    evaluation_class = "unknown" if unknown_all else calculated_class
    binding_target = calculated_class if evaluation_class == "unknown" else evaluation_class
    if binding_target == "above_class_c":
        binding_target = "C"
    binding = [item for item in constraints if item["target_class"] == binding_target]
    max_normalized = max((item["normalized_sum"] for item in binding), default=0.0)
    failed = any(not item["passes"] for item in binding)
    binding = [item for item in binding if
               abs(item["normalized_sum"] - max_normalized) <= 1e-15 * max(abs(max_normalized), 1.0)
               or (failed and not item["passes"])]
    row_details.sort(key=lambda item: (item["table"], item["column"] or 0, item["row_id"], item["nuclide"]))
    classified = table1_names | set().union(*t2_names_by_column)
    unlisted = sorted(name for name in active if name in properties and name not in classified)
    return {
        "class": final_class,
        "evaluation_class": evaluation_class,
        "calculated_only_class": calculated_class,
        "coverage": "incomplete" if unknown_all else "complete",
        "unknown_nuclides": unknown_all,
        "unknown_reason": "required external H-3 not declared" if unknown_external else (unknown_all if unknown_all else None),
        "row_details": row_details,
        "row_fractions": row_fractions,
        "constraints": constraints,
        "binding_constraints": binding,
        "unlisted_nuclides": unlisted,
        "unlisted_activity_bq": math.fsum(active[name] * mass_g for name in unlisted),
    }


def close_number(actual: object, expected: object) -> bool:
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return False
    if not isinstance(expected, (int, float)) or isinstance(expected, bool):
        return False
    left, right = float(actual), float(expected)
    return math.isfinite(left) and math.isfinite(right) and abs(left - right) <= max(1.0e-12, 1.0e-10 * max(abs(left), abs(right)))


def verify_frozen_vectors() -> tuple[dict, list[dict], dict]:
    document = json.loads(VECTOR_FIXTURE.read_text(encoding="utf-8"))
    seal = json.loads(VECTOR_SEAL.read_text(encoding="utf-8"))
    digest = sha256(VECTOR_FIXTURE)
    generator_digest = sha256(ROOT / "controls/p103_vector_fixture.py")
    if document.get("schema") != "actinv-p103-classification-vectors-1":
        raise ValueError("unsupported P103 classification vector fixture schema")
    if seal.get("schema") != "actinv-p103-vector-seal-1" or seal.get("phase") != "P103":
        raise ValueError("P103 G0b vector seal is absent or has an unsupported schema")
    if seal.get("fixture_sha256") != digest or digest != VECTOR_SHA256:
        raise ValueError("P103 vector fixture hash does not match its pre-runtime seal")
    if seal.get("generator_sha256") != generator_digest or generator_digest != VECTOR_GENERATOR_SHA256:
        raise ValueError("P103 vector generator hash does not match its pre-runtime seal")
    if seal.get("protocol_sha256") != sha256(PROTOCOL) or seal.get("vector_count") != len(document.get("vectors", [])):
        raise ValueError("P103 G0b vector seal protocol identity or vector count differs")
    if not (ROOT / "results/g0_p103_seals.json").is_file():
        raise ValueError("P103 G0 source seal is missing")
    source_seal = json.loads((ROOT / "results/g0_p103_seals.json").read_text(encoding="utf-8"))
    if source_seal.get("sealed") is not True or source_seal.get("verdict") != "P103-G0-SEALED":
        raise ValueError("P103 G0 source seal has not passed")
    pack_rows = json.loads(PACK.read_text(encoding="utf-8"))["rows"]
    vectors = document["vectors"]
    for case in vectors:
        actual = independent_classification(case, pack_rows)
        expected = case["expected"]
        for field in ("class", "calculated_only_class", "coverage", "unknown_nuclides"):
            if actual[field] != expected[field]:
                raise ValueError(f"vector {case['id']} expected {field} differs from independent arithmetic")
        expected_unknown_reason = expected.get("external_unknown_reason")
        if expected_unknown_reason is None and expected.get("unknown_nuclides"):
            expected_unknown_reason = expected["unknown_nuclides"]
        if actual["unknown_reason"] != expected_unknown_reason:
            raise ValueError(f"vector {case['id']} expected unknown_reason differs")
        if actual["row_fractions"].keys() != expected["row_fractions"].keys():
            raise ValueError(f"vector {case['id']} row-fraction inventory differs")
        for key, value in actual["row_fractions"].items():
            expected_value = expected["row_fractions"][key]
            if not close_number(value, expected_value):
                raise ValueError(f"vector {case['id']} row fraction {key} differs")
        if not close_number(actual["unlisted_activity_bq"], math.fsum(
            case["activity_Bq_per_g"][name] * case["mass_g"] for name in actual["unlisted_nuclides"]
        )):
            raise ValueError(f"vector {case['id']} unlisted activity arithmetic differs")
        expected_constraints = expected["constraints"]
        if len(actual["constraints"]) != len(expected_constraints):
            raise ValueError(f"vector {case['id']} expected constraint count differs")
        for got, want in zip(actual["constraints"], expected_constraints):
            for field in ("target_class", "table", "column", "strict", "passes", "contributor_count", "contributors"):
                if got[field] != want[field]:
                    raise ValueError(f"vector {case['id']} constraint {field} differs")
            for field in ("source_sum_fraction", "normalized_sum"):
                if not close_number(got[field], want[field]):
                    raise ValueError(f"vector {case['id']} constraint {field} differs")
            if not close_number(got["normalized_margin"], 1.0 - want["normalized_sum"]):
                raise ValueError(f"vector {case['id']} constraint normalized_margin differs")
    return document, vectors, {"fixture_sha256": digest, "generator_sha256": generator_digest, "vector_count": len(vectors)}


def _constraint_key(value: dict) -> tuple:
    return (value.get("target_class"), value.get("table"), value.get("column") or 0)


def _compare_constraint_lists(actual: object, expected: list[dict], label: str) -> list[str]:
    if not isinstance(actual, list) or len(actual) != len(expected):
        return [f"{label} count differs"]
    actual_sorted = sorted(actual, key=_constraint_key)
    expected_sorted = sorted(expected, key=_constraint_key)
    errors = []
    for index, (got, want) in enumerate(zip(actual_sorted, expected_sorted)):
        for field in ("target_class", "table", "column", "strict", "passes", "contributor_count", "contributors"):
            if got.get(field) != want[field]:
                errors.append(f"{label}[{index}].{field} differs")
        for field in ("source_sum_fraction", "normalized_sum", "normalized_margin"):
            if not close_number(got.get(field), want[field]):
                errors.append(f"{label}[{index}].{field} differs")
    return errors


def verify_vector_target(case: dict, expected: dict, target: object) -> list[str]:
    label = case["id"]
    if not isinstance(target, dict):
        return [f"{label}: target result is not an object"]
    errors = []
    target_expected = {
        "class": expected["class"],
        "calculated_only_class": "unknown" if expected["unknown_nuclides"] else expected["calculated_only_class"],
        "coverage": expected["coverage"],
        "status": "unknown" if expected["class"] == "unknown" else "nominal",
        "unknown_reason": expected.get("external_unknown_reason", expected.get("unknown_nuclides") or None),
    }
    for field, value in target_expected.items():
        if target.get(field) != value:
            errors.append(f"{label}: target {field} differs")
    if target.get("t_s") != 0.0:
        errors.append(f"{label}: selected timestamp differs")
    expected_inventory = {
        name: activity * case["mass_g"]
        for name, activity in case["activity_Bq_per_g"].items()
    }
    actual_inventory = target.get("inventory_activity_bq")
    if not isinstance(actual_inventory, dict):
        errors.append(f"{label}: inventory_activity_bq is not an object")
    else:
        for name in set(expected_inventory) | set(actual_inventory):
            if not close_number(actual_inventory.get(name, 0.0), expected_inventory.get(name, 0.0)):
                errors.append(f"{label}: emitted inventory for {name} differs")
    expected_external = expected.get("_external_tritium_activity_bq", 0.0)
    if not close_number(target.get("external_tritium_activity_bq"), expected_external):
        errors.append(f"{label}: external tritium activity differs")
    evaluation = target.get("evaluation")
    if not isinstance(evaluation, dict):
        return errors + [f"{label}: evaluation is not an object"]
    independent = expected["_independent"]
    for field, value in (
        ("class", independent["evaluation_class"]),
        ("calculated_only_class", independent["calculated_only_class"]),
        ("coverage", independent["coverage"]),
        ("unknown_nuclides", independent["unknown_nuclides"]),
        ("unlisted_nuclides", independent["unlisted_nuclides"]),
    ):
        if evaluation.get(field) != value:
            errors.append(f"{label}: evaluation {field} differs")
    if not close_number(evaluation.get("unlisted_activity_bq"), independent["unlisted_activity_bq"]):
        errors.append(f"{label}: unlisted activity differs")
    errors.extend(_compare_constraint_lists(evaluation.get("constraints"), independent["constraints"], f"{label}: constraints"))
    errors.extend(_compare_constraint_lists(evaluation.get("binding_constraints"), independent["binding_constraints"], f"{label}: binding_constraints"))

    actual_rows = evaluation.get("row_fractions")
    expected_rows = independent["row_details"]
    if not isinstance(actual_rows, list) or len(actual_rows) != len(expected_rows):
        errors.append(f"{label}: row-fraction count differs")
    else:
        row_key = lambda row: (row.get("table"), row.get("column") or 0, row.get("row_id"), row.get("nuclide"))
        actual_sorted = sorted(actual_rows, key=row_key)
        expected_sorted = sorted(expected_rows, key=row_key)
        for index, (got, want) in enumerate(zip(actual_sorted, expected_sorted)):
            for field in ("table", "column", "row_id", "nuclide", "unit", "limit"):
                if got.get(field) != want[field]:
                    errors.append(f"{label}: row_fraction[{index}].{field} differs")
            for field in ("concentration", "fraction"):
                if want[field] is None:
                    if got.get(field) is not None:
                        errors.append(f"{label}: row_fraction[{index}].{field} should be null")
                elif not close_number(got.get(field), want[field]):
                    errors.append(f"{label}: row_fraction[{index}].{field} differs")
    return errors


def _run_cli(arguments: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(arguments, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"bounded CLI command exceeded 120 seconds: {' '.join(arguments)}") from error


def run_g1_invalid_inputs(work: Path) -> dict:
    """Exercise the protocol's invalid-input and mesh accounting refusals."""
    base_run = {
        "entry_point": "cli",
        "mode": "coupled",
        "steps": [{"step": 1, "t_s": 0.0, "activity_Bq_per_g": {"C14": 1.0}}],
    }
    base_spec = {
        "schema": "actinv-waste-spec-1",
        "rules": "us-nrc-10cfr61.55-v1",
        "targets": [1],
        "nuclide_properties": {"C14": {"z": 6, "half_life_s": 1.8e11, "alpha_emitting": False}},
        "components": [{
            "id": "p105-invalid-input-component",
            "mass_g": 1.0,
            "displaced_volume_cm3": 1.0,
            "waste_type": "general",
            "external_tritium": {"status": "not_applicable"},
        }],
    }
    outcomes = {}

    def reject(name: str, spec: dict, input_doc: object, *, mesh_text: str | None = None) -> None:
        spec_path = work / f"invalid-{name}.json"
        input_path = work / f"invalid-{name}.input"
        output_path = work / f"invalid-{name}.result.json"
        spec = copy.deepcopy(spec)
        spec["input"] = str(input_path)
        spec_path.write_text(json.dumps(spec, separators=(",", ":")) + "\n", encoding="utf-8")
        if mesh_text is None:
            input_path.write_text(json.dumps(input_doc, separators=(",", ":")) + "\n", encoding="utf-8")
        else:
            input_path.write_text(mesh_text, encoding="utf-8")
        process = _run_cli([str(ACTINV), "waste", str(spec_path), str(output_path)])
        passed = process.returncode != 0 and not output_path.exists()
        outcomes[name] = {
            "returncode": process.returncode,
            "output_published": output_path.exists(),
            "stderr_tail": process.stderr.strip().replace(str(work), "<WORK>")[-400:],
            "pass": passed,
        }

    # The native RunResult contract has no schema field; an explicit marker
    # must be rejected rather than guessed as a legacy shape.
    reject("run_schema", base_spec, {"schema": "actinv-run-result-1", **base_run})
    bad_geometry = copy.deepcopy(base_spec)
    bad_geometry["components"][0]["displaced_volume_cm3"] = 0.0
    reject("nonpositive_geometry", bad_geometry, base_run)
    both_geometry = copy.deepcopy(base_spec)
    both_geometry["components"][0]["density_g_cm3"] = 7.8
    reject("conflicting_geometry", both_geometry, base_run)
    native_cells = copy.deepcopy(base_spec)
    native_cells["components"][0]["cells"] = [{"id": "cell-a", "mass_g": 1.0}]
    reject("native_cells", native_cells, base_run)
    alias_activity = {
        "entry_point": "cli", "mode": "coupled",
        "steps": [{"step": 1, "t_s": 0.0, "activity_Bq_per_g": {"Nb94": 1.0, "Nb-94": 2.0}}],
    }
    aliases = copy.deepcopy(base_spec)
    aliases["components"][0]["waste_type"] = "activated_metal"
    aliases["nuclide_properties"] = {
        "Nb94": {"z": 41, "half_life_s": 6.4e11, "alpha_emitting": False},
        "Nb-94": {"z": 41, "half_life_s": 6.4e11, "alpha_emitting": False},
    }
    reject("duplicate_alias", aliases, alias_activity)
    missing_decl = copy.deepcopy(base_spec)
    missing_decl["components"][0]["external_tritium"] = {
        "status": "declared", "source": "synthetic negative plant", "excludes_activation": True, "activity_bq": {},
    }
    reject("missing_declared_h3_step", missing_decl, base_run)
    false_attest = copy.deepcopy(base_spec)
    false_attest["components"][0]["external_tritium"] = {
        "status": "declared", "source": "synthetic negative plant", "excludes_activation": False, "activity_bq": {"1": 0.0},
    }
    reject("false_h3_attestation", false_attest, base_run)
    missing_target = copy.deepcopy(base_spec)
    missing_target["targets"] = [2]
    reject("missing_target_step", missing_target, base_run)

    cell_result = {
        "entry_point": "cli", "mode": "coupled",
        "steps": [{"step": 1, "t_s": 0.0, "activity_Bq_per_g": {"C14": 1.0}}],
    }
    mesh_records = [
        {"record": "header", "schema": "actinv-mesh-result-1", "cell_count": 1},
        {"record": "cell", "id": "cell-a", "result": cell_result},
    ]
    bad_cell_mass = copy.deepcopy(base_spec)
    bad_cell_mass["components"][0]["cells"] = [{"id": "cell-a", "mass_g": 0.5}]
    records = mesh_records + [{"record": "footer", "cell_count": 1}]
    reject("unequal_cell_masses", bad_cell_mass, None, mesh_text="".join(json.dumps(row) + "\n" for row in records))
    bad_footer = copy.deepcopy(base_spec)
    bad_footer["components"][0]["cells"] = [{"id": "cell-a", "mass_g": 1.0}]
    records = mesh_records + [{"record": "footer", "cell_count": 2}]
    reject("mesh_footer_count", bad_footer, None, mesh_text="".join(json.dumps(row) + "\n" for row in records))
    bad_header = copy.deepcopy(base_spec)
    bad_header["components"][0]["cells"] = [{"id": "cell-a", "mass_g": 1.0}]
    records = [dict(mesh_records[0], schema="actinv-mesh-result-unsupported"), mesh_records[1], {"record": "footer", "cell_count": 1}]
    reject("mesh_header_schema", bad_header, None, mesh_text="".join(json.dumps(row) + "\n" for row in records))

    valid_mesh_text = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in mesh_records + [{"record": "footer", "cell_count": 1}])
    valid_mesh_spec = copy.deepcopy(base_spec)
    valid_mesh_spec["components"][0]["cells"] = [{"id": "cell-a", "mass_g": 1.0}]
    reject("absent_mesh_cell", {**valid_mesh_spec, "components": [{**valid_mesh_spec["components"][0], "cells": [{"id": "absent", "mass_g": 1.0}]}]}, None, mesh_text=valid_mesh_text)
    reject("empty_component_cells", {**valid_mesh_spec, "components": [{**valid_mesh_spec["components"][0], "cells": []}]}, None, mesh_text=valid_mesh_text)

    duplicate_cell_records = mesh_records + [mesh_records[1], {"record": "footer", "cell_count": 2}]
    reject("duplicate_mesh_cell_id", valid_mesh_spec, None, mesh_text="".join(json.dumps(row) + "\n" for row in duplicate_cell_records))
    empty_cell_id_records = [mesh_records[0], {**mesh_records[1], "id": ""}, {"record": "footer", "cell_count": 1}]
    reject("empty_mesh_cell_id", valid_mesh_spec, None, mesh_text="".join(json.dumps(row) + "\n" for row in empty_cell_id_records))

    two_cells = [
        {"record": "header", "schema": "actinv-mesh-result-1", "cell_count": 2},
        {"record": "cell", "id": "cell-a", "result": copy.deepcopy(cell_result)},
        {"record": "cell", "id": "cell-b", "result": copy.deepcopy(cell_result)},
        {"record": "footer", "cell_count": 2},
    ]
    overlap_spec = copy.deepcopy(valid_mesh_spec)
    overlap_spec["components"] = [
        {**copy.deepcopy(valid_mesh_spec["components"][0]), "id": "component-a", "mass_g": 0.5, "cells": [{"id": "cell-a", "mass_g": 0.5}]},
        {**copy.deepcopy(valid_mesh_spec["components"][0]), "id": "component-b", "mass_g": 0.5, "cells": [{"id": "cell-a", "mass_g": 0.5}]},
    ]
    reject("overlapping_component_cell_membership", overlap_spec, None, mesh_text="".join(json.dumps(row) + "\n" for row in two_cells))
    duplicate_component_ids = copy.deepcopy(overlap_spec)
    duplicate_component_ids["components"][1]["id"] = "component-a"
    duplicate_component_ids["components"][1]["cells"] = [{"id": "cell-b", "mass_g": 0.5}]
    reject("duplicate_component_ids", duplicate_component_ids, None, mesh_text="".join(json.dumps(row) + "\n" for row in two_cells))
    unequal_times = copy.deepcopy(two_cells)
    unequal_times[2]["result"]["steps"][0]["t_s"] = 1.0
    both_cells_spec = copy.deepcopy(valid_mesh_spec)
    both_cells_spec["components"][0]["cells"] = [{"id": "cell-a", "mass_g": 0.5}, {"id": "cell-b", "mass_g": 0.5}]
    reject("unequal_cell_timestamps", both_cells_spec, None, mesh_text="".join(json.dumps(row) + "\n" for row in unequal_times))

    empty_targets = copy.deepcopy(base_spec)
    empty_targets["targets"] = []
    reject("empty_targets", empty_targets, base_run)
    duplicate_targets = copy.deepcopy(base_spec)
    duplicate_targets["targets"] = [1, 1]
    reject("duplicate_targets", duplicate_targets, base_run)
    empty_components = copy.deepcopy(base_spec)
    empty_components["components"] = []
    reject("empty_components", empty_components, base_run)
    return {"vectors": outcomes, "pass": all(item["pass"] for item in outcomes.values())}


def run_unequal_mass_mesh_aggregation(work: Path, case: dict) -> dict:
    """Check mass-weighted mesh aggregation against a frozen full-component vector."""
    input_path = work / "unequal-mass-mesh.jsonl"
    spec_path = work / "unequal-mass-mesh-spec.json"
    output_path = work / "unequal-mass-mesh-result.json"
    first_activity = {name: value * 2.0 for name, value in case["activity_Bq_per_g"].items()}
    second_activity = {name: value * (2.0 / 3.0) for name, value in case["activity_Bq_per_g"].items()}
    cell_result = lambda activity: {
        "entry_point": "cli", "mode": "coupled",
        "steps": [{"step": 1, "t_s": 0.0, "activity_Bq_per_g": activity}],
    }
    records = [
        {"record": "header", "schema": "actinv-mesh-result-1", "cell_count": 2},
        {"record": "cell", "id": "light-cell", "result": cell_result(first_activity)},
        {"record": "cell", "id": "heavy-cell", "result": cell_result(second_activity)},
        {"record": "footer", "cell_count": 2},
    ]
    input_path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
    spec = {
        "schema": "actinv-waste-spec-1", "rules": "us-nrc-10cfr61.55-v1",
        "input": str(input_path), "targets": [1],
        "nuclide_properties": case["nuclide_properties"],
        "components": [{
            "id": "p105-unequal-cell-mass-component", "mass_g": 1.0,
            "displaced_volume_cm3": 1.0, "waste_type": case["waste_type"],
            "cells": [{"id": "light-cell", "mass_g": 0.25}, {"id": "heavy-cell", "mass_g": 0.75}],
            "external_tritium": {"status": "not_applicable"},
        }],
    }
    spec_path.write_text(json.dumps(spec, separators=(",", ":")) + "\n", encoding="utf-8")
    process = _run_cli([str(ACTINV), "waste", str(spec_path), str(output_path)])
    if process.returncode != 0:
        return {"pass": False, "error": process.stderr.strip()[-1200:]}
    result = json.loads(output_path.read_text(encoding="utf-8"))
    components = result.get("components")
    targets = components[0].get("targets", []) if isinstance(components, list) and components else []
    expected = dict(case["expected"])
    expected["_independent"] = independent_classification(case, json.loads(PACK.read_text(encoding="utf-8"))["rows"])
    errors = []
    if result.get("schema") != "actinv-waste-result-1":
        errors.append("mesh result schema differs")
    if len(components or []) != 1:
        errors.append("mesh component count differs")
    if len(targets) != 1:
        errors.append("mesh target count differs")
    else:
        errors.extend(verify_vector_target(case, expected, targets[0]))
        target = targets[0]
        inventory = target.get("inventory_activity_bq", {})
        for name, activity_per_g in case["activity_Bq_per_g"].items():
            if not close_number(inventory.get(name), activity_per_g):
                errors.append(f"mass-weighted inventory for {name} differs")
    return {
        "source_vector": case["id"],
        "transformation": "0.25 g at 2x plus 0.75 g at 2/3x preserves the frozen 1 g component activity",
        "cell_masses_g": [0.25, 0.75],
        "pass": not errors,
        "errors": errors,
    }


def run_declared_h3_invariant(work: Path, case: dict) -> dict:
    """Move a frozen activated H-3 inventory into an explicitly declared source."""
    input_path = work / "declared-h3-run.json"
    spec_path = work / "declared-h3-spec.json"
    output_path = work / "declared-h3-result.json"
    h3_activity_bq = case["activity_Bq_per_g"].get("H3", 0.0) * case["mass_g"]
    if h3_activity_bq <= 0.0:
        return {"pass": False, "error": "selected frozen H-3 vector has no positive activity"}
    activation_only = {
        "entry_point": "cli", "mode": "coupled",
        "steps": [{"step": 1, "t_s": 0.0, "activity_Bq_per_g": {"H3": 0.0}}],
    }
    input_path.write_text(json.dumps(activation_only, separators=(",", ":")) + "\n", encoding="utf-8")
    spec = {
        "schema": "actinv-waste-spec-1", "rules": "us-nrc-10cfr61.55-v1",
        "input": str(input_path), "targets": [1],
        "nuclide_properties": case["nuclide_properties"],
        "components": [{
            "id": "p105-declared-h3-component", "mass_g": case["mass_g"],
            "displaced_volume_cm3": case["displaced_volume_cm3"], "waste_type": case["waste_type"],
            "external_tritium": {
                "status": "declared", "source": "frozen synthetic H-3 relocation invariant",
                "excludes_activation": True, "activity_bq": {"1": h3_activity_bq},
            },
        }],
    }
    spec_path.write_text(json.dumps(spec, separators=(",", ":")) + "\n", encoding="utf-8")
    process = _run_cli([str(ACTINV), "waste", str(spec_path), str(output_path)])
    if process.returncode != 0:
        return {"pass": False, "error": process.stderr.strip()[-1200:]}
    result = json.loads(output_path.read_text(encoding="utf-8"))
    components = result.get("components")
    targets = components[0].get("targets", []) if isinstance(components, list) and components else []
    expected_final = dict(case["expected"])
    expected_final["_independent"] = independent_classification(
        case, json.loads(PACK.read_text(encoding="utf-8"))["rows"]
    )
    expected_final["_external_tritium_activity_bq"] = h3_activity_bq
    activation_zero = dict(case)
    activation_zero["activity_Bq_per_g"] = {"H3": 0.0}
    zero_expected = independent_classification(activation_zero, json.loads(PACK.read_text(encoding="utf-8"))["rows"])
    errors = []
    if len(targets) != 1:
        errors.append("declared H-3 target count differs")
    else:
        target = targets[0]
        # Reuse the complete final-inventory comparison, then separately assert
        # the wrapper's calculated-only result is the zero-activation A class.
        checked_target = copy.deepcopy(target)
        checked_target["calculated_only_class"] = expected_final["calculated_only_class"]
        errors.extend(verify_vector_target(case, expected_final, checked_target))
        if target.get("calculated_only_class") != zero_expected["calculated_only_class"]:
            errors.append("calculated-only class did not reflect zero activation (expected A)")
        if target.get("class") == zero_expected["calculated_only_class"]:
            errors.append("declared external H-3 did not change the zero-activation class")
        if not close_number(target.get("external_tritium_activity_bq"), h3_activity_bq):
            errors.append("declared external H-3 activity differs from relocated source activity")
        inventory = target.get("inventory_activity_bq", {})
        if not close_number(inventory.get("H3"), h3_activity_bq):
            errors.append("merged H-3 inventory differs from frozen activation inventory")
    return {
        "source_vector": case["id"],
        "external_h3_activity_bq": h3_activity_bq,
        "zero_activation_calculated_only_class": zero_expected["calculated_only_class"],
        "pass": not errors,
        "errors": errors,
    }


def run_g1_classes(no_write: bool = False) -> int:
    document, vectors, identities = verify_frozen_vectors()
    if not ACTINV.is_file():
        raise RuntimeError(f"ACTINV_BIN does not point to a built application: {ACTINV}")
    work_root = ROOT / "target/p105-g1-controls"
    work_root.mkdir(parents=True, exist_ok=True)

    groups: dict[tuple, list[dict]] = {}
    for case in vectors:
        group_key = (
            case["waste_type"],
            case["external_tritium"]["status"],
            tuple(sorted(set(case["activity_Bq_per_g"]) - set(case["nuclide_properties"]))),
        )
        groups.setdefault(group_key, []).append(case)

    compact_vectors = {}
    actual_by_case = {}
    overall_pass = True
    rules_sha256 = sha256(PACK)
    repeated_classification_checks = {}
    component_checks_by_group = {}
    with tempfile.TemporaryDirectory(prefix="p105-g1-", dir=work_root) as temporary:
        work = Path(temporary)
        for group_number, ((waste_type, external_status, _missing), cases) in enumerate(groups.items(), 1):
            property_names = set().union(*(set(case["nuclide_properties"]) for case in cases))
            properties = {
                name: next(case["nuclide_properties"][name] for case in cases if name in case["nuclide_properties"])
                for name in sorted(property_names)
            }
            input_path = work / f"run-{group_number}.json"
            spec_path = work / f"waste-{group_number}.json"
            output_path = work / f"result-{group_number}.json"
            step_cases = {step: case for step, case in enumerate(cases, 1)}
            run_result = {
                "entry_point": "cli",
                "mode": "coupled",
                "steps": [
                    {"step": step, "t_s": 0.0, "activity_Bq_per_g": case["activity_Bq_per_g"]}
                    for step, case in step_cases.items()
                ],
            }
            input_path.write_text(json.dumps(run_result, separators=(",", ":")) + "\n", encoding="utf-8")
            spec = {
                "schema": "actinv-waste-spec-1",
                "rules": "us-nrc-10cfr61.55-v1",
                "input": str(input_path),
                "targets": list(step_cases),
                "nuclide_properties": properties,
                "components": [{
                    "id": f"p105-g1-component-{group_number}",
                    "mass_g": 1.0,
                    "displaced_volume_cm3": 1.0,
                    "waste_type": waste_type,
                    "external_tritium": {"status": external_status},
                }],
            }
            spec_path.write_text(json.dumps(spec, separators=(",", ":")) + "\n", encoding="utf-8")
            process = _run_cli([str(ACTINV), "waste", str(spec_path), str(output_path)])
            if process.returncode != 0:
                raise RuntimeError(
                    f"P105 G1 CLI failed ({process.returncode}): {process.stderr[-2000:]}"
                )
            actual_result = json.loads(output_path.read_text(encoding="utf-8"))
            if group_number == 1:
                original_output_bytes = output_path.read_bytes()
                repeat_path = work / f"result-{group_number}-repeat.json"
                repeated = _run_cli([str(ACTINV), "waste", str(spec_path), str(repeat_path)])
                repeated_classification_checks[str(group_number)] = (
                    repeated.returncode == 0 and repeat_path.is_file()
                    and repeat_path.read_bytes() == original_output_bytes
                )
                if not repeated_classification_checks[str(group_number)]:
                    overall_pass = False
            if actual_result.get("schema") != "actinv-waste-result-1":
                raise RuntimeError("P105 G1 CLI returned an unexpected result schema")
            if actual_result.get("rules") != "us-nrc-10cfr61.55-v1" or actual_result.get("rule_pack_sha256") != rules_sha256:
                raise RuntimeError("P105 G1 CLI returned an unexpected selected rule identity")
            if actual_result.get("targets") != list(step_cases):
                raise RuntimeError("P105 G1 CLI selected target steps differ")
            components = actual_result.get("components")
            if not isinstance(components, list) or len(components) != 1:
                raise RuntimeError("P105 G1 native input did not return exactly one component")
            component = components[0]
            expected_component_status = "conditional" if any(
                case["expected"]["class"] == "unknown" for case in cases
            ) else "nominal"
            component_checks = {
                "mass_g": close_number(component.get("mass_g"), cases[0]["mass_g"]),
                "displaced_volume_cm3": close_number(component.get("displaced_volume_cm3"), cases[0]["displaced_volume_cm3"]),
                "waste_type": component.get("waste_type") == waste_type,
                "status": component.get("status") == expected_component_status,
            }
            component_checks_by_group[str(group_number)] = component_checks
            if not all(component_checks.values()):
                overall_pass = False
            target_by_step = {target.get("step"): target for target in component.get("targets", [])}
            for step, case in step_cases.items():
                independent = independent_classification(case, json.loads(PACK.read_text(encoding="utf-8"))["rows"])
                frozen_expected = case["expected"]
                for field in ("class", "calculated_only_class", "coverage", "unknown_nuclides"):
                    if independent[field] != frozen_expected[field]:
                        raise RuntimeError(f"frozen vector {case['id']} disagrees with independent {field}")
                if independent["unknown_reason"] != frozen_expected.get("external_unknown_reason", frozen_expected.get("unknown_nuclides") or None):
                    raise RuntimeError(f"frozen vector {case['id']} disagrees with independent unknown reason")
                expected = dict(frozen_expected)
                expected["_independent"] = independent
                actual_target = target_by_step.get(step)
                errors = verify_vector_target(case, expected, actual_target)
                overall_pass &= not errors
                actual_by_case[case["id"]] = actual_target
                compact_vectors[case["id"]] = {
                    "class": target_by_step.get(step, {}).get("class"),
                    "calculated_only_class": target_by_step.get(step, {}).get("calculated_only_class"),
                    "coverage": target_by_step.get(step, {}).get("coverage"),
                    "expected_class": frozen_expected["class"],
                    "expected_row_fractions": frozen_expected["row_fractions"],
                    "expected_constraints": frozen_expected["constraints"],
                    "pass": not errors,
                    "errors": errors,
                }
        # Use a robust, non-boundary frozen case to test aggregation invariance.
        mesh_case = next(case for case in vectors if case["id"] == "activated-metal-c14-substitution")
        mesh_aggregation = run_unequal_mass_mesh_aggregation(work, mesh_case)
        overall_pass &= mesh_aggregation["pass"]
        h3_case = next(case for case in vectors if case["id"] == "t2-H-3-col1-above")
        declared_h3 = run_declared_h3_invariant(work, h3_case)
        overall_pass &= declared_h3["pass"]
        invalid_inputs = run_g1_invalid_inputs(work)
        overall_pass &= invalid_inputs["pass"]

    # Prove the result checker detects planted class, limit, and sum corruption.
    first_id = next(iter(compact_vectors))
    mutation_checks = {"class": False, "constraint_sum": False, "row_limit": False}
    witness = next(case for case in vectors if case["id"] == first_id)
    witness_expected = dict(witness["expected"])
    witness_expected["_independent"] = independent_classification(witness, json.loads(PACK.read_text(encoding="utf-8"))["rows"])
    witness_target = actual_by_case.get(first_id)
    for mutation, path in (("class", "class"), ("constraint_sum", "constraint"), ("row_limit", "limit")):
        planted = copy.deepcopy(witness_target)
        if isinstance(planted, dict):
            if path == "class":
                planted["class"] = "planted-invalid-class"
            elif path == "constraint" and planted.get("evaluation", {}).get("constraints"):
                planted["evaluation"]["constraints"][0]["source_sum_fraction"] += 0.25
            elif path == "limit" and planted.get("evaluation", {}).get("row_fractions"):
                row = planted["evaluation"]["row_fractions"][0]
                row["limit"] = (row.get("limit") or 0.0) + 1.0
        mutation_checks[mutation] = bool(verify_vector_target(witness, witness_expected, planted))
    overall_pass &= all(mutation_checks.values())
    result = {
        "schema": "actinv-p105-g1-classes-1",
        "successor_protocol_sha256": SUCCESSOR_PROTOCOL_SHA256,
        "fixture": identities,
        "vectors": compact_vectors,
        "invalid_inputs": invalid_inputs["vectors"],
        "unequal_mass_mesh_aggregation": mesh_aggregation,
        "declared_h3_relocation": declared_h3,
        "repeated_classification_byte_identity": repeated_classification_checks,
        "component_checks": component_checks_by_group,
        "mutations_rejected": mutation_checks,
        "pass": overall_pass,
    }
    if no_write:
        try:
            persisted = json.loads(CLASS_RESULT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result["pass"] = False
        else:
            same_result = persisted == result
            result["persisted_result_matches"] = same_result
            result["pass"] &= same_result
    else:
        CLASS_RESULT.parent.mkdir(parents=True, exist_ok=True)
        CLASS_RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


def main(no_write: bool = False) -> int:
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
        "version": pack.get("version"),
        "ecfr_source_url": pack.get("source_url"),
        "source_as_of": pack.get("source_as_of"),
        "source_url": pack.get("annual_source_url"),
        "source_sha256_matches_xml": pack.get("annual_source_sha256") == identities["source_xml_sha256"],
        "ci_bq": pack.get("ci_bq"),
        "year_s": pack.get("year_s"),
        "pass": pack.get("schema") == "actinv-waste-rules-1"
        and pack.get("id") == "us-nrc-10cfr61.55-v1"
        and pack.get("version") == 1
        and pack.get("source_url") == "https://www.ecfr.gov/current/title-10/chapter-I/part-61/subpart-D/section-61.55"
        and pack.get("source_as_of") == "2026-10-01"
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
    if no_write:
        try:
            persisted = json.loads(RESULT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            result["checks"]["persisted_seal"] = {"pass": False, "error": str(error)}
            result["sealed"] = False
            result["verdict"] = "P103-G0-FAIL"
            passed = False
        else:
            persisted_pass = persisted.get("sealed") is True and persisted.get("verdict") == "P103-G0-SEALED"
            same_inputs = persisted.get("input_sha256") == identities
            # Amendment A: the initial G0 seal predates three extra descriptive
            # metadata fields. All original evidence must remain byte-for-value
            # identical, and the expanded source checks above must still pass.
            comparable = copy.deepcopy(result)
            for field in ("version", "ecfr_source_url", "source_as_of"):
                if field not in persisted.get("checks", {}).get("pack_identity_and_units", {}):
                    comparable["checks"]["pack_identity_and_units"].pop(field, None)
            same_result = persisted == comparable
            result["checks"]["persisted_seal"] = {
                "sealed": persisted.get("sealed"),
                "verdict": persisted.get("verdict"),
                "same_inputs": same_inputs,
                "same_result": same_result,
                "pass": persisted_pass and same_inputs and same_result,
            }
            passed = passed and persisted_pass and same_inputs and same_result
            result["sealed"] = passed
            result["verdict"] = "P103-G0-SEALED" if passed else "P103-G0-FAIL"
    else:
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--g0-only", action="store_true", help="check only the source pack and seal")
    parser.add_argument("--g1-only", action="store_true", help="run source-derived classification vectors")
    parser.add_argument("--g2-only", action="store_true", help="run budget algebra and refusal controls")
    parser.add_argument("--no-write", action="store_true", help="compare generated evidence with persisted seals")
    arguments = parser.parse_args()
    selected = sum((arguments.g0_only, arguments.g1_only, arguments.g2_only))
    if selected > 1:
        parser.error("--g0-only, --g1-only, and --g2-only are mutually exclusive")
    if arguments.g0_only:
        # The original P103 source seal is inherited read-only by P105.
        if main(no_write=True) != 0:
            raise SystemExit(1)
        raise SystemExit(verify_successor_g0(no_write=arguments.no_write))
    if arguments.g1_only:
        if main(no_write=True) != 0 or verify_successor_g0(no_write=True) != 0:
            raise SystemExit(1)
        raise SystemExit(run_g1_classes(no_write=arguments.no_write))
    if arguments.g2_only:
        if main(no_write=True) != 0 or verify_successor_g0(no_write=True) != 0 or run_g1_classes(no_write=True) != 0:
            raise SystemExit(1)
        from p105_budget_control import run_g2_budget
        budget_result = run_g2_budget(no_write=arguments.no_write)
        print(json.dumps(budget_result, indent=2, sort_keys=True))
        raise SystemExit(0 if budget_result.get("pass", budget_result.get("passed", False)) else 1)
    if (main(no_write=True) != 0 or verify_successor_g0(no_write=True) != 0
            or run_g1_classes(no_write=arguments.no_write) != 0):
        raise SystemExit(1)
    from p105_budget_control import run_g2_budget
    budget_result = run_g2_budget(no_write=arguments.no_write)
    print(json.dumps(budget_result, indent=2, sort_keys=True))
    raise SystemExit(0 if budget_result.get("pass", budget_result.get("passed", False)) else 1)
