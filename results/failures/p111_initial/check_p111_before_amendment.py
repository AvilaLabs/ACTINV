#!/usr/bin/env python3
"""Independent sealed controls for the optional P111 draft intrusion screen.

The checker owns no solver process: only G1/G2 call the already reviewed
bounded P105 CLI runner. `--seal` is source-only and must run before production
implementation changes; `--no-write` performs exact read-only replay.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

import check_p105
import check_p110_verdict
import p111_intrusion_control as oracle
import p105_budget_control as bounded

ROOT=Path(__file__).resolve().parents[1]
PROTOCOL=ROOT/"protocols/ACTINV-P111_PROTOCOL.md"
PROTOCOL_SHA256="c2ffdb958682090803702c1de4adf670b3838b87db47eb8312356278199f231f"
PACK=ROOT/"data/waste_us_nrc_nureg1556_v22_draft_2026_02_v1.json"
MIRROR=ROOT/"crates/actinv-core/data/waste_us_nrc_nureg1556_v22_draft_2026_02_v1.json"
FIXTURE=ROOT/"controls/fixtures/p111/cases.json"
LITERAL_ROWS=ROOT/"controls/fixtures/p111/literal_rows.json"
RECONCILIATION=ROOT/"controls/fixtures/p111/source_reconciliation.json"
EXCERPTS={"february_2026":ROOT/"controls/fixtures/p111/source_excerpt_feb2026.txt",
          "march_2025":ROOT/"controls/fixtures/p111/source_excerpt_mar2025.txt"}
G0=ROOT/"results/g0_p111_intrusion_screen.json"
G1=ROOT/"results/g1_p111_intrusion_screen.json"
G2=ROOT/"results/g2_p111_intrusion_screen.json"
ACTINV=Path(os.environ.get("ACTINV_BIN",ROOT/"target/release/actinv"))
SOURCE_IDS=("P105","P107","P108","P109","P110")
PRIOR_VERDICTS={"P105":"P105-PASS","P107":"P107-PASS","P108":"P108-PASS",
                "P109":"P109-FAIL","P110":"P110-PASS"}
CONTROL_FILES=(
    "controls/check_p111.py","controls/p111_intrusion_control.py",
    "controls/test_p111_oracle.py","controls/test_p111_seal.py",
    "controls/test_p111_verdict.py","controls/check_p111_verdict.py",
    "controls/fixtures/p111/cases.json","controls/fixtures/p111/literal_rows.json",
    "controls/fixtures/p111/source_reconciliation.json",
    "controls/fixtures/p111/source_excerpt_feb2026.txt",
    "controls/fixtures/p111/source_excerpt_mar2025.txt",
    "data/waste_us_nrc_nureg1556_v22_draft_2026_02_v1.json",
    "crates/actinv-core/data/waste_us_nrc_nureg1556_v22_draft_2026_02_v1.json",
)
CASE_COUNT_MIN=40
TARGET_COUNT_MIN=40
CI_BQ_CM3=37_000.0


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha(path: Path) -> str | None:
    try: return sha_bytes(path.read_bytes())
    except OSError: return None


def _registered() -> bool:
    registry=ROOT/"protocols/protocol_hash.txt"
    wanted=f"{PROTOCOL_SHA256}  protocols/ACTINV-P111_PROTOCOL.md"
    try: return sha(PROTOCOL)==PROTOCOL_SHA256 and wanted in registry.read_text(encoding="utf-8").splitlines()
    except OSError: return False


def _json(path: Path) -> dict | None:
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else None
    except (OSError,ValueError): return None


def _historical_p110() -> bool:
    """Use the prior verdict's own historical source/CI materializer."""
    try:
        derived=check_p110_verdict.derive()
        persisted=_json(ROOT/"results/p110_verdict.json")
        return derived==persisted and derived.get("verdict")=="P110-PASS"
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
        return False


def _prior_verdicts() -> tuple[dict,dict,bool]:
    evidence={}; details={}; okay=True
    for phase,wanted in PRIOR_VERDICTS.items():
        path=ROOT/f"results/{phase.lower()}_verdict.json"
        record=_json(path); digest=sha(path)
        evidence[path.relative_to(ROOT).as_posix()]=digest
        details[phase]={"sha256":digest,"verdict":record.get("verdict") if record else None}
        okay=okay and record is not None and record.get("verdict")==wanted and digest is not None
    return evidence,details,okay


def _norm_text(value: str) -> str:
    return " ".join(value.replace("\u2009"," ").split()).casefold()


def _num_token(token:str)->float|None:
    token=token.strip()
    if token.casefold()=="no limit": return None
    cleaned=token.replace(",","").replace(" ","")
    cleaned=re.sub(r"[xX]10", "e", cleaned)
    try: return float(cleaned)
    except ValueError: raise ValueError(f"unparsed printed table value {token!r}")


def _parse_row_cells(row_text:str,unit:str)->tuple[list[float|None],str|None]:
    match=re.search(r"\b"+re.escape(unit)+r"\b",row_text,re.IGNORECASE)
    if not match: raise ValueError(f"row does not include unit {unit}")
    body=row_text[match.end():]
    tokens=re.findall(r"No\s+limit|\d[\d,]*(?:\.\d+)?(?:\s*[xX]\s*10\s*\^?\s*\d+)?",body,re.IGNORECASE)
    if len(tokens)<3: raise ValueError(f"row has fewer than three class cells: {row_text!r}")
    note_match=re.search(r"([a-g](?:\s*,\s*[a-g])*)\s*$",body,re.IGNORECASE)
    note="".join(note_match.group(1).split()) if note_match else None
    return [_num_token(token) for token in tokens[:3]],note


def _parse_february_excerpt(text:str)->tuple[list[dict],list[str]]:
    errors=[]; norm=" ".join(text.replace("\u2009"," ").split())
    marker="[Table 8-5, printed pages 8-70–8-71: complete row text and footnotes]"
    start=norm.find(marker); end=norm.find("Notes:",start)
    if start<0 or end<0: return [],["February table excerpt markers missing"]
    block=norm[start:end]; rows=[]; expected=oracle.public_rows(); cursor=0
    for i,want in enumerate(expected):
        label=want["label"]; position=block.find(label,cursor)
        if position<0:
            errors.append(f"February source table row label missing: {want['id']}"); continue
        next_positions=[]
        for later in expected[i+1:]:
            found=block.find(later["label"],position+len(label))
            if found>=0: next_positions.append(found)
        finish=min(next_positions) if next_positions else len(block)
        row_text=block[position:finish]
        try: values,note=_parse_row_cells(row_text,want["unit"])
        except ValueError as error: errors.append(f"February {want['id']}: {error}"); values=[]; note=None
        rows.append({"id":want["id"],"label":label,"unit":want["unit"],"limits":values,"basis_note":note})
        cursor=position+len(label)
    return rows,errors


def _parse_march_excerpt(text:str)->tuple[list[dict],list[str]]:
    errors=[]; lines=text.splitlines(); in_table=False; parsed=[]
    for line in lines:
        if "[Table 8-5," in line: in_table=True; continue
        if in_table and line.startswith("Notes:"): break
        if not in_table or "|" not in line: continue
        fields=[field.strip() for field in line.strip().split("|")]
        if len(fields)!=7 or fields[0] in ("id",""): continue
        row_id,label,unit,*cells,note=fields
        try: limits=[_num_token(value) for value in cells]
        except ValueError as error: errors.append(f"March {row_id}: {error}"); continue
        parsed.append({"id":row_id,"label":label,"unit":unit,"limits":limits,"basis_note":note})
    expected=oracle.public_rows()
    if len(parsed)!=25: errors.append(f"March source excerpt parsed {len(parsed)} rows, expected 25")
    for i,(got,want) in enumerate(zip(parsed,expected)):
        if (got["label"],got["unit"],got["limits"],got["basis_note"]) != (want["label"],want["unit"],want["limits"],want["basis_note"]):
            errors.append(f"March source row {i} values/label/unit/note differ")
    return parsed,errors


