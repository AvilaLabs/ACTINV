#!/usr/bin/env python3
"""P90 checker (protocols/ACTINV-P90_PROTOCOL.md): lean `steps.<field>[.<key>...]` selection built
directly from `RunResult`/`StepOut` fields, never serializing the whole result.

    python3 controls/check_p90.py run     # reference vs candidate mesh runs: G2, G3, G6
    python3 controls/check_p90.py check   # verdict -> results/p90_verdict.json

G4 (adapter tests) and part of G1 (fmt/clippy/test/release) are produced by `target/p90/build.sh`,
whose stdout is redirected to `target/p90/build.log` and whose adapter-test run is written to
`target/p90/adapter_tests.txt`. G5 (CI replay) runs separately; its step log must land at
`target/p90/ci_replay_summary.log` before `check` is run.

G2 mesh outputs are compared as bytes: every line but the footer (this includes the header) is
hashed together, and the footer is compared as JSON without `wall_time_s`/`cells_per_s`. Candidate
and reference share the same spec for every G2 case (only the binary differs), so the header's
`spec_fingerprint_sha256` is expected to match too and is deliberately left inside the hashed body —
a change there would mean the candidate spec parses differently, which G2 must catch.

G3 runs the candidate (this worktree's release binary) against the reference binary (master
`e546292`, archived as `ref_actinv`) on the *same* profile spec but with different
`cell_result_fields`: the candidate gets the new dotted selection, the reference gets no selection
(full output). `MeshSpec::fingerprint_sha256` folds `cell_result_fields` into the canonical spec hash
it signs, so the two specs legitimately fingerprint differently; G3's header comparison excludes
`spec_fingerprint_sha256` for this reason (on top of the footer's timing keys), and documents that
exclusion here as the concrete resolution of the protocol's "inspect a real header to decide which
keys those are" instruction.
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
WORK = ROOT / "target" / "p90"
REF = WORK / "ref_actinv"
CAND = ROOT / "target" / "release" / "actinv"
MESH = ["fe_coupled", "fe_p21like", "ss316_r2s"]
MESH_DIR = MAIN / "target" / "meshprof"
PROTOCOL = ROOT / "protocols" / "ACTINV-P90_PROTOCOL.md"
VERDICT = ROOT / "results" / "p90_verdict.json"
ENV = {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}

FOOTER_TIMING = ("wall_time_s", "cells_per_s")
# Mixes `cell_result_fields` into the spec's canonical hash (MeshSpec::fingerprint_sha256), so it
# legitimately differs between a full-output run and a lean-selection run of the same profile; G3
# alone (candidate and reference specs there differ only in that field) excludes it.
HEADER_FINGERPRINT = "spec_fingerprint_sha256"

SPEED_REPEATS = 3
GATED_REPEATS = 5
RESUME_CELLS = 20
THREADS = (1, 3)

G3_PROFILES = ["fe_coupled", "ss316_r2s"]
G3_SELECTION = [
    "mode",
    "ledger",
    "pruned_states",
    "steps.photon_source.groups",
    "steps.heat_W_per_g",
    "steps.t_s",
]

G6_PROFILE = "ss316_r2s"
G6_CAND_SELECTION = ["mode", "ledger", "steps.photon_source.groups"]
G6_REF_SELECTION = ["mode", "steps", "ledger"]
G6_THRESHOLD = 1.25
G6_THREADS = (1, 3)

UNIT_TESTS = (
    "run_result_field_matches_the_value_route_for_every_name",
    "step_field_matches_the_value_route_for_every_name",
    "run_result_accessor_fields_cover_every_key_a_populated_result_serializes",
    "step_field_names_cover_every_key_a_populated_step_out_serializes",
    "cell_result_fields_rejects_a_non_step_field_dotted_path",
    "cell_result_fields_rejects_a_path_past_a_scalar",
    "cell_result_fields_rejects_an_unknown_key_under_an_object_field",
    "cell_result_fields_accepts_a_dynamic_map_field_whole_but_not_a_key_inside_it",
    "cell_result_fields_rejects_plain_steps_combined_with_a_dotted_entry",
    "cell_result_fields_accepts_the_g3_selection",
    "lean_cell_text_always_excludes_ms_even_if_selected",
    "lean_text_matches_the_pruned_subtree_of_the_full_value_route",
)


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
    """sha256 of every line but the last (the footer; the header line is included), plus the
    footer without timing keys. See the module docstring for why the header's fingerprint stays in."""
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


