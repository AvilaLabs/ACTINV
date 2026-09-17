#!/usr/bin/env python3
"""P32 G2 checker: re-derive the control outcomes from raw artifacts."""
import copy
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
EV = json.load(open(os.path.join(RES, "g1_p32.json")))
SEALS = json.load(open(os.path.join(RES, "g0_p32_seals.json")))
G2 = os.path.join(RES, "g2_p32.json")
OUT = os.path.join(RES, "g2_p32_check.json")


def check(rec, fs):
    if rec.get("schema") != "actinv-p32-g2-1" or rec.get("phase") != "P32":
        fs.append("schema/phase wrong")
    c = rec.get("controls", {})
    need = {"cell_identity_volume", "source_conservation",
            "spectrum_fidelity", "cooling_separation",
            "spatial_sampling", "activation_comparison"}
    if set(c) != need:
        fs.append(f"control set {set(c)} != {need}")
    for name in need & set(c):
        if not c[name].get("pass"):
            fs.append(f"control {name} not passing")

    # re-derive conservation from raw source files + mesh result
    cells = {}
    with open(EV["artifacts"]["mesh_result"]["path"]) as f:
        for line in f:
            r = json.loads(line)
            if r["record"] == "cell":
                cells[r["id"]] = r
    for step, s in EV.get("sources", {}).items():
        text = open(s["path"]).read()
        strengths = [float(x) for x in
                     re.findall(r"strength=([0-9.eE+-]+)\)\)", text)]
        total = float(re.search(r"TOTAL_PHOTONS_S = ([0-9.e+-]+)",
                                text).group(1))
        if abs(sum(strengths) - total) > 1e-9 * total:
            fs.append(f"step {step}: exported strengths do not "
                      "conserve declared total")
        # vs mesh result directly
        truth = 0.0
        for c in cells.values():
            st = [x for x in c["result"]["steps"]
                  if x["step"] == int(step)][0]
            truth += st["photon_source"]["total_photons_s"]
        if abs(sum(strengths) - truth) > 1e-9 * truth:
            fs.append(f"step {step}: exported total deviates from "
                      "mesh result total")
    # cooling separation: step totals must differ
    totals = {}
    for step, s in EV.get("sources", {}).items():
        text = open(s["path"]).read()
        totals[int(step)] = float(
            re.search(r"TOTAL_PHOTONS_S = ([0-9.e+-]+)", text).group(1))
    vals = list(totals.values())
    if len(set(vals)) < len(vals):
        fs.append("cooling steps produced identical totals")


def main():
    rec = json.load(open(G2))
    fs = []
    check(rec, fs)

    planted = rejected = 0
    for label, mut in [
        ("schema", lambda v: v.__setitem__("schema", "x")),
        ("drop_control",
         lambda v: v["controls"].pop("spatial_sampling")),
        ("flip", lambda v: v["controls"]["source_conservation"]
            .__setitem__("pass", True)),
        ("phase", lambda v: v.__setitem__("phase", "P33")),
    ]:
        planted += 1
        v = copy.deepcopy(rec)
        if label == "flip":
            v["controls"]["source_conservation"]["pass"] = False
        else:
            mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)

    out = {"gate": "G2", "phase": "P32", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
