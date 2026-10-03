#!/usr/bin/env python3
"""Independent exact vertex oracle for the frozen P108 artificial population.

No Rust optimizer is imported and no process is launched here. Decimal inputs
are the exact values of the binary floats used by the production command.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from decimal import Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"


def decimal(value: int | float) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a finite numeric input")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("expected a finite numeric input")
    return Decimal.from_float(number)


def vertices(weights: dict) -> list[dict[str, Decimal]]:
    """Enumerate every vertex: all but at most one coordinate at a box face."""
    names = sorted(weights)
    if not 1 <= len(names) <= 8:
        raise ValueError("tiny independent oracle requires 1..8 coordinates")
    lower = {n: decimal(weights[n]["lower_wt_percent"]) for n in names}
    upper = {n: decimal(weights[n]["upper_wt_percent"]) for n in names}
    with localcontext() as ctx:
        ctx.prec = 10000
        if any(not Decimal(0) <= lower[n] <= upper[n] <= Decimal(100) for n in names):
            raise ValueError("invalid composition interval")
        if sum(lower.values()) > 100 or sum(upper.values()) < 100:
            raise ValueError("infeasible composition interval")
        found = set()
        for free in names:
            fixed = [n for n in names if n != free]
            for faces in itertools.product((False, True), repeat=len(fixed)):
                point = {n: upper[n] if hi else lower[n] for n, hi in zip(fixed, faces)}
                point[free] = Decimal(100) - sum(point.values())
                if lower[free] <= point[free] <= upper[free]:
                    found.add(tuple(point[n] for n in names))
        if not found:
            raise ValueError("feasible composition has no enumerated vertex")
        return [dict(zip(names, p)) for p in sorted(found)]


def response_value(point: dict, coefficients: dict, mass_g: int | float) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 10000
        return decimal(mass_g) * sum(point[n] * decimal(coefficients[n]) / 100 for n in point)


def scalar_extrema(weights: dict, coefficients: dict, mass_g: int | float) -> dict:
    if set(weights) != set(coefficients):
        raise ValueError("response coordinates differ")
    if decimal(mass_g) <= 0 or any(decimal(v) < 0 for v in coefficients.values()):
        raise ValueError("invalid mass or response")
    points = vertices(weights)
    values = [response_value(p, coefficients, mass_g) for p in points]
    return {"minimum": min(values), "maximum": max(values), "vertices": points}


def greedy_expected(weights: dict, coefficients: dict, maximize: bool) -> dict[str, Decimal]:
    """Independent exact deterministic witness; vertex extrema remain the oracle."""
    with localcontext() as ctx:
        ctx.prec=10000
        point={n:decimal(b["lower_wt_percent"]) for n,b in weights.items()}
        remaining=Decimal(100)-sum(point.values())
        order=sorted(weights,key=lambda n:((-decimal(coefficients[n]) if maximize
                                           else decimal(coefficients[n])),n))
        for n in order:
            addition=min(remaining,decimal(weights[n]["upper_wt_percent"])-point[n])
            point[n]+=addition
            remaining-=addition
        if remaining!=0:
            raise ValueError("greedy witness is infeasible")
        return point


def dual_value(weights: dict, coefficients: dict, mass_g: int | float,
               multiplier: int | float, upper: bool) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 10000
        lam = decimal(multiplier)
        value = 100 * lam
        for name, limits in weights.items():
            slope = decimal(coefficients[name]) / 100 - lam
            pair = [slope * decimal(limits[key]) for key in ("lower_wt_percent", "upper_wt_percent")]
            value += max(pair) if upper else min(pair)
        return decimal(mass_g) * value


def generate_cases() -> list[dict]:
    rows = json.loads(PACK.read_text(encoding="utf-8"))["rows"]
    def fraction(name: str, f: float) -> float:
        selector = {"C14": "C-14", "Tc99": "Tc-99"}[name]
        row = next(r for r in rows if r["table"] == 1 and r["selector"] == selector
                   and r["applicability"] in ("general", "all"))
        return f * (float(row["limits"][0]) * 37000.0)
    def box(**values) -> dict:
        return {n: {"lower_wt_percent": float(lo), "upper_wt_percent": float(hi)}
                for n, (lo, hi) in values.items()}
    def target(response: dict, step: int = 1, t_s: float = 0.0, incomplete: bool = False) -> dict:
        return {"step": step, "t_s": t_s, "element_activity_bq_per_g": response,
                "inventory_coverage": "incomplete" if incomplete else "complete",
                "unbounded_inventory_reasons": ["artificial unbounded inventory"] if incomplete else [],
                "bounds_source": "P108 artificial pure-constituent basis",
                "bounds_assumptions": "fixed affine model; supplied products exact within this model"}
    def component(name: str, weights: dict, targets: list[dict], properties=None, external=None) -> dict:
        products = {n for t in targets for response in t["element_activity_bq_per_g"].values() for n in response}
        if external:
            products.add("H3")
        props = {n: {"z": {"C14": 6, "Tc99": 43, "H3": 1}[n],
                     "half_life_s": 10.0 * 31557600.0, "alpha_emitting": False} for n in sorted(products)}
        return {"id": name, "mass_g": 1.0, "displaced_volume_cm3": 1.0,
                "waste_type": "general", "nuclide_properties": props if properties is None else properties,
                "external_tritium": external or {"status": "not_applicable"},
                "composition_wt_percent_bounds": weights, "targets": targets}
    fixed = box(Nb=(100,100))
    zero = box(Si=(0,100), Fe=(0,100))
    correlated = box(Si=(10,10), Nb=(20,60), Fe=(30,70))
    one = {"Si": {}, "Nb": {"C14": fraction("C14",.25)}, "Fe": {}}
    opposite = {"Si": {}, "Nb": {"C14": fraction("C14",1)}, "Fe": {"Tc99": fraction("Tc99",1)}}
    ext = lambda lo,hi: {"status": "bounded", "source": "artificial disjoint external H-3",
                        "excludes_activation": True,
                        "activity_bounds_bq": {"1": {"lower_bq": float(lo), "upper_bq": float(hi)}}}
    cases = [
        component("single_fixed",fixed,[target({"Nb":{"C14":fraction("C14",.05)}})]),
        component("all_zero",zero,[target({"Si":{},"Fe":{}})]),
        component("correlated_two",correlated,[target(one)]),
        component("opposite_products",correlated,[target(opposite)]),
        component("fixed_three",box(Si=(10,10),Nb=(60,60),Fe=(30,30)),[target(opposite)]),
        component("coefficient_tie",zero,[target({"Si":{"C14":fraction("C14",.05)},"Fe":{"C14":fraction("C14",.05)}})]),
        component("zero_crossing",box(Si=(10,10),Nb=(0,60),Fe=(30,90)),
                  [target({"Si":{"Tc99":fraction("Tc99",.5)},"Nb":{"C14":fraction("C14",.25)},"Fe":{}})]),
        component("external_h3",fixed,[target({"Nb":{}})],external=ext(740000,2220000)),
        component("external_merge",fixed,[target({"Nb":{"H3":888000.0}})],external=ext(888000,888000)),
        component("incomplete",zero,[target({"Si":{},"Fe":{}},incomplete=True)]),
        component("missing_properties",box(Si=(0,100),Nb=(0,100)),[target({"Si":{},"Nb":{"C14":74000.0}})],properties={}),
        component("two_times",correlated,[target(one),target({n:{"C14":fraction("C14",.02)} for n in correlated},2,100.0)]),
    ]
    return copy.deepcopy(cases)


def derive_population(cases: list[dict]) -> list[dict]:
    records = []
    for comp in cases:
        targets = []
        for target in comp["targets"]:
            response = target["element_activity_bq_per_g"]
            products = sorted({n for r in response.values() for n in r})
            projected = {}
            for n in products:
                coeff = {e: response[e].get(n,0.0) for e in response}
                exact = scalar_extrema(comp["composition_wt_percent_bounds"], coeff, comp["mass_g"])
                projected[n] = {"minimum_exact_bq": str(exact["minimum"]),
                                "maximum_exact_bq": str(exact["maximum"]),
                                "vertex_count": len(exact["vertices"]),
                                "min_witness_exact_wt_percent": {e:str(v) for e,v in greedy_expected(
                                    comp["composition_wt_percent_bounds"],coeff,False).items()},
                                "max_witness_exact_wt_percent": {e:str(v) for e,v in greedy_expected(
                                    comp["composition_wt_percent_bounds"],coeff,True).items()}}
            targets.append({"step": target["step"], "projections": projected})
        records.append({"input": comp, "exact": {"id": comp["id"], "targets": targets}})
    return records


def verify_projection(actual: object, weights: dict, coefficients: dict, mass_g: float) -> list[str]:
    errors = []
    if not isinstance(actual,dict):
        return ["projection is not an object"]
    exact = scalar_extrema(weights,coefficients,mass_g)
    try:
        lo,hi = decimal(actual["lower_bq"]),decimal(actual["upper_bq"])
        minimum,maximum = exact["minimum"],exact["maximum"]
        if not Decimal(0) <= lo <= minimum <= maximum <= hi:
            errors.append("scalar interval does not contain exact feasible extrema")
        with localcontext() as ctx:
            ctx.prec=10000
            for value,want in ((lo,minimum),(hi,maximum)):
                if abs(value-want)>max(Decimal("1e-12"),Decimal("1e-10")*abs(want)):
                    errors.append("scalar interval exceeds frozen tightness tolerance")
        if all(v==0 for v in coefficients.values()) and (lo!=0 or hi!=0):
            errors.append("all-zero response is not exact zero")
        for field,want in (("min_witness_wt_percent",minimum),("max_witness_wt_percent",maximum)):
            point = actual[field]
            if not isinstance(point,dict) or set(point)!=set(weights):
                errors.append(f"{field}: coordinate identities differ")
                continue
            point={n:decimal(v) for n,v in point.items()}
            deterministic=greedy_expected(weights,coefficients,field.startswith("max_"))
            with localcontext() as ctx:
                ctx.prec=10000
                if abs(sum(point.values())-100)>Decimal("1e-10"):
                    errors.append(f"{field}: equality is infeasible")
                for n,v in point.items():
                    if abs(v-deterministic[n])>Decimal("1e-10"):
                        errors.append(f"{field}: deterministic greedy witness differs")
                    if (v < decimal(weights[n]["lower_wt_percent"])-Decimal("1e-10") or
                            v > decimal(weights[n]["upper_wt_percent"])+Decimal("1e-10")):
                        errors.append(f"{field}: coordinate outside box")
                value=response_value(point,coefficients,mass_g)
                if abs(value-want)>max(Decimal("1e-12"),Decimal("1e-10")*abs(want)):
                    errors.append(f"{field}: witness response is not optimal")
        candidates={0.0}|{float(v)/100.0 for v in coefficients.values()}
        for upper,field,bound in ((False,"lower_dual_lambda",lo),(True,"upper_dual_lambda",hi)):
            lam=actual[field]
            if lam not in candidates:
                errors.append(f"{field}: multiplier is not a declared candidate")
            certificate=dual_value(weights,coefficients,mass_g,lam,upper)
            if (upper and bound<certificate) or (not upper and bound>max(Decimal(0),certificate)):
                errors.append(f"{field}: output does not enclose its exact dual certificate")
    except (KeyError,ValueError,TypeError,ArithmeticError) as error:
        errors.append(f"malformed projection: {error}")
    return errors


def _projected_component(component: dict, intervals: list[dict]) -> dict:
    value = {k: copy.deepcopy(v) for k,v in component.items()
             if k not in ("composition_wt_percent_bounds", "targets")}
    value["targets"] = []
    for target,bounds in zip(component["targets"],intervals):
        t = {k:copy.deepcopy(v) for k,v in target.items() if k!="element_activity_bq_per_g"}
        t["activity_bounds_bq"] = copy.deepcopy(bounds)
        value["targets"].append(t)
    return value


def expected_labels(cases: list[dict], rows: list[dict]) -> dict:
    """Derive endpoint labels from exact vertex extrema, independent of Rust."""
    from p106_bounds_control import derive_case
    labels = {}
    for comp in cases:
        intervals=[]
        for target in comp["targets"]:
            response=target["element_activity_bq_per_g"]
            products=sorted({n for r in response.values() for n in r})
            bounds={}
            for n in products:
                exact=scalar_extrema(comp["composition_wt_percent_bounds"],
                                     {e:r.get(n,0.0) for e,r in response.items()},comp["mass_g"])
                bounds[n]={"lower_bq":float(exact["minimum"]),"upper_bq":float(exact["maximum"])}
            intervals.append(bounds)
        projected=_projected_component(comp,intervals)
        derived=derive_case({"id":comp["id"],"component":projected},rows)
        labels[comp["id"]]=[t["class_envelope"] for t in derived["targets"]]
    frozen={"single_fixed":[["A"]],"all_zero":[["A"]],
            "correlated_two":[["A","B","C"]],"opposite_products":[["C","above_class_c"]],
            "fixed_three":[["C"]],"coefficient_tie":[["A"]],"zero_crossing":[["A","B","C"]],
            "external_h3":[["A","B"]],"external_merge":[["B"]],
            "incomplete":[["unknown"]],"missing_properties":[["unknown"]],
            "two_times":[["A","B","C"],["A"]]}
    opposite=next(c for c in cases if c["id"]=="opposite_products")
    points=vertices(opposite["composition_wt_percent_bounds"])
    response=opposite["targets"][0]["element_activity_bq_per_g"]
    for point in points:
        with localcontext() as ctx:
            ctx.prec=10000
            fractions=sum(response_value(point,{e:r.get(n,0.0) for e,r in response.items()},1.0)
                          / decimal(limit*37000.0) for n,limit in (("C14",8.0),("Tc99",3.0)))
            if fractions!=Decimal("0.9"):
                return {"pass":False,"labels":labels,"error":"opposite response correlation differs"}
    return {"pass":labels==frozen,"labels":labels,"frozen_labels":frozen,
            "opposite_products_every_vertex_fraction":"0.9"}


def verify_report(actual: object, spec: dict, rows: list[dict]) -> list[str]:
    """Validate optimization first, then classify its certified projected box."""
    from check_p107 import _validate_report
    from p106_bounds_control import derive_case
    errors=[]
    if not isinstance(actual,dict):
        return ["result is not an object"]
    for field,want in (("schema","actinv-waste-composition-result-1"),
                       ("method","declared_affine_composition_polytope"),
                       ("response_model","fixed_rate_affine_activity"),
                       ("activity_unit","Bq"),("composition_unit","wt_percent"),
                       ("response_unit","Bq/g"),("input_sha256",spec["_input_sha256"])):
        if actual.get(field)!=want:
            errors.append(f"root {field} differs")
    components=actual.get("components")
    if not isinstance(components,list) or len(components)!=len(spec["components"]):
        return errors+["component count differs"]
    if any(not isinstance(c,dict) for c in components):
        return errors+["component is not an object"]
    by_id={c.get("id"):c for c in components}
    if len(by_id)!=len(components):
        errors.append("duplicate component identities")
    projected_components=[]
    expected_by_id={}
    for comp in spec["components"]:
        got=by_id.get(comp["id"])
        if not isinstance(got,dict):
            errors.append(f"missing component {comp['id']}")
            continue
        if got.get("composition_wt_percent_bounds")!=comp["composition_wt_percent_bounds"]:
            errors.append(f"{comp['id']}: composition bounds differ")
        if got.get("composition_sum_constraint")!={"operator":"equal","value_wt_percent":100.0}:
            errors.append(f"{comp['id']}: composition equality differs")
        actual_targets=got.get("targets")
        if not isinstance(actual_targets,list) or any(not isinstance(t,dict) for t in actual_targets):
            errors.append(f"{comp['id']}: malformed target list")
            continue
        target_by_step={t.get("step"):t for t in actual_targets}
        if len(target_by_step)!=len(actual_targets) or len(actual_targets)!=len(comp["targets"]):
            errors.append(f"{comp['id']}: target identities/count differ")
        intervals=[]
        for target in comp["targets"]:
            t=target_by_step.get(target["step"])
            if not isinstance(t,dict):
                errors.append(f"{comp['id']}: missing target {target['step']}")
                intervals.append({})
                continue
            response=target["element_activity_bq_per_g"]
            if t.get("t_s")!=target["t_s"]:
                errors.append(f"{comp['id']}: target time differs")
            if t.get("element_activity_bq_per_g")!=response:
                errors.append(f"{comp['id']}: response basis differs")
            products=sorted({n for r in response.values() for n in r})
            records=t.get("projection_records")
            if not isinstance(records,dict) or set(records)!=set(products):
                errors.append(f"{comp['id']}: projection identities differ")
                intervals.append({})
                continue
            bounds={}
            for n in products:
                coeff={e:r.get(n,0.0) for e,r in response.items()}
                problems=verify_projection(records[n],comp["composition_wt_percent_bounds"],coeff,comp["mass_g"])
                errors.extend(f"{comp['id']} step{target['step']} {n}: {p}" for p in problems)
                if not isinstance(records[n],dict) or "lower_bq" not in records[n] or "upper_bq" not in records[n]:
                    continue
                bounds[n]={k:records[n][k] for k in ("lower_bq","upper_bq")}
            intervals.append(bounds)
        projected=_projected_component(comp,intervals)
        projected_components.append(projected)
        # Only already independently validated intervals can feed the inherited
        # scalar rule oracle. Malformed optimization reports stop before it.
        if errors:
            continue
        expected_by_id[comp["id"]]=derive_case({"id":comp["id"],"component":projected},rows)
    if errors:
        return errors
    projected_spec={"schema":"actinv-waste-bounds-spec-1","rules":spec["rules"],
                    "components":projected_components}
    projected_text=json.dumps(projected_spec,sort_keys=True,separators=(",",":"),allow_nan=False)
    if actual.get("projected_input_sha256")!=hashlib.sha256(projected_text.encode()).hexdigest():
        errors.append("projected input SHA differs from independently reconstructed input")
    bounds_result=copy.deepcopy(actual)
    bounds_result["schema"]="actinv-waste-bounds-result-1"
    bounds_result["method"]="declared_activity_box"
    projected_spec["_input_sha256"]=spec["_input_sha256"]
    errors.extend(_validate_report(bounds_result,projected_spec,expected_by_id,rows))
    return errors
