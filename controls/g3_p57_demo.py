#!/usr/bin/env python3
"""P57 G3 — demonstration: the corpus p52 r2s-source emits all three
foreign formats; totals and groups reconcile; the OpenMC fragment round-
trips through openmc's own from_xml_element when importable.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p57_case as p57  # noqa: E402

RESULT = ROOT / "results/g3_p57_demo.json"
CORPUS = ROOT / "results/p52_r2s_source.ndjson"
PERSIST = ROOT / "results/p57_sources"  # G4/G5 repurpose these bytes


def main() -> int:
    checks = {}
    recs = [json.loads(l) for l in CORPUS.read_text().splitlines()
            if l.strip()]
    cells = [r for r in recs if r.get("record") == "cell"]
    total = sum(r["photons_s"] for r in cells)

    PERSIST.mkdir(exist_ok=True)
    outs = {}
    for fmt in ("openmc", "mcnp", "serpent"):
        out = PERSIST / f"p57_r2s.{fmt if fmt != 'openmc' else 'xml'}"
        r = subprocess.run(
            [str(p57.ACTINV), "export-source", fmt, str(CORPUS), str(out)],
            capture_output=True, text=True)
        checks[f"{fmt}_emits"] = r.returncode == 0
        if not checks[f"{fmt}_emits"]:
            print(r.stderr)
            continue
        outs[fmt] = out.read_text()

    # openmc structural + totals
    root = ET.fromstring(outs["openmc"])
    srcs = [e for e in root if e.tag == "source"]
    checks["openmc_cells"] = len(srcs) == len(cells)
    checks["openmc_strength_total"] = abs(
        sum(float(s.get("strength")) for s in srcs) - total) < 1e-12 * total
    for s in srcs:
        en = s.find("energy")
        if en is not None:
            vals = en.find("parameters").text.split()
            ps = [float(v) for v in vals[len(vals) // 2:]]
            assert abs(sum(ps) - 1.0) < 1e-12, "openmc probs not near 1.0"

    # mcnp: weight column sums to 1
    mcnp_lines = [l for l in outs["mcnp"].splitlines()
                  if l.startswith("SDEF")]
    ws = [float(l.split("WGT=")[1]) for l in mcnp_lines]
    checks["mcnp_cells"] = len(mcnp_lines) == len(cells)
    checks["mcnp_weight_sum"] = abs(sum(ws) - 1.0) < 1e-12

    # serpent: sw sums to 1, all sb spectra INTT=0
    slines = [l for l in outs["serpent"].splitlines()
              if l.startswith("src ")]
    sws = [float(l.split(" sw ")[1].split()[0]) for l in slines]
    checks["serpent_cells"] = len(slines) == len(cells)
    checks["serpent_weight_sum"] = abs(sum(sws) - 1.0) < 1e-12
    checks["serpent_intt0"] = all(
        l.split(" sb ")[1].split()[1] == "0" for l in slines)

    # real openmc parse, if the env's python exists
    if p57.OPENMC_PY.exists():
        probe = f"""
import xml.etree.ElementTree as ET, json
import openmc
tree = ET.parse('{PERSIST}/p57_r2s.xml')
srcs = [openmc.IndependentSource.from_xml_element(e)
        for e in tree.getroot() if e.tag == 'source']
print(json.dumps({{
  "n": len(srcs),
  "strength": sum(s.strength for s in srcs),
  "particle_ok": all(s.particle == 'photon' for s in srcs),
  "energy_ok": all(abs(sum(s.energy.p) - 1.0) < 1e-12 for s in srcs),
}}))
"""
        r = subprocess.run([str(p57.OPENMC_PY), "-c", probe],
                           capture_output=True, text=True)
        if r.returncode == 0:
            got = json.loads(r.stdout.strip().splitlines()[-1])
            checks["openmc_parsed_by_openmc"] = (
                got["n"] == len(cells) and got["particle_ok"] and
                got["energy_ok"] and
                abs(got["strength"] - total) < 1e-12 * total)
            checks["openmc_real_parse"] = True
        else:
            checks["openmc_parsed_by_openmc"] = False
            checks["openmc_real_parse"] = False
    else:
        checks["openmc_real_parse"] = "skipped: openmc env absent"

    evidence = {"schema": "actinv-p57-g3-demo-1",
                "corpus": str(CORPUS.relative_to(ROOT)),
                "cells": len(cells), "total_photons_s": total,
                "persisted": str(PERSIST.relative_to(ROOT)),
                "pass": all(v is True or isinstance(v, str)
                            for v in checks.values()),
                "checks": checks}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps(checks, indent=1, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
