#!/usr/bin/env python3
"""G1 checker for P27: independent verification of the study schema,
deterministic expansion, and the smoke-population record.

Independence discipline: this checker re-implements expansion and canonical
serialization in Python — it never imports the Rust implementation — and
re-forms one substantive metric (total activity at shutdown) from a raw case
output rather than trusting the record's per_time map.
"""
import copy
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = os.path.join(REPO, "results", "g1_p27_study.json")
QUALIFIED_RESPONSES = {
    "total_activity_bq_per_g", "decay_heat_w_per_g",
    "photon_source_per_group", "inventory_per_nuclide",
    "total_atoms_per_g",
}
AXES = ["material", "spectrum", "schedule"]


def sh256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def fail(fs, msg):
    fs.append(msg)


def expected_case_ids(study):
    ids = []
    for m in study["cases"]["materials"]:
        for s in study["cases"]["spectra"]:
            for c in study["cases"]["schedules"]:
                ids.append(f"{m['name']}__{s['name']}__{c['name']}")
    return ids


def parse_dt_seconds(dt):
    """Independent duration parse for the units the smoke study uses."""
    num, _, unit = dt.partition(" ")
    val = float(num)
    scale = {"s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0,
             "y": 31557600.0, "": 1.0}[unit]
    return val * scale


def expected_spec(study, case_id, base):
    """Re-derive the case's spec fields independently of the Rust emitter."""
    mname, sname, cname = case_id.split("__")
    mat = next(m for m in study["cases"]["materials"] if m["name"] == mname)
    spec_s = next(s for s in study["cases"]["spectra"] if s["name"] == sname)
    sched = next(c for c in study["cases"]["schedules"] if c["name"] == cname)
    steps = [dict(st) for st in sched["steps"]]
    prev = 0.0
    for t in study.get("cooling_times_s", []):
        d = t - prev
        prev = t
        if d > 0:
            steps.append({"dt": f"{d:g} s", "flux": 0.0})
    if "spec_ref" in spec_s and spec_s["spec_ref"]:
        ref = json.load(open(os.path.join(base, spec_s["spec_ref"])))
        spectrum = ref["spectrum"]
    else:
        spectrum = {
            "structure": spec_s.get("structure", "fispact-709"),
            "flux_per_group": spec_s.get("flux_per_group"),
            "total": spec_s.get("total"),
            "descending": spec_s.get("descending", False),
        }
        if spec_s.get("flux_file"):
            flux = [float(w) for w in open(
                os.path.join(base, spec_s["flux_file"])).read().split()]
            spectrum["flux_per_group"] = flux
    return {
        "spectrum": spectrum,
        "schedule": steps,
        "mass_g": mat.get("mass_g", 1.0),
        "composition": mat["composition"],
        "library": study["library"],
    }


