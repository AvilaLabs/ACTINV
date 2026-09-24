#!/usr/bin/env python3
"""P44 band-coverage scorer (frozen artifact).

Consumes per-experiment band records (from p44_bands.py) plus the raw
`.exp` measured points, emits per-point outcomes and coverage
aggregates — per experiment, per material family, per experiment type
and pooled, separately for each band type x metric. Never blends band
types.

Frozen scoring rules (ACTINV-P44_PROTOCOL.md):
- measured point: heat_uW_g > 0 row aligned to a cooling step within
  max(2%, 1 s) after unit inference over {s, min, h, d, y};
- band_only: covered iff lo <= measured <= hi (inclusive);
- combined_sigma: covered iff |measured - c| <= sqrt(w^2 + sigma^2)
  with c = band midpoint, w = half-width (inclusive);
- nominal <= 0 -> zero_prediction; missing/failed band ->
  band_unavailable; unmappable valid row -> undefined. All count in
  denominators except nonpositive-time/nonpositive-measurement rows,
  which are excluded before scoring.

Usage:
  p44_band_coverage.py --partition development   (free rescore)
  p44_band_coverage.py --sealed --seal results/g0_p44_seals.json
      verifies sealed identities and writes results/p44_sealed_coverage.json
      exactly once; refuses to overwrite.
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
sys.path.insert(0, str(ROOT / "controls"))
from harness import fispact_io as fio  # noqa: E402

DATA = Path(os.environ.get("ACTINV_DATA", Path.home() / "nuclear-data"))
FNS = DATA / "conderc-fns/fns"
WORK = Path(os.environ.get("ACTINV_P44_WORK", DATA / "p44-work"))
BANDS = WORK / "bands"
RESULT = ROOT / "results/p44_sealed_coverage.json"

SECONDS_PER_YEAR = 365.25 * 86400.0
UNITS = [("s", 1.0), ("min", 60.0), ("h", 3600.0), ("d", 86400.0),
         ("y", SECONDS_PER_YEAR)]
BAND_TYPES = ["first_order", "sampled"]
METRICS = ["band_only", "combined_sigma"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def align(cooling: list[float], t_raw: list[float], heat: list[float]):
    """Infer the time unit and match each row to a cooling step.

    Returns (matched, excluded, unit) where matched[i] = (row, step_idx)
    and excluded carries {row, reason}.
    """
    valid = [(t > 0.0 and h > 0.0) for t, h in zip(t_raw, heat)]

    def mismatch(factor):
        vals = [abs(c - t * factor) / (t * factor)
                for t, ok in zip(t_raw, valid) if ok
                for c in [min(cooling, key=lambda x: abs(x - t * factor))]]
        return sorted(vals)[len(vals) // 2] if vals else math.inf

    unit, factor = min(UNITS, key=lambda u: mismatch(u[1]))
    matched, excluded = [], []
    for i, (t, h) in enumerate(zip(t_raw, heat)):
        if t <= 0.0:
            excluded.append({"row": i, "reason": "nonpositive_time"})
            continue
        if h <= 0.0:
            excluded.append({"row": i, "reason": "nonpositive_measurement"})
            continue
        ts = t * factor
        j = min(range(len(cooling)), key=lambda k: abs(cooling[k] - ts))
        if abs(cooling[j] - ts) <= max(0.02 * ts, 1.0):
            matched.append((i, j))
        else:
            excluded.append({"row": i,
                             "reason": "no_cooling_step_within_2_percent"})
    return matched, excluded, unit


def point_outcome(band: dict | None, measured: float, sigma: float,
                  status: str) -> dict:
    """Score one measured point against one band record entry."""
    if band is None or status != "executed":
        return {"band_only": "not_covered", "combined_sigma": "not_covered",
                "reason": "band_unavailable"}
    nominal, lo, hi = band.get("nominal"), band.get("lo"), band.get("hi")
    if not all(isinstance(v, (int, float)) and math.isfinite(v)
               for v in (lo, hi)) or lo > hi:
        return {"band_only": "not_covered", "combined_sigma": "not_covered",
                "reason": "band_unavailable"}
    if not (isinstance(nominal, (int, float)) and nominal > 0.0):
        return {"band_only": "not_covered", "combined_sigma": "not_covered",
                "reason": "zero_prediction"}
    bo = "covered" if lo <= measured <= hi else "not_covered"
    if not (isinstance(sigma, (int, float)) and sigma > 0.0):
        return {"band_only": bo, "combined_sigma": "n/a",
                "reason": None}
    c, w = (lo + hi) / 2.0, (hi - lo) / 2.0
    half = math.sqrt(w * w + sigma * sigma)
    cs = "covered" if abs(measured - c) <= half else "not_covered"
    return {"band_only": bo, "combined_sigma": cs, "reason": None}


def score_experiment(material: str, experiment: str) -> dict:
    d = FNS / material
    inp = fio.read_i(d / f"TENDL-2017_{experiment}.i")
    meas = fio.read_exp(d / f"{experiment}.exp")
    cooling = [float(t) for t in inp["cooling_cum_s"]]
    t_raw = [float(t) for t in meas["t_raw"]]
    heat = [float(h) for h in meas["heat_uW_g"]]
    sigma = [float(s) for s in meas["sigma_uW_g"]]
    matched, excluded, unit = align(cooling, t_raw, heat)

    rec_path = BANDS / f"{material}__{experiment}.json"
    rec = json.loads(rec_path.read_text()) if rec_path.is_file() else {}

    points = []
    for row, step in matched:
        m, s = heat[row] * 1e-6, sigma[row] * 1e-6  # uW/g -> W/g
        point = {"row": row, "t_s": cooling[step], "measured_W_g": m,
                 "sigma_W_g": s, "bands": {}}
        for bt in BAND_TYPES:
            leg = rec.get(bt) or {}
            bands = leg.get("bands") or []
            band = bands[step] if step < len(bands) else None
            oc = point_outcome(band, m, s, leg.get("status"))
            oc["band"] = band
            point["bands"][bt] = oc
        points.append(point)
    # unmatched valid rows -> undefined
    for ex in excluded:
        if ex["reason"] == "no_cooling_step_within_2_percent":
            points.append({"row": ex["row"], "t_s": None,
                           "measured_W_g": heat[ex["row"]] * 1e-6,
                           "sigma_W_g": sigma[ex["row"]] * 1e-6,
                           "bands": {bt: {"band_only": "undefined",
                                          "combined_sigma": "undefined",
                                          "reason": ex["reason"],
                                          "band": None}
                                     for bt in BAND_TYPES}})
    n_excluded = sum(1 for e in excluded
                     if e["reason"] != "no_cooling_step_within_2_percent")
    return {"material": material, "experiment": experiment,
            "time_unit_inferred": unit, "points": points,
            "excluded_nonpoints": n_excluded,
            "excluded_rows": excluded}


def aggregate(records: list[dict]) -> dict:
    """coverage counts per band_type x metric, grouped four ways."""
    out = {}
    for bt in BAND_TYPES:
        for met in METRICS:
            groups: dict[str, dict] = {}

            def bump(key, gname, outcome):
                g = groups.setdefault((key, gname),
                                      {"covered": 0, "not_covered": 0,
                                       "undefined": 0, "n/a": 0})
                g[outcome if outcome in g else "not_covered"] += 1

            for r in records:
                eid = f"{r['material']}/{r['experiment']}"
                etype = r["experiment"]
                for p in r["points"]:
                    oc = p["bands"][bt][met]
                    for gname, key in (("experiment", eid),
                                       ("material", r["material"]),
                                       ("experiment_type", etype),
                                       ("pooled", "all")):
                        bump(key, gname, oc)
            cov = {}
            for (key, gname), g in groups.items():
                denom = g["covered"] + g["not_covered"] + g["undefined"]
                cov.setdefault(gname, {})[key] = {
                    **g, "denominator": denom,
                    "coverage": (g["covered"] / denom) if denom else None}
            out[f"{bt}.{met}"] = cov
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--partition", choices=["development", "sealed", "all"])
    ap.add_argument("--sealed", action="store_true")
    ap.add_argument("--seal", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    import p44_bands  # frozen driver: shares DEVELOPMENT + enumeration
    all_exps = p44_bands.experiments()
    if args.partition == "development":
        todo = [e for e in all_exps if e in p44_bands.DEVELOPMENT]
    elif args.partition == "sealed":
        todo = [e for e in all_exps if e not in p44_bands.DEVELOPMENT]
    else:
        todo = all_exps

    sealed_identities = None
    if args.sealed:
        if not args.seal or not args.seal.is_file():
            raise SystemExit("sealed scoring requires --seal <seal.json>")
        seal = json.loads(args.seal.read_text())
        sealed_list = sorted(tuple(e) for e in
                             seal["partitions"]["sealed"])
        if sorted(todo) != sealed_list:
            raise SystemExit("partition mismatch vs seal")
        expected = {
            "scorer": sha256(Path(__file__).resolve()),
            "driver": sha256(ROOT / "controls/p44_bands.py"),
            "corpus_reader": sha256(ROOT / "controls/harness/fispact_io.py"),
        }
        for k, v in expected.items():
            if seal["code_sha256"].get(k) != v:
                raise SystemExit(f"sealed {k} hash mismatch")
        sealed_identities = seal
        out = RESULT
        if out.is_file():
            raise SystemExit(f"sealed coverage already scored: {out}")
    else:
        out = args.out or (WORK / f"coverage_{args.partition}.json")

    records = [score_experiment(m, e) for m, e in todo]
    report = {
        "schema": "actinv-p44-coverage-1",
        "partition": args.partition,
        "sealed": bool(args.sealed),
        "n_experiments": len(records),
        "n_points": sum(len(r["points"]) for r in records),
        "n_excluded_nonpoints": sum(r["excluded_nonpoints"] for r in records),
        "aggregates": aggregate(records),
        "experiments": records,
        "caveat": ("points within an experiment share data and measurement "
                   "systematics; pooled coverage is a descriptive statistic, "
                   "not a binomial confidence statement"),
    }
    if sealed_identities:
        report["seal_sha256"] = sha256(args.seal)
    out.write_text(json.dumps(report, indent=1))
    pooled = {k: v["pooled"]["all"] for k, v in report["aggregates"].items()}
    print(json.dumps({"partition": args.partition,
                      "experiments": len(records),
                      "points": report["n_points"],
                      "pooled": pooled}, indent=1))


if __name__ == "__main__":
    main()
