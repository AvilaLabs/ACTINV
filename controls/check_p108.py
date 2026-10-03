#!/usr/bin/env python3
"""P108 sealed source/composition controls and independent CLI oracle gate."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import tempfile
from pathlib import Path

import p108_composition_control as oracle

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P108_PROTOCOL.md"
PROTOCOL_SHA256 = "ae503d3fe2ff9569c793f162d3b9c19975815f80a53d20458513ee8b9d267fdd"
AMENDMENT = ROOT / "protocols/ACTINV-P108_AMENDMENT_A.md"
AMENDMENT_SHA256 = "ce4fa1fa0abff041ee57349be7ca6009ed864a3368edeeb21089354b48f33d9f"
G3_FAILURE_LOG = ROOT / "results/p108_g3_check_attempt_1.log"
G3_FAILURE_LOG_SHA256 = "c14f21651e7aeee55834f97f20ffaa203fdf8c6d661478ec030c02baa0265b90"
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"
PACK_SHA256 = "890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7"
PACK_MIRROR = ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json"
FIXTURE = ROOT / "controls/fixtures/p108/composition_cases.json"
P105_VERDICT = ROOT / "results/p105_verdict.json"
P103_VERDICT = ROOT / "results/p103_verdict.json"
P104_VERDICT = ROOT / "results/p104_verdict.json"
P106_VERDICT = ROOT / "results/p106_verdict.json"
P107_VERDICT = ROOT / "results/p107_verdict.json"
P108_G0 = ROOT / "results/g0_p108_composition.json"
P108_G1 = ROOT / "results/g1_p108_composition.json"
P108_G2 = ROOT / "results/g2_p108_composition.json"
P107_HISTORY = ROOT / "controls/check_p107_history.py"
P107_HISTORY_TEST = ROOT / "controls/test_p107_history.py"
ACTINV = Path(__import__("os").environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
CASE_SCHEMA = "actinv-p108-composition-cases-1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registered() -> bool:
    wanted = f"{PROTOCOL_SHA256}  {PROTOCOL.relative_to(ROOT)}"
    registry = ROOT / "protocols/protocol_hash.txt"
    return sha256(PROTOCOL) == PROTOCOL_SHA256 and registry.is_file() and wanted in registry.read_text(encoding="utf-8").splitlines()


def _amendment_registered() -> bool:
    wanted = f"{AMENDMENT_SHA256}  {AMENDMENT.relative_to(ROOT)}"
    registry = ROOT / "protocols/protocol_hash.txt"
    return (sha256(AMENDMENT) == AMENDMENT_SHA256 and registry.is_file()
            and wanted in registry.read_text(encoding="utf-8").splitlines())


def _fixture_content() -> tuple[list[dict], bytes]:
    cases = oracle.generate_cases()
    records = oracle.derive_population(cases)
    document = {"schema": CASE_SCHEMA, "source": "artificial declared affine composition models",
                "records": records}
    return cases, (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _history_replay() -> dict:
    import check_p107_history
    return check_p107_history.verify()


def _g0_base() -> dict:
    cases, fixture_bytes = _fixture_content()
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    labels = oracle.expected_labels(cases, pack["rows"])
    fixture_matches = FIXTURE.is_file() and FIXTURE.read_bytes() == fixture_bytes
    p105 = json.loads(P105_VERDICT.read_text(encoding="utf-8"))
    p103 = json.loads(P103_VERDICT.read_text(encoding="utf-8"))
    p104 = json.loads(P104_VERDICT.read_text(encoding="utf-8"))
    p106 = json.loads(P106_VERDICT.read_text(encoding="utf-8"))
    p107 = json.loads(P107_VERDICT.read_text(encoding="utf-8"))
    history = _history_replay()
    own_paths = [ROOT / "controls/check_p108.py", ROOT / "controls/test_p108_seal.py",
                 ROOT / "controls/check_p108_verdict.py", ROOT / "controls/p108_composition_control.py",
                 P107_HISTORY, P107_HISTORY_TEST]
    hashes = {str(path.relative_to(ROOT)): sha256(path) for path in own_paths if path.is_file()}
    pack_ok = (sha256(PACK) == PACK_SHA256 and sha256(PACK_MIRROR) == PACK_SHA256
               and PACK.read_bytes() == PACK_MIRROR.read_bytes())
    report = {
        "schema": "actinv-p108-composition-g0-1", "phase": "P108",
        "protocol_sha256": sha256(PROTOCOL), "protocol_registered": _registered(),
        "amendment_sha256": sha256(AMENDMENT), "amendment_registered": _amendment_registered(),
        "g3_check_failure_log_sha256": sha256(G3_FAILURE_LOG),
        "g3_check_failure_log_matches": sha256(G3_FAILURE_LOG) == G3_FAILURE_LOG_SHA256,
        "pack_sha256": sha256(PACK), "pack_mirror_identical": pack_ok,
        "p105_terminal_verdict": p105.get("verdict"),
        "p105_verdict_sha256": sha256(P105_VERDICT),
        "p103_terminal_verdict": p103.get("verdict"),
        "p103_verdict_sha256": sha256(P103_VERDICT),
        "p104_terminal_verdict": p104.get("verdict"),
        "p104_verdict_sha256": sha256(P104_VERDICT),
        "p106_terminal_verdict": p106.get("verdict"),
        "p106_verdict_sha256": sha256(P106_VERDICT),
        "p107_terminal_verdict": p107.get("verdict"),
        "p107_verdict_sha256": sha256(P107_VERDICT),
        "p107_implementation_record_sha256": sha256(ROOT / "results/p107_implementation_commit.json"),
        "p107_ci_record_sha256": sha256(ROOT / "results/p107_ci_runs.json"),
        "historical_p107_verified": history.get("pass") is True,
        "p107_historical_replay_sha256": sha256(P107_HISTORY),
        "case_count": len(cases), "target_count": sum(len(c["targets"]) for c in cases),
        "endpoint_count": 2 * sum(len(c["targets"]) for c in cases),
        "expected_labels": labels,
        "case_fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "case_fixture_matches": fixture_matches,
        "control_hashes": hashes,
    }
    report["pass"] = bool(
        report["protocol_registered"] and report["amendment_registered"]
        and report["g3_check_failure_log_matches"] and pack_ok and p103.get("verdict") == "P103-FAIL"
        and p104.get("verdict") == "P104-FAIL" and p105.get("verdict") == "P105-PASS"
        and p106.get("verdict") == "P106-FAIL" and p107.get("verdict") == "P107-PASS"
        and history.get("pass") is True
        and len(cases) == 12 and report["target_count"] == 13 and report["endpoint_count"] == 26
        and labels.get("pass") is True and fixture_matches and len(hashes) == len(own_paths)
    )
    return report


def g0(no_write: bool = False, seal: bool = False) -> int:
    report = _g0_base()
    if seal:
        if report["pass"]:
            P108_G0.parent.mkdir(parents=True, exist_ok=True)
            P108_G0.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif no_write:
        try:
            persisted = json.loads(P108_G0.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report["pass"] = False
        else:
            matches = persisted == report
            report["pass"] = report["pass"] and matches
            report["persisted_seal_matches"] = matches
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["pass"] else 1


def _run_cli(argv: list[str]):
    from p105_budget_control import _run
    return _run(argv, cwd=ROOT, timeout_s=120)


def _base_refusal_spec() -> dict:
    return {
        "schema": "actinv-waste-composition-spec-1",
        "rules": "us-nrc-10cfr61.55-v1",
        "response_model": "fixed_rate_affine_activity",
        "components": [{
            "id": "refusal", "mass_g": 1.0, "displaced_volume_cm3": 1.0,
            "waste_type": "general",
            "nuclide_properties": {"C14": {"z": 6, "half_life_s": 315576000.0, "alpha_emitting": False}},
            "external_tritium": {"status": "not_applicable"},
            "composition_wt_percent_bounds": {
                "Si": {"lower_wt_percent": 0.0, "upper_wt_percent": 50.0},
                "Fe": {"lower_wt_percent": 50.0, "upper_wt_percent": 100.0}},
            "targets": [{
                "step": 1, "t_s": 0.0,
                "element_activity_bq_per_g": {"Si": {}, "Fe": {"C14": 1.0}},
                "inventory_coverage": "complete", "unbounded_inventory_reasons": [],
                "bounds_source": "P108 artificial refusal control",
                "bounds_assumptions": "fixed affine response model"}],
        }],
    }


def _refusal_controls(work: Path) -> dict:
    base = _base_refusal_spec()
    variants: dict[str, str] = {}

    def put(name, mutate):
        spec = copy.deepcopy(base)
        mutate(spec)
        variants[name] = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"

    component = lambda x: x["components"][0]
    weights = lambda x: component(x)["composition_wt_percent_bounds"]
    target = lambda x: component(x)["targets"][0]
    put("negative_weight", lambda x: weights(x)["Si"].__setitem__("lower_wt_percent", -0.1))
    put("reversed_weight", lambda x: weights(x)["Si"].update({"lower_wt_percent": 70.0, "upper_wt_percent": 50.0}))
    put("over_100_weight", lambda x: weights(x)["Si"].__setitem__("upper_wt_percent", 100.1))
    put("infeasible_lower_sum", lambda x: weights(x).update({
        "Si": {"lower_wt_percent": 60.0, "upper_wt_percent": 100.0},
        "Fe": {"lower_wt_percent": 50.0, "upper_wt_percent": 100.0}}))
    put("infeasible_upper_sum", lambda x: weights(x).update({
        "Si": {"lower_wt_percent": 0.0, "upper_wt_percent": 40.0},
        "Fe": {"lower_wt_percent": 50.0, "upper_wt_percent": 50.0}}))
    def exact_binary_infeasible(x):
        component(x)["composition_wt_percent_bounds"] = {
            symbol: {"lower_wt_percent": 100.0 / 3.0, "upper_wt_percent": 100.0 / 3.0}
            for symbol in ("Fe", "Nb", "Si")}
        target(x)["element_activity_bq_per_g"] = {"Fe": {}, "Nb": {}, "Si": {}}
    put("exact_binary_infeasible_empty_responses", exact_binary_infeasible)
    put("empty_coordinates", lambda x: component(x).__setitem__("composition_wt_percent_bounds", {}))
    put("isotope_coordinate", lambda x: weights(x).update({"C14": {"lower_wt_percent": 0.0, "upper_wt_percent": 100.0}}))
    put("noncanonical_element_alias", lambda x: weights(x).update({"si": {"lower_wt_percent": 0.0, "upper_wt_percent": 1.0}}))
    def duplicate_canonical_nuclide(x):
        target(x)["element_activity_bq_per_g"]["Fe"] = {"C14": 1.0, "C-14": 1.0}
    put("duplicate_canonical_nuclide", duplicate_canonical_nuclide)
    outer_duplicate = json.dumps(base, sort_keys=True, separators=(",", ":"), allow_nan=False)
    needle = '"element_activity_bq_per_g":{"Fe":{"C14":1.0},"Si":{}}'
    outer_duplicate = outer_duplicate.replace(needle,
        '"element_activity_bq_per_g":{"Fe":{"C14":1.0},"Si":{},"Si":{}}', 1) + "\n"
    variants["duplicate_outer_response_key"] = outer_duplicate
    def duplicate_property_alias(x):
        component(x)["nuclide_properties"]["C-14"] = copy.deepcopy(component(x)["nuclide_properties"]["C14"])
    put("duplicate_property_alias", duplicate_property_alias)
    put("missing_response_constituent", lambda x: target(x).__setitem__("element_activity_bq_per_g", {"Si": {}}))
    put("extra_response_constituent", lambda x: target(x)["element_activity_bq_per_g"].update({"Nb": {}}))
    put("negative_coefficient", lambda x: target(x)["element_activity_bq_per_g"]["Fe"].__setitem__("C14", -1.0))
    put("unknown_field", lambda x: component(x).__setitem__("unrecognized", True))
    put("unknown_base_spec", lambda x: x.__setitem__("base_spec", "forbidden"))
    put("unknown_confidence", lambda x: x.__setitem__("confidence", 0.95))
    put("bad_geometry", lambda x: component(x).__setitem__("mass_g", 0.0))
    put("null_geometry", lambda x: component(x).__setitem__("displaced_volume_cm3", None))
    put("both_geometry", lambda x: component(x).__setitem__("density_g_cm3", 1.0))
    put("bad_property", lambda x: component(x)["nuclide_properties"]["C14"].__setitem__("z", 7))
    put("duplicate_target_step", lambda x: component(x)["targets"].append(copy.deepcopy(target(x))))
    put("zero_target_step", lambda x: target(x).__setitem__("step", 0))
    put("negative_target_time", lambda x: target(x).__setitem__("t_s", -1.0))
    put("coverage_conflict", lambda x: target(x).update({"inventory_coverage": "incomplete", "unbounded_inventory_reasons": []}))
    put("complete_with_reason", lambda x: target(x).update({"inventory_coverage": "complete", "unbounded_inventory_reasons": ["contradiction"]}))
    put("missing_source", lambda x: target(x).__setitem__("bounds_source", " "))
    put("missing_assumptions", lambda x: target(x).__setitem__("bounds_assumptions", " "))
    put("missing_external_declaration", lambda x: component(x).pop("external_tritium"))
    put("external_wrong_exclusion", lambda x: component(x).__setitem__("external_tritium", {
        "status": "bounded", "source": "external", "excludes_activation": False,
        "activity_bounds_bq": {"1": {"lower_bq": 0.0, "upper_bq": 0.0}}}))
    def wrong_external_step(x):
        component(x)["external_tritium"] = {"status": "bounded", "source": "external",
            "excludes_activation": True, "activity_bounds_bq": {"2": {"lower_bq": 0.0, "upper_bq": 0.0}}}
    put("external_step_missing", wrong_external_step)
    def noncanonical_external_step(x):
        component(x)["external_tritium"] = {"status": "bounded", "source": "external",
            "excludes_activation": True, "activity_bounds_bq": {"01": {"lower_bq": 0.0, "upper_bq": 0.0}}}
    put("external_step_alias", noncanonical_external_step)
    duplicate_doc = json.dumps(base, sort_keys=True, separators=(",", ":"), allow_nan=False)
    raw_duplicate = duplicate_doc.replace('"schema":"actinv-waste-composition-spec-1"',
        '"schema":"actinv-waste-composition-spec-1","schema":"actinv-waste-composition-spec-1"', 1) + "\n"
    variants["duplicate_json_key"] = raw_duplicate
    duplicate_coordinate = duplicate_doc.replace(
        '"Fe":{"lower_wt_percent":50.0,"upper_wt_percent":100.0}',
        '"Fe":{"lower_wt_percent":50.0,"upper_wt_percent":100.0},"Fe":{"lower_wt_percent":50.0,"upper_wt_percent":100.0}', 1) + "\n"
    variants["duplicate_raw_coordinate"] = duplicate_coordinate
    duplicate_inner = duplicate_doc.replace('"C14":1.0', '"C14":1.0,"C14":2.0', 1) + "\n"
    variants["duplicate_raw_inner_nuclide"] = duplicate_inner
    nonfinite_weight = duplicate_doc.replace('"upper_wt_percent":50.0', '"upper_wt_percent":NaN', 1) + "\n"
    variants["nonfinite_weight"] = nonfinite_weight
    nonfinite_coefficient = duplicate_doc.replace('"C14":1.0', '"C14":Infinity', 1) + "\n"
    variants["nonfinite_coefficient"] = nonfinite_coefficient
    overflow_coefficient = duplicate_doc.replace('"C14":1.0', '"C14":1e309', 1) + "\n"
    variants["coefficient_overflow"] = overflow_coefficient
    def invalid_later_component(x):
        later = copy.deepcopy(x["components"][0])
        later["id"] = "later-invalid"
        later["composition_wt_percent_bounds"]["Si"]["lower_wt_percent"] = 90.0
        later["composition_wt_percent_bounds"]["Si"]["upper_wt_percent"] = 100.0
        later["composition_wt_percent_bounds"]["Fe"]["lower_wt_percent"] = 50.0
        later["composition_wt_percent_bounds"]["Fe"]["upper_wt_percent"] = 100.0
        x["components"].append(later)
    put("invalid_later_component", invalid_later_component)
    checks = {}
    for name, contents in variants.items():
        in_path, out_path = work / f"reject-{name}.json", work / f"reject-{name}.out.json"
        in_path.write_text(contents, encoding="utf-8")
        out_path.write_text("sentinel\n", encoding="utf-8")
        child = _run_cli([str(ACTINV), "waste", "composition", str(in_path), str(out_path)])
        checks[name] = child.returncode != 0 and out_path.read_text(encoding="utf-8") == "sentinel\n"
    return {"checks": checks, "pass": bool(checks) and all(checks.values())}


def run_g1(no_write: bool = False) -> int:
    cases, fixture_bytes = _fixture_content()
    if not FIXTURE.is_file() or FIXTURE.read_bytes() != fixture_bytes:
        raise RuntimeError("P108 frozen artificial case fixture differs from deterministic generator")
    seal = json.loads(P108_G0.read_text(encoding="utf-8"))
    if seal.get("pass") is not True or seal.get("case_fixture_sha256") != sha256(FIXTURE):
        raise RuntimeError("P108 G0 seal is missing, failed, or not bound to fixture")
    if not ACTINV.is_file():
        raise RuntimeError(f"ACTINV_BIN does not identify release CLI: {ACTINV}")
    rows = json.loads(PACK.read_text(encoding="utf-8"))["rows"]
    spec = {"schema": "actinv-waste-composition-spec-1", "rules": "us-nrc-10cfr61.55-v1",
            "response_model": "fixed_rate_affine_activity", "components": cases}
    text = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    spec["_input_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    work_root = ROOT / "target/p108-g1-controls"
    work_root.mkdir(parents=True, exist_ok=True)
    failures = []
    mutation_results = {}
    with tempfile.TemporaryDirectory(prefix="p108-g1-", dir=work_root) as temp:
        work = Path(temp)
        input_path, output_path, repeat_path = work / "composition.json", work / "out.json", work / "repeat.json"
        input_path.write_text(text, encoding="utf-8")
        first = _run_cli([str(ACTINV), "waste", "composition", str(input_path), str(output_path)])
        if first.returncode != 0:
            raise RuntimeError(f"P108 G1 CLI failed ({first.returncode}): {first.stderr[-2500:]}")
        output = json.loads(output_path.read_text(encoding="utf-8"))
        failures.extend(oracle.verify_report(output, spec, rows))
        second = _run_cli([str(ACTINV), "waste", "composition", str(input_path), str(repeat_path)])
        repeated = second.returncode == 0 and repeat_path.is_file() and repeat_path.read_bytes() == output_path.read_bytes()
        if not repeated:
            failures.append("separate-output repeated run is not byte-identical")

        def component(result, case_id):
            return next(x for x in result["components"] if x["id"] == case_id)

        def target(result, case_id, step=1):
            return next(x for x in component(result, case_id)["targets"] if x["step"] == step)

        def reject_mutation(name, mutation):
            changed = copy.deepcopy(output)
            mutation(changed)
            mutation_results[name] = bool(oracle.verify_report(changed, spec, rows))

        reject_mutation("response_coefficient", lambda x: target(x,"correlated_two")["element_activity_bq_per_g"]["Nb"].__setitem__("C14", 1.0))
        reject_mutation("composition_bounds", lambda x: component(x,"correlated_two")["composition_wt_percent_bounds"]["Nb"].__setitem__("upper_wt_percent", 50.0))
        reject_mutation("sum_equality", lambda x: component(x,"correlated_two")["composition_sum_constraint"].__setitem__("value_wt_percent", 99.0))
        reject_mutation("projection_endpoint", lambda x: target(x,"correlated_two")["projection_records"]["C14"].__setitem__("upper_bq", 1.0))
        reject_mutation("projection_witness", lambda x: target(x,"correlated_two")["projection_records"]["C14"]["max_witness_wt_percent"].__setitem__("Nb", 99.0))
        reject_mutation("dual_multiplier", lambda x: target(x,"correlated_two")["projection_records"]["C14"].__setitem__("upper_dual_lambda", 1e30))
        def alternate_tie_witness(x):
            record = target(x,"coefficient_tie")["projection_records"]["C14"]
            record["min_witness_wt_percent"] = {"Fe": 0.0, "Si": 100.0}
            record["max_witness_wt_percent"] = {"Fe": 0.0, "Si": 100.0}
        reject_mutation("alternate_optimal_tie_witness", alternate_tie_witness)
        reject_mutation("geometry", lambda x: component(x,"correlated_two").__setitem__("mass_g", 2.0))
        reject_mutation("input_sha", lambda x: x.__setitem__("input_sha256", "0" * 64))
        reject_mutation("projected_input_sha", lambda x: x.__setitem__("projected_input_sha256", "0" * 64))
        reject_mutation("rule_identity", lambda x: x["rules"].__setitem__("sha256", "0" * 64))
        reject_mutation("target_time", lambda x: target(x,"two_times",2).__setitem__("t_s",101.0))
        reject_mutation("endpoint_class", lambda x: target(x,"single_fixed")["lower"].__setitem__("class","C"))
        reject_mutation("row_fraction", lambda x: target(x,"correlated_two")["lower"]["row_fractions"][0].__setitem__("fraction", 9.0))
        reject_mutation("constraint_sum", lambda x: target(x,"correlated_two")["upper"]["constraints"][0].__setitem__("normalized_sum", 9.0))
        reject_mutation("class_envelope", lambda x: target(x,"correlated_two").__setitem__("class_envelope", ["A"]))
        reject_mutation("stability", lambda x: target(x,"single_fixed").__setitem__("class_is_stable", False))
        reject_mutation("coverage", lambda x: target(x,"incomplete").__setitem__("inventory_coverage", "complete"))
        reject_mutation("source", lambda x: target(x,"single_fixed").__setitem__("bounds_source", "changed"))
        reject_mutation("property", lambda x: component(x,"single_fixed")["nuclide_properties"]["C14"].__setitem__("z", 1))
        def alter_external_h3(x):
            h3 = target(x,"external_merge")["external_tritium_activity_bounds_bq"]
            h3["upper_bq"] += 1.0
        reject_mutation("external_h3", alter_external_h3)
        if not mutation_results or not all(mutation_results.values()):
            failures.append("one or more P108 report mutations were not rejected")
        refusals = _refusal_controls(work)
        if refusals.get("pass") is not True:
            failures.append("one or more invalid composition specs were accepted or output sentinel changed")
        report = {"schema": "actinv-p108-composition-g1-1", "protocol_sha256": PROTOCOL_SHA256,
                  "case_count": len(cases), "target_count": sum(len(c["targets"]) for c in cases),
                  "endpoint_count": 2 * sum(len(c["targets"]) for c in cases),
                  "independent_comparison_count": len(cases),
                  "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                  "repeat_byte_identical": repeated, "mutations_rejected": mutation_results,
                  "refusal_controls": refusals, "failures": failures, "pass": not failures}
    return _persist(report, P108_G1, no_write)


def _persist(report: dict, path: Path, no_write: bool) -> int:
    if no_write:
        try:
            persisted = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 1
        return 0 if persisted == report and report.get("pass") is True else 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("pass") is True else 1


def run_g2(no_write: bool = False, *, g1_pass: bool) -> int:
    try:
        g1 = json.loads(P108_G1.read_text(encoding="utf-8"))
        g1_sha = sha256(P108_G1)
    except (OSError, json.JSONDecodeError) as error:
        g1, g1_sha = {}, None
        failure = str(error)
    else:
        failure = None
    passed = (g1_pass and g1.get("schema") == "actinv-p108-composition-g1-1"
              and g1.get("pass") is True and g1.get("protocol_sha256") == PROTOCOL_SHA256
              and g1.get("repeat_byte_identical") is True)
    report = {"schema": "actinv-p108-composition-g2-1", "phase": "P108",
              "protocol_sha256": PROTOCOL_SHA256, "g1_result_sha256": g1_sha,
              "separate_output_paths_byte_identical": g1.get("repeat_byte_identical") is True,
              "failures": [] if passed else [failure or "G1 replay or deterministic-output check failed"],
              "pass": passed}
    return _persist(report, P108_G2, no_write)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seal", action="store_true", help="freeze G0 before production CLI execution")
    parser.add_argument("--g0-only", action="store_true")
    parser.add_argument("--g1-only", action="store_true")
    parser.add_argument("--g2-only", action="store_true")
    parser.add_argument("--no-write", action="store_true", help="replay against persisted stable evidence")
    args = parser.parse_args()
    if sum((args.g0_only, args.g1_only, args.g2_only)) > 1:
        parser.error("--g0-only, --g1-only, and --g2-only are mutually exclusive")
    if args.seal:
        if args.no_write or args.g1_only or args.g2_only:
            parser.error("--seal is a G0-only write operation")
        raise SystemExit(g0(seal=True))
    if g0(no_write=True) != 0:
        raise SystemExit(1)
    if args.g0_only:
        raise SystemExit(0)
    if args.g1_only:
        raise SystemExit(run_g1(no_write=args.no_write))
    if args.g2_only:
        g1_status = run_g1(no_write=True)
        if g1_status != 0:
            raise SystemExit(1)
        raise SystemExit(run_g2(no_write=args.no_write, g1_pass=True))
    g1_status = run_g1(no_write=args.no_write)
    if g1_status != 0:
        raise SystemExit(1)
    raise SystemExit(run_g2(no_write=args.no_write, g1_pass=True))


if __name__ == "__main__":
    raise SystemExit(main())
