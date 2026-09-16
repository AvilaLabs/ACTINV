#!/usr/bin/env python3
"""P26b G2 independent checker — executed comparator leg.

Imports no production, conversion or leg-executor module.  Re-verifies the
frozen leg contract and the append-only ledger against the on-disk
artifacts:

  * contract input integrity — every parent file recorded in the contract
    (protocol, G0 seals, G1 record, frozen P26 contract, libraries, decay
    files, element library) is re-hashed and must match the recorded
    digest (post-freeze edit detection);
  * case-list independence — the contract's case names are re-derived from
    the frozen P26 contract and must match exactly (regeneration, not copy);
  * unrecorded case loss — every contract case has exactly one ledger row
    per applicable leg; no ledger row names a case outside the contract;
  * gap accounting — contract_gap rows carry missing-isotope lists per arm;
    executability census matches the contract's expected counts and the
    summary's census;
  * artifact digests — recorded per-case artifact sha256s re-verified on
    disk for every executed row;
  * metric re-formation — for every executed product_plus_data row the
    recorded totals are re-formed from the raw out.json / case.stdout
    independently and must match the ledger values;
  * diagnostic discipline — exactly one probe row, diagnostic partition,
    never counted in a leg census;
  * mutation self-test — planted mutations are rejected.

Writes ``results/g2_p26b_check.json``; exit nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
CONTRACT = RESULTS / "g2_p26b_leg_contract.json"
LEDGER = RESULTS / "g2_p26b_leg_ledger.jsonl"
SUMMARY = RESULTS / "g2_p26b_leg.json"
OUT = RESULTS / "g2_p26b_check.json"
P26_CONTRACT = RESULTS / "g2_p26_contract.json"
WORK = Path.home() / "nuclear-data" / "p26b-work" / "g2-run" / "cases"

LEG_STATUSES = {"executed", "executed_with_failures", "contract_gap"}
failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_obj(o) -> str:
    return hashlib.sha256(json.dumps(
        o, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def p26_case_names() -> list[str]:
    p26 = json.loads(P26_CONTRACT.read_text())
    return sorted(c["case"] for wl in p26["workloads"].values()
                  for c in wl["eligible_population"].get("cases", []))


# ---- independent metric re-formation (no executor imports)

ROW_RE = re.compile(r"^-(\d+)(m(\d+)?)?\s")
UNIT_S = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0,
          "y": 31536000.0, "c": 3153600000.0}


def reform_alara_totals(case_dir: Path) -> dict:
    """Re-form ALARA per-time totals from case.stdout — independent of the
    executor's parser (reads section totals only, no isotope identity)."""
    text = (case_dir / "case.stdout").read_text(errors="replace")
    out = {}
    for key, section in (("total_activity_bq_per_g", "Specific Activity [Bq/g]"),
                         ("decay_heat_w_per_g", "Total Decay Heat [W/g]"),
                         ("total_atoms_per_g", "Number Density [atoms/g]")):
        m = re.search(r"\*\*\* %s \*\*\*(.*?)(?=\n\*\*\*|\Z)"
                      % re.escape(section), text, re.S)
        if not m:
            continue
        hm = re.search(r"isotope\s+t_1/2\(s\)\s+pre-irrad\s+(.*?)\n=+",
                       m.group(1), re.S)
        if not hm:
            continue
        times = [0.0] + [float(mm.group(1)) * UNIT_S[mm.group(2)]
                         for mm in re.finditer(r"([\d.eE+-]+)\s+([smhdwyc])",
                                               hm.group(1))]
        tm = re.search(r"^total\s+\S+\s+(.*)$", m.group(1), re.M)
        if not tm:
            continue
        vals = [float(x) for x in tm.group(1).split()][1:]  # drop pre-irrad
        out[key] = dict(zip((f"{t:g}" for t in times), vals))
    return out


def reform_actinv_totals(case_dir: Path, irr_s: float) -> dict:
    o = json.loads((case_dir / "out.json").read_text())
    out = {"total_activity_bq_per_g": {}, "decay_heat_w_per_g": {},
           "total_atoms_per_g": {}}
    for st in o.get("steps", []):
        t = f"{st['t_s'] - irr_s:g}"
        act = st.get("activity_Bq_per_g") or {}
        out["total_activity_bq_per_g"][t] = sum(act.values())
        heat = st.get("heat_W_per_g") or {}
        out["decay_heat_w_per_g"][t] = (heat.get("total") if isinstance(
            heat, dict) else heat)
        out["total_atoms_per_g"][t] = st.get("total_atoms_per_g")
    return out


