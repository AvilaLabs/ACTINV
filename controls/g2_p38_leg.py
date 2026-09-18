#!/usr/bin/env python3
"""P38 G2: re-score the P26b identical-data leg on the relaxed FENDL
artifact (actinv_fendl32c_709_p38, 31/36 parents).

Executes every contract case whose missing set is covered by the seven
newly admitted isotopes (Cr-50/52/53/54, Fe-57, Mn-55, W-180) and applies
the frozen identical_data tolerances. Ledger is append-only JSONL and
resumable; all runs sequential inside the caller's cgroup.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import g2_p26b_leg as leg  # noqa: E402

ADMITTED = {"Cr-50", "Cr-52", "Cr-53", "Cr-54", "Fe-57", "Mn-55", "W-180"}
P38_NPZ = Path.home() / "nuclear-data" / "p26b-work" / "p38-run" / \
    "actinv_fendl32c_709_p38.npz"
LEDGER = ROOT / "results" / "g2_p38_leg_ledger.jsonl"
OUT = ROOT / "results" / "g2_p38_leg.json"
WORK = Path.home() / "nuclear-data" / "p26b-work" / "p38-run" / "g2-cases"

TOL = leg.CONTRACT_PATH.read_text()  # loaded once below


def executable_now(case: dict) -> bool:
    missing = (case["legs"]["identical_data"].get("missing") or {}) \
        .get("actinv_fendl32c_709", [])
    return all(m in ADMITTED for m in missing)


def run_case(case: dict, spectra: dict, elelib: dict, hl: dict,
             lib_sha: str) -> dict:
    """Same two-arm execution as the P26b runner, ACTINV arm on the p38
    artifact."""
    row = {"case": case["case"], "leg": "identical_data",
           "status": "executable"}
    spec_name = case["spectrum"]
    flux_desc = (spectra[spec_name] if spec_name == "fns_709"
                 else list(reversed(spectra[spec_name])))
    case_dir = WORK / case["case"]
    case_dir.mkdir(parents=True, exist_ok=True)

    io = leg.write_alara_case(case_dir, case, flux_desc, elelib)
    arun = leg.timed_run([str(leg.ALARA), "case.in"], case_dir)
    (case_dir / "case.stdout").write_text(arun.get("stdout", ""))
    a = {"tool": "alara_2_9_2", "arm": "alara_fendl32c_709",
         **io, "wall_s": arun["wall_s"], "returncode": arun["returncode"],
         "stderr_tail": arun.get("stderr_tail")}
    if arun.get("returncode") == 0:
        try:
            a["result"] = leg.parse_alara_result(
                case_dir, hl, n_roots=len(case["required_isotopes"]["alara"]))
            a["result"]["artifacts"] = {
                "stdout_sha256": leg.sha256_file(case_dir / "case.stdout"),
                "dump_sha256": leg.sha256_file(case_dir / "alara.dmp"),
                "photsrc_sha256": leg.sha256_file(case_dir / "photsrc.out"),
            }
        except Exception as e:
            a["failure"] = f"alara_output_parse: {e!r}"
    else:
        a["failure"] = arun.get("failure") or "comparator_error"

    spec = leg.build_actinv_spec(
        case, flux=spectra[spec_name],
        descending=(spec_name == "fns_709"),
        lib=P38_NPZ, lib_sha=lib_sha)
    spec_path = case_dir / "spec.json"
    spec_path.write_text(json.dumps(spec))
    crun = leg.timed_run(
        [str(leg.ACTINV), "run", str(spec_path),
         str(case_dir / "out.json")], case_dir)
    c = {"tool": "actinv", "arm": "actinv_fendl32c_709_p38",
         "spec_sha256": leg.sha256_file(spec_path),
         "wall_s": crun["wall_s"], "returncode": crun["returncode"],
         "stderr_tail": crun.get("stderr_tail")}
    if crun.get("returncode") == 0 and (case_dir / "out.json").exists():
        try:
            c["result"] = leg.parse_actinv_result(
                case_dir / "out.json", case["irradiation_s"])
            c["result"]["artifacts"] = {
                "out_json_sha256": leg.sha256_file(case_dir / "out.json")}
        except Exception as e:
            c["failure"] = f"actinv_output_parse: {e!r}"
    else:
        c["failure"] = crun.get("failure") or "actinv_error"

    row["arms"] = {"alara": a, "actinv": c}
    row["wall_s_total"] = round(a["wall_s"] + c["wall_s"], 6)
    fails = {k: v.get("failure") for k, v in (("alara", a), ("actinv", c))
             if v.get("failure")}
    row["status"] = "executed_with_failures" if fails else "executed"
    if fails:
        row["arm_failures"] = fails
    return row


def _rel(a_val, c_val):
    if a_val is None or c_val is None or c_val == 0:
        return None
    return (a_val - c_val) / c_val


def tolerance_check(row: dict, tol: dict) -> dict:
    """Apply the frozen identical_data tolerances to an executed row."""
    ar = (row["arms"]["alara"].get("result") or {}).get("per_time") or {}
    cr = (row["arms"]["actinv"].get("result") or {}).get("per_time") or {}
    report = {"times_checked": 0, "violations": []}
    for t, pa in ar.items():
        pc = cr.get(t) or {}
        report["times_checked"] += 1
        for k, bound in (("total_activity_bq_per_g",
                          tol["activity_relative"]),
                         ("decay_heat_w_per_g", tol["decay_heat_relative"]),
                         ("product_atoms_per_g", tol["inventory_relative"])):
            v = _rel(pa.get(k), pc.get(k))
            if v is not None and abs(v) > bound:
                report["violations"].append(
                    {"time": t, "response": k, "rel_diff": v,
                     "bound": bound})
        pa_p, pc_p = pa.get("photon_source_per_group_24"), \
            pc.get("photon_source_per_group_24")
        if pa_p and pc_p:
            tot = sum(pa_p)
            gtol = tol["photon_group_relative"]
            for g, (x, y) in enumerate(zip(pa_p, pc_p)):
                if tot and x / tot >= 1e-6 and y and \
                        abs((x - y) / y) > gtol["tolerance"]:
                    report["violations"].append(
                        {"time": t, "response": f"photon_group_{g}",
                         "rel_diff": (x - y) / y,
                         "bound": gtol["tolerance"]})
        a_top = {n["nuclide"] for n in
                 pa.get("top5_nuclides_by_activity") or []}
        c_top = {n["nuclide"] for n in
                 pc.get("top5_nuclides_by_activity") or []}
        if a_top and c_top and a_top != c_top:
            report["violations"].append(
                {"time": t, "response": "top5_set",
                 "alara": sorted(a_top), "actinv": sorted(c_top)})
    report["pass"] = not report["violations"]
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--only", type=str, default=None,
                    help="JSON file listing case names to run")
    args = ap.parse_args()

    contract = json.loads(leg.CONTRACT_PATH.read_text())
    lib_sha = leg.sha256_file(P38_NPZ)
    tol = contract["tolerances"]["identical_data"]
    all_executable = [c for c in contract["cases"] if executable_now(c)]
    cases = all_executable
    if args.only:
        wanted = set(json.loads(Path(args.only).read_text()))
        cases = [c for c in cases if c["case"] in wanted]

    if not args.summarize:
        elelib = leg.elelib_parse()
        hl = leg.idx_parse(Path(str(leg.ALARA_LIB_BASE) + ".idx"))
        spectra = {"fns_709": leg.fns_spectrum(),
                   "irdff_sp_mat9861_709": leg.collapse_to_709()}
        scale = sum(spectra["fns_709"]) / \
            sum(spectra["irdff_sp_mat9861_709"])
        spectra["irdff_sp_mat9861_709"] = [
            v * scale for v in spectra["irdff_sp_mat9861_709"]]
        WORK.mkdir(parents=True, exist_ok=True)

        done = set()
        if args.resume and LEDGER.exists():
            for line in LEDGER.read_text().splitlines():
                r = json.loads(line)
                done.add(r["case"])
        n = 0
        for case in cases:
            if case["case"] in done:
                continue
            row = run_case(case, spectra, elelib, hl, lib_sha)
            if row["status"] == "executed":
                row["tolerance_report"] = tolerance_check(row, tol)
                row["status"] = ("tolerance_pass" if
                                 row["tolerance_report"]["pass"]
                                 else "tolerance_fail")
            with LEDGER.open("a") as f:
                f.write(json.dumps({"case": row["case"],
                                    "leg": "identical_data",
                                    "record": row}) + "\n")
            n += 1
            if args.limit and n >= args.limit:
                break
        print(f"executed {n} new case(s); ledger {LEDGER}")

    # summarize
    rows = [json.loads(l) for l in LEDGER.read_text().splitlines()
            if l.strip()]
    census = {}
    tol_results = {"tolerance_pass": 0, "tolerance_fail": 0}
    failures = {}
    cmp_stats = {}
    for r in rows:
        rec = r["record"]
        census[rec["status"]] = census.get(rec["status"], 0) + 1
        if rec["status"] in tol_results:
            tol_results[rec["status"]] += 1
        for arm, f in (rec.get("arm_failures") or {}).items():
            failures.setdefault(f.split(":")[0], []).append(rec["case"])
        if rec["status"].startswith("tolerance"):
            ar = (rec["arms"]["alara"].get("result") or {}).get("per_time") \
                or {}
            cr = (rec["arms"]["actinv"].get("result") or {}).get("per_time") \
                or {}
            for t, pa in ar.items():
                pc = cr.get(t) or {}
                for k in ("total_activity_bq_per_g", "decay_heat_w_per_g",
                          "product_atoms_per_g"):
                    v = _rel(pa.get(k), pc.get(k))
                    if v is not None:
                        s = cmp_stats.setdefault(k, {}).setdefault(t, [])
                        s.append(v)
    cmp_out = {k: {t: {"n": len(vs), "mean_rel": sum(vs) / len(vs),
                       "max_abs_rel": max(abs(x) for x in vs),
                       "median_rel": sorted(vs)[len(vs) // 2]}
                   for t, vs in times.items()}
               for k, times in cmp_stats.items()}
    out = {
        "schema": "actinv-p38-g2-leg-1",
        "artifact": {"path": str(P38_NPZ), "sha256": lib_sha},
        "expected_executable": len(all_executable),
        "ledger_rows": len(rows),
        "census": census,
        "tolerance": tol_results,
        "arm_failure_rollups": failures,
        "response_comparison": {
            "note": "rel diff (alara-actinv)/actinv per response per time",
            "responses": cmp_out,
        },
        "blocked_isotopes": {"Ni-62": 100, "W-182": 4, "W-183": 4,
                             "W-184": 4, "W-186": 4},
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"census": census, "tolerance": tol_results},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
