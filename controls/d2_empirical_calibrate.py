#!/usr/bin/env python3
"""D2 — empirical calibration fit on a committed deterministic corpus.

Reads a directory of actinv run results (schema produced by the FNS
conversion harness: material/experiment/steps[].t_s/heat_uW_g), joins
each step to the measured ``.exp`` points under
``$ACTINV_DATA/conderc-fns/fns/<Mat>/<experiment>.exp``, and fits the
declared unmodeled-error scale *u* per material family — the field
P63 emits through ``uncertainty.unmodeled_relative``.

Holdout discipline: a deterministic SHA-split assigns each experiment
to ``fit`` or ``holdout``. The fit uses only fit-partition residuals;
the certificate reports held-out coverage of the widened band at the
P63 z multiplier (1.0 ≙ ~68%) and 2.0 (≙ ~95%). A fit that only looks
good on its own residuals is not a calibration.

Emits ``actinv-calibration-fit-1``:

  coverage(u) per partition, u* per material and pooled, the split
  manifest, per-material residual statistics, and sha-256 bindings of
  every input.

Usage:
  python controls/d2_empirical_calibrate.py \
      --results results/fns_tendl_decay --out results/d2_calibration.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__import__("os").environ.get("ACTINV_DATA", Path.home() / "nuclear-data"))
FNS = DATA / "conderc-fns/fns"
sys.path.insert(0, str(ROOT / "controls"))
from harness import fispact_io as fio  # noqa: E402

Z1 = 1.0   # ~68% one-sided band multiplier used by the emitted bands
Z2 = 2.0   # ~95%
TOL = 0.02  # time alignment tolerance (p44 convention)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def align(cooling: list[float], t_raw: list[float], heat: list[float],
          sigma: list[float]):
    """Match measured points to cooling steps (p44's unit/tolerance rules)."""
    matched, excluded = [], []
    n = len(cooling)
    for row, (t, h, s) in enumerate(zip(t_raw, heat, sigma)):
        if not (t > 0 and h > 0):
            excluded.append({"row": row, "reason": "nonpositive_point"})
            continue
        best, br = None, None
        for i, c in enumerate(cooling):
            if c <= 0:
                continue
            r = abs(c - t) / t
            if br is None or r < br:
                best, br = i, r
        if best is None or br > TOL:
            # the .exp time may be cumulative-from-EOI already; try exact
            excluded.append({"row": row, "t": t, "reason": "no_step_within_2pct",
                             "best_rel": br})
            continue
        matched.append({"row": row, "t_s": t, "step": best,
                        "measured_W_g": h * 1e-6, "sigma_W_g": s * 1e-6})
    return matched, excluded


def partition(material: str, experiment: str) -> str:
    """Deterministic 1:1 split; SHA of the experiment id decides."""
    b = hashlib.sha256(f"{material}/{experiment}".encode()).digest()
    return "fit" if b[0] < 128 else "holdout"


def collect(results_dir: Path):
    """Return {eid: {material, experiment, points:[{t_s,measured,pred,sigma}]}}."""
    out = {}
    for path in sorted(results_dir.glob("*.json")):
        d = json.loads(path.read_text())
        material, experiment = d["material"], d["experiment"]
        exp_path = FNS / material / f"{experiment}.exp"
        if not exp_path.is_file():
            continue
        meas = fio.read_exp(exp_path)
        # result t_s is cumulative from t=0; .exp times are since end-of-
        # irradiation. EOI = end of the last nonzero-flux schedule step.
        sched = d["spec"].get("schedule", [])
        n_irr = sum(1 for s in sched if float(s.get("flux", 0) or 0) > 0)
        t_eoi = d["steps"][n_irr - 1]["t_s"] if n_irr else 0.0
        cooling = [s["t_s"] - t_eoi for s in d["steps"]]
        heat = [h * 1e-6 for h in d["heat_uW_g"]]  # µW/g → W/g
        # .exp time units vary (s/min); pick the unit minimising mismatch
        best = None
        for factor in (1.0, 60.0, 3600.0):
            mt, ex = align(cooling, [t * factor for t in meas["t_raw"]],
                           meas["heat_uW_g"], meas["sigma_uW_g"])
            if best is None or len(mt) > len(best[0]):
                best = (mt, ex, factor)
        matched, excluded, unit = best
        pts = []
        for m in matched:
            idx = m["step"] - n_irr  # heat_uW_g is aligned to cooling steps
            pred = heat[idx] if 0 <= idx < len(heat) else None
            if not (pred and pred > 0):
                continue
            pts.append({"t_s": m["t_s"], "measured_W_g": m["measured_W_g"],
                        "sigma_W_g": m["sigma_W_g"], "pred_W_g": pred,
                        "ln_residual": math.log(pred / m["measured_W_g"]),
                        "ln_sigma_meas": m["sigma_W_g"] / m["measured_W_g"]})
        out[f"{material}/{experiment}"] = {
            "material": material, "experiment": experiment,
            "result_sha256": sha256(path),
            "partition": partition(material, experiment),
            "points": pts, "n_excluded": len(excluded)}
    return out


def rms(xs: list[float]) -> float:
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else 0.0


def fit_u(residuals: list[float]) -> float:
    """Per-family u* = rms of ln(pred/meas) — the empirical relative scale."""
    return rms(residuals)


def coverage(points: list[dict], u: float, z: float) -> float | None:
    """Held-out coverage of pred·e^{±z·u} widened with measurement sigma."""
    ok = n = 0
    for p in points:
        half = math.sqrt((z * u) ** 2 + p["ln_sigma_meas"] ** 2)
        n += 1
        if abs(p["ln_residual"]) <= half:
            ok += 1
    return ok / n if n else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--target", type=float, default=0.68)
    args = ap.parse_args()

    corpus = collect(args.results)
    if not corpus:
        raise SystemExit("no experiments joined to measured data")

    fit_pts, hold_pts, per_mat = [], [], {}
    for e in corpus.values():
        dst = fit_pts if e["partition"] == "fit" else hold_pts
        dst.extend(e["points"])
        per_mat.setdefault(e["material"], []).extend(e["points"])

    u_pooled = fit_u([p["ln_residual"] for p in fit_pts])
    u_by_mat = {}
    for mat in sorted(per_mat):
        fit_r = [p["ln_residual"] for p in corpus_points_of(corpus, mat, "fit")]
        u_by_mat[mat] = round(fit_u(fit_r), 4) if fit_r else None

    def cov_report(pts, u):
        return {f"z{z:g}": round(c, 4) for z in (Z1, Z2)
                if (c := coverage(pts, u, z)) is not None}

    def u_at_target(pts, target, z):
        """Smallest u whose held-out coverage at z reaches target."""
        lo, hi = 0.0, 50.0
        for _ in range(64):
            mid = 0.5 * (lo + hi)
            c = coverage(pts, mid, z)
            if c is not None and c >= target:
                hi = mid
            else:
                lo = mid
        return round(hi, 4)

    # the consumable artifact: `uncertainty.unmodeled_relative` values per
    # material family, fit only on the fit partition — set the value for
    # the experiment family being solved.
    table = {"schema": "actinv-unmodeled-table-1",
             "default": round(u_pooled, 4),
             "per_material": {k: v for k, v in u_by_mat.items() if v is not None}}

    cert = {
        "schema": "actinv-calibration-fit-1",
        "results_dir": str(args.results),
        "unmodeled_table": table,
        "n_experiments": len(corpus),
        "n_fit_experiments": sum(1 for e in corpus.values() if e["partition"] == "fit"),
        "n_fit_points": len(fit_pts),
        "n_holdout_points": len(hold_pts),
        "u_pooled": round(u_pooled, 4),
        "u_holdout_at_target_z1": u_at_target(hold_pts, args.target, Z1),
        "u_holdout_at_target_z2": u_at_target(hold_pts, 0.95, Z2),
        "u_by_material_fit_points": u_by_mat,
        "coverage_holdout_pooled_u": cov_report(hold_pts, u_pooled),
        "coverage_fit_pooled_u": cov_report(fit_pts, u_pooled),
        "experiments": {eid: {"partition": e["partition"],
                              "n_points": len(e["points"]),
                              "n_excluded": e["n_excluded"],
                              "result_sha256": e["result_sha256"]}
                        for eid, e in sorted(corpus.items())},
    }
    text = json.dumps(cert, indent=1) + "\n"
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0


def corpus_points_of(corpus: dict, material: str, part: str) -> list[dict]:
    pts = []
    for e in corpus.values():
        if e["material"] == material and e["partition"] == part:
            pts.extend(e["points"])
    return pts


if __name__ == "__main__":
    raise SystemExit(main())