def check_report(contract: dict, rows: list[dict],
                 summary: dict) -> list[str]:
    f = []

    # case list regenerated independently from the frozen P26 contract
    contract_names = sorted(c["case"] for c in contract["cases"])
    if contract_names != p26_case_names():
        f.append("contract case list differs from frozen P26 case list")

    # per-leg status validity + contract linkage
    allowed_legs = {"identical_data", "product_plus_data"}
    seen = {}
    for r in rows:
        if r["leg"] == "probe":
            continue
        if r["leg"] not in allowed_legs:
            f.append(f"unknown leg {r['leg']}")
            continue
        key = (r["case"], r["leg"])
        if key in seen:
            f.append(f"duplicate ledger row {key}")
        seen[key] = r
        if r["status"] not in LEG_STATUSES:
            f.append(f"{key}: bad status {r['status']}")

    case_map = {c["case"]: c for c in contract["cases"]}
    for c in contract["cases"]:
        for leg in allowed_legs:
            r = seen.get((c["case"], leg))
            if r is None:
                f.append(f"unrecorded case {c['case']} leg {leg}")
                continue
            declared = c["legs"][leg]["status"]
            if declared == "contract_gap":
                if r["status"] != "contract_gap":
                    f.append(f"{c['case']}/{leg}: declared gap but ran")
                elif not r.get("missing"):
                    f.append(f"{c['case']}/{leg}: gap without missing list")
            else:
                if r["status"] == "contract_gap":
                    f.append(f"{c['case']}/{leg}: declared executable "
                             "but ledgered gap")
    for (name, leg) in seen:
        if name not in case_map:
            f.append(f"ledger row for non-contract case {name}")

    # census agreement: the contract records declared executability
    # ("executable"/"contract_gap"); the ledger records realized status
    # ("executed"/"executed_with_failures" for executable cases).  Compare
    # like-for-like, and the summary census verbatim.
    census = {}
    for r in rows:
        if r["leg"] == "probe":
            continue
        census.setdefault(r["leg"], {})[r["status"]] = \
            census.setdefault(r["leg"], {}).get(r["status"], 0) + 1
    for leg in allowed_legs:
        got_leg = census.get(leg, {})
        n_ran = got_leg.get("executed", 0) + \
            got_leg.get("executed_with_failures", 0)
        want = contract["counts"]["legs"].get(leg, {})
        if want.get("executable") != n_ran:
            f.append(f"{leg}.executable: ledger {n_ran} != contract "
                     f"{want.get('executable')}")
        if want.get("contract_gap") != got_leg.get("contract_gap", 0):
            f.append(f"{leg}.contract_gap: ledger "
                     f"{got_leg.get('contract_gap', 0)} != contract "
                     f"{want.get('contract_gap')}")
        if (summary.get("census") or {}).get(leg) != got_leg:
            f.append(f"{leg}: summary census {summary.get('census',{}).get(leg)}"
                     f" != ledger {got_leg}")

    # probe discipline
    probes = [r for r in rows if r["leg"] == "probe"]
    if len(probes) != 1:
        f.append(f"expected exactly 1 diagnostic probe row, found "
                 f"{len(probes)}")
    elif probes[0]["record"].get("partition") != "diagnostic":
        f.append("probe row not in diagnostic partition")
    return f


def check_artifacts_and_metrics(contract: dict, rows: list[dict]) -> None:
    """Digest re-verification + independent metric re-formation for every
    executed product_plus_data row."""
    n_checked = 0
    for r in rows:
        if r["leg"] != "product_plus_data" or "arms" not in r:
            continue
        case_dir = WORK / r["leg"] / r["case"]
        a, c = r["arms"]["alara"], r["arms"]["actinv"]
        arts = (a.get("result") or {}).get("artifacts") or {}
        for fname, key in (("case.stdout", "stdout_sha256"),
                           ("alara.dmp", "dump_sha256"),
                           ("photsrc.out", "photsrc_sha256")):
            p = case_dir / fname
            if key in arts:
                if not p.is_file() or sha256(p) != arts[key]:
                    failures.append(f"{r['case']}: {fname} digest mismatch")
        if a.get("result"):
            if sha256(case_dir / "case.in") != a.get("input_sha256"):
                failures.append(f"{r['case']}: case.in digest mismatch")
            if sha256(case_dir / "flux.txt") != a.get("flux_sha256"):
                failures.append(f"{r['case']}: flux.txt digest mismatch")
        c_arts = (c.get("result") or {}).get("artifacts") or {}
        if c_arts.get("out_json_sha256"):
            p = case_dir / "out.json"
            if not p.is_file() or sha256(p) != c_arts["out_json_sha256"]:
                failures.append(f"{r['case']}: out.json digest mismatch")
        if sha256(case_dir / "spec.json") != c.get("spec_sha256"):
            failures.append(f"{r['case']}: spec.json digest mismatch")

        # metric re-formation (only where both arms parsed)
        if r["status"] != "executed" or not a.get("result") or \
                not c.get("result"):
            continue
        case_meta = next(x for x in contract["cases"]
                         if x["case"] == r["case"])
        ra = reform_alara_totals(case_dir)
        rc = reform_actinv_totals(case_dir, case_meta["irradiation_s"])
        for k in ("total_activity_bq_per_g", "decay_heat_w_per_g",
                  "total_atoms_per_g"):
            for t, rec in (a["result"]["per_time"] or {}).items():
                v_rec, v_new = rec.get(k), (ra.get(k) or {}).get(t)
                if v_rec is None or v_new is None:
                    continue
                if not math.isclose(v_rec, v_new, rel_tol=1e-9):
                    failures.append(
                        f"{r['case']} alara {k}@{t}: ledger {v_rec} "
                        f"!= re-formed {v_new}")
            for t, rec in (c["result"]["per_time"] or {}).items():
                v_rec, v_new = rec.get(k), (rc.get(k) or {}).get(t)
                if v_rec is None or v_new is None:
                    continue
                if not math.isclose(v_rec, v_new, rel_tol=1e-9):
                    failures.append(
                        f"{r['case']} actinv {k}@{t}: ledger {v_rec} "
                        f"!= re-formed {v_new}")
        n_checked += 1
    if n_checked == 0:
        failures.append("no executed product_plus_data rows to re-check")


