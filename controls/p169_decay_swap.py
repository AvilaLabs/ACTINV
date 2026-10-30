#!/usr/bin/env python3
"""P169 harness (protocols/ACTINV-P169_PROTOCOL.md): does different decay data close ACTINV's short-time fission-product
decay-heat bias? Candidates are chosen on the P166 CoNDERC tables (train stage) and the selected one is scored once on
the P9 Dickens and Yarnell U-235 measurements (test stage).

The model is P166's, unchanged (controls/p166_harness.py is imported, not copied): one-group library, trace mode,
ENDF/B-VIII.0 independent yields at 0.0253 eV, constant fission rate, a second flux-off run for the parent's own heat.
Only the `decay` block of the specification differs by candidate:

  A  ENDF/B-VIII.0 decay as the only source (the P166 and P9 configuration)
  B  JEFF-3.3 decay as primary, ENDF/B-VIII.0 as fallback for nuclides JEFF-3.3 lacks (spec decay.fallback)
  D  ENDF/B-VIII.0 decay with the mean beta, gamma and heavy-particle energies of ten short-lived nuclides replaced
     by JEFF-3.3's (spec decay.overrides, an actinv-decay-overrides-1 document written here from the two pinned files)

Candidate C (ENDF/B-VIII.1 decay) is not run: the local VIII.1 package is the OpenMC HDF5 library, which has no decay
sublibrary, and the decay directory prepared for it is empty.

Usage: p169_decay_swap.py train [--record FILE] [--work DIR] [--only DATASET ...]
       p169_decay_swap.py test  [--train-record FILE] [--record FILE] [--work DIR]
       p169_decay_swap.py overrides OUT.json     (write the candidate-D override document and print its SHA-256)
Run inside the bounded scope from AGENTS.md. `--only` is for smoke runs; use a scratch --record with it.
"""
import argparse
import contextlib
import json
import math
import os
import platform
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p166_harness as base  # noqa: E402

TRAIN_RECORD = ROOT / "results" / "p169_train.json"
TEST_RECORD = ROOT / "results" / "p169_test.json"
P166_RECORD = ROOT / "results" / "p166_conderc.json"
SCHEMA_TRAIN = "actinv-p169-train-1"
SCHEMA_TEST = "actinv-p169-test-1"

JEFF_DECAY = base.DATA / "jeff-3.3-decay" / "bulk" / "jeff-3-3_decay.dat"
DICKENS = base.P9_YARNELL.with_name("Dickens_pulse.csv")
YARNELL = base.P9_YARNELL
PINNED = {
    "jeff_decay": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    "dickens": "2a0442af2c232a86b07c198680461f5acfd3530bcf78bb3d23967f34a018f47b",
    "yarnell": "3d35f4bb7bcf167a5ec2dae8371093b65bea9d6a687156a0be552be23a3aad0c",
    "p166_record": "2f080d9e84d47ee1e7cedc9b97ee064026412252da23ce0ccf00c0b5f60cb6bf",
    "overrides_D": "d5458572e9dec58a90e3576d46ffc7c2f4c6b33521e0bc50cd25b7a8bbd827a1",
}

# Candidate D's frozen nuclide list: (name, ZA, LISO), ground states of the short-lived products whose mean
# beta/gamma energies differ most between ENDF/B-VIII.0 and JEFF-3.3.
OVERRIDE_NUCLIDES = (
    ("Rb92", 37092, 0), ("Rb94", 37094, 0), ("Rb95", 37095, 0), ("Br87", 35087, 0), ("Br88", 35088, 0),
    ("Br89", 35089, 0), ("Sb134", 51134, 0), ("Cs142", 55142, 0), ("I138", 53138, 0), ("Y95", 39095, 0),
)
CANDIDATES = ("A", "B", "D")
SHORT_S = 1000.0  # train/test split of the cooling-time axis: short is t < 1000 s
LONG_MARGIN = 0.005  # a candidate may worsen the median |ln C/E| of the t >= 1000 s train points by at most this
MIN_GAIN = 0.005  # and must lower the t < 1000 s median by at least this to displace the baseline
EDGE_EPS = 1e-12  # float slack so a candidate exactly on either threshold is treated as meeting it
# P9's recorded geometric-mean total C/E, which the baseline must reproduce through this harness
P9_GEOMEAN_TOTAL = {"Dickens": 1.0069797096711348, "Yarnell": 0.9845114169374127}
P9_REPRODUCTION_TOLERANCE = 1e-4
TEST_DATASETS = {
    # name: (path, irradiation_s, normalization, time-weighted pulse, channels)
    "P9-Dickens-pulse": (DICKENS, 1.0e-6, "integrated", True, ("beta", "gamma", "total")),
    "P9-Yarnell-20000s": (YARNELL, 20000.0, "rate", False, ("total",)),
}
TEST_COUNTS = {"P9-Dickens-pulse": {"beta": 32, "gamma": 32, "total": 32}, "P9-Yarnell-20000s": {"total": 79}}


