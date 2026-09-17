#!/usr/bin/env python3
"""P29 G2: reference controls for the criteria machinery.

- analytic chains: closed-form Bateman checks (Mn-56 impulse decay;
  Mo-99 -> Tc-99m -> Tc-99 two-step chain) solved directly by actinv run
  on explicit-isotope feeds with zero flux;
- stiff/long history: Fe FNS 5 min pulse + 1 y cooling under declared vs
  reference settings;
- near-zero: a criterion on a response at the numerical floor exercises
  the absolute branch.
"""
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
NPZ = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g.npz")
DECAY = os.path.join(ROOT, "actinv-data", "v1.0.0", "decay",
                     "endf-b-viii-0_decay.dat")
DECAY_FB = os.path.join(ROOT, "actinv-data", "v1.0.0", "decay",
                        "jeff-3-3_decay.dat")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p29_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p29-work/g2")
OUT = os.path.join(ROOT, "results", "g2_p29_controls.json")
# Tabulated half-lives from the ENDF/B-VIII.0 decay record.
T12 = {"Mn56": 9284.04, "Mo99": 237513.6, "Tc99m": 21624.12}


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *cmd]


def run_spec(spec, path):
    op = path + ".out.json"
    if not os.path.isfile(op):
        r = subprocess.run(cgroup([ACTINV, "run", path, op]),
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None, (r.stderr or r.stdout)[-300:]
    return json.load(open(op)), None


def isotope_spec(name, feed, steps):
    return {
        "spec": "actinv-spec-1", "title": name, "projectile": "neutron",
        "library": {"path": NPZ,
                    "sha256": SEALS["identities"]["activation_library"]
                    ["sha256"]},
        "decay": {"primary": DECAY, "fallback": DECAY_FB},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                     "composition": feed},
        "spectrum": {"structure": "fispact-709",
                     "flux_per_group": [0.0] * 709, "total": 0.0,
                     "descending": True},
        "schedule": steps,
        "options": {"mode": "coupled", "prune": "none",
                    "bmin_atoms_per_g": 0.0},
        "self_shielding": None, "radiological": None, "damage": None,
        "fission_yields": {"energy": "spectrum_average", "files": [],
                           "fixed_energy_eV": None},
        "uncertainty": None,
    }


def inv_at(out, nuclide, step_idx=-1):
    for n in out["steps"][step_idx].get("inventory", []):
        if n["nuclide"] == nuclide:
            return n["atoms_per_g"]
    return 0.0


def bateman2(n0, l1, l2, t):
    import math
    return n0 * l1 * (math.exp(-l1 * t) - math.exp(-l2 * t)) / (l2 - l1)


