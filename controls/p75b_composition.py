#!/usr/bin/env python3
"""P75b runner: composition superposition at real flux (protocols/ACTINV-P75B_PROTOCOL.md).

At fixed flux the inventory equations are linear in the initial composition, so a mixture's
responses should equal the wt%-weighted sum of pure-element runs at the same flux — at any fluence,
with no background subtraction. Reuses the P75 case machinery; separate work directory.

    python3 controls/p75b_composition.py build
    python3 controls/p75b_composition.py run
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("p75", ROOT / "controls" / "p75_linear_response.py")
p75 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p75)

p75.WORK = ROOT / "target" / "p75b"
p75.SPECS = p75.WORK / "specs"
p75.RAW = p75.WORK / "raw"
p75.CHECKPOINT = p75.WORK / "runs.jsonl"
p75.MANIFEST = p75.WORK / "cases.json"

SPECTRA = ("fns", "mix", "maxwell")
AMPLITUDES = (1.0e10, 1.0e13, 1.0e15)
SCHEDULE = "s1_1y"
ARMS = {"C": ("coupled", "reach"), "R": ("trace", "reach"), "A": ("auto", "rate")}


def elements() -> list[str]:
    return sorted({e for m in p75.MIXTURES for e in p75.MATERIALS[m]})


def build_cases() -> list[dict]:
    sp, sc = p75.spectra(), p75.schedules()
    cases = []
    for arm, (mode, prune) in ARMS.items():
        for p in SPECTRA:
            for a in AMPLITUDES:
                members = [(m, p75.MATERIALS[m]) for m in p75.MIXTURES]
                members += [(f"el_{e}", {e: 100.0}) for e in elements()]
                for name, comp in members:
                    rid = f"{arm}__{name}__{p}__{a:.0e}"
                    doc = p75.spec(rid, comp, sp[p], a, sc[SCHEDULE]["steps"], mode, prune)
                    cases.append({"id": rid, "arm": arm, "material": name, "spectrum": p,
                                  "schedule": SCHEDULE, "amplitude": a, "mode": mode, "prune": prune,
                                  "zero_flux": False, "compared": sc[SCHEDULE]["compared"], "doc": doc})
    return cases


def cmd_build() -> None:
    p75.SPECS.mkdir(parents=True, exist_ok=True)
    manifest = []
    for c in build_cases():
        data = json.dumps(c["doc"], sort_keys=True, separators=(",", ":")).encode()
        (p75.SPECS / f"{c['id']}.json").write_bytes(data)
        row = {k: v for k, v in c.items() if k != "doc"}
        row["spec_sha256"] = hashlib.sha256(data).hexdigest()
        manifest.append(row)
    p75.MANIFEST.write_text(json.dumps({"cases": manifest}, indent=1, sort_keys=True))
    print(f"{len(manifest)} cases; manifest sha256 {p75.sha256_file(p75.MANIFEST)}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv[:1] == ["build"]:
        cmd_build()
    elif argv[:1] == ["run"]:
        p75.cmd_run(None, None)
    else:
        sys.exit(__doc__)
