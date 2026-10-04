#!/usr/bin/env python3
"""Derive P110 from sealed controls, source-bound quality and exact-commit CI."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_SHA256 = "8ad8870d42d30f3f8c330021b15e7f4f570032deba1ee5b79f57b9411e40e7f9"
AMENDMENT_SHA256 = "0778cb569a3ce347fc271a182477fe05b66955ffdc37571a271aba64dbb4bf3c"
P109_VERDICT_SHA256 = "b6d370e3e560d0d7f780b0a7bb6344413fee87eb69eab865ac9e5ac58b449398"
REPAIR_SHA256 = "a240a0e1b7e194f8d548beb7d7a7f694e6089a9e53a052659a03f77374b6aa10"
REPAIR_EVIDENCE = {
    "results/p110_control_failure.log":"70fa3b77bc9fa70fc4859b2babee1cd94d5e339e3e17533945e016592c768139",
    "results/g0_p110_before_amendment.json":"8b8796f2724ba6f6c4c8dcadda41c1f0fc6171165b6f963b35ebfd60d9276dc2",
    "results/p110_oracle_before_amendment.py":"36b442caa1cd19c8eec14797f9b117d091bfb123fb7786dd7932145cd7a1f12c",
    "results/p110_initial_oracle_diagnostic.log":"d982f22213590625260244389211db73157525701551a26d33d7236089e6a03f",
    "results/p110_child_invocation_failure.log":"428c56482e8a59a74d33dacbd734a3c7444f373a44c2e5151ab294136b279466",
}
VERDICT = ROOT / "results/p110_verdict.json"
CI = ROOT / "results/p110_ci_runs.json"
IMPLEMENTATION = ROOT / "results/p110_implementation_commit.json"
ARTIFACTS = {name: ROOT / f"results/{name}" for name in (
    "g0_p110_composition_solve.json", "g1_p110_composition_solve.json", "g2_p110_composition_solve.json", "g3_p110_quality.json")}
REQUIRED_GATES = {
    "native_oracle_regressions", "seal_identity_regressions", "historical_source_regressions",
    "historical_p107_replay", "historical_p108_replay", "control_child_lifecycle",
    "full_control_read_only_replay", "g1_read_only_replay", "p105_scientific_replay",
    "p107_scientific_replay", "p108_scientific_replay", "handbook_build",
    "handbook_chromium", "handbook_links", "rust_check_all_targets_features",
    "rust_clippy_all_targets_features", "rust_fmt", "rust_test_all_targets_features",
    "historical_p109_fail_replay",
}
REQUIRED_WORKFLOWS = {"controls", "desktop builds", "Build browser workbench",
                      "Build handbook", "fusion-isotope", "fns-iron"}
RESOURCES = {"memory_max_bytes":6442450944,"memory_swap_max_bytes":0,"tasks_max":128,
             "cpu_quota_percent":200,"disk_tmpdir":"target/preflight-tmp","coordinator_jobs":"serial"}
REQUIRED_SOURCES = {"crates/actinv-core/src/run.rs",
                    "crates/actinv-cli/src/waste_composition_solve.rs",
                    "crates/actinv-cli/src/waste_composition_verify.rs",
                    "crates/actinv-cli/src/waste_composition.rs",
                    "crates/actinv-cli/src/waste_bounds.rs",
                    "crates/actinv-cli/src/lib.rs",
                    "crates/actinv-cli/src/command.rs"}


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def read(path: Path):
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        return None
    return value


def _quality(quality: dict, record: dict | None) -> tuple[bool,dict]:
    from check_p107_history import _materialize_history, _git_blob, _safe_rust_path
    gates=quality.get("gates",{})
    gates_ok=(isinstance(gates,dict) and REQUIRED_GATES.issubset(gates)
              and all(isinstance(v,dict) and type(v.get("exit_code")) is int
                      and v["exit_code"]==0 for v in gates.values()))
    resources_ok=quality.get("resource_limits")==RESOURCES
    tests=quality.get("workspace_tests",{})
    tests_ok=(isinstance(tests,dict) and type(tests.get("failed")) is int and tests["failed"]==0
              and type(tests.get("passed")) is int and tests["passed"]>0)
    hashes=quality.get("production_rust_sha256",{})
    source_ok=False
    historical=False
    try:
        if not isinstance(hashes,dict) or not REQUIRED_SOURCES.issubset(hashes):
            raise ValueError("missing source hashes")
        for path,digest in hashes.items():
            _safe_rust_path(path)
            if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest):
                raise ValueError("malformed source hash")
        if record is not None:
            commit=record["commit_sha"]
            if not isinstance(commit,str) or not re.fullmatch(r"[0-9a-f]{40}",commit):
                raise ValueError("malformed implementation commit")
            # Bind the quality artifact to the implementation tree as well as
            # its separately retained record. Future source edits stay valid.
            committed_quality=_git_blob(commit,"results/g3_p110_quality.json")
            if hashlib.sha256(committed_quality).hexdigest()!=sha(ARTIFACTS["g3_p110_quality.json"]):
                raise ValueError("quality artifact differs from implementation tree")
            committed_g0=_git_blob(commit,"results/g0_p110_composition_solve.json")
            if hashlib.sha256(committed_g0).hexdigest()!=sha(ARTIFACTS["g0_p110_composition_solve.json"]):
                raise ValueError("G0 artifact differs from implementation tree")
            temporary=ROOT / "target/p110-verdict-history"
            temporary.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="p110-",dir=temporary) as temp:
                _materialize_history(commit,hashes,Path(temp))
            source_ok=True
            historical=True
        else:
            source_ok=all(sha(ROOT/path)==digest for path,digest in hashes.items())
    except (OSError,ValueError,RuntimeError,KeyError) as error:
        detail={"error":str(error)}
    else:
        detail={}
    detail.update({"required_gate_names_present":gates_ok,"resource_limits_match":resources_ok,
                   "workspace_tests_reported_green":tests_ok,"source_hashes_match":source_ok,
                   "historical_source_verification":historical})
    passed=(quality.get("schema")=="actinv-p110-quality-1" and quality.get("phase")=="P110"
            and quality.get("pass") is True and gates_ok and resources_ok and tests_ok and source_ok)
    return passed,detail


def derive() -> dict:
    artifacts={name:read(path) for name,path in ARTIFACTS.items()}
    g0,g1,g2,g3=(artifacts[name] if isinstance(artifacts[name],dict) else {} for name in ARTIFACTS)
    record=read(IMPLEMENTATION)
    if not isinstance(record,dict):
        record=None
    checks={}
    amendment=ROOT / "protocols/ACTINV-P109_AMEND-A.md"
    registry=(ROOT / "protocols/protocol_hash.txt").read_text().splitlines()
    checks["predecessor_amendment"]=(sha(amendment)==AMENDMENT_SHA256
                         and f"{AMENDMENT_SHA256}  protocols/ACTINV-P109_AMEND-A.md" in registry
                         and sha(ROOT/"results/p109_format_failure.log")=="0a9ee2fb99edbbcd617c3db2763bf981948c4fc48d20f76f4c02052f12b00c10")
    checks["repair_amendment"]=(sha(ROOT/"protocols/ACTINV-P110_AMEND-A.md")==REPAIR_SHA256
                         and f"{REPAIR_SHA256}  protocols/ACTINV-P110_AMEND-A.md" in registry
                         and all(sha(ROOT/path)==digest for path,digest in REPAIR_EVIDENCE.items()))
    checks["G0"]=(g0.get("schema")=="actinv-p110-composition-solve-g0-1" and g0.get("phase")=="P110"
                  and g0.get("pass") is True and g0.get("protocol_sha256")==PROTOCOL_SHA256
                  and g0.get("protocol_registered") is True and g0.get("pack_mirror_identical") is True
                  and g0.get("pack_sha256")=="890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7"
                  and g0.get("case_fixture_matches") is True
                  and g0.get("abundance_mass_pinned") is True
                  and g0.get("synthetic_artifact_review",{}).get("pass") is True
                  and g0.get("expected_labels",{}).get("pass") is True
                  and g0.get("historical_p108_verified") is True
                  and g0.get("historical_p109_verified") is True
                  and g0.get("predecessor_p109_fail_sha256")==P109_VERDICT_SHA256
                  and g0.get("repair_amendment_sha256")==REPAIR_SHA256
                  and g0.get("repair_amendment_registered") is True and g0.get("repair_rounds")==1
                  and g0.get("repair_evidence_sha256")==REPAIR_EVIDENCE
                  and g0.get("repair_evidence_matches") is True
                  and g0.get("case_count")==9 and g0.get("target_count")==18 and g0.get("endpoint_count")==36)
    mutations=g1.get("mutations_rejected",{})
    refusals=g1.get("refusal_controls",{})
    case_evidence=g1.get("case_evidence",[])
    evidence_ok=(isinstance(case_evidence,list) and len(case_evidence)==9
                 and all(isinstance(case,dict) and isinstance(case.get("id"),str)
                         and all(isinstance(case.get(field),str)
                                 and re.fullmatch(r"[0-9a-f]{64}",case[field])
                                 for field in ("input_sha256","output_sha256","generated_response_input_sha256"))
                         for case in case_evidence)
                 and len({case["id"] for case in case_evidence})==9)
    checks["G1"]=(g1.get("schema")=="actinv-p110-composition-solve-g1-1"
                  and g1.get("pass") is True and g1.get("protocol_sha256")==PROTOCOL_SHA256
                  and g1.get("case_count")==9 and g1.get("target_count")==18 and g1.get("endpoint_count")==36
                  and g1.get("independent_comparison_count")==9
                  and g1.get("repeat_byte_identical") is True and g1.get("failures")==[]
                  and evidence_ok
                  and isinstance(mutations,dict) and len(mutations)>=20 and all(v is True for v in mutations.values())
                  and isinstance(refusals,dict) and refusals.get("pass") is True
                  and isinstance(refusals.get("checks"),dict) and len(refusals["checks"])>=30
                  and all(v is True for v in refusals["checks"].values()))
    checks["G2"]=(g2.get("schema")=="actinv-p110-composition-solve-g2-1" and g2.get("phase")=="P110"
                  and g2.get("pass") is True and g2.get("protocol_sha256")==PROTOCOL_SHA256
                  and g2.get("g1_result_sha256")==sha(ARTIFACTS["g1_p110_composition_solve.json"])
                  and g2.get("separate_output_paths_byte_identical") is True)
    checks["G3_local"],quality_detail=_quality(g3,record)
    predecessor=read(ROOT/"results/p108_verdict.json")
    checks["P108"]=(isinstance(predecessor,dict) and predecessor.get("verdict")=="P108-PASS"
                    and g0.get("historical_p108_verified") is True)
    failed_predecessor=read(ROOT/"results/p109_verdict.json")
    checks["P109_preserved_FAIL"]=(isinstance(failed_predecessor,dict)
                    and failed_predecessor.get("verdict")=="P109-FAIL"
                    and sha(ROOT/"results/p109_verdict.json")==P109_VERDICT_SHA256
                    and not (ROOT/"results/g2_p109_composition_solve.json").exists()
                    and g0.get("historical_p109_verified") is True)
    ci_runs=read(CI)
    ci_pass=False
    if CI.exists():
        record_ok=(record is not None and record.get("schema")=="actinv-p110-implementation-1"
                   and record.get("g0_sha256")==sha(ARTIFACTS["g0_p110_composition_solve.json"])
                   and record.get("g3_sha256")==sha(ARTIFACTS["g3_p110_quality.json"]))
        if record_ok and isinstance(ci_runs,list) and ci_runs:
            names={r.get("workflowName") for r in ci_runs if isinstance(r,dict)}
            ci_pass=(REQUIRED_WORKFLOWS.issubset(names) and all(
                isinstance(r,dict) and r.get("headSha")==record["commit_sha"]
                and r.get("status")=="completed" and r.get("conclusion")=="success" for r in ci_runs))
    local=all(checks.values())
    verdict="P110-PASS" if local and ci_pass else "P110-LOCAL-PASS" if local and not CI.exists() else "P110-FAIL"
    return {"schema":"actinv-p110-verdict-1","phase":"P110","repair_rounds":1,"verdict":verdict,
            "gates":{**{k:"PASS" if v else "FAIL" for k,v in checks.items()},
                     "G3_CI":"PASS" if ci_pass else "FAIL" if CI.exists() else "PENDING"},
            "quality_checks":quality_detail,
            "evidence_sha256":{name:sha(path) for name,path in ARTIFACTS.items()},
            "ci_evidence_sha256":sha(CI),"implementation_record_sha256":sha(IMPLEMENTATION),
            "implementation_commit":record.get("commit_sha") if record else None,
            "qualification":"Conservative class supersets for a generated fixed-rate coupled/reach affine composition basis, with full native selected-extremum witness verification and explicit modeled-inventory coverage downgrades; no global numerical solver/model-error bound, physical completeness, statistical uncertainty or disposal-acceptance conclusion."}


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--write",action="store_true")
    args=parser.parse_args()
    result=derive()
    if args.write:
        VERDICT.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        same=True
    else:
        same=read(VERDICT)==result
    print(json.dumps({"verdict":result["verdict"],"gates":result["gates"],"persisted_matches":same},indent=2,sort_keys=True))
    return 0 if same and result["verdict"] in ("P110-LOCAL-PASS","P110-PASS") else 1


if __name__=="__main__":
    raise SystemExit(main())
