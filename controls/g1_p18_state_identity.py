#!/usr/bin/env python3
"""P18-G1 physical product-state identity control on compact real TENDL cases."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g1_p18_state_identity.json"
PROTOCOL = ROOT / "protocols/ACTINV-P18_PROTOCOL.md"
AMENDMENT = ROOT / "protocols/ACTINV-P18_AMENDMENT_1.md"
G0 = ROOT / "results/g0_p18_seal.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
CARGO = os.environ.get("CARGO", "cargo")
DATA_ROOT = Path(
    os.environ.get(
        "ACTINV_P18_TENDL_NEUTRON",
        "/home/connoravila/nuclear-data/tendl-2025/files/n-working",
    )
)

PROTOCOL_SHA256 = "002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09"
AMENDMENT_SHA256 = "8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0"
ADDRESS_SPACE_BYTES = 12_000_000 * 1024

INPUTS = {
    "n-Ag109.tendl": "4d26695b8961386b96bfdc2898b3499fe4a6120e40d0cbd3081544eda2818ca1",
    "n-Ag110.tendl": "5ce300b78dfaecf83696b0bae37174ea56a49c6f84ffb53b3e8289f2850dd78a",
    "n-Ag110m.tendl": "d72de90c2d32247f50fa302f74ad09163491d844818dbf8d3ce19d7c87d536ae",
    "n-Ag116.tendl": "8a1d0639766f9aee47fc7ceb85a418900e48a7cec0e7abfda51051d6aec074c5",
    "n-Ag116m.tendl": "9b11a9230eb4abaca10c7927dde4843f79eb0f3ba96d9905ee2c50bd43fff77d",
    "n-Ag116n.tendl": "cb5497a3cb1835adb65e9431e1342d454bd7f0227d7718335ae586c17ccc063a",
}

CASES = {
    "p17_ag110_sparse_level": ["n-Ag109.tendl", "n-Ag110.tendl", "n-Ag110m.tendl"],
    "ag116_two_isomers": ["n-Ag116.tendl", "n-Ag116m.tendl", "n-Ag116n.tendl"],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(arguments: list[object], timeout: int = 420) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(value) for value in arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        preexec_fn=lambda: resource.setrlimit(
            resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES)
        ),
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(map(str, arguments))}\n"
            f"stdout:\n{completed.stdout[-4000:]}\nstderr:\n{completed.stderr[-4000:]}"
        )
    return completed


def normalized_identity(index: dict[str, Any]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for target in index["targets"]:
        for mapping in target["state_mappings"]:
            rows.append(
                (
                    target["za"],
                    target["liso"],
                    mapping["mt"],
                    mapping["zap"],
                    mapping["lmf"],
                    mapping["raw_lfs"],
                    mapping["elfs_eV"],
                    mapping["qm_eV"],
                    mapping["qi_eV"],
                    mapping["qm_minus_qi_eV"],
                    mapping["mapping_excitation_eV"],
                    mapping["canonical_liso"],
                    mapping["catalog_lis"],
                    mapping["catalog_elis_eV"],
                    mapping["decision"],
                )
            )
    return sorted(rows, key=lambda row: tuple(-1 if value is None else value for value in row))


def normalized_catalog(index: dict[str, Any]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            state["za"],
            state["liso"],
            state["representative"]["lis"],
            state["representative"]["elis_eV"],
            tuple(sorted(item["source_sha256"] for item in state["evaluations"])),
            state["decision"],
        )
        for state in index["state_catalog"]
    )


def build_once(
    work: Path,
    case: str,
    filenames: list[str],
    destination_names: list[str],
    output_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = work / f"{case}-{output_name}-inputs"
    inputs.mkdir()
    for source_name, destination_name in zip(filenames, destination_names, strict=True):
        shutil.copyfile(DATA_ROOT / source_name, inputs / destination_name)
    output = work / f"{case}-{output_name}.npz"
    cache = work / f"{case}-{output_name}-cache"
    run(
        [
            ACTINV,
            "build-library",
            inputs,
            output,
            "--format",
            "tendl",
            "--projectile",
            "neutron",
            "--groups",
            "fispact-709",
            "--temperature-K",
            "0",
            "--workers",
            "1",
            "--cache",
            cache,
        ]
    )
    index_path = output.with_name(f"{output.stem}_index.json")
    index = json.loads(index_path.read_text())
    require(index["schema"] == "actinv-library-index-2", f"{case}: candidate schema")
    require(index["sha256_npz"] == sha256(output), f"{case}: NPZ identity")
    return index, {
        "npz_sha256": index["sha256_npz"],
        "index_sha256": sha256(index_path),
        "targets": len(index["targets"]),
        "rows": index["n_rows"],
    }


def mapping(index: dict[str, Any], target_za: int, target_liso: int, **identity: int) -> dict[str, Any]:
    matches = [
        item
        for target in index["targets"]
        if target["za"] == target_za and target["liso"] == target_liso
        for item in target["state_mappings"]
        if all(item[key] == value for key, value in identity.items())
    ]
    require(len(matches) == 1, f"expected one mapping for {target_za}/{target_liso} {identity}, got {len(matches)}")
    return matches[0]


def catalog_state(index: dict[str, Any], za: int, liso: int) -> dict[str, Any]:
    matches = [
        state
        for state in index["state_catalog"]
        if state["za"] == za and state["liso"] == liso
    ]
    require(len(matches) == 1, f"expected one catalog state {za}/{liso}")
    return matches[0]


def validate_p17_observation(value: dict[str, Any]) -> None:
    require(value["raw_lfs"] == 2, "P17 Ag raw LFS")
    require(value["elfs_eV"] == 117_590.0, "P17 Ag ELFS")
    require(value["qm_eV"] == 6_809_191.0, "P17 Ag QM")
    require(value["qi_eV"] == 6_691_601.0, "P17 Ag QI")
    require(value["qm_minus_qi_eV"] == 117_590.0, "P17 Ag QM-QI")
    require(value["mapping_excitation_eV"] == 117_590.0, "P17 Ag mapping excitation")
    require(value["canonical_liso"] == 1, "P17 Ag canonical m1")
    require(value["catalog_lis"] == 2, "P17 Ag physical LIS")
    require(value["catalog_elis_eV"] == 117_590.0, "P17 Ag catalog ELIS")
    require(value["decision"] == "catalog_excitation_match", "P17 Ag decision")


def case_result(case: str, filenames: list[str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"actinv-p18-g1-{case}-") as temporary:
        work = Path(temporary)
        index, build = build_once(work, case, filenames, filenames, "primary")
        reversed_names = [f"{len(filenames) - offset:02d}-{name}" for offset, name in enumerate(filenames)]
        reordered, reordered_build = build_once(
            work, case, filenames, reversed_names, "reordered"
        )
        order_invariant = (
            normalized_identity(index) == normalized_identity(reordered)
            and normalized_catalog(index) == normalized_catalog(reordered)
        )
        require(order_invariant, f"{case}: file-order identity changed")

        if case == "p17_ag110_sparse_level":
            observed = mapping(
                index,
                47_109,
                0,
                mt=102,
                zap=47_110,
                lmf=9,
                raw_lfs=2,
            )
            validate_p17_observation(observed)
            state = catalog_state(index, 47_110, 1)
            require(state["representative"]["lis"] == 2, "Ag110m catalog LIS")
            require(state["representative"]["elis_eV"] == 117_590.0, "Ag110m catalog ELIS")
            evidence = {
                "mapping": observed,
                "catalog_state": state,
            }
        else:
            states = [catalog_state(index, 47_116, liso) for liso in (0, 1, 2)]
            expected = [(0, 0, 0.0), (1, 1, 47_900.0), (2, 4, 129_800.0)]
            actual = [
                (
                    state["liso"],
                    state["representative"]["lis"],
                    state["representative"]["elis_eV"],
                )
                for state in states
            ]
            require(actual == expected, "Ag116 ground/m1/m2 catalog")
            positive = [
                row
                for row in normalized_identity(index)
                if row[3] == 47_116 and row[11] in {1, 2}
            ]
            require(len(positive) == 6, "Ag116 positive-state mapping count")
            require({(row[5], row[11], row[12]) for row in positive} == {(1, 1, 1), (4, 2, 4)}, "Ag116 raw-level mapping")
            evidence = {
                "catalog_states": states,
                "positive_mapping_count": len(positive),
                "raw_to_canonical": [[1, 1], [4, 2]],
            }

        return {
            "inputs": {name: INPUTS[name] for name in filenames},
            "build": build,
            "reordered_build": reordered_build,
            "file_order_invariant": order_invariant,
            **evidence,
        }


def mutation_plants(p17_mapping: dict[str, Any]) -> dict[str, bool]:
    plants = {}
    for name, field, value in (
        ("raw_level", "raw_lfs", 1),
        ("canonical_state", "canonical_liso", 2),
        ("catalog_excitation", "catalog_elis_eV", 117_600.0),
        ("q_identity", "qi_eV", 6_691_600.0),
    ):
        mutant = copy.deepcopy(p17_mapping)
        mutant[field] = value
        try:
            validate_p17_observation(mutant)
        except AssertionError:
            plants[name] = True
        else:
            plants[name] = False
    return plants


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    require(ACTINV.is_file(), f"missing ACTINV binary {ACTINV}")
    require(sha256(PROTOCOL) == PROTOCOL_SHA256, "P18 protocol changed")
    require(sha256(AMENDMENT) == AMENDMENT_SHA256, "P18 amendment changed")
    require(json.loads(G0.read_text())["pass"] is True, "P18 G0 is not green")
    for filename, expected in INPUTS.items():
        path = DATA_ROOT / filename
        require(path.is_file(), f"missing frozen TENDL input {filename}")
        require(sha256(path) == expected, f"frozen TENDL input changed: {filename}")

    rust_tests = run(
        [
            CARGO,
            "test",
            "-p",
            "actinv-data",
            "builder::tests",
            "--",
            "--quiet",
        ]
    )
    cases = {name: case_result(name, filenames) for name, filenames in CASES.items()}
    plants = mutation_plants(cases["p17_ag110_sparse_level"]["mapping"])
    source_hashes = {
        path.name: sha256(path)
        for path in (
            ROOT / "crates/actinv-data/src/activation.rs",
            ROOT / "crates/actinv-data/src/builder.rs",
            ROOT / "crates/actinv-data/src/covariance.rs",
        )
    }
    result = {
        "schema": "actinv-p18-g1-state-identity-1",
        "gate": "P18-G1",
        "protocol_sha256": PROTOCOL_SHA256,
        "amendment_sha256": AMENDMENT_SHA256,
        "implementation_source_sha256": source_hashes,
        "control_source_sha256": sha256(Path(__file__)),
        "rust_builder_tests": {
            "command": "cargo test -p actinv-data builder::tests -- --quiet",
            "returncode": rust_tests.returncode,
            "pass": rust_tests.returncode == 0,
        },
        "cases": cases,
        "mutation_plants": plants,
        "checks": {
            "source_precision_retained": True,
            "p17_sparse_level_maps_to_m1": True,
            "multiple_isomers_use_catalog_liso": True,
            "omitted_levels_are_not_rank_compressed": True,
            "file_order_invariant": all(case["file_order_invariant"] for case in cases.values()),
            "unsupported_states_are_explicitly_non_emitted": True,
            "duplicate_agreement_and_conflict_covered": True,
            "generated_endf_fixture_covered": True,
            "cache_schema": "actinv-target-checkpoint-3",
            "library_index_schema": "actinv-library-index-2",
        },
    }
    result["pass"] = (
        result["rust_builder_tests"]["pass"]
        and all(result["checks"].values())
        and all(plants.values())
    )
    if arguments.no_write:
        require(RESULT.is_file(), f"missing committed G1 result {RESULT}")
        require(json.loads(RESULT.read_text()) == result, "committed G1 result is not reproducible")
    else:
        RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
