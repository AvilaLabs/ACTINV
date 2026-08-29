#!/usr/bin/env python3
"""P17-G2: broader identical-processed-data depletion controls.

The ALARA leg extracts product-specific records from ALARA's official FENDL-2
sample library.  ACTINV receives the same arrays in its ascending-energy NPZ
layout.  An independent Python ENDF reader builds the dense reference.  OpenMC's
IndependentOperator receives the same collapsed rates, decay branches, initial
vector, and schedule through a generated one-group depletion chain.

All evaluated inputs and generated libraries remain external or temporary.  The
committed result contains hashes and scalar evidence only.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.linalg import expm

import chain as independent_chain
from endf_decay import endf_float, fields
from g1_p17_operators import actinv_step as actinv_cram48_step
from p9_fixtures import BIN, ROOT, command, inventory, relative, sha256, write_json


RESULTS = Path(os.environ.get("ACTINV_P17_RESULTS", ROOT / "results"))
ALARA_SOURCE = Path(
    os.environ.get("ACTINV_ALARA_SOURCE", Path.home() / "nuclear-data" / "alara-2.9.2")
)
ALARA_BUILD = Path(
    os.environ.get(
        "ACTINV_ALARA_BUILD", Path.home() / "nuclear-data" / "alara-2.9.2-build"
    )
)
ALARA_BIN = Path(os.environ.get("ACTINV_ALARA_BIN", ALARA_BUILD / "src" / "alara"))

GROUPS = 175
INITIAL_PER_TARGET = 2.5e19
INITIAL_TOTAL = 1.0e20
REPORTABLE_FRACTION = 1.0e-12
ALARA_RELATIVE_TOLERANCE = 5.0e-4
G1_RELATIVE_TOLERANCE = 5.0e-12
G1_ABSOLUTE_TOLERANCE = 5.0e-14 * INITIAL_TOTAL
SCHEDULE = [
    (6.0 * 3600.0, 1.0),
    (2.0 * 3600.0, 0.0),
    (6.0 * 3600.0, 1.0),
    (2.0 * 3600.0, 0.0),
    (6.0 * 3600.0, 1.0),
    (2.0 * 3600.0, 0.0),
    (6.0 * 3600.0, 1.0),
]

EXPECTED = {
    "alara_commit": "faa5b330460fe865e38fc788f1b792ea33d13d1b",
    "activation_sha256": "f45ced4d5676c993f6b6dd562d5e312e897eabb959dc6ebba56bbeaecde22312",
    "decay_sha256": "810f3b8ca46dd55b965e37b84c9793057a7ee53aa2a194a2fcb1ff0d1b681940",
    "element_sha256": "bdfcfdb255d89b4988be9fab4279c36fb9615709ee6a738e963591db6146c290",
}

# EAF reaction code is MT * 10 + product state.  Arrays are already
# product-specific; EAFLib.C does not apply the descriptive factor printed at
# the end of the header.
REACTION_RECORDS = [
    (11023, 1020, 102, 11024, 0),
    (11023, 1021, 102, 11024, 1),
    (11023, 1030, 103, 10023, 0),
    (26056, 1020, 102, 26057, 0),
    (26056, 1030, 103, 25056, 0),
    (26057, 1020, 102, 26058, 0),
    (27059, 1020, 102, 27060, 0),
    (27059, 1021, 102, 27060, 1),
    (27059, 1030, 103, 26059, 0),
    (27060, 1020, 102, 27061, 0),
    (28058, 1020, 102, 28059, 0),
    (28058, 1030, 103, 27058, 0),
    (28058, 1031, 103, 27058, 1),
    (28059, 1020, 102, 28060, 0),
]
INITIAL_KEYS = [(11023, 0), (26056, 0), (27059, 0), (28058, 0)]
TARGET_KEYS = sorted({(target, 0) for target, *_ in REACTION_RECORDS})
ATOMIC_SYMBOL = [
    "n",
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
]


def checked_paths() -> dict[str, Path]:
    data = ALARA_SOURCE / "sample" / "data"
    paths = {
        "activation": data / "truncated_fendlg-2.0_175_for_samples_only",
        "decay": data / "truncated_fendld-2.0_for_samples_only",
        "element": data / "myElelib",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing pinned P17 ALARA inputs: " + ", ".join(missing))
    actual = {f"{name}_sha256": sha256(path) for name, path in paths.items()}
    expected = {key: EXPECTED[key] for key in actual}
    if actual != expected:
        raise RuntimeError(f"pinned ALARA inputs changed: {actual}")
    if command(["git", "-C", ALARA_SOURCE, "rev-parse", "HEAD"]).stdout.strip() != EXPECTED[
        "alara_commit"
    ]:
        raise RuntimeError("pinned ALARA source commit changed")
    version = command([ALARA_BIN, "-V"]).stdout
    if "ALARA 2.9.2" not in version:
        raise RuntimeError(f"unexpected ALARA version: {version.strip()}")
    return paths


def actinv_name(key: tuple[int, int]) -> str:
    za, state = key
    symbol = ATOMIC_SYMBOL[za // 1000]
    suffix = f"m{state}" if state else ""
    return f"{symbol}{za % 1000}{suffix}"


def alara_name(key: tuple[int, int]) -> str:
    za, state = key
    symbol = ATOMIC_SYMBOL[za // 1000].lower()
    suffix = "m" if state else ""
    return f"{symbol}-{za % 1000}{suffix}"


def openmc_name(key: tuple[int, int]) -> str:
    za, state = key
    symbol = ATOMIC_SYMBOL[za // 1000]
    suffix = f"_m{state}" if state else ""
    return f"{symbol}{za % 1000}{suffix}"


def extract_records(path: Path) -> tuple[list[str], list[dict]]:
    lines = path.read_text().splitlines()
    marker = next(index for index, line in enumerate(lines) if line.startswith("#"))
    wanted = {(target, code) for target, code, *_ in REACTION_RECORDS}
    records = []
    raw = lines[: marker + 1]
    cursor = marker + 1
    while cursor < len(lines):
        fields = lines[cursor].split()
        try:
            target_kza = int(fields[0])
            identity = (target_kza // 10, int(fields[1]))
            count = int(fields[2])
        except (ValueError, IndexError):
            cursor += 1
            continue
        start = cursor
        cursor += 3
        values: list[float] = []
        while len(values) < count:
            if cursor >= len(lines):
                raise RuntimeError(f"truncated EAF record {identity}")
            values.extend(float(value) for value in lines[cursor].split())
            cursor += 1
        if len(values) != count:
            raise RuntimeError(f"EAF record {identity} supplied too many values")
        if target_kza % 10 != 0 or identity not in wanted:
            continue
        padded = values + [values[-1]] * (GROUPS - len(values))
        if len(padded) != GROUPS:
            raise RuntimeError(f"EAF record {identity} cannot be padded to {GROUPS} groups")
        raw.extend(lines[start:cursor])
        records.append(
            {
                "target": identity[0],
                "code": identity[1],
                "declared_groups": count,
                "header": lines[start].rstrip(),
                "raw_lines": lines[start:cursor],
                "sigma_high_to_low": np.asarray(padded, dtype="<f8"),
            }
        )
    found = {(record["target"], record["code"]) for record in records}
    if found != wanted or len(records) != len(wanted):
        raise RuntimeError(f"selected EAF record mismatch: wanted={wanted}, found={found}")
    records.sort(key=lambda record: (record["target"], record["code"]))
    return raw, records


def flux_vector() -> np.ndarray:
    flux = np.zeros(GROUPS, dtype="<f8")
    # EAF order is high-to-low.  Two fast and two thermal-side groups exercise
    # threshold products and capture chains in the same calculation.
    flux[0] = 2.0e15
    flux[1] = 1.0e15
    flux[-2] = 5.0e13
    flux[-1] = 1.0e14
    return flux


def write_flux(path: Path, flux: np.ndarray) -> None:
    path.write_text(
        "\n".join(
            " ".join(f"{value:.17E}" for value in flux[start : start + 6])
            for start in range(0, GROUPS, 6)
        )
        + "\n"
    )


def record_map(records: list[dict]) -> dict[tuple[int, int], dict]:
    return {(record["target"], record["code"]): record for record in records}


def collapsed_rates(records: list[dict], flux: np.ndarray) -> dict[tuple[int, int], float]:
    return {
        (record["target"], record["code"]): float(
            np.dot(record["sigma_high_to_low"], flux) * 1.0e-24
        )
        for record in records
    }


def write_actinv_library(work: Path, records: list[dict]) -> tuple[Path, Path]:
    records_by_id = record_map(records)
    target_index = {key[0]: index for index, key in enumerate(TARGET_KEYS)}
    grouped: dict[tuple[int, int], list[tuple[int, int, np.ndarray]]] = defaultdict(list)
    for target, code, mt, product, state in REACTION_RECORDS:
        grouped[(target, mt)].append(
            (product, state, records_by_id[(target, code)]["sigma_high_to_low"])
        )

    rows: list[list[int]] = []
    sigma: list[np.ndarray] = []
    for (target, mt), products in sorted(grouped.items()):
        total = np.sum([values for _, _, values in products], axis=0)
        rows.append([target_index[target], mt, -1, -1, 0])
        sigma.append(total[::-1])
        for product, state, values in products:
            rows.append([target_index[target], mt, product, state, 3])
            sigma.append(values[::-1])

    library = work / "p17-fendl2-identical.npz"
    np.savez(
        library,
        rows=np.asarray(rows, dtype="<i8"),
        sig=np.asarray(sigma, dtype="<f8"),
        bounds=np.arange(1.0, GROUPS + 2.0, dtype="<f8"),
    )
    index = library.with_name(library.stem + "_index.json")
    write_json(
        index,
        {
            "schema": "actinv-library-index-1",
            "projectile": "neutron",
            "groups": GROUPS,
            "n_rows": len(rows),
            "temperature_K": 293.6,
            "sha256_npz": sha256(library),
            "targets": [
                {
                    "za": za,
                    "liso": state,
                    # AWR is not used for an explicit atoms_per_g composition.
                    "awr": (za % 1000) / 1.00866491595,
                    "ledger": [],
                }
                for za, state in TARGET_KEYS
            ],
        },
    )
    return library, index


def run_alara(
    work: Path,
    paths: dict[str, Path],
    subset: list[str],
    flux_path: Path,
    network: dict,
) -> tuple[dict[str, dict[str, float]], dict]:
    activation = work / "p17-fendl2-identical.eaf"
    activation.write_text("\n".join(subset) + "\n")
    library_stem = work / "p17-fendl2-identical-bin"
    conversion_input = work / "convert-p17"
    conversion_input.write_text(
        f"convert_lib eaflib alaralib {activation.name} {paths['decay']} {library_stem.name}\n"
    )
    conversion = command([ALARA_BIN, conversion_input.name], cwd=work, timeout=300.0)

    # ALARA 2.9.2 documents direct isotope constituents, but the released
    # parser has that branch disabled.  Fabricated element names are its
    # supported exact-isotope route.  Unit reference densities and explicit
    # 100% abundances make the four starting number densities identical.
    element_library = work / "p17-elements"
    additions = []
    for symbol, za in (("na", 11023), ("fe", 26056), ("co", 27059), ("ni", 28058)):
        mass_number = za % 1000
        additions.extend(
            [
                f"{symbol}:p17 {mass_number:.17e} {za // 1000} 1.00000000000000000e+00 1",
                f"    {mass_number} 1.00000000000000000e+02",
            ]
        )
    element_library.write_text(paths["element"].read_text().rstrip() + "\n" + "\n".join(additions) + "\n")

    volume_fraction = 0.25
    density_scales = {
        symbol: INITIAL_PER_TARGET * (za % 1000) / (6.02e23 * volume_fraction)
        for symbol, za in (("na", 11023), ("fe", 26056), ("co", 27059), ("ni", 28058))
    }

    run_input = work / "p17-identical-run"
    run_input.write_text(
        f"""geometry rectangular

