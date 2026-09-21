#!/usr/bin/env python3
"""TENDL-2025 patched-corpus build with defect eviction (v2).

The builder fails closed on defective sources; the sealed corpus is never
modified.  This driver follows the P25C pattern: hardlink every corpus
file into a stage dir, run a single-file probe pass so each staged file
gets a real verdict from the current builder (cache hits are
near-instant), evict failures to ``build-failed/<name>/`` with their
error and source sha, then run bounded directory rounds for stragglers.

Checkpoints are per-file and pre-resolution, so probes and rounds share
one cache; the decay-table identity is not part of the checkpoint key
because state mapping happens after the cache boundary.

This script must be launched inside the enforced systemd cgroup; it runs
the builder directly (the outer scope enforces limits).

Writes:
  - the built npz + index under build/
  - build-failed/<name>/failure.json per evicted file
  - build/BUILD_RECORD.json: staged/failed census, rounds, artifact shas
"""
import hashlib
import json
import os
import re

import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ND = "/home/connoravila/nuclear-data"
SRC = os.path.join(ND, "tendl-2025-patched", "files", "n")
WORK = os.path.join(ND, "tendl-2025-patched")
STAGE = os.path.join(WORK, "build", "stage")
FAILED = os.path.join(WORK, "build-failed")
NPZ = os.path.join(WORK, "build", "neutron.n.p10.npz")
CACHE = os.path.join(WORK, "cache-build")
RECORD = os.path.join(WORK, "build", "BUILD_RECORD.json")
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
DECAY = os.path.join(ROOT, "actinv-data", "v1.0.0", "decay",
                     "endf-b-viii-0_decay.dat")
DECAY_FB = os.path.join(ND, "jeff-3.3-decay", "bulk", "jeff-3-3_decay.dat")

ROUND_TIMEOUT = 10 * 3600
PROBE_TIMEOUT = 300.0
PROBE_WORKERS = 2          # shards; each runs --workers 1 (200% CPU cap)
MAX_ROUNDS = 40

FAIL_RE = re.compile(r"(n-[A-Za-z]{1,3}\d{2,4}[a-z]?\.\w+)")

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def invoke(src, out, workers, timeout, decay=True):
    """Single bounded build over one source file or the stage dir.

    Probes pass ``decay=False``: every fail-closed check lives in XS
    parsing/processing, and the checkpoint key excludes decay-table
    identity (state mapping happens after the cache boundary), so a
    decay-free probe gives the same verdict and the same checkpoint
    while skipping ~114 MB of decay-table parsing per invocation.
    """
    args = [ACTINV, "build-library", src, out,
            "--format", "tendl", "--projectile", "neutron",
            "--groups", "fispact-709", "--temperature-K", "293.6",
            "--workers", str(workers), "--cache", CACHE]
    if decay:
        args += ["--decay", DECAY, "--decay-fallback", DECAY_FB]
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout, cwd=ROOT)
        return p.returncode, (p.stderr + "\n" + p.stdout).strip()
    except subprocess.TimeoutExpired:
        return "timeout", f"timeout {timeout}s"


def stage_corpus():
    """Hardlink every sealed file not already failed into the stage."""
    os.makedirs(STAGE, exist_ok=True)
    os.makedirs(FAILED, exist_ok=True)
    staged = 0
    for name in sorted(os.listdir(SRC)):
        if not name.startswith("n-"):
            continue
        dst = os.path.join(STAGE, name)
        if os.path.isdir(os.path.join(FAILED, name)):
            continue
        if not os.path.exists(dst):
            os.link(os.path.join(SRC, name), dst)
        staged += 1
    return staged


def cached_sources():
    out = set()
    for side in os.listdir(CACHE):
        if not side.endswith(".json"):
            continue
        try:
            out.add(json.load(open(os.path.join(CACHE, side)))
                    ["source_sha256"])
        except (OSError, KeyError, ValueError):
            pass
    return out


def evict(name, detail):
    src = os.path.join(STAGE, name)
    d = os.path.join(FAILED, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "failure.json"), "w") as f:
        json.dump(detail, f, indent=1)
    if os.path.exists(src):
        os.remove(src)          # hardlink; sealed bytes untouched


PRIOR_LEDGER = os.path.join(ROOT, "results", "p25c_release_build.json")


