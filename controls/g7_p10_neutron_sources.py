#!/usr/bin/env python3
"""P10-G7 controls for the bounded TENDL-2025 neutron source repairs."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, localcontext
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile


os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from resonance import parse_mf2  # noqa: E402


OFFICIAL = Path(
    os.environ.get(
        "ACTINV_P10_TENDL_N_OFFICIAL",
        "~/nuclear-data/tendl-2025/files/n",
    )
).expanduser()
WORKING = Path(
    os.environ.get(
        "ACTINV_P10_TENDL_N_WORKING",
        "~/nuclear-data/tendl-2025/files/n-working",
    )
).expanduser()
OFFICIAL_MANIFEST = Path(
    os.environ.get(
        "ACTINV_P10_TENDL_N_MANIFEST",
        "~/nuclear-data/tendl-2025/staging/TENDL-n.manifest.json",
    )
).expanduser()
WORKING_MANIFEST = Path(
    os.environ.get(
        "ACTINV_P10_TENDL_N_WORKING_MANIFEST",
        "~/nuclear-data/tendl-2025/staging/TENDL-n-working.manifest.json",
    )
).expanduser()
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target/release/dump"))
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
PREPARE = ROOT / "scripts" / "prepare_tendl_2025_neutron_working.py"
RESULT = ROOT / "results" / "g7_p10_neutron_sources.json"

EXPECTED = {
    "official_manifest": "b578ab395c6c71d7727dfb0513e88effd96692862664a6279802638531239b67",
    "official_file_manifest": "f38df7c49da6cef8ac3d23c45c81dfb394829eefd38ee4af0db6dde92f0beaa4",
    "working_manifest": "a6d17f996153d2671c0c51bfb6303e2a87a5af03e0696bfb34d668a31dbfb2a2",
    "working_file_manifest": "b1ea3fe043ec243e2df0a3894206872c2ce18c3b4541c19b35029b3ed3e7b15c",
    "prepare": "84a65826e87876bd9bc891bc34897412daf0eae63c092bb88a4b8f654d532190",
    "amendment_d": "5cd79e5ad00ee618b91ddb1b73e795b0cfa4de93c7ebb34c0bce33245e0e5971",
    "amendment_e": "31313e5fb09bd4e969b4cc552beebb7997208197114ceb2b362eabae4de1ffa8",
    "pb208_official": "32249bf71ee52a159ef8f94a4cb85d5c456aba13e1a4c4d9129c2304b6dc4137",
    "pb208_working": "86788a14563ecdb844628a6a455864874ba5f5bca9b8142c10a59ea00df87c72",
    "njoy_reconr": "054ede7a59e1c39cf3e72105d8a0b95a0fb1d8df0882eca6b949e765b62bf5db",
}
EXPECTED_WIDTH_FILES = {
    "n-Bi220.tendl": 100,
    "n-Fr231.tendl": 99,
    "n-Ra226.tendl": 66,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def run(arguments: list[os.PathLike[str] | str], *, ok: bool) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        [str(value) for value in arguments],
        text=True,
        stdout=subprocess.PIPE if not ok else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=300,
    )
    if (completed.returncode == 0) != ok:
        raise RuntimeError(
            f"unexpected status {completed.returncode} for {arguments}: "
            f"{completed.stderr[-1000:]}"
        )
    return completed


def failure_message(stderr: str, substitutions: dict[str, str]) -> str:
    stable = stderr
    for source, replacement in substitutions.items():
        stable = stable.replace(source, replacement)
    return next(
        (line.strip() for line in stable.splitlines() if "parse activation evaluation:" in line),
        stable.strip().splitlines()[-1],
    )


def scan_widths() -> dict[str, object]:
    records = 0
    below = Counter()
    exact_omitted = Counter()
    above_without_lrx = Counter()
    fission_widths = set()
    for path in sorted(OFFICIAL.glob("*.tendl"), key=lambda value: value.name.encode()):
        parsed = parse_mf2(path)
        if parsed is None:
            continue
        for isotope in parsed["isotopes"]:
            for energy_range in isotope["ranges"]:
                if energy_range.get("LRU") != 1 or energy_range.get("LRF") not in (1, 2):
                    continue
                for group in energy_range.get("L", []):
                    for total, neutron, capture, fission in zip(
                        group["GT"], group["GN"], group["GG"], group["GF"]
                    ):
                        records += 1
                        components = neutron + capture + fission
                        tolerance = 5e-6 * max(
                            abs(total), abs(components), sys.float_info.min
                        )
                        if total + tolerance < components:
                            below[path.name] += 1
                            without_fission = neutron + capture
                            no_fission_tolerance = 5e-6 * max(
                                abs(total), abs(without_fission), sys.float_info.min
                            )
                            if (
                                group["LRX"] == 0
                                and fission > 0.0
                                and abs(total - without_fission) <= no_fission_tolerance
                            ):
                                exact_omitted[path.name] += 1
                                fission_widths.add(float(fission))
                        if group["LRX"] == 0 and total > components + tolerance:
                            above_without_lrx[path.name] += 1
    return {
        "records": records,
        "below_component_sum": dict(sorted(below.items())),
        "exact_omitted_fission_pattern": dict(sorted(exact_omitted.items())),
        "above_component_sum_without_lrx": dict(sorted(above_without_lrx.items())),
        "omitted_fission_widths_eV": sorted(fission_widths),
        "pass": records == 267_559
        and dict(below) == EXPECTED_WIDTH_FILES
        and dict(exact_omitted) == EXPECTED_WIDTH_FILES
        and not above_without_lrx
        and fission_widths == {1e-5},
    }


def scan_nonfinite_fields() -> list[dict[str, object]]:
    found = []
    tokens = {b"nan", b"+nan", b"-nan", b"inf", b"+inf", b"-inf", b"infinity"}
    for path in sorted(OFFICIAL.glob("*.tendl"), key=lambda value: value.name.encode()):
        with path.open("rb") as stream:
            for line_number, raw in enumerate(stream, 1):
                line = raw.rstrip(b"\r\n")
                if len(line) < 75:
                    continue
                try:
                    mat = int(line[66:70])
                    mf = int(line[70:72])
                    mt = int(line[72:75])
                except ValueError:
                    continue
                for field in range(6):
                    token = line[field * 11 : (field + 1) * 11].strip().lower()
                    if token in tokens:
                        found.append(
                            {
                                "file": path.name,
                                "line": line_number,
                                "field": field + 1,
                                "token": token.decode(),
                                "mat": mat,
                                "mf": mf,
                                "mt": mt,
                            }
                        )
    return found


def verify_working_manifest() -> dict[str, object]:
    manifest = json.loads(WORKING_MANIFEST.read_text())
    mismatches = []
    changed = []
    for entry in manifest["files"]:
        actual = sha256(WORKING / entry["name"])
        if actual != entry["working_sha256"]:
            mismatches.append(entry["name"])
        if not entry["byte_identical"]:
            changed.append(entry)
    return {
        "detailed_sha256": sha256(WORKING_MANIFEST),
        "file_manifest_sha256": manifest["working_file_manifest_sha256"],
        "preparation_program_sha256": manifest["preparation_program_sha256"],
        "files": len(manifest["files"]),
        "byte_identical_files": manifest["byte_identical_files"],
        "changed": changed,
        "actual_hash_mismatches": mismatches,
        "repairs": manifest["repairs"],
        "pass": sha256(WORKING_MANIFEST) == EXPECTED["working_manifest"]
        and manifest["working_file_manifest_sha256"]
        == EXPECTED["working_file_manifest"]
        and manifest["preparation_program_sha256"] == EXPECTED["prepare"]
        and len(manifest["files"]) == 2_850
        and manifest["byte_identical_files"] == 2_849
        and len(changed) == 1
        and changed[0]["name"] == "n-Pb208.tendl"
        and changed[0]["official_sha256"] == EXPECTED["pb208_official"]
        and changed[0]["working_sha256"] == EXPECTED["pb208_working"]
        and not mismatches
        and len(manifest["repairs"]) == 2,
    }


def strict_parser_and_repair_control(prepare_module) -> dict[str, object]:
    accepted_width_files = []
    for name in EXPECTED_WIDTH_FILES:
        run([DUMP, "activation-json", OFFICIAL / name], ok=True)
        accepted_width_files.append(name)
    raw_failure = run(
        [DUMP, "activation-json", OFFICIAL / "n-Pb208.tendl"], ok=False
    ).stderr
    run([DUMP, "activation-json", WORKING / "n-Pb208.tendl"], ok=True)

    bi = (OFFICIAL / "n-Bi220.tendl").read_bytes()
    below = bi.replace(b" 1.489947-2", b" 1.389947-2", 1)
    above = bi.replace(b" 1.489947-2", b" 1.589947-2", 1)
    if below == bi or above == bi:
        raise AssertionError("could not create width rejection plants")
    with tempfile.TemporaryDirectory(prefix="actinv-p10-width-plants-") as raw:
        directory = Path(raw)
        below_path = directory / "n-Bi220-below.tendl"
        above_path = directory / "n-Bi220-above.tendl"
        below_path.write_bytes(below)
        above_path.write_bytes(above)
        below_failure = run([DUMP, "activation-json", below_path], ok=False).stderr
        above_failure = run([DUMP, "activation-json", above_path], ok=False).stderr

    official_pb = (OFFICIAL / "n-Pb208.tendl").read_bytes()
    repaired, repairs = prepare_module.repair_pb208(official_pb)
    repaired_again, repairs_again = prepare_module.repair_pb208(official_pb)
    return {
        "accepted_omitted_fission_files": accepted_width_files,
        "raw_pb208_failure": failure_message(
            raw_failure,
            {str(OFFICIAL / "n-Pb208.tendl"): "n-Pb208.tendl"},
        ),
        "unrelated_below_failure": failure_message(
            below_failure, {str(below_path): "<below-width-plant>"}
        ),
        "unrelated_above_failure": failure_message(
            above_failure, {str(above_path): "<above-width-plant>"}
        ),
        "repair_is_deterministic": repaired == repaired_again
        and repairs == repairs_again,
        "repair_matches_working_file": repaired
        == (WORKING / "n-Pb208.tendl").read_bytes(),
        "pass": "nonfinite ENDF number 'NaN'" in raw_failure
        and "below component sum" in below_failure
        and "exceeds component sum" in above_failure
        and repaired == repaired_again
        and repairs == repairs_again
        and repaired == (WORKING / "n-Pb208.tendl").read_bytes(),
    }


def pb_aggregate_invariance(prepare_module) -> dict[str, object]:
    official = (OFFICIAL / "n-Pb208.tendl").read_bytes()
    carry, _ = prepare_module.repair_pb208(official)
    right = official.replace(
        b"        NaN8237 3  1", b" 3.224674+08237 3  1"
    ).replace(b"        NaN8237 3  3", b" 7.415722-48237 3  3")
    if right.count(b"        NaN") != 0 or len(right) != len(official):
        raise AssertionError("could not create alternate finite Pb-208 repair")
    with tempfile.TemporaryDirectory(prefix="actinv-p10-pb208-invariance-") as raw:
        directory = Path(raw)
        groups = directory / "groups.json"
        groups.write_text(
            json.dumps(
                {"structure": "pb208-invariance", "boundaries_eV": [1e-5, 2e8]}
            )
        )
        outputs = []
        indexes = []
        for name, content in (("carry", carry), ("right", right)):
            source = directory / name
            source.mkdir()
            (source / "n-Pb208.tendl").write_bytes(content)
            output = directory / f"{name}.npz"
            completed = subprocess.run(
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
                    str(groups),
                    "--temperature-K",
                    "0",
                    "--workers",
                    "1",
                    "--grid-density",
                    "1",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr[-1000:])
            outputs.append(output.read_bytes())
            indexes.append(json.loads((directory / f"{name}_index.json").read_text()))
    return {
        "npz_sha256": hashlib.sha256(outputs[0]).hexdigest(),
        "byte_identical": outputs[0] == outputs[1],
        "source_hashes_differ": indexes[0]["targets"][0]["source_sha256"]
        != indexes[1]["targets"][0]["source_sha256"],
        "rows": indexes[0]["n_rows"],
        "pass": outputs[0] == outputs[1]
        and indexes[0]["targets"][0]["source_sha256"]
        != indexes[1]["targets"][0]["source_sha256"],
    }


def decimal(value: float) -> Decimal:
    return Decimal.from_float(float(value))


def penetration(l_value: int, rho: Decimal) -> Decimal:
    square = rho * rho
    if l_value == 0:
        return rho
    if l_value == 1:
        return rho * square / (Decimal(1) + square)
    if l_value == 2:
        return rho * square * square / (
            Decimal(9) + Decimal(3) * square + square * square
        )
    if l_value == 3:
        return rho * square**3 / (
            Decimal(225)
            + Decimal(45) * square
            + Decimal(6) * square * square
            + square**3
        )
    if l_value == 4:
        return rho * square**4 / (
            Decimal(11025)
            + Decimal(1575) * square
            + Decimal(135) * square**2
            + Decimal(10) * square**3
            + square**4
        )
    raise ValueError(f"unsupported high-precision L={l_value}")


def high_precision_pb_capture() -> dict[str, object]:
    energy_float = 1.0736824345386535e-5
    parsed = parse_mf2(WORKING / "n-Pb208.tendl")
    with localcontext() as context:
        context.prec = 80
        energy = decimal(energy_float)
        k_wave = decimal(2.196771e-3)
        pi = decimal(math.pi)
        capture = Decimal(0)
        sequences = 0
        for isotope in parsed["isotopes"]:
            abundance = decimal(isotope["ABN"])
            for energy_range in isotope["ranges"]:
                if not (
                    energy_range.get("LRF") == 3
                    and decimal(energy_range["EL"]) <= energy
                    and energy <= decimal(energy_range["EH"])
                ):
                    continue
                target_spin = decimal(energy_range["SPI"])
                for group in energy_range["L"]:
                    if any(float(value) != 0.0 for value in group["GFA"]) or any(
                        float(value) != 0.0 for value in group["GFB"]
                    ):
                        raise ValueError("Pb-208 high-precision control expected no fission widths")
                    awri = decimal(group["AWRI"])
                    radius = decimal(group["APL"] or energy_range["AP"])
                    l_value = int(group["L"])
                    wave = k_wave * awri / (awri + 1) * energy.sqrt()
                    p_energy = penetration(l_value, wave * radius)
                    for spin_float in sorted(set(float(value) for value in group["AJ"])):
                        real = Decimal(0)
                        imaginary = Decimal(0)
                        for index, candidate in enumerate(group["AJ"]):
                            if float(candidate) != spin_float:
                                continue
                            resonance_energy = decimal(group["ER"][index])
                            resonance_wave = (
                                k_wave
                                * awri
                                / (awri + 1)
                                * abs(resonance_energy).sqrt()
                            )
                            p_resonance = penetration(l_value, resonance_wave * radius)
                            neutron = decimal(group["GN"][index]) * p_energy / p_resonance
                            width = decimal(group["GG"][index]) / 2
                            x_value = resonance_energy - energy
                            norm = x_value * x_value + width * width
                            real += neutron * x_value / (2 * norm)
                            imaginary += neutron * width / (2 * norm)
                        spin = decimal(spin_float).copy_abs()
                        statistical = (2 * spin + 1) / (2 * (2 * target_spin + 1))
                        denominator_norm = (1 + imaginary) ** 2 + real * real
                        capture += (
                            abundance
                            * 4
                            * pi
                            / (wave * wave)
                            * statistical
                            * imaginary
                            / denominator_norm
                        )
                        sequences += 1
        completed = subprocess.run(
            [str(DUMP), "resonance-xs", str(WORKING / "n-Pb208.tendl"), repr(energy_float)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        values = completed.stdout.split()
        rust_capture = float(values[3])
        reference = float(capture)
        relative = abs(rust_capture - reference) / reference
    return {
        "energy_eV": energy_float,
        "spin_sequences": sequences,
        "rust_capture_b": rust_capture,
        "decimal80_capture_b": reference,
        "relative_difference": relative,
        "pass": sequences > 0 and reference > 0.0 and relative <= 2e-13,
    }


def main() -> None:
    required = [
        OFFICIAL,
        WORKING,
        OFFICIAL_MANIFEST,
        WORKING_MANIFEST,
        DUMP,
        ACTINV,
        PREPARE,
        ROOT / "protocols/ACTINV-P10_AMENDMENT_D.md",
        ROOT / "protocols/ACTINV-P10_AMENDMENT_E.md",
        Path("/tmp/actinv-njoy2016-79/src/reconr.f90"),
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"missing P10-G7 neutron source input(s): {missing}")
    pinned = {
        "official_manifest": sha256(OFFICIAL_MANIFEST),
        "working_manifest": sha256(WORKING_MANIFEST),
        "prepare": sha256(PREPARE),
        "amendment_d": sha256(ROOT / "protocols/ACTINV-P10_AMENDMENT_D.md"),
        "amendment_e": sha256(ROOT / "protocols/ACTINV-P10_AMENDMENT_E.md"),
        "pb208_official": sha256(OFFICIAL / "n-Pb208.tendl"),
        "pb208_working": sha256(WORKING / "n-Pb208.tendl"),
        "njoy_reconr": sha256(Path("/tmp/actinv-njoy2016-79/src/reconr.f90")),
    }
    expected_pinned = {key: EXPECTED[key] for key in pinned}
    if pinned != expected_pinned:
        raise SystemExit(f"P10-G7 neutron source pin mismatch: {pinned}")

    module_spec = importlib.util.spec_from_file_location("prepare_neutron", PREPARE)
    prepare_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(prepare_module)
    width_scan = scan_widths()
    nonfinite = scan_nonfinite_fields()
    working = verify_working_manifest()
    strict = strict_parser_and_repair_control(prepare_module)
    invariance = pb_aggregate_invariance(prepare_module)
    high_precision = high_precision_pb_capture()
    expected_nonfinite = [
        {
            "file": "n-Pb208.tendl",
            "line": 781,
            "field": 6,
            "token": "nan",
            "mat": 8237,
            "mf": 3,
            "mt": 1,
        },
        {
            "file": "n-Pb208.tendl",
            "line": 1613,
            "field": 6,
            "token": "nan",
            "mat": 8237,
            "mf": 3,
            "mt": 3,
        },
    ]
    output = {
        "gate": "P10-G7 neutron source controls",
        "pins": pinned,
        "official_file_manifest_sha256": EXPECTED["official_file_manifest"],
        "width_scan": width_scan,
        "nonfinite_numeric_fields": nonfinite,
        "working_manifest": working,
        "strict_parser_and_repair": strict,
        "pb208_aggregate_invariance": invariance,
        "reich_moore_high_precision": high_precision,
    }
    output["pass"] = bool(
        width_scan["pass"]
        and nonfinite == expected_nonfinite
        and working["pass"]
        and strict["pass"]
        and invariance["pass"]
        and high_precision["pass"]
    )
    RESULT.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output, indent=2, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
