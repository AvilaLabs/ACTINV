#!/usr/bin/env python3
"""P31 G1 independent checker: re-derives prepared-run counts from the
signature fields of the generated specs, re-verifies resumed-case
artifact integrity byte-for-byte, checks resume output identity and
reject planted mutations."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p31_seals.json")))
EV = os.path.join(RES, "g1_p31_campaign.json")
OUT = os.path.join(RES, "g1_p31_check.json")

SIG_FIELDS = ["library", "decay", "photon_response", "fission_yields",
              "projectile", "spectrum_structure", "spectrum_boundaries",
              "spectrum_descending", "spectrum_flux_per_group",
              "temperature_K", "uncertainty", "radiological", "damage",
              "self_shielding"]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def signature(spec):
    """Re-derive the prepared-run signature the executor would compute."""
    sp = spec["spectrum"]
    return canon({
        "library": spec["library"],
        "decay": spec["decay"],
        "photon_response": spec.get("photon", {}).get("response"),
        "fission_yields": spec.get("fission_yields"),
        "projectile": spec.get("projectile"),
        "spectrum_structure": sp["structure"],
        "spectrum_boundaries": sp.get("boundaries_eV"),
        "spectrum_descending": sp.get("descending"),
        "spectrum_flux_per_group": sp["flux_per_group"],
        "temperature_K": spec["options"]["temperature_K"],
        "uncertainty": spec.get("uncertainty"),
        "radiological": spec.get("radiological"),
        "damage": spec.get("damage"),
        "self_shielding": spec.get("self_shielding"),
    })


def check(ev, fs):
    seals = SEALS
    if ev.get("phase") != "P31" or ev.get("gate") != "G1":
        fs.append("gate/phase wrong")

    rec_path = ev["record_path"]
    rb_path = ev["robust_record_path"]
    rec = json.load(open(rec_path))
    outdir = os.path.dirname(rec_path)

    # 1. signature count: distinct signatures across the generated specs
    specs = {}
    for c in rec["cases"]:
        sp = os.path.join(outdir, "specs", c["case_id"] + ".json")
        specs[c["case_id"]] = json.load(open(sp))
    sigs = {signature(s) for s in specs.values()}
    measured = ev["measured"]["cold"]["prepared_runs"]
    if len(sigs) != measured:
        fs.append(
            f"prepared_runs {measured} != distinct signatures {len(sigs)}")
    if measured != seals["validation_population"]["targets"][
            "smoke_prepared_runs"]:
        fs.append("prepared_runs != sealed target")

    # 2. every recorded case: spec digest matches manifest + file
    manifest = json.load(open(os.path.join(outdir, "manifest.json")))
    mcase = {c["case_id"]: c for c in manifest["cases"]}
    for c in rec["cases"]:
        cid = c["case_id"]
        sp = os.path.join(outdir, "specs", cid + ".json")
        if sha(sp) != mcase[cid]["spec_sha256"]:
            fs.append(f"{cid}: spec digest != manifest")
        if c["spec_sha256"] != mcase[cid]["spec_sha256"]:
            fs.append(f"{cid}: record spec digest != manifest")
        if c["status"] == "executed":
            op = os.path.join(outdir, "cases", cid, "out.json")
            if sha(op) != c["out_sha256"]:
                fs.append(f"{cid}: out.json digest != record")
            if c.get("evidence_kind") != "resumed":
                fs.append(f"{cid}: final record not marked resumed")

    # 3. resume accounted every case
    resumed = set(ev["measured"]["warm_resume"]["resumed_cases"])
    all_ids = {c["case_id"] for c in rec["cases"]}
    if resumed != all_ids:
        fs.append(f"resumed set incomplete: {all_ids - resumed}")
    if not ev["measured"]["resume_digests_identical"]:
        fs.append("resumed digests not identical to cold run")
    if ev["measured"]["warm_resume"]["prepared_runs"] != 0:
        fs.append("resume built prepared runs for skipped cases")

    # 4. robustness workload: 2 signatures (nominal + uncertainty)
    rb_rec = json.load(open(rb_path))
    if ev["measured"]["robustness_workload"]["prepared_runs"] != \
            seals["validation_population"]["targets"][
                "robustness_prepared_runs"]:
        fs.append("robustness prepared_runs != sealed target")
    for c in rb_rec["cases"]:
        if c["status"] != "executed":
            fs.append(f"robustness {c['case_id']} not executed")
        # every sample artifact verifies against its recorded digest
        arts = c["robustness"]["sample_artifacts"]
        cdir = os.path.join(os.path.dirname(rb_path), "cases",
                            c["case_id"])
        for a in arts:
            i = a["sample"]
            sp = os.path.join(cdir, f"rob_{i}.json")
            if a.get("failed") is not None:
                continue
            if sha(sp) != a["spec_sha256"]:
                fs.append(f"{c['case_id']} sample {i} spec digest bad")
            if "out_sha256" in a:
                op = os.path.join(cdir, f"rob_{i}.out.json")
                if sha(op) != a["out_sha256"]:
                    fs.append(
                        f"{c['case_id']} sample {i} out digest bad")


def main():
    ev = json.load(open(EV))
    fs = []
    check(ev, fs)

    planted = rejected = 0

    def mut(key_path, value):
        v = copy.deepcopy(ev)
        d = v
        for k in key_path[:-1]:
            d = d[k]
        d[key_path[-1]] = value
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        return mfs

    for key_path, value in [
        (["measured", "cold", "prepared_runs"], 1),
        (["measured", "resume_digests_identical"], True)
        if not ev["measured"]["resume_digests_identical"]
        else (["measured", "warm_resume", "resumed_cases"], []),
        (["measured", "robustness_workload", "prepared_runs"], 99),
        (["record_path"], "/nonexistent/study_record.json"),
        (["phase"], "P32"),
    ]:
        planted += 1
        mfs = mut(key_path, value)
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", key_path, file=sys.stderr)

    out = {"gate": "G1", "phase": "P31", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
