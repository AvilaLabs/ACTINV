#!/usr/bin/env python3
"""ACTINV -> Avila Core interchange adapter (P27).

Translates an `actinv-study-1` document plus its deterministic build output
(manifest + emitted specs) into an Avila Core case package, and interprets
Core execution receipts back into study-record case-row updates.

The adapter never invokes the solver and never writes study output; it only
maps documents. Scope: the G0-qualified pair ACT-STUDY-01 / ACT-COMPARE-01
with fispact-709 neutron activation on a hash-pinned artifact.

Qualification boundary: the package it emits carries `unquantified` claim
models and the study record keeps `qualification: unqualified`. A receipt
establishes process facts, not scientific correctness.
"""
import hashlib
import json
import os
import sys

SEMANTIC_PROFILE = "avila.core/semantic/0.2-draft"
ADAPTER_ID = "avila-labs.actinv/run-case@1"
CONTRACT_SCHEMA = "avila.core/evidence-contract/v0.2-draft"
PACKAGE_SCHEMA = "avila.core/case-package/v0.1-draft"
REGISTRY_SCHEMA = "avila.core/registry-snapshot/v0.2-draft"
RECEIPT_SCHEMA = "avila.core/execution-receipt/v0.1-draft"
CLAIMS_SCHEMA = "avila.core/evidence-claims/v0.2-draft"
ADAPTER_SCHEMA = "avila.core/external-checker-adapter/v0.1-draft"

# Refusal strings the ACTINV side emits before any solve; a receipt or error
# carrying one of these is a contract_gap, not a process failure. Keep this a
# superset of the native per-case classifier in crates/actinv-core/src/study.rs
# (search "let gap ="), or the same refusal classifies differently per path.
CONTRACT_GAP_MARKERS = (
    "family_not_qualified",
    "not in the qualified",
    "unsupported study version",
    "study_too_large",
    "template_revoked",
    "cannot stat",
    "not found",
    "coverage",
)

FAILURE_CLASSES = (
    "capability_mismatch",
    "nonzero_exit",
    "timeout",
    "missing_output",
    "malformed_output",
    "contract_gap",
    "process_error",
)

MEDIA = {
    "spec": "application/vnd.actinv.problem+json",
    "result": "application/vnd.actinv.result+json",
    "library": "application/vnd.numpy.npz",
    "index": "application/json",
    "decay": "application/octet-stream",
    "executable": "application/x-executable",
    "notice": "text/markdown",
    "adapter": "application/vnd.avila.external-checker-adapter+json",
}


def sh256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def sh256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha(s):
    return "sha256:" + s


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
    return sh256_file(path)


