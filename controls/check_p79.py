#!/usr/bin/env python3
"""P79 checker (protocols/ACTINV-P79_PROTOCOL.md): `actinv budget` as a verified command.

    python3 controls/check_p79.py run     # budget runs + independent full solves (target/p79/)
    python3 controls/check_p79.py check   # verdict -> results/p79_verdict.json

G2 does not trust the command's own verification: every verification composition is re-solved here
with `actinv run` and its clearance index recomputed with the P76 prototype arithmetic (`ci_of`).
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
BIN = ROOT / "target" / "release" / "actinv"
WORK = ROOT / "target" / "p79"
INPUTS = ROOT / "controls" / "p79"
MATERIALS = ["eurofer97", "ss316ln"]
PROTOCOL = ROOT / "protocols" / "ACTINV-P79_PROTOCOL.md"
BUILD_LOG = WORK / "build.log"
TOL_G2, TOL_G3 = 1e-6, 1e-9

_s = importlib.util.spec_from_file_location("p76", ROOT / "controls" / "p76_impurity_budget.py")
p76 = importlib.util.module_from_spec(_s)
_s.loader.exec_module(p76)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def out_of(mat: str) -> Path:
    return ROOT / "results" / f"p79_budget_{mat}.json"


def cmd_run() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    log = {"binary_sha256": sha(BIN), "budget": {}, "independent": {}}
    base = json.loads((INPUTS / "base_exvessel.json").read_text())
    for mat in MATERIALS:
        t0 = time.monotonic()
        p = subprocess.run([str(BIN), "budget", str(INPUTS / f"{mat}_exvessel.json"), str(out_of(mat))],
                           cwd=ROOT, capture_output=True, text=True)
        log["budget"][mat] = {"returncode": p.returncode, "wall_s": time.monotonic() - t0,
                              "stderr_tail": p.stderr[-1500:]}
        print(mat, "rc", p.returncode, f"{time.monotonic() - t0:.1f}s", p.stderr.strip()[-300:], flush=True)
        if p.returncode != 0 or not out_of(mat).exists():
            continue
        res = json.loads(out_of(mat).read_text())
        for pt in res["verification"]["points"]:
            doc = json.loads(json.dumps(base))
            doc["title"] = f"P79 independent {mat} {pt['id']}"
            doc["material"] = {"mass_g": base["material"].get("mass_g", 1.0), "basis": "wt_percent",
                               "composition": pt["composition_wt_pct"]}
            doc["options"].update({"mode": "coupled", "prune": "reach", "outputs": ["ledger"]})
            rid = f"{mat}__{re.sub(r'[^A-Za-z0-9_]', '_', pt['id'])}"
            spec_p, out_p = WORK / f"{rid}.spec.json", WORK / f"{rid}.out.json"
            spec_p.write_text(json.dumps(doc))
            t1 = time.monotonic()
            q = subprocess.run([str(BIN), "run", str(spec_p), str(out_p)], cwd=ROOT, capture_output=True, text=True)
            row = {"returncode": q.returncode, "wall_s": time.monotonic() - t1, "mode": None, "ci": {}}
            if q.returncode == 0:
                r = json.loads(out_p.read_text())
                row["mode"] = r["mode"]
                lim = p76.limits()
                for st in r["steps"]:
                    act = {k: v for k, v in st["activity_Bq_per_g"].items() if v > 0}
                    row["ci"][str(st["step"])] = p76.ci_of(act, lim)[0]
                out_p.unlink()
            else:
                row["error"] = q.stderr[-1500:]
            log["independent"][f"{mat}/{pt['id']}"] = row
    (WORK / "run_log.json").write_text(json.dumps(log, indent=1, sort_keys=True))


def rel(a, b):
    return abs(a - b) / max(abs(b), 1e-300)


def cmd_check() -> int:
    log = json.loads((WORK / "run_log.json").read_text())
    registered = f"{sha(PROTOCOL)}  protocols/ACTINV-P79_PROTOCOL.md" in (ROOT / "protocols/protocol_hash.txt").read_text()
    g0 = registered and all(log["budget"].get(m, {}).get("returncode") == 0 for m in MATERIALS)

    blog = BUILD_LOG.read_text() if BUILD_LOG.exists() else ""
    rc = dict(re.findall(r"^(fmt|clippy|test) rc=(\d+)$", blog, re.M))
    tests = re.findall(r"^test budget::tests::(\w+) \.\.\. ok$", blog, re.M)
    need = {"budget_algebra_on_a_synthetic_table", "budget_statuses_for_no_response_and_infeasible",
            "budget_refuses_nonlinear_inputs"}
    g1 = all(rc.get(k) == "0" for k in ("fmt", "clippy", "test")) and need <= set(tests)

    g2 = {}
    for mat in MATERIALS:
        if not out_of(mat).exists():
            g2[mat] = {"status": "FAIL", "reason": "no budget output"}
            continue
        res = json.loads(out_of(mat).read_text())
        rows, edges, worst, ok = [], 0, 0.0, True
        for pt in res["verification"]["points"]:
            ind = log["independent"].get(f"{mat}/{pt['id']}")
            if ind is None or ind["returncode"] != 0:
                ok = False
                rows.append({"id": pt["id"], "ok": False, "reason": "independent solve missing or failed"})
                continue
            devs = {k: rel(ind["ci"][k], v) for k, v in pt["predicted_ci"].items()}
            d = max(devs.values())
            worst = max(worst, d)
            if any(abs(v - 1.0) < 1e-12 for v in pt["predicted_ci"].values()):
                edges += 1
            ok &= d <= TOL_G2
            rows.append({"id": pt["id"], "max_rel_dev": d, "ok": d <= TOL_G2, "independent_mode": ind["mode"]})
        status = "NOT EXERCISED" if edges == 0 else ("PASS" if ok else "FAIL")
        if not ok:
            status = "FAIL"
        g2[mat] = {"status": status, "edge_points": edges, "points": len(rows), "max_rel_dev": worst, "rows": rows}

    proto = json.loads((ROOT / "results" / "p76a_verdict.json").read_text())["report"]
    g3 = {}
    for mat in MATERIALS:
        if not out_of(mat).exists():
            g3[mat] = {"pass": False, "reason": "no budget output"}
            continue
        res = json.loads(out_of(mat).read_text())
        by_step = {t["step"]: t for t in res["targets"]}
        cmp, fails = [], []
        for pt in proto[f"exvessel/{mat}"]["targets"]:
            t = by_step.get(pt["step"] + 1)  # prototype indexes result steps from 0
            if t is None:
                fails.append(f"step {pt['step'] + 1} missing")
                continue
            pairs = [("ci_matrix_only", t["ci_matrix_only"], pt["ci_matrix_only"]),
                     ("ci_at_spec", t["ci_at_spec"], pt["ci_at_spec"]),
                     ("spec_margin_factor_k", t["spec_margin_factor_k"], pt["spec_margin_factor_k"])]
            for e, c in pt["impurity_ci_contribution_at_spec"].items():
                pairs.append((f"contribution {e}", t["impurities"][e]["ci_contribution_at_spec"], c))
            for e, lim in pt["single_impurity_limit_wt_pct_others_at_spec"].items():
                mine = t["impurities"][e]["single_limit_wt_pct_others_at_spec"]
                if lim is not None and lim > 0:
                    pairs.append((f"single limit {e}", mine, lim))
                elif mine is not None:
                    fails.append(f"step {t['step']} {e}: prototype has no positive limit ({lim}), command reports {mine}")
            for name, a, b in pairs:
                if a is None or b is None:
                    if a != b:
                        fails.append(f"step {t['step']} {name}: {a} vs {b}")
                    continue
                d = rel(a, b)
                cmp.append({"step": t["step"], "quantity": name, "command": a, "prototype": b, "rel": d})
                if d > TOL_G3:
                    fails.append(f"step {t['step']} {name}: rel {d:.3e}")
        g3[mat] = {"pass": not fails, "compared": len(cmp),
                   "max_rel": max((c["rel"] for c in cmp), default=None), "failures": fails}

    g4 = {}
    for mat in MATERIALS:
        if out_of(mat).exists():
            res = json.loads(out_of(mat).read_text())
            el = [s["ms"] for s in res["element_solves"]]
            vp = [p["ms"] for p in res["verification"]["points"]]
            g4[mat] = {"wall_s": log["budget"][mat]["wall_s"], "element_solves": len(el),
                       "verification_solves": len(vp), "element_solve_ms": el, "verification_solve_ms": vp}

    verdict = {
        "protocol": "ACTINV-P79",
        "inputs": {"protocol_sha256": sha(PROTOCOL), "binary_sha256": log["binary_sha256"],
                   "p76a_verdict_sha256": sha(ROOT / "results" / "p76a_verdict.json"),
                   **{f"budget_{m}_sha256": sha(out_of(m)) for m in MATERIALS if out_of(m).exists()},
                   **{f"input_{p.name}_sha256": sha(p) for p in sorted(INPUTS.glob("*.json"))}},
        "G0": {"pass": g0, "protocol_registered": registered},
        "G1": {"pass": g1, "exit_codes": rc, "budget_tests_ok": sorted(tests)},
        "G2": g2,
        "G3": g3,
        "G4_descriptive": g4,
    }
    (ROOT / "results" / "p79_verdict.json").write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"G0": g0, "G1": g1, "G2": {m: v["status"] for m, v in g2.items()},
                      "G3": {m: v["pass"] for m, v in g3.items()}}, indent=1))
    return 0


if __name__ == "__main__":
    fn = {"run": cmd_run, "check": cmd_check}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    fn[sys.argv[1]]()
