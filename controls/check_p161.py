#!/usr/bin/env python3
"""P161 checker (protocols/ACTINV-P161_PROTOCOL.md): pathway closure ignores noise-level inventories.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P161_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  External: fmt, check, clippy and workspace tests, recorded with --g1-log; the log must end with "G1: PASS" and
      name the two P161 unit tests and the GUI and report wording tests (G5).
  G2  Every corpus case gives identical reference and candidate results apart from `pathway_closure` and the timing
      keys `ms`, `elapsed_ms`, `wall_time_s`, `cells_per_s` (any depth).
  G3  The candidate's closure is at most the reference's, and within 1e-5 of this script's recomputation from the
      candidate's listed chains over nuclides above 1e-12 of each step's largest population.
  G4  The candidate's closure is at most 1e-5 on every corpus case.
  G6  External (CI on the pushed evidence commit).

Corpus: every examples/*.json the reference runs with exit 0 from --cwd, and the quick start with cram_order 48.
Usage: check_p161.py --reference BIN --candidate BIN --g1-log LOG [--cwd DIR] [--work DIR]
       check_p161.py --selftest
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P161_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p161_closure.json"
VOLATILE = {"ms", "elapsed_ms", "wall_time_s", "cells_per_s"}
NOISE = 1e-12
G3_TOL = 1e-5
G4_MAX = 1e-5
G1_TESTS = [
    "p161_a_noise_level_residue_does_not_raise_closure",
    "p161_gaps_above_the_noise_cut_are_still_reported",
    "p140_ps_f4_trace_decay_fed_product_is_reported_or_flagged",
    "coverage_is_the_selected_nuclides_own_and_closure_is_whole_result",
    "a_closure_gap_is_a_whole_result_caveat",
]
TIMEOUT = 1800


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def strip(node, drop_closure=True):
    """The result without timing keys (any depth) and, at the top level, without `pathway_closure`."""
    def walk(n):
        if isinstance(n, dict):
            return {k: walk(v) for k, v in n.items() if k not in VOLATILE}
        if isinstance(n, list):
            return [walk(v) for v in n]
        return n
    out = walk(node)
    if drop_closure and isinstance(out, dict):
        out.pop("pathway_closure", None)
    return out


def first_difference(a, b, where=""):
    if type(a) is not type(b):
        return f"{where or '/'}: type {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{where}/{k}: present in one result only"
            d = first_difference(a[k], b[k], f"{where}/{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{where}: length {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_difference(x, y, f"{where}/{i}")
            if d:
                return d
        return None
    return None if a == b else f"{where or '/'}: {a!r} vs {b!r}"


def recompute(result):
    """Largest gap between listed chains and population above 1e-12 of each step's largest; where it occurs."""
    gap, where = 0.0, None
    for index, (step, per) in enumerate(zip(result.get("steps", []), result.get("pathways") or [])):
        inventory = step.get("inventory", [])
        largest = max((row["atoms_per_g"] for row in inventory), default=0.0)
        for row in inventory:
            population = row["atoms_per_g"]
            if population <= NOISE * largest:
                continue
            chains = per.get(row["nuclide"])
            value = 1.0 if chains is None else abs(sum(c["atoms_per_g"] for c in chains) - population) / population
            if value > gap:
                gap, where = value, {"step": index, "nuclide": row["nuclide"], "atoms_per_g": population,
                                     "fraction_of_largest": population / largest}
    return gap, where


def run(binary, spec, out, cwd):
    if out.exists():
        out.unlink()
    p = subprocess.run([str(binary), "run", str(spec), str(out)], cwd=cwd, capture_output=True, timeout=TIMEOUT)
    return p.returncode, p.stderr.decode(errors="replace")[-400:]


def gate_g0():
    lines = HASH_LOG.read_text(encoding="utf-8").splitlines()
    paths = [PROTOCOL] + sorted((ROOT / "protocols").glob("ACTINV-P161_AMENDMENT_*.md"))
    out, ok = {}, True
    for path in paths:
        digest = sha256_file(path)
        hit = any(ln.startswith(digest) and ln.split()[-1] == f"protocols/{path.name}" for ln in lines)
        out[path.name] = {"sha256": digest, "in_protocol_hash_txt": hit}
        ok = ok and hit
    out["pass"] = ok
    return out