def registry_snapshot(study_id):
    """Minimal registry the emitted contract needs: one dimensionless kind,
    the internal-integration purpose, and the actinv roles/capability types."""
    return {
        "schema_version": REGISTRY_SCHEMA,
        "semantic_profile": SEMANTIC_PROFILE,
        "registry_id": f"{study_id}.registry",
        "revision": 1,
        "kinds": [{
            "kind_id": "core.dimensionless-ratio",
            "canonical_unit": "1",
            "unit_class": "core.dimensionless-ratio.units@1",
            "owner": "avila-labs.core",
            "units": [{"symbol": "1", "factor": "1"}],
        }],
        "purposes": [
            {"purpose": {
                "id": "avila-labs.core.internal-integration", "major": 1},
             "owner": "avila-labs.core",
             "description": "Internal integration testing inside Avila Labs; "
                            "no regulatory or operational use."},
            {"purpose": {
                "id": "nuclear.regulatory-or-operational-use", "major": 1},
             "owner": "external-responsible-authority",
             "description": "Any regulatory, facility-acceptance, packaging, "
                            "transportation, shipment, safety, contractual, "
                            "or operational use; study outputs explicitly "
                            "exclude this purpose."},
        ],
        "roles": [
            {"role": {"id": "actinv.problem", "major": 1},
             "owner": "avila-labs.actinv",
             "validator": "actinv.validate.problem@1",
             "accepted_media_types": [MEDIA["spec"]],
             "permitted_claim_models": [{"model": "unquantified"}]},
            {"role": {"id": "actinv.run-output", "major": 1},
             "owner": "avila-labs.actinv",
             "validator": "actinv.validate.result@1",
             "accepted_media_types": [MEDIA["result"]],
             "permitted_claim_models": [{"model": "unquantified"}]},
            {"role": {"id": "actinv.run-summary", "major": 1},
             "owner": "avila-labs.actinv",
             "validator": "actinv.validate.run-summary@1",
             "accepted_media_types": [MEDIA["index"]],
             "permitted_claim_models": [{"model": "unquantified"}],
             "categorical_values": ["cli", "study"],
             "non_claims": [
                 "The extracted entry point only attests which ACTINV "
                 "invocation path produced the artifact; it asserts no "
                 "scientific, qualification, or regulatory property."]},
            {"role": {"id": "actinv.executable", "major": 1},
             "owner": "avila-labs.actinv",
             "validator": "actinv.validate.executable@1",
             "accepted_media_types": [MEDIA["executable"]],
             "permitted_claim_models": [{"model": "unquantified"}]},
            {"role": {"id": "actinv.activation-library", "major": 1},
             "owner": "avila-labs.actinv",
             "validator": "actinv.validate.activation-library@1",
             "accepted_media_types": [MEDIA["library"]],
             "permitted_claim_models": [{"model": "unquantified"}]},
            {"role": {"id": "actinv.activation-index", "major": 1},
             "owner": "avila-labs.actinv",
             "validator": "actinv.validate.activation-index@1",
             "accepted_media_types": [MEDIA["index"]],
             "permitted_claim_models": [{"model": "unquantified"}]},
            {"role": {"id": "actinv.decay-data", "major": 1},
             "owner": "avila-labs.actinv",
             "validator": "actinv.validate.decay-data@1",
             "accepted_media_types": [MEDIA["decay"]],
             "permitted_claim_models": [{"model": "unquantified"}]},
            {"role": {"id": "actinv.step-runner", "major": 1},
             "owner": "avila-labs.actinv",
             "validator": "actinv.validate.step-runner@1",
             "accepted_media_types": ["text/x-python"],
             "permitted_claim_models": [{"model": "unquantified"}]},
        ],
        "capability_types": [{
            "capability_type": {"id": "actinv.run", "major": 1},
            "owner": "avila-labs.actinv",
            "reproducibility": {"determinism": "deterministic"},
            "inputs": [
                {"slot_id": "spec",
                 "role": {"id": "actinv.problem", "major": 1},
                 "accepted_media_types": [MEDIA["spec"]]},
                {"slot_id": "actinv",
                 "role": {"id": "actinv.executable", "major": 1},
                 "accepted_media_types": [MEDIA["executable"]]},
                {"slot_id": "runner",
                 "role": {"id": "actinv.step-runner", "major": 1},
                 "accepted_media_types": ["text/x-python"]},
                {"slot_id": "activation-library",
                 "role": {"id": "actinv.activation-library", "major": 1},
                 "accepted_media_types": [MEDIA["library"]]},
                {"slot_id": "library-index",
                 "role": {"id": "actinv.activation-index", "major": 1},
                 "accepted_media_types": [MEDIA["index"]]},
                {"slot_id": "decay-primary",
                 "role": {"id": "actinv.decay-data", "major": 1},
                 "accepted_media_types": [MEDIA["decay"]]},
                {"slot_id": "decay-fallback",
                 "role": {"id": "actinv.decay-data", "major": 1},
                 "accepted_media_types": [MEDIA["decay"]]},
            ],
            "outputs": [{
                "slot_id": "result",
                "role": {"id": "actinv.run-output", "major": 1},
                "media_type": MEDIA["result"],
                "permitted_claim_models": [
                    {"model": "unquantified"},
                ],
                "excluded_purposes": [{
                    "id": "nuclear.regulatory-or-operational-use",
                    "major": 1}],
            }, {
                "slot_id": "summary",
                "role": {"id": "actinv.run-summary", "major": 1},
                "media_type": MEDIA["index"],
                "permitted_claim_models": [
                    {"model": "unquantified"},
                ],
                "excluded_purposes": [{
                    "id": "nuclear.regulatory-or-operational-use",
                    "major": 1}],
            }],
        }],
    }


