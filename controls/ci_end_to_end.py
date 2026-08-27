#!/usr/bin/env python3
"""P6-G2: the end-to-end control CI runs. Builds a small iron-only library from the pinned data subset, runs the FNS Fe
5-minute spec through both the CLI and the Python module, and checks both against the recorded expected values.
The iron-only library reproduces the 255-target library on this spec (verified: 2.6e-12) because only iron targets
contribute to a pure-iron sample."""
import importlib.util
import os, sys, json, subprocess, tempfile, numpy as np
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "controls"))
from ci_result import baseline_mismatches

os.environ["PYTHONWARNINGS"] = "ignore"
DATA = os.environ.get("ACTINV_CI_DATA", os.path.expanduser("~/actinv-ci-data"))
OUT = os.environ.get("ACTINV_CI_OUT", tempfile.mkdtemp(prefix="actinv-ci-"))
ABS_LIMIT = 1e-11 * 1e-6      # the P5-G4 criterion, 1e-11 uW/g, expressed in W/g
exp = json.load(open(os.path.join(ROOT, "controls", "ci_expected.json")))
# ---- build the small library from the fetched files
lib = os.path.join(OUT, "ci_fe.npz")
if not os.path.exists(lib):
    subprocess.run([sys.executable, os.path.join(ROOT, "controls", "tendl_build.py"), os.path.join(DATA, "tendl"), OUT,
                    "--workers", "4", "--dense", "1", "--name", "ci_fe"], check=True)
# ---- build the spec against the fetched data
spec = json.load(open(os.path.join(ROOT, "examples", "fns_fe_5min.json")))
spec["library"]["path"] = lib
spec["decay"]["primary"] = os.path.join(DATA, "decay", "endf-b-viii-0_decay.dat")
spec["decay"]["fallback"] = ""
sp = os.path.join(OUT, "spec.json"); json.dump(spec, open(sp, "w"))
# ---- entry point 1: the CLI
res_cli = os.path.join(OUT, "cli.json")
subprocess.run([os.path.join(ROOT, "target", "release", "actinv"), "run", sp, res_cli], check=True)
cli = json.load(open(res_cli))
# ---- entry point 2: the installed module, or the explicitly built local extension
try:
    import actinv
except ModuleNotFoundError:
    extension = os.environ.get(
        "ACTINV_PYTHON_LIBRARY",
        os.path.join(ROOT, "python", "target", "release", "libactinv.so"),
    )
    module_spec = importlib.util.spec_from_file_location("actinv", extension)
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load Python extension {extension}")
    actinv = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(actinv)
py = json.loads(actinv.run(json.dumps(spec)))
# ---- checks
want = np.array(exp["heat_W_per_g_per_step"])
got_cli = np.array([s["heat_W_per_g"]["total"] for s in cli["steps"]])
got_py = np.array([s["heat_W_per_g"]["total"] for s in py["steps"]])
d_cli = float(np.max(np.abs(got_cli - want))); d_py = float(np.max(np.abs(got_py - want)))
identical = bool(np.array_equal(got_cli, got_py))
res = {"data_dir": DATA, "library_targets": len(json.load(open(lib.replace(".npz", "_index.json")))["targets"]),
       "expected_first_cooling_W_per_g": float(want[1]), "cli_first_cooling_W_per_g": float(got_cli[1]),
       "max_abs_deviation_cli_W_per_g": d_cli, "max_abs_deviation_python_W_per_g": d_py,
       "criterion_abs_W_per_g": ABS_LIMIT, "cli_equals_python": identical,
       "mode": cli["mode"], "pruned_states": cli["pruned_states"]}
baseline_errors = baseline_mismatches(res, exp["result_baseline"])
res["pass"] = bool(
    d_cli <= ABS_LIMIT and d_py <= ABS_LIMIT and identical
    and cli["mode"] == exp["mode"] and cli["pruned_states"] == exp["pruned_states"]
    and not baseline_errors
)
print(json.dumps(res, indent=1))
if baseline_errors:
    print("baseline mismatches: " + ", ".join(baseline_errors), file=sys.stderr)
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
with open(os.path.join(ROOT, "results", "ci_end_to_end.json"), "w") as stream:
    json.dump(res, stream, indent=1)
    stream.write("\n")
sys.exit(0 if res["pass"] else 1)
