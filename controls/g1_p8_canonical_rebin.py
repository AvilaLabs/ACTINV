#!/usr/bin/env python3
"""P8-G1: canonical/FISPACT identity, conservative rebinning and fail-closed streams."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from p8_fixtures import (
    BIN,
    BOUNDARIES_EV,
    PHYSICAL,
    PROBE,
    command,
    import_arguments,
    make_fispact,
    make_openmc,
    read_ndjson,
    sha256,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def independent_rebin(source_bounds, source_flux, destination_bounds):
    destination = [0.0] * (len(destination_bounds) - 1)
    underflow = 0.0
    overflow = 0.0
    for low, high, value in zip(source_bounds, source_bounds[1:], source_flux):
        width = math.log(high / low)
        if low < destination_bounds[0]:
            overlap_high = min(high, destination_bounds[0])
            if overlap_high > low:
                underflow += value * math.log(overlap_high / low) / width
        if high > destination_bounds[-1]:
            overlap_low = max(low, destination_bounds[-1])
            if high > overlap_low:
                overflow += value * math.log(high / overlap_low) / width
        for group, (dest_low, dest_high) in enumerate(zip(destination_bounds, destination_bounds[1:])):
            overlap_low = max(low, dest_low)
            overlap_high = min(high, dest_high)
            if overlap_high > overlap_low:
                destination[group] += value * math.log(overlap_high / overlap_low) / width
    return destination, underflow, overflow


def main() -> None:
    work = Path(os.environ.get("ACTINV_P8_WORK", tempfile.mkdtemp(prefix="actinv-p8-g1-"))) / "g1"
    work.mkdir(parents=True, exist_ok=True)
    fluxes, groups = work / "fluxes", work / "groups.json"
    make_fispact(fluxes, groups)
    canonical_a, canonical_b = work / "fispact-a.ndjson", work / "fispact-b.ndjson"
    command(import_arguments("fispact", fluxes, canonical_a, groups))
    command(import_arguments("fispact", fluxes, canonical_b, groups))
    header, cells, footer = read_ndjson(canonical_a)
    independent = {
        "schema": header["schema"],
        "boundaries": header["energy_boundaries_eV"],
        "flux": cells[0]["flux_per_group"],
        "total": cells[0]["flux_total"],
        "footer_total": footer["flux_sum_over_cells"],
        "source_sha256": header["source"]["sha256"],
        "groups_sha256": header["source"]["auxiliary_inputs"][0]["sha256"],
        "title": header["source"]["metadata"]["title"],
        "wall": header["source"]["metadata"]["first_wall_loading_MW_m2"],
    }
    expected = {
        "schema": "actinv-flux-1",
        "boundaries": BOUNDARIES_EV,
        "flux": PHYSICAL[0].tolist(),
        "total": 10.0,
        "footer_total": 10.0,
        "source_sha256": sha256(fluxes),
        "groups_sha256": sha256(groups),
        "title": "ACTINV P8 deterministic identity spectrum",
        "wall": 0.0,
    }

    exact_input = work / "exact.json"
    write_json(
        exact_input,
        {
            "source_boundaries_eV": [1.0, 2.0, 4.0, 8.0],
            "source_flux": [1.0, 2.0, 3.0],
            "destination_boundaries_eV": [1.0, 2.0, 4.0, 8.0],
        },
    )
    exact = json.loads(command([PROBE, "rebin", exact_input]).stdout)
    split_input = work / "split.json"
    source_bounds, source_flux, destination_bounds = [1.0, 4.0, 16.0], [2.0, 6.0], [2.0, 8.0]
    write_json(
        split_input,
        {
            "source_boundaries_eV": source_bounds,
            "source_flux": source_flux,
            "destination_boundaries_eV": destination_bounds,
        },
    )
    split = json.loads(command([PROBE, "rebin", split_input]).stdout)
    independent_split = independent_rebin(source_bounds, source_flux, destination_bounds)
    split_differences = [
        abs(split["flux_per_group"][0] - independent_split[0][0]),
        abs(split["underflow"] - independent_split[1]),
        abs(split["overflow"] - independent_split[2]),
    ]

    records = canonical_a.read_text().splitlines()
    truncated = work / "truncated.ndjson"
    truncated.write_text("\n".join(records[:-1]) + "\n")
    duplicate = work / "duplicate.ndjson"
    duplicate_records = [json.loads(line) for line in records]
    duplicate_records[0]["cell_count"] = 2
    second = dict(duplicate_records[1])
    second["ordinal"] = 1
    duplicate_records.insert(2, second)
    duplicate_records[-1]["cell_count"] = 2
    duplicate_records[-1]["flux_sum_over_cells"] = 20.0
    duplicate.write_text("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in duplicate_records))
    negative = work / "negative.ndjson"
    negative_records = [json.loads(line) for line in records]
    negative_records[1]["flux_per_group"][0] = -1.0
    negative_records[1]["flux_total"] = 8.0
    negative_records[-1]["flux_sum_over_cells"] = 8.0
    negative.write_text("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in negative_records))
    failures = {}
    for name, path in [("truncated", truncated), ("duplicate", duplicate), ("negative", negative)]:
        failed = command([PROBE, "validate", path], ok=False)
        failures[name] = (failed.stdout + failed.stderr).strip()

    zero_source = work / "zero.h5"
    make_openmc(zero_source)
    zero_failure = command(
        [BIN, "import-flux", "openmc", zero_source, work / "zero.ndjson", "--tally", "7", "--source-rate", "1024"],
        ok=False,
    )
    failures["zero_without_floor"] = (zero_failure.stdout + zero_failure.stderr).strip()

    output = {
        "canonical_fispact_exact": independent == expected,
        "repeat_bytes_identical": canonical_a.read_bytes() == canonical_b.read_bytes(),
        "canonical_sha256": sha256(canonical_a),
        "exact_rebin_bit_identical": exact["exact_grid"] and exact["flux_per_group"] == [1.0, 2.0, 3.0],
        "split_rebin_max_abs_difference": max(split_differences),
        "split_rebin_relative_closure": split["relative_closure"],
        "failures": failures,
    }
    output["pass"] = bool(
        output["canonical_fispact_exact"]
        and output["repeat_bytes_identical"]
        and output["exact_rebin_bit_identical"]
        and output["split_rebin_max_abs_difference"] <= 1e-12
        and output["split_rebin_relative_closure"] <= 1e-12
        and "footer" in failures["truncated"]
        and "duplicate canonical cell ID" in failures["duplicate"]
        and "negative flux" in failures["negative"]
        and "explicit positive --energy-floor-eV" in failures["zero_without_floor"]
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g1_p8_canonical_rebin.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
