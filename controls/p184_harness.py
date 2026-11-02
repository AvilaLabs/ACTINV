#!/usr/bin/env python3
"""P184 harness (protocols/ACTINV-P184_PROTOCOL.md): stage, build and observe the IRDFF-II covariance sidecar.

  python3 controls/p184_harness.py stage     IRDFF-II and the 2,850 TENDL-2025 sources of the v1.2.0 sidecar
  python3 controls/p184_harness.py build     actinv build-covariance ... --mf33-from (bounded scope)
  python3 controls/p184_harness.py observe   examples/fns_fe_5min.json with each sidecar (bounded scope)
  python3 controls/p184_harness.py assemble  write results/p184_record.json

Outputs go under target/p184-work. Each step refuses to start on a dirty tracked tree or a pin that differs; build
and observe also refuse outside a bounded cgroup (6 GiB, no swap, 128 tasks, at most 4 CPUs).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p184_covariance as pc  # noqa: E402

SCHEMA = "actinv-p184-record-1"
WORK = ROOT / "target" / "p184-work"
RECORD = ROOT / "results" / "p184_record.json"
HOME = Path.home()
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
DATA = Path(os.environ.get("ACTINV_DATA_V120", ROOT / "actinv-data" / "v1.2.0"))
IRDFF_ZIP = HOME / "nuclear-data" / "p17-irdff" / "IRDFF-II_ENDF.zip"
CORPUS = HOME / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
TENDL_ARCHIVE = Path(os.environ.get("P184_TENDL_ARCHIVE", HOME / "Documents" / "Avila-Labs" / ".nctforge-data" / "nctforge"
                                    / "tendl2025-controlled-v1" / "download" / "TENDL-n.tgz"))
WORKING_MANIFEST = HOME / "nuclear-data" / "tendl-2025" / "staging" / "TENDL-n-working.manifest.json"
PINS = {
    "irdff_zip": "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db",
    "irdff_endf": "162b1de760c6b297dc36567c7a1724aeabbe2ab9c02b5323a4bef604ae557554",
    "activation": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "cov_v120": "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde",
}
ACTIVATION = DATA / "activation" / "tendl-2025-neutron-709g.npz"
COV_V120 = DATA / "uncertainty" / "tendl-2025-neutron-709g.cov.npz"
COV_INDEX_V120 = DATA / "uncertainty" / "tendl-2025-neutron-709g.cov_index.json"
SIDECAR = WORK / "p184.cov.npz"
EXAMPLE = ROOT / "examples" / "fns_fe_5min.json"
RESPONSES = ["Mn56", "Mn54", "Fe53", "Cr51", "Mn57"]
WORKERS = 4
GIB = 1 << 30


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def text(cmd, timeout=60):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    return (p.stdout + p.stderr).strip()


def require_clean():
    dirty = text(["git", "status", "--porcelain", "--untracked-files=no"])
    if dirty:
        sys.exit(f"p184_harness: refusing to run: the tracked tree is not clean:\n{dirty}")


def require_pins(*names):
    paths = {"irdff_zip": IRDFF_ZIP, "activation": ACTIVATION, "cov_v120": COV_V120}
    bad = [n for n in names if not paths[n].is_file() or sha256(paths[n]) != PINS[n]]
    if bad:
        sys.exit(f"p184_harness: refusing to run: not the pinned file: {', '.join(bad)}")


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
        sys.exit(f"p184_harness: refusing to run: {why} ({cg}). Launch inside the bounded systemd-run scope.")
    return cg


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


# --------------------------------------------------------------------------------------------------------- stage
def extract_missing(index, out):
    """The sources whose index hash the corpus does not have, from TENDL-n.tgz, with P10 Amendment D's repairs."""
    missing = [t for t in index["targets"]
               if not (CORPUS / t["file"]).is_file() or sha256(CORPUS / t["file"]) != t["source_sha256"]]
    out.mkdir(parents=True, exist_ok=False)
    names = {t["file"] for t in missing}
    with tarfile.open(TENDL_ARCHIVE, "r:gz") as archive:
        members = [m for m in archive if m.isfile() and m.name in names]
        archive.extractall(out, members=members, filter="data")
    repairs = json.loads(WORKING_MANIFEST.read_text())["repairs"]
    repaired = []
    for t in missing:
        path = out / t["file"]
        if path.is_file() and sha256(path) != t["source_sha256"]:
            lines = path.read_text().splitlines(keepends=True)
            for r in repairs:
                line = lines[r["line"] - 1]
                if int(line[66:70]) == r["mat"] and line[55:66].strip() == r["original_field"]:
                    lines[r["line"] - 1] = line[:55] + r["replacement_field"].rjust(11) + line[66:]
            path.write_text("".join(lines))
            repaired.append(t["file"])
    return [t["file"] for t in missing], repaired