def _source_review() -> dict:
    reconciliation=_json(RECONCILIATION)
    literal=_json(LITERAL_ROWS)
    pack=_json(PACK); mirror=_json(MIRROR)
    errors=[]
    if not isinstance(reconciliation,dict): errors.append("source reconciliation is absent or malformed")
    if not isinstance(literal,dict): errors.append("literal source-row fixture is absent or malformed")
    if not isinstance(pack,dict) or not isinstance(mirror,dict): errors.append("draft pack or mirror is malformed")
    if not errors:
        if reconciliation.get("schema")!="actinv-waste-source-reconciliation-1": errors.append("reconciliation schema")
        if reconciliation.get("selected_pack_id")!="us-nrc-nureg1556-v22-draft-2026-02-v1": errors.append("selected pack identity")
        if reconciliation.get("selected_pack_sha256")!=sha(PACK): errors.append("pack hash is not bound by reconciliation")
        if reconciliation.get("literal_fixture_sha256")!=sha(LITERAL_ROWS): errors.append("literal fixture hash is not bound by reconciliation")
        if pack.get("schema")!="actinv-waste-draft-pack-1" or pack.get("id")!="us-nrc-nureg1556-v22-draft-2026-02-v1": errors.append("pack schema/id")
        if pack.get("source_status")!="draft_report_for_comment" or pack.get("table_id")!="8-5": errors.append("pack draft status/table")
        if pack.get("source_pdf_sha256")!="2deae800fc1a2ac3a7134481e81b75c90df98a1c9dade4393816719b55d27359": errors.append("February source PDF identity")
        if pack.get("source_text_sha256")!="ef1a98961cef3465461400395f384cb1296a8ead9e505ef6a53975041544f3ca": errors.append("February source text identity")
        if pack.get("ci_bq")!=37_000_000_000 or pack.get("year_s")!=31_557_600: errors.append("source unit constants")
        if pack!=mirror or PACK.read_bytes()!=MIRROR.read_bytes(): errors.append("pack/core mirror mismatch")
        if (literal.get("schema")!="actinv-waste-draft-rows-1" or literal.get("table_id")!="8-5"
                or literal.get("source_version")!="february_2026_draft_ML24092A377"):
            errors.append("literal row fixture identity")
        expected=oracle.public_rows()
        rows=literal.get("rows")
        if not isinstance(rows,list) or len(rows)!=25: errors.append("literal fixture must contain 25 rows")
        else:
            for i,(actual,want) in enumerate(zip(rows,expected)):
                for key in ("id","label","selector","members","applicability","unit","limits"):
                    if actual.get(key)!=want[key]: errors.append(f"literal row {i} {key} mismatch")
                if actual.get("basis_note")!=want["basis_note"]: errors.append(f"literal row {i} note mismatch")
        expected_pack=pack.get("rows")
        if not isinstance(expected_pack,list) or len(expected_pack)!=25: errors.append("pack must contain all 25 table rows")
        else:
            for i,(actual,want) in enumerate(zip(expected_pack,expected)):
                for key in ("id","label","selector","members","applicability","unit","limits"):
                    if actual.get(key)!=want[key]: errors.append(f"pack row {i} {key} mismatch")
                if not isinstance(actual.get("basis_note"),str) or not actual["basis_note"].startswith(want["basis_note"]):
                    errors.append(f"pack row {i} source note mismatch")
        for field,path in (("selected_source","february_2026"),("compared_source","march_2025")):
            source=reconciliation.get(field,{})
            excerpt=EXCERPTS[path]
            if source.get("excerpt_path")!=excerpt.relative_to(ROOT).as_posix() or source.get("excerpt_sha256")!=sha(excerpt):
                errors.append(f"{path} excerpt hash/path binding")
        manual=reconciliation.get("manual_pdf_review",{})
        if manual.get("performed") is not True or "G0 checks the pinned excerpt bytes" not in manual.get("automation_boundary",""):
            errors.append("manual PDF review boundary is absent")
        if manual.get("visually_checked_pdf_pages") != ["February 2026: 8-70, 8-71, Q-6", "March 2025: 8-71, 8-72"]:
            errors.append("manual PDF review page scope differs")
        if "CI does not download" not in manual.get("automation_boundary", "") and "does not download" not in manual.get("automation_boundary", ""):
            errors.append("automated full-PDF review claim boundary missing")
        comparison=reconciliation.get("comparison",{})
        if comparison.get("literal_row_count")!=25 or comparison.get("all_25_rows_match_between_versions") is not True:
            errors.append("cross-version row reconciliation")
        defects=reconciliation.get("printed_source_defects_preserved",[])
        defect_ids={x.get("id") for x in defects if isinstance(x,dict)}
        if defect_ids!={"D1","D2","D3","D4","D5"}: errors.append("printed source defects not all preserved")
        defect_text=" ".join(x.get("detail","") for x in defects if isinstance(x,dict))
        for phrase in ("460 Ci/m3","0.2/0.2/2 Ci/m3","Table 8-4","activity or is","Color Tolerance Charts and Tables"):
            if phrase.casefold() not in defect_text.casefold(): errors.append(f"source defect decision omits {phrase}")
        text_checks={
            "february_2026": ["Table 8-5","0.01 times","0.26 megabecquerel","reportable quantity","0.01 or more","Table 8-4"],
            "march_2025": ["Table 8-5","0.01 times","0.26 megabecquerel (MBq) per cubic centimeter","reportable quantity","0.01 or more","Table 8-4"],
        }
        for name,needles in text_checks.items():
            text=EXCERPTS[name].read_text(encoding="utf-8")
            normalized=_norm_text(text)
            for needle in needles:
                if _norm_text(needle) not in normalized: errors.append(f"{name} excerpt lacks source anchor {needle!r}")
        feb_rows,feb_errors=_parse_february_excerpt(EXCERPTS["february_2026"].read_text(encoding="utf-8"))
        errors.extend(feb_errors)
        if len(feb_rows)!=25: errors.append("February excerpt parser did not return all 25 rows")
        else:
            for i,(got,want) in enumerate(zip(feb_rows,expected)):
                if (got["id"],got["label"],got["unit"],got["limits"],got["basis_note"]) != (want["id"],want["label"],want["unit"],want["limits"],want["basis_note"]):
                    errors.append(f"February source row {i} values/label/unit/note differ")
        _march_rows,march_errors=_parse_march_excerpt(EXCERPTS["march_2025"].read_text(encoding="utf-8"))
        errors.extend(march_errors)
        # The source files are intentionally not fetched or opened as complete
        # PDFs by automated controls. Reconciliation must keep their pinned IDs.
        if reconciliation.get("selected_source",{}).get("adams")!="ML24092A377": errors.append("selected ADAMS identity")
        if reconciliation.get("compared_source",{}).get("adams")!="ML24295A002": errors.append("compared ADAMS identity")
    return {"pass":not errors,"row_count":25 if not errors else (len((literal or {}).get("rows",[])) if literal else 0),
            "column_checks":75 if not errors else None,"errors":errors,
            "excerpt_sha256":{name:sha(path) for name,path in EXCERPTS.items()},
            "pack_sha256":sha(PACK),"literal_rows_sha256":sha(LITERAL_ROWS)}


def _fixture_contract(fixture: dict) -> dict:
    errors=[]
    expected=oracle.generate_cases()
    if fixture.get("schema")!=oracle.CASE_SCHEMA: errors.append("case fixture schema")
    if fixture.get("source")!="synthetic protocol-declared whole-container activity maps; no evaluated nuclear data": errors.append("case fixture provenance")
    if fixture.get("cases")!=expected: errors.append("frozen cases differ from independent generator")
    ids=[case.get("id") for case in fixture.get("cases",[]) if isinstance(case,dict)]
    if len(ids)<CASE_COUNT_MIN or len(set(ids))!=len(ids): errors.append("case count/unique IDs")
    target_count=sum(len(case.get("outer",{}).get("waste_spec",{}).get("targets",[])) for case in fixture.get("cases",[]) if isinstance(case,dict))
    if target_count<TARGET_COUNT_MIN: errors.append("target count")
    required={"wac_below","wac_equal","wac_above","unlisted_both_below","unlisted_both_equal",
              "unlisted_both_above","rq_below","rq_equal","rq_above","rq_mixture_below",
              "rq_mixture_equal","rq_mixture_above","share_below","share_equal","share_above",
              "only_wac_true","only_c2_true","only_rq_true","only_share_true","all_presence_false",
              "wac_absent_indeterminate","wac_no_numeric_indeterminate","rq_missing_nuclide_indeterminate",
              "incomplete_inventory_share_unknown","required_h3_unknown","external_h3_once","zero_inventory",
              "volume_ci_m3","mass_nci_g","mesh_aggregation","short_group_and_named",
              "exact_five_year_not_group","alpha_group","no_limit_co60","no_limit_h3",
              "geometry_unequal_mass_volume","short_lt5_boundary_below","short_gt5_boundary_above",
              "draft_row_class_a_equal","draft_row_class_c_equal","no_limit_eu152_class_c",
              "no_limit_short_group_class_b","no_limit_short_group_class_c",
              "metal_nb94_literal","metal_c14_literal","cs137_literal_460","above_class_c",
              "metal_ni59_replacement","metal_ni63_replacement","part61_alpha_outside_draft_fixed8",
              "unknown_class_from_missing_metadata","other_waste_form","multi_target_steps"}
    if not required.issubset(set(ids)): errors.append("required population categories missing")
    by_id={case.get("id"):case for case in fixture.get("cases",[]) if isinstance(case,dict)}
    expected_classes={"draft_row_class_a_equal":"A","draft_row_class_c_equal":"C",
                      "no_limit_co60":"C","no_limit_h3":"C","no_limit_eu152_class_c":"C",
                      "no_limit_short_group_class_b":"B","no_limit_short_group_class_c":"C"}
    for case_id,want_class in expected_classes.items():
        case=by_id.get(case_id)
        if case is None: continue
        try: got=oracle.evaluate_target(case,1)["classification"]["class"]
        except (KeyError,ValueError,TypeError,ZeroDivisionError) as error:
            errors.append(f"{case_id}: independent expected-label arithmetic failed: {error}"); continue
        if got!=want_class: errors.append(f"{case_id}: nominal class premise {got} != {want_class}")
    for case_id,row_id,column in (("no_limit_co60","Co-60","C"),("no_limit_h3","H-3","C"),
        ("no_limit_eu152_class_c","Eu-152","C"),("no_limit_short_group_class_b","short_half_life_lt5y_sum","B"),
        ("no_limit_short_group_class_c","short_half_life_lt5y_sum","C")):
        case=by_id.get(case_id)
        if case is None: continue
        rows=oracle.evaluate_target(case,1)["rows"]
        target=next((r for r in rows if r["row_id"]==row_id),None)
        if target is None or target["column"]!=column or target["limit"] is not None:
            errors.append(f"{case_id}: intended no-limit {row_id}/{column} cell is not selected")
    return {"pass":not errors,"case_count":len(ids),"target_count":target_count,"errors":errors}


