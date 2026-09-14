#!/usr/bin/env python3
"""P26 G0 — opening, authority, baseline refresh, comparator census.

Binds the frozen protocol hash, the opening commit, all prior verdicts
asserted verbatim, the refreshed Avila Core identity (or its recorded
unavailability), a census of comparator tools executable on this
workstation, and proof that no prototype code sits on a production path.

Writes ``results/g0_p26_seals.json``.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g0_p26_seals.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P26_PROTOCOL.md"
PROTOCOL_SHA256 = "0dd9be843e4e195d3f3ebb9a9084f233af3d0eedf5045bbb48d77d665f5d1e06"
OPENING_COMMIT = "3ae2f2656f6e6e56ad401378d9f5c6a96f1cf80d"

EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS",
    "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL",
}

COMPARATOR_PROBES = {
    "fispact": {"executables": ["fispact", "fispact-II", "fispact2"],
                "python_modules": []},
    "openmc": {"executables": ["openmc"], "python_modules": ["openmc"]},
    "alara": {"executables": ["alara", "ALARA"], "python_modules": []},
    "scale_origen": {"executables": ["scale", "origen", "origen-rs"],
                     "python_modules": []},
    "njoy": {"executables": ["njoy", "njoy2016"], "python_modules": []},
    "actinv_v101": {"executables": ["actinv"], "python_modules": ["actinv"]},
}
CORE_CANDIDATES = [
    Path.home() / "Documents" / "Avila-Labs" / "avila-core",
    Path.home() / "Documents" / "Avila-Labs" / "core",
    Path.home() / "Documents" / "avila-core",
    Path.home() / "Documents" / "core",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True,
                          capture_output=True).stdout.strip()


def comparator_census() -> dict:
    out = {}
    for name, probe in COMPARATOR_PROBES.items():
        found = [e for e in probe["executables"] if shutil.which(e)]
        mods = []
        for mod in probe["python_modules"]:
            r = subprocess.run(
                [sys.executable, "-c", f"import {mod}"],
                capture_output=True)
            if r.returncode == 0:
                mods.append(mod)
        status = "executable" if (found or mods) else "not_available"
        out[name] = {"executables": found, "python_modules": mods,
                     "status": status}
    return out


def core_identity() -> dict:
    for path in CORE_CANDIDATES:
        gitdir = path / ".git"
        if gitdir.is_dir():
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=path, text=True,
                capture_output=True).stdout.strip()
            return {"status": "pinned", "path": str(path), "head": sha}
    return {
        "status": "unavailable",
        "path": None,
        "head": None,
        "consequence": (
            "No maintainer checkout of Avila Core exists on this workstation; "
            "P26 G0 records the unavailability.  Core is an optional "
            "integration and P26's feasibility work does not consume it; "
            "the identity must be pinned before any P27 adapter work."),
    }


def production_path_clean() -> dict:
    proto = ROOT / "prototypes"
    files = sorted(str(p.relative_to(ROOT)) for p in proto.rglob("*")
                   if p.is_file()) if proto.is_dir() else []
    suspicious = []
    for marker in ("crates", "python", "data", "examples"):
        for p in (ROOT / marker).rglob("*"):
            if p.is_file() and p.suffix in {".rs", ".py", ".toml"}:
                try:
                    text = p.read_text(errors="replace")
                except OSError:
                    continue
                if "prototypes" in text:
                    suspicious.append(str(p.relative_to(ROOT)))
    return {"prototype_files": files,
            "production_references_to_prototypes": suspicious,
            "clean": not suspicious}


def main() -> int:
    failures: list[str] = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol sha256 mismatch")
    if subprocess.run(
            ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
            cwd=ROOT).returncode != 0:
        failures.append("opening commit is not an ancestor of HEAD")
    if git("rev-parse", "HEAD") == OPENING_COMMIT:
        pass  # sealing at the opening commit itself is permitted

    verdicts = {}
    for name, expected in EXPECTED_VERDICTS.items():
        path = ROOT / "results" / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
            continue
        got = json.loads(path.read_text()).get("verdict")
        verdicts[name] = got
        if got != expected:
            failures.append(f"{name} verdict {got} != {expected}")

    census = comparator_census()
    if not any(v["status"] == "executable" for v in census.values()):
        failures.append("no comparator tool executable on this workstation")

    clean = production_path_clean()
    if not clean["clean"]:
        failures.append(
            f"production files reference prototypes: {clean['production_references_to_prototypes']}")

    record = {
        "schema": "actinv-p26-g0-seal-1",
        "phase": "P26",
        "gate": "G0",
        "protocol_sha256": PROTOCOL_SHA256,
        "opening_commit": OPENING_COMMIT,
        "seal_commit": git("rev-parse", "HEAD"),
        "prior_verdicts": verdicts,
        "core": core_identity(),
        "comparator_census": census,
        "production_path": clean,
        "release_hold": (
            "The 1.1.0 release hold stands unchanged; P26 produces "
            "feasibility evidence, not a release."),
        "pass": not failures,
        "failures": failures,
    }
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps(record, indent=1, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
