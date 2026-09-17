#!/usr/bin/env python3
"""P32 G1 checker: verify the executed chain's evidence independently."""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
EV = os.path.join(RES, "g1_p32.json")
SEALS = json.load(open(os.path.join(RES, "g0_p32_seals.json")))
OUT = os.path.join(RES, "g1_p32_check.json")
OMC_ENV = os.path.expanduser("~/.local/share/mamba/envs/openmc")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def openmc_python(code):
    env = dict(os.environ)
    env["PATH"] = f"{OMC_ENV}/bin:{env['PATH']}"
    env["OPENMC_CROSS_SECTIONS"] = (
        "/home/connoravila/nuclear-data/endfb-vii.1-hdf5/"
        "cross_sections.xml")
    r = subprocess.run(["python3", "-c", code], capture_output=True,
                       text=True, env=env, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"openmc inspection failed: {r.stderr[-2000:]}")
    return json.loads(r.stdout)


def check(ev, fs):
    if ev.get("schema") != "actinv-p32-g1-1" or ev.get("phase") != "P32":
        fs.append("schema/phase wrong")
    arts = ev.get("artifacts", {})
    for key in ("statepoint", "flux_ndjson", "mesh_spec",
                "mesh_result", "depletion_results"):
        a = arts.get(key, {})
        if not os.path.isfile(a.get("path", "")):
            fs.append(f"artifact missing: {key}")
        elif sha(a["path"]) != a.get("sha256"):
            fs.append(f"artifact digest drifted: {key}")

    if not fs:
        # neutron statepoint: real statepoint with the mesh tally
        sp = arts["statepoint"]["path"]
        info = openmc_python(f"""
import openmc, json
sp = openmc.StatePoint({sp!r})
t = sp.get_tally(id={1})
f = t.find_filter(openmc.MeshFilter)
e = t.find_filter(openmc.EnergyFilter)
print(json.dumps({{"scores": t.scores,
 "dim": [int(x) for x in f.mesh.dimension],
 "nbins": int(e.num_bins)}}))
""")
        if info["scores"] != ["flux"] or info["dim"] != [4, 4, 4] \
                or info["nbins"] != 709:
            fs.append(f"statepoint tally not the frozen mesh flux: {info}")

        # flux file: 64 cells, real bounds/volumes, nonempty fluxes
        hdr = cells = None
        cells = {}
        with open(arts["flux_ndjson"]["path"]) as f:
            for line in f:
                r = json.loads(line)
                if r["record"] == "header":
                    hdr = r
                elif r["record"] == "cell":
                    cells[r["id"]] = r
        if hdr is None or hdr["schema"] != "actinv-flux-1" \
                or hdr["cell_count"] != 64:
            fs.append("flux file header wrong")
        if len(cells) != 64:
            fs.append(f"flux file has {len(cells)} cells, want 64")
        nz = sum(1 for c in cells.values() if c["flux_total"] > 0)
        if nz < 8:
            fs.append(f"only {nz} cells carry flux; source not distributed")
        for cid, c in cells.items():
            if c.get("bounds_cm") is None or c.get("volume_cm3") is None:
                fs.append(f"cell {cid} lacks bounds/volume")
                break
            if abs(c["volume_cm3"] - 1.0) > 1e-12:
                fs.append(f"cell {cid} volume {c['volume_cm3']} != 1.0")
                break
        if hdr and len(hdr["energy_boundaries_eV"]) != 710:
            fs.append("flux energy boundaries not 709-group")

        # mesh result: 64 cells each with 3 steps and photon_source
        mcells = 0
        photon_ok = True
        with open(arts["mesh_result"]["path"]) as f:
            for line in f:
                r = json.loads(line)
                if r["record"] != "cell":
                    continue
                mcells += 1
                steps = r["result"].get("steps", [])
                if len(steps) != 3:
                    photon_ok = False
                    continue
                for s in steps:
                    ps = s.get("photon_source")
                    if ps is None or ps["total_photons_s"] < 0:
                        photon_ok = False
        if mcells != 64 or not photon_ok:
            fs.append(f"mesh result incomplete: {mcells} cells")

        # source files: all sealed cooling steps, 64 sources each
        want = {str(s) for s in SEALS["activation"]["cooling_steps"]}
        if set(ev.get("sources", {})) != want:
            fs.append(f"source steps {set(ev.get('sources', {}))} "
                      f"!= sealed {want}")
        for step, s in ev.get("sources", {}).items():
            text = open(s["path"]).read()
            n = text.count("sources.append(openmc.IndependentSource")
            boxes = len(re.findall(r"openmc\.stats\.Box", text))
            m = re.search(r"TOTAL_PHOTONS_S = ([0-9.e+-]+)", text)
            if n != 64 or boxes != 64 or not m:
                fs.append(f"step {step} source not spatial/complete: "
                          f"{n} sources, {boxes} boxes")

        # photon statepoints: executed runs with our tallies
        for step, s in ev.get("photon_statepoints", {}).items():
            info = openmc_python(f"""
import openmc, json
sp = openmc.StatePoint({s['path']!r})
names = [t.name for t in sp.tallies.values()]
print(json.dumps({{"tallies": names}}))
""")
            if "photon_mesh_flux" not in info["tallies"]:
                fs.append(f"step {step} photon run lacks mesh tally")

        # depletion results: per-material atoms
        dep = arts["depletion_results"]["path"]
        info = openmc_python(f"""
import openmc.deplete, json
r = openmc.deplete.ResultsList({dep!r})
t = r.get_times()
print(json.dumps({{"n_times": len(t), "times": list(t)}}))
""")
        if info["n_times"] != 4 \
                or abs(info["times"][1] - 300.0 / 86400.0) > 1e-6 \
                or abs(info["times"][2] - (300.0 / 86400.0 + 1.0)) > 1e-6:
            fs.append(f"depletion times wrong: {info}")


def main():
    ev = json.load(open(EV))
    fs = []
    check(ev, fs)

    planted = rejected = 0
    for label, mut in [
        ("schema", lambda v: v.__setitem__("schema", "x")),
        ("digest", lambda v: v["artifacts"]["flux_ndjson"]
            .__setitem__("sha256", "0" * 64)),
        ("sources", lambda v: v["sources"].popitem()),
        ("phase", lambda v: v.__setitem__("phase", "P31")),
        ("artifact", lambda v: v["artifacts"].pop("mesh_result")),
    ]:
        planted += 1
        v = copy.deepcopy(ev)
        mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)

    out = {"gate": "G1", "phase": "P32", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
