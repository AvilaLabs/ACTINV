#!/usr/bin/env python3
"""P32 G0 checker: verify the seal — protocol digest, identities,
frozen model, control vocabularies."""
import copy
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = os.path.join(RES, "g0_p32_seals.json")
OUT = os.path.join(RES, "g0_p32_check.json")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(v, fs):
    if v.get("phase") != "P32":
        fs.append("phase wrong")
    if v.get("protocol_sha256") != sha(os.path.join(
            ROOT, "protocols", "ACTINV-P32_PROTOCOL.md")):
        fs.append("protocol digest drifted")
    r = subprocess.run(["git", "-C", ROOT, "merge-base", "--is-ancestor",
                        v.get("opening_commit", ""), "HEAD"])
    if r.returncode != 0:
        fs.append("opening commit not ancestor")
    if "verdict_p32.json" not in v.get("prior_verdicts", {}):
        fs.append("prior verdicts must include verdict_p32.json")
    for f, want in v.get("prior_verdicts", {}).items():
        p = os.path.join(RES, f)
        if not os.path.isfile(p) \
                or json.load(open(p)).get("verdict") != want:
            fs.append(f"prior verdict {f} drifted")
    if v["openmc"]["exe_sha256"] != sha(os.path.join(
            v["openmc"]["env_prefix"], "bin", "openmc")):
        fs.append("openmc executable drifted")
    td = v["transport_data"]
    if sha(td["cross_sections_xml"]) != td["cross_sections_sha256"]:
        fs.append("transport library digest drifted")
    ad = v["activation_data"]
    for k in ("library", "decay_primary", "decay_fallback"):
        if not os.path.isfile(ad[k]):
            fs.append(f"activation data missing: {k}")
    if sha(ad["library"]) != ad["library_sha256"]:
        fs.append("activation library digest drifted")
    cand = v["candidate"]
    if not (len(cand.get("binary_sha256", "")) == 64):
        fs.append("candidate digest malformed")
    m = v["model"]
    if m.get("voxels") != 64 or m.get("voxel_cm3") != 1.0:
        fs.append("voxel population drifted")
    if m["source"]["energy_eV"] != 14.1e6 or m["batches"] <= 0 \
            or m["particles"] <= 0:
        fs.append("source/run parameters drifted")
    if set(v.get("controls", [])) != {
            "cell_identity", "normalization", "distributed_source",
            "cooling_separation", "depletion_comparison"}:
        fs.append("control vocabulary drifted")
    if set(v.get("negative_controls", [])) != {
            "planted_normalization", "planted_mapping",
            "interrupted_export", "missing_data", "point_source"}:
        fs.append("negative-control vocabulary drifted")
    if v.get("target_family") != "ACT-SOURCE-01":
        fs.append("target family wrong")


def main():
    seals = json.load(open(SEALS))
    fs = []
    check(seals, fs)

    planted = rejected = 0
    for label, mut in [
        ("phase", lambda v: v.__setitem__("phase", "P31")),
        ("proto", lambda v: v.__setitem__("protocol_sha256", "0" * 64)),
        ("verdict", lambda v: v["prior_verdicts"].pop(
            "verdict_p32.json")),
        ("openmc", lambda v: v["openmc"].__setitem__(
            "exe_sha256", "0" * 64)),
        ("model", lambda v: v["model"].__setitem__("voxels", 4)),
        ("controls", lambda v: v["controls"].remove("distributed_source")),
        ("candidate", lambda v: v["candidate"].__setitem__(
            "binary_sha256", "bad")),
    ]:
        planted += 1
        v = copy.deepcopy(seals)
        mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)

    out = {"gate": "G0", "phase": "P32",
           "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
