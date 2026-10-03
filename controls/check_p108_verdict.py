#!/usr/bin/env python3
"""Derive P108 from sealed controls, source-bound quality and exact-commit CI."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_SHA256 = "ae503d3fe2ff9569c793f162d3b9c19975815f80a53d20458513ee8b9d267fdd"
AMENDMENT_SHA256 = "ce4fa1fa0abff041ee57349be7ca6009ed864a3368edeeb21089354b48f33d9f"
VERDICT = ROOT / "results/p108_verdict.json"
CI = ROOT / "results/p108_ci_runs.json"
IMPLEMENTATION = ROOT / "results/p108_implementation_commit.json"
ARTIFACTS = {name: ROOT / f"results/{name}" for name in (
    "g0_p108_composition.json", "g1_p108_composition.json", "g2_p108_composition.json", "g3_p108_quality.json")}
REQUIRED_GATES = {
    "composition_oracle_regressions", "seal_identity_regressions", "historical_p107_regressions",
    "historical_p107_replay", "control_child_lifecycle", "full_control_read_only_replay",
    "g1_read_only_replay", "p105_scientific_replay", "p107_scientific_replay",
    "handbook_build", "handbook_chromium", "handbook_links", "rust_check_all_targets_features",
    "rust_clippy_all_targets_features", "rust_fmt", "rust_test_all_targets_features",
}
REQUIRED_WORKFLOWS = {"controls", "desktop builds", "Build browser workbench", "Build handbook"}
RESOURCES = {"memory_max_bytes":6442450944,"memory_swap_max_bytes":0,"tasks_max":128,
             "cpu_quota_percent":200,"disk_tmpdir":"target/preflight-tmp","coordinator_jobs":"serial"}
REQUIRED_SOURCES = {"crates/actinv-core/src/waste_composition.rs",
                    "crates/actinv-core/src/lib.rs","crates/actinv-cli/src/waste_composition.rs",
                    "crates/actinv-cli/src/lib.rs","crates/actinv-cli/src/command.rs",
                    "crates/actinv-cli/src/waste_bounds.rs"}


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
            committed_quality=_git_blob(commit,"results/g3_p108_quality.json")
            if hashlib.sha256(committed_quality).hexdigest()!=sha(ARTIFACTS["g3_p108_quality.json"]):
                raise ValueError("quality artifact differs from implementation tree")
            committed_g0=_git_blob(commit,"results/g0_p108_composition.json")
            if hashlib.sha256(committed_g0).hexdigest()!=sha(ARTIFACTS["g0_p108_composition.json"]):
                raise ValueError("G0 artifact differs from implementation tree")
            temporary=ROOT / "target/p108-verdict-history"
            temporary.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="p108-",dir=temporary) as temp:
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
    passed=(quality.get("schema")=="actinv-p108-quality-1" and quality.get("phase")=="P108"
            and quality.get("pass") is True and gates_ok and resources_ok and tests_ok and source_ok)
    return passed,detail


def derive() -> dict:
    artifacts={name:read(path) for name,path in ARTIFACTS.items()}
    g0,g1,g2,g3=(artifacts[name] if isinstance(artifacts[name],dict) else {} for name in ARTIFACTS)
    record=read(IMPLEMENTATION)
    if not isinstance(record,dict):
        record=None
    checks={}
    checks["G0"]=(g0.get("schema")=="actinv-p108-composition-g0-1" and g0.get("phase")=="P108"
                  and g0.get("pass") is True and g0.get("protocol_sha256")==PROTOCOL_SHA256
                  and g0.get("protocol_registered") is True and g0.get("pack_mirror_identical") is True
                  and g0.get("amendment_sha256")==AMENDMENT_SHA256 and g0.get("amendment_registered") is True
                  and g0.get("pack_sha256")=="890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7"
                  and g0.get("case_fixture_matches") is True
                  and g0.get("expected_labels",{}).get("pass") is True
                  and g0.get("historical_p107_verified") is True
                  and g0.get("case_count")==12 and g0.get("target_count")==13 and g0.get("endpoint_count")==26)
    mutations=g1.get("mutations_rejected",{})
    refusals=g1.get("refusal_controls",{})
    checks["G1"]=(g1.get("schema")=="actinv-p108-composition-g1-1"
                  and g1.get("pass") is True and g1.get("protocol_sha256")==PROTOCOL_SHA256
                  and g1.get("case_count")==12 and g1.get("target_count")==13 and g1.get("endpoint_count")==26
                  and g1.get("independent_comparison_count")==12
                  and g1.get("repeat_byte_identical") is True and g1.get("failures")==[]
                  and isinstance(mutations,dict) and bool(mutations) and all(v is True for v in mutations.values())
                  and isinstance(refusals,dict) and refusals.get("pass") is True
                  and isinstance(refusals.get("checks"),dict) and bool(refusals["checks"])
                  and all(v is True for v in refusals["checks"].values()))
    checks["G2"]=(g2.get("schema")=="actinv-p108-composition-g2-1" and g2.get("phase")=="P108"
                  and g2.get("pass") is True and g2.get("protocol_sha256")==PROTOCOL_SHA256
                  and g2.get("g1_result_sha256")==sha(ARTIFACTS["g1_p108_composition.json"])
                  and g2.get("separate_output_paths_byte_identical") is True)
    checks["G3_local"],quality_detail=_quality(g3,record)
    predecessor=read(ROOT/"results/p107_verdict.json")
    checks["P107"]=(isinstance(predecessor,dict) and predecessor.get("verdict")=="P107-PASS"
                    and g0.get("historical_p107_verified") is True)
    ci_runs=read(CI)
    ci_pass=False
    if CI.exists():
        record_ok=(record is not None and record.get("schema")=="actinv-p108-implementation-1"
                   and record.get("g0_sha256")==sha(ARTIFACTS["g0_p108_composition.json"])
                   and record.get("g3_sha256")==sha(ARTIFACTS["g3_p108_quality.json"]))
        if record_ok and isinstance(ci_runs,list) and ci_runs:
            names={r.get("workflowName") for r in ci_runs if isinstance(r,dict)}
            ci_pass=(REQUIRED_WORKFLOWS.issubset(names) and all(
                isinstance(r,dict) and r.get("headSha")==record["commit_sha"]
                and r.get("status")=="completed" and r.get("conclusion")=="success" for r in ci_runs))
    local=all(checks.values())
    verdict="P108-PASS" if local and ci_pass else "P108-LOCAL-PASS" if local and not CI.exists() else "P108-FAIL"
    return {"schema":"actinv-p108-verdict-1","phase":"P108","repair_rounds":1,"verdict":verdict,
            "gates":{**{k:"PASS" if v else "FAIL" for k,v in checks.items()},
                     "G3_CI":"PASS" if ci_pass else "FAIL" if CI.exists() else "PENDING"},
            "quality_checks":quality_detail,
            "evidence_sha256":{name:sha(path) for name,path in ARTIFACTS.items()},
            "ci_evidence_sha256":sha(CI),"implementation_record_sha256":sha(IMPLEMENTATION),
            "implementation_commit":record.get("commit_sha") if record else None,
            "qualification":"Conservative classification supersets for a caller-declared fixed affine activity model over feasible natural-element weight-percent intervals constrained to sum to 100%; no native model generation, physical completeness, statistical uncertainty or disposal-acceptance conclusion."}


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
    return 0 if same and result["verdict"] in ("P108-LOCAL-PASS","P108-PASS") else 1


if __name__=="__main__":
    raise SystemExit(main())
