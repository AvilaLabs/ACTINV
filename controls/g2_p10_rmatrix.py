#!/usr/bin/env python3
"""P10-G2: W-186 R-matrix-limited structure and capture against NJOY ACE."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))

from endf_common import endf_float, fields, read_list, read_tab1, sections  # noqa: E402

RESULT = ROOT / "results" / "g2_p10_rmatrix.json"
ENDF = Path(
    os.environ.get(
        "ACTINV_P10_W186_ENDF",
        "/home/connoravila/nuclear-data/fendl-3.2c/endf/n_7443_74-W-186.endf",
    )
)
ACE = Path(
    os.environ.get(
        "ACTINV_P10_W186_ACE",
        "/home/connoravila/nuclear-data/fendl-3.2c/ace/74W_186",
    )
)
GROUP = Path(
    os.environ.get(
        "ACTINV_P10_W186_GROUP",
        "/home/connoravila/nuclear-data/fendl-3.2c/group/74W_186.g",
    )
)
NJOY_DECK = Path(
    os.environ.get(
        "ACTINV_P10_W186_NJOY_DECK",
        "/home/connoravila/nuclear-data/fendl-3.2c/njoy/74W_186.nji",
    )
)
NJOY_OUTPUT = Path(
    os.environ.get(
        "ACTINV_P10_W186_NJOY_OUTPUT",
        "/home/connoravila/nuclear-data/fendl-3.2c/njoy/74W_186.out",
    )
)
EBINS_709 = Path(
    os.environ.get("ACTINV_P10_EBINS_709", "/tmp/actinv-p10-ebins/ebins_709")
)
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target" / "release" / "dump"))
ADDRESS_SPACE_BYTES = 2 * 1024**3
RML_LOW_EV = 1.0e-5
RML_HIGH_EV = 1.0e4
GROUP_TOLERANCE = 2.0e-3
INTEGRAL_TOLERANCE = 5.0e-4

EXPECTED_HASHES = {
    ENDF: "bf6bf3bb7a1583be49ae8aab865e75d256e0965f969f38a14d63260b3f4a8744",
    ACE: "b11e052d8379b010a6f3dd6d67ae6a2153666bfaa759c17503ad51b919f6d5a4",
    GROUP: "022eb861b7ebdfec0b5a47fb448889f544f66a2886c6ff4de1891c06980828f0",
    NJOY_DECK: "be073dcd636ecc4422f5b42310f6bec9db299568d3f8621e176638a7a06413b6",
    NJOY_OUTPUT: "ea9a7838b1e3e33f68708e939617f937b08fe72ead4a774bd66a1f4e2522dca0",
    EBINS_709: "31bc68b8b042cf5bddd211508bf3a6315b56d31fdf688263f57f124e972840c4",
    ROOT / "protocols" / "ACTINV-P10_PROTOCOL.md": "74273ec549d113b24367341d1f94f57d0070795d6e679b84a1921d64dbc85b27",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_A.md": "e7fb61dc755f02675c92c57d2f13f6872a6087e24165b0b3fd128dc86df140fd",
    ROOT / "protocols" / "ACTINV-P10_AMENDMENT_B.md": "36fe887080b03af2851c00a92ebcd5fe93fa4f4bded69c37415ead2626f8cc23",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def limit_address_space() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))


def run_limited(
    arguments: list[str | Path], *, ok: bool = True
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(value) for value in arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=limit_address_space,
        check=False,
    )
    if ok and completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(map(str, arguments))}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    if not ok and completed.returncode == 0:
        raise RuntimeError(f"command unexpectedly succeeded: {' '.join(map(str, arguments))}")
    return completed


def integer(value: str) -> int:
    return int(value) if value.strip() else 0


def cont(line: str) -> tuple[float, float, int, int, int, int]:
    value = fields(line)
    return (
        endf_float(value[0]),
        endf_float(value[1]),
        integer(value[2]),
        integer(value[3]),
        integer(value[4]),
        integer(value[5]),
    )


def extension(lines: list[str], index: int) -> tuple[dict, int]:
    _, _, channel, law, _, _ = cont(lines[index])
    index += 1
    real_points = 0
    imaginary_points = 0
    parameter_count = 0
    if law == 1:
        real, index = read_tab1(lines, index)
        imaginary, index = read_tab1(lines, index)
        real_points = real[5]
        imaginary_points = imaginary[5]
    elif law in (2, 3):
        parameter, index = read_list(lines, index)
        parameter_count = parameter[4]
    elif law != 0:
        raise ValueError(f"independent parser cannot consume RML extension law {law}")
    return {
        "channel": channel,
        "law": law,
        "real_points": real_points,
        "imaginary_points": imaginary_points,
        "parameter_count": parameter_count,
    }, index


def independent_structure(path: Path) -> tuple[dict, dict]:
    body = next(lines for (_, mf, mt), lines in sections(path) if (mf, mt) == (2, 151))
    za, awr, _, _, isotope_count, _ = cont(body[0])
    index = 1
    found = []
    metadata = {"za": int(za), "awr": awr, "isotope_count": isotope_count}
    for isotope_index in range(isotope_count):
        zai, abundance, _, lfw, range_count, _ = cont(body[index])
        index += 1
        metadata.update(
            {
                "zai": int(zai),
                "abundance": abundance,
                "fission_widths": lfw,
                "range_count": range_count,
            }
        )
        for range_index in range(range_count):
            low, high, lru, lrf, nro, naps = cont(body[index])
            index += 1
            if nro:
                _, index = read_tab1(body, index)
            if (lru, lrf) != (1, 7):
                if found:
                    return found[0], metadata
                raise ValueError("W-186 RML range was not first in MF=2/MT=151")

            _, _, ifg, krm, spin_group_count, _ = cont(body[index])
            index += 1
            pair_record, index = read_list(body, index)
            pair_count = pair_record[2]
            if pair_record[4] != 12 * pair_count or pair_record[5] != 2 * pair_count:
                raise ValueError("independent RML particle-pair counts disagree")
            pairs = []
            for values in np.asarray(pair_record[6]).reshape(pair_count, 12):
                pairs.append(
                    {
                        "mass_a": float(values[0]),
                        "mass_b": float(values[1]),
                        "za": int(round(values[2])),
                        "zb": int(round(values[3])),
                        "spin_a": float(values[4]),
                        "spin_b": float(values[5]),
                        "q_value": float(values[6]),
                        "penetrability": int(round(values[7])),
                        "shift": int(round(values[8])),
                        "mt": int(round(values[9])),
                        "parity_a": int(round(values[10])),
                        "parity_b": int(round(values[11])),
                    }
                )

            spin_groups = []
            for _ in range(spin_group_count):
                channel_record, index = read_list(body, index)
                spin, parity, backgrounds, phase_shifts, npl, channel_count, values = (
                    channel_record
                )
                if npl != 6 * channel_count:
                    raise ValueError("independent RML channel count disagrees")
                channels = []
                for value in np.asarray(values).reshape(channel_count, 6):
                    channels.append(
                        {
                            "pair": int(round(value[0])) - 1,
                            "l": int(round(value[1])),
                            "spin": float(value[2]),
                            "boundary": float(value[3]),
                            "effective_radius": float(value[4]),
                            "true_radius": float(value[5]),
                        }
                    )

                resonance_record, index = read_list(body, index)
                resonance_count = resonance_record[3]
                if resonance_record[5] != resonance_count:
                    raise ValueError("independent RML resonance count disagrees")
                values_per_resonance = 6 * math.ceil((channel_count + 1) / 6)
                if resonance_record[4] != values_per_resonance * resonance_count:
                    raise ValueError("independent RML resonance LIST shape disagrees")
                resonances = []
                for value in np.asarray(resonance_record[6]).reshape(
                    resonance_count, values_per_resonance
                ):
                    resonances.append(
                        {
                            "energy": float(value[0]),
                            "widths": [float(item) for item in value[1 : channel_count + 1]],
                        }
                    )
                background_values = []
                for _ in range(backgrounds):
                    value, index = extension(body, index)
                    background_values.append(value)
                phase_values = []
                for _ in range(phase_shifts):
                    value, index = extension(body, index)
                    phase_values.append(value)
                spin_groups.append(
                    {
                        "spin": spin,
                        "parity": parity,
                        "declared_channels": channel_count,
                        "declared_resonances": resonance_count,
                        "declared_backgrounds": backgrounds,
                        "declared_phase_shifts": phase_shifts,
                        "channels": channels,
                        "resonances": resonances,
                        "backgrounds": background_values,
                        "phase_shifts": phase_values,
                    }
                )
            found.append(
                {
                    "range": {
                        "isotope_index": isotope_index,
                        "range_index": range_index,
                        "energy_min_eV": low,
                        "energy_max_eV": high,
                        "lru": lru,
                        "lrf": lrf,
                        "naps": naps,
                        "nro": nro,
                        "ifg": int(ifg == 1),
                        "krm": krm,
                        "declared_particle_pairs": pair_count,
                        "declared_spin_groups": spin_group_count,
                    },
                    "particle_pairs": pairs,
                    "spin_groups": spin_groups,
                }
            )
    if len(found) != 1:
        raise ValueError(f"expected one RML range, found {len(found)}")
    return found[0], metadata


def rust_structure(path: Path) -> dict:
    completed = run_limited([DUMP, "resonance-rml", path])
    output = None
    ranges = None
    for line in completed.stdout.splitlines():
        value = line.split()
        if value[0] == "M":
            if output is not None:
                raise ValueError("Rust dump returned multiple RML ranges")
            output = {
                "range": {
                    "isotope_index": int(value[1]),
                    "range_index": int(value[2]),
                    "energy_min_eV": float(value[3]),
                    "energy_max_eV": float(value[4]),
                    "lru": int(value[5]),
                    "lrf": int(value[6]),
                    "naps": int(value[7]),
                    "nro": int(value[8]),
                    "ifg": int(value[9]),
                    "krm": int(value[10]),
                    "declared_particle_pairs": int(value[11]),
                    "declared_spin_groups": int(value[12]),
                },
                "particle_pairs": [],
                "spin_groups": [],
            }
        elif value[0] == "P":
            output["particle_pairs"].append(
                {
                    "mass_a": float(value[4]),
                    "mass_b": float(value[5]),
                    "za": int(value[6]),
                    "zb": int(value[7]),
                    "spin_a": float(value[8]),
                    "spin_b": float(value[9]),
                    "q_value": float(value[10]),
                    "penetrability": int(value[11]),
                    "shift": int(value[12]),
                    "mt": int(value[13]),
                    "parity_a": int(value[14]),
                    "parity_b": int(value[15]),
                }
            )
        elif value[0] == "G":
            output["spin_groups"].append(
                {
                    "spin": float(value[4]),
                    "parity": float(value[5]),
                    "declared_channels": int(value[6]),
                    "declared_resonances": int(value[7]),
                    "declared_backgrounds": int(value[8]),
                    "declared_phase_shifts": int(value[9]),
                    "channels": [],
                    "resonances": [],
                    "backgrounds": [],
                    "phase_shifts": [],
                }
            )
        elif value[0] == "C":
            group = output["spin_groups"][int(value[3])]
            group["channels"].append(
                {
                    "pair": int(value[5]),
                    "l": int(value[6]),
                    "spin": float(value[7]),
                    "boundary": float(value[8]),
                    "effective_radius": float(value[9]),
                    "true_radius": float(value[10]),
                }
            )
        elif value[0] == "V":
            group = output["spin_groups"][int(value[3])]
            width_count = int(value[6])
            group["resonances"].append(
                {
                    "energy": float(value[5]),
                    "widths": [float(item) for item in value[7 : 7 + width_count]],
                }
            )
        elif value[0] in ("B", "S"):
            group = output["spin_groups"][int(value[3])]
            key = "backgrounds" if value[0] == "B" else "phase_shifts"
            group[key].append(
                {
                    "channel": int(value[5]),
                    "law": int(value[6]),
                    "real_points": int(value[7]),
                    "imaginary_points": int(value[8]),
                    "parameter_count": int(value[9]),
                }
            )
        elif value[0] == "N":
            ranges = int(value[1])
    if output is None or ranges != 1:
        raise ValueError(f"Rust RML dump is incomplete: ranges={ranges}")
    return output


def source_decimal_structure(value):
    if isinstance(value, dict):
        return {key: source_decimal_structure(item) for key, item in value.items()}
    if isinstance(value, list):
        return [source_decimal_structure(item) for item in value]
    if isinstance(value, float):
        # W-186's ENDF fields carry at most ten significant decimal digits. Parsing the
        # exponent-less form via a synthesized `e` or a mantissa/power product can differ
        # by one binary ULP while representing the same source field.
        return format(value, ".10g")
    return value


def compare_structures(independent: dict, rust: dict) -> dict:
    fields_compared = 0
    float_fields = 0
    binary_representation_differences = 0
    maximum_ulp_distance = 0.0
    mismatches = []

    def visit(left, right, path: str) -> None:
        nonlocal fields_compared, float_fields
        nonlocal binary_representation_differences, maximum_ulp_distance
        if isinstance(left, dict) and isinstance(right, dict):
            if set(left) != set(right):
                mismatches.append(f"{path}: keys differ")
                return
            for key in left:
                visit(left[key], right[key], f"{path}/{key}")
            return
        if isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                mismatches.append(f"{path}: lengths {len(left)}/{len(right)}")
                return
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                visit(left_item, right_item, f"{path}/{index}")
            return
        fields_compared += 1
        if isinstance(left, float) and isinstance(right, float):
            float_fields += 1
            if left != right:
                binary_representation_differences += 1
                ulp = abs(left - right) / max(math.ulp(left), math.ulp(right))
                maximum_ulp_distance = max(maximum_ulp_distance, ulp)
            if format(left, ".10g") != format(right, ".10g"):
                mismatches.append(f"{path}: {left!r}/{right!r}")
        elif left != right:
            mismatches.append(f"{path}: {left!r}/{right!r}")

    visit(independent, rust, "")
    return {
        "fields_compared": fields_compared,
        "float_fields": float_fields,
        "binary_representation_differences": binary_representation_differences,
        "maximum_ulp_distance": maximum_ulp_distance,
        "source_decimal_mismatches": len(mismatches),
        "mismatch_examples": mismatches[:10],
        "exact_at_endf_source_precision": not mismatches,
    }


def structure_sha256(value: dict) -> str:
    encoded = json.dumps(
        source_decimal_structure(value), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def official_boundaries() -> np.ndarray:
    values = []
    for token in EBINS_709.read_text().replace(",", " ").split()[3:]:
        try:
            values.append(float(token))
        except ValueError:
            continue
    result = np.asarray(values)
    if len(result) != 710:
        raise ValueError(f"official ebins_709 has {len(result)} boundaries")
    if result[0] > result[-1]:
        result = result[::-1]
    return result


def lethargy_integral(
    energy: np.ndarray, sigma: np.ndarray, low: float, high: float
) -> float:
    total = 0.0
    first = max(0, int(np.searchsorted(energy, low, side="right")) - 1)
    last = min(len(energy) - 1, int(np.searchsorted(energy, high, side="left")) + 1)
    for index in range(first, last):
        x1, x2 = energy[index], energy[index + 1]
        a, b = max(low, x1), min(high, x2)
        if b <= a or x2 <= x1:
            continue
        slope = (sigma[index + 1] - sigma[index]) / (x2 - x1)
        value_a = sigma[index] + slope * (a - x1)
        log_ratio = math.log(b / a)
        total += value_a * log_ratio + slope * ((b - a) - a * log_ratio)
    return total


def replace_integer(line: str, field: int, value: int) -> str:
    return line[: field * 11] + f"{value:11d}" + line[(field + 1) * 11 :]


def extension_record(tail: str) -> str:
    return f"{0.0:11.4E}" * 2 + f"{0:11d}" * 4 + tail


def planted_evaluation(destination: Path, plant: str) -> None:
    all_lines = ENDF.read_text().splitlines()
    positions = []
    for index, line in enumerate(all_lines):
        if len(line) < 75:
            continue
        try:
            mf, mt = int(line[70:72]), int(line[72:75])
        except ValueError:
            continue
        if (mf, mt) == (2, 151):
            positions.append(index)
    body = [all_lines[index] for index in positions]
    if plant == "reduced-widths":
        body[3] = replace_integer(body[3], 2, 1)
    elif plant == "unsupported-krm":
        body[3] = replace_integer(body[3], 3, 4)
    elif plant == "missing-capture-channel":
        body[6] = replace_integer(body[6], 3, 103)
    elif plant == "background-extension":
        body[9] = replace_integer(body[9], 2, 1)
        resonance_head = 12
        resonance_npl = integer(fields(body[resonance_head])[4])
        insert_at = resonance_head + 1 + math.ceil(resonance_npl / 6)
        body.insert(insert_at, extension_record(body[insert_at][66:]))
    else:
        raise ValueError(f"unknown RML plant {plant}")
    all_lines[positions[0] : positions[-1] + 1] = body
    destination.write_text("\n".join(all_lines) + "\n")


def rejected_plant(work: Path, plant: str, needle: str) -> dict:
    source = work / f"{plant}.endf"
    output = work / f"{plant}.npz"
    index = work / f"{plant}_index.json"
    planted_evaluation(source, plant)
    completed = run_limited(
        [
            ACTINV,
            "build-library",
            source,
            output,
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
        ],
        ok=False,
    )
    message = completed.stdout + completed.stderr
    if needle not in message:
        raise AssertionError(f"{plant}: expected {needle!r}, got {message!r}")
    if output.exists() or index.exists():
        raise AssertionError(f"{plant}: failed build published an output/index pair")
    return {
        "input_sha256": sha256(source),
        "context": needle,
        "returncode_nonzero": True,
        "final_pair_absent": True,
    }


def main() -> int:
    for path, expected in EXPECTED_HASHES.items():
        if not path.exists():
            raise SystemExit(f"missing P10-G2 input: {path}")
        if sha256(path) != expected:
            raise SystemExit(f"P10-G2 hash mismatch: {path}")
    for path in (ACTINV, DUMP):
        if not path.exists():
            raise SystemExit(f"missing P10-G2 binary: {path}")

    independent, metadata = independent_structure(ENDF)
    rust = rust_structure(ENDF)
    structure_comparison = compare_structures(independent, rust)
    structure_exact = structure_comparison["exact_at_endf_source_precision"]
    if not structure_exact:
        raise AssertionError("independent and Rust RML structures differ")

    import openmc
    import openmc.data

    ace = openmc.data.IncidentNeutron.from_ace(ACE)
    temperature = ace.temperatures[0]
    energy = np.asarray(ace.energy[temperature], dtype=float)
    sigma = np.asarray(ace.reactions[102].xs[temperature](energy), dtype=float)
    if ace.name != "W186" or temperature != "294K":
        raise AssertionError(f"unexpected ACE identity {ace.name}/{temperature}")

    official = official_boundaries()
    vendored = np.asarray(
        json.loads((ROOT / "data" / "fispact_709_groups.json").read_text())["boundaries_eV"],
        dtype=float,
    )
    if vendored[0] > vendored[-1]:
        vendored = vendored[::-1]
    if not np.array_equal(official, vendored):
        raise AssertionError("vendored CCFE-709 boundaries differ from official ebins_709")

    with tempfile.TemporaryDirectory(prefix="actinv-p10-g2-", dir="/tmp") as temporary:
        work = Path(temporary)
        library = work / "w186.npz"
        run_limited(
            [
                ACTINV,
                "build-library",
                ENDF,
                library,
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
                "--grid-density",
                "1",
            ]
        )
        index_path = work / "w186_index.json"
        with np.load(library) as built:
            rows = np.asarray(built["rows"])
            actual = np.asarray(built["sig"])
            boundaries = np.asarray(built["bounds"])
        matches = np.flatnonzero(
            (rows[:, 0] == 0)
            & (rows[:, 1] == 102)
            & (rows[:, 2] == -1)
            & (rows[:, 3] == -1)
            & (rows[:, 4] == 0)
        )
        if len(matches) != 1:
            raise AssertionError(f"expected one W-186 MT102 loss row, found {len(matches)}")
        actual = actual[matches[0]]
        if not np.array_equal(boundaries, official):
            raise AssertionError("built library boundaries differ from official CCFE-709")

        scored = np.flatnonzero(
            (boundaries[:-1] < RML_HIGH_EV) & (boundaries[1:] > RML_LOW_EV)
        )
        reference = np.asarray(
            [
                lethargy_integral(
                    energy,
                    sigma,
                    max(boundaries[group], RML_LOW_EV),
                    min(boundaries[group + 1], RML_HIGH_EV),
                )
                / math.log(boundaries[group + 1] / boundaries[group])
                for group in scored
            ]
        )
        retained = reference >= 1.0e-6
        compared_groups = scored[retained]
        relative = np.abs(actual[compared_groups] - reference[retained]) / reference[retained]
        worst_offset = int(np.argmax(relative))
        worst_group = int(compared_groups[worst_offset])
        maximum_group_relative = float(relative[worst_offset])

        reference_integral = lethargy_integral(
            energy, sigma, RML_LOW_EV, RML_HIGH_EV
        )
        actual_integral = math.fsum(
            actual[group]
            * math.log(boundaries[group + 1] / boundaries[group])
            for group in scored
        )
        integral_relative = abs(actual_integral - reference_integral) / reference_integral
        index = json.loads(index_path.read_text())
        generated = {
            "library_sha256": sha256(library),
            "index_sha256": sha256(index_path),
            "rows": int(len(rows)),
            "target_ledger": index["targets"][0]["ledger"],
            "convergence_flags": sum(
                "not converged" in value.lower() for value in index["targets"][0]["ledger"]
            ),
        }

        rejections = {
            "reduced_widths": rejected_plant(
                work, "reduced-widths", "reduced-width amplitudes"
            ),
            "unsupported_krm": rejected_plant(
                work, "unsupported-krm", "unsupported RML KRM=4"
            ),
            "missing_capture_channel": rejected_plant(
                work,
                "missing-capture-channel",
                "needs exactly one eliminated MT=102 channel",
            ),
            "background_extension": rejected_plant(
                work,
                "background-extension",
                "background or tabulated phase-shift extension is not implemented",
            ),
        }

    numerical_pass = bool(
        maximum_group_relative <= GROUP_TOLERANCE
        and integral_relative <= INTEGRAL_TOLERANCE
    )
    output = {
        "gate": "P10-G2",
        "address_space_limit_bytes": ADDRESS_SPACE_BYTES,
        "input_hashes": {path.name: sha256(path) for path in EXPECTED_HASHES},
        "binary_sha256": sha256(ACTINV),
        "dump_sha256": sha256(DUMP),
        "control_sha256": sha256(Path(__file__)),
        "openmc_version": openmc.__version__,
        "ace": {
            "name": ace.name,
            "temperature": temperature,
            "kT_eV": float(ace.kTs[0]),
            "atomic_weight_ratio": float(ace.atomic_weight_ratio),
            "energy_points": int(len(energy)),
            "energy_min_eV": float(energy[0]),
            "energy_max_eV": float(energy[-1]),
        },
        "independent_metadata": metadata,
        "structure": {
            "exact_match": structure_exact,
            "comparison": structure_comparison,
            "independent_sha256": structure_sha256(independent),
            "rust_sha256": structure_sha256(rust),
            "particle_pairs": len(independent["particle_pairs"]),
            "spin_groups": len(independent["spin_groups"]),
            "channels": sum(len(group["channels"]) for group in independent["spin_groups"]),
            "resonances": sum(
                len(group["resonances"]) for group in independent["spin_groups"]
            ),
            "resonances_by_spin_group": [
                len(group["resonances"]) for group in independent["spin_groups"]
            ],
            "backgrounds": sum(
                len(group["backgrounds"]) for group in independent["spin_groups"]
            ),
            "phase_shifts": sum(
                len(group["phase_shifts"]) for group in independent["spin_groups"]
            ),
            "range": independent["range"],
        },
        "ccfe_709": {
            "official_matches_vendored_and_built_exactly": True,
            "groups_overlapping_rml": int(len(scored)),
            "groups_compared_at_or_above_1e-6_b": int(len(compared_groups)),
            "maximum_relative": maximum_group_relative,
            "tolerance": GROUP_TOLERANCE,
            "worst_group": worst_group,
            "worst_group_bounds_eV": [
                float(boundaries[worst_group]),
                float(boundaries[worst_group + 1]),
            ],
            "worst_rust_b": float(actual[worst_group]),
            "worst_ace_b": float(reference[retained][worst_offset]),
        },
        "flat_lethargy_integral": {
            "range_eV": [RML_LOW_EV, RML_HIGH_EV],
            "rust_b_lethargy": actual_integral,
            "ace_b_lethargy": reference_integral,
            "relative": integral_relative,
            "tolerance": INTEGRAL_TOLERANCE,
        },
        "generated": generated,
        "rejections": rejections,
        "pass": bool(
            structure_exact
            and numerical_pass
            and generated["convergence_flags"] == 0
            and all(value["final_pair_absent"] for value in rejections.values())
        ),
    }
    RESULT.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output, indent=2, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
