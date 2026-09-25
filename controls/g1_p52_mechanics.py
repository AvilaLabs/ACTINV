#!/usr/bin/env python3
"""P52 G1 mechanics — actinv-r2s-source-1 emission rules on synthetic
mesh-result records (no solver runs; the export is pure arithmetic on
embedded results, so every expectation is computed by hand here).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p52_artifacts as p52a  # noqa: E402

BIN = Path(__import__("os").environ.get(
    "ACTINV_BIN", ROOT / "target/debug/actinv"))
OUT = ROOT / "results/g1_p52_mechanics.json"

HEADER = {"record": "header", "schema": "actinv-mesh-result-1",
          "spec_fingerprint_sha256": "f" * 64,
          "certificate": {"canonical_flux":
                          {"sha256_computed": "a" * 64}}}
FOOTER = {"record": "footer", "cell_count": 2}


def cell(cid: str, nucs: list[dict], missing: str | None = None,
         total: float | None = None) -> dict:
    """Synthetic cell record: nucs = [{nuclide, photons_s, rel_sig, nominal}]"""
    by_n = []
    responses = {}
    for n in nucs:
        by_n.append({"nuclide": n["nuclide"],
                     "groups": [{"centroid_eV": 1.0e6,
                                 "photons_s": n["photons_s"]}]})
        if n.get("rel_sig") is not None:
            responses[f"activity:{n['nuclide']}"] = {
                "nominal": n.get("nominal", 1.0),
                "mf33_standard_uncertainty":
                    n["rel_sig"] * n.get("nominal", 1.0)}
    step = {"step": 1, "t_s": 300.0, "flux": 1.0}
    if missing != "photon_source":
        step["photon_source"] = {
            "total_photons_s":
                total if total is not None
                else sum(n["photons_s"] for n in nucs),
            "groups": [{"centroid_eV": 1.0e6,
                        "photons_s": total if total is not None
                        else sum(n["photons_s"] for n in nucs)}],
            "by_nuclide": by_n}
    if missing != "uncertainty":
        step["uncertainty"] = {"responses": responses,
                               "uncovered_library_rows": [3, 4],
                               "excluded_blocks": [{"a": 1}]}
    return {"record": "cell", "ordinal": 0, "id": cid,
            "bounds_cm": [[0, 1]] * 3, "volume_cm3": 1.0,
            "result": {"steps": [step]}}


def run_export(records: list[dict], step: int = 1):
    """Run export-r2s on synthetic records; returns (proc, doc_text|None)."""
    with tempfile.TemporaryDirectory(prefix="p52-g1-", dir="target") as d:
        mesh = Path(d) / "m.ndjson"
        out = Path(d) / "o.ndjson"
        mesh.write_text("".join(json.dumps(r) + "\n" for r in records))
        r = subprocess.run([str(BIN), "export-r2s", str(mesh), str(step),
                            str(out)],
                           capture_output=True, text=True)
        return r, (out.read_text() if out.exists() else None)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol * max(abs(a), abs(b), 1.0)


def main() -> int:
    drift = p52a.verify()
    problems = []
    checks = {}

    # --- nominal two-cell emission -----------------------------------------
    recs = [HEADER,
            cell("c1", [{"nuclide": "Fe55", "photons_s": 100.0,
                         "rel_sig": 0.10, "nominal": 10.0},
                        {"nuclide": "Mn54", "photons_s": 40.0,
                         "rel_sig": 0.20, "nominal": 2.0}]),
            cell("c2", [{"nuclide": "Fe55", "photons_s": 60.0,
                         "rel_sig": 0.10, "nominal": 6.0}]),
            FOOTER]
    r, doc = run_export(recs)
    ok = r.returncode == 0 and doc is not None
    checks["emits"] = ok
    if ok:
        lines = [json.loads(l) for l in doc.splitlines()]
        h, cells, foot = lines[0], lines[1:3], lines[3]
        exp = {"header": h["schema"] == "actinv-r2s-source-1"
               and h["mesh_result_sha256"],
               "c1_total": close(cells[0]["photons_s"], 140.0),
               "c1_sigma_i": close(cells[0]["sigma_photons_s_independent"],
                                   (10.0**2 + 8.0**2) ** 0.5),
               "c1_sigma_c": close(cells[0]["sigma_photons_s_conservative"],
                                   18.0),
               "c1_nuclide_sig":
                   cells[0]["per_nuclide"][0]["sigma_photons_s"] == 10.0,
               "c2_sigma_i": close(cells[1]["sigma_photons_s_independent"],
                                   6.0),
               "footer_total": close(foot["total_photons_s"], 200.0),
               "footer_indep":
                   close(foot["sigma_total_independent"],
                         (140.0**2 * 0 + (10.0**2 + 8.0**2) + 6.0**2) ** 0.5),
               "coverage_rows":
                   cells[0]["coverage"]["uncovered_library_rows"] == 2}
        checks.update(exp)
        problems += [k for k, v in exp.items() if not v]

    # --- unbanded nuclide: cell sigmas null, share ledgered ------------------
    r, doc = run_export([HEADER,
                         cell("u", [{"nuclide": "Fe55", "photons_s": 80.0,
                                     "rel_sig": 0.10},
                                    {"nuclide": "ZZ9", "photons_s": 20.0,
                                     "rel_sig": None}]),
                         FOOTER])
    ok = r.returncode == 0 and doc
    if ok:
        c = json.loads(doc.splitlines()[1])
        # banded-part sums are real: sigma = 80*0.10 = 8.0 over the one
        # banded nuclide; the unbanded contribution is flagged, not hidden.
        checks["unbanded_cell_sigma_banded_part"] = \
            close(c["sigma_photons_s_independent"], 8.0)
        checks["unbanded_flag"] = c["coverage"]["partially_unbanded"] is True
        checks["unbanded_share"] = \
            close(c["coverage"]["unbanded_photon_share"], 0.2)
        checks["unbanded_entry_null"] = \
            c["per_nuclide"][1]["sigma_photons_s"] is None
        checks["unbanded_footer_flag"] = \
            json.loads(doc.splitlines()[-1])[
                "cells_partially_unbanded"] == 1

    # --- zero-source cell emits honest zeros --------------------------------
    r, doc = run_export([HEADER, cell("z", [], total=0.0), FOOTER])
    if r.returncode == 0 and doc:
        c = json.loads(doc.splitlines()[1])
        checks["zero_source"] = (c["photons_s"] == 0.0
                                 and c["sigma_photons_s_independent"] == 0.0
                                 and c["per_nuclide"] == [])
    else:
        checks["zero_source"] = False

    # --- hard errors name the cell ------------------------------------------
    for missing, tag in [("uncertainty", "missing_uncertainty"),
                         ("photon_source", "missing_photon_source")]:
        r, _ = run_export([HEADER, cell("bad", [], missing=missing), FOOTER])
        checks[tag] = (r.returncode != 0
                       and "bad" in r.stderr + r.stdout)

    # --- truncated input rejected -------------------------------------------
    r, _ = run_export([HEADER, cell("t", [], total=0.0)])  # no footer
    checks["no_footer_rejected"] = r.returncode != 0

    # --- cell before header rejected -----------------------------------------
    r, _ = run_export([cell("x", [], total=0.0), HEADER, FOOTER])
    checks["order_rejected"] = r.returncode != 0

    # --- artifact drift ------------------------------------------------------
    checks["sealed_artifacts_unchanged"] = all(
        r2["sha256"] for r2 in drift.values())

    failed = [k for k, v in checks.items() if not v]
    result = {"pass": not failed and not problems,
              "checks": checks, "problems": problems + failed}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
