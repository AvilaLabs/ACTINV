#!/usr/bin/env python3
"""Independent compact checker for the committed P18-G0 family seal."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEAL_PATH = ROOT / "results/p18_family_seal.json"
RESULT_PATH = ROOT / "results/g0_p18_seal.json"
OUTPUT_PATH = ROOT / "results/g0_p18_check.json"
P17_PATH = ROOT / "results/g5_p17_heldout.json"
PROTOCOL_PATH = ROOT / "protocols/ACTINV-P18_PROTOCOL.md"
AMENDMENT_PATH = ROOT / "protocols/ACTINV-P18_AMENDMENT_1.md"
CONTROL_PATH = ROOT / "controls/g0_p18_seal.py"
CHECKER_PATH = ROOT / "controls/check_g0_p18.py"
HASH_LOG = ROOT / "protocols/protocol_hash.txt"

PROTOCOL_SHA256 = "002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09"
AMENDMENT_SHA256 = "8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0"
SUPPLEMENT_SHA256 = "945e66f8904bb972662f5178e94e22a08ecb8006eefe1c2d9fbda66fe599763d"
PARTITION_SEED = "ACTINV-P18-HOLDOUT-v1"
EXPECTED_FAMILIES = 962
EXPECTED_ROWS = 12_313
SUPPORTED = {"neutron", "proton", "deuteron", "alpha"}
RATIO_FORMS = {"G+M", "G+T", "M+T", "M/G", "G/M", "G/T", "M/T"}
DISCLOSED = {
    "86Kr(g,n)85Kr",
    "181Ta(g,n)180Ta",
    "35Cl(n,2n)34Cl",
    "39K(n,2n)38K",
    "45Sc(n,2n)44Sc",
}
PAPER_CASES = {"93Nb(n,a)90Y", "197Au(d,2n)197Hg"}
FAMILY_REQUIRED_KEYS = {
    "family_id",
    "source_line",
    "reaction",
    "projectile",
    "projectile_identity",
    "target",
    "product",
    "half_life_record_count",
    "rows",
    "preeligible",
    "preeligibility_reason",
    "forced_diagnostic_reason",
    "partition",
    "row_count",
    "row_ids_sha256",
}
ROW_KEYS = {
    "row_id",
    "source_line",
    "incident_energy_MeV",
    "measurement_type",
    "exfor_entry",
    "source_flags",
    "eligibility_after_unseal",
}
FLAG_NAMES = {"digitized", "repeated_energy", "uncertainty_crosses_ratio_bounds"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def p17_pairs() -> set[tuple[int, int]]:
    payload = json.loads(P17_PATH.read_text(encoding="utf-8"))
    pairs: set[tuple[int, int]] = set()
    for row in payload.get("rows", []):
        mapping = row.get("heldout_evidence", {}).get("frozen_amendment_1_mapping")
        if not isinstance(mapping, dict):
            continue
        target = mapping.get("target_za")
        product = mapping.get("decay_product_za") or mapping.get("product_za")
        if isinstance(target, int) and isinstance(product, int):
            pairs.add((target, product))
    return pairs


def expected_forced_reason(family: dict[str, Any], pairs: set[tuple[int, int]]) -> str | None:
    if family["reaction"] in DISCLOSED:
        return "amendment_1_disclosed_lines_1_140"
    if family["reaction"] in PAPER_CASES:
        return "paper_case_study"
    target_za = family["target"]["Z"] * 1000 + family["target"]["A"]
    product_za = family["product"]["Z"] * 1000 + family["product"]["A"]
    if family["projectile"] == "neutron" and (target_za, product_za) in pairs:
        return "p17_exposed_target_product_pair"
    return None


def validate_seal(seal: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if seal.get("schema") != "actinv-p18-family-seal-1":
        errors.append("schema")
    if seal.get("protocol_sha256") != PROTOCOL_SHA256:
        errors.append("protocol")
    if seal.get("amendment_sha256") != AMENDMENT_SHA256:
        errors.append("amendment")
    if seal.get("control_source_sha256") != sha256(CONTROL_PATH):
        errors.append("control_source")
    if seal.get("checker_source_sha256") != sha256(CHECKER_PATH):
        errors.append("checker_source")
    source = seal.get("source", {})
    if source.get("sha256") != SUPPLEMENT_SHA256 or source.get("bytes") != 2_065_687:
        errors.append("source_identity")
    families = seal.get("families")
    if not isinstance(families, list) or len(families) != EXPECTED_FAMILIES:
        return errors + ["family_count"]
    if [family.get("family_id") for family in families] != sorted(
        family.get("family_id") for family in families
    ):
        errors.append("family_order")
    if len({family.get("family_id") for family in families}) != EXPECTED_FAMILIES:
        errors.append("family_identity_unique")

    pairs = p17_pairs()
    row_ids: set[str] = set()
    row_lines: set[int] = set()
    partition_counts: Counter[str] = Counter()
    row_partition_counts: Counter[str] = Counter()
    forced_counts: Counter[str] = Counter()
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    disclosed_seen: set[str] = set()
    total_rows = 0

    for family in families:
        keys = set(family)
        expected_keys = set(FAMILY_REQUIRED_KEYS)
        if family.get("preeligible") and family.get("forced_diagnostic_reason") is None:
            expected_keys.add("partition_sha256")
        if keys != expected_keys:
            errors.append("family_schema")
            break
        identity = family["projectile_identity"]
        target = family["target"]
        product = family["product"]
        expected_id = (
            f"{identity['Z']:03d}-{identity['A']:03d}|{target['Z']:03d}-{target['A']:03d}|"
            f"{product['Z']:03d}-{product['A']:03d}|{family['reaction']}"
        )
        if family["family_id"] != expected_id:
            errors.append("canonical_family_id")
        preeligible = family["projectile"] in SUPPORTED and target["A"] != 0
        expected_reason = None
        if family["projectile"] not in SUPPORTED:
            expected_reason = "unsupported_projectile"
        elif target["A"] == 0:
            expected_reason = "natural_target"
        if family["preeligible"] != preeligible or family["preeligibility_reason"] != expected_reason:
            errors.append("preeligibility")
        forced = expected_forced_reason(family, pairs)
        if family["forced_diagnostic_reason"] != forced:
            errors.append("forced_diagnostic_identity")
        if forced is not None:
            forced_counts[forced] += 1
        if family["reaction"] in DISCLOSED:
            disclosed_seen.add(family["reaction"])
            if family["partition"] == "heldout":
                errors.append("disclosed_heldout")
        if not preeligible:
            if family["partition"] != "ineligible":
                errors.append("ineligible_partition")
        elif forced is not None:
            if family["partition"] != "diagnostic":
                errors.append("forced_partition")
        else:
            digest = hashlib.sha256(
                f"{PARTITION_SEED}\n{family['projectile']}\n{family['family_id']}".encode()
            ).hexdigest()
            if family.get("partition_sha256") != digest:
                errors.append("partition_digest")
            pools[family["projectile"]].append(family)
        partition_counts[family["partition"]] += 1

        rows = family["rows"]
        if family["half_life_record_count"] != 1 or family["row_count"] != len(rows) or not rows:
            errors.append("family_row_count")
        expected_row_ids_hash = hashlib.sha256(
            canonical([row.get("row_id") for row in rows])
        ).hexdigest()
        if family["row_ids_sha256"] != expected_row_ids_hash:
            errors.append("family_row_hash")
        for row in rows:
            total_rows += 1
            row_partition_counts[family["partition"]] += 1
            if set(row) != ROW_KEYS:
                errors.append("row_schema")
                continue
            if row["measurement_type"] not in RATIO_FORMS:
                errors.append("measurement_type")
            if not set(row["source_flags"]).issubset(FLAG_NAMES):
                errors.append("source_flags")
            if row["eligibility_after_unseal"] != "pending_frozen_state_and_value_predicates":
                errors.append("premature_eligibility")
            try:
                energy = float(row["incident_energy_MeV"])
            except (TypeError, ValueError):
                energy = -1.0
            if not (0.0 <= energy < float("inf")):
                errors.append("incident_energy")
            key = (
                f"{SUPPLEMENT_SHA256}\n{family['family_id']}\n{row['source_line']}\n"
                f"{row['incident_energy_MeV']}\n{row['measurement_type']}\n{row['exfor_entry']}\n"
                f"{','.join(row['source_flags'])}"
            )
            expected_row_id = (
                f"p18-row-{row['source_line']:05d}-{hashlib.sha256(key.encode()).hexdigest()[:16]}"
            )
            if row["row_id"] != expected_row_id:
                errors.append("row_identity")
            if row["row_id"] in row_ids or row["source_line"] in row_lines:
                errors.append("row_unique")
            row_ids.add(row["row_id"])
            row_lines.add(row["source_line"])

    if total_rows != EXPECTED_ROWS:
        errors.append("total_rows")
    if disclosed_seen != DISCLOSED:
        errors.append("disclosed_inventory")
    for projectile in sorted(SUPPORTED):
        pool = sorted(pools[projectile], key=lambda family: family["partition_sha256"])
        heldout_count = len(pool) // 4
        if len(pool) >= 4:
            heldout_count = max(1, heldout_count)
        expected_heldout = {family["family_id"] for family in pool[:heldout_count]}
        actual_heldout = {
            family["family_id"] for family in pool if family["partition"] == "heldout"
        }
        if actual_heldout != expected_heldout:
            errors.append(f"partition_selection_{projectile}")

    partition = seal.get("partition", {})
    if partition.get("seed") != PARTITION_SEED:
        errors.append("partition_seed")
    if partition.get("family_counts") != dict(sorted(partition_counts.items())):
        errors.append("reported_family_counts")
    if partition.get("row_counts") != dict(sorted(row_partition_counts.items())):
        errors.append("reported_row_counts")
    if partition.get("forced_diagnostic_counts") != dict(sorted(forced_counts.items())):
        errors.append("reported_forced_counts")
    if forced_counts != Counter(
        {
            "amendment_1_disclosed_lines_1_140": 5,
            "p17_exposed_target_product_pair": 9,
            "paper_case_study": 2,
        }
    ):
        errors.append("forced_counts")
    structure = seal.get("structure", {})
    if structure.get("post_delimiter_marker_counts") != {
        "D": EXPECTED_ROWS,
        "H": EXPECTED_FAMILIES,
        "R": EXPECTED_FAMILIES,
    }:
        errors.append("marker_counts")
    if (
        structure.get("preamble_column20_R_records") != 1
        or structure.get("naive_global_column20_R_count") != 963
        or structure.get("delimiters") != 963
    ):
        errors.append("preamble_reconciliation")
    quarantine = seal.get("disclosure_quarantine", {})
    if quarantine.get("physical_lines_inclusive") != [1, 140] or set(
        quarantine.get("reactions", [])
    ) != DISCLOSED:
        errors.append("quarantine_record")
    return sorted(set(errors))


def mutation_plants(seal: dict[str, Any]) -> dict[str, bool]:
    heldout = next(
        index for index, family in enumerate(seal["families"]) if family["partition"] == "heldout"
    )
    disclosed = next(
        index for index, family in enumerate(seal["families"]) if family["reaction"] in DISCLOSED
    )

    partition_plant = copy.deepcopy(seal)
    partition_plant["families"][heldout]["partition"] = "diagnostic"
    dependent_plant = copy.deepcopy(seal)
    dependent_plant["families"][0]["rows"][0]["measured_ratio"] = 0.5
    quarantine_plant = copy.deepcopy(seal)
    quarantine_plant["families"][disclosed]["partition"] = "heldout"
    row_plant = copy.deepcopy(seal)
    row_plant["families"][0]["rows"][0]["row_id"] = "p18-row-planted"
    return {
        "partition_change_rejected": bool(validate_seal(partition_plant)),
        "dependent_field_rejected": "row_schema" in validate_seal(dependent_plant),
        "quarantine_change_rejected": "disclosed_heldout" in validate_seal(quarantine_plant),
        "row_identity_change_rejected": "row_identity" in validate_seal(row_plant),
    }


def derive() -> dict[str, Any]:
    seal = json.loads(SEAL_PATH.read_text(encoding="utf-8"))
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    errors = validate_seal(seal)
    plants = mutation_plants(seal)
    hash_lines = HASH_LOG.read_text(encoding="utf-8").splitlines()
    source = result.get("source", {})
    identity_groups = [
        source.get("raw_archives", {}),
        source.get("staging_manifests", {}),
        source.get("released_libraries", {}),
        source.get("decay_payloads", {}),
    ]
    result_checks = {
        "schema": result.get("schema") == "actinv-p18-g0-seal-control-1",
        "gate_pass": result.get("pass") is True and all(result.get("checks", {}).values()),
        "seal_hash": result.get("seal_sha256") == hashlib.sha256(canonical(seal)).hexdigest(),
        "seal_counts": result.get("seal_families") == EXPECTED_FAMILIES
        and result.get("seal_rows") == EXPECTED_ROWS,
        "protocol_files": sha256(PROTOCOL_PATH) == PROTOCOL_SHA256
        and sha256(AMENDMENT_PATH) == AMENDMENT_SHA256,
        "control_files": result.get("control_source_sha256") == sha256(CONTROL_PATH)
        and result.get("checker_source_sha256") == sha256(CHECKER_PATH),
        "protocol_log": (
            f"{PROTOCOL_SHA256}  protocols/ACTINV-P18_PROTOCOL.md" in hash_lines
            and f"{AMENDMENT_SHA256}  protocols/ACTINV-P18_AMENDMENT_1.md" in hash_lines
        ),
        "provenance_identities": all(
            item.get("pass") is True and item.get("sha256") == item.get("expected_sha256")
            for group in identity_groups
            for item in group.values()
        ),
        "workflow": source.get("p17_closure_workflow", {}).get("databaseId") == 33_232_228_355
        and source.get("p17_closure_workflow", {}).get("conclusion") == "success",
        "production_unchanged": source.get("production_paths_changed_since_opening") == [],
    }
    checks = {
        "independent_seal": not errors,
        "result": all(result_checks.values()),
        "mutation_plants": all(plants.values()),
    }
    return {
        "schema": "actinv-p18-g0-independent-check-1",
        "gate": "P18-G0",
        "seal_sha256": hashlib.sha256(canonical(seal)).hexdigest(),
        "errors": errors,
        "result_checks": result_checks,
        "mutation_plants": plants,
        "checks": checks,
        "pass": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()
    try:
        output = derive()
    except (KeyError, OSError, StopIteration, TypeError, ValueError) as error:
        output = {
            "schema": "actinv-p18-g0-independent-check-1",
            "pass": False,
            "error": str(error),
        }
    if not arguments.no_write:
        OUTPUT_PATH.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
