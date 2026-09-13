#!/usr/bin/env python3
"""P22-G1: frozen CB1 numerical battery re-run on the candidate.

Re-executes the committed ``cb1_numerical.py``, ``cb1_alara.py`` and
``cb1_fns.py`` measurement code unchanged — each module is imported and
only its ``RESULT`` path is redirected to a ``results/p22_g1_*.json``
file, so no sealed CB1 record is touched. Every exercised source SHA-256
is recorded so the checker can prove the measurement code is unchanged.

Comparisons against the sealed records use the frozen band: relative
1e-6 on continuous metrics (toolchain drift admitted) and exact integer
equality on counts (the closest scored point sits 2.1e-4 from the 30%
boundary, so the band cannot flip a count).

Writes ``results/g1_p22_battery.json``.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g1_p22_battery.json"
RESULTS = ROOT / "results"
CONTROLS = ROOT / "controls"

sys.path.insert(0, str(CONTROLS))

REL_BAND = 1e-6

NUMERICAL_WORST = (
    "absolute_over_initial_norm",
    "relative_above_tolerance_crossover",
    "resolvable_relative",
    "split_merged_absolute_over_initial_norm",
)
ALARA_WORST = (
    "worst_alara_vs_analytic_relative",
    "worst_actinv_vs_analytic_relative",
)
FNS_CONTINUOUS = (
    "median_pooled_abs_log_C_over_E",
    "p90_pooled_abs_log_C_over_E",
    "pooled_geometric_mean_C_over_E",
    "median_experiment_maximum_abs_log_C_over_E",
)
FNS_COUNTS = (
    "experiments_scored",
    "experiments_total",
    "experiments_all_points_within_30_percent",
    "points_scored",
    "positive_sigma_points",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rerun(module_name: str, out_name: str, env_extra: dict[str, str]) -> dict:
    """Import the committed control unchanged; redirect only RESULT.

    The environment is applied before import because the CB1 modules
    resolve their paths at module scope.
    """
    for key, value in env_extra.items():
        os.environ[key] = value
    if module_name in sys.modules:
        raise RuntimeError(
            f"{module_name} is already imported; module-level constants are fixed"
        )
    module = importlib.import_module(module_name)
    target = RESULTS / out_name
    module.RESULT = target
    run = {
        "module": f"controls/{module_name}.py",
        "module_sha256": sha256(CONTROLS / f"{module_name}.py"),
        "out": f"results/{out_name}",
        "env": env_extra,
    }
    try:
        module.main()
        code = 0
    except SystemExit as exit_:
        code = exit_.code if isinstance(exit_.code, int) else (0 if exit_.code is None else 1)
    except Exception:  # record the traceback, then keep going honestly
        run["returncode"] = None
        run["traceback"] = traceback.format_exc()[-2000:]
        return run
    finally:
        for key in env_extra:
            os.environ.pop(key, None)
    run["returncode"] = code
    run["written"] = target.exists()
    return run


def relative_delta(candidate: float, sealed: float) -> float | None:
    try:
        candidate = float(candidate)
        sealed = float(sealed)
    except (TypeError, ValueError):
        return None
    scale = max(abs(candidate), abs(sealed))
    if scale == 0.0:
        return 0.0 if candidate == sealed else float("inf")
    return abs(candidate - sealed) / scale


def compare_metrics(
    name: str,
    candidate: dict,
    sealed: dict,
    continuous: dict[str, str],
    counts: dict[str, str],
) -> dict:
    out = {"comparisons": {}, "all_within_band": True, "counts_exact": True}
    for key in continuous:
        cval = candidate
        sval = sealed
        for part in key.split("."):
            cval = (cval or {}).get(part)
            sval = (sval or {}).get(part)
        delta = relative_delta(cval, sval)
        out["comparisons"][key] = {
            "candidate": cval,
            "sealed": sval,
            "relative_delta": delta,
            "within_band": delta is not None and delta <= REL_BAND,
        }
        if not out["comparisons"][key]["within_band"]:
            out["all_within_band"] = False
    for key in counts:
        cval = candidate
        sval = sealed
        for part in key.split("."):
            cval = (cval or {}).get(part)
            sval = (sval or {}).get(part)
        equal = cval == sval
        out["comparisons"][key] = {
            "candidate": cval,
            "sealed": sval,
            "exact": equal,
        }
        if not equal:
            out["counts_exact"] = False
    out["pass"] = bool(
        candidate.get("pass") is True and out["all_within_band"] and out["counts_exact"]
    )
    return out


def main() -> None:
    work = ROOT / "target" / "p22-g1"
    work.mkdir(parents=True, exist_ok=True)

    runs = {
        "numerical": rerun("cb1_numerical", "p22_g1_numerical.json", {}),
        # The sealed ALARA build tree records a cmake wheel that lived under
        # /tmp and is gone; the restored cmake is 4.x and needs the policy
        # floor to re-run its (no-op) regeneration on ALARA's 2.8-era project.
        # TMPDIR is pinned to /tmp because ALARA's input lexer extracts the
        # dump_file path into a char token[64] unbounded: only the ~66-char
        # /tmp/<mkdtemp>/work/g5/dump_files/... shape (as in the sealed run)
        # survives; a long repo-relative TMPDIR smashes the token buffer.
        "alara": rerun(
            "cb1_alara",
            "p22_g1_alara.json",
            {"CMAKE_POLICY_VERSION_MINIMUM": "3.5", "TMPDIR": "/tmp"},
        ),
        "fns": rerun(
            "cb1_fns",
            "p22_g1_fns.json",
            {"ACTINV_CB1_FNS_WORK": str(work / "fns")},
        ),
    }

    legs = {}
    for name, sealed_file in (
        ("numerical", "cb1_numerical.json"),
        ("alara", "cb1_alara.json"),
        ("fns", "cb1_fns.json"),
    ):
        candidate_path = RESULTS / f"p22_g1_{name}.json"
        if not candidate_path.exists():
            legs[name] = {"pass": False, "error": "re-run produced no record"}
            continue
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        sealed = json.loads((RESULTS / sealed_file).read_text(encoding="utf-8"))
        if name == "numerical":
            legs[name] = compare_metrics(
                name,
                candidate.get("worst") or {},
                sealed.get("worst") or {},
                {k: k for k in NUMERICAL_WORST},
                {},
            )
            cw, sw = candidate.get("worst") or {}, sealed.get("worst") or {}
            legs[name]["no_larger"] = all(
                float(cw.get(k, float("inf")))
                <= float(sw.get(k, float("-inf"))) * (1.0 + REL_BAND)
                for k in NUMERICAL_WORST
            )
            legs[name]["candidate_pass"] = candidate.get("pass")
            legs[name]["pass"] = bool(
                candidate.get("pass") is True
                and legs[name]["all_within_band"]
                and legs[name]["no_larger"]
            )
        elif name == "alara":
            legs[name] = compare_metrics(
                name, candidate, sealed, {k: k for k in ALARA_WORST}, {}
            )
            legs[name]["identical_inputs_equal"] = (
                candidate.get("identical_inputs") == sealed.get("identical_inputs")
            )
            legs[name]["pass"] = bool(
                candidate.get("pass") is True
                and legs[name]["all_within_band"]
                and legs[name]["identical_inputs_equal"]
            )
        else:
            legs[name] = compare_metrics(
                name,
                candidate.get("summary", {}).get("actinv_tendl2025") or {},
                sealed.get("summary", {}).get("actinv_tendl2025") or {},
                {k: k for k in FNS_CONTINUOUS},
                {k: k for k in FNS_COUNTS},
            )
            legs[name]["candidate_pass"] = candidate.get("pass")
            legs[name]["pass"] = bool(
                candidate.get("pass") is True
                and legs[name]["all_within_band"]
                and legs[name]["counts_exact"]
            )
        legs[name]["sealed_record"] = f"results/{sealed_file}"
        legs[name]["candidate_record"] = f"results/p22_g1_{name}.json"

    record = {
        "schema": "actinv-p22-g1-battery-1",
        "relative_band": REL_BAND,
        "runs": runs,
        "legs": legs,
        "pass": all(leg.get("pass") for leg in legs.values()),
    }
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(
        {"pass": record["pass"],
         "legs": {n: l.get("pass") for n, l in legs.items()},
         "runs": {n: r.get("returncode") for n, r in runs.items()}},
        indent=2,
    ))
    raise SystemExit(0 if record["pass"] else 1)


if __name__ == "__main__":
    main()
