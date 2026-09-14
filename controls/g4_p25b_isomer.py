#!/usr/bin/env python3
"""P25b G4 (population b) — isomeric-row construction and scoring.

The frozen eligible population's second half is the P25 neutron
isomeric rows — the 493 neutron held-out rows over 37 distinct
targets from the P18b ledger.  For each candidate corpus this stage

  1. resolves each isomeric target to a corpus file (ground state
     preferred, isomer files recorded as alternates), reusing G1
     outcomes for targets already censused;
  2. runs a bounded ``actinv build-library`` per unresolved file at
     the G1 90 s bound;
  3. assembles a held-out artifact ``heldout_<corpus>.npz`` over the
     surviving files by directory build;
  4. scores the unchanged ``g5_p18b_heldout.score_heldout`` fold with
     the artifact as the ``candidate`` neutron library — the frozen
     P18b reference corpus still supplies eligibility and domain
     checks, so the candidate contributes predictions only;
  5. evaluates the frozen nonregression gates on rows scored by both
     candidate and baseline.

Writes ``results/g4_p25b_isomer.json``.  Retrospective scoring only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25b-g4-isomer"
ACTINV = os.environ.get("ACTINV_BIN", "actinv")
PER_FILE_BOUND_S = 90.0
DIR_BUILD_TIMEOUT_S = 7200.0

ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": {"root": ND / "tendl-2023" / "files", "format": "tendl"},
    "fendl_32c": {"root": ND / "fendl-3.2c" / "endf", "format": "tendl"},
    "eaf_2010": {"root": ND / "eaf-2010" / "files", "format": "eaf"},
}

CENSUS = RESULTS / "g1_p25b_census.json"
REBIND = RESULTS / "g4_p25b_rebind.json"
HELDOUT = RESULTS / "g5_p18b_heldout.json"
OUTPUT = RESULTS / "g4_p25b_isomer.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def isomer_targets() -> tuple[list[tuple[int, int]], int]:
    led = json.loads(HELDOUT.read_text())["ledger"]
    targets, rows = set(), 0
    for r in led:
        if r.get("projectile") != "neutron" or not r.get("isomer_liso"):
            continue
        rows += 1
        seg = r["family_id"].split("|")[1].split("-")
        targets.add((int(seg[0]), int(seg[1])))
    return sorted(targets), rows


def parse_filename(name: str) -> tuple[int, int, str] | None:
    for pat in (r"^n_(\d{3})-[A-Za-z]{1,2}-(\d+)([mMnN]?)_\d+\.dat$",
                r"^n_\d+_(\d+)-[A-Za-z]{1,2}-(\d+)([mMnN]?)\.(?:dat|endf)$"):
        m = re.match(pat, name)
        if m:
            return int(m.group(1)), int(m.group(2)), m.group(3).upper()
    return None


def corpus_index(corpus: str) -> dict[tuple[int, int], list[tuple[str, str]]]:
    out: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for path in sorted(CORPORA[corpus]["root"].iterdir()):
        if not path.is_file():
            continue
        parsed = parse_filename(path.name)
        if parsed:
            z, a, iso = parsed
            out.setdefault((z, a), []).append((path.name, iso))
    for key in out:
        out[key].sort(key=lambda item: item[1])
    return out


def run_build(src: Path, fmt: str) -> dict:
    out = WORK / "one.npz"
    out.unlink(missing_ok=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [ACTINV, "build-library", str(src), str(out), "--format", fmt,
             "--projectile", "neutron", "--groups", "fispact-709",
             "--temperature-K", "293.6"],
            capture_output=True, text=True, timeout=PER_FILE_BOUND_S,
            cwd=ROOT)
        rec = {"exit": proc.returncode,
               "seconds": round(time.monotonic() - t0, 3),
               "message": (proc.stderr.strip() or
                           proc.stdout.strip())[-600:]}
    except subprocess.TimeoutExpired:
        rec = {"exit": "timeout",
               "seconds": round(time.monotonic() - t0, 3),
               "message": f"timeout {PER_FILE_BOUND_S}s"}
    out.unlink(missing_ok=True)
    return rec


def percentile(values, pct):
    if not values:
        return float("nan")
    return float(np.percentile(np.asarray(values, float), pct))


def metrics(abs_lns) -> dict:
    vals = [v for v in abs_lns if math.isfinite(v)]
    if not vals:
        return {"rows": 0}
    return {
        "rows": len(vals),
        "median_abs_ln": percentile(vals, 50),
        "p90_abs_ln": percentile(vals, 90),
        "within_10pct": sum(v <= math.log(1.1) for v in vals) / len(vals),
        "within_20pct": sum(v <= math.log(1.2) for v in vals) / len(vals),
        "within_30pct": sum(v <= math.log(1.3) for v in vals) / len(vals),
    }


def nonreg(b: dict, c: dict) -> dict:
    res = {"median_ok": None, "p90_ok": None, "coverage_ok": None}
    if b.get("rows") and c.get("rows"):
        med_b, med_c = b["median_abs_ln"], c["median_abs_ln"]
        p90_b, p90_c = b["p90_abs_ln"], c["p90_abs_ln"]
        res["median_ok"] = (med_c <= med_b + 0.005
                            and med_c <= 1.01 * med_b)
        res["p90_ok"] = (p90_c <= p90_b + 0.01
                         and p90_c <= 1.01 * p90_b)
        res["coverage_ok"] = all(
            c[k] >= b[k] - 0.01
            for k in ("within_10pct", "within_20pct", "within_30pct"))
    return res


def main() -> int:
    import g5_p18b_heldout as g5  # frozen P18b held-out scoring module

    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "cache").mkdir(exist_ok=True)
    targets, row_count = isomer_targets()
    census = json.loads(CENSUS.read_text())
    rebind = json.loads(REBIND.read_text())
    rebound = {(r["corpus"], r["target"]): r["class"] == "ok"
               for r in rebind["rows"]}
    g1_ok = {}
    g1_failed = {}
    for row in census["ledger"]:
        key = (row["corpus"], row["za"])
        cls = row["class"]
        if cls != "ok" and rebound.get((row["corpus"], row["target"])):
            cls = "ok"
        if cls == "ok":
            g1_ok[key] = row["file"]
        else:
            g1_failed[key] = row

    corpora_out = {}
    for corpus, spec in CORPORA.items():
        index = corpus_index(corpus)
        construction = []
        survivors = []
        for z, a in targets:
            za = z * 1000 + a
            prior = g1_ok.get((corpus, za))
            files = index.get((z, a), [])
            ground = [f for f, iso in files if iso == ""]
            entry = {"za": za, "candidate_files": [f for f, _ in files]}
            if prior:
                entry["class"] = "ok"
                entry["file"] = prior
                entry["reused_g1"] = True
                survivors.append((prior, za))
            elif not ground:
                entry["class"] = "corpus_incomplete"
            elif g1_failed.get((corpus, za)) is not None:
                entry["class"] = g1_failed[(corpus, za)]["class"]
                entry["file"] = ground[0]
                entry["reused_g1"] = True
            else:
                src = spec["root"] / ground[0]
                build = run_build(src, spec["format"])
                entry.update({"file": ground[0],
                              "file_sha256": sha256(src), **build,
                              "class": "ok" if build["exit"] == 0
                              else "construction_error"})
                if entry["class"] == "ok":
                    survivors.append((ground[0], za))
            construction.append(entry)
            print(f"{corpus} za{za}: {entry['class']}", flush=True)

        # held-out artifact over surviving files
        stage = WORK / f"stage-{corpus}"
        stage.mkdir(parents=True, exist_ok=True)
        for leftover in stage.iterdir():
            leftover.unlink()
        for fname, _za in survivors:
            shutil.copyfile(spec["root"] / fname, stage / fname)
        npz = WORK / f"heldout_{corpus}.npz"
        npz.unlink(missing_ok=True)
        t0 = time.monotonic()
        proc = subprocess.run(
            [ACTINV, "build-library", str(stage), str(npz),
             "--format", spec["format"], "--projectile", "neutron",
             "--groups", "fispact-709", "--temperature-K", "293.6",
             "--workers", "2", "--cache", str(WORK / "cache")],
            capture_output=True, text=True, timeout=DIR_BUILD_TIMEOUT_S,
            cwd=ROOT)
        index_path = npz.with_name(npz.stem + "_index.json")
        held = {"build_exit": proc.returncode,
                "seconds": round(time.monotonic() - t0, 3),
                "message": (proc.stderr.strip() or
                            proc.stdout.strip())[-400:],
                "npz_sha256": sha256(npz) if npz.is_file() else None,
                "staged_files": len(list(stage.iterdir()))}
        print(f"heldout build {corpus}: exit {proc.returncode}", flush=True)

        # held-out scoring through the unchanged P18b fold
        score_block = None
        if npz.is_file() and index_path.is_file():
            work = WORK / f"heldout-work-{corpus}"
            work.mkdir(exist_ok=True)
            for leftover in work.iterdir():
                leftover.unlink()
            shutil.copyfile(npz, work / "candidate-neutron.npz")
            shutil.copyfile(index_path, work / "candidate-neutron_index.json")
            scored_report = g5.score_heldout(work=work, report=None)
            comp = []
            hist = {}
            for row in scored_report["ledger"]:
                cand = row.get("candidate") or {}
                base = row.get("baseline") or {}
                st = cand.get("status", "unevaluated")
                hist[st] = hist.get(st, 0) + 1
                if cand.get("status") == "scored" \
                        and base.get("status") == "scored" \
                        and cand.get("ln_cm") is not None \
                        and base.get("ln_cm") is not None:
                    comp.append({"row_id": row["row_id"],
                                 "c": abs(cand["ln_cm"]),
                                 "b": abs(base["ln_cm"])})
            cand_m = metrics([r["c"] for r in comp])
            base_m = metrics([r["b"] for r in comp])
            score_block = {
                "eligible_rows": scored_report["stats"].get("eligible_rows"),
                "candidate_status_histogram": hist,
                "overall": scored_report["overall"],
                "paired_row_count": len(scored_report["paired"]),
                "paired_bootstrap": scored_report["bootstrap"],
                "comparable_rows": len(comp),
                "candidate_metrics": cand_m,
                "baseline_metrics": base_m,
                "nonregression": nonreg(base_m, cand_m),
                "scored_row_ids": [r["row_id"] for r in scored_report["ledger"]
                                   if (r.get("candidate") or {})
                                   .get("status") == "scored"],
            }
            print(f"heldout {corpus}: {hist.get('scored', 0)} scored, "
                  f"{len(comp)} comparable", flush=True)

        corpora_out[corpus] = {
            "construction": construction,
            "surviving_targets": len(survivors),
            "heldout_artifact": held,
            "heldout_score": score_block,
        }

    record = {
        "schema": "actinv-p25b-g4-isomer-1",
        "retrospective": True,
        "isomeric_row_count": row_count,
        "isomeric_target_count": len(targets),
        "corpora": corpora_out,
        "g1_census_sha256": sha256(CENSUS),
        "g4_rebind_sha256": sha256(REBIND),
    }
    OUTPUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
