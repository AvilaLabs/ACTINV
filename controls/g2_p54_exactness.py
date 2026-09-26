#!/usr/bin/env python3
"""P54 G2 exactness — closed-form reference re-derivation of the emitted
clearance arithmetic on (a) hand-synthesized run documents covering every
classification class and boundary, and (b) the solver-driven P53/P54
fixture mesh. The reference implementation is textually independent: plain
dict math + math.erf against the raw input documents.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p53_fixture as fx  # noqa: E402
import p54_fixture as p54fx  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/debug/actinv"))
OUT = ROOT / "results/g2_p54_exactness.json"
EMIT_STEP = 2
LIMITS = json.loads((ROOT / "data/clearance_iaea_2004.json").read_text())[
    "limits"]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([str(BIN)] + args, capture_output=True,
                          text=True, timeout=600)


def load_ndjson(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines()
            if l.strip()]


def table_key(nuc: str) -> str:
    """Independent actinv-name -> RS-G-1.7 label normalisation."""
    i = next((j for j, c in enumerate(nuc) if c.isdigit()), len(nuc))
    elem, rest = nuc[:i], nuc[i:]
    j = next((j for j, c in enumerate(rest) if not c.isdigit()),
             len(rest))
    mass, iso = rest[:j], rest[j:]
    iso = {"": "", "m1": "m", "m2": "m2"}.get(iso, iso)
    return f"{elem}-{mass}{iso}"


def reference(step: dict, confidence: float) -> dict:
    """Closed-form re-derivation of one cell's clearance record."""
    acts = step["activity_Bq_per_g"]
    resps = step["uncertainty"]["responses"]
    S = var = sig_cons = banded_r = unbanded_r = 0.0
    unreg_a = total_a = 0.0
    for nuc, a in acts.items():
        if not (isinstance(a, (int, float)) and a > 0.0):
            continue
        total_a += a
        lim = LIMITS.get(table_key(nuc))
        if lim is None:
            unreg_a += a
            continue
        resp = resps.get(f"activity:{nuc}") or {}
        sigma = resp.get("combined_standard_uncertainty",
                        resp.get("mf33_standard_uncertainty", 0.0)) or 0.0
        r_i, sr_i = a / lim, sigma / lim
        S += r_i
        var += sr_i * sr_i
        sig_cons += sr_i
        if sr_i > 0:
            banded_r += r_i
        else:
            unbanded_r += r_i
    sig_ind = math.sqrt(var)
    if sig_ind == 0.0 and sig_cons == 0.0:
        p = 1.0 if S < 1.0 else 0.0
        cls = "deterministic_clear" if S < 1.0 else "deterministic_fail"
        lo = hi = p
    else:
        cdf = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
        p_ind = cdf((1.0 - S) / max(sig_ind, 1e-300))
        p_cons = cdf((1.0 - S) / max(sig_cons, 1e-300))
        lo, hi = min(p_cons, p_ind), max(p_cons, p_ind)
        cls = ("clears_certified" if lo >= confidence
               else "fails_certified" if hi <= 1.0 - confidence
               else "indeterminate")
    return {"sum_ratio": S, "sigma_sum_independent": sig_ind,
            "sigma_sum_conservative": sig_cons, "p_clear_interval": [lo, hi],
            "classification": cls,
            "unregulated_share": unreg_a / total_a if total_a else 0.0,
            "banded_share": banded_r / S if S else 0.0,
            "unbanded_share": unbanded_r / S if S else 0.0}


def synth_run_doc(activities: dict[str, float],
                  sigmas: dict[str, float]) -> dict:
    """Minimal actinv-run-result-shaped document with planted values."""
    resps = {f"activity:{n}": {
        "nominal": a,
        "mf33_standard_uncertainty": sigmas.get(n, 0.0),
        "confidence_level": 0.95}
        for n, a in activities.items()}
    return {"schema": "actinv-result-1",
            "steps": [{"step": 2, "t_s": 1.0,
                       "activity_Bq_per_g": activities,
                       "uncertainty": {"responses": resps,
                                       "confidence_level": 0.95}}]}


