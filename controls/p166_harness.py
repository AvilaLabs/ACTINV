#!/usr/bin/env python3
"""P166 harness (protocols/ACTINV-P166_PROTOCOL.md): ACTINV fission-product decay heat against the CoNDERC tables.

For every dataset in results/p166/conderc_fission_tables.csv (written by controls/p166_extract.py) this runs ACTINV
twice on one parent nuclide with the P9 model (one-group fission library, trace mode, ENDF/B-VIII.0 independent yields
at 0.0253 eV, ENDF/B-VIII.0 decay data): once with the registered irradiation and once with the flux off throughout.
The second run is the parent's own decay heat, subtracted channel by channel. Heat is normalized per fission and
compared with every extracted measured point. The record is results/p166_conderc.json; check_p166.py judges it.

Run inside the bounded scope from AGENTS.md. Each ACTINV call has a 600 s limit.

Usage: p166_harness.py [--record FILE] [--work DIR]
       p166_harness.py --p9-reproduction    (development check: re-score P9's Yarnell set, print aggregates)
"""
import argparse
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = Path.home() / "nuclear-data"
BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
TABLES = ROOT / "results" / "p166" / "conderc_fission_tables.csv"
RECORD = ROOT / "results" / "p166_conderc.json"
SCHEMA = "actinv-p166-conderc-1"

THERMAL_EV = 0.0253
JOULE_PER_MEV = 1.602176634e-13
SIGMA_F_B = 1.0  # one-group fission cross section; cancels in the per-fission normalization
FLUX = 1.0e26  # n/cm2/s; with SIGMA_F_B, 100 fissions per parent atom per second (trace mode: linear)
PARENT_ATOMS = 1.0e20  # atoms per gram; held constant by trace mode
CHANNELS = ("beta", "gamma", "total")
BOUNDS_EV = [1.0, 3.0]  # the one group's edges; fixed-energy yields make them immaterial

DECAY = DATA / "endfb-viii.0-decay" / "bulk" / "endf-b-viii-0_decay.dat"
NFPY = DATA / "endfb-viii.0-nfpy"
PARENTS = {
    # parent: (ZA, AWR from the ENDF/B-VIII.0 yield file header, yield file)
    "U233": (92233, 231.0377, NFPY / "nfy-092_U_233.endf"),
    "U235": (92235, 233.0248, NFPY / "nfy-092_U_235.endf"),
    "Pu239": (94239, 236.9986, NFPY / "nfy-094_Pu_239.endf"),
}
PINNED = {
    "decay": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "U233": "8e7e7e1d174471d457bb66c52d66e188beb6bc40eead968136fe4ab59aa588cb",
    "U235": "9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41",
    "Pu239": "a2713aa6d0283b879040a07fa29d3e99b2d660b619c6d11a2a71aa9d5c5a2605",
    "nfpy_archive": "92c5371fdb21eecf4989f48828671b904186abc6386b3d7510c8fcee2ee5ffcf",
}
NFPY_ARCHIVE = DATA / "endfb-viii.0-nfpy.zip"
P9_YARNELL = DATA / "conderc-fission" / "fission" / "expDataSystem" / "U235_thermal" / "Yarnell_20000.csv"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=1, sort_keys=True) + "\n")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def cgroup_record():
    """The bounded scope this harness runs in (read-only inspection)."""
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


def library(work, parent):
    """One-group library: the parent's MT=18 loss row and its ZAP=0 fission-product marker (the P9 layout)."""
    za, awr, _ = PARENTS[parent]
    path = work / f"{parent}-fission.npz"
    rows = np.asarray([[0, 18, -1, -1, 0], [0, 18, 0, 0, 0]], dtype=np.int64)
    sig = np.asarray([[SIGMA_F_B], [SIGMA_F_B]], dtype=np.float64)
    np.savez(path, rows=rows, sig=sig, bounds=np.asarray(BOUNDS_EV, dtype=np.float64))
    write_json(path.with_name(path.stem + "_index.json"), {
        "groups": 1, "n_rows": 2, "temperature_K": 293.6, "sha256_npz": sha256(path),
        "targets": [{"za": za, "liso": 0, "awr": awr, "ledger": []}],
    })
    return path


