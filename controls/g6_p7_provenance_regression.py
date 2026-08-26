#!/usr/bin/env python3
"""P7-G6: fail-closed hashes, computed certificates, and exact pre-P7 scalar regression."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g6_p7_provenance_regression.json"


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cli_path() -> Path:
    supplied = os.environ.get("ACTINV_CLI")
    if supplied:
        return Path(supplied)
    for profile in ("release", "debug"):
        candidate = ROOT / "target" / profile / "actinv"
        if candidate.exists():
            return candidate
    raise RuntimeError("build actinv before running P7 G6")


def bad_run_cli(spec: dict, directory: Path, name: str) -> dict:
    path = directory / f"{name}.json"
    path.write_text(json.dumps(spec))
    process = subprocess.run(
        [str(cli_path()), "run", str(path), str(directory / f"{name}-out.json")],
        capture_output=True,
        text=True,
    )
    return {"returncode": process.returncode, "message": process.stderr.strip()}


def count_differences(current: dict, baseline: dict) -> tuple[int, list]:
    differences = []
    for key in ("mode", "pruned_states", "total_states"):
        if current[key] != baseline[key]:
            differences.append([key, baseline[key], current[key]])
    direct_fields = (
        "t_s", "total_atoms_per_g", "numerical_floor_atoms_per_g", "n_states_below_floor",
        "heat_bound_from_below_floor_W_per_g", "leakage_atoms_per_g", "negative_atoms_zeroed",
        "inventory", "activity_Bq_per_g",
    )
    for index, (now, then) in enumerate(zip(current["steps"], baseline["steps"])):
        if len(now["inventory"]) != then["n_inventory"]:
            differences.append([f"steps[{index}].n_inventory", then["n_inventory"], len(now["inventory"])])
        for field in direct_fields:
            if now[field] != then[field]:
                differences.append([f"steps[{index}].{field}", "changed"])
    if len(current["steps"]) - 1 != len(baseline["heat_split_uW_g"]):
        differences.append(["cooling_heat_length", len(baseline["heat_split_uW_g"]), len(current["steps"]) - 1])
    for index, (now, then) in enumerate(zip(current["steps"][1:], baseline["heat_split_uW_g"])):
        converted = {key: value * 1.0e6 for key, value in now["heat_W_per_g"].items()}
        if converted != then:
            differences.append([f"cooling_heat[{index}]", then, converted])
    return len(differences), differences[:10]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: g6_p7_provenance_regression.py RESPONSE.json")
    response_path = str(Path(sys.argv[1]).resolve())
    base_spec = json.loads((ROOT / "examples" / "fns_fe_5min.json").read_text())
    valid_response = {"path": response_path, "sha256": sha256(response_path)}
    wrong_hash = "0" * 64

    import actinv

    with tempfile.TemporaryDirectory(prefix="actinv-p7-g6-") as temporary:
        temporary = Path(temporary)
        response_bad = json.loads(json.dumps(base_spec))
        response_bad["photon"] = {"response": {"path": response_path, "sha256": wrong_hash}}
        library_bad = json.loads(json.dumps(base_spec))
        library_bad["library"]["sha256"] = wrong_hash
        library_bad["photon"] = {"response": valid_response}
        cli_failures = {
            "response": bad_run_cli(response_bad, temporary, "response-bad"),
            "library": bad_run_cli(library_bad, temporary, "library-bad"),
        }
        python_failures = {}
        for name, spec in (("response", response_bad), ("library", library_bad)):
            try:
                actinv.run(json.dumps(spec))
                python_failures[name] = {"raised": False, "message": ""}
            except Exception as error:
                python_failures[name] = {"raised": True, "message": str(error)}

    hard_errors = all(
        cli_failures[name]["returncode"] != 0
        and "SHA-256 mismatch" in cli_failures[name]["message"]
        and python_failures[name]["raised"]
        and "SHA-256 mismatch" in python_failures[name]["message"]
        for name in ("response", "library")
    )

    baseline = json.loads((ROOT / "results" / "fns_spec" / "Fe_1996exp_5min.json").read_text())
    current = json.loads(actinv.run(json.dumps(baseline["spec"])))
    regression_count, regression_examples = count_differences(current, baseline)

    g3 = json.loads((ROOT / "results" / "g3_p7_inventory_identity.json").read_text())
    prior_p5 = json.loads((ROOT / "results" / "verdict_p5.json").read_text())["verdict"]
    prior_p6 = json.loads((ROOT / "results" / "verdict_p6.json").read_text())["verdict"]
    prior_green = prior_p5 in ("P5-PASS", "P5-CONDITIONAL") and prior_p6 in ("P6-PASS", "P6-CONDITIONAL")
    passed = hard_errors and regression_count == 0 and g3["certificate_hashes_match_independent_sha256"] and prior_green
    result = {
        "hash_mismatch_hard_errors": hard_errors,
        "cli_failures": cli_failures,
        "python_failures": python_failures,
        "computed_certificate_hashes_all_entry_points": g3["certificate_hashes_match_independent_sha256"],
        "pre_P7_scalar_regression": {
            "baseline": "results/fns_spec/Fe_1996exp_5min.json",
            "differences": regression_count,
            "examples": regression_examples,
        },
        "prior_control_verdicts": {"P5": prior_p5, "P6": prior_p6, "green": prior_green},
        "python_binding": {"python": sys.version.split()[0], "module": actinv.__file__},
        "pass": passed,
    }
    RESULT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
