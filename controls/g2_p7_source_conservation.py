#!/usr/bin/env python3
"""P7-G2: independent photon integration, group collapse, normalization, and missing-data bounds."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g2_p7_source_conservation.json"
sys.path.insert(0, str(ROOT / "controls"))
from p7_spectra import parse_file, photon_shape

EV = 1.602176634e-19
BOUNDARIES = [
    0.0, 1.0e4, 2.0e4, 5.0e4, 1.0e5, 2.0e5, 3.0e5, 4.0e5, 6.0e5,
    8.0e5, 1.0e6, 1.22e6, 1.44e6, 1.66e6, 2.0e6, 2.5e6, 3.0e6,
    4.0e6, 5.0e6, 6.5e6, 8.0e6, 1.0e7, 1.2e7, 1.4e7, 2.0e7,
]
CASES = {"Co60": (27060, 0), "Cs137": (55137, 0), "Ba137m1": (56137, 1), "Mn68": (25068, 0)}


def probe_path() -> Path:
    supplied = os.environ.get("ACTINV_PHOTON_PROBE")
    if supplied:
        return Path(supplied)
    for profile in ("debug", "release"):
        candidate = ROOT / "target" / profile / "photon_probe"
        if candidate.exists():
            return candidate
    raise RuntimeError("build actinv-core's photon_probe before running P7 G2")


def relative(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: g2_p7_source_conservation.py DECAY.endf RESPONSE.json")
    decay_path, response_path = sys.argv[1:]
    records = parse_file(decay_path)
    rust = json.loads(
        subprocess.run(
            [str(probe_path()), decay_path, response_path], check=True, capture_output=True, text=True
        ).stdout
    )
    comparisons = {}
    worst_reader = worst_count = worst_energy = 0.0
    failures = []
    expected_ledgered = []
    for name, key in CASES.items():
        py = photon_shape(records[key], BOUNDARIES)
        case = rust["nuclides"][name]
        source, diagnostics = case["source"], case["diagnostics"]
        by = source["by_nuclide"][0]
        values = [
            (py["raw_count"], by["raw_photons_per_decay"]),
            (py["raw_moment_eV"], by["raw_spectrum_energy_eV_per_decay"]),
            (py["scale"], by["energy_normalization"]),
            (py["source_count"], by["source_photons_per_decay"]),
        ]
        for group, py_count, py_moment in zip(by["groups"], py["group_count"], py["group_moment_eV"]):
            values.extend([(py_count, group["photons_s_g"]), (py_moment * EV, group["power_W_g"])])
        reader_error = max(relative(a, b) for a, b in values)
        collapsed_count = sum(group["photons_s_g"] for group in source["groups"])
        collapsed_count += diagnostics["group_underflow_photons_s_g"] + diagnostics["group_overflow_photons_s_g"]
        count_error = relative(collapsed_count, by["source_photons_per_decay"])
        collapsed_energy = sum(group["power_W_g"] for group in source["groups"])
        collapsed_energy += diagnostics["group_underflow_power_W_g"] + diagnostics["group_overflow_power_W_g"]
        e_em = records[key]["energies"][2]
        energy_error = relative(collapsed_energy / EV, e_em)
        ledger_names = [item["nuclide"] for item in diagnostics["energy_normalized_spectra"]]
        if abs(py["scale"] - 1.0) > 1e-12:
            expected_ledgered.append(name)
            if name not in ledger_names:
                failures.append(f"{name} normalization absent from ledger")
        elif name in ledger_names:
            failures.append(f"{name} spurious normalization ledger entry")
        if reader_error > 1e-12 or count_error > 1e-12 or energy_error > 1e-6:
            failures.append(f"{name} conservation tolerance")
        comparisons[name] = {
            "raw_count": py["raw_count"],
            "raw_energy_eV": py["raw_moment_eV"],
            "source_scale": py["scale"],
            "python_rust_max_relative": reader_error,
            "count_closure_relative": count_error,
            "energy_to_E_EM_relative": energy_error,
        }
        worst_reader = max(worst_reader, reader_error)
        worst_count = max(worst_count, count_error)
        worst_energy = max(worst_energy, energy_error)

    planted = rust["planted_missing_spectrum"]
    expected_bound = 2.5 * records[(27060, 0)]["energies"][2] * EV
    missing_rows = planted["diagnostics"]["nuclides_with_em_energy_but_no_photon_spectrum"]
    missing_ok = (
        planted["source"]["total_photons_s_g"] == 0.0
        and len(missing_rows) == 1
        and missing_rows[0]["unrepresented_power_W_g"] == expected_bound
        and planted["source"]["unrepresented_gamma_power_W_g"] == expected_bound
    )
    if not missing_ok:
        failures.append("planted missing-spectrum bound")

    narrow = rust["planted_narrow_groups"]
    py_narrow = photon_shape(records[(27060, 0)], [1.2e6, 1.4e6])
    narrow_diag = narrow["diagnostics"]
    outside_pairs = [
        (py_narrow["under_count"], narrow_diag["group_underflow_photons_s_g"]),
        (py_narrow["under_moment_eV"] * EV, narrow_diag["group_underflow_power_W_g"]),
        (py_narrow["over_count"], narrow_diag["group_overflow_photons_s_g"]),
        (py_narrow["over_moment_eV"] * EV, narrow_diag["group_overflow_power_W_g"]),
    ]
    outside_error = max(relative(a, b) for a, b in outside_pairs)
    outside_ok = outside_error <= 1e-12 and any(a > 0.0 for a, _ in outside_pairs)
    if not outside_ok:
        failures.append("planted group underflow/overflow ledger")

    passed = not failures
    result = {
        "nuclides": comparisons,
        "normalizations_ledgered": sorted(expected_ledgered),
        "worst_python_rust_relative": worst_reader,
        "worst_count_closure_relative": worst_count,
        "worst_energy_to_E_EM_relative": worst_energy,
        "planted_missing_spectrum": {"expected_power_W_g": expected_bound, "pass": missing_ok},
        "planted_outside_groups": {"max_relative": outside_error, "pass": outside_ok},
        "failures": failures,
        "pass": passed,
    }
    RESULT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
