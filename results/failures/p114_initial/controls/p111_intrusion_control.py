"""Independent arithmetic and deterministic synthetic cases for P111.

This module implements the frozen draft-screen interpretations in the P111
protocol. It uses only artificial activity maps; it is not a legal catalog or
an acceptance calculation.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from decimal import Decimal, InvalidOperation, localcontext

import check_p105

ROOT = Path(__file__).resolve().parents[1]
CASE_SCHEMA = "actinv-p111-intrusion-cases-1"
FIVE_YEARS_S = 5 * 365.25 * 86400
CI_BQ_CM3 = 37_000.0
NCI_BQ_G = 37.0
ABSENT = object()

# Literal Table 8-5 transcription, independent of the shipped pack and parsed
# source fixture. Row member identifiers use the CLI's canonical nuclide names.
EXPECTED_ROWS = [
    {"id":"short_half_life_lt5y_sum","members":[],"applicability":"all","unit":"Ci/m3","limits":[700,None,None],"aggregate":"half_life_lt5y"},
    {"id":"H-3","members":["H3"],"applicability":"all","unit":"Ci/m3","limits":[40,1.1e8,None]},
    {"id":"C-14","members":["C14"],"applicability":"general","unit":"Ci/m3","limits":[0.8,0.8,8]},
    {"id":"C-14_activated_metal","members":["C14"],"applicability":"activated_metal","unit":"Ci/m3","limits":[8,8,80]},
    {"id":"Cl-36","members":["Cl36"],"applicability":"all","unit":"Ci/m3","limits":[110,110,1100]},
    {"id":"Co-60","members":["Co60"],"applicability":"all","unit":"Ci/m3","limits":[700,67000,None]},
    {"id":"Ni-59","members":["Ni59"],"applicability":"general","unit":"Ci/m3","limits":[2.2,2.2,22]},
    {"id":"Ni-59_activated_metal","members":["Ni59"],"applicability":"activated_metal","unit":"Ci/m3","limits":[22,22,220]},
    {"id":"Ni-63","members":["Ni63"],"applicability":"general","unit":"Ci/m3","limits":[3.5,70,700]},
    {"id":"Ni-63_activated_metal","members":["Ni63"],"applicability":"activated_metal","unit":"Ci/m3","limits":[35,700,7000]},
    {"id":"Sr-90","members":["Sr90"],"applicability":"all","unit":"Ci/m3","limits":[0.04,150,7000]},
    {"id":"Nb-94","members":["Nb94"],"applicability":"general","unit":"Ci/m3","limits":[0.02,0.02,0.2]},
    {"id":"Nb-94_activated_metal","members":["Nb94"],"applicability":"activated_metal","unit":"Ci/m3","limits":[0.2,0.2,2]},
    {"id":"Tc-99","members":["Tc99"],"applicability":"all","unit":"Ci/m3","limits":[0.3,0.3,3]},
    {"id":"I-129","members":["I129"],"applicability":"all","unit":"Ci/m3","limits":[0.008,0.008,0.08]},
    {"id":"Cs-135","members":["Cs135"],"applicability":"all","unit":"Ci/m3","limits":[84,84,840]},
    {"id":"Cs-137","members":["Cs137"],"applicability":"all","unit":"Ci/m3","limits":[1,44,460]},
    {"id":"Eu-152","members":["Eu152"],"applicability":"all","unit":"Ci/m3","limits":[0.06,6.2,None]},
    {"id":"Eu-154","members":["Eu154"],"applicability":"all","unit":"Ci/m3","limits":[0.02,1.5,5100000]},
    {"id":"U-235","members":["U235"],"applicability":"all","unit":"Ci/m3","limits":[0.04,0.04,0.4]},
    {"id":"U-238","members":["U238"],"applicability":"all","unit":"Ci/m3","limits":[0.05,0.05,0.5]},
    {"id":"Np-237","members":["Np237"],"applicability":"all","unit":"nCi/g","limits":[10,10,100]},
    {"id":"Pu-241","members":["Pu241"],"applicability":"all","unit":"nCi/g","limits":[350,350,3500]},
    {"id":"alpha_transuranic_gt5y","members":["Pu238","Pu239","Pu240","Pu242","Am241","Am243","Cm243","Cm244"],"applicability":"all","unit":"nCi/g","limits":[10,10,100]},
    {"id":"Cm-242","members":["Cm242"],"applicability":"all","unit":"nCi/g","limits":[2000,2000,20000]},
]

ROW_LABELS = {
    "short_half_life_lt5y_sum":"Sum of Radionuclides with less than a 5-year half-life",
    "C-14_activated_metal":"C-14 in activated metal",
    "Ni-59_activated_metal":"Ni-59 in activated metal",
    "Ni-63_activated_metal":"Ni-63 in activated metal",
    "Nb-94_activated_metal":"Nb-94 in activated metal",
    "alpha_transuranic_gt5y":"Pu-238, Pu-239, Pu-240, Pu-242, Am-241, Am-243, Cm-243, Cm-244",
}
ROW_SELECTORS = {
    "short_half_life_lt5y_sum":"half_life_lt5y",
    "alpha_transuranic_gt5y":"alpha_transuranic_fixed8",
}
ROW_NOTES = {
    "short_half_life_lt5y_sum":"a,b", "H-3":"a,c,d", "C-14":"a",
    "C-14_activated_metal":"a", "Cl-36":"c", "Co-60":"a,c,d",
    "Ni-59":"e", "Ni-59_activated_metal":"a", "Ni-63":"e",
    "Ni-63_activated_metal":"a", "Sr-90":"a", "Nb-94":"e",
    "Nb-94_activated_metal":"a", "Tc-99":"a", "I-129":"a",
    "Cs-135":"e", "Cs-137":"a", "Eu-152":"c", "Eu-154":"c",
    "U-235":"e", "U-238":"e", "Np-237":"f", "Pu-241":"a",
    "alpha_transuranic_gt5y":"g", "Cm-242":"a",
}


def public_rows() -> list[dict]:
    """Independent exact pack/fixture representation from the source tuples."""
    result=[]
    for row in EXPECTED_ROWS:
        row_id=row["id"]
        if row_id in ROW_SELECTORS:
            selector=ROW_SELECTORS[row_id]
        else:
            selector=row_id.removesuffix("_activated_metal")
        label=ROW_LABELS.get(row_id,selector)
        result.append({"id":row_id,"label":label,"selector":selector,
                       "members":[_hyphenate(name) for name in row["members"]],
                       "applicability":row["applicability"],"unit":row["unit"],
                       "limits":row["limits"],"basis_note":ROW_NOTES[row_id]})
    return result


def _hyphenate(name: str) -> str:
    for index,char in enumerate(name):
        if char.isdigit(): return name[:index]+"-"+name[index:]
    return name


def _props(names: list[str]) -> dict:
    # Explicit artificial properties support both Part 61 aggregation and the
    # draft's strict <5-year selector; they are not evaluated nuclear data.
    zmap={"H3":1,"C14":6,"Cl36":17,"Co60":27,"Ni59":28,"Ni63":28,
          "Sr90":38,"Nb94":41,"Tc99":43,"I129":53,"Cs135":55,"Cs137":55,
          "Eu152":63,"Eu154":63,"U235":92,"U238":92,"Np237":93,
          "Pu238":94,"Pu239":94,"Pu240":94,"Pu241":94,"Pu242":94,
          "Am241":95,"Am242":95,"Am243":95,"Cm242":96,"Cm243":96,"Cm244":96,
          "Xe135":54,"Mg24":12}
    props={}
    for name in names:
        props[name]={"z":zmap[name],"half_life_s":{
            "H3":3.887e8,"Co60":1.663e8,"Xe135":3.3e4,"Ni63":3.15e9,
            "C14":1.808e11,"Nb94":6.4e11,"Pu241":4.54e8,
            "Cm242":1.4e7,"Mg24":1.0e30,"Am242":1.0e30}.get(name,2.0e9),
            "alpha_emitting":name.startswith(("Pu","Am","Cm"))}
    return props


def _case(case_id: str, activity: dict[str,float], *, wac: object = ABSENT,
          rq: object = ABSENT, coverage: str = "complete", reasons: list[str] | None = None,
          external: dict | None = None, form: str = "equipment", mass: float = 1.0,
          volume: float = 1.0, waste_type: str = "general", props: dict | None = None,
          time_s: float = 0.0, mesh: bool = False) -> dict:
    names=sorted(activity)
    nuclide_props=_props(names) if props is None else copy.deepcopy(props)
    component={"id":"component-1","mass_g":mass,"displaced_volume_cm3":volume,
               "waste_type":waste_type,"external_tritium":external or {"status":"not_applicable"}}
    waste_spec={"schema":"actinv-waste-spec-1","rules":"us-nrc-10cfr61.55-v1",
                "input":"case-input.json","targets":[1],"nuclide_properties":nuclide_props,
                "components":[component]}
    outer={"schema":"actinv-waste-intrusion-screen-spec-1",
           "draft_rules":"us-nrc-nureg1556-v22-draft-2026-02-v1",
           "waste_spec":waste_spec,"package_basis":"single_component_container",
           "waste_form":form,"inventory_coverage":coverage,
           "unbounded_inventory_reasons":list(reasons or [])}
    if wac is not ABSENT: outer["site_wac"]=copy.deepcopy(wac)
    if rq is not ABSENT: outer["dot_rq"]=copy.deepcopy(rq)
    steps=[{"step":1,"t_s":time_s,"activity_Bq_per_g":copy.deepcopy(activity)}]
    run={"entry_point":"cli","mode":"coupled","steps":steps}
    return {"id":case_id,"outer":outer,"run_result":run,"mesh":mesh,
            "case_expectation":"synthetic control input; oracle derives output independently"}


def generate_cases() -> list[dict]:
    """Return frozen, deterministic >40 case population with one target each."""
    cases=[]
    def add(*args,**kwargs): cases.append(_case(*args,**kwargs))
    def wac(value: float, unit="nCi/g", status="listed"):
        return {"source":"synthetic WAC control","membership_coverage":"complete",
                "nuclides":{"C14":{"status":status,"limit":{"value":value,"unit":unit}}}}
    def rqmap(value: float, **entries):
        return {"source":"synthetic caller-declared DOT-RQ test values","coverage":"complete",
                "nuclides":{"C14":value,**entries}}
    # Criterion 1 strict WAC cut, including exact boundary.
    for label,factor in (("below",0.009999),("equal",0.01),("above",0.010001)):
        add(f"wac_{label}",{"C14":37.0*100.0*factor},wac=wac(100.0))
    # Criterion 2 concentration cut and its Part 61/WAC membership consensus.
    for label,value in (("below",259999.0),("equal",260000.0),("above",260001.0)):
        add(f"unlisted_both_{label}",{"Mg24":value},
            wac={"source":"synthetic WAC","membership_coverage":"complete","nuclides":{}},
            props=_props(["Mg24"]))
    add("c2_part61_listed",{"C14":260001.0},wac=wac(1.0))
    add("c2_both_listed_false",{"C14":260001.0},wac=wac(1e9))
    add("c2_wac_listed",{"Mg24":260001.0},wac={"source":"synthetic WAC","membership_coverage":"complete",
        "nuclides":{"Mg24":{"status":"listed_no_numeric_limit"}}})
    add("c2_membership_disagreement",{"C14":260001.0},wac={"source":"synthetic WAC","membership_coverage":"complete","nuclides":{}})
    add("c2_incomplete_wac_membership",{"Mg24":260001.0},wac={"source":"synthetic WAC","membership_coverage":"incomplete","nuclides":{}})
    add("c2_cl36_part61_absent_draft_listed",{"Cl36":260001.0},wac={"source":"synthetic WAC","membership_coverage":"complete","nuclides":{}})
    add("part61_alpha_outside_draft_fixed8",{"Am242":1.0e9},wac={"source":"synthetic WAC","membership_coverage":"complete","nuclides":{}})
    add("c2_missing_short_metadata",{"Xe135":260001.0},wac={"source":"synthetic WAC","membership_coverage":"complete","nuclides":{}},props={})
    # Criterion 3 individual RQ boundary and mixture treatment.
    for label,value in (("below",99.999),("equal",100.0),("above",100.001)):
        add(f"rq_{label}",{"Mg24":value},rq={"source":"synthetic RQ","coverage":"complete","nuclides":{"Mg24":100.0}},props=_props(["Mg24"]))
    for label,amount in (("below",0.99),("equal",1.0),("above",1.01)):
        add(f"rq_mixture_{label}",{"Mg24":50.0*amount,"Xe135":50.0*amount},
            rq={"source":"synthetic RQ","coverage":"complete","nuclides":{"Mg24":100.0,"Xe135":100.0}},
            props=_props(["Mg24","Xe135"]))
    # Criterion 4 inclusive 1% share and complete denominator.
    for label,share in (("below",0.009999),("equal",0.01),("above",0.010001)):
        add(f"share_{label}",{"C14":100.0*share,"Co60":100.0*(1-share)})
    # Each OR arm alone; plus all-false and partially indeterminate cases.
    add("only_wac_true",{"C14":37.1,"Co60":10000.0},wac=wac(100.0),
        rq={"source":"RQ","coverage":"complete","nuclides":{"C14":1e6,"Co60":1e12}})
    add("only_c2_true",{"Mg24":260001.0,"Co60":1.0e8},wac={"source":"WAC","membership_coverage":"complete","nuclides":{}},
        rq={"source":"RQ","coverage":"complete","nuclides":{"Mg24":1e9,"Co60":1e12}},props=_props(["Mg24","Co60"]))
    add("only_rq_true",{"Mg24":100.0,"Co60":10000.0},rq={"source":"RQ","coverage":"complete","nuclides":{"Mg24":100.0,"Co60":1e12}},props=_props(["Mg24","Co60"]))
    add("only_share_true",{"C14":1.0,"Co60":99.0})
    many_names=[f"Fe{mass}" for mass in range(26,127)]
    many_activity={name:1.0 for name in many_names}
    many_props={name:{"z":26,"half_life_s":1.0e30,"alpha_emitting":False} for name in many_names}
    many_wac={name:{"status":"listed","limit":{"value":1.0e9,"unit":"nCi/g"}} for name in many_names}
    many_rq={name:1.0e9 for name in many_names}
    add("all_presence_false",many_activity,
        wac={"source":"synthetic WAC","membership_coverage":"complete","nuclides":many_wac},
        rq={"source":"synthetic RQ","coverage":"complete","nuclides":many_rq},props=many_props)
    add("wac_absent_indeterminate",{"C14":1.0})
    add("wac_no_numeric_indeterminate",{"C14":1.0},wac={"source":"WAC","membership_coverage":"complete","nuclides":{"C14":{"status":"listed_no_numeric_limit"}}})
    add("rq_missing_nuclide_indeterminate",{"C14":1.0},rq={"source":"RQ","coverage":"complete","nuclides":{}})
    add("incomplete_inventory_share_unknown",{"C14":1.0},coverage="incomplete",reasons=["synthetic unseen inventory"])
    add("required_h3_unknown",{"C14":1.0},external={"status":"required"})
    add("unknown_inventory_coverage",{"C14":1.0},coverage="unknown",reasons=["synthetic inventory bounds not supplied"])
    # Same H3 activity represented activation + declared external: merge once.
    add("external_h3_once",{"H3":10.0},external={"status":"declared","source":"synthetic external","excludes_activation":True,"activity_bq":{"1":10.0}})
    add("zero_inventory",{})
    # Geometry conversion and both source units.
    add("volume_ci_m3",{"C14":37000.0},mass=1.0,volume=1.0)
    add("mass_nci_g",{"Np237":37.0},mass=1.0,volume=1.0)
    add("geometry_unequal_mass_volume",{"C14":37000.0},mass=2.0,volume=5.0,
        wac={"source":"synthetic WAC","membership_coverage":"complete",
             "nuclides":{"C14":{"status":"listed","limit":{"value":100000.0,"unit":"nCi/g"}}}})
    # Mesh aggregation: two cells are summed before whole component normalization.
    mesh_case=_case("mesh_aggregation",{"C14":30.0},mass=1.0,volume=1.0)
    mesh_case["mesh"]=True
    mesh_case["mesh_cells"]=[{"id":"cell-a","mass_g":0.25,"activity_Bq_per_g":{"C14":60.0}},
                             {"id":"cell-b","mass_g":0.75,"activity_Bq_per_g":{"C14":20.0}}]
    cases.append(mesh_case)
    # Group membership and overlap: <5-year sum includes named members; named rows persist.
    short_props=_props(["Xe135","Ni63"]); short_props["Ni63"]["half_life_s"]=1.0e7
    add("short_group_and_named",{"Xe135":1.0,"Ni63":2.0},props=short_props)
    add("short_lt5_boundary_below",{"Xe135":1.0},props={"Xe135":{"z":54,"half_life_s":FIVE_YEARS_S-1,"alpha_emitting":False}})
    add("exact_five_year_not_group",{"Xe135":1.0},props={"Xe135":{"z":54,"half_life_s":FIVE_YEARS_S,"alpha_emitting":False}})
    add("short_gt5_boundary_above",{"Xe135":1.0},props={"Xe135":{"z":54,"half_life_s":FIVE_YEARS_S+1,"alpha_emitting":False}})
    add("alpha_group",{"Pu238":1.0,"Pu239":1.0,"Pu240":1.0,"Pu242":1.0,
                        "Am241":1.0,"Am243":1.0,"Cm243":1.0,"Cm244":1.0,
                        "Np237":1.0,"Cm242":1.0})
    add("draft_row_class_a_equal",{"C14":0.8*CI_BQ_CM3})
    add("draft_row_class_c_equal",{"C14":8.0*CI_BQ_CM3})
    # Printed no-limit cells and class-specific column selection.
    add("no_limit_co60",{"Co60":1.0e10,"Ni63":100.0*CI_BQ_CM3})
    add("no_limit_h3",{"H3":1.0e13,"Ni63":100.0*CI_BQ_CM3})
    add("no_limit_eu152_class_c",{"Ni63":100.0*CI_BQ_CM3,"Eu152":0.01*CI_BQ_CM3})
    add("no_limit_short_group_class_b",{"Xe135":800.0*CI_BQ_CM3})
    add("no_limit_short_group_class_c",{"Xe135":800.0*CI_BQ_CM3,"Ni63":100.0*CI_BQ_CM3})
    add("metal_nb94_literal",{"Nb94":1000.0},form="metal",waste_type="activated_metal")
    add("metal_c14_literal",{"C14":1000.0},form="metal",waste_type="activated_metal")
    add("metal_ni59_replacement",{"Ni59":1000.0},form="metal",waste_type="activated_metal")
    add("metal_ni63_replacement",{"Ni63":1000.0},form="metal",waste_type="activated_metal")
    add("cs137_literal_460",{"Cs137":1.0e10})
    add("above_class_c",{"C14":1.0e9})
    add("unknown_class_from_missing_metadata",{"C14":1.0},props={})
    add("other_waste_form",{"C14":1.0},form="other")
    # Later targets exercise independent per-target arithmetic and times.
    multi=copy.deepcopy(cases[0]); multi["id"]="multi_target_steps"
    multi["outer"]["waste_spec"]["targets"]=[1,2]
    multi["run_result"]["steps"].append({"step":2,"t_s":86400.0,"activity_Bq_per_g":{"C14":74.0}})
    cases.append(multi)
    if len(cases) < 40: raise AssertionError("P111 population fell below the frozen minimum")
    ids=[c["id"] for c in cases]
    if len(set(ids)) != len(ids): raise AssertionError("duplicate P111 case IDs")
    return cases


def _part61_membership(name: str, properties: dict, waste_type: str) -> bool | None:
    unknown = False
    for row in check_p105.expected_pack_rows():
        if row["applicability"] == "general" and waste_type == "activated_metal":
            continue
        if row["applicability"] not in ("all", "general", waste_type):
            continue
        selector=str(row["selector"]).replace("-", "")
        if selector not in ("half_life_lt5y", "alpha_transuranic_gt5y") and name==selector:
            return True
        if selector=="half_life_lt5y":
            prop=properties.get(name)
            if prop is None:
                unknown = True
                continue
            half=prop.get("half_life_s")
            if not isinstance(half,(int,float)) or isinstance(half,bool) or not math.isfinite(half):
                unknown = True
            elif 0 < half < FIVE_YEARS_S:
                return True
        elif selector=="alpha_transuranic_gt5y":
            prop=properties.get(name)
            if prop is None:
                unknown=True
                continue
            z=prop.get("z"); half=prop.get("half_life_s"); alpha=prop.get("alpha_emitting")
            if not isinstance(z,int) or isinstance(z,bool) or not isinstance(half,(int,float)) or isinstance(half,bool) or not math.isfinite(half) or not isinstance(alpha,bool):
                unknown=True
            elif z>92 and alpha and half>FIVE_YEARS_S and name!="Pu241":
                return True
    return None if unknown else False


def _draft_membership(name:str,properties:dict,waste_type:str,form:str)->bool|None:
    unknown=False
    for row in EXPECTED_ROWS:
        if row["applicability"]=="general" and waste_type=="activated_metal": continue
        if row["applicability"]=="activated_metal" and form!="metal": continue
        if row["applicability"] not in ("all","general","activated_metal"): continue
        if name in row["members"]: return True
        if row.get("aggregate")=="half_life_lt5y":
            prop=properties.get(name)
            if prop is None:
                unknown=True
                continue
            half=prop.get("half_life_s")
            if not isinstance(half,(int,float)) or isinstance(half,bool) or not math.isfinite(half):
                unknown=True
            elif 0<half<FIVE_YEARS_S:
                return True
    return None if unknown else False


def _concentration_bq_cm3(activity_bq: float, mass_g: float, volume_cm3: float) -> float:
    return activity_bq / volume_cm3


def _concentration_for_unit(activity_bq: float, unit: str, mass_g: float, volume_cm3: float) -> float:
    if unit == "Ci/m3": return activity_bq / volume_cm3 / CI_BQ_CM3
    if unit == "nCi/g": return activity_bq / mass_g / NCI_BQ_G
    raise ValueError(f"unknown draft row unit {unit!r}")


def _tri_or(values: list[str]) -> str:
    if "true" in values: return "true"
    if all(value=="false" for value in values): return "false"
    return "indeterminate"


def evaluate_target(case: dict, step: int) -> dict:
    """Independent source-rule computation for one raw target activity map."""
    outer=case["outer"]
    run_steps={int(item["step"]):item for item in case["run_result"]["steps"]}
    step_data=run_steps[step]
    with localcontext() as ctx:
        ctx.prec=80
        activity_d={str(k):Decimal(str(v)) for k,v in step_data["activity_Bq_per_g"].items()}
    activity={name:float(value) for name,value in activity_d.items()}
    component=outer["waste_spec"]["components"][0]
    mass=float(component["mass_g"]); volume=float(component["displaced_volume_cm3"])
    with localcontext() as ctx:
        ctx.prec=80
        mass_d=Decimal(str(component["mass_g"])); volume_d=Decimal(str(component["displaced_volume_cm3"]))
        total_d={name:value*mass_d for name,value in activity_d.items()}
    external=component.get("external_tritium",{"status":"not_applicable"})
    external_bq=0.0
    if external.get("status")=="declared":
        external_bq=float(external.get("activity_bq",{}).get(str(step),0.0))
        with localcontext() as ctx:
            ctx.prec=80
            external_d=Decimal(str(external_bq))
            total_d["H3"]=total_d.get("H3",Decimal(0))+external_d
    properties=outer["waste_spec"].get("nuclide_properties",{})
    positive_d={name:value for name,value in total_d.items() if value>0}
    positive={name:float(value) for name,value in positive_d.items()}
    coverage=outer["inventory_coverage"]
    required_h3=external.get("status")=="required"
    active_unknown=any(name not in properties for name in positive_d)
    denominator_complete=(coverage=="complete" and not required_h3 and not active_unknown)
    denominator_status=("complete" if denominator_complete else
                        "incomplete" if coverage=="incomplete" else "unknown")
    with localcontext() as ctx:
        ctx.prec=80
        decimal_total=sum(positive_d.values(),Decimal(0))
    container_total=float(decimal_total) if denominator_complete else None
    wac=outer.get("site_wac")
    rq=outer.get("dot_rq")
    wac_entries={} if not isinstance(wac,dict) else wac.get("nuclides",{})
    rq_entries={} if not isinstance(rq,dict) else rq.get("nuclides",{})
    target_names=set(positive)
    predicate_rows=[]; present={}; concentrations={}
    for name in sorted(target_names):
        amount_d=positive_d[name]
        amount=float(amount_d)
        with localcontext() as ctx:
            ctx.prec=80
            conc_bq_cm3_d=amount_d/volume_d
        conc_bq_cm3=float(conc_bq_cm3_d)
        concentrations[name]=conc_bq_cm3
        wac_entry=wac_entries.get(name)
        if isinstance(wac_entry,dict) and wac_entry.get("status")=="listed" and isinstance(wac_entry.get("limit"),dict):
            unit=wac_entry["limit"]["unit"]; lim=float(wac_entry["limit"]["value"])
            with localcontext() as ctx:
                ctx.prec=80
                concentration_d=(amount_d/(volume_d*Decimal(str(CI_BQ_CM3))) if unit=="Ci/m3"
                                 else amount_d/(mass_d*Decimal(str(NCI_BQ_G))))
                p1="true" if concentration_d>Decimal("0.01")*Decimal(str(lim)) else "false"
        else:
            p1="indeterminate"
        p2="false"
        p2_reason="below_260000_bq_cm3"
        if conc_bq_cm3_d>Decimal("260000"):
            p61=_part61_membership(name,properties,component["waste_type"])
            if wac is None:
                wac_listed=None
            elif wac.get("membership_coverage")=="complete":
                wac_listed=(wac_entry is not None and wac_entry.get("status") in ("listed","listed_no_numeric_limit"))
            else:
                wac_listed=None if wac_entry is None else wac_entry.get("status") in ("listed","listed_no_numeric_limit")
            if p61 is True and wac_listed is True:
                p2="false"; p2_reason="listed_in_both"
            elif p61 is False and wac_listed is False:
                p2="true"; p2_reason="unlisted_in_both_above_threshold"
            else:
                p2="indeterminate"; p2_reason="membership_consensus_unresolved"
        rq_value=rq_entries.get(name) if isinstance(rq_entries,dict) else None
        if isinstance(rq_value,(int,float)) and not isinstance(rq_value,bool) and math.isfinite(float(rq_value)) and amount_d>=Decimal(str(rq_value)):
            p3="true"
        else:
            rq_map_complete=(rq is not None and rq.get("coverage")=="complete"
                             and all(n in rq_entries and isinstance(rq_entries[n],(int,float))
                                     and not isinstance(rq_entries[n],bool) and float(rq_entries[n])>0
                                     for n in positive))
            with localcontext() as ctx:
                ctx.prec=80
                mix_d=sum((positive_d[n]/Decimal(str(rq_entries[n]))
                           for n in positive if rq_map_complete),Decimal(0)) if rq_map_complete else None
            if rq_map_complete and denominator_complete and mix_d is not None and mix_d<Decimal(1):
                p3="false"
            else:
                p3="indeterminate"
        if not denominator_complete:
            p4="indeterminate"
        else:
            with localcontext() as ctx:
                ctx.prec=80
                p4="true" if decimal_total>0 and amount_d>=Decimal("0.01")*decimal_total else "false"
        p1_reason="wac_concentration_gt_0.01_limit" if p1=="true" else ("strict_equality_or_below" if p1=="false" else "numeric_wac_limit_missing")
        p3_reason=("individual_activity_at_or_above_rq" if p3=="true" else
                   "individual_below_rq_and_complete_mixture_below_one" if p3=="false"
                   else "rq_or_mixture_coverage_unresolved")
        p4_reason="complete_container_share_ge_0.01" if p4=="true" else ("complete_container_share_below_0.01" if p4=="false" else "container_denominator_incomplete")
        predicates={"wac_fraction":{"value":p1,"reason":p1_reason},
                    "unlisted_concentration":{"value":p2,"reason":p2_reason},
                    "dot_rq":{"value":p3,"reason":p3_reason},
                    "container_share":{"value":p4,"reason":p4_reason}}
        present[name]=_tri_or([v["value"] for v in predicates.values()])
        predicate_rows.append({"nuclide":name,"activity_bq":amount,"concentration_bq_cm3":conc_bq_cm3,
                               "predicates":predicates,"presence":present[name]})
    # Classification is independently derived from nominal source arithmetic.
    nominal_case={"activity_Bq_per_g":activity,
                  "nuclide_properties":properties,"mass_g":mass,
                  "displaced_volume_cm3":volume,"waste_type":component["waste_type"],
                  "external_tritium":{"status":"required"} if required_h3 else {"status":"not_applicable"}}
    if external.get("status")=="declared":
        nominal_case["activity_Bq_per_g"]["H3"]=(
            nominal_case["activity_Bq_per_g"].get("H3",0.0)+external_bq/mass)
    class_result=check_p105.independent_classification(nominal_case,check_p105.expected_pack_rows())
    class_name=class_result["class"]
    class_column={"A":0,"B":1,"C":2}.get(class_name)
    row_comparisons=[]
    form=outer["waste_form"]
    for row in EXPECTED_ROWS:
        if row["applicability"]=="activated_metal" and form!="metal": continue
        if row["applicability"]=="general" and form=="metal": continue
        if class_column is None:
            limit=None; selected_column=None
        else:
            limit=row["limits"][class_column]; selected_column=["A","B","C"][class_column]
        members=row["members"]
        if row.get("aggregate")=="half_life_lt5y":
            known=[]; unknown=False
            for name in positive:
                prop=properties.get(name)
                if prop is None: unknown=True
                elif 0<float(prop["half_life_s"])<FIVE_YEARS_S: known.append(name)
            members=sorted(set(members)|set(known))
            membership_unknown=unknown
        else:
            membership_unknown=False
        matched_members=sorted(name for name in members if name in positive_d)
        amounts=[positive_d[name] for name in matched_members]
        row_presence=_tri_or([present[name] for name in matched_members if name in present]) if matched_members else "false"
        if membership_unknown and row_presence!="true": row_presence="indeterminate"
        with localcontext() as ctx:
            ctx.prec=80
            row_activity_d=sum(amounts,Decimal(0))
            row_conc_d=(row_activity_d/(Decimal(str(volume))*Decimal(str(CI_BQ_CM3)))
                        if row["unit"]=="Ci/m3" else row_activity_d/(Decimal(str(mass))*Decimal(str(NCI_BQ_G))))
        row_conc=float(row_conc_d)
        if class_column is None:
            relation="unknown"
        elif limit is None:
            relation="no_numeric_limit"
        else:
            # Draft thresholds are concentration levels in table units; equality
            # remains indeterminate under the frozen caption/prose resolution.
            if row_conc_d < Decimal(str(limit)): relation="below"
            elif row_conc_d == Decimal(str(limit)): relation="at_limit"
            else: relation="above"
        row_status=("false" if row_presence=="false" else "indeterminate" if row_presence=="indeterminate"
                    else "true" if relation=="above" else "indeterminate" if relation in ("at_limit","unknown")
                    else "false")
        row_indicator=("review_indicated" if row_presence=="true" and relation=="above" else
                       "indeterminate" if row_presence=="indeterminate" or
                           (row_presence=="true" and relation in ("at_limit","unknown")) else
                       "not_indicated_by_implemented_checks")
        row_activity=float(row_activity_d)
        row_comparisons.append({"row_id":row["id"],"members":matched_members,"presence":row_presence,
            "membership_complete":not membership_unknown,"status":row_status,"unit":row["unit"],"column":selected_column,
            "limit":limit,"activity_bq":row_activity,"concentration":row_conc,
            "relation":relation,"assessment_indicator":row_indicator})
    clear_review=(outer["waste_form"]=="other" or any(x["presence"]=="true" and
        _draft_membership(x["nuclide"],properties,component["waste_type"],form) is False for x in predicate_rows))
    clear_review=clear_review or any(r["presence"]=="true" and r["relation"]=="above" for r in row_comparisons)
    unresolved=any(x["presence"]=="indeterminate" for x in predicate_rows) or not denominator_complete
    unresolved=unresolved or class_column is None or any(
        r["presence"]=="indeterminate" or
        (r["presence"]=="true" and r["relation"] in ("unknown","at_limit"))
        for r in row_comparisons)
    indicator="review_indicated" if clear_review else "indeterminate" if unresolved else "not_indicated_by_implemented_checks"
    ratio_decimals={}; rq_ratio_complete=(rq is not None and rq.get("coverage")=="complete" and denominator_complete)
    if rq is not None:
        for name,amount in positive.items():
            value=rq_entries.get(name)
            if isinstance(value,(int,float)) and not isinstance(value,bool) and float(value)>0:
                with localcontext() as ctx:
                    ctx.prec=80
                    ratio_decimals[name]=positive_d[name]/Decimal(str(value))
            else: rq_ratio_complete=False
    ratios={name:float(value) for name,value in ratio_decimals.items()}
    with localcontext() as ctx:
        ctx.prec=80
        mixture_decimal=sum(ratio_decimals.values(),Decimal(0))
    mixture_ratio=float(mixture_decimal)
    return {"step":step,"t_s":step_data["t_s"],"classification":class_result,
            "inventory_activity_bq":positive,"external_tritium_activity_bq":external_bq,
            "container_total_activity_bq":container_total,"container_denominator_complete":denominator_complete,
            "denominator_status":denominator_status,
            "dot_rq_mixture_ratios":ratios,"dot_rq_mixture_ratio":mixture_ratio,
            "dot_rq_mixture_complete":rq_ratio_complete,"nuclides":predicate_rows,
            "rows":row_comparisons,"assessment_indicator":indicator,
            "coverage":coverage,"unbounded_inventory_reasons":outer["unbounded_inventory_reasons"]}


def write_fixture(root: Path = ROOT) -> dict:
    path=root/"controls/fixtures/p111/cases.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    fixture={"schema":CASE_SCHEMA,"source":"synthetic protocol-declared whole-container activity maps; no evaluated nuclear data",
             "cases":generate_cases()}
    raw=json.dumps(fixture,sort_keys=True,indent=2,allow_nan=False)+"\n"
    path.write_text(raw,encoding="utf-8")
    return fixture