def stage():
    require_clean()
    require_pins("irdff_zip", "activation", "cov_v120")
    WORK.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(IRDFF_ZIP) as z:
        z.extract("IRDFF-II.endf", WORK / "irdff")
    irdff = WORK / "irdff" / "IRDFF-II.endf"
    if sha256(irdff) != PINS["irdff_endf"]:
        sys.exit("p184_harness: IRDFF-II.endf is not the pinned member")
    index = json.loads(COV_INDEX_V120.read_text())
    extracted, repaired = extract_missing(index, WORK / "orig")
    record = pc.stage(irdff, ACTIVATION, COV_INDEX_V120, [CORPUS, WORK / "orig"], WORK / "stage", WORK / "stage.json",
                      None)
    write_json(WORK / "stage_meta.json", {"extracted_from_archive": extracted, "repaired": repaired,
                                          "archive": str(TENDL_ARCHIVE), "git_head": text(["git", "rev-parse", "HEAD"])})
    print(f"staged: {len(record['replaced'])} reactions replaced, {len(record['skipped'])} left out; "
          f"{len(extracted)} sources from the archive ({len(repaired)} repaired)")


# --------------------------------------------------------------------------------------------------------- build
def build():
    require_clean()
    require_pins("activation")
    cg = require_bounded()
    cmd = [ACTINV, "build-covariance", WORK / "stage" / "inputs", ACTIVATION, SIDECAR, "--workers", str(WORKERS),
           "--mf33-from", WORK / "stage" / "mf33"]
    start = time.monotonic()
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=ROOT, timeout=6 * 3600)
    write_json(WORK / "build.json", {
        "command": [str(c) for c in cmd], "exit": p.returncode, "stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:],
        "seconds": time.monotonic() - start, "cgroup": cg, "binary_sha256": sha256(ACTINV),
        "binary_version": text([ACTINV, "--version"]), "git_head": text(["git", "rev-parse", "HEAD"]),
        "git_dirty": text(["git", "status", "--porcelain", "--untracked-files=no"]),
        "sidecar_sha256": sha256(SIDECAR) if SIDECAR.is_file() else None})
    print(f"build exit {p.returncode}")
    return p.returncode


# ------------------------------------------------------------------------------------------------------- observe
def observation_problem(covariance):
    problem = json.loads(EXAMPLE.read_text())
    problem["library"] = {"path": str(ACTIVATION), "sha256": PINS["activation"]}
    problem["decay"] = {"primary": str(DATA / "decay" / "endf-b-viii-0_decay.dat"),
                        "fallback": str(DATA / "decay" / "jeff-3-3_decay.dat")}
    problem["uncertainty"] = {"channels": ["cross_section_mf33"],
                              "responses": [f"activity:{n}" for n in RESPONSES],
                              "covariance": {"path": str(covariance), "sha256": sha256(covariance)}}
    return problem


def observe():
    require_clean()
    require_pins("activation", "cov_v120")
    cg = require_bounded()
    runs = {}
    for name, covariance in (("v1.2.0", COV_V120), ("p184", SIDECAR)):
        spec = WORK / f"observe_{name}.json"
        out = WORK / f"observe_{name}_result.json"
        write_json(spec, observation_problem(covariance))
        p = subprocess.run([str(ACTINV), "run", str(spec), str(out)], capture_output=True, text=True, cwd=ROOT,
                           timeout=3600)
        runs[name] = {"spec": str(spec), "output": str(out), "exit": p.returncode, "stderr": p.stderr[-2000:],
                      "covariance_sha256": sha256(covariance),
                      "output_sha256": sha256(out) if out.is_file() else None}
        print(f"observe {name}: exit {p.returncode}")
    write_json(WORK / "observe.json", {"runs": runs, "cgroup": cg, "binary_sha256": sha256(ACTINV),
                                       "git_head": text(["git", "rev-parse", "HEAD"])})


# ------------------------------------------------------------------------------------------------------ assemble
def assemble():
    stage_record = json.loads((WORK / "stage.json").read_text())
    record = {
        "schema": SCHEMA, "pins": PINS,
        "stage": stage_record, "stage_meta": json.loads((WORK / "stage_meta.json").read_text()),
        "build": json.loads((WORK / "build.json").read_text()),
        "observe": json.loads((WORK / "observe.json").read_text()),
        "sidecar": {"path": str(SIDECAR), "sha256": sha256(SIDECAR),
                    "index_sha256": sha256(SIDECAR.with_name("p184.cov_index.json"))},
    }
    write_json(RECORD, record)
    print(f"record -> {RECORD.relative_to(ROOT)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["stage", "build", "observe", "assemble"])
    args = ap.parse_args(argv)
    return {"stage": stage, "build": build, "observe": observe, "assemble": assemble}[args.step]() or 0


if __name__ == "__main__":
    sys.exit(main())
