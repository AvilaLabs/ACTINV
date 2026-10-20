#!/usr/bin/env python3
"""P149 S5: MF=33 covariance contributions and correlated sampling on three FNS cases (TENDL-2025).

Only TENDL-2025 has an ACTINV covariance sidecar, so S5 runs on `actinv-data/v1.1.0` (library and its bound
`tendl-2025-neutron-709g.cov.npz`), not on the TENDL-2017 library of the FNS benchmark.
Cases (spectrum, irradiation and cooling times from `cb3_fns.specification`):
  fe            FNS Fe 1996exp_5min (the P30 spectrum, byte-identical to examples/fns_fe_5min.json);
  fe_co100wppm  the same with Fe 99.99 wt% and Co 0.01 wt% (the P30 case);
  ss316         FNS SS316 1996exp_7hour.
Each case is evaluated at the first, the middle and the last measured cooling time, for total activity and total
decay heat.
  - First order: one `actinv run` with an `uncertainty` section gives the sensitivities s; `p20_collapse_probe`
    gives the collapsed covariance C of the covered rows. Reported: s^T C s against the run's
    `mf33_standard_uncertainty`, its split into the diagonal (s_i^2 C_ii), cross terms within one target nuclide and
    cross terms between targets, the ten rows with the largest |s_i (C s)_i|, the row-count coverage and the
    response-weighted coverage sum_covered |s_i x_i| / sum_all |s_i x_i| (x_i the collapsed cross section, so
    s_i x_i is the response change per unit relative change of row i; defined for rows without covariance too).
  - Sampling: an `actinv study run` with `robustness` (MF=33 channel only, SAMPLES draws, fixed SEED) and
    `statistics`. Reported: the tool's distribution and first-order comparison, and percentile-bootstrap 95 %
    intervals (BOOTSTRAP resamples, fixed seed) for the mean, standard deviation, skewness and the 2.5 % and 97.5 %
    quantiles, recomputed from the per-sample outputs `rob_<i>.out.json`.

Usage: p149_s5_covariance.py [--out results/p149/s5_covariance.json] [--work target/p149-s5] [--only CASE ...]
                             [--samples 1000]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import cb3_fns as cb3  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
PROBE = Path(os.environ.get("ACTINV_P20_PROBE", ROOT / "target/release/p20_collapse_probe"))
DATA = ROOT / "actinv-data/v1.1.0"
LIBRARY = DATA / "activation/tendl-2025-neutron-709g.npz"
COVARIANCE = DATA / "uncertainty/tendl-2025-neutron-709g.cov.npz"
CASES = {
    "fe": ("Fe", "1996exp_5min", None),
    "fe_co100wppm": ("Fe", "1996exp_5min", {"FE": 99.99, "CO": 0.01}),
    "ss316": ("SS316", "1996exp_7hour", None),
}
RESPONSES = {"activity.total": "total_activity_bq_per_g", "heat.total": "decay_heat_w_per_g"}
SAMPLES = 1000
SEED = 149005
BOOTSTRAP = 2000
BOOTSTRAP_SEED = 149505
TOP = 10
RUN_TIMEOUT_S = 3600.0
STUDY_TIMEOUT_S = 12 * 3600.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def checked(argv: list, timeout: float, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    run = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed ({run.returncode}): {run.stderr[-3000:]}")
    return run


def case_spec(name: str, identities: dict) -> tuple[dict, list[float], float]:
    """The FNS spec on TENDL-2025, cut to the irradiation and the first, middle and last measured cooling time."""
    material, experiment, composition = CASES[name]
    spec, record = cb3.specification(material, experiment)
    measurement = cb3.fio.read_exp(cb3.FNS / material / f"{experiment}.exp")
    matched, _alignment = cb3.infer_measurement_alignment(record["cooling_cum_s"], measurement)
    measured = sorted({float(record["cooling_cum_s"][index]) for _m, index in matched})
    times = [measured[0], measured[len(measured) // 2], measured[-1]]
    spec["title"] = f"P149 S5 {name} (FNS {material} {experiment}, TENDL-2025, MF=33)"
    spec["library"] = {"path": str(LIBRARY), "sha256": identities["library"]}
    if composition is not None:
        spec["material"]["composition"] = composition
    t_irr = float(record["t_irr_s"])
    spec["schedule"] = [{"dt": f"{t_irr:.17e} s", "flux": 1.0}] + [
        {"dt": f"{t - (times[i - 1] if i else 0.0):.17e} s", "flux": 0.0} for i, t in enumerate(times)]
    spec["uncertainty"] = {"covariance": {"path": str(COVARIANCE), "sha256": identities["covariance"]},
                           "responses": list(RESPONSES), "confidence_level": 0.95}
    return spec, times, t_irr


def ascending_flux(spectrum: dict) -> list[float]:
    """The probe's flux: ascending group order, scaled to `total` (as `spec.flux_ascending()`)."""
    flux = [float(v) for v in spectrum["flux_per_group"]]
    if spectrum.get("descending"):
        flux.reverse()
    total = spectrum.get("total")
    if total:
        flux = [v * float(total) / sum(flux) for v in flux]
    return flux


