#!/usr/bin/env python3
"""P29 G0 checker: identity resolution, prior verdicts verbatim, frozen
population intact, mutation self-test."""
import copy
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g0_p29_check.json")
SEALS = os.path.join(ROOT, "results", "g0_p29_seals.json")


def sh(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def check(s, fs):
    if s.get("phase") != "P29":
        fs.append("phase != P29")
    if s.get("protocol_sha256") != sh(os.path.join(
            ROOT, "protocols", "ACTINV-P29_PROTOCOL.md")):
        fs.append("protocol hash mismatch")
    op = git("log", "--diff-filter=A", "-1", "--format=%H", "--",
             "protocols/ACTINV-P29_PROTOCOL.md")
    if s.get("opening_commit") != op:
        fs.append("opening commit does not resolve to the P29 opening")
    for f, want in s.get("prior_verdicts", {}).items():
        p = os.path.join(ROOT, "results", f)
        if not os.path.isfile(p):
            fs.append(f"prior verdict {f} missing")
            continue
        if json.load(open(p))["verdict"] != want:
            fs.append(f"prior verdict {f} drifted")
    ids = s.get("identities", {})
    for k in ("actinv_binary", "activation_library", "activation_index",
              "decay_primary", "decay_fallback"):
        e = ids.get(k)
        if not e:
            fs.append(f"identity {k} missing")
            continue
        if sh(os.path.join(ROOT, e["path"])) != e["sha256"]:
            fs.append(f"identity {k} bytes drifted")
    pop = s.get("validation_population", {})
    if len(pop.get("criteria_cases", [])) != 8:
        fs.append("criteria population != 8")
    if len(pop.get("negative_controls", [])) != 3:
        fs.append("negative controls != 3")
    if s.get("partition", {}).get("qualifying") != "p29_qualifying":
        fs.append("partition seal wrong")


def main():
    s = json.load(open(SEALS))
    fs = []
    check(s, fs)

    planted = rejected = 0
    for mut in [
        lambda v: v.__setitem__("phase", "P28"),
        lambda v: v.__setitem__("protocol_sha256", "0" * 64),
        lambda v: v["identities"]["actinv_binary"]
        .__setitem__("sha256", "0" * 64),
        lambda v: v["validation_population"]["criteria_cases"].pop(),
        lambda v: v["prior_verdicts"].__setitem__("verdict_p28.json",
                                                "P28-PASS"),
        lambda v: v["validation_population"]
        .pop("negative_controls"),
    ]:
        planted += 1
        v = copy.deepcopy(s)
        mut(v)
        fs.clear()
        check(v, fs)
        if fs:
            rejected += 1
        else:
            print("  UNREJECTED mutation", file=sys.stderr)

    fs.clear()
    check(s, fs)
    out = {"gate": "G0", "phase": "P29", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
