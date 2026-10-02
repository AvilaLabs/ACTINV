#!/usr/bin/env python3
"""P102 G3 — consumer round-trip: results/p52_r2s_source.ndjson -> alara.
nucleide 0.16.0 (pinned, throwaway venv under target/) parses every file
with its own reader; totals reconcile; a PyNE-rule parse (tab split, G
from line 1, element index never advances past 0 since every file carries
exactly one TOTAL row) is applied independently. PyNE itself is not
installed (PyTables/MOAB) — recorded, not run.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p102_case as p102  # noqa: E402

RESULT = ROOT / "results/g3_p102_demo.json"
CORPUS = ROOT / "results/p52_r2s_source.ndjson"
PERSIST = ROOT / "results/p102_alara"  # G4/G5 repurpose these bytes
# Corpus shutdown: p52 irradiates IRR_S = 300 s (controls/p52_openmc_parity.py), then
# cools 1 d, 30 d and 1 y, so step 4 (step_t_s 34214700 s) is 34214400 s after shutdown.
SHUTDOWN_T_S = 300.0
NUCLEIDE_PY = ROOT / "target/p102-venv/bin/python3"


def pyne_rule_parse(text: str) -> dict:
    """PyNE's alara.photon_source_to_hdf5 rule, applied independently: split
    on literal '\\t', group count G = len(tokens on line 1) - 2, and the
    element index advances only when a non-TOTAL row follows a TOTAL row —
    which never happens here (every file carries exactly one TOTAL row),
    so index stays 0 throughout, for every file."""
    lines = [l for l in text.split("\n") if l.strip()]
    toks0 = lines[0].split("\t")
    g = len(toks0) - 2
    idx = 0
    rows = []
    prev_was_total = False
    for l in lines:
        toks = l.split("\t")
        nuclide, time_field = toks[0], toks[1]
        strengths = [float(x) for x in toks[2:2 + g]]
        if nuclide != "TOTAL" and prev_was_total:
            idx += 1
        rows.append({"idx": idx, "nuclide": nuclide, "time": time_field,
                     "strengths": strengths})
        prev_was_total = (nuclide == "TOTAL")
    return {"g": g, "rows": rows}


def main() -> int:
    checks = {}
    recs = [json.loads(l) for l in CORPUS.read_text().splitlines() if l.strip()]
    cells = [r for r in recs if r.get("record") == "cell"]
    step_t_s_values = {c["step_t_s"] for c in cells}
    checks["corpus_single_step_t_s"] = len(step_t_s_values) == 1
    step_t_s = next(iter(step_t_s_values))
    cooling = step_t_s - SHUTDOWN_T_S
    totals = [c["photons_s"] for c in cells]

    PERSIST.mkdir(exist_ok=True)
    if any(PERSIST.iterdir()):
        for p in PERSIST.iterdir():
            p.unlink()
    r = subprocess.run(
        [str(p102.actinv_bin()), "export-source", "alara", str(CORPUS),
         str(PERSIST), "--shutdown-t-s", repr(SHUTDOWN_T_S)],
        capture_output=True, text=True)
    checks["emits_ok"] = r.returncode == 0
    if not checks["emits_ok"]:
        print(r.stderr)
        RESULT.write_text(json.dumps({"pass": False, "checks": checks}) + "\n")
        return 1

    index = json.loads((PERSIST / "actinv-alara-index.json").read_text())
    checks["file_count_equals_cell_count"] = len(index["cells"]) == len(cells)
    checks["cooling_matches"] = index["cooling_s"] == cooling

    if not NUCLEIDE_PY.exists():
        checks["nucleide_importable"] = (
            f"skipped: {NUCLEIDE_PY} absent — run "
            "`python3 -m venv target/p102-venv && "
            "target/p102-venv/bin/pip install nucleide==0.16.0` first")
        evidence = {"schema": "actinv-p102-g3-demo-1", "pass": False,
                    "checks": checks}
        RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
        print(json.dumps(checks, indent=1, sort_keys=True))
        return 1

    probe = f"""
import json
import sys
sys.path.insert(0, {str(ROOT / 'controls')!r})
from nucleide import alara, r2s

