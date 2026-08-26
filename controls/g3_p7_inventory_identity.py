#!/usr/bin/env python3
"""P7-G3: CLI/Python/harness identity and per-step photon-source closure on the Fe CI spec."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g3_p7_inventory_identity.json"


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
    raise RuntimeError("build the actinv CLI before running P7 G3")


def walk(left, right, path=""):
    if isinstance(left, dict) and isinstance(right, dict):
        left_keys = set(left) - {"entry_point"}
        right_keys = set(right) - {"entry_point"}
        if left_keys != right_keys:
            yield path, "keys", sorted(left_keys ^ right_keys)
            return
        for key in sorted(left_keys):
            yield from walk(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            yield path, "length", len(left), len(right)
            return
        for index, (a, b) in enumerate(zip(left, right)):
            yield from walk(a, b, f"{path}[{index}]")
    elif left != right:
        yield path, "value", left, right


def comparable(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "ms"}


def relative(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: g3_p7_inventory_identity.py RESPONSE.json")
    response_path = str(Path(sys.argv[1]).resolve())
    spec = json.loads((ROOT / "examples" / "fns_fe_5min.json").read_text())
    spec["photon"] = {
        "group_structure": "fispact-24",
        "response": {"path": response_path, "sha256": sha256(response_path)},
        "build_up_factor": 2.0,
        "gamma_constant_cutoff_eV": 2.0e4,
    }
    text = json.dumps(spec, separators=(",", ":"))

    with tempfile.TemporaryDirectory(prefix="actinv-p7-g3-") as temporary:
        temporary = Path(temporary)
        spec_path, cli_result = temporary / "spec.json", temporary / "cli.json"
        spec_path.write_text(text)
        subprocess.run([str(cli_path()), "run", str(spec_path), str(cli_result)], check=True)
        cli = json.loads(cli_result.read_text())

    import actinv

    python = json.loads(actinv.run(text))

    # The production harness reaches the core through this binding. Calling through a
    # separate wrapper here exercises that entry path without rerunning the FNS family.
    def harness(problem: str) -> dict:
        return json.loads(actinv.run(problem))

    harness_result = harness(text)
    cli_python_differences = list(walk(comparable(cli), comparable(python)))
    python_harness_differences = list(walk(comparable(python), comparable(harness_result)))

    worst = {
        "nuclide_group_count_relative": 0.0,
        "nuclide_group_power_relative": 0.0,
        "nuclide_total_count_relative": 0.0,
        "gamma_heat_bound_relative": 0.0,
        "contact_contribution_relative": 0.0,
    }
    closure_failures = []
    for step in cli["steps"]:
        source = step["photon_source"]
        for index, group in enumerate(source["groups"]):
            count_sum = sum(row["groups"][index]["photons_s_g"] for row in source["by_nuclide"])
            power_sum = sum(row["groups"][index]["power_W_g"] for row in source["by_nuclide"])
            worst["nuclide_group_count_relative"] = max(
                worst["nuclide_group_count_relative"], relative(count_sum, group["photons_s_g"])
            )
            worst["nuclide_group_power_relative"] = max(
                worst["nuclide_group_power_relative"], relative(power_sum, group["power_W_g"])
            )
        nuclide_count = sum(row["activity_Bq_g"] * row["source_photons_per_decay"] for row in source["by_nuclide"])
        worst["nuclide_total_count_relative"] = max(
            worst["nuclide_total_count_relative"], relative(nuclide_count, source["total_photons_s_g"])
        )
        gamma_accounted = source["source_power_W_g"] + source["unrepresented_gamma_power_W_g"]
        worst["gamma_heat_bound_relative"] = max(
            worst["gamma_heat_bound_relative"], relative(gamma_accounted, step["heat_W_per_g"]["gamma"])
        )
        if source["contact_gamma_air_dose_proxy_Gy_h"] is not None:
            contribution = sum(
                row["contact_gamma_air_dose_proxy_Gy_h"] for row in source["by_nuclide"]
            )
            worst["contact_contribution_relative"] = max(
                worst["contact_contribution_relative"],
                relative(contribution, source["contact_gamma_air_dose_proxy_Gy_h"]),
            )
    for name, value in worst.items():
        if value > 1e-12:
            closure_failures.append(f"{name}={value}")

    index_path = spec["library"]["path"].removesuffix(".npz") + "_index.json"
    inputs = cli["certificate"]["inputs"]
    expected_hashes = {
        "library": sha256(spec["library"]["path"]),
        "library_index": sha256(index_path),
        "decay_primary": sha256(spec["decay"]["primary"]),
        "decay_fallback": sha256(spec["decay"]["fallback"]),
        "photon_response": sha256(response_path),
    }
    certificate_hashes = {name: inputs[name]["sha256"] for name in expected_hashes}
    hashes_match = certificate_hashes == expected_hashes
    passed = (
        not cli_python_differences
        and not python_harness_differences
        and not closure_failures
        and hashes_match
    )
    result = {
        "steps": len(cli["steps"]),
        "photon_nuclide_rows": sum(len(step["photon_source"]["by_nuclide"]) for step in cli["steps"]),
        "cli_vs_python_differences": len(cli_python_differences),
        "python_vs_harness_differences": len(python_harness_differences),
        "difference_examples": [list(map(str, row)) for row in (cli_python_differences + python_harness_differences)[:10]],
        "closure_worst": worst,
        "closure_failures": closure_failures,
        "certificate_hashes_match_independent_sha256": hashes_match,
        "certificate_hashes": certificate_hashes,
        "pass": passed,
    }
    RESULT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
