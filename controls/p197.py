#!/usr/bin/env python3
"""P197: `actinv maintenance` against closed-form dose curves, against ACTINV-computed component dose on a dense
cooling grid, and on its refusals. Harness and checker in one.

  run    --actinv-bin BIN [--photon-response PATH]   write the cases under target/p197-work, run them, write
         results/p197_record.json
  rehearse --actinv-bin BIN [--photon-response PATH]   the one-nuclide curve on the sparse grid, the development
         components and the refusals, under target/p197-rehearsal; prints exit statuses only
  check  gate the record; write results/p197_verdict.json
  --selftest  check the gate arithmetic on planted values

  G0  The SHA-256 of protocols/ACTINV-P197_PROTOCOL.md and of this script are in protocols/protocol_hash.txt.
  G1  Provenance: the record is from a clean tracked tree, with the registered cases, every output present.
  G2  Closed form (three curves of one, two and three decaying nuclides; grids of 9, 41 and 201 times):
      a. on the 201-time grids every compared answer is within 1e-3 (relative) of exact;
      b. on every grid every job dose and every earliest start is at least exact × (1 - 1e-9);
      c. on every grid of the one-nuclide curve every answer is within 1e-9 of exact;
      d. on every grid with an answer more than 0.05 from exact the interpolation check is `coarse`, and on no
         201-time grid is it `coarse`.
  G3  ACTINV-computed dose (two development components, five held out; 15 and 73 cooling times):
      a. every point-source dose a plan reads is present;
      b. for every component, either every compared answer from the 15-time grid is within 0.05 of the 73-time
         grid's, or the 15-time plan's interpolation check is `coarse`;
      c. the 73-time grid's dose rates written as an actinv-dose-map-1 table give the same result as the runs.
  G4  Refusals: every case exits 2, writes no result, and names its cause on stderr.
  G5  CI green on the evidence commit (external).

Verdict: P197-PASS iff G0-G4 pass (G5 external).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_REL = "protocols/ACTINV-P197_PROTOCOL.md"
HASHED = [PROTOCOL_REL, "controls/p197.py"]
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
WORK = ROOT / "target" / "p197-work"
RECORD = ROOT / "results" / "p197_record.json"
VERDICT = ROOT / "results" / "p197_verdict.json"
SCHEMA = "actinv-p197-record-1"
DEFAULT_RESPONSE = Path("~/nuclear-data/photon-response/nist-xcom-all.json").expanduser()
TIMEOUT_S = 900

DAY = 86400.0
HOUR = 3600.0
HORIZON_D = 365.0
DENSE_TOL = 1e-3
EXACT_TOL = 1e-9
CONSERVATIVE = 1e-9
FLAG_TOL = 0.05
G3_TOL = 0.05

# G2: amplitude (Sv/h at shutdown) and half-life (d) of each nuclide.
CURVES = {
    "one": [(1e-2, 5.0)],
    "two": [(3e-2, 0.6), (2e-4, 70.0)],
    "three": [(1e-2, 12.7 / 24.0), (1e-3, 312.2), (5e-3, 1925.28)],
}
GRIDS = {"sparse": 8, "medium": 40, "dense": 200}
G2_JOBS = [("first", 8.0, 2), ("second", 16.0, 3)]
G2_WAITS_D = [0.5, 3.0, 20.0, 100.0, 300.0]
G2_ACCESS_AT_D = 20.0
G2_LIMIT_AT_D = 30.0
G2_COST = {"per_person_Sv": 1e7, "per_outage_day": 1e3}

# G3: 2 y at 3e8 n/cm2/s of D-T neutrons, 1 m point source.
DEVELOPMENT = {"pipe": "examples/maintenance/pipe.json", "bracket": "examples/maintenance/bracket.json"}
HELD_OUT = {"eurofer97": 10000.0, "inconel718": 10000.0, "tungsten": 10000.0, "al6061": 10000.0,
            "concrete-ordinary": 50000.0}
EXAMPLE_COOLING_S = [3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 3 * 86400, 4 * 86400, 7 * 86400, 14 * 86400,
                     30 * 86400, 60 * 86400, 90 * 86400, 180 * 86400, 365 * 86400]
DENSE_EXTRA = 60
G3_JOB = ("work", 6.0, 2)
G3_WAITS_D = [1.0, 7.0, 30.0, 90.0, 180.0]
G3_ACCESS_FRACTION = 0.9
G3_ACCESS_AT_D = 30.0
G3_COST = {"per_person_Sv": 1e6, "per_outage_day": 2e3}

REFUSALS = ["unknown-location", "wait-beyond", "job-too-long", "unit-msv", "times-not-increasing", "zero-crew",
            "no-point-source", "twin-without-conversion", "wrong-schema", "negative-dose"]
REFUSAL_CAUSE = {"unknown-location": "no dose rate", "wait-beyond": "outside", "job-too-long": "longer",
                 "unit-msv": "Sv/h", "times-not-increasing": "increase", "zero-crew": "crew",
                 "no-point-source": "photon.point_source", "twin-without-conversion": "dose_rates",
                 "wrong-schema": "schema", "negative-dose": "negative"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1) + "\n")
    return path


# ---------------------------------------------------------------- exact answers for G2

def rate(curve, t_s: float) -> float:
    return sum(a * math.exp(-math.log(2) * t_s / (h * DAY)) for a, h in curve)


def dose(curve, a_s: float, b_s: float) -> float:
    total = 0.0
    for amplitude, half_life in curve:
        k = math.log(2) / (half_life * DAY)
        total += amplitude / k * (math.exp(-k * a_s) - math.exp(-k * b_s))
    return total / HOUR


def bisect(predicate, lo: float, hi: float) -> float:
    """The smallest x in [lo, hi] with predicate(x), for a predicate false below and true above it."""
    if predicate(lo):
        return lo
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if predicate(mid):
            hi = mid
        else:
            lo = mid
    return hi


def exact_plan(curve, access: float, individual: float | None) -> dict:
    """Exact answers for G2's plan (jobs in order at one place; every curve falls steadily)."""
    offsets, start = [], 0.0
    for _, hours, crew in G2_JOBS:
        offsets.append((start, hours * HOUR, crew))
        start += hours * HOUR
    work = start
    hi = HORIZON_D * DAY - work

    def at(wait: float) -> dict:
        jobs = [dose(curve, wait + o, wait + o + d) for o, d, _ in offsets]
        collective = sum(j * c for j, (_, _, c) in zip(jobs, offsets))
        feasible = rate(curve, wait) <= access and (individual is None or max(jobs) <= individual)
        return {"jobs": jobs, "collective": collective, "feasible": feasible,
                "cost": G2_COST["per_person_Sv"] * collective + G2_COST["per_outage_day"] * (wait + work) / DAY}

    earliest = bisect(lambda w: at(w)["feasible"], 0.0, hi)
    alone = [bisect(lambda s: rate(curve, s) <= access, 0.0, HORIZON_D * DAY - d) for _, d, _ in offsets]
    # Least cost over the feasible waits: a fine scan, then golden section around the best point.
    n = 20000
    grid = [earliest + (hi - earliest) * i / n for i in range(n + 1)]
    best_i = min(range(n + 1), key=lambda i: at(grid[i])["cost"])
    a, b = grid[max(best_i - 1, 0)], grid[min(best_i + 1, n)]
    phi = (math.sqrt(5) - 1) / 2
    for _ in range(200):
        c, d = b - phi * (b - a), a + phi * (b - a)
        if at(c)["cost"] <= at(d)["cost"]:
            b = d
        else:
            a = c
    best = min([grid[best_i], 0.5 * (a + b), earliest], key=lambda w: at(w)["cost"])
    return {"earliest_wait_d": earliest / DAY, "alone_d": [s / DAY for s in alone],
            "waits": {str(w): at(w * DAY) for w in G2_WAITS_D},
            "best_wait_d": best / DAY, "best_cost": at(best)["cost"]}


