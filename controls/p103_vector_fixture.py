"""Offline generator for the frozen P103 synthetic classification vectors.

Run only while preparing the pre-implementation vector seal. CI consumes the
checked-in JSON; it never regenerates or rewrites the vectors.
"""
from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "controls/fixtures/p103/classification_vectors.json"
DAY = 86400.0
FIVE_YEARS = 5.0 * 365.25 * DAY
CI_BQ_PER_CM3 = 37_000.0
NCI_BQ_PER_G = 37.0
DELTA = Decimal("1e-10")

PROPS = {
    "C14": {"z": 6, "half_life_s": 1.8e11, "alpha_emitting": False},
    "Ni59": {"z": 28, "half_life_s": 2.4e12, "alpha_emitting": False},
    "Nb94": {"z": 41, "half_life_s": 6.4e11, "alpha_emitting": False},
    "Tc99": {"z": 43, "half_life_s": 6.7e12, "alpha_emitting": False},
    "I129": {"z": 53, "half_life_s": 4.95e14, "alpha_emitting": False},
    "Am241": {"z": 95, "half_life_s": 1.36e10, "alpha_emitting": True},
    "Pu241": {"z": 94, "half_life_s": 4.5e8, "alpha_emitting": True},
    "Cm242": {"z": 96, "half_life_s": 1.4e7, "alpha_emitting": True},
    "H3": {"z": 1, "half_life_s": 3.9e8, "alpha_emitting": False},
    "Co60": {"z": 27, "half_life_s": 1.66e8, "alpha_emitting": False},
    "Ni63": {"z": 28, "half_life_s": 3.15e9, "alpha_emitting": False},
    "Sr90": {"z": 38, "half_life_s": 9.1e8, "alpha_emitting": False},
    "Cs137": {"z": 55, "half_life_s": 9.5e8, "alpha_emitting": False},
    "Xe135": {"z": 54, "half_life_s": 3.3e4, "alpha_emitting": False},
    "Fe56": {"z": 26, "half_life_s": 0.0, "alpha_emitting": False},
    "Be10": {"z": 4, "half_life_s": 5.0e14, "alpha_emitting": False},
}

# Independent CFR transcription. Units are fixed by source footnote 1.
T1_ROWS = [
    ("C-14", "C14", "general", "Ci/m3", 8.0),
    ("C-14_activated_metal", "C14", "activated_metal", "Ci/m3", 80.0),
    ("Ni-59_activated_metal", "Ni59", "activated_metal", "Ci/m3", 220.0),
    ("Nb-94_activated_metal", "Nb94", "activated_metal", "Ci/m3", 0.2),
    ("Tc-99", "Tc99", "all", "Ci/m3", 3.0),
    ("I-129", "I129", "all", "Ci/m3", 0.08),
    ("alpha_transuranic_gt5y", "alpha_transuranic_gt5y", "all", "nCi/g", 100.0),
    ("Pu-241", "Pu241", "all", "nCi/g", 3500.0),
    ("Cm-242", "Cm242", "all", "nCi/g", 20000.0),
]
T2_ROWS = [
    ("half_life_lt5y", "half_life_lt5y", "all", [700.0, None, None]),
    ("H-3", "H3", "all", [40.0, None, None]),
    ("Co-60", "Co60", "all", [700.0, None, None]),
    ("Ni-63", "Ni63", "general", [3.5, 70.0, 700.0]),
    ("Ni-63_activated_metal", "Ni63", "activated_metal", [35.0, 700.0, 7000.0]),
    ("Sr-90", "Sr90", "all", [0.04, 150.0, 7000.0]),
    ("Cs-137", "Cs137", "all", [1.0, 44.0, 4600.0]),
]
T2_NAMED = {"H3", "Co60", "Ni63", "Sr90", "Cs137"}


def concentration_activity(value: float, unit: str, mass_g: float, volume_cm3: float) -> float:
    value = Decimal(str(value))
    mass_g = Decimal(str(mass_g))
    volume_cm3 = Decimal(str(volume_cm3))
    if unit == "Ci/m3":
        activity = value * Decimal("37000") * volume_cm3 / mass_g
    else:
        activity = value * Decimal("37")
    return float(activity)