index = json.loads(open({str(PERSIST / 'actinv-alara-index.json')!r}).read())
results = []
totals = []
for entry in index["cells"]:
    text = open({str(PERSIST)!r} + "/" + entry["file"]).read()
    rows = [l for l in text.splitlines() if l.strip()]
    sums = r2s.photon_group_sums(text, ["TOTAL"], {cooling!r})
    total_strength = alara.alara_photon_total_strength(text)
    conserved = abs(total_strength * entry["volume_cm3"] - entry["photons_s"])
    denom = max(1.0, abs(entry["photons_s"]))
    results.append({{
        "file": entry["file"],
        "n_rows": len(rows),
        "nuclide": rows[0].split("\\t")[0],
        "time_s": sums["groups"][0]["time_s"] if sums["groups"] else None,
        "g": len(sums["sums"]),
        "relative_error": conserved / denom,
    }})
    totals.append(entry["photons_s"])

zone_strengths = [alara.alara_photon_total_strength(
    open({str(PERSIST)!r} + "/" + e["file"]).read()) for e in index["cells"]]
tag = r2s.tag_zone_strength(zone_strengths, list(range(len(zone_strengths))),
                            split=False)

print(json.dumps({{"results": results, "tag_total": tag["total"],
                  "tag_source_strength": tag["source_strength"]}}))
"""
    rp = subprocess.run([str(NUCLEIDE_PY), "-c", probe],
                        capture_output=True, text=True)
    checks["nucleide_run_ok"] = rp.returncode == 0
    if not checks["nucleide_run_ok"]:
        print(rp.stderr)
        evidence = {"schema": "actinv-p102-g3-demo-1", "pass": False,
                    "checks": checks}
        RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
        return 1
    got = json.loads(rp.stdout.strip().splitlines()[-1])

    checks["each_file_one_row"] = all(r["n_rows"] == 1 for r in got["results"])
    checks["each_file_nuclide_total"] = all(
        r["nuclide"] == "TOTAL" for r in got["results"])
    checks["each_file_time_s_equals_cooling"] = all(
        r["time_s"] == cooling for r in got["results"])
    checks["g_identical_across_files"] = len({r["g"] for r in got["results"]}) == 1
    checks["g_equals_grid_length"] = next(iter(
        {r["g"] for r in got["results"]})) == len(index["group_centroids_eV"])
    max_rel_err = max(r["relative_error"] for r in got["results"])
    checks["max_relative_error"] = max_rel_err
    checks["conservation_within_1e-12"] = max_rel_err <= 1e-12

    checks["tag_zone_strength_conserves_total"] = abs(
        got["tag_total"] - sum(totals)) <= 1e-9 * max(1.0, sum(totals))
    checks["tag_zone_strength_per_cell"] = all(
        abs(a - b) <= 1e-9 * max(1.0, abs(b))
        for a, b in zip(got["tag_source_strength"], totals))

    # PyNE-rule parse, applied independently per file
    pyne_checks = {}
    for entry in index["cells"]:
        text = (PERSIST / entry["file"]).read_text()
        parsed = pyne_rule_parse(text)
        pyne_checks[entry["file"]] = (
            parsed["g"] == len(index["group_centroids_eV"]) and
            len(parsed["rows"]) == 1 and
            parsed["rows"][0]["idx"] == 0 and
            parsed["rows"][0]["nuclide"] == "TOTAL")
    checks["pyne_rule_parse_all_files"] = all(pyne_checks.values())
    checks["pyne_itself_not_installed"] = (
        "PyNE needs PyTables/MOAB — not installed; the PyNE-rule parse "
        "above applies photon_source_to_hdf5's own splitting/indexing "
        "rules without the PyNE package")

    bool_checks = {k: v for k, v in checks.items() if isinstance(v, bool)}
    evidence = {"schema": "actinv-p102-g3-demo-1",
                "corpus": str(CORPUS.relative_to(ROOT)),
                "cells": len(cells),
                "persisted": str(PERSIST.relative_to(ROOT)),
                "nucleide_version": "0.16.0",
                "max_relative_error": max_rel_err,
                "pass": all(bool_checks.values()),
                "checks": checks}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps(checks, indent=1, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
