#!/usr/bin/env python3
"""P21 G0 producer: frozen identity battery + baseline profile at the opening commit.

Executes the released FNS iron public example through the CLI cold path, the CLI
warm (prepared-cache) path, the Python extension, and a one-cell mesh run, then
records each surface's normalized-result SHA-256. The candidate-phase checks
re-run this battery and require byte-identical normalized results whenever the
new P21 mesh options are absent; the mesh surface additionally tolerates exactly
the additive header/footer fields the frozen protocol declares.

Also executes the baseline memory/throughput profile: deterministic canonical
fluxes of 256 and 1,000 cells over the pinned TENDL-2025 library, threads=2,
chunk_cells=64, peak RSS measured by GNU time.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g0_p21_identity_baseline.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
PYTHON = Path(os.environ.get("ACTINV_PYTHON", sys.executable))
PYTHON_LIBRARY = Path(
    os.environ.get("ACTINV_PYTHON_LIBRARY", ROOT / "python/target/release/libactinv.so")
)
GROUPS_JSON = ROOT / "crates/actinv-data/data/fispact_709_groups.json"
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
LIBRARY = ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz"
DECAY = ROOT / "actinv-data/v1.0.0/decay/endf-b-viii-0_decay.dat"
THREADS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)
PROFILE_SIZES = (256, 1000)
PROFILE_THREADS = 2
PROFILE_CHUNK = 64


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
        cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=False,
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


def write_canonical_flux(path: Path, spectra: list[list[float]]) -> None:
    """Deterministic actinv-flux-1 writer; one source descriptor sidecar."""
    bounds = np.load(LIBRARY)["bounds"].tolist()
    descriptor = path.with_suffix(".source.json")
    descriptor.write_text(
        json.dumps({"fixture": "P21 deterministic mesh flux", "cells": len(spectra)})
    )
    n = len(spectra)
    header = {
        "record": "header",
        "schema": "actinv-flux-1",
        "source": {
            "format": "p21-control",
            "path": str(descriptor),
            "sha256": sha256(descriptor),
        },
        "energy_boundaries_eV": bounds,
        "flux_units": "n cm^-2 s^-1",
        "cell_count": n,
        "geometry": {
            "kind": "rectilinear",
            "dimension": [n, 1, 1],
            "axis_boundaries_cm": [
                [float(i) for i in range(n + 1)], [0.0, 1.0], [0.0, 1.0]
            ],
        },
    }
    lines = [json.dumps(header)]
    total = 0.0
    for ordinal, spectrum in enumerate(spectra):
        cell_total = math.fsum(spectrum)
        total += cell_total
        lines.append(json.dumps({
            "record": "cell",
            "ordinal": ordinal,
            "id": f"cell-{ordinal}",
            "index": [ordinal + 1, 1, 1],
            "bounds_cm": [[float(ordinal), float(ordinal + 1)], [0.0, 1.0], [0.0, 1.0]],
            "volume_cm3": 1.0,
            "flux_per_group": spectrum,
            "flux_total": cell_total,
        }))
    lines.append(json.dumps({
        "record": "footer",
        "cell_count": n,
        "flux_sum_over_cells": total,
        "volume_integrated_flux": total,
    }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mesh_specification(specification: dict, canonical: Path) -> dict:
    mesh = {k: v for k, v in specification.items() if k != "spectrum"}
    mesh["spec"] = "actinv-mesh-spec-1"
    mesh["flux"] = {"path": str(canonical), "sha256": sha256(canonical)}
    return mesh


def run_mesh(spec_path: Path, output: Path, env: dict[str, str]) -> tuple[float, dict]:
    completed = subprocess.run(
        ["/usr/bin/time", "-v", str(ACTINV), "mesh", str(spec_path), str(output)],
        cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800, check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"actinv mesh failed: {(completed.stdout + completed.stderr)[-3000:]}")
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", completed.stderr)
    if match is None:
        raise RuntimeError("GNU time did not expose mesh peak RSS")
    return int(match.group(1)) * 1024, json.loads(completed.stdout)


def hardware_record() -> dict:
    record = {"platform": sys.platform}
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        model = re.search(r"model name\s*:\s*(.+)", cpuinfo)
        record["cpu_model"] = model.group(1).strip() if model else None
        record["logical_cpus"] = os.cpu_count()
    except OSError:
        pass
    try:
        meminfo = Path("/proc/meminfo").read_text()
        total = re.search(r"MemTotal:\s*(\d+) kB", meminfo)
        record["mem_total_bytes"] = int(total.group(1)) * 1024 if total else None
    except OSError:
        pass
    record["kernel"] = subprocess.run(
        ["uname", "-r"], text=True, capture_output=True
    ).stdout.strip()
    record["rustc"] = subprocess.run(
        ["rustc", "--version"], text=True, capture_output=True
    ).stdout.strip()
    cgroup = {}
    for name in ("memory.max", "cpu.max", "pids.max"):
        node = Path("/sys/fs/cgroup") / name
        try:
            cgroup[name] = node.read_text().strip()
        except OSError:
            cgroup[name] = None
    record["cgroup"] = cgroup
    record["env_cgroup_enforced"] = {
        "MemoryMax": os.environ.get("P21_MEMORY_MAX", "6G (invocation-scoped)"),
    }
    return record


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

    with tempfile.TemporaryDirectory(
        prefix="actinv-p21-g0-", dir=ROOT / "target"
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
        lines = [" ".join(str(v) for v in values[i : i + 6]) for i in range(0, len(values), 6)]
        fluxes.write_text("\n".join(lines) + "\n0.5\nP21 identity battery cell\n", encoding="utf-8")
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
        mesh_spec = work / "mesh.json"
        mesh_sha = sha256(canonical_flux)
        mesh_spec.write_text(
            json.dumps(mesh_specification(specification, canonical_flux), sort_keys=True)
            + "\n",
            encoding="utf-8",
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

        # ---- baseline memory/throughput profile on the pinned library
        profile_env = os.environ.copy()
        profile_env["RAYON_NUM_THREADS"] = str(PROFILE_THREADS)
        base = list(reversed(values))
        base_sum = math.fsum(base)
        normalized_flux = [
            v * specification["spectrum"]["total"] / base_sum for v in base
        ]
        scales = [1e-6, 1e-3, 1.0, 1e3]
        profiles = []
        for cells in PROFILE_SIZES:
            spectra = [
                [
                    v * scales[i % len(scales)] * (1.0 + 0.13 * math.sin(i * 0.7))
                    for v in normalized_flux
                ]
                for i in range(cells)
            ]
            canonical = work / f"flux-{cells}.ndjson"
            write_canonical_flux(canonical, spectra)
            mesh = mesh_specification(specification, canonical)
            mesh["chunk_cells"] = PROFILE_CHUNK
            mesh["threads"] = PROFILE_THREADS
            p = work / f"mesh-{cells}.json"
            p.write_text(json.dumps(mesh, sort_keys=True) + "\n", encoding="utf-8")
            out = work / f"mesh-{cells}.result.ndjson"
            rss, summary = run_mesh(p, out, profile_env)
            profiles.append({
                "cells": cells,
                "threads": PROFILE_THREADS,
                "chunk_cells": PROFILE_CHUNK,
                "wall_time_s": summary["wall_time_s"],
                "cells_per_s": summary["cells_per_s"],
                "peak_rss_bytes": rss,
                "output_bytes": out.stat().st_size,
                "canonical_flux_bytes": canonical.stat().st_size,
            })

    surfaces = {
        "cli_cold": canonical_sha256(cli_cold),
        "cli_warm": canonical_sha256(cli_warm),
        "python": canonical_sha256(python_result),
        "mesh_cell": canonical_sha256(cell_results[0]),
    }
    evidence = {
        "schema": "actinv-p21-g0-baseline-1",
        "inputs": {
            "spec_sha256": canonical_sha256(specification),
            "library_path": specification["library"]["path"],
            "library_sha256": observed,
            "decay_primary": specification["decay"]["primary"],
            "decay_fallback": specification["decay"]["fallback"],
            "mesh_flux_sha256": mesh_sha,
        },
        "normalized_result_sha256": surfaces,
        "cli_cold_eq_warm": surfaces["cli_cold"] == surfaces["cli_warm"],
        "cli_eq_python": surfaces["cli_cold"] == surfaces["python"],
        "note": "Normalized results strip timing and entry-point fields only.",
        "baseline_profile": {
            "hardware": hardware_record(),
            "runs": profiles,
        },
    }
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"surfaces": surfaces, "profile": profiles}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