def external_checker_adapter():
    """The package-declared adapter: `actinv run <spec> <out>` with no shell."""
    return {
        "schema_version": ADAPTER_SCHEMA,
        "adapter_id": ADAPTER_ID,
        "capability_type": {"id": "actinv.run", "major": 1},
        "input_slots": ["runner", "spec", "actinv", "activation-library",
                        "library-index", "decay-primary", "decay-fallback"],
        "arguments": [
            {"kind": "input_path", "input_slot": "runner"},
            {"kind": "literal", "value": "--actinv"},
            {"kind": "input_path", "input_slot": "actinv"},
            {"kind": "literal", "value": "--spec"},
            {"kind": "input_path", "input_slot": "spec"},
            {"kind": "literal", "value": "--library"},
            {"kind": "input_path", "input_slot": "activation-library"},
            {"kind": "literal", "value": "--index"},
            {"kind": "input_path", "input_slot": "library-index"},
            {"kind": "literal", "value": "--decay-primary"},
            {"kind": "input_path", "input_slot": "decay-primary"},
            {"kind": "literal", "value": "--decay-fallback"},
            {"kind": "input_path", "input_slot": "decay-fallback"},
            {"kind": "literal", "value": "--out"},
            {"kind": "output_path", "output_id": "result"},
            {"kind": "literal", "value": "--summary"},
            {"kind": "output_path", "output_id": "summary"},
        ],
        "outputs": [{
            "output_id": "result",
            "workspace_path": "result.json",
            "media_type": MEDIA["result"],
        }, {
            "output_id": "summary",
            "workspace_path": "summary.json",
            "media_type": MEDIA["index"],
        }],
        "claims": [{
            "model": "categorical",
            "output_slot": "summary",
            "output_id": "summary",
            "pointer": "/entry_point",
            "allowed_values": ["cli"],
        }],
        "timeout_ms": 3600000,
        "limitations": [
            "The adapter invokes one case solve; spectrum, schedule and "
            "response semantics are the emitted spec's, verified by digest.",
            "A nonzero exit, timeout, missing or malformed result maps to "
            "the failure classes in INTERCHANGE.md; none produce metrics.",
        ],
    }


def _input(study, manifest_dir):
    """Contract inputs + package artifacts for the shared data context."""
    lib_path = study["library"]["path"]
    dec = study.get("decay", {})
    inputs = [
        {"input_id": "actinv-executable",
         "role": {"id": "actinv.executable", "major": 1},
         "media_type": MEDIA["executable"],
         "claim_model": {"model": "unquantified"}},
        {"input_id": "activation-library",
         "role": {"id": "actinv.activation-library", "major": 1},
         "media_type": MEDIA["library"],
         "claim_model": {"model": "unquantified"}},
        {"input_id": "library-index",
         "role": {"id": "actinv.activation-index", "major": 1},
         "media_type": MEDIA["index"],
         "claim_model": {"model": "unquantified"}},
    ]
    # source roots name the directory trees the operator maps at run time;
    # artifact paths are basenames inside them
    lib_root = "actinv-lib"
    artifacts = [
        {"artifact_id": "activation-library",
         "evidence_ids": ["input:activation-library"],
         "source_root": lib_root,
         "path": os.path.basename(lib_path),
         "_abs": lib_path},
        {"artifact_id": "library-index",
         "evidence_ids": ["input:library-index"],
         "source_root": lib_root,
         "path": os.path.basename(
             lib_path.removesuffix(".npz")) + "_index.json",
         "_abs": lib_path.removesuffix(".npz") + "_index.json"},
    ]
    for slot, key in (("decay-primary", "primary"),
                      ("decay-fallback", "fallback")):
        p = dec.get(key)
        if p:
            inputs.append({
                "input_id": slot,
                "role": {"id": "actinv.decay-data", "major": 1},
                "media_type": MEDIA["decay"],
                "claim_model": {"model": "unquantified"}})
            artifacts.append({
                "artifact_id": slot,
                "evidence_ids": [f"input:{slot}"],
                "source_root": f"actinv-{slot}",
                "path": os.path.basename(p),
                "_abs": p})
    return inputs, artifacts


