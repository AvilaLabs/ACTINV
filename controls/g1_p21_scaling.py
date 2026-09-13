#!/usr/bin/env python3
"""P21-G1: signature grouping, selectable cell fields, memory guard.

Runs the mesh path on the CI library with a deterministic 24-cell canonical
flux whose 24 spectra repeat 6 distinct shapes exactly (4 repetitions each,
interleaved so every chunk contains repeats). Asserts:

- a grouped run emits cell records bit-identical to ``group_workloads:
  false``, with footers equal modulo ``cells_served_from_reuse`` and the
  timing fields; the grouped footer records exactly the expected reuse
  count (24 - 6 = 18);
- grouping reuses across chunk boundaries (``chunk_cells: 3``);
- headers of the two runs differ only in ``spec_fingerprint_sha256`` (the
  grouping flag is part of the resume binding; it never alters bytes);
- ``cell_result_fields`` keeps only the named top-level result fields per
  cell record, the footer still closes, and the reduced cells match the
  full run on their shared fields;
- an absent ``cell_result_fields`` emits the complete result object;
- a bogus ``cell_result_fields`` entry is rejected by name;
- ``memory_limit_bytes`` below the requirement aborts with the named error
  carrying both numbers and leaves no output (atomic fresh write).

Emits ``results/g1_p21_grouping.json``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np

from p8_fixtures import BIN, command, ensure_ci_library, sha256, write_json

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

REPEATS = 4
DISTINCT = 6
CELLS = DISTINCT * REPEATS
SELECT_FIELDS = ["pruned_states", "total_states", "steps"]
FULL_RESULT_FIELDS = {
    "spec_title",
    "entry_point",
    "mode",
    "pruned_states",
    "total_states",
    "steps",
    "pathways",
    "pathway_closure",
    "ledger",
    "certificate",
}


def canonical_flux(path: Path, library: Path, spectra: list[list[float]]) -> None:
    bounds = np.load(library)["bounds"].tolist()
    descriptor = path.with_suffix(".source.json")
    write_json(
        descriptor,
        {"fixture": "P21 repeated-spectrum grouping case", "cells": len(spectra)},
    )
    x_bounds = [float(index) for index in range(len(spectra) + 1)]
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
        total = math.fsum(spectrum)
        totals.append(total)
        records.append(
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
                "flux_per_group": list(spectrum),
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
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)
    )


def spectra(library: Path) -> list[list[float]]:
    groups = len(np.load(library)["bounds"]) - 1
    distinct = []
    for shape in range(DISTINCT):
        # Deterministic, non-trivial distinct spectra: a decaying envelope
        # modulated by the shape index.
        spectrum = [
            (shape + 1) * 1e6 * math.exp(-index / (40.0 + 10 * shape))
            for index in range(groups)
        ]
        distinct.append(spectrum)
    # Interleave so every chunk of 3 holds repeats: ordinals cycle shapes.
    return [distinct[ordinal % DISTINCT] for ordinal in range(CELLS)]


def mesh_spec(shared: dict, canonical: Path, **extra) -> dict:
    spec = {
        "spec": "actinv-mesh-spec-1",
        **shared,
        "flux": {"path": str(canonical), "sha256": sha256(canonical)},
        "chunk_cells": 3,
        "threads": 2,
    }
    spec.update(extra)
    return spec


def main() -> None:
    work = Path(
        os.environ.get("ACTINV_P21_WORK", tempfile.mkdtemp(prefix="actinv-p21-g1-"))
    ) / "g1"
    work.mkdir(parents=True, exist_ok=True)
    library, decay = ensure_ci_library(work)
    shared = {
        "title": "P21 grouping case",
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
    canonical = work / "cells24.ndjson"
    cell_spectra = spectra(library)
    canonical_flux(canonical, library, cell_spectra)
    # The mesh signature is SHA-256 over the rebinned f64 little-endian
    # flux bytes; on the exact-grid CI library rebin is a copy, so the
    # signature of the source spectrum is the workload signature.
    import struct

    cell_signatures = [
        hashlib.sha256(
            b"".join(struct.pack("<d", value) for value in spectrum)
        ).hexdigest()
        for spectrum in cell_spectra
    ]

    runs: dict[str, Path] = {}
    for name, extra in (
        ("grouped", {}),
        ("ungrouped", {"group_workloads": False}),
        ("selected", {"cell_result_fields": SELECT_FIELDS}),
    ):
        spec_path, output_path = work / f"{name}.json", work / f"{name}.ndjson"
        write_json(spec_path, mesh_spec(shared, canonical, **extra))
        command([BIN, "mesh", spec_path, output_path])
        runs[name] = output_path

    raw = {name: path.read_bytes().splitlines() for name, path in runs.items()}
    parsed = {
        name: [json.loads(line) for line in lines] for name, lines in raw.items()
    }
    header_g, header_u = parsed["grouped"][0], parsed["ungrouped"][0]
    footer_g = dict(parsed["grouped"][-1])
    footer_u = dict(parsed["ungrouped"][-1])
    reuse_grouped = footer_g.pop("cells_served_from_reuse")
    reuse_ungrouped = footer_u.pop("cells_served_from_reuse")
    for key in ("wall_time_s", "cells_per_s"):
        footer_g.pop(key)
        footer_u.pop(key)

    header_diff = sorted(
        key for key in set(header_g) | set(header_u) if header_g.get(key) != header_u.get(key)
    )
    selected_cells = parsed["selected"][1:-1]
    full_cells = parsed["grouped"][1:-1]
    selected_key_sets = {tuple(sorted(cell["result"].keys())) for cell in selected_cells}
    shared_field_match = all(
        all(selected["result"][field] == full["result"][field] for field in SELECT_FIELDS)
        for selected, full in zip(selected_cells, full_cells)
    )
    full_key_sets = {tuple(sorted(cell["result"].keys())) for cell in full_cells}

    bogus_spec = mesh_spec(
        shared, canonical, cell_result_fields=["steps", "not_a_field"]
    )
    bogus_path = work / "bogus.json"
    write_json(bogus_path, bogus_spec)
    bogus = command([BIN, "mesh", bogus_path, work / "bogus.ndjson"], ok=False)
    bogus_message = (bogus.stdout + bogus.stderr).strip()

    memlim_spec = mesh_spec(shared, canonical, memory_limit_bytes=1)
    memlim_spec_path = work / "memlim.json"
    memlim_output = work / "memlim.ndjson"
    write_json(memlim_spec_path, memlim_spec)
    memlim = command([BIN, "mesh", memlim_spec_path, memlim_output], ok=False)
    memlim_message = (memlim.stdout + memlim.stderr).strip()

    output = {
        "cells": CELLS,
        "distinct_spectra": DISTINCT,
        "canonical_flux_sha256": sha256(canonical),
        "cell_flux_signature_sha256": cell_signatures,
        "grouping": {
            "cell_record_sha256_grouped": [
                hashlib.sha256(line).hexdigest() for line in raw["grouped"][1:-1]
            ],
            "cell_record_sha256_ungrouped": [
                hashlib.sha256(line).hexdigest() for line in raw["ungrouped"][1:-1]
            ],
            "cell_records_byte_identical": raw["grouped"][1:-1] == raw["ungrouped"][1:-1],
            "footer_identical_modulo_reuse_and_timing": footer_g == footer_u,
            "cells_served_from_reuse_grouped": reuse_grouped,
            "cells_served_from_reuse_ungrouped": reuse_ungrouped,
            "footer_grouped_normalized": footer_g,
            "footer_ungrouped_normalized": footer_u,
            "expected_reuse": CELLS - DISTINCT,
            "header_diff_fields": header_diff,
            "header_diff_only_fingerprint": header_diff == ["spec_fingerprint_sha256"],
        },
        "field_selection": {
            "requested": SELECT_FIELDS,
            "result_key_sets": sorted(map(list, selected_key_sets)),
            "only_named_fields": selected_key_sets == {tuple(sorted(SELECT_FIELDS))},
            "shared_fields_match_full_run": shared_field_match,
            "footer_closes": parsed["selected"][-1].get("record") == "footer"
            and parsed["selected"][-1].get("cell_count") == CELLS,
            "absent_option_full_record": full_key_sets == {tuple(sorted(FULL_RESULT_FIELDS))},
        },
        "validation": {
            "bogus_field_message": bogus_message,
            "bogus_field_rejected": "not_a_field" in bogus_message
            and bogus.returncode != 0,
        },
        "memory_guard": {
            "limit_bytes": 1,
            "message": memlim_message,
            "named_error": "memory_limit_bytes exceeded" in memlim_message,
            "carries_both_numbers": "peak RSS" in memlim_message
            and "limit" in memlim_message,
            "no_output_left": not memlim_output.exists(),
        },
    }
    output["pass"] = bool(
        output["grouping"]["cell_records_byte_identical"]
        and output["grouping"]["footer_identical_modulo_reuse_and_timing"]
        and reuse_grouped == CELLS - DISTINCT
        and reuse_ungrouped == 0
        and output["grouping"]["header_diff_only_fingerprint"]
        and all(
            value
            for key, value in output["field_selection"].items()
            if key in {"only_named_fields", "shared_fields_match_full_run", "footer_closes", "absent_option_full_record"}
        )
        and output["validation"]["bogus_field_rejected"]
        and all(output["memory_guard"][key] for key in ("named_error", "carries_both_numbers", "no_output_left"))
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g1_p21_grouping.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
