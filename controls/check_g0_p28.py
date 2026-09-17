#!/usr/bin/env python3
"""P28 G0 checker: re-verify the seal's identity resolution against live
files and reject planted mutations."""
import copy
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEALS = os.path.join(ROOT, "results", "g0_p28_seals.json")
OUT = os.path.join(ROOT, "results", "g0_p28_check.json")


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
    if sha_now := (sh(os.path.join(ROOT, "protocols",
                                   "ACTINV-P28_PROTOCOL.md"))
                   != s["protocol_sha256"]):
        fs.append("protocol digest mismatch")
    if s["opening_commit"] != "5492b96"[:7] and \
            s["opening_commit"] != git("rev-parse", "5492b96^{commit}"):
        fs.append("opening_commit does not resolve to the P28 opening")
    try:
        git("merge-base", "--is-ancestor", s["opening_commit"], "HEAD")
    except subprocess.CalledProcessError:
        fs.append("opening_commit not an ancestor of HEAD")
    for name, ident in s["identities"].items():
        if "sha256" not in ident:
            continue
        p = ident["path"]
        if not os.path.isabs(p):
            p = os.path.join(ROOT, p)
        if not os.path.isfile(p) or sh(p) != ident["sha256"]:
            fs.append(f"identity {name} digest mismatch/missing")
    # frozen structures
    pop = s["validation_population"]
    if pop["ordinary"]["cases"] != 8:
        fs.append("ordinary population != 8")
    if len(pop["boundary"]["cases"]) != 8:
        fs.append("boundary population != 8")
    if pop["rate_traces"]["groups"] != 709 or \
            pop["rate_traces"]["parents"] != ["Fe56", "Fe54", "Co59"]:
        fs.append("rate-trace freeze drifted")
    if s["selected_regime"]["projectile"]["qualified"] != ["neutron"]:
        fs.append("projectile freeze drifted")
    if s["evidence_partitions"]["p28_qualifying"]["sealed_at"] != "G0":
        fs.append("partition seal drifted")
    for f, want in s["prior_verdicts"].items():
        v = json.load(open(os.path.join(ROOT, "results", f)))
        if v["verdict"] != want:
            fs.append(f"prior verdict {f} drifted")


def main():
    seals = json.load(open(SEALS))
    fs = []
    check(seals, fs)

    planted = rejected = 0
    for mut in [
        lambda s: s["identities"]["actinv_binary"].__setitem__(
            "sha256", "0" * 64),
        lambda s: s["validation_population"]["boundary"]["cases"].pop(),
        lambda s: s["validation_population"]["rate_traces"].__setitem__(
            "groups", 175),
        lambda s: s["prior_verdicts"].__setitem__(
            "verdict_p27.json", "P27-FAIL"),
        lambda s: s["selected_regime"]["projectile"]["qualified"].append(
            "proton"),
    ]:
        planted += 1
        v = copy.deepcopy(seals)
        mut(v)
        fs.clear()
        check(v, fs)
        if fs:
            rejected += 1
        else:
            print("  UNREJECTED mutation", file=sys.stderr)

    result = {"gate": "G0", "phase": "P28", "pass": not fs and planted == rejected,
              "failures": fs,
              "mutation_self_test": {"planted": planted,
                                     "rejected": rejected}}
    # rerun the real check fresh (mutations cleared fs)
    fs.clear()
    check(seals, fs)
    result["failures"] = fs
    result["pass"] = not fs and planted == rejected
    json.dump(result, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(result, indent=1))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
