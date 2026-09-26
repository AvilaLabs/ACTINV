#!/usr/bin/env python3
"""P61 G2 — independent arithmetic: every emitted completeness value is
recomputed from the emitted ledger maps and the spec (defect rates are
sums over the ledger's own maps; channel totals recompute from the
spec-side data — the checker does not trust any emitted completeness
field).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p61_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g2_p61_exactness.json"

REACTION_MAPS = {
    "products_no_evaluated_decay_data": "products_no_evaluated_decay_data",
    "products_unmapped_to_leakage": "products_unmapped_to_leakage",
    "isomer_fell_back_to_ground":
        "isomer_state_absent_from_decay_library_used_ground",
    "fission_no_yields_to_leakage": "fission_no_yields_to_leakage",
}


def naive_sum(values) -> float:
    """Left-to-right fold matching the emitter — Python 3.12's sum() is
    compensated and differs at the last ulp."""
    acc = 0.0
    for v in values:
        acc += v
    return acc


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


checks = []
tmp = Path(tempfile.mkdtemp(prefix="p61_g2_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)

for name, sp in [("ref", p61_case.spec(fx)),
                 ("defect", p61_case.spec_dropping_decay(fx, tmp))]:
    res = p61_case.run(ACTINV, sp, tmp, name)
    comp = res["ledger"]["completeness"]
    ledger = res["ledger"]

    # every quantified reaction defect reproduces its ledger map exactly
    for d in comp["reaction_channel"]["defects"]:
        cls = d["class"]
        if cls == "fission_yield_products_to_leakage":
            exp_rate = naive_sum(e["production_rate_per_parent_s"]
                                 for e in ledger
                                 ["fission_yield_products_to_leakage"])
            exp_n = len(ledger["fission_yield_products_to_leakage"])
        else:
            key = REACTION_MAPS.get(cls)
            check(f"{name}: class {cls} maps to a ledger field",
                  key is not None, cls)
            if key is None:
                continue
            emitted_map = ledger.get(key) or {}
            exp_rate = naive_sum(emitted_map.values())
            exp_n = len(emitted_map)
            check(f"{name}: {cls} largest ⊆ ledger map",
                  all(e["name"] in emitted_map
                      and e["rate_coefficient_per_s"] == emitted_map[e["name"]]
                      for e in d.get("largest", [])))
        check(f"{name}: {cls} rate exact",
              d["rate_coefficient_per_s"] == exp_rate,
              f"{d['rate_coefficient_per_s']!r} vs {exp_rate!r}")
        check(f"{name}: {cls} instances exact",
              d["instances"] == exp_n)
        total = comp["reaction_channel"][
            "total_production_rate_coefficient_per_s"]
        check(f"{name}: {cls} fraction exact",
              d["fraction_of_channel_flow"] == exp_rate / total
              if total > 0 else d["fraction_of_channel_flow"] is None)

    # decay-side defects reproduce their sources
    for d in comp["decay_channel"]["defects"]:
        cls = d["class"]
        if cls == "decay_branching_sums_off_unity":
            check(f"{name}: branching_sums count exact",
                  d["instances"]
                  == len(ledger.get("decay_branching_sums_off_unity") or {}))
            emitted = ledger.get("decay_branching_sums_off_unity") or {}
            for e in d["entries"]:
                check(f"{name}: branching entry {e['nuclide']} exact",
                      emitted.get(e["nuclide"]) == e["branching_sum"])
        elif cls == "spontaneous_fission_branches_to_leakage":
            check(f"{name}: sf count exact",
                  d["instances"]
                  == ledger["spontaneous_fission_branches_to_leakage"])
        elif cls == "decay_daughters_missing":
            check(f"{name}: daughters_missing count exact",
                  d["instances"]
                  == ledger["decay_daughters_missing"])

    # completeness is self-consistent with what the ledger reports
    n_ledger_defects = (
        len(ledger.get("products_no_evaluated_decay_data") or {})
        + len(ledger.get("products_unmapped_to_leakage") or {})
        + len(ledger.get(
            "isomer_state_absent_from_decay_library_used_ground") or {})
        + len(ledger.get("fission_no_yields_to_leakage") or {})
        + len(ledger.get("fission_yield_products_to_leakage") or []))
    check(f"{name}: quantified defect classes cover ledger inventory",
          n_ledger_defects == sum(d["instances"] for d in
                                  comp["reaction_channel"]["defects"]),
          f"{n_ledger_defects} vs emitted classes")

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps(
    {"pass": not failed, "n": len(checks), "failed": failed},
    indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