def decompose(band: dict, rows: list[int], covariance: np.ndarray) -> dict:
    position = {row: i for i, row in enumerate(rows)}
    nominal = float(band["nominal"])
    s = np.zeros(len(rows))
    target = [None] * len(rows)
    weight_all = weight_covered = 0.0
    uncovered = []
    for entry in band["sensitivities"]:
        parameter, value = entry["parameter"], float(entry["value"])
        weight = abs(value * float(parameter["collapsed_cross_section_b"]))
        weight_all += weight
        live = parameter["covariance_covered"] and not parameter["covariance_excluded"]
        if live:
            weight_covered += weight
            i = position[parameter["library_row"]]
            s[i], target[i] = value, parameter["target"]
        elif weight > 0.0:
            uncovered.append((weight, parameter))
    cs = covariance @ s
    variance = float(s @ cs)
    diagonal = s * s * np.diag(covariance)
    same = np.equal.outer(np.asarray(target, dtype=object), np.asarray(target, dtype=object))
    np.fill_diagonal(same, False)
    outer = np.outer(s, s) * covariance
    within = float(outer[same].sum())
    between = float(outer.sum() - diagonal.sum() - within)
    share = s * cs
    parameters = {e["parameter"]["library_row"]: e["parameter"] for e in band["sensitivities"]}
    ranked = sorted(range(len(rows)), key=lambda i: abs(share[i]), reverse=True)[:TOP]
    reported = float(band["mf33_standard_uncertainty"]) ** 2
    return {
        "nominal": nominal,
        "mf33_standard_uncertainty": float(band["mf33_standard_uncertainty"]),
        "relative_standard_uncertainty": band.get("relative_standard_uncertainty"),
        "variance_recomputed": variance,
        "variance_relative_difference": abs(variance - reported) / reported if reported > 0 else None,
        "variance_split": {"diagonal": float(diagonal.sum()), "cross_within_target": within,
                           "cross_between_targets": between},
        "variance_split_fraction": ({"diagonal": float(diagonal.sum()) / variance, "cross_within_target": within
                                     / variance, "cross_between_targets": between / variance}
                                    if variance > 0 else None),
        "top_rows": [{"library_row": rows[i], "target": parameters[rows[i]]["target_nuclide"],
                      "MT": parameters[rows[i]]["MT"], "ZAP": parameters[rows[i]]["ZAP"],
                      "LFS": parameters[rows[i]]["LFS"],
                      "share": float(share[i]) / variance if variance > 0 else None,
                      "diagonal_share": float(diagonal[i]) / variance if variance > 0 else None,
                      "relative_standard_deviation": (math.sqrt(max(covariance[i, i], 0.0))
                                                      / parameters[rows[i]]["collapsed_cross_section_b"]
                                                      if parameters[rows[i]]["collapsed_cross_section_b"] else None),
                      "relative_sensitivity": (float(s[i]) * parameters[rows[i]]["collapsed_cross_section_b"]
                                               / nominal if nominal else None)}
                     for i in ranked],
        "coverage": {"row_count": {"covered": band["covered_parameters"], "total": band["total_parameters"],
                                   "fraction": (band["covered_parameters"] / band["total_parameters"]
                                                if band["total_parameters"] else None)},
                     "response_weighted": weight_covered / weight_all if weight_all > 0 else None,
                     "label": band["coverage"],
                     "largest_uncovered": [{"library_row": p["library_row"], "target": p["target_nuclide"],
                                            "MT": p["MT"], "ZAP": p["ZAP"], "LFS": p["LFS"],
                                            "relative_sensitivity": w / abs(nominal) if nominal else None}
                                           for w, p in sorted(uncovered, key=lambda x: x[0], reverse=True)[:5]]},
    }


