#!/usr/bin/env python3
"""P196 Amendment A (protocols/ACTINV-P196_AMENDMENT_A.md): P196's harness and checker with packs that deepen round
by round until the result settles (`pack_run ... auto`), on P196's suite plus a held-out set, more browser problems,
and the rounds recorded.

  python3 controls/p196a.py run --actinv-bin BIN --pack-run-bin BIN --pack-build-bin BIN --web-dist DIR
                                                   the scored run; results/p196a_record.json
  python3 controls/p196a.py rehearse ...           two seen problems, one browser run, the refusals;
                                                   target/p196a-rehearsal/record.json
  python3 controls/p196a.py check [--no-write]     the gates; results/p196a_verdict.json
  python3 controls/p196a.py --selftest

Everything else is P196's sealed code (controls/p196.py, controls/p196_browser.mjs, controls/check_p196.py), imported
unchanged: the pack build, the suite runs, the browser runs, the refusal runs and the G1-G4 checks.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p196 as c  # noqa: E402
import p196 as har  # noqa: E402

AMENDMENT = "protocols/ACTINV-P196_AMENDMENT_A.md"
SCHEMA = "actinv-p196a-record-1"
RECORD = ROOT / "results" / "p196a_record.json"
VERDICT = ROOT / "results" / "p196a_verdict.json"
WORK = ROOT / "target" / "p196a-work"
REHEARSAL = ROOT / "target" / "p196a-rehearsal"
REACTIONS = "auto"

# Held out: never run, with a pack or the whole library, before this amendment's scored run.
HELD_OUT_SOURCES = (("fission", "1e15", "10y"), ("one-over-e", "1e15", "10y"), ("thermal", "3e14", "3y"))
BROWSER_ADDED = ["tungsten-thermal-1e15-10y", "beryllium-s65-thermal-1e14-1y", "water-thermal-1e14-1y"]
BROWSER_PROBLEMS = c.BROWSER_PROBLEMS + BROWSER_ADDED
REHEARSAL_PROBLEMS = ["example-fns_fe_5min", "water-thermal-1e14-1y"]

# G2's totals below this share of the whole library's largest value over the problem's steps are compared against
# it instead of themselves (P196 compared them relatively at any size).
TOTAL_FLOOR = 1e-10

RUN_TIMEOUT_S = 3600
BROWSER_TIMEOUT_S = 3600

P196_SUITE = c.suite


def held_out() -> list[dict]:
    return [{"name": f"{material}-{source}-{flux}-{time_}", "material": material, "source": source, "flux": flux,
             "irradiate": time_}
            for material in c.MATERIALS for source, flux, time_ in HELD_OUT_SOURCES]


def suite() -> list[dict]:
    return P196_SUITE() + held_out()


ROUNDS: dict[str, list] = {}
_call = har.call


def call(args: list, timeout: float, env: dict | None = None) -> dict:
    """P196's bounded call; for an auto pack run, also keeps the rounds it reports, by output path."""
    done = _call(args, timeout, env)
    if len(args) == 5 and str(args[3]) == REACTIONS:
        found = re.search(r"^rounds (\[.*\])$", done["stderr"], re.MULTILINE)
        ROUNDS[str(args[4])] = json.loads(found.group(1)) if found else None
    return done


PLAN_LINE = har.PLAN


class LastPlan:
    """P196 reads the first plan line; an auto run prints one per round, and the last is what was fetched."""

    def search(self, text: str):
        found = list(PLAN_LINE.finditer(text))
        return found[-1] if found else None


def agreement(full: dict, packed: dict) -> dict:
    """P196's agreement, with each total compared against TOTAL_FLOOR of its peak where it is smaller; counts the
    comparisons that fell under the floor and their largest change relative to themselves."""
    keys = ("activity", "heat", "photons")
    worst = {"activity": 0.0, "heat": 0.0, "photons": 0.0, "nuclide": 0.0}
    floored = {"comparisons": 0, "largest_relative": 0.0}
    at = None
    steps_full, steps_pack = full.get("steps", []), packed.get("steps", [])
    peak = [max((abs(c.step_totals(s)[i]) for s in steps_full), default=0.0) for i in range(3)]
    for a, b in zip(steps_full, steps_pack):
        for i, (key, x, y) in enumerate(zip(keys, c.step_totals(a), c.step_totals(b))):
            floor = TOTAL_FLOOR * peak[i]
            if max(abs(x), abs(y)) < floor:
                floored["comparisons"] += 1
                floored["largest_relative"] = max(floored["largest_relative"], c.relative(x, y))
                change = abs(x - y) / floor
            else:
                change = c.relative(x, y)
            worst[key] = max(worst[key], change)
        total = c.step_totals(a)[0]
        for name, value in a.get("activity_Bq_per_g", {}).items():
            if total > 0 and value >= c.NUCLIDE_SHARE * total:
                change = c.relative(value, b.get("activity_Bq_per_g", {}).get(name, 0.0))
                if change > worst["nuclide"]:
                    worst["nuclide"], at = change, {"step": a.get("step"), "nuclide": name}
    ok = (len(steps_full) == len(steps_pack) and len(steps_full) > 0
          and max(worst[k] for k in keys) <= c.TOTAL_TOLERANCE and worst["nuclide"] <= c.NUCLIDE_TOLERANCE)
    return {"pass": ok, "steps": [len(steps_full), len(steps_pack)], "largest": worst, "at": at, "below_floor": floored}


