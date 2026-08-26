#!/usr/bin/env python3
"""P7-G4: dose constants and independent FISPACT semi-infinite-slab equation."""
from __future__ import annotations

import bisect
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g4_p7_dose.json"
sys.path.insert(0, str(ROOT / "controls"))
from p7_spectra import parse_file, photon_shape

EV = 1.602176634e-19
BOUNDARIES = [
    0.0, 1.0e4, 2.0e4, 5.0e4, 1.0e5, 2.0e5, 3.0e5, 4.0e5, 6.0e5,
    8.0e5, 1.0e6, 1.22e6, 1.44e6, 1.66e6, 2.0e6, 2.5e6, 3.0e6,
    4.0e6, 5.0e6, 6.5e6, 8.0e6, 1.0e7, 1.2e7, 1.4e7, 2.0e7,
]
KEYS = {"Co60": (27060, 0), "Cs137": (55137, 0), "Ba137m1": (56137, 1), "Mn68": (25068, 0)}


def probe_path() -> Path:
    supplied = os.environ.get("ACTINV_PHOTON_PROBE")
    if supplied:
        return Path(supplied)
    for profile in ("debug", "release"):
        candidate = ROOT / "target" / profile / "photon_probe"
        if candidate.exists():
            return candidate
    raise RuntimeError("build actinv-core's photon_probe before running P7 G4")


def relative(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def interpolate(curve: dict, energy: float) -> float | None:
    energies, values = curve["energy_eV"], curve["values_cm2_g"]
    if energy < energies[0] or energy > energies[-1]:
        return None
    upper = bisect.bisect_right(energies, energy)
    if upper and energies[upper - 1] == energy:
        return values[upper - 1]
    if upper == 0 or upper == len(energies):
        return None
    x0, x1 = energies[upper - 1 : upper + 1]
    y0, y1 = values[upper - 1 : upper + 1]
    fraction = math.log(energy / x0) / math.log(x1 / x0)
    return math.exp(math.log(y0) + fraction * math.log(y1 / y0))


def independent_dose(shape: dict, response: dict) -> tuple[float, float]:
    air = response["air_mass_energy_absorption"]
    iron = response["element_mass_attenuation"]["Fe"]
    gamma_weighted = 0.0
    contact = 0.0
    for count, moment in zip(shape["group_count"], shape["group_moment_eV"]):
        if count <= 0.0:
            continue
        energy = moment / count
        mu_air = interpolate(air, energy)
        mu_iron = interpolate(iron, energy)
        if energy >= 2.0e4 and mu_air is not None:
            gamma_weighted += moment * mu_air
        if mu_air is None or mu_iron is None:
            raise RuntimeError(f"response does not cover {energy} eV")
        # B=2, so B/2 is one. eV/s/g -> J/s/g -> Gy/h.
        contact += (mu_air / mu_iron) * moment * EV * 1000.0 * 3600.0
    gamma_constant = gamma_weighted * EV * 0.1 / (4.0 * math.pi) * 3.6e15
    return gamma_constant, contact


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: g4_p7_dose.py DECAY.endf RESPONSE.json")
    decay_path, response_path = sys.argv[1:]
    records = parse_file(decay_path)
    response = json.loads(Path(response_path).read_text())
    rust = json.loads(
        subprocess.run(
            [str(probe_path()), decay_path, response_path], check=True, capture_output=True, text=True
        ).stdout
    )
    independent = {}
    max_gamma_impl = max_contact_impl = 0.0
    for name, key in KEYS.items():
        shape = photon_shape(records[key], BOUNDARIES)
        gamma, contact = independent_dose(shape, response)
        by = rust["nuclides"][name]["source"]["by_nuclide"][0]
        gamma_error = relative(gamma, by["gamma_constant_mGy_m2_GBq_h"])
        contact_error = relative(contact, by["contact_gamma_air_dose_proxy_Gy_h"])
        max_gamma_impl = max(max_gamma_impl, gamma_error)
        max_contact_impl = max(max_contact_impl, contact_error)
        independent[name] = {
            "gamma_constant_mGy_m2_GBq_h": gamma,
            "contact_Fe_Gy_h_per_Bq_g": contact,
            "rust_gamma_relative": gamma_error,
            "rust_contact_relative": contact_error,
        }

    co = independent["Co60"]["gamma_constant_mGy_m2_GBq_h"]
    branch = next(mode["br"] for mode in records[(55137, 0)]["modes"] if round(mode["rfs"]) == 1)
    equilibrium_cs = (
        independent["Cs137"]["gamma_constant_mGy_m2_GBq_h"]
        + branch * independent["Ba137m1"]["gamma_constant_mGy_m2_GBq_h"]
    )
    reference_errors = {"Co60": relative(co, 0.309), "Cs137_Ba137m_equilibrium": relative(equilibrium_cs, 0.078)}

    activities = {"Co60": 1.0, "Cs137": 2.0, "Ba137m1": 3.0, "Mn68": 4.0}
    expected_total = sum(activities[name] * independent[name]["contact_Fe_Gy_h_per_Bq_g"] for name in KEYS)
    combined = rust["combined"]["source"]
    rust_total = combined["contact_gamma_air_dose_proxy_Gy_h"]
    rust_sum = sum(row["contact_gamma_air_dose_proxy_Gy_h"] for row in combined["by_nuclide"])
    total_equation_error = relative(expected_total, rust_total)
    contribution_error = relative(rust_sum, rust_total)

    passed = (
        max(reference_errors.values()) <= 0.02
        and max_gamma_impl <= 1e-12
        and max_contact_impl <= 1e-12
        and total_equation_error <= 1e-12
        and contribution_error <= 1e-12
    )
    result = {
        "independent": independent,
        "references": {
            "Co60": {"calculated": co, "tabulated": 0.309, "relative": reference_errors["Co60"]},
            "Cs137_Ba137m_equilibrium": {
                "branch_to_Ba137m": branch,
                "calculated": equilibrium_cs,
                "tabulated": 0.078,
                "relative": reference_errors["Cs137_Ba137m_equilibrium"],
            },
        },
        "max_independent_rust_gamma_relative": max_gamma_impl,
        "max_independent_rust_contact_relative": max_contact_impl,
        "combined_contact": {
            "independent_Gy_h": expected_total,
            "rust_Gy_h": rust_total,
            "equation_relative": total_equation_error,
            "nuclide_sum_relative": contribution_error,
        },
        "pass": passed,
    }
    RESULT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
