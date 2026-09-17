#!/usr/bin/env python3
"""P31 G0 independent checker: verifies the seal against live repo
state; planted mutations on critical fields must all be rejected."""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = os.path.join(RES, "g0_p31_seals.json")
OUT = os.path.join(RES, "g0_p31_check.json")
PROTOCOL = os.path.join(ROOT, "protocols", "ACTINV-P31_PROTOCOL.md")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def check(seals, fs):
    if seals.get("phase") != "P31":
        fs.append("phase != P31")
    if seals.get("gate") != "G0":
        fs.append("gate != G0")
    if sha(PROTOCOL) != seals.get("protocol_sha256"):
        fs.append("protocol digest mismatch")
    oc = seals.get("opening_commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", oc):
        fs.append("opening commit not a full sha")
    else:
        try:
            git("cat-file", "-e", f"{oc}^{{commit}}")
            git("merge-base", "--is-ancestor", oc, "HEAD")
        except subprocess.CalledProcessError:
            fs.append("opening commit not ancestor of HEAD")
    for f, want in seals.get("prior_verdicts", {}).items():
        p = os.path.join(RES, f)
        if not os.path.isfile(p):
            fs.append(f"prior verdict {f} missing")
            continue
        if json.load(open(p)).get("verdict") != want:
            fs.append(f"prior verdict {f} != {want}")
    pop = seals.get("validation_population", {})
    if len(pop.get("workload_cases", [])) != 8:
        fs.append("workload population != 8")
    if len(pop.get("robustness_cases", [])) != 2:
        fs.append("robustness population != 2")
    if len(pop.get("controls", [])) != 6:
        fs.append("controls != 6")
    if len(pop.get("negative_controls", [])) != 5:
        fs.append("negative controls != 5")
    bd = seals.get("baseline_binary", {})
    if not re.fullmatch(r"[0-9a-f]{64}", bd.get("sha256", "")):
        fs.append("baseline binary digest malformed")
    # data artifacts stay byte-pinned
    for k, ident in seals.get("identities", {}).items():
        p = os.path.join(ROOT, ident["path"])
        if not os.path.isfile(p) or sha(p) != ident["sha256"]:
            fs.append(f"identity {k} digest mismatch")


def main():
    seals = json.load(open(SEALS))
    fs = []
    check(seals, fs)

    planted = rejected = 0
    for field, mut in [
        ("phase", "P32"),
        ("protocol_sha256", "0" * 64),
        ("opening_commit", "f" * 40),
    ]:
        planted += 1
        v = copy.deepcopy(seals)
        v[field] = mut
        mfs = []
        check(v, mfs)
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", field, file=sys.stderr)

    for label, mut in [
        ("binary", lambda v: v["baseline_binary"].__setitem__(
            "sha256", "not-a-digest")),
        ("population", lambda v: v["validation_population"]
            ["workload_cases"].pop()),
        ("prior", lambda v: v["prior_verdicts"].__setitem__(
            "verdict_p30.json", "P30-PASS")),
        ("identity", lambda v: v["identities"]["activation_library"]
            .__setitem__("sha256", "0" * 64)),
    ]:
        planted += 1
        v = copy.deepcopy(seals)
        mut(v)
        mfs = []
        check(v, mfs)
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)

    out = {"gate": "G0", "phase": "P31", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
