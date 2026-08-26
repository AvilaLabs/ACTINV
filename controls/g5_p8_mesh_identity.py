#!/usr/bin/env python3
"""P8-G5: mesh/single-cell identity, ordering, determinism and atomic failure."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np

from p8_fixtures import BIN, command, ensure_ci_library, sha256, write_json

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
SCALES = [0.0, 1e-12, 1e-9, 1e-6, 1e-3, 1.0, 1e3, 1e6]


def canonical_flux(path: Path, library: Path, spectra: list[list[float]], *, bad_cell: int | None = None) -> None:
    bounds = np.load(library)["bounds"].tolist()
    descriptor = path.with_suffix(".source.json")
    write_json(descriptor, {"fixture": "P8 exact-library-grid independent cells", "cells": len(spectra)})
    x_bounds = [float(index) for index in range(len(spectra) + 1)]
    header = {
        "record": "header",
        "schema": "actinv-flux-1",
        "source": {"format": "p8-control", "path": str(descriptor), "sha256": sha256(descriptor)},
        "energy_boundaries_eV": bounds,
        "flux_units": "n cm^-2 s^-1",
        "cell_count": len(spectra),
        "geometry": {
            "kind": "rectilinear",
            "dimension": [len(spectra), 1, 1],
            "axis_boundaries_cm": [x_bounds, [0.0, 1.0], [0.0, 1.0]],
        },
    }
    records = [header]
    totals = []
    for ordinal, spectrum in enumerate(spectra):
        values = list(spectrum)
        if bad_cell == ordinal:
            values[0] = -1.0
        total = math.fsum(values)
        totals.append(total)
        records.append(
            {
                "record": "cell",
                "ordinal": ordinal,
                "id": f"cell-{ordinal}",
                "index": [ordinal + 1, 1, 1],
                "bounds_cm": [[float(ordinal), float(ordinal + 1)], [0.0, 1.0], [0.0, 1.0]],
                "volume_cm3": 1.0,
                "flux_per_group": values,
                "flux_total": total,
            }
        )
    records.append(
        {
            "record": "footer",
            "cell_count": len(spectra),
            "flux_sum_over_cells": math.fsum(totals),
            "volume_integrated_flux": math.fsum(totals),
        }
    )
    path.write_text("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records))


def shared_problem(library: Path, decay: Path) -> dict:
    return {
        "title": "P8 eight-cell identity",
        "library": {"path": str(library), "sha256": sha256(library)},
        "decay": {"primary": str(decay)},
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"FE": 100.0}},
        "schedule": [{"dt": "300 s", "flux": 1.0}, {"dt": "60 s", "flux": 0.0}],
        "options": {
            "mode": "trace",
            "prune": "rate",
            "bmin_atoms_per_g": 1e-8,
            "temperature_K": 293.6,
            "outputs": ["inventory", "heat", "ledger", "certificate"],
        },
        "photon": {},
    }


def normalized_result(value: dict) -> dict:
    value = json.loads(json.dumps(value))
    value.pop("ms", None)
    value.pop("entry_point", None)
    value.get("certificate", {}).pop("entry_point", None)
    return value


def contains_key(value, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(contains_key(child, key) for child in value)
    return False


def read_mesh(path: Path) -> tuple[dict, list[dict], dict]:
    records = [json.loads(line) for line in path.read_text().splitlines()]
    return records[0], records[1:-1], records[-1]


def main() -> None:
    work = Path(os.environ.get("ACTINV_P8_WORK", tempfile.mkdtemp(prefix="actinv-p8-g5-"))) / "g5"
    work.mkdir(parents=True, exist_ok=True)
    library, decay = ensure_ci_library(work)
    example = json.loads((ROOT / "examples" / "fns_fe_5min.json").read_text())
    base = list(reversed(example["spectrum"]["flux_per_group"]))
    base_sum = math.fsum(base)
    normalized = [value * example["spectrum"]["total"] / base_sum for value in base]
    spectra = [[value * scale for value in normalized] for scale in SCALES]
    canonical = work / "eight_cells.ndjson"
    canonical_flux(canonical, library, spectra)
    shared = shared_problem(library, decay)

    outputs = {}
    for threads in (1, 4):
        mesh_spec = {
            "spec": "actinv-mesh-spec-1",
            **shared,
            "flux": {"path": str(canonical), "sha256": sha256(canonical)},
            "chunk_cells": 3,
            "threads": threads,
        }
        spec_path = work / f"mesh-{threads}t.json"
        result_path = work / f"mesh-{threads}t.ndjson"
        write_json(spec_path, mesh_spec)
        command([BIN, "mesh", spec_path, result_path])
        outputs[threads] = result_path

    header_one, cells_one, footer_one = read_mesh(outputs[1])
    header_many, cells_many, footer_many = read_mesh(outputs[4])
    footer_one.pop("wall_time_s")
    footer_one.pop("cells_per_s")
    footer_many.pop("wall_time_s")
    footer_many.pop("cells_per_s")
    raw_one = outputs[1].read_bytes().splitlines()
    raw_many = outputs[4].read_bytes().splitlines()

    identity = []
    for ordinal, spectrum in enumerate(spectra):
        ordinary_spec = {
            "spec": "actinv-spec-1",
            **shared,
            "spectrum": {
                "structure": "custom",
                "flux_per_group": spectrum,
                "boundaries_eV": np.load(library)["bounds"].tolist(),
                "descending": False,
            },
        }
        spec_path, result_path = work / f"ordinary-{ordinal}.json", work / f"ordinary-{ordinal}.result.json"
        write_json(spec_path, ordinary_spec)
        command([BIN, "run", spec_path, result_path])
        ordinary = json.loads(result_path.read_text())
        mesh_result = cells_one[ordinal]["result"]
        identity.append(normalized_result(mesh_result) == normalized_result(ordinary))

    pruned = [cell["result"]["pruned_states"] for cell in cells_one]
    exact_rebin = all(
        cell["rebin"]["method"] == "copy"
        and cell["rebin"]["underflow"] == 0.0
        and cell["rebin"]["overflow"] == 0.0
        and cell["rebin"]["relative_closure"] == 0.0
        for cell in cells_one
    )

    failed_canonical = work / "failed_cell.ndjson"
    canonical_flux(failed_canonical, library, spectra, bad_cell=4)
    failed_spec = {
        "spec": "actinv-mesh-spec-1",
        **shared,
        "flux": {"path": str(failed_canonical), "sha256": sha256(failed_canonical)},
        "chunk_cells": 3,
        "threads": 4,
    }
    failed_spec_path, failed_output = work / "failed_cell.json", work / "failed_cell.result.ndjson"
    write_json(failed_spec_path, failed_spec)
    failed = command([BIN, "mesh", failed_spec_path, failed_output], ok=False)
    failed_message = (failed.stdout + failed.stderr).strip()

    output = {
        "cells": len(cells_one),
        "chunks": math.ceil(len(cells_one) / 3),
        "ordinary_identity_per_cell": identity,
        "exact_grid_copy_ledgers": exact_rebin,
        "pruned_states": pruned,
        "distinct_pruned_state_counts": len(set(pruned)),
        "thread_determinism": {
            "header_bytes_identical": raw_one[0] == raw_many[0],
            "cell_bytes_identical": raw_one[1:-1] == raw_many[1:-1],
            "normalized_footer_identical": footer_one == footer_many,
        },
        "per_cell_timing_absent": all(not contains_key(cell["result"], "ms") for cell in cells_one),
        "failed_cell": {
            "message": failed_message,
            "cell_named": "cell-4" in failed_message,
            "no_final_output": not failed_output.exists(),
        },
        "certificate_hash_bound": header_one["certificate"]["canonical_flux"]["sha256_computed"]
        == sha256(canonical),
    }
    output["pass"] = bool(
        output["cells"] == 8
        and output["chunks"] >= 3
        and all(identity)
        and exact_rebin
        and output["distinct_pruned_state_counts"] >= 3
        and all(output["thread_determinism"].values())
        and output["per_cell_timing_absent"]
        and all(output["failed_cell"].values())
        and output["certificate_hash_bound"]
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g5_p8_mesh_identity.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
