#!/usr/bin/env python3
"""P25c G5 closure checker — imports no production, audit or scoring
module (the signature census control is reused for the live leak
re-census, as at G3).  Rehashes every evidence artifact, replays a
hash-sampled set of patches from source bytes, recomputes the defect
census, coverage, floor and nonregression accounting, verifies gate
ordering via git ancestry, re-verifies all prior verdicts verbatim,
and rejects planted mutations.

Writes ``results/g5_p25c_check.json``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
sys.path.insert(0, str(ROOT / "controls"))
import g1_p25c_signature  # noqa: E402

VERDICT = RESULTS / "verdict_p25c.json"
ACCOUNTING = RESULTS / "g5_p25c_accounting.json"
SEALS = RESULTS / "g0_p25c_seals.json"
SIGNATURE = RESULTS / "g1_p25c_signature.json"
PATCH = RESULTS / "g2_p25c_patch.json"
CENSUS = RESULTS / "g3_p25c_census.json"
G3CHECK = RESULTS / "g3_p25c_check.json"
BUILD = RESULTS / "g4_p25c_build.json"
SCORE = RESULTS / "g4_p25c_score.json"
PATCHED_MANIFEST = RESULTS / "g2_p25c_patched_manifest.sha256"
SOURCE_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
OUT = RESULTS / "g5_p25c_check.json"

GATE_COMMITS = ["32fd3c2", "94a48bb", "8a35102", "6e6d131", "fc6ce8e"]
FLOORS = {"irdff": 29, "union": 43, "anchors": 41}

PRIOR_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL", "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS", "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS", "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL", "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL", "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL", "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL", "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS", "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL", "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL", "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS", "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS", "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS", "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL", "verdict_p25b.json": "P25b-FAIL",
    "verdict_p26.json": "P26-FAIL",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def nonreg_ok(b: dict, c: dict) -> dict:
    res = {"median_ok": None, "p90_ok": None, "coverage_ok": None}
    if b.get("rows") and c.get("rows"):
        res["median_ok"] = (
            c["median_abs_ln"] <= b["median_abs_ln"] + 0.005
            and c["median_abs_ln"] <= 1.01 * b["median_abs_ln"])
        res["p90_ok"] = (
            c["p90_abs_ln"] <= b["p90_abs_ln"] + 0.01
            and c["p90_abs_ln"] <= 1.01 * b["p90_abs_ln"])
        res["coverage_ok"] = all(
            c[k] >= b[k] - 0.01
            for k in ("within_10pct", "within_20pct", "within_30pct"))
    elif not c.get("rows"):
        res["unscored_candidate"] = True
    return res


def replay_patched_bytes(name: str, edits: list[dict]) -> bytes:
    """Apply the patch log's cell edits to source bytes, independently."""
    lines = (SOURCE_ROOT / name).read_text("ascii").splitlines(
        keepends=True)
    for e in edits:
        raw = lines[e["line"] - 1]
        stripped = raw.rstrip("\r\n")
        col = e["column"] - 1
        if stripped[col:col + 11] != e["old_field"]:
            raise ValueError(f"{name}: line {e['line']} source field "
                             f"{stripped[col:col + 11]!r} != logged "
                             f"old_field")
        lines[e["line"] - 1] = (
            stripped[:col] + e["new_field"].rjust(11)
            + stripped[col + 11:] + raw[len(stripped):])
    return "".join(lines).encode("ascii")


