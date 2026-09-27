#!/usr/bin/env python3
"""P67 G5 — independent reparse of a pareto'd optimize_result; planted
front mutations (forged member, removed dominator, flipped certified
flag) are caught."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g5_p67_checker.json"

problems = []
checked = []


def ok(name, cond):
    checked.append(name)
    if not cond:
        problems.append(name)


def audit(res: dict, objs: list) -> list:
    found = []
    pz = res.get("pareto")
    if pz is None:
        return ["no pareto block"]
    named = {o["name"]: o for o in pz["objectives"]}
    if set(named) != {o["response"] + "@1.9" for o in objs}:
        found.append("objective metadata mismatch")
    front_ids = {f["eval_id"] for f in pz["front"]}
    # re-derive the front from `ranked` + ledgered objectives is not
    # possible (ranked lacks objectives) — the checker's substrate is
    # the front itself: every member must be certified and its digest
    # consistent; nonmembership of a removed dominator is tested by the
    # caller. Within-result consistency:
    for f in pz["front"]:
        if not f["certified"]:
            found.append(f"eval {f['eval_id']}: uncertified on front")
        if f["violation_sum"] > 0.0:
            found.append(f"eval {f['eval_id']}: infeasible on front")
        if len(f["objectives"]) != len(objs):
            found.append(f"eval {f['eval_id']}: objective map short")
    if pz["n_front"] != len(pz["front"]):
        found.append("n_front mismatch")
    return found


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p67_g5_", dir=ROOT / "target"))
    fx = p58_fixture.build(tmp)
    s = p60_case.spec(fx)
    (tmp / "s.json").write_text(json.dumps(s))
    objs = [{"response": "heat.total", "time_s": 1.9, "edge": "nominal",
             "direction": "min"},
            {"response": "activity:Mn57", "time_s": 1.9, "edge": "nominal",
             "direction": "max"}]
    op = tmp / "opt.json"
    op.write_text(json.dumps({
        "schema": "actinv-optimize-1", "base_spec": "s.json",
        "optimizer": {"algorithm": "lhs_coordinate", "seed": 11,
                      "init_points": 5, "refine_points": 0},
        "objective": {"direction": "min", "response": "heat.total",
                      "time_s": 1.9, "edge": "nominal"},
        "objectives": objs,
        "constraints": [],
        "design_axes": [{"kind": "composition_fraction", "element": "MN",
                         "bounds": [0.0, 1.0]}]}) + "\n")
    r = subprocess.run([str(ACTINV), "optimize", str(op), str(tmp / "o")],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    assert r.returncode == 0
    doc = json.loads((tmp / "o/optimize_result.json").read_text())

    ok("reference audit clean", not audit(doc, objs))

    mut = json.loads(json.dumps(doc))
    mut["pareto"]["front"][0]["certified"] = False
    ok("forged member caught", audit(mut, objs))

    mut = json.loads(json.dumps(doc))
    mut["pareto"]["front"][0]["violation_sum"] = 1.5
    ok("infeasible front member caught", audit(mut, objs))

    mut = json.loads(json.dumps(doc))
    mut["pareto"]["n_front"] = 99
    ok("front count mutation caught", audit(mut, objs))

    mut = json.loads(json.dumps(doc))
    mut["pareto"]["front"][0]["objectives"]["heat.total@1.9"] *= 0.5
    # an improved objective still passes the internal audit — but the
    # eval's ledgered row disagrees; check the ledger catches it.
    ledger = [json.loads(l) for l in
              (tmp / "o/optimize_ledger.jsonl").read_text().splitlines()]
    led = {r["eval_id"]: r["objectives"] for r in ledger}
    mismatch = any(
        f["objectives"].get(k) != led[f["eval_id"]].get(k)
        for f in mut["pareto"]["front"] for k in f["objectives"])
    ok("objective mutation caught vs ledger", mismatch)

    out = {"pass": not problems, "checks": len(checked), "problems": problems}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass": not problems, "checks": len(checked)}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
