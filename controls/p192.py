#!/usr/bin/env python3
"""P192 harness: ACTINV's spectrum-averaged cross sections on UKAEA's integro-differential validation table.

`run` reads the CoNDERC effective-cross-section table (RR_table_oct2020.txt) and its fusion spectra, maps each
reaction label onto ACTINV's default TENDL-2025 709-group library, collapses the library row(s) with the spectrum
redistributed onto the library groups (equal lethargy, FISPACT-II's CNVTYPE 0; equal energy as a sensitivity), and
cross-checks a seeded sample of rows against `actinv inspect xs` with the same spectrum as a `rebin` spec. It writes
results/p192_record.json (or target/p192-rehearsal/record.json with --rehearsal). It prints only counts.

The inputs are never tracked: the table, the spectra and the library stay under ~/nuclear-data and are pinned by
SHA-256 in controls/check_p192.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
DATA = Path(os.environ.get("ACTINV_P192_DATA", HOME / "nuclear-data"))
TABLE = DATA / "conderc-effective-dl" / "RR_table_oct2020.txt"
SPECTRA = DATA / "conderc-spectra-dl"
LIBRARY = HOME / "nuclear-data" / "tendl-2025" / "builds" / "full" / "neutron.n.p10.npz"
INDEX = LIBRARY.with_name(LIBRARY.stem + "_index.json")
RECORD = ROOT / "results" / "p192_record.json"
REHEARSAL_RECORD = ROOT / "target" / "p192-rehearsal" / "record.json"
SCHEMA = "actinv-p192-record-1"
LIBRARY_STRUCTURE = "fispact-709"
CROSSCHECK_SEED = 192
CROSSCHECK_ROWS = 24

SYMBOLS = ("n H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb "
           "Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf "
           "Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm").split()
Z_OF = {symbol: z for z, symbol in enumerate(SYMBOLS)}
# outgoing particles -> (MT, emitted Z, emitted A); the product is the target plus the neutron minus these
CHANNELS = {"2n": (16, 0, 2), "3n": (17, 0, 3), "p": (103, 1, 1), "a": (107, 2, 4), "g": (102, 0, 0),
            "d": (104, 1, 2), "np": (28, 1, 2), "na": (22, 2, 5), "pa": (112, 3, 5), "n": (4, 0, 1)}
STATE_OF_SUFFIX = {"": None, "g": 0, "m": 1, "n": 2}
LABEL = re.compile(r"([A-Z][a-z]?)-(\d+)\(n,([a-z0-9]+)\)([A-Z][a-z]?)-(\d+)([gmn]?)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_table(path: Path = TABLE) -> list[dict]:
    lines = path.read_text(encoding="latin-1").splitlines()
    header = [cell for cell in lines[0].split("\t") if cell]
    if header != ["Reaction", "Spectrum", "sigma exp.", "Exp. uncert", "C/E", "C - T17"]:
        raise ValueError(f"unexpected table header {header}")
    rows = []
    for number, line in enumerate(lines[1:], start=1):
        cells = [cell.strip() for cell in line.split("\t") if cell.strip()]
        if not cells:
            continue
        if len(cells) != 6:
            raise ValueError(f"table line {number + 1} has {len(cells)} cells")
        reaction, spectrum, e, u, ce, c = cells
        rows.append({"row": len(rows), "reaction": reaction, "spectrum": spectrum, "E_b": float(e),
                     "E_unc_b": float(u), "CE_fispact_table": float(ce), "C_fispact_b": float(c)})
    return rows


def parse_label(label: str) -> dict:
    match = LABEL.fullmatch(label)
    if not match:
        return {"status": "label_unparsed"}
    tsym, ta, channel, psym, pa, suffix = match.groups()
    if channel not in CHANNELS or tsym not in Z_OF or psym not in Z_OF:
        return {"status": "label_unparsed"}
    mt, dz, da = CHANNELS[channel]
    tz, ta, pz, pa = Z_OF[tsym], int(ta), Z_OF[psym], int(pa)
    if (tz - dz, ta + 1 - da) != (pz, pa):
        return {"status": "label_inconsistent"}
    return {"status": "parsed", "target_za": tz * 1000 + ta, "mt": mt, "product_za": pz * 1000 + pa,
            "product_state": STATE_OF_SUFFIX[suffix], "suffix": suffix}


def read_spectrum(name: str) -> tuple[np.ndarray, np.ndarray, Path]:
    """Ascending boundaries (eV) and group fluxes (the DATA column) of CoNDERC spectrum `name`."""
    matches = sorted(SPECTRA.glob(f"*_{name}.txt"))
    if len(matches) != 1:
        raise ValueError(f"spectrum {name}: {len(matches)} files")
    upper, lower, flux, total = [], [], [], None
    for line in matches[0].read_text(encoding="latin-1").splitlines()[1:]:
        cells = line.split()
        if not cells:
            continue
        if cells[0] == "TOTAL" and len(cells) == 2:
            total = float(cells[1])
            continue
        upper.append(float(cells[1]))
        lower.append(float(cells[2]))
        flux.append(float(cells[4]))
    upper, lower, flux = (np.array(values[::-1]) for values in (upper, lower, flux))
    if not (np.all(lower[1:] == upper[:-1]) and np.all(upper > lower) and np.all(flux >= 0.0)):
        raise ValueError(f"spectrum {name}: groups are not contiguous and nonnegative")
    if total is not None and abs(flux.sum() - total) > 1e-3 * total:
        raise ValueError(f"spectrum {name}: group fluxes do not add up to the file's TOTAL")
    return np.concatenate(([lower[0]], upper)), flux, matches[0]


def rebin(bounds: np.ndarray, flux: np.ndarray, dest: np.ndarray, convention: str) -> np.ndarray:
    """Group fluxes on `dest` with each source group's flux spread flat in lethargy or in energy."""
    out = np.zeros(len(dest) - 1)
    for low, high, value in zip(bounds[:-1], bounds[1:], flux):
        if value == 0.0:
            continue
        lo = np.clip(dest[:-1], low, high)
        hi = np.clip(dest[1:], low, high)
        if convention == "equal_lethargy":
            share = np.log(hi / lo) / math.log(high / low)
        else:
            share = (hi - lo) / (high - low)
        out += value * share
    return out


