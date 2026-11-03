#!/usr/bin/env python3
"""P187 harness (protocols/ACTINV-P187_PROTOCOL.md): the MF=33 activity band against brute-force sampling.

  python3 controls/p187_harness.py build      release binary from this checkout
  python3 controls/p187_harness.py first      problems A and B with an uncertainty section (the first-order band)
  python3 controls/p187_harness.py sample     `actinv study run` with study.robustness, N = 200 per problem
  python3 controls/p187_harness.py assemble   results/p187_record.json and results/p187_samples.json.gz

Outputs go under target/p187-work. Each step refuses to start on a dirty tracked tree or a pin that differs; build,
first and sample also refuse outside a bounded cgroup (6 GiB, no swap, 128 tasks, at most 4 CPUs).
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "actinv-p187-record-1"
SAMPLES_SCHEMA = "actinv-p187-samples-1"
WORK = ROOT / "target" / "p187-work"
RECORD = ROOT / "results" / "p187_record.json"
SAMPLES = ROOT / "results" / "p187_samples.json.gz"
DATA = Path(os.environ.get("ACTINV_DATA_V120", ROOT / "actinv-data" / "v1.2.0"))
PINS = {
    "activation": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "cov_v120": "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde",
    "fns_spec": "ed55ed00f6c00fc4a2351e18332e98e7feb33d9b7ca65e7ba861e0e5e8feef39",
}
ACTIVATION = DATA / "activation" / "tendl-2025-neutron-709g.npz"
COV_V120 = DATA / "uncertainty" / "tendl-2025-neutron-709g.cov.npz"
EXAMPLE = ROOT / "examples" / "fns_fe_5min.json"
N_SAMPLES = 200
REHEARSAL = os.environ.get("P187_REHEARSAL") == "1"  # protocol "Rehearsal": 4 samples, outputs under target/ only
if REHEARSAL:
    N_SAMPLES = 4
    WORK = ROOT / "target" / "p187-rehearsal"
    RECORD = WORK / "p187_record.json"
    SAMPLES = WORK / "p187_samples.json.gz"
SEED = {"A": 187001, "B": 187002}
PROBLEMS = {
    "A": ({"FE": 100.0}, ["Fe53", "Mn54", "Mn56", "Cr51", "Mn57"]),
    "B": ({"NI": 40.0, "CO": 20.0, "AL": 20.0, "W": 10.0, "NB": 10.0},
          ["Co58", "Co57", "Ni57", "Co60", "Al26", "Na24", "W185", "W187", "Nb92m1"]),
}
SCHEDULE = [{"dt": "300.0 s", "flux": 1.0}, {"dt": "66.0 s", "flux": 0.0}, {"dt": "3600.0 s", "flux": 0.0},
            {"dt": "86400.0 s", "flux": 0.0}]
OPTIONS = {"mode": "auto", "prune": "rate", "bmin_atoms_per_g": 1e-08, "temperature_K": 293.6}
STATE_RESOLVED = 10
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
        sys.exit(f"p187_harness: refusing to run: the tracked tree is not clean:\n{dirty}")


def require_pins():
    paths = {"activation": ACTIVATION, "cov_v120": COV_V120, "fns_spec": EXAMPLE}
    bad = [n for n, p in paths.items() if not p.is_file() or sha256(p) != PINS[n]]
    if bad:
        sys.exit(f"p187_harness: refusing to run: not the pinned file: {', '.join(bad)}")


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
        sys.exit(f"p187_harness: refusing to run: {why} ({cg}). Launch inside the bounded systemd-run scope.")
    return cg


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


def binary():
    build = json.loads((WORK / "build.json").read_text())
    exe = Path(build["binary"])
    if sha256(exe) != build["binary_sha256"]:
        sys.exit("p187_harness: the binary changed since the build")
    return exe, build["binary_sha256"]


# --------------------------------------------------------------------------------------------------------- build
def build():
    require_clean()
    cg = require_bounded()
    target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
    if not target.is_absolute():
        target = ROOT / target
    p = subprocess.run(["cargo", "build", "--release", "--locked", "-p", "actinv-cli"], capture_output=True,
                       text=True, cwd=ROOT, timeout=7200)
    exe = target / "release" / "actinv"
    write_json(WORK / "build.json", {
        "cgroup": cg, "exit": p.returncode, "stderr": p.stderr[-2000:], "binary": str(exe),
        "binary_sha256": sha256(exe) if p.returncode == 0 and exe.is_file() else None,
        "git_head": text(["git", "rev-parse", "HEAD"]),
        "git_dirty": text(["git", "status", "--porcelain", "--untracked-files=no"])})
    print(f"build: exit {p.returncode}")


# ------------------------------------------------------------------------------------------------- first order
def problem(name):
    composition, responses = PROBLEMS[name]
    spec = json.loads(EXAMPLE.read_text())
    spec["title"] = f"P187 problem {name}: FNS spectrum, 5 minutes' irradiation"
    spec["library"] = {"path": str(ACTIVATION), "sha256": PINS["activation"]}
    for key in ("primary", "fallback"):
        spec["decay"][key] = str(DATA / "decay" / Path(spec["decay"][key]).name)
    spec["material"]["composition"] = composition
    spec["schedule"] = SCHEDULE
    spec["options"] = dict(OPTIONS)
    spec["uncertainty"] = {"channels": ["cross_section_mf33"],
                           "responses": [f"activity:{n}" for n in responses],
                           "covariance": {"path": str(COV_V120), "sha256": PINS["cov_v120"]}}
    return spec


def first_order_summary(out, names):
    """[{t_s, responses: {name: {nominal, sd, rel, from_reaction_total}}}] per step."""
    steps = []
    for step in out["steps"]:
        block = (step.get("uncertainty") or {}).get("responses") or {}
        row = {}
        for name in names:
            e = block.get(f"activity:{name}") or {}
            row[name] = {"nominal": e.get("nominal"), "sd": e.get("mf33_standard_uncertainty"),
                         "rel": e.get("relative_standard_uncertainty"),
                         "from_reaction_total": e.get("parameters_from_reaction_total", 0)}
        steps.append({"t_s": step["t_s"], "responses": row})
    return steps


def first():
    require_clean()
    require_pins()
    cg = require_bounded()
    exe, exe_sha = binary()
    runs = {}
    for name in PROBLEMS:
        spec = WORK / f"first_{name}.json"
        write_json(spec, problem(name))
        out = WORK / f"first_{name}_out.json"
        p = subprocess.run([str(exe), "run", str(spec), str(out)], capture_output=True, text=True, cwd=ROOT,
                           timeout=3600)
        runs[name] = {"spec": str(spec), "spec_sha256": sha256(spec), "output": str(out), "exit": p.returncode,
                      "stderr": p.stderr[-2000:], "binary_sha256": exe_sha,
                      "output_sha256": sha256(out) if out.is_file() else None}
        if p.returncode == 0:
            runs[name]["summary"] = first_order_summary(json.loads(out.read_text()), PROBLEMS[name][1])
        print(f"first {name}: exit {p.returncode}")
    write_json(WORK / "first.json", {"runs": runs, "cgroup": cg, "git_head": text(["git", "rev-parse", "HEAD"])})


# ------------------------------------------------------------------------------------------------------ sampling
def study(name):
    composition, _ = PROBLEMS[name]
    return {"study": "actinv-study-1", "study_id": f"p187-{name}",
            "library": {"path": str(ACTIVATION), "sha256": PINS["activation"]},
            "decay": {key: str(DATA / "decay" / Path(json.loads(EXAMPLE.read_text())["decay"][key]).name)
                      for key in ("primary", "fallback")},
            "options": OPTIONS,
            "cases": {"materials": [{"name": f"p{name.lower()}", "mass_g": 1.0, "composition": composition}],
                      "spectra": [{"name": "fns", "spec_ref": "fns_fe_5min.json"}],
                      "schedules": [{"name": "irr", "steps": SCHEDULE}]},
            "responses": ["total_activity_bq_per_g"],
            "robustness": {"samples": N_SAMPLES, "seed": SEED[name], "channels": {"cross_section_mf33": True},
                           "covariance": {"path": str(COV_V120), "sha256": PINS["cov_v120"]},
                           "responses": ["total_activity_bq_per_g"], "first_order_comparison": False}}


def find_key(root, key):
    stack = [root]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get(key) is not None:
                return item[key]
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def library_lmf():
    with zipfile.ZipFile(ACTIVATION) as z:
        import numpy as np
        with z.open("rows.npy") as f:
            rows = np.lib.format.read_array(f)
    return rows[:, 4]


def sample():
    require_clean()
    require_pins()
    cg = require_bounded()
    exe, exe_sha = binary()
    lmf = library_lmf()
    runs = {}
    for name in PROBLEMS:
        stage = WORK / f"study_{name}"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        shutil.copyfile(EXAMPLE, stage / "fns_fe_5min.json")
        (stage / "actinv-data").symlink_to(DATA.parent)  # the spectrum file's own data references
        write_json(stage / "study.json", study(name))
        p = subprocess.run([str(exe), "study", "run", "study.json", "study_out"], capture_output=True, text=True,
                           cwd=stage, timeout=6 * 3600)
        case = stage / "study_out" / "cases" / f"p{name.lower()}__fns__irr"
        run = {"stage": str(stage), "exit": p.returncode, "stderr": p.stderr[-2000:], "binary_sha256": exe_sha,
               "case": str(case)}
        if p.returncode == 0:
            record = json.loads((stage / "study_out" / "study_record.json").read_text())
            run["clipped_relative"] = find_key(record, "negative_eigenvalue_mass_clipped_relative")
            outputs, scaled, state_rows = [], set(), set()
            for i in range(N_SAMPLES):
                draw = json.loads((case / f"rob_{i}.json").read_text())
                keys = {int(k) for k in (draw.get("options") or {}).get("rate_scale") or {}}
                scaled |= keys
                state_rows |= {k for k in keys if int(lmf[k]) == STATE_RESOLVED}
                outputs.append({"draw_sha256": sha256(case / f"rob_{i}.json"),
                                "output_sha256": sha256(case / f"rob_{i}.out.json")})
            run.update({"samples": outputs, "scaled_rows": len(scaled), "state_resolved_rows": len(state_rows),
                        "nominal_output_sha256": sha256(case / "out.json")})
        runs[name] = run
        print(f"sample {name}: exit {p.returncode}")
    write_json(WORK / "sample.json", {"runs": runs, "cgroup": cg, "git_head": text(["git", "rev-parse", "HEAD"])})


def activities(path, names):
    """[{name: activity}] per step from one solved output; a nuclide absent from a step has activity 0."""
    out = json.loads(Path(path).read_text())
    return [{n: float(step.get("activity_Bq_per_g", {}).get(n, 0.0)) for n in names} for step in out["steps"]]


# ------------------------------------------------------------------------------------------------------ assemble
def assemble():
    first_rec = json.loads((WORK / "first.json").read_text())
    sample_rec = json.loads((WORK / "sample.json").read_text())
    samples = {"schema": SAMPLES_SCHEMA, "problems": {}}
    samples_ref = str(SAMPLES.relative_to(ROOT))
    for name, run in sample_rec["runs"].items():
        if run["exit"] != 0:
            continue
        case = Path(run["case"])
        names = PROBLEMS[name][1]
        samples["problems"][name] = {
            "responses": names,
            "nominal": activities(case / "out.json", names),
            "samples": [activities(case / f"rob_{i}.out.json", names) for i in range(N_SAMPLES)]}
    SAMPLES.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(SAMPLES, "wb", mtime=0) as f:
        f.write((json.dumps(samples, sort_keys=True) + "\n").encode())
    record = {"schema": SCHEMA, "pins": PINS, "n_samples": N_SAMPLES, "seed": SEED,
              "build": json.loads((WORK / "build.json").read_text()), "first": first_rec, "sample": sample_rec,
              "samples_file": {"path": samples_ref, "sha256": sha256(SAMPLES)}}
    write_json(RECORD, record)
    print(f"record -> {RECORD.relative_to(ROOT)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["build", "first", "sample", "assemble"])
    args = ap.parse_args(argv)
    return {"build": build, "first": first, "sample": sample, "assemble": assemble}[args.step]() or 0


if __name__ == "__main__":
    sys.exit(main())
