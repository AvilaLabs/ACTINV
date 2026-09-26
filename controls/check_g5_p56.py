#!/usr/bin/env python3
"""P56 G5 — independent checker: reparse the produced result+ledger,
recompute every certified value, catch planted mutations."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p56_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g5_p56_checker.json"

problems = []
checked = []


def ok(name, cond, detail=""):
    checked.append(name)
    if not cond:
        problems.append({"name": name, "detail": detail})


def audit(rows: list, result: dict) -> list:
    found = []
    cert = result.get("certification")
    if not isinstance(cert, dict):
        return ["certification absent"]
    opt_c = result.get("constraints", [])
    # margins recomputed exactly
    w = cert.get("winner")
    if w:
        for c in w.get("constraints", []):
            e, lim = c.get("edge_value"), c.get("limit")
            if e is None or lim is None:
                found.append(f"{c.get('name')}: missing edge/limit")
                continue
            expect = ((lim - e) if c["sense"] == "le" else (e - lim)) \
                / abs(lim)
            if c.get("margin_fraction") != expect:
                found.append(f"{c['name']}: margin {c.get('margin_fraction')}"
                             f" != {expect}")
        if result.get("infeasible"):
            found.append("winner present but infeasible flagged")
    # overcertify recomputed
    n = 0
    for r in rows:
        if r["status"] != "executed":
            continue
        nom_ok, banded_fail, has_all = True, False, True
        for i, c in enumerate(opt_c):
            v = r["violations"][i]
            if c["kind"] == "axis":
                if v is not None and v > 0:
                    nom_ok = False
                continue
            if c["edge"] == "nominal":
                if v is None or v > 0:
                    nom_ok = False
                continue
            ent = r["constraints"].get(f"constraint.{c['name']}")
            if not isinstance(ent, dict) or "nominal_violation" not in ent:
                has_all = False
                break
            if not ent["nominal_violation"] <= 0:
                nom_ok = False
            if v is not None and v > 0:
                banded_fail = True
        if has_all and nom_ok and banded_fail:
            n += 1
    if cert.get("nominal_would_overcertify") != n:
        found.append(f"overcertify {cert.get('nominal_would_overcertify')} "
                     f"!= {n}")
    return found


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p56_g5_", dir=ROOT / "target"))
    fx = p58_fixture.build(tmp)
    run = p56_case.optimize(ACTINV, p56_case.optspec(fx, tmp, 1.5e-37),
                            tmp / "out")
    rows, result = run["rows"], run["result"]

    ok("produced artifacts reproduce", not audit(rows, result),
       json.dumps(audit(rows, result)[:3]))

    mut = json.loads(json.dumps(result))
    mut["certification"]["winner"]["constraints"][0]["margin_fraction"] = -9.9
    ok("margin mutation caught", audit(rows, mut))

    mut = json.loads(json.dumps(result))
    mut["certification"]["nominal_would_overcertify"] = 99
    ok("overcertify mutation caught", audit(rows, mut))

    mut = json.loads(json.dumps(result))
    del mut["certification"]
    ok("missing certification caught", audit(rows, mut))

    out = {"pass": not problems, "checks": len(checked),
           "problems": problems}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass": not problems, "checks": len(checked),
                      "problems": len(problems)}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
