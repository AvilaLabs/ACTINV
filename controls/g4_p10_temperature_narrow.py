#!/usr/bin/env python3
"""P10 G4 exact SIGMA1, ultra-narrow, seeded-regression and performance control."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.integrate import quad


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from endf_common import fields, read_tab1, sections  # noqa: E402
from resonance import KCONST, parse_mf2, reconstruct_range  # noqa: E402


RESULT = ROOT / "results" / "g4_p10_temperature_narrow.json"
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target" / "release" / "dump"))
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
PYTHON = Path(
    os.environ.get(
        "ACTINV_CONTROL_PYTHON", "/home/connoravila/.venvs/w003env/bin/python"
    )
)
TENDL_DIR = Path(
    os.environ.get("ACTINV_TENDL2023_DIR", "/home/connoravila/nuclear-data/tendl-2023/files")
)
FR226 = Path(
    os.environ.get(
        "ACTINV_P10_FR226_ENDF", TENDL_DIR / "n_087-Fr-226_8767.dat"
    )
)
RB94 = Path(
    os.environ.get("ACTINV_P10_RB94_ENDF", TENDL_DIR / "n_037-Rb-94_3752.dat")
)
W186 = Path(
    os.environ.get(
        "ACTINV_P10_W186_ENDF",
        "/home/connoravila/nuclear-data/fendl-3.2c/endf/n_7443_74-W-186.endf",
    )
)
FE56 = Path(
    os.environ.get(
        "ACTINV_P10_FE56_ENDF", TENDL_DIR / "n_026-Fe-56_2631.dat"
    )
)
P4_LIBRARY = Path(
    os.environ.get(
        "ACTINV_P10_P4_LIBRARY",
        "/home/connoravila/nuclear-data/tendl-2023/actinv_tendl2023_709g.npz",
    )
)
P4_INDEX = Path(
    os.environ.get(
        "ACTINV_P10_P4_INDEX",
        "/home/connoravila/nuclear-data/tendl-2023/actinv_tendl2023_709g_index.json",
    )
)
REUSED_SAMPLE = os.environ.get("ACTINV_P10_G4_SAMPLE_LIBRARY")
PROFILE_RUNS = int(os.environ.get("ACTINV_P10_G4_PROFILE_RUNS", "3"))
PROFILE_WORKERS = int(os.environ.get("ACTINV_P10_G4_PROFILE_WORKERS", "4"))

EXPECTED_HASHES = {
    FR226: "5a2f9fa9b5f53cdf132444694f2502b12fe4f179ca54c06cde0672228df87e67",
    RB94: "0e25329d3881b7af74419ae3a78495c01470bf304c9f9ecc03a2a91416b693f0",
    W186: "bf6bf3bb7a1583be49ae8aab865e75d256e0965f969f38a14d63260b3f4a8744",
    P4_LIBRARY: "a9f90234e42c538676de904c734510c4a62126017459e638ed338d052072e92c",
    P4_INDEX: "9bd0910e65d57b1d80252b199b81784f0d1f2e5add88c137b453498f8f0be605",
}
TEMPERATURES_K = (0.0, 293.6, 600.0, 900.0)
W_OUTPUT_EV = (0.1, 1.0, 10.0, 100.0, 1000.0, 5000.0)
KB_EV_PER_K = 8.617_333_262e-5
KERNEL_TAIL = 12.0
GAUSS_ORDER = 64
GAUSS_NODES, GAUSS_WEIGHTS = leggauss(GAUSS_ORDER)
ADDRESS_SPACE_BYTES = 2 * 1024**3

SEED_FILES = (
    "n_019-K-42_1934.dat",
    "n_023-V-55_2340.dat",
    "n_024-Cr-50_2425.dat",
    "n_027-Co-62M_2735.dat",
    "n_030-Zn-67_3034.dat",
    "n_030-Zn-77M_3065.dat",
    "n_034-Se-77M_3435.dat",
    "n_036-Kr-87_3652.dat",
    "n_038-Sr-84_3825.dat",
    "n_038-Sr-89_3840.dat",
    "n_039-Y-79_3895.dat",
    "n_039-Y-90M_3929.dat",
    "n_041-Nb-93_4125.dat",
    "n_043-Tc-105_4343.dat",
    "n_045-Rh-95_4501.dat",
    "n_048-Cd-99_4804.dat",
    "n_049-In-117M_4938.dat",
    "n_051-Sb-111_5095.dat",
    "n_052-Te-109_5192.dat",
    "n_055-Cs-120M_5487.dat",
    "n_059-Pr-134M_5905.dat",
    "n_067-Ho-171_6743.dat",
    "n_068-Er-149_6786.dat",
    "n_074-W-164_7377.dat",
    "n_075-Re-188M_7535.dat",
    "n_076-Os-197_7664.dat",
    "n_077-Ir-201_7755.dat",
    "n_079-Au-180_7874.dat",
    "n_080-Hg-209_8064.dat",
    "n_081-Tl-188M_8081.dat",
    "n_081-Tl-195M_8102.dat",
    "n_083-Bi-210_8328.dat",
    "n_088-Ra-226_8834.dat",
    "n_090-Th-224_9016.dat",
    "n_093-Np-235_9340.dat",
    "n_095-Am-233_9519.dat",
    "n_098-Cf-245_9840.dat",
    "n_107-Bh-272_0772.dat",
)

FISSION_SENTINEL_FILES = {
    "n_075-Re-188M_7535.dat",
    "n_076-Os-197_7664.dat",
    "n_077-Ir-201_7755.dat",
    "n_079-Au-180_7874.dat",
    "n_080-Hg-209_8064.dat",
    "n_081-Tl-188M_8081.dat",
    "n_081-Tl-195M_8102.dat",
    "n_083-Bi-210_8328.dat",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def limit_address_space() -> None:
    resource.setrlimit(
        resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES)
    )


def run_limited(arguments: list[str], *, env: dict[str, str] | None = None) -> dict:
    started = time.perf_counter()
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=limit_address_space,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"stdout:\n{completed.stdout[-4000:]}\nstderr:\n{completed.stderr[-4000:]}"
        )
    return {
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "elapsed_seconds": elapsed,
    }


def relative(actual: float, reference: float) -> float:
    return abs(actual - reference) / max(abs(actual), abs(reference), 1e-300)


def stable_kernel_difference(x: np.ndarray, y: float) -> np.ndarray:
    return np.exp(-np.square(x - y)) * -np.expm1(-4.0 * x * y)


def integrate_nodes(function, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    middle = 0.5 * (low + high)
    half = 0.5 * (high - low)
    return float(half * np.dot(GAUSS_WEIGHTS, function(middle + half * GAUSS_NODES)))


def sigma1_reference(
    energy: np.ndarray,
    sigma: np.ndarray,
    temperature_k: float,
    awr: float,
    output_ev: float,
) -> float:
    """Independent finite-segment Gauss-Legendre evaluation of the SIGMA1 integral."""
    kt = KB_EV_PER_K * temperature_k / awr
    x = np.sqrt(energy / kt)
    y = math.sqrt(output_ev / kt)
    low = max(0.0, y - KERNEL_TAIL)
    high = y + KERNEL_TAIL
    total = 0.0

    low_high = min(high, float(x[0]))
    if low_high > low:
        coefficient = float(sigma[0] * x[0])
        total += integrate_nodes(
            lambda points: coefficient
            * points
            * stable_kernel_difference(points, y),
            low,
            low_high,
        )

    segment_low = np.maximum(x[:-1], low)
    segment_high = np.minimum(x[1:], high)
    valid = segment_high > segment_low
    if np.any(valid):
        left = segment_low[valid]
        right = segment_high[valid]
        middle = 0.5 * (left + right)
        half = 0.5 * (right - left)
        points = middle[:, None] + half[:, None] * GAUSS_NODES[None, :]
        indices = np.nonzero(valid)[0]
        e0 = energy[indices, None]
        e1 = energy[indices + 1, None]
        s0 = sigma[indices, None]
        s1 = sigma[indices + 1, None]
        point_energy = kt * np.square(points)
        point_sigma = s0 + (s1 - s0) * (point_energy - e0) / (e1 - e0)
        values = (
            point_sigma
            * np.square(points)
            * stable_kernel_difference(points, y)
        )
        total += float(np.sum(half * (values @ GAUSS_WEIGHTS)))

    high_low = max(low, float(x[-1]))
    if high > high_low:
        constant = float(sigma[-1])
        total += integrate_nodes(
            lambda points: constant
            * np.square(points)
            * stable_kernel_difference(points, y),
            high_low,
            high,
        )
    return total / (y * y * math.sqrt(math.pi))


def zero_k_reference(energy: np.ndarray, sigma: np.ndarray, output_ev: float) -> float:
    if output_ev <= energy[0]:
        return float(sigma[0] * math.sqrt(energy[0] / output_ev))
    if output_ev >= energy[-1]:
        return float(sigma[-1])
    upper = int(np.searchsorted(energy, output_ev, side="right"))
    lower = upper - 1
    weight = (output_ev - energy[lower]) / (energy[upper] - energy[lower])
    return float(sigma[lower] + weight * (sigma[upper] - sigma[lower]))


def parse_doppler_probe(text: str) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    inputs: dict[str, list[tuple[float, float]]] = {}
    outputs: dict[str, list[tuple[float, float]]] = {}
    for line in text.splitlines():
        values = line.split()
        if values[:1] == ["I"]:
            inputs.setdefault(values[1], []).append((float(values[2]), float(values[3])))
        elif values[:1] == ["O"]:
            outputs.setdefault(values[1], []).append((float(values[2]), float(values[3])))
    if set(inputs) != {"one_over_v", "constant", "synthetic_line"} or set(outputs) != set(
        inputs
    ):
        raise ValueError("incomplete Doppler probe output")
    return (
        {key: np.asarray(value) for key, value in inputs.items()},
        {key: np.asarray(value) for key, value in outputs.items()},
    )


def synthetic_kernel_control() -> dict:
    cases = {}
    maximum_relative = 0.0
    zero_identity_maximum_absolute = 0.0
    for temperature in TEMPERATURES_K:
        completed = run_limited([str(DUMP), "doppler-probe", str(temperature)])
        inputs, outputs = parse_doppler_probe(completed["stdout"])
        temperature_cases = {}
        for name, output in outputs.items():
            source = inputs[name]
            comparisons = []
            for output_energy, actual in output:
                if temperature == 0.0:
                    reference = zero_k_reference(source[:, 0], source[:, 1], output_energy)
                else:
                    reference = sigma1_reference(
                        source[:, 0], source[:, 1], temperature, 55.0, output_energy
                    )
                error = relative(actual, reference)
                maximum_relative = max(maximum_relative, error)
                comparisons.append(
                    {
                        "energy_eV": float(output_energy),
                        "rust_b": float(actual),
                        "reference_b": reference,
                        "relative": error,
                    }
                )
            if temperature == 0.0:
                input_by_energy = {float(e): float(s) for e, s in source}
                output_by_energy = {float(e): float(s) for e, s in output}
                for energy, expected in input_by_energy.items():
                    zero_identity_maximum_absolute = max(
                        zero_identity_maximum_absolute,
                        abs(output_by_energy[energy] - expected),
                    )
            temperature_cases[name] = comparisons
        cases[str(temperature)] = temperature_cases
    return {
        "reference": {
            "method": "64-point Gauss-Legendre on each linear-in-energy segment",
            "kernel_tail_in_sqrt_energy": KERNEL_TAIL,
            "production_moment_formulas_imported": False,
        },
        "temperatures": cases,
        "maximum_relative": maximum_relative,
        "zero_K_input_identity_maximum_absolute_b": zero_identity_maximum_absolute,
        "pass": maximum_relative <= 1e-10 and zero_identity_maximum_absolute == 0.0,
    }


def w186_kernel_control() -> dict:
    arguments = [
        str(DUMP),
        "processed-kernel",
        str(W186),
        "102",
        ",".join(str(value) for value in TEMPERATURES_K),
        *(str(value) for value in W_OUTPUT_EV),
    ]
    completed = run_limited(arguments)
    awr = None
    refinement_passes = None
    source = []
    output = []
    for line in completed["stdout"].splitlines():
        values = line.split()
        if values[:1] == ["C"]:
            awr = float(values[1])
            refinement_passes = int(values[3])
        elif values[:1] == ["I"]:
            source.append((float(values[1]), float(values[2])))
        elif values[:1] == ["O"]:
            output.append((float(values[1]), float(values[2]), float(values[3])))
    if awr is None or len(source) < 2 or len(output) != len(TEMPERATURES_K) * len(
        W_OUTPUT_EV
    ):
        raise ValueError("incomplete W-186 processed-kernel output")
    source_array = np.asarray(source)
    comparisons = []
    maximum_relative = 0.0
    for temperature, energy, actual in output:
        if temperature == 0.0:
            reference = zero_k_reference(
                source_array[:, 0], source_array[:, 1], energy
            )
        else:
            reference = sigma1_reference(
                source_array[:, 0], source_array[:, 1], temperature, awr, energy
            )
        error = relative(actual, reference)
        maximum_relative = max(maximum_relative, error)
        comparisons.append(
            {
                "temperature_K": temperature,
                "energy_eV": energy,
                "rust_b": actual,
                "reference_b": reference,
                "relative": error,
            }
        )
    return {
        "awr": awr,
        "zero_K_table_points": len(source),
        "zero_K_refinement_passes": refinement_passes,
        "duplicate_energy_points": int(np.sum(np.diff(source_array[:, 0]) == 0.0)),
        "comparisons": comparisons,
        "maximum_relative": maximum_relative,
        "pass": maximum_relative <= 1e-10,
    }


def isolated_range(raw_range: dict, group: dict, resonance_index: int) -> dict:
    result = copy.deepcopy(raw_range)
    selected_group = copy.deepcopy(group)
    count = len(group["ER"])
    for key, value in group.items():
        if isinstance(value, np.ndarray) and value.shape == (count,):
            selected_group[key] = value[[resonance_index]]
    result["L"] = [selected_group]
    return result


def retained_fraction(distance: float, inner: float, outer: float) -> float:
    if distance <= inner:
        return 0.0
    if distance >= outer:
        return 1.0
    fraction = (distance - inner) / (outer - inner)
    return fraction * fraction * (3.0 - 2.0 * fraction)


def effective_total_width(group: dict, index: int) -> float:
    """Derive the LRF=1/2 natural width frozen by P10 Amendment D."""
    reported = float(group["GT"][index])
    components = float(group["GN"][index] + group["GG"][index] + group["GF"][index])
    return components if int(group["LRX"]) == 0 else max(reported, components)


def independent_line_area(
    evaluation: dict, raw_range: dict, group: dict, index: int, mt: int, certificate: dict
) -> tuple[float, float]:
    energy = float(group["ER"][index])
    width = effective_total_width(group, index)
    reaction = float(group["GG"][index] if mt == 102 else group["GF"][index])
    neutron = float(group["GN"][index])
    spin = float(group["AJ"][index])
    wave = KCONST * group["AWRI"] / (group["AWRI"] + 1.0) * math.sqrt(energy)
    statistical = (2.0 * abs(spin) + 1.0) / (
        2.0 * (2.0 * raw_range["SPI"] + 1.0)
    )
    full_closed = (
        2.0
        * math.pi**2
        / wave**2
        * statistical
        * neutron
        * reaction
        / width
    )
    low_energy = certificate["core_low_eV"]
    high_energy = certificate["core_high_eV"]
    theta_low = math.atan(2.0 * (low_energy - energy) / width)
    theta_high = math.atan(2.0 * (high_energy - energy) / width)
    if certificate["range_edge"]:
        closed = full_closed * (theta_high - theta_low) / math.pi
    else:
        closed = full_closed

    one_line = isolated_range(raw_range, group, index)
    one_line["L"][0]["GT"][0] = width
    available_left = (energy - raw_range["EL"]) / width
    available_right = (raw_range["EH"] - energy) / width
    edge_is_upper = available_right <= available_left
    outer = max((energy - low_energy) / width, (high_energy - energy) / width)
    inner = outer - 100_000.0

    def integrand(theta: float) -> float:
        tangent = math.tan(theta)
        point = energy + 0.5 * width * tangent
        distance = abs(point - energy) / width
        if certificate["range_edge"]:
            far_side = point < energy if edge_is_upper else point > energy
            if far_side:
                removed = 1.0 - retained_fraction(distance, 600_000.0, 700_000.0)
            else:
                removed = 1.0
        else:
            removed = 1.0 - retained_fraction(distance, inner, outer)
        reconstructed = reconstruct_range(
            one_line, np.asarray([point]), evaluation["AWR"]
        )
        channel = "capture" if mt == 102 else "fission"
        jacobian = 0.5 * width * (1.0 + tangent * tangent)
        return float(reconstructed[channel][0] * removed * jacobian)

    breakpoints = []
    if certificate["range_edge"]:
        transition = math.atan(2.0 * 600_000.0)
        candidate = -transition if edge_is_upper else transition
        if theta_low < candidate < theta_high:
            breakpoints.append(candidate)
    else:
        for candidate in (-math.atan(2.0 * inner), math.atan(2.0 * inner)):
            if theta_low < candidate < theta_high:
                breakpoints.append(candidate)
    boundaries = [theta_low, *sorted(breakpoints), theta_high]
    direct = 0.0
    for low, high in zip(boundaries[:-1], boundaries[1:]):
        part, _ = quad(
            integrand,
            low,
            high,
            epsabs=max(abs(closed) * 2e-9 / (len(boundaries) - 1), 1e-300),
            epsrel=2e-9,
            limit=200,
        )
        direct += part
    return float(direct), float(closed)


def parse_ultra_lines(text: str) -> list[dict]:
    lines = []
    declared_count = None
    for line in text.splitlines():
        values = line.split()
        if values[:1] == ["U"]:
            lines.append(
                {
                    "zai": int(values[1]),
                    "energy_eV": float(values[2]),
                    "total_width_eV": float(values[3]),
                    "doppler_width_eV": float(values[4]),
                    "width_to_doppler": float(values[5]),
                    "rust_direct_b_eV": float(values[6]),
                    "rust_closed_b_eV": float(values[7]),
                    "group": int(values[8]),
                    "range_edge": bool(int(values[9])),
                    "core_low_eV": float(values[10]),
                    "core_high_eV": float(values[11]),
                }
            )
        elif values[:1] == ["N"]:
            declared_count = int(values[1])
    if declared_count is None or declared_count != len(lines):
        raise ValueError("incomplete ultra-narrow certificate output")
    return lines


def ultra_narrow_control() -> dict:
    evaluation = parse_mf2(FR226)
    if evaluation is None:
        raise ValueError("independent parser found no Fr-226 MF=2")
    by_mt = {}
    maximum_rust_direct_closed = 0.0
    maximum_direct_reference = 0.0
    maximum_closed_reference = 0.0
    maximum_reported_effective_relative = 0.0
    worst_reported_effective = None
    for mt in (18, 102):
        completed = run_limited([str(DUMP), "ultra-lines", str(FR226), str(mt), "293.6"])
        certificates = parse_ultra_lines(completed["stdout"])
        comparisons = []
        for certificate in certificates:
            matches = []
            for isotope in evaluation["isotopes"]:
                if int(round(isotope["ZAI"])) != certificate["zai"]:
                    continue
                for raw_range in isotope["ranges"]:
                    if raw_range.get("LRU") != 1 or raw_range.get("LRF") not in (1, 2):
                        continue
                    for group in raw_range.get("L", []):
                        reaction = group["GG"] if mt == 102 else group["GF"]
                        for index, (energy, width, reaction_width) in enumerate(
                            zip(group["ER"], group["GT"], reaction)
                        ):
                            if reaction_width <= 0.0:
                                continue
                            effective_width = effective_total_width(group, index)
                            mismatch = relative(float(energy), certificate["energy_eV"]) + relative(
                                effective_width, certificate["total_width_eV"]
                            )
                            if mismatch <= 1e-10:
                                matches.append((mismatch, raw_range, group, index))
            if not matches:
                raise ValueError(
                    f"cannot match Fr-226 MT{mt} line at {certificate['energy_eV']} eV"
                )
            _, raw_range, group, index = min(matches, key=lambda item: item[0])
            reported_width = float(group["GT"][index])
            effective_width = effective_total_width(group, index)
            reported_effective_relative = relative(reported_width, effective_width)
            if reported_effective_relative > maximum_reported_effective_relative:
                maximum_reported_effective_relative = reported_effective_relative
                worst_reported_effective = {
                    "mt": mt,
                    "energy_eV": certificate["energy_eV"],
                    "reported_GT_eV": reported_width,
                    "effective_width_eV": effective_width,
                    "relative": reported_effective_relative,
                }
            direct_reference, closed_reference = independent_line_area(
                evaluation, raw_range, group, index, mt, certificate
            )
            rust_direct_closed = relative(
                certificate["rust_direct_b_eV"], certificate["rust_closed_b_eV"]
            )
            direct_error = relative(certificate["rust_direct_b_eV"], direct_reference)
            closed_error = relative(certificate["rust_closed_b_eV"], closed_reference)
            maximum_rust_direct_closed = max(
                maximum_rust_direct_closed, rust_direct_closed
            )
            maximum_direct_reference = max(maximum_direct_reference, direct_error)
            maximum_closed_reference = max(maximum_closed_reference, closed_error)
            comparisons.append(
                {
                    **certificate,
                    "reported_GT_eV": reported_width,
                    "independent_effective_width_eV": effective_width,
                    "independent_direct_b_eV": direct_reference,
                    "independent_closed_b_eV": closed_reference,
                    "rust_direct_vs_closed_relative": rust_direct_closed,
                    "rust_vs_independent_direct_relative": direct_error,
                    "rust_vs_independent_closed_relative": closed_error,
                }
            )
        by_mt[str(mt)] = comparisons
    return {
        "reference": "independent ENDF parser/reconstruction plus SciPy theta-coordinate adaptive quadrature",
        "lines": by_mt,
        "line_count": sum(len(value) for value in by_mt.values()),
        "maximum_rust_direct_vs_closed_relative": maximum_rust_direct_closed,
        "maximum_rust_vs_independent_direct_relative": maximum_direct_reference,
        "maximum_rust_vs_independent_closed_relative": maximum_closed_reference,
        "width_semantics": {
            "rule": "LRX=0: GN+GG+GF; otherwise max(GT, GN+GG+GF)",
            "maximum_reported_GT_vs_effective_relative": maximum_reported_effective_relative,
            "worst": worst_reported_effective,
        },
        "pass": max(
            maximum_rust_direct_closed,
            maximum_direct_reference,
            maximum_closed_reference,
        )
        <= 1e-6,
    }


def load_small_library(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        return archive["rows"], archive["sig"], archive["bounds"]


def build_density(source: Path, name: str, density: int, directory: Path) -> tuple[Path, dict]:
    output = directory / f"{name}-d{density}.npz"
    cache = directory / f"cache-{name}-d{density}"
    completed = run_limited(
        [
            str(ACTINV),
            "build-library",
            str(source),
            str(output),
            "--format",
            "tendl",
            "--projectile",
            "neutron",
            "--groups",
            "fispact-709",
            "--temperature-K",
            "293.6",
            "--workers",
            "1",
            "--cache",
            str(cache),
            "--grid-density",
            str(density),
        ]
    )
    index_path = output.with_name(output.stem + "_index.json")
    return output, {
        "npz_sha256": sha256(output),
        "index_sha256": sha256(index_path),
        "elapsed_seconds": completed["elapsed_seconds"],
        "index": json.loads(index_path.read_text()),
    }


def density_control(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    targets = {"Fr-226": FR226, "Rb-94": RB94}
    result = {}
    maximum_relative = 0.0
    negative_values = 0
    convergence_flags = []
    for name, source in targets.items():
        builds = {}
        libraries = {}
        for density in (1, 2, 4):
            path, evidence = build_density(source, name.lower(), density, directory)
            builds[str(density)] = {key: value for key, value in evidence.items() if key != "index"}
            rows, sigma, bounds = load_small_library(path)
            libraries[density] = (rows, sigma, bounds)
            negative_values += int(np.sum(sigma < 0.0))
            for target in evidence["index"]["targets"]:
                for entry in target.get("ledger", []):
                    if "convergence" in entry.lower():
                        convergence_flags.append(f"{name}/d{density}: {entry}")
        comparisons = {}
        for left_density, right_density in ((1, 2), (2, 4)):
            left_rows, left_sigma, left_bounds = libraries[left_density]
            right_rows, right_sigma, right_bounds = libraries[right_density]
            if not np.array_equal(left_bounds, right_bounds):
                raise ValueError(f"{name} density builds have different boundaries")
            left_map = {tuple(map(int, row[1:])): index for index, row in enumerate(left_rows)}
            right_map = {
                tuple(map(int, row[1:])): index for index, row in enumerate(right_rows)
            }
            if set(left_map) != set(right_map):
                raise ValueError(f"{name} density builds have different row identities")
            pair_maximum = 0.0
            scored_groups = 0
            worst = None
            for identity in sorted(left_map):
                if identity[0] not in (18, 102):
                    continue
                left = left_sigma[left_map[identity]]
                right = right_sigma[right_map[identity]]
                scored = np.maximum(np.abs(left), np.abs(right)) >= 1e-4
                if not np.any(scored):
                    continue
                errors = np.abs(left - right) / np.maximum(
                    np.maximum(np.abs(left), np.abs(right)), 1e-300
                )
                group = int(np.argmax(np.where(scored, errors, -1.0)))
                if errors[group] > pair_maximum:
                    pair_maximum = float(errors[group])
                    worst = {
                        "identity": identity,
                        "group": group,
                        "left_b": float(left[group]),
                        "right_b": float(right[group]),
                    }
                scored_groups += int(np.sum(scored))
            maximum_relative = max(maximum_relative, pair_maximum)
            comparisons[f"d{left_density}_vs_d{right_density}"] = {
                "maximum_relative": pair_maximum,
                "scored_groups": scored_groups,
                "worst": worst,
            }
        result[name] = {"builds": builds, "comparisons": comparisons}
    return {
        "targets": result,
        "maximum_relative": maximum_relative,
        "negative_values": negative_values,
        "convergence_flags": convergence_flags,
        "pass": maximum_relative <= 1e-3
        and negative_values == 0
        and not convergence_flags,
    }


def expected_identity_changes(filename: str) -> tuple[set[tuple], set[tuple]]:
    if filename == "n_036-Kr-87_3652.dat":
        return set(), {(102, -1, -1, 0), (102, 36088, 0, -1)}
    if filename in FISSION_SENTINEL_FILES:
        return {(18, -1, 0, 10)}, {(18, 0, 0, 0)}
    return set(), set()


def exact_linline_group(
    energy: np.ndarray, sigma: np.ndarray, low: float, high: float
) -> float:
    integrals = []
    for e0, e1, s0, s1 in zip(energy[:-1], energy[1:], sigma[:-1], sigma[1:]):
        segment_low = max(low, float(e0))
        segment_high = min(high, float(e1))
        if segment_high <= segment_low or e1 <= e0:
            continue
        slope = (s1 - s0) / (e1 - e0)
        sigma_low = s0 + slope * (segment_low - e0)
        logarithm = math.log1p((segment_high - segment_low) / segment_low)
        # Local coordinates avoid cancellation between a large global intercept and slope*E.
        integrals.append(
            sigma_low * logarithm
            + slope
            * ((segment_high - segment_low) - segment_low * logarithm)
        )
    return float(math.fsum(integrals) / math.log1p((high - low) / low))


def parse_mf3(path: Path) -> dict[int, tuple]:
    result = {}
    for (_, mf, mt), lines in sections(path):
        if mf == 3:
            result[mt] = read_tab1(lines, 1)[0]
    return result


def y90_discontinuity(
    path: Path,
    old_target: int,
    new_target: int,
    old_rows: np.ndarray,
    old_sigma: np.ndarray,
    new_rows: np.ndarray,
    new_sigma: np.ndarray,
    bounds: np.ndarray,
) -> dict:
    selected = None
    for (_, mf, mt), lines in sections(path):
        if mf != 10 or mt != 37:
            continue
        count = int(fields(lines[0])[4])
        index = 1
        for _ in range(count):
            record, index = read_tab1(lines, index)
            if int(round(record[2])) == 39087 and int(record[3]) == 1:
                selected = record
    if selected is None:
        raise ValueError("Y-90m discontinuity table was not found")
    energy = np.asarray(selected[7])
    sigma = np.asarray(selected[8])
    group = int(
        np.nonzero((bounds[:-1] == 29e6) & (bounds[1:] == 30e6))[0][0]
    )
    reference = exact_linline_group(energy, sigma, bounds[group], bounds[group + 1])
    identity = (37, 39087, 1, 10)
    old_index = np.nonzero(
        np.all(old_rows == np.asarray((old_target, *identity)), axis=1)
    )[0][0]
    new_index = np.nonzero(
        np.all(new_rows == np.asarray((new_target, *identity)), axis=1)
    )[0][0]
    old_value = float(old_sigma[old_index, group])
    rust_value = float(new_sigma[new_index, group])
    rust_error = relative(rust_value, reference)
    legacy_error = relative(old_value, reference)
    return {
        "source_points_eV_b": [
            [float(e), float(s)]
            for e, s in zip(energy, sigma)
            if 28e6 <= e <= 30e6
        ],
        "group": group,
        "bounds_eV": [float(bounds[group]), float(bounds[group + 1])],
        "legacy_b": old_value,
        "rust_b": rust_value,
        "independent_exact_b": reference,
        "legacy_relative": legacy_error,
        "rust_relative": rust_error,
        "pass": bool(
            rust_error <= 2e-12 or abs(rust_value - reference) <= 1e-14
        ),
    }


def stream_old_subset(old_indices: list[int], directory: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    prefix = directory / "old-seeded"
    completed = run_limited(
        [
            str(DUMP),
            "library-targets",
            str(P4_LIBRARY),
            ",".join(str(value) for value in sorted(old_indices)),
            str(prefix),
        ]
    )
    row_count, group_count = (int(value) for value in completed["stdout"].split())
    rows = np.fromfile(str(prefix) + ".rows", dtype="<i8").reshape(row_count, 5)
    sigma = np.fromfile(str(prefix) + ".sig", dtype="<f8").reshape(
        row_count, group_count
    )
    bounds = np.fromfile(str(prefix) + ".bounds", dtype="<f8")
    return rows, sigma, bounds


def build_seed_sample(directory: Path) -> tuple[Path, Path, dict]:
    if REUSED_SAMPLE:
        library = Path(REUSED_SAMPLE)
        index = library.with_name(library.stem + "_index.json")
        return library, index, {"mode": "reused external deterministic build"}
    input_directory = directory / "seed-input"
    input_directory.mkdir()
    for filename in SEED_FILES:
        shutil.copy2(TENDL_DIR / filename, input_directory / filename)
    library = directory / "seeded-current.npz"
    completed = run_limited(
        [
            str(ACTINV),
            "build-library",
            str(input_directory),
            str(library),
            "--format",
            "tendl",
            "--projectile",
            "neutron",
            "--groups",
            "fispact-709",
            "--temperature-K",
            "293.6",
            "--workers",
            "1",
            "--cache",
            str(directory / "seed-cache"),
        ]
    )
    index = library.with_name(library.stem + "_index.json")
    return library, index, {
        "mode": "fresh Rust build",
        "elapsed_seconds": completed["elapsed_seconds"],
    }


def seeded_regression(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    library, index_path, build = build_seed_sample(directory)
    new_index = json.loads(index_path.read_text())
    old_index = json.loads(P4_INDEX.read_text())
    if [target["file"] for target in new_index["targets"]] != list(SEED_FILES):
        raise ValueError("fresh seeded build membership/order differs from the frozen sample")
    old_target_by_key = {
        (target["za"], target["liso"]): index
        for index, target in enumerate(old_index["targets"])
    }
    new_target_by_key = {
        (target["za"], target["liso"]): index
        for index, target in enumerate(new_index["targets"])
    }
    if set(new_target_by_key) - set(old_target_by_key):
        raise ValueError("seeded Rust build contains a target absent from the P4 baseline")
    old_indices = [old_target_by_key[key] for key in new_target_by_key]
    old_rows, old_sigma, old_bounds = stream_old_subset(old_indices, directory)
    new_rows, new_sigma, new_bounds = load_small_library(library)
    if not np.array_equal(old_bounds, new_bounds):
        raise ValueError("seeded P4/Rust libraries have different group boundaries")

    old_row_map = {tuple(map(int, row)): index for index, row in enumerate(old_rows)}
    new_row_map = {tuple(map(int, row)): index for index, row in enumerate(new_rows)}
    changes = []
    structural_pass = True
    eligible_rows = 0
    eligible_groups = 0
    worst_tolerance_fraction = 0.0
    worst = None
    source_hashes = {}
    for new_target, target in enumerate(new_index["targets"]):
        key = (target["za"], target["liso"])
        old_target = old_target_by_key[key]
        old_identities = {
            tuple(map(int, row[1:])) for row in old_rows if int(row[0]) == old_target
        }
        new_identities = {
            tuple(map(int, row[1:])) for row in new_rows if int(row[0]) == new_target
        }
        old_only = old_identities - new_identities
        new_only = new_identities - old_identities
        expected_old, expected_new = expected_identity_changes(target["file"])
        identity_pass = old_only == expected_old and new_only == expected_new
        structural_pass &= identity_pass
        if old_only or new_only:
            changes.append(
                {
                    "file": target["file"],
                    "old_only": sorted(old_only),
                    "new_only": sorted(new_only),
                    "pass": identity_pass,
                }
            )

        source = TENDL_DIR / target["file"]
        source_hashes[target["file"]] = sha256(source)
        for mt, record in parse_mf3(source).items():
            if mt in (4, 18, 102) or 51 <= mt <= 91:
                continue
            nbt = record[6]
            energy = np.asarray(record[7])
            sigma = np.asarray(record[8])
            if any(law != 2 for _, law in nbt) or np.any(np.diff(energy) < 0.0):
                continue
            identity = (mt, -1, -1, 0)
            old_row = old_row_map.get((old_target, *identity))
            new_row = new_row_map.get((new_target, *identity))
            if old_row is None or new_row is None:
                continue
            mask = (old_bounds[:-1] >= energy[0]) & (old_bounds[1:] <= energy[-1])
            discontinuities = energy[:-1][
                (np.diff(energy) == 0.0) & (np.diff(sigma) != 0.0)
            ]
            for edge in discontinuities:
                mask &= ~((old_bounds[:-1] <= edge) & (edge <= old_bounds[1:]))
            left = old_sigma[old_row, mask]
            right = new_sigma[new_row, mask]
            absolute = np.abs(left - right)
            tolerance = np.maximum(
                1e-14, 2e-12 * np.maximum(np.abs(left), np.abs(right))
            )
            fractions = absolute / tolerance
            if len(fractions):
                index = int(np.argmax(fractions))
                if fractions[index] > worst_tolerance_fraction:
                    groups = np.nonzero(mask)[0]
                    group = int(groups[index])
                    worst_tolerance_fraction = float(fractions[index])
                    worst = {
                        "file": target["file"],
                        "mt": mt,
                        "group": group,
                        "legacy_b": float(left[index]),
                        "rust_b": float(right[index]),
                        "absolute_b": float(absolute[index]),
                        "tolerance_b": float(tolerance[index]),
                    }
            eligible_rows += 1
            eligible_groups += int(np.sum(mask))

    y_target_key = (39090, 1)
    y_control = y90_discontinuity(
        TENDL_DIR / "n_039-Y-90M_3929.dat",
        old_target_by_key[y_target_key],
        new_target_by_key[y_target_key],
        old_rows,
        old_sigma,
        new_rows,
        new_sigma,
        old_bounds,
    )
    unchanged_pass = bool(
        eligible_rows == 928
        and eligible_groups == 188_327
        and worst_tolerance_fraction <= 1.0
    )
    return {
        "build": build,
        "library_sha256": sha256(library),
        "index_sha256": sha256(index_path),
        "source_hashes": source_hashes,
        "target_count": len(new_index["targets"]),
        "row_count": int(len(new_rows)),
        "structural": {
            "enumerated_changes": changes,
            "pass": structural_pass,
        },
        "unchanged_domain": {
            "eligible_rows": eligible_rows,
            "eligible_groups": eligible_groups,
            "expected_eligible_rows": 928,
            "expected_eligible_groups": 188_327,
            "worst_tolerance_fraction": worst_tolerance_fraction,
            "worst": worst,
            "pass": unchanged_pass,
        },
        "y90m_discontinuity": y_control,
        "pass": bool(structural_pass and unchanged_pass and y_control["pass"]),
    }


def parse_maximum_rss(stderr: str) -> int:
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", stderr)
    if not match:
        raise ValueError("GNU time did not report maximum RSS")
    return int(match.group(1))


def timed(arguments: list[str], env: dict[str, str]) -> dict:
    completed = run_limited(["/usr/bin/time", "-v", *arguments], env=env)
    return {
        "wall_seconds": completed["elapsed_seconds"],
        "peak_rss_kib": parse_maximum_rss(completed["stderr"]),
    }


def performance_control(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    if PROFILE_RUNS < 3 or PROFILE_RUNS % 2 == 0:
        return {
            "requested_runs": PROFILE_RUNS,
            "error": "an odd profile run count of at least three is required",
            "pass": False,
        }
    input_directory = directory / "profile-input"
    input_directory.mkdir()
    shutil.copy2(FE56, input_directory / FE56.name)
    environment = dict(os.environ)
    environment.update(
        {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "ACTINV_DENSE": "1",
        }
    )
    rust_runs = []
    python_runs = []
    for repeat in range(PROFILE_RUNS):
        order = ("rust", "python") if repeat % 2 == 0 else ("python", "rust")
        for implementation in order:
            run_directory = directory / f"profile-{implementation}-{repeat}"
            if implementation == "rust":
                result = timed(
                    [
                        str(ACTINV),
                        "build-library",
                        str(input_directory),
                        str(run_directory / "fe.npz"),
                        "--format",
                        "tendl",
                        "--projectile",
                        "neutron",
                        "--groups",
                        "fispact-709",
                        "--temperature-K",
                        "293.6",
                        "--workers",
                        str(PROFILE_WORKERS),
                        "--cache",
                        str(run_directory / "cache"),
                    ],
                    environment,
                )
                rust_runs.append(result)
            else:
                result = timed(
                    [
                        str(PYTHON),
                        str(ROOT / "controls" / "tendl_build.py"),
                        str(input_directory),
                        str(run_directory),
                        "--workers",
                        str(PROFILE_WORKERS),
                        "--limit",
                        "1",
                        "--name",
                        "fe",
                    ],
                    environment,
                )
                python_runs.append(result)
    rust_wall = statistics.median(run["wall_seconds"] for run in rust_runs)
    python_wall = statistics.median(run["wall_seconds"] for run in python_runs)
    rust_rss = statistics.median(run["peak_rss_kib"] for run in rust_runs)
    python_rss = statistics.median(run["peak_rss_kib"] for run in python_runs)
    return {
        "runs": PROFILE_RUNS,
        "workers_requested_for_both_builders": PROFILE_WORKERS,
        "rust_nested_pointwise_work_uses_the_same_pool": True,
        "fresh_cache_each_run": True,
        "alternating_order": True,
        "rust": {
            "measurements": rust_runs,
            "median_wall_seconds": rust_wall,
            "median_peak_rss_kib": rust_rss,
        },
        "python_predecessor": {
            "measurements": python_runs,
            "median_wall_seconds": python_wall,
            "median_peak_rss_kib": python_rss,
        },
        "pass": rust_wall <= python_wall and rust_rss <= python_rss,
    }


def main() -> None:
    required = [DUMP, ACTINV, PYTHON, TENDL_DIR, FR226, RB94, W186, FE56, P4_LIBRARY, P4_INDEX]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"missing P10 G4 input(s): {missing}")
    mismatched = {
        str(path): {"expected": expected, "actual": sha256(path)}
        for path, expected in EXPECTED_HASHES.items()
        if sha256(path) != expected
    }
    if mismatched:
        raise SystemExit(f"P10 G4 input hash mismatch: {mismatched}")
    for filename in SEED_FILES:
        if not (TENDL_DIR / filename).is_file():
            raise SystemExit(f"missing seeded evaluation: {filename}")

    with tempfile.TemporaryDirectory(prefix="actinv-p10-g4-") as temporary:
        work = Path(temporary)
        synthetic = synthetic_kernel_control()
        w186 = w186_kernel_control()
        ultra = ultra_narrow_control()
        density = density_control(work / "density")
        regression = seeded_regression(work / "regression")
        performance = performance_control(work / "performance")

    passed = all(
        control["pass"]
        for control in (synthetic, w186, ultra, density, regression, performance)
    )
    output = {
        "schema": "actinv-p10-g4-temperature-narrow-1",
        "protocol_sha256": sha256(ROOT / "protocols" / "ACTINV-P10_PROTOCOL.md"),
        "amendment_sha256": sha256(
            ROOT / "protocols" / "ACTINV-P10_AMENDMENT_A.md"
        ),
        "inputs": {
            "dump_sha256": sha256(DUMP),
            "actinv_sha256": sha256(ACTINV),
            "fr226_sha256": sha256(FR226),
            "rb94_sha256": sha256(RB94),
            "w186_sha256": sha256(W186),
            "fe56_sha256": sha256(FE56),
            "p4_library_sha256": sha256(P4_LIBRARY),
            "p4_index_sha256": sha256(P4_INDEX),
            "control_python_sha256": sha256(Path(__file__)),
            "production_doppler_sha256": sha256(
                ROOT / "crates" / "actinv-data" / "src" / "doppler.rs"
            ),
            "production_processing_sha256": sha256(
                ROOT / "crates" / "actinv-data" / "src" / "processing.rs"
            ),
            "independent_resonance_sha256": sha256(ROOT / "controls" / "resonance.py"),
        },
        "memory_limit_bytes_per_subprocess": ADDRESS_SPACE_BYTES,
        "synthetic_exact_kernel": synthetic,
        "w186_exact_kernel": w186,
        "fr226_ultra_narrow": ultra,
        "density_convergence": density,
        "seeded_p4_regression": regression,
        "performance": performance,
        "pass": passed,
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