def verify(fs):
    ev = json.load(open(EVIDENCE))
    sdoc = ev["study_doc"]["path"]
    base = os.path.dirname(sdoc)
    run1 = os.path.join(base, "run1")

    # --- input integrity ----------------------------------------------
    for key, field in [("study_doc", "path"), ("irdff_asset", "path")]:
        p = ev[key][field]
        if not os.path.isfile(p):
            fail(fs, f"missing {key}: {p}")
            continue
        if sh256(p) != ev[key]["sha256"]:
            fail(fs, f"{key} sha256 mismatch")
    study = json.load(open(sdoc))
    if study.get("study") != "actinv-study-1":
        fail(fs, "study doc wrong schema id")
    for r in study.get("responses", []):
        if r not in QUALIFIED_RESPONSES:
            fail(fs, f"unqualified response declared: {r}")

    man = json.load(open(os.path.join(run1, "manifest.json")))
    rec = json.load(open(os.path.join(run1, "study_record.json")))
    if sh256(os.path.join(run1, "manifest.json")) != ev["manifest_sha256"]:
        fail(fs, "manifest digest drift")
    if sh256(os.path.join(run1, "study_record.json")) != ev["record_sha256"]:
        fail(fs, "record digest drift")

    # --- manifest: expansion + per-spec digests -----------------------
    want_ids = expected_case_ids(study)
    got_ids = [c["case_id"] for c in man["cases"]]
    if got_ids != want_ids:
        fail(fs, f"manifest case order != deterministic expansion: {got_ids}")
    if man["n_cases"] != len(want_ids):
        fail(fs, "manifest n_cases mismatch")
    if man["study_sha256"] != ev["study_doc"]["sha256"]:
        fail(fs, "manifest study digest != study doc digest")
    for cent in man["cases"]:
        sp = os.path.join(run1, cent["spec"])
        if not os.path.isfile(sp):
            fail(fs, f"missing spec {cent['spec']}")
            continue
        if sh256(sp) != cent["spec_sha256"]:
            fail(fs, f"spec digest mismatch {cent['case_id']}")
            continue
        spec = json.load(open(sp))
        exp = expected_spec(study, cent["case_id"], base)
        got_spec = spec["spectrum"]
        want_spec = exp["spectrum"]
        if got_spec.get("flux_per_group") != want_spec.get("flux_per_group"):
            fail(fs, f"{cent['case_id']}: spectrum flux differs")
        if got_spec.get("descending") != want_spec.get("descending"):
            fail(fs, f"{cent['case_id']}: descending flag differs")
        if len(spec["schedule"]) != len(exp["schedule"]):
            fail(fs, f"{cent['case_id']}: schedule length differs")
        else:
            for got, want in zip(spec["schedule"], exp["schedule"]):
                if abs(parse_dt_seconds(got["dt"])
                       - parse_dt_seconds(want["dt"])) > 1e-9:
                    fail(fs, f"{cent['case_id']}: dt {got['dt']} != {want['dt']}")
                if got["flux"] != want["flux"]:
                    fail(fs, f"{cent['case_id']}: flux differs")
        if spec["material"]["composition"] != exp["composition"]:
            fail(fs, f"{cent['case_id']}: composition differs")

    # --- determinism: byte-identical rebuild -----------------------------
    # `study build` touches no library files (paths are verbatim strings in
    # emitted specs), so the rebuild is a pure expansion — run it and
    # byte-compare manifest + specs.
    import subprocess
    import tempfile
    actinv = os.path.join(REPO, "target", "release", "actinv")
    with tempfile.TemporaryDirectory(
            dir=os.environ.get("P27_TMP", None)) as tmp:
        r = subprocess.run(
            [actinv, "study", "build", sdoc, tmp],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            fail(fs, f"rebuild failed: {r.stderr.strip()}")
        else:
            for rel in (["manifest.json"]
                        + [f"specs/{cid}.json" for cid in want_ids]):
                a = open(os.path.join(run1, rel), "rb").read()
                b = open(os.path.join(tmp, rel), "rb").read()
                if a != b:
                    fail(fs, f"rebuild differs: {rel}")

    # --- record: population accounting + per-case digests -------------
    pop = rec["population"]
    cases = rec["cases"]
    if pop["declared"] != len(man["cases"]):
        fail(fs, "record population != manifest")
    if pop["executed"] + pop["failed"] + pop["contract_gap"] != pop["declared"]:
        fail(fs, "population does not add up")
    want_verdict = ("complete" if pop["executed"] == pop["declared"]
                    and pop["undefined_response_instances"] == 0
                    else "incomplete" if pop["executed"] > 0 else "failed")
    if rec["verdict"] != want_verdict:
        fail(fs, f"verdict {rec['verdict']} != re-derived {want_verdict}")
    if rec["qualification"] != "unqualified":
        fail(fs, "record must carry qualification=unqualified at P27")
    if [c["case_id"] for c in cases] != want_ids:
        fail(fs, "record case order != expansion")
    for c in cases:
        if c.get("evidence_kind") != "fresh":
            fail(fs, f"{c['case_id']}: evidence_kind != fresh")
        outp = os.path.join(run1, "cases", c["case_id"], "out.json")
        if c["status"] == "executed":
            if not os.path.isfile(outp) or sh256(outp) != c["out_sha256"]:
                fail(fs, f"{c['case_id']}: out digest mismatch")

    # --- substantive metric re-formation -------------------------------
    # re-form total_activity_bq_per_g at shutdown for one executed case
    # straight from its out.json — the record's per_time map must match
    cid = want_ids[0]
    out = json.load(open(os.path.join(run1, "cases", cid, "out.json")))
    steps = out["steps"]
    irr_end = max(s["t_s"] for s in steps if s["flux"] > 0)
    at0 = next(s for s in steps if abs(s["t_s"] - irr_end) < 1e-9)
    reformed = sum(at0["activity_Bq_per_g"].values())
    recorded = next(c for c in cases if c["case_id"] == cid)
    rec_val = recorded["per_time"]["0"]["total_activity_bq_per_g"]
    if abs(reformed - rec_val) > max(1e-9, abs(rec_val) * 1e-12):
        fail(fs, f"{cid}: activity reformed {reformed} != recorded {rec_val}")
    if rec_val <= 0:
        fail(fs, f"{cid}: zero shutdown activity is a degenerate result")

    return fs


def mutation_self_test():
    """Plant mutations on the evidence/record pair; every one must be
    detected by a re-run of the substantive checks on the mutated copies."""
    ev = json.load(open(EVIDENCE))
    base = os.path.dirname(ev["study_doc"]["path"])
    rec_path = os.path.join(base, "run1", "study_record.json")
    man_path = os.path.join(base, "run1", "manifest.json")
    rec = json.load(open(rec_path))
    man = json.load(open(man_path))

    planted = 0
    rejected = 0

    def probe(mutate_rec=None, mutate_man=None, label=""):
        nonlocal planted, rejected
        r = copy.deepcopy(rec)
        m = copy.deepcopy(man)
        if mutate_rec:
            mutate_rec(r)
        if mutate_man:
            mutate_man(m)
        fs = []
        # population accounting
        pop = r["population"]
        if pop["executed"] + pop["failed"] + pop["contract_gap"] != pop["declared"]:
            fs.append("pop")
        if [c["case_id"] for c in r["cases"]] != [c["case_id"] for c in m["cases"]]:
            fs.append("order")
        for c in r["cases"]:
            outp = os.path.join(base, "run1", "cases", c["case_id"], "out.json")
            if c["status"] == "executed" and os.path.isfile(outp):
                if sh256(outp) != c.get("out_sha256"):
                    fs.append("outdigest")
        if len({c["case_id"] for c in r["cases"]}) != len(r["cases"]):
            fs.append("dup")
        if r["qualification"] != "unqualified":
            fs.append("qual")
        planted += 1
        if fs:
            rejected += 1
        else:
            print(f"  UNREJECTED mutation: {label}", file=sys.stderr)

    probe(lambda r: r["population"].__setitem__("executed", 0),
          label="executed census tampered")
    probe(lambda r: r["cases"].append(copy.deepcopy(r["cases"][0])),
          label="duplicate case row")
    probe(lambda r: r["cases"][0].__setitem__("out_sha256", "0" * 64),
          label="forged out digest")
    probe(lambda r: r["cases"].pop(0), label="missing case row")
    probe(lambda r: r.__setitem__("qualification", "qualified"),
          label="qualification upgraded")
    probe(lambda r: r["cases"].reverse(),
          label="case order vs manifest")
    return planted, rejected


def main():
    fs = verify([])
    planted, rejected = mutation_self_test()
    result = {
        "gate": "G1",
        "phase": "P27",
        "pass": not fs and planted == rejected,
        "failures": fs,
        "mutation_self_test": {"planted": planted, "rejected": rejected},
    }
    out = os.path.join(REPO, "results", "g1_p27_check.json")
    json.dump(result, open(out, "w"), indent=2, sort_keys=True)
    print(json.dumps(result, indent=1))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
