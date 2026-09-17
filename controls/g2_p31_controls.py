#!/usr/bin/env python3
"""P31 G2 controls: reuse_correctness, resume_torn, resume_corrupt,
resume_stale_spec, distinct_signatures, interrupted_run."""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
G1 = json.load(open(os.path.join(ROOT, "results", "g1_p31_campaign.json")))
WORK = os.path.expanduser("~/nuclear-data/p31-work/g2")
OUT = os.path.join(ROOT, "results", "g2_p31_controls.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "g1c", os.path.join(ROOT, "controls", "g1_p31_campaign.py"))
_g1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_g1)


def cgroup(cmd, env_flags=()):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *env_flags, *cmd]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def run(study, outdir, env=None):
    os.makedirs(outdir, exist_ok=True)
    sp = os.path.join(outdir, "study.json")
    if not os.path.exists(sp):
        json.dump(study, open(sp, "w"), indent=1)
    e = dict(os.environ)
    e.update(env or {})
    flags = [f"{k}={v}" for k, v in (env or {}).items()]
    r = subprocess.run(
        cgroup([ACTINV, "study", "run", sp, outdir],
               env_flags=flags),
        capture_output=True, text=True, env=e)
    return r


VOLATILE = {"ms", "wall_s", "elapsed_ms"}


def semantic(o):
    if isinstance(o, dict):
        return {k: semantic(v) for k, v in o.items()
                if k not in VOLATILE}
    if isinstance(o, list):
        return [semantic(v) for v in o]
    return o


def sem_sha(p):
    return hashlib.sha256(json.dumps(
        semantic(json.load(open(p))), sort_keys=True).encode()
    ).hexdigest()


def out_digests(outdir):
    cdir = os.path.join(outdir, "cases")
    return {d: sem_sha(os.path.join(cdir, d, "out.json"))
            for d in sorted(os.listdir(cdir))
            if os.path.isfile(os.path.join(cdir, d, "out.json"))}


def record_digs(rec):
    return {c["case_id"]: sem_sha_from_sem(c)
            for c in rec["cases"]}


def sem_sha_from_sem(o):
    return hashlib.sha256(json.dumps(
        semantic(o), sort_keys=True).encode()).hexdigest()