def first_order(name: str, spec: dict, times: list[float], work: Path) -> dict:
    spec_path, result_path = work / "first_order.json", work / "first_order.out.json"
    spec_path.write_text(json.dumps(spec, indent=1))
    checked([ACTINV, "run", spec_path, result_path], RUN_TIMEOUT_S)
    result = json.loads(result_path.read_text())
    steps = result["steps"][1:]
    if len(steps) != len(times):
        raise RuntimeError(f"{name}: {len(steps)} cooling steps for {len(times)} times")
    rows = sorted({e["parameter"]["library_row"] for step in steps
                   for band in step["uncertainty"]["responses"].values() for e in band["sensitivities"]
                   if e["parameter"]["covariance_covered"] and not e["parameter"]["covariance_excluded"]})
    (work / "flux.csv").write_text(",".join(repr(v) for v in ascending_flux(spec["spectrum"])))
    (work / "rows.csv").write_text(",".join(str(r) for r in rows))
    probe = json.loads(checked([PROBE, LIBRARY, COVARIANCE, work / "flux.csv", work / "rows.csv"],
                               RUN_TIMEOUT_S).stdout)
    collapsed = probe["collapsed"]
    if collapsed["row_indices"] != rows:
        raise RuntimeError(f"{name}: probe rows differ from the requested rows")
    covariance = np.asarray(collapsed["covariance_barn2"], dtype=float).reshape(len(rows), len(rows))
    nominal_b = {e["parameter"]["library_row"]: e["parameter"]["collapsed_cross_section_b"]
                 for e in steps[0]["uncertainty"]["responses"]["activity.total"]["sensitivities"]}
    collapse_difference = max((abs(b - nominal_b[r]) / abs(nominal_b[r])
                               for r, b in zip(rows, collapsed["one_group_barns"]) if nominal_b.get(r)), default=0.0)
    points = []
    for time, step in zip(times, steps):
        bands = step["uncertainty"]["responses"]
        points.append({"cooling_s": time, "t_s": step["t_s"],
                       "responses": {selector: decompose(bands[selector], rows, covariance)
                                     for selector in RESPONSES}})
    return {"result_sha256": sha256(result_path), "spec_sha256": sha256(spec_path),
            "probe": {k: probe[k] for k in ("schema", "library_sha256", "covariance_sha256")}
            | {"rows": len(rows), "uncovered_rows": len(collapsed["uncovered_rows"]),
               "excluded_blocks": collapsed["excluded_blocks"],
               "maximum_asymmetry_barn2": collapsed["maximum_asymmetry_barn2"],
               "collapsed_cross_section_max_relative_difference": collapse_difference},
            "total_states": result["total_states"], "pruned_states": result["pruned_states"],
            "points": points}


def nearest_rank(values: np.ndarray, p: float) -> float:
    ordered = np.sort(values)
    k = max(1, math.ceil(len(ordered) * p / 100.0))
    return float(ordered[k - 1])


def moments(values: np.ndarray) -> dict:
    centered = values - values.mean()
    m2, m3 = float(np.mean(centered ** 2)), float(np.mean(centered ** 3))
    return {"mean": float(values.mean()), "std": float(values.std(ddof=1)),
            "skewness": m3 / m2 ** 1.5 if m2 > 0 else None,
            "q2.5": nearest_rank(values, 2.5), "q97.5": nearest_rank(values, 97.5)}