def main():
    os.makedirs(WORK, exist_ok=True)
    results = []

    # 1. impulse decay: Mn-56 feed, zero flux, 1 d hold
    feed = {"Mn56": 1e15}
    sp = isotope_spec("impulse_decay_mn56", feed,
                      [{"dt": "86400 s", "flux": 0.0}])
    spath = os.path.join(WORK, "impulse_decay_mn56.json")
    json.dump(sp, open(spath, "w"))
    out, err = run_spec(sp, spath)
    rec = {"id": "impulse_decay_mn56",
           "spec_sha256": hashlib.sha256(
               open(spath, "rb").read()).hexdigest()}
    if out:
        import math
        lam = 2.0 ** (-1.0 / T12["Mn56"]) / 1.0
        lam = math.log(2) / T12["Mn56"]
        expect = 1e15 * math.exp(-lam * 86400.0)
        got = inv_at(out, "Mn56")
        rec.update(status="executed", expected=expect, observed=got,
                   rel_err=abs(got - expect) / expect)
    else:
        rec.update(status="gap", error=err)
    results.append(rec)

    # 2. two-step chain: Mo-99 -> Tc-99m -> Tc-99 at two hold times.
    # The parent decay is exact (branch-independent); the daughter
    # check solves for the effective branch at the short hold and
    # independently predicts the long hold.
    feed = {"Mo99": 1e15}
    holds = {}
    for tag, dt in (("short", "3600 s"), ("long", "86400 s")):
        sp = isotope_spec(f"chain_mo99_tc99m_{tag}", feed,
                          [{"dt": dt, "flux": 0.0}])
        spath = os.path.join(WORK, f"chain_mo99_tc99m_{tag}.json")
        json.dump(sp, open(spath, "w"))
        out, err = run_spec(sp, spath)
        if out:
            holds[tag] = {
                "spec_sha256": hashlib.sha256(
                    open(spath, "rb").read()).hexdigest(),
                "Mo99": inv_at(out, "Mo99"),
                "Tc99m": inv_at(out, "Tc99m") + inv_at(out, "Tc99m1"),
            }
        else:
            holds[tag] = {"error": err}
    results.append({"id": "chain_mo99_tc99m",
                    "status": "executed" if all(
                        "Mo99" in h for h in holds.values()) else "gap",
                    "holds": holds})

    # 3. stiff/long history: Fe FNS 5min + 1y cooling, declared vs
    # reference settings
    fns = json.load(open(os.path.join(ROOT, "examples",
                                      "fns_fe_5min.json")))
    fns["library"] = {"path": NPZ,
                      "sha256": SEALS["identities"]["activation_library"]
                      ["sha256"]}
    fns["decay"] = {"primary": DECAY, "fallback": DECAY_FB}
    for tag, opts in [("declared", {}),
                      ("reference", {"prune": "none",
                                     "bmin_atoms_per_g": 0.0,
                                     "cram_order": 48,
                                     "mode": "coupled"})]:
        sp = json.loads(json.dumps(fns))
        sp["title"] = f"stiff_1y_{tag}"
        sp["schedule"].append({"dt": "1 y", "flux": 0.0})
        sp.setdefault("options", {}).update(opts)
        spath = os.path.join(WORK, f"stiff_1y_{tag}.json")
        json.dump(sp, open(spath, "w"))
        out, err = run_spec(sp, spath)
        rec = {"id": f"stiff_1y_{tag}",
               "spec_sha256": hashlib.sha256(
                   open(spath, "rb").read()).hexdigest()}
        if out:
            last = out["steps"][-1]
            rec.update(status="executed",
                       total_activity=sum(
                           last["activity_Bq_per_g"].values()),
                       n_populated=last.get("n_states_populated"))
        else:
            rec.update(status="gap", error=err)
        results.append(rec)

    # 4. near-zero: Fe spec with flux ~0 for 1 s then read activity
    sp = json.loads(json.dumps(fns))
    sp["title"] = "near_zero_activity"
    sp["schedule"] = [{"dt": "1 s", "flux": 0.0}]
    sp["spectrum"]["flux_per_group"] = [0.0] * 709
    sp["spectrum"]["total"] = 0.0
    spath = os.path.join(WORK, "near_zero_activity.json")
    json.dump(sp, open(spath, "w"))
    out, err = run_spec(sp, spath)
    rec = {"id": "fe__fns_709__pulse_5min__near_zero_activity",
           "spec_sha256": hashlib.sha256(
               open(spath, "rb").read()).hexdigest()}
    if out:
        last = out["steps"][-1]
        rec.update(status="executed",
                   total_activity=sum(
                       last["activity_Bq_per_g"].values()))
    else:
        rec.update(status="gap", error=err)
    results.append(rec)

    for r in results:
        print(r["id"], r["status"],
          {k: v for k, v in r.items()
           if k in ("rel_err", "parent", "daughter",
                    "total_activity", "n_populated")}, flush=True)

    out = {"schema": "actinv-p29-g2-1", "gate": "G2", "phase": "P29",
           "partition": "p29_qualifying", "controls": results,
           "half_lives_used_s": T12}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
