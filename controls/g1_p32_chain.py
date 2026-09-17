#!/usr/bin/env python3
"""P32 G1: execute the frozen R2S chain end-to-end.

Stages (all subprocesses run under the enforced systemd cgroup):
  A. OpenMC neutron fixed-source run on the voxelized Fe cube ->
     mesh x energy flux tally (for ACTINV) + cell flux tally (depletion leg)
  B. actinv import-flux openmc -> actinv-flux-1 ndjson
  C. actinv mesh -> actinv-mesh-result-1 (per-voxel activation + photons)
  D. actinv export-openmc-mesh -> per-cooling-step spatial sources
  E. OpenMC photon fixed-source runs consuming the exported sources
  F. openmc.deplete predictor leg on the same voxel materials
  G. emit results/g1_p32.json
"""
import hashlib
import json
import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ND = "/home/connoravila/nuclear-data"
WORK = os.path.join(ND, "p32-work", "chain")
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
OMC_ENV = os.path.expanduser("~/.local/share/mamba/envs/openmc")
XS = os.path.join(ND, "endfb-vii.1-hdf5", "cross_sections.xml")
GROUPS_JSON = os.path.join(ROOT, "crates", "actinv-data", "data",
                           "fispact_709_groups.json")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p32_seals.json")))
OUT = os.path.join(ROOT, "results", "g1_p32.json")

MODEL = SEALS["model"]
N_VOX = MODEL["voxels"]          # 64, a 4x4x4 grid
SIDE = 4                         # voxels per axis; cube spans [-2,2] cm
VOX = MODEL["voxel_cm3"]         # 1.0 cm3
DENSITY = MODEL["density_g_cm3"]
SRC_RATE = MODEL["source"]["strength_n_per_s"]
ENERGY = MODEL["source"]["energy_eV"]
BATCHES, PARTICLES = MODEL["batches"], MODEL["particles"]
PB, PP = MODEL["photon_batches"], MODEL["photon_particles"]

CG = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=6G",
      "-p", "MemorySwapMax=0", "-p", "TasksMax=128", "-p", "CPUQuota=200%",
      "--", "env"]
OMC_BASE_ENV = dict(
    OMP_NUM_THREADS="2",
    OPENMC_CROSS_SECTIONS=XS,
    PATH=f"{OMC_ENV}/bin:{os.environ['PATH']}",
    HOME=os.environ["HOME"],
)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def run(cmd, cwd, extra_env=None):
    env = dict(OMC_BASE_ENV)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(CG + [f"PATH={env['PATH']}",
                             f"OPENMC_CROSS_SECTIONS={env['OPENMC_CROSS_SECTIONS']}",
                             f"OMP_NUM_THREADS={env['OMP_NUM_THREADS']}",
                             f"HOME={env['HOME']}"] + cmd,
                       cwd=cwd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd} failed:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def stage_a_neutron():
    """Voxelized Fe cube, 14.1 MeV point source, mesh+energy flux tally."""
    d = os.path.join(WORK, "neutron")
    os.makedirs(d, exist_ok=True)
    groups = json.load(open(GROUPS_JSON))
    bnd = groups["boundaries_eV"] if isinstance(groups, dict) else groups
    # fispact-709 is stored descending; OpenMC EnergyFilter needs ascending
    bnd = sorted(bnd)
    script = textwrap.dedent(f"""
        import openmc
        bnd = {bnd!r}
        mats, cells = [], []
        for k in range(4):
            for j in range(4):
                for i in range(4):
                    m = openmc.Material(name=f"v_{{i+1}}_{{j+1}}_{{k+1}}")
                    for nuc, frac in [("Fe54", 0.05845), ("Fe56", 0.91754),
                                      ("Fe57", 0.02119), ("Fe58", 0.00282)]:
                        m.add_nuclide(nuc, frac)
                    m.set_density("g/cm3", {DENSITY})
                    m.volume = {VOX}
                    m.depletable = True
                    mats.append(m)
                    x0, y0, z0 = -2.0 + i, -2.0 + j, -2.0 + k
                    reg = (+openmc.XPlane(x0) & -openmc.XPlane(x0 + 1.0)
                           & +openmc.YPlane(y0) & -openmc.YPlane(y0 + 1.0)
                           & +openmc.ZPlane(z0) & -openmc.ZPlane(z0 + 1.0))
                    cells.append(openmc.Cell(fill=m, region=reg,
                                             name=f"v_{{i+1}}_{{j+1}}_{{k+1}}"))
        outer = openmc.Sphere(r=50.0, boundary_type="vacuum")
        void = openmc.Cell(region=-outer)
        for c in cells:
            void.region &= ~c.region
        cells.append(void)
        src = openmc.IndependentSource()
        src.energy = openmc.stats.Discrete([{ENERGY}], [1.0])
        src.space = openmc.stats.Point((0.0, 0.0, 0.0))
        src.angle = openmc.stats.Isotropic()
        src.strength = {SRC_RATE}
        src.particle = "neutron"
        settings = openmc.Settings()
        settings.source = src
        settings.batches = {BATCHES}
        settings.particles = {PARTICLES}
        settings.run_mode = "fixed source"
        mesh = openmc.RegularMesh()
        mesh.lower_left = (-2.0, -2.0, -2.0)
        mesh.upper_right = (2.0, 2.0, 2.0)
        mesh.dimension = (4, 4, 4)
        t1 = openmc.Tally(name="mesh_flux")
        t1.filters = [openmc.MeshFilter(mesh), openmc.EnergyFilter(bnd)]
        t1.scores = ["flux"]
        t1.estimator = "tracklength"
        t2 = openmc.Tally(name="cell_flux")
        t2.filters = [openmc.CellFilter(cells[:-1])]
        t2.scores = ["flux"]
        model = openmc.model.Model(
            geometry=openmc.Geometry(cells), settings=settings,
            tallies=openmc.Tallies([t1, t2]), materials=mats)
        model.export_to_xml()
        import json
        json.dump({{"mesh_tally_id": t1.id, "cell_tally_id": t2.id,
                   "mesh_id": mesh.id}},
                  open("tally_ids.json", "w"))
        openmc.run()
    """)
    open(os.path.join(d, "build_run.py"), "w").write(script)
    sp = sorted(
        (os.path.join(d, f) for f in os.listdir(d) if f.startswith("statepoint")),
        key=os.path.getmtime)
    if not sp:
        run(["python3", "build_run.py"], d)
        sp = [os.path.join(d, f) for f in os.listdir(d)
              if f.startswith("statepoint")]
    return d, sp[-1], json.load(open(os.path.join(d, "tally_ids.json")))