def emit_package(study, manifest_dir, outdir, actinv_path,
                 runner_path=None, python_path=None):
    """Write a Core case package for the study's built manifest.

    `manifest_dir` is the `study build` outdir (manifest.json + specs/).
    `actinv_path` is the executable the step invokes, bound by digest;
    `runner_path` is the staged step runner script; `python_path` is the
    python3 interpreter the capability binds.
    Returns the package manifest digest.
    """
    manifest = json.load(open(os.path.join(manifest_dir, "manifest.json")))
    inputs, artifacts = _input(study, manifest_dir)
    runner_path = runner_path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "run_case.py")
    python_path = python_path or sys.executable
    inputs.append({
        "input_id": "step-runner",
        "role": {"id": "actinv.step-runner", "major": 1},
        "media_type": "text/x-python",
        "claim_model": {"model": "unquantified"}})
    artifacts.append({
        "artifact_id": "step-runner",
        "evidence_ids": ["input:step-runner"],
        "source_root": "actinv-src",
        "path": os.path.basename(runner_path),
        "_abs": runner_path})

    # per-case spec inputs/artifacts/steps
    workflow = []
    executions = []
    requirements = []
    expected_summaries = {}
    for cent in manifest["cases"]:
        cid = cent["case_id"]
        spec_rel = cent["spec"]
        spec_abs = os.path.join(manifest_dir, spec_rel)
        inp = f"spec-{cid}"
        inputs.append({
            "input_id": inp,
            "role": {"id": "actinv.problem", "major": 1},
            "media_type": MEDIA["spec"],
            "claim_model": {"model": "unquantified"}})
        artifacts.append({
            "artifact_id": inp,
            "evidence_ids": [f"input:{inp}"],
            "source_root": "actinv-build",
            "path": spec_rel,
            "_abs": spec_abs})
        data_bindings = [
            ("activation-library", ".data/library.npz"),
            ("library-index", ".data/library_index.json"),
            ("decay-primary", ".data/decay_primary.dat"),
            ("decay-fallback", ".data/decay_fallback.dat"),
        ]
        step_inputs = [
            {"input_slot": "spec",
             "source": {"source": "contract_input", "input_id": inp}},
            {"input_slot": "actinv",
             "source": {"source": "contract_input",
                        "input_id": "actinv-executable"}},
            {"input_slot": "runner",
             "source": {"source": "contract_input",
                        "input_id": "step-runner"}},
        ] + [
            {"input_slot": slot,
             "source": {"source": "contract_input", "input_id": slot}}
            for slot, _ in data_bindings
        ]
        workflow.append({
            "step_id": cid,
            "capability_type": {"id": "actinv.run", "major": 1},
            "bindings": step_inputs})
        executions.append({
            "step_id": cid,
            "adapter": ADAPTER_ID,
            "capability_id": "python3",
            "inputs": [
                {"input_slot": "spec", "workspace_path": spec_rel},
                {"input_slot": "actinv", "workspace_path": "tools/actinv"},
                {"input_slot": "runner",
                 "workspace_path": "tools/run_case.py"},
            ] + [
                {"input_slot": slot, "workspace_path": wpath}
                for slot, wpath in data_bindings
            ],
            "outputs": [
                {"output_slot": "summary", "claim_id": f"{cid}-summary"},
            ],
        })
        # the study run already produced the deterministic result bytes;
        # the expected summary identity is declared so a divergent staged
        # run fails to bind its claim (the raw result carries binary floats
        # and binds evidence only through its receipt entry)
        out_abs = os.path.join(manifest_dir, "cases", cid, "out.json")
        if os.path.isfile(out_abs):
            out_doc = json.load(open(out_abs))
            expected_summary = {
                "case_id": cid,
                "entry_point": "cli",
                "n_steps": len(out_doc.get("steps", [])),
            }
            sum_rel = f"cases/{cid}/summary.json"
            sum_abs = os.path.join(manifest_dir, sum_rel)
            sum_sha = _write_json(sum_abs, expected_summary)
            expected_summaries[cid] = sum_sha
            artifacts.append({
                "artifact_id": f"{cid}-summary",
                "evidence_ids": [f"{cid}-summary"],
                "source_root": "actinv-build",
                "path": sum_rel,
                "_abs": sum_abs})
        requirements.append({
            "requirement_id": f"{cid}-executed",
            "statement": f"Case {cid} produced its declared result artifact "
                         "stamped with the cli entry point.",
            "purpose": {"id": "avila-labs.core.internal-integration",
                        "major": 1},
            "metric": {"source": "step_output", "step_id": cid,
                       "output_slot": "summary"},
            "predicate": {"operator": "equals", "value": "cli"},
        })

    contract = {
        "schema_version": CONTRACT_SCHEMA,
        "semantic_profile": SEMANTIC_PROFILE,
        "contract_id": f"{study['study_id']}.actinv-study",
        "revision": 1,
        "status": "draft",
        "question": (f"Does every case of study '{study['study_id']}' execute "
                     "through the bound actinv capability and produce its "
                     "declared result artifact?"),
        "assumptions": [
            "Cases are executed by the digest-bound actinv executable; Core "
            "verifies identities and bytes, not solver correctness.",
            "Comparative ACT-COMPARE-01 rules are evaluated ACTINV-side; "
            "this contract asserts per-case execution only.",
            "Outputs are unqualified: internal integration evidence, not "
            "regulatory or operational use.",
        ],
        "execution_policy": {},
        "inputs": inputs,
        "workflow": workflow,
        "requirements": [],
        "categorical_requirements": requirements,
    }

    registry = registry_snapshot(study["study_id"])
    adapter_doc = external_checker_adapter()
    py_sha = sh256_file(python_path)
    claims_doc = {
        "schema_version": CLAIMS_SCHEMA,
        "semantic_profile": SEMANTIC_PROFILE,
        "claims": [{
            "claim_id": f"{c['case_id']}-summary",
            "step_id": c["case_id"],
            "output_slot": "summary",
            "artifact": {"media_type": MEDIA["index"],
                         "sha256": sha(expected_summaries[c["case_id"]])},
            "claim": {"model": "unquantified", "value": "cli"},
            "producer": {"package_id": "org.python/cpython@3",
                         "sha256": sha(py_sha)},
        } for c in manifest["cases"]],
        "inputs": [],
        "compiled_snapshot_sha256": "sha256:" + "0" * 64,
    }

    actinv_sha = sh256_file(actinv_path)
    documents = []
    for doc_id, role, name, obj in [
        (f"{study['study_id']}-contract", "contract", "contract.json", contract),
        (f"{study['study_id']}-registry", "registry", "registry.json", registry),
        (f"{study['study_id']}-claims", "claims", "claims.json", claims_doc),
        (ADAPTER_ID, "external_checker_adapter", "adapter.json", adapter_doc),
    ]:
        digest = _write_json(os.path.join(outdir, name), obj)
        documents.append({
            "document_id": doc_id,
            "role": role,
            "path": name,
            "sha256": sha(digest)})

    pkg_artifacts = []
    for a in artifacts:
        if not os.path.isfile(a["_abs"]):
            raise SystemExit(
                f"artifact missing at emit time: {a['_abs']}")
        pkg_artifacts.append({
            "artifact_id": a["artifact_id"],
            "evidence_ids": a["evidence_ids"],
            "source_root": a["source_root"],
            "path": a["path"],
            "sha256": sha(sh256_file(a["_abs"]))})
    pkg_artifacts.append({
        "artifact_id": "actinv-executable",
        "evidence_ids": ["input:actinv-executable"],
        "source_root": "actinv-release",
        "path": "actinv",
        "sha256": sha(actinv_sha)})

    package = {
        "schema_version": PACKAGE_SCHEMA,
        "case_id": study["study_id"],
        "title": f"ACTINV study {study['study_id']} -> Avila Core",
        "documents": documents,
        "artifacts": pkg_artifacts,
        "capabilities": [{
            "capability_id": "python3",
            "package_id": "org.python/cpython@3",
            "executable_sha256": sha(sh256_file(python_path)),
        }],
        "executions": executions,
    }
    pkg_sha = _write_json(os.path.join(outdir, "package.json"), package)
    return pkg_sha


