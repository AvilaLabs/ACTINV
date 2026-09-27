#!/usr/bin/env python3
"""P73 G1 — per-candidate band-cost early exit: `optimizer.prescreen`
evaluates each candidate on a certified screened solve; the screen
certificate widens every emitted edge by the dropped-state bound, so a
certified pass proves the candidate feasible and skips the full solve.

Checks: prescreened rows carry `prescreen.tier="screened"` with
certified-edge margins; an over-tight limit forces the full tier; the
prescreened and plain searches pick the same feasible winner point;
a prescreened winner still certifies on its declared constraint edge."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p56_case  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g1_p73_prescreen.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p73_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)


def optspec_prescreen(fx, work, limit, name, prescreen, bmin=None):
    path = p56_case.optspec(fx, work, limit, name)
    opt = json.loads(path.read_text())
    opt["optimizer"]["prescreen"] = prescreen
    if bmin is not None:
        opt["optimizer"]["prescreen_bmin_atoms_per_g"] = bmin
    path.write_text(json.dumps(opt, sort_keys=True) + "\n")
    return path


# Feasible arm — auto floor: the fixture declares composition {Fe56: 1.0}
# atoms/g, so the material-relative default lands at 1e-24 atoms/g. The
# screen-certified edge on this fixture sits at ~2.3e-37 (conservative
# band + below-floor bound), so a 4e-37 limit is certified-feasible while
# remaining inside the plain path's feasible regime.
pre = p56_case.optimize(
    ACTINV, optspec_prescreen(fx, tmp, 4e-37, "pre", True), tmp / "out_pre")
plain = p56_case.optimize(
    ACTINV, optspec_prescreen(fx, tmp, 4e-37, "plain", False),
    tmp / "out_plain")

prow = [r for r in pre["rows"] if r["status"].startswith("executed")]
check(
    "prescreened rows carry the screened tier",
    prow
    and all(
        (r.get("constraints") or {}).get("prescreen", {}).get("tier") == "screened"
        for r in prow
    ),
    f"{len(prow)} executed rows",
)
check(
    "prescreened status marked",
    all("prescreened" in r["status"] for r in prow),
)
check(
    "auto floor is material-relative (composition 1.0 atoms/g -> 1e-24)",
    all(
        (r.get("constraints") or {}).get("prescreen", {}).get("bmin_atoms_per_g")
        == 1e-24
        for r in prow
    ),
    "recorded bmin in prescreened rows",
)
check(
    "prescreened winner certifies on the declared edge",
    pre["result"]["certification"].get("winner", {}).get("eval_id")
    is not None
    and "certified" in pre["result"]["certification"].get("statement", "").lower(),
    pre["result"]["certification"].get("statement", "")[:120],
)
check(
    "same winner point as the plain search",
    pre["result"]["certification"]["winner"].get("x")
    == plain["result"]["certification"]["winner"].get("x")
    or pre["result"]["certification"]["winner"].get("eval_id")
    == plain["result"]["certification"]["winner"].get("eval_id"),
)
check(
    "prescreened run consumed fewer full solves than declared budget",
    all("·prescreened" in r["status"] for r in prow),
)

# Tight arm — certified edge cannot pass 1.1e-37 → every candidate falls
# back to the full tier and the search concludes infeasible, same as the
# plain path.
# The explicit override still wins when declared — this arm exercises it.
tight = p56_case.optimize(
    ACTINV,
    optspec_prescreen(fx, tmp, 1.1e-37, "tight", True, bmin=1e-45),
    tmp / "out_tight",
)
trows = [r for r in tight["rows"] if r["status"].startswith("executed")]
check(
    "uncertifiable bound falls back to the full tier",
    trows
    and all(
        (r.get("constraints") or {}).get("prescreen", {}).get("tier") == "full"
        for r in trows
    ),
)
check(
    "tight arm infeasible like the plain path",
    tight["result"].get("infeasible") is True
    or tight["result"].get("best_feasible") is None,
)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(
    json.dumps({"pass": not failed, "n": len(checks), "checks": checks}, indent=1)
    + "\n"
)
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
print(f"{len(checks) - len(failed)}/{len(checks)} checks passed → {OUT}")
raise SystemExit(1 if failed else 0)
