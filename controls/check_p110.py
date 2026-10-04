#!/usr/bin/env python3
"""Sealed P110 native composition controls. Coordinator-only CLI invocation."""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import struct
import zipfile
from pathlib import Path

import p110_composition_solve_control as oracle
import p108_composition_control as p108
import p110_native_identity as native_identity

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P110_PROTOCOL.md"
PROTOCOL_SHA256 = "8ad8870d42d30f3f8c330021b15e7f4f570032deba1ee5b79f57b9411e40e7f9"
AMENDMENT = ROOT / "protocols/ACTINV-P110_AMEND-A.md"
AMENDMENT_SHA256 = "a240a0e1b7e194f8d548beb7d7a7f694e6089a9e53a052659a03f77374b6aa10"
PRE_REPAIR_ARTIFACTS = {
    "results/p110_control_failure.log": "70fa3b77bc9fa70fc4859b2babee1cd94d5e339e3e17533945e016592c768139",
    "results/g0_p110_before_amendment.json": "8b8796f2724ba6f6c4c8dcadda41c1f0fc6171165b6f963b35ebfd60d9276dc2",
    "results/p110_oracle_before_amendment.py": "36b442caa1cd19c8eec14797f9b117d091bfb123fb7786dd7932145cd7a1f12c",
    "results/p110_initial_oracle_diagnostic.log": "d982f22213590625260244389211db73157525701551a26d33d7236089e6a03f",
    "results/p110_child_invocation_failure.log": "428c56482e8a59a74d33dacbd734a3c7444f373a44c2e5151ab294136b279466",
}
P109_AMENDMENT = ROOT / "protocols/ACTINV-P109_AMEND-A.md"
P109_AMENDMENT_SHA256 = "0778cb569a3ce347fc271a182477fe05b66955ffdc37571a271aba64dbb4bf3c"
P109_FORMAT_FAILURE = ROOT / "results/p109_format_failure.log"
P109_FORMAT_FAILURE_SHA256 = "0a9ee2fb99edbbcd617c3db2763bf981948c4fc48d20f76f4c02052f12b00c10"
P109_VERDICT_SHA256 = "b6d370e3e560d0d7f780b0a7bb6344413fee87eb69eab865ac9e5ac58b449398"
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"
PACK_SHA256 = "890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7"
PACK_MIRROR = ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json"
ABUNDANCE = ROOT / "results/tables/abundance_mass.json"
ABUNDANCE_SHA256 = "285b38a823dcee398a1dcab798c3b5a17d6b7ae5817edc663ed502314367a87b"
FIXTURE = ROOT / "controls/fixtures/p110/cases.json"
G0 = ROOT / "results/g0_p110_composition_solve.json"
G1 = ROOT / "results/g1_p110_composition_solve.json"
G2 = ROOT / "results/g2_p110_composition_solve.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str | None:
    return sha_bytes(path.read_bytes()) if path.is_file() else None


def _registered() -> bool:
    line = f"{PROTOCOL_SHA256}  {PROTOCOL.relative_to(ROOT)}"
    registry = ROOT / "protocols/protocol_hash.txt"
    return sha(PROTOCOL) == PROTOCOL_SHA256 and registry.is_file() and line in registry.read_text().splitlines()


def _amendment_registered() -> bool:
    line=f"{AMENDMENT_SHA256}  {AMENDMENT.relative_to(ROOT)}"
    registry=ROOT/"protocols/protocol_hash.txt"
    return sha(AMENDMENT)==AMENDMENT_SHA256 and registry.is_file() and line in registry.read_text().splitlines()


def _predecessor_amendment_registered() -> bool:
    line=f"{P109_AMENDMENT_SHA256}  {P109_AMENDMENT.relative_to(ROOT)}"
    registry=ROOT/"protocols/protocol_hash.txt"
    return sha(P109_AMENDMENT)==P109_AMENDMENT_SHA256 and registry.is_file() and line in registry.read_text().splitlines()


def _historical_p108() -> bool:
    import check_p108_verdict
    derived = check_p108_verdict.derive()
    try:
        persisted = json.loads((ROOT / "results/p108_verdict.json").read_text())
    except (OSError, ValueError):
        return False
    return derived == persisted and derived.get("verdict") == "P108-PASS"


def _generated_fixture() -> dict:
    generated = oracle.write_fixture(ROOT)
    paths = [Path(ROOT / item) for variant in generated.values() if isinstance(variant, dict)
             for item in variant.get("paths", {}).values()]
    paths.extend(Path(ROOT / item["path"]) for item in generated["base_specs"].values())
    return {"generated": generated,
            "generated_sha256": {str(p.relative_to(ROOT)): sha(p) for p in paths}}


def _read_npy(raw: bytes, expected_descr: str) -> tuple[tuple[int,...],bytes]:
    if raw[:6]!=b"\x93NUMPY": raise ValueError("bad NPY magic")
    major,minor=raw[6],raw[7]
    if (major,minor)!=(1,0): raise ValueError("unsupported NPY version")
    n=struct.unpack("<H",raw[8:10])[0]
    header=ast.literal_eval(raw[10:10+n].decode("latin1").strip())
    if header.get("descr")!=expected_descr or header.get("fortran_order") is not False:
        raise ValueError("NPY dtype/order differs from frozen fixture")
    return tuple(header["shape"]),raw[10+n:]


