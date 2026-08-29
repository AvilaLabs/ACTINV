#!/usr/bin/env python3
"""P17-G4: open diagnostic scoring and controlled attribution evidence.

This external control reads only the protocol's open IRDFF-II Tables 18--20
and the already-seen 132-experiment CoNDERC FNS family.  It executes the
unchanged ACTINV 1.0.1 path, preserves every source row, and writes compact
evidence for the data-independent checker.  No held-out table identifier is
accepted by this program.
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("ACTINV_CACHE_DIR", "/tmp/actinv-p17-g4-prepared")
sys.path.insert(0, str(ROOT / "controls"))
import cb1_fns as cb1  # noqa: E402
from harness import fispact_io as fio  # noqa: E402
from p17_irdff import diagnostic_rows as irdff_diagnostic_rows  # noqa: E402
from p17_scoring import (  # noqa: E402
    CAUSE_LEDGER_SCHEMA,
    PROTOCOL_SHA256,
    SCHEMA,
    all_family_metrics,
    canonical_sha256,
    cause_entry,
    make_input_set,
    make_row,
    mismatch_keys,
    score_calculation,
    unscored_calculation,
)


RESULT = ROOT / "results" / "g4_p17_diagnostics.json"
CAUSE_LEDGER = ROOT / "results" / "p17_cause_ledger.json"
REPORT = ROOT / "docs" / "P17_DIAGNOSTICS.md"
WORK = Path(os.environ.get("ACTINV_P17_G4_WORK", "/tmp/actinv-p17-g4-fns")).resolve()

DATA = Path.home() / "nuclear-data"
FNS = Path(os.environ.get("ACTINV_FNS_ROOT", DATA / "conderc-fns" / "fns")).resolve()
FNS_ARCHIVE = Path(
    os.environ.get("ACTINV_FNS_ARCHIVE", DATA / "conderc-fns" / "fns.zip")
).resolve()
LIBRARY = Path(
    os.environ.get(
        "ACTINV_LIBRARY", DATA / "tendl-2025" / "builds" / "full" / "neutron.n.p10.npz"
    )
).resolve()
LIBRARY_INDEX = LIBRARY.with_name(LIBRARY.stem + "_index.json")
DECAY_ENDF = Path(
    os.environ.get(
        "ACTINV_ENDF_DECAY", DATA / "endfb-viii.0-decay" / "bulk" / "endf-b-viii-0_decay.dat"
    )
).resolve()
DECAY_JEFF = Path(
    os.environ.get(
        "ACTINV_JEFF_DECAY", DATA / "jeff-3.3-decay" / "bulk" / "jeff-3-3_decay.dat"
    )
).resolve()
MODULE = Path(
    os.environ.get("ACTINV_PYTHON_MODULE", ROOT / "python" / "target" / "release" / "libactinv.so")
).resolve()

EXPECTED = {
    "fns_archive": "ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d",
    "library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "library_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "decay_endf": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_jeff": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    "actinv_module": "a5be3011ce63e3ff62748de47cefa7c1f6e163657a55b1531cece2928ec95400",
}
# Cache identity of the calculation-bearing implementation used for the fresh
# 132-experiment campaign.  Documentation/checker-only edits do not invalidate
# those certified scalar results; any change to specification, alignment, run,
# or row-calculation logic must update this value and rerun the campaign.
FNS_CALCULATION_IMPLEMENTATION_SHA256 = (
    "bbaa765537719ba8849c30a8ce02d71296f28f42b00193578bdef4c69c7076b4"
)

VARIANT_PRODUCTION = "actinv_tendl2025"
VARIANT_DECAY = "actinv_tendl2025_jeff_primary"
VARIANT_FISPACT_CONTEXT = "fispact4_tendl2017_published_context"
VARIANT_IRDFF = "irdff_ii_groupwise_diagnostic"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def relative(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0e-300)


def checked_global_inputs() -> dict[str, str]:
    paths = {
        "fns_archive": FNS_ARCHIVE,
        "library": LIBRARY,
        "library_index": LIBRARY_INDEX,
        "decay_endf": DECAY_ENDF,
        "decay_jeff": DECAY_JEFF,
        "actinv_module": MODULE,
    }
    identities = {}
    for role, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing P17 G4 input {role}: {path}")
        actual = sha256(path)
        if actual != EXPECTED[role]:
            raise RuntimeError(f"{role} changed: {actual}, expected {EXPECTED[role]}")
        identities[role] = actual
    if getattr(cb1.actinv, "__version__", None) != "1.0.1":
        raise RuntimeError(f"P17 must execute ACTINV 1.0.1, got {cb1.actinv.__version__!r}")
    return identities


def prior_gate_evidence() -> dict[str, Any]:
    output = {}
    for gate, filename in (
        ("numerical_solver", "g1_p17_operators.json"),
        ("chain_construction", "g2_p17_identical_data.json"),
        ("processor", "g3_p17_processing.json"),
    ):
        path = ROOT / "results" / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("pass"):
            raise RuntimeError(f"prior P17 evidence is not green: {filename}")
        output[gate] = {"path": f"results/{filename}", "sha256": sha256(path), "pass": True}
        if gate == "numerical_solver":
            output[gate]["maximum_meaningful_relative"] = data["worst"][
                "relative_above_tolerance_crossover"
            ]
            output[gate]["maximum_absolute_over_initial_norm"] = data["worst"][
                "absolute_over_initial_norm"
            ]
        elif gate == "chain_construction":
            output[gate]["maximum_rate_relative"] = data["maximum_rate_relative"]
            output[gate]["maximum_actinv_alara_relative"] = data[
                "maximum_actinv_alara_relative_reportable"
            ]
            output[gate]["maximum_actinv_cli_dense_relative"] = data[
                "maximum_actinv_cli_dense_relative_reportable"
            ]
        else:
            output[gate]["maximum_group_relative"] = data["summary"][
                "maximum_group_relative"
            ]
            output[gate]["maximum_one_group_relative"] = data["summary"][
                "maximum_one_group_relative"
            ]
    return output


def add_input_set(
    input_sets: dict[str, dict[str, Any]],
    identifier: str,
    hashes: dict[str, str],
    *,
    changed: set[str],
) -> str:
    if identifier in input_sets:
        raise ValueError(f"duplicate P17 input set {identifier}")
    input_sets[identifier] = make_input_set(
        input_hashes=hashes,
        identical_input_roles=set(hashes) - changed,
        changed_input_roles=changed,
    )
    return identifier


def irdff_rows(
    input_sets: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_rows, diagnostics = irdff_diagnostic_rows()
    implementation_hash = sha256(ROOT / "controls" / "p17_irdff.py")
    rows = []
    for source in source_rows:
        family = f"irdff_table_{source['table']}"
        production_set = f"{family}:production"
        official_set = f"{family}:irdff-groupwise"
        if production_set not in input_sets:
            common = {
                "measurement": diagnostics["input_identities"]["pdf"],
                "spectrum": diagnostics["input_identities"]["irdff_spectrum_archive"],
                "activation_data": diagnostics["input_identities"]["production_library"],
                "activation_data_index": diagnostics["input_identities"]["production_index"],
                "calculation_implementation": implementation_hash,
            }
            add_input_set(input_sets, production_set, common, changed=set())
            official = {
                **common,
                "activation_data": diagnostics["input_identities"]["irdff_group_archive"],
                "activation_data_index": diagnostics["input_identities"]["irdff_group_archive"],
            }
            add_input_set(
                input_sets,
                official_set,
                official,
                changed={"activation_data", "activation_data_index"},
            )

        calculations = {}
        if source["production_tendl2025_mb"] is None:
            calculations[VARIANT_PRODUCTION] = unscored_calculation(
                input_set_id=production_set,
                reason=source["production_reason"],
                interpretation="unchanged v1.0.1 TENDL-2025 production path",
            )
        else:
            calculations[VARIANT_PRODUCTION] = score_calculation(
                source["measured_mb"],
                source["production_tendl2025_mb"],
                input_set_id=production_set,
                interpretation="unchanged v1.0.1 TENDL-2025 production path",
            )
        if source["official_groupwise_mb"] is None:
            calculations[VARIANT_IRDFF] = unscored_calculation(
                input_set_id=official_set,
                reason="variant_reaction_unavailable",
                interpretation="IRDFF-II validation-derived diagnostic context",
            )
        else:
            calculations[VARIANT_IRDFF] = score_calculation(
                source["measured_mb"],
                source["official_groupwise_mb"],
                input_set_id=official_set,
                interpretation=(
                    "controlled activation-evaluation substitution; IRDFF-II is validation-derived "
                    "diagnostic context, not blind performance"
                ),
            )
        source_record = {
            key: source[key]
            for key in (
                "table",
                "table_row",
                "label",
                "E50_MeV",
                "measured_mb",
                "experimental_uncertainty_percent",
                "reference",
                "published_calculated_mb",
                "source_line",
                "mapping",
                "official_group_key",
                "production_mapping",
                "spectrum_mat",
            )
        }
        rows.append(
            make_row(
                row_id=f"irdff:t{source['table']}:r{source['table_row']:03d}",
                family=family,
                source_id=f"IRDFF-II:Table-{source['table']}:row-{source['table_row']}",
                source_record=source_record,
                observable="spectrum-averaged cross section",
                unit="mb",
                experimental_value=source["measured_mb"],
                experimental_uncertainty=source["experimental_uncertainty_percent"],
                experimental_uncertainty_unit="percent",
                inclusion_status="scored",
                inclusion_reason="scored",
                calculations=calculations,
            )
        )
    return rows, diagnostics


def fns_experiments() -> list[tuple[str, str]]:
    experiments = [
        (material.name, Path(path).stem)
        for material in sorted(path for path in FNS.iterdir() if path.is_dir())
        for path in sorted(glob.glob(str(material / "*.exp")))
    ]
    if len(experiments) != 132:
        raise RuntimeError(f"expected all 132 FNS experiments, found {len(experiments)}")
    return experiments


def fns_file_identities(material: str, experiment: str) -> dict[str, str]:
    directory = FNS / material
    paths = {
        "measurement": directory / f"{experiment}.exp",
        "schedule": directory / f"TENDL-2017_{experiment}.i",
        "spectrum": directory / f"{experiment}_fluxes",
        "published_reference": directory / f"TENDL-2017_{experiment}.nuclides",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing FNS experiment input: " + ", ".join(missing))
    return {role: sha256(path) for role, path in paths.items()}


def fns_cache_fingerprint(global_identities: dict[str, str]) -> str:
    identities = {
        **global_identities,
        "control": FNS_CALCULATION_IMPLEMENTATION_SHA256,
        "established_alignment": sha256(ROOT / "controls" / "cb1_fns.py"),
        "protocol": PROTOCOL_SHA256,
    }
    return canonical_sha256(identities)


def run_actinv_spec(spec: dict[str, Any]) -> tuple[list[float], dict[str, Any]]:
    result = json.loads(
        cb1.actinv.run(json.dumps(spec, allow_nan=False, separators=(",", ":"), sort_keys=True))
    )
    calculated = [step["heat_W_per_g"]["total"] * 1.0e6 for step in result["steps"][1:]]
    certificate = {
        role: entry["sha256"] if entry is not None else None
        for role, entry in result["certificate"]["inputs"].items()
        if role in {"library", "library_index", "decay_primary", "decay_fallback"}
    }
    return calculated, {
        "certificate_inputs": certificate,
        "mode": result["mode"],
        "total_states": result["total_states"],
        "pruned_states": result["pruned_states"],
    }


def run_fns_experiment(
    material: str,
    experiment: str,
    fingerprint: str,
) -> tuple[dict[str, Any], bool]:
    WORK.mkdir(parents=True, exist_ok=True)
    cache_path = WORK / f"{material}_{experiment}.json"
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fingerprint:
                return cached, True
        except (OSError, json.JSONDecodeError):
            pass

    spec, input_record = cb1.specification(material, experiment)
    production_values, production_run = run_actinv_spec(spec)
    decay_spec = json.loads(json.dumps(spec))
    decay_spec["title"] = f"P17 G4 FNS decay substitution {material} {experiment}"
    decay_spec["decay"] = {"primary": str(DECAY_JEFF), "fallback": str(DECAY_ENDF)}
    decay_values, decay_run = run_actinv_spec(decay_spec)
    cooling = [float(value) for value in input_record["cooling_cum_s"]]
    if len(production_values) != len(cooling) or len(decay_values) != len(cooling):
        raise RuntimeError(f"{material}/{experiment}: result and cooling lengths disagree")

    directory = FNS / material
    reference = fio.read_nuclides(directory / f"TENDL-2017_{experiment}.nuclides")
    reference_values = [float(value) for value in reference["total_kW_kg"][1:] * 1.0e6]
    if len(reference_values) < len(cooling):
        raise RuntimeError(f"{material}/{experiment}: published reference is too short")
    record = {
        "fingerprint": fingerprint,
        "cooling_s": cooling,
        "production_uW_g": production_values,
        "jeff_primary_uW_g": decay_values,
        "fispact_context_uW_g": reference_values[: len(cooling)],
        "production_run": production_run,
        "decay_run": decay_run,
    }
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(cache_path)
    return record, False


def alignment_reason(cb1_reason: str) -> str:
    return {
        "nonpositive_time": "nonpositive_measurement_time",
        "nonpositive_measurement": "nonpositive_experimental_value",
        "no_cooling_step_within_2_percent": "no_cooling_step_within_2_percent",
    }[cb1_reason]


def fns_input_sets(
    input_sets: dict[str, dict[str, Any]],
    material: str,
    experiment: str,
    file_hashes: dict[str, str],
    global_identities: dict[str, str],
) -> tuple[str, str, str]:
    common = {
        "measurement": file_hashes["measurement"],
        "schedule": file_hashes["schedule"],
        "spectrum": file_hashes["spectrum"],
        "activation_library": global_identities["library"],
        "activation_index": global_identities["library_index"],
        "decay_primary": global_identities["decay_endf"],
        "decay_fallback": global_identities["decay_jeff"],
        "runtime": global_identities["actinv_module"],
        "alignment_implementation": sha256(ROOT / "controls" / "cb1_fns.py"),
    }
    prefix = f"fns:{material}:{experiment}"
    production = add_input_set(input_sets, f"{prefix}:production", common, changed=set())
    swapped = {
        **common,
        "decay_primary": global_identities["decay_jeff"],
        "decay_fallback": global_identities["decay_endf"],
    }
    decay = add_input_set(
        input_sets,
        f"{prefix}:jeff-primary",
        swapped,
        changed={"decay_primary", "decay_fallback"},
    )
    context_hashes = {
        "published_reference": file_hashes["published_reference"],
        "source_archive": global_identities["fns_archive"],
    }
    context = add_input_set(
        input_sets,
        f"{prefix}:fispact-context",
        context_hashes,
        changed=set(context_hashes),
    )
    return production, decay, context


def fns_rows(
    input_sets: dict[str, dict[str, Any]],
    global_identities: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fingerprint = fns_cache_fingerprint(global_identities)
    rows = []
    fresh = 0
    cached = 0
    alignments = {}
    run_summaries = {}
    for number, (material, experiment) in enumerate(fns_experiments(), 1):
        run, was_cached = run_fns_experiment(material, experiment, fingerprint)
        cached += int(was_cached)
        fresh += int(not was_cached)
        directory = FNS / material
        measurement = fio.read_exp(directory / f"{experiment}.exp")
        _, input_record = cb1.specification(material, experiment)
        matched, alignment = cb1.infer_measurement_alignment(
            input_record["cooling_cum_s"], measurement
        )
        matched_by_measurement = dict(matched)
        excluded_by_measurement = {
            int(entry["row"]): alignment_reason(entry["reason"])
            for entry in alignment["excluded_rows"]
        }
        file_hashes = fns_file_identities(material, experiment)
        production_set, decay_set, context_set = fns_input_sets(
            input_sets, material, experiment, file_hashes, global_identities
        )
        expected_production_certificate = {
            "library": global_identities["library"],
            "library_index": global_identities["library_index"],
            "decay_primary": global_identities["decay_endf"],
            "decay_fallback": global_identities["decay_jeff"],
        }
        expected_decay_certificate = {
            "library": global_identities["library"],
            "library_index": global_identities["library_index"],
            "decay_primary": global_identities["decay_jeff"],
            "decay_fallback": global_identities["decay_endf"],
        }
        if run["production_run"]["certificate_inputs"] != expected_production_certificate:
            raise RuntimeError(f"{material}/{experiment}: production certificate mismatch")
        if run["decay_run"]["certificate_inputs"] != expected_decay_certificate:
            raise RuntimeError(f"{material}/{experiment}: decay-substitution certificate mismatch")
        family = f"fns:{material}/{experiment}"
        for measurement_index in range(len(measurement["t_raw"])):
            measured = float(measurement["heat_uW_g"][measurement_index])
            sigma = float(measurement["sigma_uW_g"][measurement_index])
            source_record: dict[str, Any] = {
                "material": material,
                "experiment": experiment,
                "measurement_row": measurement_index + 1,
                "measurement_time_raw": float(measurement["t_raw"][measurement_index]),
                "measurement_heat_uW_g": measured,
                "measurement_sigma_uW_g": sigma,
                "inferred_time_unit": alignment["time_unit"],
                "measurement_file_sha256": file_hashes["measurement"],
            }
            if measurement_index in matched_by_measurement:
                cooling_index = matched_by_measurement[measurement_index]
                source_record.update(
                    {
                        "cooling_step": cooling_index + 1,
                        "cooling_time_s": float(run["cooling_s"][cooling_index]),
                        "alignment_relative_mismatch": abs(
                            run["cooling_s"][cooling_index]
                            - measurement["t_raw"][measurement_index]
                            * {
                                "s": 1.0,
                                "min": 60.0,
                                "h": 3600.0,
                                "d": 86400.0,
                                "y": 365.25 * 86400.0,
                            }[alignment["time_unit"]]
                        )
                        / run["cooling_s"][cooling_index],
                    }
                )
                calculations = {
                    VARIANT_PRODUCTION: score_calculation(
                        measured,
                        run["production_uW_g"][cooling_index],
                        input_set_id=production_set,
                        interpretation="unchanged v1.0.1 TENDL-2025 production path",
                    ),
                    VARIANT_DECAY: score_calculation(
                        measured,
                        run["jeff_primary_uW_g"][cooling_index],
                        input_set_id=decay_set,
                        interpretation=(
                            "controlled decay-source priority substitution with activation data, "
                            "solver, chain, spectrum, schedule and measurement alignment fixed"
                        ),
                    ),
                    VARIANT_FISPACT_CONTEXT: unscored_calculation(
                        input_set_id=context_set,
                        reason="different_data_context",
                        value=run["fispact_context_uW_g"][cooling_index],
                        interpretation=(
                            "published FISPACT-II 4.0/TENDL-2017 context; not an identical-data "
                            "solver comparison and not scored for P17 attribution"
                        ),
                    ),
                }
                inclusion_status = "scored"
                inclusion_reason = "scored"
            else:
                source_record.update({"cooling_step": None, "cooling_time_s": None})
                calculations = {
                    VARIANT_PRODUCTION: unscored_calculation(
                        input_set_id=production_set,
                        reason="not_applicable",
                        interpretation="source row fails the frozen measurement-alignment predicate",
                    ),
                    VARIANT_DECAY: unscored_calculation(
                        input_set_id=decay_set,
                        reason="not_applicable",
                        interpretation="source row fails the frozen measurement-alignment predicate",
                    ),
                    VARIANT_FISPACT_CONTEXT: unscored_calculation(
                        input_set_id=context_set,
                        reason="different_data_context",
                        interpretation="unaligned published different-data context",
                    ),
                }
                inclusion_status = "unscored"
                inclusion_reason = excluded_by_measurement[measurement_index]
            rows.append(
                make_row(
                    row_id=(
                        f"fns:{material}:{experiment}:r{measurement_index + 1:03d}"
                    ),
                    family=family,
                    source_id=(
                        f"CoNDERC-FNS:{material}/{experiment}.exp:row-{measurement_index + 1}"
                    ),
                    source_record=source_record,
                    observable="decay heat",
                    unit="uW/g",
                    experimental_value=measured,
                    experimental_uncertainty=sigma,
                    experimental_uncertainty_unit="uW/g",
                    inclusion_status=inclusion_status,
                    inclusion_reason=inclusion_reason,
                    calculations=calculations,
                )
            )
        alignments[family] = {
            "time_unit": alignment["time_unit"],
            "median_relative_mismatch": alignment["median_relative_mismatch"],
            "source_rows": int(len(measurement["t_raw"])),
            "matched_rows": len(matched),
            "excluded_reasons": {
                reason: sum(value == reason for value in excluded_by_measurement.values())
                for reason in sorted(set(excluded_by_measurement.values()))
            },
        }
        run_summaries[family] = {
            "production_mode": run["production_run"]["mode"],
            "production_states": run["production_run"]["pruned_states"],
            "decay_substitution_mode": run["decay_run"]["mode"],
            "decay_substitution_states": run["decay_run"]["pruned_states"],
        }
        if number == 1 or number % 10 == 0 or number == 132:
            print(
                f"P17 G4 FNS {number:3d}/132 {material:>3s}/{experiment:<18s} "
                f"rows={len(measurement['t_raw']):2d} cached={cached}",
                file=sys.stderr,
                flush=True,
            )
    return rows, {
        "experiments": 132,
        "cache_fingerprint": fingerprint,
        "calculation_implementation_sha256": FNS_CALCULATION_IMPLEMENTATION_SHA256,
        "alignments": alignments,
        "run_summaries": run_summaries,
    }


def established_fns_identity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = ROOT / "results" / "cb1_fns.json"
    baseline = json.loads(path.read_text(encoding="utf-8"))
    generated = {
        (row["source_record"]["material"], row["source_record"]["experiment"]): []
        for row in rows
    }
    for row in rows:
        if row["inclusion"]["status"] != "scored":
            continue
        key = (row["source_record"]["material"], row["source_record"]["experiment"])
        generated[key].append(row)
    maximum = 0.0
    count = 0
    alignment_match = True
    for record in baseline["records"]:
        key = (record["material"], record["experiment"])
        current = generated[key]
        if len(current) != len(record["pairs"]):
            alignment_match = False
            continue
        for row, pair in zip(current, record["pairs"], strict=True):
            comparisons = (
                (row["source_record"]["cooling_time_s"], pair["time_s"]),
                (row["experimental"]["value"], pair["measured_uW_g"]),
                (
                    row["calculations"][VARIANT_PRODUCTION]["value"],
                    pair["actinv_tendl2025_uW_g"],
                ),
            )
            maximum = max(maximum, *(relative(float(left), float(right)) for left, right in comparisons))
            count += 1
    return {
        "baseline_path": "results/cb1_fns.json",
        "baseline_sha256": sha256(path),
        "pairs_compared": count,
        "alignment_identity": alignment_match,
        "maximum_relative_deviation": maximum,
        "pass": alignment_match and count == 2360 and maximum <= 1.0e-14,
    }


def controlled_substitutions(
    rows: list[dict[str, Any]], prior: dict[str, Any]
) -> dict[str, Any]:
    irdff_pairs = []
    decay_pairs = []
    measurement_alignment = []
    for row in rows:
        calculations = row["calculations"]
        if VARIANT_IRDFF in calculations:
            left = calculations[VARIANT_PRODUCTION]
            right = calculations[VARIANT_IRDFF]
            if left["status"] == right["status"] == "scored":
                irdff_pairs.append(right["signed_log_C_over_E"] - left["signed_log_C_over_E"])
        if VARIANT_DECAY in calculations:
            left = calculations[VARIANT_PRODUCTION]
            right = calculations[VARIANT_DECAY]
            if left["status"] == right["status"] == "scored":
                decay_pairs.append(right["signed_log_C_over_E"] - left["signed_log_C_over_E"])
                measurement_alignment.append(row["source_record"]["alignment_relative_mismatch"])

    def distribution(values: list[float]) -> dict[str, Any]:
        array = np.abs(np.asarray(values, dtype=float))
        return {
            "rows": len(values),
            "median_abs_signed_log_change": float(np.quantile(array, 0.5, method="linear")),
            "p90_abs_signed_log_change": float(np.quantile(array, 0.9, method="linear")),
            "maximum_abs_signed_log_change": float(np.max(array)),
        }

    return {
        "numerical_solver": {
            **prior["numerical_solver"],
            "status": "bounded-by-identical-operator-control",
            "changed_input_roles": ["solver"],
            "identical_input_roles": ["operator", "initial_vector", "schedule"],
        },
        "chain_construction": {
            **prior["chain_construction"],
            "status": "bounded-by-identical-processed-data-control",
            "changed_input_roles": ["chain_implementation"],
            "identical_input_roles": ["rates", "decay", "initial_vector", "schedule"],
        },
        "processor": {
            **prior["processor"],
            "status": "bounded-by-same-raw-evaluation-control",
            "changed_input_roles": ["processor"],
            "identical_input_roles": ["raw_evaluation", "temperature", "groups", "spectrum"],
        },
        "evaluation": {
            "status": "quantified-diagnostic-not-blind",
            "substitution": "TENDL-2025 production activation data -> IRDFF-II groupwise activation data",
            "changed_input_roles": ["activation_data", "activation_data_index"],
            "identical_input_roles": ["measurement", "spectrum", "calculation_implementation"],
            **distribution(irdff_pairs),
        },
        "decay_yield": {
            "status": "quantified-controlled-substitution",
            "substitution": "ENDF/B-VIII.0 primary + JEFF-3.3 fallback -> JEFF-3.3 primary + ENDF/B-VIII.0 fallback",
            "changed_input_roles": ["decay_primary", "decay_fallback"],
            "identical_input_roles": [
                "measurement",
                "schedule",
                "spectrum",
                "activation_library",
                "activation_index",
                "runtime",
                "alignment_implementation",
            ],
            **distribution(decay_pairs),
        },
        "measurement_definition": {
            "status": "bounded-no-supported-alternative",
            "substitution": None,
            "reason": (
                "Only the publication-defined positive-row unit alignment and cooling-step match is supported; "
                "no undocumented EOI, interpolation, or unit alternative was introduced."
            ),
            "rows": len(measurement_alignment),
            "maximum_alignment_relative_mismatch": max(measurement_alignment),
            "calculation_effect": 0.0,
        },
    }


def build_cause_ledger(rows: list[dict[str, Any]], prior: dict[str, Any]) -> dict[str, Any]:
    entries = []
    for row in rows:
        for variant, calculation in sorted(row["calculations"].items()):
            if calculation["status"] != "scored" or not calculation["material_mismatch"]:
                continue
            mismatch_key = f"{row['row_id']}::{variant}"
            if VARIANT_IRDFF in row["calculations"]:
                counterpart_name = VARIANT_IRDFF if variant == VARIANT_PRODUCTION else VARIANT_PRODUCTION
                counterpart = row["calculations"].get(counterpart_name)
                secondary = ["evaluation", "processor"]
                substitution = "IRDFF-II/TENDL-2025 activation-data substitution"
            elif VARIANT_DECAY in row["calculations"]:
                counterpart_name = VARIANT_DECAY if variant == VARIANT_PRODUCTION else VARIANT_PRODUCTION
                counterpart = row["calculations"].get(counterpart_name)
                secondary = ["decay-yield"]
                substitution = "ENDF/B-VIII.0/JEFF-3.3 decay-priority substitution"
            else:
                counterpart = None
                secondary = []
                substitution = "no supported row-level substitution"
            change = 0.0
            if counterpart is not None and counterpart["status"] == "scored":
                change = counterpart["signed_log_C_over_E"] - calculation["signed_log_C_over_E"]
            entry = cause_entry(
                mismatch_key,
                primary_cause="unresolved",
                secondary_causes=secondary,
                controlled_substitution=substitution,
                signed_log_change=change,
                evidence=[
                    f"input-set:{calculation['input_set_id']}",
                    prior["numerical_solver"]["path"] + "#pass",
                    prior["chain_construction"]["path"] + "#pass",
                    prior["processor"]["path"] + "#pass",
                ],
                confidence="unresolved",
            )
            entry["sequence"] = len(entries) + 1
            entry["entry_sha256"] = canonical_sha256(entry)
            entries.append(entry)
    ledger = {
        "schema": CAUSE_LEDGER_SCHEMA,
        "protocol_sha256": PROTOCOL_SHA256,
        "append_only": True,
        "taxonomy": [
            "solver",
            "chain-construction",
            "processor",
            "evaluation",
            "decay-yield",
            "measurement-definition",
            "unsupported-model",
            "unresolved",
        ],
        "entries": entries,
    }
    ledger["entries_sha256"] = canonical_sha256(entries)
    return ledger


def diagnostic_report(output: dict[str, Any]) -> str:
    metrics = output["family_metrics"]
    lines = [
        "# P17 open diagnostic report",
        "",
        "This is the pre-unseal diagnostic checkpoint. It contains only IRDFF-II Tables 18–20 and the already-seen "
        "CoNDERC FNS experiments. Accuracy is reported, never used as a hidden acceptance threshold.",
        "",
        "## What the controls isolate",
        "",
        "- Numerical solver and chain construction are bounded by byte-identical operator/rate controls.",
        "- Raw-evaluation processing is bounded by the fresh ACTINV/NJOY differential.",
        "- IRDFF-II versus TENDL-2025 changes only the activation-data roles in the diagnostic SACS calculation.",
        "- The FNS decay variant swaps ENDF/B-VIII.0 and JEFF-3.3 priority while holding activation data, schedule, "
        "spectrum, solver, chain construction, and measurement alignment fixed.",
        "- FISPACT-II/TENDL-2017 remains explicitly different-data context and is not called a solver comparison.",
        "",
        "## IRDFF-II open tables",
        "",
        "| family | variant | scored | unscored | geometric mean C/E | median abs(ln C/E) | p90 abs(ln C/E) | within 30% |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for family in ("irdff_table_18", "irdff_table_19", "irdff_table_20"):
        for variant, record in metrics[family].items():
            gm = record["geometric_mean_C_over_E"]
            median = record["median_abs_log_C_over_E"]
            p90 = record["p90_abs_log_C_over_E"]
            fraction = record["fraction_within_30_percent"]
            lines.append(
                f"| {family} | {variant} | {record['scored_rows']} | {record['unscored_rows']} | "
                f"{gm:.6g} | {median:.6g} | {p90:.6g} | {fraction:.1%} |"
            )
    fns_families = [family for family in metrics if family.startswith("fns:")]
    production = [metrics[family][VARIANT_PRODUCTION] for family in fns_families]
    decay = [metrics[family][VARIANT_DECAY] for family in fns_families]
    lines.extend(
        [
            "",
            "## CoNDERC FNS family",
            "",
            f"All {len(fns_families)} experiments are retained as separate families; they are not pooled into a "
            "winner score. Across those family-level reports:",
            "",
            f"- production rows scored: {sum(item['scored_rows'] for item in production)}; unscored: "
            f"{sum(item['unscored_rows'] for item in production)}",
            f"- production experiments whose every scored row is within 30%: "
            f"{sum(item['fraction_within_30_percent'] == 1.0 for item in production)}/{len(production)}",
            f"- JEFF-primary decay-substitution experiments whose every scored row is within 30%: "
            f"{sum(item['fraction_within_30_percent'] == 1.0 for item in decay)}/{len(decay)}",
            "",
            "Every outside-30% row is present in the append-only cause ledger. `unresolved` is used where the "
            "controlled evidence bounds layers but does not demonstrate a unique cause.",
            "",
            "## Reproduction",
            "",
            "With the hash-pinned public inputs listed in the P17 protocol and a release Python module built at "
            "`python/target/release/libactinv.so`, run:",
            "",
            "```bash",
            "python controls/g4_p17_diagnostics.py",
            "python controls/check_g4_p17.py",
            "```",
            "",
            "The generator is resumable under `/tmp/actinv-p17-g4-fns`; caches are fingerprinted by every input "
            "identity and the calculation-implementation identity. The committed checker needs no bulk nuclear data.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    global_identities = checked_global_inputs()
    prior = prior_gate_evidence()
    input_sets: dict[str, dict[str, Any]] = {}
    ir_rows, ir_diagnostics = irdff_rows(input_sets)
    fn_rows, fn_diagnostics = fns_rows(input_sets, global_identities)
    rows = ir_rows + fn_rows
    row_ids = [row["row_id"] for row in rows]
    if len(row_ids) != len(set(row_ids)):
        raise RuntimeError("P17 G4 generated duplicate row identities")
    source_ids = [row["source_id"] for row in rows]
    if len(source_ids) != len(set(source_ids)):
        raise RuntimeError("P17 G4 generated duplicate source identities")

    family_metrics = all_family_metrics(rows)
    controlled = controlled_substitutions(rows, prior)
    baseline_identity = established_fns_identity(fn_rows)
    cause_ledger = build_cause_ledger(rows, prior)
    expected_mismatches = mismatch_keys(rows)
    actual_mismatches = sorted(entry["mismatch_key"] for entry in cause_ledger["entries"])
    checks = {
        "only_open_irdff_tables": set(ir_diagnostics["row_counts"]) == {"18", "19", "20"},
        "all_124_irdff_rows": len(ir_rows) == 124,
        "all_132_fns_experiments": fn_diagnostics["experiments"] == 132,
        "all_fns_source_rows_preserved": len(fn_rows)
        == sum(item["source_rows"] for item in fn_diagnostics["alignments"].values()),
        "all_irdff_official_rows_folded": ir_diagnostics["official_rows_folded"] == 124,
        "official_folds_match_published_within_5_percent": ir_diagnostics[
            "maximum_official_vs_published_relative"
        ]
        <= 0.05,
        "established_fns_alignment_and_production_identical": baseline_identity["pass"],
        "prior_layer_controls_green": all(item["pass"] for item in prior.values()),
        "every_material_mismatch_has_one_cause": expected_mismatches == actual_mismatches,
        "input_sets_are_referenced": set(input_sets)
        == {
            calculation["input_set_id"]
            for row in rows
            for calculation in row["calculations"].values()
        },
    }
    output = {
        "schema": SCHEMA,
        "protocol_sha256": PROTOCOL_SHA256,
        "opening_source_commit": "f9e6a5c8faf15f1748f1b2c4683889ea8a631c9d",
        "control_source_sha256": sha256(Path(__file__)),
        "scoring_source_sha256": sha256(ROOT / "controls" / "p17_scoring.py"),
        "irdff_source_sha256": sha256(ROOT / "controls" / "p17_irdff.py"),
        "checker_source_sha256": sha256(ROOT / "controls" / "check_g4_p17.py"),
        "schema_identities": {
            "diagnostic": sha256(
                ROOT / "controls" / "fixtures" / "p17_diagnostic_schema.json"
            ),
            "cause_ledger": sha256(
                ROOT / "controls" / "fixtures" / "p17_cause_ledger_schema.json"
            ),
        },
        "global_input_identities": global_identities,
        "prior_layer_evidence": prior,
        "variant_definitions": {
            VARIANT_PRODUCTION: {
                "role": "production-score",
                "description": "unchanged ACTINV 1.0.1 TENDL-2025 public path",
            },
            VARIANT_DECAY: {
                "role": "controlled-decay-substitution",
                "description": "JEFF-3.3 primary and ENDF/B-VIII.0 fallback, all other FNS inputs fixed",
            },
            VARIANT_IRDFF: {
                "role": "controlled-evaluation-diagnostic",
                "description": "IRDFF-II groupwise data folded independently; validation-derived, not blind",
            },
            VARIANT_FISPACT_CONTEXT: {
                "role": "different-data-context",
                "description": "published FISPACT-II 4.0/TENDL-2017 values; never a solver comparison",
            },
        },
        "input_sets": dict(sorted(input_sets.items())),
        "controlled_substitutions": controlled,
        "source_diagnostics": {"irdff": ir_diagnostics, "fns": fn_diagnostics},
        "established_fns_identity": baseline_identity,
        "row_counts": {
            "total": len(rows),
            "irdff": len(ir_rows),
            "fns": len(fn_rows),
            "scored_source_rows": sum(row["inclusion"]["status"] == "scored" for row in rows),
            "unscored_source_rows": sum(row["inclusion"]["status"] == "unscored" for row in rows),
        },
        "rows": rows,
        "family_metrics": family_metrics,
        "material_mismatches": {
            "g1": 0,
            "g2": 0,
            "g3": 0,
            "g4": len(expected_mismatches),
            "keys_sha256": canonical_sha256(expected_mismatches),
        },
        "cause_ledger": {
            "path": "results/p17_cause_ledger.json",
            "entries": len(cause_ledger["entries"]),
            "entries_sha256": cause_ledger["entries_sha256"],
        },
        "checks": checks,
        "pass": all(checks.values()),
    }
    output["evidence_sha256"] = canonical_sha256(
        {key: value for key, value in output.items() if key != "evidence_sha256"}
    )
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    CAUSE_LEDGER.write_text(
        json.dumps(cause_ledger, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    REPORT.write_text(diagnostic_report(output), encoding="utf-8")
    print(
        json.dumps(
            {
                "schema": output["schema"],
                "row_counts": output["row_counts"],
                "material_mismatches": output["material_mismatches"],
                "controlled_substitutions": output["controlled_substitutions"],
                "checks": checks,
                "pass": output["pass"],
            },
            indent=1,
            sort_keys=True,
        )
    )
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