def _g0_base() -> dict:
    fixture=_json(FIXTURE) or {}
    fixture_check=_fixture_contract(fixture)
    source_review=_source_review()
    prior,prior_details,prior_ok=_prior_verdicts()
    historical=_historical_p110()
    pack_mirror=(sha(PACK)==sha(MIRROR) and PACK.exists() and MIRROR.exists() and PACK.read_bytes()==MIRROR.read_bytes())
    hashes={name:sha(ROOT/name) for name in CONTROL_FILES}
    g0={"schema":"actinv-p111-intrusion-screen-g0-1","phase":"P111","repair_rounds":0,
        "protocol_sha256":sha(PROTOCOL),"protocol_registered":_registered(),
        "pack_sha256":sha(PACK),"pack_mirror_identical":pack_mirror,
        "case_fixture_matches":fixture_check["pass"],"case_fixture_check":fixture_check,
        "source_review":source_review,"source_rows_checked":source_review["row_count"],
        "column_checks":source_review["column_checks"],"historical_p110_verified":historical,
        "prior_verdict_sha256":prior,"prior_verdict_details":prior_details,"prior_verdicts_match":prior_ok,
        "case_count":fixture_check["case_count"],"target_count":fixture_check["target_count"],
        "predicate_boundary_count":12,"control_sha256":hashes}
    g0["pass"]=bool(g0["protocol_registered"] and pack_mirror and g0["case_fixture_matches"]
                    and source_review["pass"] and historical and prior_ok
                    and g0["case_count"]>=CASE_COUNT_MIN and g0["target_count"]>=TARGET_COUNT_MIN
                    and all(hashes.values()))
    return g0


def g0(*,seal:bool=False,no_write:bool=False)->int:
    result=_g0_base()
    if no_write:
        persisted=_json(G0)
        equal=(persisted==result)
        result["persisted_seal_matches"]=equal
        result["pass"]=result["pass"] and equal
    elif seal:
        G0.parent.mkdir(parents=True,exist_ok=True)
        G0.write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(result,sort_keys=True,indent=2))
    return 0 if result.get("pass") is True else 1


def _close(actual:object,expected:object,rel:float=1e-10,abs_:float=1e-12)->bool:
    if isinstance(actual,bool) or isinstance(expected,bool): return False
    if not isinstance(actual,(int,float)) or not isinstance(expected,(int,float)): return False
    return math.isfinite(float(actual)) and math.isfinite(float(expected)) and abs(float(actual)-float(expected))<=max(abs_,rel*max(abs(float(actual)),abs(float(expected))))


def _invoke(argv:list[str]):
    """Use the coordinator-reviewed bounded terminate/kill/reap P105 runner."""
    return bounded._run(argv,cwd=ROOT,timeout_s=120)


def _reject_duplicate_pairs(pairs:list[tuple[str,object]])->dict:
    result={}
    for key,value in pairs:
        if key in result: raise ValueError(f"duplicate JSON key {key}")
        result[key]=value
    return result


def _eq_map(actual:object,expected:dict,label:str,errors:list[str])->None:
    if not isinstance(actual,dict) or set(actual)!=set(expected):
        errors.append(f"{label}: key set differs"); return
    for key,value in expected.items():
        got=actual[key]
        if isinstance(value,bool):
            if type(got) is not bool or got is not value: errors.append(f"{label}.{key}: boolean value differs")
        elif isinstance(value,float):
            if not _close(got,value): errors.append(f"{label}.{key}: numeric value differs")
        elif got!=value: errors.append(f"{label}.{key}: value differs")


def _validate_target(case:dict,expected:dict,actual:object,label:str)->list[str]:
    errors=[]
    if not isinstance(actual,dict): return [f"{label}: target is not an object"]
    component=case["outer"]["waste_spec"]["components"][0]
    properties=case["outer"]["waste_spec"].get("nuclide_properties",{})
    if not isinstance(properties,dict): properties={}
    active_unknown=any(name not in properties for name,value in expected["inventory_activity_bq"].items() if value>0)
    wanted_fields={
        "step":expected["step"],"t_s":expected["t_s"],
        "nominal_class":expected["classification"]["class"],
        "inventory_coverage":expected["coverage"],
        "unbounded_inventory_reasons":expected["unbounded_inventory_reasons"],
        "inventory_activity_bq":expected["inventory_activity_bq"],
        "external_tritium_activity_bq":expected["external_tritium_activity_bq"],
        "known_total_activity_bq":math.fsum(expected["inventory_activity_bq"].values()),
        "container_total_activity_bq":expected["container_total_activity_bq"],
        "container_denominator_complete":expected["container_denominator_complete"],
        "dot_rq_mixture_ratios":expected["dot_rq_mixture_ratios"],
        "dot_rq_mixture_ratio":expected["dot_rq_mixture_ratio"],
        "dot_rq_mixture_complete":expected["dot_rq_mixture_complete"],
        "assessment_indicator":expected["assessment_indicator"],
    }
    for key,want in wanted_fields.items():
        got=actual.get(key)
        if isinstance(want,dict): _eq_map(got,want,f"{label}.{key}",errors)
        elif isinstance(want,float):
            if not _close(got,want): errors.append(f"{label}.{key} differs")
        elif got!=want: errors.append(f"{label}.{key} differs")
    for key in ("container_denominator_complete","dot_rq_mixture_complete"):
        if type(actual.get(key)) is not bool or actual.get(key) is not expected[key]:
            errors.append(f"{label}.{key} must be a boolean with independent value")
    # Three-valued denominator evidence is separate from the caller's coverage
    # declaration; a known subtotal never substitutes for a whole container.
    missing=actual.get("denominator_missing_reasons")
    expected_complete=expected["container_denominator_complete"]
    if not isinstance(missing,list) or any(not isinstance(item,str) or not item for item in missing):
        errors.append(f"{label}: denominator missing-reason list malformed")
    elif expected_complete and missing:
        errors.append(f"{label}: complete denominator reports missing reasons")
    elif not expected_complete:
        if not missing: errors.append(f"{label}: incomplete denominator lacks reasons")
        reason_text=" ".join(missing).casefold()
        if expected["coverage"] in ("incomplete","unknown") and not any(token in reason_text for token in ("inventory","coverage","caller")):
            errors.append(f"{label}: caller coverage cause absent from denominator reasons")
        external=case["outer"]["waste_spec"]["components"][0].get("external_tritium",{})
        if isinstance(external,dict) and external.get("status")=="required" and not any(token in reason_text for token in ("h3","tritium","external")):
            errors.append(f"{label}: required external H3 cause absent from denominator reasons")
        properties=case["outer"]["waste_spec"].get("nuclide_properties",{})
        missing_props=[name for name,activity in expected["inventory_activity_bq"].items()
                       if activity>0 and name not in properties]
        for name in missing_props:
            if name.casefold() not in reason_text and not any(token in reason_text for token in ("property","metadata","nuclide")):
                errors.append(f"{label}: missing-property cause for {name} absent from denominator reasons")
    actual_nuclides=actual.get("nuclides")
    if not isinstance(actual_nuclides,list) or len(actual_nuclides)!=len(expected["nuclides"]):
        errors.append(f"{label}: per-nuclide count differs")
    else:
        by_name={item.get("nuclide"):item for item in actual_nuclides if isinstance(item,dict)}
        want_names={item["nuclide"] for item in expected["nuclides"]}
        if len(by_name)!=len(actual_nuclides) or set(by_name)!=want_names: errors.append(f"{label}: per-nuclide identities differ")
        for want in expected["nuclides"]:
            got=by_name.get(want["nuclide"])
            if got is None: continue
            if not _close(got.get("activity_bq"),want["activity_bq"]): errors.append(f"{label}: {want['nuclide']} activity differs")
            if not _close(got.get("concentration_bq_cm3"),want["concentration_bq_cm3"]): errors.append(f"{label}: {want['nuclide']} concentration differs")
            share=want["activity_bq"]/expected["container_total_activity_bq"] if expected["container_total_activity_bq"] else None
            if share is not None and not _close(got.get("activity_share"),share): errors.append(f"{label}: {want['nuclide']} share differs")
            if share is None and got.get("activity_share") is not None: errors.append(f"{label}: {want['nuclide']} share must remain unknown")
            if got.get("presence")!=want["presence"]: errors.append(f"{label}: {want['nuclide']} presence differs")
            listing=_expected_predicate_evidence(case,expected,want,"unlisted_concentration")
            if got.get("part61_listing")!=listing["part61_listing"]: errors.append(f"{label}: {want['nuclide']} Part 61 listing differs")
            if got.get("wac_listing")!=listing["wac_listing"]: errors.append(f"{label}: {want['nuclide']} WAC listing differs")
            wac_evidence=_expected_predicate_evidence(case,expected,want,"wac_fraction")
            expected_wac_concentration=(None if wac_evidence["concentration"] is None else
                                        {"value":wac_evidence["concentration"],"unit":wac_evidence["unit"]})
            if expected_wac_concentration is None:
                if got.get("wac_concentration") is not None: errors.append(f"{label}: {want['nuclide']} WAC concentration differs")
            else:
                _eq_map(got.get("wac_concentration"),expected_wac_concentration,f"{label}.{want['nuclide']}.wac_concentration",errors)
            predicates=got.get("predicates")
            if not isinstance(predicates,dict): errors.append(f"{label}: {want['nuclide']} predicates missing"); continue
            for predicate_name,predicate_want in want["predicates"].items():
                actual_pred=predicates.get(predicate_name)
                if not isinstance(actual_pred,dict): errors.append(f"{label}: predicate {predicate_name} absent"); continue
                if actual_pred.get("status")!=predicate_want["value"]: errors.append(f"{label}: predicate {predicate_name} status differs")
                reasons=actual_pred.get("reasons")
                if not isinstance(reasons,list) or not reasons: errors.append(f"{label}: predicate {predicate_name} reason evidence missing")
                evidence=actual_pred.get("evidence")
                expected_evidence=_expected_predicate_evidence(case,expected,want,predicate_name)
                _eq_map(evidence,expected_evidence,f"{label}.{want['nuclide']}.{predicate_name}.evidence",errors)
                if not isinstance(actual_pred.get("reasons"),list) or not actual_pred["reasons"]:
                    errors.append(f"{label}: predicate {predicate_name} reasons absent")
    if not expected["nuclides"] and actual_nuclides not in ([],None): errors.append(f"{label}: zero inventory should have no nuclide predicates")
    if not active_unknown and actual.get("container_denominator_complete") is not expected["container_denominator_complete"]:
        errors.append(f"{label}: denominator completeness contradicts active property coverage")
    actual_rows=actual.get("rows")
    expected_rows=expected["rows"]
    if not isinstance(actual_rows,list) or len(actual_rows)!=len(expected_rows):
        errors.append(f"{label}: Table 8-5 row count differs")
    else:
        by_id={row.get("row_id"):row for row in actual_rows if isinstance(row,dict)}
        if len(by_id)!=len(actual_rows) or set(by_id)!={row["row_id"] for row in expected_rows}:
            errors.append(f"{label}: Table 8-5 row identities differ")
        for want in expected_rows:
            got=by_id.get(want["row_id"])
            if got is None: continue
            for field,expected_value in (("members",want["members"]),("unit",want["unit"]),
                ("selected_class",want["column"]),("limit",want["limit"]),
                ("membership_complete",want["membership_complete"]),("applicability",want["presence"]),
                ("relation",want["relation"]),("assessment_indicator",want["assessment_indicator"])):
                if got.get(field)!=expected_value: errors.append(f"{label}: {want['row_id']} {field} differs")
            if type(got.get("membership_complete")) is not bool or got.get("membership_complete") is not want["membership_complete"]:
                errors.append(f"{label}: {want['row_id']} membership_complete must be boolean")
            if not _close(got.get("known_concentration"),want["concentration"]): errors.append(f"{label}: {want['row_id']} concentration differs")
            if not _close(got.get("known_activity_bq"),want["activity_bq"]): errors.append(f"{label}: {want['row_id']} activity differs")
            contributions=got.get("contributions_bq")
            expected_contributions={name:expected["inventory_activity_bq"][name] for name in want["members"] if name in expected["inventory_activity_bq"]}
            _eq_map(contributions,expected_contributions,f"{label}.{want['row_id']}.contributions_bq",errors)
    return errors