def read_records(path: Path) -> tuple[dict, list, dict]:
    header, cells, footer = None, [], None
    with path.open() as f:
        for line in f:
            rec = json.loads(line)
            kind = rec.get("record")
            if kind == "header":
                header = rec
            elif kind == "cell":
                cells.append(rec)
            elif kind == "footer":
                footer = rec
    return header, cells, footer


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


# -- G3: dotted-selection subtree equality against the reference's full output ------------------

def prune(value: dict, tails: list) -> dict:
    """Mirror mesh.rs's `prune_object`: group `tails` by first segment, keep a key's value whole
    where some tail ends there, recurse where none does. A key absent from `value` (its full-output
    field was itself unset, e.g. `photon_source` with no photon output) is left out, exactly as
    `build_lean_step` leaves it out of the candidate's record."""
    grouped: dict = {}
    for tail in tails:
        head, rest = tail[0], tail[1:]
        grouped.setdefault(head, []).append(rest)
    out = {}
    for key, rests in grouped.items():
        if key not in value:
            continue
        child = value[key]
        if any(len(rest) == 0 for rest in rests):
            out[key] = child
        else:
            out[key] = prune(child, rests)
    return out


def expected_lean_result(full_result: dict, selection: list) -> dict:
    """The lean `result` object mesh.rs's dotted route should produce for `full_result` (the
    reference's complete `RunResult` for the same cell) under `selection`."""
    top_fields = [f for f in selection if not f.startswith("steps.")]
    step_paths = [f[len("steps."):].split(".") for f in selection if f.startswith("steps.")]
    expected = {f: full_result[f] for f in top_fields if f not in ("steps", "ms") and f in full_result}
    if step_paths:
        steps = []
        for step in full_result["steps"]:
            lean_step = {"step": step["step"]}
            lean_step.update(prune(step, step_paths))
            steps.append(lean_step)
        expected["steps"] = steps
    return expected


def g3_check_profile(profile: str) -> dict:
    cand_spec = spec_variant("g3cand", {"threads": 1, "cell_result_fields": G3_SELECTION}, profile)
    ref_spec = spec_variant("g3ref", {"threads": 1, "cell_result_fields": None}, profile)
    cand_out = WORK / f"g3_{profile}.cand.ndjson"
    ref_out = WORK / f"g3_{profile}.ref.ndjson"
    cand_run = run_mesh(CAND, cand_spec, cand_out)
    ref_run = run_mesh(REF, ref_spec, ref_out)
    result = {"cand_returncode": cand_run["returncode"], "ref_returncode": ref_run["returncode"]}
    if cand_run["returncode"] != 0 or ref_run["returncode"] != 0:
        result["pass"] = False
        result["error"] = "mesh run failed"
        cand_out.unlink(missing_ok=True)
        ref_out.unlink(missing_ok=True)
        return result

    cand_header, cand_cells, cand_footer = read_records(cand_out)
    ref_header, ref_cells, ref_footer = read_records(ref_out)

    ch, rh = dict(cand_header), dict(ref_header)
    ch.pop(HEADER_FINGERPRINT, None)
    rh.pop(HEADER_FINGERPRINT, None)
    header_ok = ch == rh

    cf, rf = dict(cand_footer), dict(ref_footer)
    for key in FOOTER_TIMING:
        cf.pop(key, None)
        rf.pop(key, None)
    footer_ok = cf == rf

    mismatches = []
    if len(cand_cells) != len(ref_cells):
        mismatches.append({"error": f"cell count {len(cand_cells)} != {len(ref_cells)}"})
    for cand_rec, ref_rec in zip(cand_cells, ref_cells):
        expected = expected_lean_result(ref_rec["result"], G3_SELECTION)
        actual = cand_rec["result"]
        if actual != expected:
            mismatches.append({"ordinal": cand_rec.get("ordinal"), "expected": expected, "actual": actual})

    result["pass"] = header_ok and footer_ok and not mismatches
    result["header_ok"] = header_ok
    result["footer_ok"] = footer_ok
    result["cell_count"] = len(cand_cells)
    result["mismatch_count"] = len(mismatches)
    result["mismatches"] = mismatches[:5]
    cand_out.unlink(missing_ok=True)
    ref_out.unlink(missing_ok=True)
    return result


