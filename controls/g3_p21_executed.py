#!/usr/bin/env python3
"""P21-G3: executed large-scale case and measured memory bounds.

Executes — never extrapolates — the frozen P21 qualification case on the
pinned TENDL-2025 709-group library and ENDF-B-VIII.0 decay file:

- cell-count memory leg: 1,000 / 5,000 / 20,000 cells at fixed
  ``chunk_cells=64`` on the compact executed-case spec (reduced
  ``options.outputs`` plus ``cell_result_fields``); the frozen gate is
  peak-RSS span <= 64 MB across sizes;
- chunk-tracking leg: 1,000 cells at ``chunk_cells`` 16 vs 256 on the
  full-output spec (the published FNS example options), where serialized
  cell records are ~5 MB; the frozen gate is RSS(256) - RSS(16) >= 32 MB;
- grouping leg: 1,000 cells built from 25 repeated distinct spectra,
  grouped vs ``group_workloads: false``, recording the reuse count and
  the rate contrast on the same compact spec;
- the 20,000-cell run is the executed scale case: distinct non-repeating
  spectra, full hardware/cgroup/version record, input and output hashes.

All legs run ``threads=2`` under the bounded invocation environment; peak
RSS is measured by GNU ``time -v`` on the child process. Emits
``results/g3_p21_executed.json``.
"""
from __future__ import annotations

import hashlib
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
RESULT = ROOT / "results/g3_p21_executed.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
LIBRARY = ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz"
DECAY = ROOT / "actinv-data/v1.0.0/decay/endf-b-viii-0_decay.dat"

SIZE_CELLS = (1_000, 5_000, 20_000)
SIZE_CHUNK = 64
CHUNK_LEG_CELLS = 1_000
CHUNK_LEGS = (16, 256)
CHUNK_SLACK_BYTES = 64 << 20
CHUNK_DELTA_BYTES = 32 << 20
SIZE_SLACK_BYTES = 64 << 20
GROUP_CELLS = 1_000
GROUP_DISTINCT = 25
THREADS = 2

EXECUTED_FIELDS = [
    "spec_title",
    "mode",
    "pruned_states",
    "total_states",
    "steps",
    "pathway_closure",
    "certificate",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_canonical_flux(path: Path, spectra: list[list[float]]) -> None:
    bounds = np.load(LIBRARY)["bounds"].tolist()
    descriptor = path.with_suffix(".source.json")
    descriptor.write_text(
        json.dumps({"fixture": "P21 executed-case flux", "cells": len(spectra)})
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
                [float(i) for i in range(n + 1)],
                [0.0, 1.0],
                [0.0, 1.0],
            ],
        },
    }
    total = 0.0
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header) + "\n")
        for ordinal, spectrum in enumerate(spectra):
            cell_total = math.fsum(spectrum)
            total += cell_total
            handle.write(
                json.dumps(
                    {
                        "record": "cell",
                        "ordinal": ordinal,
                        "id": f"cell-{ordinal}",
                        "index": [ordinal + 1, 1, 1],
                        "bounds_cm": [
                            [float(ordinal), float(ordinal + 1)],
                            [0.0, 1.0],
                            [0.0, 1.0],
                        ],
                        "volume_cm3": 1.0,
                        "flux_per_group": spectrum,
                        "flux_total": cell_total,
                    }
                )
                + "\n"
            )
        handle.write(
            json.dumps(
                {
                    "record": "footer",
                    "cell_count": n,
                    "flux_sum_over_cells": total,
                    "volume_integrated_flux": total,
                }
            )
            + "\n"
        )


def base_spectrum() -> list[float]:
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    values = list(reversed(example["spectrum"]["flux_per_group"]))
    total = math.fsum(values)
    return [v * example["spectrum"]["total"] / total for v in values]


def distinct_spectra(cells: int) -> list[list[float]]:
    """Every cell distinct: smooth scale + a per-cell shape modulation."""
    base = base_spectrum()
    groups = len(base)
    return [
        [
            v
            * (1.0 + 0.13 * math.sin(i * 0.7))
            * (1.0 + 0.02 * math.sin(i * 1.31 + g * 0.017))
            * 10.0 ** ((i % 7) - 3)
            for g, v in enumerate(base)
        ]
        for i in range(cells)
    ]


def repeated_spectra(cells: int, distinct: int) -> list[list[float]]:
    base = base_spectrum()
    groups = len(base)
    shapes = [
        [
            v * (1.0 + 0.11 * math.sin(k * 0.9 + g * 0.013)) * 10.0 ** ((k % 5) - 2)
            for g, v in enumerate(base)
        ]
        for k in range(distinct)
    ]
    return [shapes[i % distinct] for i in range(cells)]


