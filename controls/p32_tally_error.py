#!/usr/bin/env python3
"""P32 condition discharge: propagate neutron tally MC error into activation.

The executed P32 chain consumed the neutron mesh tally's mean flux but never
carried its statistical error into the activation comparison. This addendum:

  1. reads the frozen neutron statepoint's per-(cell, energy-bin) mean and
     std_dev,
  2. writes K perturbed flux files — each non-empty bin scaled by an
     independent mean-preserving lognormal factor exp(s*z - s^2/2) with
     s^2 = ln(1 + (std/mean)^2) (the same convention as the robustness
     sampler; empty bins carry no signal and stay untouched),
  3. re-runs `actinv mesh` on each perturbed flux file (new sha per file —
     the spec hash-pins it, so each run is a distinct honest input),
  4. reports per-cell total-activity spread at each cooling step against
     the nominal mesh result.

Outputs results/p32_tally_error.json.
"""
import hashlib
import json
import math
import os
import random
import subprocess
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ND = "/home/connoravila/nuclear-data"
WORK = os.path.join(ND, "p32-work", "tally_error")
CHAIN = os.path.join(ND, "p32-work", "chain")
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
OMC_ENV = os.path.expanduser("~/.local/share/mamba/envs/openmc")
XS = os.path.join(ND, "endfb-vii.1-hdf5", "cross_sections.xml")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p32_seals.json")))
G1 = json.load(open(os.path.join(ROOT, "results", "g1_p32.json")))
OUT = os.path.join(ROOT, "results", "p32_tally_error.json")

SAMPLES = 8
SEED = 20260920
MODEL = SEALS["model"]
DENSITY = MODEL["density_g_cm3"]
VOX = MODEL["voxel_cm3"]

CG = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=6G",
      "-p", "MemorySwapMax=0", "-p", "TasksMax=128", "-p", "CPUQuota=200%",
      "--", "env"]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def omc_env():
    env = dict(os.environ)
    env.update(OMP_NUM_THREADS="2", OPENMC_CROSS_SECTIONS=XS,
               PATH=f"{OMC_ENV}/bin:{env['PATH']}", HOME=env["HOME"])
    return env


