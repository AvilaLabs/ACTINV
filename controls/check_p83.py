#!/usr/bin/env python3
"""P83 checker (protocols/ACTINV-P83_PROTOCOL.md): verbatim mesh cell result text, multi-thread gate.

    python3 controls/check_p83.py run     # reference vs candidate mesh runs, variants, timing
    python3 controls/check_p83.py check   # verdict -> results/p83_verdict.json

Mesh outputs are compared as bytes except the footer, compared as JSON without wall_time_s/cells_per_s.
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path.home() / "Documents" / "actinv"
WORK = ROOT / "target" / "p83"
REF = WORK / "ref_actinv"
CAND = ROOT / "target" / "release" / "actinv"
MESH = ["fe_coupled", "fe_p21like", "ss316_r2s"]
MESH_DIR = MAIN / "target" / "meshprof"
PROTOCOL = ROOT / "protocols" / "ACTINV-P83_PROTOCOL.md"
VERDICT = ROOT / "results" / "p83_verdict.json"
ENV = {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
FOOTER_TIMING = ("wall_time_s", "cells_per_s")
SPEED_REPEATS = 3
RESUME_CELLS = 20
GATED = ("ss316_r2s", 3)
THREADS = (1, 3)
THRESHOLD = 1.3


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run_mesh(binary: Path, spec: Path, out: Path) -> dict:
    t0 = time.monotonic()
    p = subprocess.run([str(binary), "mesh", str(spec), str(out)], cwd=MAIN, capture_output=True, text=True,
                       env=ENV)
    return {"returncode": p.returncode, "wall_s": time.monotonic() - t0, "stderr_tail": p.stderr[-300:]}


def digest(path: Path) -> dict:
    """sha256 of every line but the last (the footer), plus the footer without timing keys."""
    h = hashlib.sha256()
    lines = 0
    last = None
    with path.open("rb") as f:
        for line in f:
            if last is not None:
                h.update(last)
                lines += 1
            last = line
    footer = json.loads(last)
    for key in FOOTER_TIMING:
        footer.pop(key, None)
    return {"body_sha256": h.hexdigest(), "body_lines": lines, "footer": footer}


def spec_variant(name: str, change: dict, profile: str = "fe_coupled") -> Path:
    base = json.loads((MESH_DIR / f"{profile}.json").read_text())
    path = WORK / f"{profile}.{name}.json"
    path.write_text(json.dumps({**base, **change}))
    return path


def compare_run(binary: Path, spec: Path, out: Path) -> dict:
    r = run_mesh(binary, spec, out)
    if r["returncode"] == 0:
        r["digest"] = digest(out)
    out.unlink(missing_ok=True)
    return r


def cmd_run() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    log = {"reference_sha256": sha(REF), "candidate_sha256": sha(CAND), "mesh": {}, "variants": {},
           "speed": {}}
    for c in MESH:
        for threads in THREADS:
            spec = spec_variant(f"t{threads}", {"threads": threads}, c)
            for tag, b in (("ref", REF), ("cand", CAND)):
                r = compare_run(b, spec, WORK / f"mesh_{c}_t{threads}.{tag}.ndjson")
                log["mesh"].setdefault(f"{c}@t{threads}", {})[tag] = r
                print(c, threads, tag, r["returncode"], f"{r['wall_s']:.1f}s", flush=True)

    group = spec_variant("group", {"group_workloads": True})
    log["variants"]["group_workloads"] = {
        tag: compare_run(b, group, WORK / f"variant_group.{tag}.ndjson") for tag, b in (("ref", REF), ("cand", CAND))}

    # Resume: a full candidate run cut back to header + RESUME_CELLS cell records, then resumed; the
    # reference runs the same resume spec in one pass on a fresh output file.
    resume = spec_variant("resume", {"resume": True})
    out = WORK / "variant_resume.cand.ndjson"
    first = run_mesh(CAND, MESH_DIR / "fe_coupled.json", out)
    kept = out.read_bytes().splitlines(keepends=True)[: 1 + RESUME_CELLS]
    out.write_bytes(b"".join(kept))
    cand = compare_run(CAND, resume, out)
    cand["first_returncode"] = first["returncode"]
    cand["kept_lines"] = len(kept)
    ref_out = WORK / "variant_resume.ref.ndjson"
    ref_out.unlink(missing_ok=True)
    log["variants"]["resume"] = {"ref": compare_run(REF, resume, ref_out), "cand": cand}
    print("variants", {k: {t: x["returncode"] for t, x in v.items()} for k, v in log["variants"].items()},
          flush=True)

    for c in MESH:
        for threads in THREADS:
            spec = WORK / f"{c}.t{threads}.json"
            key = f"{c}@t{threads}"
            for _ in range(SPEED_REPEATS):
                for tag, b in (("ref", REF), ("cand", CAND)):
                    out = WORK / f"speed_{c}.{tag}.ndjson"
                    r = run_mesh(b, spec, out)
                    out.unlink(missing_ok=True)
                    log["speed"].setdefault(key, {}).setdefault(tag, []).append(
                        r["wall_s"] if r["returncode"] == 0 else None)
            print("speed", key, log["speed"][key], flush=True)
    (WORK / "run_log.json").write_text(json.dumps(log, indent=1, sort_keys=True))


def cmd_check() -> int:
    log = json.loads((WORK / "run_log.json").read_text())
    registered = f"{sha(PROTOCOL)}  protocols/ACTINV-P83_PROTOCOL.md" in (ROOT / "protocols/protocol_hash.txt").read_text()
    blog = (WORK / "build.log").read_text()
    rc = dict(re.findall(r"^(fmt|clippy|test|release) rc=(\d+)$", blog, re.M))
    unit = "spliced_result_text_equals_round_tripped_record_byte_for_byte ... ok" in (WORK / "test.txt").read_text()
    g1 = all(rc.get(k) == "0" for k in ("fmt", "clippy", "test", "release")) and unit

    profiles = {}
    for key, m in log["mesh"].items():
        ok = m["ref"]["returncode"] == 0 and m["cand"]["returncode"] == 0
        profiles[key] = {"pass": ok and m["ref"]["digest"] == m["cand"]["digest"],
                         "ref": m["ref"].get("digest"), "cand": m["cand"].get("digest")}
    variants = {}
    for name, v in log["variants"].items():
        ok = v["ref"]["returncode"] == 0 and v["cand"]["returncode"] == 0
        variants[name] = {"pass": ok and v["ref"]["digest"] == v["cand"]["digest"],
                          "ref": v["ref"].get("digest"), "cand": v["cand"].get("digest"),
                          **({"first_returncode": v["cand"]["first_returncode"],
                              "kept_lines": v["cand"]["kept_lines"]} if name == "resume" else {})}
    g2 = {"pass": len(profiles) == len(MESH) * len(THREADS) and all(p["pass"] for p in profiles.values())
          and all(v["pass"] for v in variants.values()), "profiles": profiles, "variants": variants}

    speed = {}
    for key, runs in log["speed"].items():
        ok = None not in runs["ref"] and None not in runs["cand"]
        rm = statistics.median(runs["ref"]) if ok else None
        cm = statistics.median(runs["cand"]) if ok else None
        speed[key] = {"ref_wall_s": runs["ref"], "cand_wall_s": runs["cand"], "ref_median_s": rm,
                      "cand_median_s": cm, "speedup": rm / cm if ok else None}
    gated = f"{GATED[0]}@t{GATED[1]}"
    g5 = {"pass": (speed.get(gated, {}).get("speedup") or 0) >= THRESHOLD, "threshold": THRESHOLD,
          "gated": gated, "measures": speed}

    verdict = {
        "protocol": "ACTINV-P83",
        "inputs": {"protocol_sha256": sha(PROTOCOL), "reference_sha256": log["reference_sha256"],
                   "candidate_sha256": log["candidate_sha256"]},
        "G0": {"pass": registered},
        "G1": {"pass": g1, "exit_codes": rc, "unit_test_ok": unit},
        "G2": g2,
        "G5": g5,
    }
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"G0": registered, "G1": g1, "G2": {c: p["pass"] for c, p in profiles.items()},
                      "G2_variants": {n: v["pass"] for n, v in variants.items()}, "G5": g5["pass"],
                      "speedup_median": {c: v["speedup"] and round(v["speedup"], 2) for c, v in speed.items()}},
                     indent=1))
    return 0


if __name__ == "__main__":
    fn = {"run": cmd_run, "check": cmd_check}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    fn[sys.argv[1]]()