def compact_spec(canonical: Path) -> dict:
    return {
        "spec": "actinv-mesh-spec-1",
        "title": "P21 executed scale case",
        "library": {"path": str(LIBRARY), "sha256": sha256(LIBRARY)},
        "decay": {"primary": str(DECAY)},
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"FE": 100.0}},
        "schedule": [{"dt": "300 s", "flux": 1.0}, {"dt": "60 s", "flux": 0.0}],
        "options": {
            "mode": "trace",
            "prune": "rate",
            "bmin_atoms_per_g": 1e-8,
            "temperature_K": 293.6,
            "outputs": ["inventory", "heat", "ledger", "certificate"],
        },
        "flux": {"path": str(canonical), "sha256": sha256(canonical)},
        "chunk_cells": SIZE_CHUNK,
        "threads": THREADS,
        "cell_result_fields": EXECUTED_FIELDS,
    }


def full_spec(canonical: Path, chunk: int) -> dict:
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    spec = {k: v for k, v in example.items() if k != "spectrum"}
    spec["spec"] = "actinv-mesh-spec-1"
    spec["title"] = "P21 chunk-tracking case"
    spec["library"] = {"path": str(LIBRARY), "sha256": sha256(LIBRARY)}
    spec["decay"] = {"primary": str(DECAY)}
    spec["flux"] = {"path": str(canonical), "sha256": sha256(canonical)}
    spec["chunk_cells"] = chunk
    spec["threads"] = THREADS
    return spec


