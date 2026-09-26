#!/usr/bin/env python3
"""P62 G5 — independent ledger checker. Parses a produced optimize ledger
and re-derives the cache mechanics invariants — then plants mutations.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p62_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g5_p62_checker.json"

SKIP = ("infeasible_by_axis", "axis_apply_error", "catalog_resolve_error",
        "spec_error", "run_error")

problems = []
checked = []


def ok(name, cond, detail=""):
    checked.append(name)
    if not cond:
        problems.append({"name": name, "detail": detail})


def audit(rows: list) -> list:
    found = []
    solved = [r for r in rows
              if not any(s in r["status"] for s in SKIP)]
    for r in solved:
        pc = (r.get("constraints") or {}).get("prepared_cache")
        if not isinstance(pc, dict):
            found.append(f"eval {r['eval_id']}: prepared_cache absent")
            continue
        if not isinstance(pc.get("hit"), bool):
            found.append(f"eval {r['eval_id']}: hit not boolean")
        if not isinstance(pc.get("fingerprint_ms"), (int, float)) \
                or pc["fingerprint_ms"] < 0:
            found.append(f"eval {r['eval_id']}: fingerprint_ms bad")
    if solved:
        if solved[0].get("constraints", {}).get("prepared_cache",
                                               {}).get("hit") is not False:
            found.append("first solved eval not a cold miss")
        for r in solved[1:]:
            pc = (r.get("constraints") or {}).get("prepared_cache")
            if isinstance(pc, dict) and pc.get("hit") is not True:
                found.append(f"eval {r['eval_id']}: warm eval not a hit")
    for r in rows:
        if r in solved:
            continue
        pc = (r.get("constraints") or {}).get("prepared_cache")
        if pc is not None:
            found.append(f"eval {r['eval_id']}: non-solved row claims "
                         "prepared_cache")
    return found


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p61_g5_", dir=ROOT / "target"))
    fx = p58_fixture.build(tmp)
    opt_path = p62_case.optspec(fx, tmp)
    run = p62_case.optimize(ACTINV, opt_path, tmp / "out")
    rows = run["rows"]

    found = audit(rows)
    ok("produced ledger reproduces", not found, json.dumps(found[:4]))

    mut = json.loads(json.dumps(rows))
    solved = [r for r in mut
              if not any(s in r["status"] for s in SKIP)]
    ok("need >=2 solved evals for mutations", len(solved) >= 2)
    if len(solved) >= 2:
        solved[0]["constraints"]["prepared_cache"]["hit"] = True
        ok("first-eval hit-flip caught", audit(mut))

        mut = json.loads(json.dumps(rows))
        solved = [r for r in mut
                  if not any(s in r["status"] for s in SKIP)]
        solved[1]["constraints"]["prepared_cache"]["hit"] = False
        ok("warm-eval miss-flip caught", audit(mut))

        mut = json.loads(json.dumps(rows))
        solved = [r for r in mut
                  if not any(s in r["status"] for s in SKIP)]
        del solved[1]["constraints"]["prepared_cache"]
        ok("missing-block caught", audit(mut))

    out = {"pass": not problems, "checks": len(checked),
           "problems": problems}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass": not problems, "checks": len(checked),
                      "problems": len(problems)}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
