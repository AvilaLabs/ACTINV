#!/usr/bin/env python3
"""Compute the browser workbench's example results ahead of time, into web/examples/.

The browser cannot solve, so its Start page opens results computed here: the iron example it loads as a problem, and
the impurity screen of a reduced-activation steel. `scripts/prepare_web.py` serves them beside the app, and
`manifest.json` records the ACTINV version and the SHA-256 of each file; `scripts/test_prepare_web.py` fails when the
workspace version moves on without them, so a release recomputes them.

Run from the repository root with the nuclear data installed (`actinv data fetch`) and the photon response the
impurities example names (`photon-response/nist-xcom-all.json`, see examples/impurities_ra_steel/README.md):

    ACTINV_BIN=target/release/actinv python3 scripts/web_examples.py
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web/examples"
# (served file, actinv arguments before the output path)
EXAMPLES = [
    ("iron-result.json", ["run", "examples/fns_fe_5min.json"]),
    ("impurities-result.json", ["impurities", "examples/impurities_ra_steel/impurities.json"]),
]


def workspace_version() -> str:
    return tomllib.loads((ROOT / "Cargo.toml").read_text())["workspace"]["package"]["version"]


def main() -> None:
    binary = os.environ.get("ACTINV_BIN", str(ROOT / "target/release/actinv"))
    version = workspace_version()
    reported = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True, timeout=60).stdout
    if version not in reported:
        raise SystemExit(f"{binary} reports {reported.strip()!r}, the workspace is {version}: rebuild it first")
    OUT.mkdir(parents=True, exist_ok=True)
    files = {}
    with tempfile.TemporaryDirectory() as temp:
        for name, args in EXAMPLES:
            raw = Path(temp) / name
            subprocess.run([binary, *args, str(raw)], cwd=ROOT, check=True, timeout=1800)
            # Compact: the browser fetches these, and pretty printing doubles the size.
            text = json.dumps(json.loads(raw.read_text()), separators=(",", ":")) + "\n"
            (OUT / name).write_text(text)
            files[name] = {"command": " ".join(["actinv", *args, name]),
                           "sha256": hashlib.sha256(text.encode()).hexdigest()}
    manifest = {"schema": "actinv-web-examples-1", "actinv_version": version, "files": files}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for name, entry in files.items():
        print(f"{name}: {(OUT / name).stat().st_size} bytes, {entry['sha256']}")


if __name__ == "__main__":
    main()
