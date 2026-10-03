#!/usr/bin/env python3
"""P107 seal-replay successor and inherited conservative-bounds controls."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import tempfile
from pathlib import Path

from p106_bounds_control import PACK, P103_VECTORS, derive_case, generate_cases

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P107_PROTOCOL.md"
PROTOCOL_SHA256 = "8fe3c19a4c662092c1fb3ed0021857c8bb9337e265f76cd7903ea4f537ffe42b"
AMENDMENT = ROOT / "protocols/ACTINV-P107_AMENDMENT_A.md"
AMENDMENT_SHA256 = "cfb4e6e723489d0041192cf257a05fd7a3cda16dbe7208e31a85a350ea2cd09e"
INHERITED_PROTOCOL = ROOT / "protocols/ACTINV-P106_PROTOCOL.md"
INHERITED_PROTOCOL_SHA256 = "163a7a265363583c42b8d27a28ec11664b87d393a0b2565fcc76ec3904d606ee"
INHERITED_AMENDMENT = ROOT / "protocols/ACTINV-P106_AMENDMENT_A.md"
INHERITED_AMENDMENT_SHA256 = "a9b8568fb5e7fde35b8325b525646fbc441e6989c4292fa6ef8735ce84c17f97"
PACK_SHA256 = "890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7"
P103_VECTORS_SHA256 = "bfefb655b2df52da7ccb7a93cfd7c22bdc18762917e900e828ebd97d58b2bb42"
P106_CASES = ROOT / "controls/fixtures/p106/bounds_cases.json"
P106_CASES_SHA256 = "6041be8d54015e604a6aec0578d05fcc6f1cf4aed82322cb03459d108d293ff4"
P106_G0 = ROOT / "results/g0_p106_bounds.json"
P106_FAILURE = ROOT / "results/g0_p106_replay_failure.json"
P106_VERDICT = ROOT / "results/p106_verdict.json"
P105_VERDICT = ROOT / "results/p105_verdict.json"
SEAL = ROOT / "results/g0_p107_bounds.json"
G1_RESULT = ROOT / "results/g1_p107_bounds.json"
G2_RESULT = ROOT / "results/g2_p107_bounds.json"
ACTINV = Path(__import__("os").environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))


EXPECTED_20 = {
    "empty_complete": [("A", "A", ["A"], True, "A")],
    "empty_incomplete": [("unknown", "unknown", ["unknown"], False, None)],
    "required_h3": [("unknown", "unknown", ["unknown"], False, None)],
    "single_t1_boundary_box": [("A", "C", ["A", "B", "C"], False, None)],
    "mixture_t1_strict_box": [("C", "above_class_c", ["C", "above_class_c"], False, None)],
    "zero_crossing_contributor": [("A", "C", ["A", "B", "C"], False, None)],
    "sr90_cs137_example": [("B", "B", ["B"], True, "B")],
    "t1_t2_separate": [("C", "C", ["C"], True, "C")],
    "t2_three_columns": [("A", "A", ["A"], True, "A"), ("B", "B", ["B"], True, "B"),
                         ("C", "C", ["C"], True, "C")],
    "metal_nb94": [("C", "C", ["C"], True, "C")],
    "alpha_tru": [("C", "C", ["C"], True, "C")],
    "cm242_cross_table": [("C", "C", ["C"], True, "C")],
    "dedicated_co60": [("A", "A", ["A"], True, "A")],
    "missing_active_properties": [("unknown", "unknown", ["unknown"], False, None)],
    "inactive_and_malformed_properties": [("A", "A", ["A"], True, "A")],
    "long_lived_unlisted": [("A", "A", ["A"], True, "A")],
    "external_h3_cross": [("A", "B", ["A", "B"], False, None)],
    "external_h3_merge": [("B", "B", ["B"], True, "B")],
    "target_coverage_change": [("A", "A", ["A"], True, "A"), ("unknown", "unknown", ["unknown"], False, None)],
    "target_bounds_change": [("A", "A", ["A"], True, "A"), ("C", "C", ["C"], True, "C")],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _registered(path: Path, digest: str) -> bool:
    wanted = f"{digest}  {path.relative_to(ROOT)}"
    registry = ROOT / "protocols/protocol_hash.txt"
    return registry.is_file() and wanted in registry.read_text(encoding="utf-8").splitlines()


def _derive_population() -> tuple[dict, list[dict]]:
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    # Reuse P105's audited frozen-vector verifier solely to establish that the
    # 126 inherited point cases and their sealed expected values remain intact.
    import check_p105
    check_p105.verify_frozen_vectors()
    cases = generate_cases()
    derived = []
    for case in cases:
        expected = derive_case(case, pack["rows"])
        derived.append({"input": case, "expected": expected})
    return pack, derived


def _protocol_expected_labels(population: list[dict]) -> tuple[bool, dict]:
    actual = {}
    for record in population:
        case_id = record["input"]["id"]
        if case_id not in EXPECTED_20:
            continue
        actual[case_id] = [
            (t["lower"]["class"], t["upper"]["class"], t["class_envelope"],
             t["class_is_stable"], t["stable_class"])
            for t in record["expected"]["targets"]
        ]
    wanted = EXPECTED_20
    # These are source-derived expectations, not copied from the implementation.
    return actual == wanted and len(actual) == 20, {
        "actual": actual,
        "expected": wanted,
        "pass": actual == wanted and len(actual) == 20,
    }


def _json_compatible(value):
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _g0_base_report() -> tuple[dict, bytes]:
    pack, population = _derive_population()
    labels_ok, labels = _protocol_expected_labels(population)
    registration_ok = sha256(PROTOCOL) == PROTOCOL_SHA256 and _registered(PROTOCOL, PROTOCOL_SHA256)
    amendment_ok = sha256(AMENDMENT) == AMENDMENT_SHA256 and _registered(AMENDMENT, AMENDMENT_SHA256)
    inherited_protocol_ok = (sha256(INHERITED_PROTOCOL) == INHERITED_PROTOCOL_SHA256
                             and _registered(INHERITED_PROTOCOL, INHERITED_PROTOCOL_SHA256))
    inherited_amendment_ok = (sha256(INHERITED_AMENDMENT) == INHERITED_AMENDMENT_SHA256
                              and _registered(INHERITED_AMENDMENT, INHERITED_AMENDMENT_SHA256))
    source_ok = (sha256(PACK) == PACK_SHA256 and sha256(ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json") == PACK_SHA256
                 and PACK.read_bytes() == (ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json").read_bytes())
    vector_ok = sha256(P103_VECTORS) == P103_VECTORS_SHA256
    fixture_document = {"schema": "actinv-p106-bounds-cases-1", "source": "artificial total-Bq boxes",
                        "cases": population}
    fixture_bytes = (json.dumps(fixture_document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    fixture_matches = P106_CASES.is_file() and P106_CASES.read_bytes() == fixture_bytes
    fixture_sha_ok = sha256(P106_CASES) == P106_CASES_SHA256 if P106_CASES.is_file() else False
    try:
        inherited_g0 = json.loads(P106_G0.read_text(encoding="utf-8"))
        p106_failure = json.loads(P106_FAILURE.read_text(encoding="utf-8"))
        p106_terminal = json.loads(P106_VERDICT.read_text(encoding="utf-8"))
        p105_terminal = json.loads(P105_VERDICT.read_text(encoding="utf-8"))
        predecessors_read = True
    except (OSError, json.JSONDecodeError):
        inherited_g0, p106_failure, p106_terminal, p105_terminal = {}, {}, {}, {}
        predecessors_read = False
    p106_failed = (predecessors_read and p106_terminal.get("verdict") == "P106-FAIL"
                   and p106_terminal.get("phase") == "P106"
                   and p106_failure.get("pass") is False
                   and p106_failure.get("persisted_seal_matches") is False
                   and p106_failure.get("case_fixture_matches") is True)
    p105_passed = p105_terminal.get("verdict") == "P105-PASS"
    inherited_paths = [ROOT / "controls/check_p106.py", ROOT / "controls/p106_bounds_control.py",
                       ROOT / "controls/test_p106_bounds.py", ROOT / "controls/check_p105.py",
                       ROOT / "controls/p105_budget_control.py", ROOT / "controls/test_p105_children.py"]
    inherited_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in inherited_paths if path.is_file()}
    sealed_hashes = inherited_g0.get("control_hashes", {})
    inherited_controls_unchanged = len(inherited_hashes) == 6 and inherited_hashes == sealed_hashes
    own_paths = [ROOT / "controls/check_p107.py", ROOT / "controls/check_p107_verdict.py",
                 ROOT / "controls/test_p107_seal.py"]
    own_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in own_paths if path.is_file()}
    control_hashes = {**inherited_hashes, **own_hashes}
    expected_20_pass = labels_ok and len(population) == 146
    report = {
        "schema": "actinv-p107-bounds-g0-seal-1", "phase": "P107",
        "protocol_sha256": sha256(PROTOCOL), "protocol_registered": registration_ok,
        "amendment_sha256": sha256(AMENDMENT), "amendment_registered": amendment_ok,
        "inherited_p106_protocol_sha256": sha256(INHERITED_PROTOCOL),
        "inherited_p106_protocol_registered": inherited_protocol_ok,
        "inherited_p106_amendment_sha256": sha256(INHERITED_AMENDMENT),
        "inherited_p106_amendment_registered": inherited_amendment_ok,
        "inherited_p105_verdict": p105_terminal.get("verdict"),
        "inherited_p105_verdict_sha256": sha256(P105_VERDICT) if P105_VERDICT.is_file() else None,
        "inherited_p106_verdict": p106_terminal.get("verdict"),
        "inherited_p106_verdict_sha256": sha256(P106_VERDICT) if P106_VERDICT.is_file() else None,
        "inherited_p106_g0_sha256": sha256(P106_G0) if P106_G0.is_file() else None,
        "inherited_p106_replay_failure_sha256": sha256(P106_FAILURE) if P106_FAILURE.is_file() else None,
        "inherited_p106_terminal_failure": p106_failed,
        "inherited_p106_fixture_sha256": sha256(P106_CASES) if P106_CASES.is_file() else None,
        "inherited_p106_fixture_matches_regeneration": fixture_matches,
        "pinned_pack_sha256": sha256(PACK), "pack_mirror_identical": source_ok,
        "inherited_p103_vector_sha256": sha256(P103_VECTORS),
        "case_count": len(population), "case_fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "frozen_case_fixture_sha256_matches": fixture_sha_ok,
        "expected_labels": labels,
        "control_hashes": control_hashes,
        "inherited_control_hashes_unchanged": inherited_controls_unchanged,
        "pass": (registration_ok and amendment_ok and inherited_protocol_ok and inherited_amendment_ok
                 and p106_failed and p105_passed and source_ok and vector_ok and fixture_matches and fixture_sha_ok
                 and expected_20_pass and inherited_controls_unchanged and len(own_hashes) == len(own_paths)),
    }
    return report, fixture_bytes


def seal_roundtrip_regression(report: dict | None = None) -> bool:
    """Write/read the actual stable seal representation and reject mutation."""
    import tempfile
    if report is None:
        report, _ = _g0_base_report()
    with tempfile.TemporaryDirectory(prefix="p107-seal-regression-") as temp:
        path = Path(temp) / "seal.json"
        path.write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        persisted = json.loads(path.read_text(encoding="utf-8"))
    stable = _json_compatible(report)
    changed = copy.deepcopy(persisted)
    changed["case_count"] = int(changed["case_count"]) + 1
    return persisted == stable and changed != stable


def g0(no_write: bool = False) -> int:
    report, fixture_bytes = _g0_base_report()
    roundtrip_ok = seal_roundtrip_regression(report)
    report["seal_json_roundtrip_regression"] = roundtrip_ok
    report["pass"] = report["pass"] and roundtrip_ok
    # Normalize tuple/list shapes before either persistence or identity replay.
    stable_report = _json_compatible(report)
    if no_write:
        try:
            recorded_fixture = P106_CASES.read_bytes()
            recorded_seal = json.loads(SEAL.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            stable_report["pass"] = False
            stable_report["replay_error"] = str(error)
        else:
            seal_matches = recorded_seal == stable_report
            fixture_matches = recorded_fixture == fixture_bytes
            stable_report["case_fixture_matches"] = fixture_matches
            stable_report["persisted_seal_matches"] = seal_matches
            stable_report["pass"] = stable_report["pass"] and fixture_matches and seal_matches
    elif stable_report["pass"]:
        SEAL.parent.mkdir(parents=True, exist_ok=True)
        SEAL.write_text(json.dumps(stable_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(stable_report, indent=2, sort_keys=True))
    return 0 if stable_report["pass"] else 1


def _close(actual: object, expected: object) -> bool:
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return False
    if not isinstance(expected, (int, float)) or isinstance(expected, bool):
        return False
    a, e = float(actual), float(expected)
    return math.isfinite(a) and math.isfinite(e) and abs(a - e) <= max(1e-12, 1e-6 * max(abs(a), abs(e)))


def _check_eval(actual: object, expected: dict, where: str, errors: list[str]) -> None:
    if not isinstance(actual, dict):
        errors.append(f"{where}: endpoint evaluation is not an object")
        return
    for field in ("class", "calculated_only_class", "coverage", "unknown_nuclides", "unlisted_nuclides"):
        if actual.get(field) != expected.get(field):
            errors.append(f"{where}: endpoint {field} differs")
    if not _close(actual.get("unlisted_activity_bq"), expected.get("unlisted_activity_bq")):
        errors.append(f"{where}: unlisted activity differs")
    expected_rows = expected["row_details"]
    actual_rows = actual.get("row_fractions")
    key = lambda row: (row.get("table"), row.get("column"), row.get("row_id"), row.get("nuclide"), row.get("unit"))
    if not isinstance(actual_rows, list) or len(actual_rows) != len(expected_rows):
        errors.append(f"{where}: row-fraction count differs")
    else:
        for got, want in zip(sorted(actual_rows, key=key), sorted(expected_rows, key=key)):
            if key(got) != key(want) or got.get("limit") != want.get("limit"):
                errors.append(f"{where}: row identity or limit differs")
                continue
            for field in ("concentration", "fraction"):
                if want.get(field) is None:
                    if got.get(field) is not None:
                        errors.append(f"{where}: row {field} should be null")
                elif not _close(got.get(field), want.get(field)):
                    errors.append(f"{where}: row {field} differs")
    expected_constraints = {(x["target_class"], x["table"], x["column"]): x for x in expected["constraints"]}
    actual_constraints = actual.get("constraints")
    if not isinstance(actual_constraints, list) or len(actual_constraints) != len(expected_constraints):
        errors.append(f"{where}: constraint count differs")
    else:
        actual_ids=[(c.get("target_class"),c.get("table"),c.get("column")) for c in actual_constraints]
        if len(set(actual_ids))!=len(actual_ids) or set(actual_ids)!=set(expected_constraints):
            errors.append(f"{where}: constraint identities are duplicated or incomplete")
        for got in actual_constraints:
            ident = (got.get("target_class"), got.get("table"), got.get("column"))
            want = expected_constraints.get(ident)
            if want is None:
                errors.append(f"{where}: unexpected constraint {ident}")
                continue
            for field in ("strict", "passes", "contributor_count", "contributors"):
                if got.get(field) != want.get(field):
                    errors.append(f"{where}: constraint {ident} {field} differs")
            for field in ("source_sum_fraction", "normalized_sum", "normalized_margin"):
                if not _close(got.get(field), want.get(field, 1.0-want.get("normalized_sum", 0.0)
                                                    if field == "normalized_margin" else None)):
                    errors.append(f"{where}: constraint {ident} {field} differs")
    want_binding = expected["binding_constraints"]
    got_binding = actual.get("binding_constraints")
    def bind_id(c: dict) -> tuple:
        return c.get("target_class"), c.get("table"), c.get("column"), tuple(c.get("contributors", [])), c.get("strict"), c.get("passes")
    if not isinstance(got_binding, list) or sorted(map(bind_id, got_binding), key=repr) != sorted(map(bind_id, want_binding), key=repr):
        errors.append(f"{where}: binding constraints differ")
    elif isinstance(got_binding,list):
        expected_by_id={bind_id(item):item for item in want_binding}
        for item in got_binding:
            want=expected_by_id[bind_id(item)]
            for field in ("source_sum_fraction","normalized_sum","normalized_margin"):
                expected_value=want.get(field,1.0-want.get("normalized_sum",0.0) if field=="normalized_margin" else None)
                if not _close(item.get(field),expected_value):
                    errors.append(f"{where}: binding constraint {field} differs")


def _expected_ranges(expected: dict) -> dict:
    lower = {(r["table"],r["column"],r["row_id"],r["nuclide"],r["unit"]):r for r in expected["lower"]["row_details"]}
    upper = {(r["table"],r["column"],r["row_id"],r["nuclide"],r["unit"]):r for r in expected["upper"]["row_details"]}
    out = {}
    for key in lower.keys() | upper.keys():
        exemplar = lower.get(key) or upper[key]
        lo = lower.get(key, {**exemplar,"concentration":0.0,"fraction":None if exemplar["limit"] is None else 0.0})
        hi = upper.get(key, {**exemplar,"concentration":0.0,"fraction":None if exemplar["limit"] is None else 0.0})
        out[key] = (lo,hi)
    return out


def _validate_target(target: object, expected: dict, case_target: dict, rows: list[dict],
                     component: dict, result: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(target, dict):
        return ["target is not an object"]
    if target.get("step") != case_target["step"] or not _close(target.get("t_s"),case_target["t_s"]):
        errors.append("target identity/time differs")
    for field in ("inventory_coverage","unbounded_inventory_reasons","bounds_source","bounds_assumptions"):
        if target.get(field) != case_target[field]:
            errors.append(f"target {field} differs")
    coverage_reasons = list(case_target["unbounded_inventory_reasons"])
    external = component["external_tritium"]
    if external["status"] == "required":
        coverage_reasons.append("required external H-3 activity bounds were not supplied")
    known = {key.replace("-", "") for key in component["nuclide_properties"]}
    merged_for_coverage = copy.deepcopy(case_target["activity_bounds_bq"])
    if external["status"] == "bounded":
        extra = external["activity_bounds_bq"][str(case_target["step"])]
        prior = merged_for_coverage.get("H3", {"lower_bq": 0.0, "upper_bq": 0.0})
        merged_for_coverage["H3"] = {
            "lower_bq": prior["lower_bq"] + extra["lower_bq"],
            "upper_bq": prior["upper_bq"] + extra["upper_bq"],
        }
    unknown = sorted(
        name.replace("-", "") for name, interval in merged_for_coverage.items()
        if interval["upper_bq"] > 0.0 and name.replace("-", "") not in known
    )
    coverage_reasons.extend(
        f"positive upper activity for {name} lacks nuclide properties" for name in unknown
    )
    if target.get("coverage_reasons") != coverage_reasons:
        errors.append("coverage reasons differ")
    coverage_reasons=list(case_target["unbounded_inventory_reasons"])
    external=component["external_tritium"]
    if external["status"]=="required":
        coverage_reasons.append("required external H-3 activity bounds were not supplied")
    known={key.replace("-","") for key in component["nuclide_properties"]}
    merged_for_unknown=copy.deepcopy(case_target["activity_bounds_bq"])
    if external["status"]=="bounded":
        extra=external["activity_bounds_bq"][str(case_target["step"])]
        old=merged_for_unknown.get("H3",{"lower_bq":0.0,"upper_bq":0.0})
        merged_for_unknown["H3"]={"lower_bq":old["lower_bq"]+extra["lower_bq"],
                                  "upper_bq":old["upper_bq"]+extra["upper_bq"]}
    unknown=sorted(name.replace("-","") for name,bounds in merged_for_unknown.items()
                   if bounds["upper_bq"]>0 and name.replace("-","") not in known)
    coverage_reasons.extend(f"positive upper activity for {name} lacks nuclide properties" for name in unknown)
    if target.get("coverage_reasons")!=coverage_reasons:
        errors.append("coverage reasons differ")
    for field in ("activation_activity_bounds_bq",):
        if target.get(field) != case_target["activity_bounds_bq"]:
            errors.append(f"target {field} differs from frozen input")
    external = component["external_tritium"]
    expected_external = None if external["status"] != "bounded" else external["activity_bounds_bq"][str(case_target["step"])]
    if target.get("external_tritium_activity_bounds_bq") != expected_external:
        errors.append("external H-3 interval differs from frozen input")
    merged = copy.deepcopy(case_target["activity_bounds_bq"])
    if expected_external is not None:
        old=merged.get("H3",{"lower_bq":0.0,"upper_bq":0.0})
        merged["H3"]={"lower_bq":old["lower_bq"]+expected_external["lower_bq"],
                       "upper_bq":old["upper_bq"]+expected_external["upper_bq"]}
    if target.get("merged_activity_bounds_bq") != merged:
        errors.append("merged activity interval differs from independent H-3 addition")
    for end in ("lower","upper"):
        _check_eval(target.get(end), expected[end], end, errors)
    if target.get("coverage") != expected["lower"]["coverage"]:
        errors.append("target coverage differs")
    for field in ("class_envelope","class_is_stable","stable_class","conservative_superset"):
        if target.get(field) != expected[field]:
            errors.append(f"target {field} differs")
    if target.get("merged_activity_bounds_bq") != merged:
        errors.append("canonical evaluated activity bounds differ")
    ranges=target.get("row_fraction_ranges")
    want_ranges=_expected_ranges(expected)
    key=lambda row:(row.get("table"),row.get("column"),row.get("row_id"),row.get("nuclide"),row.get("unit"))
    if not isinstance(ranges,list) or len(ranges)!=len(want_ranges):
        errors.append("row range count differs")
    else:
        range_ids=[key(item) for item in ranges]
        if len(set(range_ids))!=len(range_ids) or set(range_ids)!=set(want_ranges):
            errors.append("row range identities are duplicated or incomplete")
        for got in ranges:
            ident=key(got); pair=want_ranges.get(ident)
            if pair is None:
                errors.append(f"unexpected row range {ident}");continue
            for field in ("table","column","row_id","nuclide","unit"):
                if got.get(field)!=pair[0].get(field): errors.append(f"row range {ident} {field} differs")
            for bound,want in zip(("lower","upper"),pair):
                actual=got.get(bound,{})
                for field in ("limit","concentration","fraction"):
                    value=want.get(field)
                    if value is None:
                        if actual.get(field) is not None: errors.append(f"row range {ident} {bound}.{field} should be null")
                    elif field=="limit":
                        if actual.get(field)!=value: errors.append(f"row range {ident} {bound}.{field} differs")
                    elif not _close(actual.get(field),value): errors.append(f"row range {ident} {bound}.{field} differs")
    got_constraints=target.get("constraint_ranges")
    expected_constraints={(x["target_class"],x["table"],x["column"]):x for x in expected["lower"]["constraints"]}
    expected_upper={(x["target_class"],x["table"],x["column"]):x for x in expected["upper"]["constraints"]}
    if not isinstance(got_constraints,list) or len(got_constraints)!=6:
        errors.append("constraint range count is not six")
    else:
        range_ids=[(item.get("target_class"),item.get("table"),item.get("column")) for item in got_constraints]
        if len(set(range_ids))!=len(range_ids) or set(range_ids)!=set(expected_constraints):
            errors.append("constraint range identities are duplicated or incomplete")
        for got in got_constraints:
            ident=(got.get("target_class"),got.get("table"),got.get("column"))
            lo=expected_constraints.get(ident);hi=expected_upper.get(ident)
            if lo is None or hi is None:
                errors.append(f"unexpected constraint range {ident}");continue
            for bound,want in (("lower",lo),("upper",hi)):
                point=got.get(bound,{})
                for field in ("source_sum_fraction","normalized_sum","normalized_margin"):
                    val=want.get(field,1.0-want.get("normalized_sum",0.0) if field=="normalized_margin" else None)
                    if not _close(point.get(field),val): errors.append(f"constraint range {ident} {bound}.{field} differs")
                for field in ("strict","passes","contributor_count","contributors"):
                    if point.get(field)!=want.get(field): errors.append(f"constraint range {ident} {bound}.{field} differs")
    return errors


def _validate_report(actual: dict, spec: dict, expected_by_id: dict, rows: list[dict]) -> list[str]:
    errors=[]
    if actual.get("schema")!="actinv-waste-bounds-result-1" or actual.get("method")!="declared_activity_box":
        errors.append("result schema/method differs")
    if actual.get("input_sha256")!=spec.get("_input_sha256"):
        errors.append("input SHA-256 differs from exact frozen CLI input bytes")
    if (actual.get("rules",{}).get("id")!="us-nrc-10cfr61.55-v1"
            or actual.get("rules",{}).get("sha256")!=PACK_SHA256
            or actual.get("rules",{}).get("version")!=1
            or actual.get("rules",{}).get("source_url")!=json.loads(PACK.read_text(encoding="utf-8"))["source_url"]
            or actual.get("rules",{}).get("source_as_of")!=json.loads(PACK.read_text(encoding="utf-8"))["source_as_of"]):
        errors.append("selected rules identity/hash differs")
    components=actual.get("components")
    if not isinstance(components,list) or len(components)!=len(spec["components"]):
        return errors+["component count differs"]
    by_id={c.get("id"):c for c in components}
    for source in spec["components"]:
        got=by_id.get(source["id"])
        if not isinstance(got,dict): errors.append(f"component {source['id']} missing");continue
        for field in ("id","mass_g","waste_type","nuclide_properties","external_tritium"):
            if got.get(field)!=source.get(field): errors.append(f"component {source['id']} {field} differs")
        geometry=got.get("geometry",{})
        if (got.get("displaced_volume_cm3")!=source.get("displaced_volume_cm3")
                or got.get("density_g_cm3")!=source.get("density_g_cm3")
                or not isinstance(geometry,dict)
                or geometry.get("mass_g")!=source.get("mass_g")
                or geometry.get("displaced_volume_cm3")!=source.get("displaced_volume_cm3")):
            errors.append(f"component {source['id']} geometry differs")
        actual_targets=got.get("targets",[])
        target_by_step={t.get("step"):t for t in actual_targets if isinstance(t,dict)}
        if len(actual_targets)!=len(source["targets"]) or len(target_by_step)!=len(actual_targets):
            errors.append(f"component {source['id']} target count/step uniqueness differs")
        for t in source["targets"]:
            target_actual=target_by_step.get(t["step"])
            expected=expected_by_id[source["id"]]["targets"]
            expected_target=next(x for x in expected if x["step"]==t["step"])
            errors.extend(_validate_target(target_actual,expected_target,t,rows,source,actual))
    return errors


def _run_cli(argv: list[str]):
    # Reuse the P105-reviewed bounded lifecycle runner: timeout, terminate,
    # kill, and reap are bounded, including the cancellation path.
    from p105_budget_control import _run
    return _run(argv,cwd=ROOT,timeout_s=120)


def _run_refusals(work: Path) -> dict:
    base={"schema":"actinv-waste-bounds-spec-1","rules":"us-nrc-10cfr61.55-v1",
          "components":[{"id":"refusal","mass_g":1.0,"displaced_volume_cm3":1.0,
          "waste_type":"general","nuclide_properties":{"C14":{"z":6,"half_life_s":3.0e8,"alpha_emitting":False}},
          "external_tritium":{"status":"not_applicable"},"targets":[{"step":1,"t_s":0.0,
          "activity_bounds_bq":{"C14":{"lower_bq":0.0,"upper_bq":1.0}},
          "inventory_coverage":"complete","unbounded_inventory_reasons":[],
          "bounds_source":"artificial","bounds_assumptions":"rectangular"}]}]}
    variants={}
    def put(name, change):
        value=copy.deepcopy(base);change(value);variants[name]=json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n"
    put("negative_bound",lambda x:x["components"][0]["targets"][0]["activity_bounds_bq"]["C14"].__setitem__("lower_bq",-1.0))
    put("reversed_bound",lambda x:x["components"][0]["targets"][0]["activity_bounds_bq"]["C14"].update({"lower_bq":2.0,"upper_bq":1.0}))
    put("duplicate_alias",lambda x:x["components"][0]["targets"][0]["activity_bounds_bq"].update({"C-14":{"lower_bq":0.0,"upper_bq":1.0}}))
    put("duplicate_property_alias",lambda x:x["components"][0]["nuclide_properties"].update({"C-14":{"z":6,"half_life_s":3.0e8,"alpha_emitting":False}}))
    put("invalid_geometry",lambda x:x["components"][0].__setitem__("mass_g",-1.0))
    put("missing_target_boxes",lambda x:x["components"][0].__setitem__("targets",[]))
    put("missing_activity_bounds",lambda x:x["components"][0]["targets"][0].pop("activity_bounds_bq"))
    def duplicate_steps(x):
        target=copy.deepcopy(x["components"][0]["targets"][0]);target["t_s"]=1.0
        x["components"][0]["targets"].append(target)
    put("duplicate_target_step",duplicate_steps)
    put("incomplete_without_reason",lambda x:x["components"][0]["targets"][0].update({"inventory_coverage":"incomplete","unbounded_inventory_reasons":[]}))
    put("complete_with_reason",lambda x:x["components"][0]["targets"][0].update({"unbounded_inventory_reasons":["contradiction"]}))
    put("missing_source",lambda x:x["components"][0]["targets"][0].__setitem__("bounds_source"," "))
    put("missing_assumptions",lambda x:x["components"][0]["targets"][0].__setitem__("bounds_assumptions"," "))
    put("custom_rule_pack",lambda x:x.__setitem__("rules","custom.json"))
    put("bad_metadata",lambda x:x["components"][0]["nuclide_properties"]["C14"].__setitem__("z",5))
    put("nonpositive_half_life",lambda x:x["components"][0]["nuclide_properties"]["C14"].__setitem__("half_life_s",0.0))
    put("missing_property_field",lambda x:x["components"][0]["nuclide_properties"]["C14"].pop("alpha_emitting"))
    put("negative_target_time",lambda x:x["components"][0]["targets"][0].__setitem__("t_s",-1.0))
    put("duplicate_component_id",lambda x:x["components"].append(copy.deepcopy(x["components"][0])))
    put("missing_external_declaration",lambda x:x["components"][0].pop("external_tritium"))
    def no_external_source(x):
        x["components"][0]["external_tritium"]={"status":"bounded","source":" ","excludes_activation":True,
          "activity_bounds_bq":{"1":{"lower_bq":0.0,"upper_bq":0.0}}}
    put("empty_external_source",no_external_source)
    def external_overlaps(x):
        x["components"][0]["external_tritium"]={"status":"bounded","source":"bad","excludes_activation":False,
          "activity_bounds_bq":{"1":{"lower_bq":0.0,"upper_bq":0.0}}}
    put("external_includes_activation",external_overlaps)
    def missing_external_step(x):
        x["components"][0]["external_tritium"]={"status":"bounded","source":"valid","excludes_activation":True,
          "activity_bounds_bq":{"2":{"lower_bq":0.0,"upper_bq":0.0}}}
    put("missing_bounded_step",missing_external_step)
    def noncanonical_step(x):
        x["components"][0]["external_tritium"]={"status":"bounded","source":"valid","excludes_activation":True,
          "activity_bounds_bq":{"01":{"lower_bq":0.0,"upper_bq":0.0}}}
    put("noncanonical_external_step",noncanonical_step)
    overflow=copy.deepcopy(base);target=overflow["components"][0]["targets"][0]
    target["activity_bounds_bq"]={"C14":{"lower_bq":1.79e308,"upper_bq":1.79e308},
                                  "Tc99":{"lower_bq":1.79e308,"upper_bq":1.79e308}}
    overflow["components"][0]["nuclide_properties"]["Tc99"]={"z":43,"half_life_s":3e8,"alpha_emitting":False}
    variants["activity_accumulation_overflow"]=json.dumps(overflow,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n"
    nonfinite=variants["negative_bound"].replace('"lower_bq":-1.0','"lower_bq":NaN')
    variants["nonfinite_bound"]=nonfinite
    duplicate_json='{"schema":"actinv-waste-bounds-spec-1","rules":"us-nrc-10cfr61.55-v1","components":[],"components":[]}'
    variants["duplicate_json_key"]=duplicate_json
    duplicate_activity_json=("{\"schema\":\"actinv-waste-bounds-spec-1\",\"rules\":\"us-nrc-10cfr61.55-v1\",\"components\":["
      "{\"id\":\"x\",\"mass_g\":1,\"displaced_volume_cm3\":1,\"waste_type\":\"general\",\"nuclide_properties\":{\"C14\":{\"z\":6,\"half_life_s\":300000000,\"alpha_emitting\":false}},"
      "\"external_tritium\":{\"status\":\"not_applicable\"},\"targets\":[{\"step\":1,\"t_s\":0,\"activity_bounds_bq\":{\"C14\":{\"lower_bq\":0,\"upper_bq\":1},\"C14\":{\"lower_bq\":0,\"upper_bq\":2}},"
      "\"inventory_coverage\":\"complete\",\"unbounded_inventory_reasons\":[],\"bounds_source\":\"x\",\"bounds_assumptions\":\"x\"}]}]}")
    variants["duplicate_json_activity_key"]=duplicate_activity_json
    checks={}
    for name,text in variants.items():
        spec_path=work/f"refusal-{name}.json";output=work/f"refusal-{name}-out.json"
        spec_path.write_text(text,encoding="utf-8");output.write_text("sentinel\n",encoding="utf-8")
        child=_run_cli([str(ACTINV),"waste","bounds",str(spec_path),str(output)])
        checks[name]=child.returncode!=0 and output.read_text(encoding="utf-8")=="sentinel\n"
    return {"checks":checks,"pass":bool(checks) and all(checks.values())}


def run_g1(no_write: bool = False) -> int:
    pack, records = _derive_population()
    population_doc={"schema":"actinv-p106-bounds-cases-1","source":"artificial total-Bq boxes","cases":records}
    fixture_text=json.dumps(population_doc,sort_keys=True,indent=2,allow_nan=False)+"\n"
    if not P106_CASES.is_file() or P106_CASES.read_text(encoding="utf-8")!=fixture_text:
        raise RuntimeError("frozen P106 case fixture does not match deterministic generator")
    seal=json.loads(SEAL.read_text(encoding="utf-8"))
    if not seal.get("pass") or seal.get("case_fixture_sha256")!=sha256(P106_CASES):
        raise RuntimeError("P107 G0 seal is missing, failed, or no longer matches the frozen P106 case fixture")
    if not ACTINV.is_file():
        raise RuntimeError(f"ACTINV_BIN does not identify the release CLI: {ACTINV}")
    spec={"schema":"actinv-waste-bounds-spec-1","rules":"us-nrc-10cfr61.55-v1",
          "components":[record["input"]["component"] for record in records]}
    spec_text=json.dumps(spec,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n"
    spec["_input_sha256"]=hashlib.sha256(spec_text.encode()).hexdigest()
    expected_by_id={record["input"]["id"]:record["expected"] for record in records}
    work_root=ROOT/"target/p107-g1-controls";work_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p107-g1-",dir=work_root) as temp:
        work=Path(temp);spec_path=work/"bounds.json";out_path=work/"result.json";repeat_path=work/"repeat.json"
        spec_path.write_text(spec_text,encoding="utf-8")
        args=[str(ACTINV),"waste","bounds",str(spec_path),str(out_path)]
        first=_run_cli(args)
        if first.returncode!=0:
            raise RuntimeError(f"P107 G1 CLI rejected frozen batch ({first.returncode}): {first.stderr[-2500:]}")
        result=json.loads(out_path.read_text(encoding="utf-8"))
        failures=_validate_report(result,spec,expected_by_id,pack["rows"])
        second=_run_cli([str(ACTINV),"waste","bounds",str(spec_path),str(repeat_path)])
        repeated=(second.returncode==0 and repeat_path.is_file() and repeat_path.read_bytes()==out_path.read_bytes())
        if not repeated: failures.append("separate-path repeated CLI output is not byte-identical")
        mutation_controls={}
        witnesses={"range":next(r for r in records if r["input"]["id"]=="mixture_t1_strict_box"),
                   "external":next(r for r in records if r["input"]["id"]=="external_h3_merge"),
                   "identity":next(r for r in records if r["input"]["id"]=="empty_complete")}
        def target_at(document: dict, case_id: str, step: int = 1) -> dict:
            comp=next(c for c in document["components"] if c["id"]==case_id)
            return next(t for t in comp["targets"] if t["step"]==step)
        def verify_mutation(label: str, mutate) -> None:
            changed=copy.deepcopy(result);mutate(changed)
            mutation_controls[label]=bool(_validate_report(changed,spec,expected_by_id,pack["rows"]))
        mixture_id=witnesses["range"]["input"]["id"]
        external_id=witnesses["external"]["input"]["id"]
        empty_id=witnesses["identity"]["input"]["id"]
        row_id=next(r["input"]["id"] for r in records if r["input"]["id"]=="single_t1_boundary_box")
        verify_mutation("upper_activity",lambda x: target_at(x,mixture_id)["merged_activity_bounds_bq"]["C14"].__setitem__("upper_bq",999.0))
        verify_mutation("geometry",lambda x: next(c for c in x["components"] if c["id"]==mixture_id).__setitem__("mass_g",2.0))
        verify_mutation("nested_geometry",lambda x: next(c for c in x["components"] if c["id"]==mixture_id)["geometry"].__setitem__("displaced_volume_cm3",2.0))
        verify_mutation("rules_sha",lambda x: x["rules"].__setitem__("sha256","0"*64))
        verify_mutation("class_envelope",lambda x: target_at(x,mixture_id).__setitem__("class_envelope",["A"]))
        verify_mutation("row_fraction",lambda x: target_at(x,row_id)["row_fraction_ranges"][0]["upper"].__setitem__("fraction",9.0))
        verify_mutation("constraint_sum",lambda x: target_at(x,mixture_id)["constraint_ranges"][0]["upper"].__setitem__("normalized_sum",9.0))
        verify_mutation("contributor_count",lambda x: target_at(x,mixture_id)["constraint_ranges"][0]["upper"].__setitem__("contributor_count",9))
        verify_mutation("target_time",lambda x: target_at(x,mixture_id).__setitem__("t_s",9.0))
        verify_mutation("stable_class",lambda x: target_at(x,empty_id).__setitem__("stable_class","C"))
        verify_mutation("input_sha",lambda x: x.__setitem__("input_sha256","0"*64))
        incomplete_id=next(r["input"]["id"] for r in records if r["input"]["id"]=="empty_incomplete")
        verify_mutation("coverage_completeness",lambda x: target_at(x,incomplete_id).__setitem__("inventory_coverage","complete"))
        # Use a strict multi-contributor target to ensure the strictness mutation
        # is checked against independent source contributors.
        strict_copy=copy.deepcopy(result)
        strict_target=next(c for c in strict_copy["components"] if c["id"]==mixture_id)["targets"][0]
        strict_target["constraint_ranges"][0]["upper"]["strict"]=not strict_target["constraint_ranges"][0]["upper"]["strict"]
        mutation_controls["strictness"]=bool(_validate_report(strict_copy,spec,expected_by_id,pack["rows"]))
        margin_copy=copy.deepcopy(result)
        margin_target=next(c for c in margin_copy["components"] if c["id"]==mixture_id)["targets"][0]
        margin_target["upper"]["binding_constraints"][0]["normalized_margin"]+=0.25
        mutation_controls["binding_margin"]=bool(_validate_report(margin_copy,spec,expected_by_id,pack["rows"]))
        duplicate_row=copy.deepcopy(result)
        row_target=target_at(duplicate_row,mixture_id)
        row_target["row_fraction_ranges"][1]=copy.deepcopy(row_target["row_fraction_ranges"][0])
        mutation_controls["duplicate_row_range"]=bool(_validate_report(duplicate_row,spec,expected_by_id,pack["rows"]))
        duplicate_constraint=copy.deepcopy(result)
        constraint_target=target_at(duplicate_constraint,mixture_id)
        constraint_target["constraint_ranges"][1]=copy.deepcopy(constraint_target["constraint_ranges"][0])
        mutation_controls["duplicate_constraint_range"]=bool(_validate_report(duplicate_constraint,spec,expected_by_id,pack["rows"]))
        def mutate_source(x):
            next(c for c in x["components"] if c["id"]==external_id)["external_tritium"]["source"]="mutated source"
        verify_mutation("external_source",mutate_source)
        def mutate_external(x):
            target_at(x,external_id)["external_tritium_activity_bounds_bq"]["upper_bq"]+=100.0
        verify_mutation("external_interval",mutate_external)
        def mutate_excludes(x):
            next(c for c in x["components"] if c["id"]==external_id)["external_tritium"]["excludes_activation"]=False
        verify_mutation("external_exclusion",mutate_excludes)
        missing_props_id=row_id
        def mutate_metadata(x):
            next(c for c in x["components"] if c["id"]==missing_props_id)["nuclide_properties"].pop("C14",None)
        verify_mutation("upper_positive_missing_metadata",mutate_metadata)
        if not all(mutation_controls.values()): failures.append("one or more planted mutation controls were not sensitive")
        refusal_controls=_run_refusals(work)
        if not refusal_controls.get("pass"): failures.append("one or more invalid bounds specifications were accepted or changed output")
        report={"schema":"actinv-p107-bounds-g1-1","protocol_sha256":PROTOCOL_SHA256,
                "case_count":len(records),"output_sha256":hashlib.sha256(out_path.read_bytes()).hexdigest(),
                "repeat_byte_identical":repeated,"mutations_rejected":mutation_controls,
                "refusal_controls":refusal_controls,
                "target_count":sum(len(component["targets"]) for component in spec["components"]),
                "endpoint_count":2*sum(len(component["targets"]) for component in spec["components"]),
                "independent_comparison_count":len(records),"failures":failures,"pass":not failures}
    return _persist_gate(report,G1_RESULT,no_write)


def _persist_gate(report: dict, path: Path, no_write: bool) -> int:
    if no_write:
        try: persisted=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):
            report["failures"].append("persisted gate result missing or malformed");report["pass"]=False
        else:
            same=persisted==report
            report["persisted_result_matches"]=same
            report["pass"] &= same
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if report["pass"] else 1


def run_g2(no_write: bool = False, *, g1_pass: bool) -> int:
    try:
        g1 = json.loads(G1_RESULT.read_text(encoding="utf-8"))
        g1_hash = sha256(G1_RESULT)
    except (OSError, json.JSONDecodeError) as error:
        g1 = {}; g1_hash = None
        failure = str(error)
    else:
        failure = None
    passed = (g1_pass and g1.get("pass") is True and g1.get("protocol_sha256") == PROTOCOL_SHA256
              and g1.get("repeat_byte_identical") is True)
    report = {"schema":"actinv-p107-bounds-g2-1","phase":"P107",
              "protocol_sha256":PROTOCOL_SHA256,"g1_result_sha256":g1_hash,
              "separate_output_paths_byte_identical":g1.get("repeat_byte_identical") is True,
              "failures":[] if passed else [failure or "G1 persisted replay or deterministic output check failed"],
              "pass":passed}
    return _persist_gate(report,G2_RESULT,no_write)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seal", action="store_true", help="derive and freeze G0 before production CLI execution")
    parser.add_argument("--g0-only", action="store_true")
    parser.add_argument("--g1-only", action="store_true")
    parser.add_argument("--g2-only", action="store_true")
    parser.add_argument("--no-write", action="store_true", help="replay against frozen result artifacts")
    args = parser.parse_args()
    if sum((args.g0_only, args.g1_only, args.g2_only)) > 1 or (args.seal and (args.g1_only or args.g2_only or args.no_write)):
        parser.error("--g0-only, --g1-only, and --g2-only are mutually exclusive")
    if args.seal:
        raise SystemExit(g0())
    if g0(no_write=True)!=0:
        raise SystemExit(1)
    if args.g0_only:
        raise SystemExit(0)
    if args.g1_only:
        raise SystemExit(run_g1(no_write=args.no_write))
    if args.g2_only:
        g1_status=run_g1(no_write=True)
        if g1_status!=0:
            raise SystemExit(1)
        raise SystemExit(run_g2(no_write=args.no_write,g1_pass=True))
    g1_status=run_g1(no_write=args.no_write)
    if g1_status!=0:
        raise SystemExit(1)
    raise SystemExit(run_g2(no_write=args.no_write,g1_pass=True))


if __name__ == "__main__":
    raise SystemExit(main())