def check(verdict: dict, accounting: dict, live: dict) -> list[str]:
    failures = []
    if verdict.get("verdict") not in ("P25c-PASS", "P25c-FAIL"):
        failures.append("verdict value not in vocabulary")
    expected = "P25c-PASS" if (accounting.get("all_floors_met")
                               and accounting.get(
                                   "nonregression_all_ok")) \
        else "P25c-FAIL"
    if verdict.get("verdict") != expected:
        failures.append(f"verdict {verdict.get('verdict')} != computed "
                        f"{expected}")
    if verdict.get("blind_evidence") is not False:
        failures.append("blind_evidence must be false")
    for name, want in (verdict.get("evidence_sha256") or {}).items():
        p = RESULTS / name
        if not p.is_file() or sha256(p) != want:
            failures.append(f"evidence hash mismatch: {name}")
    if sha256(ACCOUNTING) != verdict.get("accounting_sha256"):
        failures.append("accounting hash mismatch")
    if verdict.get("protocol_sha256") != live["protocol_sha256"]:
        failures.append("protocol hash mismatch")

    # gate ordering by git ancestry
    for a, b in zip(GATE_COMMITS, GATE_COMMITS[1:]):
        r = subprocess.run(["git", "merge-base", "--is-ancestor", a, b],
                           capture_output=True, cwd=ROOT)
        if r.returncode != 0:
            failures.append(f"gate ordering: {a} not ancestor of {b}")

    for name, want in PRIOR_VERDICTS.items():
        got = json.loads((RESULTS / name).read_text()).get("verdict")
        if got != want:
            failures.append(f"prior verdict {name}: {got!r} != {want!r}")

    # ---- recompute accounting from raw records ----
    sig = json.loads(SIGNATURE.read_text())
    census = json.loads(CENSUS.read_text())
    build = json.loads(BUILD.read_text())
    score = json.loads(SCORE.read_text())
    g3c = json.loads(G3CHECK.read_text())
    blk = score["corpora"]["tendl_2025_patched"]

    dc = accounting["defect_census"]
    if dict(Counter(r["class"] for r in sig["files"].values())) != \
            dc["pre_patch_classes"]:
        failures.append("defect_census.pre_patch_classes mismatch")
    if dict(Counter("+".join(d["post_kinds"]) or "clean"
                    for d in census["rescan"].values())) != \
            dc["post_rescan_kinds_histogram"]:
        failures.append("post_rescan histogram mismatch")
    if dc["residual_signature_hits"] != len(
            g3c["residual_signature_files"]):
        failures.append("residual_signature_hits mismatch")
    if live["residual_signature"]:
        failures.append("live residual signature: "
                        f"{live['residual_signature']}")

    cov = accounting["coverage"]
    c = census["coverage"]
    if cov["irdff"] != {"source": c["source"]["irdff_built"],
                      "patched": c["patched"]["irdff_built"],
                      "total": c["irdff_total"]}:
        failures.append("coverage.irdff mismatch")
    if cov["union"] != {"source": c["source"]["union_built"],
                      "patched": c["patched"]["union_built"],
                      "total": c["union_total"]}:
        failures.append("coverage.union mismatch")
    if cov["anchors"] != {"source": c["source"]["anchors_built"],
                         "patched": c["patched"]["anchors_built"],
                         "total": c["anchors_total"]}:
        failures.append("coverage.anchors mismatch")
    if sorted(cov["recovered_files"]) != sorted(census["recovered_files"]):
        failures.append("recovered_files mismatch")
    if sorted(cov["newly_broken_files"]) != \
            sorted(census["newly_broken_files"]):
        failures.append("newly_broken_files mismatch")

    floors = accounting["floors"]
    recomputed = {
        "F1_no_regression": not census["newly_broken_files"],
        "F2_non_inferiority": (
            c["patched"]["irdff_built"] >= FLOORS["irdff"]
            and c["patched"]["union_built"] >= FLOORS["union"]
            and c["patched"]["anchors_built"] >= FLOORS["anchors"]),
        "F3_material_recovery": len(census["recovered_files"]) >= 1,
        "F4_leak_clearance": not g3c["residual_signature_files"],
        "F5_artifact_coverage": (
            build["build"]["exit"] == 0
            and len(build["index_targets"] or [])
            == len(build["staged_files"])),
    }
    for k, want in recomputed.items():
        if floors.get(k, {}).get("met") != want:
            failures.append(f"floor {k} met-flag mismatch")
    if accounting["all_floors_met"] != all(recomputed.values()):
        failures.append("all_floors_met mismatch")

    ir = blk["irdff_partition"]
    iso = blk["isomeric_partition"]
    if ir["nonregression"] != nonreg_ok(ir.get("baseline_metrics") or {},
                                      ir.get("candidate_metrics") or {}):
        failures.append("irdff nonreg recompute mismatch")
    if iso["nonreg"] != nonreg_ok(iso.get("paired_baseline") or {},
                                iso.get("paired_candidate") or {}):
        failures.append("isomer nonreg recompute mismatch")
    all_ok = all(v is True for part in (ir["nonregression"], iso["nonreg"])
                 for k, v in part.items() if k.endswith("_ok"))
    if accounting["nonregression_all_ok"] != all_ok:
        failures.append("nonregression_all_ok mismatch")

    # artifact index must contain every union survivor's target (F5)
    jobs = {j["file"]: j["role"] for j in census["jobs"]}
    union_ok = sorted(f for f, b in census["builds"]["patched"].items()
                      if b["class"] == "ok"
                      and jobs[f] in ("irdff", "isomeric"))
    if union_ok != sorted(build["union_survivors_expected"]):
        failures.append("union_survivors_expected recompute mismatch")

    # sampled patch replays: 2 signature files, hash-selected
    patch = json.loads(PATCH.read_text())
    manifest = {}
    for line in PATCHED_MANIFEST.read_text().splitlines():
        d, n = line.split(None, 1)
        manifest[n.strip()] = d
    sig_files = sorted(n for n, r in sig["files"].items()
                       if r["class"] == "confirmed_leak_signature")
    picks = sorted(sig_files,
                   key=lambda n: hashlib.sha256(n.encode()).hexdigest())[:2]
    replayed = []
    for name in picks:
        edits = [e for e in patch["patch_log"] if e["file"] == name]
        try:
            got = hashlib.sha256(
                replay_patched_bytes(name, edits)).hexdigest()
        except ValueError as exc:
            replayed.append(name)
            failures.append(f"replay {name}: {exc}")
            continue
        replayed.append(name)
        if got != manifest.get(name):
            failures.append(f"replay {name}: sha != patched manifest")
    live["replayed_patches"] = replayed
    return failures


