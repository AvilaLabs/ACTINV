#!/usr/bin/env python3
"""P76 impurity-budget prototype (protocols/ACTINV-P76_PROTOCOL.md).

At fixed flux every activity — hence the IAEA clearance index CI = Σ A_n / L_n — is linear in the
initial composition (P75b: coupled-mode superposition holds to 1e-11). One coupled run per element
at the component's flux therefore gives CI for any composition as a weighted sum, and impurity
limits follow from linear algebra. The iron balance is exact: w_Fe = 100 − Σ others (wt%).

    python3 controls/p76_impurity_budget.py build     # element specs + manifest
    python3 controls/p76_impurity_budget.py run       # element runs (checkpointed)
    python3 controls/p76_impurity_budget.py budget    # compose budgets, write verification specs
    python3 controls/p76_impurity_budget.py verify    # full solves at the computed budget edges
    python3 controls/p76_impurity_budget.py check     # derive the verdict -> results/p76_verdict.json
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("p75", ROOT / "controls" / "p75_linear_response.py")
p75 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p75)

WORK = ROOT / "target" / "p76"
SPECS = WORK / "specs"
RAW = WORK / "raw"
CHECKPOINT = WORK / "runs.jsonl"
MANIFEST = WORK / "cases.json"
BUDGET = WORK / "budget.json"
BIN = ROOT / "target" / "p75" / "actinv"
LIMITS = ROOT / "data" / "clearance_iaea_2004.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P76_PROTOCOL.md"

YEAR = p75.YEAR_S
IRRADIATION_S = 5 * YEAR
COOLING_S = [1.0e6, YEAR, 10 * YEAR, 30 * YEAR, 50 * YEAR, 100 * YEAR]
TARGET_COOLING_S = [50 * YEAR, 100 * YEAR]  # clearance targets
VERIFY_TOL = 1e-6

# Matrix (fixed) and declared impurity reference specifications, wt%. Representative, not certified.
MATERIALS = {
    "eurofer97": {
        "matrix": {"Cr": 9.0, "W": 1.1, "Mn": 0.4, "V": 0.2, "Ta": 0.12, "C": 0.11, "N": 0.03, "Si": 0.05},
        "impurity_spec": {"Nb": 0.001, "Mo": 0.005, "Ni": 0.005, "Cu": 0.005, "Co": 0.005, "Al": 0.01,
                          "Ti": 0.01, "Ag": 0.0001},
    },
    "ss316ln": {
        "matrix": {"Cr": 17.5, "Ni": 12.25, "Mo": 2.5, "Mn": 1.8, "Si": 0.5, "N": 0.07, "C": 0.03,
                   "P": 0.025, "S": 0.01},
        "impurity_spec": {"Co": 0.05, "Nb": 0.01, "Ta": 0.01, "Cu": 0.3, "B": 0.001, "Ag": 0.0001},
    },
}
SCENARIOS = {
    # moderated field, vacuum-vessel-like order of magnitude
    "vessel": {"spectrum": "mix", "total": 1.0e12},
    # hard D-T field behind a thin first wall
    "firstwall": {"spectrum": "fns", "total": 1.0e14},
}


def schedule() -> tuple[list[dict], list[int]]:
    steps = [{"dt": f"{IRRADIATION_S!r} s", "flux": 1.0}]
    prev = 0.0
    for t in COOLING_S:
        steps.append({"dt": f"{t - prev!r} s", "flux": 0.0})
        prev = t
    return steps, list(range(len(steps)))


def elements() -> list[str]:
    s = {"Fe"}
    for m in MATERIALS.values():
        s |= set(m["matrix"]) | set(m["impurity_spec"])
    return sorted(s)


def doc_for(rid: str, composition: dict, scen: dict) -> dict:
    steps, _ = schedule()
    return p75.spec(rid, composition, p75.spectra()[scen["spectrum"]], scen["total"], steps, "coupled", "reach")


def write_specs(cases: list[dict]) -> list[dict]:
    SPECS.mkdir(parents=True, exist_ok=True)
    out = []
    for c in cases:
        data = json.dumps(c["doc"], sort_keys=True, separators=(",", ":")).encode()
        (SPECS / f"{c['id']}.json").write_bytes(data)
        row = {k: v for k, v in c.items() if k != "doc"}
        row["spec_sha256"] = hashlib.sha256(data).hexdigest()
        out.append(row)
    return out


def cmd_build() -> None:
    cases = []
    for sname, scen in SCENARIOS.items():
        for e in elements():
            rid = f"E__{sname}__{e}"
            cases.append({"id": rid, "arm": "E", "scenario": sname, "element": e,
                          "doc": doc_for(rid, {e: 100.0}, scen)})
    manifest = write_specs(cases)
    MANIFEST.write_text(json.dumps({"cases": manifest}, indent=1, sort_keys=True))
    print(f"{len(manifest)} element runs; manifest sha256 {p75.sha256_file(MANIFEST)}")


def solve(rid: str) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"{rid}.json"
    t0 = time.monotonic()
    proc = subprocess.run([str(BIN), "run", str(SPECS / f"{rid}.json"), str(out)],
                          capture_output=True, text=True, timeout=3600)
    row = {"id": rid, "returncode": proc.returncode, "wall_s": time.monotonic() - t0}
    if proc.returncode != 0:
        row["error"] = (proc.stderr or proc.stdout)[-2000:]
    else:
        res = json.loads(out.read_text())
        row["result_sha256"] = p75.sha256_file(out)
        row["mode"] = res["mode"]
        row["steps"] = [{"t_s": s["t_s"], "activity": {k: v for k, v in s["activity_Bq_per_g"].items() if v > 0}}
                        for s in res["steps"]]
    if out.exists():
        out.unlink()
    return row


def checkpoint_rows(path: Path) -> dict:
    rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["id"]] = r
    return rows


def run_ids(ids: list[str], path: Path) -> None:
    done = checkpoint_rows(path)
    todo = [i for i in ids if i not in done]
    print(f"binary sha256 {p75.sha256_file(BIN)}; {len(done)} done, {len(todo)} to run", flush=True)
    with open(path, "a") as f:
        for i in todo:
            row = solve(i)
            f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
            print(f"{i} rc={row['returncode']} {row['wall_s']:.2f}s", flush=True)


def cmd_run() -> None:
    run_ids([c["id"] for c in json.loads(MANIFEST.read_text())["cases"]], CHECKPOINT)


# ---------------------------------------------------------------- clearance arithmetic
def limits() -> dict:
    doc = json.loads(LIMITS.read_text())
    return doc["limits"]


_NUC = re.compile(r"^([A-Z][a-z]?)(\d+)(m(\d+))?$")


def rsg_key(nuclide: str) -> str | None:
    """ACTINV 'Co60' / 'Co60m1' -> RS-G-1.7 'Co-60' / 'Co-60m'. Higher isomers have no key."""
    m = _NUC.match(nuclide)
    if not m:
        return None
    sym, a, _, iso = m.groups()
    if iso is None:
        return f"{sym}-{a}"
    return f"{sym}-{a}m" if iso == "1" else None


def ci_of(activity: dict, lim: dict, with_uncovered: bool = False):
    """(clearance index, uncovered activity Bq/g, per-nuclide CI contributions[, uncovered per nuclide])."""
    ci, unc, parts, unc_parts = 0.0, 0.0, {}, {}
    for n, a in activity.items():
        k = rsg_key(n)
        if k is not None and k in lim:
            c = a / lim[k]
            ci += c
            parts[n] = c
        else:
            unc += a
            unc_parts[n] = a
    return (ci, unc, parts, unc_parts) if with_uncovered else (ci, unc, parts)


def element_ci(rows: dict, scen: str, lim: dict) -> dict:
    """Per element, per step: CI of the pure element (per 100 wt%) and uncovered activity."""
    out = {}
    for e in elements():
        r = rows[f"E__{scen}__{e}"]
        out[e] = [dict(zip(("ci", "uncovered", "parts", "uncovered_parts"), ci_of(s["activity"], lim, True))) | {"t_s": s["t_s"],
                  "activity_total": sum(s["activity"].values())} for s in r["steps"]]
    return out


def composed(eci: dict, comp_wt: dict, step: int) -> dict:
    ci = sum(w / 100.0 * eci[e][step]["ci"] for e, w in comp_wt.items())
    unc = sum(w / 100.0 * eci[e][step]["uncovered"] for e, w in comp_wt.items())
    act = sum(w / 100.0 * eci[e][step]["activity_total"] for e, w in comp_wt.items())
    return {"ci": ci, "uncovered_Bq_g": unc, "activity_Bq_g": act}


def full_comp(matrix: dict, imp: dict) -> dict:
    comp = dict(matrix)
    for e, w in imp.items():
        comp[e] = comp.get(e, 0.0) + w
    comp["Fe"] = 100.0 - sum(v for k, v in comp.items() if k != "Fe")
    return comp


def target_steps() -> list[int]:
    steps, _ = schedule()
    cum, out = 0.0, []
    for i, st in enumerate(steps):
        if i == 0:
            continue
        cum = COOLING_S[i - 1]
        if any(abs(cum - t) < 1e-6 * t for t in TARGET_COOLING_S):
            out.append(i)
    return out


def cmd_budget() -> None:
    rows = checkpoint_rows(CHECKPOINT)
    lim = limits()
    report, verify = {}, []
    for sname, scen in SCENARIOS.items():
        eci = element_ci(rows, sname, lim)
        for mname, mat in MATERIALS.items():
            spec_imp = mat["impurity_spec"]
            key = f"{sname}/{mname}"
            per_t = []
            for k in target_steps():
                matrix_only = composed(eci, full_comp(mat["matrix"], {}), k)
                at_spec = composed(eci, full_comp(mat["matrix"], spec_imp), k)
                # d CI / d w_e (per wt%) with Fe as balance: (r_e - r_Fe)/100
                grad = {e: (eci[e][k]["ci"] - eci["Fe"][k]["ci"]) / 100.0 for e in spec_imp}
                contrib = {e: spec_imp[e] * grad[e] for e in spec_imp}
                headroom_spec = 1.0 - at_spec["ci"]
                single, single_status = {}, {}
                for e in spec_imp:
                    # others held at spec: w_e,max = spec_e + headroom / grad_e
                    if grad[e] <= 0:
                        single[e], single_status[e] = None, "no clearance-index response"
                        continue
                    w = spec_imp[e] + headroom_spec / grad[e]
                    if w > 0:
                        single[e], single_status[e] = w, "limit"
                    else:
                        single[e], single_status[e] = None, "infeasible: the other constituents alone exceed CI = 1"
                imp_ci = sum(contrib.values())
                k_margin = (1.0 - matrix_only["ci"]) / imp_ci if imp_ci > 0 else None
                # top nuclides at spec composition
                parts = {}
                for e, w in full_comp(mat["matrix"], spec_imp).items():
                    for n, c in eci[e][k]["parts"].items():
                        parts[n] = parts.get(n, 0.0) + w / 100.0 * c
                top = sorted(parts.items(), key=lambda x: -x[1])[:6]
                uparts = {}
                for e, w in full_comp(mat["matrix"], spec_imp).items():
                    for n, a in eci[e][k]["uncovered_parts"].items():
                        uparts[n] = uparts.get(n, 0.0) + w / 100.0 * a
                top_unc = sorted(uparts.items(), key=lambda x: -x[1])[:6]
                per_t.append({
                    "step": k, "cooling_y": COOLING_S[k - 1] / YEAR,
                    "ci_matrix_only": matrix_only["ci"], "ci_at_spec": at_spec["ci"],
                    "uncovered_share_at_spec": at_spec["uncovered_Bq_g"] / at_spec["activity_Bq_g"]
                    if at_spec["activity_Bq_g"] > 0 else 0.0,
                    "impurity_ci_contribution_at_spec": contrib,
                    "single_impurity_limit_wt_pct_others_at_spec": single,
                    "single_impurity_limit_status": single_status,
                    "spec_margin_factor_k": k_margin,
                    "feasible_with_zero_impurities": matrix_only["ci"] < 1.0,
                    "top_nuclides_ci_at_spec": top,
                    "top_uncovered_nuclides_Bq_g_at_spec": top_unc,
                })
            report[key] = {"scenario": scen, "matrix": mat["matrix"], "impurity_spec": spec_imp,
                           "targets": per_t}
            # verification points at the earliest target cooling (binding for decaying inventories)
            t0 = per_t[0]
            if t0["spec_margin_factor_k"] is not None and t0["spec_margin_factor_k"] > 0:
                kf = t0["spec_margin_factor_k"]
                verify.append({"id": f"V__{sname}__{mname}__spec_x_k", "scenario": sname, "step": t0["step"],
                               "composition": full_comp(mat["matrix"], {e: kf * w for e, w in spec_imp.items()}),
                               "predicted_ci": 1.0})
            dom = max(t0["impurity_ci_contribution_at_spec"], key=t0["impurity_ci_contribution_at_spec"].get)
            lim_e = t0["single_impurity_limit_wt_pct_others_at_spec"][dom]
            if lim_e is not None and lim_e > 0:
                imp = dict(spec_imp)
                imp[dom] = lim_e
                verify.append({"id": f"V__{sname}__{mname}__limit_{dom}", "scenario": sname, "step": t0["step"],
                               "composition": full_comp(mat["matrix"], imp), "predicted_ci": 1.0})
            # always verify the composed CI at spec (the as-specified component)
            verify.append({"id": f"V__{sname}__{mname}__at_spec", "scenario": sname, "step": t0["step"],
                           "composition": full_comp(mat["matrix"], spec_imp), "predicted_ci": t0["ci_at_spec"]})
    cases = [{"id": v["id"], "arm": "V", "scenario": v["scenario"],
              "doc": doc_for(v["id"], v["composition"], SCENARIOS[v["scenario"]])} for v in verify]
    vman = write_specs(cases)
    BUDGET.write_text(json.dumps({"report": report, "verify": verify, "verify_manifest": vman},
                                 indent=1, sort_keys=True))
    print(f"budget written; {len(verify)} verification solves")


def cmd_verify() -> None:
    b = json.loads(BUDGET.read_text())
    run_ids([v["id"] for v in b["verify"]], WORK / "verify.jsonl")


def cmd_check() -> int:
    b = json.loads(BUDGET.read_text())
    vrows = checkpoint_rows(WORK / "verify.jsonl")
    erows = checkpoint_rows(CHECKPOINT)
    lim = limits()
    frozen = (ROOT / "protocols" / "protocol_hash.txt").read_text()
    checks = []
    for v in b["verify"]:
        r = vrows.get(v["id"])
        if r is None or r["returncode"] != 0:
            checks.append({"id": v["id"], "ok": False, "reason": "missing or failed solve"})
            continue
        ci, unc, _ = ci_of(r["steps"][v["step"]]["activity"], lim)
        d = abs(ci - v["predicted_ci"]) / v["predicted_ci"]
        checks.append({"id": v["id"], "predicted_ci": v["predicted_ci"], "solved_ci": ci, "rel_dev": d,
                       "ok": d <= VERIFY_TOL, "mode": r["mode"]})
    missing_e = [c["id"] for c in json.loads(MANIFEST.read_text())["cases"]
                 if erows.get(c["id"], {}).get("returncode") != 0]
    verdict = {
        "protocol": "ACTINV-P76",
        "inputs": {"protocol_sha256": p75.sha256_file(PROTOCOL), "manifest_sha256": p75.sha256_file(MANIFEST),
                   "element_checkpoint_sha256": p75.sha256_file(CHECKPOINT),
                   "verify_checkpoint_sha256": p75.sha256_file(WORK / "verify.jsonl"),
                   "budget_sha256": p75.sha256_file(BUDGET), "binary_sha256": p75.sha256_file(BIN),
                   "limits_sha256": p75.sha256_file(LIMITS)},
        "protocol_hash_registered": p75.sha256_file(PROTOCOL) in frozen,
        "G0_pass": not missing_e,
        "G1_pass": bool(checks) and all(c["ok"] for c in checks),
        "max_rel_dev": max((c.get("rel_dev", float("inf")) for c in checks), default=None),
        "checks": checks,
        "report": b["report"],
    }
    (ROOT / "results" / "p76_verdict.json").write_text(json.dumps(verdict, indent=1, sort_keys=True))
    print(json.dumps({k: verdict[k] for k in ("protocol_hash_registered", "G0_pass", "G1_pass", "max_rel_dev")},
                     indent=1))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"build": cmd_build, "run": cmd_run, "budget": cmd_budget, "verify": cmd_verify, "check": cmd_check}
    if cmd not in fn:
        sys.exit(__doc__)
    fn[cmd]()
