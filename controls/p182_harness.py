#!/usr/bin/env python3
"""P182 harness (protocols/ACTINV-P182_PROTOCOL.md): the baseline build (d054c16) and the candidate build (ead00ec) of
`actinv` on P172's 132 JAEA FNS problems, each on two libraries (`actinv run`), and on P181's mixed-material mesh at 512
and 4,096 elements on both libraries (`actinv mesh`): 268 problems, each run once by each build, one after the other,
the order alternating (baseline first on even problems). Each process is timed from start to exit (time.monotonic)
with its CPU time from wait4. The FNS problems are cb2_fns.specification's, as P180 writes them, with only the library
changed.

Modes (each its own process; `run` inside the bounded scope AGENTS.md prescribes):
  prepare   writes the FNS problems under <work>/inputs (with p180_speed's experiment list and cb2_fns)
  run       one untimed warm-up of each build on the first FNS problem, then the timed pass; writes <work>/timings.json
  assemble  compares every pair of outputs (check_p182's functions) and writes results/p182_record.json;
            --delete-outputs removes the outputs afterwards

  systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- \\
    env RAYON_NUM_THREADS=2 TMPDIR=$PWD/target/p182-tmp python3 controls/p182_harness.py run

The harness refuses to start if a pin differs, the tracked tree is dirty, or (run) the cgroup is not within 6 GiB, no
swap, 128 tasks and 200% CPU.
"""
import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p181_harness as h181  # noqa: E402

MAIN = Path("/home/connoravila/Documents/actinv")  # the main checkout, which holds the builds, inputs and data release
SCHEMA = "actinv-p182-record-1"
WORK = MAIN / "target" / "p182-work"
INPUTS = MAIN / "target" / "p182-inputs"
RECORD = ROOT / "results" / "p182_record.json"
TIMINGS = WORK / "timings.json"