def gate_g1(log):
    text = Path(log).read_text(errors="replace") if log and Path(log).is_file() else ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    missing = [t for t in G1_TESTS if f"{t} ... ok" not in text]
    ok = bool(lines) and lines[-1].strip() == "G1: PASS" and not missing and "FAILED" not in text
    return {"log": str(log), "sha256": sha256_file(log) if text else None, "missing_tests": missing, "pass": ok}


def corpus(work):
    cases = [(p.stem, p) for p in sorted((ROOT / "examples").glob("*.json"))]
    quick = json.loads((ROOT / "examples" / "fns_fe_5min.json").read_text())
    quick.setdefault("options", {})["cram_order"] = 48
    variant = work / "fns_fe_5min_cram48.json"
    variant.write_text(json.dumps(quick))
    return cases + [("fns_fe_5min_cram48", variant)]


def evaluate(reference, candidate, cwd, work):
    cases, skipped = {}, {}
    for name, spec in corpus(work):
        ref_out, cand_out = work / f"{name}.reference.json", work / f"{name}.candidate.json"
        code, err = run(reference, spec, ref_out, cwd)
        if code != 0:
            skipped[name] = f"reference exit {code}: {err.strip()[-160:]}"
            continue
        ccode, cerr = run(candidate, spec, cand_out, cwd)
        case = {"spec": str(spec.relative_to(ROOT)) if spec.is_relative_to(ROOT) else spec.name,
                "spec_sha256": sha256_file(spec), "candidate_exit": ccode}
        if ccode != 0:
            case.update({"stderr_tail": cerr, "g2": False, "g3": False, "g4": False})
            cases[name] = case
            continue
        ref, cand = json.loads(ref_out.read_text()), json.loads(cand_out.read_text())
        diff = first_difference(strip(ref), strip(cand))
        rc, cc = ref.get("pathway_closure"), cand.get("pathway_closure")
        mine, where = recompute(cand)
        case.update({
            "mode": cand.get("mode"), "steps": len(cand.get("steps", [])), "listed_steps": len(cand.get("pathways") or []),
            "reference_closure": rc, "candidate_closure": cc, "recomputed": mine, "recomputed_at": where,
            "reference_recomputed_at_any_level": recompute_any(ref),
            "g2_first_difference": diff, "g2": diff is None,
            "g3": isinstance(rc, (int, float)) and isinstance(cc, (int, float)) and cc <= rc and abs(cc - mine) <= G3_TOL,
            "g4": isinstance(cc, (int, float)) and cc <= G4_MAX,
        })
        cases[name] = case
    return cases, skipped


def recompute_any(result):
    """The old definition's argmax (every population above zero), to record what the reference's value came from."""
    gap, where = 0.0, None
    for index, (step, per) in enumerate(zip(result.get("steps", []), result.get("pathways") or [])):
        for row in step.get("inventory", []):
            population = row["atoms_per_g"]
            chains = per.get(row["nuclide"])
            if population <= 0 or chains is None:
                continue
            value = abs(sum(c["atoms_per_g"] for c in chains) - population) / population
            if value > gap:
                gap, where = value, {"step": index, "nuclide": row["nuclide"], "atoms_per_g": population}
    return {"gap": gap, "at": where}


