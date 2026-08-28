#!/usr/bin/env python3
"""P12-G5: versions, prior evidence, clean-clone packages, interfaces, CI and release docs."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g5_p12_release.json"
VERSION = "1.0.0"
MEMORY_LIMIT = "4294967296"
CRATES = ("actinv-data", "actinv-core", "actinv-cli")
EMBEDDED_TABLE_HASHES = {
    "mt_products.json": "31d4e97d773a96e000fff42b834e63fada0aaba55825cac6d334e64dc95b204b",
    "fispact_709_groups.json": "78f606ac7ac2f776e0818eb8eae99f350b470c6772715f339b5fe25755ab32e2",
    "fispact_162_groups.json": "09e64a81de6195dad74f7afcabfd27b53013a6873ddeb21d1de5a5553d29ec6a",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def command(
    arguments: list[str | Path],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 1800,
) -> subprocess.CompletedProcess[str]:
    argv = ["prlimit", f"--as={MEMORY_LIMIT}", "--"] + [str(value) for value in arguments]
    print(f"[P12-G5] {' '.join(argv)}", file=sys.stderr, flush=True)
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode:
        tail = (result.stdout + result.stderr)[-5000:]
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(argv)}\n{tail}")
    return result


def read_toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def semantic_json_sha256(path: Path) -> str:
    value = json.loads(path.read_text())
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def source_checks(root: Path) -> dict:
    workspace = read_toml(root / "Cargo.toml")
    py_cargo = read_toml(root / "python" / "Cargo.toml")
    pyproject = read_toml(root / "python" / "pyproject.toml")
    readme = (root / "README.md").read_text()
    notes = (root / "docs" / "RELEASE_NOTES_v1.0.md").read_text()
    qualification = (root / "docs" / "QUALIFICATION.md").read_text()
    checklist = (root / "docs" / "RELEASE_CHECKLIST.md").read_text()
    workflow = (root / ".github" / "workflows" / "release-artifacts.yml").read_text()
    ci = (root / ".github" / "workflows" / "ci.yml").read_text()

    versions = {
        "workspace": workspace["workspace"]["package"]["version"],
        "python_cargo": py_cargo["package"]["version"],
        "python_project": pyproject["project"]["version"],
    }
    dependency_versions = {
        "actinv-core->actinv-data": read_toml(root / "crates/actinv-core/Cargo.toml")[
            "dependencies"
        ]["actinv-data"]["version"],
        "actinv-cli->actinv-core": read_toml(root / "crates/actinv-cli/Cargo.toml")[
            "dependencies"
        ]["actinv-core"]["version"],
        "actinv-cli->actinv-data": read_toml(root / "crates/actinv-cli/Cargo.toml")[
            "dependencies"
        ]["actinv-data"]["version"],
        "actinv-py->actinv-core": py_cargo["dependencies"]["actinv-core"]["version"],
    }
    licence_copies_match = all(
        sha256(root / name) == sha256(root / "python" / name)
        for name in ("LICENSE-MIT", "LICENSE-APACHE")
    )
    embedded_table_hashes = {
        name: semantic_json_sha256(root / "crates" / "actinv-data" / "data" / name)
        for name in EMBEDDED_TABLE_HASHES
    }
    required_doc_phrases = {
        "one_line_install": "pip install actinv" in readme,
        "research_grade": "research-grade" in readme.lower(),
        "qualification_boundary": "Qualification boundary" in qualification,
        "regulatory_selection": "jurisdiction" in qualification
        and "radiological table" in qualification,
        "input_hashes": "SHA-256" in qualification,
        "validation_applicability": "validation applicability" in qualification,
        "carried_limitations": "## Carried limitations" in notes,
        "public_acts_separate": "separate maintainer actions" in notes,
        "release_checklist": "## Public acts" in checklist,
    }
    workflow_checks = {
        "pinned_maturin_action": "PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b"
        in workflow,
        "pinned_maturin": "maturin-version: v1.15.0" in workflow,
        "linux_x86_64": "x86_64-unknown-linux-gnu" in workflow,
        "linux_aarch64": "aarch64-unknown-linux-gnu" in workflow,
        "macos_x86_64": "x86_64-apple-darwin" in workflow,
        "macos_aarch64": "aarch64-apple-darwin" in workflow,
        "windows_x86_64": "x86_64-pc-windows-msvc" in workflow,
        "no_publish_command": not re.search(r"maturin\s+publish|cargo\s+publish", workflow),
    }
    ci_checks = {
        "strict_rust_commands": all(
            phrase in ci
            for phrase in (
                "cargo fmt --all -- --check",
                "cargo check --workspace --all-targets --all-features",
                "cargo clippy --workspace --all-targets --all-features -- -D warnings",
                "cargo test --workspace --all-targets --all-features",
            )
        ),
        "wheel_install": "pip install ../dist/*.whl" in ci,
        "prior_verdicts": "check_prior_verdicts.py" in ci,
        "p12_radiological_subset": "g1_p12_radiological.py" in ci,
        "p12_input_reliability_subset": "g3_p12_parser_fuzz.py --smoke" in ci,
    }
    return {
        "versions": versions,
        "versions_exact": set(versions.values()) == {VERSION},
        "dependency_versions": dependency_versions,
        "dependency_versions_exact": set(dependency_versions.values()) == {f"={VERSION}"},
        "abi3_py39": py_cargo["dependencies"]["pyo3"].get("features") == ["abi3-py39"],
        "requires_python": pyproject["project"]["requires-python"],
        "licence_copies_match": licence_copies_match,
        "embedded_table_hashes": embedded_table_hashes,
        "embedded_tables_exact": embedded_table_hashes == EMBEDDED_TABLE_HASHES,
        "documentation": required_doc_phrases,
        "release_workflow": workflow_checks,
        "ci": ci_checks,
        "pass": bool(
            set(versions.values()) == {VERSION}
            and set(dependency_versions.values()) == {f"={VERSION}"}
            and py_cargo["dependencies"]["pyo3"].get("features") == ["abi3-py39"]
            and pyproject["project"]["requires-python"] == ">=3.9"
            and licence_copies_match
            and embedded_table_hashes == EMBEDDED_TABLE_HASHES
            and all(required_doc_phrases.values())
            and all(workflow_checks.values())
            and all(ci_checks.values())
        ),
    }


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if destination not in target.parents and target != destination:
                raise RuntimeError(f"unsafe archive member: {member.name}")
        stream.extractall(destination, members=members, filter="data")


def append_standalone_workspace(manifest: Path) -> None:
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write("\n[workspace]\n")


def package_checks(clone: Path, work: Path, env: dict[str, str]) -> dict:
    package_dir = Path(env["CARGO_TARGET_DIR"]) / "package"
    command(["cargo", "package", "--locked", "--package", "actinv-data"], cwd=clone, env=env)
    command(
        [
            "cargo",
            "package",
            "--no-verify",
            "--exclude-lockfile",
            "--package",
            "actinv-core",
        ],
        cwd=clone,
        env=env,
    )
    command(
        [
            "cargo",
            "package",
            "--no-verify",
            "--exclude-lockfile",
            "--package",
            "actinv-cli",
        ],
        cwd=clone,
        env=env,
    )

    unpacked = work / "unpacked-crates"
    unpacked.mkdir()
    archives = {name: package_dir / f"{name}-{VERSION}.crate" for name in CRATES}
    for archive in archives.values():
        safe_extract(archive, unpacked)

    data = unpacked / f"actinv-data-{VERSION}"
    core = unpacked / f"actinv-core-{VERSION}"
    cli = unpacked / f"actinv-cli-{VERSION}"
    for directory in (core, cli):
        shutil.copy2(clone / "Cargo.lock", directory / "Cargo.lock")
        append_standalone_workspace(directory / "Cargo.toml")

    data_patch = f'patch.crates-io.actinv-data.path="{data}"'
    core_patch = f'patch.crates-io.actinv-core.path="{core}"'
    command(
        ["cargo", "build", "--locked", "--manifest-path", core / "Cargo.toml", "--config", data_patch],
        cwd=work,
        env=env,
    )
    command(
        [
            "cargo",
            "build",
            "--locked",
            "--manifest-path",
            cli / "Cargo.toml",
            "--config",
            data_patch,
            "--config",
            core_patch,
        ],
        cwd=work,
        env=env,
    )
    embedded = {
        name: (data / "data" / name).is_file()
        for name in ("mt_products.json", "fispact_709_groups.json", "fispact_162_groups.json")
    }
    return {
        "archives": sorted(archive.name for archive in archives.values()),
        "data_crate_verified_by_cargo": True,
        "dependent_archives_built": True,
        "dependent_archives_verified_with_local_packaged_dependencies": True,
        "embedded_release_data": embedded,
        "pass": all(embedded.values()),
    }


def python_package_checks(
    clone: Path, work: Path, env: dict[str, str], maturin: str
) -> dict:
    wheel_dir = work / "wheel"
    sdist_dir = work / "sdist"
    wheel_dir.mkdir()
    sdist_dir.mkdir()
    command(
        [maturin, "build", "--release", "--locked", "--out", wheel_dir],
        cwd=clone / "python",
        env=env,
    )
    wheels = sorted(wheel_dir.glob("actinv-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {[path.name for path in wheels]}")
    wheel = wheels[0]
    if not re.fullmatch(r"actinv-1\.0\.0-cp39-abi3-.+\.whl", wheel.name):
        raise RuntimeError(f"wheel does not carry the expected stable ABI tag: {wheel.name}")

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_name = f"actinv-{VERSION}.dist-info/METADATA"
        metadata = email.parser.Parser().parsestr(archive.read(metadata_name).decode())
        license_names = {
            f"actinv-{VERSION}.dist-info/licenses/LICENSE-MIT",
            f"actinv-{VERSION}.dist-info/licenses/LICENSE-APACHE",
        }
        has_sbom = any(name.endswith(".cyclonedx.json") for name in names)
        extracted = work / "wheel-import"
        archive.extractall(extracted)

    import_env = dict(env)
    import_env["PYTHONPATH"] = str(extracted)
    imported = command(
        [
            sys.executable,
            "-c",
            "import actinv; assert actinv.__version__ == '1.0.0'; "
            "assert all(hasattr(actinv, n) for n in ('run','validate','broaden','cram_step'))",
        ],
        cwd=work,
        env=import_env,
    )

    command([maturin, "sdist", "--out", sdist_dir], cwd=clone / "python", env=env)
    sdists = sorted(sdist_dir.glob("actinv-*.tar.gz"))
    if len(sdists) != 1:
        raise RuntimeError(f"expected one source archive, found {[path.name for path in sdists]}")
    with tarfile.open(sdists[0], "r:gz") as archive:
        sdist_names = set(archive.getnames())
    prefix = f"actinv-{VERSION}"
    required_sdist = {
        f"{prefix}/python/Cargo.toml",
        f"{prefix}/python/src/lib.rs",
        f"{prefix}/crates/actinv-core/Cargo.toml",
        f"{prefix}/crates/actinv-data/Cargo.toml",
        f"{prefix}/crates/actinv-data/data/mt_products.json",
        f"{prefix}/LICENSE-MIT",
        f"{prefix}/LICENSE-APACHE",
    }
    checks = {
        "wheel_version": metadata["Version"] == VERSION,
        "wheel_requires_python": metadata["Requires-Python"] == ">=3.9",
        "wheel_license_expression": metadata["License-Expression"] == "MIT OR Apache-2.0",
        "wheel_abi": "cp39-abi3" in wheel.name,
        "wheel_licenses": license_names.issubset(names),
        "wheel_sbom": has_sbom,
        "wheel_import": imported.returncode == 0,
        "sdist_complete": required_sdist.issubset(sdist_names),
    }
    return {
        "wheel": "actinv-1.0.0-cp39-abi3-<platform>.whl",
        "source_distribution": "actinv-1.0.0.tar.gz",
        "checks": checks,
        "pass": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-only", action="store_true")
    arguments = parser.parse_args()

    source = source_checks(ROOT)
    if arguments.source_only:
        print(json.dumps(source, indent=1, sort_keys=True))
        return 0 if source["pass"] else 1
    if not source["pass"]:
        raise RuntimeError("source release checks failed")

    dirty = command(["git", "status", "--porcelain"], cwd=ROOT, env=dict(os.environ)).stdout
    if dirty:
        raise RuntimeError("P12-G5 full run requires a clean committed source tree")

    maturin = os.environ.get("ACTINV_MATURIN") or shutil.which("maturin")
    if not maturin:
        raise RuntimeError("maturin is required; set ACTINV_MATURIN to the pinned executable")

    work_root = Path(os.environ.get("ACTINV_P12_G5_WORK_ROOT", tempfile.gettempdir()))
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="actinv-p12-g5-", dir=work_root) as temporary:
        work = Path(temporary)
        clone = work / "clone"
        env = dict(os.environ)
        env["CARGO_BUILD_JOBS"] = "1"
        env["ACTINV_CI_WORKERS"] = "1"
        env["CARGO_TARGET_DIR"] = str(clone / "target")
        command(["git", "clone", "--quiet", "--no-hardlinks", ROOT, clone], cwd=work, env=env)

        commands = [
            ("rustfmt", ["cargo", "fmt", "--all", "--", "--check"]),
            ("check", ["cargo", "check", "--workspace", "--all-targets", "--all-features"]),
            (
                "clippy",
                [
                    "cargo",
                    "clippy",
                    "--workspace",
                    "--all-targets",
                    "--all-features",
                    "--",
                    "-D",
                    "warnings",
                ],
            ),
            ("test", ["cargo", "test", "--workspace", "--all-targets", "--all-features"]),
            ("release build", ["cargo", "build", "--release", "--workspace", "--locked"]),
            ("dependency declaration", [sys.executable, "controls/check_dependencies.py"]),
            ("release notes", [sys.executable, "controls/check_release_notes.py"]),
            ("prior verdict evidence", [sys.executable, "controls/check_prior_verdicts.py"]),
        ]
        command_results = {}
        for name, argv in commands:
            command(argv, cwd=clone, env=env)
            command_results[name] = True

        version = command([clone / "target/release/actinv", "--version"], cwd=clone, env=env)
        package = package_checks(clone, work, env)
        python_package = python_package_checks(clone, work, env, maturin)
        env["ACTINV_PYTHON_LIBRARY"] = str(clone / "target/release/libactinv.so")

        command([sys.executable, "controls/ci_end_to_end.py"], cwd=clone, env=env)
        command([sys.executable, "controls/g1_p12_radiological.py"], cwd=clone, env=env)
        command(
            [sys.executable, "controls/g3_p12_parser_fuzz.py", "--smoke"],
            cwd=clone,
            env=env,
        )
        command([sys.executable, "controls/g1_self_contained.py"], cwd=clone, env=env)
        command_results.update(
            {
                "end_to_end_cli_python": True,
                "p12_radiological_ci_subset": True,
                "p12_input_reliability_ci_subset": True,
                "self_contained_clone": True,
            }
        )

        clone_diff = command(["git", "status", "--porcelain"], cwd=clone, env=env).stdout
        only_expected_results = all(
            line[3:] in {
                "results/check_dependencies.json",
                "results/check_release_notes.json",
                "results/ci_end_to_end.json",
                "results/g1_p12_radiological.json",
                "results/g1_self_contained.json",
            }
            for line in clone_diff.splitlines()
        )

    result = {
        "gate": "P12-G5",
        "schema": "actinv-p12-g5-result-1",
        "source": source,
        "commands": command_results,
        "standalone_binary": {
            "version_output": version.stdout.strip(),
            "pass": version.stdout.strip() == f"actinv {VERSION}",
        },
        "crate_packages": package,
        "python_package": python_package,
        "clean_clone": {
            "source_was_clean": True,
            "only_expected_control_results_changed": only_expected_results,
        },
    }
    result["pass"] = bool(
        source["pass"]
        and all(command_results.values())
        and result["standalone_binary"]["pass"]
        and package["pass"]
        and python_package["pass"]
        and only_expected_results
    )
    RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