def _inspect_synthetic_artifacts(generated: dict) -> dict:
    expectations={"complete":(list(range(10)),11,12),
                  "omit_fe58_target":([0,1,2,4,5,6,7,8,9],10,12),
                  "omit_nb94_decay":(list(range(10)),11,11)}
    variants={}; failures=[]
    mat_by_index=[2601,2602,2603,2604,1401,1402,1403,4101,4102,4201]
    za_by_index=[26054,26056,26057,26058,14028,14029,14030,41093,41094,42094]
    for name,(indices,row_count,decay_count) in expectations.items():
        data=generated[name]; paths=data["paths"]
        library_path=Path(paths["library"])
        library_stem=library_path.with_suffix("")
        expected_index=library_stem.with_name(library_stem.name+"_index.json")
        if Path(paths["index"])!=expected_index or not (ROOT/expected_index).is_file():
            failures.append(f"{name}: native index filename does not follow stem-based resolver")
        if sha(ROOT/expected_index)!=data["sha256"]["index"]:
            failures.append(f"{name}: native index file SHA differs from generated bytes")
        index=json.loads((ROOT/paths["index"]).read_text())
        targets=index.get("targets",[])
        expected_ids=[mat_by_index[i] for i in indices]
        if [t.get("mat") for t in targets]!=expected_ids: failures.append(f"{name}: target identities")
        if [(t.get("za"),t.get("liso")) for t in targets]!=[(za_by_index[i],0) for i in indices]:
            failures.append(f"{name}: target ZA/LISO identities")
        if index.get("sha256_npz")!=sha(ROOT/paths["library"]):
            failures.append(f"{name}: index library SHA")
        boundary_sha=sha_bytes(b"ACTINV-GROUP-BOUNDARIES-v1\0"+struct.pack("<2d",1.0,2.0))
        if index.get("group_boundary_sha256")!=boundary_sha:
            failures.append(f"{name}: index group-boundary SHA")
        if data.get("target_count")!=len(indices) or data.get("row_count")!=row_count or data.get("decay_record_count")!=decay_count:
            failures.append(f"{name}: generator counts")
        with zipfile.ZipFile(ROOT/paths["library"],"r") as archive:
            row_shape,row_payload=_read_npy(archive.read("rows.npy"),"<i8")
            sig_shape,sig_payload=_read_npy(archive.read("sig.npy"),"<f8")
            bound_shape,bound_payload=_read_npy(archive.read("bounds.npy"),"<f8")
        rows=[]; sig=[]
        for pos in range(row_shape[0]): rows.append(struct.unpack_from("<5q",row_payload,pos*40))
        sig=[struct.unpack_from("<d",sig_payload,pos*8)[0] for pos in range(sig_shape[0])]
        expected_rows=[]; expected_sig=[]
        for pos,idx in enumerate(indices):
            if mat_by_index[idx]==4101:
                expected_rows.extend(((pos,102,-1,-1,0),(pos,102,41094,0,3))); expected_sig.extend((1.0,1.0))
            else: expected_rows.append((pos,102,-1,-1,0)); expected_sig.append(0.0)
        if row_shape!=(row_count,5) or rows!=expected_rows or sig!=expected_sig:
            failures.append(f"{name}: synthetic one-group rows/signatures")
        if sig_shape!=(row_count,1) or bound_shape!=(2,) or struct.unpack("<2d",bound_payload)!=(1.0,2.0):
            failures.append(f"{name}: group boundaries or cross-section array shape")
        lines=(ROOT/paths["decay"]).read_text().splitlines()
        sections=[]
        for line in lines:
            if len(line)>=80:
                try: mat,mf,mt,seq=int(line[66:70]),int(line[70:72]),int(line[72:75]),int(line[75:80])
                except ValueError: continue
                if mf==8 and mt==457 and seq==1: sections.append(mat)
        expected_decay=[2601,2602,2603,2604,1401,1402,1403,4101,4102,4201,1001,2001]
        if name=="omit_nb94_decay": expected_decay.remove(4102)
        if sections!=expected_decay or len(sections)!=decay_count:
            failures.append(f"{name}: primary decay identities")
        decay_errors=native_identity.inspect_decay_bytes(
            (ROOT/paths["decay"]).read_bytes(), omit_nb94=name=="omit_nb94_decay")
        failures.extend(f"{name}: {reason}" for reason in decay_errors)
        variants[name]={"target_mats":expected_ids,"row_count":len(rows),"decay_sections":sections,
                        "library_sha256":data["sha256"]["library"],"index_sha256":data["sha256"]["index"],
                        "decay_sha256":data["sha256"]["decay"]}
    return {"pass":not failures,"variants":variants,"failures":failures}


