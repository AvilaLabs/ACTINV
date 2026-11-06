#!/usr/bin/env python3
"""P193 harness: a per-reaction choice among four evaluations, scored leave-one-facility-out on P192's table.

`run` collapses every row of UKAEA's integro-differential table (P192's inputs and mapping rules, imported from
controls/p192.py) with four ACTINV-format 709-group libraries: TENDL-2025 (the default), TENDL-2023, TENDL-2017 and
ENDF/B-VIII.1. It cross-checks a seeded sample of non-default values against `actinv inspect xs` and writes
results/p193_record.json (or target/p193-rehearsal/record.json with --rehearsal). The choice rule and the scoring
are in controls/check_p193.py. It prints only counts.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p192  # noqa: E402

HOME = Path.home()
LIBRARIES = {
    "tendl-2025": HOME / "nuclear-data" / "tendl-2025" / "builds" / "full" / "neutron.n.p10.npz",
    "tendl-2023": HOME / "nuclear-data" / "tendl-2023" / "actinv_tendl2023_709g.npz",
    "tendl-2017": HOME / "nuclear-data" / "tendl-2017" / "build" / "neutron.n.p10.npz",
    "endfb-viii.1": HOME / "nuclear-data" / "endfb-viii.1-p172" / "endfb81_all_709g.npz",
}
DEFAULT = "tendl-2025"
RECORD = ROOT / "results" / "p193_record.json"
REHEARSAL_RECORD = ROOT / "target" / "p193-rehearsal" / "record.json"
SCHEMA = "actinv-p193-record-1"
CROSSCHECK_SEED = 193
CROSSCHECK_PER_LIBRARY = 8


def index_path(library: Path) -> Path:
    return library.with_name(library.stem + "_index.json")


class Library(p192.Library):
    def __init__(self, path: Path) -> None:
        archive = np.load(path, allow_pickle=False)
        self.rows = np.asarray(archive["rows"], dtype=np.int64)
        self.sigma = np.asarray(archive["sig"], dtype=float)
        self.bounds = np.asarray(archive["bounds"], dtype=float)
        index = json.loads(index_path(path).read_text(encoding="utf-8"))
        self.target = {int(t["za"]): i for i, t in enumerate(index["targets"]) if int(t.get("liso", 0)) == 0}


def binary_one_group(binary: Path, library: Path, mapping: dict, bounds, flux, workdir: Path) -> list[dict]:
    spec = {"structure": p192.LIBRARY_STRUCTURE, "boundaries_eV": bounds.tolist(), "flux_per_group": flux.tolist(),
            "rebin": {"convention": "equal_lethargy"}}
    path = workdir / "spectrum.json"
    path.write_text(json.dumps(spec))
    z, a = divmod(mapping["target_za"], 1000)
    cmd = [str(binary), "inspect", "xs", str(library), "--target", f"{p192.SYMBOLS[z]}{a}", "--mt",
           str(mapping["mt"]), "--spectrum", str(path)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise RuntimeError(f"actinv inspect xs failed ({done.returncode}): {done.stderr[-400:]}")
    return json.loads(done.stdout)["reactions"]


def run(args: argparse.Namespace) -> int:
    rows = p192.read_table()
    libraries = {name: Library(path) for name, path in LIBRARIES.items()}
    bounds = libraries[DEFAULT].bounds
    for name, library in libraries.items():
        if not np.array_equal(library.bounds, bounds):
            raise ValueError(f"{name} does not share the default library's 709 group boundaries")
    spectra = {}
    for name in sorted({row["spectrum"] for row in rows}):
        source_bounds, flux, path = p192.read_spectrum(name)
        spectra[name] = {"bounds": source_bounds, "flux": flux, "file": path.name, "sha256": p192.sha256(path),
                         "weights": p192.rebin(source_bounds, flux, bounds, "equal_lethargy")}
    for row in rows:
        mapping = p192.parse_label(row["reaction"])
        row["mapping"] = mapping
        row["values"] = {}
        if mapping["status"] != "parsed":
            row["status"] = mapping["status"]
            continue
        for name, library in libraries.items():
            picked, how = library.select(mapping)
            entry = {"selection": how}
            if picked:
                value = library.collapse(picked, spectra[row["spectrum"]]["weights"])
                entry["library_rows"] = [[int(v) for v in library.rows[r][:4]] for r in picked]
                if math.isfinite(value) and value > 0.0:
                    entry["C_b"] = value
                else:
                    entry["selection"] = "nonpositive_calculated_value"
            row["values"][name] = entry
        row["status"] = "scored" if "C_b" in row["values"][DEFAULT] else row["values"][DEFAULT]["selection"]
    checks = []
    rng = random.Random(CROSSCHECK_SEED)
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        for name in LIBRARIES:
            if name == DEFAULT:
                continue
            candidates = [row["row"] for row in rows if "C_b" in row["values"].get(name, {})]
            for index in sorted(rng.sample(candidates, min(CROSSCHECK_PER_LIBRARY, len(candidates)))):
                row = rows[index]
                entry = row["values"][name]
                spectrum = spectra[row["spectrum"]]
                reactions = binary_one_group(Path(args.actinv_bin), LIBRARIES[name], row["mapping"],
                                             spectrum["bounds"], spectrum["flux"], Path(tmp))
                wanted = {tuple(r[1:4]) for r in entry["library_rows"]}
                values = [r["one_group_xs_b"] for r in reactions
                          if (r["mt"], r["product_za"], r["product_lfs"]) in wanted]
                binary_value = float(sum(values)) if len(values) == len(wanted) else None
                rel = (None if binary_value is None
                       else abs(binary_value - entry["C_b"]) / max(abs(entry["C_b"]), 1e-300))
                checks.append({"library": name, "row": index, "binary_one_group_b": binary_value,
                               "relative_difference": rel})
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    record = {
        "schema": SCHEMA, "rehearsal": bool(args.rehearsal),
        "inputs": {"table": {"path": str(p192.TABLE), "sha256": p192.sha256(p192.TABLE)},
                   "libraries": {name: {"path": str(path), "sha256": p192.sha256(path),
                                        "index_sha256": p192.sha256(index_path(path))}
                                 for name, path in LIBRARIES.items()},
                   "actinv_binary": {"path": str(args.actinv_bin), "sha256": p192.sha256(Path(args.actinv_bin))}},
        "environment": {"git_head": git("rev-parse", "HEAD"),
                        "git_dirty_paths": git("status", "--porcelain", "--untracked-files=no")},
        "spectra": {name: {"file": s["file"], "sha256": s["sha256"]} for name, s in spectra.items()},
        "crosscheck": {"seed": CROSSCHECK_SEED, "rows": checks},
        "rows": rows,
    }
    out = REHEARSAL_RECORD if args.rehearsal else RECORD
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    available = {name: sum("C_b" in row["values"].get(name, {}) for row in rows) for name in LIBRARIES}
    print(f"p193: wrote {out}: {len(rows)} rows, " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(
        row["status"] for row in rows).items())) + f"; values per library {available}; crosscheck {len(checks)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--actinv-bin", required=True)
    p_run.add_argument("--rehearsal", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