def nuclide_fraction(activity_bq_per_g: float, unit: str, limit: float, mass_g: float, volume_cm3: float) -> float:
    total_activity_bq = activity_bq_per_g * mass_g
    if unit == "Ci/m3":
        return total_activity_bq / (limit * CI_BQ_PER_CM3 * volume_cm3)
    return total_activity_bq / (limit * NCI_BQ_PER_G * mass_g)


def expected(case: dict) -> dict:
    waste_type = case["waste_type"]
    mass_g = case["mass_g"]
    volume_cm3 = case["displaced_volume_cm3"]
    activity = case["activity_Bq_per_g"]
    properties = case["nuclide_properties"]
    row_fractions = {}
    t1_contributors = []
    t2_fraction_by_col = [[], [], []]
    t2_names_by_col = [set(), set(), set()]

    def active(name: str) -> bool:
        return activity.get(name, 0.0) > 0.0

    for row_id, selector, applicability, unit, limit in T1_ROWS:
        if applicability not in ("all", waste_type):
            continue
        if selector == "alpha_transuranic_gt5y":
            names = [
                name for name, prop in properties.items()
                if name in activity and active(name) and prop["z"] > 92
                and prop["alpha_emitting"] and prop["half_life_s"] > FIVE_YEARS
                and name != "Pu241"
            ]
        else:
            names = [selector] if active(selector) else []
        if names:
            fraction = math.fsum(
                nuclide_fraction(activity[name], unit, limit, mass_g, volume_cm3) for name in names
            )
            row_fractions[f"T1:{row_id}"] = fraction
            t1_contributors.extend(names)

    for row_id, selector, applicability, limits in T2_ROWS:
        if applicability not in ("all", waste_type):
            continue
        if selector == "half_life_lt5y":
            names = [
                name for name, prop in properties.items()
                if name in activity and active(name) and 0.0 < prop["half_life_s"] < FIVE_YEARS
                and name not in T2_NAMED
            ]
        else:
            names = [selector] if active(selector) else []
        for index, limit in enumerate(limits):
            if limit is None:
                continue
            if names:
                contribution = math.fsum(
                    nuclide_fraction(activity[name], "Ci/m3", limit, mass_g, volume_cm3)
                    for name in names
                )
                row_fractions[f"T2:{row_id}:col{index + 1}"] = contribution
                t2_fraction_by_col[index].append(contribution)
                t2_names_by_col[index].update(names)

    t1_sum = math.fsum(row_fractions[key] for key in row_fractions if key.startswith("T1:"))
    t1_count = len(set(t1_contributors))
    t2_sums = [math.fsum(col) for col in t2_fraction_by_col]
    t2_counts = [len(names) for names in t2_names_by_col]

    t1_names_sorted = sorted(set(t1_contributors))
    constraint_rows = []
    for target, t2_column in (("A", 0), ("B", 1), ("C", 2)):
        t1_threshold = 1.0 if target == "C" else 0.1
        for table, column, source_sum, contributors in (
            (1, None, t1_sum, t1_names_sorted),
            (2, t2_column + 1, t2_sums[t2_column], sorted(t2_names_by_col[t2_column])),
        ):
            normalized_sum = source_sum / t1_threshold if table == 1 else source_sum
            strict = len(contributors) > 1
            constraint_rows.append({
                "target_class": target,
                "table": table,
                "column": column,
                "source_sum_fraction": source_sum,
                "normalized_sum": normalized_sum,
                "strict": strict,
                "passes": normalized_sum < 1.0 if strict else normalized_sum <= 1.0,
                "contributor_count": len(contributors),
                "contributors": contributors,
            })
    result_class = next(
        (target for target in ("A", "B", "C") if all(
            constraint["passes"] for constraint in constraint_rows if constraint["target_class"] == target
        )),
        "above_class_c",
    )

    return {
        "class": result_class,
        "calculated_only_class": result_class,
        "coverage": "complete",
        "unknown_nuclides": [],
        "table1": {"sum_fractions": t1_sum, "contributors": t1_count},
        "table2": {
            "column_sums": {str(i + 1): value for i, value in enumerate(t2_sums)},
            "column_contributors": {str(i + 1): value for i, value in enumerate(t2_counts)},
        },
        "constraints": constraint_rows,
        "row_fractions": row_fractions,
    }


