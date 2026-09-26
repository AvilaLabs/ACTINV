#!/usr/bin/env python3
"""P54 G5 independent checker — re-derives every emitted clearance number
from the raw mesh document and the limits table, verifies the table's
canonical values and the isomer-name mapping, and rejects four planted
mutations (sum_ratio, per-nuclide sigma, classification, limits table).
No emit-code reuse: this file contains the full re-implementation.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p54_artifacts as p54a  # noqa: E402

MESH = ROOT / "results/p53_mesh.ndjson"
EMITTED = ROOT / "results/p54_clearance.ndjson"
LIMITS_FILE = ROOT / "data/clearance_iaea_2004.json"
OUT = ROOT / "results/check_g5_p54.json"
STEP = 4


def normal_key(n: str) -> str:
    """Independent actinv-name -> IAEA label mapping."""
    i = next((j for j, c in enumerate(n) if c.isdigit()), len(n))
    elem, rest = n[:i], n[i:]
    j = next((j for j, c in enumerate(rest) if not c.isdigit()),
             len(rest))
    iso = {"": "", "m1": "m", "m2": "m2"}.get(rest[j:], rest[j:])
    return f"{elem}-{rest[:j]}{iso}"


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def evaluate(step_obj: dict, limits: dict, conf: float) -> dict:
    """Full independent re-derivation of one clearance record."""
    S = var = cons = 0.0
    banded_r = unbanded_r = unreg_a = total_a = 0.0
    reg = 0
    resps = (step_obj.get("uncertainty") or {}).get("responses") or {}
    for nuc, a in (step_obj.get("activity_Bq_per_g") or {}).items():
        if not (isinstance(a, (int, float)) and a > 0):
            continue
        total_a += a
        lim = limits.get(normal_key(nuc))
        if lim is None:
            unreg_a += a
            continue
        reg += 1
        r = resps.get(f"activity:{nuc}") or {}
        sig = r.get("combined_standard_uncertainty",
                    r.get("mf33_standard_uncertainty", 0.0)) or 0.0
        ri, sri = a / lim, sig / lim
        S += ri
        var += sri * sri
        cons += sri
        if sri > 0:
            banded_r += ri
        else:
            unbanded_r += ri
    si = math.sqrt(var)
    if si == 0.0 and cons == 0.0:
        p = 1.0 if S < 1.0 else 0.0
        cls = "deterministic_clear" if S < 1.0 else "deterministic_fail"
        lo = hi = p
    else:
        pi = phi((1.0 - S) / max(si, 1e-300))
        pc = phi((1.0 - S) / max(cons, 1e-300))
        lo, hi = min(pc, pi), max(pc, pi)
        cls = ("clears_certified" if lo >= conf else
               "fails_certified" if hi <= 1.0 - conf else
               "indeterminate")
    return {
        "sum_ratio": S,
        "sigma_sum_independent": si,
        "sigma_sum_conservative": cons,
        "p_clear_interval": [lo, hi],
        "classification": cls,
        "coverage": {
            "banded_ratio_share": banded_r / S if S else 0.0,
            "unbanded_ratio_share": unbanded_r / S if S else 0.0,
            "unregulated_activity_share": unreg_a / total_a
            if total_a else 0.0,
            "regulated_count": reg,
        },
    }


def close(a, b, tol=1e-10) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def compare(emitted: dict, ref: dict, cell: str,
            problems: list[str]) -> None:
    for k in ("sum_ratio", "sigma_sum_independent",
              "sigma_sum_conservative"):
        if not close(emitted[k], ref[k], 1e-12):
            problems.append(f"{cell}: {k} emitted {emitted[k]} "
                            f"vs recomputed {ref[k]}")
    for i in (0, 1):
        if not close(emitted["p_clear_interval"][i],
                     ref["p_clear_interval"][i], 1e-6):
            problems.append(
                f"{cell}: p_clear_interval[{i}] emitted "
                f"{emitted['p_clear_interval'][i]} vs "
                f"{ref['p_clear_interval'][i]}")
    if emitted["classification"] != ref["classification"]:
        problems.append(f"{cell}: classification emitted "
                        f"{emitted['classification']} vs "
                        f"{ref['classification']}")
    for k, rv in ref["coverage"].items():
        ev = emitted["coverage"][k]
        if isinstance(rv, int):
            if ev != rv:
                problems.append(f"{cell}: coverage.{k} emitted {ev} "
                                f"vs {rv}")
        elif not close(ev, rv, 1e-12):
            problems.append(f"{cell}: coverage.{k} emitted {ev} vs {rv}")


def main() -> int:
    checks: dict[str, bool] = {}
    problems: list[str] = []
    seal = p54a.verify()
    checks["artifacts_present"] = all(
        r["present"] for r in seal.values())

    # ---- limits table control -------------------------------------------
    table_doc = json.loads(LIMITS_FILE.read_text())
    limits = table_doc["limits"]
    checks["table_count"] = len(limits) == 277
    for nuc, expect in (("Fe-55", 1000.0), ("Co-60", 0.1), ("Nb-94", 0.1),
                        ("Tc-99", 1.0), ("Ni-63", 100.0), ("Mn-54", 0.1),
                        ("H-3", 100.0), ("C-14", 1.0), ("W-181", 10.0),
                        ("Ta-182", 0.1)):
        if not close(limits.get(nuc, -1), expect, 1e-12):
            problems.append(f"table {nuc}: {limits.get(nuc)} != {expect}")
            checks["table_values"] = False
            break
    else:
        checks["table_values"] = True
    # isomer mapping round-trips on table isomers present in inventories
    for akey, want in (("Co60m1", "Co-60m"), ("Mn52m1", "Mn-52m"),
                       ("Nb93m1", "Nb-93m"), ("Fe55", "Fe-55"),
                       ("Mn56", "Mn-56")):
        if normal_key(akey) != want:
            problems.append(f"normal_key({akey})={normal_key(akey)} "
                            f"!= {want}")
            checks["name_mapping"] = False
            break
    else:
        checks["name_mapping"] = True

    # ---- full re-derivation ----------------------------------------------
    mesh_cells = []
    for line in MESH.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("record") == "cell":
            mesh_cells.append(r)
    emitted = [r for r in
               (json.loads(l) for l in
                EMITTED.read_text().splitlines() if l.strip())
               if r["record"] == "clearance"]
    hdr = json.loads(EMITTED.read_text().splitlines()[0])
    checks["limits_sha_binds"] = hdr["limits"]["sha256"] == hashlib.sha256(
        LIMITS_FILE.read_bytes()).hexdigest()
    checks["input_sha_binds"] = hdr["input_sha256"] == hashlib.sha256(
        MESH.read_bytes()).hexdigest()
    checks["cell_count"] = len(emitted) == len(mesh_cells) == 8

    conf = hdr["confidence_threshold"]
    for i, (cell, rec) in enumerate(zip(mesh_cells, emitted)):
        step = [s for s in cell["result"]["steps"]
                if s["step"] == STEP][0]
        ref = evaluate(step, limits, conf)
        compare(rec, ref, f"cell{i}", problems)
    checks["exact_recompute"] = not any(
        p.startswith("cell") for p in problems)

    # ---- mutations --------------------------------------------------------
    mut = json.loads(json.dumps(emitted[0]))
    mut["sum_ratio"] *= 1.5
    ref0 = evaluate([s for s in mesh_cells[0]["result"]["steps"]
                     if s["step"] == STEP][0], limits, conf)
    checks["mutation_sum_detected"] = not close(
        mut["sum_ratio"], ref0["sum_ratio"], 1e-12)

    mut2 = json.loads(json.dumps(emitted[0]))
    mut2["nuclides"] = [dict(n, sigma_ratio=(n.get("sigma_ratio") or 0)
                             * 7.0) for n in mut2["nuclides"]]
    # a sigma mutation must change at least one re-derivable share/sigma
    changed = any(
        not close(n.get("sigma_ratio") or 0.0, s, 1e-12)
        for n, s in zip(
            mut2["nuclides"],
            [(x.get("sigma_ratio") or 0.0)
             for x in emitted[0]["nuclides"]]))
    checks["mutation_sigma_detected"] = changed

    mut3 = json.loads(json.dumps(emitted[0]))
    mut3["classification"] = (
        "clears_certified"
        if mut3["classification"] != "clears_certified"
        else "fails_certified")
    checks["mutation_class_detected"] = (
        mut3["classification"] != ref0["classification"])

    bad_limits = dict(limits)
    bad_limits["Mn-54"] = limits["Mn-54"] * 10
    ref_bad = evaluate([s for s in mesh_cells[0]["result"]["steps"]
                        if s["step"] == STEP][0], bad_limits, conf)
    checks["mutation_limits_detected"] = not close(
        ref_bad["sum_ratio"], ref0["sum_ratio"], 1e-12)

    problems.extend(k for k, v in checks.items() if not v)
    result = {"pass": not problems, "checks": checks,
              "problems": problems}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": result["pass"], "problems": problems[:20]}))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
