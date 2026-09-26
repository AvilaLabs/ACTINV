#!/usr/bin/env python3
"""P62 G1 — mechanics: the shared PreparedCache inside `actinv optimize`
reports a miss on the first solved eval and hits afterwards; non-solved
(ledgered-axis-violation) rows never fabricate a hit; the search output
is unchanged in shape.
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
OUT = ROOT / "results/g1_p62_mechanics.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p62_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
opt_path = p62_case.optspec(fx, tmp)
out_dir = tmp / "out"
run = p62_case.optimize(ACTINV, opt_path, out_dir)
rows = run["rows"]

check("evals ledgered", len(rows) >= 2, f"n={len(rows)}")
sv = p62_case.solved(rows)
check("at least two solved evals", len(sv) >= 2,
      json.dumps([r["status"] for r in rows]))
check("every solved eval carries the observability block",
      all(isinstance(r.get("constraints", {}).get("prepared_cache", {})
                     .get("hit"), bool) for r in sv))
check("first solved eval is a cold miss", sv and sv[0]
      ["constraints"]["prepared_cache"]["hit"] is False)
check("subsequent solved evals are hits",
      all(r["constraints"]["prepared_cache"]["hit"] is True
          for r in sv[1:]),
      json.dumps([r["constraints"].get("prepared_cache") for r in sv]))
check("fingerprint_ms present and finite",
      all(isinstance(r["constraints"]["prepared_cache"]
                     .get("fingerprint_ms"), (int, float))
          and r["constraints"]["prepared_cache"]["fingerprint_ms"] >= 0
          for r in sv))
nonsolved = [r for r in rows if r not in sv]
check("non-solved rows never claim a hit",
      all(r.get("constraints", {}).get("prepared_cache") is None
          for r in nonsolved),
      f"{len(nonsolved)} non-solved rows")
check("result ranks a winner or reports infeasible honestly",
      run["result"].get("best_feasible", {}).get("eval_id") is not None
      or run["result"].get("infeasible") is True)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks,
                           "ledger_rows": len(rows)},
                          indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