def _g0_base() -> dict:
    cases = oracle.generate_cases()
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data = _generated_fixture()
    fixture_exact=_fixture_contract(cases,fixture,data["generated"])
    artifact_review=_inspect_synthetic_artifacts(data["generated"])
    labels = _expected_labels(cases)
    pack_ok = (sha(PACK) == PACK_SHA256 and sha(PACK_MIRROR) == PACK_SHA256
               and PACK.read_bytes() == PACK_MIRROR.read_bytes())
    historical = _historical_p108()
    import check_p109_history
    historical_p109=check_p109_history.verify()
    frozen = {"controls/check_p110.py", "controls/p110_composition_solve_control.py",
              "controls/p110_native_identity.py",
              "controls/test_p110_native_identity.py",
              "controls/test_p110_seal.py", "controls/check_p110_verdict.py",
              "controls/check_p109_history.py", "controls/test_p109_history.py",
              "controls/check_p109.py", "controls/p109_composition_solve_control.py",
              "controls/p109_native_identity.py", "controls/test_p109_seal.py",
              "controls/test_p109_native_identity.py", "controls/check_p109_verdict.py",
              "controls/check_p108_verdict.py", "controls/check_p108.py",
              "controls/p108_composition_control.py", "controls/p106_bounds_control.py",
              "controls/check_p107.py", "controls/p105_budget_control.py", "controls/check_p105.py",
              "controls/fixtures/p110/cases.json", "results/tables/abundance_mass.json"}
    hashes = {name: sha(ROOT / name) for name in sorted(frozen)}
    preserved_pre_repair={name:sha(ROOT/name) for name in PRE_REPAIR_ARTIFACTS}
    pre_repair_matches=all(preserved_pre_repair[name]==want
                           for name,want in PRE_REPAIR_ARTIFACTS.items())
    wanted_ids={"ranged_volume_1","high_fixed_volume_7","ranged_volume_point_1",
                "low_fixed_external_h3","required_external_h3","caller_incomplete",
                "missing_fe58_target","missing_nb94_decay","zero_response_reference"}
    fixture_ok=(fixture.get("schema")==oracle.CASE_SCHEMA and len(cases)==9
                and {case.get("id") for case in cases}==wanted_ids
                and all(len(case.get("targets",[]))==2 for case in cases))
    p109_amendment_registered=_predecessor_amendment_registered()
    report = {"schema":"actinv-p110-composition-solve-g0-1","phase":"P110",
              "repair_rounds":1,
              "protocol_sha256":sha(PROTOCOL),"protocol_registered":_registered(),
              "repair_amendment_sha256":sha(AMENDMENT),
              "repair_amendment_registered":_amendment_registered(),
              "repair_evidence_sha256":preserved_pre_repair,
              "repair_evidence_matches":pre_repair_matches,
              "p109_amendment_sha256":sha(P109_AMENDMENT),
              "p109_amendment_registered":p109_amendment_registered,
              "p109_format_failure_log_sha256":sha(P109_FORMAT_FAILURE),
              "p109_format_failure_log_matches":sha(P109_FORMAT_FAILURE)==P109_FORMAT_FAILURE_SHA256,
              "predecessor_p109_fail_sha256":P109_VERDICT_SHA256,
              "historical_p109_verified":(historical_p109.get("pass") is True
                  and historical_p109.get("checkpoint_commit")=="495d6bf99248d3b99af43ca82894631c3e6b8c87"
                  and historical_p109.get("derived_verdict_matches_persisted") is True
                  and historical_p109.get("derived_verdict")=="P109-FAIL"
                  and historical_p109.get("g2_absent") is True
                  and historical_p109.get("historical_source_verification") is False),
              "historical_p109_evidence":historical_p109,
              "pack_sha256":sha(PACK),"pack_mirror_identical":pack_ok,
              "abundance_mass_sha256":sha(ABUNDANCE),"abundance_mass_pinned":sha(ABUNDANCE)==ABUNDANCE_SHA256,
              "case_fixture_matches":fixture_ok and fixture_exact["pass"],
              "case_fixture_contract":fixture_exact,
              "synthetic_artifact_review":artifact_review,
              "expected_labels":labels,"historical_p108_verified":historical,
              "case_count":len(cases),"target_count":sum(len(c["targets"]) for c in cases),
              "endpoint_count":2*sum(len(c["targets"]) for c in cases),
              "control_hashes":hashes,**data}
    report["pass"] = bool(report["protocol_registered"] and pack_ok and historical
                          and report["repair_amendment_registered"] and report["repair_rounds"]==1
                          and report["repair_evidence_matches"]
                          and report["case_fixture_matches"] and report["abundance_mass_pinned"] and labels.get("pass") is True
                          and artifact_review.get("pass") is True
                          and report["p109_amendment_registered"] and report["p109_format_failure_log_matches"]
                          and report["historical_p109_verified"]
                          and report["case_count"]==9 and report["target_count"]==18
                          and report["endpoint_count"]==36 and all(hashes.values()))
    return report


def _expected_labels(cases: list[dict]) -> dict:
    # Combine the Decimal analytic Nb94 activity and caller-declared H3 box,
    # then feed the raw inventory to P105's independent rule arithmetic.
    from p106_bounds_control import _p105_oracle
    classifier = _p105_oracle().independent_classification
    pack=json.loads(PACK.read_text())
    labels=[]
    for c in cases:
        xlo=c["composition_wt_percent_bounds"].get("Nb",{}).get("lower_wt_percent",0)
        xhi=c["composition_wt_percent_bounds"].get("Nb",{}).get("upper_wt_percent",0)
        lo=oracle.analytic_nb_activity(oracle.Decimal(str(xlo)),1)
        hi=oracle.analytic_nb_activity(oracle.Decimal(str(xhi)),1)
        classes=[]
        for activity, external_bound in ((lo,"lower_bq"),(hi,"upper_bq")):
            activity_map={"Nb94":float(activity)} if activity else {}
            ext=c["external_tritium"]
            if ext.get("status")=="bounded":
                h3=float(ext["activity_bounds_bq"]["1"][external_bound])
                if h3: activity_map["H3"]=h3
            props={"Nb94":{"z":41,"half_life_s":1e11,"alpha_emitting":False},
                   "H3":{"z":1,"half_life_s":1e9,"alpha_emitting":False}}
            evaluated=classifier({"activity_Bq_per_g":activity_map,"mass_g":1.0,
                                  "displaced_volume_cm3":c["displaced_volume_cm3"],
                                  "waste_type":"activated_metal","nuclide_properties":props,
                                  "external_tritium":{"status":"required" if ext.get("status")=="required" else "not_applicable"}},
                                 pack["rows"])
            if c["inventory_coverage"]!="complete" or c["variant"]!="complete":
                evaluated["class"]="unknown"
            classes.append(evaluated["class"])
        labels.append({"id":c["id"],"lower_class":classes[0],"upper_class":classes[1]})
    expected={"ranged_volume_1":["A","C"],"high_fixed_volume_7":["A","A"],
              "ranged_volume_point_1":["C","above_class_c"],
              "low_fixed_external_h3":["A","B"],
              "required_external_h3":["unknown","unknown"],
              "caller_incomplete":["unknown","unknown"],
              "missing_fe58_target":["unknown","unknown"],
              "missing_nb94_decay":["unknown","unknown"],
              "zero_response_reference":["A","A"]}
    derived={x["id"]:[x["lower_class"],x["upper_class"]] for x in labels}
    return {"pass":len(labels)==9 and derived==expected,"labels":labels,"expected":expected}


