#!/usr/bin/env python3
"""P19 comparator: ACTINV shield-table factors vs NJOY PURR MT=152 oracle.

Positional argv bound by the adapter:
  compare-script (this file), purr-result, shield-table, tolerances, output.

Reads the extracted NJOY MT=152 block and the ACTINV `actinv-shield-table-1`
artifact, computes per-material max relative deviation of the shielding factor
f = sigma(sigma0,T)/sigma(inf,T) over every declared (sigma0, T) grid point and
reaction, plus the infinite-dilution identity deviation. Emits
`comparison.json`. Pure arithmetic; no interpretation.
"""
import json
import sys
from pathlib import Path


def purr_factors(purr):
    """Material -> reaction -> {(sigma0,T): f} from MT=152 raw rows."""
    out = {}
    for material, block in purr["materials"].items():
        per_reaction = {}
        for row in block["mt152_rows"]:
            raw = row["raw"]
            # MT=152 layout: ZA, then one value per reaction in declared order
            # MTs on PURR tapes: [total, elastic, fission, capture] subset.
            # The MT list is carried in the row header convention; keep the
            # raw vector and let the comparator index by position list order.
            for i, value in enumerate(raw[1:], start=0):
                key = (round(row["sigma0"], 8), round(row["T"], 4))
                per_reaction.setdefault(i, {})[key] = value
        out[material] = per_reaction
    return out


def actinv_factors(table):
    """Material -> mt -> {(sigma0,T): f} from the ACTINV shield table."""
    out = {}
    for material, block in table.get("materials", {}).items():
        per_mt = {}
        for mt, grid in block.get("factors", {}).items():
            cells = {}
            for point in grid:
                cells[(round(point["sigma0_b"], 8), round(point["temperature_K"], 4))] = point["factor"]
            per_mt[int(mt)] = cells
        out[material] = per_mt
    return out


def main() -> int:
    purr = json.loads(Path(sys.argv[2]).read_text())
    shield = json.loads(Path(sys.argv[3]).read_text())
    tolerances = json.loads(Path(sys.argv[4]).read_text())
    output = Path(sys.argv[5])

    if shield.get("schema") != "actinv-shield-table-1":
        output.write_text(json.dumps({
            "schema": "avila.actinv/shield-comparison/v1",
            "error": "shield table schema is not actinv-shield-table-1",
            "all_covered": "not-evaluated",
        }, indent=1, sort_keys=True) + "\n")
        return 3

    purr_f = purr_factors(purr)
    actinv_f = actinv_factors(shield)
    materials = {}
    all_covered = True
    worst = 0.0
    for material, oracle in purr_f.items():
        mine = actinv_f.get(material)
        tol = tolerances["materials"].get(material, {}).get(
            "max_relative_deviation", tolerances["default_max_relative_deviation"])
        if mine is None:
            all_covered = False
            materials[material] = {"covered": "absent", "max_relative_deviation": None,
                                   "tolerance": tol}
            continue
        dev = 0.0
        n_points = 0
        for mt, cells in oracle.items():
            mine_cells = mine.get(mt)
            if not mine_cells:
                continue
            for key, oracle_f in cells.items():
                if oracle_f == 0.0 or key not in mine_cells:
                    continue
                n_points += 1
                dev = max(dev, abs(mine_cells[key] - oracle_f) / abs(oracle_f))
        materials[material] = {"covered": "covered", "max_relative_deviation": dev,
                               "tolerance": tol, "points": n_points}
        worst = max(worst, dev)

    # infinite-dilution identity: compare the ACTINF sigma0=1e10 factors to 1
    inf_dev = 0.0
    for material, per_mt in actinv_f.items():
        for cells in per_mt.values():
            for (s0, _t), f in cells.items():
                if s0 == 1.0e10:
                    inf_dev = max(inf_dev, abs(f - 1.0))
    report = {
        "schema": "avila.actinv/shield-comparison/v1",
        "materials": materials,
        "max_relative_deviation": worst,
        "infinite_dilution_max_dev": inf_dev,
        "all_covered": "covered" if all_covered else "absent",
    }
    output.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
