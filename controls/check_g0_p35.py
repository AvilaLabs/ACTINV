#!/usr/bin/env python3
"""P35 G0 seal checker: verifies the sealed battery/checker inventory,
prior verdict digests, reproduction study and candidate identity."""
import copy
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = os.path.join(RES, "g0_p35_seals.json")
OUT = os.path.join(RES, "g0_p35_check.json")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(s, fs):
    if s.get("phase") != "P35":
        fs.append("phase wrong")
    if sha(os.path.join(ROOT, s["protocol"])) != s["protocol_sha256"]:
        fs.append("protocol digest mismatch")
    if not subprocess.run(
            ["git", "-C", ROOT, "merge-base", "--is-ancestor",
             s["opening_commit"], "HEAD"]).returncode == 0:
        fs.append("opening commit is not an ancestor of HEAD")
    # battery inventory must cover each phase's closure checker
    want = sorted("controls/check_g4_%s.py" % p
                  for p in ("p26b", "p27", "p28", "p29", "p30", "p31"))
    if s["battery"]["checkers"] != want:
        fs.append("battery checker inventory drifted")
    for pth, d in s["battery"]["checker_sha256"].items():
        full = os.path.join(ROOT, pth)
        if not os.path.isfile(full) or sha(full) != d:
            fs.append(f"checker digest mismatch: {pth}")
    # prior verdict digests verify
    for f, d in s["prior_verdicts"].items():
        full = os.path.join(RES, f)
        if not os.path.isfile(full) or sha(full) != d:
            fs.append(f"verdict digest mismatch: {f}")
    actual = {f for f in os.listdir(RES)
              if f.startswith("verdict_p") and f.endswith(".json")}
    if set(s["prior_verdicts"]) != actual:
        fs.append("sealed prior-verdict set does not match disk")
    if not any(f.endswith("_p31.json") for f in s["prior_verdicts"]):
        fs.append("P31 verdict not in sealed priors")
    # reproduction leg pinned (amended to the frozen P31 smoke study)
    if s["reproduction"].get("study_source") != \
            "frozen P31 smoke population (explicit path+sha256 refs)":
        fs.append("reproduction leg not pinned")
    # candidate binary: recorded as sealed value, format-checked
    # (P35 may legitimately rebuild it later in the phase)
    d = s["candidate"]["binary_sha256"]
    if not (isinstance(d, str) and len(d) == 64
            and all(c in "0123456789abcdef" for c in d)):
        fs.append("candidate binary digest malformed")
    if set(s.get("controls", [])) != {
            "matrix_consistency", "battery_complete",
            "reproduction_identity", "blocked_families_named"}:
        fs.append("control set drifted")
    if set(s.get("negative_controls", [])) != {
            "upgraded_verdict", "omitted_family",
            "forged_reproduction", "unsupported_claim"}:
        fs.append("negative-control set drifted")
    if set(s["matrix"].get("statuses", [])) != {
            "qualified", "conditional", "unmeasured", "blocked"}:
        fs.append("matrix status vocabulary drifted")
    # every seal amendment must name scope and reason
    for a in s.get("amendments", []):
        if not (a.get("scope") and a.get("reason")):
            fs.append("seal amendment missing scope/reason")


def main():
    s = json.load(open(SEALS))
    fs = []
    check(s, fs)

    planted = rejected = 0
    for label, mut in [
        ("phase", lambda v: v.__setitem__("phase", "P34")),
        ("proto", lambda v: v.__setitem__("protocol_sha256", "0" * 64)),
        ("checkers", lambda v: v["battery"]["checkers"].pop()),
        ("verdict", lambda v: v["prior_verdicts"].popitem()),
        ("status", lambda v: v["matrix"]["statuses"].append("maybe")),
        ("study", lambda v: v["reproduction"].__setitem__(
            "study_source", "some other study")),
        ("binary", lambda v: v["candidate"].__setitem__(
            "binary_sha256", "not-a-hex-digest")),
    ]:
        planted += 1
        v = copy.deepcopy(s)
        mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)

    out = {"gate": "G0", "phase": "P35", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
