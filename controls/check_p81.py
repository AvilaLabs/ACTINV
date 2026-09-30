#!/usr/bin/env python3
"""P81 checker (protocols/ACTINV-P81_PROTOCOL.md): chunk-batched mesh collapse must be bitwise exact and faster.

    python3 controls/check_p81.py run     # reference vs candidate on mesh profiles + P75b specs
    python3 controls/check_p81.py check   # verdict -> results/p81_verdict.json

Outputs are compared as parsed JSON after removing timing keys; nothing else is normalised.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path.home() / "Documents" / "actinv"
WORK = ROOT / "target" / "p81"
REF = WORK / "ref_actinv"
CAND = ROOT / "target" / "release" / "actinv"
MESH = ["fe_coupled", "fe_p21like", "ss316_r2s"]
MESH_DIR = MAIN / "target" / "meshprof"
P75B = MAIN / "target" / "p75b" / "specs"
PROTOCOL = ROOT / "protocols" / "ACTINV-P81_PROTOCOL.md"
TIMING_KEYS = {"ms", "elapsed_ms", "wall_s", "wall_time_s", "cells_per_s"}
WORKERS = 3
ENV = {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def strip_timing(v, parent: str = ""):
    if isinstance(v, dict):
        return {k: strip_timing(x, k) for k, x in v.items()
                if k not in TIMING_KEYS and not k.endswith("_ms")
                and not (parent == "timing" and k.endswith("_s"))}
    if isinstance(v, list):
        return [strip_timing(x, parent) for x in v]
    return v


def run_spec(binary: Path, spec: Path) -> tuple[int, str | None, float]:
    with tempfile.TemporaryDirectory(dir=WORK) as d:
        out = Path(d) / "out.json"
        t0 = time.monotonic()
        p = subprocess.run([str(binary), "run", str(spec), str(out)], capture_output=True, text=True, env=ENV)
        wall = time.monotonic() - t0
        if p.returncode != 0:
            return p.returncode, p.stderr[-500:], wall
        doc = strip_timing(json.loads(out.read_text()))
        return 0, hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest(), wall


def run_mesh(binary: Path, spec: Path, out: Path) -> dict:
    t0 = time.monotonic()
    p = subprocess.run([str(binary), "mesh", str(spec), str(out)], cwd=MAIN, capture_output=True, text=True,
                       env=ENV)
    return {"returncode": p.returncode, "wall_s": time.monotonic() - t0, "stderr_tail": p.stderr[-300:]}


VARIANTS = {"threads3": {"threads": 3}, "chunk1": {"chunk_cells": 1}}
SPEED_REPEATS = 3


def cmd_run() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    log = {"reference_sha256": sha(REF), "candidate_sha256": sha(CAND), "mesh": {}, "variants": {}, "speed": {},
           "single": {}}
    for c in MESH:
        for tag, b in (("ref", REF), ("cand", CAND)):
            log["mesh"].setdefault(c, {})[tag] = r = run_mesh(b, MESH_DIR / f"{c}.json", WORK / f"mesh_{c}.{tag}.ndjson")
            print(c, tag, r["returncode"], f"{r['wall_s']:.1f}s", flush=True)
    base = json.loads((MESH_DIR / "fe_coupled.json").read_text())
    for name, change in VARIANTS.items():
        spec = WORK / f"fe_coupled.{name}.json"
        spec.write_text(json.dumps({**base, **change}))
        log["variants"][name] = r = run_mesh(CAND, spec, WORK / f"mesh_fe_coupled.{name}.ndjson")
        print("variant", name, r["returncode"], f"{r['wall_s']:.1f}s", flush=True)
    for c in MESH:
        for i in range(SPEED_REPEATS):
            for tag, b in (("ref", REF), ("cand", CAND)):
                r = run_mesh(b, MESH_DIR / f"{c}.json", WORK / f"speed_{c}.{tag}.ndjson")
                log["speed"].setdefault(c, {}).setdefault(tag, []).append(r["wall_s"] if r["returncode"] == 0 else None)
        print("speed", c, log["speed"][c], flush=True)
    specs = sorted(P75B.glob("*.json"))
    def both(spec: Path):
        return spec.name, run_spec(REF, spec), run_spec(CAND, spec)
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for i, (name, r, c) in enumerate(ex.map(both, specs)):
            log["single"][name] = {"ref": list(r), "cand": list(c)}
            if i % 100 == 0:
                print(f"single {i}/{len(specs)} {time.monotonic() - t0:.0f}s", flush=True)
    (WORK / "run_log.json").write_text(json.dumps(log, indent=1, sort_keys=True))


def mesh_records(path: Path) -> list:
    return [strip_timing(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def cmd_check() -> int:
    log = json.loads((WORK / "run_log.json").read_text())
    registered = f"{sha(PROTOCOL)}  protocols/ACTINV-P81_PROTOCOL.md" in (ROOT / "protocols/protocol_hash.txt").read_text()
    blog = (WORK / "build.log").read_text()
    rc = dict(re.findall(r"^(fmt|clippy|test|release) rc=(\d+)$", blog, re.M))
    tlog = (WORK / "test.txt").read_text()
    unit = "batched_collapse_equals_single_cell_collapse_bit_for_bit ... ok" in tlog
    g1 = all(rc.get(k) == "0" for k in ("fmt", "clippy", "test", "release")) and unit

    g2, speed = {}, {}
    for c in MESH:
        m = log["mesh"][c]
        if m["ref"]["returncode"] or m["cand"]["returncode"]:
            g2[c] = {"pass": False, "reason": "run failed"}
            continue
        a, b = mesh_records(WORK / f"mesh_{c}.ref.ndjson"), mesh_records(WORK / f"mesh_{c}.cand.ndjson")
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        g2[c] = {"pass": len(a) == len(b) and not diff, "records": len(a), "differing_records": diff[:10]}
        speed[c] = {"ref_wall_s": m["ref"]["wall_s"], "cand_wall_s": m["cand"]["wall_s"],
                    "speedup": m["ref"]["wall_s"] / m["cand"]["wall_s"]}

    variants = {}
    ref_records = mesh_records(WORK / "mesh_fe_coupled.ref.ndjson")
    for name in VARIANTS:
        v = log["variants"][name]
        records = mesh_records(WORK / f"mesh_fe_coupled.{name}.ndjson") if v["returncode"] == 0 else []
        diff = [i for i, (x, y) in enumerate(zip(ref_records, records)) if x != y]
        variants[name] = {"pass": v["returncode"] == 0 and len(records) == len(ref_records) and not diff,
                          "records": len(records), "differing_records": diff[:10]}

    import statistics
    speed5 = {}
    for c, runs in log["speed"].items():
        ok = None not in runs["ref"] and None not in runs["cand"]
        ref_m = statistics.median(runs["ref"]) if ok else None
        cand_m = statistics.median(runs["cand"]) if ok else None
        speed5[c] = {"ref_wall_s": runs["ref"], "cand_wall_s": runs["cand"], "ref_median_s": ref_m,
                     "cand_median_s": cand_m, "speedup": ref_m / cand_m if ok else None}
    g5 = {"pass": (speed5.get("fe_coupled", {}).get("speedup") or 0) >= 1.3, "threshold": 1.3,
          "gated_profile": "fe_coupled", "profiles": speed5}

    single = log["single"]
    fails = [n for n, v in single.items() if v["ref"][0] != 0 or v["cand"][0] != 0 or v["ref"][1] != v["cand"][1]]
    ref_t = sum(v["ref"][2] for v in single.values())
    cand_t = sum(v["cand"][2] for v in single.values())
    g3 = {"pass": bool(single) and not fails, "specs": len(single), "differing_or_failed": fails[:20],
          "ref_total_wall_s": ref_t, "cand_total_wall_s": cand_t}

    verdict = {
        "protocol": "ACTINV-P81",
        "inputs": {"protocol_sha256": sha(PROTOCOL), "reference_sha256": log["reference_sha256"],
                   "candidate_sha256": log["candidate_sha256"]},
        "G0": {"pass": registered},
        "G1": {"pass": g1, "exit_codes": rc, "unit_test_ok": unit},
        "G2": {"profiles": g2, "variants": variants,
               "pass": all(v["pass"] for v in g2.values()) and all(v["pass"] for v in variants.values())},
        "G3": g3,
        "G5": g5,
        "G5_single_pass_timing": {"mesh": speed},
    }
    (ROOT / "results" / "p81_verdict.json").write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"G0": registered, "G1": g1, "G2": {c: v["pass"] for c, v in g2.items()},
                      "G2_variants": {n: v["pass"] for n, v in variants.items()}, "G3": g3["pass"],
                      "G5": g5["pass"], "speedup_median": {c: v["speedup"] and round(v["speedup"], 2)
                                                          for c, v in speed5.items()},
                      "single_ref_s": round(ref_t), "single_cand_s": round(cand_t)}, indent=1))
    return 0


if __name__ == "__main__":
    fn = {"run": cmd_run, "check": cmd_check}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    fn[sys.argv[1]]()
