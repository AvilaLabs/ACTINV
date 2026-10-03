#!/usr/bin/env python3
"""Independent synthetic activation and class-budget controls for P105 G2/G3.

The fixture is deliberately tiny and artificial. It is generated under target
and never represents evaluated nuclear data.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import subprocess
import copy
import zipfile
from pathlib import Path


def _npy(descr: str, shape: tuple[int, ...], payload: bytes) -> bytes:
    shape_text = "(" + ", ".join(map(str, shape)) + ("," if len(shape) == 1 else "") + ")"
    header = ("{'descr': '" + descr + "', 'fortran_order': False, 'shape': " + shape_text + ", }").encode()
    padding = (16 - ((10 + len(header) + 1) % 16)) % 16
    header += b" " * padding + b"\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header + payload


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def _endf_record(values: list[float | int], mat: int, mf: int, mt: int, seq: int) -> str:
    fields = "".join(f"{int(v):11d}" if isinstance(v, int) else f"{float(v):11.4E}" for v in values)
    return fields + f"{mat:4d}{mf:2d}{mt:3d}{seq:5d}"


def _write_synthetic_decay(path: Path) -> None:
    # Minimal MF=8/MT=457 records: stable Fe-56 and Nb-93 plus artificial
    # beta-minus Nb-94; its 1e11 s half-life is a P105 control parameter.
    records: list[str] = []
    for mat, za, awr, stable, half_life, mode in (
        (2601, 26054, 53.939, 1, 0.0, 0),
        (2602, 26056, 55.454, 1, 0.0, 0),
        (2603, 26057, 56.453, 1, 0.0, 0),
        (2604, 26058, 57.933, 1, 0.0, 0),
        (4101, 41093, 92.906, 1, 0.0, 0),
        (4102, 41094, 93.907, 0, 1.0e11, 6),
        (4201, 42094, 93.906, 1, 0.0, 0),
    ):
        seq = 1
        records.append(_endf_record([za, awr, 0, 0, stable, 0], mat, 8, 457, seq)); seq += 1
        records.append(_endf_record([half_life, 0.0, 0, 0, mode, 0], mat, 8, 457, seq)); seq += 1
        if not stable:
            # Six energy slots: light, uncertainty, EM, uncertainty, heavy, uncertainty.
            records.append(_endf_record([0.0, 0.0, 0.0, 0.0, 0.0, 0.0], mat, 8, 457, seq)); seq += 1
        records.append(_endf_record([0.0] * 6, mat, 8, 457, seq)); seq += 1
        records.append(_endf_record([0.0] * 6, mat, 8, 0, seq))
    path.write_text("\n".join(records) + "\n")


def _run(argv: list[str], cwd: Path, *, timeout_s: float = 120) -> subprocess.CompletedProcess[str]:
    """Bounded child invocation with explicit terminate/kill/reap on timeout."""
    child = subprocess.Popen(argv, cwd=cwd, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = child.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            child.terminate()
        except ProcessLookupError:
            pass
        try:
            child.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                child.kill()
            except ProcessLookupError:
                pass
            try:
                child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                child.wait(timeout=3)
                if child.stdout is not None:
                    child.stdout.close()
                if child.stderr is not None:
                    child.stderr.close()
        raise RuntimeError(f"command timed out after {timeout_s}s: {argv[1:3]}")
    return subprocess.CompletedProcess(argv, child.returncode, out, err)


def _make_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    work = root / "target" / "p105-g2-controls"
    work.mkdir(parents=True, exist_ok=True)
    lib = work / "nb_capture.npz"
    rows = _npy("<i8", (2, 5), struct.pack("<10q", 0, 102, -1, -1, 0,
                                                   0, 102, 41094, 0, 3))
    sig = _npy("<f8", (2, 1), struct.pack("<2d", 1.0, 1.0))
    bounds = _npy("<f8", (2,), struct.pack("<2d", 1.0, 2.0))
    with zipfile.ZipFile(lib, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in (("rows.npy", rows), ("sig.npy", sig), ("bounds.npy", bounds)):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    lib_bytes = lib.read_bytes()
    boundaries_sha = _sha(b"ACTINV-GROUP-BOUNDARIES-v1\0" + struct.pack("<2d", 1.0, 2.0))
    index = {
        "schema": "actinv-library-index-1", "sha256_npz": _sha(lib_bytes),
        "group_boundary_sha256": boundaries_sha, "groups": "custom",
        "temperature_K": 293.6, "projectile": "neutron",
        "targets": [{"file": "p105-synthetic.endf", "source_sha256": "1" * 64,
                     "mat": 4101, "za": 41093, "liso": 0, "awr": 92.906,
                     "ledger": []}],
    }
    _write_json(work / "nb_capture_index.json", index)
    decay_path = work / "synthetic_decay.endf"
    _write_synthetic_decay(decay_path)
    base = {
        "spec": "actinv-spec-1", "title": "P105 synthetic Nb capture",
        "projectile": "neutron", "library": {"path": str(lib), "sha256": _sha(lib_bytes)},
        "decay": {"primary": str(decay_path)},
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"Fe": 100.0}},
        "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0],
                     "flux_per_group": [1.0e12], "total": 1.0e12, "descending": False},
        "schedule": [{"dt": "1e6 s", "flux": 1.0}, {"dt": "1e6 s", "flux": 0.0}],
        "options": {"mode": "coupled", "prune": "reach", "bmin_atoms_per_g": 0.0,
                    "temperature_K": 293.6},
    }
    base_path = work / "base_run.json"
    _write_json(base_path, base)
    budget_path = work / "budget.json"
    budget = {
        "schema": "actinv-waste-budget-1", "base_spec": str(base_path),
        "balance": "Fe", "matrix": {}, "impurities": {"Nb": 0.01},
        "targets": [1, 2], "rules": "us-nrc-10cfr61.55-v1",
        "target_class": "C", "mass_g": 1.0, "displaced_volume_cm3": 1.0 / 7.8,
        "waste_type": "activated_metal",
        "nuclide_properties": {"Nb94": {"z": 41, "half_life_s": 1e11, "alpha_emitting": False}},
        "external_tritium": {"status": "not_applicable"},
    }
    _write_json(budget_path, budget)
    return work, base_path, budget_path, decay_path


def _close(a: float, b: float, rel: float = 1e-6, abs_: float = 1e-12) -> bool:
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


def _activity_map(target: dict) -> dict[str, float]:
    inventory = target.get("verification_inventory_activity_bq")
    if not isinstance(inventory, dict):
        raise ValueError("budget target has no full verification inventory")
    return {str(k): float(v) for k, v in inventory.items()}


def _binding_matches(actual: object, expected: dict) -> bool:
    if not isinstance(actual, dict):
        return expected.get("step") is None and actual is None
    return (
        actual.get("step") == expected.get("step")
        and actual.get("table") == expected.get("table")
        and actual.get("column") == expected.get("column")
        and actual.get("contributor_count") == expected.get("contributor_count")
        and actual.get("strict") == expected.get("strict")
        and actual.get("nuclides") == expected.get("contributors")
    )


def _validate_row_fractions(actual_list: object, expected_rows: list[dict]) -> list[str]:
    errors = []
    if not isinstance(actual_list, list):
        return ["row_fractions_type"]
    if len(actual_list) != len(expected_rows):
        errors.append("row_fractions_count")
    def key(row: dict) -> tuple:
        return (row.get("table"), row.get("column"), row.get("row_id"), row.get("nuclide"))
    expected = {key(row): row for row in expected_rows}
    actual = {key(row): row for row in actual_list}
    if len(expected) != len(expected_rows) or len(actual) != len(actual_list) or expected.keys() != actual.keys():
        return errors + ["row_fractions_keys"]
    for row_key, want in expected.items():
        got = actual[row_key]
        for field in ("table", "column", "row_id", "nuclide", "unit"):
            if got.get(field) != want.get(field):
                errors.append(f"row_fraction_{field}")
        for field in ("concentration", "limit", "fraction"):
            expected_value = want.get(field)
            actual_value = got.get(field)
            if expected_value is None:
                if actual_value is not None:
                    errors.append(f"row_fraction_{field}")
            elif actual_value is None or not _close(float(actual_value), float(expected_value)):
                errors.append(f"row_fraction_{field}")
    return errors


def _constraint_records_match(actual_list: object, expected_list: list[dict]) -> bool:
    if not isinstance(actual_list, list) or len(actual_list) != len(expected_list):
        return False
    key = lambda x: (x.get("target_class"), x.get("table"), x.get("column"))
    expected = {key(item): item for item in expected_list}
    actual = {key(item): item for item in actual_list}
    if len(expected) != len(expected_list) or len(actual) != len(actual_list) or expected.keys() != actual.keys():
        return False
    for item_key, want in expected.items():
        got = actual[item_key]
        for field in ("target_class", "table", "column", "strict", "passes", "contributor_count", "contributors"):
            if got.get(field) != want.get(field):
                return False
        for field in ("source_sum_fraction", "normalized_sum", "normalized_margin"):
            if not _close(float(got.get(field, math.nan)), float(want.get(field, math.nan))):
                return False
    return True


def _validate_target(target: dict, rows: list[dict], props: dict, volume: float,
                     *, require_class_pass: bool = True) -> tuple[list[str], dict]:
    errors: list[str] = []
    activity = _activity_map(target)
    case = {"activity_Bq_per_g": activity, "mass_g": 1.0,
            "displaced_volume_cm3": volume, "waste_type": "activated_metal",
            "nuclide_properties": props, "external_tritium": {"status": "not_applicable"}}
    independent = _independent(case, rows)
    if independent["class"] != target.get("class"):
        errors.append("class")
    errors.extend(_validate_row_fractions(target.get("row_fractions", []), independent["row_details"]))
    if not _close(float(target.get("external_tritium_activity_bq", math.nan)), 0.0):
        errors.append("external_tritium_activity")
    expected_class_pass = independent["class"] in ("A", "B", "C")
    if target.get("class_passes") is not expected_class_pass:
        errors.append("class_passes")
    items = target.get("constraint_sums", [])
    expected_keys = {(1, None), (2, 3)}
    actual_keys = {(int(item["table"]), item.get("column")) for item in items}
    if len(items) != 2 or actual_keys != expected_keys:
        errors.append("constraint_keys")
    for item in items:
        match = next((x for x in independent["constraints"]
                      if x["target_class"] == "C" and x["table"] == item["table"]
                      and x["column"] == item.get("column")), None)
        if match is None or not _close(float(item.get("solved_normalized_sum", math.nan)), float(match["normalized_sum"])):
            errors.append("constraint_sum")
        predicted = float(item.get("predicted_normalized_sum", math.nan))
        solved = float(item.get("solved_normalized_sum", math.nan))
        if match is not None and not _close(predicted, float(match["normalized_sum"])):
            errors.append("predicted_constraint_sum")
        agrees = _close(predicted, solved)
        if not agrees:
            errors.append("predicted_solved_agreement")
        if item.get("pass") is not agrees:
            errors.append("sum_pass_flag")
    expected_target_pass = all(item.get("pass") is True for item in items)
    if require_class_pass:
        expected_target_pass &= expected_class_pass
    if target.get("pass") is not expected_target_pass:
        errors.append("target_pass")
    if target.get("coverage") != "complete":
        errors.append("coverage")
    return errors, independent


def _independent(case: dict, rows: list[dict]) -> dict:
    # Import is resolved by run_g2_budget after the shared G1 checker is loaded.
    return _CLASS_CHECKER.independent_classification(case, rows)


_CLASS_CHECKER = None


def _g3_waste_identity(binary: Path, root: Path, work: Path, base_path: Path,
                       rows: list[dict], props: dict, volume: float) -> dict:
    """Real small-component run -> waste CLI -> independent arithmetic -> byte repeat."""
    run_spec_doc = json.loads(base_path.read_text())
    run_spec_doc["material"]["composition"] = {"Fe": 99.99, "Nb": 0.01}
    run_spec = work / "g3_component_run.json"
    run_out = work / "g3_component_run.out.json"
    _write_json(run_spec, run_spec_doc)
    solved = _run([str(binary), "run", str(run_spec), str(run_out)], root)
    if solved.returncode:
        raise RuntimeError(f"G3 component activation run failed: {solved.stderr[-2000:]}")

    waste_doc = {
        "schema": "actinv-waste-spec-1", "rules": "us-nrc-10cfr61.55-v1",
        "input": str(run_out), "targets": [1, 2], "nuclide_properties": props,
        "components": [{"id": "p105-nb-component", "mass_g": 1.0,
            "displaced_volume_cm3": volume, "waste_type": "activated_metal",
            "external_tritium": {"status": "not_applicable"}}],
    }
    waste_spec = work / "g3_component_waste.json"
    _write_json(waste_spec, waste_doc)
    first_path = work / "g3_component_waste.first.json"
    second_path = work / "g3_component_waste.repeat.json"
    for output_path in (first_path, second_path):
        output_path.unlink(missing_ok=True)
    first = _run([str(binary), "waste", str(waste_spec), str(first_path)], root)
    if first.returncode:
        raise RuntimeError(f"G3 component waste classification failed: {first.stderr[-2000:]}")
    repeat = _run([str(binary), "waste", str(waste_spec), str(second_path)], root)
    if repeat.returncode:
        raise RuntimeError(f"G3 repeated waste classification failed: {repeat.stderr[-2000:]}")
    deterministic = first_path.read_bytes() == second_path.read_bytes()
    if not deterministic:
        raise RuntimeError("G3 same-spec waste output differs across distinct output paths")

    run_result = json.loads(run_out.read_text())
    waste_result = json.loads(first_path.read_text())
    components = waste_result.get("components", [])
    if len(components) != 1 or components[0].get("id") != "p105-nb-component":
        raise RuntimeError("G3 waste result component identity differs")
    target_results = components[0].get("targets", [])
    classes = []
    for step_no, expected_time in ((1, 1e6), (2, 2e6)):
        run_step = next((s for s in run_result.get("steps", []) if int(s.get("step", -1)) == step_no), None)
        waste_target = next((s for s in target_results if int(s.get("step", -1)) == step_no), None)
        if run_step is None or waste_target is None:
            raise RuntimeError(f"G3 run/waste result lacks target {step_no}")
        if not (_close(float(run_step.get("t_s", math.nan)), expected_time, rel=0.0, abs_=1e-8)
                and _close(float(waste_target.get("t_s", math.nan)), expected_time, rel=0.0, abs_=1e-8)):
            raise RuntimeError(f"G3 run/waste result has wrong target time at step {step_no}")
        source_activity = {str(k): float(v) for k, v in run_step.get("activity_Bq_per_g", {}).items()}
        waste_inventory = {str(k): float(v) for k, v in waste_target.get("inventory_activity_bq", {}).items()}
        if set(source_activity) != set(waste_inventory) or any(
            not _close(source_activity[k], waste_inventory[k]) for k in source_activity
        ):
            raise RuntimeError(f"G3 waste inventory does not match its native run at step {step_no}")
        case = {"activity_Bq_per_g": source_activity, "mass_g": 1.0,
                "displaced_volume_cm3": volume, "waste_type": "activated_metal",
                "nuclide_properties": props, "external_tritium": {"status": "not_applicable"}}
        independent = _independent(case, rows)
        evaluation = waste_target.get("evaluation", {})
        if independent["class"] != waste_target.get("class") or independent["class"] != evaluation.get("class"):
            raise RuntimeError(f"G3 independent class differs at step {step_no}")
        if evaluation.get("coverage") != "complete" or waste_target.get("coverage") != "complete":
            raise RuntimeError(f"G3 component coverage is not complete at step {step_no}")
        if _validate_row_fractions(evaluation.get("row_fractions", []), independent["row_details"]):
            raise RuntimeError(f"G3 row fractions differ independently at step {step_no}")
        if not _constraint_records_match(evaluation.get("constraints"), independent["constraints"]):
            raise RuntimeError(f"G3 class constraint fields differ independently at step {step_no}")
        if not _constraint_records_match(evaluation.get("binding_constraints"), independent["binding_constraints"]):
            raise RuntimeError(f"G3 binding constraint fields differ independently at step {step_no}")
        if (evaluation.get("calculated_only_class") != independent["calculated_only_class"]
                or waste_target.get("calculated_only_class") != independent["calculated_only_class"]
                or not _close(float(waste_target.get("external_tritium_activity_bq", math.nan)), 0.0)):
            raise RuntimeError(f"G3 calculated-only or external H-3 fields differ at step {step_no}")
        classes.append(independent["class"])
    return {"checked": True, "class_by_target": classes,
            "target_times_s": [1e6, 2e6], "repeat_byte_identical": deterministic}


def _algebra_vectors() -> dict:
    # Frozen algebraic references; production behavior is covered by the named
    # Rust regression tests recorded alongside this report, not these values.
    return {
        "negative_slope": {"b": 2.0, "g": -1.0, "cap": 3.0, "expected_lower": 1.0,
                           "expected_upper": 3.0, "expected_status": "limit"},
        "matrix_infeasible": {"b": 1.1, "g": 0.0, "expected_feasible": False,
                              "expected_status": "infeasible"},
        "no_response": {"b": 0.25, "g": 0.0, "cap": 2.0,
                        "expected_no_response": True, "expected_upper": 2.0,
                        "expected_status": "no_response"},
        "composition_bound": {"b": 0.0, "g": -0.1, "cap": 2.0,
                              "expected_upper": 2.0, "expected_status": "composition_bound"},
        "zero_slope_contributor_change": {"b": 1.0, "g": 0.0,
            "contributors_at_zero": 1, "contributors_for_positive_x": 2,
            "expected_interval": [0.0, 0.0], "expected_upper_open": False,
            "rust_regression": "constant_threshold_with_positive_composition_strictness_is_a_closed_zero_singleton"},
        "rust_regressions": [
            "negative_gradient_creates_a_lower_composition_bound",
            "infeasible_constant_and_strict_boundary_are_rejected",
            "constant_threshold_with_positive_composition_strictness_is_a_closed_zero_singleton",
            "no_response_and_positive_balance_cap_keep_distinct_statuses",
        ],
    }


def _expected_nb_upper(output: dict, props: dict, rows: list[dict], volume: float) -> tuple[float, dict]:
    bases = {x["element"]: x for x in output.get("basis_solves", [])}
    keys = []
    for step in (1, 2):
        fs = bases["Fe"]["steps"][str(step)]
        ns = bases["Nb"]["steps"][str(step)]
        def sums(activity: dict) -> dict[tuple[int, int | None], float]:
            case = {"activity_Bq_per_g": activity, "mass_g": 1.0,
                    "displaced_volume_cm3": volume, "waste_type": "activated_metal",
                    "nuclide_properties": props, "external_tritium": {"status": "not_applicable"}}
            c = _independent(case, rows)
            return {(x["table"], x["column"]): float(x["normalized_sum"])
                    for x in c["constraints"] if x["target_class"] == "C"}
        fsums, nsums = sums(fs), sums(ns)
        for key, b in fsums.items():
            g = (nsums.get(key, 0.0) - b) / 100.0
            if g > 0.0:
                edge = (1.0 - b) / g
                if 0.0 <= edge < 100.0:
                    keys.append((edge, step, key, b, g))
    if not keys:
        return 100.0, {"step": None, "table": None, "column": None, "strict": False}
    edge, step, key, base, slope = min(keys, key=lambda x: x[0])
    x = max(0.0, min(100.0, edge))
    composition = {"Fe": 100.0 - x, "Nb": x}
    inventory = {}
    for element, weight in composition.items():
        for name, value in bases[element]["steps"][str(step)].items():
            inventory[name] = inventory.get(name, 0.0) + weight / 100.0 * float(value)
    case = {"activity_Bq_per_g": inventory, "mass_g": 1.0,
            "displaced_volume_cm3": volume, "waste_type": "activated_metal",
            "nuclide_properties": props, "external_tritium": {"status": "not_applicable"}}
    independent = _independent(case, rows)
    rule = next(v for v in independent["constraints"] if v["target_class"] == "C"
                and v["table"] == key[0] and v["column"] == key[1])
    return edge, {"step": step, "table": key[0], "column": key[1],
                  "strict": bool(rule["strict"]), "contributors": rule["contributors"],
                  "contributor_count": rule["contributor_count"], "normalized": rule["normalized_sum"]}


def _validate_emitted_limit(baseline: dict, candidate: dict, label: str,
                            props: dict, rows: list[dict], volume: float) -> bool:
    parts = label.split(".")
    group = parts[1]
    expected, binding = _expected_nb_upper(baseline, props, rows, volume)
    original = baseline["impurities"]["Nb"][group]
    altered = candidate["impurities"]["Nb"][group]
    expected_attained = not binding["strict"] if expected < 100.0 else False
    return (_close(float(original["upper_supremum_wt_pct"]), expected)
            and original.get("supremum_attained") == expected_attained
            and _binding_matches(original.get("binding_constraint"), binding)
            and _close(float(altered["upper_supremum_wt_pct"]), expected)
            and altered.get("supremum_attained") == expected_attained
            and _binding_matches(altered.get("binding_constraint"), binding))


def run_g2_budget(no_write: bool = False) -> dict:
    """Generate frozen G2 inputs and independently validate G2/G3 CLI evidence.

    ``no_write`` requires an exact match with results/g2_p105_budget.json;
    synthetic nuclear inputs and raw CLI outputs stay under target/p105-g2-controls.
    """
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[1]
    checker_path = root / "controls" / "check_p105.py"
    spec = importlib.util.spec_from_file_location("p105_class_checker", checker_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load independent P105 classification checker")
    checker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = checker
    spec.loader.exec_module(checker)
    global _CLASS_CHECKER
    _CLASS_CHECKER = checker
    work, base_path, budget_path, decay_path = _make_fixture(root)
    binary = Path(os.environ.get("ACTINV_BIN", root / "target/release/actinv"))
    if not binary.is_file():
        raise RuntimeError(f"release CLI is missing: {binary}")
    if not decay_path.is_file():
        raise RuntimeError(f"required local decay source is missing: {decay_path}")

    out_path = work / "budget.out.json"
    command = _run([str(binary), "waste", "budget", str(budget_path), str(out_path)], root)
    if command.returncode:
        raise RuntimeError(f"waste budget failed ({command.returncode}): {command.stderr[-3000:]}")
    raw = out_path.read_bytes()
    output = json.loads(raw)
    second = work / "budget.repeat.json"
    repeat = _run([str(binary), "waste", "budget", str(budget_path), str(second)], root)
    if repeat.returncode:
        raise RuntimeError(f"repeat waste budget failed: {repeat.stderr[-3000:]}")
    deterministic = raw == second.read_bytes()
    failures: list[str] = []
    if not deterministic:
        failures.append("budget output is not byte-deterministic")
    verification = output.get("verification", {}).get("points", [])
    if not verification:
        failures.append("budget emitted no full verification points")
    expected_ids = {"at_spec", "sole_Nb_interior", "others_at_spec_Nb_interior", "joint_interior"}
    point_ids = [p.get("id") for p in verification]
    if len(point_ids) != len(set(point_ids)) or set(point_ids) != expected_ids:
        failures.append("verification point ids differ from the frozen four-point set")
    for point in verification:
        targets = point.get("targets", [])
        if [int(t.get("step", -1)) for t in targets] != [1, 2]:
            failures.append(f"{point.get('id')}: target steps are not exactly [1, 2]")
        for target, expected_time in zip(targets, (1e6, 2e6)):
            if not _close(float(target.get("t_s", math.nan)), expected_time, rel=0.0, abs_=1e-8):
                failures.append(f"{point.get('id')} step {target.get('step')}: target time mismatch")

    # Reconstruct each emitted inventory's class and all normalized constraints
    # from the sealed row transcription, independently of the Rust waste core.
    rows = checker.expected_pack_rows()
    props = json.loads(budget_path.read_text())["nuclide_properties"]
    volume = 1.0 / 7.8
    checked_targets = 0
    for point in verification:
        for target in point.get("targets", []):
            errors, independent = _validate_target(
                target, rows, props, volume,
                require_class_pass=point.get("id") != "at_spec",
            )
            failures.extend(f"{point.get('id')} step {target.get('step')}: independent check {e}" for e in errors)
            if point.get("id") != "at_spec":
                if independent["class"] == "above_class_c" or not target.get("class_passes"):
                    failures.append(f"{point.get('id')} step {target.get('step')}: independent class exceeds C")
            checked_targets += 1
    expected_verified = all(
        target.get("pass") is True
        for point in verification for target in point.get("targets", [])
    )
    if not expected_verified or output.get("verification", {}).get("verified") is not True:
        failures.append("verification summary does not report all targets as passing")

    # Basis superposition: reconstruct each verification point per nuclide from
    # the pure-element basis inventories at its full composition.
    bases = {x["element"]: x for x in output.get("basis_solves", [])}
    if not {"Fe", "Nb"}.issubset(bases):
        failures.append("Fe/Nb basis solves missing")
    else:
        expected_times = {1: 1.0e6, 2: 2.0e6}
        for element, basis_record in bases.items():
            for step, time_s in expected_times.items():
                actual_time = float(basis_record.get("times_s", {}).get(str(step), math.nan))
                if not _close(actual_time, time_s, rel=0.0, abs_=1e-8):
                    failures.append(f"{element} basis step {step}: target time mismatch")
        for point in verification:
            composition = point.get("composition_wt_pct", {})
            for target in point.get("targets", []):
                step = str(target.get("step"))
                expected: dict[str, float] = {}
                for element, weight in composition.items():
                    basis_steps = bases[element].get("steps", {})
                    for nuclide, per_g in basis_steps.get(step, {}).items():
                        expected[nuclide] = expected.get(nuclide, 0.0) + float(weight) / 100.0 * float(per_g)
                actual = _activity_map(target)
                for nuclide in set(expected) | set(actual):
                    if not _close(expected.get(nuclide, 0.0), actual.get(nuclide, 0.0)):
                        failures.append(f"{point.get('id')} step {step}: basis linearity failed for {nuclide}")

    # Independently assess the emitted usable upper limits and joint point against
    # all targets, using the verification inventories as ground truth.
    budget_rows = output.get("impurities", {}).get("Nb", {})
    usable: list[tuple[str, dict]] = []
    expected_interval_groups = {"sole", "others_at_spec"}
    if not isinstance(budget_rows, dict) or not expected_interval_groups.issubset(budget_rows):
        failures.append("budget output is missing a required Nb interval group")
    for group in ("sole", "others_at_spec"):
        item = budget_rows.get(group, {})
        if not isinstance(item, dict):
            failures.append(f"Nb.{group}: interval is missing")
        elif item.get("feasible") is not True or item.get("verified_value") is None:
            failures.append(f"Nb.{group}: interval is not feasible and verified")
        else:
            usable.append((f"Nb.{group}.", item))
    if {label.split(".")[1] for label, _ in usable} != expected_interval_groups:
        failures.append("not all expected Nb intervals are independently checkable")
    for label, interval in usable:
        group = label.split(".")[1]
        expected_id = "sole_Nb_interior" if group == "sole" else "others_at_spec_Nb_interior"
        if interval.get("verification_point_id") != expected_id:
            failures.append(f"{label}: linked point id differs from frozen contract")
        lower = interval.get("lower_wt_pct", math.nan)
        upper = interval.get("upper_supremum_wt_pct", math.inf)
        if not _close(float(lower), 0.0) or interval.get("lower_open") is not False:
            failures.append(f"{label}: lower bound is not zero and closed")
        if lower > upper:
            failures.append(f"{label}: invalid interval bounds")
        binding = interval.get("binding_constraint") or {}
        if binding.get("strict") and interval.get("supremum_attained"):
            failures.append(f"{label}: open upper edge falsely marked attained")
        expected_upper, expected_binding = _expected_nb_upper(output, props, rows, volume)
        if not _close(float(upper), expected_upper):
            failures.append(f"{label}: interval endpoint differs from independent intersection")
        expected_attained = (not expected_binding["strict"]) if expected_upper < 100.0 else False
        if interval.get("supremum_attained") is not expected_attained:
            failures.append(f"{label}: upper attainment differs from independent strictness")
        if interval.get("status") != "limit" or interval.get("no_response") is not False:
            failures.append(f"{label}: finite impurity bound has an unexpected status")
        if expected_binding.get("step") is not None and (
            binding.get("step") != expected_binding["step"]
            or binding.get("table") != expected_binding["table"]
            or binding.get("column") != expected_binding["column"]
            or binding.get("contributor_count") != expected_binding["contributor_count"]
            or binding.get("strict") != expected_binding["strict"]
            or binding.get("nuclides") != expected_binding["contributors"]
        ):
            failures.append(f"{label}: binding constraint/contributor metadata differs")
        point_id = interval.get("verification_point_id")
        verified_point = next((p for p in verification if p.get("id") == point_id), None)
        if verified_point is None:
            failures.append(f"{label}: verification point reference is unresolved")
        else:
            value = float(verified_point.get("composition_wt_pct", {}).get("Nb", math.nan))
            if not _close(float(interval.get("verified_value", math.nan)), value):
                failures.append(f"{label}: verified_value differs from linked Nb composition")
            if not (value <= upper and value >= lower):
                failures.append(f"{label}: verified composition is outside its reported interval")
            if not all(_validate_target(t, rows, props, volume)[1]["class"] != "above_class_c"
                       and t.get("class_passes") is True
                       for t in verified_point.get("targets", [])):
                failures.append(f"{label}: interior point failed a target")
    joint = output.get("joint_specification_margin", {})
    joint_usable = isinstance(joint, dict) and joint.get("feasible") is True and joint.get("verified_value") is not None
    if not joint_usable:
        failures.append("joint specification margin is missing, infeasible, or unverified")
    if joint_usable:
        if joint.get("verification_point_id") != "joint_interior":
            failures.append("joint margin point id differs from frozen contract")
        point = next((p for p in verification if p.get("id") == "joint_interior"), None)
        joint_upper = float(joint.get("upper_supremum_scale_factor", math.nan))
        impurity_upper = float(budget_rows.get("others_at_spec", {}).get(
            "upper_supremum_wt_pct", math.nan))
        if not _close(joint_upper, impurity_upper / 0.01):
            failures.append("joint upper scale does not equal the Nb upper limit divided by its specification")
        expected_upper, expected_binding = _expected_nb_upper(output, props, rows, volume)
        joint_attained = (not expected_binding["strict"]) if expected_upper < 100.0 else False
        if joint.get("supremum_attained") is not joint_attained:
            failures.append("joint upper attainment differs from independent strictness")
        if joint.get("status") != "limit" or joint.get("no_response") is not False:
            failures.append("joint finite margin has an unexpected status")
        if not _close(float(joint.get("lower_scale_factor", math.nan)), 0.0) or joint.get("lower_open") is not False:
            failures.append("joint lower bound is not zero and closed")
        if point is None:
            failures.append("joint margin verification point does not resolve")
        else:
            if not _close(float(joint.get("verified_value", math.nan)), float(point.get("scale_factor", math.nan))):
                failures.append("joint verified_value differs from linked scale factor")
            if not _close(float(point.get("composition_wt_pct", {}).get("Nb", math.nan)),
                          float(point.get("scale_factor", math.nan)) * 0.01):
                failures.append("joint point Nb composition differs from specification-scaled value")
            if not all(_validate_target(t, rows, props, volume)[1]["class"] != "above_class_c"
                       for t in point.get("targets", [])):
                failures.append("joint margin point exceeds class C independently")
        if point is None or not all(t.get("pass") is True for t in point.get("targets", [])):
            failures.append("joint margin verification point does not pass every target")
        joint_binding = joint.get("binding_constraint") or {}
        if usable:
            _, expected_binding = _expected_nb_upper(output, props, rows, volume)
            if expected_binding.get("step") is not None and not _binding_matches(joint_binding, expected_binding):
                failures.append("joint binding constraint metadata differs from the independent Nb edge")

    # Full fresh solve immediately above the computed Nb limit must classify
    # above Class C independently at both frozen target times.
    above_result = {"checked": False, "composition_wt_pct": None, "classes": []}
    if usable:
        upper = float(budget_rows["others_at_spec"]["upper_supremum_wt_pct"])
        if upper >= 100.0:
            failures.append("no positive-composition headroom exists for the above-C perturbation")
        else:
            above_nb = min(100.0 - 1e-9, upper + max(abs(upper) * 1e-4, 1e-6))
            changed = json.loads(base_path.read_text())
            changed["material"]["composition"] = {"Fe": 100.0 - above_nb, "Nb": above_nb}
            above_spec = work / "above_limit_run.json"
            above_out = work / "above_limit_run.out.json"
            _write_json(above_spec, changed)
            result = _run([str(binary), "run", str(above_spec), str(above_out)], root)
            if result.returncode:
                failures.append(f"above-limit activation solve failed: {result.stderr[-2000:]}")
            else:
                run_output = json.loads(above_out.read_text())
                steps = run_output.get("steps", [])
                observed = []
                for step_number, expected_time in ((1, 1e6), (2, 2e6)):
                    step = next((s for s in steps if int(s.get("step", -1)) == step_number), None)
                    if step is None or not _close(float(step.get("t_s", math.nan)), expected_time, rel=0.0, abs_=1e-8):
                        failures.append(f"above-limit run missing target step/time {step_number}")
                        continue
                    case = {"activity_Bq_per_g": step.get("activity_Bq_per_g", {}), "mass_g": 1.0,
                            "displaced_volume_cm3": volume, "waste_type": "activated_metal",
                            "nuclide_properties": props, "external_tritium": {"status": "not_applicable"}}
                    classification = _independent(case, rows)["class"]
                    observed.append(classification)
                    if classification != "above_class_c":
                        failures.append(f"above-limit independent classification at step {step_number} is {classification}")
                above_result = {"checked": len(observed) == 2 and all(x == "above_class_c" for x in observed),
                                "composition_wt_pct": above_nb, "classes": observed}

    # Separate end-to-end proof for the nominal small component: native run
    # result -> real actinv waste output -> independent source arithmetic, then
    # same-spec repeat at a distinct output path.
    g3_waste = _g3_waste_identity(binary, root, work, base_path, rows, props, volume)

    # Planted mutations must be detected by the independent comparisons.
    mutation_controls = {
        "inventory_rejected": False, "sum_rejected": False, "limit_rejected": False,
        "row_limit_rejected": False, "binding_contributors_rejected": False,
    }
    if verification and verification[0].get("targets"):
        original_target = verification[0]["targets"][0]
        candidate = copy.deepcopy(original_target)
        inventory = candidate["verification_inventory_activity_bq"]
        if inventory:
            name = next(iter(inventory))
            inventory[name] = float(inventory[name]) + max(abs(float(inventory[name])) * 0.05, 1.0)
            mutation_controls["inventory_rejected"] = bool(_validate_target(candidate, rows, props, volume)[0])
        candidate = copy.deepcopy(original_target)
        if candidate.get("constraint_sums"):
            candidate["constraint_sums"][0]["solved_normalized_sum"] += 0.05
            mutation_controls["sum_rejected"] = bool(_validate_target(candidate, rows, props, volume)[0])
        candidate = copy.deepcopy(original_target)
        finite_row = next((row for row in candidate.get("row_fractions", []) if row.get("limit") is not None), None)
        if finite_row is not None:
            finite_row["limit"] = float(finite_row["limit"]) + 1.0
            mutation_controls["row_limit_rejected"] = bool(_validate_target(candidate, rows, props, volume)[0])
    if usable:
        altered_output = copy.deepcopy(output)
        group = usable[0][0].split(".")[1]
        location = altered_output["impurities"]["Nb"][group]
        original = float(location["upper_supremum_wt_pct"])
        location["upper_supremum_wt_pct"] = original + max(abs(original) * 0.05, 1e-8)
        mutation_controls["limit_rejected"] = not _validate_emitted_limit(
            output, altered_output, usable[0][0], props, rows, volume)
        altered_output = copy.deepcopy(output)
        contributor_binding = altered_output["impurities"]["Nb"][group].get("binding_constraint")
        if isinstance(contributor_binding, dict) and contributor_binding.get("nuclides"):
            contributor_binding["nuclides"][0] = "P105-PLANTED-INVALID"
            mutation_controls["binding_contributors_rejected"] = not _validate_emitted_limit(
                output, altered_output, usable[0][0], props, rows, volume)

    # Closed-form Nb-93 capture / Nb-94 decay prediction, independently using
    # the frozen 1 barn, 1e12 n cm-2 s-1 and one million second irradiation.
    avogadro, molar_mass, sigma_b, flux = 6.02214076e23, 92.90637317, 1.0e-24, 1.0e12
    capture_rate, lam, irradiation_s, cooling_s = sigma_b * flux, math.log(2.0) / 1e11, 1e6, 1e6
    n0 = avogadro / molar_mass
    delta = lam - capture_rate
    produced = n0 * capture_rate / delta * math.exp(-capture_rate * irradiation_s) * (-math.expm1(-delta * irradiation_s))
    analytic = {"step1_Bq_per_g": lam * produced,
                "step2_Bq_per_g": lam * produced * math.exp(-lam * cooling_s)}
    analytic_ok = True
    if "Nb" in bases:
        analytic["step1_actual_Bq_per_g"] = float(bases["Nb"]["steps"]["1"].get("Nb94", 0.0))
        analytic["step2_actual_Bq_per_g"] = float(bases["Nb"]["steps"]["2"].get("Nb94", 0.0))
        for step, key in ((1, "step1_Bq_per_g"), (2, "step2_Bq_per_g")):
            actual_nb94 = float(bases["Nb"]["steps"][str(step)].get("Nb94", 0.0))
            analytic_ok &= _close(actual_nb94, analytic[key], rel=1e-6, abs_=1e-12)
    else:
        analytic_ok = False
    if not analytic_ok:
        failures.append("analytic Nb93 capture/Nb94 decay prediction differs")
    if not all(mutation_controls.values()):
        failures.append("one or more planted mutation controls were not sensitive")

    algebra = _algebra_vectors()
    refusal_controls = {}
    # These controls verify fail-closed behavior from copied documents. Each has
    # an isolated output path, which must remain absent on refusal.
    original_doc = json.loads(budget_path.read_text())
    variants = {
        "nonlinear": lambda d: None,
        "unknown_metadata": lambda d: d.update({"nuclide_properties": {}}),
        "required_h3": lambda d: d.update({"external_tritium": {"status": "required"}}),
    }
    for label, mutate in variants.items():
        doc = json.loads(json.dumps(original_doc))
        mutate(doc)
        # Embed a modified base spec for the nonlinear refusal variant.
        if label == "nonlinear":
            bd = json.loads(base_path.read_text())
            bd["self_shielding"] = {"unsupported": True}
            altered = work / "nonlinear_base.json"
            _write_json(altered, bd)
            doc["base_spec"] = str(altered)
        candidate = work / f"refuse_{label}.json"
        _write_json(candidate, doc)
        refused_out = work / f"refuse_{label}.out.json"
        refused_out.unlink(missing_ok=True)
        result = _run([str(binary), "waste", "budget", str(candidate), str(refused_out)], root)
        refusal_controls[label] = result.returncode != 0 and not refused_out.exists()
        if not refusal_controls[label]:
            failures.append(f"CLI did not refuse {label} budget")

    interval_summary = [{"limit": label, "lower_wt_pct": interval.get("lower_wt_pct"),
                         "upper_supremum_wt_pct": interval.get("upper_supremum_wt_pct"),
                         "supremum_attained": interval.get("supremum_attained"),
                         "verified_value_wt_pct": interval.get("verified_value"),
                         "binding_constraint": interval.get("binding_constraint")}
                        for label, interval in usable]
    joint_summary = {key: joint.get(key) for key in (
        "status", "lower_scale_factor", "upper_supremum_scale_factor", "supremum_attained",
        "verified_value", "binding_constraint")}
    report = {"schema": "actinv-p105-g2-budget-control-1", "pass": not failures,
              "failures": failures, "deterministic": deterministic,
              "verified_target_count": checked_targets, "verified_point_count": len(verification),
              "usable_interval_count": len(usable), "usable_intervals": interval_summary,
              "joint_margin": joint_summary, "mutation_controls": mutation_controls,
              "refusal_controls": refusal_controls, "algebra": algebra,
              "required_rust_regressions": algebra["rust_regressions"],
              "rust_regressions_executed_by_control": False,
              "analytic_nb94": {**analytic, "passed": analytic_ok},
              "g3_waste_identity": g3_waste,
              "above_class_c_perturbation": above_result,
              "fixture": "target/p105-g2-controls"}
    result_path = root / "results" / "g2_p105_budget.json"
    if no_write:
        if not result_path.is_file():
            report["pass"] = False
            report["failures"].append("committed G2 result is missing")
        else:
            committed = json.loads(result_path.read_text())
            if committed != report:
                report["pass"] = False
                report["failures"].append("recomputed G2 result differs from committed result")
    else:
        _write_json(result_path, report)
    return report