volume
    1.0 zone_0
end

mat_loading
    zone_0 mix_0
end

mixture mix_0
    element na:p17 {density_scales['na']:.17e} {volume_fraction:.17e}
    element fe:p17 {density_scales['fe']:.17e} {volume_fraction:.17e}
    element co:p17 {density_scales['co']:.17e} {volume_fraction:.17e}
    element ni:p17 {density_scales['ni']:.17e} {volume_fraction:.17e}
end

element_lib {element_library.name}
data_library alaralib {library_stem.name}
flux flux_1 {flux_path.name} 1 0 default

schedule total
    6 h flux_1 pulsed 0 s
end

pulsehistory pulsed
    4 2 h
end

cooling
    1 s
end

output zone
    number_density
end

dump_file dump_files/p17-identical.dump
truncation 1e-24
"""
    )
    (work / "output").mkdir(exist_ok=True)
    (work / "dump_files").mkdir(exist_ok=True)
    run = command([ALARA_BIN, run_input.name], cwd=work, timeout=300.0)
    transcript = run.stdout + run.stderr
    transcript_path = work / "alara-transcript.txt"
    transcript_path.write_text(transcript)
    lines = transcript.splitlines()
    header = next(
        (
            index
            for index, line in enumerate(lines)
            if "isotope" in line and "pre-irrad" in line and "shutdown" in line
        ),
        None,
    )
    if header is None:
        raise RuntimeError("ALARA did not emit a number-density table")
    table: dict[str, dict[str, float]] = {}
    unlabeled_rows = []
    row_pattern = re.compile(
        r"^\s*([a-z]{1,2}-\d+(?:m)?)\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", re.I
    )
    omitted_symbol_pattern = re.compile(
        r"^\s*-(\d+)(m?)\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", re.I
    )
    for line in lines[header + 1 :]:
        match = row_pattern.match(line)
        if match:
            table[match.group(1).lower()] = {
                "half_life_s": float(match.group(2)),
                "pre_irrad": float(match.group(3)),
                "shutdown": float(match.group(4)),
            }
            continue
        omitted = omitted_symbol_pattern.match(line)
        if omitted:
            unlabeled_rows.append(
                {
                    "mass": int(omitted.group(1)),
                    "state": 1 if omitted.group(2) else 0,
                    "half_life_s": float(omitted.group(3)),
                    "pre_irrad": float(omitted.group(4)),
                    "shutdown": float(omitted.group(5)),
                }
            )
        elif table and not line.strip():
            break

    if unlabeled_rows:
        metadata = network["metadata"]
        unused = set(network["keys"])
        for row in unlabeled_rows:
            candidates = [
                key
                for key in unused
                if key[0] % 1000 == row["mass"] and key[1] == row["state"]
            ]
            scored = []
            for key in candidates:
                record = metadata[key]
                expected_half_life = -1.0 if record["nst"] == 1 else record["half_life"]
                half_life_score = (
                    0.0
                    if expected_half_life < 0.0 and row["half_life_s"] < 0.0
                    else relative(expected_half_life, row["half_life_s"])
                )
                expected_initial = key in INITIAL_KEYS
                observed_initial = row["pre_irrad"] > 0.0
                initial_penalty = 1.0 if expected_initial != observed_initial else 0.0
                scored.append((initial_penalty + half_life_score, key))
            if not scored:
                # ALARA explicitly tracks emitted light particles such as H-1;
                # ACTINV's inventory network intentionally does not.
                continue
            scored.sort()
            best_score, key = scored[0]
            if best_score > 5.0e-4 or (
                len(scored) > 1 and abs(scored[1][0] - best_score) <= 1.0e-12
            ):
                raise RuntimeError(f"ambiguous ALARA row identity: row={row}, candidates={scored}")
            unused.remove(key)
            table[alara_name(key)] = {
                "half_life_s": row["half_life_s"],
                "pre_irrad": row["pre_irrad"],
                "shutdown": row["shutdown"],
            }
    if not table:
        raise RuntimeError("ALARA number-density table was empty")
    for key in INITIAL_KEYS:
        row = table.get(alara_name(key))
        if row is None or relative(row["pre_irrad"], INITIAL_PER_TARGET) > 5.0e-5:
            raise RuntimeError(f"ALARA initial population mismatch for {key}: {row}")
    return table, {
        "activation_subset_sha256": sha256(activation),
        "activation_subset_bytes": activation.stat().st_size,
        "conversion_input_sha256": sha256(conversion_input),
        "generated_element_library_sha256": sha256(element_library),
        "run_input_sha256": sha256(run_input),
        "transcript_sha256": sha256(transcript_path),
        "conversion_returncode": conversion.returncode,
        "run_returncode": run.returncode,
        "four_pulses": "num_pulses_per_level: [4]" in transcript,
        "two_hour_delay": "delay_seconds_per_level: [7200]" in transcript,
        "number_density_rows": len(table),
        "omitted_symbol_rows": len(unlabeled_rows),
        "resolved_symbol_rows": len(table),
    }


def endf_field(value: float | int) -> str:
    if isinstance(value, int):
        return f"{value:11d}"
    encoded = f"{value:11.4E}"
    if len(encoded) != 11:
        raise ValueError(f"cannot encode {value} in an ENDF field")
    return encoded


def endf_record(
    values: list[float | int], material: int, mf: int, mt: int, sequence: int
) -> str:
    return "".join(endf_field(value) for value in values) + f"{material:4d}{mf:2d}{mt:3d}{sequence:5d}"


def write_stable_adapter(work: Path, network: dict) -> tuple[Path, list[tuple[int, int]]]:
    """Represent FENDL's header-only stable records as ordinary MF=8/MT=457."""
    stable_keys = [
        key for key in network["keys"] if network["decay_constants"][key] == 0.0
    ]
    lines = []
    for material, key in enumerate(stable_keys, 800):
        metadata = network["metadata"][key]
        sequence = 1
        lines.append(
            endf_record(
                [float(key[0]), float(metadata["awr"]), 0, key[1], 1, 0],
                material,
                8,
                457,
                sequence,
            )
        )
        sequence += 1
        lines.append(endf_record([0.0, 0.0, 0, 0, 0, 0], material, 8, 457, sequence))
        sequence += 1
        lines.append(endf_record([0.0, 0.0, 0, 0, 0, 0], material, 8, 457, sequence))
        sequence += 1
        lines.append(endf_record([0.0, 0.0, 0, 0, 0, 0], material, 8, 0, sequence))
    path = work / "fendl2-stable-mf8-adapter.endf"
    path.write_text("\n".join(lines) + "\n")
    return path, stable_keys