def add_vector(vectors: list[dict], vector_id: str, waste_type: str, concentrations: dict[str, tuple[float, str]], properties: dict[str, dict] | None = None, *, missing_properties: list[str] = (), external_tritium: dict | None = None, expected_class: str | None = None) -> None:
    mass_g = 1.0
    volume_cm3 = 1.0
    activities = {
        name: concentration_activity(value, unit, mass_g, volume_cm3)
        for name, (value, unit) in concentrations.items()
    }
    props = {name: dict(PROPS[name]) for name in concentrations if name in PROPS}
    props.update(properties or {})
    for name in missing_properties:
        props.pop(name, None)
    case = {
        "id": vector_id,
        "waste_type": waste_type,
        "mass_g": mass_g,
        "displaced_volume_cm3": volume_cm3,
        "activity_Bq_per_g": activities,
        "nuclide_properties": props,
        "external_tritium": external_tritium or {"status": "not_applicable"},
    }
    case["expected"] = expected(case)
    if expected_class is not None:
        case["expected"]["class"] = expected_class
    if missing_properties:
        case["expected"]["calculated_only_class"] = case["expected"]["class"]
        case["expected"]["class"] = "unknown"
        case["expected"]["coverage"] = "incomplete"
        case["expected"]["unknown_nuclides"] = sorted(missing_properties)
    if case["external_tritium"].get("status") == "required":
        case["expected"]["calculated_only_class"] = case["expected"]["class"]
        case["expected"]["class"] = "unknown"
        case["expected"]["external_unknown_reason"] = "required external H-3 not declared"
    vectors.append(case)


