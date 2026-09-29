#!/usr/bin/env python3
"""P75b checker: composition superposition verdicts (protocols/ACTINV-P75B_PROTOCOL.md).

    python3 controls/check_p75b.py [--out results/p75b_verdict.json]
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


c75 = _load("check_p75", ROOT / "controls" / "check_p75.py")
r75 = _load("p75", ROOT / "controls" / "p75_linear_response.py")

WORK = ROOT / "target" / "p75b"
MANIFEST = WORK / "cases.json"
CHECKPOINT = WORK / "runs.jsonl"
P75_CHECKPOINT = ROOT / "target" / "p75" / "runs.jsonl"
P75_MANIFEST = ROOT / "target" / "p75" / "cases.json"
BINARY = ROOT / "target" / "p75" / "actinv"
PROTOCOL = ROOT / "protocols" / "ACTINV-P75B_PROTOCOL.md"
FROZEN_MANIFEST = "d872e559bd5fef143d0dcae1d33b2ef27fb211834cf7ab20eec378a0c045393c"
FROZEN_PROTOCOL = "4833fc0679c7b18bf19db44bb112153fb341a78536e8458cf3c9577ff2db6a70"
TOL = 1e-6


def rows_of(path):
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r["id"] in out:
                raise SystemExit(f"duplicate row {r['id']}")
            out[r["id"]] = r
    return out


def main(out_path: Path) -> int:
    cases = {c["id"]: c for c in json.loads(MANIFEST.read_text())["cases"]}
    rows = rows_of(CHECKPOINT)
    v = {"protocol": "ACTINV-P75B", "inputs": {
        "protocol_sha256": c75.sha(PROTOCOL), "manifest_sha256": c75.sha(MANIFEST),
        "checkpoint_sha256": c75.sha(CHECKPOINT), "binary_sha256": c75.sha(BINARY),
        "p75_yield_checkpoint_sha256": c75.sha(P75_CHECKPOINT)}}
    missing = sorted(set(cases) - set(rows))
    failed = sorted(i for i, r in rows.items() if r["returncode"] != 0)
    g0 = {"protocol_hash_matches": v["inputs"]["protocol_sha256"] == FROZEN_PROTOCOL,
          "manifest_hash_matches": v["inputs"]["manifest_sha256"] == FROZEN_MANIFEST,
          "runs_expected": len(cases), "runs_missing": len(missing), "runs_failed": failed[:20],
          "wall_s_total": c75.fmt(sum(r["wall_s"] for r in rows.values()))}
    g0["pass"] = not missing and not failed and g0["protocol_hash_matches"] and g0["manifest_hash_matches"]
    v["G0"] = g0
    if not g0["pass"]:
        out_path.write_text(json.dumps(v, indent=1, sort_keys=True))
        print(json.dumps(g0, indent=1))
        return 1

    p75_cases = {c["id"]: c for c in json.loads(P75_MANIFEST.read_text())["cases"]}
    table, zero, _ = c75.yield_table(p75_cases, rows_of(P75_CHECKPOINT))
    R = {cid: c75.Resp.of(rows[cid]) for cid in rows}

    arms = {}
    unavailable = 0
    for arm in ("C", "R", "A"):
        worst_rows, worst, worst_nuc = [], 0.0, 0.0
        mode_mismatch = 0
        for mix in r75.MIXTURES:
            comp = r75.MATERIALS[mix]
            for p in ("fns", "mix", "maxwell"):
                for a in (1.0e10, 1.0e13, 1.0e15):
                    mid = f"{arm}__{mix}__{p}__{a:.0e}"
                    terms = [(w / 100.0, f"{arm}__el_{e}__{p}__{a:.0e}") for e, w in comp.items()]
                    modes = {rows[t]["data"]["mode"] for _, t in terms}
                    mix_mode = rows[mid]["data"]["mode"]
                    if modes != {mix_mode}:
                        mode_mismatch += 1
                    for k, sm in enumerate(R[mid].steps):
                        act = defaultdict(float)
                        heat = 0.0
                        for w, t in terms:
                            st = R[t].steps[k]
                            assert st["index"] == sm["index"]
                            for n, x in st["activity"].items():
                                act[n] += w * x
                            heat += w * st["heat"]
                        pred = {"activity": dict(act), "heat": heat, "kappa": 0.0}
                        m = c75.metrics(pred, sm, table, zero)
                        unavailable += bool(m.get("photon_unavailable"))
                        e = m["e_agg"] or 0.0
                        nd = c75.per_nuclide_dev(pred, sm)
                        worst, worst_nuc = max(worst, e), max(worst_nuc, nd)
                        worst_rows.append({"mixture": mix, "spectrum": p, "amplitude": a, "step": sm["index"],
                                           "t_s": sm["t_s"], "e_A": c75.fmt(m["e_A"]), "e_H": c75.fmt(m["e_H"]),
                                           "e_P": c75.fmt(m["e_P"]), "e_agg": c75.fmt(e),
                                           "per_nuclide_max": c75.fmt(nd), "worst_nuclide": m["worst_nuclide"],
                                           "mix_mode": mix_mode, "element_modes": sorted(modes)})
        arms[arm] = {"max_e_agg": c75.fmt(worst), "max_per_nuclide_dev": c75.fmt(worst_nuc),
                     "n_steps": len(worst_rows), "n_over_tol": sum((r["e_agg"] or 0) > TOL for r in worst_rows),
                     "mode_mismatch_mixtures": mode_mismatch,
                     "worst_rows": sorted(worst_rows, key=lambda r: -(r["e_agg"] or 0))[:12]}
    v["arms"] = arms
    v["photon_steps_unavailable"] = unavailable
    v["G1_coupled_pass"] = arms["C"]["max_e_agg"] <= TOL
    v["G2_trace_pass"] = arms["R"]["max_e_agg"] <= TOL
    v["summary"] = {"G0": g0["pass"], "G1_coupled": v["G1_coupled_pass"], "G2_trace": v["G2_trace_pass"],
                    "A_auto_max_e_agg_descriptive": arms["A"]["max_e_agg"]}
    out_path.write_text(json.dumps(v, indent=1, sort_keys=True))
    print(json.dumps(v["summary"], indent=1))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else ROOT / "results" / "p75b_verdict.json"
    sys.exit(main(out))
