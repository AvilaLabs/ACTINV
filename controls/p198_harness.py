#!/usr/bin/env python3
"""P198 harness (protocols/ACTINV-P198_PROTOCOL.md): the MF=33 band where the covariance does not reach.

  python3 controls/p198_harness.py build     the candidate (this checkout) and the baseline (BASE) release binaries
  python3 controls/p198_harness.py observe   problems A, B and C with each binary and the v1.2.0 sidecar
  python3 controls/p198_harness.py assemble  write results/p198_record.json

Problems A and B are P185's (with Amendment A's Nb92m1); C is Learn ACTINV chapter 1's steel bracket with 0.1 wt%
cobalt. Outputs go under target/p198-work. Each step refuses to start on a dirty tracked tree or a pin that differs;
build and observe also refuse outside a bounded cgroup (6 GiB, no swap, 128 tasks, at most 4 CPUs). The tree,
pin, cgroup and build helpers are P185's sealed harness, imported unchanged.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p185_harness as p185  # noqa: E402

SCHEMA = "actinv-p198-record-1"
WORK = ROOT / "target" / "p198-work"
RECORD = ROOT / "results" / "p198_record.json"
DATA = p185.DATA
BASE = "6cd018eebfdd83b2ce28e8fc7a723fbeb9f61472"
BASE_SRC = WORK / "base-src"
BASE_TARGET = WORK / "base-target"
PINS = dict(p185.PINS, bracket="32811eb64f86bd8457ca0b68b708268be4721dfd4af78348b1fc81c47d191f25")
BRACKET = ROOT / "web" / "learn" / "first-irradiation" / "files" / "bracket.json"
RESPONSES = {
    "A": ["Fe53", "Mn54", "Mn56", "Cr51", "Mn57"],
    "B": ["Co58", "Co57", "Ni57", "Co60", "Al26", "Na24", "W185", "W187", "Nb92m1"],
    "C": ["Co60", "Mn54", "Co58", "Cr51", "Fe55", "Mn56"],
}
C_SCHEDULE = [{"dt": "2592000.0 s", "flux": 1.0}, {"dt": "86400.0 s", "flux": 0.0},
              {"dt": "2592000.0 s", "flux": 0.0}]


def problem(name):
    if name in ("A", "B"):
        spec = p185.problem("A")
        spec["material"]["composition"] = p185.PROBLEMS[name][0]
        spec["title"] = f"P198 problem {name}: FNS spectrum, 5 minutes' irradiation (P185's problem {name})"
    else:
        spec = json.loads(BRACKET.read_text())
        spec["title"] = "P198 problem C: Learn ACTINV's steel bracket with 0.1 wt% cobalt, 30 days in its field"
        spec["library"] = {"path": str(p185.ACTIVATION), "sha256": PINS["activation"]}
        spec["decay"]["primary"] = str(DATA / "decay" / "endf-b-viii-0_decay.dat")
        spec["decay"]["fallback"] = str(DATA / "decay" / "jeff-3-3_decay.dat")
        composition = spec["material"]["composition"]
        composition["Fe"] = round(composition["Fe"] - 0.1, 6)
        composition["Co"] = 0.1
        spec["schedule"] = C_SCHEDULE
        del spec["photon"]
    spec["uncertainty"] = {"channels": ["cross_section_mf33"],
                           "responses": [f"activity:{n}" for n in RESPONSES[name]],
                           "covariance": {"path": str(p185.COV_V120), "sha256": PINS["cov_v120"]}}
    return spec


def require_pins():
    p185.require_pins()
    if p185.sha256(BRACKET) != PINS["bracket"]:
        sys.exit("p198_harness: refusing to run: not the pinned bracket problem")


def build():
    p185.require_clean()
    cg = p185.require_bounded()
    WORK.mkdir(parents=True, exist_ok=True)
    if not BASE_SRC.exists():
        p = subprocess.run(["git", "worktree", "add", "--detach", str(BASE_SRC), BASE], capture_output=True,
                           text=True, cwd=ROOT, timeout=600)
        if p.returncode:
            sys.exit(f"p198_harness: git worktree add failed: {p.stderr}")
    candidate_target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
    if not candidate_target.is_absolute():
        candidate_target = ROOT / candidate_target
    record = {"cgroup": cg}
    for name, cwd, target_dir in (("candidate", ROOT, candidate_target), ("baseline", BASE_SRC, BASE_TARGET)):
        record[name] = p185.cargo_build(cwd, target_dir)
        print(f"build {name}: exit {record[name]['exit']}")
    p185.write_json(WORK / "build.json", record)


def observe():
    p185.require_clean()
    require_pins()
    cg = p185.require_bounded()
    builds = json.loads((WORK / "build.json").read_text())
    runs = {}
    for binary in ("baseline", "candidate"):
        exe = Path(builds[binary]["binary"])
        if p185.sha256(exe) != builds[binary]["binary_sha256"]:
            sys.exit(f"p198_harness: the {binary} binary changed since the build")
        for name in RESPONSES:
            spec = WORK / f"problem_{name}.json"
            p185.write_json(spec, problem(name))
            out = WORK / f"{binary}_{name}.json"
            out.unlink(missing_ok=True)
            p = subprocess.run([str(exe), "run", str(spec), str(out)], capture_output=True, text=True, cwd=ROOT,
                               timeout=7200)
            runs[f"{binary}_{name}"] = {"binary": binary, "problem": name, "spec": str(spec),
                                        "spec_sha256": p185.sha256(spec), "output": str(out), "exit": p.returncode,
                                        "stderr": p.stderr[-2000:], "binary_sha256": p185.sha256(exe),
                                        "output_sha256": p185.sha256(out) if out.is_file() else None}
            print(f"observe {binary} {name}: exit {p.returncode}")
    p185.write_json(WORK / "observe.json", {"runs": runs, "cgroup": cg,
                                            "git_head": p185.text(["git", "rev-parse", "HEAD"])})


def assemble():
    record = {"schema": SCHEMA, "pins": PINS, "base": BASE,
              "build": json.loads((WORK / "build.json").read_text()),
              "observe": json.loads((WORK / "observe.json").read_text())}
    p185.write_json(RECORD, record)
    print(f"record -> {RECORD.relative_to(ROOT)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["build", "observe", "assemble"])
    args = ap.parse_args(argv)
    return {"build": build, "observe": observe, "assemble": assemble}[args.step]() or 0


if __name__ == "__main__":
    sys.exit(main())