def build_vectors() -> list[dict]:
    vectors = []
    # Single-contributor Table 1: both the 0.1 A threshold and 1.0 C
    # threshold, each with below/equal/above cases for every finite row.
    for row_id, isotope, applicability, unit, limit in T1_ROWS:
        vector_isotope = "Am241" if isotope == "alpha_transuranic_gt5y" else isotope
        waste_type = "activated_metal" if applicability == "activated_metal" else "general"
        for threshold, outputs in ((Decimal("0.1"), ("A", "A", "C")), (Decimal("1"), ("C", "C", "above_class_c"))):
            for side, factor, class_name in zip(("below", "equal", "above"), (threshold * (1 - DELTA), threshold, threshold * (1 + DELTA)), outputs):
                exact_value = Decimal(str(limit)) * factor
                add_vector(vectors, f"t1-{row_id}-{threshold:g}-{side}", waste_type, {vector_isotope: (exact_value, unit)}, expected_class=class_name)

    # Every finite Table 2 column boundary, including both explicit Ni-63 forms.
    for row_id, isotope, applicability, limits in T2_ROWS:
        vector_isotope = "Xe135" if isotope == "half_life_lt5y" else isotope
        if not any(limit is not None for limit in limits):
            continue
        waste_type = "activated_metal" if applicability == "activated_metal" else "general"
        for column, limit in enumerate(limits):
            if limit is None:
                continue
            classes = (("A", "A", "B") if column == 0 else
                       ("B", "B", "C") if column == 1 else
                       ("C", "C", "above_class_c"))
            for side, factor, class_name in zip(("below", "equal", "above"), (1 - DELTA, Decimal("1"), 1 + DELTA), classes):
                exact_value = Decimal(str(limit)) * factor
                add_vector(vectors, f"t2-{row_id}-col{column + 1}-{side}", waste_type, {vector_isotope: (exact_value, "Ci/m3")}, expected_class=class_name)

    # Strict mixture boundaries in Table 1, using two distinct named rows.
    t1_pairs = (("Tc99", 3.0), ("I129", 0.08))
    for threshold in (Decimal("0.1"), Decimal("1")):
        classes = (("A", "C", "C") if threshold == Decimal("0.1") else ("C", "above_class_c", "above_class_c"))
        for side, scale, class_name in zip(("below", "equal", "above"), (1 - DELTA, Decimal("1"), 1 + DELTA), classes):
            add_vector(vectors, f"t1-mixture-{threshold:g}-{side}", "general", {
                name: (Decimal(str(limit)) * threshold * scale / Decimal("2"), "Ci/m3") for name, limit in t1_pairs
            }, expected_class=class_name)

    # Strict Table 2 mixture boundaries in all three columns. Fractions 1/4
    # and 3/4 make an independently obvious unit sum at each selected edge.
    t2_pair_limits = {"Sr90": (0.04, 150.0, 7000.0), "Cs137": (1.0, 44.0, 4600.0)}
    for column in range(3):
        classes = (("A", "B", "B") if column == 0 else
                   ("B", "C", "C") if column == 1 else
                   ("C", "above_class_c", "above_class_c"))
        for side, scale, class_name in zip(("below", "equal", "above"), (1 - DELTA, Decimal("1"), 1 + DELTA), classes):
            add_vector(vectors, f"t2-mixture-col{column + 1}-{side}", "general", {
                name: (Decimal(str(limits[column])) * fraction * scale, "Ci/m3")
                for name, limits, fraction in (("Sr90", t2_pair_limits["Sr90"], Decimal("0.25")), ("Cs137", t2_pair_limits["Cs137"], Decimal("0.75")))
            }, expected_class=class_name)

    # Frozen edge/category interactions and coverage vectors.
    add_vector(vectors, "aggregate-short-lived", "general", {"Xe135": (700.0, "Ci/m3")})
    add_vector(vectors, "alpha-transuranic-aggregate", "general", {"Am241": (100.0, "nCi/g")})
    add_vector(vectors, "pu241-dedicated-precedence", "general", {"Pu241": (3500.0, "nCi/g")})
    add_vector(vectors, "cm242-both-tables", "general", {"Cm242": (20000.0, "nCi/g")})
    add_vector(vectors, "h3-named-table2-precedence", "general", {"H3": (40.0, "Ci/m3")})
    add_vector(vectors, "activated-metal-c14-substitution", "activated_metal", {"C14": (80.0, "Ci/m3")})
    add_vector(vectors, "activated-metal-ni63-substitution", "activated_metal", {"Ni63": (35.0, "Ci/m3")})
    add_vector(vectors, "mixed-unit-t1", "general", {"Tc99": (1.5, "Ci/m3"), "Pu241": (1750.0, "nCi/g")})
    add_vector(vectors, "no-covered-rows", "general", {"Be10": (1.0e-8, "Ci/m3")})
    add_vector(vectors, "unknown-short-lived-metadata", "general", {"Xe135": (10.0, "Ci/m3")}, missing_properties=["Xe135"])
    add_vector(vectors, "missing-required-external-h3", "activated_metal", {}, external_tritium={"status": "required"})
    add_vector(vectors, "mixed-tables-table1-a-table2-b", "general", {"Tc99": (0.3, "Ci/m3"), "Sr90": (0.08, "Ci/m3")})
    return vectors


def main() -> None:
    document = {
        "schema": "actinv-p103-classification-vectors-1",
        "description": "Frozen synthetic vectors derived from the checked-in 10 CFR 61.55 tables; no nuclear-data library inputs.",
        "geometry": {"mass_g": 1.0, "displaced_volume_cm3": 1.0},
        "conversions": {"Ci_per_m3_to_Bq_per_g": "value * 37000 * volume_cm3 / mass_g", "nCi_per_g_to_Bq_per_g": "value * 37"},
        "vectors": build_vectors(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(document['vectors'])} frozen vectors to {OUTPUT}")


if __name__ == "__main__":
    main()
