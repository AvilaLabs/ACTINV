#!/usr/bin/env python3
"""P180 harness (protocols/ACTINV-P180_PROTOCOL.md): wall time of ACTINV 1.7.1 and OpenMC 0.15.3 depletion on P172's
132 JAEA FNS decay-heat problems, one problem per process, the two codes paired problem by problem.

For each experiment, in sorted order, three processes run one after another inside this harness's bounded scope:
  actinv_endf8  the released `actinv 1.7.1` binary, `actinv run`, on the repaired ENDF/B-VIII.1 709-group library (the one
                P178 and P179 pin): the same cross sections as OpenMC
  actinv_tendl  the same binary on the shipped TENDL-2025 library of data catalog 1.2.0
  openmc        controls/openmc_fns_driver.py (P172's driver, unmodified) with a one-case job: OpenMC 0.15.3,
                MicroXS.from_multigroup_flux -> IndependentOperator -> PredictorIntegrator, ENDF/B-VIII.1 and the
                published chain_endfb81_fast.xml, with P172's environment
The ACTINV problems are cb2_fns.specification's, as P172 ran them, with only the library changed. Each process is timed
from start to exit (time.monotonic) with its CPU time from wait4. OpenMC's driver also reports its session start-up,
collapse and depletion times.

Modes (each its own process, inside the bounded scope AGENTS.md prescribes):
  prepare   writes the ACTINV problems and the OpenMC one-case jobs under <work>/inputs
  run       one untimed warm-up of each arm on the first problem, then the timed pass; writes <out>/timings.json
  assemble  writes results/p180_speed.json (timings plus the agreement diagnostics with P172 and P179)

P180_REHEARSAL=1: the first 3 problems only, every output under target/p180-rehearsal*, nothing scored or kept.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p172_openmc_fns as p172  # noqa: E402

REHEARSAL = os.environ.get("P180_REHEARSAL") == "1"
TAG = "p180-rehearsal" if REHEARSAL else "p180"
WORK = ROOT / "target" / f"{TAG}-work"
OUT_DIR = ROOT / "target" / TAG if REHEARSAL else ROOT / "results" / "p180"
RECORD = OUT_DIR / "record.json" if REHEARSAL else ROOT / "results" / "p180_speed.json"
SCHEMA = "actinv-p180-speed-1"
PROBLEMS = 3 if REHEARSAL else p172.EXPERIMENTS

MAIN = Path("/home/connoravila/Documents/actinv")  # the main checkout, which holds the data release and pinned copies
ACTINV_BIN = MAIN / "target/p180-bin/actinv"
REPAIRED_NPZ = p172.DATA / "endfb-viii.1-p178/endfb81_all_709g.npz"
REPAIRED_INDEX = p172.DATA / "endfb-viii.1-p178/endfb81_all_709g_index.json"
TENDL_NPZ = MAIN / "actinv-data/v1.2.0/activation/tendl-2025-neutron-709g.npz"
TENDL_INDEX = MAIN / "actinv-data/v1.2.0/activation/tendl-2025-neutron-709g_index.json"
P172_OPENMC = ROOT / "results/p172/openmc.json"
P179_ARM = ROOT / "results/p179/actinv_endf8_repaired.json"

PINNED = {
    "actinv_bin": (ACTINV_BIN, "bd0c21c96a17918d6a21b29553be33877ff3c5b7fa4b92882598d7a37f6458bc"),
    "repaired_library": (REPAIRED_NPZ, "a964a44ef106b692d170ba6085859ca2827ff967b3e9a6ddc60ca8fa9ac92051"),
    "repaired_index": (REPAIRED_INDEX, "b5a4b668324a92bd96e8e90c5feb521a79ca8838e42a517ac080652074b7f41a"),
    "tendl2025_library": (TENDL_NPZ, p172.PINNED["tendl2025_library"][1]),
    "tendl2025_index": (TENDL_INDEX, p172.PINNED["tendl2025_index"][1]),
    "chain_xml": p172.PINNED["chain_xml"],
    "cross_sections_xml": p172.PINNED["cross_sections_xml"],
    "decay_primary": p172.PINNED["decay_primary"],
    "decay_fallback": p172.PINNED["decay_fallback"],
    "fns_archive": p172.PINNED["fns_archive"],
    "abundance_mass": p172.PINNED["abundance_mass"],
}
ARMS = ("actinv_endf8", "actinv_tendl", "openmc")
LIBRARIES = {"actinv_endf8": ("repaired_library", REPAIRED_NPZ), "actinv_tendl": ("tendl2025_library", TENDL_NPZ)}
ACTINV_LIMIT_S = 900
OPENMC_LIMIT_S = 3600
POLL_S = 0.002


def require_pins():
    bad = [name for name, (path, digest) in PINNED.items() if p172.sha256_file(path) != digest]
    if bad:
        sys.exit(f"p180_speed: refusing to run: not the pinned file: {', '.join(bad)}")


def experiments():
    found = sorted((d.name, p.stem) for d in (p172.DATA / "conderc-fns/fns").iterdir() if d.is_dir()
                   for p in d.glob("*.exp"))
    if len(found) != p172.EXPERIMENTS:
        sys.exit(f"p180_speed: expected {p172.EXPERIMENTS} experiments, found {len(found)}")
    return found[:PROBLEMS]


def case_name(material, experiment):
    return f"{material}_{experiment}"


# ------------------------------------------------------------------------------------------------------ prepare

def prepare():
    require_pins()
    os.environ["ACTINV_LIBRARY"] = str(TENDL_NPZ)
    import cb2_fns  # noqa: E402  (loads the Python module named by ACTINV_PYTHON_MODULE at import; only its spec is used)
    import openmc_fns_headtohead as h  # noqa: E402
    inputs = WORK / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    listing = []
    for material, experiment in experiments():
        name = case_name(material, experiment)
        spec, record = cb2_fns.specification(material, experiment)
        for arm, (pin, path) in LIBRARIES.items():
            problem = json.loads(json.dumps(spec))
            problem["library"] = {"path": str(path), "sha256": PINNED[pin][1]}
            (inputs / f"{name}.{arm}.json").write_text(json.dumps(problem, indent=1, sort_keys=True))
        job, meta = h.build_job([(material, experiment)], WORK / "openmc" / name)
        job["chain_file"] = str(p172.CHAIN_XML)
        (inputs / f"{name}.openmc.json").write_text(json.dumps(job))
        listing.append({"case": name, "material": material, "experiment": experiment,
                        "cooling_cum_s": record["cooling_cum_s"]})
    p172.write_atomic(inputs / "cases.json", listing)


# ------------------------------------------------------------------------------------------------------ run

def timed(cmd, env, log, limit_s):
    """Start `cmd`, reap it with wait4 (bounded by `limit_s`; on expiry it is killed and reaped), and return its wall
    time, CPU time and exit code."""
    load = os.getloadavg()[0]
    with open(log, "w") as stream:
        started = time.monotonic()
        proc = subprocess.Popen([str(c) for c in cmd], env=env, stdout=stream, stderr=subprocess.STDOUT)
        deadline, timed_out = started + limit_s, False
        while True:
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() > deadline:
                timed_out = True
                proc.kill()
                pid, status, usage = os.wait4(proc.pid, 0)
                break
            time.sleep(POLL_S)
        wall = time.monotonic() - started
    proc.returncode = os.waitstatus_to_exitcode(status)  # reaped here; Popen must not wait again
    return {"wall_s": wall, "cpu_s": usage.ru_utime + usage.ru_stime, "returncode": proc.returncode,
            "timed_out": timed_out, "max_rss_kib": usage.ru_maxrss, "loadavg_1min_before": load}


def other_scopes():
    """Transient systemd scopes of this user other than the one this harness runs in."""
    own = (p172.cgroup_record().get("path") or "").rsplit("/", 1)[-1]
    out = subprocess.run(["systemctl", "--user", "list-units", "--type=scope", "--state=running", "--no-legend",
                          "--plain"], capture_output=True, text=True, timeout=30).stdout
    return sorted(u.split()[0] for u in out.splitlines() if u.startswith("run-") and u.split()[0] != own)


def run_arm(arm, name, env_actinv, env_openmc):
    inputs, results = WORK / "inputs", WORK / "results"
    if arm == "openmc":
        job = WORK / "inputs" / f"{name}.openmc.json"
        return timed([p172.OPENMC_PY, p172.DRIVER, job], env_openmc, results / f"{name}.openmc.log", OPENMC_LIMIT_S)
    return timed([ACTINV_BIN, "run", inputs / f"{name}.{arm}.json", results / f"{name}.{arm}.result.json"],
                 env_actinv, results / f"{name}.{arm}.log", ACTINV_LIMIT_S)


def run(out):
    cg = p172.require_bounded(6)
    require_pins()
    cases = json.loads((WORK / "inputs" / "cases.json").read_text())
    (WORK / "results").mkdir(parents=True, exist_ok=True)
    (WORK / "tmp").mkdir(exist_ok=True)
    env_actinv = dict(os.environ, ACTINV_CACHE_DIR=str(WORK / "actinv-cache"), RAYON_NUM_THREADS="2")
    env_openmc = dict(os.environ, OPENMC_CROSS_SECTIONS=str(p172.XS_XML), OMP_NUM_THREADS="2",
                      OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONHASHSEED="0", TMPDIR=str(WORK / "tmp"))
    version = subprocess.run([str(ACTINV_BIN), "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    probe = subprocess.run([str(p172.OPENMC_PY), "-c", "import openmc; print(openmc.__version__)"],
                           capture_output=True, text=True, timeout=120).stdout.strip()
    if probe != p172.OPENMC_VERSION:
        sys.exit(f"p180_speed: expected OpenMC {p172.OPENMC_VERSION}, interpreter reports {probe!r}")
    scopes_start = other_scopes()
    warmup = {arm: run_arm(arm, cases[0]["case"], env_actinv, env_openmc) for arm in ARMS}  # caches, page cache
    timings, started = [], time.monotonic()
    for case in cases:
        row = {"case": case["case"], "material": case["material"], "experiment": case["experiment"]}
        row["other_scopes_before"] = other_scopes()
        for arm in ARMS:
            row[arm] = run_arm(arm, case["case"], env_actinv, env_openmc)
        driver = WORK / "openmc" / case["case"] / "driver" / "openmc_results.json"
        if driver.is_file():
            res = json.loads(driver.read_text())
            o = res["cases"].get(case["case"]) or {}
            row["openmc"].update(status=o.get("status"), session_init_s=res.get("session_init_s"),
                                 micros_s=sum(g["micros_s"] for g in res.get("groups", {}).values()),
                                 deplete_s=o.get("deplete_s"))
        timings.append(row)
    document = {"schema": SCHEMA + "-timings", "actinv_version": version, "openmc_version": probe,
                "wall_s": time.monotonic() - started, "other_scopes_start": scopes_start,
                "other_scopes_end": other_scopes(), "warmup": warmup, "cases": timings,
                "environment": {**p172.environment_record(cg), "p180_harness_sha256": p172.sha256_file(Path(__file__)),
                                "cpu_count": os.cpu_count(), "kernel": os.uname().release,
                                "cpu_model": cpu_model()}}
    p172.write_atomic(out, document)


def cpu_model():
    for line in (p172.read_text("/proc/cpuinfo") or "").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


# ------------------------------------------------------------------------------------------------------ assemble

def actinv_heat(path):
    """Decay heat (uW/g) at each cooling time of an `actinv run` result."""
    result = json.loads(Path(path).read_text())
    return [step["heat_W_per_g"]["total"] * 1.0e6 for step in result["steps"][1:]]


def max_abs_log(pairs):
    logs = [abs(math.log(a / b)) for a, b in pairs if a and b and a > 0 and b > 0]
    return max(logs) if logs else None


def assemble(record_path):
    import openmc_fns_headtohead as h  # noqa: E402
    timings = json.loads((OUT_DIR / "timings.json").read_text())
    cases = {c["case"]: c for c in json.loads((WORK / "inputs" / "cases.json").read_text())}
    p172_openmc = {(r["material"], r["experiment"]): r for r in json.loads(P172_OPENMC.read_text())["records"]}
    p179 = ({(r["material"], r["experiment"]): r for r in json.loads(P179_ARM.read_text())["records"]}
            if P179_ARM.is_file() else {})
    rows = []
    for row in timings["cases"]:
        name, key = row["case"], (row["material"], row["experiment"])
        cooling = cases[name]["cooling_cum_s"]
        out = dict(row)
        heats = {}
        for arm in ("actinv_endf8", "actinv_tendl"):
            path = WORK / "results" / f"{name}.{arm}.result.json"
            heats[arm] = actinv_heat(path) if path.is_file() and row[arm]["returncode"] == 0 else None
            out[arm] = dict(row[arm], steps_ok=heats[arm] is not None and len(heats[arm]) == len(cooling)
                            and all(math.isfinite(v) and v >= 0 for v in heats[arm]))
        driver = WORK / "openmc" / name / "driver" / "openmc_results.json"
        omc = None
        if driver.is_file():
            res = json.loads(driver.read_text())
            o = res["cases"].get(name) or {}
            if o.get("status") == "executed":
                omc, _ = h.heat_uW_g(o["atoms_atom_per_cm3"], res.get("half_life_s") or {},
                                     res.get("decay_energy_ev") or {})
        out["openmc"] = dict(row["openmc"], heat_ok=omc is not None and all(math.isfinite(v) for v in omc))
        old = p172_openmc.get(key, {}).get("heat_uW_g")
        out["openmc_vs_p172_max_abs_log"] = max_abs_log(zip(omc, old)) if omc and old else None
        rec = p179.get(key)
        if rec and heats["actinv_endf8"]:
            pairs = [(heats["actinv_endf8"][min(range(len(cooling)), key=lambda i: abs(cooling[i] - p["time_s"]))],
                      p["actinv_tendl2025_uW_g"]) for p in rec["pairs"]]
            out["actinv_endf8_vs_p179_max_abs_log"] = max_abs_log(pairs)
        rows.append(out)
    inputs = {name: {"path": str(path), "sha256": p172.sha256_file(path)} for name, (path, _) in PINNED.items()}
    document = {"schema": SCHEMA, "inputs": inputs, "actinv_version": timings["actinv_version"],
                "openmc_version": timings["openmc_version"], "environment": timings["environment"],
                "p172_driver_sha256": json.loads(P172_OPENMC.read_text())["environment"]["driver_sha256"],
                "wall_s": timings["wall_s"], "other_scopes_start": timings["other_scopes_start"],
                "other_scopes_end": timings["other_scopes_end"], "warmup": timings["warmup"],
                "timings_sha256": p172.sha256_file(OUT_DIR / "timings.json"), "cases": rows}
    p172.write_atomic(record_path, document)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("prepare", "run", "assemble"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    p172.require_bounded(6)
    if args.mode == "prepare":
        prepare()
    elif args.mode == "run":
        run(OUT_DIR / "timings.json")
    else:
        assemble(RECORD)


if __name__ == "__main__":
    main()
