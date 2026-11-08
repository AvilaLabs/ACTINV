#!/usr/bin/env python3
"""P196 checker: gates on results/p196_record.json and the outputs under target/p196-work; writes
results/p196_verdict.json.

  G0  The SHA-256 of the protocol, controls/p196.py, controls/p196_browser.mjs and this checker are in
      protocols/protocol_hash.txt.
  G1  Provenance: the record is the registered run (not a rehearsal), from a clean tracked tree, on the registered
      suite, browser problems and refusal cases, at two reactions; every output matches its SHA-256.
  G2  Agreement, recomputed here: on every suite problem both runs finish with the same steps, and at every step the
      total activity, total decay heat and total photon emission from the pack are within 1e-4 (relative) of the
      whole library's, and every nuclide carrying at least 0.1% of the step's activity is within 1e-3.
  G3  The browser: on every browser problem the result from the engine worker in Chromium is identical to the
      native pack run apart from wall times and the entry point.
  G4  Refusals: every refusal case exits non-zero without writing a result, naming its cause.
  G5  Size and time (reported, never a gate).
  G6  CI green on the evidence commit (external).

Verdict: P196-PASS iff G0, G1, G2, G3 and G4 pass (G6 external).

`--selftest` checks the checker on planted outputs and exits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROTOCOL_REL = "protocols/ACTINV-P196_PROTOCOL.md"
HASHED = [PROTOCOL_REL, "controls/p196.py", "controls/p196_browser.mjs", "controls/check_p196.py"]
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p196_record.json"
VERDICT = ROOT / "results" / "p196_verdict.json"
SCHEMA = "actinv-p196-record-1"

DATA_DIR = ROOT / "actinv-data" / "v1.2.0"
LIBRARY_FILE = "activation/tendl-2025-neutron-709g.npz"
DECAY_FILES = ("decay/endf-b-viii-0_decay.dat", "decay/jeff-3-3_decay.dat")
LIBRARY_SHA256 = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
DECAY_SHA256 = ("6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
                "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123")
PACK_IDS = ("tendl-2025-neutron-709g", "endfb-viii-0-decay", "jeff-3-3-decay")
REACTIONS = "2"

TOTAL_TOLERANCE = 1e-4
NUCLIDE_TOLERANCE = 1e-3
NUCLIDE_SHARE = 1e-3

MATERIALS = ("eurofer97", "eurofer97-3-measured", "ss304", "ss304l", "ss316", "inconel718", "zircaloy4", "al6061",
             "concrete-ordinary", "water", "graphite-reactor", "tungsten", "tantalum", "niobium", "titanium-grade2",
             "copper", "beryllium-s65", "v-4cr-4ti", "pb-15.7li")
SOURCES = ("fusion-14mev", "fission", "thermal", "one-over-e")
HIGH_FLUENCE_SOURCES = ("fusion-14mev", "thermal")
EXAMPLES = ("fns_fe_5min", "pulsed_demo", "feed_removal_demo", "shielding_demo")

BROWSER_PROBLEMS = [
    "example-fns_fe_5min", "example-pulsed_demo", "example-feed_removal_demo",
    "eurofer97-fusion-14mev-1e15-10y", "ss316-thermal-1e15-10y", "tungsten-fusion-14mev-1e15-10y",
    "zircaloy4-thermal-1e15-10y", "concrete-ordinary-thermal-1e14-1y", "copper-fission-1e14-1y",
    "inconel718-one-over-e-1e14-1y", "pb-15.7li-fusion-14mev-1e14-1y", "v-4cr-4ti-fission-1e14-1y",
]
REHEARSAL_PROBLEMS = ["example-fns_fe_5min", "al6061-thermal-1e14-1y", "niobium-fusion-14mev-1e15-10y"]


def suite() -> list[dict]:
    problems = [{"name": f"example-{name}", "example": f"examples/{name}.json"} for name in EXAMPLES]
    for material in MATERIALS:
        for source in SOURCES:
            problems.append({"name": f"{material}-{source}-1e14-1y", "material": material, "source": source,
                             "flux": "1e14", "irradiate": "1y"})
        for source in HIGH_FLUENCE_SOURCES:
            problems.append({"name": f"{material}-{source}-1e15-10y", "material": material, "source": source,
                             "flux": "1e15", "irradiate": "10y"})
    return problems


# The refusal cases: the iron example edited so that a pack cannot reproduce the whole library. Each must exit
# non-zero without a result and name its cause (a lower-case substring of the message).
REFUSAL_BASE = "examples/fns_fe_5min.json"


def _set(path: str, value):
    def edit(spec: dict):
        node = spec
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return edit


REFUSALS = [
    ("other-library", _set("library", {"path": "data/another-library.npz"}), "this pack holds"),
    ("other-library-pin", _set("library.sha256", "0" * 64), "sha-256"),
    ("decay-order", _set("decay", {"primary": "catalog:jeff-3-3-decay", "fallback": "catalog:endfb-viii-0-decay"}),
     "decay data"),
    ("fission-yields", _set("fission_yields", {"files": [{"path": "data/nfy.endf", "sha256": "1" * 64}]}),
     "fission products"),
    ("damage", _set("damage", {"table": {"path": "catalog:tendl-2025-neutron-damage",
                                         "sha256": "c9f1b2f2d60511f9da1b6c961b4a2bc4920d11604d4db32325565a265eed1069"}}), "damage"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def relative(a: float, b: float) -> float:
    return 0.0 if a == b else abs(a - b) / max(abs(a), abs(b))


def step_totals(step: dict) -> tuple[float, float, float]:
    return (sum(step.get("activity_Bq_per_g", {}).values()),
            step.get("heat_W_per_g", {}).get("total", 0.0),
            step.get("photon_source", {}).get("total_photons_s_g", 0.0))


def agreement(full: dict, packed: dict) -> dict:
    """Largest relative changes of the pack run from the whole-library run, and where."""
    worst = {"activity": 0.0, "heat": 0.0, "photons": 0.0, "nuclide": 0.0}
    at = None
    steps_full, steps_pack = full.get("steps", []), packed.get("steps", [])
    for a, b in zip(steps_full, steps_pack):
        for key, x, y in zip(("activity", "heat", "photons"), step_totals(a), step_totals(b)):
            worst[key] = max(worst[key], relative(x, y))
        total = step_totals(a)[0]
        for name, value in a.get("activity_Bq_per_g", {}).items():
            if total > 0 and value >= NUCLIDE_SHARE * total:
                change = relative(value, b.get("activity_Bq_per_g", {}).get(name, 0.0))
                if change > worst["nuclide"]:
                    worst["nuclide"], at = change, {"step": a.get("step"), "nuclide": name}
    ok = (len(steps_full) == len(steps_pack) and len(steps_full) > 0
          and max(worst["activity"], worst["heat"], worst["photons"]) <= TOTAL_TOLERANCE
          and worst["nuclide"] <= NUCLIDE_TOLERANCE)
    return {"pass": ok, "steps": [len(steps_full), len(steps_pack)], "largest": worst, "at": at}


IGNORED = {"ms", "wall_time_s", "entry_point"}


def stripped(value):
    if isinstance(value, dict):
        return {k: stripped(v) for k, v in value.items() if k not in IGNORED}
    if isinstance(value, list):
        return [stripped(v) for v in value]
    return value


def identical(a: dict, b: dict) -> bool:
    return json.dumps(stripped(a), sort_keys=True) == json.dumps(stripped(b), sort_keys=True)


def load(rel: str | None, pinned: str | None) -> dict | None:
    if not rel or not pinned:
        return None
    path = ROOT / rel
    if not path.is_file() or sha256(path) != pinned:
        return None
    return json.loads(path.read_text())


def g0() -> dict:
    lines = HASH_LOG.read_text().splitlines() if HASH_LOG.is_file() else []
    missing = [rel for rel in HASHED
               if not (ROOT / rel).is_file() or f"{sha256(ROOT / rel)}  {rel}" not in lines]
    return {"pass": not missing, "missing": missing}


def g1(record: dict) -> dict:
    failures = []
    if record.get("schema") != SCHEMA:
        failures.append("schema")
    if record.get("rehearsal") is not False:
        failures.append("the record is a rehearsal")
    if not record.get("tracked_tree_clean"):
        failures.append("tracked tree was not clean")
    if record.get("reactions") != int(REACTIONS):
        failures.append("reactions")
    if [p["name"] for p in record.get("problems", [])] != [p["name"] for p in suite()]:
        failures.append("suite differs from the registered one")
    if [b["name"] for b in record.get("browser", [])] != BROWSER_PROBLEMS:
        failures.append("browser problems differ")
    if [r["name"] for r in record.get("refusals", [])] != [name for name, _, _ in REFUSALS]:
        failures.append("refusal cases differ")
    graph = ROOT / record.get("pack", {}).get("dir", "missing") / "graph.json"
    if not graph.is_file() or sha256(graph) != record["pack"].get("graph_sha256"):
        failures.append("pack graph")
    else:
        names = json.loads(graph.read_text()).get("names", {})
        if names.get("library", {}).get("id") != PACK_IDS[0] or \
                [d.get("id") for d in names.get("decay", [])] != list(PACK_IDS[1:]):
            failures.append("pack names")
        if json.loads(graph.read_text()).get("library_sha256") != LIBRARY_SHA256:
            failures.append("pack library SHA-256")
    for row in record.get("problems", []):
        for kind in ("full", "pack"):
            if row.get(f"{kind}_exit") == 0 and load(row.get(kind), row.get(f"{kind}_sha256")) is None:
                failures.append(f"{row['name']} {kind} output does not match its SHA-256")
    return {"pass": not failures, "failures": failures}


def g2(record: dict) -> dict:
    rows, failures = [], []
    for row in record.get("problems", []):
        full = load(row.get("full"), row.get("full_sha256"))
        packed = load(row.get("pack"), row.get("pack_sha256"))
        if full is None or packed is None:
            failures.append(row["name"])
            rows.append({"name": row["name"], "pass": False, "reason": "a run failed or its output is missing"})
            continue
        result = {"name": row["name"], **agreement(full, packed)}
        if not result["pass"]:
            failures.append(row["name"])
        rows.append(result)
    worst = {key: max((r["largest"][key] for r in rows if "largest" in r), default=None)
             for key in ("activity", "heat", "photons", "nuclide")}
    return {"pass": bool(rows) and not failures, "failures": failures, "largest": worst, "problems": rows}


def g3(record: dict) -> dict:
    by_name = {row["name"]: row for row in record.get("problems", [])}
    rows, failures = [], []
    for row in record.get("browser", []):
        native = by_name.get(row["name"], {})
        packed = load(native.get("pack"), native.get("pack_sha256"))
        browser = load(row.get("browser"), row.get("browser_sha256"))
        same = packed is not None and browser is not None and row.get("exit") == 0 and identical(packed, browser)
        if not same:
            failures.append(row["name"])
        rows.append({"name": row["name"], "identical": same, "report": row.get("report")})
    return {"pass": bool(rows) and not failures, "failures": failures, "problems": rows}


def g4(record: dict) -> dict:
    expected = {name: cause for name, _, cause in REFUSALS}
    rows, failures = [], []
    for row in record.get("refusals", []):
        cause = expected.get(row["name"], "\0")
        ok = row.get("exit") not in (0, None) and not row.get("wrote_output") and cause in row.get("stderr", "").lower()
        if not ok:
            failures.append(row["name"])
        rows.append({"name": row["name"], "pass": ok})
    return {"pass": bool(rows) and not failures, "failures": failures, "cases": rows}


def g5(record: dict) -> dict:
    plans = [row["plan"] for row in record.get("problems", []) if row.get("plan")]
    browser = [row.get("report") or {} for row in record.get("browser", [])]
    seconds = [b["seconds"] for b in browser if isinstance(b.get("seconds"), (int, float))]
    mb = [p["bytes"] / 1e6 for p in plans]
    return {
        "pack_megabytes": {"median": statistics.median(mb) if mb else None, "max": max(mb, default=None)},
        "pack_files": {"median": statistics.median([p["files"] for p in plans]) if plans else None,
                       "max": max((p["files"] for p in plans), default=None)},
        "browser_seconds": {"median": statistics.median(seconds) if seconds else None,
                            "max": max(seconds, default=None)},
        "native_pack_seconds_median": statistics.median([r["pack_wall_s"] for r in record.get("problems", [])])
        if record.get("problems") else None,
        "note": "browser time is from a local Python web server on the evidence machine; a hosted site differs",
    }


def sentences(record: dict, verdict: dict) -> list[str]:
    largest = verdict["G2"]["largest"]
    totals = max(largest["activity"], largest["heat"], largest["photons"])
    size = verdict["G5"]["pack_megabytes"]
    return [
        "In the browser, ACTINV solves a problem with the standard TENDL-2025 library, fetching only the reactions "
        "its material can reach in two steps and the decay data they lead to.",
        f"On {len(verdict['G2']['problems'])} problems ({len(MATERIALS)} materials under four standard spectra at "
        f"1e14 n/cm²/s for a year, two of them at 1e15 for ten years, and {len(EXAMPLES)} of the examples), its "
        f"total activity, decay heat and photon emission at every step were within {totals:.1g} of the whole "
        f"library's, and every nuclide carrying 0.1% of the activity within {largest['nuclide']:.1g}.",
        f"Run in Chromium, the browser's result was identical to the native one on all {len(verdict['G3']['problems'])} "
        f"problems tried; a problem fetched a median of {size['median']:.0f} MB of data (at most {size['max']:.0f} MB).",
    ]


def check() -> int:
    record = json.loads(RECORD.read_text())
    verdict = {"G0": g0(), "G1": g1(record), "G2": g2(record), "G3": g3(record), "G4": g4(record), "G5": g5(record)}
    passed = all(verdict[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4"))
    verdict["verdict"] = "P196-PASS" if passed else "P196-FAIL"
    verdict["G6"] = "CI on the evidence commit (external)"
    if passed:
        verdict["sentences"] = sentences(record, verdict)
    VERDICT.write_text(json.dumps(verdict, indent=1) + "\n")
    print(verdict["verdict"], {g: verdict[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4")})
    return 0 if passed else 1


def selftest() -> int:
    step = lambda act, heat=1.0, ph=1.0: {"step": 1, "activity_Bq_per_g": act, "heat_W_per_g": {"total": heat},
                                          "photon_source": {"total_photons_s_g": ph}}
    base = {"steps": [step({"A": 1000.0, "B": 2.0, "C": 0.5})]}
    same = json.loads(json.dumps(base))
    assert agreement(base, same)["pass"]
    total_off = {"steps": [step({"A": 1000.0, "B": 2.0, "C": 0.5}, heat=1.0002)]}
    assert not agreement(base, total_off)["pass"], "a heat change above 1e-4 must fail"
    nuclide_off = {"steps": [step({"A": 1000.0, "B": 2.003, "C": 0.5})]}
    assert not agreement(base, nuclide_off)["pass"], "a 0.15% change of a 0.2% nuclide must fail"
    small_off = {"steps": [step({"A": 1000.0, "B": 2.0, "C": 0.6})]}
    assert agreement(base, small_off)["pass"], "a nuclide under 0.1% of the activity is not gated"
    assert not agreement(base, {"steps": []})["pass"], "missing steps must fail"
    timed = json.loads(json.dumps(base))
    timed.update({"ms": 3.0, "entry_point": "browser"})
    assert identical(base, timed) and not identical(base, total_off)
    names = [p["name"] for p in suite()]
    assert len(names) == len(set(names)) == len(EXAMPLES) + len(MATERIALS) * (len(SOURCES) + len(HIGH_FLUENCE_SOURCES))
    assert set(BROWSER_PROBLEMS) <= set(names) and set(REHEARSAL_PROBLEMS) <= set(names)
    spec = json.loads((ROOT / REFUSAL_BASE).read_text())
    for name, edit, cause in REFUSALS:
        edited = json.loads(json.dumps(spec))
        edit(edited)
        assert edited != spec and cause == cause.lower(), name
    print("check_p196 selftest: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    return selftest() if args.selftest else check()


if __name__ == "__main__":
    raise SystemExit(main())
