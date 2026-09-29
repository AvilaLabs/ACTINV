#!/usr/bin/env python3
"""P77 verification (protocols/ACTINV-P77_PROTOCOL.md): trace reservoir fix + exact mesh collapse.

    python3 controls/p77_verify.py run      # P75b population with the patched binary
    python3 controls/p77_verify.py mesh     # both mesh profiles + FNS iron with the patched binary
    python3 controls/p77_verify.py check    # verdict -> results/p77_verdict.json
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


p75 = _load("p75", ROOT / "controls" / "p75_linear_response.py")
c75 = _load("check_p75", ROOT / "controls" / "check_p75.py")
c75b = _load("check_p75b", ROOT / "controls" / "check_p75b.py")

WORK = ROOT / "target" / "p77"
NEW_BIN = WORK / "actinv"
OLD_BIN = ROOT / "target" / "p75" / "actinv"
P75B = ROOT / "target" / "p75b"
MESHPROF = ROOT / "target" / "meshprof"
PROTOCOL = ROOT / "protocols" / "ACTINV-P77_PROTOCOL.md"
FROZEN = "dae03e2be5bfee597e6f9f914c85dfc5185ac3bfbc25ac68e957bc4ae2f6d5b4"


def cmd_run():
    p75.WORK, p75.SPECS, p75.RAW = WORK, P75B / "specs", WORK / "raw"
    p75.CHECKPOINT, p75.MANIFEST, p75.BIN = WORK / "runs.jsonl", P75B / "cases.json", NEW_BIN
    p75.cmd_run(None, None)


def timed(cmd):
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc, time.monotonic() - t0


def cmd_mesh():
    out = {}
    for m in ("fe_p21like", "ss316_r2s"):
        spec = MESHPROF / f"{m}.json"
        dest = MESHPROF / f"{m}.p77.ndjson"
        proc, wall = timed([str(NEW_BIN), "mesh", str(spec), str(dest)])
        out[m] = {"returncode": proc.returncode, "wall_s": wall, "stderr_tail": proc.stderr[-500:]}
        print(m, proc.returncode, f"{wall:.2f}s", flush=True)
    fe = ROOT / "examples" / "fns_fe_5min.json"
    for tag, b in (("old", OLD_BIN), ("new", NEW_BIN)):
        proc, wall = timed([str(b), "run", str(fe), str(WORK / f"fns_fe_{tag}.json")])
        out[f"fns_fe_{tag}"] = {"returncode": proc.returncode, "wall_s": wall}
    (WORK / "mesh_runs.json").write_text(json.dumps(out, indent=1))


def cell_steps(path: Path) -> dict:
    cells = {}
    for line in path.read_text().splitlines():
        d = json.loads(line)
        if d.get("record") == "cell":
            cells[d["ordinal"]] = json.dumps(d["result"]["steps"], sort_keys=True)
    return cells


def cmd_check() -> int:
    old = c75b.rows_of(P75B / "runs.jsonl")
    new = c75b.rows_of(WORK / "runs.jsonl")
    cases = {c["id"]: c for c in json.loads((P75B / "cases.json").read_text())["cases"]}
    v = {"protocol": "ACTINV-P77", "inputs": {
        "protocol_sha256": c75.sha(PROTOCOL), "new_binary_sha256": c75.sha(NEW_BIN),
        "old_binary_sha256": c75.sha(OLD_BIN), "new_checkpoint_sha256": c75.sha(WORK / "runs.jsonl"),
        "p75b_checkpoint_sha256": c75.sha(P75B / "runs.jsonl")}}
    failed = [i for i, r in new.items() if r["returncode"] != 0]
    missing = sorted(set(cases) - set(new))
    mesh_runs = json.loads((WORK / "mesh_runs.json").read_text())
    v["G0"] = {"pass": not failed and not missing and v["inputs"]["protocol_sha256"] == FROZEN
               and all(x["returncode"] == 0 for x in mesh_runs.values()),
               "failed": failed[:10], "missing": len(missing)}

    # G1: trace-arm additivity with the patched binary
    p75c = {x["id"]: x for x in json.loads((ROOT / "target/p75/cases.json").read_text())["cases"]}
    table, zero, _ = c75.yield_table(p75c, c75b.rows_of(ROOT / "target/p75/runs.jsonl"))
    R = {cid: c75.Resp.of(new[cid]) for cid in new}
    worst, worst_at, by_amp = 0.0, None, defaultdict(float)
    for mix in p75.MIXTURES:
        comp = p75.MATERIALS[mix]
        for p in ("fns", "mix", "maxwell"):
            for a in (1.0e10, 1.0e13, 1.0e15):
                mid = f"R__{mix}__{p}__{a:.0e}"
                for k, sm in enumerate(R[mid].steps):
                    act, heat = defaultdict(float), 0.0
                    for e, w in comp.items():
                        st = R[f"R__el_{e}__{p}__{a:.0e}"].steps[k]
                        for n, x in st["activity"].items():
                            act[n] += w / 100.0 * x
                        heat += w / 100.0 * st["heat"]
                    m = c75.metrics({"activity": dict(act), "heat": heat, "kappa": 0.0}, sm, table, zero)
                    e = m["e_agg"] or 0.0
                    by_amp[a] = max(by_amp[a], e)
                    if e > worst:
                        worst, worst_at = e, f"{mid} step {sm['index']} ({m['worst_nuclide']})"
    v["G1"] = {"pass": worst <= 1e-6, "max_e_agg": worst, "worst_at": worst_at,
               "max_by_amplitude": {f"{a:.0e}": x for a, x in by_amp.items()}}

    # G2: coupled path bitwise unchanged
    diffs, compared = [], 0
    for cid, c in cases.items():
        if c["arm"] == "R":
            continue
        o, n = old[cid]["data"], new[cid]["data"]
        if c["arm"] == "A" and not (o["mode"] == "coupled" and n["mode"] == "coupled"):
            continue
        compared += 1
        if json.dumps(o["steps"], sort_keys=True) != json.dumps(n["steps"], sort_keys=True):
            diffs.append(cid)
    v["G2"] = {"pass": not diffs and compared > 0, "compared": compared, "differing": diffs[:20]}

    # G3: mesh records bitwise unchanged; speed descriptive
    g3 = {}
    ok = True
    for m in ("fe_p21like", "ss316_r2s"):
        a, b = cell_steps(MESHPROF / f"{m}.out.ndjson"), cell_steps(MESHPROF / f"{m}.p77.ndjson")
        same = a.keys() == b.keys() and all(a[k] == b[k] for k in a)
        ok &= same and len(a) == 60
        old_footer = json.loads((MESHPROF / f"{m}.out.ndjson").read_text().splitlines()[-1])
        new_footer = json.loads((MESHPROF / f"{m}.p77.ndjson").read_text().splitlines()[-1])
        g3[m] = {"cells": len(a), "bitwise_identical_steps": same,
                 "old_cells_per_s": old_footer.get("cells_per_s"), "new_cells_per_s": new_footer.get("cells_per_s"),
                 "speedup": (new_footer.get("cells_per_s") or 0) / (old_footer.get("cells_per_s") or 1)}
    v["G3"] = {"pass": ok, **g3}

    # descriptive: FNS iron old vs new
    fo = json.loads((WORK / "fns_fe_old.json").read_text())
    fn = json.loads((WORK / "fns_fe_new.json").read_text())
    worst_a, worst_h, changed = 0.0, 0.0, set()
    for so, sn in zip(fo["steps"], fn["steps"]):
        ao, an = sum(so["activity_Bq_per_g"].values()), sum(sn["activity_Bq_per_g"].values())
        worst_a = max(worst_a, abs(an - ao) / ao if ao else 0.0)
        ho, hn = so["heat_W_per_g"]["total"], sn["heat_W_per_g"]["total"]
        worst_h = max(worst_h, abs(hn - ho) / ho if ho else 0.0)
        for k in set(so["activity_Bq_per_g"]) | set(sn["activity_Bq_per_g"]):
            if so["activity_Bq_per_g"].get(k) != sn["activity_Bq_per_g"].get(k):
                changed.add(k)
    v["fns_fe_descriptive"] = {"mode_old": fo["mode"], "mode_new": fn["mode"], "max_rel_activity_change": worst_a,
                               "max_rel_heat_change": worst_h, "nuclides_changed": sorted(changed)[:30],
                               "n_changed": len(changed)}
    v["summary"] = {"G0": v["G0"]["pass"], "G1": v["G1"]["pass"], "G2": v["G2"]["pass"], "G3": v["G3"]["pass"]}
    (ROOT / "results" / "p77_verdict.json").write_text(json.dumps(v, indent=1, sort_keys=True))
    print(json.dumps(v["summary"], indent=1))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"run": cmd_run, "mesh": cmd_mesh, "check": cmd_check}
    if cmd not in fn:
        sys.exit(__doc__)
    fn[cmd]()
