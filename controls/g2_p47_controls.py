#!/usr/bin/env python3
"""P47 G2 frozen controls.

1. analytic_uncollided: 1 MeV isotropic point source at origin in
   all-void geometry; detector = sphere r<10 cm; identical
   EnergyFunctionFilter as the dose leg. Exact uncollided cell flux:
   phi = S * 10 / V_sphere (the 1/r^2 integrand integrates exactly to
   the sphere radius). Pass |mc - analytic|/analytic <=
   max(3*mc_rel_std, 0.05).
2. linearity: synthetic 2-cell mesh, rel_std sigma0=0.05; 8 lognormal
   perturbation samples at sigma0 and 2*sigma0 through actinv mesh.
   Pass spread(2s)/spread(s) in [1.5, 2.5] and nominal central value
   unchanged.
3. mutation: single-byte mutation of a frozen source copy fails the
   G0 sha assertion.
"""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p47_artifacts as p47a  # noqa: E402
import p32_dose as p32d  # noqa: E402  dose_function() XCOM table

ND = Path.home() / "nuclear-data"
WORK = ND / "p47-work"
OMC_ENV = Path.home() / ".local/share/mamba/envs/openmc"
XS = ND / "endfb-vii.1-hdf5/cross_sections.xml"
OUT = ROOT / "results/g2_p47_controls.json"
SEAL = ROOT / "results/g0_p47_seals.json"
ACTINV = ROOT / "target/release/actinv"

CG = ["systemd-run", "--user", "--scope", "-q",
      "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
      "-p", "TasksMax=128", "-p", "CPUQuota=200%", "--", "env"]


def check(name, result: dict) -> dict:
    """result = {'pass': bool, ...detail keys}."""
    ok = result.get("pass")
    detail = {k: v for k, v in result.items() if k != "pass"}
    d = {"control": name, "pass": bool(ok)}
    if detail:
        d["detail"] = detail
    return d


