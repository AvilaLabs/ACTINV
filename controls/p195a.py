#!/usr/bin/env python3
"""P195 Amendment A (protocols/ACTINV-P195_AMENDMENT_A.md): P195's harness and checker with the In and Nb foils'
isomers named as results name them, and every declared G3 measurement required.

  python3 controls/p195a.py g1 --actinv-bin BIN    P195's cargo gates; results/p195a_g1.json
  python3 controls/p195a.py run --actinv-bin BIN   P195's scored run; results/p195a_record.json
  python3 controls/p195a.py check [--no-write]     P195's gates plus the added G3 condition; results/p195a_verdict.json
  python3 controls/p195a.py --selftest

Everything else is P195's sealed code (controls/p195.py, controls/check_p195.py), imported unchanged.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p195 as c  # noqa: E402
import p195 as har  # noqa: E402

AMENDMENT = "protocols/ACTINV-P195_AMENDMENT_A.md"
RENAMED = {"In115m": "In115m1", "In116m": "In116m1", "Nb92m": "Nb92m1"}
DECLARED = 15
RECORD = ROOT / "results" / "p195a_record.json"
G1_RECORD = ROOT / "results" / "p195a_g1.json"
VERDICT = ROOT / "results" / "p195a_verdict.json"
WORK = ROOT / "target" / "p195a-work"


def amend():
    c.G3_FOILS = [(element, [RENAMED.get(n, n) for n in nuclides]) for element, nuclides in c.G3_FOILS]
    c.HASHED = list(c.HASHED) + [AMENDMENT, "controls/p195a.py"]
    c.SCHEMA = "actinv-p195a-record-1"
    c.RECORD, c.G1_RECORD, c.VERDICT = RECORD, G1_RECORD, VERDICT
    har.RECORD, har.WORK, har.CACHE = RECORD, WORK, WORK / "cache"


def added_g3_condition(record: dict) -> list[str]:
    meta = record["g3"]
    problems = []
    if meta.get("dropped"):
        problems.append(f"declared measurements dropped: {meta['dropped']}")
    if meta.get("measurements") != DECLARED:
        problems.append(f"{meta.get('measurements')} G3 measurements, not {DECLARED}")
    return problems


def check(write: bool) -> int:
    record = json.loads(RECORD.read_text())
    verdict = c.evaluate(record)
    verdict["gates"]["G3"] = verdict["gates"]["G3"] + added_g3_condition(record)
    passed = all(not verdict["gates"][k] for k in ("G0", "G1", "G2", "G3", "G5", "G6"))
    verdict["verdict"] = "P195A-PASS" if passed else "P195A-FAIL"
    if not passed:
        verdict["sentences"] = None
    if write:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    for gate in ("G0", "G1", "G2", "G3", "G5", "G6"):
        problems = verdict["gates"][gate]
        print(f"{gate}: {'PASS' if not problems else 'FAIL'} ({len(problems)} problems)")
        for problem in problems[:10]:
            print(f"  {problem}")
    print(verdict["verdict"])
    return 0 if passed else 1


def selftest() -> int:
    amend()
    names = [n for _, nuclides in c.G3_FOILS for n in nuclides]
    assert len(names) == DECLARED and {"In115m1", "In116m1", "Nb92m1"} <= set(names)
    assert not {"In115m", "In116m", "Nb92m"} & set(names)
    assert AMENDMENT in c.HASHED and "controls/p195a.py" in c.HASHED and len(c.HASHED) == 5
    assert har.RECORD == c.RECORD == RECORD and har.WORK == WORK and c.G1_RECORD == G1_RECORD
    assert added_g3_condition({"g3": {"dropped": [], "measurements": 15}}) == []
    assert added_g3_condition({"g3": {"dropped": ["In:In115m1"], "measurements": 14}})
    if c.selftest() != 0:
        return 1
    print("p195a selftest: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="command")
    for name in ("g1", "run"):
        sub.add_parser(name).add_argument("--actinv-bin", required=True, help="the amendment candidate's binary")
    sub.add_parser("check").add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    amend()
    if args.command == "g1":
        return har.g1(args)
    if args.command == "run":
        return har.run(args)
    if args.command == "check":
        return check(not args.no_write)
    parser.error("give a command or --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
