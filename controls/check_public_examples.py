#!/usr/bin/env python3
"""Keep shipped examples portable and aligned with the embedded data catalog."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
CATALOG = ROOT / "crates" / "actinv-cli" / "data" / "actinv-data-catalog-v1.0.0.json"
PATH_KEYS = {"path", "primary", "fallback"}


def path_values(value: object, location: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{location}.{key}" if location else key
            if key in PATH_KEYS and isinstance(item, str) and item:
                yield child, item
            yield from path_values(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from path_values(item, f"{location}[{index}]")


def portable(path: str) -> bool:
    return not (
        Path(path).is_absolute()
        or PureWindowsPath(path).is_absolute()
        or path.startswith("~")
        or re.match(r"^[A-Za-z]:", path)
    )


def binary() -> Path:
    supplied = os.environ.get("ACTINV_BIN")
    if supplied:
        return Path(supplied)
    for profile in ("release", "debug"):
        candidate = ROOT / "target" / profile / "actinv"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("build the actinv CLI or set ACTINV_BIN")


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    bundle = next(
        item for item in catalog["bundles"] if item["id"] == catalog["default_bundle"]
    )
    artifacts = {item["id"]: item for item in catalog["artifacts"]}
    roles = {artifacts[item]["role"]: artifacts[item] for item in bundle["artifacts"]}
    root = Path("actinv-data") / f"v{catalog['catalog_version']}"
    expected = {
        "projectile": bundle["projectile"],
        "library.path": str(root / roles["activation-library"]["path"]),
        "library.sha256": roles["activation-library"]["sha256"],
        "decay.primary": str(root / roles["decay-primary"]["path"]),
        "decay.fallback": str(root / roles["decay-fallback"]["path"]),
        "spectrum.structure": bundle["groups"],
        "options.temperature_K": bundle["temperature_K"],
    }

    example_paths = sorted(EXAMPLES.glob("*.json"))
    if not example_paths:
        raise RuntimeError("no public JSON examples found")
    errors: list[str] = []
    parsed: dict[Path, dict] = {}
    for example in example_paths:
        value = json.loads(example.read_text(encoding="utf-8"))
        parsed[example] = value
        for location, path in path_values(value):
            if not portable(path):
                errors.append(f"{example.relative_to(ROOT)}:{location} is machine-specific: {path}")

    quickstart = EXAMPLES / "fns_fe_5min.json"
    value = parsed[quickstart]
    observed = {
        "projectile": value.get("projectile"),
        "library.path": value["library"].get("path"),
        "library.sha256": value["library"].get("sha256"),
        "decay.primary": value["decay"].get("primary"),
        "decay.fallback": value["decay"].get("fallback"),
        "spectrum.structure": value["spectrum"].get("structure"),
        "options.temperature_K": value["options"].get("temperature_K"),
    }
    for field, expected_value in expected.items():
        if observed[field] != expected_value:
            errors.append(
                f"{quickstart.relative_to(ROOT)}:{field} is {observed[field]!r}, "
                f"expected catalog value {expected_value!r}"
            )

    validation = subprocess.run(
        [binary(), "validate", quickstart],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if validation.returncode:
        errors.append(f"quick-start validation failed: {validation.stderr.strip()}")

    result = {
        "examples": [str(path.relative_to(ROOT)) for path in example_paths],
        "portable_paths": not any("machine-specific" in error for error in errors),
        "default_bundle": catalog["default_bundle"],
        "catalog_fragment_exact": observed == expected,
        "spec_valid": validation.returncode == 0,
        "errors": errors,
        "pass": not errors,
    }
    print(json.dumps(result, indent=1))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, StopIteration, subprocess.SubprocessError, ValueError) as error:
        print(f"public-example control failed: {error}", file=sys.stderr)
        raise SystemExit(1)
