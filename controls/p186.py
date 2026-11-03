#!/usr/bin/env python3
"""P186 (protocols/ACTINV-P186_PROTOCOL.md): P185's qualification of state-resolved (MF=10) coverage, rerun.

  python3 controls/p186.py build | observe | assemble   P185's harness steps; record results/p186_record.json
  python3 controls/p186.py check [--no-write]           P185's gates as P186 states them; results/p186_verdict.json
  python3 controls/p186.py --selftest

P185's sealed code (controls/p185_harness.py, controls/check_p185.py) is imported unchanged. P186 changes:
  - problem B names Nb-92m `Nb92m1` (P185 Amendment A);
  - G4's last condition holds at every step where the response's activity is nonzero;
  - sentence 2 covers each response that meets that condition.
Outputs go under target/p185-work, replacing P185 Amendment A's; the baseline worktree and its build are reused.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p185 as chk  # noqa: E402
import p185_harness as har  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P186_PROTOCOL.md"
B_RESPONSES = ["Co58", "Co57", "Ni57", "Co60", "Al26", "Na24", "W185", "W187", "Nb92m1"]
MUST_TAKE_RULE = {"A": "Fe53", "B": "Co58"}
RECORD = ROOT / "results" / "p186_record.json"
VERDICT = ROOT / "results" / "p186_verdict.json"
SEALED = ("protocols/ACTINV-P185_PROTOCOL.md", "controls/p185_harness.py", "controls/check_p185.py",
          "controls/p186.py")


def configure():
    har.PROBLEMS["B"] = (har.PROBLEMS["B"][0], list(B_RESPONSES))
    har.RECORD = RECORD
    har.SCHEMA = "actinv-p186-record-1"
    chk.RESPONSES["B"] = list(B_RESPONSES)
    chk.MUST_TAKE_RULE = {}  # P186's own condition below replaces P185's
    chk.RECORD, chk.VERDICT, chk.PROTOCOL = RECORD, VERDICT, PROTOCOL
    chk.SCHEMA = "actinv-p186-verdict-1"


def takes_rule(out, problem):
    """{response: (steps with nonzero activity, of which with a state-resolved parameter)} for the candidate."""
    counts = {}
    for _, _, block, _ in chk.responses(out):
        for name in chk.RESPONSES[problem]:
            entry = block.get(f"activity:{name}")
            if not isinstance(entry, dict) or entry.get("nominal", 0.0) == 0.0:
                continue
            active, taken = counts.get(name, (0, 0))
            counts[name] = (active + 1, taken + (chk.from_reaction_total(entry) > 0))
    return counts


def check():
    """P185's check under P186: G1 bound to P186's registration, G0 to the sealed files, G4's condition per step."""
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[:4] == ["git", "log", "--diff-filter=A", "--format=%H"]:
            cmd = cmd[:-1] + ["protocols/ACTINV-P186_PROTOCOL.md"]
        return real_run(cmd, *args, **kwargs)
    chk.subprocess.run = run
    try:
        verdict = chk.check()
    finally:
        chk.subprocess.run = real_run
    gates = verdict["gates"]
    sealed = (ROOT / "protocols" / "protocol_hash.txt").read_text()
    for path in SEALED:
        ok = chk.sha256(ROOT / path) in sealed
        gates["G0"][path] = ok
        gates["G0"]["pass"] = gates["G0"]["pass"] and ok
    if gates["G1"]["pass"]:
        runs = json.loads(RECORD.read_text())["observe"]["runs"]
        rule = {}
        for problem in chk.RESPONSES:
            out = json.loads(Path(runs[f"candidate_{problem}"]["output"]).read_text())
            rule[problem] = takes_rule(out, problem)
            del out
        problems = list(gates["G4"]["problems"])
        for problem, nuclide in MUST_TAKE_RULE.items():
            active, taken = rule[problem].get(nuclide, (0, 0))
            if not active or taken != active:
                problems.append(f"{problem}: {nuclide} takes the rule at {taken} of {active} steps with activity")
        affected = {p: sorted(n for n, (a, t) in r.items() if a and t == a) for p, r in rule.items()}
        gates["G4"].update({"pass": not problems, "problems": problems[:20], "affected_responses": affected,
                            "steps_with_activity": {p: {n: list(v) for n, v in r.items()} for p, r in rule.items()}})
    passed = all(gates[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4", "G5"))
    verdict["verdict"] = "P186-PASS (pending G6)" if passed else "P186-FAIL"
    verdict.pop("allowed_sentences", None)
    if passed:
        verdict["allowed_sentences"] = chk.sentences(gates["G5"]["observations"], gates["G4"]["affected_responses"])
    return verdict


def selftest():
    configure()
    assert har.problem("B")["uncertainty"]["responses"][-1] == "activity:Nb92m1"
    assert chk.RECORD == har.RECORD == RECORD and chk.PROTOCOL == PROTOCOL and chk.MUST_TAKE_RULE == {}

    def entry(nominal, lmf, value):
        p = {"LMF": lmf, "covariance_covered": True, "covariance_excluded": False}
        return {"nominal": nominal, "sensitivities": [{"parameter": p, "value": value}]}
    out = {"steps": [{"uncertainty": {"responses": {"activity:Fe53": entry(5.0, 10, 1.0)}}},
                     {"uncertainty": {"responses": {"activity:Fe53": entry(2.0, 10, 1.0)}}},
                     {"uncertainty": {"responses": {"activity:Fe53": entry(0.0, 10, 0.0)}}}]}
    assert takes_rule(out, "A")["Fe53"] == (2, 2), "a step without activity is not counted"
    out["steps"][1]["uncertainty"]["responses"]["activity:Fe53"] = entry(2.0, 0, 1.0)
    assert takes_rule(out, "A")["Fe53"] == (2, 1), "a step with activity and no state-resolved parameter counts"
    chk.selftest()
    print("p186 selftest: PASS")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        selftest()
        return 0
    configure()
    if argv and argv[0] in ("build", "observe", "assemble"):
        return har.main(argv[:1]) or 0
    if argv and argv[0] == "check":
        verdict = check()
        text = json.dumps(verdict, indent=1, sort_keys=True) + "\n"
        if "--no-write" in argv:
            same = VERDICT.is_file() and VERDICT.read_text() == text
            print(f"{verdict['verdict']}; committed verdict {'matches' if same else 'DIFFERS'}")
            return 0 if same else 1
        VERDICT.write_text(text)
        for gate, body in verdict["gates"].items():
            print(gate, body["pass"])
        print(verdict["verdict"])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
