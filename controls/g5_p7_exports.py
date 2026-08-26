#!/usr/bin/env python3
"""P7-G5: independently parse OpenMC/MCNP source fragments and check conservation/syntax."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g5_p7_exports.json"


def executable(name: str, environment: str) -> Path:
    supplied = os.environ.get(environment)
    if supplied:
        return Path(supplied)
    for profile in ("debug", "release"):
        candidate = ROOT / "target" / profile / name
        if candidate.exists():
            return candidate
    raise RuntimeError(f"build {name} before running P7 G5")


def card_values(lines: list[str], prefix: str) -> list[float]:
    start = next(index for index, line in enumerate(lines) if line.startswith(prefix))
    first_fields = lines[start].split()
    values = first_fields[2:] if prefix == "SI1 L" else first_fields[1:]
    for line in lines[start + 1 :]:
        if not line.startswith("     "):
            break
        values.extend(line.split())
    return list(map(float, values))


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: g5_p7_exports.py DECAY.endf RESPONSE.json")
    decay_path, response_path = sys.argv[1:]
    probe = json.loads(
        subprocess.run(
            [str(executable("photon_probe", "ACTINV_PHOTON_PROBE")), decay_path, response_path],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    source = probe["combined"]["source"]
    expected_energy = [group["centroid_eV"] for group in source["groups"] if group["photons_s"] > 0.0]
    raw_weight = [group["photons_s"] for group in source["groups"] if group["photons_s"] > 0.0]
    weight_total = sum(raw_weight)
    expected_probability = [value / weight_total for value in raw_weight]

    with tempfile.TemporaryDirectory(prefix="actinv-p7-g5-") as temporary:
        temporary = Path(temporary)
        result_path = temporary / "result.json"
        openmc_path = temporary / "source.py"
        mcnp_path = temporary / "source.sdef"
        result_path.write_text(json.dumps({"steps": [{"step": 1, "photon_source": source}]}))
        cli = executable("actinv", "ACTINV_CLI")
        subprocess.run([str(cli), "export-openmc", str(result_path), "1", str(openmc_path)], check=True)
        subprocess.run([str(cli), "export-mcnp", str(result_path), "1", str(mcnp_path)], check=True)
        openmc = openmc_path.read_text()
        mcnp = mcnp_path.read_text()

        ast.parse(openmc, filename=str(openmc_path))
        discrete = re.search(r"Discrete\(\[([^]]+)\], \[([^]]+)\]\)", openmc)
        if not discrete:
            raise RuntimeError("OpenMC export has no Discrete distribution")
        openmc_energy = [float(value) for value in discrete.group(1).split(",")]
        openmc_probability = [float(value) for value in discrete.group(2).split(",")]
        openmc_strength = float(re.search(r"strength=([+\-.0-9eE]+)", openmc).group(1))

        lines = mcnp.splitlines()
        mcnp_energy_mev = card_values(lines, "SI1 L")
        mcnp_probability = card_values(lines, "SP1")
        mcnp_strength = float(re.search(r"\bWGT=([+\-.0-9eE]+)", mcnp).group(1))
        mcnp_energy = [value * 1.0e6 for value in mcnp_energy_mev]
        max_line_length = max(map(len, lines))
        continuation_ok = all(
            len(line) <= 5 or line.startswith("     ")
            for line in lines
            if line and line[0].isspace()
        )

        narrow_path = temporary / "narrow.json"
        narrow_path.write_text(
            json.dumps({"steps": [{"step": 1, "photon_source": probe["planted_narrow_groups"]["source"]}]})
        )
        rejected = subprocess.run(
            [str(cli), "export-openmc", str(narrow_path), "1", str(temporary / "bad.py")],
            capture_output=True,
            text=True,
        )

    expected_energy_relative = max(
        abs(a - b) / max(abs(a), abs(b), 1e-300) for a, b in zip(openmc_energy, expected_energy)
    )
    expected_probability_absolute = max(
        abs(a - b) for a, b in zip(openmc_probability, expected_probability)
    )
    identity = {
        "cross_export_energy_after_unit_conversion": all(
            a * 1.0e-6 == b for a, b in zip(openmc_energy, mcnp_energy_mev)
        ),
        "cross_export_probability": openmc_probability == mcnp_probability,
    }
    strength_relative = max(
        abs(openmc_strength - source["total_photons_s"]),
        abs(mcnp_strength - source["total_photons_s"]),
    ) / max(abs(source["total_photons_s"]), 1e-300)
    passed = (
        all(identity.values())
        and expected_energy_relative <= 1e-15
        and expected_probability_absolute <= 1e-15
        and abs(sum(openmc_probability) - 1.0) <= 1e-15
        and abs(sum(mcnp_probability) - 1.0) <= 1e-15
        and strength_relative <= 1e-12
        and "MODE P" in mcnp
        and "SDEF PAR=P" in mcnp
        and max_line_length <= 78
        and continuation_ok
        and rejected.returncode != 0
        and "omits" in rejected.stderr
    )
    result = {
        "nonzero_groups": len(expected_energy),
        "identity_at_float_parse": identity,
        "source_energy_max_relative": expected_energy_relative,
        "source_probability_max_absolute": expected_probability_absolute,
        "openmc_probability_sum": sum(openmc_probability),
        "mcnp_probability_sum": sum(mcnp_probability),
        "strength_relative": strength_relative,
        "openmc_python_syntax": "PASS",
        "mcnp_max_line_length": max_line_length,
        "mcnp_continuations": continuation_ok,
        "incomplete_group_export_rejected": rejected.returncode != 0 and "omits" in rejected.stderr,
        "pass": passed,
    }
    RESULT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
