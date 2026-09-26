#!/usr/bin/env python3
"""P61 G5 — independent checker. Re-parses a produced run document and
re-derives every completeness field from the emitted ledger itself
(any consumer could do this — the audit must not contradict the ledger
it summarises). Then proves mutations get caught.
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
OUT = ROOT / "results/g5_p61_checker.json"

problems = []
checked = []


def ok(name, cond, detail=""):
    checked.append(name)
    if not cond:
        problems.append({"name": name, "detail": detail})


MAPS = {
    "products_no_evaluated_decay_data": "products_no_evaluated_decay_data",
    "products_unmapped_to_leakage": "products_unmapped_to_leakage",
    "isomer_fell_back_to_ground":
        "isomer_state_absent_from_decay_library_used_ground",
    "fission_no_yields_to_leakage": "fission_no_yields_to_leakage",
}


def audit(doc: dict) -> list:
    found = []
    ledger = doc["ledger"]
    comp = ledger.get("completeness")
    if comp is None:
        return ["completeness block absent"]
    has_defect = (bool(comp["reaction_channel"]["defects"])
                  or bool(comp["decay_channel"]["defects"])
                  or bool(comp["unquantified"]))
    if comp["status"] != ("incomplete" if has_defect else "complete"):
        found.append(f"status {comp['status']} inconsistent with inventory")
    for ch, tot_key in (("reaction_channel",
                         "total_production_rate_coefficient_per_s"),
                        ("decay_channel",
                         "total_decay_rate_coefficient_per_s")):
        total = comp[ch][tot_key]
        prev = None
        for d in comp[ch]["defects"]:
            cls = d["class"]
            if cls in MAPS:
                m = ledger.get(MAPS[cls]) or {}
                if d["instances"] != len(m):
                    found.append(f"{cls}: instances {d['instances']} != "
                                 f"ledger {len(m)}")
                acc = 0.0
                for v in m.values():
                    acc += v
                if d["rate_coefficient_per_s"] != acc:
                    found.append(f"{cls}: rate mismatch")
                for e in d.get("largest", []):
                    if e["name"] not in m \
                            or m[e["name"]] != e["rate_coefficient_per_s"]:
                        found.append(f"{cls}: largest entry not in ledger")
                if d.get("largest") and len(d["largest"]) != \
                        min(3, len(m)):
                    found.append(f"{cls}: largest not min(3,n)")
                if d["largest"]:
                    vs = [e["rate_coefficient_per_s"] for e in d["largest"]]
                    if vs != sorted(vs, reverse=True):
                        found.append(f"{cls}: largest not sorted")
            elif cls == "fission_yield_products_to_leakage":
                exp = ledger.get("fission_yield_products_to_leakage") or []
                if d["instances"] != len(exp):
                    found.append("fission_yield_products count mismatch")
                acc = 0.0
                for e in exp:
                    acc += e["production_rate_per_parent_s"]
                if d["rate_coefficient_per_s"] != acc:
                    found.append("fission_yield_products rate mismatch")
            elif cls == "decay_branching_sums_off_unity":
                exp = ledger.get("decay_branching_sums_off_unity") or {}
                if d["instances"] != len(exp):
                    found.append("branching_sums count mismatch")
                for e in d.get("entries", []):
                    if exp.get(e["nuclide"]) != e["branching_sum"]:
                        found.append("branching_sums entry mismatch")
            elif cls == "spontaneous_fission_branches_to_leakage":
                if d["instances"] != ledger.get(
                        "spontaneous_fission_branches_to_leakage"):
                    found.append("sf count mismatch")
            elif cls == "decay_daughters_missing":
                if d["instances"] != ledger.get("decay_daughters_missing"):
                    found.append("daughters_missing count mismatch")
            else:
                found.append(f"unknown defect class {cls}")
            if "rate_coefficient_per_s" in d and "fraction_of_channel_flow" in d:
                rate = d["rate_coefficient_per_s"]
                frac = d["fraction_of_channel_flow"]
                if total > 0.0:
                    if frac != rate / total:
                        found.append(f"{cls}: fraction != rate/total")
                elif frac is not None:
                    found.append(f"{cls}: fraction non-null on dead channel")
            if "rate_coefficient_per_s" in d:
                if prev is not None and \
                        d["rate_coefficient_per_s"] > prev:
                    found.append("defects not sorted desc")
                prev = d["rate_coefficient_per_s"]
    seen_unq = {d["class"] for d in comp["unquantified"]}
    known = {"targets_absent_from_decay_library", "bulk_production_dropped",
             "negative_atoms_zeroed",
             "composition_isotopes_absent_from_decay_library",
             "decay_nuclides_from_fallback"}
    if not seen_unq <= known:
        found.append(f"unknown unquantified classes {seen_unq - known}")
    return found


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p61_g5_", dir=ROOT / "target"))
    fx = p58_fixture.build(tmp)
    doc = p61_case.run(ACTINV, p61_case.spec(fx), tmp, "ref")
    found = audit(doc)
    ok("reference document reproduces", not found, json.dumps(found[:6]))

    doc_d = p61_case.run(ACTINV, p61_case.spec_dropping_decay(fx, tmp),
                         tmp, "defect")
    found_d = audit(doc_d)
    ok("defect document reproduces", not found_d,
       json.dumps(found_d[:6]))
    ok("defect doc reports the planted class",
       any(d["class"] == "products_no_evaluated_decay_data"
           for d in doc_d["ledger"]["completeness"]
           ["reaction_channel"]["defects"]))

    mut = json.loads(json.dumps(doc_d))
    mut["ledger"]["completeness"]["status"] = "complete"
    ok("status-flip mutation caught", audit(mut))

    mut = json.loads(json.dumps(doc_d))
    mut["ledger"]["completeness"]["reaction_channel"]["defects"][0][
        "rate_coefficient_per_s"] *= 2.0
    ok("rate mutation caught", audit(mut))

    mut = json.loads(json.dumps(doc_d))
    mut["ledger"]["completeness"]["reaction_channel"]["defects"][0][
        "fraction_of_channel_flow"] = 0.999
    ok("fraction mutation caught", audit(mut))

    mut = json.loads(json.dumps(doc_d))
    mut["ledger"]["completeness"]["reaction_channel"]["defects"][0][
        "largest"][0]["name"] = "Fe56"
    ok("largest-name mutation caught", audit(mut))

    out = {"pass": not problems and not found and not found_d,
           "checks": len(checked), "problems": problems}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass": out["pass"], "checks": len(checked),
                      "problems": len(problems)}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
