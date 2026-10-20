#!/usr/bin/env python3
"""P149 S6: states, non-zeros, solve time and peak memory against pruning mode and CRAM order.

Cases (TENDL-2017, the FNS benchmark library; spectra, irradiations and cooling from `cb3_fns.specification`):
  fe     FNS Fe 1996exp_5min;
  ss316  FNS SS316 1996exp_7hour;
  w      FNS W 1996exp_7hour;
  u_nat  natural uranium (U 100 wt%) in the Fe 1996exp_5min spectrum and schedule, mode `coupled`, with the
         ENDF/B-VIII.0 neutron fission-yield evaluations for U-234, U-235, U-238 and Pu-239 (ACTINV ships none).
For each case, `prune` in {rate, reach, none} and `cram_order` in {16, 48}: one warm-up and REPEATS timed
`actinv run` processes under GNU time, with `outputs` = inventory, activity, heat and ledger (the solve). Photon and
pathway outputs over an unpruned network cost far more than the solve, so each `prune` mode also gets one run at CRAM
order 16 with the default outputs, reported apart. Reported: kept and total states, the solve's core time (`ms`) and the process
wall time (median, minimum, maximum) and the largest peak RSS. Repeats are compared by the SHA-256 of the result
with its timing field `ms` removed (Amendment C); every row records the distinct hashes, which must be one. Non-zeros are counted from `prune_probe`'s capture
of the pruning inputs (the full decay, reaction, feed and removal triplets): the number of distinct off-diagonal
positions among the kept states plus one diagonal entry per kept state, i.e. the pattern of the burn matrix; `none`
keeps every state. The host, its CPU and the enclosing cgroup's cpu.max and memory.max are recorded; times hold for
that host only.

Usage: p149_s6_scaling.py [--out results/p149/s6_scaling.json] [--work target/p149-s6] [--only CASE ...]
                          [--repeats 10]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import cb3_fns as cb3  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
PRUNE_PROBE = Path(os.environ.get("ACTINV_PRUNE_PROBE", ROOT / "target/release/prune_probe"))
GNU_TIME = Path("/usr/bin/time")
NFY = Path(os.environ.get("ACTINV_NFY_DIR", Path.home() / "nuclear-data/endfb-viii.0-nfpy"))
YIELD_FILES = ("nfy-092_U_234.endf", "nfy-092_U_235.endf", "nfy-092_U_238.endf", "nfy-094_Pu_239.endf")
CASES = {"fe": ("Fe", "1996exp_5min"), "ss316": ("SS316", "1996exp_7hour"), "w": ("W", "1996exp_7hour"),
         "u_nat": ("Fe", "1996exp_5min")}
PRUNES = ("rate", "reach", "none")
ORDERS = (16, 48)
REPEATS = 10
TIMEOUT_S = 1800.0
OUTPUTS = ["inventory", "activity", "heat", "ledger"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def case_spec(name: str) -> dict:
    material, experiment = CASES[name]
    spec, _record = cb3.specification(material, experiment)
    spec["title"] = f"P149 S6 {name}"
    if name == "u_nat":
        spec["material"]["composition"] = {"U": 100.0}
        spec["options"]["mode"] = "coupled"
        spec["fission_yields"] = {"files": [{"path": str(NFY / f), "sha256": sha256(NFY / f)} for f in YIELD_FILES],
                                  "energy": "spectrum_average"}
    return spec


def timed_run(spec_path: Path, out_path: Path) -> dict:
    started = time.perf_counter()
    run = subprocess.run([str(GNU_TIME), "-f", "P149RSS %M", str(ACTINV), "run", str(spec_path), str(out_path)],
                         cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT_S)
    wall_s = time.perf_counter() - started
    if run.returncode != 0:
        raise RuntimeError(f"{spec_path}: actinv failed ({run.returncode}): {run.stderr[-3000:]}")
    rss = [line for line in run.stderr.splitlines() if line.startswith("P149RSS ")]
    result = json.loads(out_path.read_text())
    untimed = json.dumps({k: v for k, v in result.items() if k != "ms"}, sort_keys=True).encode()
    return {"wall_s": wall_s, "core_ms": float(result["ms"]), "peak_rss_kib": int(rss[-1].split()[1]),
            "states": (result["pruned_states"], result["total_states"]), "mode": result["mode"],
            "result_sha256_without_ms": hashlib.sha256(untimed).hexdigest()}


def pattern(spec: dict, prune: str, work: Path) -> dict:
    """Kept states and burn-matrix pattern non-zeros from the pruning capture (reach capture for `none`)."""
    probe_spec = json.loads(json.dumps(spec))
    probe_spec["options"]["prune"] = "reach" if prune == "none" else prune
    spec_path, out_path = work / f"probe_{prune}.json", work / f"probe_{prune}.out.json"
    spec_path.write_text(json.dumps(probe_spec, indent=1))
    run = subprocess.run([str(PRUNE_PROBE), str(spec_path), str(out_path)], cwd=ROOT, capture_output=True, text=True,
                         timeout=TIMEOUT_S)
    if run.returncode != 0:
        raise RuntimeError(f"prune_probe failed ({run.returncode}): {run.stderr[-3000:]}")
    captures = json.loads(out_path.read_text())["captures"]
    out_path.unlink()
    if len(captures) != 1:
        raise RuntimeError(f"{len(captures)} pruning captures; expected one")
    capture = captures[0]
    n = capture["n"]
    keep = set(range(n)) if prune == "none" else set(capture["keep"])
    positions = set()
    for triplets in [capture["decay"], *capture["spectra"], *capture["feed"], *capture["removal"]]:
        for i, j, value in triplets:
            if i != j and value != 0.0 and i in keep and j in keep:
                positions.add((i, j))
    return {"states_in_capture": n, "kept_states": len(keep), "off_diagonal": len(positions),
            "nonzeros": len(positions) + len(keep)}


def host() -> dict:
    cpu = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")), None)
    group = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[-1]
    limits = {}
    for key in ("cpu.max", "memory.max", "memory.swap.max", "pids.max"):
        path = Path("/sys/fs/cgroup") / group.lstrip("/") / key
        limits[key] = path.read_text().strip() if path.is_file() else None
    return {"node": platform.node(), "machine": platform.machine(), "kernel": platform.release(), "cpu": cpu,
            "logical_cpus": os.cpu_count(), "cgroup": group, "cgroup_limits": limits,
            "environment": {k: os.environ.get(k) for k in ("RAYON_NUM_THREADS", "OMP_NUM_THREADS")}}


def run_case(name: str, repeats: int, work: Path) -> dict:
    spec = case_spec(name)
    rows = []
    for prune in PRUNES:
        counts = pattern(spec, prune, work)
        for order in ORDERS:
            variant = json.loads(json.dumps(spec))
            variant["options"].update({"prune": prune, "cram_order": order, "outputs": OUTPUTS})
            spec_path, out_path = work / f"{prune}_{order}.json", work / f"{prune}_{order}.out.json"
            spec_path.write_text(json.dumps(variant, indent=1))
            timed_run(spec_path, out_path)  # warm-up: page cache
            runs = [timed_run(spec_path, out_path) for _ in range(repeats)]
            distinct = sorted({r["result_sha256_without_ms"] for r in runs})
            if len(distinct) != 1:
                raise RuntimeError(f"{name} {prune} {order}: repeats differ apart from `ms`")
            kept, total = runs[0]["states"]
            rows.append({
                "prune": prune, "cram_order": order, "mode": runs[0]["mode"], "kept_states": kept,
                "total_states": total, "pattern": counts, "pattern_matches_kept": counts["kept_states"] == kept,
                "core_ms": {"median": statistics.median(r["core_ms"] for r in runs),
                            "min": min(r["core_ms"] for r in runs), "max": max(r["core_ms"] for r in runs)},
                "wall_s": {"median": statistics.median(r["wall_s"] for r in runs),
                           "min": min(r["wall_s"] for r in runs), "max": max(r["wall_s"] for r in runs)},
                "peak_rss_mib": max(r["peak_rss_kib"] for r in runs) / 1024.0,
                "distinct_result_sha256_without_ms": distinct, "repeats": repeats,
            })
            out_path.unlink()
            print(f"{name} {prune} {order}: kept {kept} nnz {counts['nonzeros']} "
                  f"core {rows[-1]['core_ms']['median']:.1f} ms rss {rows[-1]['peak_rss_mib']:.0f} MiB",
                  file=sys.stderr, flush=True)
    default_outputs = {}
    for prune in PRUNES:
        variant = json.loads(json.dumps(spec))
        variant["options"].update({"prune": prune, "cram_order": 16})
        variant["options"].pop("outputs", None)
        spec_path, out_path = work / f"{prune}_default.json", work / f"{prune}_default.out.json"
        spec_path.write_text(json.dumps(variant, indent=1))
        run = timed_run(spec_path, out_path)
        out_path.unlink()
        default_outputs[prune] = {k: run[k] for k in ("wall_s", "core_ms", "peak_rss_kib")}
        print(f"{name} {prune} default outputs: wall {run['wall_s']:.1f} s rss {run['peak_rss_kib'] / 1024:.0f} MiB",
              file=sys.stderr, flush=True)
    return {"experiment": "/".join(CASES[name]), "default_outputs_cram16": default_outputs, "timed_outputs": OUTPUTS, "composition": spec["material"]["composition"],
            "options": {k: spec["options"][k] for k in ("mode", "bmin_atoms_per_g", "temperature_K")},
            "fission_yields": spec["fission_yields"]["files"], "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "results/p149/s6_scaling.json")
    ap.add_argument("--work", type=Path, default=ROOT / "target/p149-s6")
    ap.add_argument("--only", nargs="*", choices=list(CASES), default=list(CASES))
    ap.add_argument("--repeats", type=int, default=REPEATS)
    args = ap.parse_args()
    record = json.loads(args.out.read_text()) if args.out.exists() else {}
    record.update({"schema": "actinv-p149-s6-scaling-1", "protocol": "P149 S6", "host": host(),
                   "identities": {"actinv": sha256(ACTINV), "prune_probe": sha256(PRUNE_PROBE),
                                  "library": sha256(cb3.LIBRARY), "decay_primary": sha256(cb3.DECAY_PRIMARY),
                                  "decay_fallback": sha256(cb3.DECAY_FALLBACK)},
                   "nonzeros_definition": "distinct off-diagonal (i, j) with a nonzero decay, reaction, feed or "
                                          "removal entry between kept states, plus one diagonal entry per kept state",
                   "timing_note": "times and memory hold for the recorded host and cgroup only"})
    cases = record.setdefault("cases", {})
    for name in args.only:
        work = args.work.resolve() / name
        work.mkdir(parents=True, exist_ok=True)
        cases[name] = run_case(name, args.repeats, work)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