def num(field):
    """One ENDF float field ('1.234567+3', '-2.5-1') or an empty field."""
    field = field.strip()
    if not field:
        return 0.0
    for index in range(len(field) - 1, 0, -1):
        if field[index] in "+-" and field[index - 1] not in "eE+-":
            return float(field[:index] + "e" + field[index:])
    return float(field)


def mean_energies(path, wanted):
    """{(ZA, LISO): (half_life_s, [E_light, dE, E_em, dE, E_heavy, dE])} from the MF8/MT457 sections named in `wanted`."""
    found, buffer = {}, []
    with open(path) as stream:
        for line in stream:
            if len(line) < 75:
                continue
            try:
                mf, mt = int(line[70:72]), int(line[72:75])
            except ValueError:
                continue
            if mf == 8 and mt == 457:
                buffer.append(line)
                continue
            if buffer:
                key = (int(num(buffer[0][0:11])), int(buffer[0][33:44]))
                if key in wanted:
                    found[key] = (num(buffer[1][0:11]), [num(buffer[2][i:i + 11]) for i in range(0, 66, 11)])
                buffer = []
    return found


def override_document():
    """The candidate-D document: JEFF-3.3 mean energies (eV) for the frozen nuclides, deterministic."""
    wanted = {(za, liso) for _, za, liso in OVERRIDE_NUCLIDES}
    jeff = mean_energies(JEFF_DECAY, wanted)
    endf = mean_energies(base.DECAY, wanted)
    missing = [name for name, za, liso in OVERRIDE_NUCLIDES if (za, liso) not in jeff or (za, liso) not in endf]
    if missing:
        raise RuntimeError(f"candidate D nuclides missing from a decay library: {missing}")
    entries = []
    for name, za, liso in OVERRIDE_NUCLIDES:
        energies = jeff[(za, liso)][1]
        entries.append({
            "nuclide": name,
            "e_light_eV": energies[0], "e_em_eV": energies[2], "e_heavy_eV": energies[4],
            "source": "JEFF-3.3 radioactive decay sublibrary (jeff-3-3_decay.dat, MF8/MT457 mean decay energies)",
            "reason": "P169 candidate D: mean beta, gamma and heavy-particle energies replaced from JEFF-3.3 "
                      "(ENDF/B-VIII.0 values: light %.1f, em %.1f, heavy %.1f eV)" % tuple(
                          endf[(za, liso)][1][i] for i in (0, 2, 4)),
        })
    return {"schema": "actinv-decay-overrides-1", "overrides": entries}


def write_overrides(path):
    base.write_json(path, override_document())
    return base.sha256(path)


def decay_block(candidate, overrides_path):
    if candidate == "A":
        return {"primary": str(base.DECAY)}
    if candidate == "B":
        return {"primary": str(JEFF_DECAY), "fallback": str(base.DECAY)}
    if candidate == "D":
        return {"primary": str(base.DECAY), "overrides": str(overrides_path)}
    raise ValueError(candidate)


@contextlib.contextmanager
def candidate_decay(candidate, overrides_path, sink):
    """Run p166_harness.per_fission with this candidate's decay block; `sink` collects the decay ledger entries."""
    specification, run = base.specification, base.run

    def patched_specification(*args, **kwargs):
        spec = specification(*args, **kwargs)
        spec["decay"] = decay_block(candidate, overrides_path)
        return spec

    def recording_run(work, name, spec):
        result = run(work, name, spec)
        if name.endswith("-on"):
            ledger = result["ledger"]
            sink.update({
                "decay_nuclides_from_fallback": ledger.get("decay_nuclides_from_fallback"),
                "decay_overrides_applied": ledger.get("decay_overrides_applied"),
                "max_leakage_fraction": max((step["leakage_atoms_per_g"] / step["total_atoms_per_g"]
                                             for step in result["steps"] if step["total_atoms_per_g"] > 0),
                                            default=0.0),
            })
        return result

    base.specification, base.run = patched_specification, recording_run
    try:
        yield
    finally:
        base.specification, base.run = specification, run


