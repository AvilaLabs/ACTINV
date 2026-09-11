#!/usr/bin/env python3
"""P19 comparator: ACTINV shield-table factors vs NJOY PURR MT=152 oracle and
NJOY GROUPR GENDF group constants.

Positional argv bound by the adapter:
  compare-script (this file), purr-result, shield-table, tolerances,
  groupr-result, output.

Node leg: Bondarenko factors f = sigma(sigma0,T)/sigma(inf,T) at matching
node energies — the oracle's `materials[m].mt152_sections[*].temperatures[*]
.energies[*]` rows against the artifact's `nuclides[m].nodes[*]` entries.
Both processors take node energies from the evaluation's unresolved-range
grid, so node sets coincide; a 1e-3 eV relative tolerance resolves ordering
floats. NJOY's five MT=152 channels map to the table's four via
total->total, elastic->elastic, fission->fission, capture->capture (heating
is ignored: it is not a reaction rate the library fold consumes).

Group leg: the artifact's `group_shielded_b` (full-group Bondarenko cross
sections) and `group_factors` against GROUPR's GENDF MF=3 records
(`groupr-result.materials[m].mf3_sections[mt][T][group]`, 1-based ascending
groups; the artifact's `group` is 0-based ascending, so gendf group g equals
artifact group g-1). GROUPR applies the pointwise Bondarenko weight over the
whole group, including resolved-resonance structure in uncovered parts;
the declared unresolved-only scope is therefore not expected to close on
edge groups whose uncovered sliver sits inside a resolved range.

Metrics per material: max and rms relative factor deviation over all
(node, sigma0, T, channel) cells below the infinite-dilution column, the
infinite-dilution column deviation, and the sigma0=0.1 (deep-tail) maximum —
reported separately because that corner carries the documented residual.
Group metrics per material: max/rms over (group, sigma0, T, channel) for the
fully-covered groups, and the same restricted to partially-covered groups.
Emits `comparison.json`. Pure arithmetic; no interpretation.
"""
import json
import sys
from pathlib import Path

CHANNELS = ("total", "elastic", "fission", "capture")
CHANNEL_MT = {"total": 1, "elastic": 2, "fission": 18, "capture": 102}
DEEP_SIGMA0 = 0.1


def actinv_node_factors(table):
    """material -> {node E -> {channel -> [[sigma0 x T factor]]}}."""
    out = {}
    nuclides = table.get("nuclides", {})
    for name, block in nuclides.items():
        per_node = {}
        for node in block.get("nodes", []):
            inf = node["infinite_dilution_b"]
            per_channel = {}
            for ci, channel in enumerate(CHANNELS):
                if inf[ci] == 0.0:
                    per_channel[channel] = None
                    continue
                grid = node["bondarenko_b"][channel]
                per_channel[channel] = [
                    [row[t] / inf[ci] for t in range(len(row))] for row in grid
                ]
            per_node[round(node["energy_ev"], 4)] = per_channel
        out[name] = per_node
    return out


def actinv_groups(table):
    """material -> {0-based group -> row}."""
    return {
        name: {int(g["group"]): g for g in block.get("groups", [])}
        for name, block in table.get("nuclides", {}).items()
    }


def interp_ln(grid_vals, sig0_grid, s0_eff):
    """ln-sigma0 interpolation matching the runtime's factor_at."""
    import math
    lx = math.log(s0_eff)
    lgs = [math.log(v) for v in sig0_grid]
    if lx >= lgs[0]:
        return grid_vals[0]
    if lx <= lgs[-1]:
        return grid_vals[-1]
    for i in range(1, len(lgs)):
        if lgs[i] <= lx:
            w = (lx - lgs[i]) / (lgs[i - 1] - lgs[i])
            return grid_vals[i] + w * (grid_vals[i - 1] - grid_vals[i])
    return grid_vals[-1]