def run_actinv(
    work: Path,
    paths: dict[str, Path],
    library: Path,
    flux: np.ndarray,
    stable_adapter: Path,
) -> dict:
    specification = {
        "spec": "actinv-spec-1",
        "title": "P17 broader ALARA identical-data network",
        "library": {"path": str(library), "sha256": sha256(library)},
        # ALARA's stable records are header-only.  The generated fallback merely
        # represents those same zero-decay-constant identities as MF=8/MT=457.
        "decay": {"primary": str(paths["decay"]), "fallback": str(stable_adapter)},
        "material": {
            "mass_g": 1.0,
            "basis": "atoms_per_g",
            "composition": {actinv_name(key): INITIAL_PER_TARGET for key in INITIAL_KEYS},
        },
        "spectrum": {
            "structure": "custom",
            "boundaries_eV": np.arange(1.0, GROUPS + 2.0).tolist(),
            "flux_per_group": flux.tolist(),
            "descending": True,
        },
        "schedule": [
            {"dt": f"{duration:.17e} s", "flux": multiplier}
            for duration, multiplier in SCHEDULE
        ],
        "options": {
            "mode": "coupled",
            "prune": "none",
            "bmin_atoms_per_g": 0.0,
            "temperature_K": 293.6,
            "outputs": ["inventory", "activity", "ledger", "certificate"],
        },
        "fission_yields": {"files": [], "energy": "spectrum_average"},
    }
    spec_path = work / "p17-identical.json"
    result_path = work / "p17-identical.result.json"
    write_json(spec_path, specification)
    command(
        [BIN, "run", spec_path, result_path],
        timeout=300.0,
        env={"ACTINV_CACHE_DIR": str(work / "actinv-cache")},
    )
    return json.loads(result_path.read_text())


