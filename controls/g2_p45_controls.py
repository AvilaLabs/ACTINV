#!/usr/bin/env python3
"""P45 G2 frozen controls — synthetic and structural checks that the
scoring, ledger, and workload machinery obey the frozen contract before
the campaign runs. Emits results/g2_p45_controls.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "g2_p45_controls.json"

sys.path.insert(0, str(ROOT / "controls"))
import g2_p26b_leg as leg  # noqa: E402
import p45_campaign as camp  # noqa: E402
import p45_parity as parity  # noqa: E402
import p45_robustness as rob  # noqa: E402

T0 = "0"
RESULT_KEYS = ("total_activity_bq_per_g", "product_atoms_per_g",
               "decay_heat_w_per_g")


def result_at(t: str, vals: dict) -> dict:
    """per_time entry shaped like parse_*_result output."""
    per = {"cooling_s": float(t)}
    per.update(vals)
    return per


def case_row(wl, case, arm, status="executed", result=None):
    return {"workload": wl, "case": case, "arm": arm,
            "arms": {arm: {"status": status, "result": result}}}


def check(name, ok, detail=None):
    d = {"control": name, "pass": bool(ok)}
    if detail is not None:
        d["detail"] = detail
    return d


def main() -> int:
    checks = []
    times = ["0", "86400", "2592000"]

    # c1: parity_ok path — within tolerance everywhere
    a = {t: result_at(t, {"total_activity_bq_per_g": 105.0,
                          "product_atoms_per_g": 1000.0,
                          "decay_heat_w_per_g": 1.0})
         for t in times}
    c = {t: result_at(t, {"total_activity_bq_per_g": 100.0,
                          "product_atoms_per_g": 1000.0,
                          "decay_heat_w_per_g": 1.0})
         for t in times}
    a["0"]["top5_nuclides_by_activity"] = [
        {"nuclide": "Mn56", "activity_bq_per_g": 50.0}]
    c["0"]["top5_nuclides_by_activity"] = [
        {"nuclide": "Mn56", "activity_bq_per_g": 52.0}]
    rows = [case_row("campaign", "x", "alara", result={"per_time": a}),
            case_row("campaign", "x", "actinv_fendl",
                     result={"per_time": c})]
    out = parity.score_ledger(rows)
    ok = out["per_case"]["campaign|x"]["identical"]["status"] == \
        "parity_ok"
    checks.append(check("parity_ok_path", ok,
                        out["per_case"]["campaign|x"]["identical"]
                        .get("divergences")))

    # c2: parity_divergence path — 15% activity gap at t=0 only
    a2 = dict(a)
    a2["0"] = result_at("0", {"total_activity_bq_per_g": 116.0,
                             "product_atoms_per_g": 1000.0,
                             "decay_heat_w_per_g": 1.0})
    a2["0"]["top5_nuclides_by_activity"] = [
        {"nuclide": "Mn56", "activity_bq_per_g": 50.0}]
    rows2 = [case_row("campaign", "x", "alara",
                      result={"per_time": a2}),
             case_row("campaign", "x", "actinv_fendl",
                      result={"per_time": c})]
    out2 = parity.score_ledger(rows2)
    div = out2["per_case"]["campaign|x"]["identical"]
    checks.append(check("parity_divergence_path",
                        div["status"] == "parity_divergence"
                        and any(d["t"] == "0" for d in
                                div["divergences"]),
                        div.get("divergences")))

    # c3: arm_failure propagates (not scored as divergence)
    rows3 = [case_row("campaign", "x", "alara", status="arm_failure",
                      result=None),
             case_row("campaign", "x", "actinv_fendl",
                      result={"per_time": c})]
    out3 = parity.score_ledger(rows3)
    checks.append(check(
        "arm_failure_not_scored",
        out3["per_case"]["campaign|x"]["identical"]["status"]
        == "arm_failure"))

    # c4: arm-missing semantics — one-sided missing -> arm_failure;
    # both sides missing -> absent (e.g. mesh cells have no
    # actinv_fendl leg at all)
    rows4 = [case_row("campaign", "x", "actinv_fendl",
                      result={"per_time": c})]
    out4 = parity.score_ledger(rows4)
    st4 = out4["per_case"]["campaign|x"]["identical"]["status"]
    rows4b = [case_row("mesh", "c", "alara",
                       result={"per_time": a})]
    out4b = parity.score_ledger(rows4b)
    st4b = out4b["per_case"]["mesh|c"]["identical"]["status"]
    rows4c = [case_row("mesh", "c", "openmc",
                       result={"per_time": a})]
    out4c = parity.score_ledger(rows4c)
    st4c = out4c["per_case"]["mesh|c"]["identical"]["status"]
    checks.append(check(
        "missing_arm_semantics",
        st4 == "arm_failure" and st4b == "arm_failure"
        and st4c == "absent",
        {"one_sided": st4, "alara_only": st4b,
         "both_missing": st4c}))

    # c5: openmc step-index alignment — state i+1 is cooling time i.
    # Fake atoms: two states, activity ramps; half_life from batch.
    atoms = {"Mn56": [0.0, 1e10, 1e6, 0.0, 0.0, 0.0]}
    hl = {"Mn56": 9282.0}
    batch_row = {"workload": "campaign", "case": "_batch",
                 "arm": "openmc",
                 "arms": {"openmc": {"status": "executed",
                                    "results": {"half_life_s": hl}}}}
    om_row = case_row("campaign", "x", "openmc", result=None)
    om_row["arms"]["openmc"]["atoms_atom_per_cm3"] = atoms
    ct_row = case_row("campaign", "x", "actinv_tendl",
                      result={"per_time": c})
    out5 = parity.score_ledger([batch_row, om_row, ct_row])
    desc = (out5["per_case"]["campaign|x"]["mismatch"]
            ["openmc_total_activity_rel"])
    # expected: rel(om_tot[1], actinv_t0=100) — om_tot[1]=lambda*1e10
    import math
    lam = math.log(2) / 9282.0
    exp_rel = (lam * 1e10 - 100.0) / 100.0
    checks.append(check(
        "openmc_step_alignment",
        abs(desc["0"] - exp_rel) < 1e-9
        and desc["86400"] is not None,
        {"desc_0": desc.get("0"), "expected": exp_rel}))

    # c6: mesh flux canonical identity — ascending bounds, per-cell
    # flux equals reversed population spectrum, footer present.
    pop = json.loads(camp.POPULATION.read_text())
    cells = pop["mesh"]["cells"][:2]
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        spec_path = camp.build_mesh_spec(cells, Path(td))
        lines = (Path(td) / "flux.ndjson").read_text().splitlines()
        hdr, cell0, foot = (json.loads(lines[0]),
                            json.loads(lines[1]),
                            json.loads(lines[-1]))
        b = hdr["energy_boundaries_eV"]
        asc = all(b[i] < b[i + 1] for i in range(len(b) - 1))
        flux_ok = cell0["flux_per_group"] == list(
            reversed(cells[0]["flux_descending"]))
        foot_ok = foot["record"] == "footer" and \
            foot["cell_count"] == 2
    checks.append(check(
        "mesh_flux_canonical",
        asc and flux_ok and foot_ok
        and hdr["flux_units"] == "n cm^-2 s^-1"
        and hdr["cell_count"] == 2,
        {"ascending": asc, "flux_reversed": flux_ok,
         "footer": foot_ok}))

    # c7: digest repeat sensitivity — repeat=1 vs 3 digests differ
    d1 = camp.digest_case_arm("x", "alara",
                              {"arm": "alara", "repeat": 1})
    d3 = camp.digest_case_arm("x", "alara",
                              {"arm": "alara", "repeat": 3})
    checks.append(check("digest_repeat_sensitivity", d1 != d3))

    # c8: population integrity — 28 frozen cases, pattern-conforming
    # names, 16 distinct-spectrum cells
    cases = pop["campaign"]["cases"]
    import re
    pat = re.compile(
        r"^fe(_(co|cr|mn|v)\d+wppm)?__fns_709__"
        r"(60s|86400s|pulse_5min|cont_2y)$")
    names_ok = all(pat.match(c["case"]) for c in cases)
    cell_flux = {c["flux_sha256"] for c in pop["mesh"]["cells"]}
    checks.append(check(
        "population_integrity",
        len(cases) == 28 and names_ok and len(cell_flux) == 16,
        {"n_cases": len(cases), "names_ok": names_ok,
         "distinct_flux": len(cell_flux)}))

    # c9: robustness study carries the frozen block
    study = rob.build_study(pop)
    rb = study["robustness"]
    checks.append(check(
        "robustness_frozen_block",
        rb["samples"] == 16 and rb["seed"] == 20260924
        and rb["channels"]["cross_section_mf33"]
        and rb["channels"]["decay_constants"]
        and not rb["channels"]["fission_yields"]
        and rb["channels"]["flux_rel_std"] == 0.0
        and rb["first_order_comparison"] is False
        and len(study["cases"]["materials"]) == 12
        and len(study["cases"]["schedules"]) == 2))

    out = {"spec": "actinv-p45-g2-controls-1",
           "controls": checks,
           "all_pass": all(c["pass"] for c in checks)}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "controls": {c["control"]: c["pass"]
                                   for c in checks}}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
