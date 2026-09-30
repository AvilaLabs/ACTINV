#!/usr/bin/env python3
"""P82 checker (protocols/ACTINV-P82_PROTOCOL.md): mesh cell records written from the result text.

    python3 controls/check_p82.py run     # reference vs candidate mesh runs, variants, timing
    python3 controls/check_p82.py check   # verdict -> results/p82_verdict.json

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
WORK = ROOT / "target" / "p82"
REF = WORK / "ref_actinv"
CAND = ROOT / "target" / "release" / "actinv"
MESH = ["fe_coupled", "fe_p21like", "ss316_r2s"]
MESH_DIR = MAIN / "target" / "meshprof"
PROTOCOL = ROOT / "protocols" / "ACTINV-P82_PROTOCOL.md"
VERDICT = ROOT / "results" / "p82_verdict.json"
ENV = {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
FOOTER_TIMING = ("wall_time_s", "cells_per_s")
SPEED_REPEATS = 3
RESUME_CELLS = 20
GATED = "ss316_r2s"
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


def spec_variant(name: str, change: dict) -> Path:
    base = json.loads((MESH_DIR / "fe_coupled.json").read_text())
    path = WORK / f"fe_coupled.{name}.json"
    path.write_text(json.dumps({**base, **change}))
    return path


def cmd_run() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    log = {"reference_sha256": sha(REF), "candidate_sha256": sha(CAND), "mesh": {}, "variants": {},
           "speed": {}}
    for c in MESH:
        for tag, b in (("ref", REF), ("cand", CAND)):
            out = WORK / f"mesh_{c}.{tag}.ndjson"
            r = run_mesh(b, MESH_DIR / f"{c}.json", out)
            if r["returncode"] == 0:
                r["digest"] = digest(out)
            out.unlink(missing_ok=True)
            log["mesh"].setdefault(c, {})[tag] = r
            print(c, tag, r["returncode"], f"{r['wall_s']:.1f}s", flush=True)

    out = WORK / "variant_group.ndjson"
    r = run_mesh(CAND, spec_variant("group", {"group_workloads": True}), out)
    if r["returncode"] == 0:
        r["digest"] = digest(out)
    out.unlink(missing_ok=True)
    log["variants"]["group_workloads"] = r

    # Resume: a full candidate run cut back to header + RESUME_CELLS cell records, then resumed.
    out = WORK / "variant_resume.ndjson"
    first = run_mesh(CAND, MESH_DIR / "fe_coupled.json", out)
    kept = out.read_bytes().splitlines(keepends=True)[: 1 + RESUME_CELLS]
    out.write_bytes(b"".join(kept))
    r = run_mesh(CAND, spec_variant("resume", {"resume": True}), out)
    r["first_returncode"] = first["returncode"]
    r["kept_lines"] = len(kept)
    if r["returncode"] == 0:
        r["digest"] = digest(out)
    out.unlink(missing_ok=True)
    log["variants"]["resume"] = r
    print("variants", {k: v["returncode"] for k, v in log["variants"].items()}, flush=True)

    for c in MESH:
        for _ in range(SPEED_REPEATS):
            for tag, b in (("ref", REF), ("cand", CAND)):
                out = WORK / f"speed_{c}.{tag}.ndjson"
                r = run_mesh(b, MESH_DIR / f"{c}.json", out)
                out.unlink(missing_ok=True)
                log["speed"].setdefault(c, {}).setdefault(tag, []).append(
                    r["wall_s"] if r["returncode"] == 0 else None)
        print("speed", c, log["speed"][c], flush=True)
    (WORK / "run_log.json").write_text(json.dumps(log, indent=1, sort_keys=True))


def cmd_check() -> int:
    log = json.loads((WORK / "run_log.json").read_text())
    registered = f"{sha(PROTOCOL)}  protocols/ACTINV-P82_PROTOCOL.md" in (ROOT / "protocols/protocol_hash.txt").read_text()
    blog = (WORK / "build.log").read_text()
    rc = dict(re.findall(r"^(fmt|clippy|test|release) rc=(\d+)$", blog, re.M))
    unit = "spliced_result_text_equals_round_tripped_record_byte_for_byte ... ok" in (WORK / "test.txt").read_text()
    g1 = all(rc.get(k) == "0" for k in ("fmt", "clippy", "test", "release")) and unit

    profiles = {}
    for c in MESH:
        m = log["mesh"][c]
        ok = m["ref"]["returncode"] == 0 and m["cand"]["returncode"] == 0
        profiles[c] = {"pass": ok and m["ref"]["digest"] == m["cand"]["digest"],
                       "ref": m["ref"].get("digest"), "cand": m["cand"].get("digest")}
    ref_fe = log["mesh"]["fe_coupled"]["ref"].get("digest")
    variants = {}
    for name, v in log["variants"].items():
        variants[name] = {"pass": v["returncode"] == 0 and v.get("digest") == ref_fe,
                          "returncode": v["returncode"], "digest": v.get("digest"),
                          **({"first_returncode": v["first_returncode"], "kept_lines": v["kept_lines"]}
                             if name == "resume" else {})}
    g2 = {"pass": all(p["pass"] for p in profiles.values()) and all(v["pass"] for v in variants.values()),
          "profiles": profiles, "variants": variants}

    speed = {}
    for c, runs in log["speed"].items():
        ok = None not in runs["ref"] and None not in runs["cand"]
        rm = statistics.median(runs["ref"]) if ok else None
        cm = statistics.median(runs["cand"]) if ok else None
        speed[c] = {"ref_wall_s": runs["ref"], "cand_wall_s": runs["cand"], "ref_median_s": rm,
                    "cand_median_s": cm, "speedup": rm / cm if ok else None}
    g5 = {"pass": (speed.get(GATED, {}).get("speedup") or 0) >= THRESHOLD, "threshold": THRESHOLD,
          "gated_profile": GATED, "profiles": speed}

    verdict = {
        "protocol": "ACTINV-P82",
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
