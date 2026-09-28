#!/usr/bin/env python3
"""D2b — banded-corpus calibration: fit the model-remainder scale on top
of propagated bands.

The deterministic fitter ``d2_empirical_calibrate.py`` fits one u that
covers *all* residual error (propagated + remainder). When a spec already
carries an ``uncertainty`` block the propagated channels are inside the
emitted band, so the remainder must be fit *on top of* the band —
otherwise the unmodeled term double-counts propagated width.

This producer consumes the sealed-band corpus under
``$ACTINV_P44_WORK/bands`` (written by ``p44_bands.py``: per-experiment
``first_order`` and ``sampled`` legs at confidence_level = 0.6827, i.e.
z = 1 one-sigma ``normal_interval`` half-widths), joins each cooling-step
band to the measured ``.exp`` points under
``$ACTINV_DATA/conderc-fns`` with the same unit inference and 2%
alignment tolerance as the deterministic fitter, and per band type:

- measures baseline coverage with u = 0 (propagated-only bands);
- fits the remainder u per material family and pooled — the smallest u
  whose fit-partition coverage at z = 1 reaches 0.6827 (bisection);
- reports held-out coverage at z = 1 and z = 2 for both baseline and
  remainder-widened bands;
- emits the remainder as a consumable ``actinv-unmodeled-table-1``.

Coverage model (log space, per aligned point): covered iff

    |ln(nominal / measured)| <= sqrt( z^2 * (h_ln^2 + u^2) + s_meas^2 )

where h_ln = (ln(hi) - ln(lo)) / 2 is the band's propagated 1-sigma
half-width and s_meas = sigma / measured is the measurement's relative
sigma. With u = 0 this reduces to |resid| <= z * sqrt(h_ln^2 + s_meas^2).

Holdout discipline is identical to the deterministic fitter: a
deterministic SHA split assigns each experiment to ``fit`` or
``holdout``; u is fit on the fit partition only.

Emits ``actinv-banded-calibration-1``:

  coverage tables per band type with and without the fitted remainder,
  the consumable remainder table, per-material u values, partition
  manifest, and sha-256 bindings of every band record and ``.exp``
  consumed.

Usage:
  python controls/d2_banded_calibrate.py \
      --bands ~/nuclear-data/p44-work/bands \
      --out results/d2_banded_calibration.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("ACTINV_DATA", Path.home() / "nuclear-data"))
FNS = DATA / "conderc-fns/fns"
WORK = Path(os.environ.get("ACTINV_P44_WORK", DATA / "p44-work"))
BANDS = WORK / "bands"
sys.path.insert(0, str(ROOT / "controls"))
from harness import fispact_io as fio  # noqa: E402
from d2_empirical_calibrate import align, partition, sha256  # noqa: E402

Z1 = 1.0   # emitted bands are at confidence_level = 0.6827 (z = 1)
Z2 = 2.0
TARGET_Z1 = 0.6827
TARGET_Z2 = 0.95
UNIT_FACTORS = (1.0, 60.0, 3600.0)  # s / min / h — same inference set as d2


def load_bands(bands_dir: Path) -> dict:
    """{eid: {material, experiment, sha, legs: {band_type: [step rows]}}}."""
    out = {}
    for path in sorted(bands_dir.glob("*.json")):
        d = json.loads(path.read_text())
        material, experiment = d["material"], d["experiment"]
        legs = {}
        for bt in ("first_order", "sampled"):
            leg = d.get(bt) or {}
            rows = leg.get("bands") or []
            if leg.get("status") == "executed" and rows:
                legs[bt] = rows
        out[f"{material}/{experiment}"] = {
            "material": material, "experiment": experiment,
            "record_sha256": sha256(path), "legs": legs,
        }
    return out


def join(entry: dict, leg_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Join one band leg's cooling-step bands to the .exp measured points.

    Returns (points, excluded). Each point carries ln_residual (band
    nominal vs measured), h_ln (propagated half-width), and the
    measurement's ln-space sigma.
    """
    cooling = [float(b["t_s"]) for b in leg_rows]
    exp_path = FNS / entry["material"] / f"{entry['experiment']}.exp"
    if not exp_path.is_file():
        return [], []
    meas = fio.read_exp(exp_path)
    best = None
    for factor in UNIT_FACTORS:
        mt, ex = align(cooling, [t * factor for t in meas["t_raw"]],
                       meas["heat_uW_g"], meas["sigma_uW_g"])
        if best is None or len(mt) > len(best[0]):
            best = (mt, ex, factor)
    matched, excluded, _unit = best
    points = []
    for m in matched:
        b = leg_rows[m["step"]]
        nominal, lo, hi = b.get("nominal"), b.get("lo"), b.get("hi")
        if not (nominal and nominal > 0 and lo and lo > 0 and hi and hi > lo):
            continue
        points.append({
            "t_s": m["t_s"], "measured_W_g": m["measured_W_g"],
            "sigma_W_g": m["sigma_W_g"], "nominal_W_g": nominal,
            "ln_residual": math.log(nominal / m["measured_W_g"]),
            "h_ln": 0.5 * (math.log(hi) - math.log(lo)),
            "ln_sigma_meas": m["sigma_W_g"] / m["measured_W_g"],
        })
    return points, excluded


