#!/usr/bin/env python3
"""P23 G2 producer: reverse-calculation battery.

Independent control: forward runs generate synthetic measurements; the reverse
command must recover the known multipliers to 1e-12 relative. Inconsistency,
underdetermination, coupled mode, zero sensitivities, absent nuclides and
feed/removal schedules each must fail or report honestly, never silently.
Imports no ACTINV production, audit or scoring module.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g2_p23_reverse.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
TOL = 1e-12


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def run_cli(args: list[str], work: Path, env: dict[str, str], name: str):
    completed = subprocess.run(
        [str(ACTINV), *args],
        cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, check=False,
    )
    return completed


def forward(spec: dict, work: Path, env: dict[str, str], name: str) -> dict:
    path = work / f"{name}.json"
    out = work / f"{name}.out.json"
    path.write_text(json.dumps(spec, sort_keys=True) + "\n", encoding="utf-8")
    completed = run_cli(["run", str(path), str(out)], work, env, name)
    if completed.returncode:
        raise RuntimeError(f"forward {name} failed: {completed.stderr[-4000:]}")
    return json.loads(out.read_text(encoding="utf-8"))


def reverse(problem_path: Path, measurements: dict, work: Path, env: dict[str, str],
            name: str, segments: bool = False):
    meas_path = work / f"{name}.meas.json"
    meas_path.write_text(json.dumps(measurements, sort_keys=True) + "\n", encoding="utf-8")
    out = work / f"{name}.rev.json"
    args = ["reverse", str(problem_path), str(meas_path), str(out)]
    if segments:
        args.append("--segments")
    completed = run_cli(args, work, env, name)
    if completed.returncode:
        return None, completed.stdout + completed.stderr
    return json.loads(out.read_text(encoding="utf-8")), completed.stdout + completed.stderr


def close(a: float, b: float, tol: float = TOL) -> bool:
    return bool(abs(a - b) <= tol * max(abs(a), abs(b), 1.0))


def main() -> None:
    specification = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    specification["library"]["path"] = str(ROOT / specification["library"]["path"])
    for role in ("primary", "fallback"):
        specification["decay"][role] = str(ROOT / specification["decay"][role])

    env = os.environ.copy()
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    preflight = ROOT / "target/preflight-tmp"
    preflight.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=preflight, prefix="p23-g2-") as directory:
        work = Path(directory)
        env["ACTINV_CACHE_DIR"] = str(work / "cache")
        env["RAYON_NUM_THREADS"] = "1"

        # ---- (a) scalar recovery from a single measurement: truth m = 1.5
        truth_a = 1.5
        spec_scalar = json.loads(json.dumps(specification))
        spec_scalar["title"] = "p23-g2 scalar"
        spec_scalar["schedule"] = [{"dt": "100.0 s", "flux": truth_a},
                                   {"dt": "500.0 s", "flux": 0.0}]
        problem_path = work / "scalar.json"
        problem_text = json.dumps(spec_scalar, sort_keys=True) + "\n"
        problem_path.write_text(problem_text, encoding="utf-8")
        fwd = forward(spec_scalar, work, env, "scalar_fwd")
        step_last = fwd["steps"][-1]
        name, activity = max(
            step_last["activity_Bq_per_g"].items(), key=lambda kv: kv[1]
        )
        meas = {"format": "actinv-reverse-input-1", "measurements": [
            {"step": "last", "nuclide": name, "activity_Bq_per_g": activity}]}
        result, _ = reverse(problem_path, meas, work, env, "scalar")
        est = result["estimates"]["multiplier"]
        checks["scalar_recovers_known_multiplier"] = close(est, truth_a)
        details["scalar"] = {"estimate": est, "truth": truth_a, "nuclide": name}
        checks["scalar_identity_hashes"] = (
            result["sensitivity"]["problem_sha256"] == sha256_text(problem_text)
            and result["sensitivity"]["measurements_sha256"]
            == sha256_text((work / "scalar.meas.json").read_text())
        )
        checks["scalar_reports_residuals"] = (
            result["mode"] == "normalization"
            and len(result["measurements"]) == 1
            and abs(result["measurements"][0]["residual_Bq_per_g"]) < activity * 1e-9
        )

        # ---- (b) multi-nuclide multi-step scalar recovery
        measurements = []
        for step_index in (1, len(fwd["steps"])):
            act = fwd["steps"][step_index - 1]["activity_Bq_per_g"]
            for nuclide, value in sorted(act.items(), key=lambda kv: -kv[1])[:3]:
                measurements.append({
                    "step": step_index, "nuclide": nuclide,
                    "activity_Bq_per_g": value,
                    "sigma_Bq_per_g": value * 0.02,
                })
        result, _ = reverse(problem_path,
                            {"format": "actinv-reverse-input-1",
                             "measurements": measurements},
                            work, env, "multi")
        est = result["estimates"]["multiplier"]
        checks["multi_nuclide_multi_step_recovers"] = close(est, truth_a)
        details["multi"] = {"estimate": est, "truth": truth_a,
                            "chi_square": result["chi_square"],
                            "dof": result["degrees_of_freedom"]}

        # ---- (c) segment recovery: truth multipliers [1.3, 0.7]
        truths = [1.3, 0.7]
        spec = json.loads(json.dumps(specification))
        spec["title"] = "p23-g2 segments"
        spec["schedule"] = [{"dt": "50.0 s", "flux": truths[0]},
                            {"dt": "500.0 s", "flux": 0.0},
                            {"dt": "50.0 s", "flux": truths[1]},
                            {"dt": "500.0 s", "flux": 0.0}]
        seg_path = work / "segments.json"
        seg_path.write_text(json.dumps(spec, sort_keys=True) + "\n", encoding="utf-8")
        fwd = forward(spec, work, env, "seg_fwd")
        act = fwd["steps"][-1]["activity_Bq_per_g"]
        measurements = [
            {"step": "last", "nuclide": n, "activity_Bq_per_g": v}
            for n, v in sorted(act.items(), key=lambda kv: -kv[1])[:5]
        ]
        result, _ = reverse(seg_path,
                            {"format": "actinv-reverse-input-1",
                             "measurements": measurements},
                            work, env, "segments", segments=True)
        recovered = {e["step"]: e["multiplier"] for e in result["estimates"]}
        checks["segments_recover_known_multipliers"] = (
            close(recovered.get(1, -1.0), truths[0])
            and close(recovered.get(3, -1.0), truths[1])
        )
        checks["segments_report_condition_number"] = (
            isinstance(result.get("condition_number"), (int, float))
            and result["condition_number"] >= 1.0
        )
        checks["segments_report_se"] = all(
            e["standard_error"] > 0.0 for e in result["estimates"]
        )
        details["segments"] = {"recovered": recovered, "truths": truths,
                               "condition_number": result["condition_number"]}

        # ---- (d) deliberately inconsistent measurement: residuals + chi-square reported
        measurements = [
            {"step": "last", "nuclide": name, "activity_Bq_per_g": activity},
            {"step": 1, "nuclide": name, "activity_Bq_per_g": activity * 0.5,
             "sigma_Bq_per_g": activity * 0.001},
        ]
        result, _ = reverse(problem_path,
                            {"format": "actinv-reverse-input-1",
                             "measurements": measurements},
                            work, env, "inconsistent")
        checks["inconsistent_reports_chi_square"] = (
            result is not None and result["chi_square"] > 0.0
            and all("residual_Bq_per_g" in m for m in result["measurements"])
            and any(abs(m["residual_Bq_per_g"]) > 0.0 for m in result["measurements"])
        )

        # ---- named refusals
        # underdetermined segments: one measurement for two unknowns
        _, err = reverse(seg_path,
                         {"measurements": [{"step": "last", "nuclide": name,
                                            "activity_Bq_per_g": activity}]},
                         work, env, "underdet", segments=True)
        checks["underdetermined_segments_rejected"] = (
            "cannot determine" in err
        )
        # coupled-mode problem
        coupled = json.loads(json.dumps(spec_scalar))
        coupled["options"] = {"mode": "coupled"}
        coupled_path = work / "coupled.json"
        coupled_path.write_text(json.dumps(coupled, sort_keys=True) + "\n", encoding="utf-8")
        _, err = reverse(coupled_path,
                         {"measurements": [{"step": "last", "nuclide": name,
                                            "activity_Bq_per_g": activity}]},
                         work, env, "coupled")
        checks["coupled_mode_rejected"] = "linear trace regime" in err
        # absent nuclide
        _, err = reverse(problem_path,
                         {"measurements": [{"step": "last", "nuclide": "U235",
                                            "activity_Bq_per_g": 1.0}]},
                         work, env, "absent")
        checks["absent_nuclide_rejected"] = "absent from the computed inventory" in err
        # zero sensitivity: a populated-but-stable nuclide
        stable_name = next(
            (n["nuclide"] for n in step_last["inventory"]
             if n["nuclide"] not in step_last["activity_Bq_per_g"]),
            None,
        )
        if stable_name is not None:
            _, err = reverse(problem_path,
                             {"measurements": [{"step": "last", "nuclide": stable_name,
                                                "activity_Bq_per_g": 1.0}]},
                             work, env, "zero_sens")
            checks["zero_sensitivity_rejected"] = (
                "sensitivity" in err and "zero" in err
            )
            details["zero_sensitivity_nuclide"] = stable_name
        else:
            checks["zero_sensitivity_rejected"] = False
        # feed/removal schedule is outside the linear problem
        fed_spec = json.loads(json.dumps(spec_scalar))
        fed_spec["schedule"][0]["feed"] = {"Co60": 1.0}
        fed_path = work / "fed.json"
        fed_path.write_text(json.dumps(fed_spec, sort_keys=True) + "\n", encoding="utf-8")
        _, err = reverse(fed_path,
                         {"measurements": [{"step": "last", "nuclide": name,
                                            "activity_Bq_per_g": activity}]},
                         work, env, "fed")
        checks["feed_schedule_rejected"] = "outside the linear" in err
        # duplicate measurement
        _, err = reverse(problem_path,
                         {"measurements": [
                             {"step": "last", "nuclide": name,
                              "activity_Bq_per_g": activity},
                             {"step": 2, "nuclide": name,
                              "activity_Bq_per_g": activity}]},
                         work, env, "dup")
        checks["duplicate_measurement_rejected"] = "duplicate" in err
        # nonpositive sigma
        _, err = reverse(problem_path,
                         {"measurements": [{"step": "last", "nuclide": name,
                                            "activity_Bq_per_g": activity,
                                            "sigma_Bq_per_g": 0.0}]},
                         work, env, "badsigma")
        checks["nonpositive_sigma_rejected"] = "finite and positive" in err
        # all-zero segment column: a segment so short nothing is produced
        zero_spec = json.loads(json.dumps(spec_scalar))
        zero_spec["schedule"] = [{"dt": "100.0 s", "flux": 1.0},
                                 {"dt": "500.0 s", "flux": 0.0},
                                 {"dt": "1e-30 s", "flux": 1.0},
                                 {"dt": "500.0 s", "flux": 0.0}]
        zero_path = work / "zero_col.json"
        zero_path.write_text(json.dumps(zero_spec, sort_keys=True) + "\n", encoding="utf-8")
        zfwd = forward(zero_spec, work, env, "zero_col_fwd")
        zact = zfwd["steps"][-1]["activity_Bq_per_g"]
        zmeas = [{"step": "last", "nuclide": n, "activity_Bq_per_g": v}
                 for n, v in sorted(zact.items(), key=lambda kv: -kv[1])[:4]]
        _, err = reverse(zero_path, {"measurements": zmeas}, work, env, "zero_col",
                         segments=True)
        checks["zero_column_segment_rejected"] = (
            "all-zero sensitivity column" in err or "rank-deficient" in err
        )

        # ---- Python surface parity
        py_lib = Path(os.environ.get(
            "ACTINV_PYTHON_LIBRARY", ROOT / "python/target/release/libactinv.so"))
        module_spec = importlib.util.spec_from_file_location("actinv", py_lib)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        problem = module.Problem(spec_scalar)
        py_result = module.reverse(
            problem, {"measurements": [{"step": "last", "nuclide": name,
                                        "activity_Bq_per_g": activity}]})
        cli_result, _ = reverse(problem_path,
                                {"measurements": [{"step": "last", "nuclide": name,
                                                   "activity_Bq_per_g": activity}]},
                                work, env, "py_parity")
        checks["python_reverse_parity"] = close(
            py_result["estimates"]["multiplier"],
            cli_result["estimates"]["multiplier"],
            0.0,
        )

    evidence = {
        "schema": "actinv-p23-g2-reverse-1",
        "binary": str(ACTINV),
        "tolerance": TOL,
        "checks": checks,
        "details": details,
        "pass": all(checks.values()),
    }
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(checks, indent=2, sort_keys=True))
    sys.exit(0 if evidence["pass"] else 1)


if __name__ == "__main__":
    main()
