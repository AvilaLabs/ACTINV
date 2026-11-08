#!/usr/bin/env python3
"""P196 harness: run the protocol's problems on the whole library and from the browser's library pack.

`run` (registered run) and `rehearse` (three problems, one browser run, its own work directory and record) take
  --actinv-bin BIN        the candidate's `actinv`
  --pack-run-bin BIN      the candidate's `pack_run` (actinv-core)
  --pack-build-bin BIN    the candidate's `pack_build` (actinv-core)
  --web-dist DIR          the candidate's browser bundle (`trunk build`), for the browser runs
and do, in this order:
  pack     build the pack from the catalog's TENDL-2025 library and decay data into the work directory;
  G2       write each suite problem with `actinv new` (damage block removed: packs refuse it) or copy it from
           examples/, run it with `actinv run` (whole library) and with `pack_run` at two reactions (the pack);
  G3       serve the bundle with the pack beside it on 127.0.0.1 and run each browser problem in headless Chromium
           through the engine worker (controls/p196_browser.mjs);
  G4       run each refusal case with `pack_run`.
Every output stays under the work directory (untracked) and is named in the record with its SHA-256. Prints only
counts. Every child runs with a bounded wait and is killed on time-out; the web server is terminated and reaped at
the end, also on failure.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p196 as c  # noqa: E402

RUN_TIMEOUT_S = 900
BROWSER_TIMEOUT_S = 600
BUILD_TIMEOUT_S = 3600


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def call(args: list[str], timeout: float, env: dict | None = None) -> dict:
    """Run a child with a bounded wait; on time-out subprocess.run kills and reaps it."""
    started = time.perf_counter()
    try:
        done = subprocess.run([str(a) for a in args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                              check=False, env={**os.environ, **(env or {})})
        return {"exit": done.returncode, "stderr": done.stderr[-4000:], "stdout": done.stdout[-4000:],
                "wall_s": time.perf_counter() - started}
    except subprocess.TimeoutExpired:
        return {"exit": None, "stderr": f"timed out after {timeout} s", "stdout": "",
                "wall_s": time.perf_counter() - started}


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1) + "\n")
    return path


def build_pack(binary: Path, work: Path) -> dict:
    pack = work / "pack"
    if pack.exists():
        shutil.rmtree(pack)
    sources = [c.DATA_DIR / rel for rel in (c.LIBRARY_FILE, *c.DECAY_FILES)]
    for path, pinned in zip(sources, (c.LIBRARY_SHA256, *c.DECAY_SHA256)):
        if sha256(path) != pinned:
            raise SystemExit(f"{path} does not match its pin")
    done = call([binary, sources[0], pack, *sources[1:]], BUILD_TIMEOUT_S, {"ACTINV_PACK_IDS": ",".join(c.PACK_IDS)})
    if done["exit"] != 0:
        raise SystemExit(f"pack_build failed: {done['stderr']}")
    return {"dir": str(pack.relative_to(ROOT)), "graph_sha256": sha256(pack / "graph.json"),
            "stderr": done["stderr"].strip()}


def suite_problem(binary: Path, problem: dict, out: Path) -> dict:
    """The problem's spec: `actinv new` with its damage block removed, or a copy of an example."""
    if "example" in problem:
        spec = json.loads((ROOT / problem["example"]).read_text())
    else:
        made = out.parent / "new.json"
        if made.exists():
            made.unlink()
        args = [binary, "new", made, "--material", problem["material"], "--source", problem["source"],
                "--flux", problem["flux"], "--irradiate", problem["irradiate"]]
        done = call(args, RUN_TIMEOUT_S)
        if done["exit"] != 0:
            raise SystemExit(f"actinv new failed for {problem['name']}: {done['stderr']}")
        spec = json.loads(made.read_text())
        spec["damage"] = None
    write_json(out, spec)
    return spec


PLAN = re.compile(r"plan [0-9.]+s: (\d+) targets, (\d+) decay states, (\d+) files, (\d+) bytes")


