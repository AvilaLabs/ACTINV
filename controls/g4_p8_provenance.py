#!/usr/bin/env python3
"""P8-G4: deterministic interchange, hashes, provenance propagation and fail-closed inputs."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from p8_fixtures import (
    BIN,
    PHYSICAL,
    PROBE,
    command,
    ensure_ci_library,
    import_arguments,
    make_all,
    make_openmc,
    read_ndjson,
    sha256,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def main() -> None:
    work = Path(os.environ.get("ACTINV_P8_WORK", tempfile.mkdtemp(prefix="actinv-p8-g4-"))) / "g4"
    work.mkdir(parents=True, exist_ok=True)
    fixtures = make_all(work / "fixtures")
    imports = [
        ("openmc", fixtures["openmc_mesh"]),
        ("meshtal", fixtures["meshtal"]),
        ("mctal", fixtures["mctal"]),
        ("fispact", fixtures["fispact"]),
    ]
    repeat_identity = {}
    hash_checks = {}
    first_cell_spectra = {}
    canonical_paths = {}
    for kind, source in imports:
        first, second = work / f"{kind}-a.ndjson", work / f"{kind}-b.ndjson"
        first_summary = json.loads(
            command(import_arguments(kind, source, first, fixtures["groups"])).stdout
        )
        command(import_arguments(kind, source, second, fixtures["groups"]))
        header, cells, _ = read_ndjson(first)
        repeat_identity[kind] = first.read_bytes() == second.read_bytes()
        source_matches = header["source"]["sha256"] == sha256(source)
        auxiliary_matches = all(
            item["sha256"] == sha256(Path(item["path"]))
            for item in header["source"].get("auxiliary_inputs", [])
        )
        canonical_matches = first_summary["sha256"] == sha256(first)
        hash_checks[kind] = {
            "source": source_matches,
            "auxiliary": auxiliary_matches,
            "canonical": canonical_matches,
        }
        first_cell_spectra[kind] = {
            "flux": cells[0]["flux_per_group"],
            "total": cells[0]["flux_total"],
        }
        canonical_paths[kind] = first

    expected_identity = {"flux": PHYSICAL[0].tolist(), "total": 10.0}
    cross_format_exact = all(value == expected_identity for value in first_cell_spectra.values())

    library, decay = ensure_ci_library(work)
    canonical = canonical_paths["fispact"]
    mesh_spec = {
        "spec": "actinv-mesh-spec-1",
        "title": "P8 provenance propagation",
        "library": {"path": str(library), "sha256": sha256(library)},
        "decay": {"primary": str(decay)},
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"FE": 100.0}},
        "flux": {"path": str(canonical), "sha256": sha256(canonical)},
        "schedule": [{"dt": "1 s", "flux": 1.0}],
        "options": {
            "mode": "trace",
            "prune": "rate",
            "bmin_atoms_per_g": 1e-8,
            "temperature_K": 293.6,
            "outputs": ["inventory", "heat", "ledger", "certificate"],
        },
        "chunk_cells": 1,
        "threads": 1,
    }
    mesh_spec_path, mesh_output = work / "mesh.json", work / "mesh.ndjson"
    write_json(mesh_spec_path, mesh_spec)
    command([BIN, "mesh", mesh_spec_path, mesh_output])
    mesh_header = json.loads(mesh_output.read_text().splitlines()[0])
    canonical_header = json.loads(canonical.read_text().splitlines()[0])
    certificate = mesh_header["certificate"]
    propagation = {
        "canonical_declared": certificate["canonical_flux"]["sha256_declared"] == sha256(canonical),
        "canonical_computed": certificate["canonical_flux"]["sha256_computed"] == sha256(canonical),
        "upstream_exact": certificate["upstream_source"] == canonical_header["source"],
        "upstream_source_hash": certificate["upstream_source"]["sha256"] == sha256(fixtures["fispact"]),
        "auxiliary_hash": certificate["upstream_source"]["auxiliary_inputs"][0]["sha256"]
        == sha256(fixtures["groups"]),
    }

    wrong_spec = dict(mesh_spec)
    wrong_spec["flux"] = {"path": str(canonical), "sha256": "0" * 64}
    wrong_spec_path, wrong_output = work / "wrong_hash.json", work / "wrong_hash.ndjson"
    write_json(wrong_spec_path, wrong_spec)
    wrong = command([BIN, "mesh", wrong_spec_path, wrong_output], ok=False)
    wrong_message = (wrong.stdout + wrong.stderr).strip()

    records = [json.loads(line) for line in canonical.read_text().splitlines()]
    nonfinite = work / "nonfinite.ndjson"
    records[1]["flux_per_group"][0] = float("nan")
    nonfinite.write_text("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records))
    nonfinite_result = command([PROBE, "validate", nonfinite], ok=False)
    nonfinite_message = (nonfinite_result.stdout + nonfinite_result.stderr).strip()
    records = [json.loads(line) for line in canonical.read_text().splitlines()]
    records[-1]["cell_count"] = 2
    bad_footer = work / "bad_footer.ndjson"
    bad_footer.write_text("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records))
    footer_result = command([PROBE, "validate", bad_footer], ok=False)
    footer_message = (footer_result.stdout + footer_result.stderr).strip()

    changing_source = work / "changing.h5"
    changing_output = work / "changing.ndjson"
    make_openmc(changing_source, padding_mb=96)
    process = subprocess.Popen(
        [str(item) for item in import_arguments("openmc", changing_source, changing_output)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for _ in range(30):
        if process.poll() is not None:
            break
        time.sleep(0.005)
        with changing_source.open("ab") as stream:
            stream.write(b"x")
            stream.flush()
            os.fsync(stream.fileno())
    mutation_stdout, mutation_stderr = process.communicate()
    mutation_message = (mutation_stdout + mutation_stderr).strip()
    mutation_failed = process.returncode != 0 and not changing_output.exists()

    output = {
        "repeat_bytes_identical": repeat_identity,
        "hash_recomputation": hash_checks,
        "cross_format_first_cell": first_cell_spectra,
        "cross_format_spectrum_and_total_exact": cross_format_exact,
        "mesh_certificate_propagation": propagation,
        "wrong_declared_hash": {
            "message": wrong_message,
            "hard_error": wrong.returncode != 0 and not wrong_output.exists() and "SHA-256 mismatch" in wrong_message,
        },
        "nonfinite": {
            "message": nonfinite_message,
            "hard_error": nonfinite_result.returncode != 0 and "canonical line" in nonfinite_message,
        },
        "changed_footer": {"message": footer_message, "hard_error": "footer cell count" in footer_message},
        "mutated_during_import": {"message": mutation_message, "hard_error": mutation_failed},
    }
    output["pass"] = bool(
        all(repeat_identity.values())
        and all(all(value.values()) for value in hash_checks.values())
        and cross_format_exact
        and all(propagation.values())
        and output["wrong_declared_hash"]["hard_error"]
        and output["nonfinite"]["hard_error"]
        and output["changed_footer"]["hard_error"]
        and output["mutated_during_import"]["hard_error"]
        and "changed while importing" in mutation_message
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g4_p8_provenance.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