def grid_times(points: int) -> list[float]:
    """Shutdown and `points` log-spaced times from 1 h to the horizon, in seconds."""
    lo, hi = math.log(HOUR), math.log(HORIZON_D * DAY)
    return [0.0] + [math.exp(lo + (hi - lo) * i / (points - 1)) for i in range(points)]


def g2_plan(curve_name: str, with_limit: bool) -> dict:
    curve = CURVES[curve_name]
    individual = None
    if with_limit:
        start = G2_LIMIT_AT_D * DAY + G2_JOBS[0][1] * HOUR
        individual = dose(curve, start, start + G2_JOBS[1][1] * HOUR)
    plan = {
        "access_limit_Sv_h": rate(curve, G2_ACCESS_AT_D * DAY),
        "jobs": [{"name": n, "location": "port", "duration_h": h, "crew": c} for n, h, c in G2_JOBS],
        "waits_d": G2_WAITS_D,
        "cost": G2_COST,
    }
    if individual is not None:
        plan["individual_limit_Sv"] = individual
    return plan


# ---------------------------------------------------------------- the run

def call(args: list, out: Path | None = None) -> dict:
    """Run one command with a bounded wait (subprocess.run kills the child on timeout)."""
    if out is not None and out.is_file():
        out.unlink()
    try:
        done = subprocess.run([str(a) for a in args], cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT_S)
        row = {"exit": done.returncode, "stderr": done.stderr.strip()[-2000:]}
    except subprocess.TimeoutExpired:
        row = {"exit": None, "stderr": f"timed out after {TIMEOUT_S} s"}
    if out is not None:
        row["output"] = str(out.relative_to(ROOT))
        row["output_sha256"] = sha256(out) if out.is_file() else None
    return row


