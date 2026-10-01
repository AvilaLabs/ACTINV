#!/usr/bin/env python3
"""P98 G5: candidate gamma residual production vs FISPACT-II TENDL-2017 tal2017-g/gxs-162.

Inputs:
  - the candidate's G5 build of the 8 raw TENDL-2017 gamma files (target/p98/g5_gamma_2017.npz
    and its index, written by controls/p94_g5_actinv.py under ACTINV_GAMMA_PROTOCOL=P98);
  - the pinned FISPACT-II archive, verified by size and SHA-256, with the gamma gxs-162 records
    stream-extracted by ~/nuclear-data/scripts/extract_fispact_gamma.py.

FISPACT-II side (records carry no MF=6/MF=8; MT5 residuals are MF=10 with ordinally relabelled
LFS, recovered by rank from the raw evaluation): group values reconstructed with the P10 G5 reader (parse_independent,
production_terms, evaluate at each group's lower bound, as processed_row does). The reader's
term logic is split per MT here so each raw LFS can be mapped through the candidate index's
(MT, ZAP, raw LFS) -> canonical LISO record; the split is checked against production_terms for
every (ZAP, LFS). One addition, disclosed in the ledger: the P10 reader was written for charged
particles and has no rule for the gamma single-neutron channel (MT4 and MT50-91 are absent from
mt_products.json). The P94-specified rule is applied: MT4 with its own explicit product is read
by the P10 logic; otherwise MT50-91 detail without explicit products each give residual
(Z, A-1) ground, and only when no such detail exists does MT4 give it.

Residual key (target ZA, ZAP, canonical LISO), ZAP > 0. Arithmetic residuals have no raw LFS
and are ground (LISO 0) on both sides.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from g5_p10_charged import evaluate, parse_independent, production_terms, relative  # noqa: E402
from p94_g5_actinv import SPECTRA  # noqa: E402

WORK = ROOT / "target" / "p98"
NPZ = WORK / "g5_gamma_2017.npz"
INDEX = WORK / "g5_gamma_2017_index.json"
FISPACT = Path.home() / "nuclear-data" / "fispact-tendl2017"
ARCHIVE = FISPACT / "TENDL2017data.tar.bz2"
ARCHIVE_SHA256 = "7f305df2277f71a7d7d6d1e1ebfec8dea9415d813e283990c1fb65804b05bec8"
ARCHIVE_BYTES = 2_595_437_294
EXTRACTED = Path(os.environ.get("P98_FISPACT_GAMMA", FISPACT / "gamma-gxs-162"))
EBINS_162 = FISPACT / "ebins" / "ebins_162"
EBINS_162_SHA256 = "4b1ba7ec855aa305b3312cb57d75cbcd6be41b4e67e93070df104bd62b500b0e"
RAW_DIR = Path.home() / "nuclear-data" / "tendl-2017" / "files" / "g"
RAW_SHA256_PREFIX = {"g-Cu063.tendl": "fad99af7", "g-Pb208.tendl": "222d9875"}
GAMMA = (0, 0)
SHARE_FLOOR = 1.0e-3
BAND = 2.0e-3
BAND_FRACTION = 0.95
CEILING = 2.0e-2
ROW_TOLERANCE = 2.5e-3  # P10 Amendment B, reported only
MT_PRODUCTS = {
    int(mt): tuple(delta)
    for mt, delta in json.loads(
        (ROOT / "crates/actinv-data/data/mt_products.json").read_text()
    )["table"].items()
}
SKIPPED = {1, 2, 3, 5, 19, 20, 21, 27, 38, 101, 444}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def official_bounds() -> np.ndarray:
    import re

    lines = EBINS_162.read_text().splitlines()
    numbers = re.findall(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)[Ee][+-]?\d+", " ".join(lines[2:]))
    descending = np.array([float(value) for value in numbers], dtype=float)
    if int(lines[1]) != 162 or len(descending) != 163 or np.any(np.diff(descending) >= 0.0):
        raise ValueError("ebins_162 is not a complete descending 162-group structure")
    return descending[::-1].copy()


def explicit(evaluation: dict, mt: int) -> bool:
    return (
        bool(evaluation["mf9"].get(mt))
        or bool(evaluation["mf10"].get(mt))
        or any(product["lmf"] in (3, 6, 9, 10) for product in evaluation["mf8"].get(mt, []))
    )


def per_mt_terms(evaluation: dict) -> list[tuple[int, int, int | None, tuple[dict, ...], str]]:
    """(MT, ZAP, raw LFS or None for arithmetic, tables, source), the P10 logic split per MT."""
    terms = []
    for mt, descriptors in evaluation["mf8"].items():
        for descriptor in descriptors:
            if descriptor["lmf"] == 3:
                terms.append((mt, descriptor["zap"], descriptor["lfs"], (evaluation["mf3"][mt],), "mf8_lmf3"))
    for mt, products in evaluation["mf9"].items():
        for product in products:
            terms.append((mt, product["zap"], product["lfs"], (evaluation["mf3"][mt], product["table"]), "mf9"))
    for mt, products in evaluation["mf10"].items():
        for product in products:
            terms.append((mt, product["zap"], product["lfs"], (product["table"],), "mf10"))
    if 5 in evaluation["mf3"] and 5 in evaluation["mf6"] and 5 in evaluation["mf8"]:
        yields = evaluation["mf6"][5]
        used = [False] * len(yields)
        for descriptor in (value for value in evaluation["mf8"][5] if value["lmf"] == 6):
            match = next(
                (i for i, product in enumerate(yields) if not used[i] and product["zap"] == descriptor["zap"]),
                None,
            )
            if match is None:
                raise ValueError(f"MF=8 MT5 product {descriptor['zap']} has no MF=6 yield")
            used[match] = True
            terms.append((5, descriptor["zap"], descriptor["lfs"], (evaluation["mf3"][5], yields[match]["table"]), "mf6_mt5"))
    target_z, target_a = divmod(evaluation["za"], 1000)
    for mt, reaction in evaluation["mf3"].items():
        if mt in SKIPPED or 201 <= mt <= 207 or 600 <= mt <= 849 or mt >= 1000:
            continue
        if explicit(evaluation, mt) or mt not in MT_PRODUCTS:
            continue
        delta_z, delta_a = MT_PRODUCTS[mt]
        residual = (target_z + delta_z + GAMMA[0]) * 1000 + (target_a + delta_a + GAMMA[1] - 1)
        terms.append((mt, residual, None, (reaction,), "arithmetic"))
    # Gamma single-neutron channel (P94 rule; absent from the P10 reader).
    one_neutron = target_z * 1000 + target_a - 1
    if 4 in evaluation["mf3"] and not explicit(evaluation, 4):
        detail = [mt for mt in sorted(evaluation["mf3"]) if 50 <= mt <= 91 and not explicit(evaluation, mt)]
        for mt in detail or [4]:
            terms.append((mt, one_neutron, None, (evaluation["mf3"][mt],), "gamma_single_neutron"))
    return terms


def group_values(tables: tuple[dict, ...], bounds: np.ndarray) -> np.ndarray:
    return np.array(
        [math.prod(evaluate(tab, float(low)) for tab in tables) for low in bounds[:-1]],
        dtype=float,
    )


def check_split(evaluation: dict, terms: list, bounds: np.ndarray) -> int:
    """Per-MT split reproduces production_terms for every (ZAP, LFS) it covers."""
    keys = {
        (zap, 0 if lfs is None else lfs)
        for _, zap, lfs, _, source in terms
        if source != "gamma_single_neutron" and zap > 0
    }
    for zap, lfs in keys:
        reference = production_terms(evaluation, zap, lfs, GAMMA)
        expected = sum(
            (group_values(product, bounds) for products in reference.values() for product in products),
            np.zeros(len(bounds) - 1),
        )
        mine = sum(
            (
                group_values(tables, bounds)
                for _, z, l, tables, source in terms
                if source != "gamma_single_neutron" and z == zap and (0 if l is None else l) == lfs
            ),
            np.zeros(len(bounds) - 1),
        )
        if not np.allclose(expected, mine, rtol=1.0e-12, atol=0.0):
            raise ValueError(f"per-MT split differs from production_terms for {zap}/{lfs}")
    return len(keys)


def main() -> int:
    if ARCHIVE.stat().st_size != ARCHIVE_BYTES or sha256(ARCHIVE) != ARCHIVE_SHA256:
        raise ValueError("FISPACT-II archive does not match the pinned size/SHA-256")
    if sha256(EBINS_162) != EBINS_162_SHA256:
        raise ValueError("ebins_162 hash mismatch")
    bounds = official_bounds()
    index = json.loads(INDEX.read_text())
    with np.load(NPZ, allow_pickle=False) as library:
        rows = library["rows"].copy()
        sig = library["sig"].copy()
        if not np.array_equal(library["bounds"], bounds):
            raise ValueError("candidate boundaries differ from official ebins_162")
    weights = {name: np.array(function(list(bounds)), dtype=float) for name, function in SPECTRA.items()}
    below_200 = bounds[:-1] < 200.0e6

    raw_hashes = {}
    for path in sorted(RAW_DIR.glob("*")):
        raw_hashes[path.name] = sha256(path)
    for name, prefix in RAW_SHA256_PREFIX.items():
        if not raw_hashes[name].startswith(prefix):
            raise ValueError(f"{name} SHA-256 does not match the protocol prefix {prefix}")

    values = []
    unmatched = []
    row_report = []
    nuclides = {}
    for target_index, target in enumerate(index["targets"]):
        za = int(target["za"])
        stem = Path(target["file"]).stem.split("-")[-1]
        candidates = sorted(EXTRACTED.glob(f"{stem}g.asc"))  # exact: Nb093mg.asc is the isomer target
        if len(candidates) != 1:
            raise ValueError(f"expected one FISPACT gamma record for {stem}, found {[p.name for p in candidates]}")
        processed = candidates[0]
        evaluation = parse_independent(processed)
        if evaluation["za"] != za:
            raise ValueError(f"{processed.name}: ZA {evaluation['za']} != {za}")
        mapping = {
            (int(m["mt"]), int(m["zap"]), int(m["raw_lfs"])): int(m["canonical_liso"])
            for m in target["state_mappings"]
        }

        candidate_rows: dict[tuple[int, int], np.ndarray] = {}
        for i in np.flatnonzero(rows[:, 0] == target_index):
            _, mt, zap, liso, _ = (int(x) for x in rows[i])
            if zap > 1:
                key = (zap, liso)
                candidate_rows[key] = candidate_rows.get(key, 0.0) + sig[i]

        terms = per_mt_terms(evaluation)
        split_keys = check_split(evaluation, terms, bounds)
        # The processed record carries no MF=6/MF=8; MT5 residuals are MF=10 sections whose LFS
        # FISPACT-II has relabelled ordinally (raw 0, 10 -> 0, 1). Recover each raw LFS by rank
        # within its (MT, ZAP) from the raw evaluation the candidate built, then map it through
        # the candidate index. A count mismatch leaves the state unmapped (unmatched).
        raw = parse_independent(RAW_DIR / target["file"])
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
        relabelled = []

        def raw_lfs(mt: int, zap: int, lfs: int) -> int | None:
            ours_raw = sorted(raw_states.get((mt, zap), ()))
            theirs = sorted(processed_states[(mt, zap)])
            if ours_raw == theirs:
                return lfs
            if len(ours_raw) != len(theirs):
                return None
            recovered = ours_raw[theirs.index(lfs)]
            relabelled.append({"mt": mt, "zap": zap, "processed_lfs": lfs, "raw_lfs": recovered})
            return recovered

        fispact_rows: dict[tuple[int, int], np.ndarray] = {}
        unmapped = []
        sources = {}
        for mt, zap, lfs, tables, source in terms:
            if zap <= 1:  # photons/sentinels, and the emitted free neutron (not a residual)
                continue
            sources[source] = sources.get(source, 0) + 1
            recovered = None if lfs is None else raw_lfs(mt, zap, lfs)
            if lfs is None:
                liso = 0
            elif recovered is not None and (mt, zap, recovered) in mapping:
                liso = mapping[(mt, zap, recovered)]
            else:
                unmapped.append({"mt": mt, "zap": zap, "processed_lfs": lfs, "raw_lfs": recovered,
                                 "source": source, "row": group_values(tables, bounds)})
                continue
            key = (zap, liso)
            fispact_rows[key] = fispact_rows.get(key, 0.0) + group_values(tables, bounds)

        nuclide_summary = {"file": processed.name, "sha256": sha256(processed),
                           "fispact_terms_by_source": sources, "split_keys_checked": split_keys,
                           "candidate_residuals": len(candidate_rows), "fispact_residuals": len(fispact_rows)}
        for spectrum, weight in weights.items():
            denominator = math.fsum(map(float, weight))

            def one_group(row: np.ndarray) -> float:
                return math.fsum(float(v) * float(w) for v, w in zip(row, weight)) / denominator

            ours = {key: one_group(row) for key, row in candidate_rows.items()}
            theirs = {key: one_group(row) for key, row in fispact_rows.items()}
            our_total = math.fsum(ours.values())
            their_total = math.fsum(theirs.values()) + math.fsum(one_group(u["row"]) for u in unmapped)
            nuclide_summary[spectrum] = {"candidate_total_b": our_total, "fispact_total_b": their_total}
            for key in sorted(set(ours) | set(theirs)):
                our_share = ours.get(key, 0.0) / our_total if our_total > 0 else 0.0
                their_share = theirs.get(key, 0.0) / their_total if their_total > 0 else 0.0
                if max(our_share, their_share) < SHARE_FLOOR:
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
                if share >= SHARE_FLOOR:
                    unmatched.append({"za": za, "zap": u["zap"], "processed_lfs": u["processed_lfs"],
                                      "raw_lfs": u["raw_lfs"], "mt": u["mt"],
                                      "spectrum": spectrum, "fispact_share": share,
                                      "reason": "no candidate state mapping for this raw LFS"})
        for key in sorted(set(candidate_rows) & set(fispact_rows)):
            ours_row, theirs_row = candidate_rows[key], fispact_rows[key]
            compare = below_200 & ((np.abs(ours_row) >= 1.0e-12) | (np.abs(theirs_row) >= 1.0e-12))
            if not np.any(compare):
                continue
            deviations = [relative(float(a), float(b)) for a, b in zip(ours_row[compare], theirs_row[compare])]
            worst = int(np.argmax(deviations))
            group = int(np.flatnonzero(compare)[worst])
            row_report.append({"za": za, "zap": key[0], "liso": key[1], "groups": int(np.count_nonzero(compare)),
                               "max_relative": deviations[worst],
                               "worst_group_eV": [float(bounds[group]), float(bounds[group + 1])],
                               "within_row_tolerance": deviations[worst] <= ROW_TOLERANCE})
        nuclide_summary["relabelled_states"] = relabelled
        nuclide_summary["unmapped_raw_states"] = [{k: v for k, v in u.items() if k != "row"} for u in unmapped]
        nuclides[str(za)] = nuclide_summary

    relatives = [value["relative"] for value in values]
    within = sum(1 for value in relatives if value <= BAND)
    fraction = within / len(relatives) if relatives else 0.0
    worst = max(relatives) if relatives else math.inf
    passed = bool(relatives) and len(index["targets"]) == 8 and fraction >= BAND_FRACTION and worst <= CEILING
    report = {
        "gate": "P98-G5",
        "archive_sha256": ARCHIVE_SHA256,
        "archive_bytes": ARCHIVE_BYTES,
        "candidate_npz_sha256": sha256(NPZ),
        "candidate_index_sha256": sha256(INDEX),
        "raw_sha256": raw_hashes,
        "control_sha256": sha256(Path(__file__)),
        "targets_built": len(index["targets"]),
        "compared_values": len(relatives),
        "within_2e-3": within,
        "fraction_within_2e-3": fraction,
        "max_relative": worst,
        "worst_values": sorted(values, key=lambda v: -v["relative"])[:20],
        "unmatched_residuals": unmatched,
        "group_rows_vs_2.5e-3": {
            "rows": len(row_report),
            "within": sum(1 for r in row_report if r["within_row_tolerance"]),
            "worst": sorted(row_report, key=lambda r: -r["max_relative"])[:20],
        },
        "state_sum_normalized_ledger_lines": sum(
            1 for target in index["targets"] for line in target["ledger"] if "state_sum_normalized" in line
        ),
        "izap0_photofission_reads": [
            {"za": target["za"], "line": line} for target in index["targets"] for line in target["ledger"]
            if "IZAP=0" in line and "photofission" in line
        ],
        "nuclides": nuclides,
        "values": values,
        "pass": passed,
    }
    (WORK / "g5.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({key: report[key] for key in ("compared_values", "within_2e-3", "fraction_within_2e-3",
                                                     "max_relative", "pass")}, indent=1))
    print("unmatched", len(unmatched), "rows", report["group_rows_vs_2.5e-3"]["rows"],
          "rows within", report["group_rows_vs_2.5e-3"]["within"])
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