def _expected_predicate_evidence(case:dict,target:dict,nuclide:dict,name:str)->dict:
    from decimal import Decimal, localcontext
    outer=case["outer"]; component=outer["waste_spec"]["components"][0]
    activity=Decimal(str(nuclide["activity_bq"]))
    mass=Decimal(str(component["mass_g"])); volume=Decimal(str(component["displaced_volume_cm3"]))
    properties=outer["waste_spec"].get("nuclide_properties",{})
    wac=outer.get("site_wac"); rq=outer.get("dot_rq")
    wac_entries=wac.get("nuclides",{}) if isinstance(wac,dict) else {}
    rq_entries=rq.get("nuclides",{}) if isinstance(rq,dict) else {}
    wac_entry=wac_entries.get(nuclide["nuclide"]) if isinstance(wac_entries,dict) else None
    rq_value=rq_entries.get(nuclide["nuclide"]) if isinstance(rq_entries,dict) else None
    with localcontext() as ctx:
        ctx.prec=80
        if name=="wac_fraction":
            if isinstance(wac_entry,dict) and wac_entry.get("status")=="listed" and isinstance(wac_entry.get("limit"),dict):
                unit=wac_entry["limit"]["unit"]; limit=Decimal(str(wac_entry["limit"]["value"]))
                concentration=(activity/(volume*Decimal(str(oracle.CI_BQ_CM3))) if unit=="Ci/m3"
                               else activity/(mass*Decimal(str(oracle.NCI_BQ_G))))
                return {"concentration":float(concentration),"unit":unit,"wac_limit":float(limit),
                        "threshold":float(limit*Decimal("0.01")),"source":wac.get("source")}
            return {"concentration":None,"unit":None,"wac_limit":None,"threshold":None,
                    "source":wac.get("source") if isinstance(wac,dict) else None}
        if name=="unlisted_concentration":
            concentration=float(activity/volume)
            p61=oracle._part61_membership(nuclide["nuclide"],properties,component["waste_type"])
            part61="listed" if p61 is True else "unlisted" if p61 is False else "unknown"
            if wac is None: wac_listing="unknown"
            elif isinstance(wac_entry,dict) and wac_entry.get("status") in ("listed","listed_no_numeric_limit"):
                wac_listing="listed"
            elif isinstance(wac_entry,dict) and wac_entry.get("status")=="unlisted": wac_listing="unlisted"
            elif wac.get("membership_coverage")=="complete": wac_listing="unlisted"
            else: wac_listing="unknown"
            return {"concentration_bq_cm3":concentration,"threshold_bq_cm3":260000.0,
                    "part61_listing":part61,"wac_listing":wac_listing,
                    "interpretation":"consensus_either_or_both"}
        if name=="dot_rq":
            individual=(Decimal(str(rq_value)) if isinstance(rq_value,(int,float)) and not isinstance(rq_value,bool) else None)
            ratio=float(activity/individual) if individual is not None and individual>0 else None
            return {"activity_bq":float(activity),"individual_rq_bq":float(individual) if individual is not None else None,
                    "individual_ratio":ratio,"known_mixture_ratio":target["dot_rq_mixture_ratio"],
                    "mixture_complete":target["dot_rq_mixture_complete"],
                    "source":rq.get("source") if isinstance(rq,dict) else None}
        if name=="container_share":
            total=target["container_total_activity_bq"]
            complete=target["container_denominator_complete"]
            return {"activity_bq":float(activity),"container_total_activity_bq":total,
                    "threshold_bq":0.01*total if total is not None else None,
                    "container_denominator_complete":complete}
    raise ValueError(f"unknown predicate {name}")