def _fixture_contract(cases: list[dict], fixture: dict, data_generated: dict) -> dict:
    ranged={"Fe":{"lower_wt_percent":50.0,"upper_wt_percent":50.0},
            "Nb":{"lower_wt_percent":1.0,"upper_wt_percent":10.0},
            "Si":{"lower_wt_percent":40.0,"upper_wt_percent":49.0}}
    fixed_high={"Fe":{"lower_wt_percent":50.0,"upper_wt_percent":50.0},
                "Nb":{"lower_wt_percent":10.0,"upper_wt_percent":10.0},
                "Si":{"lower_wt_percent":40.0,"upper_wt_percent":40.0}}
    fixed_low={"Fe":{"lower_wt_percent":50.0,"upper_wt_percent":50.0},
               "Nb":{"lower_wt_percent":1.0,"upper_wt_percent":1.0},
               "Si":{"lower_wt_percent":49.0,"upper_wt_percent":49.0}}
    zero={"Fe":{"lower_wt_percent":50.0,"upper_wt_percent":50.0},
          "Si":{"lower_wt_percent":50.0,"upper_wt_percent":50.0}}
    expected={"ranged_volume_1":(ranged,1.0,"complete",[],"complete","not_applicable"),
      "high_fixed_volume_7":(fixed_high,7.0,"complete",[],"complete","not_applicable"),
      "ranged_volume_point_1":(ranged,.1,"complete",[],"complete","not_applicable"),
      "low_fixed_external_h3":(fixed_low,1.0,"complete",[],"complete","bounded"),
      "required_external_h3":(ranged,1.0,"complete",[],"complete","required"),
      "caller_incomplete":(ranged,1.0,"incomplete",["artificial caller-declared unbounded inventory"],"complete","not_applicable"),
      "missing_fe58_target":(ranged,1.0,"complete",[],"omit_fe58_target","not_applicable"),
      "missing_nb94_decay":(ranged,1.0,"complete",[],"omit_nb94_decay","not_applicable"),
      "zero_response_reference":(zero,1.0,"complete",[],"complete","not_applicable")}
    actual={c.get("id"):c for c in cases}
    checks={}
    case_keys={"id","composition_wt_percent_bounds","targets","displaced_volume_cm3",
               "external_tritium","inventory_coverage","unbounded_inventory_reasons","variant"}
    for name,(bounds,volume,coverage,reasons,variant,extstatus) in expected.items():
        item=actual.get(name,{})
        checks[name]=bool(item.get("composition_wt_percent_bounds")==bounds
            and set(item)==case_keys
            and item.get("targets")==[1,2]
            and item.get("displaced_volume_cm3")==volume
            and item.get("inventory_coverage")==coverage
            and item.get("unbounded_inventory_reasons")==reasons
            and item.get("variant")==variant
            and item.get("external_tritium",{}).get("status")==extstatus)
    external=actual.get("low_fixed_external_h3",{}).get("external_tritium",{})
    h3_ok=(external.get("source")=="artificial external H3 offset control"
           and external.get("excludes_activation") is True
           and external.get("activity_bounds_bq")=={"1":{"lower_bq":740000.0,"upper_bq":2220000.0},
                                                     "2":{"lower_bq":740000.0,"upper_bq":2220000.0}})
    fixture_ok=(fixture.get("schema")==oracle.CASE_SCHEMA
        and fixture.get("source")=="artificial native Fe/Si/Nb capture and decay model; no evaluated nuclear data"
        and set(fixture)=={"schema","source","cases"} and fixture.get("cases")==cases)
    data_ok=(data_generated["complete"]["target_count"]==10 and data_generated["complete"]["row_count"]==11
             and data_generated["complete"]["decay_record_count"]==12
             and data_generated["omit_fe58_target"]["target_count"]==9
             and data_generated["omit_fe58_target"]["row_count"]==10
             and data_generated["omit_nb94_decay"]["target_count"]==10
             and data_generated["omit_nb94_decay"]["row_count"]==11
             and data_generated["omit_nb94_decay"]["decay_record_count"]==11)
    return {"pass":len(actual)==9 and all(checks.values()) and h3_ok and fixture_ok and data_ok,
            "case_checks":checks,"h3_box":h3_ok,"fixture_header":fixture_ok,"native_data_population":data_ok}


def g0(*, seal=False, no_write=False) -> int:
    report=_g0_base()
    if seal:
        if report["pass"]:
            G0.parent.mkdir(parents=True,exist_ok=True)
            G0.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    elif no_write:
        try: persisted=json.loads(G0.read_text())
        except (OSError,ValueError): persisted=None
        matches=persisted==report
        report["pass"]=report["pass"] and matches
        report["persisted_seal_matches"]=matches
    print(json.dumps(report,sort_keys=True,indent=2))
    return 0 if report["pass"] else 1


def _run(argv: list[str]):
    from p105_budget_control import _run as bounded_run
    return bounded_run(argv,cwd=ROOT,timeout_s=120)


def _request(case: dict, base_path: str) -> dict:
    return {"schema":"actinv-waste-composition-solve-spec-1","rules":"us-nrc-10cfr61.55-v1",
            "components":[{"id":case["id"],"base_spec":base_path,"mass_g":1.0,
                "displaced_volume_cm3":case["displaced_volume_cm3"],"waste_type":"activated_metal",
                "external_tritium":case["external_tritium"],
                "composition_wt_percent_bounds":case["composition_wt_percent_bounds"],
                "targets":[1,2],"inventory_coverage":case["inventory_coverage"],
                "unbounded_inventory_reasons":case["unbounded_inventory_reasons"],
                "model_source":"P110 synthetic artificial Fe/Si/Nb capture model",
                "model_assumptions":"fixed one-group 1-barn Nb93 capture; analytic Nb94 beta decay"}]}


