#!/usr/bin/env python3
"""P21-G4: absent-option surface identity on the post-change build.

Re-runs the frozen G0 battery's four surfaces — CLI cold, CLI warm
(prepared cache), the Python extension, and the one-cell mesh run — with
the current binaries and requires the normalized-result SHA-256 values to
equal the committed opening baseline exactly. This is the executed proof
that the new mesh spec fields change nothing when absent.

Emits ``results/g4_p21_surfaces.json``.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from g0_p21_battery import (
    ACTINV,
    EXAMPLE,
    GROUPS_JSON,
    LIBRARY,
    PYTHON_LIBRARY,
    ROOT,
    canonical_sha256,
    environment,
    mesh_specification,
    normalized,
    run_cli,
    run_python,
    sha256,
)

BASELINE = ROOT / "results/g0_p21_identity_baseline.json"
RESULT = ROOT / "results/g4_p21_surfaces.json"


def main() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    expected = baseline["normalized_result_sha256"]
    specification = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    if sha256(LIBRARY) != specification["library"]["sha256"]:
        raise RuntimeError("library sha256 does not match the example pin")
    for role in ("primary", "fallback"):
        specification["decay"][role] = str(ROOT / specification["decay"][role])
    specification["library"]["path"] = str(LIBRARY)

    with tempfile.TemporaryDirectory(
        prefix="actinv-p21-g4-", dir=ROOT / "target"
    ) as directory:
        work = Path(directory)
        cache = work / "cache"
        spec_path = work / "problem.json"
        spec_text = json.dumps(specification, sort_keys=True) + "\n"
        spec_path.write_text(spec_text, encoding="utf-8")
        env = environment(cache)

        cli_cold = normalized(run_cli(spec_path, work / "cli_cold.json", env))
        cli_warm = normalized(run_cli(spec_path, work / "cli_warm.json", env))
        python_result = normalized(run_python(spec_text, env))

        fluxes = work / "fluxes"
        values = specification["spectrum"]["flux_per_group"]
        lines = [
            " ".join(str(v) for v in values[i : i + 6])
            for i in range(0, len(values), 6)
        ]
        fluxes.write_text(
            "\n".join(lines) + "\n0.5\nP21 identity battery cell\n", encoding="utf-8"
        )
        canonical_flux = work / "flux.ndjson"
        completed = subprocess.run(
            [
                str(ACTINV),
                "import-flux",
                "fispact",
                str(fluxes),
                str(canonical_flux),
                "--groups",
                str(GROUPS_JSON),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"import-flux failed: {completed.stderr[-4000:]}")
        mesh_spec = work / "mesh.json"
        mesh_spec.write_text(
            json.dumps(mesh_specification(specification, canonical_flux), sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        mesh_out = work / "mesh_result.ndjson"
        completed = subprocess.run(
            [str(ACTINV), "mesh", str(mesh_spec), str(mesh_out)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"actinv mesh failed: {completed.stderr[-4000:]}")
        cells = [
            normalized(json.loads(line)["result"])
            for line in mesh_out.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("record") == "cell"
        ]
        if len(cells) != 1:
            raise RuntimeError(f"expected one mesh cell result, got {len(cells)}")

    observed = {
        "cli_cold": canonical_sha256(cli_cold),
        "cli_warm": canonical_sha256(cli_warm),
        "python": canonical_sha256(python_result),
        "mesh_cell": canonical_sha256(cells[0]),
    }
    evidence = {
        "schema": "actinv-p21-g4-surfaces-1",
        "baseline_record": str(BASELINE.relative_to(ROOT)),
        "expected_sha256": expected,
        "observed_sha256": observed,
        "per_surface_match": {
            name: observed[name] == expected[name] for name in expected
        },
        "binary_sha256": sha256(ACTINV),
        "python_library_sha256": sha256(PYTHON_LIBRARY),
        "note": (
            "Normalized results strip timing and entry-point fields only; "
            "equality against the opening baseline proves the new mesh spec "
            "fields change nothing when absent."
        ),
    }
    evidence["pass"] = all(evidence["per_surface_match"].values())
    RESULT.write_text(
        json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))
    raise SystemExit(0 if evidence["pass"] else 1)


if __name__ == "__main__":
    main()
