#!/usr/bin/env python3
"""P36 G1 checker: flagship execution integrity — manifest, record,
per-case outputs and robustness artifacts verified against digests."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "target", "p36-work", "w-matcmp", "out")
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p36_seals.json")))
OUT = os.path.join(RES, "g1_p36_check.json")

TIMES = ["0", "86400", "31557600", "3155760000", "31557600000"]
RESP = ["total_activity_bq_per_g", "decay_heat_w_per_g",
        "photon_source_per_group", "inventory_per_nuclide",
        "total_atoms_per_g"]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(outdir, fs):
    rec_p = os.path.join(outdir, "study_record.json")
    man_p = os.path.join(outdir, "manifest.json")
    if not (os.path.isfile(rec_p) and os.path.isfile(man_p)):
        fs.append("record or manifest missing")
        return
    rec = json.load(open(rec_p))
    man = json.load(open(man_p))
    if man.get("study_sha256") != SEALS["study"]["sha256"]:
        fs.append("manifest study digest != G0 seal")
    if rec.get("study_sha256") != SEALS["study"]["sha256"]:
        fs.append("record study digest != G0 seal")
    if rec.get("status") != "complete":
        fs.append(f"record status {rec.get('status')}")
    cases = rec.get("cases", [])
    if len(cases) != 3:
        fs.append(f"{len(cases)} case records")
    mids = {c["case_id"] for c in man.get("cases", [])}
    if {c["case_id"] for c in cases} != mids or len(mids) != 3:
        fs.append("record cases != manifest cases")
    # per-case out.json digest + content
    for c in cases:
        cid = c["case_id"]
        if c.get("status") != "executed":
            fs.append(f"{cid} not executed")
        op = os.path.join(outdir, "cases", cid, "out.json")
        if not os.path.isfile(op):
            fs.append(f"{cid} out.json missing")
            continue
        if sha(op) != c.get("out_sha256"):
            fs.append(f"{cid} out.json digest mismatch")
            continue
        o = json.load(open(op))
        pt = c.get("per_time", {})
        if sorted(pt.keys()) != sorted(TIMES):
            fs.append(f"{cid} cooling times wrong")
        for t in TIMES:
            row = pt.get(t, {})
            for r in RESP:
                if r not in row:
                    fs.append(f"{cid} missing {r}@{t}")
        # spec digest vs manifest
        mc = next((m for m in man["cases"] if m["case_id"] == cid), None)
        sp = os.path.join(outdir, mc["spec"])
        if sha(sp) != mc["spec_sha256"]:
            fs.append(f"{cid} spec digest mismatch")
        if c.get("spec_sha256") != mc["spec_sha256"]:
            fs.append(f"{cid} record spec digest mismatch")
        # robustness block
        rob = c.get("robustness")
        if not rob:
            fs.append(f"{cid} no robustness")
        else:
            if rob.get("samples") != 24 or rob.get("seed") != 20260917:
                fs.append(f"{cid} robustness samples/seed wrong")
            arts = rob.get("sample_artifacts", [])
            if len(arts) != 24:
                fs.append(f"{cid} {len(arts)} sample artifacts")
            for a in arts:
                if a.get("failed"):
                    fs.append(
                        f"{cid} sample {a.get('sample')} failed: "
                        f"{a['failed']}")
                i = a.get("sample")
                for suffix in (f"rob_{i}.json", f"rob_{i}.out.json"):
                    ap = os.path.join(outdir, "cases", cid, suffix)
                    if not os.path.isfile(ap):
                        fs.append(f"{cid} missing {suffix}")
            stats = rob.get("responses", {})
            if not stats:
                fs.append(f"{cid} no response stats")
            else:
                row = stats.get("total_activity_bq_per_g", {}) \
                    .get("3155760000", {})
                if row.get("n_samples") != 24 or row.get("mean") is None:
                    fs.append(f"{cid} response stats incomplete")
            if "pathways" not in o or not o["pathways"]:
                fs.append(f"{cid} pathways empty")
        # comparison rules evaluated
        rules = rec.get("comparison", {}).get("rules", [])
        if len(rules) != 3 or any(r["verdict"] == "undefined"
                                for r in rules):
            fs.append("comparison rules missing or undefined")
        if c.get("undefined_responses"):
            fs.append(f"{cid} has undefined responses")


def main():
    fs = []
    check(OUTDIR, fs)
    planted = rejected = 0
    base = json.load(open(os.path.join(OUTDIR, "study_record.json")))
    for label, mut in [
        ("study_sha", lambda v: v.__setitem__("study_sha256", "0" * 64)),
        ("status", lambda v: v.__setitem__("status", "partial")),
        ("undef",
         lambda v: v["cases"][0].__setitem__(
             "undefined_responses", ["x@0"])),
        ("case", lambda v: v["cases"].pop()),
        ("rules",
         lambda v: v["comparison"].__setitem__("rules", [])),
    ]:
        planted += 1
        v = copy.deepcopy(base)
        mut(v)
        tmp = os.path.join(ROOT, "target", "preflight-tmp",
                           f"g1p36-mut-{label}")
        os.makedirs(tmp, exist_ok=True)
        import shutil
        shutil.copy(os.path.join(OUTDIR, "manifest.json"), tmp)
        json.dump(v, open(os.path.join(tmp, "study_record.json"), "w"))
        shutil.copytree(os.path.join(OUTDIR, "cases"),
                        os.path.join(tmp, "cases"), dirs_exist_ok=True)
        shutil.copytree(os.path.join(OUTDIR, "specs"),
                        os.path.join(tmp, "specs"), dirs_exist_ok=True)
        mfs = []
        try:
            check(tmp, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    out = {"gate": "G1", "phase": "P36", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