def _write_request(path: Path, value: object) -> bytes:
    raw=(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)).encode()
    path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
    return raw


def _one_component_output(report: dict) -> dict:
    out=copy.deepcopy(report)
    out["components"]=[dict(item) for item in out.get("components",[]) if isinstance(item,dict)]
    return out


def _run_one(case: dict, fixture: dict, work: Path) -> tuple[dict,bytes,Path,Path,dict]:
    base=fixture["base_specs"][case["variant"]]
    # Wrapper and base paths resolve relative to the request; the source spec
    # itself keeps its ordinary CWD-relative nuclear-data semantics.
    req_dir=work/"requests"; req_dir.mkdir(parents=True,exist_ok=True)
    name=case["id"]
    wrapper=req_dir/f"{name}.json"; output=work/"outputs"/f"{name}.json"
    output.parent.mkdir(parents=True,exist_ok=True)
    base_abs=ROOT/base["path"]
    base_relative=os.path.relpath(base_abs,req_dir)
    spec=_request(case,base_relative)
    raw=_write_request(wrapper,spec)
    proc=_run([str(ACTINV),"waste","composition","solve",str(wrapper),str(output)])
    if proc.returncode!=0:
        raise RuntimeError(f"P110 request {name} failed: {proc.stderr[-2000:]}\n{proc.stdout[-1000:]}")
    actual=json.loads(output.read_text(encoding="utf-8"))
    return actual,raw,wrapper,output,spec


