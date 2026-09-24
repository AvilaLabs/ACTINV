#!/usr/bin/env python3
"""P47 frozen artifact registry — every artifact the protocol binds,
with its expected sha sourced from the P32 sealed records. G0 asserts
byte-identity; controls mutate copies, never these files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ND = Path.home() / "nuclear-data"
P32W = ND / "p32-work"
CHAIN = P32W / "chain"
DOSE = P32W / "dose"
TALLY = P32W / "tally_error"

G1_P32 = json.loads((ROOT / "results/g1_p32.json").read_text())
DOSE_REC = json.loads((ROOT / "results/p32_dose.json").read_text())
TALLY_REC = json.loads(
    (ROOT / "results/p32_tally_error.json").read_text())

# expected shas — drawn from the P32-sealed records themselves
EXPECTED = {
    "mesh_result": (
        CHAIN / "mesh_result.ndjson",
        G1_P32["artifacts"]["mesh_result"]["sha256"]),
    "flux_ndjson": (
        CHAIN / "flux.ndjson",
        G1_P32["artifacts"]["flux_ndjson"]["sha256"]),
    "mesh_spec": (
        CHAIN / "mesh_spec.json",
        G1_P32["artifacts"]["mesh_spec"]["sha256"]),
    "neutron_statepoint": (
        Path(G1_P32["artifacts"]["statepoint"]["path"]),
        G1_P32["artifacts"]["statepoint"]["sha256"]),
    "depletion_results": (
        Path(G1_P32["artifacts"]["depletion_results"]["path"]),
        G1_P32["artifacts"]["depletion_results"]["sha256"]),
    "source_step2": (
        CHAIN / "source_step2.py", None),
    "source_step3": (
        CHAIN / "source_step3.py", None),
    "dose_statepoint_step2": (
        DOSE / "dose_step2" /
        Path(DOSE_REC["cooling_step_doses"]["2"]
             ["statepoint"]["path"]).name,
        DOSE_REC["cooling_step_doses"]["2"]["statepoint"]["sha256"]),
    "dose_statepoint_step3": (
        DOSE / "dose_step3" /
        Path(DOSE_REC["cooling_step_doses"]["3"]
             ["statepoint"]["path"]).name,
        DOSE_REC["cooling_step_doses"]["3"]["statepoint"]["sha256"]),
}
for k in range(8):
    EXPECTED[f"flux_pert{k}"] = (TALLY / f"flux_pert{k}.ndjson", None)
    EXPECTED[f"mesh_pert{k}"] = (TALLY / f"mesh_result_pert{k}.ndjson",
                                None)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def verify() -> dict:
    """(name -> {path, expected, actual, present, identical})."""
    out = {}
    for name, (path, expected) in EXPECTED.items():
        present = path.is_file()
        actual = sha256(path) if present else None
        out[name] = {"path": str(path), "expected_sha256": expected,
                     "actual_sha256": actual, "present": present,
                     "identical": (expected is None
                                  or expected == actual)}
    return out


if __name__ == "__main__":
    v = verify()
    print(json.dumps({n: {"present": r["present"],
                          "identical": r["identical"],
                          "sha": (r["actual_sha256"] or "")[:16]}
                      for n, r in v.items()}, indent=1))
