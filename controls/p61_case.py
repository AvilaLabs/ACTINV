#!/usr/bin/env python3
"""P61 shared case machinery — run the p58 synthetic fixture with
outputs=["audit"] and variants that plant chain defects."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import p60_case

sha = p60_case.sha
run = p60_case.run


def spec(fx: dict, audit: bool = True) -> dict:
    """The p58 fixture spec plus options.outputs=['audit'] (or an explicit
    outputs list WITHOUT audit, which must stay byte-identical)."""
    s = p60_case.spec(fx)
    s.pop("uncertainty", None)  # audit is chain-level, not banded
    if audit:
        s["options"]["outputs"] = [
            "inventory", "activity", "heat", "photons", "dose",
            "pathways", "radiological", "ledger", "certificate", "audit"]
    else:
        s["options"]["outputs"] = [
            "inventory", "activity", "heat", "photons", "dose",
            "pathways", "radiological", "ledger", "certificate"]
    return s


def decay_dropping(fx: dict, tmp: Path, material: int = 101) -> Path:
    """A decay file with one nuclide's whole record block removed —
    plants products_no_evaluated_decay_data (and friends) in the run.
    `material` is the fixture's enumerate index: 101 = Mn56."""
    dst = tmp / f"dec61_drop{material}.txt"
    keep = [ln for ln in Path(fx["decay"]).read_text().splitlines()
            if ln[66:70].strip() != str(material)]
    dst.write_text("\n".join(keep) + "\n")
    return dst


def spec_dropping_decay(fx: dict, tmp: Path, material: int = 101) -> dict:
    s = spec(fx)
    d = decay_dropping(fx, tmp, material)
    s["decay"] = {"primary": str(d)}
    return s