def run_candidate(work, candidate, overrides_path, name, parent, irradiation_s, normalization, times):
    decay_ledger = {}
    with candidate_decay(candidate, overrides_path, decay_ledger):
        values, info = base.per_fission(work / candidate, name, parent, irradiation_s, normalization, times)
    info["decay_ledger"] = decay_ledger
    return values, info


def read_measurements(path, pulse_weighted):
    """[(t, {channel: (value, sigma)})] in the P9 units (MeV/s per fission); a pulse file holds t x power."""
    rows = []
    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split(",")
        scale = 1.0 / float(fields[0]) if pulse_weighted else 1.0
        channels = {}
        for channel, column in (("beta", 1), ("gamma", 3), ("total", 5)):
            if fields[column].strip() != "-":
                channels[channel] = (float(fields[column]) * scale, float(fields[column + 1]) * scale)
        rows.append((float(fields[0]), channels))
    return rows


def score_test(rows, values, channels):
    points = []
    for (time, measured_channels), heat in zip(rows, values):
        for channel in channels:
            if channel not in measured_channels:
                continue
            measured, sigma = measured_channels[channel]
            calculated = heat[channel]
            ce = calculated / measured
            points.append({
                "cooling_time_s": time, "channel": channel, "run": "", "measured": measured, "sigma": sigma,
                "calculated": calculated, "C_over_E": ce, "log_C_over_E": math.log(ce) if ce > 0 else None,
                "residual_over_sigma": (calculated - measured) / sigma,
            })
    return points


def median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else 0.5 * (ordered[middle - 1] + ordered[middle])


def window_medians(candidate_record):
    """Median |ln C/E| of the primary (every total-heat) train points, below and at or above SHORT_S."""
    short, long = [], []
    for dataset in candidate_record["datasets"].values():
        for point in dataset["points"]:
            if point["channel"] != "total":
                continue
            (short if point["cooling_time_s"] < SHORT_S else long).append(abs(math.log(point["C_over_E"])))
    return {"short_median_abs_log": median(short), "short_points": len(short),
            "long_median_abs_log": median(long), "long_points": len(long)}


def select(train):
    """The registered rule: the candidate with the lowest short-time median |ln C/E| among those that lower A's by at
    least MIN_GAIN without worsening A's long-time median by more than LONG_MARGIN; otherwise A."""
    windows = {name: window_medians(train["candidates"][name]) for name in CANDIDATES}
    baseline = windows["A"]
    eligible = [name for name in CANDIDATES if name != "A"
                and windows[name]["long_median_abs_log"] <= baseline["long_median_abs_log"] + LONG_MARGIN + EDGE_EPS
                and windows[name]["short_median_abs_log"] <= baseline["short_median_abs_log"] - MIN_GAIN + EDGE_EPS]
    chosen = min(eligible, key=lambda name: (windows[name]["short_median_abs_log"], name)) if eligible else "A"
    return {"windows": windows, "eligible": eligible, "selected": chosen,
            "rule": {"short_s": SHORT_S, "long_margin": LONG_MARGIN, "min_gain": MIN_GAIN}}


def environment():
    status = base.git("status", "--porcelain", "--untracked-files=no")
    return {
        "binary": str(base.BIN), "binary_sha256": base.sha256(base.BIN), "git_head": base.git("rev-parse", "HEAD"),
        "crates_tree": base.git("rev-parse", "HEAD:crates"), "clean_tree": status == "", "host": platform.node(),
        "python": platform.python_version(), "cgroup": base.cgroup_record(),
        "threads": {key: os.environ.get(key) for key in ("RAYON_NUM_THREADS",)},
    }