def main():
    os.makedirs(WORK, exist_ok=True)
    controls = {}
    cold_dir = os.path.dirname(G1["record_path"])

    # --- reuse_correctness: shared-prep vs per-case-prepared ----------
    baseline_dir = os.path.join(WORK, "baseline")
    shutil.rmtree(baseline_dir, ignore_errors=True)
    r = run(_g1.smoke_study("p31-smoke"), baseline_dir,
            env={"ACTINV_STUDY_NO_REUSE": "1"})
    rec = json.load(open(os.path.join(baseline_dir,
                                      "study_record.json")))
    cold = json.load(open(G1["record_path"]))
    cold_digs = out_digests(cold_dir)
    base_digs = out_digests(baseline_dir)
    controls["reuse_correctness"] = {
        "status": "pass"
        if r.returncode == 0 and cold_digs == base_digs
        and rec["prepared_runs"] == len(rec["cases"]) else "fail",
        "prepared_runs": rec["prepared_runs"],
        "digests_identical": cold_digs == base_digs,
    }

    # --- resume_torn: delete one out.json, resume ---------------------
    torn_dir = os.path.join(WORK, "torn")
    shutil.rmtree(torn_dir, ignore_errors=True)
    shutil.copytree(cold_dir, torn_dir,
                    ignore=shutil.ignore_patterns("study.json"))
    json.dump(_g1.smoke_study("p31-smoke"),
              open(os.path.join(torn_dir, "study.json"), "w"),
              indent=1)
    victim = "fe__irdff_sp_mat9861_709__cont_1d"
    os.remove(os.path.join(torn_dir, "cases", victim, "out.json"))
    r = run(_g1.smoke_study("p31-smoke"), torn_dir)
    rec = json.load(open(os.path.join(torn_dir, "study_record.json")))
    resumed = set(rec["resumed_cases"])
    controls["resume_torn"] = {
        "status": "pass" if r.returncode == 0
        and victim not in resumed
        and resumed == ({c["case_id"] for c in cold["cases"]} - {victim})
        and out_digests(torn_dir) == out_digests(cold_dir) else "fail",
        "resumed": sorted(resumed),
    }

    # --- resume_corrupt: truncate an out.json, resume -----------------
    cor_dir = os.path.join(WORK, "corrupt")
    shutil.rmtree(cor_dir, ignore_errors=True)
    shutil.copytree(cold_dir, cor_dir,
                    ignore=shutil.ignore_patterns("study.json"))
    json.dump(_g1.smoke_study("p31-smoke"),
              open(os.path.join(cor_dir, "study.json"), "w"),
              indent=1)
    op = os.path.join(cor_dir, "cases", victim, "out.json")
    with open(op, "r+") as f:
        f.truncate(max(0, os.path.getsize(op) // 2))
    r = run(_g1.smoke_study("p31-smoke"), cor_dir)
    rec = json.load(open(os.path.join(cor_dir, "study_record.json")))
    controls["resume_corrupt"] = {
        "status": "pass" if r.returncode == 0
        and victim not in set(rec["resumed_cases"])
        and out_digests(cor_dir) == out_digests(cold_dir) else "fail",
        "resumed": rec["resumed_cases"],
    }

    # --- resume_stale_spec: edit a case spec on disk, resume ----------
    stale_dir = os.path.join(WORK, "stale")
    shutil.rmtree(stale_dir, ignore_errors=True)
    shutil.copytree(cold_dir, stale_dir,
                    ignore=shutil.ignore_patterns("study.json"))
    # a spec the user edited on disk must be detected by the executor's
    # own re-derivation — edit the *study* so the rebuilt spec differs
    st = _g1.smoke_study("p31-smoke")
    for sch in st["cases"]["schedules"]:
        if sch["name"] == "cont_1d":
            sch["steps"][0]["dt"] = "86401 s"
    r = run(st, stale_dir)
    rec = json.load(open(os.path.join(stale_dir, "study_record.json")))
    cont_ids = {cid for cid in out_digests(cold_dir)
                if "cont_1d" in cid}
    resumed = set(rec["resumed_cases"])
    controls["resume_stale_spec"] = {
        "status": "pass" if r.returncode == 0
        and not (cont_ids & resumed) else "fail",
        "resumed": sorted(resumed),
        "re_executed": sorted(cont_ids - resumed),
    }

    # --- distinct_signatures: spectrum keys -> 2 prepared runs --------
    controls["distinct_signatures"] = {
        "status": "pass"
        if G1["measured"]["cold"]["prepared_runs"] == 2
        and len({c["case_id"] for c in cold["cases"]}) == 8
        else "fail",
        "prepared_runs": G1["measured"]["cold"]["prepared_runs"],
    }

    # --- interrupted_run: kill mid-campaign, resume -------------------
    int_dir = os.path.join(WORK, "interrupted")
    shutil.rmtree(int_dir, ignore_errors=True)
    os.makedirs(int_dir)
    sp = os.path.join(int_dir, "study.json")
    json.dump(_g1.smoke_study("p31-smoke"), open(sp, "w"),
              indent=1)
    # kill mid-campaign: wait for the first streamed partial record
    proc = subprocess.Popen(
        cgroup([ACTINV, "study", "run", sp, int_dir]),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec_path = os.path.join(int_dir, "study_record.json")
    deadline = time.time() + 60
    while time.time() < deadline:
        if os.path.isfile(rec_path):
            break
        time.sleep(0.05)
    proc.kill()
    proc.wait()
    if not os.path.isfile(rec_path):
        partial = {"cases_completed": 0}
    else:
        partial = json.load(open(rec_path))
    r = run(_g1.smoke_study("p31-smoke"), int_dir)
    rec = json.load(open(os.path.join(int_dir, "study_record.json")))
    n_partial = partial.get("cases_completed", 0)
    controls["interrupted_run"] = {
        "status": "pass" if r.returncode == 0
        and rec["population"]["executed"] == 8
        and n_partial < 8
        and out_digests(int_dir) == cold_digs
        else "fail",
        "partial_cases_completed": n_partial,
        "resumed": rec["resumed_cases"],
    }

    all_pass = all(c["status"] == "pass" for c in controls.values())
    json.dump({"gate": "G2", "phase": "P31", "controls": controls},
              open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(controls, indent=1))


if __name__ == "__main__":
    main()