def compare_groups(material, my_groups, gendf, sig0_grid, temps, deep_idx):
    """Compare full-group shielded xs and factors vs the GROUPR GENDF.

    GROUPR's Bondarenko weight is (sigma_pot + sigma0)/(sigma_t + sigma0),
    evaluated against the sigma0-resolved unresolved table; its sigma0 column
    therefore lines up with the artifact's raw sigma0 (the sigma_pot numerator
    largely normalizes out of the xs ratio). Verified empirically: the
    fully-covered group factor column matches to ~6% at sigma0=0.1 under the
    raw mapping and degrades to ~3x under a sigma0+sigma_p remap."""
    out = {"cells": 0, "full_cells": 0, "partial_cells": 0}
    worst = 0.0
    deep = 0.0
    sq = 0.0
    full_worst = 0.0
    partial_worst = 0.0
    missing = 0
    inf_xs = {"cells": 0, "max": 0.0}
    # Rate channels carry the production physics; total and elastic are
    # reported per channel but not gated, since the GROUPR weight convention
    # (sigma_pot+sigma0)/(sigma_t+sigma0) amplifies pointwise dips and the
    # total factor is dominated by the extreme ptable bins.
    per_channel = {}
    for channel, mt in CHANNEL_MT.items():
        section = gendf.get(str(mt))
        if section is None:
            continue
        ch = {"cells": 0, "full_cells": 0, "partial_cells": 0,
              "worst": 0.0, "full_worst": 0.0, "partial_worst": 0.0,
              "deep": 0.0, "sq": 0.0}
        for t_key, by_group in section.items():
            try:
                t_i = temps.index(float(t_key))
            except ValueError:
                continue
            for g_key, rec in by_group.items():
                row = my_groups.get(int(g_key) - 1)
                if row is None or "group_shielded_b" not in row:
                    missing += 1
                    continue
                c = row["overlap_fraction"]
                oracle_xs = [float(v) for v in rec["xs_b"]]
                if len(oracle_xs) != len(sig0_grid) or oracle_xs[0] == 0.0:
                    continue
                my_xs = row["group_shielded_b"][channel]
                my_f = row["group_factors"][channel]
                # Absolute sigma0=inf comparison: the artifact's unshielded
                # column collapses ladder-statistics averages while GROUPR's
                # collapses the pointwise file — a real methodological
                # difference reported separately, never a factor-gate cell.
                xs0_dev = abs(my_xs[0][t_i] - oracle_xs[0]) / oracle_xs[0]
                inf_xs["cells"] += 1
                inf_xs["max"] = max(inf_xs["max"], xs0_dev)
                for si in range(1, len(sig0_grid)):
                    f_oracle = oracle_xs[si] / oracle_xs[0]
                    if f_oracle == 0.0:
                        continue
                    f_mine = my_f[si][t_i]
                    dev = abs(f_mine - f_oracle) / f_oracle
                    out["cells"] += 1
                    sq += dev * dev
                    worst = max(worst, dev)
                    ch["cells"] += 1
                    ch["sq"] += dev * dev
                    ch["worst"] = max(ch["worst"], dev)
                    if c >= 0.999999:
                        out["full_cells"] += 1
                        full_worst = max(full_worst, dev)
                        ch["full_cells"] += 1
                        ch["full_worst"] = max(ch["full_worst"], dev)
                    else:
                        out["partial_cells"] += 1
                        partial_worst = max(partial_worst, dev)
                        ch["partial_cells"] += 1
                        ch["partial_worst"] = max(ch["partial_worst"], dev)
                    if si == deep_idx:
                        deep = max(deep, dev)
                        ch["deep"] = max(ch["deep"], dev)
        per_channel[channel] = {
            "cells": ch["cells"],
            "full_cells": ch["full_cells"],
            "max_relative_deviation": ch["worst"],
            "full_coverage_max": ch["full_worst"] if ch["full_cells"] else None,
            "partial_coverage_max": ch["partial_worst"]
            if ch["partial_cells"] else None,
            "deep_sigma0_max": ch["deep"],
            "rms_relative_deviation": (ch["sq"] / ch["cells"]) ** 0.5
            if ch["cells"] else None,
        }
    # The qualification metric: fully-covered groups on the rate channels.
    rate_full = max(
        (
            per_channel[ch]["full_coverage_max"] or 0.0
            for ch in ("capture", "fission")
            if ch in per_channel
        ),
        default=None,
    )
    out.update(
        max_relative_deviation=worst if out["cells"] else None,
        rms_relative_deviation=(sq / out["cells"]) ** 0.5 if out["cells"] else None,
        full_coverage_max=full_worst if out["full_cells"] else None,
        rate_channel_full_coverage_max=rate_full,
        partial_coverage_max=partial_worst if out["partial_cells"] else None,
        deep_sigma0_max=deep if out["cells"] else None,
        missing_groups=missing,
        per_channel=per_channel,
        inf_xs_max=inf_xs["max"] if inf_xs["cells"] else None,
    )
    return out


