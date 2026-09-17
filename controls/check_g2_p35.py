#!/usr/bin/env python3
"""P35 G2 independent checker: re-derives the matrix from verdict files
and verifies the sealed controls."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p35_seals.json")))
EV = os.path.join(RES, "g2_p35_matrix.json")
OUT = os.path.join(RES, "g2_p35_check.json")
G1 = json.load(open(os.path.join(RES, "g1_p35_battery.json")))

STATUS_BY_VERDICT = {"PASS": "qualified", "CONDITIONAL": "conditional",
                     "BLOCKED": "blocked", "FAIL": "unmeasured"}


def check(ev, fs):
    if ev.get("phase") != "P35" or ev.get("gate") != "G2":
        fs.append("gate/phase wrong")
    matrix = ev.get("matrix", {})
    want = set(SEALS["matrix"]["families"])
    if set(matrix) != want:
        fs.append(f"matrix family drift: {set(matrix) ^ want}")
        return
    # matrix_consistency: re-derive each status from the verdict file
    for fam, ent in matrix.items():
        st = ent.get("status")
        if st not in SEALS["matrix"]["statuses"]:
            fs.append(f"{fam}: invalid status {st}")
            continue
        if fam == "product_qualification":
            continue
        vf = ent.get("verdict")
        if not vf:
            fs.append(f"{fam}: no verdict named")
            continue
        vfile = os.path.join(RES, f"verdict_{vf.split('-')[0].lower()}"
                                   f".json")
        if not os.path.isfile(vfile):
            fs.append(f"{fam}: verdict file missing")
            continue
        v = json.load(open(vfile))
        derived = STATUS_BY_VERDICT.get(
            str(v.get("verdict", "")).split("-", 1)[-1], "unmeasured")
        if derived != st:
            fs.append(f"{fam}: matrix {st} != verdict-derived {derived}")
        # the recorded digest must match the file
        d = ent.get("verdict_sha256")
        if d:
            h = hashlib.sha256(open(vfile, "rb").read()).hexdigest()
            if h != d:
                fs.append(f"{fam}: verdict digest mismatch")
    # blocked_families_named
    for fam in ("spatial_handoff", "ai_assisted_setup",
                "ai_bounded_investigation"):
        e = matrix.get(fam, {})
        if e.get("status") != "blocked" or not e.get("blockers"):
            fs.append(f"{fam} not recorded blocked-with-blockers")
    # battery_complete: every sealed battery checker reported a pass
    for ck in SEALS["battery"]["checkers"]:
        if not G1["battery"].get(ck, {}).get("pass"):
            fs.append(f"battery checker {ck} did not pass")
    # reproduction_identity
    repro = G1.get("reproduction", {})
    if not repro.get("identical"):
        fs.append("reproduction runs diverged")
    # no family may claim without matching its verdict
    for fam, ent in matrix.items():
        if ent["status"] in ("blocked", "unmeasured") \
                and ent.get("claims"):
            fs.append(f"{fam}: claims attached to {ent['status']}")
    # the release recommendation must not ship a blocked/unmeasured
    # family and must name every missed gate
    rr = ev.get("release_recommendation", {})
    blocked_fams = {f for f, e in matrix.items()
                    if e["status"] in ("blocked", "unmeasured")}
    tokens = {
        "spatial_handoff": ("spatial", "r2s", "source handoff"),
        "ai_assisted_setup": ("ai-assist", "assistant", "ai setup"),
        "ai_bounded_investigation": ("ai-assist", "assistant",
                                     "bounded investigation"),
    }
    for item in rr.get("ship", []):
        low = item.lower()
        for fam in blocked_fams:
            if any(t in low for t in tokens.get(fam, ())):
                fs.append(f"ship claims blocked family {fam}: {item}")
    missed = set(rr.get("missed_gates", []))
    if missed != {"P32", "P33", "P34"}:
        fs.append("missed gates not recorded")


def main():
    ev = json.load(open(EV))
    fs = []
    check(ev, fs)

    planted = rejected = 0
    for label, mut in [
        ("upgrade", lambda v: v["matrix"]["uncertainty"]
            .__setitem__("status", "qualified")),
        ("omit", lambda v: v["matrix"].pop("spatial_handoff")),
        ("claim", lambda v: v["matrix"]["spatial_handoff"]
            .setdefault("claims", []).append("spatial source works")),
        ("gate", lambda v: v.__setitem__("gate", "G1")),
        ("digest", lambda v: v["matrix"]["uncertainty"]
            .__setitem__("verdict_sha256", "0" * 64)),
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

    out = {"gate": "G2", "phase": "P35", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