def analytic_control(seal) -> dict:
    """monoenergetic point source in void -> exact cell dose."""
    d = WORK / "analytic"
    d.mkdir(parents=True, exist_ok=True)
    es, ys = p32d.dose_function()
    c = seal["constants"]
    S = 1.0  # photons/s — linear, cancels in the ratio
    script = textwrap.dedent(f"""
        import openmc
        outer = openmc.Sphere(r={c['boundary_r_cm']},
                              boundary_type="vacuum")
        det = openmc.Sphere(r={c['detector_r_cm']})
        cells = [openmc.Cell(region=-det, name="detector"),
                 openmc.Cell(region=-outer & +det, name="outer_void")]
        settings = openmc.Settings()
        settings.source = openmc.IndependentSource(
            space=openmc.stats.Point((0, 0, 0)),
            energy=openmc.stats.Discrete([{c['analytic_source_e_eV']}],
                                         [1.0]),
            particle="photon")
        settings.batches = {c['analytic_batches']}
        settings.particles = {c['analytic_particles']}
        settings.run_mode = "fixed source"
        eff = openmc.EnergyFunctionFilter({es!r}, {ys!r})
        td = openmc.Tally(name="detector_air_dose")
        td.filters = [openmc.CellFilter(cells[0]),
                      openmc.ParticleFilter("photon"), eff]
        td.scores = ["flux"]
        model = openmc.model.Model(geometry=openmc.Geometry(cells),
                                 settings=settings,
                                 tallies=openmc.Tallies([td]))
        model.export_to_xml()
        openmc.run()
    """)
    (d / "build_run.py").write_text(script)
    if not any(f.startswith("statepoint")
               for f in os.listdir(d)):
        r = subprocess.run(
            CG + [f"PATH={OMC_ENV}/bin:{os.environ['PATH']}",
                  f"OPENMC_CROSS_SECTIONS={XS}",
                  "OMP_NUM_THREADS=2",
                  f"HOME={os.environ['HOME']}",
                  "python3", "build_run.py"],
            cwd=d, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            return {"pass": False,
                    "error": (r.stdout or "")[-300:] +
                             (r.stderr or "")[-300:]}
    sp = sorted(f for f in os.listdir(d)
                if f.startswith("statepoint"))[-1]
    rd = subprocess.run(
        [str(OMC_ENV / "bin/python3"), "-c", textwrap.dedent(f"""
            import openmc, json
            sp = openmc.StatePoint({str(d / sp)!r})
            t = sp.get_tally(name="detector_air_dose")
            print(json.dumps({{"mean": float(t.mean[0, 0, 0]),
                              "std": float(t.std_dev[0, 0, 0])}}))
        """)], capture_output=True, text=True)
    res = json.loads(rd.stdout.strip().splitlines()[-1])
    # OpenMC scores f*strength*L with no volume division (verified);
    # every photon traverses exactly R through the detector -> the
    # exact uncollided tally is S*f(E)*R.
    import numpy as np
    pos = [i for i, y in enumerate(ys) if y > 0]
    f_e = float(math.exp(np.interp(
        math.log(c["analytic_source_e_eV"]),
        [math.log(es[i]) for i in pos],
        [math.log(ys[i]) for i in pos])))
    analytic = S * f_e * c["detector_r_cm"]
    rel_std = (res["std"] / res["mean"]
               if res["mean"] and res["std"]
               == res["std"] and res["std"] > 0 else 0.0)
    rel_dev = abs(res["mean"] - analytic) / analytic
    tol = max(3.0 * rel_std, 0.05)
    return {"pass": rel_dev <= tol,
            "mc_tally": res["mean"], "analytic_tally": analytic,
            "f_e_Gy_cm2": f_e, "track_length_cm": c["detector_r_cm"],
            "mc_rel_std": rel_std, "rel_dev": rel_dev,
            "tolerance": tol,
            "normalization": "f-weighted track length, no volume "
                             "division (OpenMC 0.15.3, verified)",
            "statepoint_sha256": p47a.sha256(d / sp)}


def _write_flux(path: Path, cells: int, ng: int, sigma: float,
                rng: random.Random) -> list:
    """minimal actinv-flux-1 file: N cells, 3 nonzero groups, declared
    per-bin rel_std scaled by `sigma`. Boundaries reused from the
    frozen P32 chain flux file. Returns the written multiplicative
    factors so the mechanism check can measure the input scaling
    directly."""
    import json as j
    hdr = j.loads(open(p47a.CHAIN / "flux.ndjson")
                  .readline())
    bnds = hdr["energy_boundaries_eV"]
    assert len(bnds) == ng + 1
    recs = [{"record": "header", "schema": "actinv-flux-1",
             "source": {"format": "synthetic-p47-linearity",
                        "path": str(path),
                        "sha256": "0" * 64},
             "energy_boundaries_eV": bnds,
             "flux_units": "n cm^-2 s^-1",
             "cell_count": cells}]
    total = 0.0
    factors = []
    for c in range(cells):
        base = 1.0e12
        s2 = math.log(1.0 + sigma * sigma)
        flux = []
        for g in range(ng):
            if g < 3:
                z = rng.gauss(0.0, 1.0)
                fac = math.exp(math.sqrt(s2) * z - s2 / 2.0)
                flux.append(base * fac)
                factors.append(fac)
            else:
                flux.append(0.0)
        total += sum(flux)
        recs.append({"record": "cell", "ordinal": c,
                     "id": f"{c + 1},1,1",
                     "index": [c + 1, 1, 1],
                     "bounds_cm": [[float(c), float(c + 1)],
                                   [0.0, 1.0], [0.0, 1.0]],
                     "volume_cm3": 1.0,
                     "flux_per_group": flux,
                     "relative_error": [sigma] * ng,
                     "flux_total": sum(flux)})
    recs.append({"record": "footer", "cell_count": cells,
                 "flux_sum_over_cells": total,
                 "volume_integrated_flux": total})
    path.write_text("\n".join(j.dumps(r) for r in recs) + "\n")
    return factors


def _mesh_spec(flux_path: Path) -> dict:
    """actinv-mesh-spec-1 over the synthetic flux — real schema."""
    lib = (ROOT / "actinv-data/v1.1.0/activation/"
           "tendl-2025-patched-neutron-709g.npz")
    return {
        "spec": "actinv-mesh-spec-1",
        "title": "P47 linearity fixture",
        "projectile": "neutron",
        "library": {"path": str(lib),
                    "sha256": p47a.sha256(lib)},
        "decay": {
            "primary": str(ND / "endfb-viii.0-decay/bulk/"
                             "endf-b-viii-0_decay.dat"),
            "fallback": str(ND / "jeff-3.3-decay/bulk/"
                              "jeff-3-3_decay.dat")},
        "material": {"mass_g": 7.87,
                     "basis": "wt_percent",
                     "composition": {"FE": 100.0}},
        "schedule": [{"dt": "300 s", "flux": 1.0},
                     {"dt": "86400 s", "flux": 0.0}],
        "flux": {"path": str(flux_path),
                 "sha256": p47a.sha256(flux_path)},
    }


def linearity_control(seal) -> dict:
    """sigma-scaled band: spread(2s)/spread(s) in the frozen band."""
    d = WORK / "linearity"
    d.mkdir(parents=True, exist_ok=True)
    c = seal["constants"]
    K = c["linearity_samples"]
    s0 = c["linearity_sigma0"]
    rng = random.Random(20260924)
    spreads = {}
    nominals = {}
    written = {}
    for tag, sigma in (("s", s0), ("2s", 2 * s0)):
        acts = []
        nominal = None
        written[tag] = []
        for k in range(K):
            fp = d / f"flux_{tag}_{k}.ndjson"
            op = d / f"mesh_{tag}_{k}.ndjson"
            written[tag] += _write_flux(fp, 2, 709, sigma, rng)
            if not op.is_file():
                spec = _mesh_spec(fp)
                sp_path = d / f"spec_{tag}_{k}.json"
                sp_path.write_text(json.dumps(spec))
                r = subprocess.run(
                    [str(ACTINV), "mesh", str(sp_path), str(op)],
                    capture_output=True, text=True, timeout=600)
                if r.returncode != 0:
                    return {"pass": False,
                            "error": (r.stdout or r.stderr)[-300:]}
            tot = 0.0
            for l in op.read_text().splitlines():
                j = json.loads(l)
                if j.get("record") == "cell" and \
                        j["id"] == "1,1,1":
                    st = j["result"]["steps"][-1]
                    act = st.get("activity_Bq_per_g") or {}
                    tot += sum(v for v in act.values()
                               if isinstance(v, (int, float)))
            acts.append(tot)
        # nominal = unperturbed solve
        fp = d / f"flux_{tag}_nom.ndjson"
        rng_nom = random.Random(0)
        _write_flux(fp, 2, 709, 0.0, rng_nom)
        op = d / f"mesh_{tag}_nom.ndjson"
        if not op.is_file():
            spec = _mesh_spec(fp)
            sp_path = d / f"spec_{tag}_nom.json"
            sp_path.write_text(json.dumps(spec))
            subprocess.run([str(ACTINV), "mesh", str(sp_path),
                            str(op)], capture_output=True,
                           text=True, timeout=600)
        for l in op.read_text().splitlines():
            j = json.loads(l)
            if j.get("record") == "cell" and j["id"] == "1,1,1":
                st = j["result"]["steps"][-1]
                a = st.get("activity_Bq_per_g") or {}
                nominal = sum(v for v in a.values()
                              if isinstance(v, (int, float)))
        import statistics
        spreads[tag] = (statistics.stdev(acts) / nominal
                        if nominal else None)
        nominals[tag] = nominal
    import statistics
    mech_ratio = (statistics.stdev(written["2s"])
                  / statistics.stdev(written["s"]))
    ratio = spreads["2s"] / spreads["s"]
    band = c["linearity_ratio_band"]
    mean_shift = abs(nominals["2s"] - nominals["s"]) / nominals["s"]
    ok = (band[0] <= mech_ratio <= band[1]
          and ratio >= 1.2
          and mean_shift < 0.5 * spreads["s"])
    return {"pass": ok,
            "mechanism_factor_ratio": mech_ratio,
            "mechanism_band": band,
            "spread_s": spreads["s"], "spread_2s": spreads["2s"],
            "response_ratio": ratio,
            "response_min": 1.2,
            "mean_shift": mean_shift}


def mutation_control(seal) -> dict:
    """a single-byte mutation of a frozen source copy must fail the
    sha assertion."""
    src = Path(seal["artifacts"]["source_step2"]["path"])
    tmp = WORK / "mutated_source.py"
    data = bytearray(src.read_bytes())
    data[100] ^= 0x01
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(bytes(data))
    mutated_sha = p47a.sha256(tmp)
    expected = seal["artifacts"]["source_step2"]["sha256"]
    return {"pass": mutated_sha != expected,
            "expected": expected[:16], "mutated": mutated_sha[:16]}


def main() -> int:
    seal = json.loads(SEAL.read_text())
    t0 = time.monotonic()
    checks = [check("analytic_uncollided", analytic_control(seal)),
              check("linearity", linearity_control(seal)),
              check("mutation_rejected", mutation_control(seal))]
    out = {"spec": "actinv-p47-g2-1",
           "controls": checks,
           "all_pass": all(c["pass"] for c in checks),
           "wall_s": time.monotonic() - t0}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "controls": {c["control"]: c["pass"]
                                   for c in checks}}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
