#!/usr/bin/env python3
"""P149 S4: Doppler broadening against NJOY2016 at 293.6, 600 and 1200 K for Fe-56 and Ag-107 (FENDL-3.2c).

For each target and temperature one NJOY2016.79 run (the committed P17 G3 deck: MODER, RECONR and BROADR at 2e-4,
UNRESR for Ag-107's unresolved range) adds GROUPR on the FISPACT-II 709-group structure with iwt = 3 (1/E, constant
flux per unit lethargy) and infinite dilution, for MF=3 MT=102.
  - Pointwise: ACTINV's processed MT=2 and MT=102 (`dump processed-xs FILE MT T E...`) at NJOY's BROADR energies in the
    resolved resonance range [EL, EH] of MF=2. Reported: median, 99th percentile and largest relative difference, and
    the energy of the largest, over points where NJOY's value is at least 1e-8 of its peak in the range.
  - Groups: ACTINV's 709-group MT=102 from `actinv build-library --temperature-K T` against GROUPR. Reported: the
    largest relative group difference over groups at least 1e-8 of the peak, with its group and energies.

Usage: p149_s4_doppler.py [--out results/p149/s4_doppler.json] [--work DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import g3_p17_processing as p17  # noqa: E402
import p126_njoy_fe56 as p126  # noqa: E402

TEMPERATURES = (293.6, 600.0, 1200.0)
TARGETS = [t for t in p17.TARGETS if t["name"] in ("Fe56", "Ag107")]
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target/release/dump"))
BATCH = 4000
CUTOFF = 1.0e-8


def deck(target: dict, temperature: float, boundaries: list[float]) -> str:
    """The P17 deck at `temperature`, plus GROUPR for MT=102 on the 709 groups (iwt 3, infinite dilution)."""
    p17.TEMPERATURE_K = temperature
    lines = p17.njoy_deck(target).splitlines()
    final_tape = 24 if target["unresolved_lssf"] is not None else 23
    groupr = ["groupr", f" -21 -{final_tape} 0 -26/", f" {target['mat']} 1 0 {p126.IWT} 0 1 1 1/",
              f" 'ACTINV P149 S4 {target['name']} {temperature} K 709 groups 1/E'/", f" {temperature:.8g}/",
              f" {p126.SIGMA0:.1e}/", f" {len(boundaries) - 1}/"]
    for i in range(0, len(boundaries), 4):
        groupr.append(" " + " ".join(repr(b) for b in boundaries[i:i + 4]))
    groupr[-1] += "/"
    groupr += [" 3 102 'radiative capture'/", " 0/", " 0/", "moder", " -26 27/"]
    stop = lines.index("stop")
    return "\n".join(lines[:stop] + groupr + ["stop"]) + "\n"


def gendf_mt(path: Path, mt: int) -> tuple[list[float], list[float]]:
    egn, values = None, None
    for _mat, mf, section_mt, lines in p126.sections(path):
        if (mf, section_mt) == (1, 451):
            _za, _awr, _l1, nz, _n1, ntw = p126.record(lines, 0)
            _t, _c2, ngn, _ngg, nw, _n2 = p126.record(lines, 1)
            words, _ = p126.data_words(lines, 2, nw)
            egn = [p126.endf_number(w) for w in words[ntw + nz:ntw + nz + ngn + 1]]
        elif (mf, section_mt) == (3, mt):
            values = [0.0] * (len(egn) - 1)
            index = 1
            while index < len(lines):
                _temp, _c2, ng2, _lo, nw, group = p126.record(lines, index)
                words, index = p126.data_words(lines, index + 1, nw)
                values[group - 1] = p126.endf_number(words[nw - 1])
    if egn is None or values is None:
        raise RuntimeError(f"{path}: no MF=3/MT={mt} in the GENDF")
    return egn, values


def resolved_range(endf: Path, mat: int) -> tuple[float, float]:
    """[EL, EH] of the first energy range in MF=2/MT=151, which must be resolved (LRU = 1)."""
    for (section_mat, mf, mt), body in p17.sections(endf):
        if (section_mat, mf, mt) == (mat, 2, 151):
            el, eh, lru = p17.fields(body[2])[:3]
            if int(lru) != 1:
                raise RuntimeError(f"{endf}: the first MF=2 range is not resolved (LRU={lru})")
            return p17.endf_float(el), p17.endf_float(eh)
    raise RuntimeError(f"{endf}: no MF=2/MT=151")


def actinv_points(endf: Path, mt: int, temperature: float, energies: np.ndarray, work: Path) -> np.ndarray:
    values = []
    for start in range(0, len(energies), BATCH):
        chunk = energies[start:start + BATCH]
        run = p17.checked_run([DUMP, "processed-xs", endf, str(mt), f"{temperature:.8g}",
                               *[f"{e:.17e}" for e in chunk]], cwd=work, timeout=1800.0)
        rows = [line.split() for line in run.stdout.splitlines() if line.startswith("X ")]
        if len(rows) != len(chunk):
            raise RuntimeError(f"dump returned {len(rows)} points for {len(chunk)} energies")
        values.extend(float(row[2]) for row in rows)
    return np.asarray(values)


def compare(actual: np.ndarray, reference: np.ndarray, energy: np.ndarray) -> dict:
    keep = np.abs(reference) >= CUTOFF * float(np.max(np.abs(reference)))
    relative = np.abs(actual[keep] - reference[keep]) / np.abs(reference[keep])
    worst = int(np.argmax(relative))
    return {"points": int(np.count_nonzero(keep)), "median_relative": float(np.median(relative)),
            "p99_relative": float(np.percentile(relative, 99.0)), "maximum_relative": float(relative[worst]),
            "energy_at_maximum_ev": float(energy[keep][worst])}


def run_case(target: dict, temperature: float, work: Path, boundaries: list[float]) -> dict:
    case = work / f"{target['name']}_{temperature:g}"
    case.mkdir(parents=True)
    endf = p17.FENDL_DIR / target["filename"]
    os.symlink(endf, case / "tape20")
    text = deck(target, temperature, boundaries)
    (case / "njoy.inp").write_text(text)
    p17.checked_run([p17.NJOY], cwd=case, input_text=text, timeout=3600.0)
    low, high = resolved_range(endf, target["mat"])
    pointwise = {}
    for mt in (2, 102):
        table = p17.parse_mf3(case / "tape25", target["mat"], mt)
        inside = (table["energy"] >= low) & (table["energy"] <= high)
        energy, reference = table["energy"][inside], table["sigma"][inside]
        pointwise[f"MT{mt}"] = compare(actinv_points(endf, mt, temperature, energy, case), reference, energy)
    p17.TEMPERATURE_K = temperature  # actinv_library builds at the module's temperature
    bounds, library_sigma, library = p17.actinv_library(endf, case / "actinv.npz", [102])
    egn, groupr = gendf_mt(case / "tape27", 102)
    if not np.allclose(egn, bounds, rtol=1e-6, atol=0.0):
        raise RuntimeError("GROUPR and ACTINV group boundaries differ")
    groups = np.asarray(groupr)
    actual = library_sigma[102]
    keep = np.abs(groups) >= CUTOFF * float(np.max(np.abs(groups)))
    relative = np.abs(actual[keep] - groups[keep]) / np.abs(groups[keep])
    worst_local = int(np.argmax(relative))
    worst = int(np.flatnonzero(keep)[worst_local])
    # Groups by where they sit against the top of the resolved range, so a group that straddles the resolved and
    # unresolved (or smooth) representations is reported apart from groups wholly on one side.
    lower, upper = np.asarray(bounds[:-1]), np.asarray(bounds[1:])
    region = np.where(upper <= high, "inside_resolved", np.where(lower >= high, "above_resolved", "straddling_top"))
    by_region = {}
    for name in ("inside_resolved", "straddling_top", "above_resolved"):
        mask = keep & (region == name)
        if not np.any(mask):
            continue
        rel = np.abs(actual[mask] - groups[mask]) / np.abs(groups[mask])
        at = int(np.flatnonzero(mask)[int(np.argmax(rel))])
        by_region[name] = {"groups": int(np.count_nonzero(mask)), "median_relative": float(np.median(rel)),
                           "maximum_relative": float(np.max(rel)), "worst_group": at,
                           "worst_group_low_ev": float(bounds[at]), "worst_group_high_ev": float(bounds[at + 1])}
    return {
        "target": target["name"], "temperature_K": temperature, "resolved_range_ev": [low, high],
        "njoy_deck_sha256": p17.sha256(case / "njoy.inp"), "pendf_sha256": p17.sha256(case / "tape25"),
        "gendf_sha256": p17.sha256(case / "tape27"), "actinv_library_sha256": library["library_sha256"],
        "pointwise": pointwise,
        "groups_MT102": {"groups_compared": int(np.count_nonzero(keep)),
                         "median_relative": float(np.median(relative)),
                         "maximum_relative": float(relative[worst_local]), "worst_group": worst,
                         "worst_group_low_ev": float(bounds[worst]), "worst_group_high_ev": float(bounds[worst + 1]),
                         "actinv_b": float(actual[worst]), "groupr_b": float(groups[worst]),
                         "by_region": by_region},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "results/p149/s4_doppler.json")
    ap.add_argument("--work", type=Path)
    args = ap.parse_args()
    boundaries = p126.vendored_boundaries()
    work = (args.work or Path(tempfile.mkdtemp(prefix="p149-s4-", dir=str(ROOT / "target")))).resolve()
    cases = []
    for target in TARGETS:
        for temperature in TEMPERATURES:
            cases.append(run_case(target, temperature, work, boundaries))
            c = cases[-1]
            print(f"{target['name']} {temperature:g} K: MT102 point max {c['pointwise']['MT102']['maximum_relative']:.3e}"
                  f" group max {c['groups_MT102']['maximum_relative']:.3e}", file=sys.stderr, flush=True)
    record = {"schema": "actinv-p149-s4-doppler-1", "protocol": "P149 S4", "njoy": str(p17.NJOY),
              "njoy_commit": p17.NJOY_COMMIT, "reconstruction_tolerance": p17.RECONSTRUCTION_TOLERANCE,
              "groupr_weight": "iwt 3 (1/E), infinite dilution", "relative_cutoff": CUTOFF,
              "actinv_binary_sha256": p17.sha256(p17.ACTINV), "dump_binary_sha256": p17.sha256(DUMP),
              "cases": cases}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
