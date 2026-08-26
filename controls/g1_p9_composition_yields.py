#!/usr/bin/env python3
"""P9-G1: explicit compositions and the independent/cumulative NFPY reader."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import openmc.data

from p9_fixtures import (
    AVOGADRO,
    BIN,
    DUMP,
    NEUTRON_MASS_U,
    ROOT,
    U235_NFPY,
    base_spec,
    command,
    make_fixture,
    relative,
    sha256,
    write_json,
)

RESULTS = Path(os.environ.get("ACTINV_P9_RESULTS", ROOT / "results"))
OFFICIAL_SHA256 = "9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41"


def rust_yields(path: Path) -> dict:
    lines = command([DUMP, "fission-yields", path], timeout=60.0).stdout.splitlines()
    header = lines[0].split()
    tables: dict[tuple[str, float], dict] = {}
    current: tuple[str, float] | None = None
    for line in lines[1:]:
        fields = line.split()
        if fields[0] in ("I", "C"):
            current = (fields[0], float(fields[1]))
            tables[current] = {
                "count": int(fields[2]),
                "sum": float(fields[3]),
                "products": {},
            }
        elif fields[0] == "Y" and current is not None:
            tables[current]["products"][(int(fields[3]), int(fields[4]))] = (
                float(fields[5]),
                float(fields[6]),
            )
    return {
        "parent": (int(header[1]), int(header[2])),
        "awr": float(header[3]),
        "independent_count": int(header[4]),
        "cumulative_count": int(header[5]),
        "tables": tables,
    }


def openmc_yields(path: Path) -> dict:
    data = openmc.data.FissionProductYields(path)
    parent = data.nuclide
    tables = {}
    for kind, rows in (("I", data.independent), ("C", data.cumulative)):
        for energy, products in zip(data.energies, rows):
            converted = {}
            for name, value in products.items():
                z, mass, state = openmc.data.zam(name)
                converted[(1000 * z + mass, state)] = (float(value.n), float(value.s))
            tables[(kind, float(energy))] = {
                "count": len(converted),
                "sum": sum(value[0] for value in converted.values()),
                "products": converted,
            }
    return {
        "parent": (
            1000 * parent["atomic_number"] + parent["mass_number"],
            parent["isomeric_state"],
        ),
        "tables": tables,
    }


def compare_yields(rust: dict, independent: dict) -> tuple[float, list[str]]:
    mismatches = []
    worst = 0.0
    if rust["parent"] != independent["parent"]:
        mismatches.append(f"parent {rust['parent']} != {independent['parent']}")
    if set(rust["tables"]) != set(independent["tables"]):
        mismatches.append("energy/kind table sets differ")
    for key in sorted(set(rust["tables"]) & set(independent["tables"])):
        left, right = rust["tables"][key], independent["tables"][key]
        if set(left["products"]) != set(right["products"]):
            mismatches.append(f"{key}: product sets differ")
            continue
        for product in left["products"]:
            for field, (a, b) in zip(
                ("yield", "uncertainty"),
                zip(left["products"][product], right["products"][product]),
            ):
                difference = relative(a, b)
                worst = max(worst, difference)
                if difference > 1.0e-12:
                    mismatches.append(f"{key}/{product}/{field}: {a} != {b}")
    return worst, mismatches


def material_dump(decay: Path, basis: str, composition: dict[str, float], *, ok: bool = True):
    arguments: list[str | Path] = [DUMP, "material", decay, basis]
    for key, value in composition.items():
        arguments.extend([key, repr(value)])
    result = command(arguments, ok=ok)
    if not ok:
        return (result.stdout + result.stderr).strip()
    inventory = {}
    provenance = {}
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if fields[0] == "I":
            inventory[(int(fields[1]), int(fields[2]))] = float(fields[3])
        elif fields[0] == "N":
            provenance[fields[1]] = {
                "key": (int(fields[2]), int(fields[3])),
                "molar_mass": float(fields[4]),
                "atoms_per_g": float(fields[5]),
            }
    return inventory, provenance


def expected_material(basis: str, composition: dict[str, float], masses: dict[str, float]) -> dict[str, float]:
    if basis == "wt_percent":
        return {
            name: AVOGADRO * (value / 100.0) / masses[name]
            for name, value in composition.items()
        }
    if basis == "atom_fraction":
        denominator = sum(composition[name] * masses[name] for name in composition)
        return {name: AVOGADRO * value / denominator for name, value in composition.items()}
    if basis == "atoms_per_g":
        return dict(composition)
    raise ValueError(basis)


def replace_field(line: str, index: int, value: float | int) -> str:
    if isinstance(value, int):
        field = f"{value:11d}"
    else:
        field = f"{value:11.4E}"
    return line[: 11 * index] + field + line[11 * (index + 1) :]


def malformed_yield_files(work: Path, source: Path) -> dict[str, Path]:
    original = source.read_text().splitlines()
    negative = work / "negative-yield.endf"
    negative_lines = list(original)
    # First MT=454 payload record: field 2 is the first product's yield.
    first_payload = next(
        index
        for index, line in enumerate(negative_lines)
        if len(line) >= 75 and line[70:72].strip() == "8" and line[72:75].strip() == "454"
    ) + 2
    negative_lines[first_payload] = replace_field(negative_lines[first_payload], 2, -0.8)
    negative.write_text("\n".join(negative_lines) + "\n")

    duplicate = work / "duplicate-product.endf"
    duplicate_lines = list(original)
    duplicate_lines[first_payload] = replace_field(duplicate_lines[first_payload], 4, 36092)
    duplicate_lines[first_payload] = replace_field(duplicate_lines[first_payload], 5, 0)
    duplicate.write_text("\n".join(duplicate_lines) + "\n")

    truncated = work / "truncated.endf"
    truncated.write_text("\n".join(original[:-1]) + "\n")
    return {"negative": negative, "duplicate": duplicate, "truncated": truncated}


def main() -> None:
    if not U235_NFPY.exists():
        raise FileNotFoundError(f"P9 G1 needs the pinned U-235 NFPY evaluation at {U235_NFPY}")
    work = Path(os.environ.get("ACTINV_P9_WORK", tempfile.mkdtemp(prefix="actinv-p9-g1-"))) / "g1"
    fixture = make_fixture(work)

    rust = rust_yields(U235_NFPY)
    independent = openmc_yields(U235_NFPY)
    worst_yield_relative, yield_mismatches = compare_yields(rust, independent)
    independent_sums = [
        {
            "energy_eV": energy,
            "sum": table["sum"],
            "deviation_from_two": abs(table["sum"] - 2.0),
            "products": table["count"],
        }
        for (kind, energy), table in sorted(rust["tables"].items())
        if kind == "I"
    ]

    awr = {"u235": 233.0, "ba137m1": 135.76}
    masses = {name: value * NEUTRON_MASS_U for name, value in awr.items()}
    cases = {
        "wt_percent": {"u235": 60.0, "BA137M": 40.0},
        "atom_fraction": {"u235": 3.0, "BA137M": 2.0},
        "atoms_per_g": {"u235": 6.0e20, "BA137M": 4.0e20},
    }
    composition_rows = {}
    worst_composition_relative = 0.0
    aliases_normalized = True
    for basis, inputs in cases.items():
        inventory, provenance = material_dump(fixture["decay"], basis, inputs)
        provenance_by_lower_name = {name.lower(): value for name, value in provenance.items()}
        canonical_inputs = {"u235": inputs["u235"], "ba137m1": inputs["BA137M"]}
        expected = expected_material(basis, canonical_inputs, masses)
        actual = {"u235": inventory[(92235, 0)], "ba137m1": inventory[(56137, 1)]}
        deviations = {name: relative(actual[name], expected[name]) for name in expected}
        mass_deviations = {
            name: relative(provenance_by_lower_name[name]["molar_mass"], masses[name])
            for name in masses
        }
        worst_composition_relative = max(
            worst_composition_relative, *deviations.values(), *mass_deviations.values()
        )
        aliases_normalized &= set(provenance) == {"U235", "Ba137m1"}
        composition_rows[basis] = {
            "inputs": inputs,
            "atoms_per_g": actual,
            "expected_atoms_per_g": expected,
            "relative_deviation": deviations,
            "molar_mass_relative_deviation": mass_deviations,
        }

    failures = {
        "alias_collision": material_dump(
            fixture["decay"], "wt_percent", {"Ba137m": 50.0, "ba137M1": 50.0}, ok=False
        ),
        "element_isotope_mixing": material_dump(
            fixture["decay"], "wt_percent", {"Ba": 50.0, "Ba137": 50.0}, ok=False
        ),
        "malformed_key": material_dump(
            fixture["decay"], "wt_percent", {"U-235": 100.0}, ok=False
        ),
        "missing_mass_record": material_dump(
            fixture["decay"], "wt_percent", {"Xe140": 100.0}, ok=False
        ),
    }
    for name, path in malformed_yield_files(work, fixture["yields"]).items():
        failed = command([DUMP, "fission-yields", path], ok=False)
        failures[name] = (failed.stdout + failed.stderr).strip()

    bad_hash_spec = base_spec(fixture, composition={"U235": 1.0e20})
    bad_hash_spec["fission_yields"]["files"][0]["sha256"] = "0" * 64
    bad_hash_path = work / "bad-hash.json"
    write_json(bad_hash_path, bad_hash_spec)
    bad_hash = command([BIN, "run", bad_hash_path, work / "bad-hash.result.json"], ok=False)
    failures["bad_hash"] = (bad_hash.stdout + bad_hash.stderr).strip()

    failure_checks = {
        "alias_collision": "collide after normalization" in failures["alias_collision"],
        "element_isotope_mixing": "cannot mix natural element" in failures["element_isotope_mixing"],
        "malformed_key": "malformed explicit nuclide key" in failures["malformed_key"],
        "missing_mass_record": "absent from the decay library" in failures["missing_mass_record"],
        "negative": "invalid fission yield" in failures["negative"],
        "duplicate": "duplicate fission product" in failures["duplicate"],
        "truncated": "ended without SEND" in failures["truncated"],
        "bad_hash": "SHA-256 mismatch" in failures["bad_hash"],
    }
    output = {
        "official_u235": {
            "path": str(U235_NFPY),
            "sha256": sha256(U235_NFPY),
            "expected_sha256": OFFICIAL_SHA256,
            "parent": rust["parent"],
            "independent_tables": rust["independent_count"],
            "cumulative_tables": rust["cumulative_count"],
            "independent_sums": independent_sums,
            "rust_vs_openmc_worst_relative": worst_yield_relative,
            "mismatches": yield_mismatches[:20],
            "openmc_version": openmc.__version__,
        },
        "explicit_composition": {
            "neutron_mass_u": NEUTRON_MASS_U,
            "cases": composition_rows,
            "aliases_normalized": aliases_normalized,
            "worst_relative": worst_composition_relative,
        },
        "fail_closed": failure_checks,
        "failure_messages": failures,
    }
    output["pass"] = bool(
        output["official_u235"]["sha256"] == OFFICIAL_SHA256
        and not yield_mismatches
        and worst_yield_relative <= 1.0e-12
        and len(independent_sums) == 3
        and all(row["deviation_from_two"] <= 1.0e-6 for row in independent_sums)
        and aliases_normalized
        and worst_composition_relative <= 1.0e-12
        and all(failure_checks.values())
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g1_p9_composition_yields.json", output)
    print(json.dumps({**output, "failure_messages": "recorded in result JSON"}, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