def _run_refusals(work: Path) -> dict:
    case=oracle.generate_cases()[0]
    fixture=oracle.write_fixture(ROOT)
    base=fixture["base_specs"]["complete"]["path"]
    valid=_request(case,os.path.relpath(ROOT/base,work))
    variants={}
    def add(name,edit):
        value=copy.deepcopy(valid); edit(value["components"][0]); variants[name]=value
    add("negative_mass",lambda c:c.__setitem__("mass_g",-1.0))
    add("zero_mass",lambda c:c.__setitem__("mass_g",0.0))
    add("null_volume",lambda c:c.__setitem__("displaced_volume_cm3",None))
    add("both_geometries",lambda c:c.__setitem__("density_g_cm3",1.0))
    add("zero_density",lambda c:(c.pop("displaced_volume_cm3"),c.__setitem__("density_g_cm3",0.0)))
    add("blank_source",lambda c:c.__setitem__("model_source"," "))
    add("blank_assumptions",lambda c:c.__setitem__("model_assumptions",""))
    add("empty_component_id",lambda c:c.__setitem__("id",""))
    add("unknown_waste_type",lambda c:c.__setitem__("waste_type","other"))
    add("unknown_element",lambda c:c["composition_wt_percent_bounds"].update({"Xx":{"lower_wt_percent":0,"upper_wt_percent":0}}))
    add("negative_weight",lambda c:c["composition_wt_percent_bounds"]["Fe"].update({"lower_wt_percent":-1.0}))
    add("reversed_weight",lambda c:c["composition_wt_percent_bounds"]["Nb"].update({"lower_wt_percent":11.0,"upper_wt_percent":10.0}))
    add("infeasible_sum",lambda c:c["composition_wt_percent_bounds"].update({"Fe":{"lower_wt_percent":80,"upper_wt_percent":100}}))
    add("empty_bounds",lambda c:c.__setitem__("composition_wt_percent_bounds",{}))
    add("duplicate_target",lambda c:c.__setitem__("targets",[1,1]))
    add("zero_target",lambda c:c.__setitem__("targets",[0]))
    add("absent_target",lambda c:c.__setitem__("targets",[99]))
    add("missing_reason",lambda c:(c.__setitem__("inventory_coverage","incomplete"),c.__setitem__("unbounded_inventory_reasons",[])))
    add("complete_with_reason",lambda c:c.__setitem__("unbounded_inventory_reasons",["contradictory"]))
    add("bad_external",lambda c:c.__setitem__("external_tritium",{"status":"bounded","source":"x","excludes_activation":False,"activity_bounds_bq":{"1":{"lower_bq":0,"upper_bq":1},"2":{"lower_bq":0,"upper_bq":1}}}))
    add("missing_external_step",lambda c:c.__setitem__("external_tritium",{"status":"bounded","source":"x","excludes_activation":True,"activity_bounds_bq":{"1":{"lower_bq":0,"upper_bq":1}}}))
    add("noncanonical_external_step",lambda c:c.__setitem__("external_tritium",{"status":"bounded","source":"x","excludes_activation":True,"activity_bounds_bq":{"01":{"lower_bq":0,"upper_bq":1},"2":{"lower_bq":0,"upper_bq":1}}}))
    add("negative_h3",lambda c:c.__setitem__("external_tritium",{"status":"bounded","source":"x","excludes_activation":True,"activity_bounds_bq":{"1":{"lower_bq":-1,"upper_bq":1},"2":{"lower_bq":0,"upper_bq":1}}}))
    add("wrong_rules",lambda c:None)
    variants["wrong_rules"]["rules"]="other"
    variants["unsupported_top_key"]=copy.deepcopy(valid); variants["unsupported_top_key"]["unexpected"]=1
    variants["unsupported_component_key"]=copy.deepcopy(valid); variants["unsupported_component_key"]["components"][0]["confidence"]=0.9
    variants["wrong_schema"]=copy.deepcopy(valid); variants["wrong_schema"]["schema"]="wrong"
    variants["wrong_base_spec"]=copy.deepcopy(valid); variants["wrong_base_spec"]["components"][0]["base_spec"]="no-such-spec.json"
    variants["too_many_components"]=copy.deepcopy(valid); variants["too_many_components"]["components"]*=5
    variants["duplicate_component_id"]=copy.deepcopy(valid); variants["duplicate_component_id"]["components"].append(copy.deepcopy(variants["duplicate_component_id"]["components"][0]))
    variants["malformed_bounds"]=copy.deepcopy(valid); variants["malformed_bounds"]["components"][0]["composition_wt_percent_bounds"]["Fe"]["lower_wt_percent"]=float("nan")
    base_doc=json.loads((ROOT/fixture["base_specs"]["complete"]["path"]).read_text())
    def embedded_base(mutator):
        value=copy.deepcopy(valid); embedded=copy.deepcopy(base_doc); mutator(embedded)
        value["components"][0]["base_spec"]=embedded; return value
    variants["unsupported_uncertainty"]=embedded_base(lambda b:b.__setitem__("uncertainty",{}))
    variants["unsupported_damage"]=embedded_base(lambda b:b.__setitem__("damage",{}))
    variants["unsupported_screen"]=embedded_base(lambda b:b.setdefault("options",{}).__setitem__("screen",{}))
    variants["unsupported_gas"]=embedded_base(lambda b:b.setdefault("options",{}).__setitem__("gas",True))
    variants["unsupported_cram_order"]=embedded_base(lambda b:b.setdefault("options",{}).__setitem__("cram_order",8))
    variants["wrong_projectile"]=embedded_base(lambda b:b.__setitem__("projectile","photon"))
    valid_text=json.dumps(valid,sort_keys=True,separators=(",",":"),allow_nan=False)
    base_text=(ROOT/fixture["base_specs"]["complete"]["path"]).read_text(encoding="utf-8").strip()
    duplicate_base_text=base_text.replace('"spec": "actinv-spec-1"',
        '"spec": "actinv-spec-1", "spec": "actinv-spec-1"',1)
    duplicate_embedded=valid_text.replace('"base_spec":"base_run.complete.json"',
        '"base_spec":'+duplicate_base_text,1)
    base_duplicate_file=work/"base_duplicate.json"
    base_duplicate_file.write_text(duplicate_base_text,encoding="utf-8")
    duplicate_path=valid_text.replace('"base_spec":"base_run.complete.json"',
        '"base_spec":"base_duplicate.json"',1)
    variants_raw={
      "duplicate_outer_key":valid_text.replace('"schema":"actinv-waste-composition-solve-spec-1"',
          '"schema":"actinv-waste-composition-solve-spec-1","schema":"actinv-waste-composition-solve-spec-1"',1),
      "duplicate_bound_coordinate":valid_text.replace('"Nb":{"lower_wt_percent":1.0,"upper_wt_percent":10.0}',
          '"Nb":{"lower_wt_percent":1.0,"upper_wt_percent":10.0},"Nb":{"lower_wt_percent":1.0,"upper_wt_percent":10.0}',1),
      "duplicate_external_key":valid_text.replace('"status":"not_applicable"',
          '"status":"not_applicable","status":"not_applicable"',1),
      "duplicate_component_mass":valid_text.replace('"mass_g":1.0', '"mass_g":1.0,"mass_g":1.0',1),
      "duplicate_embedded_base_spec_key":duplicate_embedded,
      "duplicate_path_base_spec_key":duplicate_path,
    }
    checks={}; output=work/"refusal-sentinel.json"; wrapper=work/"refusal.json"
    for name,value in variants.items():
        sentinel=b"P110_OUTPUT_SENTINEL\n"; output.write_bytes(sentinel)
        raw=(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=True)).encode()
        wrapper.write_bytes(raw)
        proc=_run([str(ACTINV),"waste","composition","solve",str(wrapper),str(output)])
        checks[name]=proc.returncode!=0 and output.read_bytes()==sentinel
    for name,raw in variants_raw.items():
        sentinel=b"P110_OUTPUT_SENTINEL\n"; output.write_bytes(sentinel); wrapper.write_text(raw)
        proc=_run([str(ACTINV),"waste","composition","solve",str(wrapper),str(output)])
        checks[name]=proc.returncode!=0 and output.read_bytes()==sentinel
    return {"pass":len(checks)>=30 and all(checks.values()),"checks":checks}