def stable_decay_records(decay_path: Path) -> dict[tuple[int, int], dict]:
    """Read the special stable-nuclide MF=1 records omitted by MF=8-only parsers."""
    records = {}
    seen_materials = set()
    with decay_path.open(errors="replace") as stream:
        for line in stream:
            if len(line) < 75:
                continue
            try:
                material = int(line[66:70])
                mf = int(line[70:72])
                mt = int(line[72:75])
            except ValueError:
                continue
            if material <= 0 or material in seen_materials or (mf, mt) != (1, 451):
                continue
            seen_materials.add(material)
            values = fields(line)
            za = int(round(endf_float(values[0])))
            if za <= 0:
                continue
            records[(za, 0)] = {
                "mat": material,
                "za": float(za),
                "awr": endf_float(values[1]),
                "lis": 0,
                "liso": 0,
                "nst": 1,
                "nsp": 0,
                "half_life": 0.0,
                "d_half_life": 0.0,
                "energies": [],
                "spin": 0.0,
                "parity": 0.0,
                "ndk": 0,
                "modes": [],
            }
    return records


def reachable_decay_network(decay_path: Path) -> dict:
    parsed_keys, parsed_records, _index, _lambdas, _entries, leakage = (
        independent_chain.build(decay_path)
    )
    metadata = stable_decay_records(decay_path)
    for material in parsed_keys:
        record = parsed_records[material]
        metadata[(int(round(record["za"])), int(record["liso"]))] = record

    decay_constants = {
        key: (
            0.0
            if record["nst"] == 1 or record["half_life"] <= 0.0
            else math.log(2.0) / record["half_life"]
        )
        for key, record in metadata.items()
    }
    decay_edges = []
    for parent, record in metadata.items():
        decay_constant = decay_constants[parent]
        if decay_constant == 0.0:
            continue
        z, mass = divmod(parent[0], 1000)
        for mode in record["modes"]:
            if mode["br"] <= 0.0:
                continue
            daughter = None
            daughter_z, daughter_mass = z, mass
            valid = True
            for digit in independent_chain.rtyp_digits(mode["rtyp"]):
                if digit == 6 or digit not in independent_chain.STEP:
                    valid = False
                    break
                delta_z, delta_mass = independent_chain.STEP[digit]
                daughter_z += delta_z
                daughter_mass += delta_mass
            if valid:
                wanted = (
                    daughter_z * 1000 + daughter_mass,
                    int(round(mode["rfs"])),
                )
                if wanted in metadata:
                    daughter = wanted
                elif (wanted[0], 0) in metadata:
                    daughter = (wanted[0], 0)
            decay_edges.append((parent, daughter, decay_constant * mode["br"]))

    seed_keys = set(INITIAL_KEYS)
    seed_keys.update((product, state) for *_, product, state in REACTION_RECORDS)
    missing_seeds = sorted(seed_keys - metadata.keys())
    if missing_seeds:
        raise RuntimeError(f"selected products absent from FENDL decay data: {missing_seeds}")
    reachable = set(seed_keys)
    changed = True
    while changed:
        changed = False
        for parent, daughter, value in decay_edges:
            if (
                value <= 0.0
                or parent not in reachable
                or daughter is None
                or daughter in reachable
            ):
                continue
            reachable.add(daughter)
            changed = True
    ordered = sorted(reachable)
    local = {key: index for index, key in enumerate(ordered)}
    matrix = np.zeros((len(ordered) + 1, len(ordered) + 1), dtype=float)
    leak_index = len(ordered)
    for parent in ordered:
        decay_constant = decay_constants[parent]
        if decay_constant > 0.0:
            matrix[local[parent], local[parent]] -= decay_constant
    for parent, daughter, value in decay_edges:
        if parent not in local:
            continue
        row = local.get(daughter, leak_index)
        matrix[row, local[parent]] += value
    return {
        "leakage": leakage,
        "local": local,
        "keys": ordered,
        "metadata": {key: metadata[key] for key in ordered},
        "decay_constants": {key: decay_constants[key] for key in ordered},
        "decay_edges": [edge for edge in decay_edges if edge[0] in reachable],
        "decay_matrix": matrix,
        "leak_index": leak_index,
    }


