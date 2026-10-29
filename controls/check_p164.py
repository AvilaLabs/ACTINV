#!/usr/bin/env python3
"""P164 checker (protocols/ACTINV-P164_PROTOCOL.md): measurement-design ranking of reaction blocks.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P164_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  External: fmt, check, clippy, workspace tests, recorded with --g1-log; the log must end with "G1: PASS" and
      name the two P164 unit tests.
  G2  Reference and candidate decision outputs are identical apart from timing keys, every
      measurements.*.top_reactions array and run.result_sha256 (a hash over the whole run result, design included);
      in its place the two binaries' `actinv run` outputs of the run spec are identical apart from timing keys and
      every top_reactions array.
  G3  Candidate top_reactions: computed entries first and descending; computed entries equal the reference's for the
      same target where the reference computed one; ill_conditioned entries appear identically in the reference.
  G4  The reference exercises the defect: its top_reactions hold an ill_conditioned entry and no computed block for a
      target element in the material (the decide example: pure iron).
  G5  The candidate computes at least one block for a target element in the material, and every computed block B
      satisfies: finite; max over top_parameters of the same target (cross_section_mf33) <= reduction <= the response's
      total_propagated_variance (both to 1e-9 relative); share_of_total consistent with reduction / total.
  G5r Recorded only: off-material targets listed, and ill_conditioned reference entries absent from the candidate.
  G6  External (CI on the pushed evidence commit).

Usage: check_p164.py --reference BIN --candidate BIN --g1-log LOG [--cwd DIR] [--work DIR]
       check_p164.py --selftest
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P164_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p164_design_order.json"
DECISION = ROOT / "examples" / "decide" / "decision.json"
RUN_SPEC = ROOT / "examples" / "decide" / "run_spec.json"
VOLATILE = {"ms", "elapsed_ms", "wall_time_s", "cells_per_s"}
G1_TESTS = [
    "p164_computed_reductions_outrank_uncomputed_blocks",
    "p164_uncomputed_blocks_keep_their_relative_order",
    "p164_pseudo_inverse_matches_cholesky_on_positive_definite_blocks",
    "p164_singular_block_in_range_gives_the_blocks_own_variance",
    "p164_vector_outside_the_range_is_ill_conditioned",
]
TIMEOUT = 1800


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def strip(node):
    """The decision without timing keys (any depth) and without every measurements.*.top_reactions array."""
    def walk(n):
        if isinstance(n, dict):
            return {k: walk(v) for k, v in n.items() if k not in VOLATILE}
        if isinstance(n, list):
            return [walk(v) for v in n]
        return n
    out = walk(node)
    for block in (out.get("measurements") or {}).values():
        if isinstance(block, dict):
            block.pop("top_reactions", None)
    if isinstance(out.get("run"), dict):
        out["run"].pop("result_sha256", None)
    return out


def strip_run(node):
    """A run result without timing keys and without every top_reactions array (any depth)."""
    if isinstance(node, dict):
        return {k: strip_run(v) for k, v in node.items() if k not in VOLATILE and k != "top_reactions"}
    if isinstance(node, list):
        return [strip_run(v) for v in node]
    return node


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


def key(entry):
    return (entry["target_za"], entry["target_liso"])


def check_order(candidate_blocks, reference_blocks):
    """G3 for one top_reactions pair; returns a list of problems (empty when it holds)."""
    problems = []
    seen_uncomputed = False
    previous = None
    ref = {key(e): e for e in reference_blocks}
    for e in candidate_blocks:
        value = e.get("variance_reduction")
        if value is None:
            seen_uncomputed = True
            if e.get("status") != "ill_conditioned":
                problems.append(f"{key(e)}: null reduction with status {e.get('status')!r}")
            if key(e) not in ref or ref[key(e)] != e:
                problems.append(f"{key(e)}: ill_conditioned entry not identical in the reference")
        else:
            if seen_uncomputed:
                problems.append(f"{key(e)}: computed entry after an ill_conditioned one")
            if previous is not None and value > previous:
                problems.append(f"{key(e)}: not descending ({value!r} after {previous!r})")
            previous = value
            if key(e) in ref and ref[key(e)].get("variance_reduction") is not None and ref[key(e)] != e:
                problems.append(f"{key(e)}: computed entry differs from the reference")
    return problems


def reference_exercises_defect(reference_blocks, elements=None):
    """An ill_conditioned entry is listed and (when `elements` is given) no on-material block was computed."""
    if not any(e.get("variance_reduction") is None for e in reference_blocks):
        return False
    if elements is None:
        return True
    return not any(e.get("variance_reduction") is not None and element_of(e["target_za"]) in elements
                   for e in reference_blocks)


def check_invariants(blocks, parameters, total, elements, tol=1e-9):
    """G5 for one response: problems (empty when it holds) and the number of on-material computed blocks."""
    problems, on_material = [], 0
    for e in blocks:
        value = e.get("variance_reduction")
        if value is None:
            continue
        if element_of(e["target_za"]) in elements:
            on_material += 1
        if not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
            problems.append(f"{key(e)}: nonfinite reduction")
            continue
        singles = [p["variance_reduction"] for p in parameters
                   if p.get("channel") == "cross_section_mf33"
                   and (p["parameter"].get("target_ZA"), p["parameter"].get("target_LISO")) == key(e)]
        if singles and max(singles) > value * (1 + tol) + 1e-300:
            problems.append(f"{key(e)}: block {value!r} below its largest single parameter {max(singles)!r}")
        if value > total * (1 + tol):
            problems.append(f"{key(e)}: block {value!r} above total variance {total!r}")
        share = e.get("share_of_total")
        if share is not None and total > 0 and abs(share - value / total) > 1e-9 * max(1.0, abs(share)):
            problems.append(f"{key(e)}: share_of_total {share!r} != reduction/total")
    return problems, on_material


def material_elements(spec):
    return {k.upper() for k in spec["material"]["composition"]}


ELEMENTS = ("H HE LI BE B C N O F NE NA MG AL SI P S CL AR K CA SC TI V CR MN FE CO NI CU ZN GA GE AS SE BR KR RB SR "
            "Y ZR NB MO TC RU RH PD AG CD IN SN SB TE I XE CS BA LA CE PR ND PM SM EU GD TB DY HO ER TM YB LU HF TA "
            "W RE OS IR PT AU HG TL PB BI PO AT RN FR RA AC TH PA U").split()


def element_of(za):
    z = za // 1000
    return ELEMENTS[z - 1] if 1 <= z <= len(ELEMENTS) else str(z)


def run(binary, out, cwd):
    if out.exists():
        out.unlink()
    p = subprocess.run([str(binary), "decide", str(DECISION), str(out)], cwd=cwd, capture_output=True,
                       timeout=TIMEOUT)
    return p.returncode, p.stderr.decode(errors="replace")[-400:]


def solve(binary, out, cwd):
    if out.exists():
        out.unlink()
    p = subprocess.run([str(binary), "run", str(RUN_SPEC), str(out)], cwd=cwd, capture_output=True, timeout=TIMEOUT)
    return p.returncode, p.stderr.decode(errors="replace")[-400:]


def gate_g0():
    lines = HASH_LOG.read_text(encoding="utf-8").splitlines()
    paths = [PROTOCOL] + sorted((ROOT / "protocols").glob("ACTINV-P164_AMENDMENT_*.md"))
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


def evaluate(ref, cand, spec):
    """Gates G2-G5 on two parsed decision outputs."""
    diff = first_difference(strip(ref), strip(cand))
    elements = material_elements(spec)
    g3, g4, g5, per = True, True, True, {}
    for name in sorted(ref.get("measurements", {})):
        rb = ref["measurements"][name].get("top_reactions", [])
        cb = (cand.get("measurements", {}).get(name) or {}).get("top_reactions")
        if cb is None:
            g3 = False
            per[name] = {"problems": ["candidate has no top_reactions"]}
            continue
        problems = check_order(cb, rb)
        exercised = reference_exercises_defect(rb, elements)
        inv, on_material = check_invariants(cb, cand["measurements"][name].get("top_parameters", []),
                                            cand["measurements"][name].get("total_propagated_variance", float("inf")),
                                            elements)
        g3 = g3 and not problems
        g4 = g4 and exercised
        g5 = g5 and not inv and on_material > 0
        cand_keys = {key(e) for e in cb}
        per[name] = {
            "problems": problems, "invariant_problems": inv, "on_material_computed_blocks": on_material,
            "reference_exercises_defect": exercised,
            "reference": [[key(e)[0], e["status"], e.get("variance_reduction")] for e in rb],
            "candidate": [[key(e)[0], e["status"], e.get("variance_reduction")] for e in cb],
            "off_material_targets_listed": sorted({element_of(e["target_za"]) for e in cb} - elements),
            "reference_ill_conditioned_dropped": [key(e) for e in rb
                                                  if e.get("variance_reduction") is None and key(e) not in cand_keys],
        }
    return {"g2": diff is None, "g2_first_difference": diff, "g3": g3, "g4": g4 and bool(per), "g5": g5 and bool(per), "per_constraint": per}


def selftest():
    def expect(cond, what):
        if not cond:
            raise SystemExit(f"selftest failed: {what}")

    def blk(za, v):
        return {"target_za": za, "target_liso": 0, "mts": [102], "status": "emitted" if v is not None else "ill_conditioned",
                "covered_parameters": 1, "variance_reduction": v}
    old = [blk(20043, None), blk(20044, None), blk(26056, 0.0), blk(26054, 2e-9), blk(25055, 1e-6)]
    new = [blk(25055, 1e-6), blk(26054, 2e-9), blk(26056, 0.0), blk(20043, None), blk(20044, None)]
    expect(check_order(new, old) == [], "the fixed order passes")
    expect(reference_exercises_defect(old), "an ill_conditioned reference exercises the defect")
    expect(not reference_exercises_defect(new[:3]), "an all-computed reference does not")
    expect(any("after an ill_conditioned" in p for p in check_order(old, old)), "the old order fails")
    expect(any("not descending" in p for p in check_order([blk(26054, 2e-9), blk(25055, 1e-6)], old)), "ascending fails")
    changed = [dict(new[0], variance_reduction=2e-6)] + new[1:]
    expect(any("differs from the reference" in p for p in check_order(changed, old)), "a changed value fails")
    fabricated = new[:3] + [blk(20099, None)]
    expect(any("not identical in the reference" in p for p in check_order(fabricated, old)), "invented block fails")
    spec = {"material": {"composition": {"FE": 100.0}}}
    fe_old = [blk(20043, None), blk(20044, None), blk(26056, None)]
    fe_new = [blk(26056, 9.0e-20)] + fe_old[:2]
    for e in fe_new:
        e["share_of_total"] = e["variance_reduction"] / 1e-19 if e["variance_reduction"] is not None else None
    params = [{"channel": "cross_section_mf33", "variance_reduction": 8.0e-20,
               "parameter": {"target_ZA": 26056, "target_LISO": 0}},
              {"channel": "cross_section_mf33", "variance_reduction": 1.0,
               "parameter": {"target_ZA": 26054, "target_LISO": 0}}]
    mk = lambda blocks, extra=1, par=params: {
        "verdict": {"x": 1}, "wall_time_s": extra,
        "measurements": {"a@1": {"top_parameters": par, "top_reactions": blocks, "total_propagated_variance": 1e-19}}}
    expect(reference_exercises_defect(fe_old, {"FE"}), "all-ill_conditioned iron reference exercises the defect")
    expect(not reference_exercises_defect(fe_new, {"FE"}), "a reference with an Fe block does not")
    r = evaluate(mk(fe_old, 1), mk(fe_new, 9), spec)
    expect(r["g2"] and r["g3"] and r["g4"] and r["g5"], f"a correct candidate passes G2-G5: {r}")
    expect(r["per_constraint"]["a@1"]["off_material_targets_listed"] == ["CA"], "off-material target recorded")
    low = [dict(fe_new[0], variance_reduction=7.0e-20)] + fe_old[:2]
    expect(not evaluate(mk(fe_old), mk(low), spec)["g5"], "block below its largest parameter fails G5")
    high = [dict(fe_new[0], variance_reduction=2.0e-19)] + fe_old[:2]
    expect(not evaluate(mk(fe_old), mk(high), spec)["g5"], "block above total variance fails G5")
    badshare = [dict(fe_new[0], share_of_total=0.1)] + fe_old[:2]
    expect(not evaluate(mk(fe_old), mk(badshare), spec)["g5"], "inconsistent share fails G5")
    expect(not evaluate(mk(fe_old), mk(fe_old), spec)["g5"], "no computed iron block fails G5")
    expect(not evaluate(mk(fe_old), mk([blk(20043, None), blk(26056, 9e-20)]), spec)["g3"],
           "an ill_conditioned entry ahead of a computed one fails G3")
    bad = mk(fe_new)
    bad["measurements"]["a@1"]["top_parameters"] = [dict(params[0], variance_reduction=8.1e-20), params[1]]
    expect(not evaluate(mk(fe_old), bad, spec)["g2"], "a changed top_parameters fails G2")
    bad = mk(fe_new)
    bad["verdict"] = {"x": 2}
    expect(not evaluate(mk(fe_old), bad, spec)["g2"], "a changed verdict fails G2")
    expect(not evaluate(mk(fe_new), mk(fe_new), spec)["g4"], "a corpus without the defect fails G4")
    hashed = [mk(fe_old), mk(fe_new)]
    hashed[0]["run"], hashed[1]["run"] = {"result_sha256": "a", "run_spec": "r"}, {"result_sha256": "b", "run_spec": "r"}
    expect(evaluate(*hashed, spec)["g2"], "the run hash is excluded from the decision comparison")
    hashed[1]["run"]["run_spec"] = "s"
    expect(not evaluate(*hashed, spec)["g2"], "another run field still counts")
    runs = [{"a": {"top_reactions": [1], "ms": 3, "x": 1.0}}, {"a": {"top_reactions": [2], "ms": 4, "x": 1.0}}]
    expect(first_difference(*map(strip_run, runs)) is None, "run outputs differing only in top_reactions match")
    runs[1]["a"]["x"] = 1.5
    expect(first_difference(*map(strip_run, runs)) is not None, "a changed run value is caught")
    expect(element_of(26056) == "FE" and element_of(20043) == "CA", "element lookup")
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "g1.log"
        log.write_text("".join(f"test x::{t} ... ok\n" for t in G1_TESTS) + "G1: PASS\n")
        expect(gate_g1(log)["pass"], "a complete G1 log passes")
        log.write_text(f"test x::{G1_TESTS[0]} ... ok\nG1: PASS\n")
        expect(not gate_g1(log)["pass"], "a missing test fails G1")
        log.write_text("".join(f"test x::{t} ... ok\n" for t in G1_TESTS) + "test y ... FAILED\nG1: PASS\n")
        expect(not gate_g1(log)["pass"], "a failed test fails G1")
    print("P164 selftest: PASS")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--g1-log")
    ap.add_argument("--cwd", default=str(ROOT), help="directory holding actinv-data")
    ap.add_argument("--work", default=str(ROOT / "target" / "p164-work"))
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    if not (args.reference and args.candidate):
        ap.error("--reference and --candidate are required")
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    ref_bin, cand_bin = Path(args.reference).resolve(), Path(args.candidate).resolve()
    g0, g1 = gate_g0(), gate_g1(args.g1_log)
    ref_out, cand_out = work / "reference.json", work / "candidate.json"
    rcode, rerr = run(ref_bin, ref_out, args.cwd)
    ccode, cerr = run(cand_bin, cand_out, args.cwd)
    ref_run, cand_run = work / "reference_run.json", work / "candidate_run.json"
    rrcode, rrerr = solve(ref_bin, ref_run, args.cwd)
    crcode, crerr = solve(cand_bin, cand_run, args.cwd)
    result = {"reference_exit": rcode, "candidate_exit": ccode, "reference_run_exit": rrcode,
              "candidate_run_exit": crcode}
    if rcode == 0 and ccode == 0 and rrcode == 0 and crcode == 0:
        result.update(evaluate(json.loads(ref_out.read_text()), json.loads(cand_out.read_text()),
                               json.loads(RUN_SPEC.read_text())))
        run_diff = first_difference(strip_run(json.loads(ref_run.read_text())),
                                    strip_run(json.loads(cand_run.read_text())))
        result["g2_run_first_difference"] = run_diff
        result["g2"] = result["g2"] and run_diff is None
    else:
        result.update({"stderr_tail": [rerr, cerr, rrerr, crerr], "g2": False, "g3": False, "g4": False,
                       "g5": False})
    gates = {"G0": g0["pass"], "G1": g1["pass"], "G2": result["g2"], "G3": result["g3"], "G4": result["g4"], "G5": result["g5"]}
    record = {
        "schema": "actinv-p164-design-order-1", "protocol": "protocols/ACTINV-P164_PROTOCOL.md",
        "protocol_sha256": sha256_file(PROTOCOL), "checker_sha256": sha256_file(Path(__file__)),
        "reference_sha256": sha256_file(ref_bin), "candidate_sha256": sha256_file(cand_bin),
        "g0": g0, "g1": g1, "decide": result,
        "gates": {k: "PASS" if v else "FAIL" for k, v in gates.items()} | {"G6": "external"},
        "pass": all(gates.values()),
    }
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for k, v in gates.items():
        print(f"P164 {k}: {'PASS' if v else 'FAIL'}")
    for name, per in result.get("per_constraint", {}).items():
        print(f"  {name}: reference {per['reference']}\n    candidate {per['candidate']}"
              f"\n    off-material {per['off_material_targets_listed']}")
    print(f"P164: {'PASS' if record['pass'] else 'FAIL'} ({RECORD})")
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
