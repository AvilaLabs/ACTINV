#!/usr/bin/env python3
"""P76 amendment A (protocols/ACTINV-P76_AMENDMENT_A.md): ex-vessel scenario, nitrogen as a budget
variable. Reuses the P76 tool with its configuration and work directory overridden.

    python3 controls/p76a_impurity_budget.py {build|run|budget|verify|check}
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("p76", ROOT / "controls" / "p76_impurity_budget.py")
p76 = importlib.util.module_from_spec(_s)
_s.loader.exec_module(p76)

p76.WORK = ROOT / "target" / "p76a"
p76.SPECS = p76.WORK / "specs"
p76.RAW = p76.WORK / "raw"
p76.CHECKPOINT = p76.WORK / "runs.jsonl"
p76.MANIFEST = p76.WORK / "cases.json"
p76.BUDGET = p76.WORK / "budget.json"
p76.PROTOCOL = ROOT / "protocols" / "ACTINV-P76_AMENDMENT_A.md"

p76.SCENARIOS = {
    # moderated ex-vessel field (cryostat / port-cell order of magnitude)
    "exvessel": {"spectrum": "mix", "total": 1.0e9},
}
# nitrogen moves from the fixed matrix into the budget (C-14 driver in P76)
p76.MATERIALS = {
    "eurofer97": {
        "matrix": {"Cr": 9.0, "W": 1.1, "Mn": 0.4, "V": 0.2, "Ta": 0.12, "C": 0.11, "Si": 0.05},
        "impurity_spec": {"N": 0.03, "Nb": 0.001, "Mo": 0.005, "Ni": 0.005, "Cu": 0.005, "Co": 0.005,
                          "Al": 0.01, "Ti": 0.01, "Ag": 0.0001},
    },
    "ss316ln": {
        "matrix": {"Cr": 17.5, "Ni": 12.25, "Mo": 2.5, "Mn": 1.8, "Si": 0.5, "C": 0.03, "P": 0.025,
                   "S": 0.01},
        "impurity_spec": {"N": 0.07, "Co": 0.05, "Nb": 0.01, "Ta": 0.01, "Cu": 0.3, "B": 0.001,
                          "Ag": 0.0001},
    },
}

_orig_check = p76.cmd_check


def cmd_check():
    import json
    out = ROOT / "results" / "p76_verdict.json"
    keep = out.read_bytes() if out.exists() else None
    _orig_check()
    v = json.loads(out.read_text())
    edge = [c for c in v["checks"] if not c["id"].endswith("__at_spec")]
    v["G1_status"] = ("NOT EXERCISED" if not edge else ("PASS" if v["G1_pass"] else "FAIL"))
    v["edge_points"] = len(edge)
    (ROOT / "results" / "p76a_verdict.json").write_text(json.dumps(v, indent=1, sort_keys=True))
    print("G1_status", v["G1_status"], "edge points", len(edge))
    if keep is not None:
        out.write_bytes(keep)  # the P76 verdict is append-only; restore it
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"build": p76.cmd_build, "run": p76.cmd_run, "budget": p76.cmd_budget, "verify": p76.cmd_verify,
          "check": cmd_check}
    if cmd not in fn:
        sys.exit(__doc__)
    fn[cmd]()