def validate_report(case:dict,actual:object,nominal:object,input_sha:str,inner_json:str)->list[str]:
    errors=[]
    if not isinstance(actual,dict): return ["result is not an object"]
    if actual.get("schema")!="actinv-waste-intrusion-screen-result-1": errors.append("result schema")
    if actual.get("input_sha256")!=input_sha: errors.append("outer input SHA mismatch")
    generated=actual.get("generated_waste_spec_json")
    if not isinstance(generated,str): errors.append("embedded nominal spec absent")
    else:
        try:
            if json.loads(generated)!=json.loads(inner_json): errors.append("embedded nominal spec identity mismatch")
        except ValueError: errors.append("embedded nominal spec is malformed")
        if actual.get("generated_waste_spec_sha256")!=sha_bytes(generated.encode()): errors.append("embedded nominal spec SHA mismatch")
    if actual.get("classification")!=nominal: errors.append("nominal classification changed from waste command")
    screen=actual.get("draft_intrusion_screen")
    if not isinstance(screen,dict): return errors+["draft screen block missing"]
    for key,value in (("package_basis","single_component_container"),("waste_form",case["outer"]["waste_form"])):
        if screen.get(key)!=value: errors.append(f"screen {key} differs")
    if screen.get("inventory_coverage")!=case["outer"]["inventory_coverage"]:
        errors.append("screen inventory coverage declaration differs")
    if screen.get("unbounded_inventory_reasons")!=case["outer"]["unbounded_inventory_reasons"]:
        errors.append("screen caller inventory reasons differ")
    wac=case["outer"].get("site_wac"); rq=case["outer"].get("dot_rq")
    expected_wac={"supplied":isinstance(wac,dict),"source":wac.get("source") if isinstance(wac,dict) else None,
                  "membership_coverage":wac.get("membership_coverage") if isinstance(wac,dict) else None}
    expected_rq={"supplied":isinstance(rq,dict),"source":rq.get("source") if isinstance(rq,dict) else None,
                 "coverage":rq.get("coverage") if isinstance(rq,dict) else None,
                 "unit":"Bq","applicability_basis":"caller_declared"}
    declaration_errors=[]
    _eq_map(screen.get("site_wac"),expected_wac,"screen.site_wac",declaration_errors)
    _eq_map(screen.get("dot_rq"),expected_rq,"screen.dot_rq",declaration_errors)
    errors.extend(declaration_errors)
    expected_interpretations={"presence_or":"three_valued_any_true",
        "criterion_1":"concentration_strictly_over_1_percent_site_wac",
        "criterion_2":"consensus_either_or_both",
        "criterion_3":"individual_rq_inclusive_mixture_only_indeterminate",
        "criterion_4":"complete_container_share_at_least_1_percent",
        "row_equality":"indeterminate","short_group":"strict_lt5_julian_years_all_named_included",
        "alpha_group":"fixed_eight","row_arithmetic":"all_known_matching_inventory",
        "selected_column":"computed_nominal_class_only","metal_rows":"paired_variant_replacement",
        "unlisted_form":"review_indicated","source_gaps":"visible_when_presence_true"}
    if screen.get("interpretations")!=expected_interpretations: errors.append("screen interpretation identifiers differ")
    pack=screen.get("draft_pack")
    if not isinstance(pack,dict): errors.append("draft pack identity absent")
    else:
        expected_pack=_json(PACK) or {}
        identity_fields=("id","version","source_title","source_adams","source_status","source_url",
            "source_page","source_as_of","source_pdf_sha256","source_text_sha256","table_id",
            "table_caption","source_locations","row_interpretations","printed_defects_preserved")
        for field in identity_fields:
            if pack.get(field)!=expected_pack.get(field): errors.append(f"draft pack identity {field} differs")
        if pack.get("sha256")!=sha(PACK): errors.append("draft pack identity hash differs")
    if screen.get("disposal_acceptance")!="not_assessed" or screen.get("intrusion_dose")!="not_calculated" or screen.get("legal_compliance")!="not_determined":
        errors.append("screen qualification boundary fields differ")
    expected_targets=[oracle.evaluate_target(case,int(step)) for step in case["outer"]["waste_spec"]["targets"]]
    got_targets=screen.get("targets")
    if not isinstance(got_targets,list) or len(got_targets)!=len(expected_targets): return errors+["screen target count differs"]
    by_step={row.get("step"):row for row in got_targets if isinstance(row,dict)}
    if len(by_step)!=len(got_targets) or set(by_step)!={item["step"] for item in expected_targets}:
        errors.append("screen target identities differ")
    for expected in expected_targets:
        errors.extend(_validate_target(case,expected,by_step.get(expected["step"]),f"step {expected['step']}"))
    return errors


def _case_files(case:dict,work:Path,index:int,*,output_tag:str="one") -> tuple[Path,Path,Path,str,bytes]:
    folder=work/f"case-{index:03d}-{case['id']}"
    folder.mkdir(parents=True,exist_ok=True)
    outer=copy.deepcopy(case["outer"])
    waste_spec=outer["waste_spec"]
    run_path=folder/"case-input.json"
    if case.get("mesh"):
        # Native mesh input is record-tagged NDJSON with a single cell result
        # shape and matching membership masses in the component spec.
        mesh_lines=[json.dumps({"record":"header","schema":"actinv-mesh-result-1",
                                "cell_count":len(case["mesh_cells"])},separators=(",",":"))]
        for cell in case["mesh_cells"]:
            rec={"record":"cell","id":cell["id"],"result":{"entry_point":"cli","mode":"coupled",
                 "steps":[{"step":1,"t_s":0.0,"activity_Bq_per_g":cell["activity_Bq_per_g"]}]}}
            mesh_lines.append(json.dumps(rec,separators=(",",":")))
        mesh_lines.append(json.dumps({"record":"footer","cell_count":len(case["mesh_cells"])},separators=(",",":")))
        run_bytes=("\n".join(mesh_lines)+"\n").encode()
        component=waste_spec["components"][0]
        component["cells"]=[{"id":cell["id"],"mass_g":cell["mass_g"]} for cell in case["mesh_cells"]]
    else:
        run_bytes=(json.dumps(case["run_result"],sort_keys=True,separators=(",",":"))+"\n").encode()
    run_path.write_bytes(run_bytes)
    inner_json=json.dumps(waste_spec,sort_keys=True,separators=(",",":"))
    spec_path=folder/"spec.json"
    spec_bytes=(json.dumps(outer,sort_keys=True,separators=(",",":"))+"\n").encode()
    spec_path.write_bytes(spec_bytes)
    inner_path=folder/"nominal-waste-spec.json"
    inner_path.write_text(inner_json+"\n",encoding="utf-8")
    out=folder/f"screen-{output_tag}.json"
    nominal=folder/f"nominal-{output_tag}.json"
    return spec_path,inner_path,out,inner_json,spec_bytes


def _run_one(case:dict,work:Path,index:int)->tuple[dict,list[str],dict]:
    spec_path,inner_path,screen_path,inner_json,spec_bytes=_case_files(case,work,index)
    nominal_path=work/f"case-{index:03d}-{case['id']}"/"nominal.json"
    try: screen_cmd=_invoke([str(ACTINV),"waste","intrusion-screen",str(spec_path),str(screen_path)])
    except RuntimeError as error: return {},[f"{case['id']}: bounded screen command failed: {error}"],{}
    if screen_cmd.returncode!=0: return {},[f"{case['id']}: screen command failed: {screen_cmd.stderr[-500:]}"],{}
    try:
        actual=json.loads(screen_path.read_text())
    except (OSError,ValueError) as error: return {},[f"{case['id']}: output JSON invalid: {error}"],{}
    generated=actual.get("generated_waste_spec_json") if isinstance(actual,dict) else None
    if not isinstance(generated,str): return actual,[f"{case['id']}: generated nominal waste spec is absent"],{}
    try:
        expected_inner=json.loads(inner_json,object_pairs_hook=_reject_duplicate_pairs)
        emitted_inner=json.loads(generated,object_pairs_hook=_reject_duplicate_pairs)
    except ValueError as error: return actual,[f"{case['id']}: generated or expected nominal spec JSON invalid: {error}"],{}
    if emitted_inner!=expected_inner:
        return actual,[f"{case['id']}: generated nominal spec differs from frozen request"],{}
    generated_bytes=generated.encode("utf-8")
    if actual.get("generated_waste_spec_sha256")!=sha_bytes(generated_bytes):
        return actual,[f"{case['id']}: generated nominal spec SHA differs from exact emitted bytes"],{}
    # Reuse the screener's exact inner bytes to establish the unchanged nominal
    # report, avoiding serializer-dependent lexical comparisons.
    inner_path.write_bytes(generated_bytes)
    try: nominal_cmd=_invoke([str(ACTINV),"waste",str(inner_path),str(nominal_path)])
    except RuntimeError as error: return actual,[f"{case['id']}: bounded nominal command failed: {error}"],{}
    if nominal_cmd.returncode!=0: return actual,[f"{case['id']}: nominal waste command failed: {nominal_cmd.stderr[-500:]}"],{}
    try: nominal=json.loads(nominal_path.read_text())
    except (OSError,ValueError) as error: return actual,[f"{case['id']}: nominal output JSON invalid: {error}"],{}
    input_sha=sha_bytes(spec_path.read_bytes())
    errors=validate_report(case,actual,nominal,input_sha,inner_json)
    evidence={"id":case["id"],"input_sha256":input_sha,"output_sha256":sha(screen_path),
              "generated_waste_spec_sha256":sha_bytes(generated_bytes),
              "target_steps":[int(x) for x in case["outer"]["waste_spec"]["targets"]],
              "assessment_indicators":[x.get("assessment_indicator") for x in actual.get("draft_intrusion_screen",{}).get("targets",[]) if isinstance(x,dict)]}
    return actual,errors,evidence


