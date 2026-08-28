#!/usr/bin/env python3
"""P6-G1: the repository must be self-contained. Run it with an interpreter that has only requirements-ci.txt
installed — `controls/check_dependencies.py` enforces the declaration, this proves the declaration is enough. Every control CI runs is executed from a fresh clone with HOME
redirected to an empty directory, so any dependence on a file outside the clone fails immediately. Nuclear data are the
one permitted exception and reach the controls only through documented environment variables."""
import os, sys, json, shutil, subprocess, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
STEPS = [("cargo build", ["cargo", "build", "--release", "--quiet", "--workspace"]),
         ("unit probe", ["./target/release/unit_probe"]),
         ("G0 cram coefficients", [sys.executable, "controls/g0_cram_coefficients.py"]),
         ("gen_cram is reproducible", [sys.executable, "controls/gen_cram.py"]),
         ("release notes match roadmap", [sys.executable, "controls/check_release_notes.py"]),
         ("prior phase verdicts", [sys.executable, "controls/check_prior_verdicts.py"]),
         ("P12 release source", [sys.executable, "controls/g5_p12_release.py", "--source-only"]),
         ("crates.io release workflow", [sys.executable, "scripts/check_crates_release_workflow.py"]),
         ("P12 checker regression", [sys.executable, "controls/test_check_p12.py"]),
         ("P12 release payload evidence", [sys.executable, "controls/check_p12.py", "--through-g5"]),
         ("P12 bounded input reliability", [sys.executable, "controls/g3_p12_parser_fuzz.py", "--smoke"]),
         ("CI result tolerance regression", [sys.executable, "controls/test_ci_result.py"]),
         ("no undeclared dependencies", [sys.executable, "controls/check_dependencies.py"])]
tmp = tempfile.mkdtemp(prefix="actinv-selftest-"); clone = os.path.join(tmp, "clone"); fake_home = os.path.join(tmp, "home")
os.makedirs(fake_home)
subprocess.run(["git", "clone", "--quiet", ROOT, clone], check=True)
env = dict(os.environ); env["HOME"] = fake_home; env["PYTHONWARNINGS"] = "ignore"
env["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + env.get("PATH", "")
env["CARGO_HOME"] = os.path.expanduser("~/.cargo"); env["RUSTUP_HOME"] = os.path.expanduser("~/.rustup")
env["CARGO_TARGET_DIR"] = os.path.join(clone, "target")
results = []
for name, cmd in STEPS:
    p = subprocess.run(cmd, cwd=clone, env=env, capture_output=True, text=True)
    err = (p.stderr or "")[-300:]
    results.append({"step": name, "returncode": p.returncode, "ok": p.returncode == 0,
                    "outside_clone_reference": ("No such file" in err and fake_home not in err and clone not in err),
                    "stderr_tail": err if p.returncode else ""})
# generated sources must be unchanged by regeneration
diff = subprocess.run(["git", "diff", "--stat"], cwd=clone, capture_output=True, text=True).stdout.strip()
res = {"clone": "<temporary clone>", "home_redirected_to": "<temporary empty home>", "steps": results,
       "regeneration_left_tree_clean": diff == "", "diff": diff,
       "pass": bool(all(r["ok"] for r in results) and diff == "")}
with open(os.path.join(ROOT, "results", "g1_self_contained.json"), "w") as stream:
    json.dump(res, stream, indent=1)
    stream.write("\n")
print(json.dumps(res, indent=1))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if res["pass"] else 1)