def initial_state(network: dict) -> np.ndarray:
    state = np.zeros(len(network["keys"]) + 1, dtype=float)
    key_index = {key: index for index, key in enumerate(network["keys"])}
    for key in INITIAL_KEYS:
        state[key_index[key]] = INITIAL_PER_TARGET
    return state


def transition_matrix(
    network: dict, rates: dict[tuple[int, int], float], multiplier: float
) -> np.ndarray:
    key_index = {key: index for index, key in enumerate(network["keys"])}
    grouped: dict[tuple[int, int], list[tuple[tuple[int, int], float]]] = defaultdict(list)
    for target, code, mt, product, product_state in REACTION_RECORDS:
        grouped[(target, mt)].append(((product, product_state), rates[(target, code)]))
    matrix = network["decay_matrix"].copy()
    for (target, _mt), products in grouped.items():
        column = key_index[(target, 0)]
        total = sum(rate for _, rate in products) * multiplier
        matrix[column, column] -= total
        for product, rate in products:
            row = key_index.get(product, network["leak_index"])
            matrix[row, column] += rate * multiplier
    return matrix


def dense_solution(network: dict, rates: dict[tuple[int, int], float]) -> np.ndarray:
    initial = initial_state(network)
    matrices = [
        transition_matrix(network, rates, multiplier)[:-1, :-1]
        for _duration, multiplier in SCHEDULE
    ]
    size = len(network["keys"])
    predecessors = [set() for _ in range(size)]
    for matrix in matrices:
        rows, columns = np.nonzero(matrix)
        for row, column in zip(rows, columns):
            if row != column:
                predecessors[row].add(column)

    # Each observable is solved on its exact causal ancestor subspace.  This is
    # algebraically identical to the full block-triangular exponential, while a
    # 20 ms downstream state cannot degrade the scaling used for an upstream
    # long-lived parent.
    final = np.zeros_like(initial)
    for target in range(size):
        ancestors = {target}
        frontier = [target]
        while frontier:
            node = frontier.pop()
            for parent in predecessors[node] - ancestors:
                ancestors.add(parent)
                frontier.append(parent)
        selected = sorted(ancestors)
        target_local = selected.index(target)
        state = initial[selected].copy()
        for (duration, _multiplier), matrix in zip(SCHEDULE, matrices):
            block = matrix[np.ix_(selected, selected)]
            state = expm(block * duration) @ state
        final[target] = state[target_local]
    return final


