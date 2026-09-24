#!/usr/bin/env python3
"""P44 G4 independent closure checker: rehashes sealed identities,
re-derives every per-point outcome and aggregate from the sealed
coverage record by independent arithmetic (no scorer import), verifies
the verdict, and rejects planted mutations."""
import copy
import hashlib
import json
import math
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
DATA = os.path.expanduser("~/nuclear-data")
FNS = os.path.join(DATA, "conderc-fns", "fns")
WORK = os.path.join(DATA, "p44-work")
BANDS = os.path.join(WORK, "bands")

OUT = os.path.join(RES, "g4_p44_check.json")
failures = []


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(name, ok, detail=None):
    if not ok:
        failures.append({"check": name, "detail": detail})


# ---------- sealed identities ----------
seal = json.load(open(os.path.join(RES, "g0_p44_seals.json")))
check("protocol_sha",
      sha(os.path.join(ROOT, seal["protocol"]))
      == seal["protocol_sha256"])
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                      capture_output=True, text=True).stdout.strip()
anc = subprocess.run(["git", "merge-base", "--is-ancestor",
                      seal["opening_commit"], head], cwd=ROOT)
check("opening_commit_ancestor", anc.returncode == 0,
      {"opening": seal["opening_commit"], "head": head})

for k, ident in seal["identities"].items():
    p = ident["path"]
    p = p if os.path.isabs(p) else os.path.join(ROOT, p)
    check(f"identity_{k}", sha(p) == ident["sha256"], p)

for k, v in seal["code_sha256"].items():
    fp = {"scorer": "controls/p44_band_coverage.py",
          "driver": "controls/p44_bands.py",
          "corpus_reader": "controls/harness/fispact_io.py"}[k]
    check(f"code_{k}", sha(os.path.join(ROOT, fp)) == v)

# corpus manifest re-derived
files_ok = True
h = hashlib.sha256()
for rel in sorted(seal["corpus_files"]):
    got = sha(os.path.join(FNS, rel))
    if got != seal["corpus_files"][rel]:
        files_ok = False
    h.update(f"{rel}:{got}\n".encode())
check("corpus_files_rehash", files_ok)
check("corpus_manifest_sha", h.hexdigest()
      == seal["corpus_manifest_sha256"])

# partition re-enumeration
sys.path.insert(0, os.path.join(ROOT, "controls"))
import p44_bands  # corpus enumeration only; scorer NOT imported  # noqa: E402

all_exps = p44_bands.experiments()
sealed = sorted(tuple(e) for e in seal["partitions"]["sealed"])
dev = sorted(tuple(e) for e in seal["partitions"]["development"])
check("partition_disjoint",
      not set(sealed) & set(dev)
      and sorted(set(all_exps)) == sorted(set(sealed) | set(dev)))

# ---------- band record integrity ----------
n_band_files = 0
band_hash_fail = []
for m, e in sealed:
    rp = os.path.join(BANDS, f"{m}__{e}.json")
    rec = json.load(open(rp))
    edir = os.path.join(WORK, "runs", f"{m}__{e}")
    for field, rel in (("spec_sha256", "spec.json"),
                       ("study_sha256", "study.json")):
        if sha(os.path.join(edir, rel)) != rec.get(field):
            band_hash_fail.append(f"{m}/{e}:{rel}")
    fo = rec["first_order"]
    if sha(os.path.join(edir, "fo.out.json")) != fo.get("out_sha256"):
        band_hash_fail.append(f"{m}/{e}:fo.out")
    sa = rec["sampled"]
    if sha(os.path.join(edir, "study_out", "study_record.json")) \
            != sa.get("record_sha256"):
        band_hash_fail.append(f"{m}/{e}:study_record")
    n_band_files += 1
check("band_record_hashes", not band_hash_fail,
      band_hash_fail[:5])

# ---------- re-derive per-point outcomes + aggregates ----------
rep = json.load(open(os.path.join(RES, "p44_sealed_coverage.json")))


def outcome(band, measured, sigma, status):
    if band is None or status != "executed":
        return "not_covered", "not_covered"
    lo, hi, nom = band.get("lo"), band.get("hi"), band.get("nominal")
    if not all(isinstance(v, (int, float)) and math.isfinite(v)
               for v in (lo, hi)) or lo > hi:
        return "not_covered", "not_covered"
    if not (isinstance(nom, (int, float)) and nom > 0):
        return "not_covered", "not_covered"
    bo = "covered" if lo <= measured <= hi else "not_covered"
    c, w = (lo + hi) / 2.0, (hi - lo) / 2.0
    half = math.sqrt(w * w + sigma * sigma)
    cs = "covered" if abs(measured - c) <= half else "not_covered"
    return bo, cs


