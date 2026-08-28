#!/usr/bin/env python3
"""Install one ACTINV wheel in an isolated environment and exercise both entry points."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import venv


VERSION = "1.0.0"


def command(arguments: list[str | Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path, help="one wheel or a directory containing exactly one wheel")
    arguments = parser.parse_args()
    artifact = arguments.artifact.resolve()
    wheels = sorted(artifact.glob("actinv-*.whl")) if artifact.is_dir() else [artifact]
    if len(wheels) != 1:
        parser.error(f"expected exactly one ACTINV wheel, found {[path.name for path in wheels]}")
    wheel = wheels[0]
    if not wheel.is_file() or wheel.suffix != ".whl":
        parser.error(f"wheel does not exist: {wheel}")

    with tempfile.TemporaryDirectory(prefix="actinv-wheel-smoke-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        if (environment / "Scripts").is_dir():
            python = environment / "Scripts" / "python.exe"
            executable = environment / "Scripts" / "actinv.exe"
        else:
            python = environment / "bin" / "python"
            executable = environment / "bin" / "actinv"

        command([python, "-m", "pip", "install", "--no-deps", wheel])
        imported = command(
            [
                python,
                "-c",
                "import actinv; assert actinv.__version__ == '1.0.0'; "
                "assert all(hasattr(actinv, name) for name in "
                "('run', 'validate', 'broaden', 'cram_step'))",
            ]
        )
        version = command([executable, "--version"])
        listing = command([executable, "data", "list"])
        manifest = json.loads(command([executable, "data", "manifest"]).stdout)

    checks = {
        "import": imported.returncode == 0,
        "version": version.stdout.strip() == f"actinv {VERSION}",
        "data_list": "ACTINV data catalog v1.0.0" in listing.stdout,
        "data_manifest": manifest.get("catalog_version") == VERSION,
    }
    print(json.dumps({"wheel": wheel.name, "checks": checks, "pass": all(checks.values())}, indent=1))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
