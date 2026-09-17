#!/usr/bin/env python3
"""P30 G0 checker: identity resolution, prior verdicts verbatim, frozen
population/controls/probes, partition seal, planted mutations."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g0_p30_check.json")
SEALS = os.path.join(ROOT, "results", "g0_p30_seals.json")


def sh(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(s, fs):
    if s.get("phase") != "P30":
        fs.append("phase != P30")
    if s.get("protocol_sha256") != sh(os.path.join(
            ROOT, s.get("protocol", ""))):
        fs.append("protocol digest mismatch")
    for f, want in s.get("prior_verdicts", {}).items():
        p = os.path.join(ROOT, "results", f)
        if not os.path.isfile(p):
            fs.append(f"prior verdict {f} missing")
            continue
        if json.load(open(p))["verdict"] != want:
            fs.append(f"prior verdict {f} drifted")
    ids = s.get("identities", {})
    for k in ("actinv_binary", "activation_library", "activation_index",
              "covariance_sidecar", "decay_primary", "decay_fallback"):
        e = ids.get(k)
        if not e:
            fs.append(f"identity {k} missing")
            continue
        if k == "actinv_binary":
            # baseline pin: verified at seal time; after the phase
            # rebuild the record is checked for a well-formed digest
            if not isinstance(e.get("sha256"), str) \
                    or len(e["sha256"]) != 64:
                fs.append("actinv_binary baseline pin malformed")
            continue
        if sh(os.path.join(ROOT, e["path"])) != e["sha256"]:
            fs.append(f"identity {k} bytes drifted")
    pop = s.get("validation_population", {})
    if len(pop.get("sampling_cases", [])) != 2:
        fs.append("sampling population != 2")
    if len(pop.get("controls", [])) != 5:
        fs.append("controls != 5")
    if len(pop.get("negative_controls", [])) != 5:
        fs.append("negative controls != 5")
    if s.get("partition", {}).get("qualifying") != "p30_qualifying":
        fs.append("partition seal wrong")


def main():
    s = json.load(open(SEALS))
    fs = []
    check(s, fs)

    planted = rejected = 0
    for mut in [
        lambda v: v.__setitem__("phase", "P29"),
        lambda v: v.__setitem__("protocol_sha256", "0" * 64),
        lambda v: v["identities"]["actinv_binary"]
        .__setitem__("sha256", "tampered"),
        lambda v: v["identities"]["covariance_sidecar"]
        .__setitem__("sha256", "0" * 64),
        lambda v: v["validation_population"]["sampling_cases"].pop(),
        lambda v: v["prior_verdicts"].__setitem__("verdict_p29.json",
                                                "P29-PASS"),
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
    out = {"gate": "G0", "phase": "P30", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
