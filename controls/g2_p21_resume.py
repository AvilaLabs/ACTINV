#!/usr/bin/env python3
"""P21-G2: checkpoint/resume identity and rejection behavior.

Runs the mesh path on the CI library with a deterministic 12-cell flux.
Asserts the frozen P21 resume contract:

- ``resume: true`` on a missing output executes a fresh complete run;
- a prefix holding a header plus complete in-order cell records and a torn
  trailing line resumes after the last complete cell, producing output
  byte-identical to an uninterrupted resume-mode run everywhere except the
  footer's ``wall_time_s`` / ``cells_per_s`` (the established convention);
- resumed runs preserve ``cells_served_from_reuse`` accounting exactly
  (the prefix seeds the grouping memo);
- an output whose footer is already present returns its summary without
  re-solving and leaves the file untouched;
- a header mismatch (spec fingerprint) is refused by name;
- a corrupt mid-file record and an out-of-order cell ordinal are refused
  by name;
- a fresh (non-resume) run ignores and atomically replaces any existing
  output file.

Emits ``results/g2_p21_resume.json``.
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

CELLS = 12
PREFIX_CELLS = 5
TIMING_FIELDS = ("wall_time_s", "cells_per_s")


def canonical_flux(path: Path, library: Path, spectra: list[list[float]]) -> None:
    bounds = np.load(library)["bounds"].tolist()
    descriptor = path.with_suffix(".source.json")
    write_json(
        descriptor, {"fixture": "P21 resume case", "cells": len(spectra)}
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
    # Two distinct spectra alternating -> deterministic reuse accounting:
    # 10 of 12 cells served from reuse in every complete run.
    shapes = [
        [1e6 * math.exp(-index / 50.0) for index in range(groups)],
        [1e6 * math.exp(-index / 90.0) * 0.5 for index in range(groups)],
    ]
    return [shapes[ordinal % 2] for ordinal in range(CELLS)]


def mesh_spec(shared: dict, canonical: Path, **extra) -> dict:
    spec = {
        "spec": "actinv-mesh-spec-1",
        **shared,
        "flux": {"path": str(canonical), "sha256": sha256(canonical)},
        "chunk_cells": 2,
        "threads": 2,
    }
    spec.update(extra)
    return spec


def normalized_lines(path: Path) -> tuple[list[bytes], dict]:
    lines = path.read_bytes().splitlines()
    footer = json.loads(lines[-1])
    for key in TIMING_FIELDS:
        footer.pop(key, None)
    return lines, footer


def main() -> None:
    work = Path(
        os.environ.get("ACTINV_P21_WORK", tempfile.mkdtemp(prefix="actinv-p21-g2-"))
    ) / "g2"
    work.mkdir(parents=True, exist_ok=True)
    library, decay = ensure_ci_library(work)
    shared = {
        "title": "P21 resume case",
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
    canonical = work / "cells12.ndjson"
    canonical_flux(canonical, library, spectra(library))

    resume_spec = mesh_spec(shared, canonical, resume=True)
    resume_spec_path = work / "resume.json"
    write_json(resume_spec_path, resume_spec)

    # Uninterrupted reference run (resume mode, absent output -> fresh).
    reference = work / "reference.ndjson"
    command([BIN, "mesh", resume_spec_path, reference])
    ref_lines, ref_footer = normalized_lines(reference)

    # Interrupted run: header + PREFIX_CELLS complete cells + torn tail.
    interrupted = work / "interrupted.ndjson"
    ref_bytes = reference.read_bytes().split(b"\n")
    prefix = b"\n".join(ref_bytes[: 1 + PREFIX_CELLS]) + b"\n"
    torn = b'{"record":"cell","ordinal":%d,"id":"cell-%d",' % (
        PREFIX_CELLS,
        PREFIX_CELLS,
    )
    interrupted.write_bytes(prefix + torn)
    command([BIN, "mesh", resume_spec_path, interrupted])
    res_lines, res_footer = normalized_lines(interrupted)
    resumed_bytes = interrupted.read_bytes()

    # Already-complete output -> summary no-op, bytes untouched.
    before = interrupted.read_bytes()
    summary = command([BIN, "mesh", resume_spec_path, interrupted])
    summary_payload = json.loads(summary.stdout)
    noop_bytes_unchanged = interrupted.read_bytes() == before
    noop_summary = (
        summary_payload.get("cells") == CELLS and summary_payload.get("cells_per_s") == 0.0
    )

    # Fingerprint mismatch: same flux, mutated title -> different header.
    mut_spec = mesh_spec(shared, canonical, resume=True, title="mutated")
    mut_path = work / "mutated.json"
    write_json(mut_path, mut_spec)
    mismatch = work / "mismatch.ndjson"
    mismatch.write_bytes(reference.read_bytes())
    mut = command([BIN, "mesh", mut_path, mismatch], ok=False)
    mut_message = (mut.stdout + mut.stderr).strip()

    # Corrupt mid-file record.
    corrupt = work / "corrupt.ndjson"
    lines = reference.read_bytes().splitlines()
    corrupt_lines = lines[:4] + [b'{"record":"cell","ordinal":4 BROKEN'] + lines[5:]
    corrupt.write_bytes(b"\n".join(corrupt_lines) + b"\n")
    cor = command([BIN, "mesh", resume_spec_path, corrupt], ok=False)
    cor_message = (cor.stdout + cor.stderr).strip()

    # Out-of-order ordinal.
    ooo = work / "ooo.ndjson"
    record = json.loads(lines[3])
    record["ordinal"] = 99
    ooo_lines = lines[:3] + [json.dumps(record).encode()] + lines[4:]
    ooo.write_bytes(b"\n".join(ooo_lines[:8]) + b"\n")
    ooo_run = command([BIN, "mesh", resume_spec_path, ooo], ok=False)
    ooo_message = (ooo_run.stdout + ooo_run.stderr).strip()

    # Fresh (non-resume) run atomically replaces an existing partial file.
    fresh_spec = mesh_spec(shared, canonical)
    fresh_path = work / "fresh.json"
    write_json(fresh_path, fresh_spec)
    fresh_out = work / "fresh.ndjson"
    fresh_out.write_bytes(prefix + torn)
    command([BIN, "mesh", fresh_path, fresh_out])
    fresh_lines, _ = normalized_lines(fresh_out)
    fresh_complete = (
        len(fresh_lines) == CELLS + 2
        and json.loads(fresh_lines[-1]).get("record") == "footer"
    )

    identity = {
        "reference_line_sha256": [
            hashlib.sha256(line).hexdigest() for line in ref_lines
        ],
        "resumed_line_sha256": [
            hashlib.sha256(line).hexdigest() for line in res_lines
        ],
        "line_count_equal": len(res_lines) == len(ref_lines),
        "header_byte_identical": res_lines[0] == ref_lines[0],
        "cell_records_byte_identical": res_lines[1:-1] == ref_lines[1:-1],
        "footer_identical_modulo_timing": res_footer == ref_footer,
        "reuse_count_preserved": res_footer.get("cells_served_from_reuse")
        == CELLS - 2,
        "footer_reference_normalized": ref_footer,
        "footer_resumed_normalized": res_footer,
    }

    output = {
        "cells": CELLS,
        "prefix_cells": PREFIX_CELLS,
        "canonical_flux_sha256": sha256(canonical),
        "resume_identity": identity,
        "complete_run_noop": {
            "summary": summary_payload,
            "summary_reports_done": noop_summary,
            "bytes_unchanged": noop_bytes_unchanged,
        },
        "rejections": {
            "fingerprint_mismatch": {
                "message": mut_message,
                "named": "does not match this spec's fingerprint" in mut_message,
            },
            "corrupt_mid_record": {
                "message": cor_message,
                "named": "corrupt record" in cor_message,
            },
            "out_of_order_ordinal": {
                "message": ooo_message,
                "named": "out of order" in ooo_message,
            },
        },
        "fresh_run_atomic_replace": fresh_complete,
        "resumed_output_bytes": len(resumed_bytes),
    }
    output["pass"] = bool(
        all(identity.values())
        and noop_summary
        and noop_bytes_unchanged
        and all(entry["named"] for entry in output["rejections"].values())
        and fresh_complete
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g2_p21_resume.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