def actinv_cram48_solution(network: dict, rates: dict[tuple[int, int], float]) -> np.ndarray:
    state = initial_state(network)
    for duration, multiplier in SCHEDULE:
        matrix = transition_matrix(network, rates, multiplier)
        state = actinv_cram48_step(matrix, state, duration)
    return state


def tolerance_row(left: float, right: float) -> dict:
    absolute = abs(left - right)
    rel = relative(left, right)
    return {
        "left": left,
        "right": right,
        "absolute": absolute,
        "relative": rel,
        "within_g1_or_tolerance": bool(
            rel <= G1_RELATIVE_TOLERANCE or absolute <= G1_ABSOLUTE_TOLERANCE
        ),
    }


def run_openmc(
    work: Path,
    network: dict,
    rates: dict[tuple[int, int], float],
    records: list[dict],
    flux: np.ndarray,
) -> tuple[dict[str, float], dict]:
    import openmc
    import openmc.deplete

    chain = openmc.deplete.Chain()
    key_index = {key: index for index, key in enumerate(network["keys"])}
    grouped_products: dict[tuple[int, int], list[tuple[tuple[int, int], float]]] = defaultdict(list)
    for target, code, mt, product, state in REACTION_RECORDS:
        grouped_products[(target, mt)].append(((product, state), rates[(target, code)]))

    for key in network["keys"]:
        nuclide = openmc.deplete.Nuclide(openmc_name(key))
        decay_constant = network["decay_constants"][key]
        if decay_constant > 0.0:
            nuclide.half_life = math.log(2.0) / decay_constant
            for parent, daughter, value in network["decay_edges"]:
                if parent != key or value <= 0.0:
                    continue
                target = openmc_name(daughter) if daughter in key_index else None
                nuclide.add_decay_mode("p17", target, value / decay_constant)
        for (target, mt), products in sorted(grouped_products.items()):
            if key != (target, 0):
                continue
            reaction_name = {102: "(n,gamma)", 103: "(n,p)"}[mt]
            total_rate = sum(rate for _, rate in products)
            for product, rate in products:
                nuclide.add_reaction(
                    reaction_name,
                    openmc_name(product),
                    0.0,
                    rate / total_rate,
                )
        chain.add_nuclide(nuclide)

    openmc_dir = work / "openmc"
    openmc_dir.mkdir()
    chain_path = openmc_dir / "chain.xml"
    chain.export_to_xml(chain_path)

    reaction_names = ["(n,gamma)", "(n,p)"]
    microscopic_targets = [openmc_name(key) for key in TARGET_KEYS]
    micro_data = np.zeros((len(TARGET_KEYS), len(reaction_names), 1), dtype=float)
    total_flux = float(np.sum(flux))
    records_by_id = record_map(records)
    for target_index, (target, _state) in enumerate(TARGET_KEYS):
        for reaction_index, mt in enumerate((102, 103)):
            selected = [
                records_by_id[(record_target, code)]["sigma_high_to_low"]
                for record_target, code, record_mt, _product, _product_state in REACTION_RECORDS
                if record_target == target and record_mt == mt
            ]
            if selected:
                total_sigma = np.sum(selected, axis=0)
                micro_data[target_index, reaction_index, 0] = float(
                    np.dot(total_sigma, flux) / total_flux
                )
    micro_path = openmc_dir / "microxs.npy"
    np.save(micro_path, micro_data)
    micro_xs = openmc.deplete.MicroXS(micro_data, microscopic_targets, reaction_names)
    operator = openmc.deplete.IndependentOperator.from_nuclides(
        volume=1.0,
        nuclides={openmc_name(key): INITIAL_PER_TARGET for key in INITIAL_KEYS},
        flux=np.asarray([1.0]),
        micro_xs=micro_xs,
        chain_file=chain_path,
        nuc_units="atom/cm3",
        normalization_mode="source-rate",
    )
    operator.output_dir = openmc_dir
    integrator = openmc.deplete.PredictorIntegrator(
        operator,
        [duration for duration, _ in SCHEDULE],
        source_rates=[total_flux * multiplier for _, multiplier in SCHEDULE],
        timestep_units="s",
        solver="cram48",
    )
    integrator.integrate(final_step=False, output=False, path="depletion_results.h5")
    depletion_path = openmc_dir / "depletion_results.h5"
    output = openmc.deplete.Results(depletion_path)
    material_id = operator.burnable_mats[0]
    final = {}
    final_time = None
    for key in network["keys"]:
        times, values = output.get_atoms(
            material_id, openmc_name(key), nuc_units="atom/cm3", time_units="s"
        )
        final[actinv_name(key)] = float(values[-1])
        final_time = float(times[-1])

    package = Path(openmc.__file__).resolve().parent
    source_paths = {
        "independent_operator.py": package / "deplete" / "independent_operator.py",
        "microxs.py": package / "deplete" / "microxs.py",
        "chain.py": package / "deplete" / "chain.py",
        "integrators.py": package / "deplete" / "integrators.py",
    }
    return final, {
        "version": openmc.__version__,
        "chain_sha256": sha256(chain_path),
        "chain_nuclides": len(chain.nuclides),
        "chain_reactions": sorted(chain.reactions),
        "microxs_sha256": sha256(micro_path),
        "microxs_shape": list(micro_data.shape),
        "depletion_results_note": "HDF5 container bytes are not canonical; scalar rows are recorded above",
        "final_time_s": final_time,
        "source_hashes": {name: sha256(path) for name, path in source_paths.items()},
    }