# -- G6: adoption-threshold timing -----------------------------------------------------------------
# The protocol's "reference" is the archived master binary: the candidate binary runs the adapter's
# new selection, the reference binary runs the adapter's current selection, alternating.

def g6_measure(threads: int, repeats: int) -> dict:
    cand_spec = spec_variant(f"g6cand.t{threads}", {"threads": threads, "cell_result_fields": G6_CAND_SELECTION},
                             G6_PROFILE)
    ref_spec = spec_variant(f"g6ref.t{threads}", {"threads": threads, "cell_result_fields": G6_REF_SELECTION},
                            G6_PROFILE)
    wall = {"cand": [], "ref": []}
    bytes_ = {"cand": [], "ref": []}
    for _ in range(repeats):
        for tag, spec, binary in (("ref", ref_spec, REF), ("cand", cand_spec, CAND)):
            out = WORK / f"g6_{tag}.t{threads}.ndjson"
            r = run_mesh(binary, spec, out)
            if r["returncode"] != 0:
                wall[tag].append(None)
                bytes_[tag].append(None)
            else:
                wall[tag].append(r["wall_s"])
                bytes_[tag].append(out.stat().st_size)
            out.unlink(missing_ok=True)
    return {"wall_s": wall, "output_bytes": bytes_}


# -- run --------------------------------------------------------------------------------------

def cmd_run() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    log = {"reference_sha256": sha(REF), "candidate_sha256": sha(CAND), "mesh": {}, "variants": {}, "g3": {},
           "g6": {}}

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
    fields = spec_variant("fields", {"cell_result_fields": ["steps", "ledger"]})
    log["variants"]["cell_result_fields"] = {
        tag: compare_run(b, fields, WORK / f"variant_fields.{tag}.ndjson") for tag, b in (("ref", REF), ("cand", CAND))}

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

    for profile in G3_PROFILES:
        log["g3"][profile] = g3_check_profile(profile)
        print("g3", profile, log["g3"][profile]["pass"], flush=True)

    log["g6"]["t1"] = g6_measure(1, GATED_REPEATS)
    log["g6"]["t3_report_only"] = g6_measure(3, SPEED_REPEATS)
    print("g6", {k: v["wall_s"] for k, v in log["g6"].items()}, flush=True)

    (WORK / "run_log.json").write_text(json.dumps(log, indent=1, sort_keys=True))


# -- check ------------------------------------------------------------------------------------