def _mutate_output(actual:dict,case:dict)->dict[str,dict]:
    mutations={}
    screen=actual.get("draft_intrusion_screen",{})
    targets=screen.get("targets",[])
    if not targets: return mutations
    target=targets[0]
    nuclides=target.get("nuclides",[])
    rows=target.get("rows",[])
    def add(name,fn):
        changed=copy.deepcopy(actual); fn(changed)
        if changed==actual: raise AssertionError(f"no-op mutation: {name}")
        mutations[name]=changed
    add("wrong_outer_sha",lambda x:x.__setitem__("input_sha256","0"*64))
    add("wrong_inner_sha",lambda x:x.__setitem__("generated_waste_spec_sha256","0"*64))
    add("nominal_changed",lambda x:x.__setitem__("classification",{}))
    add("wrong_indicator",lambda x:x["draft_intrusion_screen"]["targets"][0].__setitem__("assessment_indicator",{"review_indicated":"indeterminate","indeterminate":"not_indicated_by_implemented_checks","not_indicated_by_implemented_checks":"review_indicated"}.get(x["draft_intrusion_screen"]["targets"][0].get("assessment_indicator"),"review_indicated")))
    add("wrong_nominal_class",lambda x:x["draft_intrusion_screen"]["targets"][0].__setitem__("nominal_class",{"A":"B","B":"C","C":"A","unknown":"A","above_class_c":"C"}.get(x["draft_intrusion_screen"]["targets"][0].get("nominal_class"),"A")))
    add("wrong_container_total",lambda x:x["draft_intrusion_screen"]["targets"][0].__setitem__("container_total_activity_bq",123456.789))
    add("wrong_known_total",lambda x:x["draft_intrusion_screen"]["targets"][0].__setitem__("known_total_activity_bq",123456.789))
    add("wrong_denominator_complete",lambda x:x["draft_intrusion_screen"]["targets"][0].__setitem__("container_denominator_complete",not x["draft_intrusion_screen"]["targets"][0].get("container_denominator_complete")))
    add("wrong_denominator_missing_reasons",lambda x:x["draft_intrusion_screen"]["targets"][0].__setitem__("denominator_missing_reasons",["mutated cause"]))
    add("wrong_inventory_coverage",lambda x:x["draft_intrusion_screen"]["targets"][0].__setitem__("inventory_coverage",{"complete":"incomplete","incomplete":"unknown","unknown":"complete"}.get(x["draft_intrusion_screen"]["targets"][0].get("inventory_coverage"),"unknown")))
    add("wrong_scope_qualification",lambda x:x["draft_intrusion_screen"].__setitem__("disposal_acceptance","accepted"))
    add("wrong_interpretation_policy",lambda x:x["draft_intrusion_screen"]["interpretations"].__setitem__("criterion_2","either_or_unlisted"))
    add("wrong_wac_declaration",lambda x:x["draft_intrusion_screen"]["site_wac"].__setitem__("supplied",not x["draft_intrusion_screen"]["site_wac"].get("supplied")))
    add("wrong_mixture_total",lambda x:x["draft_intrusion_screen"]["targets"][0].__setitem__("dot_rq_mixture_ratio",12345.0))
    if nuclides:
        n=nuclides[0]["nuclide"]
        add("duplicate_nuclide",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"].append(copy.deepcopy(x["draft_intrusion_screen"]["targets"][0]["nuclides"][0])))
        add("wrong_nuclide_activity",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].__setitem__("activity_bq",999.0))
        add("wrong_nuclide_presence",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].__setitem__("presence",{"true":"false","false":"indeterminate","indeterminate":"true"}.get(x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].get("presence"),"true")))
        add("wrong_part61_listing",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].__setitem__("part61_listing",{"listed":"unlisted","unlisted":"unknown","unknown":"listed"}.get(x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].get("part61_listing"),"unknown")))
        add("wrong_wac_listing",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].__setitem__("wac_listing",{"listed":"unlisted","unlisted":"unknown","unknown":"listed"}.get(x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].get("wac_listing"),"unknown")))
        add("wrong_wac_concentration",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].__setitem__("wac_concentration",{"value":9876.5,"unit":"Ci/m3"}))
        pred=nuclides[0].get("predicates",{})
        if pred:
            pkey=next(iter(pred))
            add("wrong_predicate_status",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][0]["predicates"][pkey].__setitem__("status",{"true":"false","false":"indeterminate","indeterminate":"true"}.get(x["draft_intrusion_screen"]["targets"][0]["nuclides"][0]["predicates"][pkey].get("status"),"true")))
            add("drop_predicate_reason",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][0]["predicates"][pkey].__setitem__("reasons",[]))
            add("drop_predicate_evidence",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][0]["predicates"][pkey].__setitem__("evidence",None))
        p_index=next((i for i,item in enumerate(nuclides) if item.get("nuclide")==n),0)
        add("wrong_presence_activity_evidence",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][p_index]["predicates"]["container_share"]["evidence"].__setitem__("activity_bq",98765.4321))
        add("wrong_rq_mixture_completeness_evidence",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][p_index]["predicates"]["dot_rq"]["evidence"].__setitem__("mixture_complete",not x["draft_intrusion_screen"]["targets"][0]["nuclides"][p_index]["predicates"]["dot_rq"]["evidence"].get("mixture_complete")))
        add("wrong_activity_share",lambda x:x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].__setitem__("activity_share",0.5 if x["draft_intrusion_screen"]["targets"][0]["nuclides"][0].get("activity_share")!=0.5 else 0.75))
    add("zero_case_inventory_map_mutation",lambda x:x["draft_intrusion_screen"]["targets"][0].__setitem__("inventory_activity_bq",{"mutated":1.0}))
    if rows:
        row=rows[0]
        add("wrong_row_limit",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("limit",9.876))
        add("wrong_row_concentration",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("known_concentration",9.876))
        add("wrong_row_relation",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("relation",{"above":"below","below":"above","at_limit":"below","unknown":"below","no_numeric_limit":"above"}.get(row.get("relation"),"above")))
        add("duplicate_table_row",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"].append(copy.deepcopy(x["draft_intrusion_screen"]["targets"][0]["rows"][0])))
        add("wrong_row_members",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("members",["mutated"]))
        add("wrong_row_unit",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("unit","Bq"))
        add("wrong_row_column",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("selected_class",{"A":"B","B":"C","C":"A",None:"A"}.get(row.get("selected_class"),"A")))
        add("wrong_row_indicator",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("assessment_indicator",{"true":"false","false":"indeterminate","indeterminate":"true","review_indicated":"not_indicated_by_implemented_checks","not_indicated_by_implemented_checks":"review_indicated"}.get(row.get("assessment_indicator"),"indeterminate")))
        add("wrong_row_applicability",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("applicability",{"true":"false","false":"indeterminate","indeterminate":"true"}.get(row.get("applicability"),"true")))
        add("wrong_row_membership_complete",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("membership_complete",not row.get("membership_complete")))
        add("wrong_row_contributions",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("contributions_bq",{"mutated":1.0}))
        add("wrong_row_activity",lambda x:x["draft_intrusion_screen"]["targets"][0]["rows"][0].__setitem__("known_activity_bq",999.0))
    return mutations


def _run_refusals(work:Path)->dict:
    # Every refusal starts with an existing output sentinel and proves it is
    # unchanged. Raw JSON strings exercise duplicate-key rejection pre-decode.
    base=oracle.generate_cases()[0]
    cases={"outer_schema":lambda x:x.__setitem__("schema","wrong"),
           "extra_outer":lambda x:x.__setitem__("surprise",True),
           "bad_pack":lambda x:x.__setitem__("draft_rules","missing"),
           "bad_package_basis":lambda x:x.__setitem__("package_basis","known_subtotal"),
           "bad_coverage":lambda x:x.__setitem__("inventory_coverage","partial"),
           "coverage_reason_on_complete":lambda x:x.__setitem__("unbounded_inventory_reasons",["x"]),
           "empty_incomplete_reason":lambda x:(x.__setitem__("inventory_coverage","incomplete"),x.__setitem__("unbounded_inventory_reasons",[])),
           "bad_form":lambda x:x.__setitem__("waste_form","concrete"),
           "invalid_form_waste_type":lambda x:x["waste_spec"]["components"][0].__setitem__("waste_type","activated"),
           "activated_metal_nonmetal_form":lambda x:(x.__setitem__("waste_form","equipment"),x["waste_spec"]["components"][0].__setitem__("waste_type","activated_metal")),
           "both_geometry":lambda x:x["waste_spec"]["components"][0].__setitem__("density_g_cm3",7.8),
           "zero_mass":lambda x:x["waste_spec"]["components"][0].__setitem__("mass_g",0),
           "missing_component":lambda x:x["waste_spec"].__setitem__("components",[]),
           "multiple_components":lambda x:x["waste_spec"]["components"].append(copy.deepcopy(x["waste_spec"]["components"][0])),
           "missing_step":lambda x:x["waste_spec"].__setitem__("targets",[99]),
           "duplicate_step":lambda x:x["waste_spec"].__setitem__("targets",[1,1]),
           "zero_step":lambda x:x["waste_spec"].__setitem__("targets",[0]),
           "bad_wac_source":lambda x:x.__setitem__("site_wac",{"membership_coverage":"complete","nuclides":{}}),
           "bad_wac_status":lambda x:x.__setitem__("site_wac",{"source":"x","membership_coverage":"complete","nuclides":{"C14":{"status":"n/a"}}}),
           "bad_wac_unit":lambda x:x.__setitem__("site_wac",{"source":"x","membership_coverage":"complete","nuclides":{"C14":{"status":"listed","limit":{"value":1,"unit":"Bq"}}}}),
           "negative_wac":lambda x:x.__setitem__("site_wac",{"source":"x","membership_coverage":"complete","nuclides":{"C14":{"status":"listed","limit":{"value":-1,"unit":"nCi/g"}}}}),
           "missing_rq_source":lambda x:x.__setitem__("dot_rq",{"coverage":"complete","nuclides":{}}),
           "zero_rq":lambda x:x.__setitem__("dot_rq",{"source":"x","coverage":"complete","nuclides":{"C14":0}}),
           "negative_rq":lambda x:x.__setitem__("dot_rq",{"source":"x","coverage":"complete","nuclides":{"C14":-1}}),
           "alias_wac_keys":lambda x:x.__setitem__("site_wac",{"source":"x","membership_coverage":"complete","nuclides":{"C14":{"status":"unlisted"},"C-14":{"status":"unlisted"}}}),
           "alias_rq_keys":lambda x:x.__setitem__("dot_rq",{"source":"x","coverage":"complete","nuclides":{"C14":1,"C-14":2}}),
           "bad_component_alias":lambda x:x["waste_spec"]["components"][0].__setitem__("id",""),
           "missing_input":lambda x:x["waste_spec"].__setitem__("input","does-not-exist.json"),
           "nonfinite_activity":lambda x:None,
           "bad_mesh_cells":lambda x:None,
           "mesh_cell_cap":lambda x:x["waste_spec"]["components"][0].__setitem__("cells",[{"id":f"cell-{i}","mass_g":1.0/129} for i in range(129)]),
           "bad_external_source":lambda x:x["waste_spec"]["components"][0].__setitem__("external_tritium",{"status":"declared","source":"","excludes_activation":True,"activity_bq":{"1":1}}),
           "bad_external_exclusion":lambda x:x["waste_spec"]["components"][0].__setitem__("external_tritium",{"status":"declared","source":"x","excludes_activation":False,"activity_bq":{"1":1}}),
           "bad_external_status":lambda x:x["waste_spec"]["components"][0].__setitem__("external_tritium",{"status":"sometimes"}),
           "duplicate_properties_alias":lambda x:x["waste_spec"]["nuclide_properties"].update({"C-14":copy.deepcopy(x["waste_spec"]["nuclide_properties"]["C14"])}),
           "negative_time":lambda x:None,
           "invalid_late_target":lambda x:x["waste_spec"].__setitem__("targets",[1,2]),
           "outer_size_cap":lambda x:x.__setitem__("unbounded_inventory_reasons",["x"* (9*1024*1024)]),
           "property_count_cap":lambda x:x["waste_spec"].__setitem__("nuclide_properties",{f"Fe{i}":{"z":26,"half_life_s":1e30,"alpha_emitting":False} for i in range(1,1026)}),
           "zero_half_life":lambda x:x["waste_spec"]["nuclide_properties"]["C14"].__setitem__("half_life_s",0),
           "activity_mass_overflow":lambda x:x["waste_spec"]["components"][0].__setitem__("mass_g",2.0),
           "concentration_overflow":lambda x:x["waste_spec"]["components"][0].__setitem__("displaced_volume_cm3",1e-300),
           "rq_ratio_overflow":lambda x:x.__setitem__("dot_rq",{"source":"synthetic","coverage":"complete","nuclides":{"C14":1e-300}}),
           "nonfinite_wac":lambda x:x.__setitem__("site_wac",{"source":"synthetic","membership_coverage":"complete","nuclides":{"C14":{"status":"listed","limit":{"value":1.0,"unit":"nCi/g"}}}}),
           "nonfinite_rq":lambda x:x.__setitem__("dot_rq",{"source":"synthetic","coverage":"complete","nuclides":{"C14":1.0}})}
    outcomes={}
    for index,(name,mutate) in enumerate(cases.items()):
        folder=work/f"refusal-{index:02d}-{name}"; folder.mkdir(parents=True,exist_ok=True)
        outer=copy.deepcopy(base["outer"]); mutate(outer)
        raw=json.dumps(outer,separators=(",",":"),allow_nan=False)+"\n"
        if name=="nonfinite_activity":
            outer["waste_spec"]["input"]="invalid-input.json"
            raw_path=folder/"spec.json"; raw_path.write_text(json.dumps(outer,separators=(",",":"))+"\n")
            (folder/"invalid-input.json").write_text('{"entry_point":"cli","mode":"coupled","steps":[{"step":1,"t_s":0,"activity_Bq_per_g":{"C14":1e999}}]}\n')
        elif name=="bad_mesh_cells":
            outer["waste_spec"]["input"]="invalid-input.json"
            raw_path=folder/"spec.json"; raw_path.write_text(json.dumps(outer,separators=(",",":"))+"\n")
            (folder/"invalid-input.json").write_text('{"header":{"schema":"actinv-mesh-result-1"}\n{"cell":{"id":"x","result":{"steps":[]}}}\n{"footer":{"cell_count":1}}\n')
        elif name=="mesh_cell_cap":
            outer["waste_spec"]["input"]="invalid-input.ndjson"
            raw_path=folder/"spec.json"; raw_path.write_text(json.dumps(outer,separators=(",",":"))+"\n",encoding="utf-8")
            lines=[json.dumps({"record":"header","schema":"actinv-mesh-result-1","cell_count":129},separators=(",",":"))]
            for i in range(129):
                lines.append(json.dumps({"record":"cell","id":f"cell-{i}","result":{"entry_point":"cli","mode":"coupled","steps":[{"step":1,"t_s":0,"activity_Bq_per_g":{"C14":1}}]}},separators=(",",":")))
            lines.append(json.dumps({"record":"footer","cell_count":129},separators=(",",":")))
            (folder/"invalid-input.ndjson").write_text("\n".join(lines)+"\n",encoding="utf-8")
        elif name=="negative_time":
            outer["waste_spec"]["input"]="invalid-input.json"
            raw_path=folder/"spec.json"; raw_path.write_text(json.dumps(outer,separators=(",",":"))+"\n")
            (folder/"invalid-input.json").write_text('{"entry_point":"cli","mode":"coupled","steps":[{"step":1,"t_s":-1,"activity_Bq_per_g":{"C14":1}}]}\n')
        elif name=="invalid_late_target":
            outer["waste_spec"]["targets"]=[1,2]
            outer["waste_spec"]["input"]="invalid-input.json"
            raw_path=folder/"spec.json"; raw_path.write_text(json.dumps(outer,separators=(",",":"))+"\n")
            (folder/"invalid-input.json").write_text('{"entry_point":"cli","mode":"coupled","steps":[{"step":1,"t_s":0,"activity_Bq_per_g":{"C14":1}}]}\n')
        elif name=="missing_input":
            outer["waste_spec"]["input"]="missing.json"
            raw_path=folder/"spec.json"; raw_path.write_text(json.dumps(outer,separators=(",",":"))+"\n",encoding="utf-8")
        elif name in ("activity_mass_overflow","concentration_overflow","rq_ratio_overflow"):
            outer["waste_spec"]["input"]="invalid-input.json"
            raw_path=folder/"spec.json"; raw_path.write_text(json.dumps(outer,separators=(",",":"))+"\n",encoding="utf-8")
            run=copy.deepcopy(base["run_result"])
            run["steps"][0]["activity_Bq_per_g"]={"C14":1e308}
            (folder/"invalid-input.json").write_text(json.dumps(run,separators=(",",":"))+"\n",encoding="utf-8")
        elif name in ("nonfinite_wac","nonfinite_rq"):
            outer["waste_spec"]["input"]="case-input.json"
            raw=json.dumps(outer,separators=(",",":"))
            token='"value":1.0' if name=="nonfinite_wac" else '"C14":1.0'
            replacement='"value":1e999' if name=="nonfinite_wac" else '"C14":1e999'
            raw_path=folder/"spec.json"; raw_path.write_text(raw.replace(token,replacement,1)+"\n",encoding="utf-8")
            (folder/"case-input.json").write_text(json.dumps(base["run_result"],separators=(",",":"))+"\n",encoding="utf-8")
        else:
            outer["waste_spec"]["input"]="case-input.json"
            raw_path=folder/"spec.json"; raw_path.write_text(raw,encoding="utf-8")
            (folder/"case-input.json").write_text(json.dumps(base["run_result"],separators=(",",":"))+"\n",encoding="utf-8")
        out=folder/"sentinel.json"; sentinel=b"P111-REFUSAL-SENTINEL\n"; out.write_bytes(sentinel)
        try:
            proc=_invoke([str(ACTINV),"waste","intrusion-screen",str(raw_path),str(out)])
            outcomes[name]=proc.returncode!=0 and out.read_bytes()==sentinel
        except RuntimeError:
            outcomes[name]=False
    # Raw duplicate probes keep every other field valid, so they isolate the
    # recursive duplicate-key rule rather than merely testing required fields.
    def raw_duplicate(name:str,outer_text:str,input_text:str|None=None)->None:
        folder=work/f"refusal-{name}"; folder.mkdir(parents=True,exist_ok=True)
        spec_path=folder/"spec.json"; spec_path.write_text(outer_text,encoding="utf-8")
        if input_text is not None:
            try: input_name=json.loads(outer_text)["waste_spec"]["input"]
            except (ValueError,KeyError,TypeError): input_name="case-input.json"
            input_path=folder/input_name
            input_path.parent.mkdir(parents=True,exist_ok=True)
            input_path.write_text(input_text,encoding="utf-8")
        output=folder/"sentinel.json"; sentinel=b"P111-REFUSAL-SENTINEL\n"; output.write_bytes(sentinel)
        try:
            proc=_invoke([str(ACTINV),"waste","intrusion-screen",str(spec_path),str(output)])
            outcomes[name]=proc.returncode!=0 and output.read_bytes()==sentinel
        except RuntimeError: outcomes[name]=False
    valid=copy.deepcopy(base["outer"]); valid["waste_spec"]["input"]="case-input.json"
    valid_raw=json.dumps(valid,separators=(",",":"))+"\n"
    normal_input=json.dumps(base["run_result"],separators=(",",":"))+"\n"
    duplicated_outer=valid_raw.replace('"schema":"actinv-waste-intrusion-screen-spec-1",',
        '"schema":"actinv-waste-intrusion-screen-spec-1","schema":"actinv-waste-intrusion-screen-spec-1",',1)
    raw_duplicate("raw_duplicate_outer_key",duplicated_outer,normal_input)
    duplicated_nested=valid_raw.replace('"rules":"us-nrc-10cfr61.55-v1",',
        '"rules":"us-nrc-10cfr61.55-v1","rules":"us-nrc-10cfr61.55-v1",',1)
    raw_duplicate("raw_duplicate_embedded_waste_key",duplicated_nested,normal_input)
    duplicated_input='{"entry_point":"cli","mode":"coupled","steps":[{"step":1,"step":1,"t_s":0,"activity_Bq_per_g":{"C14":1}}]}\n'
    raw_duplicate("raw_duplicate_native_input_key",valid_raw,duplicated_input)
    alias_input='{"entry_point":"cli","mode":"coupled","steps":[{"step":1,"t_s":0,"activity_Bq_per_g":{"C14":1,"C-14":2}}]}\n'
    raw_duplicate("canonical_alias_activity_collision",valid_raw,alias_input)
    wac_duplicate=valid_raw.replace('"C14":{"status":"listed"',
        '"C14":{"status":"listed"',1)
    # Valid request with one repeated nested WAC map key.
    wac_duplicate=valid_raw.replace('"nuclides":{"C14":', '"nuclides":{"C14":{"status":"listed","limit":{"value":100.0,"unit":"nCi/g"}},"C14":',1)
    raw_duplicate("raw_duplicate_wac_nuclide_key",wac_duplicate,normal_input)
    rq_base=copy.deepcopy(valid)
    rq_base["dot_rq"]={"source":"synthetic RQ","coverage":"complete","nuclides":{"C14":100.0}}
    rq_raw=json.dumps(rq_base,separators=(",",":"))+"\n"
    rq_duplicate=rq_raw.replace('"nuclides":{"C14":100.0}', '"nuclides":{"C14":100.0,"C14":200.0}',1)
    raw_duplicate("raw_duplicate_rq_nuclide_key",rq_duplicate,normal_input)
    mesh_case=next(item for item in oracle.generate_cases() if item["id"]=="mesh_aggregation")
    mesh_outer=copy.deepcopy(mesh_case["outer"])
    mesh_outer["waste_spec"]["input"]="case-input.ndjson"
    mesh_outer["waste_spec"]["components"][0]["cells"]=[{"id":cell["id"],"mass_g":cell["mass_g"]} for cell in mesh_case["mesh_cells"]]
    mesh_raw=json.dumps(mesh_outer,separators=(",",":"))+"\n"
    duplicate_mesh=('{"record":"header","schema":"actinv-mesh-result-1","schema":"actinv-mesh-result-1","cell_count":2}\n'
        '{"record":"cell","id":"cell-a","result":{"entry_point":"cli","mode":"coupled","steps":[{"step":1,"t_s":0,"activity_Bq_per_g":{"C14":60}}]}}\n'
        '{"record":"cell","id":"cell-b","result":{"entry_point":"cli","mode":"coupled","steps":[{"step":1,"t_s":0,"activity_Bq_per_g":{"C14":20}}]}}\n'
        '{"record":"footer","cell_count":2}\n')
    raw_duplicate("raw_duplicate_mesh_record_key",mesh_raw,duplicate_mesh)
    return {"pass":len(outcomes)>=30 and all(outcomes.values()),"checks":outcomes}


def run_g1(*,no_write:bool=False)->int:
    fixture=_json(FIXTURE) or {}; contract=_fixture_contract(fixture)
    work=ROOT/"target/p111-controls"
    work.mkdir(parents=True,exist_ok=True)
    failures=[]; evidence=[]; baseline={}; actuals={}
    cases=fixture.get("cases",[]) if isinstance(fixture.get("cases"),list) else []
    independent_count=0
    for index,case in enumerate(cases):
        actual,errors,proof=_run_one(case,work,index)
        failures.extend(errors); evidence.append(proof)
        actuals[case["id"]]=actual
        independent_count+=len(case["outer"]["waste_spec"]["targets"])
    # Sensitivity probes are checked against the independent expected arithmetic,
    # never against a mutated report field as its own oracle.
    mutations={}
    for case in cases:
        actual=actuals.get(case["id"])
        if not actual: continue
        _spec_path,_inner_path,_out,inner_json,_spec_bytes=_case_files(case,work,cases.index(case),output_tag="one")
        # Nominal baseline was checked by _run_one. Recreate independent target
        # evidence and ensure every mutated claimed result fails validation.
        folder=work/f"case-{cases.index(case):03d}-{case['id']}"
        nominal_path=folder/"nominal.json"
        nominal=json.loads(nominal_path.read_text())
        input_path=folder/"spec.json"; input_sha=sha(input_path)
        for name,changed in _mutate_output(actual,case).items():
            errors=validate_report(case,changed,nominal,input_sha,inner_json)
            mutations[f"{case['id']}:{name}"]=bool(errors)
    refusals=_run_refusals(work)
    repeat=True
    for index,case in enumerate(cases):
        spec_path,inner_path,out,inner_json,spec_bytes=_case_files(case,work,index,output_tag="repeat")
        try: proc=_invoke([str(ACTINV),"waste","intrusion-screen",str(spec_path),str(out)])
        except RuntimeError as error:
            repeat=False; failures.append(f"{case['id']}: repeat command failed: {error}"); continue
        first=work/f"case-{index:03d}-{case['id']}"/"screen-one.json"
        if proc.returncode!=0 or not first.is_file() or not out.is_file() or first.read_bytes()!=out.read_bytes():
            repeat=False; failures.append(f"{case['id']}: repeated output bytes differ")
    target_count=sum(len(case["outer"]["waste_spec"]["targets"]) for case in cases)
    result={"schema":"actinv-p111-intrusion-screen-g1-1","phase":"P111",
            "protocol_sha256":sha(PROTOCOL),"pass":not failures and contract["pass"] and len(mutations)>=20
                and all(mutations.values()) and refusals["pass"] and repeat,
            "failures":failures,"case_count":len(cases),"target_count":target_count,
            "independent_comparison_count":independent_count,"repeat_byte_identical":repeat,
            "mutations_rejected":mutations,"refusal_controls":refusals,"case_evidence":evidence}
    if no_write:
        persisted=_json(G1)
        same=persisted==result
        result["persisted_result_matches"]=same
        result["pass"]=result["pass"] and same
    else:
        G1.parent.mkdir(parents=True,exist_ok=True)
        G1.write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(result,sort_keys=True,indent=2))
    return 0 if result.get("pass") is True else 1