def probe_pass():
    """Single-file bounded builds for every staged file lacking a
    checkpoint.  Evicts hard failures; timeouts stay staged.  Files that
    failed under the P25C builder are probed first: they reject at
    parse/processing time in seconds, confirming evictions early."""
    have = cached_sources()
    prior_failed = set()
    if os.path.isfile(PRIOR_LEDGER):
        try:
            prior_failed = set(json.load(open(PRIOR_LEDGER))
                               .get("failure_ledger", {}))
        except ValueError:
            pass
    todo = [n for n in sorted(os.listdir(STAGE))
            if sha256(os.path.join(STAGE, n)) not in have]
    todo.sort(key=lambda n: (n not in prior_failed, n))
    print(f"probe: {len(todo)} staged files lack checkpoints "
          f"({sum(1 for n in todo if n in prior_failed)} prior failures "
          f"first)", flush=True)
    rows, lock, done = [], threading.Lock(), [0]

    def work(names, shard):
        out = os.path.join(WORK, "build", f"probe-{shard}.npz")
        for name in names:
            src = os.path.join(STAGE, name)
            code, msg = invoke(src, out, 1, PROBE_TIMEOUT, decay=False)
            if os.path.exists(out):
                os.remove(out)
            if code == "timeout":
                outcome = "probe_timeout"
            elif code == 0:
                outcome = "ok"
            else:
                outcome = "failed"
                evict(name, {"file": name, "probe": True,
                             "source_sha256": sha256(src),
                             "message": msg[-800:]})
            with lock:
                rows.append({"file": name, "outcome": outcome})
                done[0] += 1
                if done[0] % 100 == 0 or done[0] == len(todo):
                    bad = sum(1 for r in rows if r["outcome"] == "failed")
                    print(f"probe {done[0]}/{len(todo)}: {bad} failed",
                          flush=True)

    threads = [threading.Thread(target=work,
                                args=(todo[s::PROBE_WORKERS], s),
                                daemon=True)
               for s in range(PROBE_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return rows


def run_round():
    t0 = time.monotonic()
    code, msg = invoke(STAGE, NPZ, 2, ROUND_TIMEOUT, decay=True)
    return {"exit": code,
            "seconds": round(time.monotonic() - t0, 1),
            "message": msg[-2000:]}


def main():
    os.makedirs(os.path.join(WORK, "build"), exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)
    staged = stage_corpus()
    print(f"staged {staged} files", flush=True)

    probes = probe_pass()
    n_probe_fail = sum(1 for r in probes if r["outcome"] == "failed")

    rounds = []
    while True:
        res = run_round()
        res["round"] = len(rounds) + 1
        rounds.append(res)
        print(f"round {res['round']}: exit {res['exit']} "
              f"({res['seconds']}s)", flush=True)
        if res["exit"] == 0 or res["exit"] == "timeout" \
                or len(rounds) > MAX_ROUNDS:
            break
        m = FAIL_RE.search(res["message"])
        if not m:
            rounds.append({"fatal": res["message"][-1000:]})
            break
        name = m.group(1)
        src = os.path.join(STAGE, name)
        if not os.path.exists(src):
            rounds.append({"fatal": f"failure in unstaged file: {name}"})
            break
        evict(name, {"file": name, "round": res["round"],
                     "source_sha256": sha256(src),
                     "message": res["message"][-800:]})
        print(f"  evicted {name}", flush=True)

    ledger = {}
    for d in sorted(os.listdir(FAILED)):
        f = os.path.join(FAILED, d, "failure.json")
        if os.path.isfile(f):
            ledger[d] = json.load(open(f))
    idx_path = NPZ.replace(".npz", "_index.json")
    idx = json.load(open(idx_path)) if os.path.isfile(idx_path) else None
    record = {
        "schema": "tendl2025-build-record-2",
        "staged_files": len(os.listdir(STAGE)),
        "failed_files": len(ledger),
        "probe_evictions": n_probe_fail,
        "rounds": rounds,
        "failure_ledger": ledger,
        "artifact": {
            "npz": NPZ,
            "npz_sha256": sha256(NPZ) if os.path.isfile(NPZ) else None,
            "index_sha256": (sha256(idx_path)
                             if os.path.isfile(idx_path) else None),
            "index_targets": len(idx["targets"]) if idx else None,
        },
    }
    with open(RECORD, "w") as f:
        json.dump(record, f, indent=1, sort_keys=True)
    print(json.dumps(record["artifact"], indent=1))
    return 0 if record["artifact"]["npz_sha256"] else 1


if __name__ == "__main__":
    sys.exit(main())