def cmd_check() -> int:
    log = json.loads((WORK / "run_log.json").read_text())
    registered = f"{sha(PROTOCOL)}  protocols/ACTINV-P90_PROTOCOL.md" in (ROOT / "protocols/protocol_hash.txt").read_text()

    blog = (WORK / "build.log").read_text()
    rc = dict(re.findall(r"^(fmt|clippy|test|release) rc=(\d+)$", blog, re.M))
    test_text = (WORK / "test.txt").read_text()
    unit = {name: re.search(rf"^test \S*{name} \.\.\. ok$", test_text, re.M) is not None for name in UNIT_TESTS}
    g1 = all(rc.get(k) == "0" for k in ("fmt", "clippy", "test", "release")) and all(unit.values())

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

    g3 = {"pass": all(p["pass"] for p in log["g3"].values()) and len(log["g3"]) == len(G3_PROFILES),
          "profiles": log["g3"]}

    adapter_text = (WORK / "adapter_tests.txt").read_text() if (WORK / "adapter_tests.txt").exists() else ""
    adapter_rc = re.search(r"^adapter_tests rc=(\d+)$", adapter_text, re.M)
    # A run where openmc is not importable skips every test and exits 0; that proves nothing, so
    # G4 also requires the tests to have actually run (no skips, at least one test).
    ran = re.search(r"^Ran (\d+) tests? in", adapter_text, re.M)
    skipped = re.search(r"skipped=(\d+)", adapter_text)
    g4 = {"pass": adapter_rc is not None and adapter_rc.group(1) == "0" and ran is not None
          and int(ran.group(1)) > 0 and skipped is None,
          "rc": adapter_rc and adapter_rc.group(1), "ran": ran and int(ran.group(1)),
          "skipped": skipped and int(skipped.group(1))}

    replay_path = WORK / "ci_replay_summary.log"
    steps = re.findall(r"^STEP (\d+) (\S+)$", replay_path.read_text(), re.M) if replay_path.exists() else []
    failed = [name for code, name in steps if code != "0"]
    g5 = {"pass": bool(steps) and not failed, "steps": len(steps), "failed": failed}

    t1 = log["g6"]["t1"]
    def median_or_none(values):
        clean = [v for v in values if v is not None]
        return statistics.median(clean) if len(clean) == len(values) and values else None
    cand_median = median_or_none(t1["wall_s"]["cand"])
    ref_median = median_or_none(t1["wall_s"]["ref"])
    speedup = (ref_median / cand_median) if cand_median and ref_median else None
    cand_bytes = median_or_none(t1["output_bytes"]["cand"])
    ref_bytes = median_or_none(t1["output_bytes"]["ref"])
    t3 = log["g6"]["t3_report_only"]
    g6 = {"pass": speedup is not None and speedup >= G6_THRESHOLD, "threshold": G6_THRESHOLD,
          "t1": {"cand_wall_s": t1["wall_s"]["cand"], "ref_wall_s": t1["wall_s"]["ref"],
                 "cand_median_s": cand_median, "ref_median_s": ref_median, "speedup": speedup,
                 "cand_output_bytes": cand_bytes, "ref_output_bytes": ref_bytes,
                 "output_bytes_ratio": (cand_bytes / ref_bytes) if cand_bytes and ref_bytes else None},
          "t3_report_only": {"cand_wall_s": t3["wall_s"]["cand"], "ref_wall_s": t3["wall_s"]["ref"],
                             "cand_median_s": median_or_none(t3["wall_s"]["cand"]),
                             "ref_median_s": median_or_none(t3["wall_s"]["ref"])}}

    verdict = {
        "protocol": "ACTINV-P90",
        "inputs": {"protocol_sha256": sha(PROTOCOL), "reference_sha256": log["reference_sha256"],
                   "candidate_sha256": log["candidate_sha256"]},
        "G0": {"pass": registered},
        "G1": {"pass": g1, "exit_codes": rc, "unit_test_ok": unit},
        "G2": g2,
        "G3": g3,
        "G4": g4,
        "G5": g5,
        "G6": g6,
        "pass": registered and g1 and g2["pass"] and g3["pass"] and g4["pass"] and g5["pass"] and g6["pass"],
    }
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"G0": registered, "G1": g1, "G2": {k: v["pass"] for k, v in profiles.items()},
                      "G2_variants": {n: v["pass"] for n, v in variants.items()},
                      "G3": {p: r["pass"] for p, r in log["g3"].items()},
                      "G4": g4["pass"], "G5": g5["pass"], "G5_failed": failed,
                      "G6": g6["pass"], "G6_speedup": speedup and round(speedup, 3),
                      "G6_output_bytes_ratio": g6["t1"]["output_bytes_ratio"] and round(g6["t1"]["output_bytes_ratio"], 3),
                      "pass": verdict["pass"]}, indent=1))
    return 0


if __name__ == "__main__":
    fn = {"run": cmd_run, "check": cmd_check}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    fn[sys.argv[1]]()
