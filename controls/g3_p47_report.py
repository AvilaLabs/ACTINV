#!/usr/bin/env python3
"""P47 G3 — the propagated-band comparison report.

Re-derives the P32 activation comparison (ACTINV vs openmc.deplete
atoms, top-50 by ACTINV atoms) and places every deviation inside the
propagated tally band: inside_band = rel_dev <= 2 * cell_spread.

Also reports the contact-proxy / transported-dose relationship per
step with named drivers — reported, not judged.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p47_artifacts as p47a  # noqa: E402

ND = Path.home() / "nuclear-data"
OMC_PY = Path.home() / ".local/share/mamba/envs/openmc/bin/python3"
OUT = ROOT / "results/g3_p47_report.json"
SEAL = ROOT / "results/g0_p47_seals.json"


def deplete_atoms(h5: str) -> dict:
    r = subprocess.run([str(OMC_PY), "-c", textwrap.dedent(f"""
        import json, openmc.deplete
        r = openmc.deplete.ResultsList({h5!r})
        res = {{}}
        for k in range(4):
            for j in range(4):
                for i in range(4):
                    mid = str(k * 16 + j * 4 + i + 1)
                    nucs = {{}}
                    try:
                        nucs["Fe56"] = float(r.get_atoms(mid, "Fe56")[1][1])
                    except Exception:
                        continue
                    for nuc in ["Mn56", "Mn54", "Fe59", "Cr51", "V52",
                                "Sc48", "Co58", "Co60", "Fe55", "Mn57",
                                "V49", "Ti48", "Mn52"]:
                        try:
                            nucs[nuc] = float(
                                r.get_atoms(mid, nuc)[1][1])
                        except Exception:
                            pass
                    res[f"v_{{i+1}}_{{j+1}}_{{k+1}}"] = nucs
        print(json.dumps(res))
        """)], capture_output=True, text=True)
    return json.loads(r.stdout.strip().splitlines()[-1])


def actinv_atoms(mesh_ndjson: Path, mass_g: float) -> dict:
    cells = {}
    for line in mesh_ndjson.read_text().splitlines():
        j = json.loads(line)
        if j.get("record") != "cell":
            continue
        inv = [s for s in j["result"]["steps"]
               if s["step"] == 1][0]["inventory"]
        cells[j["id"]] = {n["nuclide"]: n["atoms_per_g"] * mass_g
                          for n in inv}
    return cells


def contact_proxy_sum(mesh_ndjson: Path) -> dict:
    """sum of per-cell per-step contact proxy over all cells."""
    tot = {}
    for line in mesh_ndjson.read_text().splitlines():
        j = json.loads(line)
        if j.get("record") != "cell":
            continue
        for s in j["result"]["steps"]:
            step = str(s["step"])
            ps = s.get("photon_source") or {}
            v = 0.0
            for n in ps.get("by_nuclide", []):
                v += (n.get("contact_gamma_air_dose_proxy_Gy_h")
                      or 0.0)
            tot[step] = tot.get(step, 0.0) + v
    return tot


def main() -> int:
    seal = json.loads(SEAL.read_text())
    arts = seal["artifacts"]
    p32_model = json.loads(
        (ROOT / "results/g0_p32_seals.json").read_text())["model"]
    mass = p32_model["density_g_cm3"] * p32_model["voxel_cm3"]

    dep = deplete_atoms(arts["depletion_results"]["path"])
    act = actinv_atoms(Path(arts["mesh_result"]["path"]), mass)
    tally = json.loads(
        (ROOT / "results/p32_tally_error.json").read_text())
    band_mult = seal["constants"]["band_multiplier"]

    rows = []
    for cid, inv in act.items():
        i, j, k = cid.split(",")
        dm = dep.get(f"v_{i}_{j}_{k}", {})
        for nuc, a in inv.items():
            d = dm.get(nuc, 0.0)
            if a > 1e3 or d > 1e3:
                if a > 0 and d > 0:
                    rows.append({"cell": cid, "nuclide": nuc,
                                 "actinv": a, "deplete": d,
                                 "rel": abs(a - d) / max(a, d)})
    rows.sort(key=lambda x: -x["actinv"])
    top = rows[:50]
    # band: the cell's propagated spread at step index "0" — the
    # comparison's irradiation-end basis. per_cell keys are ordinals
    # (i fastest): ordinal = (i-1) + 4*(j-1) + 16*(k-1).
    def spread(cid):
        i, j, k = (int(v) for v in cid.split(","))
        ordinal = str((i - 1) + 4 * (j - 1) + 16 * (k - 1))
        return (tally["per_cell"][ordinal]["0"]["activity_Bq_per_g"]
                ["relative_spread"])
    for r in top:
        r["cell_spread"] = spread(r["cell"])
        r["band_half_width"] = band_mult * r["cell_spread"]
        r["inside_band"] = r["rel"] <= r["band_half_width"]

    # contact proxy vs transported dose
    dose = json.loads(
        (ROOT / "results/p32_dose.json").read_text())
    proxy = contact_proxy_sum(Path(arts["mesh_result"]["path"]))
    ratios = {}
    for step in ("2", "3"):
        td = dose["cooling_step_doses"][step]["dose_Gy_h"]
        cp = proxy.get(step)
        ratios[step] = {
            "transported_dose_Gy_h": td,
            "contact_proxy_sum_Gy_h": cp,
            "ratio": td / cp if cp else None,
            "drivers": [
                "proxy is uncollided semi-infinite-slab point-isotropic"
                " approximation; the tally transports through the"
                " executed geometry",
                "proxy is per-mass-of-source-material surface model;"
                " the tally integrates the r<10 cm detector cell",
                "air mu_en convention shared (same XCOM table)"],
        }

    n_inside = sum(1 for r in top if r["inside_band"])
    rep = {
        "spec": "actinv-p47-g3-1",
        "activation_comparison": {
            "n_compared": len(rows), "n_top50": len(top),
            "n_inside_band": n_inside,
            "median_rel_top50": sorted(r["rel"] for r in top)[
                len(top) // 2] if top else None,
            "max_rel_top50": max((r["rel"] for r in top),
                                 default=None),
            "band": "2 * per-cell propagated relative_spread "
                    "(tally-error record)",
            "top50": top,
        },
        "dose_table": {s: {"dose_Gy_h":
                           dose["cooling_step_doses"][s]["dose_Gy_h"],
                           "mc_rel_std": dose["cooling_step_doses"][s]
                           ["relative_std"]}
                       for s in ("2", "3")},
        "contact_proxy_vs_transported": ratios,
        "geometry_provenance": "self-produced (no lawful external"
                               " benchmark available; condition named"
                               " verbatim)",
    }
    OUT.write_text(json.dumps(rep, indent=1))
    print(json.dumps({
        "inside_band": f"{n_inside}/{len(top)}",
        "median_rel": rep["activation_comparison"]
        ["median_rel_top50"],
        "dose_Gy_h": {s: r["dose_Gy_h"]
                      for s, r in rep["dose_table"].items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
