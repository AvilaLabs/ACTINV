#!/usr/bin/env python3
"""P120 evidence and verdict (protocols/ACTINV-P120_PROTOCOL.md, Amendment A).

  e2e3     E2: run controls/test_p120.py here. E3: in a disposable worktree of HEAD, add one comment line to
           crates/actinv-core/src/photon.rs, docs/guide/results.md and controls/check_p116.py and one new untracked
           .rs file, then run every converted verifier and its history test there; each must exit 0.
           Writes results/p120/e2_e3.json.
  verdict  Derives results/p120_verdict.json from results/p120/ci_steps_{before,after}.json, results/p120/e2_e3.json
           and, if present, results/p120/ci_runs.json (E4). Prints the verdict; exit 0 only for P120-PASS.

Run inside the required systemd cgroup with a disk-backed TMPDIR; HEAD must be the committed candidate.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/p120"
VERDICT = ROOT / "results/p120_verdict.json"
TIMEOUT_S = 3600
CONVERTED = [
    ["python3", "controls/check_p116.py", "--g0-only", "--no-write"],
    ["python3", "controls/check_p116_history.py"],
    ["python3", "controls/check_p117_history.py"],
    ["python3", "controls/check_p118_history.py"],
    ["python3", "controls/test_p116_history.py"],
    ["python3", "controls/test_p117_history.py"],
    ["python3", "controls/test_p118_history.py"],
]
EDITS = {
    "crates/actinv-core/src/photon.rs": "// P120 E3 probe: a later source change.\n",
    "docs/guide/results.md": "\n<!-- P120 E3 probe: a later handbook change. -->\n",
    "controls/check_p116.py": "# P120 E3 probe: a later control change.\n",
}
NEW_RUST = "crates/actinv-core/src/p120_e3_probe.rs"
FIELD_MEANINGS = {
    "check_p118_history.current_source_matches_except_p119_edits":
        "the 6f3f964 Git objects match the frozen maps and ci.yml matches its registered transition",
    "check_p117_history.current_source_matches_except_ci":
        "the b81e8c3 Git objects match the frozen maps and ci.yml matches its registered transition",
    "check_p116_history.all_100_rust_sources_match_git":
        "the 100 Rust Git objects at 2117f3b equal the sealed production map",
}


def run(argv, cwd, log: Path) -> int | str:
    env = dict(os.environ)
    with log.open("wb") as handle:
        try:
            return subprocess.run(argv, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                  timeout=TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            return "timeout"


def git(*args, cwd=ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def e2e3() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    logs = ROOT / "target/p120-e2e3"
    shutil.rmtree(logs, ignore_errors=True)
    logs.mkdir(parents=True)
    head = git("rev-parse", "HEAD")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("commit the candidate first: tracked files differ from HEAD")
    e2 = run(["python3", "controls/test_p120.py"], ROOT, logs / "e2.log")
    probe = ROOT / "target/p120-e3-worktree"
    if probe.exists():
        git("worktree", "remove", "--force", str(probe))
    git("worktree", "add", "--detach", str(probe), head)
    try:
        for relative, line in EDITS.items():
            with (probe / relative).open("a", encoding="utf-8") as handle:
                handle.write(line)
        (probe / NEW_RUST).write_text("// P120 E3 probe: a new untracked Rust file.\n", encoding="utf-8")
        changed = git("status", "--porcelain", "--untracked-files=all", cwd=probe).splitlines()
        rows = []
        for index, argv in enumerate(CONVERTED, start=1):
            log = logs / f"e3-{index:02d}.log"
            code = run(argv, probe, log)
            rows.append({"argv": argv, "exit": code, "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                         "log_tail": log.read_bytes().decode("utf-8", "replace")[-800:]})
            print(f"E3 {code}: {' '.join(argv)}", flush=True)
    finally:
        git("worktree", "remove", "--force", str(probe))
    record = {"schema": "actinv-p120-e2e3-1", "head": head,
              "e2": {"argv": ["python3", "controls/test_p120.py"], "exit": e2,
                     "log_tail": (logs / "e2.log").read_bytes().decode("utf-8", "replace")[-800:]},
              "e3": {"probe_changes": sorted(changed), "runs": rows}}
    (OUT / "e2_e3.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"E2 {e2}; wrote results/p120/e2_e3.json")
    return 0 if e2 == 0 and all(r["exit"] == 0 for r in rows) else 1


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def verdict() -> int:
    before, after = _load(OUT / "ci_steps_before.json"), _load(OUT / "ci_steps_after.json")
    e = _load(OUT / "e2_e3.json")
    ci = _load(OUT / "ci_runs.json")
    gates = {}

    def steps_ok(doc):
        return bool(doc) and doc["steps"] and all(s["exit"] == 0 and not s["tree_changes"] for s in doc["steps"])

    same_steps = bool(before and after) and [s["name"] for s in before["steps"]] == [s["name"] for s in after["steps"]]
    gates["E1"] = "PASS" if same_steps and steps_ok(before) and steps_ok(after) else "FAIL"
    gates["E2"] = "PASS" if e and e["e2"]["exit"] == 0 else "FAIL"
    probe_ok = bool(e) and len(e["e3"]["probe_changes"]) == len(EDITS) + 1
    gates["E3"] = "PASS" if probe_ok and e["e3"]["runs"] and all(r["exit"] == 0 for r in e["e3"]["runs"]) else "FAIL"
    if ci is None:
        gates["E4"] = "NOT_EVALUATED"
    else:
        # Every workflow triggered on the pushed commit must succeed; "controls" must be among them. The handbook
        # and browser workflows are path-filtered and do not run on a commit that touches neither.
        rows = ci.get("runs", [])
        gates["E4"] = "PASS" if (rows and "controls" in {r["name"] for r in rows} and all(
            r["conclusion"] == "success" and r["headSha"] == ci.get("commit") for r in rows)) else "FAIL"
    if "FAIL" in gates.values():
        overall = "P120-FAIL"
    elif "NOT_EVALUATED" in gates.values():
        overall = "P120-INCOMPLETE"
    else:
        overall = "P120-PASS"
    log_identity = None
    if before and after:
        log_identity = {s["name"]: s["log_sha256"] == t["log_sha256"] for s, t in zip(before["steps"], after["steps"])}
    out = {"schema": "actinv-p120-verdict-1", "phase": "P120", "verdict": overall, "gates": gates,
           "e1_candidate_head": after and after["head"], "e1_log_bytes_identical": log_identity,
           "e4_commit": ci and ci.get("commit"), "field_meanings": FIELD_MEANINGS,
           "replaced_test": {"removed": "test_current_rust_tree_must_match_the_implementation_commit",
                             "added": "test_rust_sources_must_match_the_implementation_commit_git_objects"}}
    VERDICT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": overall, "gates": gates}))
    return 0 if overall == "P120-PASS" else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["e2e3", "verdict"])
    args = ap.parse_args(argv)
    return e2e3() if args.command == "e2e3" else verdict()


if __name__ == "__main__":
    sys.exit(main())