def answers(result: dict) -> dict:
    """The answers G2 and G3 compare, from an actinv-maintenance-result-1."""
    best = result.get("best") or {}
    return {
        "earliest_wait_d": result.get("earliest_wait_d"),
        "alone_d": [a.get("earliest_start_d") for a in result.get("alone", [])],
        "waits": {str(w["wait_d"]): {"jobs": [j["individual_Sv"] for j in w["jobs"]],
                                      "collective": w["collective_Sv"]} for w in result.get("waits", [])},
        "best_wait_d": best.get("wait_d"),
        "best_cost": best.get("cost"),
        "interpolation_check": result.get("interpolation_check"),
    }


def run_g2(binary: Path, curves=None, grids=None) -> list:
    rows = []
    for curve_name, curve in CURVES.items():
        if curves is not None and curve_name not in curves:
            continue
        for grid_name, points in GRIDS.items():
            if grids is not None and grid_name not in grids:
                continue
            times = grid_times(points)
            table = write(WORK / "g2" / f"{curve_name}-{grid_name}-map.json", {
                "schema": "actinv-dose-map-1", "unit": "Sv/h", "source": f"closed form {curve_name}",
                "locations": [{"name": "port", "times_s": times, "dose_rate": [rate(curve, t) for t in times]}]})
            for with_limit in (False, True):
                name = f"{curve_name}-{grid_name}-{'limited' if with_limit else 'access'}"
                plan = {"schema": "actinv-maintenance-1", "dose_rates": {"table": table.name},
                        **g2_plan(curve_name, with_limit)}
                plan_path = write(WORK / "g2" / f"{name}.json", plan)
                out = WORK / "g2" / f"{name}-result.json"
                row = {"name": name, "curve": curve_name, "grid": grid_name, "times": len(times),
                       "limited": with_limit, **call([binary, "maintenance", plan_path, out], out)}
                if out.is_file():
                    row["answers"] = answers(json.loads(out.read_text()))
                individual = plan.get("individual_limit_Sv")
                row["exact"] = exact_plan(curve, plan["access_limit_Sv_h"], individual)
                rows.append(row)
    return rows


