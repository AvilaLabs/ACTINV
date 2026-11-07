#!/usr/bin/env python3
"""P194 harness: build the composite library that controls/check_p194.py's block rule chooses, and score it.

`run` reads P193's committed record, records for every (target, MT) block of UKAEA's table what each library holds,
applies the block rule, and writes TENDL-2025 with each chosen block replaced whole (its total row and every product
row) by the chosen library's block. It checks the composite against its sources, collapses P192's table with it,
cross-checks a seeded sample against `actinv inspect xs`, and scores three earlier data sets with TENDL-2025 and with
the composite through their own harnesses, imported unchanged: IRDFF-II spectrum-averaged cross sections
(controls/p17_irdff.py), the Rez activation rates (controls/p167_rez.py) and the JAEA-FNS decay heat
(controls/cb2_fns.py, as P172's ACTINV arm). It writes results/p194_record.json (or target/p194-rehearsal/record.json
with --rehearsal) and prints only counts.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p194 as c  # noqa: E402
import p192  # noqa: E402
import p193  # noqa: E402

HOME = Path.home()
COMPOSITE_DIR = HOME / "nuclear-data" / "p194-composite"
COMPOSITE_NAME = "tendl-2025-composite-p194-709g"
RECORD = ROOT / "results" / "p194_record.json"
REHEARSAL_DIR = ROOT / "target" / "p194-rehearsal"
SCHEMA = c.SCHEMA
CROSSCHECK_SEED = 194
DEFAULT = c.DEFAULT


def sha(path: Path) -> str:
    return p192.sha256(path)


def block_rows(library: p193.Library, za: int, mt: int) -> np.ndarray:
    target = library.target.get(za)
    if target is None:
        return np.zeros(0, dtype=np.int64)
    return np.flatnonzero((library.rows[:, 0] == target) & (library.rows[:, 1] == mt))


def block_facts(libraries: dict, p193_rows: list[dict]) -> dict:
    """For every block of the table: which labels it holds and, per library, its row count, whether it has a total
    row (zap -1) and whether it has a production row (zap = the product's ZA) for each label's product."""
    facts = {}
    for row in c.scored_rows(p193_rows):
        block = c.block_of(row)
        facts.setdefault(block, {"labels": {}})["labels"][row["reaction"]] = row["mapping"]["product_za"]
    for block, fact in facts.items():
        za, mt = (int(v) for v in block.split(":"))
        fact["libraries"] = {}
        for name, library in libraries.items():
            positions = block_rows(library, za, mt)
            zaps = set(library.rows[positions, 2].tolist())
            fact["libraries"][name] = {"rows": int(len(positions)), "total_row": -1 in zaps,
                                       "product_rows": {label: product in zaps
                                                        for label, product in sorted(fact["labels"].items())}}
    return facts


def build(libraries: dict, choice: dict, out_npz: Path, base_index_path: Path) -> dict:
    """TENDL-2025 with each chosen block replaced, in place, by the donor library's block (target column remapped);
    every other row is copied unchanged and in order."""
    base = libraries[DEFAULT]
    replace = {}
    for block, name in choice.items():
        if name == DEFAULT:
            continue
        za, mt = (int(v) for v in block.split(":"))
        donor = libraries[name]
        positions = block_rows(donor, za, mt)
        rows = donor.rows[positions].copy()
        rows[:, 0] = base.target[za]
        replace[(base.target[za], mt)] = (rows, donor.sigma[positions].copy(), name)
    out_rows, out_sig, emitted = [], [], set()
    start = 0
    keys = [tuple(k) for k in base.rows[:, :2].tolist()]
    for i, key in enumerate(keys + [None]):
        if i < len(keys) and key not in replace:
            continue
        if start < i:
            out_rows.append(base.rows[start:i])
            out_sig.append(base.sigma[start:i])
        if i < len(keys) and key not in emitted:
            rows, sig, _ = replace[key]
            out_rows.append(rows)
            out_sig.append(sig)
            emitted.add(key)
        start = i + 1
    if emitted != set(replace):
        raise RuntimeError(f"blocks missing from TENDL-2025: {sorted(set(replace) - emitted)}")
    rows = np.ascontiguousarray(np.concatenate(out_rows), dtype="<i8")
    sig = np.ascontiguousarray(np.concatenate(out_sig), dtype="<f8")
    bounds = np.ascontiguousarray(base.bounds, dtype="<f8")
    out_npz.parent.mkdir(parents=True, exist_ok=True)
    with out_npz.open("wb") as stream:
        np.savez(stream, rows=rows, sig=sig, bounds=bounds)
    index = json.loads(base_index_path.read_text(encoding="utf-8"))
    counts = np.bincount(rows[:, 0], minlength=len(index["targets"]))
    for (target, mt), (_, _, name) in sorted(replace.items()):
        index["targets"][target]["ledger"].append(f"P194 composite: MT{mt} block taken whole from {name}")
    for i, target in enumerate(index["targets"]):
        target["n_rows"] = int(counts[i])
    index["n_rows"] = int(len(rows))
    index["sha256_npz"] = sha(out_npz)
    p193.index_path(out_npz).write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(out_npz), "sha256": index["sha256_npz"], "index_sha256": sha(p193.index_path(out_npz)),
            "rows": int(len(rows)), "replaced_blocks": len(replace)}


def verify(libraries: dict, composite: p193.Library, choice: dict) -> dict:
    """Group the composite, TENDL-2025 and the donors by (target, MT) and require each composite group to be
    bit-identical, rows and cross sections in order, to the group of the library its block came from."""
    base = libraries[DEFAULT]
    donor_of = {}
    for block, name in choice.items():
        za, mt = (int(v) for v in block.split(":"))
        donor_of[(base.target[za], mt)] = (name, za)

    def groups(library):
        out = {}
        for i, key in enumerate(map(tuple, library.rows[:, :2].tolist())):
            out.setdefault(key, []).append(i)
        return out

    mine, theirs = groups(composite), groups(base)
    mismatched = []
    for key in sorted(set(mine) | set(theirs)):
        name, za = donor_of.get(key, (DEFAULT, None))
        if name == DEFAULT:
            source, positions = base, theirs.get(key, [])
            want_rows = base.rows[positions]
        else:
            source = libraries[name]
            positions = block_rows(source, za, key[1]).tolist()
            want_rows = source.rows[positions].copy()
            want_rows[:, 0] = key[0]
        got = mine.get(key, [])
        if not (np.array_equal(composite.rows[got], want_rows)
                and np.array_equal(composite.sigma[got], source.sigma[positions])):
            mismatched.append(list(key))
    return {"mismatched_blocks": len(mismatched), "mismatched": mismatched[:20],
            "bounds_equal": bool(np.array_equal(composite.bounds, base.bounds)),
            "replaced_blocks": sum(name != DEFAULT for name in choice.values()),
            "groups": len(mine)}


def table_values(composite: p193.Library, p193_rows: list[dict], weights: dict) -> list[dict]:
    out = []
    for row in p193_rows:
        if row.get("status") != "scored":
            continue
        picked, how = composite.select(row["mapping"])
        entry = {"row": row["row"], "block": c.block_of(row), "selection": how}
        if picked:
            entry["C_b"] = composite.collapse(picked, weights[row["spectrum"]])
            entry["library_rows"] = [[int(v) for v in composite.rows[r][:4]] for r in picked]
        out.append(entry)
    return out


def crosscheck(binary: Path, path: Path, table: list[dict], p193_rows: list[dict], spectra: dict, choice: dict,
               tmp: Path) -> list[dict]:
    by_row = {row["row"]: row for row in p193_rows}
    candidates = [entry["row"] for entry in table if choice.get(entry["block"], DEFAULT) != DEFAULT
                  and "C_b" in entry]
    rng = random.Random(CROSSCHECK_SEED)
    entries = {entry["row"]: entry for entry in table}
    checks = []
    for index in sorted(rng.sample(candidates, min(c.CROSSCHECK_ROWS, len(candidates)))):
        entry, row = entries[index], by_row[index]
        spectrum = spectra[row["spectrum"]]
        reactions = p193.binary_one_group(binary, path, row["mapping"], spectrum["bounds"], spectrum["flux"], tmp)
        wanted = {tuple(r[1:4]) for r in entry["library_rows"]}
        values = [r["one_group_xs_b"] for r in reactions if (r["mt"], r["product_za"], r["product_lfs"]) in wanted]
        binary_value = float(sum(values)) if len(values) == len(wanted) else None
        rel = None if binary_value is None else abs(binary_value - entry["C_b"]) / max(abs(entry["C_b"]), 1e-300)
        checks.append({"row": index, "block": entry["block"], "binary_one_group_b": binary_value,
                       "relative_difference": rel})
    return checks


# ---------------------------------------------------------------------------------------------- the earlier sets

def irdff_arm(library: Path) -> tuple[list[dict], dict]:
    import p17_irdff
    saved = (p17_irdff.PRODUCTION_LIBRARY, p17_irdff.PRODUCTION_INDEX, dict(p17_irdff.EXPECTED_HASHES))
    p17_irdff.PRODUCTION_LIBRARY, p17_irdff.PRODUCTION_INDEX = library, p193.index_path(library)
    p17_irdff.EXPECTED_HASHES.update(production_library=sha(library), production_index=sha(p193.index_path(library)))
    try:
        rows, diagnostics = p17_irdff.diagnostic_rows()
    finally:
        p17_irdff.PRODUCTION_LIBRARY, p17_irdff.PRODUCTION_INDEX = saved[:2]
        p17_irdff.EXPECTED_HASHES.clear()
        p17_irdff.EXPECTED_HASHES.update(saved[2])
    out = [{"row_id": f"irdff:t{row['table']}:r{row['table_row']:03d}", "label": row["label"],
            "measured_mb": row["measured_mb"], "C": row["production_tendl2025_mb"],
            "reason": row["production_reason"]} for row in rows]
    return out, diagnostics["input_identities"]


def rez_arm(library: Path, binary: Path) -> list[dict]:
    import p167_rez
    saved = p167_rez.LIBRARY, p167_rez.BIN
    p167_rez.LIBRARY, p167_rez.BIN = library, binary
    try:
        document = json.loads(p167_rez.TABLES.read_text())
        out = []
        with tempfile.TemporaryDirectory(prefix="actinv-p194-rez-", dir=os.environ.get("TMPDIR")) as temporary:
            work, cache = Path(temporary), {}
            for field, data in document["fields"].items():
                rate = p167_rez.actinv_production(work, field, data["spectrum"], "equal_energy", cache)
                for row in data["rates"]:
                    target, _, product = p167_rez.REACTIONS[row["reaction"]]
                    value, _ = rate(target, product)
                    out.append({"field": field, "reaction": row["reaction"], "status": row["status"],
                                "E": row["rate_per_s"], "C": value})
    finally:
        p167_rez.LIBRARY, p167_rez.BIN = saved
    return out


def fns_arms(libraries: dict[str, Path], module: Path, work_root: Path) -> dict:
    """P172's ACTINV arm, once per library, in this process: controls/cb2_fns.py with only the library, its pins,
    the work directory and the result path redirected (as controls/p172_openmc_fns.py does)."""
    os.environ["ACTINV_PYTHON_MODULE"] = str(module)
    import cb2_fns
    out = {}
    for name, library in libraries.items():
        work = work_root / f"fns-{name}"
        if work.exists():
            raise RuntimeError(f"{work} exists; P194 scores FNS with fresh work directories")
        cb2_fns.LIBRARY = library
        cb2_fns.WORK = work
        cb2_fns.RESULT = work_root / f"fns-{name}.json"
        cb2_fns.EXPECTED["library"] = sha(library)
        cb2_fns.EXPECTED["library_index"] = sha(p193.index_path(library))
        started = time.perf_counter()
        try:
            cb2_fns.main()
            exit_code = 0
        except SystemExit as stop:
            exit_code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
        result = json.loads(cb2_fns.RESULT.read_text())
        out[name] = {"result": result, "exit_code": exit_code, "wall_s": time.perf_counter() - started}
    return out


def cgroup():
    try:
        relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    except (OSError, IndexError):
        return {"path": None}
    base = Path("/sys/fs/cgroup") / relative.lstrip("/")
    record = {"path": relative}
    for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max"):
        try:
            record[name] = (base / name).read_text().strip()
        except OSError:
            record[name] = None
    return record


def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    p193_rows = json.loads(c.P193_RECORD.read_text())["rows"]
    libraries = {name: p193.Library(path) for name, path in p193.LIBRARIES.items()}
    for name, library in libraries.items():
        if not np.array_equal(library.bounds, libraries[DEFAULT].bounds):
            raise ValueError(f"{name} does not share TENDL-2025's group boundaries")
    facts = block_facts(libraries, p193_rows)
    choice = c.final_blocks(p193_rows, facts)
    out_dir = REHEARSAL_DIR if args.rehearsal else COMPOSITE_DIR
    composite_path = out_dir / f"{COMPOSITE_NAME}.npz"
    if composite_path.exists() and not args.rehearsal:
        raise RuntimeError(f"{composite_path} exists; the scored run builds it fresh")
    built = build(libraries, choice, composite_path, p193.index_path(p193.LIBRARIES[DEFAULT]))
    composite = p193.Library(composite_path)
    verification = verify(libraries, composite, choice)
    bounds = libraries[DEFAULT].bounds
    spectra = {}
    for name in sorted({row["spectrum"] for row in p193_rows}):
        source_bounds, flux, _ = p192.read_spectrum(name)
        spectra[name] = {"bounds": source_bounds, "flux": flux,
                         "weights": p192.rebin(source_bounds, flux, bounds, "equal_lethargy")}
    table = table_values(composite, p193_rows, {k: v["weights"] for k, v in spectra.items()})
    binaries = {"table": Path(args.actinv_bin), "rez": Path(args.rez_bin)}
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        checks = crosscheck(binaries["table"], composite_path, table, p193_rows, spectra, choice, Path(tmp))
    base_path = p193.LIBRARIES[DEFAULT]
    irdff_base, irdff_inputs = irdff_arm(base_path)
    irdff_comp, _ = irdff_arm(composite_path)
    irdff = [{"row_id": a["row_id"], "label": a["label"], "measured_mb": a["measured_mb"],
              "base": {"C": a["C"], "reason": a["reason"]}, "composite": {"C": b["C"], "reason": b["reason"]}}
             for a, b in zip(irdff_base, irdff_comp, strict=True)]
    rez_base, rez_comp = rez_arm(base_path, binaries["rez"]), rez_arm(composite_path, binaries["rez"])
    rez = [{**{k: a[k] for k in ("field", "reaction", "status", "E")}, "base": {"C": a["C"]},
            "composite": {"C": b["C"]}} for a, b in zip(rez_base, rez_comp, strict=True)]
    work_root = out_dir / "work" if args.rehearsal else ROOT / "target" / "p194-work"
    if args.rehearsal and work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    fns = fns_arms({"base": base_path, "composite": composite_path}, Path(args.module), work_root)
    pairs = []
    for a, b in zip(fns["base"]["result"]["records"], fns["composite"]["result"]["records"], strict=True):
        if (a["material"], a["experiment"]) != (b["material"], b["experiment"]) or len(a["pairs"]) != len(b["pairs"]):
            raise RuntimeError(f"FNS arms disagree on {a['material']}/{a['experiment']}")
        for pa, pb in zip(a["pairs"], b["pairs"]):
            if pa["time_s"] != pb["time_s"] or pa["measured_uW_g"] != pb["measured_uW_g"]:
                raise RuntimeError(f"FNS arms disagree on a point of {a['material']}/{a['experiment']}")
            pairs.append({"material": a["material"], "experiment": a["experiment"], "time_s": pa["time_s"],
                          "measured_uW_g": pa["measured_uW_g"], "sigma_uW_g": pa["sigma_uW_g"],
                          "base": pa["actinv_tendl2025_uW_g"], "composite": pb["actinv_tendl2025_uW_g"],
                          "fispact_tendl2017_uW_g": pa.get("fispact_tendl2017_uW_g")})
    fns_summary = {name: {"experiments": arm["result"]["experiment_count"],
                          "fresh_experiments": arm["result"]["fresh_experiments"],
                          "certificate_inputs_match": arm["result"]["checks"]["all_certificate_inputs_match"],
                          "cb2_pass": arm["result"]["pass"], "exit_code": arm["exit_code"], "wall_s": arm["wall_s"]}
                   for name, arm in fns.items()}
    fns_inputs = fns["base"]["result"]["input_identities"]
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    record = {
        "schema": SCHEMA, "rehearsal": bool(args.rehearsal),
        "inputs": {
            "p193_record": {"path": str(c.P193_RECORD.relative_to(ROOT)), "sha256": sha(c.P193_RECORD)},
            "libraries": {name: {"path": str(path), "sha256": sha(path), "index_sha256": sha(p193.index_path(path))}
                          for name, path in p193.LIBRARIES.items()},
            "binaries": {role: {"path": str(path), "sha256": sha(path)} for role, path in binaries.items()},
            "python_module": {"path": str(args.module), "sha256": sha(Path(args.module))},
            "irdff": {k: v for k, v in irdff_inputs.items() if k in c.P17_INPUT_SHA256},
            "fns": {k: fns_inputs.get(k) for k in c.FNS_INPUT_SHA256},
        },
        "environment": {"git_head": git("rev-parse", "HEAD"),
                        "git_dirty_paths": git("status", "--porcelain", "--untracked-files=no"),
                        "cgroup": cgroup(), "wall_s": None},
        "blocks": facts, "choice": choice,
        "composite": {**built, "verification": verification},
        "table": table,
        "crosscheck": {"seed": CROSSCHECK_SEED, "rows": checks},
        "arms": {"irdff": {"rows": irdff}, "rez": {"rows": rez},
                 "fns": {**fns_summary, "pairs": pairs}},
    }
    record["environment"]["wall_s"] = time.perf_counter() - started
    out = (REHEARSAL_DIR / "record.json") if args.rehearsal else RECORD
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"p194: wrote {out}: {len(facts)} blocks, {verification['replaced_blocks']} replaced, "
          f"{verification['mismatched_blocks']} mismatched; table {len(table)}; crosscheck {len(checks)}; "
          f"irdff {len(irdff)}, rez {len(rez)}, fns pairs {len(pairs)}; "
          f"{record['environment']['wall_s']:.0f} s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--actinv-bin", required=True, help="P192's binary, for the table cross-check")
    p_run.add_argument("--rez-bin", required=True, help="P167's binary, for the Rez rates")
    p_run.add_argument("--module", required=True, help="P172's Python module, for the FNS decay heat")
    p_run.add_argument("--rehearsal", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
