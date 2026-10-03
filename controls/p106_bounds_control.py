#!/usr/bin/env python3
"""Deterministic artificial P106 case population and independent interval oracle.

Only public rule rows and frozen P103 synthetic vectors are inputs. This module
does not launch the application or any solver.
"""
from __future__ import annotations

import importlib.util
import json
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"
P103_VECTORS = ROOT / "controls/fixtures/p103/classification_vectors.json"


@lru_cache(maxsize=1)
def _p105_oracle():
    path = ROOT / "controls/check_p105.py"
    spec = importlib.util.spec_from_file_location("p105_independent_oracle", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen independent P105 arithmetic oracle")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _props(names: list[str], *, half_life_years: float = 10.0) -> dict:
    atomic = {"C": 6, "Tc": 43, "H": 1, "Sr": 38, "Cs": 55, "Ni": 28,
              "Nb": 41, "Pu": 94, "Cm": 96, "Co": 27, "Fe": 26}
    out = {}
    for name in names:
        element = "".join(ch for ch in name if ch.isalpha())
        years = 1.0 if name in ("Co60", "Cm242") else (5.0 if name == "Fe55" else half_life_years)
        out[name] = {"z": atomic[element], "half_life_s": years * 365.25 * 86400.0,
                     "alpha_emitting": name in ("Pu239", "Cm242")}
    return out


def _interval(fraction: float, limit: float, unit: str) -> dict:
    scale = 37_000.0 if unit == "Ci/m3" else 37.0
    value = fraction * (limit * scale)
    return {"lower_bq": value, "upper_bq": value}


def _target(step: int = 1, t_s: float = 0.0, *, bounds: dict | None = None,
            coverage: str = "complete", reasons: list[str] | None = None,
            source: str = "P106 artificial vector", assumptions: str = "rectangular total-Bq box") -> dict:
    return {"step": step, "t_s": t_s, "activity_bounds_bq": bounds or {},
            "inventory_coverage": coverage, "unbounded_inventory_reasons": reasons or [],
            "bounds_source": source, "bounds_assumptions": assumptions}


def _component(case_id: str, names: list[str], targets: list[dict], *,
            waste_type: str = "general", mass: float = 1.0,
             volume: float = 1.0, external: dict | None = None,
             properties: dict | None = None) -> dict:
    return {"id": case_id, "mass_g": mass, "displaced_volume_cm3": volume,
            "waste_type": waste_type, "nuclide_properties": properties if properties is not None else _props(names),
            "external_tritium": external or {"status": "not_applicable"}, "targets": targets}


def generate_cases() -> list[dict]:
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    rows = pack["rows"]
    cases = []
    for vector in json.loads(P103_VECTORS.read_text(encoding="utf-8"))["vectors"]:
        mass = float(vector["mass_g"])
        bounds = {name: {"lower_bq": float(value) * mass, "upper_bq": float(value) * mass}
                  for name, value in vector["activity_Bq_per_g"].items()}
        cov = "incomplete" if vector["expected"]["coverage"] == "incomplete" else "complete"
        target = _target(bounds=bounds, coverage=cov,
                         reasons=["frozen P103 vector has incomplete nuclide metadata"] if cov == "incomplete" else [])
        comp = _component(vector["id"], sorted(set(vector["activity_Bq_per_g"]) |
                                               set(vector["nuclide_properties"])), [target],
                          waste_type=vector["waste_type"], mass=mass,
                          volume=float(vector["displaced_volume_cm3"]),
                          properties=vector["nuclide_properties"],
                          external=vector.get("external_tritium", {"status": "not_applicable"}))
        cases.append({"id": vector["id"], "component": comp,
                      "expected_source": "frozen_p103_zero_width"})

    def row_limit(table: int, selector: str, col: int = 0, applicability: str = "general") -> tuple[float, str]:
        row = next(r for r in rows if r["table"] == table and r["selector"] == selector
                   and (r["applicability"] == applicability or r["applicability"] == "all"))
        return float(row["limits"][col]), row["unit"]

    def frac(name: str, selector: str, fraction: float, table: int = 1, col: int = 0,
             applicability: str = "general") -> tuple[str, dict]:
        limit, unit = row_limit(table, selector, col, applicability)
        return name, _interval(fraction, limit, unit)

    def add(case_id: str, target_list: list[dict], names: list[str], **kwargs) -> None:
        cases.append({"id": case_id, "component": _component(case_id, names, target_list, **kwargs),
                      "expected_source": "p106_protocol_vector"})

    add("empty_complete", [_target()], [])
    add("empty_incomplete", [_target(coverage="incomplete", reasons=["inventory not bounded"])], [])
    add("required_h3", [_target()], [], external={"status": "required"})
    n, b = frac("C14", "C-14", .1)
    _, b_hi = frac("C14", "C-14", 1.0)
    add("single_t1_boundary_box", [_target(bounds={"C14": {"lower_bq": b["lower_bq"], "upper_bq": b_hi["upper_bq"]}})], ["C14"])
    n1, lo1 = frac("C14", "C-14", .05); n2, lo2 = frac("Tc99", "Tc-99", .05)
    _, hi1 = frac("C14", "C-14", .5); _, hi2 = frac("Tc99", "Tc-99", .5)
    add("mixture_t1_strict_box", [_target(bounds={n1: {"lower_bq": lo1["lower_bq"], "upper_bq": hi1["upper_bq"]},
                                               n2: {"lower_bq": lo2["lower_bq"], "upper_bq": hi2["upper_bq"]}})], [n1,n2])
    _, fixed = frac("C14", "C-14", .05); _, cross = frac("Tc99", "Tc-99", .05)
    add("zero_crossing_contributor", [_target(bounds={"C14": fixed, "Tc99": {"lower_bq": 0.0, "upper_bq": cross["upper_bq"]}})], ["C14","Tc99"])
    sr_limit, _ = row_limit(2,"Sr-90",1); cs_limit,_ = row_limit(2,"Cs-137",1)
    add("sr90_cs137_example", [_target(bounds={"Sr90": _interval(50.0/sr_limit,sr_limit,"Ci/m3"),
                                              "Cs137": _interval(22.0/cs_limit,cs_limit,"Ci/m3")})], ["Sr90","Cs137"])
    _, c14 = frac("C14","C-14",.2); _, ni = frac("Ni63","Ni-63",.5,table=2,col=0)
    add("t1_t2_separate", [_target(bounds={"C14":c14,"Ni63":ni})], ["C14","Ni63"])
    targets=[]
    for step,col in enumerate((0,1,2),1):
        lim,unit=row_limit(2,"Ni-63",col)
        targets.append(_target(step,100.0*(step-1),bounds={"Ni63":_interval(1.0,lim,unit)}))
    add("t2_three_columns",targets,["Ni63"])
    _, nb = frac("Nb94","Nb-94",.2,applicability="activated_metal")
    add("metal_nb94",[_target(bounds={"Nb94":nb})],["Nb94"],waste_type="activated_metal")
    _, pu = frac("Pu239","alpha_transuranic_gt5y",.2)
    add("alpha_tru",[_target(bounds={"Pu239":pu})],["Pu239"])
    _, cm = frac("Cm242","Cm-242",.2)
    cm_bounds={"Cm242":{"lower_bq":cm["lower_bq"],"upper_bq":cm["upper_bq"]}}
    add("cm242_cross_table",[_target(bounds=cm_bounds)],["Cm242"],properties=_props(["Cm242"],half_life_years=1))
    co_limit,_=row_limit(2,"Co-60")
    add("dedicated_co60",[_target(bounds={"Co60":_interval(.2,co_limit,"Ci/m3")})],["Co60"],properties=_props(["Co60"],half_life_years=1))
    add("missing_active_properties",[_target(bounds={"C14":{"lower_bq":0.0,"upper_bq":29600.0}})],[],properties={})
    add("inactive_and_malformed_properties",[_target(bounds={"C14":{"lower_bq":0.0,"upper_bq":0.0}})],[],properties={})
    add("long_lived_unlisted",[_target(bounds={"Fe55":{"lower_bq":100.0,"upper_bq":100.0}})],["Fe55"],properties=_props(["Fe55"]))
    h3_limit,_=row_limit(2,"H-3")
    add("external_h3_cross",[_target()],["H3"],external={"status":"bounded","source":"artificial external H-3 interval","excludes_activation":True,
          "activity_bounds_bq":{"1":{"lower_bq":.5*h3_limit*37000,"upper_bq":1.5*h3_limit*37000}}})
    each=.6*h3_limit*37000
    add("external_h3_merge",[_target(bounds={"H3":{"lower_bq":each,"upper_bq":each}})],["H3"],external={"status":"bounded","source":"artificial disjoint H-3 source","excludes_activation":True,
          "activity_bounds_bq":{"1":{"lower_bq":each,"upper_bq":each}}})
    add("target_coverage_change",[_target(1,0),_target(2,100,coverage="incomplete",reasons=["second target incomplete"])],[])
    _,c14a=frac("C14","C-14",.05); _,c14b=frac("C14","C-14",.2)
    add("target_bounds_change",[_target(1,0,bounds={"C14":c14a}),_target(2,100,bounds={"C14":c14b})],["C14"])
    if len(cases) != 146:
        raise ValueError(f"P106 population must contain 146 records, got {len(cases)}")
    return cases


def _endpoint(component: dict, target: dict, upper: bool, rows: list[dict]) -> dict:
    props = {key.replace("-", ""): value for key, value in component["nuclide_properties"].items()}
    mass = component["mass_g"]
    all_bounds = dict(target["activity_bounds_bq"])
    external = component["external_tritium"]
    if external["status"] == "bounded":
        extra = external["activity_bounds_bq"][str(target["step"])]
        previous = all_bounds.get("H3", {"lower_bq":0.0,"upper_bq":0.0})
        all_bounds["H3"] = {"lower_bq":previous["lower_bq"]+extra["lower_bq"],
                             "upper_bq":previous["upper_bq"]+extra["upper_bq"]}
    property_keys = set(props)
    unknown = sorted(key.replace("-", "") for key, interval in all_bounds.items()
                     if interval["upper_bq"] > 0.0 and key.replace("-", "") not in property_keys)
    merged = {}
    for nuclide, interval in target["activity_bounds_bq"].items():
        value = float(interval["upper_bq" if upper else "lower_bq"])
        canonical = nuclide.replace("-", "")
        if canonical in property_keys:
            merged[canonical] = value / mass
    required = external["status"] == "required"
    if external["status"] == "bounded":
        ext = external["activity_bounds_bq"][str(target["step"])]
        value = float(ext["upper_bq" if upper else "lower_bq"])
        if "H3" in property_keys:
            merged["H3"] = merged.get("H3",0.0) + value / mass
    oracle = _p105_oracle()
    case = {"activity_Bq_per_g":merged,"mass_g":mass,
            "displaced_volume_cm3":component["displaced_volume_cm3"],
            "waste_type":component["waste_type"],"nuclide_properties":props,
            "external_tritium":{"status":"required" if required else "not_applicable"}}
    result = oracle.independent_classification(case,rows)
    if unknown:
        result["class"] = "unknown"
        result["coverage"] = "incomplete"
        result["unknown_nuclides"] = unknown
    return result


def derive_case(case: dict, rows: list[dict]) -> dict:
    component=case["component"]
    targets=[]
    for target in component["targets"]:
        low=_endpoint(component,target,False,rows); high=_endpoint(component,target,True,rows)
        incomplete=target["inventory_coverage"]=="incomplete" or bool(low["unknown_nuclides"] or high["unknown_nuclides"]) or component["external_tritium"]["status"]=="required"
        if incomplete:
            unknown = sorted(set(low["unknown_nuclides"]) | set(high["unknown_nuclides"]))
            for endpoint in (low, high):
                endpoint["class"] = "unknown"
                endpoint["coverage"] = "incomplete"
                endpoint["unknown_nuclides"] = unknown
            envelope=["unknown"]; stable=False; stable_class=None; superset=None
        else:
            rank={"A":0,"B":1,"C":2,"above_class_c":3}
            inverse={v:k for k,v in rank.items()}
            lo,hi=rank[low["class"]],rank[high["class"]]
            if lo>hi:
                raise ValueError(f"nonmonotone endpoint class order in {case['id']}")
            envelope=[inverse[v] for v in range(lo,hi+1)]
            stable=lo==hi; stable_class=envelope[0] if stable else None; superset=True
        targets.append({"step":target["step"],"lower":low,"upper":high,
                        "class_envelope":envelope,"conservative_superset":superset,
                        "class_is_stable":stable,"stable_class":stable_class})
    return {"id":case["id"],"targets":targets}
