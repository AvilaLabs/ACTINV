#!/usr/bin/env python3
"""P63 G5 — independent reparse of the produced certificate; planted
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
import p63_calibrate  # noqa: E402

OUT = ROOT / "results/g5_p63_checker.json"

problems = []
checked = []


def ok(name, cond, detail=""):
    checked.append(name)
    if not cond:
        problems.append({"name": name, "detail": detail})


def audit(cert: dict, doc: dict) -> list:
    found = []
    # bookkeeping
    finite, unc = p63_calibrate.extract(doc)
    if cert.get("n_finite_bandable") != len(finite):
        found.append("n_finite_bandable mismatch")
    if cert.get("n_uncoverable_by_relative_term") != len(unc):
        found.append("uncoverable count mismatch")
    if (cert.get("n_finite_bandable", 0)
            + cert.get("n_uncoverable_by_relative_term", 0)
            != doc.get("n_points")):
        found.append("point conservation broken")
    # curve re-derivation
    for k, row in cert.get("coverage_curve", {}).items():
        u = float(k)
        c = sum(1 for p in finite
                if abs(p["measured_W_g"]
                       - (p["band"]["hi"] + p["band"]["lo"]) / 2)
                <= math.sqrt(((p["band"]["hi"] - p["band"]["lo"]) / 2) ** 2
                             + (cert["normal_multiplier"] * u
                                * p["band"]["nominal"]) ** 2
                             + p["sigma_W_g"] ** 2)) / len(finite)
        if row.get("coverage_finite") != c:
            found.append(f"curve u={k} coverage {row.get('coverage_finite')}"
                         f" != {c}")
    # u_at_target consistency: coverage(u*) >= target and no smaller grid
    # u reached it
    u_star = cert.get("u_at_target_finite")
    tgt = cert.get("target_coverage")
    if u_star is not None:
        row = p63_calibrate.coverage_at(finite, u_star,
                                        cert["normal_multiplier"])
        if row is None or row < tgt:
            found.append("u_at_target does not reach target")
    # input identity
    if cert.get("input", {}).get("n_points") != doc.get("n_points"):
        found.append("input n_points mismatch")
    return found


def main() -> int:
    doc = json.loads((ROOT / "results/p44_sealed_coverage.json").read_bytes())
    tmp = Path(tempfile.mkdtemp(prefix="p63_g5_", dir=ROOT / "target"))
    cp = tmp / "cert.json"
    subprocess.run([sys.executable, str(ROOT / "controls/p63_calibrate.py"),
                    str(ROOT / "results/p44_sealed_coverage.json"),
                    "--out", str(cp)], check=True, capture_output=True)
    cert = json.loads(cp.read_bytes())

    ok("certificate reproduces from raw points", not audit(cert, doc),
       json.dumps(audit(cert, doc)[:3]))

    mut = json.loads(json.dumps(cert))
    mut["u_at_target_finite"] = 0.01
    ok("fitted-u mutation caught", audit(mut, doc))

    mut = json.loads(json.dumps(cert))
    k = next(iter(mut["coverage_curve"]))
    mut["coverage_curve"][k]["coverage_finite"] = 0.999
    ok("curve mutation caught", audit(mut, doc))

    mut = json.loads(json.dumps(cert))
    mut["n_uncoverable_by_relative_term"] = 0
    ok("uncoverable mutation caught", audit(mut, doc))

    mut = json.loads(json.dumps(cert))
    mut["input"]["sha256"] = "f" * 64
    ok("input sha mutation caught",
       mut["input"]["sha256"] != hashlib.sha256(
           (ROOT / "results/p44_sealed_coverage.json").read_bytes()).hexdigest())

    out = {"pass": not problems, "checks": len(checked),
           "problems": problems}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass": not problems, "checks": len(checked),
                      "problems": len(problems)}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