def selftest():
    def expect(cond, what):
        if not cond:
            raise SystemExit(f"selftest failed: {what}")
    a = {"pathway_closure": 4.1, "x": {"ms": 3, "y": [1, {"elapsed_ms": 2, "z": 1}]}, "wall_time_s": 1.0}
    b = {"pathway_closure": 1e-7, "x": {"ms": 9, "y": [1, {"elapsed_ms": 5, "z": 1}]}, "wall_time_s": 7.0}
    expect(first_difference(strip(a), strip(b)) is None, "closure and timing keys are ignored")
    b["x"]["y"][1]["z"] = 2
    expect(first_difference(strip(a), strip(b)) == "/x/y/1/z: 1 vs 2", "a real difference is found")
    expect(first_difference(strip({"a": 1}), strip({"a": 1, "b": 2})) == "/b: present in one result only", "extra key")
    doc = {"steps": [{"inventory": [{"nuclide": "Fe56", "atoms_per_g": 1e22}, {"nuclide": "Mn58", "atoms_per_g": 2.7e-28},
                                    {"nuclide": "Co58", "atoms_per_g": 1e12}]}],
           "pathways": [{"Fe56": [{"atoms_per_g": 1e22}], "Mn58": [{"atoms_per_g": 1.38e-27}],
                         "Co58": [{"atoms_per_g": 1.1e12}]}]}
    gap, where = recompute(doc)
    expect(abs(gap - 0.1) < 1e-12 and where["nuclide"] == "Co58", "noise residue skipped, real gap found")
    expect(abs(recompute_any(doc)["gap"] - (1.38e-27 - 2.7e-28) / 2.7e-28) < 1e-9, "old definition reproduced")
    del doc["pathways"][0]["Co58"]
    expect(recompute(doc)[0] == 1.0, "a resolved nuclide with no chain is a gap of 1")
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "g1.log"
        log.write_text("".join(f"test x::{t} ... ok\n" for t in G1_TESTS) + "G1: PASS\n")
        expect(gate_g1(log)["pass"], "a complete G1 log passes")
        log.write_text("".join(f"test x::{t} ... ok\n" for t in G1_TESTS[1:]) + "G1: PASS\n")
        expect(not gate_g1(log)["pass"], "a missing test fails G1")
        log.write_text("".join(f"test x::{t} ... ok\n" for t in G1_TESTS) + "test y ... FAILED\nG1: PASS\n")
        expect(not gate_g1(log)["pass"], "a failed test fails G1")
    print("P161 selftest: PASS")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--g1-log")
    ap.add_argument("--cwd", default=str(ROOT), help="directory holding actinv-data (relative spec paths resolve there)")
    ap.add_argument("--work", default=str(ROOT / "target" / "p161-work"))
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    if not (args.reference and args.candidate):
        ap.error("--reference and --candidate are required")
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    g0, g1 = gate_g0(), gate_g1(args.g1_log)
    cases, skipped = evaluate(Path(args.reference).resolve(), Path(args.candidate).resolve(), args.cwd, work)
    gates = {
        "G0": g0["pass"], "G1": g1["pass"],
        "G2": bool(cases) and all(c["g2"] for c in cases.values()),
        "G3": bool(cases) and all(c["g3"] for c in cases.values()),
        "G4": bool(cases) and all(c["g4"] for c in cases.values()),
    }
    record = {
        "schema": "actinv-p161-closure-1", "protocol": "protocols/ACTINV-P161_PROTOCOL.md",
        "protocol_sha256": sha256_file(PROTOCOL), "checker_sha256": sha256_file(Path(__file__)),
        "reference_sha256": sha256_file(args.reference), "candidate_sha256": sha256_file(args.candidate),
        "g0": g0, "g1": g1, "cases": cases, "skipped": skipped,
        "gates": {k: "PASS" if v else "FAIL" for k, v in gates.items()} | {"G5": "in G1", "G6": "external"},
        "pass": all(gates.values()),
    }
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for k, v in gates.items():
        print(f"P161 {k}: {'PASS' if v else 'FAIL'}")
    for name, case in cases.items():
        print(f"  {name}: closure {case.get('reference_closure')} -> {case.get('candidate_closure')}"
              f" (recomputed {case.get('recomputed')})"
              + ("" if case.get("g2") else f"; G2 differs at {case.get('g2_first_difference')}"))
    for name, why in skipped.items():
        print(f"  skipped {name}: {why}")
    print(f"P161: {'PASS' if record['pass'] else 'FAIL'} ({RECORD})")
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
