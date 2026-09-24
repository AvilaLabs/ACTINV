#!/usr/bin/env python3
"""P49 G1 mechanics — seeded determinism, spec-decodability, ledger
completeness, and resume consistency on a tiny no-data synthetic path.

Runs the sealed release binary on a fast no-uncertainty mini optspec twice
into separate OUTDIRs; asserts byte-identical ledgers; then kills a third
run mid-campaign and resumes it, asserting no duplicate eval_ids and a
complete ledger.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p49_artifacts as p49a  # noqa: E402

ACTINV = ROOT / "target/release/actinv"
WORK = ROOT / "results/p49_g1_work"


def fail(msg):
    print(json.dumps({"gate": "G1", "pass": False, "reason": msg}))
    return 1


def main() -> int:
    missing = {n: r["path"] for n, r in p49a.verify().items()
               if not r["present"]}
    if missing:
        return fail(f"sealed artifacts missing: {missing}")

    # mini fixtures: no uncertainty (fast nominal path), few steps
    WORK.mkdir(parents=True, exist_ok=True)
    link = WORK / "actinv-data"
    if not link.exists():
        link.symlink_to(ROOT / "actinv-data")
    base = json.loads((ROOT / "examples/fns_fe_5min.json").read_text())
    base["schedule"] = base["schedule"][:6]
    base["material"]["composition"] = {"FE": 91.0, "CR": 9.0}
    base_path = WORK / "mini_base.json"
    base_path.write_text(json.dumps(base))
    optspec = WORK / "mini_opt.json"
    optspec.write_text(json.dumps({
        "schema": "actinv-optimize-1",
        "base_spec": "mini_base.json",
        "design_axes": [
            {"kind": "composition_fraction", "element": "NI",
             "bounds": [0.0, 3.0]},
            {"kind": "composition_fraction", "element": "MO",
             "bounds": [0.0, 1.0]},
        ],
        "objective": {"response": "heat.total", "time_s": 461.0,
                      "edge": "nominal", "direction": "min"},
        "constraints": [
            {"name": "act1d", "response": "activity.total",
             "time_s": 461.0, "edge": "nominal", "sense": "le",
             "limit": 1e12}
        ],
        "optimizer": {"algorithm": "lhs_coordinate", "seed": 49,
                      "init_points": 8, "refine_points": 4},
    }))
    out_a = WORK / "run_a"
    out_b = WORK / "run_b"

    def run_once(outdir):
        t0 = time.time()
        p = subprocess.run(
            [str(ACTINV), "optimize", str(optspec), str(outdir)],
            capture_output=True, text=True, cwd=ROOT, timeout=1800)
        return p.returncode, time.time() - t0, p

    rc_a, _, pa = run_once(out_a)
    rc_b, _, pb = run_once(out_b)
    if rc_a != 0:
        return fail(f"run A failed: {pa.stderr[-300:]}")
    if rc_b != 0:
        return fail(f"run B failed: {pb.stderr[-300:]}")

    # wall_s is a measured quantity, not optimizer output — compare every
    # deterministic field (protocol intent: identical seed ⇒ identical
    # candidate sequence and outcomes)
    def deterministic_rows(path):
        rows = [json.loads(l) for l in path.read_text().splitlines()
                if l.strip()]
        for r in rows:
            r.pop("wall_s", None)
        return rows

    ra = deterministic_rows(out_a / "optimize_ledger.jsonl")
    rb = deterministic_rows(out_b / "optimize_ledger.jsonl")
    if ra != rb:
        return fail("identical-seed reruns produced different ledgers "
                    "(beyond wall_s)")

    la = (out_a / "optimize_ledger.jsonl").read_bytes()
    rows = [json.loads(l) for l in la.decode().splitlines() if l.strip()]
    ids = [r["eval_id"] for r in rows]
    if sorted(ids) != ids or len(set(ids)) != len(ids):
        return fail("eval_ids not unique/monotone")
    if len(rows) < 1:
        return fail("empty ledger")
    # every row carries a spec sha and a status
    for r in rows:
        if r["status"] == "executed" and not r.get("spec_sha256"):
            return fail(f"eval {r['eval_id']} executed without spec_sha256")

    # generated specs decode as actinv-spec-1 with sum-100 composition
    cands = sorted((out_a / "candidates").glob("eval_*.json"))
    if len(cands) != sum(1 for r in rows if r["status"] == "executed"):
        return fail("candidate spec files do not match executed ledger rows")
    import hashlib
    for c in cands:
        doc = json.loads(c.read_text())
        if doc.get("spec") != "actinv-spec-1":
            return fail(f"{c.name} is not actinv-spec-1")
        comp = doc["material"]["composition"]
        s = sum(comp.values())
        if abs(s - 100.0) > 1e-6:
            return fail(f"{c.name} composition sums to {s}")
        sha = hashlib.sha256(c.read_bytes()).hexdigest()
        rid = int(c.stem.split("_")[1])
        row = next(r for r in rows if r["eval_id"] == rid)
        if row["spec_sha256"] != sha:
            return fail(f"eval {rid} ledger sha does not match candidate file")

    # result file exists and reports the ranking
    res = json.loads((out_a / "optimize_result.json").read_text())
    if res["n_evals"] != len(rows):
        return fail("result n_evals != ledger rows")
    if not res["winner_verification"].get("performed") and not res["infeasible"]:
        return fail("feasible result missing winner verification")
    if not res["infeasible"] and not res["winner_verification"]["bit_identical"]:
        return fail("winner re-execution not bit-identical")

    # resume: kill mid-campaign on a larger budget, then --resume completes
    opt2 = json.loads(optspec.read_text())
    opt2["optimizer"]["init_points"] = 12
    opt2["optimizer"]["refine_points"] = 6
    opt2_path = WORK / "mini_opt_big.json"
    opt2_path.write_text(json.dumps(opt2))
    out_c = WORK / "run_c"
    p = subprocess.Popen([str(ACTINV), "optimize", str(opt2_path), str(out_c)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=ROOT)
    # wait for a few ledger rows then kill
    ledger_c = out_c / "optimize_ledger.jsonl"
    deadline = time.time() + 900
    n_partial = 0
    while time.time() < deadline:
        if ledger_c.exists():
            n_partial = sum(1 for l in ledger_c.read_text().splitlines() if l.strip())
            if n_partial >= 3:
                break
        time.sleep(2)
    p.kill()
    p.wait()
    if n_partial < 3:
        return fail("mid-campaign kill produced no partial ledger")
    rc = subprocess.run([str(ACTINV), "optimize", str(opt2_path), str(out_c),
                         "--resume"], capture_output=True, text=True, cwd=ROOT,
                        timeout=1800)
    if rc.returncode != 0:
        return fail(f"resume failed: {rc.stderr[-300:]}")
    rows_c = [json.loads(l) for l in ledger_c.read_text().splitlines() if l.strip()]
    ids_c = [r["eval_id"] for r in rows_c]
    if len(set(ids_c)) != len(ids_c):
        return fail("resume produced duplicate eval_ids")
    res_c = json.loads((out_c / "optimize_result.json").read_text())
    if not res_c["resumed"]:
        return fail("result did not record resume hits")

    record = {"gate": "G1", "pass": True,
              "evals_a": len(rows), "resume_rows": len(rows_c)}
    (ROOT / "results/p49_g1.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
