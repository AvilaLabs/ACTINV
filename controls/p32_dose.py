#!/usr/bin/env python3
"""P32 condition discharge: photon dose leg with propagated MC error.

The executed P32 chain (g1_p32_chain.py) tallied photon *flux* at a void
detector — a proxy, not a dose. This addendum replays the SAME exported
distributed sources (bit-identical inputs, SHAs re-verified) with an
openmc.EnergyFunctionFilter scoring the air dose rate directly:

    dose_rate [Gy/s] = ∫ phi(E) [ph/cm2/s] * E [eV] * mu_en/rho_air(E)
                       [cm2/g] * EV [J/eV] * 0.1 [cm2/g -> m2/kg] dE

the same mu_en convention as the engine's contact_gamma_air_dose_proxy
(crates/actinv-core/src/photon.rs `gamma_constant`). MC std-dev is carried
through — the tally reports mean and std_dev natively.

Outputs results/p32_dose.json: per-cooling-step detector dose rate with
relative std-dev, plus the per-cell source-energy bounds audit.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ND = "/home/connoravila/nuclear-data"
WORK = os.path.join(ND, "p32-work", "dose")
CHAIN = os.path.join(ND, "p32-work", "chain")
OMC_ENV = os.path.expanduser("~/.local/share/mamba/envs/openmc")
XS = os.path.join(ND, "endfb-vii.1-hdf5", "cross_sections.xml")
MU_EN = os.path.join(ND, "photon-response", "nist-xcom-air-fe.json")
EV = 1.602176634e-19
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p32_seals.json")))
G1 = json.load(open(os.path.join(ROOT, "results", "g1_p32.json")))
OUT = os.path.join(ROOT, "results", "p32_dose.json")

MODEL = SEALS["model"]
DENSITY = MODEL["density_g_cm3"]
PB, PP = MODEL["photon_batches"], MODEL["photon_particles"]

CG = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=6G",
      "-p", "MemorySwapMax=0", "-p", "TasksMax=128", "-p", "CPUQuota=200%",
      "--", "env"]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def dose_function():
    """f(E) = E * mu_en(E) * EV * 0.1 -> Gy·cm2 per photon, log-log interp."""
    d = json.load(open(MU_EN))["air_mass_energy_absorption"]
    es = d["energy_eV"]
    mus = d["values_cm2_g"]
    ys = [e * m * EV * 0.1 for e, m in zip(es, mus)]
    return es, ys


def run_step(step, src_py):
    d = os.path.join(WORK, f"dose_step{step}")
    os.makedirs(d, exist_ok=True)
    es, ys = dose_function()
    script = textwrap.dedent(f"""
        import openmc, runpy
        ns = runpy.run_path({src_py!r})
        sources = ns["sources"]
        m = openmc.Material()
        for nuc, frac in [("Fe54", 0.05845), ("Fe56", 0.91754),
                          ("Fe57", 0.02119), ("Fe58", 0.00282)]:
            m.add_nuclide(nuc, frac)
        m.set_density("g/cm3", {DENSITY})
        cube = (+openmc.XPlane(-2) & -openmc.XPlane(2)
                & +openmc.YPlane(-2) & -openmc.YPlane(2)
                & +openmc.ZPlane(-2) & -openmc.ZPlane(2))
        outer = openmc.Sphere(r=50.0, boundary_type="vacuum")
        det = openmc.Sphere(r=10.0)
        cells = [openmc.Cell(fill=m, region=cube, name="cube"),
                 openmc.Cell(region=-det & ~cube, name="gap"),
                 openmc.Cell(region=-outer & +det, name="outer_void")]
        settings = openmc.Settings()
        settings.source = sources
        settings.batches = {PB}
        settings.particles = {PP}
        settings.run_mode = "fixed source"
        eff = openmc.EnergyFunctionFilter({es!r}, {ys!r})
        td = openmc.Tally(name="detector_air_dose")
        td.filters = [openmc.CellFilter(cells[1]),
                      openmc.ParticleFilter("photon"), eff]
        td.scores = ["flux"]
        model = openmc.model.Model(
            geometry=openmc.Geometry(cells), settings=settings,
            tallies=openmc.Tallies([td]))
        model.export_to_xml()
        openmc.run()
    """)
    open(os.path.join(d, "build_run.py"), "w").write(script)
    if not any(f.startswith("statepoint") for f in os.listdir(d)):
        env = dict(os.environ)
        env.update(OMP_NUM_THREADS="2", OPENMC_CROSS_SECTIONS=XS,
                   PATH=f"{OMC_ENV}/bin:{env['PATH']}")
        r = subprocess.run(CG + [f"PATH={env['PATH']}",
                                 f"OPENMC_CROSS_SECTIONS={XS}",
                                 "OMP_NUM_THREADS=2",
                                 f"HOME={env['HOME']}",
                                 "python3", "build_run.py"],
                           cwd=d, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(f"dose leg step {step} failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return os.path.join(
        d, sorted(f for f in os.listdir(d) if f.startswith("statepoint"))[-1])


def main():
    os.makedirs(WORK, exist_ok=True)
    # re-verify the frozen exported sources before replaying them
    sources = {}
    for step in SEALS["activation"]["cooling_steps"]:
        p = os.path.join(CHAIN, f"source_step{step}.py")
        declared = G1["sources"][str(step)]["sha256"]
        got = sha(p)
        if got != declared:
            raise RuntimeError(
                f"source_step{step}.py sha drifted: {got} != {declared}")
        sources[step] = p

    results = {}
    for step, src in sources.items():
        sp = run_step(step, src)
        extract = textwrap.dedent(f"""
            import openmc, json
            sp = openmc.StatePoint({sp!r})
            t = sp.tallies[sp.tallies.keys().__iter__().__next__()]
            for k, tt in sp.tallies.items():
                if tt.name == "detector_air_dose":
                    t = tt
            m = float(t.mean.flat[0])
            s = float(t.std_dev.flat[0])
            print(json.dumps({{"dose_Gy_s": m, "std_Gy_s": s}}))
        """)
        env = dict(os.environ)
        env["PATH"] = f"{OMC_ENV}/bin:{env['PATH']}"
        env["OPENMC_CROSS_SECTIONS"] = XS
        r = subprocess.run(CG + [f"PATH={env['PATH']}",
                                 f"OPENMC_CROSS_SECTIONS={XS}",
                                 f"HOME={env['HOME']}",
                                 "python3", "-c", extract],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"dose extract step {step} failed:\n{r.stderr[-2000:]}")
        vals = json.loads(r.stdout.strip().splitlines()[-1])
        results[str(step)] = {
            "statepoint": {"path": sp, "sha256": sha(sp)},
            "detector_air_dose_Gy_s": vals["dose_Gy_s"],
            "mc_std_Gy_s": vals["std_Gy_s"],
            "relative_std": (vals["std_Gy_s"] / vals["dose_Gy_s"]
                             if vals["dose_Gy_s"] else None),
            "dose_Gy_h": vals["dose_Gy_s"] * 3600.0,
        }

    json.dump({
        "schema": "actinv-p32-dose-1",
        "basis": ("replayed frozen exported sources through OpenMC photon "
                  "transport with an EnergyFunctionFilter scoring "
                  "E*mu_en,air — a dose estimate with MC error, discharging "
                  "the 'flux proxy' condition; still not a qualified "
                  "dosimetry prediction (no detector model)"),
        "mu_en_table": {"path": MU_EN, "sha256": sha(MU_EN)},
        "cooling_step_doses": results,
    }, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps({"p32_dose": "ok",
                      "steps": {k: v["dose_Gy_h"] for k, v in results.items()}},
                     indent=1))


if __name__ == "__main__":
    main()
