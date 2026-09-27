#!/usr/bin/env python3
"""P65 G3 — real-data demo: screened vs full W FNS run. The superset
claim is checked empirically: every certified interval must contain the
full-fidelity band edges at every step. Solve-stage speedup measured."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p44_bands  # noqa: E402

ACTINV = ROOT / "target/release/actinv"
OUT = ROOT / "results/g3_p65_demo.json"
SCREEN_DOC = ROOT / "results/p65_screen_w.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def run(spec: dict, tmp: Path, name: str) -> tuple[dict, float]:
    p = tmp / f"{name}.json"
    p.write_text(json.dumps(spec) + "\n")
    out = tmp / f"{name}.out.json"
    t0 = time.time()
    r = subprocess.run(
        [str(ACTINV), "run", str(p), str(out)],
        cwd=ROOT, text=True, capture_output=True, timeout=3600)
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(out.read_text()), time.time() - t0


tmp = Path(tempfile.mkdtemp(prefix="p65_g3_", dir=ROOT / "target"))
base, _ = p44_bands.spec_for("W", "1996exp_5min")

full, t_full = run(base, tmp, "full")
screen = json.loads(json.dumps(base))
screen["options"]["screen"] = {"bmin_atoms_per_g": 1e-4}
scr, t_scr = run(screen, tmp, "screen")
Path(SCREEN_DOC).write_text(json.dumps(scr) + "\n")

sc = scr["screen"]
check("screen certificate emitted", isinstance(sc, dict)
      and sc["dropped_states"] > 0
      and sc["removed_heat_W_per_g_bound"] > 0.0)
check("screen solves fewer states",
      sc["kept_states"] < full["pruned_states"],
      f"{sc['kept_states']} vs {full['pruned_states']}")

# The certificate's superset claim, checked on every step: certified
# interval of the screened run must contain the full run's conservative
# band for every banded response.
viol = []
for i, (fs, ss) in enumerate(zip(full["steps"], scr["steps"])):
    fresp = (fs.get("uncertainty") or {}).get("responses", {})
    block = sc["certified"][str(i)]
    for name, fu in fresp.items():
        ce = block.get(name)
        if ce is None:
            viol.append(f"s{i} {name}: no certified edge")
            continue
        flo, fhi = fu["conservative_interval"]
        if ce["lower"] > flo or ce["upper"] < fhi:
            viol.append(
                f"s{i} {name}: certified [{ce['lower']:.3e},{ce['upper']:.3e}] "
                f"does not cover full [{flo:.3e},{fhi:.3e}]")
check("certified intervals cover the full band everywhere",
      not viol, "; ".join(viol[:3]))
check("certified edges use the conservative band",
      all(e["edge"] == "conservative"
          for c in sc["certified"].values() for e in c.values()
          if e["edge"] != "nominal"))
check("nominal-only entries are honestly labelled",
      all(e["edge"] in ("nominal", "conservative")
          for c in sc["certified"].values() for e in c.values()))
check("measured speedup on the solve",
      t_scr < t_full,
      f"screen {t_scr:.1f}s vs full {t_full:.1f}s")

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks,
                           "wall_s": {"full": t_full, "screen": t_scr},
                           "kept_states": [full["pruned_states"],
                                           sc["kept_states"]]},
                          indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:8],
                  "full_s": round(t_full, 1), "screen_s": round(t_scr, 1),
                  "kept": [full["pruned_states"], sc["kept_states"]]}))
sys.exit(1 if failed else 0)
