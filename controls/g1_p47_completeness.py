#!/usr/bin/env python3
"""P47 G1 — dose-table + propagated-band completeness: per-step dose
and MC rel-std present, statepoint shas verify byte-identical to the
seal, all 8 perturbation solves present.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p47_artifacts as p47a  # noqa: E402

OUT = ROOT / "results/g1_p47_completeness.json"
SEAL = ROOT / "results/g0_p47_seals.json"


def check(name, ok, detail=None):
    d = {"probe": name, "pass": bool(ok)}
    if detail is not None:
        d["detail"] = detail
    return d


def main() -> int:
    seal = json.loads(SEAL.read_text())
    checks = []

    # artifact identity re-verified under seal
    cur = p47a.verify()
    bad = {n: r["actual_sha256"] for n, r in cur.items()
           if not r["present"]
           or r["actual_sha256"] !=
           seal["artifacts"][n]["sha256"]}
    checks.append(check("artifact_identity", not bad, bad))

    # dose table completeness — per step dose + rel-std present,
    # statepoint sha bound
    dose = json.loads(
        (ROOT / "results/p32_dose.json").read_text())
    dt = dose["cooling_step_doses"]
    ok = True
    det = {}
    for step in ("2", "3"):
        s = dt.get(step) or {}
        sp_name = Path(s.get("statepoint", {}).get("path", "")).name
        sealed = seal["artifacts"].get(
            f"dose_statepoint_step{step}", {})
        ok = (ok and s.get("dose_Gy_h") is not None
              and s.get("relative_std") is not None
              and s.get("relative_std") < 0.05
              and s.get("statepoint", {}).get("sha256")
              == sealed.get("sha256"))
        det[step] = {"dose_Gy_h": s.get("dose_Gy_h"),
                     "rel_std": s.get("relative_std")}
    checks.append(check("dose_table_complete", ok, det))

    # tally band completeness — per-cell relative_spread per step
    tally = json.loads(
        (ROOT / "results/p32_tally_error.json").read_text())
    per_cell = tally["per_cell"]
    n_cells = sum(len(v) for v in per_cell.values())
    spreads = [v["activity_Bq_per_g"]["relative_spread"]
               for step_cells in per_cell.values()
               for v in step_cells.values()]
    checks.append(check(
        "band_complete",
        tally["samples"] == 8 and n_cells == 64 * 3
        and all(0 < s < 0.2 for s in spreads),
        {"cells_steps": n_cells,
         "max_spread": max(spreads)}))

    out = {"spec": "actinv-p47-g1-1",
           "probes": checks,
           "all_pass": all(c["pass"] for c in checks)}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "probes": {c["probe"]: c["pass"]
                                 for c in checks}}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