class Library:
    def __init__(self) -> None:
        archive = np.load(LIBRARY, allow_pickle=False)
        self.rows = np.asarray(archive["rows"], dtype=np.int64)
        self.sigma = np.asarray(archive["sig"], dtype=float)
        self.bounds = np.asarray(archive["bounds"], dtype=float)
        index = json.loads(INDEX.read_text(encoding="utf-8"))
        self.target = {int(t["za"]): i for i, t in enumerate(index["targets"]) if int(t.get("liso", 0)) == 0}

    def select(self, mapping: dict) -> tuple[list[int], str]:
        target = self.target.get(mapping["target_za"])
        if target is None:
            return [], "target_unavailable"
        same = (self.rows[:, 0] == target) & (self.rows[:, 1] == mapping["mt"])
        total = np.flatnonzero(same & (self.rows[:, 2] == -1))
        split = same & (self.rows[:, 2] == mapping["product_za"])
        state = mapping["product_state"]
        if state is None:
            if len(total) == 1:
                return [int(total[0])], "total_row"
            rows = np.flatnonzero(split)
            return ([int(r) for r in rows], "sum_of_states") if len(rows) else ([], "reaction_unavailable")
        rows = np.flatnonzero(split & (self.rows[:, 3] == state))
        if len(rows) == 1:
            return [int(rows[0])], "state_row"
        if state == 0 and not split.any() and len(total) == 1:
            return [int(total[0])], "ground_from_unsplit_total"
        return [], "state_unavailable"

    def collapse(self, rows: list[int], weights: np.ndarray) -> float:
        return float(np.dot(self.sigma[rows].sum(axis=0), weights) / weights.sum())


