#!/usr/bin/env python3
"""P45 frozen parity scorer — folds the ledger + produced artifacts
into per-arm output comparisons per protocols/ACTINV-P45_PROTOCOL.md.

- `alara` ↔ `actinv_fendl` (identical data): (alara − actinv)/actinv
  per response per case per cooling time. Contract: |rel| ≤ 0.10 on
  total_activity_bq_per_g at every cooling time and on each shared
  nuclide of the union top-5 activity sets at t=0 (each arm reports
  its own top-5; the union nuclide set is compared where both arms
  report a value). Outcomes: parity_ok | parity_divergence |
  arm_failure | contract_gap.
- `openmc` ↔ `actinv_tendl` (declared data mismatch): descriptive
  only — median/max |rel| per response per time; never a verdict
  input. OpenMC activity derives from output atoms × chain
  half_life_s (ENDF-B-VIII.0 decay — the declared evaluation the
  chain carries), recorded in the batch results.
- Atom-index alignment: OpenMC state i (i=1..n) maps to cooling
  index i−1 (state 0 is the pre-irradiation inventory).

Usage: p45_parity.py [--ledger results/p45_ledger.jsonl] [--out ...]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import g2_p26b_leg as leg  # noqa: E402

LEDGER = ROOT / "results" / "p45_ledger.jsonl"
OUT = ROOT / "results" / "p45_parity.json"
TOLERANCE = 0.10
LN2 = math.log(2.0)


def get_per_time(result: dict) -> dict:
    return (result or {}).get("per_time") or {}


def rel(a, b):
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b


def compare_identical(a_per: dict, c_per: dict) -> dict:
    """ALARA per-time vs ACTINV per-time, identical-data contract."""
    out = {"responses": {}, "top5_union": [],
           "status": "parity_ok", "divergences": []}
    for t in sorted(set(a_per) | set(c_per), key=lambda x: float(x)):
        pa, pc = a_per.get(t, {}), c_per.get(t, {})
        # total_atoms_per_g is intentionally not compared: ALARA's printed
        # total includes the unpopulated substrate atoms while ACTINV reports
        # populated states only (P26b basis mismatch); product_atoms_per_g is
        # the comparable response on both sides.
        for k in ("total_activity_bq_per_g", "product_atoms_per_g",
                  "decay_heat_w_per_g"):
            v = rel(pa.get(k), pc.get(k))
            out["responses"].setdefault(k, {})[t] = v
            if k == "total_activity_bq_per_g" and v is not None \
                    and abs(v) > TOLERANCE:
                out["divergences"].append(
                    {"response": k, "t": t, "rel": v})
    times = sorted(a_per.keys(), key=float)
    if times:
        t0 = times[0]
        a5 = {d["nuclide"]: d["activity_bq_per_g"] for d in
              a_per.get(t0, {}).get("top5_nuclides_by_activity") or []}
        c5 = {d["nuclide"]: d["activity_bq_per_g"] for d in
              c_per.get(t0, {}).get("top5_nuclides_by_activity") or []}
        for nuc in sorted(set(a5) | set(c5)):
            v = rel(a5.get(nuc), c5.get(nuc))
            out["top5_union"].append(
                {"nuclide": nuc, "alara": a5.get(nuc),
                 "actinv": c5.get(nuc), "rel": v,
                 "in_both": nuc in a5 and nuc in c5})
            if v is not None and abs(v) > TOLERANCE:
                out["divergences"].append(
                    {"response": f"top5:{nuc}", "t": t0, "rel": v})
    if out["divergences"]:
        out["status"] = "parity_divergence"
    return out


def openmc_activity(atoms: dict, hl: dict) -> list:
    """atoms {nuc: [per-state]} -> total activity Bq/g per state."""
    n_states = len(next(iter(atoms.values()))) if atoms else 0
    tot = [0.0] * n_states
    per_nuc = {}
    for nuc, series in (atoms or {}).items():
        t_half = hl.get(nuc)
        if not t_half or series is None:
            continue
        lam = LN2 / t_half
        vals = [lam * v for v in series]
        per_nuc[nuc] = vals
        for i, v in enumerate(vals):
            tot[i] += v
    return tot, per_nuc


def score_ledger(ledger_rows: list[dict]) -> dict:
    cases = defaultdict(dict)
    batches = {}
    for r in ledger_rows:
        wl, cn, arm = r["workload"], r["case"], r["arm"]
        if cn == "_batch":
            batches[f"{wl}|{arm}"] = r["arms"].get(arm, {})
            continue
        cases[(wl, cn)][arm] = r["arms"].get(arm, {})

    hl = {}
    for b in batches.values():
        res = (b.get("results") or {})
        hl.update(res.get("half_life_s") or {})

    per_case = {}
    pool = defaultdict(lambda: defaultdict(list))
    for (wl, cn), arms in sorted(cases.items()):
        rec = {"workload": wl}
        # identical-data leg: alara vs actinv_fendl
        a, cf = arms.get("alara", {}), arms.get("actinv_fendl", {})
        if a.get("status") == "executed" and \
                cf.get("status") == "executed":
            cmp_ = compare_identical(get_per_time(a.get("result")),
                                     get_per_time(cf.get("result")))
            rec["identical"] = cmp_
            for k, ts in cmp_["responses"].items():
                for t, v in ts.items():
                    if v is not None:
                        pool[f"identical:{k}"][t].append(v)
        else:
            # absent = neither arm produced anything; arm_failure = one
            # side exists but its comparator didn't (or failed)
            rec["identical"] = {"status":
                                "arm_failure" if (a or cf) else "absent",
                                "alara": a.get("status"),
                                "actinv_fendl": cf.get("status")}

        # data-mismatch leg: openmc vs actinv_tendl (descriptive)
        om, ct = arms.get("openmc", {}), arms.get("actinv_tendl", {})
        if om.get("status") == "executed" and \
                ct.get("status") == "executed":
            atoms = om.get("atoms_atom_per_cm3") or {}
            om_tot, _ = openmc_activity(atoms, hl)
            c_per = get_per_time(ct.get("result"))
            times = sorted(c_per.keys(), key=float)
            desc = {}
            for i, t in enumerate(times):
                j = i + 1          # state 0 = pre-irradiation inventory
                if j < len(om_tot):
                    v = rel(om_tot[j],
                            c_per[t].get("total_activity_bq_per_g"))
                    desc[t] = v
                    if v is not None:
                        pool["mismatch:total_activity"][t].append(v)
            rec["mismatch"] = {"openmc_total_activity_rel": desc}
        elif om.get("status"):
            rec["mismatch"] = {"status": "arm_failure"}
        per_case[f"{wl}|{cn}"] = rec

    pooled = {k: {t: {"n": len(vs),
                      "median_rel": sorted(vs)[len(vs) // 2],
                      "max_abs_rel": max(abs(x) for x in vs)}
                  for t, vs in ts.items()}
              for k, ts in pool.items()}
    n_div = sum(1 for r in per_case.values()
                if (r.get("identical") or {}).get("status")
                == "parity_divergence")
    n_ok = sum(1 for r in per_case.values()
               if (r.get("identical") or {}).get("status")
               == "parity_ok")
    return {"schema": "actinv-p45-parity-1",
            "tolerance": TOLERANCE,
            "per_case": per_case,
            "identical_leg": {"parity_ok": n_ok,
                              "parity_divergence": n_div,
                              "pooled": pooled}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    rows = [json.loads(l) for l in
            Path(args.ledger).read_text().splitlines() if l.strip()]
    out = score_ledger(rows)
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({"parity_ok": out["identical_leg"]["parity_ok"],
                      "parity_divergence":
                          out["identical_leg"]["parity_divergence"],
                      "cases": len(out["per_case"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
