#!/usr/bin/env python3
"""P16 G1/G2: zero-cost quantity inventory, compile rejection and legacy API control."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g1_p16_quantities.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P16_PROTOCOL.md"
PROTOCOL_SHA256 = "58d9debbb3e8892ab0ad0bf3642cba5fc1afa31ffbc1079cd26095c5d0e2ce19"
AMENDMENTS = {
    "protocols/ACTINV-P16_AMENDMENT_A.md": "12903283e78171ddd64b07964945f935a45e09879ae701dcadbe4ea51ed99f21",
}
OPENING_COMMIT = "0332779401363d2f39722efe7a0b7218afcfb270"
CARGO = Path(os.environ.get("CARGO", Path.home() / ".cargo" / "bin" / "cargo"))
QUANTITY_SOURCE = ROOT / "crates" / "actinv-core" / "src" / "quantity.rs"
QUANTITY_DOC = ROOT / "docs" / "QUANTITIES.md"
FIXTURES = ROOT / "controls" / "fixtures" / "p16_quantities"
SCALAR_TYPES = (
    "Seconds",
    "ElectronVolts",
    "Kelvin",
    "Grams",
    "AtomsPerGram",
    "ParticleFlux",
    "FluxMultiplier",
    "ParticleFluence",
    "CrossSectionBarns",
    "RatePerBarnSecond",
    "RatePerSecond",
)
FAILURES = {
    "fail_time_energy.rs": ("Seconds", "ElectronVolts"),
    "fail_mass_temperature.rs": ("Grams", "Kelvin"),
    "fail_flux_multiplier.rs": ("ParticleFlux", "FluxMultiplier"),
    "fail_cross_section_rate.rs": ("RatePerBarnSecond", "Seconds"),
    "fail_grams_atoms.rs": ("AtomsPerGram", "Grams"),
    "fail_energy_rate.rs": ("RatePerSecond", "ElectronVolts"),
}
WIRING = {
    "spec_view": (
        ROOT / "crates/actinv-core/src/spec.rs",
        "pub(crate) fn physical_inputs(&self) -> Result<PhysicalInputs, String>",
    ),
    "shared_prepare": (
        ROOT / "crates/actinv-core/src/run.rs",
        "Self::prepare_profiled(spec, &physical, &mut profiler)",
    ),
    "typed_prune": (
        ROOT / "crates/actinv-core/src/run.rs",
        "crate::prune::reachable_physical(",
    ),
    "typed_rate": (
        ROOT / "crates/actinv-core/src/chain.rs",
        "RatePerBarnSecond::from_particle_flux",
    ),
    "typed_cross_section": (
        ROOT / "crates/actinv-core/src/chain.rs",
        "CrossSectionBarns::from_collapsed_kernel",
    ),
}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def command(arguments: list[str | Path], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        cwd=kwargs.pop("cwd", ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=kwargs.pop("timeout", 180),
        check=False,
        **kwargs,
    )


def committed(path: str) -> bytes:
    result = command(["git", "show", f"{OPENING_COMMIT}:{path}"])
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.encode()


def manifest_identity() -> dict[str, object]:
    paths = command(["git", "ls-files", "Cargo.toml", "*/Cargo.toml", "*/*/Cargo.toml", "Cargo.lock"])
    if paths.returncode:
        raise RuntimeError(paths.stderr)
    selected = sorted(path for path in paths.stdout.splitlines() if path)
    rows = []
    for relative in selected:
        current = (ROOT / relative).read_bytes()
        opening = committed(relative)
        rows.append(
            {
                "path": relative,
                "opening_sha256": sha256_bytes(opening),
                "current_sha256": sha256_bytes(current),
                "equal": current == opening,
            }
        )
    return {"files": rows, "pass": bool(rows) and all(row["equal"] for row in rows)}


def source_contract() -> dict[str, object]:
    source = QUANTITY_SOURCE.read_text(encoding="utf-8")
    documentation = QUANTITY_DOC.read_text(encoding="utf-8")
    layouts = {
        name: bool(
            re.search(
                rf"#\[repr\(transparent\)\]\s+pub struct {name}\(f64\);",
                source,
            )
        )
        for name in SCALAR_TYPES
    }
    documented = {name: f"`{name}`" in documentation for name in SCALAR_TYPES}
    blanket_scalar_conversion_absent = not re.search(
        r"impl\s+(?:From<f64>|Into<f64>)\s+for", source
    )
    barn_occurrences = []
    for path in sorted((ROOT / "crates/actinv-core/src").rglob("*.rs")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"1(?:\.0)?e-24", line, flags=re.IGNORECASE):
                barn_occurrences.append(
                    {"path": path.relative_to(ROOT).as_posix(), "line": line_number}
                )
    wiring = {
        name: fragment in path.read_text(encoding="utf-8")
        for name, (path, fragment) in WIRING.items()
    }
    unsafe_blocks = []
    for path in sorted((ROOT / "crates").rglob("*.rs")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bunsafe\s*(?:\{|fn\b|impl\b|trait\b)", line):
                unsafe_blocks.append(
                    {"path": path.relative_to(ROOT).as_posix(), "line": line_number}
                )
    result = {
        "scalar_types": list(SCALAR_TYPES),
        "repr_transparent_private_f64": layouts,
        "documented": documented,
        "blanket_scalar_conversion_absent": blanket_scalar_conversion_absent,
        "barn_factor_occurrences": barn_occurrences,
        "wiring": wiring,
        "unsafe_blocks": unsafe_blocks,
    }
    result["pass"] = bool(
        all(layouts.values())
        and all(documented.values())
        and blanket_scalar_conversion_absent
        and len(barn_occurrences) == 1
        and barn_occurrences[0]["path"] == "crates/actinv-core/src/quantity.rs"
        and all(wiring.values())
        and not unsafe_blocks
    )
    return result


def consumer_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["CARGO_NET_OFFLINE"] = "true"
    environment["CARGO_TARGET_DIR"] = str(ROOT / "target" / "p16-consumer")
    environment["TMPDIR"] = str(ROOT / "target" / "tmp")
    return environment


def diagnostic_codes(stderr: str) -> list[str]:
    return sorted(set(re.findall(r"error\[(E\d{4})\]", stderr)))


def compile_fixtures() -> dict[str, object]:
    temporary_root = ROOT / "target" / "p16-control-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="consumer-", dir=temporary_root) as directory:
        project = Path(directory)
        (project / "src").mkdir()
        (project / "Cargo.toml").write_text(
            "[package]\n"
            "name = \"actinv-p16-consumer\"\n"
            "version = \"0.0.0\"\n"
            "edition = \"2021\"\n\n"
            "[dependencies]\n"
            f"actinv-core = {{ path = {json.dumps(str(ROOT / 'crates/actinv-core'))} }}\n",
            encoding="utf-8",
        )
        main = project / "src" / "main.rs"
        environment = consumer_environment()

        positive_source = (FIXTURES / "pass.rs").read_text(encoding="utf-8")
        main.write_text(positive_source, encoding="utf-8")
        positive = command([CARGO, "run", "--quiet"], cwd=project, env=environment)
        positive_row = {
            "returncode": positive.returncode,
            "stdout": positive.stdout.strip(),
            "stderr_sha256": sha256_bytes(positive.stderr.encode()),
            "pass": positive.returncode == 0
            and positive.stdout.strip() == "p16-quantity-pass",
        }

        legacy_source = (FIXTURES / "legacy.rs").read_text(encoding="utf-8")
        main.write_text(legacy_source, encoding="utf-8")
        legacy = command([CARGO, "check", "--quiet"], cwd=project, env=environment)
        legacy_row = {
            "returncode": legacy.returncode,
            "stderr_sha256": sha256_bytes(legacy.stderr.encode()),
            "pass": legacy.returncode == 0,
        }

        failure_rows = {}
        for filename, expected_types in FAILURES.items():
            source = (FIXTURES / filename).read_text(encoding="utf-8")
            main.write_text(source, encoding="utf-8")
            failed = command([CARGO, "check", "--quiet"], cwd=project, env=environment)
            codes = diagnostic_codes(failed.stderr)
            expected_named = all(name in failed.stderr for name in expected_types)
            failure_rows[filename] = {
                "returncode": failed.returncode,
                "diagnostic_codes": codes,
                "expected_types": list(expected_types),
                "expected_types_named": expected_named,
                "diagnostic_sha256": sha256_bytes(failed.stderr.encode()),
                "pass": failed.returncode != 0
                and bool(set(codes) & {"E0277", "E0308"})
                and expected_named,
            }

    return {
        "positive": positive_row,
        "legacy": legacy_row,
        "compile_fail": failure_rows,
        "pass": positive_row["pass"]
        and legacy_row["pass"]
        and len(failure_rows) >= 6
        and all(row["pass"] for row in failure_rows.values()),
    }


def rust_checks() -> dict[str, object]:
    environment = consumer_environment()
    commands = {
        "quantity_tests": [
            CARGO,
            "test",
            "-p",
            "actinv-core",
            "quantity::tests",
            "--all-features",
        ],
        "doctests": [CARGO, "test", "-p", "actinv-core", "--doc", "--all-features"],
    }
    rows = {}
    for name, arguments in commands.items():
        completed = command(arguments, env=environment)
        rows[name] = {
            "returncode": completed.returncode,
            "stdout_sha256": sha256_bytes(completed.stdout.encode()),
            "stderr_sha256": sha256_bytes(completed.stderr.encode()),
            "pass": completed.returncode == 0,
        }
    return {"commands": rows, "pass": all(row["pass"] for row in rows.values())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="run the control without replacing the committed evidence",
    )
    arguments = parser.parse_args()
    protocol_actual = sha256(PROTOCOL)
    hash_log = (ROOT / "protocols" / "protocol_hash.txt").read_text(encoding="utf-8")
    protocol = {
        "path": PROTOCOL.relative_to(ROOT).as_posix(),
        "expected_sha256": PROTOCOL_SHA256,
        "actual_sha256": protocol_actual,
        "logged": f"{PROTOCOL_SHA256}  protocols/ACTINV-P16_PROTOCOL.md" in hash_log,
        "amendments": {
            relative: {
                "expected_sha256": expected,
                "actual_sha256": sha256(ROOT / relative),
                "logged": f"{expected}  {relative}" in hash_log,
            }
            for relative, expected in AMENDMENTS.items()
        },
    }
    for row in protocol["amendments"].values():
        row["pass"] = row["actual_sha256"] == row["expected_sha256"] and row["logged"]
    protocol["pass"] = (
        protocol_actual == PROTOCOL_SHA256
        and protocol["logged"]
        and all(row["pass"] for row in protocol["amendments"].values())
    )
    compiler = command([CARGO, "--version"])
    source = source_contract()
    manifests = manifest_identity()
    fixtures = compile_fixtures()
    rust = rust_checks()
    output = {
        "schema": "actinv-p16-quantities-1",
        "gate": "P16-G1-G2",
        "opening_commit": OPENING_COMMIT,
        "cargo": compiler.stdout.strip(),
        "protocol": protocol,
        "quantity_source_sha256": sha256(QUANTITY_SOURCE),
        "quantity_document_sha256": sha256(QUANTITY_DOC),
        "source_contract": source,
        "dependency_manifests": manifests,
        "consumer_fixtures": fixtures,
        "rust_checks": rust,
    }
    output["pass"] = bool(
        protocol["pass"]
        and compiler.returncode == 0
        and source["pass"]
        and manifests["pass"]
        and fixtures["pass"]
        and rust["pass"]
    )
    if not arguments.no_write:
        RESULT.parent.mkdir(exist_ok=True)
        RESULT.write_text(
            json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