def main() -> None:
    root = Path(os.environ.get("ACTINV_P17_WORK", tempfile.mkdtemp(prefix="actinv-p17-g2-")))
    work = root / "g2"
    work.mkdir(parents=True, exist_ok=True)
    paths = checked_paths()
    subset, records = extract_records(paths["activation"])
    network = reachable_decay_network(paths["decay"])
    flux = flux_vector()
    flux_path = work / "p17-flux"
    write_flux(flux_path, flux)
    library, index_path = write_actinv_library(work, records)
    stable_adapter, stable_adapter_keys = write_stable_adapter(work, network)
    alara_table, alara_run = run_alara(work, paths, subset, flux_path, network)
    actinv_result = run_actinv(work, paths, library, flux, stable_adapter)
    actinv_final = inventory(actinv_result["steps"][-1])
    rates = collapsed_rates(records, flux)
    dense_final = dense_solution(network, rates)
    actinv_cram48_final = actinv_cram48_solution(network, rates)
    dense_by_name = {
        actinv_name(key): float(dense_final[index]) for index, key in enumerate(network["keys"])
    }
    actinv_cram48_by_name = {
        actinv_name(key): float(actinv_cram48_final[index])
        for index, key in enumerate(network["keys"])
    }
    openmc_final, openmc_evidence = run_openmc(work, network, rates, records, flux)

    comparisons = {}
    reportable = []
    for key in network["keys"]:
        act_name = actinv_name(key)
        ala_name = alara_name(key)
        act_value = float(actinv_final.get(act_name, 0.0))
        ala_value = float(alara_table.get(ala_name, {}).get("shutdown", 0.0))
        dense_value = dense_by_name[act_name]
        actinv_cram48_value = actinv_cram48_by_name[act_name]
        omc_value = openmc_final[act_name]
        is_reportable = max(
            act_value, ala_value, dense_value, actinv_cram48_value, omc_value
        ) > (
            REPORTABLE_FRACTION * INITIAL_TOTAL
        )
        row = {
            "key": [key[0], key[1]],
            "alara_label": ala_name,
            "actinv": act_value,
            "alara": ala_value,
            "dense": dense_value,
            "actinv_cram48": actinv_cram48_value,
            "openmc": omc_value,
            "reportable": is_reportable,
            "actinv_alara_relative": relative(act_value, ala_value),
            "actinv_dense": tolerance_row(act_value, dense_value),
            "actinv_cram48_dense": tolerance_row(actinv_cram48_value, dense_value),
            "openmc_dense": tolerance_row(omc_value, dense_value),
            "actinv_cram48_openmc": tolerance_row(actinv_cram48_value, omc_value),
        }
        comparisons[act_name] = row
        if is_reportable:
            reportable.append(row)

    rates_rows = []
    for target, code, mt, product, state in REACTION_RECORDS:
        record = record_map(records)[(target, code)]
        alara_rate = float(np.dot(record["sigma_high_to_low"], flux) * 1.0e-24)
        actinv_rate = float(np.dot(record["sigma_high_to_low"][::-1], flux[::-1]) * 1.0e-24)
        rates_rows.append(
            {
                "target": target,
                "eaf_code": code,
                "mt": mt,
                "product": product,
                "product_state": state,
                "declared_groups": record["declared_groups"],
                "record_sha256": __import__("hashlib")
                .sha256(("\n".join(record["raw_lines"]) + "\n").encode())
                .hexdigest(),
                "alara_order_per_s": alara_rate,
                "actinv_order_per_s": actinv_rate,
                "relative": relative(alara_rate, actinv_rate),
            }
        )

    maximum_alara = max(row["actinv_alara_relative"] for row in reportable)
    maximum_rate = max(row["relative"] for row in rates_rows)
    maximum_actinv_cli_dense = max(row["actinv_dense"]["relative"] for row in reportable)
    actinv_cram48_dense_pass = all(
        row["actinv_cram48_dense"]["within_g1_or_tolerance"] for row in reportable
    )
    openmc_dense_pass = all(row["openmc_dense"]["within_g1_or_tolerance"] for row in reportable)
    actinv_cram48_openmc_pass = all(
        row["actinv_cram48_openmc"]["within_g1_or_tolerance"] for row in reportable
    )
    final_step = actinv_result["steps"][-1]
    expected_time = sum(duration for duration, _ in SCHEDULE)
    expected_exposure = sum(duration * multiplier for duration, multiplier in SCHEDULE)
    ledger = actinv_result["ledger"]
    certificate = actinv_result["certificate"]
    result = {
        "schema": "actinv-p17-g2-identical-data-1",
        "protocol_sha256": "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f",
        "provenance": {
            "alara_commit": EXPECTED["alara_commit"],
            "alara_version": command([ALARA_BIN, "-V"]).stdout.strip(),
            "alara_binary_sha256": sha256(ALARA_BIN),
            "official_hashes": {f"{name}_sha256": sha256(path) for name, path in paths.items()},
            "activation_subset_sha256": alara_run["activation_subset_sha256"],
            "actinv_library_sha256": sha256(library),
            "actinv_index_sha256": sha256(index_path),
            "flux_sha256": sha256(flux_path),
            "stable_adapter_sha256": sha256(stable_adapter),
            "stable_adapter_keys": [list(key) for key in stable_adapter_keys],
            "actinv_binary_sha256": sha256(BIN),
        },
        "features": {
            "initial_target_families": [actinv_name(key) for key in INITIAL_KEYS],
            "target_family_count": len(INITIAL_KEYS),
            "selected_product_records": len(REACTION_RECORDS),
            "capture_chain": all(key in network["keys"] for key in [(26057, 0), (26058, 0)]),
            "competing_products": True,
            "isomer_branches": [[11024, 1], [27060, 1], [27058, 1]],
            "radioactive_decay": any(
                network["decay_constants"][key] > 0.0 for key in network["keys"]
            ),
            "reachable_decay_nuclides": len(network["keys"]),
            "reportable_shutdown_nuclides": len(reportable),
        },
        "schedule": {
            "segments": len(SCHEDULE),
            "pulses": sum(multiplier > 0.0 for _, multiplier in SCHEDULE),
            "gaps": sum(multiplier == 0.0 for _, multiplier in SCHEDULE),
            "expected_time_s": expected_time,
            "actinv_time_s": final_step["t_s"],
            "expected_flux_weighted_time_s": expected_exposure,
            "actinv_flux_weighted_time_s": final_step["flux_weighted_time_s"],
            "total_flux_n_cm2_s": float(np.sum(flux)),
        },
        "alara_run": alara_run,
        "rates": rates_rows,
        "maximum_rate_relative": maximum_rate,
        "comparisons": comparisons,
        "reportable_names": [actinv_name(tuple(row["key"])) for row in reportable],
        "maximum_actinv_alara_relative_reportable": maximum_alara,
        "maximum_actinv_cli_dense_relative_reportable": maximum_actinv_cli_dense,
        "actinv_cram48_dense_pass": actinv_cram48_dense_pass,
        "openmc_dense_pass": openmc_dense_pass,
        "actinv_cram48_openmc_pass": actinv_cram48_openmc_pass,
        "openmc": openmc_evidence,
        "actinv": {
            "mode": actinv_result["mode"],
            "pruned_states": actinv_result["pruned_states"],
            "total_states": actinv_result["total_states"],
            "decay_nuclides_from_fallback": ledger["decay_nuclides_from_fallback"],
            "products_no_evaluated_decay_data": ledger["products_no_evaluated_decay_data"],
            "products_unmapped_to_leakage": ledger["products_unmapped_to_leakage"],
            "certificate": {
                "solver": certificate["solver"],
                "mode": certificate["mode"],
                "prune": certificate["prune"],
                "material_basis": certificate["material_basis"],
                "library_sha256": certificate["inputs"]["library"]["sha256"],
                "library_index_sha256": certificate["inputs"]["library_index"]["sha256"],
                "decay_primary_sha256": certificate["inputs"]["decay_primary"]["sha256"],
                "decay_fallback_sha256": certificate["inputs"]["decay_fallback"]["sha256"],
            },
        },
    }
    result["pass"] = bool(
        result["provenance"]["official_hashes"]
        == {key: EXPECTED[key] for key in result["provenance"]["official_hashes"]}
        and result["features"]["target_family_count"] >= 4
        and result["features"]["selected_product_records"] == 14
        and result["features"]["capture_chain"]
        and result["features"]["competing_products"]
        and len(result["features"]["isomer_branches"]) >= 1
        and result["features"]["radioactive_decay"]
        and result["features"]["reportable_shutdown_nuclides"] >= 12
        and alara_run["conversion_returncode"] == 0
        and alara_run["run_returncode"] == 0
        and alara_run["four_pulses"]
        and alara_run["two_hour_delay"]
        and maximum_rate <= 1.0e-12
        and maximum_alara <= ALARA_RELATIVE_TOLERANCE
        and maximum_actinv_cli_dense <= ALARA_RELATIVE_TOLERANCE
        and actinv_cram48_dense_pass
        and openmc_dense_pass
        and actinv_cram48_openmc_pass
        and relative(final_step["t_s"], expected_time) <= 1.0e-12
        and relative(final_step["flux_weighted_time_s"], expected_exposure) <= 1.0e-12
        and openmc_evidence["version"] == "0.15.3"
        and relative(openmc_evidence["final_time_s"], expected_time) <= 1.0e-12
        and ledger["decay_nuclides_from_fallback"] == len(stable_adapter_keys)
        and not ledger["products_no_evaluated_decay_data"]
        and not ledger["products_unmapped_to_leakage"]
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g2_p17_identical_data.json", result)
    print(
        json.dumps(
            {
                "reportable_shutdown_nuclides": len(reportable),
                "maximum_rate_relative": maximum_rate,
                "maximum_actinv_alara_relative_reportable": maximum_alara,
                "maximum_actinv_cli_dense_relative_reportable": maximum_actinv_cli_dense,
                "actinv_cram48_dense_pass": actinv_cram48_dense_pass,
                "openmc_dense_pass": openmc_dense_pass,
                "actinv_cram48_openmc_pass": actinv_cram48_openmc_pass,
                "pass": result["pass"],
            },
            indent=1,
        )
    )
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
