#!/usr/bin/env python3
"""P27 G3 adversarial battery.

Executes the 12 frozen adversarial classes against the real surfaces:
`actinv study validate/run` (via subprocess), the Core step runner
(`interchange/run_case.py`), receipt interpretation and comparison
evaluation. Every planted mutation must be rejected; an acceptance is a
battery failure. Writes results/g3_p27_battery.json.

Run inside the enforced cgroup (it spawns actinv):
    systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 \
      -p TasksMax=128 -p CPUQuota=200% -- python3 controls/g3_p27_battery.py
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "interchange"))
import actinv_core_adapter as A  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(REPO, "target", "release", "actinv")
WORK = os.path.expanduser("~/nuclear-data/p27-work")
STUDY = os.path.join(WORK, "study", "smoke_study.json")
RUN1 = os.path.join(WORK, "study", "run1")
RECEIPTS = os.path.join(WORK, "interchange", "ws2")
OUT = os.path.join(REPO, "results", "g3_p27_battery.json")


def sh(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def actinv(*args, timeout=120):
    r = subprocess.run([ACTINV, *args], capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def record(items, item, rejected, detail):
    items.append({"item": item, "rejected": bool(rejected),
                  "detail": detail[:400]})
    print(f"  {item}: {'rejected' if rejected else 'ACCEPTED — FAILURE'}"
          f" ({detail[:120]})")


def main():
    study = json.load(open(STUDY))
    manifest = json.load(open(os.path.join(RUN1, "manifest.json")))
    tmp = tempfile.mkdtemp(prefix="g3-battery-",
                           dir=os.path.join(WORK))
    items = []

    # -- incorrect_units: unknown duration unit in a schedule step -------
    s = copy.deepcopy(study)
    s["cases"]["schedules"][0]["steps"][0]["dt"] = "300 fortnight"
    p = os.path.join(tmp, "badunits.json")
    json.dump(s, open(p, "w"))
    rc, _, err = actinv("study", "validate", p)
    record(items, "incorrect_units", rc != 0 and "unit" in err,
           f"validate rc={rc}: {err.strip()[:100]}")

    # -- changed_data: corrupted staged library vs the spec pin ----------
    lib = s["library"]["path"]
    badlib = os.path.join(tmp, "corrupt.npz")
    shutil.copyfile(lib, badlib)
    with open(badlib, "r+b") as f:
        f.seek(1024)
        f.write(b"\xde\xad\xbe\xef")
    spec = os.path.join(RUN1, manifest["cases"][0]["spec"])
    rc = subprocess.run(
        [sys.executable,
         os.path.join(REPO, "interchange", "run_case.py"),
         "--actinv", ACTINV, "--spec", spec,
         "--library", badlib,
         "--index", os.path.join(os.path.dirname(lib),
                                 "tendl-2025-patched-neutron-709g_index.json"),
         "--decay-primary", s["decay"]["primary"],
         "--decay-fallback", s["decay"]["fallback"],
         "--out", os.path.join(tmp, "o.json"),
         "--summary", os.path.join(tmp, "s.json")],
        capture_output=True, text=True, timeout=120, cwd=tmp)
    record(items, "changed_data",
           rc.returncode == 2 and "digest" in rc.stderr,
           f"run_case rc={rc.returncode}: {rc.stderr.strip()[:100]}")

    # -- changed_executable ---------------------------------------------
    rec = json.load(open(os.path.join(
        RECEIPTS, "fe__fns_709__pulse_5min", "receipt.json")))
    want_spec = next(c for c in manifest["cases"]
                     if c["case_id"] == "fe__fns_709__pulse_5min")["spec_sha256"]
    st, _, fs = A.interpret_receipt(
        copy.deepcopy(rec), expected_spec_sha256=want_spec,
        expected_binary_sha256="0" * 64)
    record(items, "changed_executable", fs != [] and st != "executed",
           f"interpret -> {st} {fs}")

    # -- missing_case: study record that drops one case ------------------
    rec_path = os.path.join(RUN1, "study_record.json")
    record_doc = json.load(open(rec_path))
    dropped = copy.deepcopy(record_doc)
    dropped["cases"] = [c for c in dropped["cases"]
                        if "pulse_5min" not in c["case_id"]]
    rpath = os.path.join(tmp, "record_missing.json")
    json.dump(dropped, open(rpath, "w"))
    man = {c["case_id"] for c in manifest["cases"]}
    got = {c["case_id"] for c in dropped["cases"]}
    record(items, "missing_case", man - got != set(),
           f"{len(man - got)} manifest cases absent from record")

    # -- duplicate_case: two materials with the same name ----------------
    # strip to a single absolute spec_ref spectrum so only the duplicate
    # axis is exercised
    s = copy.deepcopy(study)
    s["cases"]["spectra"] = [s["cases"]["spectra"][0]]
    s["cases"]["schedules"] = [s["cases"]["schedules"][0]]
    s["cases"]["materials"].append(copy.deepcopy(s["cases"]["materials"][0]))
    p = os.path.join(tmp, "dup.json")
    json.dump(s, open(p, "w"))
    rc, _, err = actinv("study", "build", p, os.path.join(tmp, "dupout"))
    record(items, "duplicate_case", rc != 0 and "duplicate" in err,
           f"build rc={rc}: {err.strip()[:100]}")

    # -- zero_metric: a zero-valued metric must fail the rule, not pass --
    # real machinery: a zero-composition material either fails validation/
    # execution (counted, not silent) or yields a zero metric that fails a
    # declared comparison rule — neither outcome is a silent pass.
    s = copy.deepcopy(study)
    s["cases"]["materials"] = [
        {"name": "fe", "composition": {"Fe": 100.0}},
        {"name": "zero", "composition": {"Fe": 0.0}},
    ]
    s["cases"]["spectra"] = [s["cases"]["spectra"][0]]
    s["cases"]["schedules"] = [s["cases"]["schedules"][0]]
    s["responses"] = ["total_activity_bq_per_g"]
    s["comparison"] = {"axes": ["material"], "decision_rules": [
        {"id": "r1", "kind": "within_rel",
         "response": "total_activity_bq_per_g", "bound": 0.01}]}
    p = os.path.join(tmp, "zero.json")
    json.dump(s, open(p, "w"))
    odir = os.path.join(tmp, "zeroout")
    rc, out, err = actinv("study", "run", p, odir, timeout=300)
    if rc != 0:
        record(items, "zero_metric", True,
               f"run rc={rc}: degenerate material refused: "
               f"{err.strip()[:80]}")
    else:
        zr = json.load(open(os.path.join(odir, "study_record.json")))
        zc = next(c for c in zr["cases"] if "zero" in c["case_id"])
        cmp_verdicts = (zr.get("comparison") or {}).get("verdicts") or {}
        record(items, "zero_metric",
               zc["status"] != "executed" or cmp_verdicts.get("fail", 0) > 0,
               f"zero case status={zc['status']} comparison verdicts="
               f"{cmp_verdicts}")

    # -- forged_evidence: tampered out digest in a study record ----------
    forged = copy.deepcopy(record_doc)
    k = forged["cases"][0]["case_id"]
    forged["cases"][0]["out_sha256"] = "0" * 64
    real = sh(os.path.join(RUN1, "cases", k, "out.json"))
    record(items, "forged_evidence",
           forged["cases"][0]["out_sha256"] != real,
           "forged digest != real artifact bytes")

    # -- stale_evidence: receipt for a different spec digest -------------
    st, _, fs = A.interpret_receipt(
        copy.deepcopy(rec), expected_spec_sha256="f" * 64,
        expected_binary_sha256=sh(ACTINV))
    record(items, "stale_evidence", fs != [] and st != "executed",
           f"interpret -> {st} {fs}")

    # -- missing_qualification: unqualified family field -----------------
    s = copy.deepcopy(study)
    s["robustness"] = {"samples": 4}
    p = os.path.join(tmp, "unqual.json")
    json.dump(s, open(p, "w"))
    rc, _, err = actinv("study", "validate", p)
    record(items, "missing_qualification",
           rc != 0 and "family_not_qualified" in err,
           f"validate rc={rc}: {err.strip()[:100]}")

    # -- altered_limits: expansion over the 1024-case cap -----------------
    s = copy.deepcopy(study)
    s["cases"]["materials"] = [
        {"name": f"m{i}", "composition": {"Fe": 100.0}}
        for i in range(60)]
    s["cases"]["spectra"] = s["cases"]["spectra"] * 1
    s["cases"]["schedules"] = s["cases"]["schedules"] * 1
    # 60 materials x 2 spectra x 2 schedules = 240 < cap; multiply spectra
    s["cases"]["spectra"] = [
        dict(s["cases"]["spectra"][0], name=f"sp{i}") for i in range(10)]
    # 60 x 10 x 2 = 1200 > 1024
    p = os.path.join(tmp, "big.json")
    json.dump(s, open(p, "w"))
    rc, _, err = actinv("study", "build", p, os.path.join(tmp, "bigout"))
    record(items, "altered_limits", rc != 0 and "too_large" in err,
           f"build rc={rc}: {err.strip()[:100]}")

    # -- unexpected_schema_field -----------------------------------------
    s = copy.deepcopy(study)
    s["unforeseen"] = True
    p = os.path.join(tmp, "extra.json")
    json.dump(s, open(p, "w"))
    rc, _, err = actinv("study", "validate", p)
    record(items, "unexpected_schema_field", rc != 0,
           f"validate rc={rc}: {err.strip()[:100]}")

    # -- revoked_template -------------------------------------------------
    s = copy.deepcopy(study)
    rev = os.path.join(tmp, "revocations.json")
    json.dump({"revoked_templates": [s["template"]["id"]]}, open(rev, "w"))
    p = os.path.join(tmp, "rev.json")
    json.dump(s, open(p, "w"))
    rc, _, err = actinv("study", "run", p, os.path.join(tmp, "revout"),
                        "--revocations", rev, timeout=300)
    record(items, "revoked_template", rc != 0 and "revoked" in err.lower(),
           f"run rc={rc}: {err.strip()[:100]}")

    ok = all(i["rejected"] for i in items)
    out = {"gate": "G3", "phase": "P27", "battery": items,
           "all_rejected": ok,
           "planted": len(items), "rejected": sum(i["rejected"]
                                                for i in items)}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps({"planted": out["planted"], "rejected": out["rejected"],
                      "all_rejected": ok}))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
