"""Independent synthetic P113 twin-waste cases and nominal-class oracle.

All activities and uncertainty bands are artificial controls. Classification
arithmetic is independently delegated to the frozen P105 oracle; this module
only performs mesh mass aggregation and verifies the wrapper contract.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import check_p105
import p113_legacy_control as legacy_control

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"
ACTIVITY_BASIS = "original_mesh_activity_Bq_per_g"
ASSAY_ADJUSTMENT = "not_applied"


def _props(names: set[str]) -> dict:
    z = {"C14": 6, "H3": 1, "Co60": 27, "Ni59": 28, "Ni63": 28, "Tc99": 43,
         "Nb94": 41, "Cs137": 55, "Pu238": 94, "Pu239": 94, "Pu240": 94,
         "Pu242": 94, "Am241": 95, "Am243": 95, "Cm243": 96, "Cm244": 96,
         "Xe135": 54}
    short = {"H3": 3.887e8, "Co60": 1.663e8, "Ni63": 3.15e9,
             "C14": 1.808e11, "Nb94": 6.4e11, "Xe135": 3.3e4}
    return {name: {"z": z[name], "half_life_s": short.get(name, 2.0e9),
                   "alpha_emitting": name.startswith(("Pu", "Am", "Cm"))}
            for name in sorted(names)}


def _steps(inventory0: dict[str, float], inventory1: dict[str, float]) -> list[dict]:
    return [
        {"step": 1, "t_s": 0.0, "activity_Bq_per_g": copy.deepcopy(inventory0),
         "uncertainty": {"responses": {"heat.total": {
             "nominal": 1.0, "combined_standard_uncertainty": 0.1,
             "normal_multiplier": 1.959964, "conservative_interval": [0.8, 1.2]}}}},
        {"step": 2, "t_s": 86400.0, "activity_Bq_per_g": copy.deepcopy(inventory1),
         "uncertainty": {"responses": {"heat.total": {
             "nominal": 1.0, "combined_standard_uncertainty": 0.1,
             "normal_multiplier": 1.959964, "conservative_interval": [0.8, 1.2]}}}},
    ]


def _known_subset_classification(case: dict, rows: list[dict]) -> dict:
    """Apply the frozen P105 arithmetic only to active, property-known keys.

    The P105 oracle assumes its caller has already established complete
    nuclide metadata and can otherwise assign a dedicated row from a name
    alone. The twin contract instead excludes positive missing-property
    nuclides from arithmetic and downgrades the result to Unknown.
    """
    activity = case["activity_Bq_per_g"]
    properties = case["nuclide_properties"]
    unknown = sorted(name for name, value in activity.items()
                     if value > 0.0 and name not in properties)
    known_case = {**case, "activity_Bq_per_g": {
        name: value for name, value in activity.items()
        if value <= 0.0 or name in properties}}
    result = check_p105.independent_classification(known_case, rows)
    if unknown:
        result["unknown_nuclides"] = unknown
        result["coverage"] = "incomplete"
        result["evaluation_class"] = "unknown"
        result["class"] = "unknown"
        result["unknown_reason"] = ("required external H-3 not declared"
            if case.get("external_tritium", {}).get("status") == "required" else unknown)
    return result


def _mesh(cell_steps: dict[str, dict[str, dict[str, float]]]) -> str:
    # Exactly four cells, two selected steps, and reproducible field order.
    records = [{"record": "header", "schema": "actinv-mesh-result-1", "cell_count": 4}]
    for ordinal, cell in enumerate(("cell-0", "cell-1", "cell-2", "cell-3")):
        steps = []
        for step, time in ((1, 0.0), (2, 86400.0)):
            item = copy.deepcopy(cell_steps[cell][str(step)])
            steps.append({"step": step, "t_s": time, "activity_Bq_per_g": item,
                          "photon_source": {"groups": [{"photons_s": 10.0}]},
                          "uncertainty": {"responses": {"heat.total": {
                              "nominal": 1.0, "combined_standard_uncertainty": 0.1,
                              "normal_multiplier": 1.959964,
                              "conservative_interval": [0.8, 1.2]}}}})
        records.append({"record": "cell", "ordinal": ordinal, "id": cell,
                        "bounds_cm": [[ordinal, ordinal + 1], [0, 1], [0, 1]],
                        "volume_cm3": 1.0,
                        "result": {"entry_point": "synthetic", "mode": "coupled",
                                   "steps": steps}})
    records.append({"record": "footer", "cell_count": 4})
    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in records)


def _case(case_id: str, activities: list[dict], *, waste_type: str = "general",
          volume: float = 3.0, geometry: str = "volume", targets: list[int] | None = None,
          external: dict | None = None, props: dict | None = None,
          unassigned: bool = False, assay: str | None = None,
          cell_steps_override: dict | None = None, unsplit: bool = False) -> dict:
    # Component groups are the sole membership source: two disjoint pairs.
    twin_components = {"component-a": ["cell-0", "cell-1"],
                       "component-b": ["cell-2", "cell-3"]}
    mass_maps = {"component-a": {"cell-0": 0.25, "cell-1": 0.75},
                 "component-b": {"cell-2": 0.5, "cell-3": 1.5}}
    total_mass = {"component-a": 1.0, "component-b": 2.0}
    if unsplit:
        twin_components = {"component-a": ["cell-0"], "component-b": ["cell-2"]}
        mass_maps = {"component-a": {"cell-0": 1.0}, "component-b": {"cell-2": 2.0}}
    waste_components = {}
    for component in twin_components:
        record = {"mass_g": total_mass[component], "waste_type": waste_type,
                  "cell_masses_g": mass_maps[component],
                  "external_tritium": copy.deepcopy(external or {"status": "not_applicable"})}
        if geometry == "density":
            record["density_g_cm3"] = total_mass[component] / volume
        else:
            record["displaced_volume_cm3"] = volume
        waste_components[component] = record
    if len(activities) != 2 or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in activities):
        raise ValueError("each request must supply step-1/step-2 inventories for both components")
    cell_steps = {
        "cell-0": {"1": activities[0][0], "2": activities[0][1]},
        "cell-1": {"1": activities[0][0], "2": activities[0][1]},
        "cell-2": {"1": activities[1][0], "2": activities[1][1]},
        "cell-3": {"1": activities[1][0], "2": activities[1][1]},
    }
    if cell_steps_override is not None:
        cell_steps = copy.deepcopy(cell_steps_override)
    inventory_names = set().union(*(set(values) for cell in cell_steps.values() for values in cell.values()))
    properties = _props(inventory_names) if props is None else copy.deepcopy(props)
    waste = {"schema": "actinv-twin-waste-spec-1", "rules": "us-nrc-10cfr61.55-v1",
             "targets": copy.deepcopy(targets or [1, 2]),
             "nuclide_properties": properties, "components": waste_components}
    spec = {"spec": "actinv-twin-1", "mesh_output": "mesh.ndjson",
            "limits": [{"name": "heat", "response": "heat.total", "limit": 2.0}],
            "components": twin_components, "waste": waste}
    assay_files = {}
    if assay in ("scalar", "mixture", "propagated"):
        if assay == "mixture":
            assay_files["mixture.json"] = {"schema": "actinv-assay-1", "time_s": 0.0,
                "mixture": [{"response": "heat.total", "coefficient": 1.0}],
                "value": 1.0, "standard_uncertainty": 0.05}
        else:
            assay_files["scalar.json"] = {"schema": "actinv-assay-1", "response": "heat.total",
                "time_s": 0.0, "value": 1.0, "standard_uncertainty": 0.05}
        entry = {"cell": "cell-0", "assay": "mixture.json" if assay == "mixture" else "scalar.json"}
        if assay == "propagated": entry["propagates"] = [{"cell": "cell-1", "rho": 0.7}]
        spec["assays"] = [entry]
    elif assay == "dose":
        assay_files["dose.json"] = {"schema": "actinv-assay-1", "response": "heat.total",
            "time_s": 0.0, "value": 1.0, "standard_uncertainty": 0.05}
        spec["dose_points"] = [{"name": "synthetic-point", "position_cm": [0.0, 0.0, 0.0]}]
        spec["dose_assays"] = [{"dose_point": "synthetic-point", "assay": "dose.json"}]
    if unassigned:
        del twin_components["component-b"]
        del waste_components["component-b"]
    return {"id": case_id, "spec": spec,
            "mesh_ndjson": _mesh(cell_steps),
            "assay_files": assay_files,
            "case_kind": assay or "nominal"}


def generate_cases() -> list[dict]:
    """Create the deterministic 24-request P113 minimum population."""
    cases = []
    # Table 1 C-14 limit is 8 Ci/m3; A/B require <=0.1 of that,
    # Class C allows <=1.0, and above-C is strictly greater than 1.0.
    # Inputs are Bq/g and component B has twice component A's mass.
    c14_per_g = lambda fraction, mass: fraction * 8.0 * 37_000.0 * 3.0 / mass
    vectors = [
        ("class_a", {"C14": 0.0}, "general", None),
        # Co-60's A column is finite while B/C are no-limit, so this
        # independently produces Class B above its A concentration.
        ("class_b", {"Co60": 1000.0 * 37_000.0 * 3.0}, "general", None),
        ("class_c", {"C14": c14_per_g(0.5, 1.0)}, "general", None),
        ("above_class_c", {"C14": c14_per_g(1.2, 1.0)}, "general", None),
        ("two_table_mixture", {"C14": 0.06 * 8 * 37_000.0 * 3.0,
                                "Tc99": 0.06 * 3 * 37_000.0 * 3.0,
                                "Co60": 0.7 * 700 * 37_000.0 * 3.0}, "general", None),
        ("short_group", {"Xe135": 800.0 * 37_000.0 * 3.0, "Co60": 100.0}, "general", None),
        ("metal_c14", {"C14": 2.0 * 8 * 37_000.0}, "activated_metal", None),
        ("metal_ni59", {"Ni59": 23.0 * 37_000.0}, "activated_metal", None),
        ("metal_ni63", {"Ni63": 36.0 * 37_000.0}, "activated_metal", None),
        ("metal_nb94", {"Nb94": 0.21 * 37_000.0}, "activated_metal", None),
        ("mass_normalized_alpha", {"Pu239": 11.0 * 37.0}, "general", None),
        ("alpha_group", {name: 2.0 * 37.0 for name in
            ("Pu238", "Pu239", "Pu240", "Pu242", "Am241", "Am243", "Cm243", "Cm244")}, "general", None),
        ("density_geometry", {"C14": 0.08 * 8.0 * 37_000.0 * 3.0}, "general", None),
        ("volume_geometry", {"C14": 0.08 * 8.0 * 37_000.0 * 3.0}, "general", None),
        ("external_h3_once", {"H3": 3.0e6, "C14": 1.0}, "general",
         {"status": "declared", "source": "synthetic declared external", "excludes_activation": True,
          "activity_bq": {"1": 2.0e6, "2": 4.0e6}}),
        ("required_h3", {"C14": 1.0}, "general", {"status": "required"}),
        ("missing_metadata", {"C14": 1.0, "Xe135": 100.0}, "general", None),
        ("empty_inventory", {}, "general", None),
        ("unassigned_cell", {"C14": 1.0}, "general", None),
    ]
    for name, inv, kind, external in vectors:
        a0 = dict(inv); a1 = {key: value * 2.0 for key, value in inv.items()}
        b0 = {key: value / 2.0 for key, value in a0.items()}
        b1 = {key: value / 2.0 for key, value in a1.items()}
        activities = [(a0, a1), (b0, b1)]
        props = {} if name == "missing_metadata" else None
        geometry = "density" if name == "density_geometry" else "volume"
        targets = [2, 1] if name == "selected_step_reordering" else [1, 2]
        cases.append(_case(name, activities, waste_type=kind, volume=3.0,
                           geometry=geometry, external=external, props=props,
                           targets=targets, unassigned=name == "unassigned_cell"))
    # Exact/adjacent boundaries and activity-shape invariants.
    for case_id, multiplier in (("boundary_below", 0.1 - 1e-9), ("boundary_equal", 0.1),
                                ("boundary_above", 0.1 + 1e-9)):
        inv_a = {"C14": c14_per_g(multiplier, 1.0)}
        inv_b = {"C14": c14_per_g(multiplier, 2.0)}
        cases.append(_case(case_id, [(inv_a, inv_a), (inv_b, inv_b)]))
    for case_id, multiplier in (("mixture_boundary_below", 0.1 - 1e-9),
                                ("mixture_boundary_equal", 0.1),
                                ("mixture_boundary_above", 0.1 + 1e-9)):
        inv_a = {"C14": multiplier * 0.5 * 8.0 * 37_000.0 * 3.0,
                 "Tc99": multiplier * 0.5 * 3.0 * 37_000.0 * 3.0}
        inv_b = {key: value / 2.0 for key, value in inv_a.items()}
        cases.append(_case(case_id, [(inv_a, inv_a), (inv_b, inv_b)]))
    cases.extend([
        _case("assay_baseline", [({"C14": 100.0}, {"C14": 200.0}),
                                 ({"Co60": 10.0}, {"Co60": 20.0})]),
        _case("scalar_assay", [({"C14": 100.0}, {"C14": 200.0}), ({"Co60": 10.0}, {"Co60": 20.0})], assay="scalar"),
        _case("mixture_assay", [({"C14": 100.0}, {"C14": 200.0}), ({"Co60": 10.0}, {"Co60": 20.0})], assay="mixture"),
        _case("propagated_assay", [({"C14": 100.0}, {"C14": 200.0}), ({"Co60": 10.0}, {"Co60": 20.0})], assay="propagated"),
        _case("dose_assay", [({"C14": 100.0}, {"C14": 200.0}), ({"Co60": 10.0}, {"Co60": 20.0})], assay="dose"),
        _case("repeat_deterministic", [({"C14": 10.0}, {"C14": 20.0}), ({"Co60": 10.0}, {"Co60": 20.0})]),
    ])
    uniform = {"cell-0":{"1":{"C14":100.0},"2":{"C14":200.0}},
               "cell-1":{"1":{"C14":100.0},"2":{"C14":200.0}},
               "cell-2":{"1":{"Co60":10.0},"2":{"Co60":20.0}},
               "cell-3":{"1":{"Co60":10.0},"2":{"Co60":20.0}}}
    split = copy.deepcopy(uniform)
    split["cell-0"]={"1":{"C14":400.0},"2":{"C14":800.0}}
    split["cell-1"]={"1":{"C14":0.0},"2":{"C14":0.0}}
    # .25*400 + .75*0 equals the uniform weighted total .25*100+.75*100.
    cases.append(_case("uniform_split_reference", [({"C14":100.0},{"C14":200.0}),
        ({"Co60":10.0},{"Co60":20.0})], cell_steps_override=uniform))
    cases.append(_case("unequal_cell_split_equivalent", [({"C14":100.0},{"C14":200.0}),
        ({"Co60":10.0},{"Co60":20.0})], cell_steps_override=split))
    cases.append(_case("single_cell_unsplit_equivalent", [({"C14":100.0},{"C14":200.0}),
        ({"Co60":10.0},{"Co60":20.0})], unsplit=True))
    # Ensure the selected-step-order case is a true distinct request.
    cases.append(_case("selected_step_reordering", [({"C14": 100.0}, {"C14": 200.0}),
                                                      ({"Co60": 10.0}, {"Co60": 20.0})], targets=[2, 1]))
    if len(cases) < 24 or len({case["id"] for case in cases}) != len(cases):
        raise AssertionError("P113 request population is below or duplicates its frozen minimum")
    return cases


def expected_component(case: dict, component_id: str, step: int) -> dict:
    spec = case["spec"]["waste"]
    component = spec["components"][component_id]
    group_ids = case["spec"]["components"][component_id]
    cell_mass = component["cell_masses_g"]
    total_mass = component["mass_g"]
    if set(cell_mass) != set(group_ids):
        raise ValueError(f"cell mass map is not exactly the twin group for {component_id}")
    if abs(sum(cell_mass.values()) - total_mass) > 1e-9 * max(abs(total_mass), 1.0):
        raise ValueError(f"cell masses do not close for {component_id}")
    mesh_records = [json.loads(line) for line in case["mesh_ndjson"].splitlines()]
    cells = [row for row in mesh_records if row.get("record") == "cell"]
    cell_by_id = {record.get("id"): record for record in cells}
    if len(cell_by_id) != len(cells) or set(group_ids) - set(cell_by_id):
        raise ValueError("mesh contains duplicate or missing component cells")
    if not mesh_records or mesh_records[0].get("record") != "header" or mesh_records[-1].get("record") != "footer":
        raise ValueError("mesh header/footer order is invalid")
    activity = {}
    selected_times = []
    for cell, mass in cell_mass.items():
        if cell not in group_ids:
            raise ValueError(f"mass map includes cell outside twin component {component_id}: {cell}")
        record = cell_by_id[cell]
        rows = {row.get("step"): row for row in record["result"]["steps"]}
        if step not in rows:
            raise ValueError(f"frozen mesh cell {cell} lacks step {step}")
        selected_times.append(rows[step].get("t_s"))
        values = rows[step]["activity_Bq_per_g"]
        for nuclide, bq_per_g in values.items():
            activity[nuclide] = activity.get(nuclide, 0.0) + bq_per_g * mass
    if not selected_times or any(value != selected_times[0] for value in selected_times):
        raise ValueError(f"selected cell timestamps differ for {component_id}")
    activation = copy.deepcopy(activity)
    external_decl = component["external_tritium"]
    external = 0.0
    if external_decl["status"] == "declared":
        external = external_decl["activity_bq"][str(step)]
        activity["H3"] = activity.get("H3", 0.0) + external
    props = spec["nuclide_properties"]
    geometry = (component["displaced_volume_cm3"] if "displaced_volume_cm3" in component
                else total_mass / component["density_g_cm3"])
    base_case = {"activity_Bq_per_g": {key: value / total_mass for key, value in activity.items()},
                 "mass_g": total_mass, "displaced_volume_cm3": geometry,
                 "waste_type": component["waste_type"], "nuclide_properties": props,
                 "external_tritium": external_decl}
    rows = check_p105.expected_pack_rows()
    # The independent P105 oracle receives only active nuclides with known
    # properties; the wrapper then restores unknown coverage explicitly.
    combined_oracle = _known_subset_classification(base_case, rows)
    activation_case = {**base_case,
        "activity_Bq_per_g": {key: value / total_mass for key, value in activation.items()}}
    activation_oracle = _known_subset_classification(activation_case, rows)
    reported = copy.deepcopy(combined_oracle)
    reported["combined_calculated_only_class"] = combined_oracle["calculated_only_class"]
    reported["calculated_only_class"] = ("unknown" if activation_oracle["unknown_nuclides"]
                                         else activation_oracle["evaluation_class"])
    reported["evaluation_class"] = combined_oracle["evaluation_class"]
    if external_decl["status"] == "required":
        reported["class"] = "unknown"
    return {"component_id": component_id, "step": step, "t_s": selected_times[0],
            "inventory_activity_bq": dict(sorted(activity.items())),
            "activation_activity_bq": dict(sorted(activation.items())),
            "external_tritium_activity_bq": external,
            "classification": reported}


def expected_summary(case: dict) -> dict:
    groups = case["spec"].get("components", {})
    mesh_ids = [f"cell-{i}" for i in range(4)]
    assigned = sorted({cell for cells in groups.values() for cell in cells})
    targets = case["spec"]["waste"]["targets"]
    counts = []
    for step in targets:
        by_class = {name: 0 for name in ("A", "B", "C", "above_class_c", "unknown")}
        for component in groups:
            kind = case["spec"]["waste"]["components"][component]["external_tritium"]["status"]
            result = expected_component(case, component, step)["classification"]
            class_name = "unknown" if kind == "required" or result["class"] == "unknown" else result["class"]
            by_class[class_name] += 1
        counts.append({"step": step, "counts": by_class})
    return {"schema": "actinv-twin-waste-summary-1",
            "inventory_basis": "original_mesh_activity_Bq_per_g", "assay_adjustment": "not_applied",
            "assigned_cell_ids": assigned,
            "unassigned_cell_ids": sorted(set(mesh_ids) - set(assigned)),
            "assigned_cell_count": len(assigned), "unassigned_cell_count": len(set(mesh_ids)-set(assigned)),
            "membership_coverage": "complete" if len(assigned) == len(mesh_ids) else "partial",
            "component_count": len(groups), "target_class_counts": counts}


def expected_waste_document(case: dict) -> dict:
    """Full ordinary waste result independently rebuilt from exact mesh bytes."""
    spec = case["spec"]["waste"]
    mesh_bytes = case["mesh_ndjson"].encode("utf-8")
    ordinary_spec = {"schema": "actinv-waste-spec-1", "rules": spec["rules"],
        "input": case["spec"]["mesh_output"], "targets": spec["targets"],
        "nuclide_properties": spec["nuclide_properties"],
        "components": []}
    for component_id in sorted(spec["components"]):
        meta = spec["components"][component_id]
        ordinary = {"id": component_id, "mass_g": meta["mass_g"],
                    "waste_type": meta["waste_type"], "external_tritium": meta["external_tritium"],
                    "cells": [{"id": cell, "mass_g": mass}
                              for cell, mass in sorted(meta["cell_masses_g"].items())]}
        if "displaced_volume_cm3" in meta:
            ordinary["displaced_volume_cm3"] = meta["displaced_volume_cm3"]
        else:
            ordinary["density_g_cm3"] = meta["density_g_cm3"]
        ordinary_spec["components"].append(ordinary)
    spec_text = json.dumps(ordinary_spec, sort_keys=True, separators=(",", ":"))
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    rows = check_p105.expected_pack_rows()
    output_components = []
    overall_nominal = True
    for component_id in sorted(spec["components"]):
        meta = spec["components"][component_id]
        volume = (meta["displaced_volume_cm3"] if "displaced_volume_cm3" in meta
                  else meta["mass_g"] / meta["density_g_cm3"])
        targets = []
        component_nominal = True
        for step in spec["targets"]:
            expected = expected_component(case, component_id, step)
            oracle = expected["classification"]
            evaluation = {
                "class": oracle["evaluation_class"],
                "calculated_only_class": oracle["combined_calculated_only_class"],
                "coverage": oracle["coverage"],
                "unknown_nuclides": oracle["unknown_nuclides"],
                "unlisted_nuclides": oracle["unlisted_nuclides"],
                "unlisted_activity_bq": oracle["unlisted_activity_bq"],
                "row_fractions": oracle["row_details"],
                "constraints": oracle["constraints"],
                "binding_constraints": oracle["binding_constraints"],
            }
            unknown_reason = ("required external H-3 not declared"
                if meta["external_tritium"]["status"] == "required"
                else oracle["unknown_nuclides"] if oracle["unknown_nuclides"] else None)
            target_class = ("unknown" if unknown_reason is not None else oracle["class"])
            target_status = "unknown" if unknown_reason is not None else "nominal"
            component_nominal &= target_status == "nominal"
            targets.append({"step": step, "t_s": expected["t_s"],
                "inventory_activity_bq": expected["inventory_activity_bq"],
                "external_tritium_activity_bq": expected["external_tritium_activity_bq"],
                "calculated_only_class": oracle["calculated_only_class"],
                "class": target_class, "coverage": oracle["coverage"],
                "external_tritium_status": meta["external_tritium"]["status"],
                "status": target_status, "unknown_reason": unknown_reason,
                "evaluation": evaluation})
        overall_nominal &= component_nominal
        output_components.append({"id": component_id, "mass_g": meta["mass_g"],
            "displaced_volume_cm3": volume, "waste_type": meta["waste_type"],
            "external_tritium": {"status": meta["external_tritium"]["status"]},
            "status": "nominal" if component_nominal else "conditional", "targets": targets})
    return {"schema":"actinv-waste-result-1", "waste_sha256":hashlib.sha256(spec_text.encode()).hexdigest(),
        "input":case["spec"]["mesh_output"], "input_sha256":hashlib.sha256(mesh_bytes).hexdigest(),
        "input_kind":"mesh", "rules":spec["rules"],
        "rule_pack":{"id":pack["id"],"version":pack["version"],"source_url":pack["source_url"],
                      "source_as_of":pack["source_as_of"],"sha256":hashlib.sha256(PACK.read_bytes()).hexdigest()},
        "rule_pack_sha256":hashlib.sha256(PACK.read_bytes()).hexdigest(),"targets":spec["targets"],
        "status":"nominal" if overall_nominal else "conditional","components":output_components}


def frozen_fixture() -> dict:
    cases=generate_cases()
    requests=[]
    for case in cases:
        legacy_spec = copy.deepcopy(case["spec"])
        legacy_spec.pop("waste", None)
        requests.append({**case,"expected":{"waste_classification":expected_waste_document(case),
            "waste_facility_coverage":expected_summary(case),
            "legacy_twin_result": legacy_control.expected_legacy(case),
            "legacy_twin_spec": legacy_spec,
            "legacy_twin_spec_sha256": sha_json(legacy_spec),
            "request_component_target_count":len(case["spec"]["components"])*len(case["spec"]["waste"]["targets"])}})
    return {"schema":"actinv-p113-twin-waste-cases-1","requests":requests}


def fixture_counts(fixture: dict) -> dict:
    failures = []
    requests = fixture.get("requests") if isinstance(fixture, dict) else None
    if (not isinstance(fixture, dict) or fixture.get("schema") != "actinv-p113-twin-waste-cases-1"
            or not isinstance(requests, list)):
        return {"pass": False, "request_count": 0, "request_ids": [],
                "request_component_target_counts": [], "component_target_count": 0,
                "class_outcomes": [], "errors": ["fixture header or request array is malformed"]}
    if any(not isinstance(item, dict) or not isinstance(item.get("expected"), dict)
           or not isinstance(item.get("spec"), dict) for item in requests):
        return {"pass": False, "request_count": len(requests), "request_ids": [],
                "request_component_target_counts": [], "component_target_count": 0,
                "class_outcomes": [], "errors": ["request record shape is malformed"]}
    request_ids = [item.get("id") for item in requests]
    target_counts = [item["expected"].get("request_component_target_count") for item in requests]
    labels = set()
    for item in requests:
        expected = item["expected"]
        doc = expected.get("waste_classification")
        summary = expected.get("waste_facility_coverage")
        if not isinstance(doc, dict) or not isinstance(summary, dict) or not isinstance(doc.get("components"), list):
            failures.append("expected report shape is malformed")
            continue
        for component in doc["components"]:
            if not isinstance(component, dict) or not isinstance(component.get("targets"), list):
                failures.append("expected component targets are malformed")
                continue
            for target in component["targets"]:
                if isinstance(target, dict):
                    labels.add(target.get("class"))
    ids_are_safe = all(isinstance(value, str) and value for value in request_ids)
    if len(requests) < 24 or not ids_are_safe or len(set(value for value in request_ids if isinstance(value, str))) != len(request_ids):
        failures.append("request population is under 24 or has duplicate IDs")
    counts_are_safe = all(type(value) is int and value > 0 for value in target_counts)
    if not counts_are_safe:
        failures.append("per-request component-target counts must be positive integers")
    if counts_are_safe and sum(target_counts) < 32:
        failures.append("component-target population is under 32")
    if labels != {"A", "B", "C", "above_class_c", "unknown"}:
        failures.append(f"five nominal class outcomes are not all exercised: {labels}")
    alpha_case = next((item for item in requests if item.get("id") == "alpha_group"), None)
    alpha_members = {"Pu238", "Pu239", "Pu240", "Pu242", "Am241", "Am243", "Cm243", "Cm244"}
    alpha_row_id = next((row["id"] for row in check_p105.expected_pack_rows()
                         if row.get("selector") == "alpha_transuranic_gt5y"), None)
    if alpha_case is None or alpha_row_id is None:
        failures.append("fixed-eight alpha group control is missing")
    else:
        alpha_target = alpha_case["expected"]["waste_classification"]["components"][0]["targets"][0]
        observed = {row.get("nuclide") for row in alpha_target["evaluation"]["row_fractions"]
                    if row.get("row_id") == alpha_row_id}
        if observed != alpha_members:
            failures.append("fixed-eight alpha group does not include all eight declared contributors")
    return {"pass": not failures, "request_count": len(requests),
            "request_ids": request_ids, "request_component_target_counts": target_counts,
            "component_target_count": sum(target_counts) if counts_are_safe else 0,
            "class_outcomes": sorted(labels), "errors": failures}


def sha_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
