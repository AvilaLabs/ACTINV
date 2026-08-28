#!/usr/bin/env python3
"""Independently derive the P12 verdict from the frozen v1.0 gate evidence."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL_HASHES = {
    "protocols/ACTINV-P12_PROTOCOL.md": "247e669691d99a5e548734528a069bb49962e6ae356ba14f962abcf2826ed715",
    "protocols/ACTINV-P12_AMENDMENT_A.md": "21f73ecfa3858bc9967183e0bb090382c6512acd2f5b4e9f25252cf32c67571a",
    "protocols/ACTINV-P12_AMENDMENT_B.md": "c4c823c5bb07235df43a9e26c5c4b40e852745fbfb72723612b9867507df0769",
    "protocols/ACTINV-P12_AMENDMENT_C.md": "141a3e7dc70fd3d324930ffb6db328201f69c64bf447f65569650ca042fd559c",
    "protocols/ACTINV-P12_AMENDMENT_D.md": "91084144aa8ead0679bece73375c7880c8b6037ad5647d3bebc28539f30993f4",
    "protocols/ACTINV-P12_AMENDMENT_E.md": "1f05dab0e0fcd4df7a58afe3bdab2f319a553e796551a1c49d90d34117e1c6f1",
}
G3_AMENDMENTS = tuple(
    f"protocols/ACTINV-P12_AMENDMENT_{letter}.md" for letter in ("A", "B", "C", "D")
)
MANIFEST_EXCLUDED = (
    "MANIFEST.sha256",
    "results/g6_p12_complete.json",
    "results/verdict_p12.json",
)
GATE_FILES = {
    "G1": "g1_p12_radiological.json",
    "G2": "g2_p12_primary_tables.json",
    "G3": "g3_p12_parser_fuzz.json",
    "G4": "g4_p12_fng.json",
    "G5": "g5_p12_release.json",
    "G6": "g6_p12_complete.json",
}
PUBLIC_READER_FAMILIES = {
    "run_spec",
    "mesh_spec",
    "photon_response",
    "group_structure",
    "endf_records_sections",
    "activation_evaluation",
    "mf33_covariance",
    "decay",
    "fission_yields",
    "activation_library_npz",
    "canonical_flux_stream",
}
MUTATION_OPERATORS = {
    "count_value",
    "delete",
    "duplicate_span",
    "insert",
    "invalid_encoding",
    "numeric_edge",
    "replace_bit",
    "truncate",
}
READER_SOURCES = (
    "crates/actinv-core/src/bin/parser_fuzz_probe.rs",
    "crates/actinv-data/src/endf.rs",
    "crates/actinv-data/src/decay.rs",
    "crates/actinv-data/src/fission.rs",
    "crates/actinv-data/src/library.rs",
)
PRIOR_VERDICTS = {
    "verdict.json": "P1-PASS",
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def repository_manifest_integrity() -> dict[str, object]:
    inventory = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    paths = sorted(
        path for path in inventory.split("\0") if path and path not in MANIFEST_EXCLUDED
    )
    expected = "".join(f"{sha256(ROOT / path)}  ./{path}\n" for path in paths)
    manifest = ROOT / "MANIFEST.sha256"
    actual = manifest.read_text() if manifest.is_file() else ""
    return {
        "entries": len(paths),
        "excluded": list(MANIFEST_EXCLUDED),
        "byte_identical": actual == expected,
        "pass": actual == expected,
    }


def commit_integrity(commit: str) -> dict[str, object]:
    valid = re.fullmatch(r"[0-9a-f]{40}", commit) is not None
    if not valid:
        return {"object_is_commit": False, "ancestor_of_head": False, "tree": None, "pass": False}
    object_type = subprocess.run(
        ["git", "cat-file", "-t", commit],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    tree = None
    if object_type.returncode == 0 and object_type.stdout.strip() == "commit":
        tree_result = subprocess.run(
            ["git", "rev-parse", f"{commit}^{{tree}}"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if tree_result.returncode == 0:
            tree = tree_result.stdout.strip()
    return {
        "object_is_commit": object_type.returncode == 0 and object_type.stdout.strip() == "commit",
        "ancestor_of_head": ancestor,
        "tree": tree,
        "pass": object_type.returncode == 0
        and object_type.stdout.strip() == "commit"
        and ancestor
        and isinstance(tree, str)
        and re.fullmatch(r"[0-9a-f]{40}", tree) is not None,
    }


def load_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def nested(mapping: object, *keys: str, default: object = None) -> object:
    value = mapping
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def all_true(mapping: object, *, nonempty: bool = True) -> bool:
    return isinstance(mapping, dict) and (bool(mapping) or not nonempty) and all(
        value is True for value in mapping.values()
    )


def finite_at_most(value: object, limit: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= limit


def evaluate_safely(
    function: Callable[[dict[str, object] | None], dict[str, object]],
    value: dict[str, object] | None,
) -> dict[str, object]:
    try:
        return function(value)
    except (
        KeyError,
        TypeError,
        ValueError,
        StopIteration,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        return {
            "present": value is not None,
            "error": f"{type(error).__name__}: {error}",
            "pass": False,
        }


def protocol_integrity() -> dict[str, object]:
    ledger: dict[str, str] = {}
    ledger_path = ROOT / "protocols" / "protocol_hash.txt"
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  (protocols/.+)", line)
            if match:
                ledger[match.group(2)] = match.group(1)
    files = {}
    for relative, expected in PROTOCOL_HASHES.items():
        path = ROOT / relative
        actual = sha256(path) if path.is_file() else None
        files[relative] = {
            "expected_sha256": expected,
            "actual_sha256": actual,
            "ledger_sha256": ledger.get(relative),
            "pass": actual == expected and ledger.get(relative) == expected,
        }
    return {"files": files, "pass": all(item["pass"] for item in files.values())}


def family_partition(value: object, cases: int) -> bool:
    families = nested(value, "deterministic", "families")
    if not isinstance(families, list) or len(families) != len(PUBLIC_READER_FAMILIES):
        return False
    names = {item.get("family") for item in families if isinstance(item, dict)}
    if names != PUBLIC_READER_FAMILIES:
        return False
    return sum(int(item.get("cases", -1)) for item in families) == cases and all(
        isinstance(item, dict)
        and int(item.get("accepted", -1)) + int(item.get("rejected", -1)) == int(item.get("cases", -1))
        and item.get("panics") == 0
        and isinstance(item.get("corpus_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", str(item["corpus_sha256"])) is not None
        for item in families
    )


def operator_partition(value: object, cases: int) -> bool:
    operators = nested(value, "deterministic", "operators")
    return (
        isinstance(operators, dict)
        and set(operators) == MUTATION_OPERATORS
        and sum(int(count) for count in operators.values()) == cases
        and all(int(count) > 0 for count in operators.values())
    )


def evaluate_g1(value: dict[str, object] | None) -> dict[str, object]:
    if value is None:
        return {"present": False, "pass": False}
    plants = value.get("plants")
    table = ROOT / "controls" / "fixtures" / "p12_radiological_table.json"
    checks = {
        "identity": value.get("gate") == "P12-G1"
        and value.get("protocol_sha256") == PROTOCOL_HASHES["protocols/ACTINV-P12_PROTOCOL.md"],
        "fixture_hash": table.is_file() and value.get("table_sha256") == sha256(table),
        "partition": value.get("steps") == 5
        and value.get("responses_per_step") == 4
        and nested(value, "independent_dense_response", "comparisons") == 80,
        "numeric_bounds": value.get("relative_limit") == 2e-15
        and value.get("absolute_limit") == 1e-30
        and finite_at_most(
            nested(value, "independent_dense_response", "maximum_relative"), 2e-15
        )
        and finite_at_most(
            nested(value, "independent_dense_response", "maximum_absolute"), 1e-30
        ),
        "entry_points": all(
            nested(value, "entry_points", name) is True
            for name in ("cli_vs_python", "cli_vs_prepared", "cli_vs_mesh")
        )
        and nested(value, "entry_points", "labels") == ["cli", "python", "prepared", "mesh"],
        "metadata": nested(value, "independent_dense_response", "metadata_exact") is True
        and nested(value, "independent_dense_response", "pass") is True
        and all_true(value.get("certificate"))
        and all_true(value.get("coverage")),
        "rejection_plants": isinstance(plants, dict)
        and len(plants) == 13
        and all(
            isinstance(plant, dict)
            and plant.get("pass") is True
            and plant.get("output_published") is False
            for plant in plants.values()
        ),
    }
    return {
        "present": True,
        "comparisons": nested(value, "independent_dense_response", "comparisons"),
        "plants": len(plants) if isinstance(plants, dict) else None,
        "checks": checks,
        "pass": value.get("pass") is True and all(checks.values()),
    }


def evaluate_g2(value: dict[str, object] | None) -> dict[str, object]:
    if value is None:
        return {"present": False, "pass": False}
    checks = {
        "identity": value.get("gate") == "P12-G2"
        and value.get("protocol_sha256") == PROTOCOL_HASHES["protocols/ACTINV-P12_PROTOCOL.md"],
        "primary_sources": nested(value, "inputs", "meija_pdf", "sha256")
        == "d9079171301dc440e6ee40378da1aa5aef7c43e99d815f4cf31c1eb76561dd89"
        and nested(value, "inputs", "ame2020_mass_table", "sha256")
        == "e8599c6d7f724fac91934e59f1b9de8fb8f63e820f4b39456b790665ed2a3307",
        "abundance": nested(value, "abundance", "rows") == 289
        and nested(value, "abundance", "elements") == 84
        and nested(value, "abundance", "key_set_exact") is True
        and nested(value, "abundance", "binary64_values_exact") is True
        and nested(value, "abundance", "element_sums_within_limit") is True
        and finite_at_most(nested(value, "abundance", "maximum_element_sum_deviation"), 2e-15),
        "masses": nested(value, "mass", "selected_rows") == 289
        and nested(value, "mass", "key_set_exact") is True
        and nested(value, "mass", "binary64_values_exact") is True,
        "regeneration": nested(value, "regeneration", "byte_identical") is True
        and nested(value, "regeneration", "generated_sha256")
        == nested(value, "regeneration", "tracked_sha256"),
        "provenance": all_true(value.get("provenance")) and all_true(value.get("certificate")),
        "oracle": value.get("runtime_oracle") == "primary Meija PDF and AME2020 fixed-width table only",
    }
    return {
        "present": True,
        "rows": nested(value, "abundance", "rows"),
        "elements": nested(value, "abundance", "elements"),
        "checks": checks,
        "pass": value.get("pass") is True and all(checks.values()),
    }


def evaluate_g3(value: dict[str, object] | None) -> dict[str, object]:
    if value is None:
        return {"present": False, "pass": False}
    smoke = value.get("smoke")
    full = value.get("full")
    reader_hashes = nested(value, "source", "reader_source_sha256")
    expected_amendments = {
        Path(relative).stem: PROTOCOL_HASHES[relative] for relative in G3_AMENDMENTS
    }
    regressions = nested(value, "source", "regression_tests")
    checks = {
        "identity": value.get("schema") == "actinv-p12-g3-result-1"
        and value.get("gate") == "P12-G3"
        and value.get("protocol_sha256") == PROTOCOL_HASHES["protocols/ACTINV-P12_PROTOCOL.md"]
        and value.get("control_sha256") == sha256(ROOT / "controls" / "g3_p12_parser_fuzz.py")
        and value.get("amendment_sha256") == expected_amendments,
        "smoke_partition": nested(smoke, "deterministic", "cases") == 10_000
        and family_partition(smoke, 10_000)
        and operator_partition(smoke, 10_000)
        and nested(smoke, "deterministic", "outcome_sha256")
        == "dfadb80bc7fcc46d969609afd642d73f08c3c075e4c58240599aba59603a9c2e"
        and all_true(nested(smoke, "checks"))
        and nested(smoke, "pass") is True,
        "full_partition": nested(full, "deterministic", "cases") == 1_000_000
        and family_partition(full, 1_000_000)
        and operator_partition(full, 1_000_000)
        and nested(full, "seed_hex") == "414354494e565031"
        and nested(full, "deterministic", "outcome_sha256")
        == "321614d5d6de759f4423d203b3603e5800fe7d96f705fffe7a1fa6921eee5a42"
        and nested(full, "exit_code") == 0
        and nested(full, "timed_out") is False
        and nested(full, "signalled") is False
        and nested(full, "pass") is True
        and all_true(value.get("full_checks")),
        "container_minimums": all(
            next(
                int(item["cases"])
                for item in nested(full, "deterministic", "families", default=[])
                if item.get("family") == name
            )
            >= 10_000
            for name in ("activation_library_npz", "canonical_flux_stream")
        ),
        "memory": isinstance(value.get("memory_limit_bytes"), int)
        and value["memory_limit_bytes"] == 1024**3
        and isinstance(nested(full, "peak_rss_bytes"), int)
        and 0 < int(nested(full, "peak_rss_bytes")) < int(value["memory_limit_bytes"]),
        "reader_sources": isinstance(reader_hashes, dict)
        and set(reader_hashes) == set(READER_SOURCES)
        and all(reader_hashes[relative] == sha256(ROOT / relative) for relative in READER_SOURCES),
        "regressions": isinstance(regressions, dict)
        and len(regressions) == 5
        and all_true(regressions)
        and nested(value, "source", "all_regressions_present") is True
        and nested(value, "source", "unsafe_absent") is True
        and nested(value, "source", "nightly_features_absent") is True,
        "repair_record": nested(value, "repair_record", "discovery_count") == 5
        and nested(value, "repair_record", "full_partition_discoveries") == 0
        and nested(value, "repair_record", "all_discoveries_have_regressions") is True
        and len(nested(value, "repair_record", "pre_full_partition_discoveries", default=[])) == 5,
    }
    return {
        "present": True,
        "smoke_cases": nested(smoke, "deterministic", "cases"),
        "full_cases": nested(full, "deterministic", "cases"),
        "full_peak_rss_bytes": nested(full, "peak_rss_bytes"),
        "checks": checks,
        "pass": value.get("pass") is True and all(checks.values()),
    }


def evaluate_g4(value: dict[str, object] | None) -> dict[str, object]:
    if value is None:
        return {"present": False, "pass": False}
    histories = nested(value, "history_comparison", "nuclides")
    required_hashes = {
        "archive": "1c76f42dcbc3e0f488f8035c3f63e4cd4428930f76efc088329be7ec9c6b45ed",
        "chain_endfb80_reduced.xml": "f3f56d3a9ee66bcb691ea0812aad6a3696c00f6272f503de866a495b85c7270e",
        "depletion_results.h5": "1fcd608a0a8100892b4d24ca7de05d401ab952b904ac3d80c8698de36419d4d5",
        "flux_620.npy": "9f2b3223164adbe5709aa493943af0a1fde3b538654ec28993b32dfe56195828",
        "fluxes": "25bc8b50a74147f4cc4637a24e2c6d0d8b24562447abb28e7ba699bc03390fde",
        "inventory.i": "c2fdfc04547017823c533e5a48199c5bd49cfb33fe36fb7a984a88c30c20516b",
        "microxs_620.csv": "fa097a994e8a4ea93267603bd6435972c15d3daa1d89cb37b626e21147637651",
        "protocol": PROTOCOL_HASHES["protocols/ACTINV-P12_PROTOCOL.md"],
    }
    relative_limit = nested(value, "history_comparison", "relative_limit_at_or_above_1e6_atoms")
    absolute_limit = nested(value, "history_comparison", "scaled_absolute_limit_below_1e6_atoms")
    checks = {
        "identity": value.get("schema") == "actinv-p12-g4-result-1"
        and value.get("gate") == "P12-G4",
        "sources": value.get("external_data_untracked") is True
        and nested(value, "reference", "hashes") == required_hashes
        and nested(value, "reference", "cell") == 620
        and nested(value, "reference", "intervals") == 170,
        "histories": isinstance(histories, dict)
        and set(histories) == {"Co58", "Tc99_m1", "Mn56", "Cr51"}
        and relative_limit == 1e-4
        and absolute_limit == 1e-18
        and all(
            item.get("endpoints") == 170
            and finite_at_most(item.get("maximum_relative_error_at_or_above_1e6_atoms"), 1e-4)
            and finite_at_most(item.get("maximum_scaled_absolute_error_below_1e6_atoms"), 1e-18)
            and item.get("pass") is True
            for item in histories.values()
        )
        and nested(value, "history_comparison", "pass") is True,
        "reaction_rates": nested(value, "independent_reaction_rates", "comparisons") == 120
        and nested(value, "independent_reaction_rates", "formula")
        == "sigma_b * sum(flux_620) * source_rate / volume * 1e-24"
        and nested(value, "independent_reaction_rates", "pass") is True,
        "reproducibility": nested(value, "reproducibility", "pass") is True
        and nested(value, "reproducibility", "repeated_scientific_result_identical") is True
        and nested(value, "reproducibility", "temporary_decay_bytes_identical") is True
        and nested(value, "reproducibility", "temporary_library_bytes_identical") is True
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(nested(value, "reproducibility", "normalized_result_sha256", default="")),
        )
        is not None,
        "transformation": nested(value, "transformation", "conservation_mapped_labels") == 19
        and nested(value, "transformation", "reaction_label_count") == 20
        and nested(value, "transformation", "library_rows")
        == nested(value, "transformation", "loss_rows") + nested(value, "transformation", "product_rows"),
    }
    return {
        "present": True,
        "endpoints": nested(value, "reference", "intervals"),
        "reaction_rate_comparisons": nested(value, "independent_reaction_rates", "comparisons"),
        "checks": checks,
        "pass": value.get("pass") is True and all(checks.values()),
    }


def current_versions() -> dict[str, object]:
    with (ROOT / "Cargo.toml").open("rb") as stream:
        workspace = tomllib.load(stream)
    with (ROOT / "python" / "Cargo.toml").open("rb") as stream:
        python_cargo = tomllib.load(stream)
    with (ROOT / "python" / "pyproject.toml").open("rb") as stream:
        pyproject = tomllib.load(stream)
    return {
        "workspace": workspace["workspace"]["package"]["version"],
        "python_cargo": python_cargo["package"]["version"],
        "python_project": pyproject["project"]["version"],
        "requires_python": pyproject["project"]["requires-python"],
    }


def evaluate_g5(value: dict[str, object] | None) -> dict[str, object]:
    if value is None:
        return {"present": False, "pass": False}
    versions = current_versions()
    prior = {
        name: nested(load_json(RESULTS / name), "verdict") == expected
        for name, expected in PRIOR_VERDICTS.items()
    }
    commands = value.get("commands")
    archives = nested(value, "crate_packages", "archives")
    checks = {
        "identity": value.get("schema") == "actinv-p12-g5-result-1"
        and value.get("gate") == "P12-G5",
        "commands": isinstance(commands, dict)
        and {
            "rustfmt",
            "check",
            "clippy",
            "test",
            "release build",
            "dependency declaration",
            "release notes",
            "prior verdict evidence",
            "end_to_end_cli_python",
            "p12_radiological_ci_subset",
            "p12_input_reliability_ci_subset",
            "self_contained_clone",
        }.issubset(commands)
        and all(commands[name] is True for name in commands),
        "versions": set(versions.values()) == {"1.0.0", ">=3.9"}
        and nested(value, "source", "versions")
        == {
            "workspace": "1.0.0",
            "python_cargo": "1.0.0",
            "python_project": "1.0.0",
        }
        and nested(value, "source", "requires_python") == ">=3.9"
        and nested(value, "standalone_binary", "version_output") == "actinv 1.0.0"
        and nested(value, "standalone_binary", "pass") is True,
        "rust_packages": archives
        == ["actinv-cli-1.0.0.crate", "actinv-core-1.0.0.crate", "actinv-data-1.0.0.crate"]
        and nested(value, "crate_packages", "pass") is True
        and all_true(nested(value, "crate_packages", "embedded_release_data")),
        "python_package": nested(value, "python_package", "pass") is True
        and all_true(nested(value, "python_package", "checks"))
        and nested(value, "python_package", "source_distribution") == "actinv-1.0.0.tar.gz"
        and nested(value, "python_package", "wheel") == "actinv-1.0.0-cp39-abi3-<platform>.whl",
        "clean_clone": nested(value, "clean_clone", "source_was_clean") is True
        and nested(value, "clean_clone", "only_expected_control_results_changed") is True,
        "source_contract": nested(value, "source", "pass") is True
        and nested(value, "source", "versions_exact") is True
        and nested(value, "source", "dependency_versions_exact") is True
        and nested(value, "source", "embedded_tables_exact") is True
        and nested(value, "source", "licence_copies_match") is True
        and all_true(nested(value, "source", "documentation"))
        and all_true(nested(value, "source", "release_workflow"))
        and all_true(nested(value, "source", "ci")),
        "prior_verdicts": all(prior.values()),
    }
    return {
        "present": True,
        "versions": versions,
        "prior_verdicts": prior,
        "checks": checks,
        "pass": value.get("pass") is True and all(checks.values()),
    }


def evaluate_g6(value: dict[str, object] | None) -> dict[str, object]:
    if value is None:
        return {"present": False, "pass": False}
    expected_results = {
        filename: sha256(RESULTS / filename)
        for gate, filename in GATE_FILES.items()
        if gate != "G6"
    }
    release_commit = value.get("release_commit")
    release = commit_integrity(release_commit if isinstance(release_commit, str) else "")
    manifest = repository_manifest_integrity()
    session_path = ROOT / "docs" / "history" / "sessions" / "P12.md"
    session_text = session_path.read_text() if session_path.is_file() else ""
    github = value.get("github")
    run_id = nested(github, "run_id")
    run_url = nested(github, "url")
    expected_origin = "https://github.com/AvilaLabs/ACTINV.git"
    required_session_fragments = [
        str(release_commit),
        expected_origin,
        str(run_id),
        str(run_url),
        "P12-CONDITIONAL",
        *expected_results.values(),
        *PROTOCOL_HASHES.values(),
    ]
    checks = {
        "identity": value.get("schema") == "actinv-p12-g6-complete-1"
        and value.get("gate") == "P12-G6",
        "release_commit": isinstance(release_commit, str)
        and re.fullmatch(r"[0-9a-f]{40}", release_commit) is not None,
        "release_object": release["pass"] is True
        and nested(value, "remote", "release_tree") == release["tree"],
        "gate_results": value.get("gate_result_sha256") == expected_results,
        "protocols": value.get("protocol_sha256") == PROTOCOL_HASHES,
        "session": session_path.is_file()
        and nested(value, "session", "sha256") == sha256(session_path)
        and nested(value, "session", "required_fragments") == len(required_session_fragments)
        and nested(value, "session", "missing") == []
        and nested(value, "session", "pass") is True
        and all(fragment in session_text for fragment in required_session_fragments),
        "manifest": manifest["pass"] is True
        and manifest["excluded"] == list(MANIFEST_EXCLUDED)
        and nested(value, "manifest", "entries") == manifest["entries"]
        and nested(value, "manifest", "expected_entries") == manifest["entries"]
        and nested(value, "manifest", "excluded") == list(MANIFEST_EXCLUDED)
        and nested(value, "manifest", "valid_lines") is True
        and nested(value, "manifest", "duplicates") == []
        and nested(value, "manifest", "self_excluded") is True
        and nested(value, "manifest", "exact_inventory") is True
        and nested(value, "manifest", "hashes_match") is True
        and nested(value, "manifest", "regeneration_byte_identical") is True
        and nested(value, "manifest", "pass") is True,
        "remote": nested(value, "remote", "origin") == expected_origin
        and nested(value, "remote", "expected_origin") == expected_origin
        and nested(value, "remote", "release_is_ancestor_of_head") is True
        and nested(value, "remote", "origin_master_contains_release") is True
        and nested(value, "remote", "pass") is True,
        "github": isinstance(run_id, int)
        and run_id > 0
        and isinstance(nested(github, "run_attempt"), int)
        and nested(github, "run_attempt") > 0
        and nested(github, "name") == "controls"
        and nested(github, "event") == "push"
        and nested(github, "status") == "completed"
        and nested(github, "conclusion") == "success"
        and nested(github, "head_branch") in ("master", "main")
        and nested(github, "head_sha") == release_commit
        and nested(github, "workflow_path") == ".github/workflows/ci.yml"
        and isinstance(run_url, str)
        and run_url == f"https://github.com/AvilaLabs/ACTINV/actions/runs/{run_id}"
        and all_true(nested(github, "checks"))
        and nested(github, "pass") is True,
        "regeneration": nested(value, "regeneration", "preclose_rederived") is True
        and nested(value, "regeneration", "verdict_regeneration_byte_identical") is True
        and nested(value, "regeneration", "manifest_regeneration_byte_identical") is True
        and nested(value, "regeneration", "pass") is True,
    }
    return {
        "present": True,
        "release_commit": release_commit,
        "github_run_id": run_id,
        "local_release_object": release,
        "local_manifest": manifest,
        "checks": checks,
        "pass": value.get("pass") is True and all(checks.values()),
    }


def derive(*, through_g5: bool = False) -> dict[str, object]:
    protocols = protocol_integrity()
    values = {gate: load_json(RESULTS / filename) for gate, filename in GATE_FILES.items()}
    gates = {
        "G1": evaluate_safely(evaluate_g1, values["G1"]),
        "G2": evaluate_safely(evaluate_g2, values["G2"]),
        "G3": evaluate_safely(evaluate_g3, values["G3"]),
        "G4": evaluate_safely(evaluate_g4, values["G4"]),
        "G5": evaluate_safely(evaluate_g5, values["G5"]),
    }
    if through_g5:
        passed = bool(protocols["pass"] and all(item["pass"] for item in gates.values()))
        return {
            "schema": "actinv-p12-preclose-verdict-1",
            "protocols": protocols,
            "gates": gates,
            "verdict": "P12-G1-G5-PASS" if passed else "P12-G1-G5-FAIL",
            "pass": passed,
        }

    gates["G6"] = evaluate_safely(evaluate_g6, values["G6"])
    missing = [gate for gate, item in gates.items() if not item["present"]]
    if missing:
        verdict = "UNSCORED"
    elif not protocols["pass"] or any(not item["pass"] for item in gates.values()):
        verdict = "P12-FAIL"
    else:
        verdict = "P12-CONDITIONAL"
    amendments = [Path(relative).name for relative in PROTOCOL_HASHES if "AMENDMENT" in relative]
    return {
        "schema": "actinv-p12-verdict-1",
        "protocols": protocols,
        "gates": gates,
        "repair_round": bool(amendments),
        "amendments": amendments,
        "missing_gates": missing,
        "verdict": verdict,
    }


def rendered_verdict() -> str:
    return json.dumps(derive(), indent=1, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--through-g5",
        action="store_true",
        help="independently verify the release payload gates without requiring closure evidence",
    )
    arguments = parser.parse_args()
    output = derive(through_g5=arguments.through_g5)
    payload = json.dumps(output, indent=1, sort_keys=True) + "\n"
    if not arguments.through_g5:
        (RESULTS / "verdict_p12.json").write_text(payload)
    print(payload, end="")
    if arguments.through_g5:
        return 0 if output["pass"] else 1
    return 0 if output["verdict"] in ("P12-PASS", "P12-CONDITIONAL") else 1


if __name__ == "__main__":
    raise SystemExit(main())
