#!/usr/bin/env python3
"""P109 synthetic native-composition inputs and independent analytic controls.

All reaction/decay values are artificial. No production nuclear library is
used. This module never launches the application; check_p109 owns bounded CLI
execution and imports the reviewed P105 process runner.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import zipfile
from decimal import Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"
ABUNDANCE = ROOT / "results/tables/abundance_mass.json"
FIXTURE = ROOT / "controls/fixtures/p109/cases.json"
CASE_SCHEMA = "actinv-p109-composition-cases-1"
AVOGADRO = Decimal("6.02214076e23")
NB_MOLAR_MASS = Decimal("92.90637317")
CAPTURE_RATE = Decimal("1e-12")
IRRADIATION_S = Decimal("1e6")
COOLING_S = Decimal("1e6")


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _npy(descr: str, shape: tuple[int, ...], payload: bytes) -> bytes:
    shape_text = "(" + ", ".join(map(str, shape)) + ("," if len(shape) == 1 else "") + ")"
    header = ("{'descr': '" + descr + "', 'fortran_order': False, 'shape': " + shape_text + ", }").encode()
    padding = (16 - ((10 + len(header) + 1) % 16)) % 16
    header += b" " * padding + b"\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header + payload


def _endf_record(values: list[float | int], mat: int, mf: int, mt: int, seq: int) -> str:
    fields = "".join(f"{int(v):11d}" if isinstance(v, int) else f"{float(v):11.4E}" for v in values)
    return fields + f"{mat:4d}{mf:2d}{mt:3d}{seq:5d}"


_NUCLIDES = [
    (2601, 26054, 53.939608189, "Fe54", True, 0.0, None),
    (2602, 26056, 55.934935537, "Fe56", True, 0.0, None),
    (2603, 26057, 56.935391950, "Fe57", True, 0.0, None),
    (2604, 26058, 57.933273575, "Fe58", True, 0.0, None),
    (1401, 14028, 27.97692653442, "Si28", True, 0.0, None),
    (1402, 14029, 28.97649466434, "Si29", True, 0.0, None),
    (1403, 14030, 29.973770137, "Si30", True, 0.0, None),
    (4101, 41093, 92.90637317, "Nb93", True, 0.0, None),
    (4102, 41094, 93.907279, "Nb94", False, 1.0e11, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)),
    (4201, 42094, 93.905083586, "Mo94", True, 0.0, None),
    (1001, 1003, 3.016049281, "H3", False, 1.0e9, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)),
    (2001, 2003, 3.016029321, "He3", True, 0.0, None),
]


def decay_bytes(*, omit_nb94: bool = False) -> bytes:
    records: list[str] = []
    for mat, za, awr, _name, stable, half_life, mode in _NUCLIDES:
        if omit_nb94 and za == 41094:
            continue
        seq = 1
        records.append(_endf_record([za, awr, 0, 0, int(stable), 0], mat, 8, 457, seq)); seq += 1
        # MF8/MT457 half-life/energy LIST has six values; mode LIST has one
        # correctly encoded RTYP=1 beta-minus branch for Nb94 and H3.
        records.append(_endf_record([half_life, 0.0, 0, 0, 6, 0], mat, 8, 457, seq)); seq += 1
        records.append(_endf_record([0.0] * 6, mat, 8, 457, seq)); seq += 1
        if mode is None:
            records.append(_endf_record([0, 0, 0, 0, 0, 0], mat, 8, 457, seq)); seq += 1
        else:
            records.append(_endf_record([0, 0, 0, 0, 6, 1], mat, 8, 457, seq)); seq += 1
            records.append(_endf_record(list(mode), mat, 8, 457, seq)); seq += 1
        records.append(_endf_record([0] * 6, mat, 8, 0, seq))
    return ("\n".join(records) + "\n").encode()


def _target_records(*, omit_fe58: bool = False) -> list[dict]:
    ids = [(za, 0, mat, awr, name) for mat, za, awr, name, *_ in _NUCLIDES[:10]]
    if omit_fe58:
        ids = [entry for entry in ids if entry[0] != 26058]
    records = []
    for za, liso, mat, awr, name in ids:
        descriptor = {"za": za, "liso": liso, "mat": mat, "name": name,
                      "channel": "MT102 capture; zero barns" if za != 41093 else "MT102 capture; one barn"}
        records.append({"file": "p109-artificial-reaction-fixture.json",
                        "source_sha256": sha_bytes(_json_bytes(descriptor)), "mat": mat,
                        "za": za, "liso": liso, "awr": awr, "ledger": []})
    return records


def library_bytes(*, omit_fe58: bool = False) -> bytes:
    targets = _target_records(omit_fe58=omit_fe58)
    rows: list[tuple[int, int, int, int, int]] = []
    sig: list[float] = []
    for i, target in enumerate(targets):
        za = target["za"]
        if za == 41093:
            # Total capture loss and its one product branch carry the same
            # 1-barn one-group cross section, as in the P105 fixture.
            rows.extend(((i, 102, -1, -1, 0), (i, 102, 41094, 0, 3)))
            sig.extend((1.0, 1.0))
        else:
            rows.append((i, 102, -1, -1, 0))
            sig.append(0.0)
    payload = struct.pack("<" + "q" * len(rows) * 5, *(v for row in rows for v in row))
    row_array = _npy("<i8", (len(rows), 5), payload)
    sig_array = _npy("<f8", (len(sig), 1), struct.pack("<" + "d" * len(sig), *sig))
    bounds_array = _npy("<f8", (2,), struct.pack("<2d", 1.0, 2.0))
    import io
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in (("rows.npy", row_array), ("sig.npy", sig_array), ("bounds.npy", bounds_array)):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    return out.getvalue()


def _index_bytes(npz: bytes, *, omit_fe58: bool = False) -> bytes:
    value = {"schema": "actinv-library-index-1", "sha256_npz": sha_bytes(npz),
             "group_boundary_sha256": sha_bytes(b"ACTINV-GROUP-BOUNDARIES-v1\0" + struct.pack("<2d", 1.0, 2.0)),
             "groups": "custom", "temperature_K": 293.6, "projectile": "neutron",
             "targets": _target_records(omit_fe58=omit_fe58)}
    return _json_bytes(value)


def write_fixture(root: Path) -> dict:
    work = root / "target/p109-controls"
    work.mkdir(parents=True, exist_ok=True)
    products = {}
    for variant in ("complete", "omit_fe58_target", "omit_nb94_decay"):
        omit_fe58 = variant == "omit_fe58_target"
        omit_nb94 = variant == "omit_nb94_decay"
        npz = library_bytes(omit_fe58=omit_fe58)
        idx = _index_bytes(npz, omit_fe58=omit_fe58)
        decay = decay_bytes(omit_nb94=omit_nb94)
        names = {"complete": "", "omit_fe58_target": ".omit_fe58", "omit_nb94_decay": ".omit_nb94_decay"}[variant]
        files = {"library": work / f"native{names}.npz",
                 "index": work / f"native{names}.npz_index.json",
                 "decay": work / f"decay{names}.endf"}
        payloads = {"library": npz, "index": idx, "decay": decay}
        for key, path in files.items():
            path.write_bytes(payloads[key])
        products[variant] = {"paths": {k: str(v.relative_to(root)) for k,v in files.items()},
                             "sha256": {k: sha_bytes(v) for k,v in payloads.items()},
                             "target_count": len(_target_records(omit_fe58=omit_fe58)),
                             "row_count": 11 if not omit_fe58 else 10,
                             "decay_record_count": len(_NUCLIDES) - int(omit_nb94)}
    base_specs = {}
    for variant in ("complete", "omit_fe58_target", "omit_nb94_decay"):
        paths = products[variant]["paths"]
        base = {"spec": "actinv-spec-1", "title": "P109 artificial natural-element capture and decay",
                "projectile": "neutron", "library": {"path": paths["library"],
                "sha256": products[variant]["sha256"]["library"]},
                "decay": {"primary": paths["decay"]},
                "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"Fe": 50.0,"Nb": 1.0,"Si": 49.0}},
                "spectrum": {"structure": "custom", "boundaries_eV": [1.0,2.0], "flux_per_group": [1e12],
                             "total": 1e12, "descending": False},
                "schedule": [{"dt": "1e6 s", "flux": 1.0},{"dt": "1e6 s", "flux": 0.0}],
                "options": {"mode": "coupled", "prune": "reach", "bmin_atoms_per_g": 0.0,
                            "temperature_K": 293.6, "outputs": ["ledger","audit"]}}
        base_path = work / f"base_run.{variant}.json"
        base_path.write_bytes(_json_bytes(base))
        base_specs[variant] = {"path": str(base_path.relative_to(root)), "sha256": sha_bytes(base_path.read_bytes())}
    products["base_specs"] = base_specs
    return products


def fixture_document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def generate_cases() -> list[dict]:
    return fixture_document()["cases"]


def composition_for(case: dict, x: Decimal | None = None) -> dict[str, Decimal]:
    bounds = case["composition_wt_percent_bounds"]
    if x is None:
        return {name: Decimal(str(bound["lower_wt_percent"])) for name,bound in bounds.items()}
    return {"Fe": Decimal("50"), "Nb": x, "Si": Decimal("50")-x}


def _exp(value: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 100
        return value.exp()


def analytic_nb_activity(weight_percent: Decimal, step: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 80
        t = IRRADIATION_S
        lam = Decimal(2).ln() / Decimal("1e11")
        k = CAPTURE_RATE
        n0 = AVOGADRO / NB_MOLAR_MASS
        n94 = n0 * k / (lam-k) * ((-k*t).exp() - (-lam*t).exp())
        if step == 2:
            n94 *= (-lam*COOLING_S).exp()
        elif step != 1:
            raise ValueError("P109 analytic fixture has selected steps 1 and 2")
        return weight_percent / 100 * lam * n94


def analytic_nb_atoms(weight_percent: Decimal, step: int) -> dict[str, Decimal]:
    with localcontext() as ctx:
        ctx.prec = 80
        n0 = AVOGADRO / NB_MOLAR_MASS * weight_percent / 100
        k, lam, t = CAPTURE_RATE, Decimal(2).ln() / Decimal("1e11"), IRRADIATION_S
        n93 = n0 * (-k*t).exp()
        n94 = n0 * k/(lam-k) * ((-k*t).exp() - (-lam*t).exp())
        mo = n0 - n93 - n94
        if step == 2:
            lost = n94 * (Decimal(1)-(-lam*COOLING_S).exp())
            n94 -= lost
            mo += lost
        elif step != 1:
            raise ValueError("P109 analytic fixture has selected steps 1 and 2")
        return {"Nb93": n93, "Nb94": n94, "Mo94": mo}


def analytical_inventory(composition: dict[str, Decimal], step: int) -> dict[str, Decimal]:
    tables = json.loads(ABUNDANCE.read_text(encoding="utf-8"))
    abund = tables["abundance"]
    masses = tables["mass_amu"]
    out: dict[str, Decimal] = {}
    for element in ("Fe", "Si"):
        weight = composition.get(element, Decimal(0)) / 100
        molar_mass = sum(Decimal(str(abund[element][key])) * Decimal(str(masses[key]))
                         for key in abund[element])
        for key, fraction in abund[element].items():
            out[key] = AVOGADRO * Decimal(str(fraction)) / molar_mass * weight
    out.update(analytic_nb_atoms(composition.get("Nb", Decimal(0)), step))
    return out


def analytical_inventory_without_nb94_decay(composition: dict[str,Decimal],step:int)->dict[str,Decimal]:
    with localcontext() as ctx:
        ctx.prec=80
        out=analytical_inventory(composition,step)
        n0=AVOGADRO/NB_MOLAR_MASS*composition.get("Nb",Decimal(0))/100
        n93=n0*(-CAPTURE_RATE*IRRADIATION_S).exp()
        # With Nb94 decay data absent, chain construction omits that state and
        # records the capture product as leakage; it does not retain Nb94 atoms.
        out.pop("Nb94",None)
        out.pop("Mo94",None)
        return out


def _close(a: object, b: object, *, rel: float = 1e-6, abs_: float = 1e-12) -> bool:
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(x) and math.isfinite(y) and abs(x-y) <= max(abs_, rel*max(abs(x),abs(y)))


def _close_map(actual: object, expected: dict[str, Decimal], *, rel: float=1e-6,
               abs_: float=1e-12, exact_keys: bool=False) -> list[str]:
    if not isinstance(actual,dict): return ["value map is not an object"]
    try:
        got={str(k):float(v) for k,v in actual.items()}
    except (TypeError,ValueError,OverflowError): return ["value map contains a nonnumeric value"]
    want={k:float(v) for k,v in expected.items()}
    errors=[]
    if exact_keys and set(got)!=set(want): errors.append("value map identities differ")
    for key in set(got)|set(want):
        a=got.get(key,0.0); b=want.get(key,0.0)
        if not math.isfinite(a) or abs(a-b)>max(abs_,rel*max(abs(a),abs(b))):
            errors.append(f"{key} value differs")
    return errors


def _atoms_close(actual: object, expected: dict[str,Decimal]) -> list[str]:
    if not isinstance(actual,dict): return ["atom map is not an object"]
    try: got={str(k):float(v) for k,v in actual.items()}
    except (TypeError,ValueError,OverflowError): return ["atom map has nonnumeric values"]
    want={k:float(v) for k,v in expected.items()}
    if any(not math.isfinite(v) or v<0 for v in got.values()): return ["atom map has invalid values"]
    keys=set(got)|set(want)
    error=sum(abs(got.get(k,0.0)-want.get(k,0.0)) for k in keys)
    l1a=sum(abs(v) for v in got.values()); l1b=sum(abs(v) for v in want.values())
    if error>max(1e-12,1e-6*max(l1a,l1b)): return ["atom inventory L1 differs"]
    protected=[k for k in keys if k.startswith("Fe") or k.startswith("Si") or k=="Nb93"]
    return [f"stable isotope {k} differs" for k in protected
            if not _close(got.get(k,0.0),want.get(k,0.0))]


def expected_inventory(case: dict, composition: dict, step: int) -> tuple[dict[str,Decimal],dict[str,Decimal]]:
    """Independent per-gram activity and atom reference for an artificial composition."""
    weights={str(k):Decimal(str(v)) for k,v in composition.items()}
    atoms=(analytical_inventory_without_nb94_decay(weights,step) if case.get("variant")=="omit_nb94_decay"
           else analytical_inventory(weights,step))
    activity={} if case.get("variant")=="omit_nb94_decay" else {"Nb94":analytic_nb_activity(weights.get("Nb",Decimal(0)),step)}
    if activity and activity["Nb94"]==0: activity={}
    return activity,atoms


def _range_witnesses(case: dict, projections: dict) -> dict[tuple[tuple[str,float],...],list[dict]]:
    """Expected deduplicated full maps and references, independently greedy."""
    import p108_composition_control as p108
    weights=case["composition_wt_percent_bounds"]
    found={}
    target_rows=projections if isinstance(projections,list) else []
    for target in target_rows:
        responses=target.get("element_activity_bq_per_g",{})
        nuclides=sorted({n for values in responses.values() for n in values})
        for nuclide in nuclides:
            coeff={e:values.get(nuclide,0.0) for e,values in responses.items()}
            for endpoint,maximize in (("lower",False),("upper",True)):
                point=p108.greedy_expected(weights,coeff,maximize)
                key=tuple((n,float(v)) for n,v in sorted(point.items()))
                found.setdefault(key,[]).append({"step":target["step"],"nuclide":nuclide,"endpoint":endpoint})
    zero={name:0.0 for name in weights}
    point=p108.greedy_expected(weights,zero,False)
    key=tuple((n,float(v)) for n,v in sorted(point.items()))
    found.setdefault(key,[]).append({"kind":"canonical_reference"})
    return found


def validate_report(case: dict, actual: object, *, input_sha256: str,
                    expected_projection: dict | None=None, rows: list[dict] | None=None) -> list[str]:
    """Independent P109 result validator; consumes no production arithmetic."""
    import copy
    import p108_composition_control as p108
    from p106_bounds_control import _p105_oracle
    errors=[]
    if not isinstance(actual,dict): return ["result is not an object"]
    for key,want in (("schema","actinv-waste-composition-solve-result-1"),
                     ("method","native_fixed_rate_composition_polytope"),
                     ("response_model","fixed_rate_affine_activity"),
                     ("input_sha256",input_sha256),("response_unit","Bq/g"),
                     ("activity_unit","Bq"),("composition_unit","wt_percent"),
                     ("atom_inventory_unit","atoms/g")):
        if actual.get(key)!=want: errors.append(f"root {key} differs")
    components=actual.get("components")
    if not isinstance(components,list) or len(components)!=1:
        return errors+["expected one output component"]
    component=components[0]
    expected_rules={"id":"us-nrc-10cfr61.55-v1","sha256":"890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7"}
    if not isinstance(actual.get("rules"),dict) or any(actual["rules"].get(k)!=v for k,v in expected_rules.items()): errors.append("rule identity differs")
    generated_text=actual.get("generated_response_spec_json")
    if not isinstance(generated_text,str) or sha_bytes(generated_text.encode())!=actual.get("generated_response_input_sha256"):
        errors.append("generated response input text/hash differs")
    try: generated_doc=json.loads(generated_text)
    except (TypeError,ValueError): generated_doc={}
    work=actual.get("solver_work",{})
    if not isinstance(work,dict) or work.get("basis_solve_count")!=len(case["composition_wt_percent_bounds"]): errors.append("basis work count differs")
    if component.get("id")!=case["id"]: errors.append("component identity differs")
    if component.get("composition_wt_percent_bounds")!=case["composition_wt_percent_bounds"]:
        errors.append("composition bounds differ")
    if (component.get("mass_g")!=1.0 or component.get("waste_type")!="activated_metal"
            or component.get("displaced_volume_cm3")!=case["displaced_volume_cm3"]
            or component.get("density_g_cm3") is not None):
        errors.append("component mass or waste type differs")
    native_block=component.get("native")
    if not isinstance(native_block,dict): return errors+["native evidence block missing"]
    native=native_block.get("native_verification")
    if not isinstance(native,dict) or native.get("passed") is not True: return errors+["native verification missing or failed"]
    expected_props={}
    if "Nb" in case["composition_wt_percent_bounds"] and case.get("variant")!="omit_nb94_decay":
        expected_props["Nb94"]={"z":41,"half_life_s":1e11,"alpha_emitting":False}
    if case.get("external_tritium",{}).get("status")=="bounded" and any(
        v.get("upper_bq",0)>0 for v in case["external_tritium"].get("activity_bounds_bq",{}).values()):
        expected_props["H3"]={"z":1,"half_life_s":1e9,"alpha_emitting":False}
    if component.get("nuclide_properties")!=expected_props: errors.append("derived active nuclide properties differ")
    basis=native_block.get("solver_basis")
    weights=case["composition_wt_percent_bounds"]
    if not isinstance(basis,list) or {x.get("element") for x in basis if isinstance(x,dict)}!=set(weights):
        return errors+["pure-element basis identities differ"]
    if native.get("unique_witness_count")!=len(native.get("verified_witnesses",[])):
        errors.append("unique witness count differs from records")
    generated_components=generated_doc.get("components",[]) if isinstance(generated_doc,dict) else []
    if (generated_doc.get("schema")!="actinv-waste-composition-spec-1" or
        generated_doc.get("rules")!="us-nrc-10cfr61.55-v1" or len(generated_components)!=1):
        errors.append("generated P108 document identity differs")
    elif generated_components:
        generated_component=generated_components[0]
        for field,want in (("id",case["id"]),("mass_g",1.0),("waste_type","activated_metal"),
                           ("displaced_volume_cm3",case["displaced_volume_cm3"]),
                           ("composition_wt_percent_bounds",weights),
                           ("external_tritium",case["external_tritium"])):
            if generated_component.get(field)!=want: errors.append(f"generated P108 component {field} differs")
    by_element={x["element"]:x for x in basis}
    coverage_records=native_block.get("coverage_evidence",[])
    if not isinstance(coverage_records,list): coverage_records=[]
    evidence_by_element={item.get("element"):item for item in coverage_records
                         if isinstance(item,dict) and item.get("element") is not None}
    steps=(1,2)
    for elem,record in by_element.items():
        if record.get("composition_wt_percent")!={elem:100.0}: errors.append(f"{elem} basis composition differs")
        audit_record=evidence_by_element.get(elem,{})
        scan_reasons=audit_record.get("reasons",[])
        if record.get("coverage_reasons")!=scan_reasons: errors.append(f"{elem} basis reasons differ from its audit")
        evidence=audit_record.get("evidence",{})
        reached=evidence.get("reached_positive_inventory",[]) if isinstance(evidence,dict) else []
        if case.get("variant")=="omit_fe58_target":
            if not any(r.get("nuclide")=="Fe58" and r.get("target",{}).get("activation_target_present") is False for r in reached):
                errors.append(f"{elem} audit does not independently show positive Fe58 without target")
        if case.get("variant")=="omit_nb94_decay":
            omission=evidence.get("omission_ledger",{}) if isinstance(evidence,dict) else {}
            missing=omission.get("products_no_evaluated_decay_data",0)
            if not (isinstance(missing,(int,float)) and not isinstance(missing,bool) and missing>0):
                errors.append(f"{elem} audit does not independently show missing Nb94 decay data")
        targets=record.get("targets",[])
        if len(targets)!=2 or {x.get("step") for x in targets}!=set(steps): errors.append(f"{elem} basis target identities differ"); continue
        for t in targets:
            step=t["step"]
            composition={name:Decimal(100 if name==elem else 0) for name in weights}
            want_activity,want_atoms=expected_inventory(case,composition,step)
            errors.extend(f"basis {elem} step{step} activity: {e}" for e in _close_map(t.get("activity_bq_per_g"),want_activity))
            errors.extend(f"basis {elem} step{step} atoms: {e}" for e in _atoms_close(t.get("atoms_per_g"),want_atoms))
            if not _close(t.get("t_s"),2e6 if step==2 else 1e6): errors.append("basis target time differs")
    # Reconstruct the canonical P108 input from the checked basis records.
    if generated_components:
        generated_component=generated_components[0]
        if generated_component.get("nuclide_properties")!=component.get("nuclide_properties"):
            errors.append("generated properties differ from evaluated component properties")
        gen_targets=generated_component.get("targets",[])
        coverage_evidence=native_block.get("coverage_evidence",[])
        scan_reasons={reason for item in coverage_evidence if isinstance(item,dict)
                      for reason in item.get("reasons",[])}
        final_reasons=sorted(scan_reasons | set(case["unbounded_inventory_reasons"]))
        expected_coverage="complete" if case["inventory_coverage"]=="complete" and not final_reasons else "incomplete"
        if len(gen_targets)!=2 or {t.get("step") for t in gen_targets}!={1,2}:
            errors.append("generated P108 target identities differ")
        else:
            for target_row in gen_targets:
                step=target_row["step"]
                basis_responses={elem:next(t for t in by_element[elem]["targets"] if t["step"]==step)["activity_bq_per_g"]
                                 for elem in weights}
                if target_row.get("element_activity_bq_per_g")!=basis_responses:
                    errors.append(f"generated P108 step{step} differs from native pure basis")
                if target_row.get("t_s")!=(1e6 if step==1 else 2e6): errors.append("generated target time differs")
                if target_row.get("inventory_coverage")!=expected_coverage: errors.append("generated coverage differs from independent audits")
                if target_row.get("unbounded_inventory_reasons")!=final_reasons: errors.append("generated omission reasons differ")
    # P108 scalar projection verifier independently checks every coefficient
    # projection, exact feasible extrema, canonical greedy witnesses and duals.
    if rows is not None:
        p108_view=copy.deepcopy(actual)
        p108_view["schema"]="actinv-waste-composition-result-1"
        p108_view["method"]="declared_affine_composition_polytope"
        p108_view["activity_unit"]="Bq"; p108_view["composition_unit"]="wt_percent"
        p108_view["response_unit"]="Bq/g"
        p108_view["input_sha256"]=actual.get("generated_response_input_sha256")
        p108_view.pop("generated_response_spec_json",None); p108_view.pop("generated_response_input_sha256",None)
        p108_view.pop("atom_inventory_unit",None); p108_view.pop("solver_work",None)
        p108_view.pop("native_verification",None)
        p108_view["components"]=[{k:v for k,v in component.items() if k not in (
            "native","base_spec_source","base_spec_sha256")}]
        if expected_projection:
            spec=copy.deepcopy(expected_projection)
            spec["_input_sha256"]=actual.get("generated_response_input_sha256")
            errors.extend(p108.verify_report(p108_view,spec,rows))
        labels=_expected_case_labels(case,rows)
        for target in component.get("targets",[]):
            step=target.get("step")
            expected_label=labels.get(step)
            if expected_label is None: errors.append("unexpected selected target step")
            elif (target.get("lower",{}).get("class")!=expected_label[0]
                  or target.get("upper",{}).get("class")!=expected_label[1]
                  or target.get("class_envelope")!=expected_label[2]):
                errors.append(f"step {step} differs from frozen P109 class envelope")
    witnesses=native.get("verified_witnesses",[])
    expected_witnesses=_range_witnesses(case,component.get("targets",[]))
    def composition_key(comp):
        return tuple((k,float(v)) for k,v in sorted(comp.items())) if isinstance(comp,dict) else None
    if not isinstance(witnesses,list) or len(witnesses)!=len(expected_witnesses):
        errors.append("witness count differs")
    else:
        actual_keys=[composition_key(w.get("composition_wt_percent")) for w in witnesses]
        if len(set(actual_keys))!=len(actual_keys) or set(actual_keys)!=set(expected_witnesses): errors.append("witness composition identities differ")
        for witness in witnesses:
            key=composition_key(witness.get("composition_wt_percent")); want_ids=expected_witnesses.get(key)
            if want_ids is None: continue
            if witness.get("witness_ids")!=want_ids: errors.append("witness linkage differs")
            coverage_item=next((item for item in coverage_records
                if item.get("composition_wt_percent")==witness.get("composition_wt_percent")),None)
            if coverage_item is None or witness.get("coverage_reasons")!=coverage_item.get("reasons"):
                errors.append("witness coverage reasons differ from audit evidence")
            comp={k:Decimal(str(v)) for k,v in witness.get("composition_wt_percent",{}).items()}
            targets=witness.get("targets",[])
            if len(targets)!=2 or {t.get("step") for t in targets}!=set(steps): errors.append("witness target set differs"); continue
            for target in targets:
                step=target["step"]
                direct_activity,direct_atoms=expected_inventory(case,comp,step)
                errors.extend(f"direct witness step{step}: {e}" for e in _close_map(target.get("solved_activity_bq_per_g"),direct_activity))
                errors.extend(f"direct witness atoms step{step}: {e}" for e in _atoms_close(target.get("solved_atoms_per_g"),direct_atoms))
                predicted=target.get("predicted_activity_bq_per_g",{})
                predicted_atoms=target.get("predicted_atoms_per_g",{})
                weighted={}
                weighted_atoms={}
                for elem,weight in comp.items():
                    for value in by_element[elem]["targets"]:
                        if value.get("step")==step:
                            for n,v in value.get("activity_bq_per_g",{}).items(): weighted[n]=weighted.get(n,0.0)+float(v)*float(weight)/100
                            for n,v in value.get("atoms_per_g",{}).items(): weighted_atoms[n]=weighted_atoms.get(n,0.0)+float(v)*float(weight)/100
                errors.extend(f"predicted witness step{step}: {e}" for e in _close_map(predicted,{k:Decimal(str(v)) for k,v in weighted.items()}))
                errors.extend(f"predicted witness atoms step{step}: {e}" for e in _atoms_close(predicted_atoms,{k:Decimal(str(v)) for k,v in weighted_atoms.items()}))
                solved_activity=target.get("solved_activity_bq_per_g",{})
                activity_keys=set(predicted)|set(solved_activity)
                activity_errors={k:abs(float(predicted.get(k,0.0))-float(solved_activity.get(k,0.0))) for k in activity_keys}
                if target.get("activity_absolute_errors_bq_per_g")!=activity_errors: errors.append("per-nuclide activity error report differs")
                activity_rel_ok=all(_close(predicted.get(k,0.0),solved_activity.get(k,0.0)) for k in activity_keys)
                if target.get("activity_per_nuclide_pass") is not activity_rel_ok: errors.append("activity per-nuclide pass flag differs")
                predicted_l1=sum(float(v) for v in predicted.values()); solved_l1=sum(float(v) for v in solved_activity.values())
                l1_error=sum(abs(float(predicted.get(k,0.0))-float(solved_activity.get(k,0.0)))
                             for k in set(predicted)|set(solved_activity))
                if not _close(target.get("predicted_activity_l1_bq_per_g"),predicted_l1): errors.append("predicted activity L1 differs")
                if not _close(target.get("solved_activity_l1_bq_per_g"),solved_l1): errors.append("solved activity L1 differs")
                if not _close(target.get("activity_l1_absolute_error_bq_per_g"),l1_error): errors.append("activity L1 error differs")
                activity_l1_pass=l1_error<=max(1e-12,1e-6*max(predicted_l1,solved_l1))
                if target.get("activity_l1_pass") is not activity_l1_pass: errors.append("activity L1 pass flag differs")
                solved_atoms=target.get("solved_atoms_per_g",{})
                atom_errors={k:abs(float(predicted_atoms.get(k,0.0))-float(solved_atoms.get(k,0.0)))
                             for k in set(predicted_atoms)|set(solved_atoms)}
                atom_l1=sum(atom_errors.values())
                atom_limit=max(1e-12,1e-6*max(sum(float(v) for v in predicted_atoms.values()),sum(float(v) for v in solved_atoms.values())))
                if not _close(target.get("atom_l1_absolute_error_per_g"),atom_l1): errors.append("atom L1 error differs")
                if not _close(target.get("atom_l1_limit_per_g"),atom_limit): errors.append("atom L1 limit differs")
                if target.get("atom_l1_pass") is not (atom_l1<=atom_limit): errors.append("atom L1 pass flag differs")
                if target.get("pass") is not True: errors.append("witness target is not passed")
                if not _close(target.get("t_s"),2e6 if step==2 else 1e6): errors.append("witness target time differs")
                # Independently classify the predicted/direct endpoint records
                # from whole-component activity, using only the P105 oracle.
                from p106_bounds_control import _endpoint
                props=component.get("nuclide_properties",{})
                h3checks=target.get("external_h3_checks",[])
                if not isinstance(h3checks,list) or len(h3checks)!=2:
                    errors.append("external-H3 endpoint check count differs")
                else:
                    expected_h3=case["external_tritium"].get("activity_bounds_bq",{}).get(str(step),{"lower_bq":0.0,"upper_bq":0.0})
                    for idx,(endpoint_name,field) in enumerate((("lower","lower_bq"),("upper","upper_bq"))):
                        check=h3checks[idx]
                        if check.get("endpoint")!=endpoint_name: errors.append("external-H3 endpoint order differs")
                        if not _close(check.get("external_h3_activity_bq"),expected_h3[field]): errors.append("external-H3 endpoint value differs")
                        for prefix,map_field in (("predicted","predicted_activity_bq_per_g"),("solved","solved_activity_bq_per_g")):
                            per_g=target.get(map_field,{})
                            whole={k:float(v)*1.0 for k,v in per_g.items()}
                            if expected_h3[field]>0: whole["H3"]=whole.get("H3",0.0)+float(expected_h3[field])
                            errors.extend(f"{prefix} whole activity step{step}: {e}" for e in _close_map(
                                check.get(f"{prefix}_activity_bq",{}),
                                {k:Decimal(str(v)) for k,v in whole.items()},exact_keys=True))
                            bounds={k:{"lower_bq":v,"upper_bq":v} for k,v in whole.items()}
                            pseudo_component={"mass_g":1.0,"displaced_volume_cm3":case["displaced_volume_cm3"],
                                "waste_type":"activated_metal","nuclide_properties":props,
                                "external_tritium":{"status":"not_applicable"}}
                            pseudo_target={"step":step,"t_s":2e6 if step==2 else 1e6,
                                "activity_bounds_bq":bounds,"inventory_coverage":case["inventory_coverage"],
                                "unbounded_inventory_reasons":case["unbounded_inventory_reasons"],
                                "bounds_source":"P109 independent witness oracle",
                                "bounds_assumptions":"exact solved point"}
                            expected_eval=_endpoint(pseudo_component,pseudo_target,False, json.loads(PACK.read_text())["rows"])
                            actual_eval=check.get(f"{prefix}_evaluation")
                            eval_errors=[]
                            from check_p107 import _check_eval
                            _check_eval(actual_eval,expected_eval,f"{prefix} witness step{step}",eval_errors)
                            errors.extend(eval_errors)
    return errors


def _expected_case_labels(case: dict, rows: list[dict]) -> dict[int,tuple[str,str,list[str]]]:
    from p106_bounds_control import _endpoint
    xlo=Decimal(str(case["composition_wt_percent_bounds"].get("Nb",{}).get("lower_wt_percent",0)))
    xhi=Decimal(str(case["composition_wt_percent_bounds"].get("Nb",{}).get("upper_wt_percent",0)))
    result={}
    for step in (1,2):
        lower=analytic_nb_activity(xlo,step); upper=analytic_nb_activity(xhi,step)
        ext=case["external_tritium"]
        low_h3=ext.get("activity_bounds_bq",{}).get(str(step),{}).get("lower_bq",0.0)
        high_h3=ext.get("activity_bounds_bq",{}).get(str(step),{}).get("upper_bq",0.0)
        component={"mass_g":1.0,"displaced_volume_cm3":case["displaced_volume_cm3"],
                   "waste_type":"activated_metal","nuclide_properties":{
                      "Nb94":{"z":41,"half_life_s":1e11,"alpha_emitting":False},
                      "H3":{"z":1,"half_life_s":1e9,"alpha_emitting":False}},
                   "external_tritium":{"status":"not_applicable"}}
        def endpoint(activity: Decimal,h3: float):
            bounds={"Nb94":{"lower_bq":float(activity),"upper_bq":float(activity)}} if activity else {}
            if h3: bounds["H3"]={"lower_bq":h3,"upper_bq":h3}
            if case["variant"]=="omit_nb94_decay":
                # The active nuclide has no properties in the effective decay map.
                component["nuclide_properties"]={"H3":{"z":1,"half_life_s":1e9,"alpha_emitting":False}}
            pseudo={"step":step,"activity_bounds_bq":bounds,"inventory_coverage":case["inventory_coverage"],
                    "unbounded_inventory_reasons":case["unbounded_inventory_reasons"]}
            return _endpoint(component,pseudo,False,rows)
        lo=endpoint(lower,low_h3); hi=endpoint(upper,high_h3)
        incomplete=(case["inventory_coverage"]!="complete" or case["variant"]!="complete"
                    or ext.get("status")=="required")
        if incomplete:
            result[step]=( "unknown","unknown",["unknown"] )
        else:
            rank={"A":0,"B":1,"C":2,"above_class_c":3}; inverse={v:k for k,v in rank.items()}
            a,b=rank[lo["class"]],rank[hi["class"]]
            result[step]=(lo["class"],hi["class"],[inverse[x] for x in range(a,b+1)])
    return result
