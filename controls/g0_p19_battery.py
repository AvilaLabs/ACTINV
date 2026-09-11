#!/usr/bin/env python3
"""P19 G0 producer: run the frozen identity battery at the parent commit.

Executes the released FNS iron public example through the CLI cold path, the CLI
warm (prepared-cache) path, the Python extension, and a one-cell mesh run, then
records each surface's normalized-result SHA-256. The candidate-phase check
re-runs this battery and requires byte-identical normalized results whenever the
new P19 `self_shielding` section is absent.
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
RESULT = ROOT / "results/g0_p19_identity_baseline.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
PYTHON = Path(os.environ.get("ACTINV_PYTHON", sys.executable))
PYTHON_LIBRARY = Path(
    os.environ.get("ACTINV_PYTHON_LIBRARY", ROOT / "python/target/release/libactinv.so")
)
GROUPS_JSON = ROOT / "crates/actinv-data/data/fispact_709_groups.json"
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
THREADS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalized(result: dict) -> dict:
    value = dict(result)
    value.pop("ms", None)
    if "entry_point" in value:
        value["entry_point"] = "normalized"
    if isinstance(value.get("certificate"), dict):
        value["certificate"] = dict(value["certificate"])
        value["certificate"]["entry_point"] = "normalized"
    return value


def environment(cache: Path) -> dict[str, str]:
    value = os.environ.copy()
    value["ACTINV_CACHE_DIR"] = str(cache)
    for name in THREADS:
        value[name] = "1"
    return value


def run_cli(spec: Path, output: Path, env: dict[str, str]) -> dict:
    completed = subprocess.run(
        [str(ACTINV), "run", str(spec), str(output)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"actinv run failed: {completed.stderr[-4000:]}")
    return json.loads(output.read_text(encoding="utf-8"))


def run_python(spec_text: str, env: dict[str, str]) -> dict:
    os.environ.update(env)
    try:
        module_spec = importlib.util.spec_from_file_location("actinv", PYTHON_LIBRARY)
        if module_spec is None or module_spec.loader is None:
            raise RuntimeError(f"cannot load Python extension {PYTHON_LIBRARY}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        return json.loads(module.run(spec_text))
    finally:
        os.environ.pop("ACTINV_CACHE_DIR", None)


def main() -> None:
    specification = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    library = ROOT / specification["library"]["path"]
    declared = specification["library"]["sha256"]
    observed = sha256(library)
    if observed != declared:
        raise RuntimeError(f"library sha256 {observed} != declared {declared}")
    for role in ("primary", "fallback"):
        specification["decay"][role] = str(ROOT / specification["decay"][role])
    specification["library"]["path"] = str(library)

    with tempfile.TemporaryDirectory(prefix="actinv-p19-g0-") as directory:
        work = Path(directory)
        cache = work / "cache"
        spec_path = work / "problem.json"
        spec_text = json.dumps(specification, sort_keys=True) + "\n"
        spec_path.write_text(spec_text, encoding="utf-8")
        env = environment(cache)

        cli_cold = normalized(run_cli(spec_path, work / "cli_cold.json", env))
        cli_warm = normalized(run_cli(spec_path, work / "cli_warm.json", env))
        python_result = normalized(run_python(spec_text, env))

        # One-cell mesh leg: write the example spectrum as a FISPACT fluxes file,
        # canonicalize through the real importer, then run a single-cell mesh.
        fluxes = work / "fluxes"
        values = specification["spectrum"]["flux_per_group"]
        lines = [" ".join(str(v) for v in values[i : i + 6]) for i in range(0, len(values), 6)]
        fluxes.write_text("\n".join(lines) + "\n0.5\nP19 identity battery cell\n", encoding="utf-8")
        canonical_flux = work / "flux.ndjson"
        completed = subprocess.run(
            [
                str(ACTINV), "import-flux", "fispact", str(fluxes), str(canonical_flux),
                "--groups", str(GROUPS_JSON),
            ],
            cwd=ROOT, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"import-flux failed: {completed.stderr[-4000:]}")
        mesh_specification = {
            key: value for key, value in specification.items() if key != "spectrum"
        }
        mesh_specification["spec"] = "actinv-mesh-spec-1"
        mesh_specification["flux"] = {
            "path": str(canonical_flux),
            "sha256": sha256(canonical_flux),
        }
        mesh_spec = work / "mesh.json"
        mesh_spec.write_text(
            json.dumps(mesh_specification, sort_keys=True) + "\n", encoding="utf-8"
        )
        mesh_out = work / "mesh_result.ndjson"
        completed = subprocess.run(
            [str(ACTINV), "mesh", str(mesh_spec), str(mesh_out)],
            cwd=ROOT, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"actinv mesh failed: {completed.stderr[-4000:]}")
        cell_results = []
        for line in mesh_out.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("record") == "cell":
                cell_results.append(normalized(record["result"]))
        if len(cell_results) != 1:
            raise RuntimeError(f"expected one mesh cell result, got {len(cell_results)}")

    surfaces = {
        "cli_cold": canonical_sha256(cli_cold),
        "cli_warm": canonical_sha256(cli_warm),
        "python": canonical_sha256(python_result),
        "mesh_cell": canonical_sha256(cell_results[0]),
    }
    evidence = {
        "schema": "actinv-p19-g0-baseline-1",
        "inputs": {
            "spec_sha256": canonical_sha256(specification),
            "library_path": specification["library"]["path"],
            "library_sha256": observed,
            "decay_primary": specification["decay"]["primary"],
            "decay_fallback": specification["decay"]["fallback"],
            "mesh_flux_sha256": mesh_specification["flux"]["sha256"],
        },
        "normalized_result_sha256": surfaces,
        "cli_cold_eq_warm": surfaces["cli_cold"] == surfaces["cli_warm"],
        "cli_eq_python": surfaces["cli_cold"] == surfaces["python"],
        "note": "Normalized results strip timing and entry-point fields only.",
    }
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(surfaces, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
