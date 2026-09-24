#!/usr/bin/env python3
"""P47 G0 seal — binds protocol sha, opening commit, every frozen
artifact re-hashed and asserted byte-identical to its P32-sealed sha,
OpenMC version pinned, envelope declared. Emits
results/g0_p47_seals.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p47_artifacts as p47a  # noqa: E402

OUT = ROOT / "results/g0_p47_seals.json"
OMC_PY = Path.home() / ".local/share/mamba/envs/openmc/bin/python3"


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=ROOT).stdout.strip()
    artifacts = p47a.verify()
    missing = {n: r["path"] for n, r in artifacts.items()
               if not r["present"]}
    drifted = {n: {"expected": r["expected_sha256"],
                   "actual": r["actual_sha256"]}
               for n, r in artifacts.items()
               if r["present"] and not r["identical"]}
    if missing or drifted:
        print(json.dumps({"sealed": False, "missing": missing,
                          "drifted": drifted}))
        return 1

    omv = subprocess.run(
        [str(OMC_PY), "-c", "import openmc; print(openmc.__version__)"],
        capture_output=True, text=True)
    seal = {
        "schema": "actinv-p47-seal-1",
        "sealed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime()),
        "opening_commit": commit,
        "protocol_sha256": sha256(
            ROOT / "protocols/ACTINV-P47_PROTOCOL.md"),
        "artifacts": {n: {"path": r["path"],
                          "sha256": r["actual_sha256"],
                          "p32_bound_sha256": r["expected_sha256"]}
                      for n, r in artifacts.items()},
        "openmc": {"env_python": str(OMC_PY),
                   "exe_sha256": sha256(OMC_PY),
                   "version": omv.stdout.strip()},
        "code_sha256": {
            "artifacts": sha256(ROOT / "controls/p47_artifacts.py"),
            "g1": sha256(ROOT / "controls/g1_p47_completeness.py"),
            "g2": sha256(ROOT / "controls/g2_p47_controls.py"),
            "g3": sha256(ROOT / "controls/g3_p47_report.py"),
            "p32_dose": sha256(ROOT / "controls/p32_dose.py"),
            "p32_tally_error": sha256(
                ROOT / "controls/p32_tally_error.py"),
            "binary": sha256(ROOT / "target/release/actinv"),
        },
        "constants": {
            "analytic_particles": 20000,
            "analytic_batches": 100,
            "analytic_source_e_eV": 1.0e6,
            "detector_r_cm": 10.0,
            "boundary_r_cm": 50.0,
            "analytic_tolerance": "max(3*mc_rel_std, 0.05)",
            "linearity_sigma0": 0.05,
            "linearity_samples": 16,
            "linearity_ratio_band": [1.7, 2.3],
            "linearity_response_min": 1.2,
            "band_multiplier": 2.0,
            "envelope_s": 1800,
            "external_geometry": "unavailable",
        },
    }
    OUT.write_text(json.dumps(seal, indent=1))
    print(json.dumps({"sealed": True, "commit": commit[:12],
                      "artifacts": len(artifacts),
                      "openmc": omv.stdout.strip()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
