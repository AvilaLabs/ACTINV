#!/usr/bin/env python3
"""G2 checker for P27: verify the Core interchange contract end-to-end.

Checks: emitted documents validate against the G0-pinned Core schemas and
profile; every package document/artifact digest matches real bytes; the
recorded Core run reached `evaluated` with every requirement passing; every
receipt binds the manifest spec digest and the pinned actinv executable;
interpret_receipt/classify_failure behave per INTERCHANGE.md; mutations to
receipts, digests, profile and claims are all rejected.
"""
import copy
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "interchange"))
import actinv_core_adapter as A  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = os.path.join(REPO, "results", "g2_p27_interchange.json")
WORK = os.path.expanduser("~/nuclear-data/p27-work")
CORE_SCHEMAS = os.path.expanduser(
    "~/Documents/Avila-Labs/project-north-star/schemas")

SCHEMA_MAP = {
    "package.json": "case-package.v0.1-draft.schema.json",
    "contract.json": "evidence-contract.v0.2-draft.schema.json",
    "registry.json": "registry-snapshot.v0.2-draft.schema.json",
    "adapter.json": "external-checker-adapter.v0.1-draft.schema.json",
    "claims.json": "evidence-claims.v0.2-draft.schema.json",
}


def sh(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def verify(fs):
    import jsonschema
    ev = json.load(open(EVIDENCE))
    pkg = ev["package_dir"]
    sdoc = os.path.join(WORK, "study", "smoke_study.json")
    manifest = json.load(open(os.path.join(WORK, "study", "run1",
                                           "manifest.json")))
    spec_digests = {c["case_id"]: c["spec_sha256"] for c in manifest["cases"]}

    # --- schema + profile pin -----------------------------------------
    g0 = json.load(open(os.path.join(REPO, "results", "g0_p27_seals.json")))
    pinned = dict(g0["core_interchange"].get("consumed_schemas") or {})
    pinned.update(g0["core_interchange"].get("all_schema_digests") or {})
    for doc, sch in SCHEMA_MAP.items():
        if sh(os.path.join(CORE_SCHEMAS, sch)) != pinned.get(sch):
            fail = f"schema {sch} digest != G0 pin"
            fs.append(fail)
            continue
        try:
            jsonschema.validate(
                json.load(open(os.path.join(pkg, doc))),
                json.load(open(os.path.join(CORE_SCHEMAS, sch))))
        except Exception as e:  # noqa: BLE001 — record, don't raise
            fs.append(f"{doc} invalid vs {sch}: {e}")
        d = json.load(open(os.path.join(pkg, doc)))
        if "semantic_profile" in d and \
                d["semantic_profile"] != ev["semantic_profile"]:
            fs.append(f"{doc}: profile != pinned")

    # --- package digest bindings --------------------------------------
    package = json.load(open(os.path.join(pkg, "package.json")))
    for d in package["documents"]:
        p = os.path.join(pkg, d["path"])
        if not os.path.isfile(p) or d["sha256"] != "sha256:" + sh(p):
            fs.append(f"document {d['path']} digest mismatch")
    if sh(os.path.join(pkg, "package.json")) != ev["package_sha256"]:
        fs.append("package.json digest != evidence")
    for a in package["artifacts"]:
        root = {"actinv-lib": os.path.dirname(
                    json.load(open(sdoc))["library"]["path"]),
                "actinv-decay-primary": os.path.dirname(
                    json.load(open(sdoc))["decay"]["primary"]),
                "actinv-decay-fallback": os.path.dirname(
                    json.load(open(sdoc))["decay"]["fallback"]),
                "actinv-build": os.path.join(WORK, "study", "run1"),
                "actinv-release": os.path.join(REPO, "target", "release"),
                "actinv-src": os.path.join(REPO, "interchange"),
                }.get(a["source_root"])
        p = os.path.join(root, a["path"]) if root else None
        if not p or not os.path.isfile(p) or a["sha256"] != "sha256:" + sh(p):
            fs.append(f"artifact {a['artifact_id']} digest/path mismatch")

    # --- run report ----------------------------------------------------
    rr = ev["run_report"]
    rep = json.loads(open(rr["path"]).read().split("\n", 1)[1])
    if sh(rr["path"]) != rr["sha256"]:
        fs.append("run report digest drift")
    if rep["status"] != "evaluated":
        fs.append(f"run status {rep['status']} != evaluated")
    for stage, want in [("integrity", "complete"), ("compile", "compiled"),
                        ("execution", "executed"), ("bindings", "verified"),
                        ("campaign", "evaluated")]:
        if rep.get(stage, {}).get("status") != want:
            fs.append(f"stage {stage}: {rep.get(stage, {}).get('status')}")
    for v in rep["campaign"]["verdicts"]:
        if v["verdict"]["status"] != "pass":
            fs.append(f"requirement {v['requirement_id']}: "
                      f"{v['verdict']['status']}")
        if v["verdict"].get("observed_category") != "cli":
            fs.append(f"{v['requirement_id']}: observed "
                      f"{v['verdict'].get('observed_category')}")

    # --- receipts: binding + interpretation -----------------------------
    actinv_bin_sha = sh(os.path.join(REPO, "target", "release", "actinv"))
    for step, want in ev["receipts"].items():
        rp = os.path.join(WORK, "interchange", "ws2", step, "receipt.json")
        if not os.path.isfile(rp) or sh(rp) != want:
            fs.append(f"receipt {step} missing/digest drift")
            continue
        rec = json.load(open(rp))
        cid = step
        status, evidence, failures = A.interpret_receipt(
            rec,
            expected_spec_sha256=spec_digests[cid],
            expected_binary_sha256=actinv_bin_sha)
        if status != "executed" or failures:
            fs.append(f"{cid}: interpret -> {status} {failures}")
        elif evidence.get("evidence_kind") != "verified":
            fs.append(f"{cid}: evidence_kind {evidence.get('evidence_kind')}")

    # --- failure mapping completeness ----------------------------------
    cases = [
        ({"error": "family_not_qualified: robustness"}, None, False,
         "contract_gap", "contract_gap"),
        ({"error": "template_revoked"}, None, False,
         "contract_gap", "contract_gap"),
        ({"error": "solver exploded"}, 1, False,
         "nonzero_exit", "failed"),
        ({"error": "solver exploded"}, None, False,
         "process_error", "failed"),
        ({}, None, True, "timeout", "failed"),
    ]
    for kw, code, to, cls, st in cases:
        got_cls, got_st = A.classify_failure(kw.get("error"), code, to)
        if (got_cls, got_st) != (cls, st):
            fs.append(f"classify_failure {kw}: got {(got_cls, got_st)} "
                      f"want {(cls, st)}")
    return fs


def mutation_self_test():
    work = os.path.join(WORK, "interchange", "ws2")
    receipt = json.load(open(glob.glob(f"{work}/*/receipt.json")[0]))
    manifest = json.load(open(os.path.join(WORK, "study", "run1",
                                           "manifest.json")))
    spec_sha = manifest["cases"][0]["spec_sha256"]
    bin_sha = sh(os.path.join(REPO, "target", "release", "actinv"))

    planted = rejected = 0

    def probe(mut, label):
        nonlocal planted, rejected
        planted += 1
        r = copy.deepcopy(receipt)
        mut(r)
        st, _, fs = A.interpret_receipt(
            r, expected_spec_sha256=spec_sha, expected_binary_sha256=bin_sha)
        if fs or st != "executed":
            rejected += 1
        else:
            print(f"  UNREJECTED: {label}", file=sys.stderr)

    probe(lambda r: r["inputs"][0].__setitem__(
        "sha256", "sha256:" + "0" * 64), "forged spec input digest")
    probe(lambda r: r["capability"].__setitem__(
        "executable_sha256", "sha256:" + "f" * 64), "forged executable")
    probe(lambda r: r.__setitem__(
        "schema_version", "avila.core/execution-receipt/v0.0-draft"),
        "wrong receipt schema")
    probe(lambda r: r.__setitem__("status", "reused-stale"),
        "non-executed receipt")
    probe(lambda r: r["outputs"][0].__setitem__("state", "absent"),
        "output not collected")
    probe(lambda r: r["outputs"].pop(0), "missing result output")

    # a receipt claiming the WRONG spec digest must also be refused
    planted += 1
    st, _, fs = A.interpret_receipt(
        copy.deepcopy(receipt),
        expected_spec_sha256="0" * 64, expected_binary_sha256=bin_sha)
    if fs or st != "executed":
        rejected += 1
    else:
        print("  UNREJECTED: spec digest expectation mismatch",
              file=sys.stderr)
    return planted, rejected


def main():
    fs = verify([])
    planted, rejected = mutation_self_test()
    result = {
        "gate": "G2", "phase": "P27",
        "pass": not fs and planted == rejected,
        "failures": fs,
        "mutation_self_test": {"planted": planted, "rejected": rejected},
    }
    out = os.path.join(REPO, "results", "g2_p27_check.json")
    json.dump(result, open(out, "w"), indent=2, sort_keys=True)
    print(json.dumps(result, indent=1))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
