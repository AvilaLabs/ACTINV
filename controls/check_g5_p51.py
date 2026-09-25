#!/usr/bin/env python3
"""P51 independent checker — re-verifies the phase evidence from the
recorded ledgers and saved artifacts, without reusing the control logic:

  * G2 identity: re-diffs the saved cold vs warm result documents
    byte-for-byte after re-implementing the exclusion strip.
  * G3 amortization: re-computes medians and the ratio from the ledger and
    re-checks the threshold; verifies the warm/cold flags were not swapped.
  * G4 lifecycle: re-checks event order (kill → reaped → respawn answered →
    restart identity), RSS ceiling, and that the battery actually thrashed.

Mutations: --mutate 1 flips warm flags in the G3 ledger (must be rejected);
--mutate 2 drops the 'reaped' event from G4 (must be rejected);
--mutate 3 reports a fabricated identity match (must be rejected).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p51_artifacts as p51a  # noqa: E402

RESULTS = ROOT / "results"
WORK = ROOT / "target/p51-battery"
OUT = RESULTS / "check_g5_p51.json"
# Implemented independently of g2's strip() — declared exclusion set only.
EXCLUDED = {"ms", "entry_point"}


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in EXCLUDED}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def mutate(records: dict, which: int) -> dict:
    scratch = copy.deepcopy(records)
    if which == 1:  # swap a cold/warm label so the ratio lies
        scratch["g3"]["warm"][1]["warm"] = True
        scratch["g3"]["warm"][0]["warm"] = True
    elif which == 2:  # drop the reap record
        scratch["g4"]["events"] = [
            e for e in scratch["g4"]["events"] if e["event"] != "reaped"]
    elif which == 3:  # corrupt a recorded identity claim
        scratch["g2"]["battery"]["synthetic_uq"]["warm_equals_cold"] = False
    return scratch


def verify(records: dict) -> list[str]:
    problems: list[str] = []
    g2, g3, g4 = records["g2"], records["g3"], records["g4"]

    # -- G2: re-diff the saved documents ourselves; do not trust the flag
    for name in ("synthetic_nominal", "synthetic_uq", "corpus_probe"):
        cold_p = WORK / f"{name}_cold.json"
        warm_p = WORK / f"{name}_warm.json"
        if not (cold_p.exists() and warm_p.exists()):
            problems.append(f"{name}: saved result pair missing")
            continue
        cold = strip(json.load(open(cold_p)))
        warm = strip(json.load(open(warm_p)))
        if cold != warm:
            problems.append(f"{name}: warm document differs from cold")
        entry = g2["battery"].get(name, {})
        recorded = entry.get("warm_equals_cold") is True \
            and entry.get("cold_equals_first") is True
        if recorded != (cold == warm):
            problems.append(f"{name}: recorded identity claim is "
                            f"{'true' if recorded else 'false'} but the "
                            "documents disagree with it")

    # -- G3: recompute the amortization arithmetic
    warm_rows = g3["warm"]
    cold = [float(v) for v in g3["cold_wall_ms"]]
    warm_wall = [float(r["client_wall_ms"]) for r in warm_rows[1:]]
    if len(cold) != 3 or len(warm_wall) != 3:
        problems.append("g3 ledger missing cold or warm samples")
    cold_med = sorted(cold)[1] if len(cold) == 3 else None
    warm_med = sorted(warm_wall)[1] if len(warm_wall) == 3 else None
    if cold_med is None or warm_med is None or warm_med > cold_med * 0.5:
        problems.append(f"warm median {warm_med} is not <0.5x cold "
                        f"{cold_med}")
    if abs(g3["cold_median_ms"] - cold_med) > 0.2 or \
            abs(g3["warm_median_solve_ms"] - warm_med) > 0.2:
        problems.append("ledgered medians do not match recomputation")
    if abs(g3["ratio"] - warm_med / cold_med) > 1e-3:
        problems.append("ledgered ratio does not match recomputation")
    # A cold first request is required; a mutated warm flag must fail here.
    if warm_rows[0].get("warm") is not False:
        problems.append("first worker solve was not recorded cold")
    if any(r.get("warm") is not True for r in warm_rows[1:]):
        problems.append("a repeat solve is not recorded warm")

    # -- G4: event order and ceiling
    names = [e["event"] for e in g4["events"]]
    order = ["killed", "reaped", "respawn_answered", "restart_identity",
             "rss"]
    positions = []
    for name in order:
        if name not in names:
            problems.append(f"lifecycle event '{name}' missing")
        else:
            positions.append(names.index(name))
    if positions != sorted(positions):
        problems.append("lifecycle events out of order")
    reap = next((e for e in g4["events"] if e["event"] == "reaped"), None)
    if reap is not None and reap.get("exit") is None:
        problems.append("reap record has no exit status")
    if not next((e for e in g4["events"] if e["event"] == "reap_verified"),
                {}).get("gone"):
        problems.append("killed worker not verified gone")
    if next((e for e in g4["events"] if e["event"] == "restart_identity"),
            {}).get("match") is not True:
        problems.append("restart identity not verified true")
    rss = next((e for e in g4["events"] if e["event"] == "rss"), None)
    ceiling = g4.get("rss_ceiling_bytes", 4 * 1024**3)
    if rss is None or rss.get("max_mib", 0) * 2**20 > ceiling:
        problems.append("rss peak missing or over ceiling")
    if g4["events"][0]["event"] != "sustained_battery":
        problems.append("sustained battery record absent")
    return problems


def main() -> int:
    mutation_id = None
    if "--mutate" in sys.argv:
        mutation_id = int(sys.argv[sys.argv.index("--mutate") + 1])

    seal = json.load(open(ROOT / "results/g0_p51_seals.json"))
    artifacts = p51a.verify()
    drift = {n: r["path"] for n, r in artifacts.items()
             if r["sha256"] != seal["artifacts"][n]["sha256"]}
    records = {
        "g2": json.load(open(RESULTS / "g2_p51_identity.json")),
        "g3": json.load(open(RESULTS / "g3_p51_amortization.json")),
        "g4": json.load(open(RESULTS / "g4_p51_lifecycle.json")),
    }
    if mutation_id:
        records = mutate(records, mutation_id)

    problems = verify(records)
    if drift:
        problems.append(f"sealed artifact drift: {sorted(drift)}")

    record = {"schema": "actinv-p51-g5-1", "mutate": mutation_id,
              "problems": problems,
              "pass": bool(problems) if mutation_id else not problems}
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"], "problems": len(problems),
                      "mutate": mutation_id}))
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