def main() -> int:
    purr = json.loads(Path(sys.argv[1]).read_text())
    shield = json.loads(Path(sys.argv[2]).read_text())
    tolerances = json.loads(Path(sys.argv[3]).read_text())
    groupr = json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) > 5 else None
    output = Path(sys.argv[5] if len(sys.argv) > 5 else sys.argv[4])

    if shield.get("format") != "actinv-shield-table-1":
        output.write_text(json.dumps({
            "schema": "avila.actinv/shield-comparison/v1",
            "error": "shield table schema is not actinv-shield-table-1",
            "all_covered": "not-evaluated",
        }, indent=1, sort_keys=True) + "\n")
        return 3

    sig0_grid = [float(v) for v in purr["sigma0_b"]]
    temps = [float(v) for v in purr["temperatures_K"]]
    deep_idx = min(range(len(sig0_grid)), key=lambda i: abs(sig0_grid[i] - DEEP_SIGMA0))
    mine_nodes = actinv_node_factors(shield)
    mine_groups = actinv_groups(shield)

    materials = {}
    all_covered = True
    inf_worst = 0.0
    for material, block in purr["materials"].items():
        key = material.replace("-", "")
        my_nodes = mine_nodes.get(key)
        tol_block = tolerances["materials"].get(material, {})
        tol = tol_block.get(
            "max_relative_deviation",
            tolerances["default_max_relative_deviation"],
        )
        if my_nodes is None:
            all_covered = False
            materials[material] = {
                "covered": "absent",
                "max_relative_deviation": None,
            }
            continue
        cells = 0
        worst = 0.0
        deep_worst = 0.0
        sq_sum = 0.0
        missing_nodes = 0
        for section in block["mt152_sections"]:
            for t_i, temp_block in enumerate(section["temperatures"]):
                for entry in temp_block["energies"]:
                    node = my_nodes.get(round(float(entry["E"]), 4))
                    if node is None:
                        missing_nodes += 1
                        continue
                    for channel in CHANNELS:
                        oracle_col = entry[channel]
                        oracle_inf = float(oracle_col[0])
                        my_grid = node[channel]
                        if my_grid is None or oracle_inf == 0.0:
                            continue
                        for si, s0 in enumerate(sig0_grid):
                            f_oracle = float(oracle_col[si]) / oracle_inf
                            f_mine = my_grid[si][t_i]
                            dev = abs(f_mine - f_oracle) / f_oracle if f_oracle else 0.0
                            if si == 0:
                                inf_worst = max(inf_worst, dev)
                                continue
                            cells += 1
                            sq_sum += dev * dev
                            worst = max(worst, dev)
                            if si == deep_idx:
                                deep_worst = max(deep_worst, dev)
        rms = (sq_sum / cells) ** 0.5 if cells else None
        rms_tol = tol_block.get(
            "node_rms_relative_deviation",
            tolerances.get("default_node_rms_relative_deviation"),
        )
        entry = {
            "covered": "covered",
            "cells": cells,
            "missing_nodes": missing_nodes,
            "max_relative_deviation": worst,
            "rms_relative_deviation": rms,
            "deep_sigma0_max_relative_deviation": deep_worst,
            "tolerance": tol,
            "rms_tolerance": rms_tol,
            "within_tolerance": worst <= tol
            and (rms_tol is None or (rms is not None and rms <= rms_tol)),
        }
        if groupr is not None:
            gendf = groupr.get("materials", {}).get(material, {}).get(
                "mf3_sections", {}
            )
            g_tol = tol_block.get(
                "group_max_relative_deviation",
                tolerances.get(
                    "group_default_max_relative_deviation",
                    tolerances["default_max_relative_deviation"],
                ),
            )
            group_cmp = compare_groups(
                material, mine_groups.get(key, {}), gendf,
                sig0_grid, temps, deep_idx,
            )
            group_cmp["tolerance"] = g_tol
            group_cmp["within_tolerance"] = (
                group_cmp["rate_channel_full_coverage_max"] is not None
                and group_cmp["rate_channel_full_coverage_max"] <= g_tol
            )
            entry["group_comparison"] = group_cmp
        materials[material] = entry

    result = {
        "schema": "avila.actinv/shield-comparison/v1",
        "materials": materials,
        "infinite_dilution_max_dev": inf_worst,
        "all_covered": "covered" if all_covered else "absent",
    }
    output.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
