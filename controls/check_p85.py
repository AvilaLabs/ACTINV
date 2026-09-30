#!/usr/bin/env python3
"""P85 checker (protocols/ACTINV-P85_PROTOCOL.md): groupwise prepared data after a spectrum-only miss.

    python3 controls/check_p85.py run     # probe sequences, single runs
    python3 controls/check_p85.py check   # verdict -> results/p85_verdict.json
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path.home() / "Documents" / "actinv"
WORK = ROOT / "target" / "p85"
CACHES = WORK / "cachedirs"
REF = {"actinv": ROOT / "target" / "p84" / "ref_actinv", "probe": ROOT / "target" / "p84" / "ref_cache_probe"}
CAND = {"actinv": ROOT / "target" / "release" / "actinv", "probe": ROOT / "target" / "release" / "cache_probe"}
SPECS = MAIN / "target" / "p75b" / "specs"
PROBE_SPECS = ["A__ss316ln__fns__1e+13", "A__eurofer97__maxwell__1e+13", "A__concrete__mix__1e+13"]
GATED = "A__ss316ln__fns__1e+13"
PROTOCOL = ROOT / "protocols" / "ACTINV-P85_PROTOCOL.md"
VERDICT = ROOT / "results" / "p85_verdict.json"
TIMING_KEYS = {"ms", "elapsed_ms", "wall_s", "wall_time_s", "cells_per_s"}
REPEATS = 3
THRESHOLD = 3.0
WORKERS = 3


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def env(cache_dir: Path | None = None) -> dict:
    e = {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
    if cache_dir is not None:
        e["ACTINV_CACHE_DIR"] = str(cache_dir)
    return e


def probe(binaries: dict, spec: str, mode: str) -> dict:
    CACHES.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(tempfile.mkdtemp(prefix="cache-", dir=CACHES))
    try:
        t0 = time.monotonic()
        p = subprocess.run([str(binaries["probe"]), str(SPECS / f"{spec}.json"), mode], cwd=MAIN,
                           capture_output=True, text=True, env=env(cache_dir))
        runs = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        return {"returncode": p.returncode, "wall_s": time.monotonic() - t0, "runs": runs,
                "stderr_tail": p.stderr[-300:]}
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)


def strip_timing(v, parent: str = ""):
    if isinstance(v, dict):
        return {k: strip_timing(x, k) for k, x in v.items()
                if k not in TIMING_KEYS and not k.endswith("_ms")
                and not (parent == "timing" and k.endswith("_s"))}
    if isinstance(v, list):
        return [strip_timing(x, parent) for x in v]
    return v


def run_single(binary: Path, spec: Path) -> list:
    with tempfile.TemporaryDirectory(dir=WORK) as d:
        out = Path(d) / "out.json"
        p = subprocess.run([str(binary), "run", str(spec), str(out)], capture_output=True, text=True, env=env())
        if p.returncode != 0:
            return [p.returncode, p.stderr[-300:]]
        doc = strip_timing(json.loads(out.read_text()))
        return [0, hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()]


def cmd_run() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    log = {"reference": {k: sha(v) for k, v in REF.items()}, "candidate": {k: sha(v) for k, v in CAND.items()},
           "sequences": {}, "timing": [], "single": {}}
    for spec in PROBE_SPECS:
        for tag, binaries in (("ref", REF), ("cand", CAND)):
            for mode in ("cold", "warm"):
                r = probe(binaries, spec, mode)
                log["sequences"][f"{spec}|{tag}|{mode}"] = r
                print(spec, tag, mode, r["returncode"], f"{r['wall_s']:.1f}s", flush=True)
    for i in range(REPEATS):
        for tag, binaries in (("ref", REF), ("cand", CAND)):
            r = probe(binaries, GATED, "warm")
            log["timing"].append({"repeat": i, "tag": tag, **r})
            print("timing", i, tag, r["returncode"], f"{r['wall_s']:.1f}s", flush=True)
    specs = sorted(SPECS.glob("*.json"))

    def both(spec: Path):
        return spec.name, run_single(REF["actinv"], spec), run_single(CAND["actinv"], spec)

    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for i, (name, r, c) in enumerate(ex.map(both, specs)):
            log["single"][name] = {"ref": r, "cand": c}
            if i % 100 == 0:
                print(f"single {i}/{len(specs)} {time.monotonic() - t0:.0f}s", flush=True)
    (WORK / "run_log.json").write_text(json.dumps(log, indent=1, sort_keys=True))


def cmd_check() -> int:
    log = json.loads((WORK / "run_log.json").read_text())
    registered = f"{sha(PROTOCOL)}  protocols/ACTINV-P85_PROTOCOL.md" in (ROOT / "protocols/protocol_hash.txt").read_text()
    blog = (WORK / "build.log").read_text()
    rc = dict(re.findall(r"^(fmt|clippy|test|release) rc=(\d+)$", blog, re.M))
    unit = "collapsed_artifact_equals_groupwise_collapse_bit_for_bit ... ok" in (WORK / "test.txt").read_text()
    g1 = all(rc.get(k) == "0" for k in ("fmt", "clippy", "test", "release")) and unit

    g2 = {}
    for spec in PROBE_SPECS:
        seqs = {key.split("|", 1)[1]: v for key, v in log["sequences"].items() if key.startswith(spec + "|")}
        ok = all(v["returncode"] == 0 and len(v["runs"]) == 10 for v in seqs.values())
        hashes = {k: [r["sha256"] for r in v["runs"]] for k, v in seqs.items()}
        identical = ok and len({tuple(h) for h in hashes.values()}) == 1
        cand_hits = [r["hit"] for r in seqs["cand|warm"]["runs"]] if ok else None
        hits_ok = cand_hits == [False, False] + [True] * 8
        g2[spec] = {"pass": identical and hits_ok, "hashes_identical": identical, "candidate_warm_hits": cand_hits,
                    "reference_warm_hits": [r["hit"] for r in seqs["ref|warm"]["runs"]] if ok else None,
                    "wall_ms": {k: [round(r["wall_ms"], 1) for r in v["runs"]] for k, v in seqs.items()}}

    per_tag = {"ref": [], "cand": []}
    for t in log["timing"]:
        if t["returncode"] == 0 and len(t["runs"]) == 10:
            per_tag[t["tag"]].append(statistics.median(r["wall_ms"] for r in t["runs"][2:]))
    ok = len(per_tag["ref"]) == REPEATS and len(per_tag["cand"]) == REPEATS
    ref_m = statistics.median(per_tag["ref"]) if ok else None
    cand_m = statistics.median(per_tag["cand"]) if ok else None
    speedup = ref_m / cand_m if ok else None
    g5 = {"pass": (speedup or 0) >= THRESHOLD, "threshold": THRESHOLD, "gated": GATED,
          "ref_repeat_medians_ms": per_tag["ref"], "cand_repeat_medians_ms": per_tag["cand"],
          "ref_median_ms": ref_m, "cand_median_ms": cand_m, "speedup": speedup}

    single = log["single"]
    fails = [n for n, v in single.items() if v["ref"][0] != 0 or v["cand"][0] != 0 or v["ref"][1] != v["cand"][1]]
    g3 = {"pass": len(single) == 783 and not fails, "specs": len(single), "differing_or_failed": fails[:20]}

    verdict = {
        "protocol": "ACTINV-P85",
        "inputs": {"protocol_sha256": sha(PROTOCOL), "reference": log["reference"], "candidate": log["candidate"]},
        "G0": {"pass": registered},
        "G1": {"pass": g1, "exit_codes": rc, "unit_test_ok": unit},
        "G2": {"pass": all(v["pass"] for v in g2.values()), "specs": g2},
        "G3": g3,
        "G5": g5,
    }
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"G0": registered, "G1": g1, "G2": {k: v["pass"] for k, v in g2.items()}, "G3": g3["pass"],
                      "G5": g5["pass"], "speedup": speedup and round(speedup, 2),
                      "ref_ms": ref_m and round(ref_m), "cand_ms": cand_m and round(cand_m)}, indent=1))
    return 0


if __name__ == "__main__":
    fn = {"run": cmd_run, "check": cmd_check}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    fn[sys.argv[1]]()