def _mutations_rejected(records: list[tuple[dict,dict,bytes,list[dict]]]) -> dict:
    checks={}
    def _flip_class(eval_record):
        old=eval_record.get("class")
        eval_record["class"]="B" if old=="A" else "A"
    mutations=(
        ("outer_schema",lambda x:x.__setitem__("schema","bad")),
        ("outer_input_sha",lambda x:x.__setitem__("input_sha256","0"*64)),
        ("generated_input_sha",lambda x:x.__setitem__("generated_response_input_sha256","0"*64)),
        ("rules_sha",lambda x:x["rules"].__setitem__("sha256","0"*64)),
        ("component_id",lambda x:x["components"][0].__setitem__("id","bad")),
        ("weight_bounds",lambda x:x["components"][0]["composition_wt_percent_bounds"]["Nb"].__setitem__("upper_wt_percent",9.0)),
        ("class_envelope",lambda x:x["components"][0]["targets"][0].__setitem__("class_envelope",["A"])),
        ("projection_lower",lambda x:x["components"][0]["targets"][0]["projection_records"]["Nb94"].__setitem__("lower_bq",0.0)),
        ("projection_upper",lambda x:x["components"][0]["targets"][0]["projection_records"]["Nb94"].__setitem__("upper_bq",0.0)),
        ("projection_witness",lambda x:x["components"][0]["targets"][0]["projection_records"]["Nb94"]["min_witness_wt_percent"].__setitem__("Nb",0.0)),
        ("basis_activity",lambda x:x["components"][0]["native"]["solver_basis"][0]["targets"][0]["activity_bq_per_g"].__setitem__("Nb94",123.0)),
        ("basis_atoms",lambda x:x["components"][0]["native"]["solver_basis"][0]["targets"][0]["atoms_per_g"].__setitem__("Fe56",0.0)),
        ("basis_step",lambda x:x["components"][0]["native"]["solver_basis"][0]["targets"][0].__setitem__("step",88)),
        ("basis_missing_step",lambda x:x["components"][0]["native"]["solver_basis"][0]["targets"][0].pop("step")),
        ("basis_duplicate_step",lambda x:x["components"][0]["native"]["solver_basis"][0]["targets"][1].__setitem__("step",x["components"][0]["native"]["solver_basis"][0]["targets"][0]["step"])),
        ("basis_duplicate_element",lambda x:x["components"][0]["native"]["solver_basis"][1].__setitem__("element",x["components"][0]["native"]["solver_basis"][0]["element"])),
        ("witness_atoms",lambda x:x["components"][0]["native"]["native_verification"]["verified_witnesses"][0]["targets"][0]["solved_atoms_per_g"].__setitem__("Nb93",0.0)),
        ("witness_activity",lambda x:x["components"][0]["native"]["native_verification"]["verified_witnesses"][0]["targets"][0]["solved_activity_bq_per_g"].__setitem__("Nb94",0.0)),
        ("witness_class",lambda x:_flip_class(x["components"][0]["native"]["native_verification"]["verified_witnesses"][0]["targets"][0]["external_h3_checks"][0]["solved_evaluation"])),
        ("witness_pass",lambda x:x["components"][0]["native"]["native_verification"]["verified_witnesses"][0]["targets"][0].__setitem__("pass",False)),
        ("native_pass",lambda x:x["components"][0]["native"]["native_verification"].__setitem__("passed",False)),
        ("witness_count",lambda x:x["components"][0]["native"]["native_verification"].__setitem__("unique_witness_count",999)),
        ("target_time",lambda x:x["components"][0]["targets"][0].__setitem__("t_s",0.0)),
    )
    for case,actual,input_raw,spec in records:
        rows=json.loads(PACK.read_text())["rows"]
        for name,mutate in mutations:
            changed=copy.deepcopy(actual)
            try: mutate(changed)
            except (KeyError,IndexError,TypeError):
                checks[f"{case['id']}:{name}"]=False; continue
            errors=oracle.validate_report(case,changed,input_sha256=hashlib.sha256(input_raw).hexdigest(),
                                          expected_projection=spec,rows=rows)
            checks[f"{case['id']}:{name}"]=bool(errors)
        break
    return checks


def _persist_report(report: dict, path: Path, *, no_write: bool) -> int:
    if no_write:
        try: persisted=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError): persisted=None
        matches=persisted==report
        report["pass"]=report.get("pass") is True and matches
        report["persisted_result_matches"]=matches
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,sort_keys=True,indent=2))
    return 0 if report.get("pass") is True else 1