def stage_b_import(sp, tally_id):
    out = os.path.join(WORK, "flux.ndjson")
    if not os.path.isfile(out):
        run([ACTINV, "import-flux", "openmc", sp, out,
             "--tally", str(tally_id), "--source-rate", str(SRC_RATE)],
            WORK)
    return out


def stage_c_mesh(flux_path):
    spec = {
        "spec": "actinv-mesh-spec-1",
        "title": "P32 voxelized Fe cube activation",
        "projectile": "neutron",
        "library": {
            "path": SEALS["activation_data"]["library"],
            "sha256": SEALS["activation_data"]["library_sha256"],
        },
        "decay": {
            "primary": SEALS["activation_data"]["decay_primary"],
            "fallback": SEALS["activation_data"]["decay_fallback"],
        },
        "material": {"mass_g": DENSITY * VOX, "basis": "wt_percent",
                     "composition": {"FE": 100.0}},
        "schedule": [{"dt": "300 s", "flux": 1.0},
                     {"dt": "86400 s", "flux": 0.0},
                     {"dt": "86400 s", "flux": 0.0}],
        "flux": {"path": flux_path, "sha256": sha(flux_path)},
    }
    spath = os.path.join(WORK, "mesh_spec.json")
    json.dump(spec, open(spath, "w"), indent=1)
    out = os.path.join(WORK, "mesh_result.ndjson")
    if not os.path.isfile(out):
        run([ACTINV, "mesh", spath, out], WORK)
    return spath, out


def stage_d_export(mesh_result):
    outs = {}
    for step in SEALS["activation"]["cooling_steps"]:
        out = os.path.join(WORK, f"source_step{step}.py")
        if not os.path.isfile(out):
            run([ACTINV, "export-openmc-mesh", mesh_result, str(step), out],
                WORK)
        outs[step] = out
    return outs