def coverage(points: list[dict], u: float, z: float) -> float | None:
    """Fraction covered by z * sqrt(h_ln^2 + u^2) widened with meas sigma."""
    if not points:
        return None
    n = 0
    for p in points:
        half = math.sqrt(z * z * (p["h_ln"] ** 2 + u * u)
                         + p["ln_sigma_meas"] ** 2)
        if abs(p["ln_residual"]) <= half:
            n += 1
    return n / len(points)


def u_at_target(points: list[dict], target: float, z: float) -> float:
    """Smallest u whose coverage at z reaches target on these points."""
    lo, hi = 0.0, 50.0
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        c = coverage(points, mid, z)
        if c is not None and c >= target:
            hi = mid
        else:
            lo = mid
    return round(hi, 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bands", type=Path, default=BANDS)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    corpus = load_bands(args.bands)
    if not corpus:
        raise SystemExit(f"no banded records under {args.bands}")

    exp_sha = {}
    for e in corpus.values():
        p = FNS / e["material"] / f"{e['experiment']}.exp"
        if p.is_file():
            exp_sha[f"{e['material']}/{e['experiment']}"] = sha256(p)

    cert = {
        "schema": "actinv-banded-calibration-1",
        "bands_dir": str(args.bands),
        "confidence_level_of_emitted_bands": 0.6827,
        "coverage_model": "covered iff |ln(nominal/measured)| <= "
                          "sqrt(z^2*(h_ln^2+u^2)+ln_sigma_meas^2)",
        "partition_rule": "sha256('material/experiment')[0] < 128 -> fit",
        "fit_target": TARGET_Z1,
        "n_experiments": len(corpus),
        "band_types": {},
    }

    for bt in ("first_order", "sampled"):
        fit_pts, hold_pts, per_mat = [], [], {}
        experiments = {}
        for eid, e in sorted(corpus.items()):
            rows = e["legs"].get(bt)
            if not rows:
                experiments[eid] = {"partition": partition(e["material"], e["experiment"]),
                                    "status": "band_unavailable"}
                continue
            pts, excluded = join(e, rows)
            part = partition(e["material"], e["experiment"])
            experiments[eid] = {"partition": part, "n_points": len(pts),
                                "n_excluded": len(excluded),
                                "record_sha256": e["record_sha256"]}
            (fit_pts if part == "fit" else hold_pts).extend(pts)
            per_mat.setdefault(e["material"], []).append(
                (part, pts))

        # per-material u: fit partition points of that material only
        u_by_mat = {}
        for mat in sorted(per_mat):
            mfit = [p for part, pts in per_mat[mat] if part == "fit" for p in pts]
            if mfit:
                u_by_mat[mat] = u_at_target(mfit, TARGET_Z1, Z1)
        u_pooled = u_at_target(fit_pts, TARGET_Z1, Z1)

        # per-material application: each holdout point widened with its own
        # material's fitted u (falling back to pooled u for unseen materials)
        # — this is what `uncertainty.unmodeled_table` resolves at run time.
        cov_table = {}
        tail_mats: dict[str, int] = {}
        for z in (Z1, Z2):
            hit = 0
            for eid, e in sorted(corpus.items()):
                rows = e["legs"].get(bt)
                if not rows or partition(e["material"], e["experiment"]) != "holdout":
                    continue
                u = u_by_mat.get(e["material"], u_pooled)
                for p in join(e, rows)[0]:
                    half = math.sqrt(z * z * (p["h_ln"] ** 2 + u * u)
                                     + p["ln_sigma_meas"] ** 2)
                    covered = abs(p["ln_residual"]) <= half
                    hit += covered
                    if z == Z2 and not covered:
                        tail_mats[e["material"]] = tail_mats.get(e["material"], 0) + 1
            cov_table[f"z{z:g}"] = round(hit / len(hold_pts), 4) if hold_pts else None

        def cov_report(pts, u):
            return {f"z{z:g}": round(c, 4) for z in (Z1, Z2)
                    if (c := coverage(pts, u, z)) is not None}

        cert["band_types"][bt] = {
            "n_fit_points": len(fit_pts),
            "n_holdout_points": len(hold_pts),
            "u_pooled_remainder": u_pooled,
            "u_by_material_fit_partition": u_by_mat,
            "coverage_holdout_no_remainder": cov_report(hold_pts, 0.0),
            "coverage_fit_no_remainder": cov_report(fit_pts, 0.0),
            "coverage_holdout_pooled_remainder": cov_report(hold_pts, u_pooled),
            "coverage_fit_pooled_remainder": cov_report(fit_pts, u_pooled),
            "coverage_holdout_per_material_table": cov_table,
            "uncovered_at_z2_by_material": dict(sorted(
                tail_mats.items(), key=lambda kv: -kv[1])),
            "u_needed_holdout_z1_at_95": u_at_target(hold_pts, 0.95, Z1)
            if hold_pts else None,
            "experiments": experiments,
        }

    fo = cert["band_types"]["first_order"]
    cert["unmodeled_table"] = {
        "schema": "actinv-unmodeled-table-1",
        "default": fo["u_pooled_remainder"],
        "per_material": fo["u_by_material_fit_partition"],
        "calibrated_against": {
            "bands_dir": str(args.bands),
            "band_type": "first_order",
            "channels": ["cross_section_mf33", "decay_constants"],
        },
    }
    cert["input_sha256"] = {
        "band_records": {eid: e["record_sha256"]
                         for eid, e in sorted(corpus.items())},
        "exp_files": exp_sha,
    }

    text = json.dumps(cert, indent=1, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