def inputs(overrides_path, need_p166_record):
    paths = {"decay": base.DECAY, "jeff_decay": JEFF_DECAY, "dickens": DICKENS, "yarnell": YARNELL,
             "overrides_D": overrides_path, **{parent: base.PARENTS[parent][2] for parent in base.PARENTS},
             "nfpy_archive": base.NFPY_ARCHIVE}
    if need_p166_record:
        paths["p166_record"] = P166_RECORD
    hashes = {name: base.sha256(path) for name, path in paths.items()}
    expected = {**base.PINNED, **PINNED}
    wrong = {name: digest for name, digest in hashes.items() if expected[name] != digest}
    if wrong:
        sys.exit(f"pinned input mismatch: {json.dumps(wrong)}")
    return {name: {"path": str(path), "sha256": hashes[name]} for name, path in paths.items()}


def train_stage(args, work, overrides_path):
    tables = base.TABLES
    datasets = base.load_tables(tables)
    names = sorted(datasets) if not args.only else sorted(args.only)
    record = {"schema": SCHEMA_TRAIN, "environment": environment(), "inputs": inputs(overrides_path, True),
              "tables": {"path": str(tables.relative_to(ROOT)), "sha256": base.sha256(tables)},
              "smoke": bool(args.only), "candidates": {}}
    for candidate in CANDIDATES:
        results = {}
        for name in names:
            entry = datasets[name]
            times = sorted({float(row["cooling_time_s"]) for row in entry["rows"]})
            values, info = run_candidate(work, candidate, overrides_path, name, entry["parent"],
                                         entry["irradiation_s"], entry["normalization"], times)
            results[name] = {key: entry[key] for key in ("parent", "irradiation_s", "normalization", "laboratory",
                                                         "author", "year", "table")}
            results[name]["model"] = info
            results[name]["points"] = base.score(entry, values, times)
            print(f"{candidate} {name}: {len(results[name]['points'])} points", flush=True)
        record["candidates"][candidate] = {"decay": decay_block(candidate, overrides_path), "datasets": results}
    if not args.only:
        record["selection"] = select(record)
        print(f"selected: {record['selection']['selected']}")
    base.write_json(args.record, record)
    print(f"wrote {args.record}")


def test_stage(args, work, overrides_path):
    if args.record.exists():
        sys.exit(f"{args.record} exists: the held-out sets are scored once")
    train = json.loads(args.train_record.read_text())
    if train.get("schema") != SCHEMA_TRAIN or train.get("smoke") or "selection" not in train:
        sys.exit("the train record is not a complete registered train record")
    selected = select(train)["selected"]
    if selected != train["selection"]["selected"]:
        sys.exit("the train record's recorded selection does not follow from its points")
    candidates = ["A"] if selected == "A" else ["A", selected]
    record = {"schema": SCHEMA_TEST, "environment": environment(), "inputs": inputs(overrides_path, True),
              "train_record": {"path": str(args.train_record.relative_to(ROOT)),
                               "sha256": base.sha256(args.train_record)},
              "selected": selected, "candidates": {}}
    for candidate in candidates:
        results = {}
        for name, (path, irradiation_s, normalization, weighted, channels) in TEST_DATASETS.items():
            rows = read_measurements(path, weighted)
            times = [time for time, _ in rows]
            values, info = run_candidate(work, candidate, overrides_path, name, "U235", irradiation_s, normalization,
                                         times)
            results[name] = {"parent": "U235", "irradiation_s": irradiation_s, "normalization": normalization,
                             "laboratory": name.split("-")[1], "model": info,
                             "points": score_test(rows, values, channels)}
            print(f"{candidate} {name}: {len(results[name]['points'])} points", flush=True)
        record["candidates"][candidate] = {"decay": decay_block(candidate, overrides_path), "datasets": results}
    base.write_json(args.record, record)
    print(f"wrote {args.record}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("train", "test", "overrides"))
    parser.add_argument("out", nargs="?", type=Path)
    parser.add_argument("--record", type=Path)
    parser.add_argument("--train-record", type=Path, default=TRAIN_RECORD)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    if args.stage == "overrides":
        if args.out is None:
            sys.exit("overrides needs an output path")
        print(write_overrides(args.out))
        return
    args.record = args.record or (TRAIN_RECORD if args.stage == "train" else TEST_RECORD)
    work = args.work or Path(tempfile.mkdtemp(prefix="actinv-p169-", dir=os.environ.get("TMPDIR")))
    for candidate in CANDIDATES:
        (work / candidate).mkdir(parents=True, exist_ok=True)
    overrides_path = work / "overrides_D.json"
    write_overrides(overrides_path)
    (train_stage if args.stage == "train" else test_stage)(args, work, overrides_path)


if __name__ == "__main__":
    main()
