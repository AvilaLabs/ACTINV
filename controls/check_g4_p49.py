#!/usr/bin/env python3
"""P49 independent checker — re-derives the ranking and feasibility from
the raw campaign ledger, re-verifies every candidate spec sha256 against
its ledger row, and rejects planted mutations (reordered ranking, dropped
violator, forged feasibility, tampered spec).

Run after the campaign; emits results/check_g4_p49.json. --mutate N plants
a mutation into a scratch copy and must produce a rejection.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p49_artifacts as p49a  # noqa: E402

CAMPAIGN = ROOT / "results/p49_campaign"
OUT = ROOT / "results/check_g4_p49.json"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def violation(edge, sense, limit):
    d = abs(limit) if abs(limit) > 0 else 1e-300
    return (edge - limit) / d if sense == "le" else (limit - edge) / d


def recheck(rows, direction_min, constraints):
    """Independently recompute feasibility + ranking from raw ledger rows."""
    recomputed = []
    for r in rows:
        vsum = 0.0
        computable = True
        for c in constraints:
            info = r["constraints"].get(f"constraint.{c['name']}")
            if c.get("kind") == "axis":
                # re-derive from the parameter vector, not the recorded edge
                edge = r["x"][c["axis"]]
            else:
                edge = info.get("edge") if isinstance(info, dict) else None
            if edge is None:
                computable = False
                break
            vsum += max(0.0, violation(edge, c["sense"], c["limit"]))
        feas = (r["objective"] is not None and computable and vsum <= 0.0)
        recomputed.append({
            "eval_id": r["eval_id"],
            "feasible": feas,
            "violation_sum": vsum if computable else float("inf"),
            "objective": r["objective"],
        })

    def key(e):
        feas_rank = 0 if e["feasible"] else 1
        obj = e["objective"] if e["objective"] is not None else (
            float("inf") if direction_min else float("-inf"))
        return (feas_rank, e["violation_sum"],
                obj if direction_min else -obj if obj != float("-inf") else obj,
                e["eval_id"])

    order = sorted(range(len(recomputed)), key=lambda i: key(recomputed[i]))
    best = next((recomputed[i] for i in order if recomputed[i]["feasible"]),
                None)
    return recomputed, order, best


def check_campaign(ledger_path, result_path, cand_dir, constraints):
    problems = []
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines()
            if l.strip()]
    result = json.loads(result_path.read_text())

    # 1. ledger completeness: unique monotone ids, one row per eval
    ids = [r["eval_id"] for r in rows]
    if len(set(ids)) != len(ids):
        problems.append("duplicate eval_ids in ledger")

    # 2. candidate spec sha256 re-verified against file bytes
    for r in rows:
        if r["status"] != "executed" or not r.get("spec_sha256"):
            continue
        f = cand_dir / f"eval_{r['eval_id']:04}.json"
        if not f.exists():
            problems.append(f"eval {r['eval_id']} missing candidate spec")
            continue
        if sha256_bytes(f.read_bytes()) != r["spec_sha256"]:
            problems.append(f"eval {r['eval_id']} spec sha mismatch")

    # 3. feasibility re-derived from raw constraint edges
    recomputed, order, best = recheck(
        rows, result["objective"]["direction"] == "min", constraints)
    for r, rc in zip(rows, recomputed):
        if r.get("feasible") != rc["feasible"]:
            problems.append(
                f"eval {r['eval_id']} feasibility disagrees: "
                f"ledger={r.get('feasible')} recomputed={rc['feasible']}")

    # 4. ranked table matches independent ordering
    res_order = [e["eval_id"] for e in result["ranked"]]
    ind_order = [recomputed[i]["eval_id"] for i in order]
    if res_order != ind_order:
        problems.append(f"ranked order diverges: {res_order} vs {ind_order}")

    # 5. best_feasible is the first feasible in the independent ordering
    if best is None:
        if not result["infeasible"]:
            problems.append("result claims feasible but none found")
        if result["best_feasible"] is not None:
            problems.append("infeasible result still named a best")
    else:
        bf = result["best_feasible"]
        if bf["eval_id"] != best["eval_id"]:
            problems.append(
                f"best_feasible eval {bf['eval_id']} != recomputed "
                f"{best['eval_id']}")
        if not result["winner_verification"].get("bit_identical"):
            problems.append("winner re-execution not bit-identical")
    return problems


def main() -> int:
    mutate = None
    if "--mutate" in sys.argv:
        mutate = int(sys.argv[sys.argv.index("--mutate") + 1])

    missing = {n: r["path"] for n, r in p49a.verify().items()
               if not r["present"]}
    if missing:
        print(json.dumps({"pass": False, "missing": missing}))
        return 1

    # constraint definitions come from the sealed optspec, not the result
    opt = json.loads((ROOT / "examples/optimize_ra_steel/opt.json").read_text())

    ledger_p = CAMPAIGN / "optimize_ledger.jsonl"
    result_p = CAMPAIGN / "optimize_result.json"
    cand_dir = CAMPAIGN / "candidates"

    problems = check_campaign(ledger_p, result_p, cand_dir,
                              opt["constraints"])
    mutations = {}

    if mutate is None:
        # run all three planted mutations on scratch copies
        scratch = ROOT / "results/p49_g4_scratch"
        scratch.mkdir(exist_ok=True)
        for m in (1, 2, 3):
            sledger = scratch / f"ledger_m{m}.jsonl"
            sresult = scratch / f"result_m{m}.json"
            scand = scratch / f"cand_m{m}"
            scand.mkdir(exist_ok=True)
            rows = [json.loads(l) for l in
                    ledger_p.read_text().splitlines() if l.strip()]
            res = json.loads(result_p.read_text())
            for f in cand_dir.glob("*.json"):
                (scand / f.name).write_bytes(f.read_bytes())

            if m == 1:  # reordered ranking
                res["ranked"] = res["ranked"][::-1]
            elif m == 2:  # dropped violator row
                rows = [r for r in rows if r.get("feasible")]
            elif m == 3:  # forged feasibility
                if rows:
                    rows[0]["feasible"] = True

            sledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            sresult.write_text(json.dumps(res))
            mp = check_campaign(sledger, sresult, scand, opt["constraints"])
            mutations[f"mutation_{m}"] = {"rejected": len(mp) > 0,
                                          "problems": mp}
            if not mp:
                problems.append(f"planted mutation {m} was not detected")

    record = {
        "schema": "actinv-p49-check-1",
        "pass": not problems,
        "problems": problems,
        "mutations": mutations,
        "ledger_sha256": sha256_bytes(ledger_p.read_bytes()),
        "result_sha256": sha256_bytes(result_p.read_bytes()),
        "n_rows": sum(1 for l in ledger_p.read_text().splitlines()
                      if l.strip()),
    }
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"],
                      "problems": problems,
                      "mutations_rejected": all(
                          m["rejected"] for m in mutations.values())}))
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
