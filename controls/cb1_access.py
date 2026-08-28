#!/usr/bin/env python3
"""CB1-G0: verify the benchmark inputs, executable access, and host inventory."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "cb1_access.json"
SOURCE_PIN = "19afc18d1f65d696512d52d848ec0a145e67534e"


def configured(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


DATA = Path.home() / "nuclear-data"
PATHS = {
    "activation_library": configured(
        "ACTINV_LIBRARY", DATA / "tendl-2025/builds/full/neutron.n.p10.npz"
    ),
    "activation_index": configured(
        "ACTINV_LIBRARY_INDEX", DATA / "tendl-2025/builds/full/neutron.n.p10_index.json"
    ),
    "decay_primary": configured(
        "ACTINV_ENDF_DECAY", DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
    ),
    "decay_fallback": configured(
        "ACTINV_JEFF_DECAY", DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat"
    ),
    "fns_archive": configured("ACTINV_FNS_ARCHIVE", DATA / "conderc-fns/fns.zip"),
    "fission_archive": configured("ACTINV_FISSION_ARCHIVE", DATA / "conderc-fission.zip"),
}
EXPECTED = {
    "activation_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "activation_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    "fns_archive": "ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d",
    "fission_archive": "30756fef88c0f3637246bf8ad8ef1fc5397a3f784e5408f2861bc474993e74a5",
}
ALARA_SOURCE = configured("ACTINV_ALARA_SOURCE", DATA / "alara-2.9.2")
ALARA_BINARY = configured(
    "ACTINV_ALARA_BIN", DATA / "alara-2.9.2-build/src/alara"
)
OPENMC_PYTHON = configured(
    "ACTINV_OPENMC_PYTHON", Path.home() / ".venvs/w003env/bin/python"
)
ACTINV_BINARY = configured("ACTINV_BIN", ROOT / "target/release/actinv")
ACTINV_MODULE = configured(
    "ACTINV_PYTHON_MODULE", ROOT / "python/target/release/libactinv.so"
)
RUSTC = configured(
    "RUSTC", Path.home() / ".rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/rustc"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command(arguments: list[str | Path], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in arguments],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )


def version(arguments: list[str | Path], pattern: str) -> tuple[str, bool]:
    run = command(arguments)
    text = (run.stdout + run.stderr).strip()
    return text.splitlines()[0] if text else "", run.returncode == 0 and re.search(pattern, text) is not None


def memory_bytes() -> int | None:
    try:
        line = next(
            row for row in Path("/proc/meminfo").read_text().splitlines() if row.startswith("MemTotal:")
        )
        return int(line.split()[1]) * 1024
    except (OSError, StopIteration, ValueError):
        return None


def cpu_model() -> str:
    try:
        line = next(
            row for row in Path("/proc/cpuinfo").read_text().splitlines() if row.startswith("model name")
        )
        return line.split(":", 1)[1].strip()
    except (OSError, StopIteration, IndexError):
        return platform.processor() or "unavailable"


def main() -> None:
    identities = {}
    for role, path in PATHS.items():
        actual = sha256(path) if path.is_file() else None
        identities[role] = {
            "expected_sha256": EXPECTED[role],
            "actual_sha256": actual,
            "bytes": path.stat().st_size if path.is_file() else None,
            "matches": actual == EXPECTED[role],
        }

    source_object = command(["git", "cat-file", "-e", f"{SOURCE_PIN}^{{commit}}"])
    product_diff = command(
        [
            "git",
            "diff",
            "--quiet",
            SOURCE_PIN,
            "--",
            "Cargo.toml",
            "Cargo.lock",
            "crates",
            "python/Cargo.toml",
            "python/Cargo.lock",
            "python/src",
            "data",
        ]
    )
    head = command(["git", "rev-parse", "HEAD"]).stdout.strip()

    actinv_version, actinv_ok = version([ACTINV_BINARY, "--version"], r"^actinv 1\.0\.0$")
    alara_version, alara_version_ok = version([ALARA_BINARY, "-V"], r"ALARA 2\.9\.2")
    alara_commit_run = command(["git", "-C", ALARA_SOURCE, "rev-parse", "HEAD"])
    alara_commit = alara_commit_run.stdout.strip()
    alara_ok = alara_version_ok and alara_commit == "faa5b330460fe865e38fc788f1b792ea33d13d1b"

    module_script = (
        "import json,openmc,numpy,scipy; "
        "print(json.dumps({'openmc':openmc.__version__,'numpy':numpy.__version__,'scipy':scipy.__version__}))"
    )
    module_run = command([OPENMC_PYTHON, "-c", module_script])
    try:
        module_versions = json.loads(module_run.stdout)
    except json.JSONDecodeError:
        module_versions = {}
    openmc_ok = module_run.returncode == 0 and module_versions == {
        "openmc": "0.15.3",
        "numpy": "2.5.2",
        "scipy": "1.18.0",
    }

    rustc_run = command([RUSTC, "-Vv"]) if RUSTC.is_file() else None
    rustc_version = rustc_run.stdout.strip() if rustc_run and rustc_run.returncode == 0 else "unavailable"
    thread_variables = {
        name: os.environ.get(name, "unset")
        for name in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "RAYON_NUM_THREADS",
        )
    }
    access = {
        "ACTINV 1.0.0": {
            "class": "executed",
            "detail": "standalone CLI and current Rust Python module are present",
            "version": actinv_version,
            "executable_sha256": sha256(ACTINV_BINARY) if ACTINV_BINARY.is_file() else None,
            "python_module_sha256": sha256(ACTINV_MODULE) if ACTINV_MODULE.is_file() else None,
        },
        "ALARA 2.9.2": {
            "class": "executed",
            "detail": "official pinned source and locally built executable are present",
            "version": alara_version,
            "source_commit": alara_commit,
            "executable_sha256": sha256(ALARA_BINARY) if ALARA_BINARY.is_file() else None,
        },
        "OpenMC 0.15.3 depletion": {
            "class": "executed",
            "detail": "OpenMC CRAM48 is available through the recorded Python environment",
            "versions": module_versions,
            "python_sha256": sha256(OPENMC_PYTHON) if OPENMC_PYTHON.is_file() else None,
        },
        "FISPACT-II": {
            "class": "published-reference",
            "detail": "no executable supplied or found; frozen CoNDERC outputs identify FISPACT-II Release 4.0 and TENDL-2017",
            "fresh_execution": "not-available",
        },
        "SCALE/ORIGEN": {
            "class": "not-available",
            "detail": "no licensed executable or suitable executable-level public result was supplied",
        },
    }
    host = {
        "platform": platform.platform(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "cpu_model": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "affinity_cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "memory_bytes": memory_bytes(),
        "python": platform.python_version(),
        "rustc": rustc_version,
        "thread_environment_at_inventory": thread_variables,
    }
    checks = {
        "source_pin_exists": source_object.returncode == 0,
        "product_source_unchanged_since_pin": product_diff.returncode == 0,
        "all_external_identities_match": all(row["matches"] for row in identities.values()),
        "actinv_1_0_0_executable": actinv_ok and ACTINV_MODULE.is_file(),
        "alara_2_9_2_pinned": alara_ok,
        "openmc_environment_pinned": openmc_ok,
        "fns_extracted_family_present": len(list((PATHS["fns_archive"].parent / "fns").glob("*/*.exp"))) == 132,
    }
    output = {
        "schema": "actinv-cb1-access-1",
        "source_pin": SOURCE_PIN,
        "execution_head": head,
        "access": access,
        "identities": identities,
        "host": host,
        "checks": checks,
        "pass": all(checks.values()),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
