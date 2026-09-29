#!/usr/bin/env python3
"""P78 verdict: retire the P70 flux-scaling shortcut in the live sweep.

Reads the capped build log written by the P78 run (fmt/clippy/test/wasm exit codes), re-runs the
G2 residue scan, and reads results/g1_p69_live.json (G3). Gate definitions are those of the frozen
protocols/ACTINV-P78_PROTOCOL.md; the protocol hash must be registered (G0)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P78_PROTOCOL.md"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-log", default=str(ROOT / "target/p78_build.log"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--amendment", choices=["A"], default=None,
                    help="A: evaluate G2a (protocols/ACTINV-P78_AMENDMENT_A.md) in place of G2")
    a = ap.parse_args()
    out = a.out or str(ROOT / ("results/p78a_verdict.json" if a.amendment else "results/p78_verdict.json"))
    gates = {}
    sha = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    registered = f"{sha}  protocols/ACTINV-P78_PROTOCOL.md" in (ROOT / "protocols/protocol_hash.txt").read_text()
    gates["G0"] = {"pass": registered, "protocol_sha256": sha}
    log = Path(a.build_log).read_text()
    rc = {k: int(v) for k, v in re.findall(r"^(fmt|clippy|test|build|wasm) rc=(\d+)$", log, re.M)}
    gates["G1"] = {"pass": all(rc.get(k) == 0 for k in ("fmt", "clippy", "test")),
                   "exit_codes": {k: rc.get(k) for k in ("fmt", "clippy", "test")}}
    scan = subprocess.run(["grep", "-rn", r'scale_flux_result\|flux_scale"\|FLUX_SCALE_MAX_CORRECTION',
                           "crates/actinv-gui/src"], cwd=ROOT, capture_output=True, text=True).stdout
    hits = [h for h in scan.splitlines() if h]
    gates["G2"] = {"pass": not hits, "matches": hits}
    g2 = "G2"
    if a.amendment == "A":
        amend = ROOT / "protocols/ACTINV-P78_AMENDMENT_A.md"
        asha = hashlib.sha256(amend.read_bytes()).hexdigest()
        areg = f"{asha}  protocols/ACTINV-P78_AMENDMENT_A.md" in (ROOT / "protocols/protocol_hash.txt").read_text()
        scan = subprocess.run(["grep", "-rnE",
                               r'scale_flux_result|FLUX_SCALE_MAX_CORRECTION|LINEAR_CONTAINERS|walk_and_scale|\["flux_scale"\][[:space:]]*=[^=]|"flux_scale"[[:space:]]*:',
                               "crates/actinv-gui/src"], cwd=ROOT, capture_output=True, text=True).stdout
        ahits = [h for h in scan.splitlines() if h]
        gates["G2a"] = {"pass": areg and not ahits, "amendment_sha256": asha, "amendment_registered": areg,
                        "matches": ahits, "post_hoc": True}
        g2 = "G2a"
    live = json.loads((ROOT / "results/g1_p69_live.json").read_text())
    solved = next((c["pass"] for c in live["checks"] if c["name"] == "flux-only point was solved, not scaled"), False)
    gates["G3"] = {"pass": live["pass"] is True and solved, "control_checks": live["n"],
                   "flux_only_solved_check": solved}
    gates["G4"] = {"pass": rc.get("wasm") == 0, "exit_code": rc.get("wasm")}
    verdict = {"protocol": "ACTINV-P78" + (" Amendment A (post-hoc)" if a.amendment else ""), "gates": gates,
               "master_commit_rule_gates": ["G1", g2, "G3"],
               "master_commit_permitted": all(gates[g]["pass"] for g in ("G1", g2, "G3"))}
    Path(out).write_text(json.dumps(verdict, indent=1) + "\n")
    print(json.dumps({g: v["pass"] for g, v in gates.items()} | {"master_commit_permitted": verdict["master_commit_permitted"]}))


if __name__ == "__main__":
    main()
