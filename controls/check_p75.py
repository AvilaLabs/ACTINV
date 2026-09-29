#!/usr/bin/env python3
"""P75 checker: derives every gate verdict of protocols/ACTINV-P75_PROTOCOL.md from the checkpoint.

    python3 controls/check_p75.py [--out results/p75_verdict.json]

Inputs are sha-bound: the case manifest (must equal the frozen hash), the extraction checkpoint,
and the solver binary that produced it.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "target" / "p75"
MANIFEST = WORK / "cases.json"
CHECKPOINT = WORK / "runs.jsonl"
BINARY = WORK / "actinv"
PROTOCOL = ROOT / "protocols" / "ACTINV-P75_PROTOCOL.md"
FROZEN_MANIFEST = "0d9b3f5a54e992222153c8d54f863efddab71e2ed0abba28486d73ad9da241f9"
FROZEN_PROTOCOL = "0203ed0ab8160326bb74dbd2a0c47faa39750b68cfbb4a021d02ff75014e2359"

MIX_WEIGHTS = {"fns": 0.2, "flat": 0.5, "maxwell": 0.3}
AMPLITUDES = [1.0e10, 1.0e12, 1.0e13, 1.0e14, 1.0e15]
R_VESSEL_FLUENCE = 3.2e20
G1_TOL = 1e-9
G2_GO, G2_STRONG = 1e-2, 1e-3
G0_YIELD_TOL = 1e-9
G0_COMPOSE_TOL = 1e-9
COVERAGE_TOL = 1e-6
SLIDER_CAP = 1e-3
EPS = 2.220446049250313e-16


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(a: float, b: float) -> float | None:
    """|a - b| / |b|; None when the reference is zero."""
    if b == 0:
        return None if a == 0 else math.inf
    return abs(a - b) / abs(b)


def load():
    cases = {c["id"]: c for c in json.loads(MANIFEST.read_text())["cases"]}
    rows = {}
    for line in CHECKPOINT.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["id"] in rows:
            raise SystemExit(f"duplicate checkpoint row {r['id']}")
        rows[r["id"]] = r
    return cases, rows


# ---------------------------------------------------------------- G0: yields and coverage
def yield_table(cases, rows):
    table, zero, spread_in, spread_x = {}, set(), 0.0, 0.0
    worst_x = None
    for cid, c in cases.items():
        if c["arm"] != "Y":
            continue
        y = rows[cid]["yields"]
        zero |= set(y["active_without_photon_record"])
        for n, rec in y["yields"].items():
            spread_in = max(spread_in, rec["spread"])
            if n not in table:
                table[n] = rec
                continue
            ref = table[n]
            for x, z in zip(ref["power"] + ref["photons"], rec["power"] + rec["photons"]):
                if x != 0 or z != 0:
                    d = abs(x - z) / max(abs(x), abs(z))
                    if d > spread_x:
                        spread_x, worst_x = d, n
    zero -= set(table)  # a nuclide with a photon record anywhere is not a zero emitter
    return table, zero, {"max_within_run_spread": spread_in, "max_cross_run_spread": spread_x,
                         "worst_cross_run_nuclide": worst_x}


def compose(activity: dict, table: dict, zero: set):
    """Group power and rate from per-nuclide activity; uncovered share of activity."""
    p, r = [0.0] * 24, [0.0] * 24
    uncovered, total = 0.0, 0.0
    for n, a in activity.items():
        total += a
        rec = table.get(n)
        if rec is None:
            if n not in zero:
                uncovered += a
            continue
        for g in range(24):
            p[g] += a * rec["power"][g]
            r[g] += a * rec["photons"][g]
    return p, r, (uncovered / total if total > 0 else 0.0)


def g0_compose_check(cases, rows, table, zero):
    worst, worst_at = 0.0, None
    for cid, c in cases.items():
        if c["arm"] != "Y":
            continue
        for si, sg in enumerate(rows[cid]["yields"]["step_groups"]):
            p, _, _ = compose(sg["activity"], table, zero)
            tot = sum(sg["power_W_g"])
            for g, ref in enumerate(sg["power_W_g"]):
                if tot > 0 and ref >= 1e-6 * tot:
                    d = abs(p[g] - ref) / ref
                    if d > worst:
                        worst, worst_at = d, f"{cid} step {si} group {g}"
    return worst, worst_at


# ---------------------------------------------------------------- response vectors
class Resp:
    """Compared-step responses of one run (or a linear combination of runs)."""

    def __init__(self, steps):
        self.steps = steps  # list of dicts: activity{}, heat, heat_parts, fluence, t_s, kappa

    @classmethod
    def of(cls, row):
        out = []
        for s in row["data"]["steps"]:
            out.append({"activity": dict(s["activity"]), "heat": s["heat"]["total"],
                        "fluence": s["fluence_n_cm2"], "t_s": s["t_s"], "index": s["index"],
                        "kappa": 0.0})
        return cls(out)


def affine(b: Resp, u: Resp, a: float) -> Resp:
    """L = B + a (U - B), per nuclide; heat through totals with its cancellation factor."""
    out = []
    for sb, su in zip(b.steps, u.steps):
        assert sb["index"] == su["index"]
        act = {}
        for n in set(sb["activity"]) | set(su["activity"]):
            vb, vu = sb["activity"].get(n, 0.0), su["activity"].get(n, 0.0)
            act[n] = vb + a * (vu - vb)
        diff = su["heat"] - sb["heat"]
        kappa = abs(sb["heat"]) / abs(diff) if diff != 0 else (math.inf if sb["heat"] else 0.0)
        out.append({"activity": act, "heat": sb["heat"] + a * diff, "fluence": None,
                    "t_s": su["t_s"], "index": su["index"], "kappa": kappa})
    return Resp(out)


def combine(terms) -> Resp:
    """Σ w_i (U_i − B_i), per nuclide and heat."""
    base = terms[0][1]
    out = []
    for k in range(len(base.steps)):
        act, heat, kappa_num = defaultdict(float), 0.0, 0.0
        for w, u, b in terms:
            su, sb = u.steps[k], b.steps[k]
            for n in set(su["activity"]) | set(sb["activity"]):
                act[n] += w * (su["activity"].get(n, 0.0) - sb["activity"].get(n, 0.0))
            heat += w * (su["heat"] - sb["heat"])
            kappa_num += abs(w * sb["heat"])
        out.append({"activity": dict(act), "heat": heat, "fluence": None,
                    "t_s": base.steps[k]["t_s"], "index": base.steps[k]["index"],
                    "kappa": kappa_num / abs(heat) if heat else 0.0})
    return Resp(out)


def subtract(u: Resp, b: Resp) -> Resp:
    return combine([(1.0, u, b)])


def metrics(pred: dict, ref: dict, table, zero):
    """Errors of one predicted step against one reference step."""
    a_pred, a_ref = sum(pred["activity"].values()), sum(ref["activity"].values())
    m = {"e_A": rel(a_pred, a_ref), "e_H": rel(pred["heat"], ref["heat"]),
         "heat_kappa": max(pred.get("kappa", 0.0), ref.get("kappa", 0.0))}
    pp, rp, unc_p = compose(pred["activity"], table, zero)
    pr, rr, unc_r = compose(ref["activity"], table, zero)
    m["uncovered_share"] = max(unc_p, unc_r)
    if m["uncovered_share"] <= COVERAGE_TOL and sum(pr) > 0:
        tot = sum(pr)
        m["e_P"] = sum(abs(x - y) for x, y in zip(pp, pr)) / tot
        m["e_N"] = sum(abs(x - y) for x, y in zip(rp, rr)) / sum(rr) if sum(rr) > 0 else None
        gm = [abs(x - y) / y for x, y in zip(pp, pr) if y >= 1e-3 * tot]
        m["e_Gmax"] = max(gm) if gm else None
    else:
        m["e_P"] = m["e_N"] = m["e_Gmax"] = None
        m["photon_unavailable"] = True
    vals = [v for v in (m["e_A"], m["e_H"], m["e_P"]) if v is not None]
    m["e_agg"] = max(vals) if vals else None
    # nuclide responsible for the largest absolute activity discrepancy
    worst_n, worst_d = None, 0.0
    for n in set(pred["activity"]) | set(ref["activity"]):
        d = abs(pred["activity"].get(n, 0.0) - ref["activity"].get(n, 0.0))
        if d > worst_d:
            worst_n, worst_d = n, d
    m["worst_nuclide"] = worst_n
    m["worst_nuclide_share_of_ref_activity"] = worst_d / a_ref if a_ref > 0 else None
    return m


def per_nuclide_dev(pred: dict, ref: dict) -> float:
    tot = sum(ref["activity"].values())
    worst = 0.0
    for n, v in ref["activity"].items():
        if tot > 0 and v >= 1e-6 * tot:
            worst = max(worst, abs(pred["activity"].get(n, 0.0) - v) / v)
    return worst


def fmt(x):
    return None if x is None else float(f"{x:.4g}")


# ---------------------------------------------------------------- main
def main(out_path: Path) -> int:
    cases, rows = load()
    verdict = {"protocol": "ACTINV-P75", "inputs": {
        "protocol_sha256": sha(PROTOCOL), "manifest_sha256": sha(MANIFEST),
        "checkpoint_sha256": sha(CHECKPOINT), "binary_sha256": sha(BINARY)}}
    g0 = {}
    g0["protocol_hash_matches"] = verdict["inputs"]["protocol_sha256"] == FROZEN_PROTOCOL
    g0["manifest_hash_matches"] = verdict["inputs"]["manifest_sha256"] == FROZEN_MANIFEST
    missing = sorted(set(cases) - set(rows))
    failed = sorted(i for i, r in rows.items() if r["returncode"] != 0)
    g0["runs_expected"], g0["runs_present"] = len(cases), len(rows)
    g0["runs_missing"], g0["runs_failed"] = len(missing), failed[:20]
    g0["wall_s_total"] = fmt(sum(r["wall_s"] for r in rows.values()))
    if missing or failed or not (g0["protocol_hash_matches"] and g0["manifest_hash_matches"]):
        g0["pass"] = False
        verdict["G0"] = g0
        verdict["stop"] = "G0 failed: incomplete, failed, or unbound inputs"
        out_path.write_text(json.dumps(verdict, indent=1, sort_keys=True))
        print(json.dumps(g0, indent=1))
        return 1
    table, zero, spreads = yield_table(cases, rows)
    g0.update({k: fmt(v) if isinstance(v, float) else v for k, v in spreads.items()})
    g0["yield_nuclides"], g0["zero_photon_nuclides"] = len(table), len(zero)
    worst_c, worst_c_at = g0_compose_check(cases, rows, table, zero)
    g0["compose_max_rel"], g0["compose_worst_at"] = fmt(worst_c), worst_c_at
    g0["yields_pass"] = max(spreads["max_within_run_spread"], spreads["max_cross_run_spread"]) <= G0_YIELD_TOL
    g0["compose_pass"] = worst_c <= G0_COMPOSE_TOL
    g0["pass"] = g0["yields_pass"] and g0["compose_pass"]
    verdict["G0"] = g0

    R = {cid: Resp.of(rows[cid]) for cid in rows if rows[cid].get("data")}
    materials = sorted({c["material"] for c in cases.values() if c["arm"] == "U"})
    spectra = ["fns", "flat", "maxwell", "mix"]
    schedules = sorted({c["schedule"] for c in cases.values() if c["arm"] == "U"})
    coverage_unavailable = 0

    # ---------------- G1
    g1_rows, g1_worst, g1_nuc = [], 0.0, 0.0
    for m in materials:
        for s in schedules:
            b = R[f"B__{m}__{s}"]
            lhs = subtract(R[f"U__{m}__mix__{s}"], b)
            rhs = combine([(w, R[f"U__{m}__{p}__{s}"], b) for p, w in MIX_WEIGHTS.items()])
            for sl, sr in zip(lhs.steps, rhs.steps):
                mt = metrics(sr, sl, table, zero)
                e = mt["e_agg"]
                g1_worst = max(g1_worst, e or 0.0)
                g1_nuc = max(g1_nuc, per_nuclide_dev(sr, sl))
                g1_rows.append({"kind": "spectral", "material": m, "schedule": s, "step": sl["index"],
                                "e_agg": fmt(e), "heat_kappa": fmt(mt["heat_kappa"])})
    import importlib.util
    spec = importlib.util.spec_from_file_location("p75run", ROOT / "controls" / "p75_linear_response.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    for mix in runner.MIXTURES:
        comp = runner.MATERIALS[mix]
        for p in ("fns", "mix"):
            lhs = subtract(R[f"U__{mix}__{p}__s1_1y"], R[f"B__{mix}__s1_1y"])
            terms = []
            for e, w in comp.items():
                if e in ("Fe", "W", "Cu", "Co"):
                    key = e.lower()
                    terms.append((w / 100.0, R[f"U__{key}__{p}__s1_1y"], R[f"B__{key}__s1_1y"]))
                else:
                    terms.append((w / 100.0, R[f"CU__{e}__{p}__s1_1y"], R[f"CB__{e}__s1_1y"]))
            rhs = combine(terms)
            for sl, sr in zip(lhs.steps, rhs.steps):
                mt = metrics(sr, sl, table, zero)
                e = mt["e_agg"]
                g1_worst = max(g1_worst, e or 0.0)
                g1_nuc = max(g1_nuc, per_nuclide_dev(sr, sl))
                g1_rows.append({"kind": "composition", "material": mix, "spectrum": p, "step": sl["index"],
                                "e_agg": fmt(e), "heat_kappa": fmt(mt["heat_kappa"])})
    verdict["G1"] = {"pass": g1_worst <= G1_TOL, "max_e_agg": fmt(g1_worst),
                     "max_per_nuclide_dev_descriptive": fmt(g1_nuc),
                     "worst_rows": sorted(g1_rows, key=lambda r: -(r["e_agg"] or 0))[:10],
                     "n_compared_steps": len(g1_rows)}

    # ---------------- G2 + G3
    g2_cases = []
    for m in materials:
        for p in spectra:
            for s in schedules:
                b, u = R[f"B__{m}__{s}"], R[f"U__{m}__{p}__{s}"]
                for a in AMPLITUDES:
                    tid = f"T__{m}__{p}__{s}__{a:.0e}"
                    t = R[tid]
                    lpred = affine(b, u, a)
                    worst, worst_step, worst_m = -1.0, None, None
                    per_step = []
                    for sl, st in zip(lpred.steps, t.steps):
                        mt = metrics(sl, st, table, zero)
                        coverage_unavailable += bool(mt.get("photon_unavailable"))
                        per_step.append({"step": st["index"], "t_s": st["t_s"],
                                         **{k: fmt(v) if isinstance(v, float) else v for k, v in mt.items()}})
                        if (mt["e_agg"] or 0.0) > worst:
                            worst, worst_step, worst_m = mt["e_agg"] or 0.0, st["index"], mt
                    led = rows[tid]["data"]["ledger"]
                    fluence = t.steps[0]["fluence"]
                    g2_cases.append({
                        "id": tid, "material": m, "spectrum": p, "schedule": s, "amplitude": a,
                        "fluence": fluence, "in_R_vessel": fluence <= R_VESSEL_FLUENCE,
                        "e_agg_max": worst, "worst_step": worst_step,
                        "worst_metric": max(("e_A", "e_H", "e_P"),
                                            key=lambda k: worst_m.get(k) or 0.0) if worst_m else None,
                        "worst_nuclide": worst_m.get("worst_nuclide") if worst_m else None,
                        "heat_kappa_at_worst": worst_m.get("heat_kappa") if worst_m else None,
                        "tau_p": led.get("max_product_optical_depth"),
                        "tau_p_nuclide": led.get("max_product_optical_depth_nuclide"),
                        "tau_bulk": led.get("max_burnup_optical_depth"),
                        "tau_bulk_nuclide": led.get("max_burnup_nuclide"),
                        "steps": per_step})
    inR = [c for c in g2_cases if c["in_R_vessel"]]
    worst_inR = max(inR, key=lambda c: c["e_agg_max"])
    envelope = {}
    for m in materials:
        for p in spectra:
            cs = [c for c in g2_cases if c["material"] == m and c["spectrum"] == p]
            envelope[f"{m}/{p}"] = {
                "max_fluence_all_steps_le_1e-3": fmt(max([c["fluence"] for c in cs if c["e_agg_max"] <= G2_STRONG],
                                                        default=0.0)),
                "min_fluence_failing_1e-3": fmt(min([c["fluence"] for c in cs if c["e_agg_max"] > G2_STRONG],
                                                    default=math.inf)),
                "max_fluence_all_steps_le_1e-2": fmt(max([c["fluence"] for c in cs if c["e_agg_max"] <= G2_GO],
                                                        default=0.0)),
                "min_fluence_failing_1e-2": fmt(min([c["fluence"] for c in cs if c["e_agg_max"] > G2_GO],
                                                    default=math.inf)),
            }
    verdict["G2"] = {
        "GO": all(c["e_agg_max"] <= G2_GO for c in inR),
        "STRONG": all(c["e_agg_max"] <= G2_STRONG for c in inR),
        "n_cases": len(g2_cases), "n_in_R_vessel": len(inR),
        "n_in_R_vessel_over_1e-3": sum(c["e_agg_max"] > G2_STRONG for c in inR),
        "n_in_R_vessel_over_1e-2": sum(c["e_agg_max"] > G2_GO for c in inR),
        "worst_in_R_vessel": {k: v for k, v in worst_inR.items() if k != "steps"},
        "photon_steps_unavailable": coverage_unavailable,
        "envelope": envelope,
    }

    def spearman(xs, ys):
        def ranks(v):
            order = sorted(range(len(v)), key=lambda i: v[i])
            r = [0.0] * len(v)
            i = 0
            while i < len(order):
                j = i
                while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                    j += 1
                for k in range(i, j + 1):
                    r[order[k]] = (i + j) / 2.0
                i = j + 1
            return r
        rx, ry = ranks(xs), ranks(ys)
        n = len(xs)
        mx, my = sum(rx) / n, sum(ry) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
        sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
        sy = math.sqrt(sum((b - my) ** 2 for b in ry))
        return cov / (sx * sy) if sx and sy else None

    ev = [c["e_agg_max"] for c in g2_cases]
    verdict["G3"] = {
        "n_cases": len(g2_cases),
        "n_e_agg_gt_tau_p": sum(c["e_agg_max"] > (c["tau_p"] or 0.0) for c in g2_cases),
        "n_e_agg_gt_tau_p_plus_tau_bulk": sum(
            c["e_agg_max"] > (c["tau_p"] or 0.0) + (c["tau_bulk"] or 0.0) for c in g2_cases),
        "spearman_e_agg_vs_tau_p": fmt(spearman([c["tau_p"] or 0.0 for c in g2_cases], ev)),
        "spearman_e_agg_vs_tau_bulk": fmt(spearman([c["tau_bulk"] or 0.0 for c in g2_cases], ev)),
        "spearman_e_agg_vs_fluence": fmt(spearman([c["fluence"] for c in g2_cases], ev)),
        "max_ratio_e_agg_over_tau_p": fmt(max((c["e_agg_max"] / c["tau_p"] for c in g2_cases
                                              if c["tau_p"]), default=None)),
        "tau_p_driver_nuclides": sorted({c["tau_p_nuclide"] for c in g2_cases if c["tau_p_nuclide"]}),
    }

    # ---------------- G4: shipped P70 slider
    pairs, violations = [], []
    for m in materials:
        for p in spectra:
            for s in schedules:
                for a0 in (1.0e10, 1.0e13):
                    base_row = rows[f"S__{m}__{p}__{s}__{a0:.0e}"]
                    base = base_row["data"]
                    tau_p = base["ledger"].get("max_product_optical_depth")
                    tau_p = math.inf if tau_p is None else tau_p
                    for a in AMPLITUDES:
                        if a == a0:
                            continue
                        v = a / a0
                        bound = tau_p * abs(v - 1.0)
                        if not bound <= SLIDER_CAP:
                            pairs.append({"certified": False})
                            continue
                        tgt = rows[f"S__{m}__{p}__{s}__{a:.0e}"]["data"]
                        tru = rows[f"T__{m}__{p}__{s}__{a:.0e}"]["data"]
                        worst = {"e_A": 0.0, "e_H": 0.0, "e_atoms": 0.0, "e_P_unscaled": 0.0,
                                 "e_A_vs_T": 0.0, "e_H_vs_T": 0.0}
                        for sb, st, sT in zip(base["steps"], tgt["steps"], tru["steps"]):
                            eA = rel(v * sb["activity_total"], st["activity_total"]) or 0.0
                            eH = rel(v * sb["heat"]["total"], st["heat"]["total"]) or 0.0
                            eX = rel(v * sb["total_atoms_per_g"], st["total_atoms_per_g"]) or 0.0
                            pb, _, ub = compose(sb["activity"], table, zero)
                            pt, _, ut = compose(st["activity"], table, zero)
                            eP = (sum(abs(x - y) for x, y in zip(pb, pt)) / sum(pt)
                                  if sum(pt) > 0 and max(ub, ut) <= COVERAGE_TOL else 0.0)
                            worst["e_A"] = max(worst["e_A"], eA)
                            worst["e_H"] = max(worst["e_H"], eH)
                            worst["e_atoms"] = max(worst["e_atoms"], eX)
                            worst["e_P_unscaled"] = max(worst["e_P_unscaled"], eP)
                            worst["e_A_vs_T"] = max(worst["e_A_vs_T"],
                                                    rel(v * sb["activity_total"], sT["activity_total"]) or 0.0)
                            worst["e_H_vs_T"] = max(worst["e_H_vs_T"],
                                                    rel(v * sb["heat"]["total"], sT["heat"]["total"]) or 0.0)
                        rec = {"certified": True, "material": m, "spectrum": p, "schedule": s, "a0": a0,
                               "a": a, "v": v, "tau_p": tau_p, "tau_p_nuclide":
                               base["ledger"].get("max_product_optical_depth_nuclide"), "bound": bound,
                               "base_mode": base["mode"], "target_mode": tgt["mode"],
                               **{k: fmt(x) for k, x in worst.items()}}
                        pairs.append(rec)
                        if worst["e_A"] > bound or worst["e_H"] > bound:
                            rec["excess_ratio"] = fmt(max(worst["e_A"], worst["e_H"]) / bound if bound > 0
                                                      else math.inf)
                            violations.append(rec)
    cert = [q for q in pairs if q["certified"]]
    verdict["G4"] = {
        "pass": not violations,
        "n_pairs": len(pairs), "n_certified": len(cert), "n_violations": len(violations),
        "violations_top": sorted(violations, key=lambda r: -(r.get("excess_ratio") or 0))[:25],
        "max_e_atoms_certified_descriptive": fmt(max((q["e_atoms"] for q in cert), default=0.0)),
        "max_e_P_unscaled_certified_descriptive": fmt(max((q["e_P_unscaled"] for q in cert), default=0.0)),
    }

    summary = {
        "G0": g0["pass"], "G1": verdict["G1"]["pass"], "G2_GO": verdict["G2"]["GO"],
        "G2_STRONG": verdict["G2"]["STRONG"], "G4": verdict["G4"]["pass"]}
    verdict["summary"] = summary
    verdict["g2_cases"] = [{k: (fmt(v) if isinstance(v, float) else v) for k, v in c.items() if k != "steps"}
                           for c in g2_cases]
    out_path.write_text(json.dumps(verdict, indent=1, sort_keys=True, allow_nan=True))
    (WORK / "g2_steps.json").write_text(json.dumps(g2_cases, sort_keys=True, default=str))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else ROOT / "results" / "p75_verdict.json"
    sys.exit(main(out))
