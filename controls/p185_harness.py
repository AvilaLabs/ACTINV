#!/usr/bin/env python3
"""P185 harness (protocols/ACTINV-P185_PROTOCOL.md): state-resolved (MF=10) rows in the MF=33 band.

  python3 controls/p185_harness.py build     the candidate (this checkout) and the baseline (BASE) release binaries
  python3 controls/p185_harness.py observe   problems A and B with each binary and the v1.2.0 sidecar
  python3 controls/p185_harness.py assemble  write results/p185_record.json

Outputs go under target/p185-work. Each step refuses to start on a dirty tracked tree or a pin that differs; build
and observe also refuse outside a bounded cgroup (6 GiB, no swap, 128 tasks, at most 4 CPUs).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "actinv-p185-record-1"
WORK = ROOT / "target" / "p185-work"
RECORD = ROOT / "results" / "p185_record.json"
DATA = Path(os.environ.get("ACTINV_DATA_V120", ROOT / "actinv-data" / "v1.2.0"))
BASE = "b7629fb05213c15b21b01ace1adf9627d4aa93cb"
BASE_SRC = WORK / "base-src"
BASE_TARGET = WORK / "base-target"
PINS = {
    "activation": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "cov_v120": "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde",
}
ACTIVATION = DATA / "activation" / "tendl-2025-neutron-709g.npz"
COV_V120 = DATA / "uncertainty" / "tendl-2025-neutron-709g.cov.npz"
EXAMPLE = ROOT / "examples" / "fns_fe_5min.json"
PROBLEMS = {
    "A": ({"FE": 100.0}, ["Fe53", "Mn54", "Mn56", "Cr51", "Mn57"]),
    "B": ({"NI": 40.0, "CO": 20.0, "AL": 20.0, "W": 10.0, "NB": 10.0},
          ["Co58", "Co57", "Ni57", "Co60", "Al26", "Na24", "W185", "W187", "Nb92m"]),
}
SCHEDULE = [{"dt": "300.0 s", "flux": 1.0}, {"dt": "66.0 s", "flux": 0.0}, {"dt": "3600.0 s", "flux": 0.0},
            {"dt": "86400.0 s", "flux": 0.0}]
GIB = 1 << 30


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def text(cmd, cwd=ROOT, timeout=60):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return (p.stdout + p.stderr).strip()


def require_clean():
    dirty = text(["git", "status", "--porcelain", "--untracked-files=no"])
    if dirty:
        sys.exit(f"p185_harness: refusing to run: the tracked tree is not clean:\n{dirty}")


def require_pins():
    bad = [n for n, p in (("activation", ACTIVATION), ("cov_v120", COV_V120)) if not p.is_file() or sha256(p) != PINS[n]]
    if bad:
        sys.exit(f"p185_harness: refusing to run: not the pinned file: {', '.join(bad)}")


def cgroup():
    rel = None
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            rel = parts[2]
    base = Path("/sys/fs/cgroup") / (rel or "").lstrip("/")

    def read(name):
        try:
            return (base / name).read_text().strip()
        except OSError:
            return None
    return {"path": rel, "memory_max": read("memory.max"), "memory_swap_max": read("memory.swap.max"),
            "pids_max": read("pids.max"), "cpu_max": read("cpu.max")}


def bounded(cg):
    """(ok, reason): memory at most 6 GiB, swap 0, at most 128 tasks, CPU quota at most 4 CPUs."""
    try:
        if int(cg["memory_max"]) > 6 * GIB:
            return False, "memory.max above 6 GiB"
        if int(cg["memory_swap_max"]) != 0:
            return False, "memory.swap.max not 0"
        if int(cg["pids_max"]) > 128:
            return False, "pids.max above 128"
        quota, period = cg["cpu_max"].split()
        if int(quota) / int(period) > 4.0 + 1e-9:
            return False, "cpu.max above 400%"
    except (TypeError, ValueError, KeyError, AttributeError):
        return False, "cgroup limits unreadable or unset"
    return True, "ok"


def require_bounded():
    cg = cgroup()
    ok, why = bounded(cg)
    if not ok:
        sys.exit(f"p185_harness: refusing to run: {why} ({cg}). Launch inside the bounded systemd-run scope.")
    return cg


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


# --------------------------------------------------------------------------------------------------------- build
def cargo_build(cwd, target_dir):
    env = dict(os.environ, CARGO_TARGET_DIR=str(target_dir))
    p = subprocess.run(["cargo", "build", "--release", "--locked", "-p", "actinv-cli"], capture_output=True,
                       text=True, cwd=cwd, env=env, timeout=7200)
    binary = Path(target_dir) / "release" / "actinv"
    return {"exit": p.returncode, "stderr": p.stderr[-2000:], "binary": str(binary),
            "binary_sha256": sha256(binary) if p.returncode == 0 and binary.is_file() else None,
            "git_head": text(["git", "rev-parse", "HEAD"], cwd=cwd),
            "git_dirty": text(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd)}


def build():
    require_clean()
    cg = require_bounded()
    WORK.mkdir(parents=True, exist_ok=True)
    if not BASE_SRC.exists():
        p = subprocess.run(["git", "worktree", "add", "--detach", str(BASE_SRC), BASE], capture_output=True,
                           text=True, cwd=ROOT, timeout=600)
        if p.returncode:
            sys.exit(f"p185_harness: git worktree add failed: {p.stderr}")
    candidate_target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
    if not candidate_target.is_absolute():
        candidate_target = ROOT / candidate_target
    record = {"cgroup": cg}
    for name, cwd, target_dir in (("candidate", ROOT, candidate_target), ("baseline", BASE_SRC, BASE_TARGET)):
        record[name] = cargo_build(cwd, target_dir)
        print(f"build {name}: exit {record[name]['exit']}")
    write_json(WORK / "build.json", record)


# ------------------------------------------------------------------------------------------------------- observe
def problem(name):
    composition, responses = PROBLEMS[name]
    spec = json.loads(EXAMPLE.read_text())
    spec["title"] = f"P185 problem {name}: FNS spectrum, 5 minutes' irradiation"
    spec["library"] = {"path": str(ACTIVATION), "sha256": PINS["activation"]}
    for key in ("primary", "fallback"):
        spec["decay"][key] = str(DATA / "decay" / Path(spec["decay"][key]).name)
    spec["material"]["composition"] = composition
    spec["schedule"] = SCHEDULE
    spec["uncertainty"] = {"channels": ["cross_section_mf33"],
                           "responses": [f"activity:{n}" for n in responses],
                           "covariance": {"path": str(COV_V120), "sha256": PINS["cov_v120"]}}
    return spec


def observe():
    require_clean()
    require_pins()
    cg = require_bounded()
    builds = json.loads((WORK / "build.json").read_text())
    runs = {}
    for binary in ("baseline", "candidate"):
        exe = Path(builds[binary]["binary"])
        if sha256(exe) != builds[binary]["binary_sha256"]:
            sys.exit(f"p185_harness: the {binary} binary changed since the build")
        for name in PROBLEMS:
            spec = WORK / f"problem_{name}.json"
            write_json(spec, problem(name))
            out = WORK / f"{binary}_{name}.json"
            p = subprocess.run([str(exe), "run", str(spec), str(out)], capture_output=True, text=True, cwd=ROOT,
                               timeout=3600)
            runs[f"{binary}_{name}"] = {"binary": binary, "problem": name, "spec": str(spec),
                                        "spec_sha256": sha256(spec), "output": str(out), "exit": p.returncode,
                                        "stderr": p.stderr[-2000:], "binary_sha256": sha256(exe),
                                        "output_sha256": sha256(out) if out.is_file() else None}
            print(f"observe {binary} {name}: exit {p.returncode}")
    write_json(WORK / "observe.json", {"runs": runs, "cgroup": cg, "git_head": text(["git", "rev-parse", "HEAD"])})


# ------------------------------------------------------------------------------------------------------ assemble
def assemble():
    record = {"schema": SCHEMA, "pins": PINS, "base": BASE,
              "build": json.loads((WORK / "build.json").read_text()),
              "observe": json.loads((WORK / "observe.json").read_text())}
    write_json(RECORD, record)
    print(f"record -> {RECORD.relative_to(ROOT)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["build", "observe", "assemble"])
    args = ap.parse_args(argv)
    return {"build": build, "observe": observe, "assemble": assemble}[args.step]() or 0


if __name__ == "__main__":
    sys.exit(main())