def bootstrap(values: np.ndarray, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    draws = [moments(values[rng.integers(0, len(values), len(values))]) for _ in range(BOOTSTRAP)]
    out = {}
    for key in ("mean", "std", "skewness", "q2.5", "q97.5"):
        series = np.asarray([d[key] for d in draws if d[key] is not None])
        out[key] = [float(np.percentile(series, 2.5)), float(np.percentile(series, 97.5))] if len(series) else None
    return out


def sample_value(output: dict, response: str, t_s: float) -> float:
    step = min(output["steps"], key=lambda s: abs(s["t_s"] - t_s))
    if abs(step["t_s"] - t_s) > 1e-6 * max(1.0, t_s):
        raise RuntimeError(f"no step at t = {t_s} s")
    if response == "total_activity_bq_per_g":
        return float(sum(step["activity_Bq_per_g"].values()))
    return float(step["heat_W_per_g"]["total"])


def sampling(name: str, spec: dict, times: list[float], t_irr: float, samples: int, work: Path,
             identities: dict) -> dict:
    spectrum_path = work / "spectrum_spec.json"
    spectrum_path.write_text(json.dumps(spec, indent=1))
    study = {
        "study": "actinv-study-1", "study_id": f"p149-s5-{name.replace('_', '-')}",
        "title": f"P149 S5 sampling {name}", "template": {"id": "p149-s5-sampling", "version": 1},
        "library": {"path": str(LIBRARY), "sha256": identities["library"]},
        "decay": spec["decay"],
        "cases": {"materials": [{"name": name, "composition": spec["material"]["composition"]}],
                  "spectra": [{"name": "fns", "spec_ref": str(spectrum_path)}],
                  "schedules": [{"name": "irradiation", "steps": [{"dt": f"{t_irr:.17e} s", "flux": 1.0}]}]},
        "cooling_times_s": times,
        "responses": list(RESPONSES.values()),
        "robustness": {"samples": samples, "seed": SEED, "channels": {"cross_section_mf33": True},
                       "covariance": {"path": str(COVARIANCE), "sha256": identities["covariance"]},
                       "responses": list(RESPONSES.values()), "statistics": {"bootstrap_resamples": 200}},
    }
    study_path, out = work / "study.json", work / "study"
    study_path.write_text(json.dumps(study, indent=1))
    checked([ACTINV, "study", "run", study_path, out], STUDY_TIMEOUT_S)
    record = json.loads((out / "study_record.json").read_text())
    (case,) = record["cases"]
    robustness = case["robustness"]
    case_dir = out / "cases" / case["case_id"]
    outputs = [json.loads((case_dir / f"rob_{i}.out.json").read_text()) for i in range(robustness["samples"])]
    statistics = robustness["sampling_statistics"]["responses"]
    per_time = {}
    for j, time in enumerate(times):
        for k, response in enumerate(RESPONSES.values()):
            values = np.asarray([sample_value(o, response, t_irr + time) for o in outputs])
            tool = statistics[response][next(key for key in statistics[response] if math.isclose(float(key), time))]
            mine = moments(values)
            per_time.setdefault(response, {})[repr(time)] = {
                "tool_distribution": tool["distribution"], "tool_first_order": tool["first_order"],
                "tool_convergence": tool["convergence"],
                "recomputed": mine,
                "recomputed_matches_tool": (math.isclose(mine["mean"], tool["distribution"]["mean"], rel_tol=1e-12)
                                            and math.isclose(mine["std"], tool["distribution"]["std"],
                                                             rel_tol=1e-9)),
                "bootstrap_95": bootstrap(values, BOOTSTRAP_SEED + 10 * j + k),
            }
    return {"study_sha256": sha256(study_path), "record_status": record.get("status", case["status"]),
            "case_status": case["status"], "samples": robustness["samples"],
            "samples_declared": robustness["samples_declared"], "n_failed_samples": robustness["n_failed_samples"],
            "truncated_by_resource_limit": robustness["truncated_by_resource_limit"], "seed": robustness["seed"],
            "channels": {k: v for k, v in robustness["channels"]["cross_section_mf33"].items()
                         if k != "uncovered_rows"}
            | {"uncovered_rows_count": len(robustness["channels"]["cross_section_mf33"].get("uncovered_rows", []))},
            "local_vs_nonlinear": robustness["local_vs_nonlinear"], "per_time": per_time}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "results/p149/s5_covariance.json")
    ap.add_argument("--work", type=Path, default=ROOT / "target/p149-s5")
    ap.add_argument("--only", nargs="*", choices=list(CASES), default=list(CASES))
    ap.add_argument("--samples", type=int, default=SAMPLES)
    args = ap.parse_args()
    identities = {"library": sha256(LIBRARY), "covariance": sha256(COVARIANCE),
                  "actinv": sha256(ACTINV), "probe": sha256(PROBE)}
    record = json.loads(args.out.read_text()) if args.out.exists() else {}
    record.update({"schema": "actinv-p149-s5-covariance-1", "protocol": "P149 S5", "identities": identities,
                   "library_path": str(LIBRARY.relative_to(ROOT)), "covariance_path": str(COVARIANCE.relative_to(ROOT)),
                   "seed": SEED, "bootstrap": {"resamples": BOOTSTRAP, "seed": BOOTSTRAP_SEED},
                   "response_weighted_coverage": "sum over covered rows of |s_i x_i| / sum over all rows of |s_i x_i|"})
    cases = record.setdefault("cases", {})
    for name in args.only:
        work = args.work.resolve() / name
        work.mkdir(parents=True, exist_ok=True)
        spec, times, t_irr = case_spec(name, identities)
        cases[name] = {"experiment": "/".join(CASES[name][:2]), "composition": spec["material"]["composition"],
                       "cooling_times_s": times, "irradiation_s": t_irr,
                       "first_order": first_order(name, spec, times, work)}
        print(f"{name}: first order done", file=sys.stderr, flush=True)
        cases[name]["sampling"] = sampling(name, spec, times, t_irr, args.samples, work, identities)
        print(f"{name}: sampling done", file=sys.stderr, flush=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
