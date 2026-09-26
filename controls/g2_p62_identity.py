#!/usr/bin/env python3
"""P62 G2 — identity: cache reuse cannot change an answer. Every solved
candidate's canonical spec is re-run through the bare `actinv run` path
(fresh preparation each call) and its objective recomputed — bit-equal
to the ledgered objective.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p62_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g2_p62_identity.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p62_g2_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
opt_path = p62_case.optspec(fx, tmp)
out_dir = tmp / "out"
run = p62_case.optimize(ACTINV, opt_path, out_dir)
rows = run["rows"]
sv = p62_case.solved(rows)

check("solved evals exist", len(sv) >= 2)
for r in sv:
    canon = json.loads(
        (out_dir / "candidates" / f"eval_{r['eval_id']:04}.json")
        .read_text())
    sp = tmp / f"re_{r['eval_id']}.spec.json"
    op = tmp / f"re_{r['eval_id']}.out.json"
    sp.write_text(json.dumps(canon, sort_keys=True) + "\n")
    rr = subprocess.run([str(ACTINV), "run", str(sp), str(op)],
                        cwd=ROOT, text=True, capture_output=True,
                        timeout=120)
    check(f"eval {r['eval_id']} re-runs", rr.returncode == 0,
          rr.stderr[-200:])
    if rr.returncode != 0:
        continue
    doc = json.loads(op.read_text())
    t_s = 0.9
    obj = None
    for step in doc["steps"]:
        if abs(step["t_s"] - t_s) <= 1e-3 * max(1.0, t_s):
            obj = step.get("heat_W_per_g", {}).get("total")
    check(f"eval {r['eval_id']} objective bit-identical",
          obj is not None and obj == r["objective"],
          f"{obj!r} vs {r['objective']!r}")

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
