#!/usr/bin/env python3
"""P149 S1: the 132 FNS experiments on TENDL-2017 with gas production, and the leading heat contributors.

The spec is `cb3_fns.specification()` (the run behind `results/cb3_fns_tendl2017.json`) with `options.gas` enabled
and a step summary of the three leading heat contributors. For every measured point the record keeps ACTINV's and
the published FISPACT-II/TENDL-2017 heat, and each code's three leading nuclides by heat at that time. The decay
data differ: ACTINV uses ENDF/B-VIII.0 with a JEFF-3.3 fallback.

Usage: p149_s1_fns.py [--out results/p149/s1_fns_tendl2017_gas.json]
Environment as for cb3_fns.py; ACTINV_P149_S1_WORK caches finished experiments (default target/p149-s1).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cb3_fns as cb3  # noqa: E402

ROOT = HERE.parent
WORK = Path(os.environ.get("ACTINV_P149_S1_WORK", ROOT / "target/p149-s1")).resolve()
TOP = 3


def fispact_leaders(reference: dict, row: int) -> list[dict]:
    """The three nuclides with the largest printed heat at reference row `row` (row 0 is the end of irradiation)."""
    total = float(reference["total_kW_kg"][row])
    ranked = sorted(((float(series[row]), name) for name, series in reference["nuclides"].items()), reverse=True)
    return [{"nuclide": name.replace(" ", ""), "percent": 100.0 * value / total if total > 0 else None}
            for value, name in ranked[:TOP] if value > 0]


def actinv_leaders(step: dict) -> list[dict]:
    heat = step["summary"]["dominant"]["heat"]
    return [{"nuclide": entry["nuclide"], "percent": entry["percent"]} for entry in heat["top"][:TOP]]


def run_experiment(material: str, experiment: str, fingerprint: str) -> dict:
    spec, input_record = cb3.specification(material, experiment)
    spec["title"] = f"P149 S1 FNS {material} {experiment} (gas)"
    spec["options"]["gas"] = True
    spec["summary"] = {"dominant_top": TOP, "nuclides": False, "elements": False}
    directory = cb3.FNS / material
    measurement = cb3.fio.read_exp(directory / f"{experiment}.exp")
    reference_path = directory / f"TENDL-2017_{experiment}.nuclides"
    reference = cb3.fio.read_nuclides(reference_path) if reference_path.is_file() else None
    started = time.perf_counter()
    result = json.loads(cb3.actinv.run(json.dumps(spec, sort_keys=True, separators=(",", ":"))))
    wall_s = time.perf_counter() - started
    cooling = input_record["cooling_cum_s"]
    if len(result["steps"]) != len(cooling) + 1:
        raise RuntimeError(f"{material}/{experiment}: {len(result['steps'])} steps for {len(cooling)} cooling points")
    matched, alignment = cb3.infer_measurement_alignment(cooling, measurement)
    pairs = []
    for measurement_index, cooling_index in matched:
        step = result["steps"][cooling_index + 1]
        row = {
            "time_s": float(cooling[cooling_index]),
            "measured_uW_g": float(measurement["heat_uW_g"][measurement_index]),
            "sigma_uW_g": float(measurement["sigma_uW_g"][measurement_index]),
            "actinv_tendl2017_gas_uW_g": float(step["heat_W_per_g"]["total"] * 1.0e6),
            "actinv_leaders": actinv_leaders(step),
        }
        if reference is not None:
            row["fispact_tendl2017_uW_g"] = float(reference["total_kW_kg"][cooling_index + 1] * 1.0e6)
            row["fispact_leaders"] = fispact_leaders(reference, cooling_index + 1)
        pairs.append(row)
    end_of_irradiation = result["steps"][0].get("gas") or {}
    return {
        "fingerprint": fingerprint,
        "material": material,
        "experiment": experiment,
        "wall_s": wall_s,
        "core_ms": result["ms"],
        "mode": result["mode"],
        "states": {"total": result["total_states"], "pruned": result["pruned_states"]},
        "gas_end_of_irradiation": {key: end_of_irradiation.get(key) for key in ("H_appm", "He_appm")}
        | {"H3_atoms_per_g": (end_of_irradiation.get("H3") or {}).get("atoms_per_g")},
        "gas_ledger": result["ledger"].get("gas"),
        "certificate_inputs": {role: entry["sha256"] if entry is not None else None
                               for role, entry in result["certificate"]["inputs"].items()
                               if role in ("library", "library_index", "decay_primary", "decay_fallback")},
        "alignment": alignment,
        "pairs": pairs,
        "metrics": {"actinv_tendl2017_gas_uW_g": cb3.product_metrics(pairs, "actinv_tendl2017_gas_uW_g"),
                    "fispact_tendl2017_uW_g": cb3.product_metrics(pairs, "fispact_tendl2017_uW_g")},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "results/p149/s1_fns_tendl2017_gas.json")
    args = ap.parse_args()
    base, identities = cb3.cache_fingerprint()
    identities["p149_s1_control"] = cb3.sha256(Path(__file__))
    fingerprint = hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()
    expected = {name: cb3.EXPECTED[name] for name in
                ("library", "library_index", "decay_primary", "decay_fallback", "fns_archive")}
    if any(identities[name] != value for name, value in expected.items()):
        raise SystemExit(f"S1 input mismatch: {identities}")
    experiments = [(material.name, Path(path).stem)
                   for material in sorted(p for p in cb3.FNS.iterdir() if p.is_dir())
                   for path in sorted(glob.glob(str(material / "*.exp")))]
    if len(experiments) != 132:
        raise SystemExit(f"expected 132 FNS experiments, found {len(experiments)}")
    WORK.mkdir(parents=True, exist_ok=True)
    records, cached, started = [], 0, time.perf_counter()
    for index, (material, experiment) in enumerate(experiments, 1):
        cache = WORK / f"{material}_{experiment}.json"
        record = None
        if cache.is_file():
            candidate = json.loads(cache.read_text())
            if candidate.get("fingerprint") == fingerprint:
                record, cached = candidate, cached + 1
        if record is None:
            record = run_experiment(material, experiment, fingerprint)
            cache.with_suffix(".tmp").write_text(json.dumps(record, sort_keys=True) + "\n")
            cache.with_suffix(".tmp").replace(cache)
        records.append(record)
        print(f"{index:3d}/132 {material:>5s} {experiment:<16s} wall={record['wall_s']:.2f}s cached={cached}",
              file=sys.stderr, flush=True)
    output = {
        "schema": "actinv-p149-s1-fns-tendl2017-gas-1",
        "protocol": "P149 S1",
        "decay_data_note": "ACTINV uses ENDF/B-VIII.0 decay data with a JEFF-3.3 fallback; the FISPACT-II reference "
                           "uses its own TENDL-2017-era decay library. The activation evaluation is shared; "
                           "processing, decay data and solvers are not.",
        "input_identities": identities,
        "fingerprint": fingerprint,
        "actinv_version": getattr(cb3.actinv, "__version__", "unavailable"),
        "experiment_count": len(records),
        "summary": {"actinv_tendl2017_gas": cb3.aggregate(records, "actinv_tendl2017_gas_uW_g"),
                    "fispact_4_tendl2017_published": cb3.aggregate(records, "fispact_tendl2017_uW_g")},
        "wall_s": time.perf_counter() - started,
        "records": records,
    }
    output["checks"] = {
        "all_132_experiments": len(records) == 132,
        "all_certificate_inputs_match": all(
            r["certificate_inputs"] == {k: expected[k] for k in r["certificate_inputs"]} for r in records),
        "all_have_pairs": all(r["pairs"] for r in records),
        "all_gas_runs": all(r["gas_ledger"] is not None for r in records),
    }
    output["pass"] = all(output["checks"].values())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: output[k] for k in ("summary", "checks", "pass")}, indent=1))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
