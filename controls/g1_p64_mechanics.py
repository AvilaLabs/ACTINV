#!/usr/bin/env python3
"""P64 G1 — decide mechanics on the fixture: certifying + failing +
overcertify constraints, injection of audit/design, refusal on an
unbanded spec."""
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
OUT = ROOT / "results/g1_p64_mechanics.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def decide(dpath: Path, out: Path) -> tuple[dict, subprocess.CompletedProcess]:
    r = subprocess.run([str(ACTINV), "decide", str(dpath), str(out)],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    return (json.loads(out.read_text()) if out.exists() else {}, r)


tmp = Path(tempfile.mkdtemp(prefix="p64_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
spec_path = tmp / "run.json"
spec_path.write_text(json.dumps(base, sort_keys=True) + "\n")

# Probe once to place limits inside/outside the band.
probe = p60_case.run(ACTINV, base, tmp, "probe")
resp = probe["steps"][1]["uncertainty"]["responses"]["heat.total"]
hi = resp["conservative_interval"][1]
nom = resp["nominal"]
nhi = resp["normal_interval"][1]

dspec = {
    "schema": "actinv-decide-1",
    "run_spec": str(spec_path),
    "decision": {
        "measurement_top": 3,
        "constraints": [
            # certifying: limit above the conservative edge
            {"name": "heat_ok", "response": "heat.total", "time_s": 0.9,
             "edge": "conservative_upper", "sense": "le",
             "limit": hi * 1.1},
            # failing + overcertify: nominal passes, band fails
            {"name": "heat_tight", "response": "heat.total", "time_s": 0.9,
             "edge": "normal_upper", "sense": "le",
             "limit": (nom + nhi) / 2.0},
        ],
    },
}
dp = tmp / "decide.json"
dp.write_text(json.dumps(dspec) + "\n")
doc, r = decide(dp, tmp / "decision.json")
check("decide ran clean", r.returncode == 0, r.stderr[-300:])
check("schema", doc.get("schema") == "actinv-decision-1")

c0, c1 = doc["constraints"]
check("constraint values are the band edges",
      c0["value"] == hi and c1["value"] == nhi,
      f"{c0['value']!r} {c1['value']!r}")
check("certifying constraint satisfied", c0["satisfied"] is True
      and c0["violation"] < 0)
check("failing constraint fails at band edge", c1["satisfied"] is False
      and c1["violation"] > 0)
check("failing constraint passes nominally",
      c1["nominal"] == nom and c1["nominal_satisfied"] is True)
check("verdict not certified", doc["verdict"]["certified"] is False)
check("nominal_would_overcertify == 1",
      doc["verdict"]["nominal_would_overcertify"] == 1)
check("provenance names covariance",
      doc["uncertainty_provenance"]["confidence_level"] == 0.95
      and len(doc["uncertainty_provenance"]["covariance_sha256"]) == 64)
check("audit block present (injected)",
      isinstance(doc.get("completeness"), dict))
check("measurements emitted for constraint response",
      "heat.total@0.9" in doc["measurements"]
      and len(doc["measurements"]["heat.total@0.9"]["top_parameters"]) <= 3)

# All-pass variant certifies.
dspec["decision"]["constraints"][1]["limit"] = nhi * 1.1
dp.write_text(json.dumps(dspec) + "\n")
doc2, r2 = decide(dp, tmp / "decision2.json")
check("relaxed spec certifies", r2.returncode == 0
      and doc2["verdict"]["certified"] is True
      and doc2["verdict"]["nominal_would_overcertify"] == 0)
check("certified statement names covariance+confidence",
      "certified" in doc2["verdict"]["statement"]
      and "0.95" in doc2["verdict"]["statement"])

# Unbanded run spec is refused.
nb = json.loads(json.dumps(base))
nb.pop("uncertainty")
spec_path.write_text(json.dumps(nb) + "\n")
doc3, r3 = decide(dp, tmp / "decision3.json")
check("unbanded run spec refused", r3.returncode != 0
      and "banded run" in r3.stderr)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:10]}))
sys.exit(1 if failed else 0)
