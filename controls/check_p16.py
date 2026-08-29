#!/usr/bin/env python3
"""Independently rederive the frozen P16 typed-boundary verdict."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols/ACTINV-P16_PROTOCOL.md"
PROTOCOL_SHA256 = "58d9debbb3e8892ab0ad0bf3642cba5fc1afa31ffbc1079cd26095c5d0e2ce19"
OPENING_COMMIT = "0332779401363d2f39722efe7a0b7218afcfb270"
QUANTITIES = RESULTS / "g1_p16_quantities.json"
METAMORPHIC = RESULTS / "g2_p16_metamorphic.json"
COMPATIBILITY = RESULTS / "g3_p16_compatibility.json"
PERFORMANCE = RESULTS / "p16_performance.json"
QUALITY = RESULTS / "p16_quality.json"
SESSION = RESULTS / "session_p16.json"
VERDICT = RESULTS / "verdict_p16.json"

SCALAR_TYPES = (
    "Seconds",
    "ElectronVolts",
    "Kelvin",
    "Grams",
    "AtomsPerGram",
    "ParticleFlux",
    "FluxMultiplier",
    "ParticleFluence",
    "CrossSectionBarns",
    "RatePerBarnSecond",
    "RatePerSecond",
)
FAILURE_FIXTURES = {
    "fail_time_energy.rs": ("Seconds", "ElectronVolts"),
    "fail_mass_temperature.rs": ("Grams", "Kelvin"),
    "fail_flux_multiplier.rs": ("ParticleFlux", "FluxMultiplier"),
    "fail_cross_section_rate.rs": ("RatePerBarnSecond", "Seconds"),
    "fail_grams_atoms.rs": ("AtomsPerGram", "Grams"),
    "fail_energy_rate.rs": ("RatePerSecond", "ElectronVolts"),
}
DEPENDENCY_MANIFESTS = (
    "Cargo.lock",
    "Cargo.toml",
    "crates/actinv-cli/Cargo.toml",
    "crates/actinv-core/Cargo.toml",
    "crates/actinv-data/Cargo.toml",
    "python/Cargo.toml",
)
LIMITS = {
    "scaling": 5.0e-12,
    "analytic_decay": 5.0e-11,
    "atom_conservation": 5.0e-12,
    "schedule_split": 5.0e-11,
    "rebin_closure": 1.0e-12,
    "rebin_scaling": 5.0e-15,
    "mode_parent": 1.0e-10,
    "mode_first_order": 0.25,
    "mesh_scaling": 5.0e-12,
}
RELATIONS = {
    "analytic_decay",
    "duration_spellings",
    "mesh_rebin",
    "mode_limit",
    "scaling",
    "schedule_splitting",
}
COMPATIBILITY_CASES = {
    "trace",
    "coupled",
    "charged_proton",
    "uncertainty",
    "radiological",
}
REJECTION_CASES = {
    "mass",
    "temperature",
    "threshold",
    "group_flux",
    "gamma_cutoff",
}
EXPECTED_INPUTS = {
    "activation_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "activation_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
}
THREADS = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "RAYON_NUM_THREADS": "1",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def canonical_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def close(left: object, right: object, *, rel_tol: float = 2e-12, abs_tol: float = 1e-18) -> bool:
    return finite_number(left) and finite_number(right) and math.isclose(
        float(left), float(right), rel_tol=rel_tol, abs_tol=abs_tol
    )


def relative(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0e-300)


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def protocol_check() -> dict[str, object]:
    actual = sha256(PROTOCOL) if PROTOCOL.is_file() else None
    expected_line = f"{PROTOCOL_SHA256}  protocols/ACTINV-P16_PROTOCOL.md"
    try:
        ledger = (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        ledger = []
    return {
        "expected_sha256": PROTOCOL_SHA256,
        "actual_sha256": actual,
        "ledger_entry": expected_line in ledger,
        "pass": actual == PROTOCOL_SHA256 and expected_line in ledger,
    }


def opening_file(relative: str) -> bytes | None:
    completed = subprocess.run(
        ["git", "show", f"{OPENING_COMMIT}:{relative}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else None


def source_contract() -> dict[str, object]:
    quantity_path = ROOT / "crates/actinv-core/src/quantity.rs"
    document_path = ROOT / "docs/QUANTITIES.md"
    try:
        source = quantity_path.read_text(encoding="utf-8")
        documentation = document_path.read_text(encoding="utf-8")
    except OSError:
        return {"checks": {"files_present": False}, "pass": False}

    layouts = {
        name: bool(re.search(rf"#\[repr\(transparent\)\]\s+pub struct {name}\(f64\);", source))
        for name in SCALAR_TYPES
    }
    documented = {name: f"`{name}`" in documentation for name in SCALAR_TYPES}
    boundary_tokens = (
        "material.mass_g",
        "options.temperature_K",
        "options.bmin_atoms_per_g",
        "schedule[].dt",
        "schedule[].flux",
        "photon.gamma_constant_cutoff_eV",
        "fission_yields.fixed_energy_eV",
        "CrossSectionBarns::from_collapsed_kernel",
        "RatePerBarnSecond::from_particle_flux",
        "RatePerSecond::get",
    )
    barn_occurrences: list[dict[str, object]] = []
    unsafe_blocks: list[dict[str, object]] = []
    for path in sorted((ROOT / "crates").rglob("*.rs")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if path.parts[-3:-1] == ("actinv-core", "src") and re.search(
                r"1(?:\.0)?e-24", line, flags=re.IGNORECASE
            ):
                barn_occurrences.append(
                    {"path": path.relative_to(ROOT).as_posix(), "line": line_number}
                )
            if re.search(r"\bunsafe\s*(?:\{|fn\b|impl\b|trait\b)", line):
                unsafe_blocks.append(
                    {"path": path.relative_to(ROOT).as_posix(), "line": line_number}
                )
    source_files = {
        relative: (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "crates/actinv-core/src/spec.rs",
            "crates/actinv-core/src/run.rs",
            "crates/actinv-core/src/prune.rs",
            "crates/actinv-core/src/chain.rs",
        )
    }
    wiring = {
        "spec_view": "pub(crate) fn physical_inputs(&self) -> Result<PhysicalInputs, String>"
        in source_files["crates/actinv-core/src/spec.rs"],
        "shared_prepare": "Self::prepare_profiled(spec, &physical, &mut profiler)"
        in source_files["crates/actinv-core/src/run.rs"],
        "typed_prune": "crate::prune::reachable_physical("
        in source_files["crates/actinv-core/src/run.rs"],
        "typed_rate": "RatePerBarnSecond::from_particle_flux"
        in source_files["crates/actinv-core/src/chain.rs"],
        "typed_cross_section": "CrossSectionBarns::from_collapsed_kernel"
        in source_files["crates/actinv-core/src/chain.rs"],
    }
    source_diff = subprocess.run(
        ["git", "diff", "--unified=0", OPENING_COMMIT, "--", "crates"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    workaround_pattern = re.compile(
        r"\b(?:Arc|Mutex|RefCell|UnsafeCell)\b|\bCell\s*<|\.clone\s*\("
    )
    new_workaround_lines = [
        line[1:].strip()
        for line in source_diff.stdout.splitlines()
        if line.startswith("+")
        and not line.startswith("+++")
        and workaround_pattern.search(line[1:])
    ]
    checks = {
        "layouts": all(layouts.values()),
        "documented_types": all(documented.values()),
        "documented_boundaries": all(token in documentation for token in boundary_tokens),
        "no_blanket_scalar_conversion": re.search(
            r"impl\s+(?:From<f64>|Into<f64>)\s+for", source
        )
        is None,
        "one_barn_conversion": len(barn_occurrences) == 1
        and barn_occurrences[0]["path"] == "crates/actinv-core/src/quantity.rs",
        "production_wiring": all(wiring.values()),
        "no_unsafe": not unsafe_blocks,
        "no_new_clone_or_shared_mutability_workaround": source_diff.returncode == 0
        and not new_workaround_lines,
    }
    return {
        "checks": checks,
        "layouts": layouts,
        "documented": documented,
        "barn_factor_occurrences": barn_occurrences,
        "wiring": wiring,
        "unsafe_blocks": unsafe_blocks,
        "new_workaround_lines": new_workaround_lines,
        "pass": all(checks.values()),
    }


def dependency_contract(value: object) -> bool:
    if not isinstance(value, dict) or value.get("pass") is not True:
        return False
    rows = value.get("files")
    if not isinstance(rows, list) or len(rows) != len(DEPENDENCY_MANIFESTS):
        return False
    by_path = {row.get("path"): row for row in rows if isinstance(row, dict)}
    if set(by_path) != set(DEPENDENCY_MANIFESTS):
        return False
    for relative in DEPENDENCY_MANIFESTS:
        opening = opening_file(relative)
        path = ROOT / relative
        if opening is None or not path.is_file():
            return False
        current_hash = sha256(path)
        opening_hash = sha256_bytes(opening)
        row = by_path[relative]
        if not (
            row.get("opening_sha256") == opening_hash
            and row.get("current_sha256") == current_hash
            and opening_hash == current_hash
            and row.get("equal") is True
        ):
            return False
    return True


def derive_quantities(value: dict[str, Any] | None) -> dict[str, object]:
    if value is None:
        return {"checks": {"evidence_present": False}, "pass": False}
    contract = source_contract()
    evidence_contract = value.get("source_contract", {})
    fixtures = value.get("consumer_fixtures", {})
    failures = fixtures.get("compile_fail", {}) if isinstance(fixtures, dict) else {}
    positive = fixtures.get("positive", {}) if isinstance(fixtures, dict) else {}
    legacy = fixtures.get("legacy", {}) if isinstance(fixtures, dict) else {}
    rust = value.get("rust_checks", {})
    rust_commands = rust.get("commands", {}) if isinstance(rust, dict) else {}
    protocol = value.get("protocol", {})

    failure_checks = isinstance(failures, dict) and set(failures) == set(FAILURE_FIXTURES)
    if failure_checks:
        for filename, expected_types in FAILURE_FIXTURES.items():
            row = failures[filename]
            codes = row.get("diagnostic_codes", []) if isinstance(row, dict) else []
            failure_checks = failure_checks and bool(
                isinstance(row, dict)
                and isinstance(row.get("returncode"), int)
                and row["returncode"] != 0
                and isinstance(codes, list)
                and bool(set(codes) & {"E0277", "E0308"})
                and row.get("expected_types") == list(expected_types)
                and row.get("expected_types_named") is True
                and canonical_sha(row.get("diagnostic_sha256"))
                and row.get("pass") is True
            )

    evidence_layouts = evidence_contract.get("repr_transparent_private_f64", {})
    evidence_docs = evidence_contract.get("documented", {})
    evidence_wiring = evidence_contract.get("wiring", {})
    checks = {
        "schema": value.get("schema") == "actinv-p16-quantities-1"
        and value.get("gate") == "P16-G1-G2",
        "reported_pass": value.get("pass") is True,
        "opening_commit": value.get("opening_commit") == OPENING_COMMIT,
        "compiler_recorded": isinstance(value.get("cargo"), str)
        and value["cargo"].startswith("cargo "),
        "protocol": isinstance(protocol, dict)
        and protocol.get("expected_sha256") == PROTOCOL_SHA256
        and protocol.get("actual_sha256") == PROTOCOL_SHA256
        and protocol.get("logged") is True
        and protocol.get("pass") is True,
        "source_hash": value.get("quantity_source_sha256")
        == sha256(ROOT / "crates/actinv-core/src/quantity.rs"),
        "document_hash": value.get("quantity_document_sha256")
        == sha256(ROOT / "docs/QUANTITIES.md"),
        "independent_source_contract": contract.get("pass") is True,
        "evidence_source_contract": isinstance(evidence_contract, dict)
        and evidence_contract.get("scalar_types") == list(SCALAR_TYPES)
        and evidence_layouts == contract.get("layouts")
        and evidence_docs == contract.get("documented")
        and evidence_contract.get("blanket_scalar_conversion_absent") is True
        and evidence_contract.get("barn_factor_occurrences")
        == contract.get("barn_factor_occurrences")
        and evidence_wiring == contract.get("wiring")
        and evidence_contract.get("unsafe_blocks") == []
        and evidence_contract.get("pass") is True,
        "dependency_identity": dependency_contract(value.get("dependency_manifests")),
        "positive_consumer": isinstance(positive, dict)
        and positive.get("returncode") == 0
        and positive.get("stdout") == "p16-quantity-pass"
        and canonical_sha(positive.get("stderr_sha256"))
        and positive.get("pass") is True,
        "legacy_consumer": isinstance(legacy, dict)
        and legacy.get("returncode") == 0
        and canonical_sha(legacy.get("stderr_sha256"))
        and legacy.get("pass") is True,
        "compile_failures": failure_checks,
        "fixture_summary": isinstance(fixtures, dict) and fixtures.get("pass") is True,
        "rust_checks": isinstance(rust_commands, dict)
        and set(rust_commands) == {"quantity_tests", "doctests"}
        and all(
            isinstance(row, dict)
            and row.get("returncode") == 0
            and row.get("pass") is True
            and canonical_sha(row.get("stdout_sha256"))
            and canonical_sha(row.get("stderr_sha256"))
            for row in rust_commands.values()
        )
        and rust.get("pass") is True,
    }
    return {"checks": checks, "source_contract": contract, "pass": all(checks.values())}


def derive_scaling(row: object) -> bool:
    if not isinstance(row, dict) or row.get("pass") is not True or row.get("limit") != LIMITS["scaling"]:
        return False
    values = row.get("scaled_values", {})
    errors = row.get("scaling_relative_errors", {})
    if not isinstance(values, dict) or set(values) != {"0.5", "1.0", "2.0"}:
        return False
    if not isinstance(errors, dict) or set(errors) != {"0.5", "2.0"}:
        return False
    calculated: list[float] = []
    for factor in ("0.5", "2.0"):
        if not isinstance(values[factor], dict) or not isinstance(errors[factor], dict):
            return False
        if set(values[factor]) != {"activity", "heat", "inventory"}:
            return False
        if set(errors[factor]) != set(values[factor]):
            return False
        for quantity in values[factor]:
            actual = float(values[factor][quantity])
            reference = float(values["1.0"][quantity]) * float(factor)
            derived = relative(actual, reference)
            if not close(errors[factor][quantity], derived):
                return False
            calculated.append(derived)
    fixed = row.get("fixed_fluence_relative_errors", {})
    mass = row.get("mass_per_gram_bit_identity", {})
    return bool(
        isinstance(fixed, dict)
        and set(fixed) == {"Ba141", "Kr92", "Sr100"}
        and all(finite_number(item) and float(item) <= LIMITS["scaling"] for item in fixed.values())
        and close(row.get("maximum_fixed_fluence_relative"), max(map(float, fixed.values())))
        and close(row.get("maximum_scaling_relative"), max(calculated))
        and max(calculated) <= LIMITS["scaling"]
        and isinstance(mass, dict)
        and set(mass) == {"activity_Bq_per_g", "heat_W_per_g", "inventory"}
        and all(item is True for item in mass.values())
    )


def derive_analytic_decay(row: object) -> bool:
    if not isinstance(row, dict) or row.get("pass") is not True:
        return False
    half_life = row.get("half_life_s")
    decay_constant = row.get("decay_constant_per_s")
    cases = row.get("cases", {})
    if not close(half_life, 100.0) or not close(decay_constant, math.log(2.0) / 100.0):
        return False
    if not isinstance(cases, dict) or set(cases) != {"1", "2", "3"}:
        return False
    state_errors: list[float] = []
    conservation_errors: list[float] = []
    for count in (1, 2, 3):
        case = cases[str(count)]
        if not isinstance(case, dict):
            return False
        duration = 100.0 * count
        expected_parent = 1.0e20 * math.exp(-float(decay_constant) * duration)
        expected_daughter = 1.0e20 - expected_parent
        calculated_parent = case.get("calculated_parent")
        calculated_daughter = case.get("calculated_daughter")
        if not all(finite_number(item) for item in (calculated_parent, calculated_daughter)):
            return False
        parent_error = relative(float(calculated_parent), expected_parent)
        daughter_error = relative(float(calculated_daughter), expected_daughter)
        conservation = relative(float(calculated_parent) + float(calculated_daughter), 1.0e20)
        if not (
            close(case.get("duration_s"), duration)
            and close(case.get("expected_parent"), expected_parent)
            and close(case.get("expected_daughter"), expected_daughter)
            and close(case.get("parent_relative"), parent_error)
            and close(case.get("daughter_relative"), daughter_error)
            and close(case.get("atom_conservation_relative"), conservation)
        ):
            return False
        state_errors.extend((parent_error, daughter_error))
        conservation_errors.append(conservation)
    return bool(
        row.get("state_limit") == LIMITS["analytic_decay"]
        and row.get("conservation_limit") == LIMITS["atom_conservation"]
        and close(row.get("maximum_state_relative"), max(state_errors))
        and close(row.get("maximum_atom_conservation_relative"), max(conservation_errors))
        and max(state_errors) <= LIMITS["analytic_decay"]
        and max(conservation_errors) <= LIMITS["atom_conservation"]
    )


def derive_schedule(row: object) -> bool:
    if not isinstance(row, dict) or row.get("pass") is not True or row.get("limit") != LIMITS["schedule_split"]:
        return False
    families = row.get("families", {})
    if not isinstance(families, dict) or set(families) != {"decay", "trace_source", "coupled_depletion"}:
        return False
    all_errors: list[float] = []
    for family in families.values():
        if not isinstance(family, dict):
            return False
        values = family.get("final_values", {})
        errors = family.get("relative_errors", {})
        if not isinstance(values, dict) or set(values) != {"unsplit", "two", "three"}:
            return False
        if not isinstance(errors, dict) or set(errors) != {"two", "three"}:
            return False
        names = set(values["unsplit"])
        if not names or any(set(values[partition]) != names for partition in ("two", "three")):
            return False
        family_errors = []
        for partition in ("two", "three"):
            if set(errors[partition]) != names:
                return False
            for name in names:
                derived = relative(
                    float(values[partition][name]), float(values["unsplit"][name])
                )
                if not close(errors[partition][name], derived):
                    return False
                family_errors.append(derived)
        if not close(family.get("maximum_relative"), max(family_errors)):
            return False
        all_errors.extend(family_errors)
    return close(row.get("maximum_relative"), max(all_errors)) and max(all_errors) <= LIMITS["schedule_split"]


def derive_mode(row: object) -> bool:
    if not isinstance(row, dict) or row.get("pass") is not True:
        return False
    tau = row.get("optical_depth")
    products = row.get("product_relative_differences", {})
    absolute = row.get("product_absolute_differences", {})
    if not close(tau, 1.0e-8) or not isinstance(products, dict) or not isinstance(absolute, dict):
        return False
    if set(products) != {"Ba141", "Kr92", "Sr100"} or set(absolute) != set(products):
        return False
    measured = max(map(float, products.values()))
    maximum_absolute = max(map(float, absolute.values()))
    analytic = 1.0 - (-math.expm1(-float(tau))) / float(tau)
    first_order = relative(measured, analytic)
    expected_parent = 1.0e20 * math.exp(-float(tau))
    calculated_parent = row.get("calculated_parent")
    if not finite_number(calculated_parent):
        return False
    parent_error = relative(float(calculated_parent), expected_parent)
    floor = row.get("reported_numerical_floor")
    return bool(
        all(finite_number(item) and float(item) >= 0.0 for item in products.values())
        and all(finite_number(item) and float(item) >= 0.0 for item in absolute.values())
        and close(row.get("maximum_product_relative"), measured)
        and close(row.get("maximum_product_absolute"), maximum_absolute)
        and close(row.get("analytic_first_order_relative_difference"), analytic)
        and close(row.get("first_order_agreement_relative"), first_order)
        and row.get("first_order_limit") == LIMITS["mode_first_order"]
        and first_order <= LIMITS["mode_first_order"]
        and close(row.get("expected_parent"), expected_parent)
        and close(row.get("parent_relative"), parent_error)
        and row.get("parent_limit") == LIMITS["mode_parent"]
        and parent_error <= LIMITS["mode_parent"]
        and finite_number(floor)
        and maximum_absolute <= float(floor)
        and row.get("absolute_below_floor") is True
    )


def derive_mesh_case(case: object, *, exact: bool) -> bool:
    if not isinstance(case, dict):
        return False
    spectra = case.get("spectra")
    footer = case.get("footer", {})
    identities = case.get("ordinary_identity")
    if not isinstance(spectra, list) or not spectra or not isinstance(footer, dict):
        return False
    try:
        expected_total = math.fsum(math.fsum(map(float, spectrum)) for spectrum in spectra)
    except (TypeError, ValueError):
        return False
    source = footer.get("source_flux_sum_over_cells")
    destination = footer.get("destination_flux_sum_over_cells")
    underflow = footer.get("underflow_sum_over_cells")
    overflow = footer.get("overflow_sum_over_cells")
    if not all(finite_number(item) for item in (source, destination, underflow, overflow)):
        return False
    source_error = relative(float(source), expected_total)
    destination_error = relative(
        float(destination) + float(underflow) + float(overflow), expected_total
    )
    return bool(
        canonical_sha(case.get("canonical_sha256"))
        and close(case.get("expected_total"), expected_total)
        and footer.get("cell_count") == len(spectra)
        and footer.get("record") == "footer"
        and close(case.get("source_footer_relative"), source_error)
        and close(case.get("destination_footer_relative"), destination_error)
        and source_error <= LIMITS["rebin_closure"]
        and destination_error <= LIMITS["rebin_closure"]
        and isinstance(identities, list)
        and len(identities) == len(spectra)
        and all(item is True for item in identities)
        and case.get("thread_identity") is True
        and ((len(case.get("source_boundaries_eV", [])) == 2) if exact else True)
    )


def derive_mesh(row: object) -> bool:
    if not isinstance(row, dict) or row.get("pass") is not True:
        return False
    errors = row.get("rebin_scale_relative_errors", {})
    if not isinstance(errors, dict) or set(errors) != {"0->1", "1->3"}:
        return False
    flat_errors: list[float] = []
    for values in errors.values():
        if not isinstance(values, dict) or set(values) != {
            "source_total",
            "destination_total",
            "underflow",
            "overflow",
        }:
            return False
        if not all(finite_number(item) and float(item) >= 0.0 for item in values.values()):
            return False
        flat_errors.extend(map(float, values.values()))
    exact = row.get("exact_grid", {})
    split = row.get("split_grid", {})
    split_closures = [
        float(split.get("source_footer_relative", math.inf)),
        float(split.get("destination_footer_relative", math.inf)),
    ] if isinstance(split, dict) else [math.inf]
    return bool(
        row.get("closure_limit") == LIMITS["rebin_closure"]
        and row.get("rebin_scaling_limit") == LIMITS["rebin_scaling"]
        and row.get("footer_scaling_limit") == LIMITS["mesh_scaling"]
        and derive_mesh_case(exact, exact=True)
        and derive_mesh_case(split, exact=False)
        and row.get("exact_grid_copy_bit_identity") is True
        and row.get("ordinary_thread_repeated_identity") is True
        and close(row.get("maximum_rebin_scale_relative"), max(flat_errors))
        and max(flat_errors) <= LIMITS["rebin_scaling"]
        and close(row.get("maximum_split_closure"), max(split_closures))
        and max(split_closures) <= LIMITS["rebin_closure"]
        and finite_number(row.get("maximum_footer_scaling_relative"))
        and float(row["maximum_footer_scaling_relative"]) <= LIMITS["mesh_scaling"]
    )


def derive_durations(row: object) -> bool:
    if not isinstance(row, dict) or row.get("pass") is not True:
        return False
    accepted = row.get("accepted_seconds", {})
    rejected = row.get("rejected", {})
    expected_accepted = {
        "300 s",
        "300s",
        "5 min",
        "5min",
        "0.08333333333333333 h",
    }
    expected_rejected = {"-1 s", "nan s", "inf s", "1 fortnight"}
    if not isinstance(accepted, dict) or set(accepted) != expected_accepted:
        return False
    if not isinstance(rejected, dict) or set(rejected) != expected_rejected:
        return False
    differences = [abs(float(value) - 300.0) for value in accepted.values()]
    ulp = math.ulp(300.0)
    return bool(
        all(finite_number(value) for value in accepted.values())
        and close(row.get("maximum_absolute_difference_s"), max(differences))
        and close(row.get("ulp_at_300_s"), ulp)
        and max(differences) <= ulp
        and all(
            isinstance(item, dict)
            and isinstance(item.get("returncode"), int)
            and item["returncode"] != 0
            and item.get("no_result") is True
            and isinstance(item.get("message"), str)
            and item["message"]
            and item.get("pass") is True
            for item in rejected.values()
        )
    )


def derive_relation_run(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"checks": {"present": False}, "pass": False}
    relations = value.get("relations", {})
    plants = value.get("planted_comparator_rejections", {})
    checks = {
        "relation_set": isinstance(relations, dict) and set(relations) == RELATIONS,
        "scaling": derive_scaling(relations.get("scaling")) if isinstance(relations, dict) else False,
        "analytic_decay": derive_analytic_decay(relations.get("analytic_decay")) if isinstance(relations, dict) else False,
        "schedule_splitting": derive_schedule(relations.get("schedule_splitting")) if isinstance(relations, dict) else False,
        "mode_limit": derive_mode(relations.get("mode_limit")) if isinstance(relations, dict) else False,
        "mesh_rebin": derive_mesh(relations.get("mesh_rebin")) if isinstance(relations, dict) else False,
        "duration_spellings": derive_durations(relations.get("duration_spellings")) if isinstance(relations, dict) else False,
        "plants": isinstance(plants, dict)
        and set(plants)
        == {
            "scaling",
            "analytic_decay",
            "schedule_splitting",
            "mode_limit",
            "mesh_identity",
            "mesh_rebin",
            "duration_spellings",
        }
        and all(item is True for item in plants.values()),
        "reported_pass": value.get("pass") is True,
    }
    return {"checks": checks, "pass": all(checks.values())}


def derive_metamorphic(value: dict[str, Any] | None) -> dict[str, object]:
    if value is None:
        return {"checks": {"evidence_present": False}, "pass": False}
    runs = value.get("runs", {})
    binaries = value.get("binaries", {})
    fixture = value.get("fixture", {})
    derived_runs = {
        name: derive_relation_run(runs.get(name)) if isinstance(runs, dict) else {"pass": False}
        for name in ("release", "candidate")
    }
    checks = {
        "schema": value.get("schema") == "actinv-p16-metamorphic-1"
        and value.get("gate") == "P16-G3",
        "reported_pass": value.get("pass") is True,
        "limits": value.get("limits") == LIMITS,
        "binary_set": isinstance(binaries, dict) and set(binaries) == {"release", "candidate"}
        and all(
            isinstance(item, dict)
            and canonical_sha(item.get("sha256"))
            and isinstance(item.get("bytes"), int)
            and item["bytes"] > 0
            and item.get("version") == "actinv 1.0.1"
            for item in binaries.values()
        ),
        "fixture_hashes": isinstance(fixture, dict)
        and bool(fixture)
        and all(
            isinstance(item, dict) and canonical_sha(item.get("sha256"))
            for item in fixture.values()
        ),
        "run_set": isinstance(runs, dict) and set(runs) == {"release", "candidate"},
        "release_relations": derived_runs["release"].get("pass") is True,
        "candidate_relations": derived_runs["candidate"].get("pass") is True,
        "not_loosened": value.get("release_relations_not_loosened") is True,
    }
    return {"checks": checks, "runs": derived_runs, "pass": all(checks.values())}


def derive_compatibility(value: dict[str, Any] | None) -> dict[str, object]:
    if value is None:
        return {"checks": {"evidence_present": False}, "pass": False}
    cases = value.get("cases", {})
    case_pass = isinstance(cases, dict) and set(cases) == COMPATIBILITY_CASES
    if case_pass:
        for row in cases.values():
            if not isinstance(row, dict):
                case_pass = False
                break
            release_hashes = row.get("release_sha256", {})
            candidate_hashes = row.get("candidate_sha256", {})
            entry_keys = {"cli", "prepared", "python"}
            case_pass = case_pass and bool(
                isinstance(release_hashes, dict)
                and isinstance(candidate_hashes, dict)
                and set(release_hashes) == entry_keys
                and set(candidate_hashes) == entry_keys
                and len(set(release_hashes.values())) == 1
                and release_hashes == candidate_hashes
                and all(canonical_sha(item) for item in release_hashes.values())
                and row.get("release_entry_points")
                == {"cli_prepared": True, "cli_python": True}
                and row.get("candidate_entry_points")
                == {"cli_prepared": True, "cli_python": True}
                and row.get("release_candidate_exact") is True
                and row.get("pass") is True
            )

    mesh = value.get("mesh", {})
    rejected = value.get("rejected_input_corpus", {})
    rejected_cases = rejected.get("cases", {}) if isinstance(rejected, dict) else {}
    rejection_pass = isinstance(rejected_cases, dict) and set(rejected_cases) == REJECTION_CASES
    if rejection_pass:
        for row in rejected_cases.values():
            binaries = row.get("binaries", {}) if isinstance(row, dict) else {}
            release = binaries.get("release", {}) if isinstance(binaries, dict) else {}
            candidate = binaries.get("candidate", {}) if isinstance(binaries, dict) else {}
            rejection_pass = rejection_pass and bool(
                isinstance(release, dict)
                and isinstance(candidate, dict)
                and release == candidate
                and isinstance(release.get("returncode"), int)
                and release["returncode"] != 0
                and release.get("no_output") is True
                and release.get("context_present") is True
                and isinstance(release.get("message"), str)
                and release["message"]
                and row.get("exact_rejection_parity") is True
                and row.get("pass") is True
            )
    executables = value.get("executables", {})
    checks = {
        "schema": value.get("schema") == "actinv-p16-compatibility-1"
        and value.get("gate") == "P16-G4",
        "reported_pass": value.get("pass") is True,
        "cases": case_pass,
        "mesh": isinstance(mesh, dict)
        and canonical_sha(mesh.get("release_sha256"))
        and mesh.get("release_sha256") == mesh.get("candidate_sha256")
        and mesh.get("release_candidate_exact") is True
        and mesh.get("pass") is True,
        "rejected_inputs": rejection_pass and rejected.get("pass") is True,
        "executables": isinstance(executables, dict)
        and set(executables)
        == {
            "release",
            "candidate",
            "prepared_release",
            "prepared_candidate",
            "python_release",
            "python_candidate",
        }
        and all(
            isinstance(item, dict)
            and canonical_sha(item.get("sha256"))
            and isinstance(item.get("bytes"), int)
            and item["bytes"] > 0
            for item in executables.values()
        ),
        "comparator_plant": value.get("comparator_plant_rejected") is True,
    }
    return {"checks": checks, "pass": all(checks.values())}


def input_checks(inputs: object) -> bool:
    return bool(
        isinstance(inputs, dict)
        and set(inputs) == set(EXPECTED_INPUTS)
        and all(
            isinstance(inputs[name], dict)
            and inputs[name].get("actual_sha256") == digest
            and inputs[name].get("expected_sha256") == digest
            and isinstance(inputs[name].get("bytes"), int)
            and inputs[name]["bytes"] > 0
            for name, digest in EXPECTED_INPUTS.items()
        )
    )


def derive_performance(value: dict[str, Any] | None) -> dict[str, object]:
    if value is None:
        return {"checks": {"evidence_present": False}, "pass": False}
    warm = value.get("warm", {})
    rss = value.get("peak_rss_bytes", {})
    ratios = value.get("ratios", {})
    limits = value.get("limits", {})
    statistics_pass = isinstance(warm, dict) and set(warm) == {"release", "candidate"}
    derived_stats: dict[str, dict[str, float]] = {}
    if statistics_pass:
        for name in ("release", "candidate"):
            row = warm[name]
            raw = row.get("raw_ms", []) if isinstance(row, dict) else []
            statistics_pass = statistics_pass and bool(
                isinstance(raw, list)
                and len(raw) == 15
                and all(finite_number(item) and float(item) > 0.0 for item in raw)
                and row.get("samples") == 15
            )
            if not statistics_pass:
                break
            values = list(map(float, raw))
            derived = {
                "minimum_ms": min(values),
                "median_ms": statistics.median(values),
                "p95_ms": quantile(values, 0.95),
                "mean_ms": statistics.fmean(values),
                "sample_standard_deviation_ms": statistics.stdev(values),
            }
            derived_stats[name] = derived
            statistics_pass = statistics_pass and all(
                close(row.get(key), expected) for key, expected in derived.items()
            )
    ratios_pass = False
    thresholds_pass = False
    effective_ceiling = None
    if statistics_pass and isinstance(rss, dict) and set(rss) == {"release", "candidate"}:
        if all(isinstance(item, int) and item > 0 for item in rss.values()):
            median_ratio = derived_stats["candidate"]["median_ms"] / derived_stats["release"]["median_ms"]
            p95_ratio = derived_stats["candidate"]["p95_ms"] / derived_stats["release"]["p95_ms"]
            rss_ratio = rss["candidate"] / rss["release"]
            effective_ceiling = max(1.10 * rss["release"], rss["release"] + 16 * 1024**2)
            ratios_pass = bool(
                isinstance(ratios, dict)
                and close(ratios.get("candidate_over_release_median"), median_ratio)
                and close(ratios.get("candidate_over_release_p95"), p95_ratio)
                and close(ratios.get("candidate_over_release_peak_rss"), rss_ratio)
            )
            thresholds_pass = median_ratio <= 1.10 and p95_ratio <= 1.15 and rss["candidate"] <= effective_ceiling
    binaries = value.get("binaries", {})
    checks = {
        "schema": value.get("schema") == "actinv-p16-performance-1"
        and value.get("gate") == "P16-G5",
        "reported_pass": value.get("pass") is True,
        "release_commit": value.get("release_commit") == OPENING_COMMIT
        and value.get("resolved_signed_tag_commit") == OPENING_COMMIT,
        "inputs": input_checks(value.get("inputs")),
        "sample_contract": value.get("warmups_per_binary") == 5
        and value.get("samples_per_binary") == 15,
        "statistics": statistics_pass,
        "limits": isinstance(limits, dict)
        and limits.get("median_ratio") == 1.10
        and limits.get("p95_ratio") == 1.15
        and limits.get("rss_ratio") == 1.10
        and limits.get("rss_absolute_allowance_bytes") == 16 * 1024**2
        and close(limits.get("effective_rss_ceiling_bytes"), effective_ceiling),
        "ratios": ratios_pass,
        "thresholds": thresholds_pass,
        "thread_environment": value.get("host", {}).get("thread_environment") == THREADS,
        "cold_and_rss_visible": isinstance(value.get("cold_cache_ms"), dict)
        and isinstance(value.get("rss_measurement_wall_ms"), dict)
        and all(
            finite_number(item) and float(item) > 0.0
            for item in (
                *value["cold_cache_ms"].values(),
                *value["rss_measurement_wall_ms"].values(),
            )
        ),
        "exact_result": canonical_sha(value.get("normalized_result_sha256"))
        and value.get("checks", {}).get("normalized_result_exact") is True,
        "binaries": isinstance(binaries, dict)
        and set(binaries) == {"release", "candidate"}
        and all(
            isinstance(item, dict)
            and canonical_sha(item.get("sha256"))
            and isinstance(item.get("bytes"), int)
            and item["bytes"] > 0
            for item in binaries.values()
        ),
        "comparator_plant": value.get("checks", {}).get("comparator_plant_rejected") is True,
        "reported_checks": isinstance(value.get("checks"), dict)
        and set(value["checks"])
        == {"samples", "median", "p95", "peak_rss", "normalized_result_exact", "comparator_plant_rejected"}
        and all(item is True for item in value["checks"].values()),
    }
    return {
        "checks": checks,
        "rederived": {"statistics": derived_stats, "effective_rss_ceiling_bytes": effective_ceiling},
        "pass": all(checks.values()),
    }


def cargo_path() -> str:
    configured = os.environ.get("CARGO")
    if configured:
        return configured
    candidate = Path.home() / ".cargo/bin/cargo"
    return str(candidate) if candidate.is_file() else "cargo"


def quality_commands() -> list[list[str]]:
    cargo = cargo_path()
    python = sys.executable
    return [
        [cargo, "fmt", "--all", "--", "--check"],
        [cargo, "check", "--workspace", "--all-targets", "--all-features"],
        [cargo, "clippy", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"],
        [cargo, "test", "--workspace", "--all-targets", "--all-features"],
        [cargo, "test", "-p", "actinv-core", "--doc", "--all-features"],
        [cargo, "fmt", "--manifest-path", "python/Cargo.toml", "--", "--check"],
        [cargo, "check", "--manifest-path", "python/Cargo.toml", "--all-targets", "--all-features"],
        [cargo, "clippy", "--manifest-path", "python/Cargo.toml", "--all-targets", "--all-features", "--", "-D", "warnings"],
        [cargo, "test", "--manifest-path", "python/Cargo.toml", "--all-targets", "--all-features"],
        [python, "controls/g1_p16_quantities.py", "--no-write"],
        [python, "controls/check_prior_verdicts.py"],
        [python, "controls/check_release_notes.py"],
        [python, "controls/check_dependencies.py"],
        [python, "controls/check_public_examples.py"],
        [python, "controls/check_p12.py", "--through-g5"],
        [python, "controls/check_p13.py", "--no-write"],
        [python, "controls/check_cb1.py", "--no-write"],
        [python, "controls/check_p14.py", "--no-write"],
        [python, "controls/check_p15.py", "--no-write"],
        [python, "controls/g1_self_contained.py"],
        [python, "controls/ci_end_to_end.py"],
        [python, "controls/g3_p12_parser_fuzz.py", "--smoke"],
        [python, "controls/g5_p8_mesh_identity.py"],
        [python, "controls/g3_p9_coupled_auto.py"],
        [python, "controls/g6_p10_projectile_runtime.py"],
    ]


def command_result(arguments: list[str], environment: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=900,
        check=False,
    )
    executable = Path(arguments[0]).name
    display = "cargo" if executable == "cargo" else "python" if executable.startswith("python") else executable
    return {
        "command": " ".join([display, *arguments[1:]]),
        "returncode": completed.returncode,
        "error_tail": "" if completed.returncode == 0 else "\n".join(
            (completed.stdout + completed.stderr).replace(str(ROOT), "<ROOT>").splitlines()[-30:]
        ),
        "pass": completed.returncode == 0,
    }


def run_quality() -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(THREADS)
    environment["CARGO"] = cargo_path()
    environment["PATH"] = str(Path(cargo_path()).resolve().parent) + os.pathsep + environment.get("PATH", "")
    environment["TMPDIR"] = str(ROOT / "target/tmp")
    environment["ACTINV_BIN"] = str(ROOT / "target/release/actinv")
    environment["ACTINV_PYTHON_LIBRARY"] = str(ROOT / "python/target/release/libactinv.so")
    environment["ACTINV_CI_DATA"] = os.environ.get("ACTINV_CI_DATA", "/tmp/actinv-ci-data")
    environment["ACTINV_CI_OUT"] = str(ROOT / "target/p16-quality-ci")
    environment["ACTINV_CACHE_DIR"] = str(ROOT / "target/p16-quality-cache")
    environment["ACTINV_P8_WORK"] = str(ROOT / "target/p16-quality-p8")
    environment["ACTINV_P9_WORK"] = str(ROOT / "target/p16-quality-p9")
    environment["ACTINV_P9_RESULTS"] = str(ROOT / "target/p16-quality-p9-results")
    environment["ACTINV_P10_WORK"] = str(ROOT / "target/p16-quality-p10")
    records = []
    commands = quality_commands()
    for position, command in enumerate(commands, 1):
        print(f"P16 quality {position}/{len(commands)}: {' '.join(command[1:])}", flush=True)
        records.append(command_result(command, environment))
    result = {
        "schema": "actinv-p16-quality-1",
        "commands": records,
        "pass": all(record["pass"] for record in records),
    }
    QUALITY.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return result


def closure_source_contract() -> dict[str, object]:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    checks = {
        "p16_ci_control": "controls/g1_p16_quantities.py --no-write" in ci,
        "p16_ci_checker": "controls/check_p16.py --no-write" in ci,
        "quantity_document": (ROOT / "docs/QUANTITIES.md").is_file(),
        "protocol_control": source_contract().get("pass") is True,
    }
    return {"checks": checks, "pass": all(checks.values())}


def evidence_plant_checks(
    quantities: dict[str, Any] | None,
    metamorphic: dict[str, Any] | None,
    compatibility: dict[str, Any] | None,
    performance: dict[str, Any] | None,
) -> dict[str, bool]:
    if None in (quantities, metamorphic, compatibility, performance):
        return {"evidence_present": False}
    planted_quantity_hash = copy.deepcopy(quantities)
    planted_quantity_hash["quantity_source_sha256"] = "0" * 64
    planted_compile = copy.deepcopy(quantities)
    planted_compile["consumer_fixtures"]["compile_fail"]["fail_time_energy.rs"]["returncode"] = 0
    planted_decay = copy.deepcopy(metamorphic)
    planted_decay["runs"]["candidate"]["relations"]["analytic_decay"]["cases"]["1"]["calculated_parent"] *= 1.01
    planted_mesh = copy.deepcopy(metamorphic)
    planted_mesh["runs"]["candidate"]["relations"]["mesh_rebin"]["exact_grid_copy_bit_identity"] = False
    planted_compatibility = copy.deepcopy(compatibility)
    planted_compatibility["cases"]["trace"]["candidate_sha256"]["cli"] = "0" * 64
    planted_rejection = copy.deepcopy(compatibility)
    planted_rejection["rejected_input_corpus"]["cases"]["mass"]["binaries"]["candidate"]["returncode"] = 0
    planted_performance = copy.deepcopy(performance)
    planted_performance["warm"]["candidate"]["raw_ms"][0] *= 2.0
    planted_input = copy.deepcopy(performance)
    planted_input["inputs"]["activation_library"]["actual_sha256"] = "0" * 64
    return {
        "quantity_source_hash": not derive_quantities(planted_quantity_hash)["pass"],
        "compile_result": not derive_quantities(planted_compile)["pass"],
        "analytic_value": not derive_metamorphic(planted_decay)["pass"],
        "mesh_identity": not derive_metamorphic(planted_mesh)["pass"],
        "compatibility_hash": not derive_compatibility(planted_compatibility)["pass"],
        "rejection_result": not derive_compatibility(planted_rejection)["pass"],
        "performance_statistic": not derive_performance(planted_performance)["pass"],
        "performance_input": not derive_performance(planted_input)["pass"],
    }


def derive_quality(full: bool, plants: dict[str, bool]) -> dict[str, object]:
    quality = run_quality() if full else load(QUALITY)
    commands = quality.get("commands", []) if quality else []
    source = closure_source_contract()
    expected_commands = [
        command_result_name(command) for command in quality_commands()
    ]
    actual_commands = [record.get("command") for record in commands if isinstance(record, dict)]
    checks = {
        "quality_record": quality is not None
        and quality.get("schema") == "actinv-p16-quality-1"
        and quality.get("pass") is True
        and len(commands) == len(expected_commands)
        and actual_commands == expected_commands
        and all(record.get("returncode") == 0 and record.get("pass") is True for record in commands),
        "evidence_plants": len(plants) == 8 and all(plants.values()),
        "source_contract": source.get("pass") is True,
    }
    return {
        "checks": checks,
        "quality": quality,
        "evidence_plants": plants,
        "source_contract": source,
        "pass": all(checks.values()),
    }


def command_result_name(arguments: list[str]) -> str:
    executable = Path(arguments[0]).name
    display = "cargo" if executable == "cargo" else "python" if executable.startswith("python") else executable
    return " ".join([display, *arguments[1:]])


def git_file_sha(commit: str, relative: str) -> str | None:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return hashlib.sha256(completed.stdout).hexdigest() if completed.returncode == 0 else None


def manifest_reproduces() -> bool:
    sys.path.insert(0, str(ROOT / "controls"))
    import g6_p12_complete

    try:
        return g6_p12_complete.manifest_evidence()["pass"] is True
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


def session_check(evidence_hashes: dict[str, str | None]) -> dict[str, object]:
    value = load(SESSION)
    if value is None:
        return {"present": False, "pass": False}
    commit = value.get("source_evidence_commit")
    workflow = value.get("workflow", {})
    valid_commit = isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) is not None
    paths = {
        "quantities": QUANTITIES,
        "metamorphic": METAMORPHIC,
        "compatibility": COMPATIBILITY,
        "performance": PERFORMANCE,
        "quality": QUALITY,
    }
    commit_hashes = {
        name: git_file_sha(commit, path.relative_to(ROOT).as_posix()) if valid_commit else None
        for name, path in paths.items()
    }
    checks = {
        "schema": value.get("schema") == "actinv-p16-session-1",
        "protocol": value.get("protocol_sha256") == PROTOCOL_SHA256,
        "source_commit": valid_commit,
        "workflow": isinstance(workflow, dict)
        and workflow.get("head_sha") == commit
        and workflow.get("status") == "completed"
        and workflow.get("conclusion") == "success"
        and workflow.get("name") == "controls"
        and isinstance(workflow.get("run_id"), int)
        and workflow["run_id"] > 0,
        "evidence_hashes": value.get("evidence_sha256") == evidence_hashes,
        "source_commit_evidence": commit_hashes == evidence_hashes,
        "manifest": manifest_reproduces(),
        "reported_pass": value.get("pass") is True and value.get("verdict") == "P16-PASS",
    }
    return {
        "present": True,
        "checks": checks,
        "source_commit_evidence_sha256": commit_hashes,
        "pass": all(checks.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="run every local P16 quality command")
    parser.add_argument("--no-write", action="store_true", help="derive without replacing the verdict")
    parser.add_argument("--require-closure", action="store_true", help="require a recorded green pushed workflow")
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()

    quantities = load(QUANTITIES)
    metamorphic = load(METAMORPHIC)
    compatibility = load(COMPATIBILITY)
    performance = load(PERFORMANCE)
    protocol = protocol_check()
    plants = evidence_plant_checks(quantities, metamorphic, compatibility, performance)
    gates = {
        "G1_G2": derive_quantities(quantities),
        "G3": derive_metamorphic(metamorphic),
        "G4": derive_compatibility(compatibility),
        "G5": derive_performance(performance),
        "G6": derive_quality(arguments.full, plants),
    }
    source_pass = protocol["pass"] and all(gate["pass"] for gate in gates.values())
    evidence_paths = {
        "quantities": QUANTITIES,
        "metamorphic": METAMORPHIC,
        "compatibility": COMPATIBILITY,
        "performance": PERFORMANCE,
        "quality": QUALITY,
    }
    evidence_hashes = {
        name: sha256(path) if path.is_file() else None for name, path in evidence_paths.items()
    }
    session = session_check(evidence_hashes)
    closed = source_pass and session["pass"]
    verdict = "P16-PASS" if closed else "P16-SOURCE-PASS" if source_pass else "P16-FAIL"
    output = {
        "schema": "actinv-p16-verdict-1",
        "protocol": protocol,
        "evidence_sha256": evidence_hashes,
        "gates": gates,
        "source_pass": source_pass,
        "session": session,
        "closed": closed,
        "verdict": verdict,
        "pass": closed if arguments.require_closure else source_pass,
    }
    if not arguments.no_write:
        VERDICT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    displayed = output if arguments.verbose else {
        "schema": output["schema"],
        "protocol": protocol["pass"],
        "gates": {name: gate["pass"] for name, gate in gates.items()},
        "performance_ratios": performance.get("ratios") if performance else None,
        "source_pass": source_pass,
        "session": session["pass"],
        "closed": closed,
        "verdict": verdict,
        "pass": output["pass"],
    }
    print(json.dumps(displayed, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
