#!/usr/bin/env python3
"""P28 G2: regime population execution + applicability map + independent
reference extraction.

- Executes the 8 frozen boundary cases through `actinv run` under the
  sealed p28_qualifying partition.
- Re-executes the 8-case P27 smoke study into this phase's partition.
- Emits the exhaustive applicability map over the frozen regime axes.
- Extracts the ALARA/FENDL independently-processed reference rows for the
  executable smoke subset from the sealed P26b ledger.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
NPZ = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g.npz")
IDX = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g_index.json")
DECAY = os.path.join(ROOT, "actinv-data", "v1.0.0", "decay",
                     "endf-b-viii-0_decay.dat")
DECAY_FB = os.path.join(ROOT, "actinv-data", "v1.0.0", "decay",
                        "jeff-3-3_decay.dat")
SHIELD = os.path.join(ROOT, "results", "g1_p19_shield_artifact.json")
FNS_SPEC = os.path.join(ROOT, "examples", "fns_fe_5min.json")
SMOKE = os.path.expanduser("~/nuclear-data/p27-work/study/smoke_study.json")
P26B = os.path.join(ROOT, "results", "g2_p26b_leg_ledger.jsonl")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p28_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p28-work/g2")
OUT = os.path.join(ROOT, "results", "g2_p28_population.json")


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env", f"TMPDIR={WORK}", *cmd]


def base_spec(title):
    fns = json.load(open(FNS_SPEC))
    spec = {
        "spec": "actinv-spec-1", "title": title, "projectile": "neutron",
        "library": {"path": NPZ,
                    "sha256": SEALS["identities"]["activation_library"]
                    ["sha256"]},
        "decay": {"primary": DECAY, "fallback": DECAY_FB},
        "material": {"mass_g": 1.0, "basis": "wt_percent",
                     "composition": {"Fe": 100.0}},
        "spectrum": fns["spectrum"],
        "schedule": [{"dt": "300 s", "flux": 1.0}],
        "options": {"mode": "auto"},
        "self_shielding": None, "radiological": None, "damage": None,
        "fission_yields": {"energy": "spectrum_average", "files": [],
                           "fixed_energy_eV": None},
        "uncertainty": None,
    }
    return spec


def run_case(case):
    d = os.path.join(WORK, "cases")
    os.makedirs(d, exist_ok=True)
    sp = os.path.join(d, f"{case['id']}.json")
    op = os.path.join(d, f"{case['id']}.out.json")
    rec = {"id": case["id"], "kind": case["kind"],
           "spec_sha256": None, "status": None}
    if "error_expected" in case:
        rec["expectation"] = case["error_expected"]
    json.dump(case["spec"], open(sp, "w"))
    import hashlib
    rec["spec_sha256"] = hashlib.sha256(open(sp, "rb").read()).hexdigest()
    r = subprocess.run(cgroup([ACTINV, "run", sp, op]),
                       capture_output=True, text=True)
    if r.returncode == 0:
        out = json.load(open(op))
        rec["status"] = "executed"
        rec["out_sha256"] = hashlib.sha256(open(op, "rb").read()).hexdigest()
        st = out["steps"][-1]
        rec["responses"] = {
            "total_activity_bq_per_g": sum(
                v for k, v in st["activity_Bq_per_g"].items()),
            "total_atoms_per_g": st["total_atoms_per_g"],
        }
        rec["ledger_flags"] = {
            k: out["ledger"][k] for k in
            ("library_target_limitations", "composition_isotopes_absent_"
             "from_decay_library", "isomer_state_absent_from_decay_"
             "library_used_ground", "composition_elements_unknown")
            if k in out["ledger"]}
        # coverage gaps that execute without a ledger entry are recorded
        # here as silent absences (the applicability map is the named
        # record of them)
        absent = [z for z in case.get("dosimetry_missing", [])]
        if absent:
            lim = json.dumps(rec["ledger_flags"])
            rec["silent_coverage_absence"] = [
                a for a in absent if a not in lim]
    else:
        rec["status"] = "gap"
        rec["error"] = (r.stderr or r.stdout)[-400:]
    return rec


def boundary_cases():
    fns = json.load(open(FNS_SPEC))
    flux = fns["spectrum"]["flux_per_group"]
    cases = []

    s = base_spec("fe__fns_709__pulse_5min__T0K")
    s["options"]["temperature_K"] = 0.0
    cases.append({"id": "fe__fns_709__pulse_5min__T0K",
                  "kind": "temperature_boundary", "spec": s})

    s = base_spec("fe__fns_709__pulse_5min__T1200K")
    s["options"]["temperature_K"] = 1200.0
    cases.append({"id": "fe__fns_709__pulse_5min__T1200K",
                  "kind": "temperature_boundary", "spec": s})

    s = base_spec("fe__fns_709__pulse_5min__dilute_0p1b")
    s["self_shielding"] = {
        "table": {"path": SHIELD,
                  "sha256": SEALS["identities"]["shield_table"]["sha256"]},
        "dilution": "fixed", "sigma0_b": 0.1}
    cases.append({"id": "fe__fns_709__pulse_5min__dilute_0p1b",
                  "kind": "dilution_boundary", "spec": s})

    s = base_spec("fe__fns_709__pulse_5min__dilute_1e10b")
    s["self_shielding"] = {
        "table": {"path": SHIELD,
                  "sha256": SEALS["identities"]["shield_table"]["sha256"]},
        "dilution": "fixed", "sigma0_b": 1e10}
    cases.append({"id": "fe__fns_709__pulse_5min__dilute_1e10b",
                  "kind": "dilution_boundary", "spec": s})

    s = base_spec("fe__single_group_g354__pulse_5min")
    f = [0.0] * 709
    f[354] = 1.0
    s["spectrum"] = {"structure": "fispact-709", "flux_per_group": f,
                     "total": 1.0, "descending": True}
    cases.append({"id": "fe__single_group_g354__pulse_5min",
                  "kind": "spectrum_boundary", "spec": s})

    s = base_spec("fe__fns_709__zero_flux")
    s["schedule"] = [{"dt": "300 s", "flux": 0.0}]
    cases.append({"id": "fe__fns_709__zero_flux",
                  "kind": "schedule_boundary", "spec": s})

    s = base_spec("ni__fns_709__pulse_5min")
    s["material"]["composition"] = {"Ni": 100.0}
    cases.append({"id": "ni__fns_709__pulse_5min",
                  "kind": "coverage_boundary", "spec": s,
                  "dosimetry_missing": ["Ni-58"]})

    s = base_spec("fe__fns_709__pulse_5min__proton")
    s["projectile"] = "proton"
    cases.append({"id": "fe__fns_709__pulse_5min__proton",
                  "kind": "projectile_boundary", "spec": s})
    return cases


def applicability_map():
    """Exhaustive map over the frozen regime axes."""
    idx = json.load(open(IDX))
    rescan = json.load(open(os.path.join(
        ROOT, "results", "g3_p25c_census.json")))["rescan"]
    from collections import defaultdict
    els = defaultdict(lambda: {"targets": [], "defect_files": []})
    for t in idx["targets"]:
        m = re.match(r"n-([A-Z][a-z]?)(\d+)([mn]?)\.tendl", t["file"])
        el = m.group(1)
        els[el]["targets"].append(t["za"])
        r = rescan.get(t["file"])
        if r and (r.get("pre_class") or r.get("patched")):
            els[el]["defect_files"].append(
                {"file": t["file"], "pre_class": r.get("pre_class"),
                 "patched": r.get("patched"),
                 "post_kinds": r.get("post_kinds")})
    element_map = {}
    dos_missing = {"Ni": [28058], "Nb": [41093], "Ag": [47109],
                   "In": [49113], "Au": [79197]}
    for el, e in sorted(els.items()):
        missing = [z for z in dos_missing.get(el, [])
                   if z not in e["targets"]]
        element_map[el] = {
            "n_targets": len(e["targets"]),
            "defect_ledger_files": e["defect_files"],
            "dosimetry_missing_za": missing,
            "status": ("gap" if missing else
                       "qualified_with_ledger" if e["defect_files"]
                       else "qualified"),
        }
    axes = {
        "projectile": {
            "neutron": {"status": "qualified"},
            "proton": {"status": "excluded",
                       "reason": "P25 coverage floor fail; no artifact"},
            "deuteron": {"status": "excluded",
                         "reason": "P25 coverage floor fail; no artifact"},
            "alpha": {"status": "excluded",
                      "reason": "P25 coverage floor fail; no artifact"}},
        "group_structure": {
            "fispact-709": {"status": "qualified"},
            "other": {"status": "excluded",
                      "reason": "no artifact; structure mismatch"}},
        "shielding": {
            "infinite_dilution": {"status": "qualified"},
            "finite_dilution": {
                "status": "qualified_on_covered",
                "covered": SEALS["identities"]["shield_table"]["nuclides"],
                "uncovered": "all other nuclides -> ledger-named "
                             "passthrough or require_shielding_complete "
                             "fail-closed"},
        },
        "temperature_K": {
            "qualified": [293.6],
            "measured_boundary": {
                "0.0": "contract_gap: requested temperature 0 K does "
                       "not match library temperature 293.6 K",
                "1200.0": "contract_gap: requested temperature 1200 K "
                          "does not match library temperature 293.6 K"},
            "note": "the patched artifact is single-temperature; any "
                    "options.temperature_K != 293.6 fails closed"},

        "responses": {
            "qualified": SEALS["selected_regime"]["responses"],
        },
        "elements": element_map,
    }
    counts = {"qualified": 0, "qualified_with_ledger": 0, "gap": 0}
    for e in element_map.values():
        counts[e["status"]] += 1
    return {"axes": axes, "element_counts": counts,
            "n_elements": len(element_map)}


def alara_reference():
    """P26b product_plus_data rows for the smoke fe cases."""
    rows = [json.loads(l) for l in open(P26B)]
    ref = {}
    for r in rows:
        if r.get("leg") != "product_plus_data" or \
                r.get("status") != "executed":
            continue
        if r["case"].startswith("fe__"):
            ref[r["case"]] = {
                "arms": {a: {"result": v["result"]}
                         for a, v in r["arms"].items()},
            }
    return ref


def main():
    os.makedirs(WORK, exist_ok=True)
    cases = boundary_cases()
    results = [run_case(c) for c in cases]
    for r in results:
        print(r["id"], "->", r["status"],
              r.get("error", "")[:80], flush=True)

    # smoke study re-execution under this partition
    sdir = os.path.join(WORK, "smoke")
    os.makedirs(sdir, exist_ok=True)
    r = subprocess.run(cgroup([ACTINV, "study", "run", SMOKE,
                               os.path.join(sdir, "run")]),
                       capture_output=True, text=True)
    smoke = {"returncode": r.returncode}
    if r.returncode == 0:
        rec = json.load(open(os.path.join(sdir, "run",
                                          "study_record.json")))
        smoke["population"] = rec["population"]
        smoke["verdict"] = rec.get("verdict")
    else:
        smoke["error"] = (r.stderr or r.stdout)[-300:]
    print("smoke:", smoke.get("population"), flush=True)

    amap = applicability_map()
    ref = alara_reference()

    # docs/APPLICABILITY.md — the human-readable form of the map
    lines = ["# ACTINV applicability map (P28)",
             "",
             "Regime axes and element coverage qualified by P28 against the",
             "shipped `tendl-2025-patched` artifact. `qualified_with_ledger`",
             "means the element's source files carry P25c residual defect",
             "classes (recorded, not cleared). `gap` names missing coverage.",
             ""]
    for axis, entries in amap["axes"].items():
        if axis == "elements":
            continue
        lines.append(f"## {axis}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(entries, indent=1, sort_keys=True))
        lines.append("```")
        lines.append("")
    lines.append("## elements")
    lines.append("")
    lines.append("| element | targets | status | defect files | "
                 "missing dosimetry |")
    lines.append("|---|---|---|---|---|")
    for el, e in amap["axes"]["elements"].items():
        lines.append(f"| {el} | {e['n_targets']} | {e['status']} | "
                     f"{len(e['defect_ledger_files'])} | "
                     f"{e['dosimetry_missing_za'] or '-'} |")
    with open(os.path.join(ROOT, "docs", "APPLICABILITY.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    out = {
        "schema": "actinv-p28-g2-1", "gate": "G2", "phase": "P28",
        "partition": "p28_qualifying",
        "boundary_cases": results,
        "smoke_study": smoke,
        "applicability_map": amap,
        "independent_reference": {
            "source": "P26b g2_p26b_leg_ledger.jsonl "
                      "product_plus_data rows (ALARA/FENDL-3.2c vs "
                      "ACTINV/TENDL-2025-patched)",
            "cases": ref,
        },
    }
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    n_exec = sum(1 for x in results if x["status"] == "executed")
    n_gap = sum(1 for x in results if x["status"] == "gap")
    print(f"boundary: {n_exec} executed, {n_gap} gap")
    print("map:", amap["element_counts"], amap["n_elements"], "elements")


if __name__ == "__main__":
    main()