def g2(actinv: Path, pack_run: Path, pack: Path, work: Path, problems: list[dict]) -> list[dict]:
    rows = []
    for problem in problems:
        case = work / "problems" / problem["name"]
        case.mkdir(parents=True, exist_ok=True)
        spec_path = case / "spec.json"
        suite_problem(actinv, problem, spec_path)
        full, packed = case / "full.json", case / "pack.json"
        for path in (full, packed):
            if path.exists():
                path.unlink()
        whole = call([actinv, "run", spec_path, full], RUN_TIMEOUT_S)
        from_pack = call([pack_run, spec_path, pack, c.REACTIONS, packed], RUN_TIMEOUT_S)
        plan = PLAN.search(from_pack["stderr"])
        row = {"name": problem["name"], "spec": str(spec_path.relative_to(ROOT)), "spec_sha256": sha256(spec_path),
               "full_exit": whole["exit"], "full_wall_s": whole["wall_s"],
               "pack_exit": from_pack["exit"], "pack_wall_s": from_pack["wall_s"],
               "plan": dict(zip(("targets", "decay_states", "files", "bytes"), map(int, plan.groups())))
               if plan else None}
        if whole["exit"] == 0:
            row["full"], row["full_sha256"] = str(full.relative_to(ROOT)), sha256(full)
        else:
            row["full_stderr"] = whole["stderr"]
        if from_pack["exit"] == 0:
            row["pack"], row["pack_sha256"] = str(packed.relative_to(ROOT)), sha256(packed)
        else:
            row["pack_stderr"] = from_pack["stderr"]
        rows.append(row)
    return rows


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def g3(web_dist: Path, pack: Path, work: Path, names: list[str], playwright: str) -> list[dict]:
    site = work / "site"
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(web_dist, site, symlinks=True)
    link = site / "data" / c.PACK_IDS[0]
    link.parent.mkdir(exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(pack.resolve())
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                               "--directory", str(site)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rows = []
    try:
        base = f"http://127.0.0.1:{port}/"
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        bundle = {"engine_js_sha256": sha256(site / "actinv-web-engine.js"),
                  "engine_wasm_sha256": sha256(site / "actinv-web-engine_bg.wasm")}
        for name in names:
            case = work / "problems" / name
            out = case / "browser.json"
            if out.exists():
                out.unlink()
            done = call(["node", ROOT / "controls" / "p196_browser.mjs", base, case / "spec.json", out],
                        BROWSER_TIMEOUT_S, {"PLAYWRIGHT_MODULE": playwright})
            row = {"name": name, "exit": done["exit"], "wall_s": done["wall_s"], **bundle}
            try:
                row["report"] = json.loads(done["stdout"].strip().splitlines()[-1])
            except (IndexError, json.JSONDecodeError):
                row["stderr"] = done["stderr"]
            if out.exists():
                row["browser"], row["browser_sha256"] = str(out.relative_to(ROOT)), sha256(out)
            rows.append(row)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    return rows


def g4(pack_run: Path, pack: Path, work: Path) -> list[dict]:
    base = json.loads((ROOT / c.REFUSAL_BASE).read_text())
    rows = []
    for name, edit, _ in c.REFUSALS:
        spec = copy.deepcopy(base)
        edit(spec)
        path = write_json(work / "refusals" / f"{name}.json", spec)
        out = work / "refusals" / f"{name}.out.json"
        done = call([pack_run, path, pack, c.REACTIONS, out], RUN_TIMEOUT_S)
        rows.append({"name": name, "exit": done["exit"], "stderr": done["stderr"].strip()[-1000:],
                     "wrote_output": out.exists()})
    return rows


def execute(args: argparse.Namespace, rehearsal: bool) -> int:
    work = ROOT / ("target/p196-rehearsal" if rehearsal else "target/p196-work")
    record_path = work / "record.json" if rehearsal else c.RECORD
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    binaries = {name: Path(getattr(args, name)).resolve() for name in ("actinv_bin", "pack_run_bin", "pack_build_bin")}
    problems = c.suite()
    browser = c.BROWSER_PROBLEMS
    if rehearsal:
        problems = [p for p in problems if p["name"] in c.REHEARSAL_PROBLEMS]
        browser = c.REHEARSAL_PROBLEMS[:1]
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    pack = build_pack(binaries["pack_build_bin"], work)
    pack_dir = ROOT / pack["dir"]
    rows = g2(binaries["actinv_bin"], binaries["pack_run_bin"], pack_dir, work, problems)
    browser_rows = g3(Path(args.web_dist), pack_dir, work, browser, args.playwright)
    refusals = g4(binaries["pack_run_bin"], pack_dir, work)
    record = {
        "schema": c.SCHEMA,
        "rehearsal": rehearsal,
        "commit": git("rev-parse", "HEAD"),
        "tracked_tree_clean": git("status", "--porcelain", "--untracked-files=no") == "",
        "binaries": {name: {"path": str(path), "sha256": sha256(path)} for name, path in binaries.items()},
        "pack": pack,
        "reactions": int(c.REACTIONS),
        "problems": rows,
        "browser": browser_rows,
        "refusals": refusals,
        "wall_s": time.time() - started,
    }
    write_json(record_path, record)
    failed = sum(1 for r in rows if r["full_exit"] != 0 or r["pack_exit"] != 0)
    print(f"{len(rows)} problems ({failed} with a failed run), {len(browser_rows)} browser runs, "
          f"{len(refusals)} refusal cases; record {record_path.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "rehearse"):
        p = sub.add_parser(name)
        p.add_argument("--actinv-bin", required=True)
        p.add_argument("--pack-run-bin", required=True)
        p.add_argument("--pack-build-bin", required=True)
        p.add_argument("--web-dist", required=True)
        p.add_argument("--playwright", default=str(ROOT / "web/node_modules/playwright/index.mjs"))
    args = parser.parse_args()
    return execute(args, rehearsal=args.command == "rehearse")


if __name__ == "__main__":
    raise SystemExit(main())