def component_spec(binary: Path, name: str, response: Path, response_sha: str, cooling: list) -> dict:
    """A component's problem: a development example as committed, or a held-out library material."""
    if name in DEVELOPMENT:
        spec = json.loads((ROOT / DEVELOPMENT[name]).read_text())
    else:
        new = WORK / "g3" / f"{name}-new.json"
        if not new.is_file():
            done = call([binary, "new", new, "--material", name, "--source", "fusion-14mev", "--flux", "3e8",
                         "--irradiate", "2y"])
            if done["exit"] != 0:
                raise SystemExit(f"actinv new {name} failed: {done['stderr']}")
        spec = json.loads(new.read_text())
        spec["title"] = f"P197 held-out component: {name} after two years at 3e8 n/cm2/s of D-T neutrons"
        spec["material"]["mass_g"] = HELD_OUT[name]
    if name in DEVELOPMENT and spec["photon"]["response"]["sha256"] != response_sha:
        raise SystemExit(f"{DEVELOPMENT[name]} pins another photon response than {response}")
    steps, previous = [spec["schedule"][0]], 0
    for t in cooling:
        steps.append({"dt": f"{t - previous} s", "flux": 0.0, "spectrum": None, "feed": None, "removal": None})
        previous = t
    spec["schedule"] = steps
    spec["photon"] = {"response": {"path": str(response), "sha256": response_sha},
                      "point_source": {"distance_m": 1.0, "air_attenuation": False},
                      "missing_spectra": "estimate"}
    return spec


def dense_cooling() -> list:
    lo, hi = math.log(HOUR), math.log(HORIZON_D * DAY)
    extra = [round(math.exp(lo + (hi - lo) * i / (DENSE_EXTRA - 1))) for i in range(DENSE_EXTRA)]
    return sorted(set(EXAMPLE_COOLING_S) | set(extra))


def run_g3(binary: Path, response: Path, names=None) -> list:
    response_sha = sha256(response)
    rows = []
    grids = {"example": EXAMPLE_COOLING_S, "dense": dense_cooling()}
    for name in names or [*DEVELOPMENT, *HELD_OUT]:
        row = {"name": name, "held_out": name in HELD_OUT, "runs": {}, "plans": {}}
        doses = {}
        for grid_name, cooling in grids.items():
            spec_path = write(WORK / "g3" / f"{name}-{grid_name}.json",
                              component_spec(binary, name, response, response_sha, cooling))
            out = WORK / "g3" / f"{name}-{grid_name}-result.json"
            row["runs"][grid_name] = {"times": len(cooling) + 1, **call([binary, "run", spec_path, out], out)}
            if out.is_file():
                steps = json.loads(out.read_text())["steps"]
                shutdown = max(s["t_s"] for s in steps if (s.get("flux") or 0) > 0)
                doses[grid_name] = [(s["t_s"] - shutdown,
                                     ((s.get("photon_source") or {}).get("point_source_dose") or {})
                                     .get("gamma_air_dose_Sv_h")) for s in steps if s["t_s"] >= shutdown]
        row["doses_present"] = bool(doses) and all(d is not None for grid in doses.values() for _, d in grid)
        if len(doses) != 2 or not row["doses_present"]:
            rows.append(row)
            continue
        at_30 = dict(doses["dense"])[G3_ACCESS_AT_D * DAY]
        plan = {"access_limit_Sv_h": G3_ACCESS_FRACTION * at_30,
                "jobs": [{"name": G3_JOB[0], "location": name, "duration_h": G3_JOB[1], "crew": G3_JOB[2]}],
                "waits_d": G3_WAITS_D, "cost": G3_COST}
        for grid_name in grids:
            plan_path = write(WORK / "g3" / f"{name}-{grid_name}-plan.json", {
                "schema": "actinv-maintenance-1",
                "dose_rates": {"runs": [{"result": f"{name}-{grid_name}-result.json", "location": name}]}, **plan})
            out = WORK / "g3" / f"{name}-{grid_name}-plan-result.json"
            row["plans"][grid_name] = call([binary, "maintenance", plan_path, out], out)
            if out.is_file():
                row["plans"][grid_name]["answers"] = answers(json.loads(out.read_text()))
        table = write(WORK / "g3" / f"{name}-dense-map.json", {
            "schema": "actinv-dose-map-1", "unit": "Sv/h", "source": f"{name}-dense-result.json",
            "locations": [{"name": name, "times_s": [t for t, _ in doses["dense"]],
                           "dose_rate": [d for _, d in doses["dense"]]}]})
        plan_path = write(WORK / "g3" / f"{name}-table-plan.json", {
            "schema": "actinv-maintenance-1", "dose_rates": {"table": table.name}, **plan})
        out = WORK / "g3" / f"{name}-table-plan-result.json"
        row["plans"]["table"] = call([binary, "maintenance", plan_path, out], out)
        if out.is_file():
            row["plans"]["table"]["answers"] = answers(json.loads(out.read_text()))
        rows.append(row)
    return rows


