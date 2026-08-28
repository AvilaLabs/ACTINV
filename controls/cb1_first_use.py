#!/usr/bin/env python3
"""CB1-G5: clean first-use exercises and planted diagnostic probes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/cb1_first_use.json"
CATALOG = ROOT / "crates/actinv-cli/data/actinv-data-catalog-v1.0.0.json"
RELEASE_SESSION = ROOT / "results/session_v1_release.json"
ALARA_SOURCE = Path(
    os.environ.get("ACTINV_ALARA_SOURCE", Path.home() / "nuclear-data/alara-2.9.2")
).resolve()
BASE_PYTHON = Path(os.environ.get("ACTINV_CB1_BASE_PYTHON", sys.executable)).resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def command(
    arguments: list[str | Path],
    *,
    cwd: Path,
    timeout: float = 1200.0,
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.perf_counter_ns()
    run = subprocess.run(
        [str(item) for item in arguments],
        cwd=cwd,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return run, (time.perf_counter_ns() - started) * 1.0e-9


def require(run: subprocess.CompletedProcess[str], label: str) -> None:
    if run.returncode != 0:
        raise RuntimeError(f"{label} failed ({run.returncode}): {(run.stdout + run.stderr)[-3000:]}")


def sanitized(text: str, scratch: Path) -> str:
    return text.replace(str(scratch), "<scratch>").replace(str(Path.home()), "<home>")[-600:]


def diagnostic(
    arguments: list[str | Path],
    *,
    cwd: Path,
    scratch: Path,
    expected: list[str],
) -> dict[str, object]:
    run, elapsed = command(arguments, cwd=cwd, timeout=60.0)
    message = sanitized((run.stdout + run.stderr).strip(), scratch)
    lower = message.lower()
    return {
        "exit_code": run.returncode,
        "nonzero": run.returncode != 0,
        "elapsed_s": elapsed,
        "expected_terms": expected,
        "names_offending_item": any(term.lower() in lower for term in expected),
        "message_tail": message,
    }


def installed_actinv_bytes(venv: Path) -> int:
    files = []
    for item in venv.rglob("*"):
        if not item.is_file():
            continue
        normalized = str(item).lower()
        if "/actinv/" in normalized or "actinv-1.0.0.dist-info" in normalized or item.name == "actinv":
            files.append(item)
    return sum(item.stat().st_size for item in files)


def actinv_exercise(scratch: Path) -> dict[str, object]:
    scratch.mkdir(parents=True, exist_ok=True)
    downloads = scratch / "downloads"
    downloads.mkdir()
    download, download_s = command(
        [
            BASE_PYTHON,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--only-binary=:all:",
            "--no-cache-dir",
            "--dest",
            downloads,
            "actinv==1.0.0",
        ],
        cwd=scratch,
    )
    require(download, "PyPI artifact download")
    wheels = list(downloads.glob("actinv-1.0.0-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one ACTINV wheel, found {[path.name for path in wheels]}")
    wheel = wheels[0]
    public_files = json.loads(RELEASE_SESSION.read_text(encoding="utf-8"))["pypi"]["files"]
    published = public_files.get(wheel.name)
    if published is None:
        raise RuntimeError(f"downloaded wheel {wheel.name} is absent from the release record")

    venv = scratch / "venv"
    created, venv_s = command([BASE_PYTHON, "-m", "venv", venv], cwd=scratch)
    require(created, "clean Python virtual environment")
    pip = venv / "bin/pip"
    python = venv / "bin/python"
    actinv = venv / "bin/actinv"
    before_install = tree_bytes(venv)
    installed, install_s = command(
        [pip, "install", "--no-deps", "--no-cache-dir", "actinv==1.0.0"], cwd=scratch
    )
    require(installed, "one-line PyPI install")
    after_install = tree_bytes(venv)
    version, version_s = command([actinv, "--version"], cwd=scratch)
    require(version, "installed ACTINV version")
    imported, import_s = command(
        [python, "-c", "import actinv; print(actinv.__version__)"], cwd=scratch
    )
    require(imported, "installed ACTINV import")
    base_version, _ = command([BASE_PYTHON, "--version"], cwd=scratch)
    require(base_version, "base Python version")

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    bundle = next(item for item in catalog["bundles"] if item["id"] == "tendl-2025-neutron")
    artifacts = {item["id"]: item for item in catalog["artifacts"]}
    bundle_artifacts = [artifacts[name] for name in bundle["artifacts"]]
    declared_network_bytes = sum(item["source"]["bytes"] for item in bundle_artifacts)
    declared_installed_bytes = sum(item["bytes"] for item in bundle_artifacts)
    data_output = scratch / "actinv-data"
    data_root = data_output / "v1.0.0"
    fetched, fetch_s = command(
        [actinv, "data", "fetch", "tendl-2025-neutron", "--output", data_output], cwd=scratch
    )
    require(fetched, "default data fetch")
    verified, verify_s = command(
        [actinv, "data", "verify", "tendl-2025-neutron", "--output", data_output], cwd=scratch
    )
    require(verified, "default data verification")
    actual_artifacts = {}
    for artifact in bundle_artifacts:
        path = data_root / artifact["path"]
        actual_artifacts[artifact["id"]] = {
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": sha256(path) if path.is_file() else None,
            "matches": path.is_file()
            and path.stat().st_size == artifact["bytes"]
            and sha256(path) == artifact["sha256"],
        }

    example_result = scratch / "example.result.json"
    example, example_s = command(
        [actinv, "run", ROOT / "examples/fns_fe_5min.json", example_result], cwd=scratch
    )
    require(example, "public example")
    calculated = json.loads(example_result.read_text(encoding="utf-8"))

    diagnostic_dir = scratch / "actinv-diagnostics"
    diagnostic_dir.mkdir()
    base = json.loads((ROOT / "examples/fns_fe_5min.json").read_text(encoding="utf-8"))
    base["library"]["path"] = str(data_root / "activation/tendl-2025-neutron-709g.npz")
    base["decay"] = {
        "primary": str(data_root / "decay/endf-b-viii-0_decay.dat"),
        "fallback": str(data_root / "decay/jeff-3-3_decay.dat"),
    }

    missing = json.loads(json.dumps(base))
    missing["library"] = {"path": str(diagnostic_dir / "missing-library.npz")}
    missing_path = diagnostic_dir / "missing.json"
    missing_path.write_text(json.dumps(missing), encoding="utf-8")
    hash_mismatch = json.loads(json.dumps(base))
    hash_mismatch["library"]["sha256"] = "0" * 64
    hash_path = diagnostic_dir / "hash-mismatch.json"
    hash_path.write_text(json.dumps(hash_mismatch), encoding="utf-8")
    malformed = json.loads(json.dumps(base))
    malformed["unexpected_field"] = True
    malformed_path = diagnostic_dir / "malformed.json"
    malformed_path.write_text(json.dumps(malformed), encoding="utf-8")
    diagnostics = {
        "missing_file": diagnostic(
            [actinv, "run", missing_path, diagnostic_dir / "missing.result.json"],
            cwd=diagnostic_dir,
            scratch=scratch,
            expected=["missing-library.npz", "No such file"],
        ),
        "inconsistent_hash": diagnostic(
            [actinv, "run", hash_path, diagnostic_dir / "hash.result.json"],
            cwd=diagnostic_dir,
            scratch=scratch,
            expected=["SHA-256 mismatch", "tendl-2025-neutron-709g.npz"],
        ),
        "malformed_input": diagnostic(
            [actinv, "validate", malformed_path],
            cwd=diagnostic_dir,
            scratch=scratch,
            expected=["unexpected_field", "unknown field"],
        ),
    }
    return {
        "access": "executed",
        "documented_user_commands": [
            "pip install actinv",
            "actinv data fetch",
            "actinv run examples/fns_fe_5min.json result.json",
        ],
        "user_command_count_through_first_result": 3,
        "artifact_measurement": {
            "download_elapsed_s": download_s,
            "wheel_filename": wheel.name,
            "bytes": wheel.stat().st_size,
            "sha256": sha256(wheel),
            "matches_published_release_record": wheel.stat().st_size == published["bytes"]
            and sha256(wheel) == published["sha256"],
        },
        "clean_environment": {
            "base_python": (base_version.stdout + base_version.stderr).strip(),
            "venv_creation_s": venv_s,
            "one_line_pypi_install_s": install_s,
            "version_probe_s": version_s,
            "import_probe_s": import_s,
            "version": version.stdout.strip(),
            "imported_version": imported.stdout.strip(),
            "venv_regular_file_bytes_before_install": before_install,
            "venv_regular_file_bytes_after_install": after_install,
            "regular_file_byte_delta": after_install - before_install,
            "actinv_named_installed_bytes": installed_actinv_bytes(venv),
        },
        "default_data": {
            "fetch_elapsed_s": fetch_s,
            "verify_elapsed_s": verify_s,
            "declared_network_bytes": declared_network_bytes,
            "declared_installed_bytes": declared_installed_bytes,
            "actual_installed_tree_bytes": tree_bytes(data_root),
            "artifacts": actual_artifacts,
            "all_artifacts_match": all(row["matches"] for row in actual_artifacts.values()),
        },
        "first_result": {
            "elapsed_s": example_s,
            "steps": len(calculated["steps"]),
            "mode": calculated["mode"],
            "states_total": calculated["total_states"],
            "states_pruned": calculated["pruned_states"],
            "first_cooling_W_per_g": calculated["steps"][1]["heat_W_per_g"]["total"],
        },
        "diagnostics": diagnostics,
    }


def source_bytes() -> int:
    listed = subprocess.run(
        ["git", "-C", str(ALARA_SOURCE), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if listed.returncode != 0:
        return tree_bytes(ALARA_SOURCE)
    return sum(
        (ALARA_SOURCE / relative.decode()).stat().st_size
        for relative in listed.stdout.split(b"\0")
        if relative and (ALARA_SOURCE / relative.decode()).is_file()
    )


def alara_exercise(scratch: Path) -> dict[str, object]:
    tools_venv = scratch / "build-tools"
    created, tools_venv_s = command([BASE_PYTHON, "-m", "venv", tools_venv], cwd=scratch)
    require(created, "ALARA build-tools environment")
    pip = tools_venv / "bin/pip"
    cmake = tools_venv / "bin/cmake"
    cmake_install, cmake_install_s = command(
        [pip, "install", "--no-cache-dir", "cmake==3.31.6"], cwd=scratch
    )
    require(cmake_install, "CMake build dependency")
    cmake_version, _ = command([cmake, "--version"], cwd=scratch)
    require(cmake_version, "CMake version")

    build = scratch / "alara-build"
    install = scratch / "alara-install"
    configured, configure_s = command(
        [
            cmake,
            "-S",
            ALARA_SOURCE,
            "-B",
            build,
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={install}",
        ],
        cwd=scratch,
    )
    require(configured, "clean ALARA configure")
    built, build_s = command([cmake, "--build", build, "--parallel", "1"], cwd=scratch)
    require(built, "clean ALARA build")
    installed, install_s = command([cmake, "--install", build], cwd=scratch)
    require(installed, "clean ALARA install")
    candidates = [install / "bin/alara", build / "src/alara"]
    binary = next((path for path in candidates if path.is_file()), None)
    if binary is None:
        raise FileNotFoundError("clean ALARA build did not produce an executable")
    version, version_s = command([binary, "-V"], cwd=scratch)
    require(version, "clean ALARA version")

    sample = scratch / "alara-sample"
    shutil.copytree(ALARA_SOURCE / "sample", sample)
    (sample / "output").mkdir(exist_ok=True)
    (sample / "dump_files").mkdir(exist_ok=True)
    converted, conversion_s = command([binary, "sample1"], cwd=sample)
    require(converted, "ALARA sample data conversion")
    solved, solve_s = command([binary, "sample3"], cwd=sample)
    require(solved, "ALARA sample solve")

    diagnostic_dir = scratch / "alara-diagnostics"
    diagnostic_dir.mkdir()
    malformed_path = diagnostic_dir / "malformed"
    malformed_path.write_text("definitely_not_an_alara_keyword\n", encoding="utf-8")
    isolated_sample = diagnostic_dir / "isolated-sample3"
    shutil.copy2(ALARA_SOURCE / "sample/sample3", isolated_sample)
    diagnostics = {
        "missing_file": diagnostic(
            [binary, "definitely-missing-input"],
            cwd=diagnostic_dir,
            scratch=scratch,
            expected=["definitely-missing-input", "Unable to open"],
        ),
        "malformed_input": diagnostic(
            [binary, malformed_path.name],
            cwd=diagnostic_dir,
            scratch=scratch,
            expected=["definitely_not_an_alara_keyword", "Invalid token"],
        ),
        "inconsistent_data": diagnostic(
            [binary, isolated_sample.name],
            cwd=diagnostic_dir,
            scratch=scratch,
            expected=["data", "Unable to open", "myElelib"],
        ),
    }
    commit = subprocess.run(
        ["git", "-C", str(ALARA_SOURCE), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    ).stdout.strip()
    return {
        "access": "executed",
        "documented_build_commands": [
            "git clone https://github.com/svalinn/ALARA",
            "cmake -S ALARA -B build -DCMAKE_INSTALL_PREFIX=install",
            "cmake --build build",
            "cmake --install build",
        ],
        "build_command_count_excluding_dependency_install": 4,
        "prerequisites": ["C++ compiler", "CMake", "Ninja or Make"],
        "source_commit": commit,
        "source_tracked_bytes": source_bytes(),
        "build_dependencies": {
            "tools_venv_creation_s": tools_venv_s,
            "cmake_install_s": cmake_install_s,
            "cmake_version": cmake_version.stdout.splitlines()[0],
            "tools_environment_bytes": tree_bytes(tools_venv),
        },
        "clean_build": {
            "configure_s": configure_s,
            "build_one_job_s": build_s,
            "install_s": install_s,
            "version_probe_s": version_s,
            "version": version.stdout.strip(),
            "installed_tree_bytes": tree_bytes(install),
        },
        "first_sample": {
            "conversion_s": conversion_s,
            "solve_s": solve_s,
            "conversion_succeeded": converted.returncode == 0,
            "solve_succeeded": solved.returncode == 0,
            "ten_pulses_named": "num_pulses_per_level: [10]" in solved.stdout + solved.stderr,
            "note": "official sample data are explicitly truncated and unsuitable for real calculations",
        },
        "diagnostics": diagnostics,
    }


def diagnostics_pass(product: dict[str, object]) -> bool:
    return all(
        row["nonzero"] and row["names_offending_item"] for row in product["diagnostics"].values()
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cb1u-") as directory:
        scratch = Path(directory)
        actinv = actinv_exercise(scratch / "actinv")
        alara_root = scratch / "alara"
        alara_root.mkdir()
        alara = alara_exercise(alara_root)
    checks = {
        "actinv_public_artifact": actinv["artifact_measurement"]["matches_published_release_record"],
        "actinv_version": actinv["clean_environment"]["version"] == "actinv 1.0.0"
        and actinv["clean_environment"]["imported_version"] == "1.0.0",
        "actinv_data": actinv["default_data"]["all_artifacts_match"],
        "actinv_example": actinv["first_result"]["steps"] == 21,
        "actinv_diagnostics": diagnostics_pass(actinv),
        "alara_source": alara["source_commit"] == "faa5b330460fe865e38fc788f1b792ea33d13d1b",
        "alara_version": "ALARA 2.9.2" in alara["clean_build"]["version"],
        "alara_sample": alara["first_sample"]["conversion_succeeded"]
        and alara["first_sample"]["solve_succeeded"]
        and alara["first_sample"]["ten_pulses_named"],
        "alara_diagnostics": diagnostics_pass(alara),
    }
    output = {
        "schema": "actinv-cb1-first-use-1",
        "ACTINV": actinv,
        "ALARA": alara,
        "FISPACT-II": {
            "access": "not-available",
            "detail": "no licensed executable was supplied; installation was not executed",
        },
        "SCALE/ORIGEN": {
            "access": "not-available",
            "detail": "no licensed executable was supplied; installation was not executed",
        },
        "OpenMC": {
            "access": "not-applicable",
            "detail": "CB1 uses an existing OpenMC environment only as a depletion/numerical anchor",
        },
        "interpretation": [
            "times are one run on the recorded CB1 host and include network conditions where applicable",
            "ACTINV's data step installs production public data; ALARA's official sample is deliberately truncated",
            "command counts remain separate from diagnostic observations; there is no ease-of-use total",
        ],
        "checks": checks,
        "pass": all(checks.values()),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ACTINV": {
                    "install_s": actinv["clean_environment"]["one_line_pypi_install_s"],
                    "wheel_bytes": actinv["artifact_measurement"]["bytes"],
                    "data_fetch_s": actinv["default_data"]["fetch_elapsed_s"],
                    "data_network_bytes": actinv["default_data"]["declared_network_bytes"],
                    "first_result_s": actinv["first_result"]["elapsed_s"],
                },
                "ALARA": {
                    "cmake_install_s": alara["build_dependencies"]["cmake_install_s"],
                    "configure_s": alara["clean_build"]["configure_s"],
                    "build_s": alara["clean_build"]["build_one_job_s"],
                    "sample_conversion_s": alara["first_sample"]["conversion_s"],
                    "sample_solve_s": alara["first_sample"]["solve_s"],
                },
                "checks": checks,
                "pass": output["pass"],
            },
            indent=1,
        )
    )
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
