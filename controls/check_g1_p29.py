#!/usr/bin/env python3
"""P29 G1 checker: re-derives criterion verdicts from the raw run
artifacts (declared out.json vs ref_reference.json) and rejects planted
mutations."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.expanduser("~/nuclear-data/p29-work/g1")
EV = os.path.join(ROOT, "results", "g1_p29_refinement.json")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p29_seals.json")))
OUT = os.path.join(ROOT, "results", "g1_p29_check.json")
ABS_SCALE = SEALS["tolerances"]["abs_scale_atoms_per_g"]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def tkey(t):
    return str(int(t)) if t == int(t) else f"{t:e}"


def scalar(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list):
        xs = [scalar(x) for x in v]
        return sum(x for x in xs if x is not None) if xs else None
    if isinstance(v, dict):
        xs = [scalar(x) for x in v.values()]
        return sum(x for x in xs if x is not None) if xs else None
    return None


def resp_at(out, response, t):
    steps = out["steps"]
    irr_end = next((s["t_s"] for s in reversed(steps)
                    if s.get("flux", 0) > 0), 0.0)
    for s in steps:
        if tkey(s["t_s"] - irr_end) == tkey(t):
            if response == "total_activity_bq_per_g":
                return sum(s.get("activity_Bq_per_g", {}).values())
            if response == "decay_heat_w_per_g":
                return s.get("heat_W_per_g", {}).get("total")
            if response == "total_atoms_per_g":
                return s.get("total_atoms_per_g")
            if response == "inventory_per_nuclide":
                return s.get("inventory")
            if response == "photon_source_per_group":
                return s.get("photon_source", {}).get("groups")
    return None


def max_rel(a, b):
    if isinstance(a, list) or isinstance(b, list):
        a = a or []
        b = b or []
        n = max(len(a), len(b))
        m = 0.0
        for i in range(n):
            xa = scalar(a[i].get("atoms_per_g") if isinstance(a[i], dict)
                        else a[i]) if i < len(a) else 0.0
            xb = scalar(b[i].get("atoms_per_g") if isinstance(b[i], dict)
                        else b[i]) if i < len(b) else 0.0
            xa = xa or 0.0
            xb = xb or 0.0
            if xa == 0.0 and xb == 0.0:
                continue
            m = max(m, abs(xa - xb) / max(abs(xa), abs(xb)))
        return m
    a = scalar(a) or 0.0
    b = scalar(b) or 0.0
    if a == 0.0 and b == 0.0:
        return 0.0
    return abs(a - b) / max(abs(a), abs(b))


def expected_status(decl, ref, diff, c):
    if decl is None or ref is None or diff is None:
        return "unestablished"
    if abs(scalar(ref) or 0.0) < ABS_SCALE:
        if c.get("abs") is None:
            return "unestablished"
        return "satisfied" if diff <= max(c["abs"], 1e-300) else "unmet"
    if c.get("rel") is None:
        return "unestablished"
    return "satisfied" if diff <= c["rel"] else "unmet"


def check(res, fs):
    want = set(SEALS["validation_population"]["criteria_cases"])
    got = {c["case_id"] for c in res["cases"]}
    if got != want:
        fs.append(f"population drift: {got ^ want}")
        return
    for c in res["cases"]:
        cid = c["case_id"]
        if c["status"] != "executed":
            continue
        dpath = os.path.join(WORK, "run", "cases", cid, "out.json")
        rpath = os.path.join(WORK, "run", "cases", cid,
                             "ref_reference.json")
        if sha(dpath) != c["out_sha256"]:
            fs.append(f"{cid}: declared out digest mismatch")
        if not os.path.isfile(rpath):
            fs.append(f"{cid}: reference artifact missing")
            continue
        if sha(rpath) != c.get("reference_sha256"):
            fs.append(f"{cid}: reference digest mismatch")
        decl = json.load(open(dpath))
        ref = json.load(open(rpath))
        for cr in c["refinement"]["criteria"]:
            dv = resp_at(decl, cr["response"], cr["time_s"])
            rv = resp_at(ref, cr["response"], cr["time_s"])
            d = max_rel(dv, rv)
            want_v = expected_status(dv, rv, d, cr)
            # escalation may have improved the final verdict: an unmet
            # initial verdict with escalation runs can end satisfied.
            got_v = cr["verdict"]
            esc_n0 = cr.get("escalation_runs", 0)
            ok = (
                (want_v == "satisfied" and got_v == "satisfied")
                or (want_v == "unmet" and got_v in ("unmet", "satisfied")
                    and (got_v != "satisfied" or esc_n0 > 0))
                or (want_v == "unestablished"
                    and got_v == "unestablished"))
            if not ok:
                fs.append(f"{cid} {cr['response']}@{cr['time_s']}: "
                          f"re-derived {want_v} (esc={esc_n0}), "
                          f"recorded {got_v}")
            if d is not None and cr.get("initial_rel_diff") is not None:
                if abs(d - cr["initial_rel_diff"]) > \
                        0.01 * max(d, cr["initial_rel_diff"], 1e-300):
                    fs.append(f"{cid} {cr['response']}@{cr['time_s']}: "
                              "initial diff not re-derivable")
            # the final observed diff must be re-derivable from the last
            # persisted escalation artifact (or equal initial when no
            # escalation ran)
            esc_n = cr.get("escalation_runs", 0)
            if esc_n == 0 and cr.get("observed_rel_diff") is not None \
                    and cr.get("initial_rel_diff") is not None:
                if abs(cr["observed_rel_diff"]
                       - cr["initial_rel_diff"]) > \
                        0.01 * max(cr["initial_rel_diff"], 1e-300):
                    fs.append(f"{cid} {cr['response']}@{cr['time_s']}: "
                              "final diff differs from initial without "
                              "escalation")
            if esc_n:
                esc_path = os.path.join(
                    WORK, "run", "cases", cid,
                    f"esc_{cr['response']}_{tkey(cr['time_s'])}_"
                    f"{esc_n}.json")
                if not os.path.isfile(esc_path):
                    fs.append(f"{cid} {cr['response']}@{cr['time_s']}: "
                              "final escalation artifact missing")
                else:
                    eo = json.load(open(esc_path))
                    ev = resp_at(eo, cr["response"], cr["time_s"])
                    fd = max_rel(ev, rv)
                    if fd is not None and \
                            cr.get("observed_rel_diff") is not None:
                        if abs(fd - cr["observed_rel_diff"]) > \
                                0.01 * max(fd, cr["observed_rel_diff"],
                                           1e-300):
                            fs.append(f"{cid} {cr['response']}@"
                                      f"{cr['time_s']}: final diff not "
                                      "re-derivable")
        rf = c["refinement"]
        if rf["satisfied"] + rf["unmet"] + rf["unestablished"] != \
                len(rf["criteria"]):
            fs.append(f"{cid}: criterion counts do not reconcile")
        if rf["runs_used"] < 3:
            fs.append(f"{cid}: reference solve not accounted in runs")


def main():
    res = json.load(open(EV))
    fs = []
    check(res, fs)

    planted = rejected = 0

    def mut_verdict(r):
        for c in r["cases"]:
            for cr in c.get("refinement", {}).get("criteria", []):
                if cr["verdict"] == "satisfied":
                    cr["verdict"] = "unmet"
                    return

    def mut_diff(r):
        for c in r["cases"]:
            for cr in c.get("refinement", {}).get("criteria", []):
                if cr.get("initial_rel_diff") is not None:
                    cr["initial_rel_diff"] = 9.9
                    return

    def mut_counts(r):
        for c in r["cases"]:
            if c.get("refinement"):
                c["refinement"]["unmet"] = 5
                return

    def mut_digest(r):
        r["cases"][0]["out_sha256"] = "0" * 64

    for mut in [mut_verdict, mut_diff, mut_counts, mut_digest,
                lambda r: r["cases"].pop()]:
        planted += 1
        v = copy.deepcopy(res)
        mut(v)
        fs.clear()
        check(v, fs)
        if fs:
            rejected += 1
        else:
            print("  UNREJECTED mutation", file=sys.stderr)

    fs.clear()
    check(res, fs)
    out = {"gate": "G1", "phase": "P29", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
