#!/usr/bin/env python3
"""P100 G5: candidate gamma residual production vs FISPACT-II TENDL-2017 tal2017-g/gxs-162.

The P98 control's reconstruction rules carry over unchanged and are imported from it
(controls/p98_g5_fispact.py, 2c490ed6...): the P10 reader split per MT, the gamma single-neutron
channel, free-neutron exclusion, MT5 raw-LFS recovery by rank, and the candidate index mapping.

G5a: one-group values per (nuclide, residual, spectrum) from every contribution except MT5 on both
sides; residuals carrying >= 1e-3 of the nuclide's summed non-MT5 production on either side; every
nuclide contributes under gdr_flat_8_30_MeV and brems_20_MeV; >= 95 % within 2e-3, all within 2e-2.

G5b: every processed MF=10 MT5 section (ZAP > 1) equals rule R built from the raw evaluation:
points sigma(E)*y(E) on the union of the raw MF=3 MT5 and yield grids, right-continuous except at
the lowest grid energy (first-listed values), interpolated lin-lin and lethargy-averaged per group;
groups below 200 MeV where either value >= 1e-12 b, within 1e-5 relative.

Reported: P98's all-MT comparison, group rows vs 2.5e-3, extension and state_sum_normalized lines.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from g5_p10_charged import collapse_product, evaluate, parse_independent, relative  # noqa: E402
import p98_g5_fispact as p98  # noqa: E402

WORK = ROOT / "target" / "p100"
NPZ = WORK / "g5_gamma_2017.npz"
INDEX = WORK / "g5_gamma_2017_index.json"
P98_CONTROL_SHA256 = "2c490ed617e83dca8e6d0611cc826d34b5245745bfef7e3189fc6439c8f460c2"
RULE_R_TOLERANCE = 1.0e-5
RULE_R_FLOOR_B = 1.0e-12
GATED_SPECTRA = ("gdr_flat_8_30_MeV", "brems_20_MeV")


def lin_lin(points: list[tuple[float, float]]) -> dict:
    return {"nbt": ((len(points), 2),), "x": tuple(p[0] for p in points), "y": tuple(p[1] for p in points)}


def first_listed(tab: dict, energy: float) -> float:
    index = tab["x"].index(energy) if energy in tab["x"] else None
    return tab["y"][index] if index is not None else evaluate(tab, energy)


def rule_r_rows(sigma: dict, yield_tab: dict, bounds: np.ndarray) -> tuple[np.ndarray, list[float]]:
    grid = sorted(set(sigma["x"]) | set(yield_tab["x"]))
    lowest = grid[0]
    points = []
    for energy in grid:
        if energy == lowest:
            value = first_listed(sigma, energy) * first_listed(yield_tab, energy)
        else:
            value = evaluate(sigma, energy) * evaluate(yield_tab, energy)
        points.append((energy, value))
    doubled = sorted(
        {x for tab in (sigma, yield_tab) for x in tab["x"] if tab["x"].count(x) > 1 and x != lowest}
    )
    table = lin_lin(points)
    row = np.array(
        [collapse_product((table,), float(low), float(high)) for low, high in zip(bounds[:-1], bounds[1:])],
        dtype=float,
    )
    return row, doubled


def raw_mt5_yields(raw: dict) -> dict[tuple[int, int], dict]:
    """(ZAP, raw LFS) -> MF=6 yield table, paired exactly as production_terms pairs them."""
    yields = raw["mf6"].get(5, [])
    used = [False] * len(yields)
    paired = {}
    for descriptor in (value for value in raw["mf8"].get(5, []) if value["lmf"] == 6):
        match = next(
            (i for i, product in enumerate(yields) if not used[i] and product["zap"] == descriptor["zap"]),
            None,
        )
        if match is None:
            raise ValueError(f"raw MF=8 MT5 product {descriptor['zap']} has no MF=6 yield")
        used[match] = True
        paired[(descriptor["zap"], descriptor["lfs"])] = yields[match]["table"]
    return paired


def compare(candidate_rows, fispact_rows, unmapped, weights, za):
    values, unmatched = [], []
    totals = {}
    for spectrum, weight in weights.items():
        denominator = math.fsum(map(float, weight))

        def one_group(row: np.ndarray) -> float:
            return math.fsum(float(v) * float(w) for v, w in zip(row, weight)) / denominator

        ours = {key: one_group(row) for key, row in candidate_rows.items()}
        theirs = {key: one_group(row) for key, row in fispact_rows.items()}
        our_total = math.fsum(ours.values())
        their_total = math.fsum(theirs.values()) + math.fsum(one_group(u["row"]) for u in unmapped)
        totals[spectrum] = {"candidate_total_b": our_total, "fispact_total_b": their_total}
        for key in sorted(set(ours) | set(theirs)):
            our_share = ours.get(key, 0.0) / our_total if our_total > 0 else 0.0
            their_share = theirs.get(key, 0.0) / their_total if their_total > 0 else 0.0
            if max(our_share, their_share) < p98.SHARE_FLOOR:
                continue
            record = {"za": za, "zap": key[0], "liso": key[1], "spectrum": spectrum,
                      "candidate_share": our_share, "fispact_share": their_share}
            if key not in ours or key not in theirs:
                record.update({"candidate_b": ours.get(key), "fispact_b": theirs.get(key)})
                unmatched.append(record)
                continue
            record.update({"candidate_b": ours[key], "fispact_b": theirs[key],
                           "relative": relative(ours[key], theirs[key])})
            values.append(record)
        for u in unmapped:
            share = one_group(u["row"]) / their_total if their_total > 0 else 0.0
            if share >= p98.SHARE_FLOOR:
                unmatched.append({"za": za, "zap": u["zap"], "processed_lfs": u["processed_lfs"],
                                  "raw_lfs": u["raw_lfs"], "mt": u["mt"], "spectrum": spectrum,
                                  "fispact_share": share, "reason": "no candidate state mapping"})
    return values, unmatched, totals


def summary(values: list[dict]) -> dict:
    relatives = [v["relative"] for v in values]
    within = sum(1 for r in relatives if r <= p98.BAND)
    return {
        "compared_values": len(relatives),
        "within_2e-3": within,
        "fraction_within_2e-3": within / len(relatives) if relatives else 0.0,
        "max_relative": max(relatives) if relatives else math.inf,
        "by_spectrum": {
            s: {"n": sum(1 for v in values if v["spectrum"] == s),
                "within": sum(1 for v in values if v["spectrum"] == s and v["relative"] <= p98.BAND)}
            for s in p98.SPECTRA
        },
        "worst": sorted(values, key=lambda v: -v["relative"])[:20],
    }


def main() -> int:
    if p98.sha256(ROOT / "controls" / "p98_g5_fispact.py") != P98_CONTROL_SHA256:
        raise ValueError("controls/p98_g5_fispact.py differs from the P98 control named by P100")
    if p98.ARCHIVE.stat().st_size != p98.ARCHIVE_BYTES or p98.sha256(p98.ARCHIVE) != p98.ARCHIVE_SHA256:
        raise ValueError("FISPACT-II archive does not match the pinned size/SHA-256")
    if p98.sha256(p98.EBINS_162) != p98.EBINS_162_SHA256:
        raise ValueError("ebins_162 hash mismatch")
    bounds = p98.official_bounds()
    index = json.loads(INDEX.read_text())
    with np.load(NPZ, allow_pickle=False) as library:
        rows = library["rows"].copy()
        sig = library["sig"].copy()
        if not np.array_equal(library["bounds"], bounds):
            raise ValueError("candidate boundaries differ from official ebins_162")
    weights = {name: np.array(function(list(bounds)), dtype=float) for name, function in p98.SPECTRA.items()}
    below_200 = bounds[:-1] < 200.0e6
    raw_hashes = {path.name: p98.sha256(path) for path in sorted(p98.RAW_DIR.glob("*"))}
    for name, prefix in p98.RAW_SHA256_PREFIX.items():
        if not raw_hashes[name].startswith(prefix):
            raise ValueError(f"{name} SHA-256 does not match the protocol prefix {prefix}")

    g5a_values, g5a_unmatched = [], []
    all_values, all_unmatched = [], []
    rule_r = []
    rule_r_doubled = []
    row_report = []
    nuclides = {}
    for target_index, target in enumerate(index["targets"]):
        za = int(target["za"])
        stem = Path(target["file"]).stem.split("-")[-1]
        processed = p98.EXTRACTED / f"{stem}g.asc"
        evaluation = parse_independent(processed)
        raw = parse_independent(p98.RAW_DIR / target["file"])
        if evaluation["za"] != za or raw["za"] != za:
            raise ValueError(f"{processed.name}: ZA mismatch")
        mapping = {
            (int(m["mt"]), int(m["zap"]), int(m["raw_lfs"])): int(m["canonical_liso"])
            for m in target["state_mappings"]
        }

        candidate = {"all": {}, "non_mt5": {}}
        for i in np.flatnonzero(rows[:, 0] == target_index):
            _, mt, zap, liso, _ = (int(x) for x in rows[i])
            if zap > 1:
                key = (zap, liso)
                candidate["all"][key] = candidate["all"].get(key, 0.0) + sig[i]
                if mt != 5:
                    candidate["non_mt5"][key] = candidate["non_mt5"].get(key, 0.0) + sig[i]

        terms = p98.per_mt_terms(evaluation)
        p98.check_split(evaluation, terms, bounds)
        raw_states: dict[tuple[int, int], set[int]] = {}
        for mt, descriptors in raw["mf8"].items():
            for descriptor in descriptors:
                raw_states.setdefault((mt, descriptor["zap"]), set()).add(descriptor["lfs"])
        for kind in ("mf9", "mf10"):
            for mt, products in raw[kind].items():
                for product in products:
                    raw_states.setdefault((mt, product["zap"]), set()).add(product["lfs"])
        processed_states: dict[tuple[int, int], set[int]] = {}
        for mt, zap, lfs, _, _ in terms:
            if lfs is not None:
                processed_states.setdefault((mt, zap), set()).add(lfs)

        def raw_lfs(mt: int, zap: int, lfs: int) -> int | None:
            ours_raw = sorted(raw_states.get((mt, zap), ()))
            theirs = sorted(processed_states[(mt, zap)])
            if ours_raw == theirs:
                return lfs
            if len(ours_raw) != len(theirs):
                return None
            return ours_raw[theirs.index(lfs)]

        fispact = {"all": {}, "non_mt5": {}}
        unmapped = {"all": [], "non_mt5": []}
        yields = raw_mt5_yields(raw)
        for mt, zap, lfs, tables, source in terms:
            if zap <= 1:
                continue
            recovered = None if lfs is None else raw_lfs(mt, zap, lfs)
            row = p98.group_values(tables, bounds)
            if mt == 5 and source == "mf10":
                tab = (yields.get((zap, recovered)) if recovered is not None else None)
                if tab is None:
                    rule_r.append({"za": za, "zap": zap, "processed_lfs": lfs, "raw_lfs": recovered,
                                   "pass": False, "reason": "no raw MT5 yield for the recovered state"})
                else:
                    expected, doubled = rule_r_rows(raw["mf3"][5], tab, bounds)
                    if doubled:
                        rule_r_doubled.append({"za": za, "zap": zap, "raw_lfs": recovered, "energies_eV": doubled})
                    mask = below_200 & ((np.abs(row) >= RULE_R_FLOOR_B) | (np.abs(expected) >= RULE_R_FLOOR_B))
                    deviations = [relative(float(a), float(b)) for a, b in zip(row[mask], expected[mask])]
                    worst = max(deviations) if deviations else 0.0
                    rule_r.append({"za": za, "zap": zap, "processed_lfs": lfs, "raw_lfs": recovered,
                                   "groups": int(np.count_nonzero(mask)), "max_relative": worst,
                                   "pass": worst <= RULE_R_TOLERANCE})
            if lfs is None:
                liso = 0
            elif recovered is not None and (mt, zap, recovered) in mapping:
                liso = mapping[(mt, zap, recovered)]
            else:
                entry = {"mt": mt, "zap": zap, "processed_lfs": lfs, "raw_lfs": recovered, "row": row}
                unmapped["all"].append(entry)
                if mt != 5:
                    unmapped["non_mt5"].append(entry)
                continue
            key = (zap, liso)
            fispact["all"][key] = fispact["all"].get(key, 0.0) + row
            if mt != 5:
                fispact["non_mt5"][key] = fispact["non_mt5"].get(key, 0.0) + row

        values, unmatched, totals_a = compare(candidate["non_mt5"], fispact["non_mt5"], unmapped["non_mt5"], weights, za)
        g5a_values += values
        g5a_unmatched += unmatched
        values_all, unmatched_all, _ = compare(candidate["all"], fispact["all"], unmapped["all"], weights, za)
        all_values += values_all
        all_unmatched += unmatched_all
        for key in sorted(set(candidate["all"]) & set(fispact["all"])):
            ours_row, theirs_row = candidate["all"][key], fispact["all"][key]
            mask = below_200 & ((np.abs(ours_row) >= 1.0e-12) | (np.abs(theirs_row) >= 1.0e-12))
            if not np.any(mask):
                continue
            deviations = [relative(float(a), float(b)) for a, b in zip(ours_row[mask], theirs_row[mask])]
            row_report.append({"za": za, "zap": key[0], "liso": key[1], "max_relative": max(deviations),
                               "within_row_tolerance": max(deviations) <= p98.ROW_TOLERANCE})
        nuclides[str(za)] = {
            "file": processed.name,
            "g5a_values_by_spectrum": {s: sum(1 for v in values if v["spectrum"] == s) for s in p98.SPECTRA},
            "g5a_totals": totals_a,
            "extension_lines": [line for line in target["ledger"] if "MF=3 threshold extension" in line],
            "state_sum_normalized_lines": [line for line in target["ledger"] if "state_sum_normalized" in line],
        }

    g5a = summary(g5a_values)
    coverage = {za: all(n["g5a_values_by_spectrum"][s] >= 1 for s in GATED_SPECTRA) for za, n in nuclides.items()}
    g5a["coverage"] = coverage
    g5a["unmatched"] = g5a_unmatched
    g5a["pass"] = (
        len(index["targets"]) == 8
        and all(coverage.values())
        and g5a["compared_values"] > 0
        and g5a["fraction_within_2e-3"] >= p98.BAND_FRACTION
        and g5a["max_relative"] <= p98.CEILING
    )
    g5b = {
        "sections": len(rule_r),
        "failed": [r for r in rule_r if not r["pass"]],
        "max_relative": max((r.get("max_relative", math.inf) for r in rule_r), default=math.inf),
        "doubled_points_beyond_first_grid_energy": rule_r_doubled,
        "tolerance": RULE_R_TOLERANCE,
    }
    g5b["pass"] = bool(rule_r) and not g5b["failed"]
    reported = summary(all_values)
    reported["unmatched"] = all_unmatched
    report = {
        "gate": "P100-G5",
        "archive_sha256": p98.ARCHIVE_SHA256,
        "candidate_npz_sha256": p98.sha256(NPZ),
        "candidate_index_sha256": p98.sha256(INDEX),
        "raw_sha256": raw_hashes,
        "control_sha256": p98.sha256(Path(__file__)),
        "p98_control_sha256": P98_CONTROL_SHA256,
        "targets_built": len(index["targets"]),
        "G5a_non_mt5": g5a,
        "G5b_rule_r": g5b,
        "reported_all_mt": reported,
        "reported_group_rows_vs_2.5e-3": {
            "rows": len(row_report),
            "within": sum(1 for r in row_report if r["within_row_tolerance"]),
            "worst": sorted(row_report, key=lambda r: -r["max_relative"])[:20],
        },
        "nuclides": nuclides,
        "g5a_values": g5a_values,
        "pass": bool(g5a["pass"] and g5b["pass"]),
    }
    (WORK / "g5.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({
        "G5a": {k: g5a[k] for k in ("compared_values", "within_2e-3", "fraction_within_2e-3", "max_relative", "pass")},
        "G5a_coverage": coverage,
        "G5b": {k: g5b[k] for k in ("sections", "max_relative", "pass")},
        "G5b_failed": len(g5b["failed"]),
        "reported_all_mt": {k: reported[k] for k in ("compared_values", "within_2e-3", "max_relative")},
        "pass": report["pass"],
    }, indent=1))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