def run_env(cmd, cwd):
    env = omc_env()
    r = subprocess.run(
        CG + [f"PATH={env['PATH']}",
              f"OPENMC_CROSS_SECTIONS={XS}",
              f"OMP_NUM_THREADS=2", f"HOME={env['HOME']}",
              f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}"] + cmd,
        cwd=cwd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd} failed:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def mesh_std_devs():
    """Per-(cell, bin) relative std_dev from the frozen neutron statepoint."""
    sp = os.path.join(CHAIN, "neutron",
                      sorted(f for f in os.listdir(os.path.join(CHAIN, "neutron"))
                             if f.startswith("statepoint"))[-1])
    extract = textwrap.dedent(f"""
        import openmc, json
        sp = openmc.StatePoint({sp!r})
        t = None
        for k, tt in sp.tallies.items():
            if tt.name == "mesh_flux":
                t = tt
        # tally axes: (mesh cell, energy bin, score) — flatten C-order
        print(json.dumps({{"mean": t.mean.flatten().tolist(),
                          "std": t.std_dev.flatten().tolist(),
                          "shape": list(t.mean.shape)}}))
    """)
    env = omc_env()
    r = subprocess.run(CG + [f"PATH={env['PATH']}",
                             f"OPENMC_CROSS_SECTIONS={XS}",
                             f"HOME={env['HOME']}",
                             "python3", "-c", extract],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"statepoint read failed:\n{r.stderr[-2000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1]), sp


def write_perturbed(nominal_lines, flat_relstd, n_cells, seed_i):
    """Return (path, header+cells) for sample seed_i."""
    rng = random.Random(SEED + seed_i)
    p = os.path.join(WORK, f"flux_pert{seed_i}.ndjson")
    with open(p, "w") as f:
        for line in nominal_lines:
            rec = json.loads(line)
            if rec.get("record") == "cell":
                c = rec["ordinal"]
                new = []
                for g, v in enumerate(rec["flux_per_group"]):
                    rs = flat_relstd.get(f"{c}:{g}", 0.0)
                    if v > 0.0 and rs > 0.0:
                        s2 = math.log(1.0 + rs * rs)
                        new.append(v * math.exp(-0.5 * s2
                                                + math.sqrt(s2) * rng.gauss(0, 1)))
                    else:
                        new.append(v)
                rec["flux_per_group"] = new
            f.write(json.dumps(rec) + "\n")
    return p


def mesh_spec(flux_path):
    return {
        "spec": "actinv-mesh-spec-1",
        "title": "P32 voxelized Fe cube activation (tally-error sample)",
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


def run_mesh(spec_path, out_path):
    if not os.path.isfile(out_path):
        run_env([ACTINV, "mesh", spec_path, out_path], WORK)
    return out_path


def activities(result_path):
    """per-cell responses at each step -> {cell: {step: {activity, heat}}}"""
    per = {}
    with open(result_path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("record") != "cell":
                continue
            steps = rec.get("result", {}).get("steps") or []
            vals = {}
            for i, st in enumerate(steps):
                act = st.get("activity_Bq_per_g")
                heat = (st.get("heat_W_per_g") or {}).get("total")
                vals[str(i)] = {
                    "activity_Bq_per_g": (sum(act.values())
                                          if isinstance(act, dict) else act),
                    "heat_W_per_g": heat,
                }
            if vals:
                per[str(rec.get("ordinal", rec.get("id")))] = vals
    return per


def main():
    os.makedirs(WORK, exist_ok=True)
    nominal_flux = os.path.join(CHAIN, "flux.ndjson")
    nominal_result = os.path.join(CHAIN, "mesh_result.ndjson")
    # re-verify frozen inputs
    for k, p in (("flux", nominal_flux), ("mesh_result", nominal_result)):
        declared = (G1.get("inputs") or {}).get(k, {}).get("sha256")
        if declared and sha(p) != declared:
            raise RuntimeError(f"frozen {k} sha drifted")
    rel, sp = mesh_std_devs()
    n_cells = 64
    flat_relstd = {}
    mean, std, shape = rel["mean"], rel["std"], rel["shape"]
    # OpenMC mesh tally axis order: (cell, energy) flattened C-order
    n_bins = len(mean) // n_cells
    for c in range(n_cells):
        for g in range(n_bins):
            i = c * n_bins + g
            m, s = mean[i], std[i]
            if m > 0.0:
                flat_relstd[f"{c}:{g}"] = s / m
    # tally axes must be (cell, energy, score); assert before flattening
    shape = rel["shape"]
    if len(shape) < 2 or shape[0] != n_cells:
        raise RuntimeError(f"unexpected mesh tally shape {shape}")
    nominal_lines = open(nominal_flux).read().splitlines()
    nominal_acts = activities(nominal_result)
    samples = []
    for i in range(SAMPLES):
        fp = write_perturbed(nominal_lines, flat_relstd, n_cells, i)
        spath = os.path.join(WORK, f"mesh_spec_pert{i}.json")
        json.dump(mesh_spec(fp), open(spath, "w"), indent=1)
        opath = os.path.join(WORK, f"mesh_result_pert{i}.ndjson")
        run_mesh(spath, opath)
        samples.append(activities(opath))
    # per-cell, per-step spread on both responses
    report = {}
    for cell, steps in nominal_acts.items():
        report[cell] = {}
        for step, nom_dict in steps.items():
            entry = {}
            for resp in ("activity_Bq_per_g", "heat_W_per_g"):
                nom = nom_dict.get(resp)
                vals = [s.get(cell, {}).get(step, {}).get(resp)
                        for s in samples]
                vals = [v for v in vals if v is not None]
                if not vals or not nom or nom <= 0.0:
                    continue
                mu = sum(vals) / len(vals)
                var = (sum((v - mu) ** 2 for v in vals)
                       / max(len(vals) - 1, 1))
                entry[resp] = {
                    "nominal": nom,
                    "sample_mean": mu,
                    "sample_std": math.sqrt(var),
                    "relative_spread": math.sqrt(var) / nom,
                }
            if entry:
                report[cell][step] = entry
    flat = [r["relative_spread"] for cell in report.values()
            for step in cell.values() for r in step.values()]
    json.dump({
        "schema": "actinv-p32-tally-error-1",
        "basis": ("independent per-(cell,bin) lognormal flux perturbations "
                  "sized by the neutron mesh tally's std_dev; empty bins "
                  "carry no signal; %d mesh re-solves" % SAMPLES),
        "statepoint": {"path": sp, "sha256": sha(sp)},
        "samples": SAMPLES,
        "seed": SEED,
        "per_cell": report,
        "summary": {
            "max_relative_spread": max(flat) if flat else None,
            "median_relative_spread": (sorted(flat)[len(flat) // 2]
                                       if flat else None),
            "max_flux_relstd": max(flat_relstd.values())
            if flat_relstd else None,
        },
    }, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps({"p32_tally_error": "ok",
                      "max_relative_spread": max(flat) if flat else None},
                     indent=1))


if __name__ == "__main__":
    main()