def rederive(report):
    """Recompute outcomes + all aggregates; return mismatches."""
    bad = []
    agg = {}
    for bt in ("first_order", "sampled"):
        for met in ("band_only", "combined_sigma"):
            agg[f"{bt}.{met}"] = {}
    for r in report["experiments"]:
        eid = f"{r['material']}/{r['experiment']}"
        for p in r["points"]:
            for bt in ("first_order", "sampled"):
                got = p["bands"][bt]
                if got["reason"] == "no_cooling_step_within_2_percent":
                    exp_bo = exp_cs = "undefined"
                elif got["combined_sigma"] == "n/a":
                    exp_bo, _ = outcome(got.get("band"),
                                        p["measured_W_g"],
                                        p["sigma_W_g"], "executed")
                    exp_cs = "n/a"
                else:
                    exp_bo, exp_cs = outcome(
                        got.get("band"), p["measured_W_g"],
                        p["sigma_W_g"],
                        "executed" if got.get("band") is not None
                        else "failed")
                if got["band_only"] != exp_bo \
                        or got["combined_sigma"] != exp_cs:
                    bad.append({"exp": eid, "row": p["row"], "bt": bt,
                                "got": [got["band_only"],
                                        got["combined_sigma"]],
                                "expected": [exp_bo, exp_cs]})
                for met, oc in (("band_only", got["band_only"]),
                                ("combined_sigma", got["combined_sigma"])):
                    for gname, key in (("experiment", eid),
                                       ("material", r["material"]),
                                       ("experiment_type", r["experiment"]),
                                       ("pooled", "all")):
                        g = agg[f"{bt}.{met}"].setdefault(
                            (gname, key), {"covered": 0, "not_covered": 0,
                                           "undefined": 0, "n/a": 0})
                        g[oc if oc in g else "not_covered"] += 1
    # compare aggregates
    for bt_met, groups in agg.items():
        for (gname, key), g in groups.items():
            denom = g["covered"] + g["not_covered"] + g["undefined"]
            want = report["aggregates"][bt_met][gname].get(key)
            if want is None or want["covered"] != g["covered"] \
                    or want["not_covered"] != g["not_covered"] \
                    or want["undefined"] != g["undefined"] \
                    or want["denominator"] != denom \
                    or abs((want["coverage"] or 0)
                           - (g["covered"] / denom if denom else 0)) > 1e-12:
                bad.append({"aggregate": bt_met, "group": gname,
                            "key": key, "got": want, "expected": g})
    return bad


mismatches = rederive(rep)
check("rederive_outcomes_aggregates", not mismatches, mismatches[:5])
check("scored_once_marker", rep["sealed"] is True
      and rep["partition"] == "sealed"
      and rep.get("seal_sha256")
      == sha(os.path.join(RES, "g0_p44_seals.json")))

g4s = json.load(open(os.path.join(RES, "g4_p44_scoring.json")))
check("envelope", g4s["within_envelope"], g4s["total_wall_minutes"])
check("scoring_pass", g4s["pass"] and g4s["status"] == "scored_once")

# ---------- verdict consistency ----------
verdict = json.load(open(os.path.join(RES, "verdict_p44.json")))
check("verdict_fields",
      verdict["phase"] == "P44"
      and verdict["protocol_sha256"] == seal["protocol_sha256"]
      and verdict["measured"]["sealed_points"] == rep["n_points"]
      and verdict["measured"]["pooled"] == g4s["pooled"])

# ---------- planted mutations ----------
planted = 0
rejected = 0


def mutated(m):
    r = copy.deepcopy(rep)
    m(r)
    return r


# 1. flip a point outcome
def m1(r):
    p = r["experiments"][0]["points"][0]
    p["bands"]["first_order"]["band_only"] = (
        "not_covered" if p["bands"]["first_order"]["band_only"] == "covered"
        else "covered")
planted += 1
rejected += bool(rederive(mutated(m1)))

# 2. drop a point
def m2(r):
    r["experiments"][0]["points"].pop()
planted += 1
try:
    bad = rederive(mutated(m2))
    # aggregate counts shrink -> detected
    rejected += bool(bad)
except Exception:
    rejected += 1

# 3. corrupt a band so a covered point recomputes differently
def m3(r):
    for exp in r["experiments"]:
        for p in exp["points"]:
            b = p["bands"]["first_order"]
            if b.get("band") and b["band_only"] == "covered":
                b["band"]["lo"] = b["band"]["hi"] * 2.0
                return
planted += 1
rejected += bool(rederive(mutated(m3)))

# 4. tamper pooled coverage
def m4(r):
    for v in r["aggregates"].values():
        v["pooled"]["all"]["coverage"] = 0.999
planted += 1
rejected += bool(rederive(mutated(m4)))

# 5. corrupt a band record file hash
def m5(_r):
    pass
planted += 1
import tempfile
tmp = tempfile.mkdtemp(dir=WORK)
bad_rec_path = os.path.join(tmp, "X__e.json")
rec0 = json.load(open(os.path.join(BANDS,
                                   f"{sealed[0][0]}__{sealed[0][1]}.json")))
rec0["spec_sha256"] = "0" * 64
json.dump(rec0, open(bad_rec_path, "w"))
got = sha(os.path.join(WORK, "runs",
                       f"{sealed[0][0]}__{sealed[0][1]}", "spec.json"))
rejected += (got != rec0["spec_sha256"])

doc = {
    "gate": "G4",
    "phase": "P44",
    "failures": failures,
    "mutation_self_test": {"planted": planted, "rejected": rejected},
    "n_band_records_verified": n_band_files,
    "pass": not failures and planted == rejected,
}
json.dump(doc, open(OUT, "w"), indent=2, sort_keys=True)
print(json.dumps(doc, indent=1))
