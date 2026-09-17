#!/usr/bin/env python3
"""P32 G3: negative controls — the spatial export must fail closed.

Fixtures are minimal actinv-mesh-result-1 files crafted to carry one
defect each; the check requires a nonzero exit and a named stderr
reason for every case.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
OUT = os.path.join(RES, "g3_p32.json")
TMP = os.path.join(ROOT, "target", "preflight-tmp")

CG = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=6G",
      "-p", "MemorySwapMax=0", "-p", "TasksMax=128", "-p",
      "CPUQuota=200%", "--", "env", f"TMPDIR={TMP}"]

GOOD_PHOTON = {
    "group_structure": "test",
    "boundaries_eV": [0.5e6, 1.5e6],
    "groups": [{"centroid_eV": 1.0e6, "low_eV": 0.5e6,
                "high_eV": 1.5e6, "photons_s": 1.0e6,
                "photons_s_g": 1.0e5, "power_W": 1.6e-7,
                "power_W_g": 2.0e-8}],
    "lines": [], "by_nuclide": [],
    "grouped_photons_s_g": 1.0e5, "grouped_photons_s": 1.0e6,
    "total_photons_s_g": 1.0e5, "total_photons_s": 1.0e6,
    "source_power_W_g": 2.0e-8, "source_power_W": 1.6e-7,
    "ungrouped_power_W_g": 0.0,
    "unrepresented_gamma_power_W_g": 0.0,
    "represented_gamma_power_fraction": 1.0,
    "contact_gamma_air_dose_proxy_Gy_h": None,
    "dose_response_power_coverage": None}


def cell(bounds=[[0, 1], [0, 1], [0, 1]], volume=1.0, step=1,
         photon=GOOD_PHOTON):
    s = {"step": step}
    if photon is not None:
        s["photon_source"] = photon
    return {"record": "cell", "ordinal": 0, "id": "1,1,1",
            "index": [1, 1, 1], "bounds_cm": bounds,
            "volume_cm3": volume, "source_relative_error": None,
            "rebin": {}, "result": {"steps": [s]}}


def fixture(records):
    hdr = {"record": "header", "schema": "actinv-mesh-result-1",
           "spec_title": "g3", "cell_count": len(records),
           "flux_units": "n cm^-2 s^-1",
           "source_energy_boundaries_eV": [0, 1],
           "activation_energy_boundaries_eV": [0, 1]}
    ftr = {"record": "footer", "cells_written": len(records)}
    fd, p = tempfile.mkstemp(dir=TMP, suffix=".ndjson")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(hdr) + "\n")
        for r in records:
            f.write(json.dumps(r) + "\n")
        f.write(json.dumps(ftr) + "\n")
    return p


CASES = {
    "missing_bounds": dict(
        records=[{k: v for k, v in cell().items() if k != "bounds_cm"}],
        step="1", want="bounds"),
    "missing_step": dict(records=[cell(step=1)], step="2",
                         want="step"),
    "no_photon_source": dict(records=[cell(photon=None)], step="1",
                             want="photon"),
    "zero_volume": dict(
        records=[cell(volume=0.0)], step="1", want="volume"),
    "negative_volume": dict(
        records=[cell(volume=-2.0)], step="1", want="volume"),
    "degenerate_bounds": dict(
        records=[cell(bounds=[[1, 1], [0, 1], [0, 1]])], step="1",
        want="bounds"),
    "omitted_photon_strength": dict(
        records=[cell(photon={**GOOD_PHOTON,
                              "total_photons_s": 2.0e6})],
        step="1", want="conserv"),
    "wrong_schema": dict(records="badheader", step="1",
                         want="mesh-result"),
}


def run_case(name, case, step, out):
    p = fixture(case["records"]) if case["records"] != "badheader" \
        else None
    if p is None:
        fd, p = tempfile.mkstemp(dir=TMP, suffix=".ndjson")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"record": "header",
                                "schema": "something-else"}) + "\n")
            f.write(json.dumps(cell()) + "\n")
    r = subprocess.run(CG + [ACTINV, "export-openmc-mesh", p,
                             str(step), out],
                       capture_output=True, text=True, timeout=120)
    os.unlink(p)
    ok = r.returncode != 0 and not os.path.isfile(out)
    stderr = (r.stderr + r.stdout).lower()
    named = case["want"] in stderr
    return {"exit": r.returncode, "named_reason": named,
            "fail_closed": ok, "stderr": (r.stderr or r.stdout)[-300:]}


def main():
    os.makedirs(TMP, exist_ok=True)
    results = {}
    for name, case in CASES.items():
        out = os.path.join(TMP, f"g3_{name}.py")
        results[name] = run_case(name, case, case["step"], out)
    doc = {"schema": "actinv-p32-g3-1", "phase": "P32", "gate": "G3",
           "controls": results}
    json.dump(doc, open(OUT, "w"), indent=1, sort_keys=True)
    for name, r in results.items():
        print(f"{name}: exit={r['exit']} named={r['named_reason']} "
              f"closed={r['fail_closed']}")


if __name__ == "__main__":
    main()