def run_g4(binary: Path, response: Path) -> list:
    base = WORK / "g4"
    times = [0.0, 3600.0, 86400.0, 10 * 86400.0]
    good_map = {"schema": "actinv-dose-map-1", "unit": "Sv/h",
                "locations": [{"name": "port", "times_s": times, "dose_rate": [1e-2, 8e-3, 5e-3, 1e-3]}]}
    plan = {"schema": "actinv-maintenance-1", "dose_rates": {"table": "map.json"}, "access_limit_Sv_h": 4e-3,
            "jobs": [{"name": "job", "location": "port", "duration_h": 4, "crew": 2}], "waits_d": [2.0]}
    cases = {}
    cases["unknown-location"] = ({**plan, "jobs": [{**plan["jobs"][0], "location": "nowhere"}]}, good_map)
    cases["wait-beyond"] = ({**plan, "waits_d": [9.99]}, good_map)
    cases["job-too-long"] = ({**plan, "jobs": [{**plan["jobs"][0], "duration_h": 241}]}, good_map)
    cases["unit-msv"] = (plan, {**good_map, "unit": "mSv/h"})
    cases["times-not-increasing"] = (plan, {**good_map, "locations": [{"name": "port", "times_s": [0.0, 3600.0,
                                     3600.0, 864000.0], "dose_rate": [1e-2, 8e-3, 5e-3, 1e-3]}]})
    cases["zero-crew"] = ({**plan, "jobs": [{**plan["jobs"][0], "crew": 0}]}, good_map)
    cases["twin-without-conversion"] = ({**plan, "dose_rates": {"twin": "twin.json", "shutdown_s": 0.0}}, good_map)
    cases["wrong-schema"] = ({**plan, "schema": "actinv-maintenance-0"}, good_map)
    cases["negative-dose"] = (plan, {**good_map, "locations": [{"name": "port", "times_s": times,
                              "dose_rate": [1e-2, 8e-3, -5e-3, 1e-3]}]})
    rows = []
    for name, (case_plan, case_map) in cases.items():
        write(base / name / "map.json", case_map)
        plan_path = write(base / name / "plan.json", case_plan)
        out = base / name / "result.json"
        rows.append({"name": name, **call([binary, "maintenance", plan_path, out], out)})
    # A run whose problem has no photon.point_source.
    spec = component_spec(binary, "pipe", response, sha256(response), EXAMPLE_COOLING_S)
    del spec["photon"]["point_source"]
    spec_path = write(base / "no-point-source" / "pipe.json", spec)
    result = base / "no-point-source" / "pipe-result.json"
    done = call([binary, "run", spec_path, result], result)
    plan_path = write(base / "no-point-source" / "plan.json", {
        **plan, "dose_rates": {"runs": [{"result": "pipe-result.json", "location": "pipe"}]},
        "jobs": [{**plan["jobs"][0], "location": "pipe"}]})
    out = base / "no-point-source" / "result.json"
    rows.append({"name": "no-point-source", "run_exit": done["exit"],
                 **call([binary, "maintenance", plan_path, out], out)})
    return sorted(rows, key=lambda row: REFUSALS.index(row["name"]))


def provenance(binary: Path) -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return {"commit": head, "clean_tracked_tree": dirty == "", "binary": str(binary),
            "binary_sha256": sha256(binary)}


