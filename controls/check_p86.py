#!/usr/bin/env python3
"""P86 checker (protocols/ACTINV-P86_PROTOCOL.md): P85 with the shared-prepare wiring kept.

    python3 controls/check_p86.py   # verdict -> results/p86_verdict.json

Inputs: target/p86/build.log and test.txt, the release binaries, the archived P85 candidate binaries
(target/p85/cand_*), results/p86_p85_gates.json when the binaries differ, and the local CI replay
log copied to target/p86/ci_replay_summary.log.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "target" / "p86"
PROTOCOL = ROOT / "protocols" / "ACTINV-P86_PROTOCOL.md"
VERDICT = ROOT / "results" / "p86_verdict.json"
P85_VERDICT = ROOT / "results" / "p85_verdict.json"
RERUN = ROOT / "results" / "p86_p85_gates.json"
BINARIES = {
    "actinv": (ROOT / "target" / "release" / "actinv", ROOT / "target" / "p85" / "cand_actinv"),
    "cache_probe": (ROOT / "target" / "release" / "cache_probe", ROOT / "target" / "p85" / "cand_cache_probe"),
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    registered = f"{sha(PROTOCOL)}  protocols/ACTINV-P86_PROTOCOL.md" in (ROOT / "protocols/protocol_hash.txt").read_text()
    blog = (WORK / "build.log").read_text()
    rc = dict(re.findall(r"^(fmt|clippy|test|release) rc=(\d+)$", blog, re.M))
    unit = "collapsed_artifact_equals_groupwise_collapse_bit_for_bit ... ok" in (WORK / "test.txt").read_text()
    g1 = all(rc.get(k) == "0" for k in ("fmt", "clippy", "test", "release")) and unit

    hashes = {name: {"built": sha(built), "p85_candidate": sha(p85)} for name, (built, p85) in BINARIES.items()}
    identical = all(h["built"] == h["p85_candidate"] for h in hashes.values())
    p85 = json.loads(P85_VERDICT.read_text())
    p85_runtime = all(p85[g]["pass"] for g in ("G2", "G3", "G5"))
    g2 = {"binaries_identical": identical, "binaries": hashes,
          "p85_runtime_gates_pass": p85_runtime, "p85_speedup": p85["G5"]["speedup"]}
    if identical:
        g2["pass"] = p85_runtime
    else:
        # Binaries differ: the P85 runtime gates must pass again on this build (controls/check_p86_rerun.py).
        rerun = json.loads(RERUN.read_text()) if RERUN.exists() else None
        on_this_build = rerun is not None and rerun["inputs"]["candidate"] == {
            "actinv": hashes["actinv"]["built"], "probe": hashes["cache_probe"]["built"]}
        rerun_pass = on_this_build and all(rerun[g]["pass"] for g in ("G2", "G3", "G5"))
        g2.update({"pass": rerun_pass, "rerun_on_this_build": on_this_build,
                   "rerun": None if rerun is None else {
                       "G2": rerun["G2"]["pass"], "G3": rerun["G3"]["pass"], "G5": rerun["G5"]["pass"],
                       "speedup": rerun["G5"]["speedup"], "ref_median_ms": rerun["G5"]["ref_median_ms"],
                       "cand_median_ms": rerun["G5"]["cand_median_ms"]}})

    steps = re.findall(r"^STEP (\d+) (\S+)$", (WORK / "ci_replay_summary.log").read_text(), re.M)
    failed = [name for code, name in steps if code != "0"]
    g3 = {"pass": bool(steps) and not failed, "steps": len(steps), "failed": failed}

    p85_steps = re.findall(r"^STEP (\d+) (\S+)$", (ROOT / "target" / "p85" / "ci_replay_summary.log").read_text(), re.M)
    p85_g4 = {"pass": bool(p85_steps) and all(code == "0" for code, _ in p85_steps),
              "failed": [name for code, name in p85_steps if code != "0"]}

    verdict = {
        "protocol": "ACTINV-P86",
        "p85_G4_ci_replay": p85_g4,
        "inputs": {"protocol_sha256": sha(PROTOCOL), "p85_verdict_sha256": sha(P85_VERDICT)},
        "G0": {"pass": registered},
        "G1": {"pass": g1, "exit_codes": rc, "unit_test_ok": unit},
        "G2": g2,
        "G3": g3,
        "pass": registered and g1 and g2["pass"] and g3["pass"],
    }
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"G0": registered, "G1": g1, "G2": g2["pass"], "binaries_identical": identical,
                      "G3": g3["pass"], "failed_steps": failed, "pass": verdict["pass"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
