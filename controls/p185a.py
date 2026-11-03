#!/usr/bin/env python3
"""P185 Amendment A (protocols/ACTINV-P185_AMENDMENT_A.md): P185's harness and checker with Nb-92m named Nb92m1.

  python3 controls/p185a.py build | observe | assemble   P185's harness steps; record results/p185a_record.json
  python3 controls/p185a.py check [--no-write]           P185's gates; verdict results/p185a_verdict.json
  python3 controls/p185a.py --selftest

Everything else is P185's sealed code (controls/p185_harness.py, controls/check_p185.py), imported unchanged. The
fresh run's outputs replace P185's under target/p185-work; the baseline worktree and its build are reused.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p185 as chk  # noqa: E402
import p185_harness as har  # noqa: E402

AMENDMENT = ROOT / "protocols" / "ACTINV-P185_AMENDMENT_A.md"
B_RESPONSES = ["Co58", "Co57", "Ni57", "Co60", "Al26", "Na24", "W185", "W187", "Nb92m1"]
RECORD = ROOT / "results" / "p185a_record.json"
VERDICT = ROOT / "results" / "p185a_verdict.json"


def amend():
    har.PROBLEMS["B"] = (har.PROBLEMS["B"][0], list(B_RESPONSES))
    har.RECORD = RECORD
    har.SCHEMA = "actinv-p185a-record-1"
    chk.RESPONSES["B"] = list(B_RESPONSES)
    chk.RECORD, chk.VERDICT, chk.PROTOCOL = RECORD, VERDICT, AMENDMENT
    chk.SCHEMA = "actinv-p185a-verdict-1"


def check():
    """P185's check, with G1's registration commit the amendment's and P185's protocol also sealed."""
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[:4] == ["git", "log", "--diff-filter=A", "--format=%H"]:
            cmd = cmd[:-1] + ["protocols/ACTINV-P185_AMENDMENT_A.md"]
        return real_run(cmd, *args, **kwargs)
    chk.subprocess.run = run
    try:
        verdict = chk.check()
    finally:
        chk.subprocess.run = real_run
    sealed = (ROOT / "protocols" / "protocol_hash.txt").read_text()
    for path in ("protocols/ACTINV-P185_PROTOCOL.md", "controls/p185_harness.py", "controls/check_p185.py",
                 "controls/p185a.py"):
        ok = chk.sha256(ROOT / path) in sealed
        verdict["gates"]["G0"][path] = ok
        verdict["gates"]["G0"]["pass"] = verdict["gates"]["G0"]["pass"] and ok
    passed = all(verdict["gates"][g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4", "G5"))
    verdict["verdict"] = "P185A-PASS (pending G6)" if passed else "P185A-FAIL"
    if not passed:
        verdict.pop("allowed_sentences", None)
    return verdict


def selftest():
    amend()
    assert har.PROBLEMS["B"][1][-1] == "Nb92m1" and chk.RESPONSES["B"][-1] == "Nb92m1"
    assert har.PROBLEMS["A"][1] == chk.RESPONSES["A"]
    assert har.problem("B")["uncertainty"]["responses"][-1] == "activity:Nb92m1"
    assert chk.RECORD == har.RECORD == RECORD and chk.PROTOCOL == AMENDMENT
    chk.selftest()
    print("p185a selftest: PASS")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        selftest()
        return 0
    amend()
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