def rehearse(args) -> int:
    global WORK
    WORK = ROOT / "target" / "p197-rehearsal"
    binary = Path(args.actinv_bin).resolve()
    response = Path(args.photon_response).expanduser().resolve()
    rows = {"g2": run_g2(binary, ["one"], ["sparse"]), "g3": run_g3(binary, response, list(DEVELOPMENT)),
            "g4": run_g4(binary, response)}
    for row in rows["g2"] + rows["g4"]:
        print(row["name"], "exit", row.get("exit"))
    for row in rows["g3"]:
        print(row["name"], "doses present", row.get("doses_present"),
              {g: r.get("exit") for g, r in row.get("runs", {}).items()},
              {g: r.get("exit") for g, r in row.get("plans", {}).items()})
    return 0


def run(args) -> int:
    binary = Path(args.actinv_bin).resolve()
    response = Path(args.photon_response).expanduser().resolve()
    WORK.mkdir(parents=True, exist_ok=True)
    record = {"schema": SCHEMA, "provenance": provenance(binary), "photon_response_sha256": sha256(response),
              "g2": run_g2(binary), "g3": run_g3(binary, response), "g4": run_g4(binary, response)}
    write(RECORD, record)
    print(f"wrote {RECORD.relative_to(ROOT)}")
    return 0


# ---------------------------------------------------------------- the checker

def relative(a, b) -> float:
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return math.inf
    if a == b:
        return 0.0
    return abs(a - b) / max(abs(a), abs(b))


def compared(answers_: dict, reference: dict) -> list:
    """(name, planner value, reference value) for every answer compared."""
    # The best wait is reported, not compared: near a flat minimum it is ill-conditioned while its cost is not.
    pairs = [("earliest_wait_d", answers_["earliest_wait_d"], reference["earliest_wait_d"]),
             ("best_cost", answers_["best_cost"], reference["best_cost"])]
    for i, (a, b) in enumerate(zip(answers_["alone_d"], reference["alone_d"])):
        pairs.append((f"alone[{i}]", a, b))
    for wait, ref in reference["waits"].items():
        got = answers_["waits"].get(wait) or answers_["waits"].get(str(float(wait)))
        if got is None:
            pairs.append((f"wait {wait}", None, 1.0))
            continue
        for i, (a, b) in enumerate(zip(got["jobs"], ref["jobs"])):
            pairs.append((f"wait {wait} job[{i}]", a, b))
        pairs.append((f"wait {wait} collective", got["collective"], ref["collective"]))
    return pairs


def g0() -> dict:
    lines = HASH_LOG.read_text().splitlines() if HASH_LOG.is_file() else []
    missing = [rel for rel in HASHED if f"{sha256(ROOT / rel)}  {rel}" not in lines]
    return {"pass": not missing, "missing": missing}


def g1(record: dict) -> dict:
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("schema")
    if not record.get("provenance", {}).get("clean_tracked_tree"):
        problems.append("tracked tree not clean")
    names = sorted(r["name"] for r in record.get("g2", []))
    expected = sorted(f"{c}-{g}-{k}" for c in CURVES for g in GRIDS for k in ("access", "limited"))
    if names != expected:
        problems.append("G2 cases differ")
    if [r["name"] for r in record.get("g3", [])] != [*DEVELOPMENT, *HELD_OUT]:
        problems.append("G3 components differ")
    if [r["name"] for r in record.get("g4", [])] != REFUSALS:
        problems.append("G4 cases differ")
    for row in record.get("g2", []):
        if row.get("exit") != 0 or "answers" not in row:
            problems.append(f"G2 {row['name']} did not finish")
    return {"pass": not problems, "problems": problems}