def main() -> int:
    contract = json.loads(CONTRACT.read_text())
    rows = [json.loads(l) for l in LEDGER.read_text().splitlines()
            if l.strip()]
    summary = json.loads(SUMMARY.read_text())

    # contract input integrity: every recorded parent/library digest re-hashed
    def _file_of(ent):
        p = Path(ent["file"]) if "file" in ent else Path(ent["path"])
        return p if p.is_absolute() else REPO / p
    for name, ent in contract["parents"].items():
        if sha256(_file_of(ent)) != ent["sha256"]:
            failures.append(f"contract parent {name} digest drifted")
    if sha256(REPO / contract["protocol"]["file"]) != \
            contract["protocol"]["sha256"]:
        failures.append("protocol digest drifted")
    libs = contract["libraries"]
    for ext, dig in libs["alara_fendl32c_709"]["files"].items():
        if sha256(Path(libs["alara_fendl32c_709"]["base"] + ext)) != dig:
            failures.append(f"alara library {ext} digest drifted")
    for key in ("actinv_fendl32c_709", "actinv_shipped_tendl2025"):
        if sha256(Path(libs[key]["npz"])) != libs[key]["npz_sha256"]:
            failures.append(f"{key} npz digest drifted")
    for key in ("primary", "fallback"):
        ent = libs["actinv_decay"][key]
        if sha256(Path(ent["path"])) != ent["sha256"]:
            failures.append(f"decay {key} digest drifted")
    if sha256(Path(libs["alara_element_lib"]["path"])) != \
            libs["alara_element_lib"]["sha256"]:
        failures.append("element library digest drifted")
    if sha256(CONTRACT) != summary["contract"]["sha256"]:
        failures.append("contract digest != summary-recorded digest")
    if sha256(LEDGER) != summary["ledger"]["sha256"]:
        failures.append("ledger digest != summary-recorded digest")

    failures.extend(check_report(contract, rows, summary))
    check_artifacts_and_metrics(contract, rows)

    # ---- mutation self-test
    mutations = rejected = 0
    exec_case = next(r["case"] for r in rows
                     if r["leg"] == "product_plus_data"
                     and r["status"] == "executed")
    gap_case = next(r["case"] for r in rows
                    if r["leg"] == "identical_data"
                    and r["status"] == "contract_gap")

    def plant_drop_case(c, _r, _s):
        c["cases"] = [x for x in c["cases"] if x["case"] != exec_case]

    def plant_promote_gap(c, _r, _s):
        next(x for x in c["cases"] if x["case"] == gap_case)[
            "legs"]["identical_data"] = {"status": "executable"}

    def plant_status(c, _r, _s):
        _r[:] = [dict(x, status="executed") if x["case"] == gap_case
                 and x["leg"] == "identical_data" else x for x in _r]

    def plant_census(c, _r, s):
        s["census"]["product_plus_data"]["executed"] = 0

    def plant_missing(c, _r, _s):
        for x in _r:
            if x["case"] == gap_case and x["leg"] == "identical_data":
                x.pop("missing", None)

    def plant_extra(c, _r, _s):
        _r.append({"case": "not_in_contract", "leg": "product_plus_data",
                   "status": "executed"})

    for plant in (plant_drop_case, plant_promote_gap, plant_status,
                  plant_census, plant_missing, plant_extra):
        mc = copy.deepcopy(contract)
        mr = copy.deepcopy(rows)
        ms = copy.deepcopy(summary)
        plant(mc, mr, ms)
        mutations += 1
        if check_report(mc, mr, ms):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p26b-g2-check-1",
        "pass": not failures,
        "failures": failures,
        "ledger_rows": len(rows),
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    OUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