P196_AGREEMENT = c.agreement


def amend():
    c.agreement = agreement
    c.suite = suite
    c.REACTIONS = REACTIONS
    c.BROWSER_PROBLEMS = BROWSER_PROBLEMS
    c.HASHED = list(c.HASHED) + [AMENDMENT, "controls/p196a.py"]
    c.SCHEMA, c.RECORD, c.VERDICT = SCHEMA, RECORD, VERDICT
    har.call, har.RUN_TIMEOUT_S, har.BROWSER_TIMEOUT_S = call, RUN_TIMEOUT_S, BROWSER_TIMEOUT_S
    har.PLAN = LastPlan()


def execute(args: argparse.Namespace, rehearsal: bool) -> int:
    work = REHEARSAL if rehearsal else WORK
    record_path = work / "record.json" if rehearsal else RECORD
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    binaries = {name: Path(getattr(args, name)).resolve() for name in ("actinv_bin", "pack_run_bin", "pack_build_bin")}
    problems, browser = suite(), BROWSER_PROBLEMS
    if rehearsal:
        problems = [p for p in problems if p["name"] in REHEARSAL_PROBLEMS]
        browser = REHEARSAL_PROBLEMS[1:]
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    pack = har.build_pack(binaries["pack_build_bin"], work)
    pack_dir = ROOT / pack["dir"]
    rows = har.g2(binaries["actinv_bin"], binaries["pack_run_bin"], pack_dir, work, problems)
    for row in rows:
        row["rounds"] = ROUNDS.get(str(work / "problems" / row["name"] / "pack.json"))
    browser_rows = har.g3(Path(args.web_dist), pack_dir, work, browser, args.playwright)
    refusals = har.g4(binaries["pack_run_bin"], pack_dir, work)
    record = {
        "schema": SCHEMA,
        "rehearsal": rehearsal,
        "commit": git("rev-parse", "HEAD"),
        "tracked_tree_clean": git("status", "--porcelain", "--untracked-files=no") == "",
        "binaries": {name: {"path": str(path), "sha256": har.sha256(path)} for name, path in binaries.items()},
        "pack": pack,
        "reactions": REACTIONS,
        "problems": rows,
        "browser": browser_rows,
        "refusals": refusals,
        "wall_s": time.time() - started,
    }
    har.write_json(record_path, record)
    failed = sum(1 for r in rows if r["full_exit"] != 0 or r["pack_exit"] != 0)
    print(f"{len(rows)} problems ({failed} with a failed run), {len(browser_rows)} browser runs, "
          f"{len(refusals)} refusal cases; record {record_path.relative_to(ROOT)}")
    return 0


def g1(record: dict) -> dict:
    """P196's G1 with the reaction count `auto`, and every pack run's rounds recorded."""
    failures = [] if record.get("reactions") == REACTIONS else ["reactions"]
    failures += [f"{row['name']} rounds" for row in record.get("problems", [])
                 if row.get("pack_exit") == 0 and not row.get("rounds")]
    c.REACTIONS = "0"
    try:
        base = c.g1({**record, "reactions": 0})
    finally:
        c.REACTIONS = REACTIONS
    failures += base["failures"]
    return {"pass": not failures, "failures": failures}


def g5(record: dict) -> dict:
    report = c.g5(record)
    depths = [row["rounds"][-1]["reactions"] for row in record.get("problems", []) if row.get("rounds")]
    counts = [len(row["rounds"]) for row in record.get("problems", []) if row.get("rounds")]
    report["settled_reactions"] = {"median": statistics.median(depths) if depths else None,
                                   "max": max(depths, default=None),
                                   "by_depth": {str(d): depths.count(d) for d in sorted(set(depths))}}
    report["rounds"] = {"median": statistics.median(counts) if counts else None, "max": max(counts, default=None)}
    return report


def floored(verdict: dict) -> dict:
    """G2's comparisons under the floor, over all problems, and how many problems pass P196's G2 unamended."""
    rows = [r for r in verdict["G2"]["problems"] if "below_floor" in r]
    return {"comparisons": sum(r["below_floor"]["comparisons"] for r in rows),
            "problems": sum(1 for r in rows if r["below_floor"]["comparisons"]),
            "largest_relative": max((r["below_floor"]["largest_relative"] for r in rows), default=0.0)}


