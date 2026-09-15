#!/usr/bin/env python3
"""Independent checker for P25c G4 — union artifact + dual scoring.

Imports no production module.  Verifies: every staged file's recorded
SHA-256 against the live patched corpus and the G2 patched manifest;
the Amendment-1 F5 floor (every union-set G3 survivor's target present
in the artifact index); artifact/index hash integrity; state-catalog
contents; scoring-block internal consistency (histogram sums, paired ≤
scored, nonreg recomputed verbatim from recorded metrics); and a
hash-sampled replay of two staged builds.  Rejects planted mutations.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25c-g4-check"
ACTINV = ROOT / "target" / "release" / "actinv"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
CENSUS = RESULTS / "g3_p25c_census.json"
BUILD = RESULTS / "g4_p25c_build.json"
SCORE = RESULTS / "g4_p25c_score.json"
IRDFF_SCORE = RESULTS / "g4_p25c_irdff_score.json"
PATCHED_MANIFEST = RESULTS / "g2_p25c_patched_manifest.sha256"

ELEMENT_SYMBOLS = (
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg",
    "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr",
    "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br",
    "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd",
    "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La",
    "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er",
    "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au",
    "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md",
    "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
    "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
)
SYM_TO_Z = {s.upper(): i + 1 for i, s in enumerate(ELEMENT_SYMBOLS)}
FILE_RE = re.compile(r"^n-([A-Za-z]{1,2})(\d{3})([mn]?)\.tendl$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_za(name: str) -> int | None:
    m = FILE_RE.match(name)
    if not m:
        return None
    return SYM_TO_Z[m.group(1).upper()] * 1000 + int(m.group(2))


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


def check_report(record: dict, live: dict) -> list[str]:
    failures = []
    if record.get("schema") != "actinv-p25c-g4-score-1":
        failures.append("score schema")
    if not record.get("retrospective"):
        failures.append("retrospective label missing")
    for key, path in (("g3_census_sha256", CENSUS),
                      ("g4_build_sha256", BUILD),
                      ("g4_irdff_score_sha256", IRDFF_SCORE)):
        if record.get(key) != sha256(path):
            failures.append(f"{key} mismatch")
    blk = (record.get("corpora") or {}).get("tendl_2025_patched") or {}

    ir = blk.get("irdff_partition") or {}
    if ir.get("nonregression") != nonreg_ok(
            ir.get("baseline_metrics") or {},
            ir.get("candidate_metrics") or {}):
        failures.append("irdff nonreg mismatch")
    if (ir.get("comparable_rows") or 0) > (ir.get("candidate_scored") or 0):
        failures.append("irdff comparable exceeds scored")
    if ir.get("status") != "scored":
        failures.append("irdff partition not scored")

    iso = blk.get("isomeric_partition") or {}
    histo = iso.get("candidate_status_histogram") or {}
    if sum(histo.values()) != iso.get("eligible_rows"):
        failures.append("isomer status histogram sum != eligible_rows")
    if (iso.get("paired_rows") or 0) > (iso.get("candidate_scored_rows") or 0):
        failures.append("isomer paired exceeds scored")
    if iso.get("nonreg") != nonreg_ok(iso.get("paired_baseline") or {},
                                    iso.get("paired_candidate") or {}):
        failures.append("isomer nonreg mismatch")
    if blk.get("artifact_sha256") != live.get("npz_sha256"):
        failures.append("artifact sha != live")
    if blk.get("index_sha256") != live.get("index_sha256"):
        failures.append("index sha != live")
    return failures


def main() -> int:
    failures: list[str] = []
    census = json.loads(CENSUS.read_text())
    build = json.loads(BUILD.read_text())
    score = json.loads(SCORE.read_text())
    manifest = {}
    for line in PATCHED_MANIFEST.read_text().splitlines():
        d, n = line.split(None, 1)
        manifest[n.strip()] = d

    # staged files: recorded hash == live patched bytes == G2 manifest
    for fname, det in build["staged_files"].items():
        live_sha = sha256(PATCHED_ROOT / fname)
        if det["sha256"] != live_sha:
            failures.append(f"staged {fname}: sha != live corpus")
        if manifest.get(fname) != live_sha:
            failures.append(f"staged {fname}: sha != patched manifest")
    union_survivors = {f for f, b in census["builds"]["patched"].items()
                       if b["class"] == "ok"
                       and {j["file"]: j["role"] for j in census["jobs"]}[f]
                       in ("irdff", "isomeric")}
    if sorted(union_survivors) != sorted(
            build.get("union_survivors_expected", [])):
        failures.append("union_survivors_expected mismatch")

    # artifact + index integrity; F5: every union survivor's ZA in index
    npz = Path(build["npz"])
    index_path = Path(build["index"])
    live = {}
    if not npz.is_file() or sha256(npz) != build.get("npz_sha256"):
        failures.append("artifact NPZ hash mismatch")
    else:
        live["npz_sha256"] = sha256(npz)
    if not index_path.is_file() \
            or sha256(index_path) != build.get("index_sha256"):
        failures.append("artifact index hash mismatch")
    else:
        live["index_sha256"] = sha256(index_path)
        idx = json.loads(index_path.read_text())
        idx_targets = sorted(t["za"] for t in idx["targets"])
        if idx_targets != build.get("index_targets"):
            failures.append("index_targets mismatch")
        missing = sorted(
            za for za in {file_za(f) for f in union_survivors}
            if za not in idx_targets)
        if missing:
            failures.append(f"F5: union survivors absent from artifact: "
                            f"{missing}")
        lisos = {e["liso"] for e in idx.get("state_catalog", [])}
        if sorted(lisos) != sorted(build.get("catalog_liso_values") or []):
            failures.append("catalog_liso_values mismatch")
        if not any(t.get("state_mappings") for t in idx.get("targets", [])):
            failures.append("no state_mappings in index")

    if build["build"].get("exit") != 0:
        failures.append("directory build did not succeed")

    # hash-sampled replay: 2 staged union files rebuilt independently
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "cache").mkdir(exist_ok=True)
    picks = sorted(union_survivors,
                   key=lambda f: hashlib.sha256(f.encode()).hexdigest())[:2]
    replayed = []
    for fname in picks:
        out = WORK / "one.npz"
        out.unlink(missing_ok=True)
        try:
            proc = subprocess.run(
                [str(ACTINV), "build-library",
                 str(PATCHED_ROOT / fname), str(out),
                 "--format", "tendl", "--projectile", "neutron",
                 "--groups", "fispact-709", "--temperature-K", "293.6",
                 "--cache", str(WORK / "cache")],
                capture_output=True, text=True, timeout=300.0, cwd=ROOT)
            replayed.append(f"{fname}:{proc.returncode}")
            if proc.returncode != 0:
                failures.append(f"replay {fname}: exit {proc.returncode}")
        except subprocess.TimeoutExpired:
            replayed.append(f"{fname}:timeout")
            failures.append(f"replay {fname}: timeout")
        finally:
            out.unlink(missing_ok=True)

    failures.extend(check_report(score, live))

    mutations = rejected = 0
    plants = [
        lambda r: r["corpora"]["tendl_2025_patched"]["irdff_partition"]
                  ["nonregression"].update(median_ok=False),
        lambda r: r["corpora"]["tendl_2025_patched"]["isomeric_partition"]
                  .update(paired_rows=9999),
        lambda r: r["corpora"]["tendl_2025_patched"]["isomeric_partition"]
                  ["nonreg"].update(median_ok=False),
        lambda r: r.update({"retrospective": False}),
        lambda r: r["corpora"]["tendl_2025_patched"]
                  .update({"artifact_sha256": "0" * 64}),
        lambda r: r.update({"g4_build_sha256": "0" * 64}),
    ]
    for plant in plants:
        m = copy.deepcopy(score)
        plant(m)
        mutations += 1
        if check_report(m, live):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p25c-g4-check-1",
        "pass": not failures,
        "failures": failures,
        "replayed_builds": replayed,
        "staged_files_verified": len(build["staged_files"]),
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g4_p25c_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
