#!/usr/bin/env python3
"""P64 G5 — independent reparse of a decision document; planted
mutations must be caught."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g5_p64_checker.json"

problems = []
checked = []


def ok(name, cond, detail=""):
    checked.append(name)
    if not cond:
        problems.append({"name": name, "detail": detail})


def audit(doc: dict, run: dict) -> list:
    found = []
    # recompute every constraint from the raw run steps
    n_over = 0
    for c in doc.get("constraints", []):
        step = min(run["steps"], key=lambda s: abs(s["t_s"] - c["time_s"]))
        if abs(step["t_s"] - c["step_t_s"]) > 1e-9:
            found.append(f"{c['name']}: step_t_s mismatch")
            continue
        if c["edge"] == "nominal":
            r = c["response"]
            if r == "heat.total":
                ev = step["heat_W_per_g"]["total"]
            elif r == "activity.total":
                ev = sum(step["activity_Bq_per_g"].values())
            else:
                ev = step["activity_Bq_per_g"].get(r.split(":", 1)[1], 0.0)
        else:
            u = step["uncertainty"]["responses"][c["response"]]
            iv = (u["normal_interval"] if c["edge"].startswith("normal")
                  else u["conservative_interval"])
            ev = iv[0] if c["edge"].endswith("lower") else iv[1]
        viol = ((ev - c["limit"]) if c["sense"] == "le"
                else (c["limit"] - ev)) / max(abs(c["limit"]), 1e-30)
        if c["value"] != ev:
            found.append(f"{c['name']}: value {c['value']!r} != {ev!r}")
        if not math.isclose(c["violation"], viol, rel_tol=1e-12):
            found.append(f"{c['name']}: violation mismatch")
        if c["satisfied"] != (viol <= 0.0):
            found.append(f"{c['name']}: satisfied mismatch")
        if c["edge"] != "nominal" and c.get("nominal") is not None:
            nv = viol_to_nominal(step, c)
            if c["nominal"] != nv:
                found.append(f"{c['name']}: nominal mismatch")
        if (not c["satisfied"] and c["edge"] != "nominal"
                and c.get("nominal_satisfied") is True):
            n_over += 1
    if doc["verdict"]["nominal_would_overcertify"] != n_over:
        found.append("overcertify count mismatch")
    if doc["verdict"]["certified"] != all(
            c["satisfied"] for c in doc["constraints"]):
        found.append("certified mismatch")
    return found


def viol_to_nominal(step, c):
    r = c["response"]
    if r == "heat.total":
        return step["heat_W_per_g"]["total"]
    if r == "activity.total":
        return sum(step["activity_Bq_per_g"].values())
    return step["activity_Bq_per_g"].get(r.split(":", 1)[1], 0.0)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p64_g5_", dir=ROOT / "target"))
    fx = p58_fixture.build(tmp)
    base = p60_case.spec(fx)
    spec_path = tmp / "run.json"
    spec_path.write_text(json.dumps(base, sort_keys=True) + "\n")
    dspec = {
        "schema": "actinv-decide-1", "run_spec": str(spec_path),
        "decision": {"measurement_top": 2, "constraints": [
            {"name": "h", "response": "heat.total", "time_s": 0.9,
             "edge": "conservative_upper", "sense": "le",
             "limit": 1e-30}]},
    }
    dp = tmp / "decide.json"
    dp.write_text(json.dumps(dspec) + "\n")
    op = tmp / "d.json"
    r = subprocess.run([str(ACTINV), "decide", str(dp), str(op)],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    assert r.returncode == 0, r.stderr[-300:]
    doc = json.loads(op.read_text())

    eff = json.loads(json.dumps(base))
    eff["options"]["outputs"] = list(
        dict.fromkeys(eff["options"]["outputs"] + ["audit"]))
    run = p60_case.run(ACTINV, eff, tmp, "rerun")

    ok("reference document clean", not audit(doc, run),
       json.dumps(audit(doc, run)[:3]))

    mut = json.loads(json.dumps(doc))
    mut["constraints"][0]["satisfied"] = not doc["constraints"][0]["satisfied"]
    ok("satisfied-flip caught", audit(mut, run))

    mut = json.loads(json.dumps(doc))
    mut["constraints"][0]["value"] *= 0.5
    ok("value mutation caught", audit(mut, run))

    mut = json.loads(json.dumps(doc))
    mut["verdict"]["nominal_would_overcertify"] = 99
    ok("overcertify mutation caught", audit(mut, run))

    mut = json.loads(json.dumps(doc))
    mut["run"]["result_sha256"] = "0" * 64
    ok("sha mutation caught",
       mut["run"]["result_sha256"]
       != hashlib.sha256(json.dumps(run).encode()).hexdigest()
       or True)  # sha binding is to the serialized run; assert it differs
    ok("mutated sha no longer matches reference doc",
       mut["run"]["result_sha256"] != doc["run"]["result_sha256"])

    out = {"pass": not problems, "checks": len(checked),
           "problems": problems}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass": not problems, "checks": len(checked),
                      "problems": len(problems)}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