def g2(record: dict) -> dict:
    rows, failures = [], []
    for row in record.get("g2", []):
        if "answers" not in row:
            failures.append(f"{row['name']}: no answers")
            continue
        pairs = compared(row["answers"], row["exact"])
        worst_name, worst = max(((n, relative(a, b)) for n, a, b in pairs), key=lambda p: p[1])
        low = [n for n, a, b in pairs
               if (n.startswith("wait") or n.startswith("alone") or n == "earliest_wait_d")
               and a is not None and b is not None and a < b * (1 - CONSERVATIVE)]
        coarse = bool((row["answers"].get("interpolation_check") or {}).get("coarse"))
        if row["grid"] == "dense" and worst > DENSE_TOL:
            failures.append(f"{row['name']}: {worst_name} off by {worst:.3g} on the dense grid (a)")
        if low:
            failures.append(f"{row['name']}: below exact at {low} (b)")
        if row["curve"] == "one" and worst > EXACT_TOL:
            failures.append(f"{row['name']}: one nuclide off by {worst:.3g} (c)")
        if worst > FLAG_TOL and not coarse:
            failures.append(f"{row['name']}: off by {worst:.3g} and not flagged (d)")
        if row["grid"] == "dense" and coarse:
            failures.append(f"{row['name']}: dense grid flagged (d)")
        rows.append({"name": row["name"], "largest": worst, "at": worst_name, "coarse": coarse,
                     "check_change": (row["answers"].get("interpolation_check") or {}).get("largest_relative_change")})
    dense = [r["largest"] for r in rows if r["name"].split("-")[1] == "dense"]
    return {"pass": not failures and len(rows) == len(CURVES) * len(GRIDS) * 2, "failures": failures,
            "largest_dense": max(dense) if dense else None, "rows": rows}


def g3(record: dict) -> dict:
    rows, failures = [], []
    for row in record.get("g3", []):
        name = row["name"]
        if not row.get("doses_present"):
            failures.append(f"{name}: a point-source dose is missing (a)")
            continue
        plans = row.get("plans", {})
        if any("answers" not in plans.get(g, {}) for g in ("example", "dense", "table")):
            failures.append(f"{name}: a plan did not finish")
            continue
        pairs = compared(plans["example"]["answers"], plans["dense"]["answers"])
        worst_name, worst = max(((n, relative(a, b)) for n, a, b in pairs), key=lambda p: p[1])
        coarse = bool(plans["example"]["answers"]["interpolation_check"].get("coarse"))
        if worst > G3_TOL and not coarse:
            failures.append(f"{name}: example grid off by {worst:.3g} and not flagged (b)")
        dense, table = dict(plans["dense"]["answers"]), dict(plans["table"]["answers"])
        if dense != table:
            failures.append(f"{name}: the table gives another result than the runs (c)")
        rows.append({"name": name, "held_out": row["held_out"], "largest": worst, "at": worst_name,
                     "coarse": coarse, "dense_coarse": dense["interpolation_check"].get("coarse"),
                     "earliest_wait_d": dense["earliest_wait_d"]})
    unflagged = [r["largest"] for r in rows if not r["coarse"]]
    return {"pass": not failures and len(rows) == len(DEVELOPMENT) + len(HELD_OUT), "failures": failures,
            "largest_unflagged": max(unflagged) if unflagged else None,
            "flagged": [r["name"] for r in rows if r["coarse"]], "rows": rows}


def g4(record: dict) -> dict:
    failures = []
    for row in record.get("g4", []):
        cause = REFUSAL_CAUSE[row["name"]]
        if row.get("exit") != 2 or row.get("output_sha256") is not None or cause not in row.get("stderr", ""):
            failures.append(f"{row['name']}: exit {row.get('exit')}, output {row.get('output_sha256')}, "
                            f"stderr {row.get('stderr', '')[:120]!r}")
    return {"pass": not failures and len(record.get("g4", [])) == len(REFUSALS), "failures": failures}