def run_mesh(spec: dict, work: Path, name: str, timeout: int) -> dict:
    # Each completed leg's measurement is persisted immediately, so a
    # restarted control reuses only legs that actually executed.
    legs_dir = work / "legs"
    legs_dir.mkdir(exist_ok=True)
    record_path = legs_dir / f"{name}.json"
    if record_path.exists():
        return json.loads(record_path.read_text(encoding="utf-8"))
    spec_path = work / f"{name}.json"
    output = work / f"{name}.ndjson"
    spec_path.write_text(json.dumps(spec, sort_keys=True) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env["RAYON_NUM_THREADS"] = str(THREADS)
    completed = subprocess.run(
        ["/usr/bin/time", "-v", str(ACTINV), "mesh", str(spec_path), str(output)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"actinv mesh {name} failed: {(completed.stdout + completed.stderr)[-3000:]}"
        )
    match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)", completed.stderr
    )
    if match is None:
        raise RuntimeError(f"GNU time exposed no peak RSS for {name}")
    summary = json.loads(completed.stdout)
    # Stream the output: chunk-leg records can be multi-GB; only the record
    # count and the final footer line are needed.
    records = 0
    last_line = b""
    with output.open("rb") as handle:
        for line in handle:
            if line.strip():
                records += 1
                last_line = line
    footer = json.loads(last_line)
    record = {
        "cells": footer["cell_count"],
        "threads": spec["threads"],
        "chunk_cells": spec["chunk_cells"],
        "cell_result_fields": spec.get("cell_result_fields"),
        "wall_time_s": summary["wall_time_s"],
        "cells_per_s": summary["cells_per_s"],
        "peak_rss_bytes": int(match.group(1)) * 1024,
        "output_bytes": output.stat().st_size,
        "output_sha256": sha256(output),
        "output_records": records,
        "cells_served_from_reuse": footer.get("cells_served_from_reuse"),
        "canonical_flux_sha256": spec["flux"]["sha256"],
        "canonical_flux_bytes": Path(spec["flux"]["path"]).stat().st_size,
    }
    record_path.write_text(
        json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record


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
        total = re.search(r"MemTotal:\s*(\d+) kB", Path("/proc/meminfo").read_text())
        record["mem_total_bytes"] = int(total.group(1)) * 1024 if total else None
    except OSError:
        pass
    for name, argv in (
        ("kernel", ["uname", "-r"]),
        ("rustc", ["rustc", "--version"]),
        ("actinv", [str(ACTINV), "--version"]),
    ):
        try:
            record[name] = subprocess.run(
                argv, text=True, capture_output=True, timeout=30
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            record[name] = None
    cgroup = {}
    for name in ("memory.max", "memory.swap.max", "cpu.max", "pids.max"):
        node = Path("/sys/fs/cgroup") / name
        try:
            cgroup[name] = node.read_text().strip()
        except OSError:
            cgroup[name] = None
    record["cgroup"] = cgroup
    return record


def main() -> None:
    if not LIBRARY.exists() or not DECAY.exists():
        raise SystemExit(f"pinned inputs absent: {LIBRARY} / {DECAY}")
    work_root = Path(
        os.environ.get("ACTINV_P21_WORK", tempfile.mkdtemp(prefix="actinv-p21-g3-"))
    )
    work_root.mkdir(parents=True, exist_ok=True)
    work = work_root / "g3"
    work.mkdir(exist_ok=True)

    legs: dict[str, dict] = {}
    fluxes: dict[int, Path] = {}

    # -- cell-count memory leg on the compact executed-case spec ----------
    for cells in SIZE_CELLS:
        canonical = work / f"flux-{cells}.ndjson"
        if not canonical.exists():
            write_canonical_flux(canonical, distinct_spectra(cells))
        fluxes[cells] = canonical
        spec = compact_spec(canonical)
        legs[f"size_{cells}"] = run_mesh(
            spec, work, f"size-{cells}", timeout=3_600 * 3
        )

    # -- chunk-tracking leg at fixed cell count, full-output spec ---------
    canonical = fluxes[SIZE_CELLS[0]]
    for chunk in CHUNK_LEGS:
        spec = full_spec(canonical, chunk)
        legs[f"chunk_{chunk}"] = run_mesh(
            spec, work, f"chunk-{chunk}", timeout=3_600
        )

    # -- grouping leg: repeated spectra, grouped vs ungrouped -------------
    repeated = work / f"flux-repeat-{GROUP_CELLS}.ndjson"
    if not repeated.exists():
        write_canonical_flux(repeated, repeated_spectra(GROUP_CELLS, GROUP_DISTINCT))
    for name, extra in (("group_on", {}), ("group_off", {"group_workloads": False})):
        spec = compact_spec(repeated)
        spec.update(extra)
        legs[name] = run_mesh(spec, work, name, timeout=3_600)

    rss_sizes = [legs[f"size_{cells}"]["peak_rss_bytes"] for cells in SIZE_CELLS]
    rss_chunk = [legs[f"chunk_{chunk}"]["peak_rss_bytes"] for chunk in CHUNK_LEGS]
    executed = legs[f"size_{SIZE_CELLS[-1]}"]

    evidence = {
        "schema": "actinv-p21-g3-executed-1",
        "executed_case": {
            "cells": SIZE_CELLS[-1],
            "distinct_spectra": True,
            "library": str(LIBRARY),
            "library_sha256": sha256(LIBRARY),
            "decay": str(DECAY),
            "decay_sha256": sha256(DECAY),
            "material": "Fe 100 wt%, 1.0 g",
            "schedule": "300 s irradiation + 60 s decay (two steps)",
            "cell_result_fields": EXECUTED_FIELDS,
            "options_outputs": ["inventory", "heat", "ledger", "certificate"],
            "result": executed,
        },
        "legs": legs,
        "memory_gates": {
            "cell_count_span_bytes": max(rss_sizes) - min(rss_sizes),
            "cell_count_span_limit_bytes": SIZE_SLACK_BYTES,
            "cell_count_flat": max(rss_sizes) - min(rss_sizes) <= SIZE_SLACK_BYTES,
            "chunk_delta_bytes": rss_chunk[1] - rss_chunk[0],
            "chunk_delta_limit_bytes": CHUNK_DELTA_BYTES,
            "chunk_tracked": rss_chunk[1] - rss_chunk[0] >= CHUNK_DELTA_BYTES,
        },
        "grouping": {
            "cells": GROUP_CELLS,
            "distinct_spectra": GROUP_DISTINCT,
            "reuse_grouped": legs["group_on"]["cells_served_from_reuse"],
            "reuse_expected": GROUP_CELLS - GROUP_DISTINCT,
            "rate_grouped": legs["group_on"]["cells_per_s"],
            "rate_ungrouped": legs["group_off"]["cells_per_s"],
        },
        "hardware": hardware_record(),
        "actinv_binary_sha256": sha256(ACTINV),
        "head_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True
        ).stdout.strip(),
        "note": (
            "Executed evidence only: every leg ran end-to-end under the "
            "recorded environment. No extrapolated cell counts are claimed."
        ),
    }
    evidence["pass"] = bool(
        evidence["memory_gates"]["cell_count_flat"]
        and evidence["memory_gates"]["chunk_tracked"]
        and executed["cells"] == SIZE_CELLS[-1]
        and executed["output_records"] == SIZE_CELLS[-1] + 2
        and legs["group_on"]["cells_served_from_reuse"] == GROUP_CELLS - GROUP_DISTINCT
        and legs["group_off"]["cells_served_from_reuse"] == 0
    )
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"pass": evidence["pass"], "memory_gates": evidence["memory_gates"],
                      "executed_wall_s": executed["wall_time_s"],
                      "executed_cells_per_s": executed["cells_per_s"]}, indent=2))
    raise SystemExit(0 if evidence["pass"] else 1)


if __name__ == "__main__":
    main()