def main() -> int:
    verdict = json.loads(VERDICT.read_text())
    accounting = json.loads(ACCOUNTING.read_text())
    seals = json.loads(SEALS.read_text())

    live = {
        "protocol_sha256": sha256(
            ROOT / "protocols" / "ACTINV-P25c_PROTOCOL.md"),
    }
    # live signature re-census over the patched sealed population
    g1_p25c_signature.SOURCE_ROOT = PATCHED_ROOT
    live["residual_signature"] = sorted(
        n for n, det in seals["sealed_population"]["files"].items()
        if g1_p25c_signature.census_file(n, det)["class"]
        == "confirmed_leak_signature")

    failures = check(verdict, accounting, live)

    mutations = []
    def expect_reject(label, v=None, a=None):
        vv = copy.deepcopy(verdict if v is None else v)
        aa = copy.deepcopy(accounting if a is None else a)
        if check(vv, aa, dict(live, residual_signature=[])):
            mutations.append(label)

    v = copy.deepcopy(verdict); v["verdict"] = "P25c-FAIL"
    expect_reject("verdict flip rejected", v=v)
    v = copy.deepcopy(verdict); v["blind_evidence"] = True
    expect_reject("blind-evidence claim rejected", v=v)
    a = copy.deepcopy(accounting)
    a["coverage"]["union"]["patched"] = 77
    expect_reject("coverage inflation rejected", a=a)
    a = copy.deepcopy(accounting)
    a["floors"]["F4_leak_clearance"]["met"] = False
    expect_reject("floor flag flip rejected", a=a)
    a = copy.deepcopy(accounting)
    a["defect_census"]["residual_signature_hits"] = 5
    expect_reject("residual count tamper rejected", a=a)
    a = copy.deepcopy(accounting)
    a["nonregression_all_ok"] = False
    expect_reject("nonreg flag flip rejected", a=a)
    v = copy.deepcopy(verdict)
    v["evidence_sha256"]["g2_p25c_patch.json"] = "0" * 64
    expect_reject("evidence hash tamper rejected", v=v)

    out = {
        "schema": "actinv-p25c-g5-check-1",
        "pass": not failures and len(mutations) == 7,
        "failures": failures,
        "mutations_rejected": mutations,
        "residual_signature_files": live["residual_signature"],
        "replayed_patches": live.get("replayed_patches"),
        "verdict_sha256": sha256(VERDICT),
        "accounting_sha256": sha256(ACCOUNTING),
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
