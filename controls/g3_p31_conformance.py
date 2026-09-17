#!/usr/bin/env python3
"""P31 G3 conformance: the frozen negative controls — stale/corrupt/
phantom/mismatched state must never be silently accepted."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p31_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p31-work/g3")
OUT = os.path.join(ROOT, "results", "g3_p31_conformance.json")

_spec = importlib.util.spec_from_file_location(
    "g1c", os.path.join(ROOT, "controls", "g1_p31_campaign.py"))
_g1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_g1)


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *cmd]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def run(study, outdir):
    os.makedirs(outdir, exist_ok=True)
    sp = os.path.join(outdir, "study.json")
    if not os.path.exists(sp):
        json.dump(study, open(sp, "w"), indent=1)
    return subprocess.run(cgroup([ACTINV, "study", "run", sp, outdir]),
                          capture_output=True, text=True)


def fresh_cold(tag):
    """A clean 2-case reference run to corrupt/resume against."""
    d = os.path.join(WORK, tag)
    shutil.rmtree(d, ignore_errors=True)
    st = _g1.smoke_study("p31-smoke")
    st["study_id"] = tag
    st["cases"]["spectra"] = [s for s in st["cases"]["spectra"]
                             if s["name"] == "fns_709"]
    st["cases"]["schedules"] = [s for s in st["cases"]["schedules"]
                                 if s["name"] == "pulse_5min"]
    st.pop("comparison", None)
    r = run(st, d)
    assert r.returncode == 0, r.stderr[-400:]
    return d


def main():
    os.makedirs(WORK, exist_ok=True)
    probes = {}

    # --- spec_mismatch_accepted --------------------------------------
    # A spec whose declared library digest differs from the prepared
    # identity must be refused, never run.
    d = os.path.join(WORK, "mismatch")
    shutil.rmtree(d, ignore_errors=True)
    st = _g1.smoke_study("p31-mismatch")
    st["library"]["sha256"] = "0" * 64
    r = run(st, d)
    mm = json.load(open(os.path.join(d, "study_record.json")))
    probes["spec_mismatch_accepted"] = {
        "rejected": r.returncode == 0
        and mm["population"]["executed"] == 0
        and all("SHA-256 mismatch" in c.get("error", "")
                for c in mm["cases"]),
        "stderr": mm["cases"][0].get("error", "")[-160:],
    }

    # --- corrupt_record_swallowed ------------------------------------
    d = fresh_cold("corrupt_record")
    with open(os.path.join(d, "study_record.json"), "w") as f:
        f.write("{this is not json")
    r = run(_g1.smoke_study("corrupt_record"), d)
    rec = json.load(open(os.path.join(d, "study_record.json")))
    probes["corrupt_record_swallowed"] = {
        "rejected": r.returncode == 0
        and not rec.get("resumed_cases")
        and rec["population"]["executed"] == 2,
        "detail": "torn record discarded, all cases re-executed"
        if r.returncode == 0 else r.stderr[-200:],
    }

    # --- phantom_resume ----------------------------------------------
    d = fresh_cold("phantom")
    rec = json.load(open(os.path.join(d, "study_record.json")))
    # fabricate a record entry claiming execution of a case whose
    # artifacts were deleted
    victim = rec["cases"][0]["case_id"]
    shutil.rmtree(os.path.join(d, "cases", victim))
    r = run(_g1.smoke_study("phantom"), d)
    rec2 = json.load(open(os.path.join(d, "study_record.json")))
    probes["phantom_resume"] = {
        "rejected": r.returncode == 0
        and victim not in rec2.get("resumed_cases", [])
        and rec2["population"]["executed"] == 2
        and os.path.isfile(os.path.join(d, "cases", victim,
                                        "out.json")),
        "detail": "record claim without artifacts re-executed",
    }

    # --- option_drift ------------------------------------------------
    d = fresh_cold("drift")
    st = json.load(open(os.path.join(d, "study.json")))
    for m in st["cases"]["materials"]:
        if m["name"] == "fe_co100wppm":
            m["composition"]["Co"] = 0.02
    json.dump(st, open(os.path.join(d, "study.json"), "w"), indent=1)
    r = run(st, d)
    rec2 = json.load(open(os.path.join(d, "study_record.json")))
    resumed = set(rec2.get("resumed_cases", []))
    drifted = {c["case_id"] for c in rec2["cases"]
               if "fe_co100wppm" in c["case_id"]}
    probes["option_drift"] = {
        "rejected": r.returncode == 0
        and not (drifted & resumed)
        and resumed == {c["case_id"] for c in rec2["cases"]}
            - drifted,
        "resumed": sorted(resumed),
    }

    # --- directory_pollution ------------------------------------------
    d = fresh_cold("pollution")
    stray = os.path.join(d, "cases", "not_a_case_evil")
    os.makedirs(stray)
    with open(os.path.join(stray, "out.json"), "w") as f:
        f.write('{"planted": true}')
    ref_digs = {c["case_id"]: c["out_sha256"]
                for c in json.load(open(
                    os.path.join(d, "study_record.json")))["cases"]}
    r = run(_g1.smoke_study("pollution"), d)
    rec2 = json.load(open(os.path.join(d, "study_record.json")))
    probes["directory_pollution"] = {
        "rejected": r.returncode == 0
        and len(rec2["cases"]) == 2
        and {c["case_id"] for c in rec2["cases"]} == set(ref_digs)
        and rec2["population"]["declared"] == 2,
        "detail": "foreign case dir ignored; manifest unchanged",
    }

    all_rej = all(p["rejected"] for p in probes.values())
    json.dump({"gate": "G3", "phase": "P31", "probes": probes,
               "all_rejected": all_rej},
              open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(probes, indent=1))


if __name__ == "__main__":
    main()