def run_g1(*, no_write: bool=False) -> int:
    work=ROOT/"target/p110-controls"
    fixture=oracle.write_fixture(ROOT)
    cases=oracle.generate_cases(); rows=json.loads(PACK.read_text())["rows"]
    records=[]; errors=[]; repeats=[]; case_evidence=[]
    for case in cases:
        try:
            actual,raw,wrapper,output,request=_run_one(case,fixture,work)
            output_bytes=output.read_bytes()
            repeated_path=work/"outputs"/f"{case['id']}.repeat.json"
            repeated_path.parent.mkdir(parents=True,exist_ok=True)
            proc=_run([str(ACTINV),"waste","composition","solve",str(wrapper),str(repeated_path)])
            repeat_ok=proc.returncode==0 and repeated_path.read_bytes()==output_bytes
            repeats.append(repeat_ok)
            try: generated=json.loads(actual["generated_response_spec_json"])
            except (KeyError,ValueError,TypeError) as e:
                generated={}; errors.append(f"{case['id']}: malformed generated P108 specification: {e}")
            generated_raw=actual.get("generated_response_spec_json","").encode()
            if hashlib.sha256(generated_raw).hexdigest()!=actual.get("generated_response_input_sha256"):
                errors.append(f"{case['id']}: generated response input SHA mismatch")
            errs=oracle.validate_report(case,actual,input_sha256=hashlib.sha256(raw).hexdigest(),
                                        expected_projection=generated,rows=rows)
            errors.extend(f"{case['id']}: {e}" for e in errs)
            comp=actual.get("components",[{}])[0]
            if isinstance(comp,dict):
                errors.extend(f"{case['id']}: {e}" for e in native_identity.validate_native_identity(
                    case,comp,fixture,ROOT))
            native=comp.get("native",{}) if isinstance(comp,dict) else {}
            work_counts=actual.get("solver_work",{})
            expected_basis=len(case["composition_wt_percent_bounds"])
            expected_witnesses=len(native.get("native_verification",{}).get("verified_witnesses",[]))
            if (work_counts.get("basis_solve_count")!=expected_basis
                    or work_counts.get("full_witness_solve_count")!=expected_witnesses
                    or work_counts.get("unique_witness_count")!=expected_witnesses
                    or work_counts.get("max_basis_solves")!=32
                    or work_counts.get("max_full_witness_solves")!=64):
                errors.append(f"{case['id']}: solver work counts/caps differ")
            expected_scope={"activity_model":"fixed_rate_affine_activity",
                "solver_error_bounded":False,"nuclear_data_or_physical_model_error_bounded":False,
                "verification":"selected extremum and canonical reference compositions only",
                "probability_or_confidence_claim":False}
            if actual.get("scientific_scope")!=expected_scope:
                errors.append(f"{case['id']}: scientific scope differs")
            base_expected=fixture["base_specs"][case["variant"]]
            got_base_sha=native.get("base_spec_sha256",comp.get("base_spec_sha256"))
            if got_base_sha!=base_expected["sha256"]:
                errors.append(f"{case['id']}: base spec SHA differs from frozen fixture")
            base_source=native.get("base_spec_source",comp.get("base_spec_source"))
            if not isinstance(base_source,str) or Path(base_source).is_absolute():
                errors.append(f"{case['id']}: base spec source is missing or absolute")
            provenance=native.get("solver_provenance",{})
            if provenance.get("base_spec_source")!=base_source:
                errors.append(f"{case['id']}: solver provenance base source differs")
            provenance_text=json.dumps(provenance,sort_keys=True)
            for digest in fixture[case["variant"]]["sha256"].values():
                if digest not in provenance_text:
                    errors.append(f"{case['id']}: solver certificate omits a frozen data SHA")
            if base_expected["sha256"] not in json.dumps(native,sort_keys=True):
                errors.append(f"{case['id']}: native record omits the exact base spec SHA")
            native_verify=native.get("native_verification",{})
            coverage=native.get("coverage_evidence",[])
            all_reasons=[reason for item in coverage if isinstance(item,dict)
                         for reason in item.get("reasons",[])]
            if case["variant"]=="complete" and not all_reasons and native_verify.get("passed") is not True:
                errors.append(f"{case['id']}: complete native verification evidence is absent")
            if case["variant"]!="complete" and not all_reasons:
                errors.append(f"{case['id']}: intended missing-data downgrade has no audit reason")
            records.append((case,actual,raw,generated))
            native=actual.get("components",[{}])[0].get("native",{})
            targets=actual.get("components",[{}])[0].get("targets",[])
            max_activity_error=max((float(t.get("activity_l1_absolute_error_bq_per_g",0.0))
                                    for w in native.get("native_verification",{}).get("verified_witnesses",[])
                                    for t in w.get("targets",[])),default=0.0)
            max_atom_error=max((float(t.get("atom_l1_absolute_error_per_g",0.0))
                                for w in native.get("native_verification",{}).get("verified_witnesses",[])
                                for t in w.get("targets",[])),default=0.0)
            case_evidence.append({"id":case["id"],"input_sha256":hashlib.sha256(raw).hexdigest(),
                "output_sha256":hashlib.sha256(output_bytes).hexdigest(),
                "generated_response_input_sha256":actual.get("generated_response_input_sha256"),
                "selected_steps":[{"step":t.get("step"),"lower_class":t.get("lower",{}).get("class"),
                    "upper_class":t.get("upper",{}).get("class"),"class_envelope":t.get("class_envelope")}
                    for t in targets],
                "basis_solve_count":len(native.get("solver_basis",[])),
                "full_witness_solve_count":len(native.get("native_verification",{}).get("verified_witnesses",[])),
                "maximum_activity_l1_error_bq_per_g":max_activity_error,
                "maximum_atom_l1_error_per_g":max_atom_error})
        except Exception as exc:
            errors.append(f"{case['id']}: {type(exc).__name__}: {exc}")
    mutations=_mutations_rejected(records[:1]) if records else {}
    refusals=_run_refusals(work)
    target_count=sum(len(c.get("targets",[])) or 2 for c in cases)
    endpoint_count=2*target_count
    report={"schema":"actinv-p110-composition-solve-g1-1","phase":"P110",
            "protocol_sha256":PROTOCOL_SHA256,"case_count":len(cases),"target_count":target_count,
            "endpoint_count":endpoint_count,"independent_comparison_count":len(records),
            "repeat_byte_identical":len(repeats)==9 and all(repeats),
            "case_evidence":case_evidence,
            "mutations_rejected":mutations,"refusal_controls":refusals,"failures":errors}
    report["pass"]=bool(not errors and len(records)==9 and target_count==18 and endpoint_count==36
                        and len(mutations)>=20 and all(mutations.values())
                        and refusals.get("pass") is True and report["repeat_byte_identical"])
    return _persist_report(report,G1,no_write=no_write)


def run_g2(*, no_write: bool=False) -> int:
    g1_path=G1
    before=sha(g1_path)
    if before is None: return 1
    # Recompute G1 through the same bounded native cases; its persisted replay
    # compares the stable report before adding the diagnostic replay field.
    g1_status=run_g1(no_write=True)
    report={"schema":"actinv-p110-composition-solve-g2-1","phase":"P110",
            "protocol_sha256":PROTOCOL_SHA256,"g1_result_sha256":before,
            "separate_output_paths_byte_identical":g1_status==0}
    report["pass"]=g1_status==0 and before==sha(g1_path)
    return _persist_report(report,G2,no_write=no_write)


def main() -> int:
    ap=argparse.ArgumentParser()
    modes=ap.add_mutually_exclusive_group()
    modes.add_argument("--seal",action="store_true")
    modes.add_argument("--no-write",action="store_true")
    ap.add_argument("--g0-only",action="store_true")
    ap.add_argument("--g1-only",action="store_true")
    ap.add_argument("--g2-only",action="store_true")
    args=ap.parse_args()
    if args.g1_only: return run_g1(no_write=args.no_write)
    if args.g2_only: return run_g2(no_write=args.no_write)
    if args.g0_only or args.seal: return g0(seal=args.seal,no_write=args.no_write)
    g0_status=g0(no_write=True)
    if g0_status: return g0_status
    g1_status=run_g1(no_write=args.no_write)
    if g1_status: return g1_status
    return run_g2(no_write=args.no_write)


if __name__=="__main__":
    raise SystemExit(main())