def stage_e_photon(sources):
    outs = {}
    for step, src_py in sources.items():
        d = os.path.join(WORK, f"photon_step{step}")
        os.makedirs(d, exist_ok=True)
        script = textwrap.dedent(f"""
            import openmc, runpy
            ns = runpy.run_path({src_py!r})
            sources = ns["sources"]
            total = ns["TOTAL_PHOTONS_S"]
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
            mesh = openmc.RegularMesh()
            mesh.lower_left = (-2.0, -2.0, -2.0)
            mesh.upper_right = (2.0, 2.0, 2.0)
            mesh.dimension = (4, 4, 4)
            tm = openmc.Tally(name="photon_mesh_flux")
            tm.filters = [openmc.MeshFilter(mesh),
                          openmc.ParticleFilter("photon")]
            tm.scores = ["flux"]
            td = openmc.Tally(name="detector_flux")
            td.filters = [openmc.CellFilter(cells[1]),
                          openmc.ParticleFilter("photon")]
            td.scores = ["flux"]
            model = openmc.model.Model(
                geometry=openmc.Geometry(cells), settings=settings,
                tallies=openmc.Tallies([tm, td]))
            model.export_to_xml()
            openmc.run()
            open("total_photons_s.txt", "w").write(repr(total))
        """)
        open(os.path.join(d, "build_run.py"), "w").write(script)
        if not any(f.startswith("statepoint") for f in os.listdir(d)):
            run(["python3", "build_run.py"], d)
        outs[step] = os.path.join(
            d, sorted(f for f in os.listdir(d)
                      if f.startswith("statepoint"))[-1])
    return outs


def stage_f_depletion(neutron_dir):
    d = os.path.join(WORK, "depletion")
    os.makedirs(d, exist_ok=True)
    res = os.path.join(neutron_dir, "depletion_results.h5")
    if not os.path.isfile(res):
        script = textwrap.dedent(f"""
            import glob, os, numpy as np
            import openmc, openmc.deplete
            work = {neutron_dir!r}
            os.chdir(work)
            chain_path = os.path.join({d!r}, "chain.xml")
            if not os.path.isfile(chain_path):
                files = sorted(glob.glob(
                    {ND + "/p32-work/endf-decay/dec-*.endf"!r}))
                nfiles = sorted(glob.glob(
                    {ND + "/tendl-2025/files/n/n-*.tendl"!r}))
                chain = openmc.deplete.Chain.from_endf(
                    decay_files=files, fpy_files=[],
                    neutron_files=nfiles, progress=False)
                chain.export_to_xml(chain_path)
            model = openmc.Model.from_xml(
                "geometry.xml", "materials.xml", "settings.xml")
            mats = [m for m in model.materials if m.depletable]
            import os as _os
            have = {{f[:-3] for f in _os.listdir(
                {ND + "/endfb-vii.1-hdf5/neutron"!r})
                if f.endswith(".h5")}}
            nuclides = [e + str(a) for e in
                        ("Fe", "Mn", "Cr", "Co", "V", "Ti", "Sc")
                        for a in range(44, 70) if e + str(a) in have]
            fluxes, micros = openmc.deplete.get_microxs_and_flux(
                model, mats, nuclides=nuclides, chain_file=chain_path,
                energies=[0.0, 20e6])
            op = openmc.deplete.IndependentOperator(
                mats, fluxes, micros, chain_file=chain_path,
                normalization_mode="source-rate")
            integ = openmc.deplete.PredictorIntegrator(
                op, [300.0, 86400.0, 86400.0],
                source_rates=[{SRC_RATE}, 0.0, 0.0])
            integ.output_dir = {d!r}
            integ.integrate()
        """)
        open(os.path.join(d, "deplete.py"), "w").write(script)
        run(["python3", "deplete.py"], d)
    return res


def main():
    os.makedirs(WORK, exist_ok=True)
    ndir, statepoint, tids = stage_a_neutron()
    flux_path = stage_b_import(statepoint, tids["mesh_tally_id"])
    spec_path, mesh_result = stage_c_mesh(flux_path)
    sources = stage_d_export(mesh_result)
    photon_sps = stage_e_photon(sources)
    depletion = stage_f_depletion(ndir)

    evidence = {
        "schema": "actinv-p32-g1-1", "phase": "P32", "gate": "G1",
        "artifacts": {
            "statepoint": {"path": statepoint, "sha256": sha(statepoint)},
            "flux_ndjson": {"path": flux_path, "sha256": sha(flux_path)},
            "mesh_spec": {"path": spec_path, "sha256": sha(spec_path)},
            "mesh_result": {"path": mesh_result,
                            "sha256": sha(mesh_result)},
            "depletion_results": {"path": depletion,
                                  "sha256": sha(depletion)},
        },
        "sources": {str(s): {"path": p, "sha256": sha(p)}
                    for s, p in sources.items()},
        "photon_statepoints": {str(s): {"path": p, "sha256": sha(p)}
                               for s, p in photon_sps.items()},
        "chain_executed": True,
    }
    json.dump(evidence, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps({"g1_p32": "chain executed",
                      "artifacts": len(evidence["artifacts"])}, indent=1))


if __name__ == "__main__":
    main()