def binary_one_group(binary: Path, mapping: dict, bounds: np.ndarray, flux: np.ndarray, workdir: Path) -> list[dict]:
    spec = {"structure": LIBRARY_STRUCTURE, "boundaries_eV": bounds.tolist(), "flux_per_group": flux.tolist(),
            "rebin": {"convention": "equal_lethargy"}}
    path = workdir / "spectrum.json"
    path.write_text(json.dumps(spec))
    z, a = divmod(mapping["target_za"], 1000)
    cmd = [str(binary), "inspect", "xs", str(LIBRARY), "--target", f"{SYMBOLS[z]}{a}", "--mt", str(mapping["mt"]),
           "--spectrum", str(path)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise RuntimeError(f"actinv inspect xs failed ({done.returncode}): {done.stderr[-400:]}")
    return json.loads(done.stdout)["reactions"]


def run(args: argparse.Namespace) -> int:
    rows = read_table()
    library = Library()
    spectra = {}
    for name in sorted({row["spectrum"] for row in rows}):
        bounds, flux, path = read_spectrum(name)
        weights = {c: rebin(bounds, flux, library.bounds, c) for c in ("equal_lethargy", "equal_energy")}
        spectra[name] = {"bounds": bounds, "flux": flux, "weights": weights, "file": path.name,
                         "sha256": sha256(path), "landed_fraction": float(weights["equal_lethargy"].sum() / flux.sum())}
    for row in rows:
        mapping = parse_label(row["reaction"])
        row["mapping"] = mapping
        if mapping["status"] != "parsed":
            row["status"] = mapping["status"]
            continue
        picked, how = library.select(mapping)
        row["selection"] = how
        if not picked:
            row["status"] = how
            continue
        row["library_rows"] = [[int(v) for v in library.rows[r][:4]] for r in picked]
        weights = spectra[row["spectrum"]]["weights"]
        row["C_actinv_b"] = library.collapse(picked, weights["equal_lethargy"])
        row["C_actinv_equal_energy_b"] = library.collapse(picked, weights["equal_energy"])
        ok = all(math.isfinite(row[k]) and row[k] > 0.0 for k in ("C_actinv_b", "C_actinv_equal_energy_b"))
        row["status"] = "scored" if ok else "nonpositive_calculated_value"
    scored = [row for row in rows if row["status"] == "scored"]
    sample = sorted(random.Random(CROSSCHECK_SEED).sample([row["row"] for row in scored], CROSSCHECK_ROWS))
    checks = []
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        for index in sample:
            row = rows[index]
            spectrum = spectra[row["spectrum"]]
            reactions = binary_one_group(Path(args.actinv_bin), row["mapping"], spectrum["bounds"], spectrum["flux"],
                                         Path(tmp))
            wanted = {tuple(r[1:4]) for r in row["library_rows"]}
            values = [r["one_group_xs_b"] for r in reactions
                      if (r["mt"], r["product_za"], r["product_lfs"]) in wanted]
            binary_value = float(sum(values)) if len(values) == len(wanted) else None
            rel = (None if binary_value is None
                   else abs(binary_value - row["C_actinv_b"]) / max(abs(row["C_actinv_b"]), 1e-300))
            checks.append({"row": index, "rows_matched": len(values), "binary_one_group_b": binary_value,
                           "relative_difference": rel})
    record = {
        "schema": SCHEMA, "rehearsal": bool(args.rehearsal),
        "inputs": {"table": {"path": str(TABLE), "sha256": sha256(TABLE)},
                   "library": {"path": str(LIBRARY), "sha256": sha256(LIBRARY)},
                   "index": {"path": str(INDEX), "sha256": sha256(INDEX)},
                   "actinv_binary": {"path": str(args.actinv_bin), "sha256": sha256(Path(args.actinv_bin))}},
        "environment": {"git_head": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                                                   text=True).stdout.strip(),
                        "git_dirty_paths": subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                                                           "--untracked-files=no"], capture_output=True,
                                                          text=True).stdout.strip()},
        "spectra": {name: {"file": s["file"], "sha256": s["sha256"], "groups": len(s["flux"]),
                           "landed_fraction": s["landed_fraction"]} for name, s in spectra.items()},
        "crosscheck": {"seed": CROSSCHECK_SEED, "rows": checks},
        "rows": rows,
    }
    out = REHEARSAL_RECORD if args.rehearsal else RECORD
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    counts = Counter(row["status"] for row in rows)
    print(f"p192: wrote {out}: {len(rows)} rows, " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
          + f"; crosscheck {len(checks)} rows")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--actinv-bin", required=True)
    p_run.add_argument("--rehearsal", action="store_true")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
