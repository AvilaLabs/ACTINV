#!/usr/bin/env python3
"""CB1-G3: fresh ACTINV/TENDL-2025 FNS comparison and public FISPACT reference score."""
from __future__ import annotations

import glob
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from harness import fispact_io as fio  # noqa: E402


RESULT = ROOT / "results/cb1_fns.json"
WORK = Path(os.environ.get("ACTINV_CB1_FNS_WORK", "/tmp/actinv-cb1-fns")).resolve()
DATA = Path.home() / "nuclear-data"
FNS = Path(os.environ.get("ACTINV_FNS_ROOT", DATA / "conderc-fns/fns")).resolve()
LIBRARY = Path(
    os.environ.get("ACTINV_LIBRARY", DATA / "tendl-2025/builds/full/neutron.n.p10.npz")
).resolve()
DECAY_PRIMARY = Path(
    os.environ.get(
        "ACTINV_ENDF_DECAY", DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
    )
).resolve()
DECAY_FALLBACK = Path(
    os.environ.get("ACTINV_JEFF_DECAY", DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat")
).resolve()
MODULE = Path(
    os.environ.get("ACTINV_PYTHON_MODULE", ROOT / "python/target/release/libactinv.so")
).resolve()
FNS_ARCHIVE = Path(os.environ.get("ACTINV_FNS_ARCHIVE", DATA / "conderc-fns/fns.zip")).resolve()

EXPECTED = {
    "library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "library_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    "fns_archive": "ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d",
}
SECONDS_PER_YEAR = 365.25 * 86400.0
WITHIN_LOG = math.log(1.3)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_actinv():
    if not MODULE.is_file():
        raise FileNotFoundError(f"build the current Python module first: {MODULE}")
    spec = importlib.util.spec_from_file_location("actinv", MODULE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load ACTINV extension from {MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["actinv"] = module
    spec.loader.exec_module(module)
    return module


actinv = load_actinv()


def read_flux(path: Path) -> list[float]:
    values = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        try:
            values.extend(float(value) for value in line.split())
        except ValueError:
            break
    if len(values) < 709:
        raise ValueError(f"{path.name} supplies {len(values)} flux groups, expected at least 709")
    selected = values[:709]
    if not all(math.isfinite(value) and value >= 0.0 for value in selected):
        raise ValueError(f"{path.name} contains invalid flux values")
    return selected


def specification(material: str, experiment: str) -> tuple[dict[str, object], dict[str, object]]:
    directory = FNS / material
    input_record = fio.read_i(directory / f"TENDL-2017_{experiment}.i")
    flux = read_flux(directory / f"{experiment}_fluxes")
    cooling = input_record["cooling_cum_s"]
    if not cooling or any(value <= 0.0 for value in cooling):
        raise ValueError(f"{material}/{experiment} has an invalid cooling schedule")
    schedule = [
        {"dt": f"{input_record['t_irr_s']:.17e} s", "flux": 1.0},
        {"dt": f"{cooling[0]:.17e} s", "flux": 0.0},
    ]
    schedule.extend(
        {
            "dt": f"{cooling[index] - cooling[index - 1]:.17e} s",
            "flux": 0.0,
        }
        for index in range(1, len(cooling))
    )
    spec = {
        "spec": "actinv-spec-1",
        "title": f"CB1 FNS {material} {experiment}",
        "projectile": "neutron",
        "library": {"path": str(LIBRARY), "sha256": EXPECTED["library"]},
        "decay": {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)},
        "material": {
            "mass_g": float(input_record["mass_kg"] * 1000.0),
            "basis": "wt_percent",
            "composition": input_record["elements"],
        },
        "spectrum": {
            "structure": "fispact-709",
            "flux_per_group": flux,
            "total": float(input_record["flux_total"]),
            "descending": True,
        },
        "schedule": schedule,
        "options": {
            "mode": "auto",
            "prune": "rate",
            "bmin_atoms_per_g": 1.0e-8,
            "temperature_K": 293.6,
            "outputs": ["inventory", "activity", "heat", "ledger", "certificate"],
        },
        "fission_yields": {"files": [], "energy": "spectrum_average"},
    }
    return spec, input_record


def infer_measurement_alignment(
    cooling_s: list[float], measurement: dict[str, np.ndarray]
) -> tuple[list[tuple[int, int]], dict[str, object]]:
    cooling = np.asarray(cooling_s, dtype=float)
    raw_time = np.asarray(measurement["t_raw"], dtype=float)
    heat = np.asarray(measurement["heat_uW_g"], dtype=float)
    valid_for_inference = (raw_time > 0.0) & (heat > 0.0)
    units = [
        ("s", 1.0),
        ("min", 60.0),
        ("h", 3600.0),
        ("d", 86400.0),
        ("y", SECONDS_PER_YEAR),
    ]

    def mismatch(factor: float) -> float:
        if not np.any(valid_for_inference):
            return math.inf
        scaled = raw_time[valid_for_inference] * factor
        return float(
            np.median([np.min(np.abs(cooling - value) / value) for value in scaled])
        )

    unit, factor = min(units, key=lambda item: mismatch(item[1]))
    scaled = raw_time * factor
    matched = []
    excluded = []
    for index, value in enumerate(scaled):
        nearest = int(np.argmin(np.abs(cooling - value)))
        close = abs(cooling[nearest] - value) <= max(0.02 * value, 1.0)
        if raw_time[index] > 0.0 and heat[index] > 0.0 and close:
            matched.append((index, nearest))
        else:
            if raw_time[index] <= 0.0:
                reason = "nonpositive_time"
            elif heat[index] <= 0.0:
                reason = "nonpositive_measurement"
            else:
                reason = "no_cooling_step_within_2_percent"
            excluded.append({"row": index, "reason": reason})
    return matched, {
        "time_unit": unit,
        "median_relative_mismatch": mismatch(factor),
        "measured_rows": int(len(raw_time)),
        "matched_positive_rows": len(matched),
        "excluded_rows": excluded,
    }


def product_metrics(pairs: list[dict[str, float]], key: str) -> dict[str, object]:
    scored = [row for row in pairs if row.get(key, 0.0) > 0.0 and row["measured_uW_g"] > 0.0]
    ratios = np.asarray([row[key] / row["measured_uW_g"] for row in scored], dtype=float)
    if not len(ratios):
        return {"points": 0, "unscored_nonpositive_calculation": len(pairs)}
    absolute_log = np.abs(np.log(ratios))
    sigma_rows = [row for row in scored if row["sigma_uW_g"] > 0.0]
    normalized = np.asarray(
        [(row[key] - row["measured_uW_g"]) / row["sigma_uW_g"] for row in sigma_rows]
    )
    return {
        "points": int(len(ratios)),
        "unscored_nonpositive_calculation": len(pairs) - len(scored),
        "geometric_mean_C_over_E": float(np.exp(np.mean(np.log(ratios)))),
        "maximum_abs_log_C_over_E": float(np.max(absolute_log)),
        "all_points_within_30_percent": bool(np.all(absolute_log <= WITHIN_LOG)),
        "positive_sigma_points": len(sigma_rows),
        "rms_measurement_sigma": float(np.sqrt(np.mean(normalized**2))) if len(normalized) else None,
    }


def aggregate(records: list[dict[str, object]], key: str) -> dict[str, object]:
    experiments = [record for record in records if record["metrics"][key].get("points", 0) > 0]
    ratios = np.asarray(
        [
            row[key] / row["measured_uW_g"]
            for record in experiments
            for row in record["pairs"]
            if row.get(key, 0.0) > 0.0 and row["measured_uW_g"] > 0.0
        ],
        dtype=float,
    )
    experiment_geomeans = np.asarray(
        [record["metrics"][key]["geometric_mean_C_over_E"] for record in experiments]
    )
    experiment_maxima = np.asarray(
        [record["metrics"][key]["maximum_abs_log_C_over_E"] for record in experiments]
    )
    absolute_log = np.abs(np.log(ratios))
    sigma_residuals = np.asarray(
        [
            (row[key] - row["measured_uW_g"]) / row["sigma_uW_g"]
            for record in experiments
            for row in record["pairs"]
            if row.get(key, 0.0) > 0.0
            and row["measured_uW_g"] > 0.0
            and row["sigma_uW_g"] > 0.0
        ]
    )
    return {
        "experiments_scored": len(experiments),
        "experiments_total": len(records),
        "points_scored": int(len(ratios)),
        "pooled_geometric_mean_C_over_E": float(np.exp(np.mean(np.log(ratios)))),
        "median_experiment_geometric_mean_C_over_E": float(np.median(experiment_geomeans)),
        "median_pooled_abs_log_C_over_E": float(np.median(absolute_log)),
        "p90_pooled_abs_log_C_over_E": float(np.quantile(absolute_log, 0.9)),
        "median_experiment_maximum_abs_log_C_over_E": float(np.median(experiment_maxima)),
        "experiments_all_points_within_30_percent": int(
            sum(record["metrics"][key]["all_points_within_30_percent"] for record in experiments)
        ),
        "fraction_experiments_all_points_within_30_percent": float(
            np.mean(
                [record["metrics"][key]["all_points_within_30_percent"] for record in experiments]
            )
        ),
        "positive_sigma_points": int(len(sigma_residuals)),
        "rms_measurement_sigma": float(np.sqrt(np.mean(sigma_residuals**2)))
        if len(sigma_residuals)
        else None,
        "unscored_nonpositive_calculation": int(
            sum(record["metrics"][key]["unscored_nonpositive_calculation"] for record in records)
        ),
    }


def cache_fingerprint() -> tuple[str, dict[str, str]]:
    identities = {
        "control": sha256(Path(__file__)),
        "actinv_module": sha256(MODULE),
        "library": sha256(LIBRARY),
        "library_index": sha256(LIBRARY.with_name(LIBRARY.stem + "_index.json")),
        "decay_primary": sha256(DECAY_PRIMARY),
        "decay_fallback": sha256(DECAY_FALLBACK),
        "fns_archive": sha256(FNS_ARCHIVE),
    }
    encoded = json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), identities


def run_experiment(material: str, experiment: str, fingerprint: str) -> dict[str, object]:
    spec, input_record = specification(material, experiment)
    directory = FNS / material
    measurement = fio.read_exp(directory / f"{experiment}.exp")
    reference_path = directory / f"TENDL-2017_{experiment}.nuclides"
    reference = fio.read_nuclides(reference_path) if reference_path.is_file() else None
    started = time.perf_counter()
    result = json.loads(actinv.run(json.dumps(spec, sort_keys=True, separators=(",", ":"))))
    wall_s = time.perf_counter() - started
    cooling = input_record["cooling_cum_s"]
    if len(result["steps"]) != len(cooling) + 1:
        raise RuntimeError(
            f"{material}/{experiment}: {len(result['steps'])} result steps for {len(cooling)} cooling points"
        )
    calculated = [step["heat_W_per_g"]["total"] * 1.0e6 for step in result["steps"][1:]]
    matched, alignment = infer_measurement_alignment(cooling, measurement)
    pairs = []
    for measurement_index, cooling_index in matched:
        row = {
            "time_s": float(cooling[cooling_index]),
            "measured_uW_g": float(measurement["heat_uW_g"][measurement_index]),
            "sigma_uW_g": float(measurement["sigma_uW_g"][measurement_index]),
            "actinv_tendl2025_uW_g": float(calculated[cooling_index]),
        }
        if reference is not None:
            reference_heat = reference["total_kW_kg"][1:] * 1.0e6
            if cooling_index >= len(reference_heat):
                raise RuntimeError(f"{material}/{experiment}: FISPACT reference is too short")
            row["fispact_tendl2017_uW_g"] = float(reference_heat[cooling_index])
        pairs.append(row)

    reference_time_mismatch = None
    if reference is not None:
        reference_times = reference["t_y"][1:] * SECONDS_PER_YEAR
        count = min(len(reference_times), len(cooling))
        if count:
            reference_time_mismatch = float(
                np.max(
                    np.abs(reference_times[:count] - np.asarray(cooling[:count]))
                    / np.asarray(cooling[:count])
                )
            )
    certificate_inputs = {
        role: entry["sha256"] if entry is not None else None
        for role, entry in result["certificate"]["inputs"].items()
        if role in ("library", "library_index", "decay_primary", "decay_fallback")
    }
    expected_certificate = {
        "library": EXPECTED["library"],
        "library_index": EXPECTED["library_index"],
        "decay_primary": EXPECTED["decay_primary"],
        "decay_fallback": EXPECTED["decay_fallback"],
    }
    metrics = {"actinv_tendl2025_uW_g": product_metrics(pairs, "actinv_tendl2025_uW_g")}
    metrics["fispact_tendl2017_uW_g"] = product_metrics(pairs, "fispact_tendl2017_uW_g")
    return {
        "fingerprint": fingerprint,
        "material": material,
        "experiment": experiment,
        "wall_s": wall_s,
        "core_ms": result["ms"],
        "mode": result["mode"],
        "states": {"total": result["total_states"], "pruned": result["pruned_states"]},
        "alignment": alignment,
        "reference_time_max_relative_mismatch": reference_time_mismatch,
        "certificate_inputs": certificate_inputs,
        "certificate_inputs_match": certificate_inputs == expected_certificate,
        "pairs": pairs,
        "metrics": metrics,
    }


def main() -> None:
    fingerprint, identities = cache_fingerprint()
    expected_identity_values = {
        "library": EXPECTED["library"],
        "library_index": EXPECTED["library_index"],
        "decay_primary": EXPECTED["decay_primary"],
        "decay_fallback": EXPECTED["decay_fallback"],
        "fns_archive": EXPECTED["fns_archive"],
    }
    identity_match = all(identities[name] == value for name, value in expected_identity_values.items())
    if not identity_match:
        raise RuntimeError(f"CB1 FNS input mismatch: {identities}")
    experiments = [
        (material.name, Path(path).stem)
        for material in sorted(path for path in FNS.iterdir() if path.is_dir())
        for path in sorted(glob.glob(str(material / "*.exp")))
    ]
    if len(experiments) != 132:
        raise RuntimeError(f"expected 132 FNS experiments, found {len(experiments)}")
    WORK.mkdir(parents=True, exist_ok=True)
    records = []
    cached = 0
    started = time.perf_counter()
    for index, (material, experiment) in enumerate(experiments, 1):
        cache_path = WORK / f"{material}_{experiment}.json"
        record = None
        if cache_path.is_file():
            try:
                candidate = json.loads(cache_path.read_text(encoding="utf-8"))
                if candidate.get("fingerprint") == fingerprint:
                    record = candidate
                    cached += 1
            except (OSError, json.JSONDecodeError):
                pass
        if record is None:
            record = run_experiment(material, experiment, fingerprint)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(cache_path)
        records.append(record)
        if index == 1 or index % 10 == 0 or index == len(experiments):
            print(
                f"{index:3d}/{len(experiments)} {material:>3s} {experiment:<18s} "
                f"points={record['metrics']['actinv_tendl2025_uW_g'].get('points', 0):2d} "
                f"wall={record['wall_s']:.2f}s cached={cached}",
                file=sys.stderr,
                flush=True,
            )

    alignment_exclusions: dict[str, int] = {}
    for record in records:
        for excluded in record["alignment"]["excluded_rows"]:
            reason = excluded["reason"]
            alignment_exclusions[reason] = alignment_exclusions.get(reason, 0) + 1
    reference_mismatches = [
        record["reference_time_max_relative_mismatch"]
        for record in records
        if record["reference_time_max_relative_mismatch"] is not None
    ]
    output = {
        "schema": "actinv-cb1-fns-1",
        "access": {
            "ACTINV/TENDL-2025": "executed",
            "FISPACT-II 4.0/TENDL-2017": "published-reference",
            "ALARA": "not-applicable",
            "OpenMC": "not-applicable",
            "SCALE/ORIGEN": "not-available",
        },
        "input_identities": identities,
        "fingerprint": fingerprint,
        "actinv_version": getattr(actinv, "__version__", "unavailable"),
        "experiment_count": len(records),
        "fresh_experiments": len(records) - cached,
        "cached_experiments": cached,
        "wall_s": time.perf_counter() - started,
        "scoring": {
            "measurement_floor": None,
            "nuclide_exclusions": None,
            "positive_aligned_pairs_only": True,
            "within_30_percent_definition": "abs(ln(C/E)) <= ln(1.3)",
            "alignment_exclusions": alignment_exclusions,
        },
        "summary": {
            "actinv_tendl2025": aggregate(records, "actinv_tendl2025_uW_g"),
            "fispact_4_tendl2017_published": aggregate(
                records, "fispact_tendl2017_uW_g"
            ),
        },
        "reference_time_worst_relative_mismatch": max(reference_mismatches),
        "records": records,
    }
    checks = {
        "all_132_experiments": len(records) == 132,
        "all_certificate_inputs_match": all(record["certificate_inputs_match"] for record in records),
        "all_experiments_have_positive_measurement_pairs": all(
            record["metrics"]["actinv_tendl2025_uW_g"].get("points", 0) > 0
            for record in records
        ),
        "all_fispact_references_present": all(
            record["metrics"]["fispact_tendl2017_uW_g"].get("points", 0) > 0
            for record in records
        ),
        "reference_times_align_within_2_percent": bool(reference_mismatches)
        and max(reference_mismatches) <= 0.02,
        "input_identities_match": identity_match,
    }
    output["checks"] = checks
    output["pass"] = all(checks.values())
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("schema", "summary", "checks", "pass")}, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
