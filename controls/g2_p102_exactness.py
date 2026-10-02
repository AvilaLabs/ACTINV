#!/usr/bin/env python3
"""P102 G2 — exactness: independently re-derive every emitted token of the
ALARA export (time token, densities, union grid, file names, index
numbers) from the fixture bytes at machine precision.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p102_case as p102  # noqa: E402

RESULT = ROOT / "results/g2_p102_exactness.json"


def ordinal_width(n: int) -> int:
    return 1 if n <= 1 else len(str(n - 1))


def expected_grid(cells: list) -> list:
    seen = []
    for c in cells:
        for g in c.get("groups", []):
            e = g["centroid_eV"]
            if e > 0 and g["photons_s"] > 0 and e not in seen:
                seen.append(e)
    return sorted(seen)


def expected_densities(cell: dict, grid: list) -> list:
    nz = {g["centroid_eV"]: g["photons_s"] for g in cell.get("groups", [])
          if g["centroid_eV"] > 0 and g["photons_s"] > 0}
    return [nz.get(e, 0.0) / cell["volume_cm3"] for e in grid]


def main() -> int:
    checks = {}
    fixture = p102.fixture_r2s()
    sha = hashlib.sha256(fixture.encode()).hexdigest()
    cells = [json.loads(l) for l in fixture.splitlines()
             if json.loads(l).get("record") == "cell"]
    grid = expected_grid(cells)
    step_t_s = cells[0]["step_t_s"]
    cooling = step_t_s - p102.SHUTDOWN_T_S
    time_token = "shutdown" if cooling == 0.0 else f"{p102.rust_fmt(cooling)} s"
    width = ordinal_width(len(cells))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "out"
        r = p102.run_export_alara(fixture, out, td)
        assert r.returncode == 0, r.stderr
        index = json.loads((out / "actinv-alara-index.json").read_text())

        header = json.loads(fixture.splitlines()[0])
        checks["index_input_sha256"] = index["input_sha256"] == sha
        checks["index_step"] = index["step"] == header["step"]
        checks["index_step_t_s"] = index["step_t_s"] == step_t_s
        checks["index_shutdown_t_s"] = index["shutdown_t_s"] == p102.SHUTDOWN_T_S
        checks["index_cooling_s"] = index["cooling_s"] == cooling
        checks["index_time_token"] = index["time_token"] == time_token
        checks["index_group_centroids"] = index["group_centroids_eV"] == grid
        checks["index_units"] = index["units"] == "photons/s/cm3"
        checks["index_group_order"] = index["group_order"] == "ascending centroid_eV"

        for i, c in enumerate(cells):
            name = f"{i:0{width}d}_{p102.sanitize_alara_id(c['id'])}.photonSrc"
            entry = index["cells"][i]
            checks[f"file_name_{i}"] = entry["file"] == name
            path = out / name
            checks[f"file_exists_{i}"] = path.exists()
            text = path.read_text()
            densities = expected_densities(c, grid)
            exp_row = "TOTAL\t" + time_token + "\t" + "\t".join(
                p102.rust_fmt(d) for d in densities) + "\n"
            checks[f"row_exact_{i}"] = text == exp_row
            checks[f"index_bounds_{i}"] = entry["bounds_cm"] == c["bounds_cm"]
            checks[f"index_volume_{i}"] = entry["volume_cm3"] == c["volume_cm3"]
            checks[f"index_photons_s_{i}"] = entry["photons_s"] == c["photons_s"]
            checks[f"index_sigma_indep_{i}"] = entry["sigma_photons_s_independent"] == (
                c.get("sigma_photons_s_independent"))
            checks[f"index_sigma_consv_{i}"] = entry["sigma_photons_s_conservative"] == (
                c.get("sigma_photons_s_conservative"))
            nz = [g for g in c.get("groups", [])
                  if g["centroid_eV"] > 0 and g["photons_s"] > 0]
            checks[f"index_nonzero_groups_{i}"] = entry["nonzero_groups"] == len(nz)
            checks[f"index_sha256_{i}"] = entry["sha256"] == hashlib.sha256(
                text.encode()).hexdigest()
            checks[f"index_ordinal_{i}"] = entry["ordinal"] == i
            checks[f"index_id_{i}"] = entry["id"] == c["id"]

    evidence = {"schema": "actinv-p102-g2-exactness-1",
                "pass": all(checks.values()), "checks": checks}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    failed = [k for k, v in checks.items() if not v]
    print(json.dumps({"pass": evidence["pass"], "n": len(checks),
                      "failed": failed}))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