def finalize_claims(pkg_dir, generated_claims_path):
    """Replace the package's committed claims with the claims a Core run
    generated, then re-write package.json so its document digests bind the
    canonical committed state. Returns the new package digest."""
    pkg = json.load(open(os.path.join(pkg_dir, "package.json")))
    gen = json.load(open(generated_claims_path))
    if gen.get("schema_version") != CLAIMS_SCHEMA:
        raise SystemExit("generated claims schema mismatch")
    _write_json(os.path.join(pkg_dir, "claims.json"), gen)
    for d in pkg["documents"]:
        p = os.path.join(pkg_dir, d["path"])
        d["sha256"] = sha(sh256_file(p))
    pkg_sha = _write_json(os.path.join(pkg_dir, "package.json"), pkg)
    return pkg_sha


def classify_failure(error_text, exit_code=None, timed_out=False):
    """Map an observed process/error condition to a failure class and the
    study-record case status it produces."""
    if timed_out:
        return "timeout", "failed"
    text = error_text or ""
    if any(m in text for m in CONTRACT_GAP_MARKERS):
        return "contract_gap", "contract_gap"
    if exit_code is not None and exit_code != 0:
        return "nonzero_exit", "failed"
    return "process_error", "failed"


def interpret_receipt(receipt, expected_spec_sha256=None,
                      expected_binary_sha256=None):
    """Translate an execution-receipt into a study-record case-row update.

    Returns (status, evidence, failures). `failures` lists binding violations
    — a receipt that mis-binds the spec or executable is evidence of nothing.
    """
    failures = []
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        failures.append("receipt schema_version mismatch")
    if receipt.get("status") != "completed":
        failures.append(f"receipt status {receipt.get('status')}")
    inputs = receipt.get("inputs", [])
    spec_in = next((i for i in inputs if i.get("input_slot") == "spec"), None)
    if expected_spec_sha256:
        got = (spec_in or {}).get("sha256", "")
        if got != sha(expected_spec_sha256.removeprefix("sha256:")):
            failures.append("spec input digest != manifest digest")
    if expected_binary_sha256:
        actinv_in = next(
            (i for i in inputs if i.get("input_slot") == "actinv"), None)
        got = (actinv_in or {}).get("sha256", "")
        if got != sha(expected_binary_sha256.removeprefix("sha256:")):
            failures.append("actinv executable digest mismatch")
    result_out = next(
        (o for o in receipt.get("outputs", [])
         if o.get("output_id") == "result" or o.get("output_slot") == "result"),
        None)
    if not result_out or result_out.get("state") != "collected":
        failures.append("result output missing or not collected")
    if failures:
        return "failed", {}, failures
    out_sha = result_out["sha256"].removeprefix("sha256:")
    return "executed", {
        "evidence_kind": "verified",
        "out_sha256": out_sha,
        "receipt_adapter": receipt.get("adapter"),
        "receipt_step_id": receipt.get("step_id"),
    }, []


def main(argv):
    if len(argv) >= 2 and argv[1] == "finalize-claims":
        if len(argv) != 4:
            print("usage: actinv_core_adapter.py finalize-claims "
                  "PKG_DIR GENERATED_CLAIMS.json", file=sys.stderr)
            return 2
        digest = finalize_claims(argv[2], argv[3])
        print(f"ok: package.json sha256:{digest}")
        return 0
    if len(argv) != 6 or argv[1] != "emit-package":
        print("usage: actinv_core_adapter.py emit-package STUDY.json "
              "MANIFEST_DIR OUTDIR ACTINV_BIN\n"
              "       actinv_core_adapter.py finalize-claims PKG_DIR "
              "GENERATED_CLAIMS.json", file=sys.stderr)
        return 2
    _, _, study_path, manifest_dir, outdir, actinv = argv
    study = json.load(open(study_path))
    if study.get("study") != "actinv-study-1":
        print("study schema not actinv-study-1", file=sys.stderr)
        return 2
    digest = emit_package(study, manifest_dir, outdir, actinv)
    print(f"ok: package.json sha256:{digest} -> {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
