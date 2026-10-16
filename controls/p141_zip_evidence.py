#!/usr/bin/env python3
"""P141 evidence Z1/Z2: writers and readers byte-identical between the zip 8.6.0 reference binary and the
zip 9.0.4 candidate. Coordinator-only; every ACTINV call runs inside the required systemd cgroup, serially.

    python3 controls/p141_zip_evidence.py REF_BINARY CAND_BINARY
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "target/p141/work"
RESULT = ROOT / "results/p141/z1_z2.json"
DATA = ROOT / "actinv-data/v1.1.0"
PROTON = Path.home() / "nuclear-data/tendl-2025/files/p"
NEUTRON = Path.home() / "nuclear-data/tendl-2025/files/n-working"
P11_INPUTS = {"n-Fe056.tendl": "f33f867a4f9c4579a62954fe31dc6e70768ab2424dc8f282a122a93f156d2e1e",
              "n-Ni058.tendl": "26c8ae72a203187fae84f9a11c3997086cd70153386e04cf204b4122ab368916"}
LIBRARIES = ["activation/tendl-2025-neutron-709g.npz", "activation/tendl-2025-patched-neutron-709g.npz",
             "activation/tendl-2025-proton-162g.npz"]
COVARIANCE = "uncertainty/tendl-2025-neutron-709g.cov.npz"
CG = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
      "-p", "TasksMax=128", "-p", "CPUQuota=200%", "--"]
TIMING_KEYS = {"ms", "elapsed_ms", "elapsed_s", "wall_time_s", "timing", "timings", "duration_ms"}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def run(binary: Path, args: list, *, out: Path | None = None) -> dict:
    env = dict(os.environ, RAYON_NUM_THREADS="2", TMPDIR=str(ROOT / "target/preflight-tmp"))
    command = CG + [str(binary)] + [str(a) for a in args]
    if out is None:
        child = subprocess.run(command, cwd=ROOT, capture_output=True, env=env, timeout=7200)
        return {"returncode": child.returncode, "stdout": child.stdout, "stderr_tail": child.stderr[-600:].decode(errors="replace")}
    with out.open("wb") as stream:
        child = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.PIPE, env=env, timeout=7200)
    return {"returncode": child.returncode, "stderr_tail": child.stderr[-600:].decode(errors="replace")}


def strip_timing(value):
    if isinstance(value, dict):
        return {k: strip_timing(v) for k, v in value.items() if k not in TIMING_KEYS}
    if isinstance(value, list):
        return [strip_timing(v) for v in value]
    return value


def first_difference(a, b, path=""):
    if type(a) is not type(b):
        return path or "/"
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}/{key}"
            found = first_difference(a[key], b[key], f"{path}/{key}")
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path} (length)"
        for i, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else (path or "/")


def main() -> int:
    ref, cand = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    report = {"schema": "actinv-p141-z1-z2-1", "reference_sha256": sha(ref), "candidate_sha256": sha(cand),
              "z1": {}, "z2": {}}

    # Z1a: full TENDL-2025 proton build, as P99 G2.
    proton = {}
    for tag, binary in (("ref", ref), ("cand", cand)):
        out = WORK / f"proton_{tag}.npz"
        proton[tag] = run(binary, ["build-library", PROTON, out, "--format", "tendl", "--projectile", "proton",
                                   "--groups", "fispact-162", "--temperature-K", "0", "--workers", "3",
                                   "--continue-on-error", "true", "--decay", DATA / "decay/endf-b-viii-0_decay.dat",
                                   "--decay-fallback", DATA / "decay/jeff-3-3_decay.dat"])
        proton[tag].pop("stdout", None)
        proton[tag]["npz_sha256"] = sha(out) if out.exists() else None
    report["z1"]["proton_full"] = {**proton, "npz_identical": proton["ref"]["npz_sha256"] is not None
                                   and proton["ref"]["npz_sha256"] == proton["cand"]["npz_sha256"],
                                   "shipped_sha256": sha(DATA / LIBRARIES[2])}

    # Z1b: the two pinned P11 inputs, activation library then covariance sidecar.
    inputs = WORK / "p11-inputs"
    inputs.mkdir()
    for name, digest in P11_INPUTS.items():
        shutil.copyfile(NEUTRON / name, inputs / name)
        if sha(inputs / name) != digest:
            raise SystemExit(f"{name} does not match its pinned digest")
    small = {}
    for tag, binary in (("ref", ref), ("cand", cand)):
        activation, covariance = WORK / f"p11_{tag}.npz", WORK / f"p11_{tag}.cov.npz"
        a = run(binary, ["build-library", inputs, activation, "--workers", "2"])
        c = run(binary, ["build-covariance", inputs, activation, covariance, "--workers", "1"])
        small[tag] = {"library_rc": a["returncode"], "covariance_rc": c["returncode"],
                      "library_sha256": sha(activation) if activation.exists() else None,
                      "covariance_sha256": sha(covariance) if covariance.exists() else None,
                      "stderr_tail": a["stderr_tail"] + c["stderr_tail"]}
    report["z1"]["p11_inputs"] = {**small,
        "library_identical": small["ref"]["library_sha256"] is not None
        and small["ref"]["library_sha256"] == small["cand"]["library_sha256"],
        "covariance_identical": small["ref"]["covariance_sha256"] is not None
        and small["ref"]["covariance_sha256"] == small["cand"]["covariance_sha256"]}

    # Z2: reads of every shipped library, and two runs.
    for library in LIBRARIES:
        row = {}
        for command in (["inspect", "library"], ["inspect", "xs"]):
            outs = {}
            for tag, binary in (("ref", ref), ("cand", cand)):
                out = WORK / f"{Path(library).stem}_{command[1]}_{tag}.out"
                result = run(binary, command + [DATA / library], out=out)
                outs[tag] = {"returncode": result["returncode"], "sha256": sha(out), "bytes": out.stat().st_size}
            outs["identical"] = outs["ref"]["returncode"] == 0 and outs["ref"]["sha256"] == outs["cand"]["sha256"]
            row[" ".join(command)] = outs
        report["z2"][library] = row

    spec = json.loads((ROOT / "examples/fns_fe_5min.json").read_text())
    uncertain = json.loads(json.dumps(spec))
    uncertain["uncertainty"] = {"covariance": {"path": f"actinv-data/v1.1.0/{COVARIANCE}",
                                               "sha256": sha(DATA / COVARIANCE)},
                                "responses": ["heat.total", "activity:Mn56"], "confidence_level": 0.95}
    for name, document in (("fns_fe_5min", spec), ("fns_fe_5min_uncertainty", uncertain)):
        spec_path = WORK / f"{name}.json"
        spec_path.write_text(json.dumps(document))
        results = {}
        for tag, binary in (("ref", ref), ("cand", cand)):
            out = WORK / f"{name}_{tag}_result.json"
            outcome = run(binary, ["run", spec_path, out])
            results[tag] = {"returncode": outcome["returncode"], "stderr_tail": outcome["stderr_tail"],
                            "data": json.loads(out.read_text()) if out.exists() else None}
        a, b = results["ref"]["data"], results["cand"]["data"]
        report["z2"][name] = {
            "returncodes": [results["ref"]["returncode"], results["cand"]["returncode"]],
            "raw_identical": a == b,
            "identical_apart_from_timing": a is not None and strip_timing(a) == strip_timing(b),
            "first_difference": None if a is None or b is None else first_difference(a, b),
            "stderr_tails": [results["ref"]["stderr_tail"], results["cand"]["stderr_tail"]]}

    z1 = report["z1"]
    z2_reads = all(cmd["identical"] for lib in LIBRARIES for cmd in report["z2"][lib].values())
    z2_runs = all(report["z2"][n]["returncodes"] == [0, 0] and report["z2"][n]["identical_apart_from_timing"]
                  for n in ("fns_fe_5min", "fns_fe_5min_uncertainty"))
    report["z1_pass"] = (z1["proton_full"]["npz_identical"] and z1["p11_inputs"]["library_identical"]
                         and z1["p11_inputs"]["covariance_identical"])
    report["z2_pass"] = z2_reads and z2_runs
    report["pass"] = report["z1_pass"] and report["z2_pass"]
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: report[k] for k in ("z1_pass", "z2_pass", "pass")}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
