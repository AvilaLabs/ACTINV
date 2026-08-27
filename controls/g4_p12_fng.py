#!/usr/bin/env python3
"""P12-G4: reproduce the published FNG/ITER campaign-1 cell-620 history.

The Zenodo inputs stay external.  This control verifies their pinned hashes, derives a
temporary one-group ACTINV library and decay file, runs all 170 published intervals, and
compares the four histories selected by Peterson et al.  The generated files are discarded.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import h5py
import numpy as np

from harness.elements import SYM_OF, Z_OF


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g4_p12_fng.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P12_PROTOCOL.md"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
ARCHIVE = Path(
    os.environ.get("ACTINV_P12_FNG_ARCHIVE", "/tmp/actinv-p12-research_data.tar.xz")
)
ADDRESS_SPACE_BYTES = 4 * 1024**3
RELATIVE_LIMIT = 1.0e-4
LOW_POPULATION_ATOMS = 1.0e6
SCALED_ABSOLUTE_LIMIT = 1.0e-18
SELECTED_NUCLIDES = ("Co58", "Tc99_m1", "Mn56", "Cr51")

EXPECTED_HASHES = {
    "protocol": "247e669691d99a5e548734528a069bb49962e6ae356ba14f962abcf2826ed715",
    "archive": "1c76f42dcbc3e0f488f8035c3f63e4cd4428930f76efc088329be7ec9c6b45ed",
    "microxs_620.csv": "fa097a994e8a4ea93267603bd6435972c15d3daa1d89cb37b626e21147637651",
    "depletion_results.h5": "1fcd608a0a8100892b4d24ca7de05d401ab952b904ac3d80c8698de36419d4d5",
    "flux_620.npy": "9f2b3223164adbe5709aa493943af0a1fde3b538654ec28993b32dfe56195828",
    "inventory.i": "c2fdfc04547017823c533e5a48199c5bd49cfb33fe36fb7a984a88c30c20516b",
    "fluxes": "25bc8b50a74147f4cc4637a24e2c6d0d8b24562447abb28e7ba699bc03390fde",
    "chain_endfb80_reduced.xml": "f3f56d3a9ee66bcb691ea0812aad6a3696c00f6272f503de866a495b85c7270e",
}

# ENDF reaction numbers are provenance labels here.  ACTINV consumes the explicitly mapped
# product ZA, while the emitted (mass, charge) tuple independently determines that product.
REACTIONS = {
    "(n,2a)": (108, 8, 4),
    "(n,2n)": (16, 2, 0),
    "(n,2na)": (24, 6, 2),
    "(n,2np)": (41, 3, 1),
    "(n,2p)": (111, 2, 2),
    "(n,3He)": (106, 3, 2),
    "(n,3n)": (17, 3, 0),
    "(n,3na)": (25, 7, 2),
    "(n,a)": (107, 4, 2),
    "(n,d)": (104, 2, 1),
    "(n,gamma)": (102, 0, 0),
    "(n,na)": (22, 5, 2),
    "(n,nd)": (32, 3, 1),
    "(n,np)": (28, 2, 1),
    "(n,nt)": (33, 4, 1),
    "(n,p)": (103, 1, 1),
    "(n,pa)": (112, 5, 3),
    "(n,pd)": (115, 3, 2),
    "(n,t)": (105, 3, 1),
    "(n,t2a)": (113, 11, 5),
}

DECAY_TYPES = {
    "beta-": 1.0,
    "ec/beta+": 2.0,
    "IT": 3.0,
    "alpha": 4.0,
    "beta-,n": 1.5,
    "beta-,alpha": 1.4,
    "beta-,beta-": 1.1,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def locate_source() -> Path:
    configured = os.environ.get("ACTINV_P12_FNG_ROOT")
    if configured:
        root = Path(configured)
    else:
        candidates = sorted(Path("/tmp").glob("actinv-p12-fng.*"))
        if len(candidates) != 1:
            raise FileNotFoundError(
                "set ACTINV_P12_FNG_ROOT to the extracted Zenodo research-data directory"
            )
        root = candidates[0]
    if not root.is_dir():
        raise FileNotFoundError(f"P12 FNG source directory does not exist: {root}")
    return root


def verified_sources(source: Path) -> tuple[Path, dict[str, str]]:
    cell = source / "activation_and_transport_validation" / "cell_620"
    paths = {
        "protocol": PROTOCOL,
        "archive": ARCHIVE,
        "chain_endfb80_reduced.xml": source / "chain_endfb80_reduced.xml",
        **{
            name: cell / name
            for name in (
                "microxs_620.csv",
                "depletion_results.h5",
                "flux_620.npy",
                "inventory.i",
                "fluxes",
            )
        },
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing P12 G4 input(s): " + ", ".join(missing))
    actual = {name: sha256(path) for name, path in paths.items()}
    mismatch = {
        name: {"expected": EXPECTED_HASHES[name], "actual": digest}
        for name, digest in actual.items()
        if digest != EXPECTED_HASHES[name]
    }
    if mismatch:
        raise RuntimeError(f"P12 G4 pinned input mismatch: {json.dumps(mismatch, sort_keys=True)}")
    return cell, actual


def parse_name(name: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"([A-Z][a-z]?)([0-9]+)(?:_m([0-9]+))?", name)
    if not match or match.group(1) not in Z_OF:
        raise ValueError(f"invalid chain nuclide name {name!r}")
    return Z_OF[match.group(1)], int(match.group(2)), int(match.group(3) or 0)


def ground_name(z: int, mass: int) -> str | None:
    symbol = SYM_OF.get(z)
    return None if symbol is None or mass <= 0 else f"{symbol}{mass}"


def field(value: float | int) -> str:
    if isinstance(value, int):
        text = f"{value:11d}"
        if len(text) != 11:
            raise ValueError(f"integer does not fit an ENDF field: {value}")
        return text
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"nonfinite ENDF value {value}")
    if value == 0.0:
        return " 0.000000+0"
    exponent = math.floor(math.log10(abs(value)))
    mantissa = abs(value) / 10.0**exponent
    exponent_digits = len(str(abs(exponent)))
    decimals = 7 - exponent_digits
    if decimals < 0:
        raise ValueError(f"ENDF exponent is too large: {value}")
    mantissa = round(mantissa, decimals)
    if mantissa >= 10.0:
        mantissa /= 10.0
        exponent += 1
        exponent_digits = len(str(abs(exponent)))
        decimals = 7 - exponent_digits
    sign = "-" if value < 0.0 else " "
    text = f"{sign}{mantissa:.{decimals}f}{exponent:+d}"
    if len(text) != 11:
        raise ValueError(f"cannot encode {value} as an ENDF field: {text!r}")
    return text


def record(values: list[float | int], mat: int, mf: int, mt: int, sequence: int) -> str:
    if len(values) != 6:
        raise ValueError("an ENDF record needs six values")
    return "".join(field(value) for value in values) + f"{mat:4d}{mf:2d}{mt:3d}{sequence:5d}"


def write_decay(path: Path, chain: Path) -> dict[str, int]:
    root = ET.parse(chain).getroot()
    if root.tag != "depletion_chain":
        raise ValueError(f"unexpected chain root {root.tag!r}")
    nuclides = sorted(root.findall("nuclide"), key=lambda node: parse_name(node.attrib["name"]))
    known = {node.attrib["name"] for node in nuclides}
    lines: list[str] = []
    mode_count = 0
    stable_count = 0
    for material, node in enumerate(nuclides, 100):
        name = node.attrib["name"]
        z, mass, liso = parse_name(name)
        modes = node.findall("decay")
        stable = "half_life" not in node.attrib
        if stable != (len(modes) == 0):
            raise ValueError(f"inconsistent half-life/decay records for {name}")
        stable_count += int(stable)
        half_life = 0.0 if stable else float(node.attrib["half_life"])
        if not stable and (not math.isfinite(half_life) or half_life <= 0.0):
            raise ValueError(f"invalid half-life for {name}")
        sequence = 1
        lines.append(
            record([z * 1000 + mass, mass, 0, liso, int(stable), 0], material, 8, 457, sequence)
        )
        sequence += 1
        lines.append(record([half_life, 0.0, 0, 0, 0, 0], material, 8, 457, sequence))
        sequence += 1
        values: list[float | int] = []
        for mode in modes:
            kind = mode.attrib.get("type")
            if kind not in DECAY_TYPES:
                raise ValueError(f"unsupported chain decay type {kind!r} for {name}")
            target = mode.attrib.get("target")
            if target is not None and target not in known:
                raise ValueError(f"chain daughter {target!r} for {name} is absent")
            # OpenMC deliberately omits a target when the daughter is outside the reduced
            # chain.  ACTINV reaches the same leakage state through conservation when that
            # daughter is absent; ground state is therefore the correct RFS declaration.
            target_liso = 0 if target is None else parse_name(target)[2]
            branch = float(mode.attrib["branching_ratio"])
            if not math.isfinite(branch) or branch <= 0.0 or branch > 1.0:
                raise ValueError(f"invalid branching ratio for {name}")
            values.extend([DECAY_TYPES[kind], float(target_liso), 0.0, 0.0, branch, 0.0])
            mode_count += 1
        if modes and abs(math.fsum(float(mode.attrib["branching_ratio"]) for mode in modes) - 1.0) > 1e-12:
            raise ValueError(f"decay branches do not close for {name}")
        lines.append(record([0.0, 0.0, 0, 0, len(values), len(modes)], material, 8, 457, sequence))
        sequence += 1
        for start in range(0, len(values), 6):
            lines.append(record(values[start : start + 6], material, 8, 457, sequence))
            sequence += 1
        lines.append(record([0.0] * 6, material, 8, 0, sequence))
    path.write_text("\n".join(lines) + "\n")
    return {
        "nuclides": len(nuclides),
        "stable_nuclides": stable_count,
        "decay_modes": mode_count,
    }


def npz_member(array: np.ndarray) -> bytes:
    output = io.BytesIO()
    np.lib.format.write_array(output, np.asarray(array), allow_pickle=False)
    return output.getvalue()


def deterministic_npz(path: Path, arrays: list[tuple[str, np.ndarray]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, array in arrays:
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, npz_member(array), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)


def boundary_hash(boundaries: np.ndarray) -> str:
    digest = hashlib.sha256(b"ACTINV-GROUP-BOUNDARIES-v1\0")
    for value in boundaries:
        digest.update(struct.pack("<d", float(value)))
    return digest.hexdigest()


def write_library(path: Path, index_path: Path, csv_path: Path, chain_path: Path) -> dict:
    chain = ET.parse(chain_path).getroot()
    chain_names = {node.attrib["name"] for node in chain.findall("nuclide")}
    with csv_path.open(newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    nonzero = [row for row in source_rows if float(row["xs"]) != 0.0]
    fission = [row for row in nonzero if row["reactions"] == "fission"]
    non_fission = [row for row in nonzero if row["reactions"] != "fission"]
    labels = {row["reactions"] for row in non_fission}
    if labels != set(REACTIONS):
        raise ValueError(f"cell-620 reaction labels differ: {sorted(labels)}")
    targets = sorted(
        {row["nuclides"] for row in nonzero},
        key=lambda name: (*parse_name(name), name),
    )
    target_index = {name: index for index, name in enumerate(targets)}
    rows: list[list[int]] = []
    sigma: list[list[float]] = []
    product_rows = 0
    loss_only = 0
    for source_row in nonzero:
        target = source_row["nuclides"]
        reaction = source_row["reactions"]
        value = float(source_row["xs"])
        if reaction == "fission":
            # The published reduced chain contains none of these 84 actinide targets and
            # supplies no fission-yield mapping.  Retain every cross section as a loss-only
            # MT=18 row; ACTINV will report an absent target rather than inventing products.
            rows.append([target_index[target], 18, -1, -1, 0])
            sigma.append([value])
            loss_only += 1
            continue
        mt, emitted_mass, emitted_charge = REACTIONS[reaction]
        rows.append([target_index[target], mt, -1, -1, 0])
        sigma.append([value])
        z, mass, _ = parse_name(target)
        product = ground_name(z - emitted_charge, mass + 1 - emitted_mass)
        if product in chain_names:
            product_z, product_mass, _ = parse_name(product)
            rows.append([target_index[target], mt, product_z * 1000 + product_mass, 0, 0])
            sigma.append([value])
            product_rows += 1
        else:
            loss_only += 1
    bounds = np.asarray([1.0, 2.0], dtype=np.float64)
    deterministic_npz(
        path,
        [
            ("rows", np.asarray(rows, dtype=np.int64)),
            ("sig", np.asarray(sigma, dtype=np.float64)),
            ("bounds", bounds),
        ],
    )
    digest = sha256(path)
    source_digest = sha256(csv_path)
    index = {
        "schema": "actinv-library-index-1",
        "projectile": "neutron",
        "groups": "custom",
        "group_boundary_sha256": boundary_hash(bounds),
        "temperature_K": 293.6,
        "sha256_npz": digest,
        "source": {
            "citation": "Peterson et al., Nuclear Fusion 64 (2024) 056011",
            "doi": "10.1088/1741-4326/ad32dd",
            "archive_doi": "10.5281/zenodo.10660030",
            "cell": 620,
            "microxs_sha256": source_digest,
            "derivation": "temporary one-group table from every nonzero published microscopic cross section; fission is loss-only without supplied yields",
        },
        "targets": [
            {
                "file": "microxs_620.csv",
                "source_sha256": source_digest,
                "mat": index + 1,
                "za": parse_name(name)[0] * 1000 + parse_name(name)[1],
                "liso": parse_name(name)[2],
                "ledger": ["P12-G4 temporary published cell-620 one-group input"],
            }
            for index, name in enumerate(targets)
        ],
    }
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    return {
        "csv_rows": len(source_rows),
        "nonzero_rows": len(nonzero),
        "nonzero_fission_loss_rows": len(fission),
        "nonzero_non_fission_rows": len(non_fission),
        "reaction_labels": sorted(labels),
        "reaction_label_count": len(labels),
        "conservation_mapped_labels": len(labels - {"(n,gamma)"}),
        "targets": len(targets),
        "targets_in_reduced_chain": sum(name in chain_names for name in targets),
        "loss_rows": len(nonzero),
        "product_rows": product_rows,
        "loss_only_channels": loss_only,
        "library_rows": len(rows),
        "library_sha256": digest,
        "index_sha256": sha256(index_path),
    }


def read_inventory(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    in_fuel = False
    for line in path.read_text().splitlines():
        if line.strip().startswith("FUEL "):
            in_fuel = True
            continue
        if in_fuel and line.strip() == "ATOMS":
            break
        if in_fuel:
            fields = line.split()
            if len(fields) == 2:
                values[fields[0]] = float(fields[1])
    if not values:
        raise ValueError("published inventory contains no nuclides")
    return values


def command(arguments: list[str | Path], timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    def limit_address_space() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))

    completed = subprocess.run(
        [str(value) for value in arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        preexec_fn=limit_address_space,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(map(str, arguments))}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def normalized_result(value: dict, work: Path) -> dict:
    def scrub(item):
        if isinstance(item, dict):
            return {key: scrub(child) for key, child in item.items() if key != "ms"}
        if isinstance(item, list):
            return [scrub(child) for child in item]
        if isinstance(item, str):
            return item.replace(str(work), "<WORK>")
        return item

    return scrub(value)


def main() -> None:
    source = locate_source()
    cell, hashes = verified_sources(source)
    chain_path = source / "chain_endfb80_reduced.xml"
    flux = np.load(cell / "flux_620.npy", allow_pickle=False)
    if flux.shape != (709,) or not np.isfinite(flux).all() or np.any(flux < 0.0):
        raise ValueError("published cell-620 flux has an invalid shape or value")
    flux_sum = float(np.sum(flux, dtype=np.float64))
    inventory_input = read_inventory(cell / "inventory.i")

    with h5py.File(cell / "depletion_results.h5", "r") as reference:
        if reference["number"].shape != (171, 1, 1, 753):
            raise ValueError(f"unexpected reference number shape {reference['number'].shape}")
        time_bounds = np.asarray(reference["time"][:, :], dtype=np.float64)
        source_rates = np.asarray(reference["source_rate"][:, 0], dtype=np.float64)
        if time_bounds.shape != (171, 2) or source_rates.shape != (171,):
            raise ValueError("unexpected published schedule shape")
        if not np.array_equal(time_bounds[:-1, 1], time_bounds[1:, 0]):
            raise ValueError("published interval endpoints are not contiguous")
        if time_bounds[-1, 0] != time_bounds[-1, 1]:
            raise ValueError("published final duplicate endpoint is absent")
        durations = time_bounds[:170, 1] - time_bounds[:170, 0]
        if np.any(durations <= 0.0) or np.any(source_rates[:170] < 0.0):
            raise ValueError("published schedule contains an invalid interval")
        number = np.asarray(reference["number"][:, 0, 0, :], dtype=np.float64)
        reference_initial = {
            name: float(number[0, int(reference[f"nuclides/{name}"].attrs["atom number index"])])
            for name in inventory_input
        }
        inventory_maximum_relative = max(
            abs(reference_initial[name] - value) / value
            for name, value in inventory_input.items()
        )
        if inventory_maximum_relative > 5.0e-15:
            raise ValueError(
                "published inventory.i and HDF5 initial atom counts differ by "
                f"{inventory_maximum_relative:.17e} relative"
            )
        # The text input is rounded to 15 significant figures.  Use the published result's
        # binary64 initial vector after independently confirming that it represents that input.
        inventory = reference_initial
        initial_total = math.fsum(inventory.values())
        volume = float(reference["materials/1"].attrs["volume"])
        if not math.isfinite(volume) or volume <= 0.0:
            raise ValueError("published cell volume is invalid")

        rate_checks = []
        reaction_rates = reference["reaction rates"]
        with (cell / "microxs_620.csv").open(newline="") as stream:
            microscopic = {
                (row["nuclides"], row["reactions"]): float(row["xs"])
                for row in csv.DictReader(stream)
            }
        for nuclide, reaction in (
            ("Fe56", "(n,gamma)"),
            ("Ni58", "(n,p)"),
            ("Cr52", "(n,2n)"),
        ):
            nuclide_index = int(reference[f"nuclides/{nuclide}"].attrs["reaction rate index"])
            reaction_index = int(reference[f"reactions/{reaction}"].attrs["index"])
            sigma_b = microscopic[(nuclide, reaction)]
            for interval in np.flatnonzero(source_rates[:170] > 0.0):
                expected = sigma_b * flux_sum * source_rates[interval] / volume * 1.0e-24
                actual = float(reaction_rates[interval, 0, 0, nuclide_index, reaction_index])
                relative = float(
                    abs(actual - expected)
                    / max(abs(actual), abs(expected), float.fromhex("0x1p-1022"))
                )
                rate_checks.append(relative)
        maximum_rate_relative = float(max(rate_checks, default=0.0))
        if maximum_rate_relative > 5.0e-15:
            raise AssertionError(f"independent reaction-rate check failed: {maximum_rate_relative}")

        with tempfile.TemporaryDirectory(prefix="actinv-p12-g4-") as directory:
            work = Path(directory)
            decay = work / "cell-620-decay.endf"
            decay_counts = write_decay(decay, chain_path)
            repeated_decay = work / "cell-620-decay-repeat.endf"
            repeated_decay_counts = write_decay(repeated_decay, chain_path)
            decay_reproducible = (
                decay_counts == repeated_decay_counts
                and decay.read_bytes() == repeated_decay.read_bytes()
            )
            if not decay_reproducible:
                raise AssertionError("temporary decay transformation is not reproducible")
            library_a = work / "cell-620-a.npz"
            index_a = work / "cell-620-a_index.json"
            transformation = write_library(library_a, index_a, cell / "microxs_620.csv", chain_path)
            library_b = work / "cell-620-b.npz"
            index_b = work / "cell-620-b_index.json"
            repeated = write_library(library_b, index_b, cell / "microxs_620.csv", chain_path)
            library_reproducible = library_a.read_bytes() == library_b.read_bytes()
            if not library_reproducible or transformation["library_sha256"] != repeated["library_sha256"]:
                raise AssertionError("temporary activation library is not reproducible")

            cell_flux = flux_sum * source_rates[:170] / volume
            specification = {
                "spec": "actinv-spec-1",
                "title": "P12 G4 Peterson et al. FNG/ITER campaign-1 cell 620",
                "library": {"path": str(library_a), "sha256": sha256(library_a)},
                "decay": {"primary": str(decay)},
                "material": {
                    "mass_g": 1.0,
                    "basis": "atoms_per_g",
                    "composition": inventory,
                },
                "spectrum": {
                    "structure": "custom",
                    "boundaries_eV": [1.0, 2.0],
                    "flux_per_group": [1.0],
                    "total": 1.0,
                    "descending": False,
                },
                "schedule": [
                    {"dt": f"{duration:.17e} s", "flux": float(multiplier)}
                    for duration, multiplier in zip(durations, cell_flux, strict=True)
                ],
                "options": {
                    "mode": "coupled",
                    "prune": "none",
                    "bmin_atoms_per_g": 0.0,
                    "temperature_K": 293.6,
                    "cram_order": 48,
                    "outputs": ["inventory", "ledger", "certificate"],
                },
            }
            spec_path = work / "cell-620.json"
            spec_path.write_text(json.dumps(specification, indent=2, sort_keys=True) + "\n")
            output_a = work / "result-a.json"
            output_b = work / "result-b.json"
            command([ACTINV, "run", spec_path, output_a])
            command([ACTINV, "run", spec_path, output_b])
            result_a = json.loads(output_a.read_text())
            result_b = json.loads(output_b.read_text())
            scientific_a = json.dumps(
                normalized_result(result_a, work), sort_keys=True, separators=(",", ":")
            )
            scientific_b = json.dumps(
                normalized_result(result_b, work), sort_keys=True, separators=(",", ":")
            )
            result_reproducible = scientific_a == scientific_b
            if not result_reproducible:
                raise AssertionError("repeated ACTINV result differs outside elapsed time")
            if len(result_a["steps"]) != 170:
                raise AssertionError(f"ACTINV returned {len(result_a['steps'])} steps, expected 170")

            comparisons = {}
            all_histories_pass = True
            for nuclide in SELECTED_NUCLIDES:
                reference_index = int(
                    reference[f"nuclides/{nuclide}"].attrs["atom number index"]
                )
                expected = number[1:, reference_index]
                actual = np.asarray(
                    [
                        next(
                            (
                                row["atoms_per_g"]
                                for row in step["inventory"]
                                if row["nuclide"] == nuclide.replace("_m", "m")
                            ),
                            0.0,
                        )
                        for step in result_a["steps"]
                    ],
                    dtype=np.float64,
                )
                high = expected >= LOW_POPULATION_ATOMS
                relative = np.zeros_like(expected)
                relative[high] = np.abs(actual[high] - expected[high]) / expected[high]
                scaled_absolute = np.zeros_like(expected)
                scaled_absolute[~high] = np.abs(actual[~high] - expected[~high]) / initial_total
                high_max = float(np.max(relative[high], initial=0.0))
                low_max = float(np.max(scaled_absolute[~high], initial=0.0))
                passed = bool(
                    high_max <= RELATIVE_LIMIT and low_max <= SCALED_ABSOLUTE_LIMIT
                )
                all_histories_pass &= passed
                comparisons[nuclide] = {
                    "endpoints": len(expected),
                    "endpoints_at_or_above_1e6_atoms": int(np.count_nonzero(high)),
                    "endpoints_below_1e6_atoms": int(np.count_nonzero(~high)),
                    "maximum_relative_error_at_or_above_1e6_atoms": high_max,
                    "maximum_scaled_absolute_error_below_1e6_atoms": low_max,
                    "pass": passed,
                }

            tracked = set(
                subprocess.run(
                    ["git", "ls-files"],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    check=True,
                ).stdout.splitlines()
            )
            forbidden_names = {
                "research_data.tar.xz",
                "microxs_620.csv",
                "depletion_results.h5",
                "flux_620.npy",
                "chain_endfb80_reduced.xml",
            }
            external_data_untracked = not any(Path(name).name in forbidden_names for name in tracked)
            output = {
                "schema": "actinv-p12-g4-result-1",
                "gate": "P12-G4",
                "reference": {
                    "article": "Peterson et al., Nuclear Fusion 64 (2024) 056011",
                    "article_doi": "10.1088/1741-4326/ad32dd",
                    "archive": "Zenodo record 10660030, CC-BY-4.0",
                    "archive_doi": "10.5281/zenodo.10660030",
                    "hashes": hashes,
                    "cell": 620,
                    "volume_cm3": volume,
                    "flux_groups": len(flux),
                    "flux_sum_per_source_particle": flux_sum,
                    "initial_nuclides": len(inventory),
                    "initial_total_atoms": initial_total,
                    "inventory_i_maximum_relative_rounding": inventory_maximum_relative,
                    "intervals": len(durations),
                    "final_time_s": float(time_bounds[169, 1]),
                },
                "transformation": {
                    **decay_counts,
                    **transformation,
                    "decay_sha256": sha256(decay),
                },
                "independent_reaction_rates": {
                    "formula": "sigma_b * sum(flux_620) * source_rate / volume * 1e-24",
                    "selected_pairs": ["Fe56 (n,gamma)", "Ni58 (n,p)", "Cr52 (n,2n)"],
                    "comparisons": len(rate_checks),
                    "maximum_relative_error": maximum_rate_relative,
                    "pass": maximum_rate_relative <= 5.0e-15,
                },
                "history_comparison": {
                    "relative_limit_at_or_above_1e6_atoms": RELATIVE_LIMIT,
                    "scaled_absolute_limit_below_1e6_atoms": SCALED_ABSOLUTE_LIMIT,
                    "nuclides": comparisons,
                    "pass": all_histories_pass,
                },
                "reproducibility": {
                    "temporary_library_bytes_identical": library_reproducible,
                    "temporary_decay_bytes_identical": decay_reproducible,
                    "normalized_result_sha256": hashlib.sha256(scientific_a.encode()).hexdigest(),
                    "repeated_scientific_result_identical": result_reproducible,
                    "pass": decay_reproducible and library_reproducible and result_reproducible,
                },
                "external_data_untracked": external_data_untracked,
            }
            output["pass"] = all(
                (
                    output["independent_reaction_rates"]["pass"],
                    output["history_comparison"]["pass"],
                    output["reproducibility"]["pass"],
                    external_data_untracked,
                    len(durations) == 170,
                    transformation["reaction_label_count"] == 20,
                    transformation["conservation_mapped_labels"] == 19,
                )
            )
            RESULT.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
            print(json.dumps(output, indent=2, sort_keys=True))
            if not output["pass"]:
                raise SystemExit(1)


if __name__ == "__main__":
    main()