def run_g2(*,no_write:bool=False)->int:
    g1_path=G1
    g0_replay=g0(no_write=True)==0
    g1_replay=run_g1(no_write=True)==0
    exact_replay=g0_replay and g1_replay
    result={"schema":"actinv-p111-intrusion-screen-g2-1","phase":"P111",
            "protocol_sha256":sha(PROTOCOL),"g1_result_sha256":sha(g1_path),
            "g0_exact_replay_equal":g0_replay,"exact_replay_equal":exact_replay,
            "separate_output_paths_byte_identical":g1_replay,"pass":exact_replay}
    if no_write:
        persisted=_json(G2); same=persisted==result
        result["persisted_result_matches"]=same; result["pass"]=result["pass"] and same
    else:
        G2.parent.mkdir(parents=True,exist_ok=True)
        G2.write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(result,sort_keys=True,indent=2))
    return 0 if result.get("pass") is True else 1


def main()->int:
    parser=argparse.ArgumentParser()
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument("--seal",action="store_true",help="source-only G0 seal; run before CLI implementation")
    modes.add_argument("--g0-only",action="store_true")
    modes.add_argument("--g1-only",action="store_true")
    modes.add_argument("--g2-only",action="store_true")
    parser.add_argument("--no-write",action="store_true")
    args=parser.parse_args()
    if args.seal:return g0(seal=True)
    if args.g0_only:return g0(no_write=args.no_write)
    if args.g1_only:return run_g1(no_write=args.no_write)
    if args.g2_only:return run_g2(no_write=args.no_write)
    first=g0(no_write=args.no_write)
    if first:return first
    if args.no_write:
        return run_g2(no_write=True)
    return run_g1()


if __name__=="__main__": raise SystemExit(main())