def close(a: float, b: float, tol: float = 1e-12) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def main() -> int:
    checks: dict[str, bool] = {}
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="p54-g2-", dir="target") as d:
        work = Path(d)

        # ---- Leg A: hand-synthesized boundary docs -----------------------
        # A1: S exactly 1.0, no sigma -> deterministic_fail.
        #     Fe-55 (L=1000): a=500 -> r=0.5 ; Co-60 (L=0.1): a=0.05 -> r=0.5
        doc = synth_run_doc({"Fe55": 500.0, "Co60": 0.05}, {})
        dp = work / "a1.json"
        dp.write_text(json.dumps(doc))
        r = run(["clearance", str(dp), "2", str(work / "a1.ndjson")])
        checks["a1_ran"] = r.returncode == 0
        if r.returncode == 0:
            rec = [x for x in load_ndjson(work / "a1.ndjson")
                   if x["record"] == "clearance"][0]
            checks["a1_sum_one"] = rec["sum_ratio"] == 1.0
            checks["a1_det_fail"] = (rec["classification"]
                                     == "deterministic_fail")
            checks["a1_p_zero"] = rec["p_clear_interval"] == [0.0, 0.0]

        # A2: S = 0.5, sigma on Co-60 only -> clears at chosen confidence.
        #     Co-60: a=0.04, sigma=0.004 -> S=0.4+0.1=0.5, sigma_S=0.04
        doc = synth_run_doc({"Fe55": 100.0, "Co60": 0.04},
                            {"Co60": 0.004})
        dp = work / "a2.json"
        dp.write_text(json.dumps(doc))
        r = run(["clearance", str(dp), "2", str(work / "a2.ndjson")])
        checks["a2_ran"] = r.returncode == 0
        if r.returncode == 0:
            rec = [x for x in load_ndjson(work / "a2.ndjson")
                   if x["record"] == "clearance"][0]
            exp_S = 0.1 + 0.4
            exp_s = 0.004 / 0.1
            exp_p = 0.5 * (1 + math.erf(((1 - exp_S) / exp_s)
                                        / math.sqrt(2)))
            checks["a2_sum"] = close(rec["sum_ratio"], exp_S)
            checks["a2_sigma_ind"] = close(
                rec["sigma_sum_independent"], exp_s)
            checks["a2_sigma_cons"] = close(
                rec["sigma_sum_conservative"], exp_s)
            checks["a2_p"] = close(rec["p_clear_interval"][0], exp_p,
                                   1e-6) and close(
                rec["p_clear_interval"][1], exp_p, 1e-6)
            checks["a2_certified"] = (rec["classification"]
                                      == "clears_certified")

        # A3: straddling band -> indeterminate. S=0.9, sigma_S=0.2:
        #     P_lo = Phi(-0.5) ~ 0.309 < 0.95, P_hi same -> indeterminate.
        doc = synth_run_doc({"Co60": 0.09}, {"Co60": 0.02})
        dp = work / "a3.json"
        dp.write_text(json.dumps(doc))
        r = run(["clearance", str(dp), "2", str(work / "a3.ndjson")])
        checks["a3_ran"] = r.returncode == 0
        if r.returncode == 0:
            rec = [x for x in load_ndjson(work / "a3.ndjson")
                   if x["record"] == "clearance"][0]
            checks["a3_indeterminate"] = (rec["classification"]
                                          == "indeterminate")
            exp_lo = 0.5 * (1 + math.erf(0.5 / math.sqrt(2)))
            checks["a3_p_lo"] = close(rec["p_clear_interval"][0],
                                      exp_lo, 1e-6)

        # A4: zero-activity + unlisted nuclide ledgers.
        #     Zz99 (un-normalisable name) and Nb97m (not in table).
        doc = synth_run_doc({"Fe55": 0.0, "Nb97m1": 10.0,
                             "Co60": 0.001}, {"Co60": 0.0005})
        dp = work / "a4.json"
        dp.write_text(json.dumps(doc))
        r = run(["clearance", str(dp), "2", str(work / "a4.ndjson")])
        checks["a4_ran"] = r.returncode == 0
        if r.returncode == 0:
            rec = [x for x in load_ndjson(work / "a4.ndjson")
                   if x["record"] == "clearance"][0]
            checks["a4_zero_skipped"] = all(
                n["nuclide"] != "Fe55" for n in rec["nuclides"])
            checks["a4_unregulated"] = any(
                n["nuclide"] == "Nb97m1" and not n["in_table"]
                for n in rec["nuclides"])
            total_a = 10.0 + 0.001
            checks["a4_unreg_share"] = close(
                rec["coverage"]["unregulated_activity_share"],
                10.0 / total_a)

        # A5: conservative-vs-independent spread — two equal sigma_ratios
        #     (0.1 each) give sigma_cons/sigma_ind = sqrt(2) exactly.
        #     Co-60: a=0.05 L=0.1 -> r=0.5; Mn-54: a=0.05 L=0.1 -> r=0.5.
        doc = synth_run_doc({"Co60": 0.05, "Mn54": 0.05},
                            {"Co60": 0.01, "Mn54": 0.01})
        dp = work / "a5.json"
        dp.write_text(json.dumps(doc))
        r = run(["clearance", str(dp), "2", str(work / "a5.ndjson")])
        checks["a5_ran"] = r.returncode == 0
        if r.returncode == 0:
            rec = [x for x in load_ndjson(work / "a5.ndjson")
                   if x["record"] == "clearance"][0]
            s_ind = math.sqrt(0.1 ** 2 + 0.1 ** 2)
            s_cons = 0.1 + 0.1
            checks["a5_sigmas"] = (close(rec["sigma_sum_independent"],
                                         s_ind)
                                   and close(
                                       rec["sigma_sum_conservative"],
                                       s_cons))
            checks["a5_ratio"] = close(
                rec["sigma_sum_conservative"] / rec["sigma_sum_independent"],
                math.sqrt(2.0))

        # ---- Leg B: solver-driven fixture, exact re-derivation ------------
        fxmap = fx.make_fixture(work)
        flux = work / "flux.ndjson"
        fx.write_flux(flux, [[1.0, 1.0], [1.5, 0.5]])
        spec_path = work / "mesh_spec.json"
        spec_path.write_text(json.dumps(fx.mesh_spec(fxmap, flux)))
        mesh_out = work / "mesh.ndjson"
        cl_out = work / "cl.ndjson"
        r = run(["mesh", str(spec_path), str(mesh_out)])
        checks["fixture_mesh"] = r.returncode == 0
        if r.returncode == 0:
            r = run(["clearance", str(mesh_out), str(EMIT_STEP),
                     str(cl_out)])
            checks["fixture_clearance"] = r.returncode == 0
        if checks.get("fixture_clearance"):
            mesh_recs = {i: json.loads(l) for i, l in
                         enumerate(l for l in
                                   mesh_out.read_text().splitlines()
                                   if l.strip())}
            cells = {r["cell_index"]: r
                     for r in load_ndjson(cl_out)
                     if r["record"] == "clearance"}
            ok = True
            for cell_idx, rec in cells.items():
                raw = [v for v in mesh_recs.values()
                       if v.get("record") == "cell"][cell_idx]
                step = [s for s in raw["result"]["steps"]
                        if s["step"] == EMIT_STEP][0]
                ref = reference(step, 0.95)
                ok &= close(rec["sum_ratio"], ref["sum_ratio"], 1e-14)
                ok &= close(rec["sigma_sum_independent"],
                            ref["sigma_sum_independent"], 1e-14)
                ok &= close(rec["sigma_sum_conservative"],
                            ref["sigma_sum_conservative"], 1e-14)
                ok &= close(rec["p_clear_interval"][0],
                            ref["p_clear_interval"][0], 1e-6)
                ok &= close(rec["p_clear_interval"][1],
                            ref["p_clear_interval"][1], 1e-6)
                ok &= rec["classification"] == ref["classification"]
                ok &= close(
                    rec["coverage"]["unregulated_activity_share"],
                    ref["unregulated_share"], 1e-14)
            checks["fixture_exact"] = ok

    problems.extend(k for k, v in checks.items() if not v)
    result = {"pass": not problems, "checks": checks,
              "problems": problems}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
