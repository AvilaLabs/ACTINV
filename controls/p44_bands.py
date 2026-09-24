#!/usr/bin/env python3
"""P44 band-production driver (frozen artifact).

Builds the frozen spec + study per FNS experiment, runs both legs under
the bounded scope, and emits one band record per experiment at
`~/nuclear-data/p44-work/bands/<material>__<exp>.json`.

Legs (frozen by ACTINV-P44_PROTOCOL.md):
- first_order: spec `uncertainty` block, channels cross_section_mf33 +
  decay_constants, response heat.total, confidence_level 0.6827; band =
  emitted normal_interval per post-shutdown step.
- sampled: one-case study, robustness {samples: 32, seed: 287444822,
  channels cross_section_mf33 + decay_constants only}, band = central
  quantile interval [Q(0.15865), Q(0.84135)] (type-7 linear
  interpolation) of sample values per post-shutdown step.

Idempotent: an experiment whose band record already exists and carries
both leg statuses is skipped; a missing/failed leg is (re)produced.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from harness import fispact_io as fio  # noqa: E402

DATA = Path(os.environ.get("ACTINV_DATA", Path.home() / "nuclear-data"))
FNS = DATA / "conderc-fns/fns"
WORK = Path(os.environ.get("ACTINV_P44_WORK", DATA / "p44-work"))
BANDS = WORK / "bands"
ACTINV = ROOT / "target/release/actinv"

LIBRARY = ROOT / "actinv-data/v1.1.0/activation/tendl-2025-patched-neutron-709g.npz"
LIBRARY_SHA = "fb13c16c703c71a862ff82c78bc7fbd0761902264cf45efb97aa1a7e5e43b48d"
COVARIANCE = DATA / "p43-work/p43.cov.npz"
COVARIANCE_SHA = "18b8afaf239d22436d9e4cda1fd3f3d8347b7eda1a76ba2b8cd07af0cbe5348a"
DECAY_PRIMARY = DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
DECAY_FALLBACK = DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat"

LEVEL = 0.6827
TAIL = (1.0 - LEVEL) / 2.0          # 0.15865
SAMPLES = 32
SEED = 287444822

DEVELOPMENT = {
    ("Fe", "1996exp_5min"), ("Fe", "1996exp_7hour"), ("Fe", "2000exp_5min"),
    ("Ni", "2000exp_5min"), ("Ti", "2000exp_5min"), ("Al", "2000exp_5min"),
    ("W", "2000exp_5min"), ("V", "2000exp_5min"), ("Cu", "2000exp_5min"),
    ("SS316", "2000exp_5min"),
}


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def experiments() -> list[tuple[str, str]]:
    out = []
    for d in sorted(FNS.iterdir()):
        if not d.is_dir():
            continue
        for i in sorted(d.glob("TENDL-2017_*.i")):
            out.append((d.name, i.stem.replace("TENDL-2017_", "")))
    return out


def read_flux(path: Path) -> list[float]:
    values = []
    for line in path.read_text().splitlines():
        try:
            values.extend(float(v) for v in line.split())
        except ValueError:
            break
    if len(values) < 709:
        raise ValueError(f"{path.name}: {len(values)} flux groups < 709")
    return values[:709]


def spec_for(material: str, experiment: str) -> tuple[dict, dict]:
    d = FNS / material
    inp = fio.read_i(d / f"TENDL-2017_{experiment}.i")
    flux = read_flux(d / f"{experiment}_fluxes")
    cooling = inp["cooling_cum_s"]
    sched = [{"dt": f"{inp['t_irr_s']:.17e} s", "flux": 1.0},
             {"dt": f"{cooling[0]:.17e} s", "flux": 0.0}]
    sched += [{"dt": f"{cooling[i] - cooling[i-1]:.17e} s", "flux": 0.0}
              for i in range(1, len(cooling))]
    spec = {
        "spec": "actinv-spec-1",
        "title": f"P44 FNS {material} {experiment}",
        "projectile": "neutron",
        "library": {"path": str(LIBRARY), "sha256": LIBRARY_SHA},
        "decay": {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)},
        "material": {"mass_g": float(inp["mass_kg"] * 1000.0),
                     "basis": "wt_percent", "composition": inp["elements"]},
        "spectrum": {"structure": "fispact-709", "flux_per_group": flux,
                     "total": float(inp["flux_total"]), "descending": True},
        "schedule": sched,
        "options": {"mode": "auto", "prune": "rate",
                    "bmin_atoms_per_g": 1.0e-8, "temperature_K": 293.6,
                    "outputs": ["inventory", "activity", "heat",
                                "ledger", "certificate"]},
        "fission_yields": {"files": [], "energy": "spectrum_average"},
        "uncertainty": {
            "covariance": {"path": str(COVARIANCE), "sha256": COVARIANCE_SHA},
            "responses": ["heat.total"],
            "channels": ["cross_section_mf33", "decay_constants"],
            "confidence_level": LEVEL,
            "require_complete": False,
        },
    }
    return spec, inp


def study_for(material: str, experiment: str, spec: dict) -> dict:
    mat = spec["material"]
    return {
        "study": "actinv-study-1",
        "study_id": f"p44-{material}-{experiment}",
        "library": {"path": str(LIBRARY), "sha256": LIBRARY_SHA},
        "decay": {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)},
        "cases": {
            "materials": [{"name": "case", "mass_g": mat["mass_g"],
                           "composition": mat["composition"]}],
            "spectra": [{"name": "spec",
                         "flux_per_group": spec["spectrum"]["flux_per_group"],
                         "total": spec["spectrum"]["total"],
                         "descending": spec["spectrum"].get("descending", True)}],
            "schedules": [{"name": "sched", "steps": spec["schedule"]}],
        },
        "responses": ["decay_heat_w_per_g"],
        "options": spec["options"],
        "robustness": {
            "samples": SAMPLES,
            "seed": SEED,
            "covariance": {"path": str(COVARIANCE), "sha256": COVARIANCE_SHA},
            "channels": {
                "cross_section_mf33": True,
                "decay_constants": True,
                "fission_yields": False,
                "flux_rel_std": 0.0,
                "composition_rel_std": {},
            },
            "responses": ["decay_heat_w_per_g"],
            "first_order_comparison": False,
        },
    }


def cooling_step_ends(inp: dict) -> list[float]:
    return [float(t) for t in inp["cooling_cum_s"]]


def post_shutdown_bands(outv: dict, response: str) -> tuple[list, str | None]:
    """(per-step {t_s, nominal, lo, hi}) for cooling steps, or error."""
    steps = outv.get("steps") or []
    irr_end = 0.0
    for st in steps:
        if (st.get("flux") or 0.0) > 0.0:
            irr_end = st.get("t_s", 0.0)
    bands = []
    for st in steps:
        t = st.get("t_s", 0.0) - irr_end
        if t <= 0.0:
            continue
        unc = (st.get("uncertainty") or {}).get("responses", {}).get(response)
        nominal = (st.get("heat_W_per_g") or {}).get("total")
        if not unc:
            return bands, f"no uncertainty record at t={t}"
        iv = unc.get("normal_interval") or [None, None]
        bands.append({"t_s": t, "nominal": nominal, "lo": iv[0], "hi": iv[1]})
    return bands, None


def quantile(vals: list[float], q: float) -> float | None:
    """Type-7 (numpy 'linear') quantile; None if empty."""
    if not vals:
        return None
    v = sorted(vals)
    if len(v) == 1:
        return v[0]
    pos = q * (len(v) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def run_cli(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(args, capture_output=True, text=True, timeout=3600)
    return p.returncode, (p.stderr or "") + (p.stdout or "")


def produce(material: str, experiment: str, rec: dict) -> dict:
    edir = WORK / "runs" / f"{material}__{experiment}"
    edir.mkdir(parents=True, exist_ok=True)
    spec, inp = spec_for(material, experiment)
    spec_path = edir / "spec.json"
    spec_path.write_text(json.dumps(spec))
    rec["cooling_step_ends_s"] = cooling_step_ends(inp)
    rec["spec_sha256"] = sha256(spec_path)

    # ---- first-order leg ----
    fo_out = edir / "fo.out.json"
    fo = {"status": None, "out_sha256": None, "bands": [], "error": None}
    if fo_out.is_file():
        try:
            ov = json.loads(fo_out.read_text())
            bands, err = post_shutdown_bands(ov, "heat.total")
            fo.update(status="executed" if not err else "failed",
                      out_sha256=sha256(fo_out), bands=bands, error=err)
        except Exception as e:
            fo.update(status="failed", error=f"parse: {e}")
    if fo["status"] != "executed":
        rc, log = run_cli([str(ACTINV), "run", str(spec_path), str(fo_out)])
        if rc != 0 or not fo_out.is_file():
            fo.update(status="failed",
                      error=log.strip().splitlines()[-1][:300] if log.strip()
                      else f"exit {rc}")
        else:
            ov = json.loads(fo_out.read_text())
            bands, err = post_shutdown_bands(ov, "heat.total")
            fo.update(status="executed" if not err else "failed",
                      out_sha256=sha256(fo_out), bands=bands, error=err)
    rec["first_order"] = fo

    # ---- sampled leg ----
    study = study_for(material, experiment, spec)
    study_path = edir / "study.json"
    study_path.write_text(json.dumps(study))
    rec["study_sha256"] = sha256(study_path)
    sdir = edir / "study_out"
    srec = sdir / "study_record.json"
    sa = {"status": None, "record_sha256": None, "n_samples": SAMPLES,
          "bands": [], "error": None}
    if srec.is_file():
        try:
            rec_v = json.loads(srec.read_text())
            sa = extract_sampled(rec_v, cooling_step_ends(inp), sa, srec)
        except Exception as e:
            sa.update(status="failed", error=f"parse: {e}")
    if sa["status"] != "executed":
        rc, log = run_cli([str(ACTINV), "study", "run",
                           str(study_path), str(sdir)])
        if rc != 0 or not srec.is_file():
            sa.update(status="failed",
                      error=log.strip().splitlines()[-1][:300] if log.strip()
                      else f"exit {rc}")
        else:
            rec_v = json.loads(srec.read_text())
            sa = extract_sampled(rec_v, cooling_step_ends(inp), sa, srec)
    rec["sampled"] = sa
    return rec


def extract_sampled(rec_v: dict, cooling: list[float],
                    sa: dict, srec: Path) -> dict:
    cases = rec_v.get("cases") or []
    if len(cases) != 1:
        sa.update(status="failed", error=f"{len(cases)} cases")
        return sa
    case = cases[0]
    if case.get("status") != "executed":
        sa.update(status="failed", error=f"case {case.get('status')}")
        return sa
    rb = case.get("robustness") or {}
    sv = rb.get("sample_values") or []
    nominal_pt = (case.get("per_time") or {})
    if len(sv) != SAMPLES or any(e is None for e in sv):
        sa.update(status="failed",
                  error=f"sample_values {len(sv)} entries, "
                        f"nulls={sum(1 for e in sv if e is None)}")
        return sa
    if (rb.get("n_failed_samples") or 0) > 0:
        sa.update(status="failed",
                  error=f"{rb.get('n_failed_samples')} failed samples")
        return sa
    def keyed(items):
        out = {}
        for tk, metrics in items:
            try:
                t = float(tk)
            except (TypeError, ValueError):
                continue
            v = (metrics or {}).get("decay_heat_w_per_g")
            if isinstance(v, (int, float)) and math.isfinite(v):
                out[t] = float(v)
        return out

    sample_series: list[dict[float, float]] = [keyed((e or {}).items())
                                               for e in sv if e]
    nom_map = keyed(nominal_pt.items())

    def nearest_t(t: float, keys) -> float | None:
        cand = [k for k in keys if abs(k - t) <= max(1e-6 * t, 1e-9)]
        return min(cand, key=lambda k: abs(k - t)) if cand else None

    bands = []
    for t in cooling:
        vals = []
        for series in sample_series:
            k = nearest_t(t, series.keys())
            if k is not None:
                vals.append(series[k])
        nk = nearest_t(t, nom_map.keys())
        if len(vals) < 2:
            sa.update(status="failed",
                      error=f"{len(vals)} sample values at t={t}")
            return sa
        bands.append({"t_s": t, "nominal": nom_map.get(nk) if nk is not None else None,
                      "lo": quantile(vals, TAIL),
                      "hi": quantile(vals, 1.0 - TAIL),
                      "n": len(vals)})
    sa.update(status="executed", record_sha256=sha256(srec), bands=bands)
    return sa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--partition", choices=["development", "sealed", "all"],
                    required=True)
    ap.add_argument("--experiments", nargs="*", default=None,
                    help="explicit Material/exp list overrides partition")
    args = ap.parse_args()

    all_exps = experiments()
    if args.experiments:
        sel = {tuple(e.split("/", 1)) for e in args.experiments}
        todo = [e for e in all_exps if e in sel]
    elif args.partition == "development":
        todo = [e for e in all_exps if e in DEVELOPMENT]
    elif args.partition == "sealed":
        todo = [e for e in all_exps if e not in DEVELOPMENT]
    else:
        todo = all_exps
    BANDS.mkdir(parents=True, exist_ok=True)
    done = failed = skipped = 0
    for material, exp in todo:
        out = BANDS / f"{material}__{exp}.json"
        rec = {"material": material, "experiment": exp}
        if out.is_file():
            try:
                prev = json.loads(out.read_text())
                if (prev.get("first_order", {}).get("status") == "executed"
                        and prev.get("sampled", {}).get("status") == "executed"):
                    skipped += 1
                    continue
            except Exception:
                pass
        print(f"[{material}/{exp}] producing bands", flush=True)
        try:
            rec = produce(material, exp, rec)
        except Exception as e:
            rec.update(first_order={"status": "failed", "error": str(e)},
                       sampled={"status": "failed", "error": str(e)})
        ok = (rec.get("first_order", {}).get("status") == "executed"
              and rec.get("sampled", {}).get("status") == "executed")
        done += 1 if ok else 0
        failed += 0 if ok else 1
        out.write_text(json.dumps(rec, indent=1))
        print(f"  -> {'executed' if ok else 'FAILED'}", flush=True)
    print(json.dumps({"partition": args.partition, "total": len(todo),
                      "executed": done, "failed": failed,
                      "skipped_complete": skipped}))


if __name__ == "__main__":
    main()