def specification(title, lib, parent, irradiation_s, cooling_times, flux_on):
    previous, cooling = 0.0, []
    for time in cooling_times:
        cooling.append({"dt": f"{time - previous:.17e} s", "flux": 0.0})
        previous = time
    yields = PARENTS[parent][2]
    return {
        "spec": "actinv-spec-1",
        "title": title,
        "library": {"path": str(lib), "sha256": sha256(lib)},
        "decay": {"primary": str(DECAY)},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {parent: PARENT_ATOMS}},
        "spectrum": {"structure": "custom", "boundaries_eV": BOUNDS_EV, "flux_per_group": [1.0], "total": FLUX,
                     "descending": False},
        "schedule": [{"dt": f"{irradiation_s:.17e} s", "flux": 1.0 if flux_on else 0.0}, *cooling],
        "options": {"mode": "trace", "prune": "none", "bmin_atoms_per_g": 0.0, "temperature_K": 293.6,
                    "outputs": ["inventory", "heat", "ledger", "certificate"]},
        "fission_yields": {"files": [{"path": str(yields), "sha256": sha256(yields)}], "energy": "fixed",
                           "fixed_energy_eV": THERMAL_EV},
    }


def run(work, name, spec):
    spec_path, result_path = work / f"{name}.json", work / f"{name}.result.json"
    write_json(spec_path, spec)
    subprocess.run([str(BIN), "run", str(spec_path), str(result_path)], check=True, timeout=600,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return json.loads(result_path.read_text())


def per_fission(work, dataset, parent, irradiation_s, normalization, cooling_times):
    """Per-fission heat (MeV/fission or MeV/s per fission) at each cooling time, plus model diagnostics."""
    lib = library(work, parent)
    on = run(work, f"{dataset}-on", specification(dataset, lib, parent, irradiation_s, cooling_times, True))
    off = run(work, f"{dataset}-off", specification(dataset, lib, parent, irradiation_s, cooling_times, False))
    za = PARENTS[parent][0]
    balance = on["ledger"]["fission_yield_balance"][f"{za}_0"]
    rate_per_parent = balance["fission_rate_per_parent_s"]
    independent = SIGMA_F_B * 1.0e-24 * FLUX
    fission_rate = PARENT_ATOMS * rate_per_parent
    divisor = fission_rate * irradiation_s if normalization == "integrated" else fission_rate
    steps_on, steps_off = on["steps"][1:], off["steps"][1:]
    if len(steps_on) != len(cooling_times) or len(steps_off) != len(cooling_times):
        raise RuntimeError(f"{dataset}: step count does not match the cooling times")
    values, background_fraction, time_error = [], 0.0, 0.0
    for time, step_on, step_off in zip(cooling_times, steps_on, steps_off):
        time_error = max(time_error, abs(step_on["t_s"] - irradiation_s - time) / time)
        heat = {}
        for channel in CHANNELS:
            signal = step_on["heat_W_per_g"][channel] - step_off["heat_W_per_g"][channel]
            if step_on["heat_W_per_g"][channel] > 0:
                background_fraction = max(background_fraction,
                                          step_off["heat_W_per_g"][channel] / step_on["heat_W_per_g"][channel])
            heat[channel] = signal / divisor / JOULE_PER_MEV
        values.append(heat)
    certificate = on["certificate"]
    return values, {
        "parent": parent,
        "irradiation_s": irradiation_s,
        "normalization": normalization,
        "cooling_times_s": cooling_times,
        "fission_rate_per_parent_s": rate_per_parent,
        "fission_rate_per_parent_s_independent": independent,
        "relative_rate_closure": abs(rate_per_parent - independent) / independent,
        "fission_yield_balance": balance,
        "fission_yields": certificate["fission_yields"],
        "certificate_inputs": certificate["inputs"],
        "mode": on["mode"],
        "max_background_fraction": background_fraction,
        "max_relative_time_error": time_error,
        "numerical_floor_atoms_per_g_max": max(step["numerical_floor_atoms_per_g"] for step in on["steps"]),
    }


def load_tables(path):
    with open(path, newline="") as stream:
        rows = list(csv.DictReader(stream))
    datasets = {}
    for row in rows:
        entry = datasets.setdefault(row["dataset"], {
            "parent": row["parent"], "irradiation_s": float(row["irradiation_s"]),
            "normalization": row["normalization"], "laboratory": row["laboratory"], "author": row["author"],
            "year": int(row["year"]), "table": int(row["table"]), "rows": []})
        for key in ("parent", "normalization"):
            if entry[key] != row[key]:
                raise RuntimeError(f"{row['dataset']}: inconsistent {key}")
        entry["rows"].append(row)
    return datasets


def score(entry, values, cooling_times):
    index = {time: position for position, time in enumerate(cooling_times)}
    scored = []
    for row in entry["rows"]:
        calculated = values[index[float(row["cooling_time_s"])]][row["channel"]]
        measured = float(row["measured"])
        sigma = measured * float(row["sigma_percent"]) / 100.0
        ce = calculated / measured
        scored.append({
            "cooling_time_s": float(row["cooling_time_s"]), "channel": row["channel"], "run": row["run"],
            "measured": measured, "sigma": sigma, "calculated": calculated, "C_over_E": ce,
            "log_C_over_E": math.log(ce) if ce > 0 else None, "residual_over_sigma": (calculated - measured) / sigma,
            "origen_calculated": float(row["origen_calculated"]) if row["origen_calculated"] else None,
            "origen_c_over_m": float(row["origen_c_over_m"]) if row["origen_c_over_m"] else None,
        })
    return scored


def p9_reproduction(work):
    """Re-score P9's Yarnell 20,000 s set through this harness; P9 recorded a geometric-mean C/E of 0.98451."""
    rows = []
    for line in P9_YARNELL.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split(",")
        rows.append((float(fields[0]), float(fields[5])))
    times = [time for time, _ in rows]
    values, info = per_fission(work, "p9-yarnell", "U235", 20000.0, "rate", times)
    logs = [math.log(value["total"] / measured) for value, (_, measured) in zip(values, rows)]
    print(json.dumps({"points": len(logs), "geometric_mean_C_over_E": math.exp(sum(logs) / len(logs)),
                      "max_abs_log": max(map(abs, logs)), "max_background_fraction": info["max_background_fraction"],
                      "relative_rate_closure": info["relative_rate_closure"]}, indent=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--p9-reproduction", action="store_true")
    args = parser.parse_args()
    work = args.work or Path(tempfile.mkdtemp(prefix="actinv-p166-", dir=os.environ.get("TMPDIR")))
    work.mkdir(parents=True, exist_ok=True)
    inputs = {"decay": DECAY, **{parent: PARENTS[parent][2] for parent in PARENTS}, "nfpy_archive": NFPY_ARCHIVE}
    hashes = {name: sha256(path) for name, path in inputs.items()}
    if hashes != PINNED:
        sys.exit(f"pinned input mismatch: {json.dumps({k: v for k, v in hashes.items() if PINNED[k] != v})}")
    if args.p9_reproduction:
        p9_reproduction(work)
        return
    datasets = load_tables(TABLES)
    results = {}
    for name, entry in sorted(datasets.items()):
        times = sorted({float(row["cooling_time_s"]) for row in entry["rows"]})
        values, info = per_fission(work, name, entry["parent"], entry["irradiation_s"], entry["normalization"],
                                   times)
        results[name] = {key: entry[key] for key in ("parent", "irradiation_s", "normalization", "laboratory",
                                                     "author", "year", "table")}
        results[name]["model"] = info
        results[name]["points"] = score(entry, values, times)
        print(f"{name}: {len(results[name]['points'])} points", flush=True)
    status = git("status", "--porcelain", "--untracked-files=no")
    record = {
        "schema": SCHEMA,
        "environment": {
            "binary": str(BIN), "binary_sha256": sha256(BIN), "git_head": git("rev-parse", "HEAD"),
            "crates_tree": git("rev-parse", "HEAD:crates"), "clean_tree": status == "", "host": platform.node(),
            "python": platform.python_version(), "cgroup": cgroup_record(),
            "threads": {key: os.environ.get(key) for key in ("RAYON_NUM_THREADS",)},
        },
        "inputs": {name: {"path": str(path), "sha256": hashes[name]} for name, path in inputs.items()},
        "tables": {"path": str(TABLES.relative_to(ROOT)), "sha256": sha256(TABLES)},
        "constants": {"thermal_eV": THERMAL_EV, "sigma_f_b": SIGMA_F_B, "flux": FLUX, "parent_atoms_per_g": PARENT_ATOMS,
                      "joule_per_MeV": JOULE_PER_MEV},
        "datasets": results,
    }
    write_json(args.record, record)
    print(f"wrote {args.record}")


if __name__ == "__main__":
    main()