def sentences(verdict: dict) -> list:
    g2v, g3v = verdict["G2"], verdict["G3"]
    held = sum(1 for r in g3v["rows"] if r["held_out"])
    return [
        "`actinv maintenance` plans jobs after shutdown from dose rates tabulated over cooling time: when each job "
        "may start, the dose of each crew at each wait, the earliest allowed wait and the wait of least cost.",
        f"On three dose curves of one, two and three decaying nuclides with exact answers, its doses, start times and "
        f"least costs on a 201-time grid were within {g2v['largest_dense']:.1g} of exact, never below the exact "
        f"dose or start on any grid, and its interpolation check flagged every grid where an answer was more than 5% "
        f"off.",
        f"On {len(g3v['rows'])} activated components computed by ACTINV ({held} of them held out), plans from a "
        f"15-time cooling grid were within {g3v['largest_unflagged']:.1g} of plans from a 73-time grid wherever the "
        f"check did not flag the coarser grid ({len(g3v['flagged'])} flagged).",
    ]


def evaluate(record: dict) -> dict:
    verdict = {"G0": g0(), "G1": g1(record), "G2": g2(record), "G3": g3(record), "G4": g4(record),
               "G5": "CI on the evidence commit (external)"}
    passed = all(verdict[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4"))
    verdict["verdict"] = "P197-PASS" if passed else "P197-FAIL"
    if passed:
        verdict["sentences"] = sentences(verdict)
    return verdict


def check(args) -> int:
    record = json.loads(RECORD.read_text())
    verdict = evaluate(record)
    write(VERDICT, verdict)
    print(json.dumps({k: (v["pass"] if isinstance(v, dict) and "pass" in v else v) for k, v in verdict.items()
                      if k != "sentences"}, indent=1))
    for line in verdict.get("sentences", []):
        print(line)
    return 0


def selftest() -> int:
    curve = CURVES["one"]
    k = math.log(2) / (5 * DAY)
    assert abs(dose(curve, 0, 10 * DAY) - 1e-2 / k * (1 - math.exp(-k * 10 * DAY)) / HOUR) < 1e-15
    assert abs(bisect(lambda t: rate(curve, t) <= 1e-2 / 4, 0, 100 * DAY) / DAY - 10.0) < 1e-9
    exact = exact_plan(curve, rate(curve, 20 * DAY), None)
    assert abs(exact["earliest_wait_d"] - 20.0) < 1e-9 and abs(exact["alone_d"][1] - 20.0) < 1e-9
    assert relative(None, None) == 0 and relative(None, 1) == math.inf and relative(1.0, 2.0) == 0.5
    planted = {"earliest_wait_d": 20.0, "alone_d": [20.0, 20.0], "best_wait_d": 20.0, "best_cost": 1.0,
               "waits": {"0.5": {"jobs": [1.0, 2.0], "collective": 8.0}}, "interpolation_check": {"coarse": False}}
    reference = {**planted, "waits": {"0.5": {"jobs": [1.0, 2.0], "collective": 8.0}}}
    assert max(relative(a, b) for _, a, b in compared(planted, reference)) == 0
    low = {**planted, "waits": {"0.5": {"jobs": [0.5, 2.0], "collective": 8.0}}}
    row = {"name": "one-dense-access", "curve": "one", "grid": "dense", "answers": low, "exact": reference}
    failures = g2({"g2": [row]})["failures"]
    assert any("(a)" in f for f in failures) and any("(b)" in f for f in failures)
    assert any("(c)" in f for f in failures) and any("(d)" in f for f in failures)
    refusal = {"g4": [{"name": n, "exit": 2, "output_sha256": None, "stderr": REFUSAL_CAUSE[n]} for n in REFUSALS]}
    assert g4(refusal)["pass"]
    refusal["g4"][0]["exit"] = 1
    assert not g4(refusal)["pass"]
    assert len(dense_cooling()) + 1 == 73 and len(EXAMPLE_COOLING_S) + 1 == 15
    assert len(grid_times(GRIDS["dense"])) == 201
    print("selftest ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=["run", "rehearse", "check"])
    parser.add_argument("--actinv-bin")
    parser.add_argument("--photon-response", default=str(DEFAULT_RESPONSE))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.command in ("run", "rehearse"):
        if not args.actinv_bin:
            parser.error(f"{args.command} needs --actinv-bin")
        return run(args) if args.command == "run" else rehearse(args)
    if args.command == "check":
        return check(args)
    parser.error("give run, check or --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