THREADS = 2
CASES = 132
LIBRARIES = ("endf", "tendl")
BUILD_ORDER = ("baseline", "candidate")
# build -> (binary, SHA-256, commit)
BUILDS = {
    "baseline": (MAIN / "target/p182-bin/baseline/actinv",
                 "2ca4bd75df5119b20bc0ca8224d4fecc7c3922d78a8f8dd7434111875a91dc28", "d054c16"),
    "candidate": (MAIN / "target/p182-bin/candidate/actinv",
                  "00508aea92f62bea4684beea12652cad59f5c81aba1e34456da86f89d03633c6", "ead00ec"),
}
# libraries as P180 pins them
REPAIRED_NPZ = Path.home() / "nuclear-data/endfb-viii.1-p178/endfb81_all_709g.npz"
REPAIRED_INDEX = Path.home() / "nuclear-data/endfb-viii.1-p178/endfb81_all_709g_index.json"
TENDL_NPZ = MAIN / "actinv-data/v1.2.0/activation/tendl-2025-neutron-709g.npz"
TENDL_INDEX = MAIN / "actinv-data/v1.2.0/activation/tendl-2025-neutron-709g_index.json"
LIBRARY_FILES = {"endf": REPAIRED_NPZ, "tendl": TENDL_NPZ}
LIBRARY_SHA = {"endf": "a964a44ef106b692d170ba6085859ca2827ff967b3e9a6ddc60ca8fa9ac92051",
               "tendl": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"}
# name -> (path, SHA-256)
PINS = {
    "baseline_binary": (BUILDS["baseline"][0], BUILDS["baseline"][1]),
    "candidate_binary": (BUILDS["candidate"][0], BUILDS["candidate"][1]),
    "repaired_library": (REPAIRED_NPZ, LIBRARY_SHA["endf"]),
    "repaired_index": (REPAIRED_INDEX, "b5a4b668324a92bd96e8e90c5feb521a79ca8838e42a517ac080652074b7f41a"),
    "tendl_library": (TENDL_NPZ, LIBRARY_SHA["tendl"]),
    "tendl_index": (TENDL_INDEX, "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb"),
    "flux_d8": (INPUTS / "d8/run/activation/flux.ndjson",
                "d44a5ca1be4ce4d6c3dfbf21a29df942838f113568b4aa19c20de2cf1b18c4d3"),
    "flux_d16": (INPUTS / "d16/run/activation/flux.ndjson",
                 "709dd7db7799966dc012532f59bf43b8190deeaea5fea328179502fe1edbd52b"),
    "mesh_d8_tendl": (INPUTS / "mesh_d8_tendl.json", "744e11cb5f836d057ce37290cca4b43ef60e12d39616150e4c1373fdbc5d7333"),
    "mesh_d8_endf": (INPUTS / "mesh_d8_endf.json", "739a84bc0ab66c4259bae5cc3304c9252aeb140be343cbe0cd296b5abdc6d8bb"),
    "mesh_d16_tendl": (INPUTS / "mesh_d16_tendl.json",
                       "55a6c565313ab926dc06377a861ef95707bb03ccdb0541eb30724cbcc3135ab5"),
    "mesh_d16_endf": (INPUTS / "mesh_d16_endf.json", "0c9faf23da79fe405caa1e3b149b62c7c8a00e96416d4d5af2c642271969f1d3"),
}
MESHES = {
    "mesh_d8_tendl": {"elements": 512, "regions": 776, "library": "tendl"},
    "mesh_d8_endf": {"elements": 512, "regions": 776, "library": "endf"},
    "mesh_d16_tendl": {"elements": 4096, "regions": 5120, "library": "tendl"},
    "mesh_d16_endf": {"elements": 4096, "regions": 5120, "library": "endf"},
}
PROBLEMS = CASES * len(LIBRARIES) + len(MESHES)  # 132 cases x 2 libraries + 4 meshes = 268
LIMIT_S = {"run": 600, "mesh": 4 * 3600}
POLL_S = 0.002


def first_build(k):
    """Baseline runs first on even problems, the candidate on odd ones."""
    return BUILD_ORDER[k % 2]


def build_order(k):
    first = first_build(k)
    return (first, BUILD_ORDER[1 - BUILD_ORDER.index(first)])


def plan_problems(cases):
    """The registered problems in order: every FNS (case, library) sorted, then the four meshes."""
    problems = [{"id": f"{case}.{lib}", "kind": "run", "case": case, "library": lib,
                 "spec": str(WORK / "inputs" / f"{case}.{lib}.json")} for case, lib in
                sorted((c, lib) for c in cases for lib in LIBRARIES)]
    problems += [{"id": name, "kind": "mesh", "library": m["library"], "spec": str(PINS[name][0])}
                 for name, m in MESHES.items()]
    return problems


def output_path(build, problem):
    return WORK / "out" / build / (problem["id"] + (".json" if problem["kind"] == "run" else ".ndjson"))


def command(build, problem, out):
    return [BUILDS[build][0], problem["kind"], problem["spec"], out]


# ------------------------------------------------------------------------------------------------------ guards

def dirty_paths():
    return h181.run_text(["git", "status", "--porcelain", "--untracked-files=no"])


def require_pins():
    bad = [name for name, (path, digest) in PINS.items() if not Path(path).is_file()
           or h181.sha256_file(path) != digest]
    if bad:
        sys.exit(f"p182_harness: refusing to run: not the pinned file: {', '.join(bad)}")
    if dirty_paths():
        sys.exit("p182_harness: refusing to run: the tracked tree is not clean")


def require_bounded():
    cg = h181.cgroup_record()
    ok, why = h181.cgroup_enforced(cg, 6)
    if not ok:
        sys.exit(f"p182_harness: refusing to run: {why} ({cg}). Launch inside the bounded systemd-run scope.")
    return cg


# ------------------------------------------------------------------------------------------------------ prepare

def prepare():
    """The FNS problems of P180's prepare (cb2_fns.specification) for both libraries; no OpenMC job."""
    if os.environ.get("P180_REHEARSAL") == "1":
        sys.exit("p182_harness: P180_REHEARSAL is set")
    require_pins()
    import p180_speed as p180  # noqa: E402
    os.environ["ACTINV_LIBRARY"] = str(TENDL_NPZ)
    import cb2_fns  # noqa: E402  (loads the Python module named by ACTINV_PYTHON_MODULE at import; only its spec is used)
    inputs = WORK / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    listing = []
    for material, experiment in p180.experiments():
        name = p180.case_name(material, experiment)
        spec, _ = cb2_fns.specification(material, experiment)
        for lib in LIBRARIES:
            problem = json.loads(json.dumps(spec))
            problem["library"] = {"path": str(LIBRARY_FILES[lib]), "sha256": LIBRARY_SHA[lib]}
            (inputs / f"{name}.{lib}.json").write_text(json.dumps(problem, indent=1, sort_keys=True))
        listing.append({"case": name, "material": material, "experiment": experiment})
    if len(listing) != CASES:
        sys.exit(f"p182_harness: expected {CASES} cases, found {len(listing)}")
    h181.write_atomic(inputs / "cases.json", listing)


def load_cases():
    return sorted(c["case"] for c in json.loads((WORK / "inputs" / "cases.json").read_text()))


# ------------------------------------------------------------------------------------------------------ run

def kill_group(proc):
    try:
        os.killpg(proc.pid, 9)
    except OSError:
        proc.kill()


def timed(cmd, env, log, limit_s):
    """Start `cmd`, reap it with wait4 (bounded by `limit_s`; on expiry its process group is killed and reaped), and
    return its wall time, CPU time and exit code."""
    with open(log, "w") as stream:
        started = time.monotonic()
        proc = subprocess.Popen([str(c) for c in cmd], env=env, stdout=stream, stderr=subprocess.STDOUT,
                                start_new_session=True)
        deadline, timed_out = started + limit_s, False
        try:
            while True:
                pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    break
                if time.monotonic() > deadline:
                    timed_out = True
                    kill_group(proc)
                    pid, status, usage = os.wait4(proc.pid, 0)
                    break
                time.sleep(POLL_S)
        except BaseException:  # interrupted: leave no child behind
            kill_group(proc)
            os.wait4(proc.pid, 0)
            raise
        wall = time.monotonic() - started
    proc.returncode = os.waitstatus_to_exitcode(status)  # reaped here; Popen must not wait again
    return {"wall_s": wall, "cpu_s": usage.ru_utime + usage.ru_stime, "exit_code": proc.returncode,
            "timed_out": timed_out, "max_rss_kib": usage.ru_maxrss}


def run_one(build, problem, env, position=None, out=None):
    out = out or output_path(build, problem)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = timed(command(build, problem, out), env, str(out) + ".log", LIMIT_S[problem["kind"]])
    return {"problem": problem["id"], "build": build, **({} if position is None else {"position": position}),
            **result, "output": str(out),
            "output_sha256": h181.sha256_file(out) if out.is_file() else None}


def run():
    cg = require_bounded()
    require_pins()
    problems = plan_problems(load_cases())
    if len(problems) != PROBLEMS:
        sys.exit(f"p182_harness: {len(problems)} problems planned, expected {PROBLEMS}")
    own = cg["path"]
    shutil.rmtree(WORK / "out", ignore_errors=True)
    env = dict(os.environ, ACTINV_CACHE_DIR=str(WORK / "actinv-cache"), RAYON_NUM_THREADS=str(THREADS))
    versions = {b: h181.run_text([BUILDS[b][0], "--version"]) for b in BUILD_ORDER}
    document = {"schema": SCHEMA + "-timings", "versions": versions, "cgroup": cg,
                "git_head": h181.run_text(["git", "rev-parse", "HEAD"]), "git_dirty_paths": dirty_paths(),
                "harness_sha256": h181.sha256_file(Path(__file__)), "cpu_model": h181.lscpu_model(),
                "logical_cpus": os.cpu_count(), "kernel": platform.release(),
                "load_average_start": list(os.getloadavg()), "other_scopes_start": h181.other_scopes(own),
                "warmup": {}, "before": [], "runs": []}
    first = next(p for p in problems if p["kind"] == "run")
    for build in BUILD_ORDER:  # untimed: caches and page cache
        document["warmup"][build] = run_one(build, first, env, out=WORK / "warmup" / f"{build}.json")
    started = time.monotonic()
    for k, problem in enumerate(problems):
        document["before"].append({"problem": problem["id"], "load_average": list(os.getloadavg()),
                                   "other_scopes": h181.other_scopes(own)})
        for position, build in enumerate(build_order(k)):
            document["runs"].append(run_one(build, problem, env, position))
        h181.write_atomic(TIMINGS, document)
    document.update(wall_s=time.monotonic() - started, load_average_end=list(os.getloadavg()),
                    other_scopes_end=h181.other_scopes(own), git_dirty_paths_end=dirty_paths())
    h181.write_atomic(TIMINGS, document)
    print(f"p182_harness: wrote {TIMINGS} ({len(document['runs'])} runs)")


# ------------------------------------------------------------------------------------------------------ assemble

def mesh_regions(path):
    """cell_count of a mesh output's header record."""
    with open(path) as f:
        return json.loads(f.readline()).get("cell_count")


def assemble(record_path, delete_outputs):
    import check_p182 as check  # noqa: E402  (imports this module; loaded late)
    require_pins()
    timings = json.loads(TIMINGS.read_text())
    if timings["harness_sha256"] != h181.sha256_file(Path(__file__)):
        sys.exit("p182_harness: the harness changed since the run")
    cases = load_cases()
    problems = plan_problems(cases)
    runs = {(r["problem"], r["build"]): r for r in timings["runs"]}
    comparisons = {}
    for problem in problems:
        pair = [runs.get((problem["id"], b)) for b in BUILD_ORDER]
        if any(r is None or r["exit_code"] != 0 or not Path(r["output"]).is_file() for r in pair):
            comparisons[problem["id"]] = {"unchanged": False, "first_differences": ["a run failed or is missing"],
                                          "kept_sets_equal": False, "pruning": check.empty_checks()}
            continue
        bmin = check.bmin_of(json.loads(Path(problem["spec"]).read_text()))
        comparisons[problem["id"]] = check.compare_files(problem["kind"], pair[0]["output"], pair[1]["output"], bmin)
    inputs = {name: {"path": str(path), "sha256": h181.sha256_file(path)} for name, (path, _) in PINS.items()}
    inputs.update({name: {"path": str(ROOT / rel), "sha256": h181.sha256_file(ROOT / rel)}
                   for name, rel in check.REPO_INPUTS.items()})
    walls = {key: r["wall_s"] for key, r in runs.items()}
    medians = {f"{lib}_{b}_median_s": statistics.median(walls[(p["id"], b)] for p in problems
                                                         if p["kind"] == "run" and p["library"] == lib
                                                         and (p["id"], b) in walls)
               for lib in LIBRARIES for b in BUILD_ORDER}
    document = {
        "schema": SCHEMA,
        "environment": {k: timings.get(k) for k in (
            "cpu_model", "logical_cpus", "kernel", "git_head", "git_dirty_paths", "git_dirty_paths_end", "cgroup",
            "versions", "load_average_start", "load_average_end", "other_scopes_start", "other_scopes_end",
            "wall_s")},
        "inputs": inputs,
        "plan": {"cases": cases, "problems": problems, "threads": THREADS,
                 "builds": {b: {"commit": v[2], "sha256": v[1]} for b, v in BUILDS.items()},
                 "order": [{"problem": p["id"], "first": first_build(k)} for k, p in enumerate(problems)],
                 "limits_s": LIMIT_S},
        "warmup": timings["warmup"], "before": timings["before"],
        "runs": timings["runs"], "comparisons": comparisons,
        "timing_summary": {"fns_median_wall_s": medians,
                           "mesh_wall_s": {n: {b: walls.get((n, b)) for b in BUILD_ORDER} for n in MESHES}},
    }
    document["environment"]["mesh_regions"] = {n: comparisons[n].get("cell_count") for n in MESHES}
    h181.write_atomic(record_path, document)
    if delete_outputs:
        shutil.rmtree(WORK / "out", ignore_errors=True)
        shutil.rmtree(WORK / "warmup", ignore_errors=True)
    print(f"p182_harness: wrote {record_path} ({len(problems)} problems compared)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("prepare")
    sub.add_parser("run")
    a = sub.add_parser("assemble")
    a.add_argument("--record", type=Path, default=RECORD)
    a.add_argument("--delete-outputs", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "prepare":
        prepare()
    elif args.mode == "run":
        run()
    else:
        assemble(args.record, args.delete_outputs)


if __name__ == "__main__":
    main()