def sentences(record: dict, verdict: dict) -> list[str]:
    largest = verdict["G2"]["largest"]
    totals = max(largest["activity"], largest["heat"], largest["photons"])
    size = verdict["G5"]["pack_megabytes"]
    return [
        "In the browser, ACTINV solves a problem with the standard TENDL-2025 library, fetching only the reactions "
        "its material can reach and the decay data they lead to, and following more reactions, round by round, "
        "until the result no longer changes.",
        f"On {len(verdict['G2']['problems'])} problems ({len(c.MATERIALS)} materials under four standard spectra at "
        f"1e14 n/cm²/s for a year and at 1e15 for ten years, under a thermal spectrum at 3e14 for three years, and "
        f"{len(c.EXAMPLES)} of the examples), its total activity, decay heat and photon emission at every step were "
        f"within {totals:.1g} of the whole library's (of 1e-10 of its largest value, where it had fallen below that), and "
        f"every nuclide carrying 0.1% of the activity within {largest['nuclide']:.1g}.",
        f"Run in Chromium, the browser's result was identical to the native one on all {len(verdict['G3']['problems'])} "
        f"problems tried; a problem fetched a median of {size['median']:.0f} MB of data (at most {size['max']:.0f} MB).",
    ]


def check(write: bool) -> int:
    record = json.loads(RECORD.read_text())
    verdict = {"G0": c.g0(), "G1": g1(record), "G2": c.g2(record), "G3": c.g3(record), "G4": c.g4(record),
               "G5": g5(record)}
    verdict["G5"]["below_floor"] = floored(verdict)
    unamended = [row["name"] for row in record.get("problems", [])
                 if (full := c.load(row.get("full"), row.get("full_sha256"))) is not None
                 and (packed := c.load(row.get("pack"), row.get("pack_sha256"))) is not None
                 and not P196_AGREEMENT(full, packed)["pass"]]
    verdict["G5"]["failing_p196_g2_unamended"] = unamended
    passed = all(verdict[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4"))
    verdict["verdict"] = "P196A-PASS" if passed else "P196A-FAIL"
    verdict["G6"] = "CI on the evidence commit (external)"
    if passed:
        verdict["sentences"] = sentences(record, verdict)
    if write:
        VERDICT.write_text(json.dumps(verdict, indent=1) + "\n")
    print(verdict["verdict"], {g: verdict[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4")})
    return 0 if passed else 1


def selftest() -> int:
    names = [p["name"] for p in suite()]
    seen = [p["name"] for p in P196_SUITE()]
    assert names[:len(seen)] == seen and len(names) == len(set(names)) == len(seen) + 3 * len(c.MATERIALS)
    assert not set(seen) & {p["name"] for p in held_out()}
    assert set(BROWSER_PROBLEMS) <= set(seen) and len(BROWSER_PROBLEMS) == len(set(BROWSER_PROBLEMS))
    assert set(REHEARSAL_PROBLEMS) <= set(seen)
    plan = "plan 0.01s: 1 targets, 2 decay states, 3 files, 4 bytes\nround 2: run 1.0s\n" \
           "plan 0.02s: 5 targets, 6 decay states, 7 files, 8 bytes\n"
    assert LastPlan().search(plan).groups() == ("5", "6", "7", "8")
    rounds = [{"reactions": 2, "targets": 1, "nuclides": 2, "change": None}]
    record = {"reactions": "auto", "problems": [{"name": "x", "pack_exit": 0, "rounds": None}]}
    assert "x rounds" in g1(record)["failures"] and "reactions" not in g1(record)["failures"]
    assert c.REACTIONS == REACTIONS
    assert "reactions" in g1({**record, "reactions": 2})["failures"]
    report = g5({"problems": [{"plan": {"bytes": 1e6, "files": 3}, "pack_wall_s": 1.0, "rounds": rounds}]})
    assert report["settled_reactions"]["max"] == 2 and report["rounds"]["max"] == 1
    step = lambda act, ph: {"step": 1, "activity_Bq_per_g": {"A": act}, "heat_W_per_g": {"total": act},
                            "photon_source": {"total_photons_s_g": ph}}
    full = {"steps": [step(1e6, 1e6), step(1.0, 1e-12)]}
    trace = {"steps": [step(1e6, 1e6), step(1.0, 5e-13)]}
    assert not P196_AGREEMENT(full, trace)["pass"], "P196 compares the trace relatively"
    result = agreement(full, trace)
    assert result["pass"] and result["below_floor"] == {"comparisons": 1, "largest_relative": 0.5}, result
    near = {"steps": [step(1e6, 1e6), step(1.0002, 1e-12)]}
    assert not agreement(full, near)["pass"], "a total above the floor is still compared relatively"
    under = {"steps": [step(1e6, 1e6), step(1.0, 1e-12 + 2e-8)]}
    assert not agreement(full, under)["pass"], "under the floor the difference must stay within 1e-4 of it"
    assert c.agreement is agreement
    print("p196a selftest: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="command")
    for name in ("run", "rehearse"):
        p = sub.add_parser(name)
        p.add_argument("--actinv-bin", required=True)
        p.add_argument("--pack-run-bin", required=True)
        p.add_argument("--pack-build-bin", required=True)
        p.add_argument("--web-dist", required=True)
        p.add_argument("--playwright", default=str(ROOT / "web/node_modules/playwright/index.mjs"))
    p = sub.add_parser("check")
    p.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    amend()
    if args.selftest:
        return selftest()
    if args.command in ("run", "rehearse"):
        return execute(args, rehearsal=args.command == "rehearse")
    if args.command == "check":
        return check(write=not args.no_write)
    parser.error("a command is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
