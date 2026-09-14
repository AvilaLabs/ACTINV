#!/usr/bin/env python3
"""P25b G4 — bounded repair, rebuild, retrospective scoring.

Stages (resumable; each writes its own record):

  rebind  — re-run the six Amendment-1 rows at the frozen 600 s
            bound; writes results/g4_p25b_rebind.json.
  build   — stage every surviving target source per corpus and run
            one directory build per corpus into a candidate NPZ;
            writes results/g4_p25b_build.json.
  score   — re-parse nothing: rebuild the P24 fold context once and
            run the unchanged ``p24_scorer.score_fresh_partition``
            once per corpus with that corpus's candidate NPZ as the
            ``candidate`` library and the hash-pinned v1.0.1 NPZ as
            ``baseline``; writes results/g4_p25b_score.json with
            per-corpus ledgers, comparable-row metrics and the
            frozen nonregression gates evaluated.

All scoring is retrospective: every benchmark partition is consumed
evidence and the record says so.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25b-g4"
ACTINV = os.environ.get("ACTINV_BIN", "actinv")
REBIND_BOUND_S = 600.0
DIR_BUILD_TIMEOUT_S = 7200.0

ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": {"root": ND / "tendl-2023" / "files", "format": "tendl"},
    "fendl_32c": {"root": ND / "fendl-3.2c" / "endf", "format": "tendl"},
    "eaf_2010": {"root": ND / "eaf-2010" / "files", "format": "eaf"},
}

CENSUS = RESULTS / "g1_p25b_census.json"
TRACES = RESULTS / "g2_p25b_traces.json"
REBIND = RESULTS / "g4_p25b_rebind.json"
BUILD = RESULTS / "g4_p25b_build.json"
SCORE = RESULTS / "g4_p25b_score.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(args: list[str], timeout: float) -> dict:
    t0 = time.monotonic()
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, cwd=ROOT)
        return {"exit": proc.returncode,
                "seconds": round(time.monotonic() - t0, 3),
                "message": (proc.stderr.strip() or
                            proc.stdout.strip())[-600:]}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout",
                "seconds": round(time.monotonic() - t0, 3),
                "message": f"timeout {timeout}s"}


def stage_rebind() -> dict:
    census = json.loads(CENSUS.read_text())
    traces = json.loads(TRACES.read_text())
    timeouts = {(t["corpus"], t["target"]) for t in traces["traces"]
                if t["adjudication"].startswith("bound_limited_timeout")}
    rows = []
    for row in census["ledger"]:
        key = (row["corpus"], row["target"])
        if key not in timeouts:
            continue
        src = CORPORA[row["corpus"]]["root"] / row["file"]
        out = WORK / "rebind.npz"
        out.unlink(missing_ok=True)
        rec = run([ACTINV, "build-library", str(src), str(out),
                   "--format", CORPORA[row["corpus"]]["format"],
                   "--projectile", "neutron", "--groups", "fispact-709",
                   "--temperature-K", "293.6"], REBIND_BOUND_S)
        out.unlink(missing_ok=True)
        rows.append({
            "corpus": row["corpus"], "target": row["target"],
            "file": row["file"], "file_sha256": row["file_sha256"],
            "bound_s": REBIND_BOUND_S, **rec,
            "class": "ok" if rec["exit"] == 0 else "construction_error",
        })
        print(f"rebind {row['corpus']} {row['target']}: "
              f"{rows[-1]['class']} ({rec['seconds']}s)", flush=True)
    record = {"schema": "actinv-p25b-g4-rebind-1",
              "bound_s": REBIND_BOUND_S, "rows": rows}
    REBIND.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def ok_targets(census: dict, rebind: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {c: [] for c in CORPORA}
    rebound = {(r["corpus"], r["target"]): r["class"] == "ok"
               for r in rebind["rows"]}
    for row in census["ledger"]:
        cls = row["class"]
        if cls != "ok" and rebound.get((row["corpus"], row["target"])):
            cls = "ok"
        if cls == "ok" and row.get("file"):
            out[row["corpus"]].append(row)
    return out


def stage_build(rebind: dict) -> dict:
    census = json.loads(CENSUS.read_text())
    targets = ok_targets(census, rebind)
    artifacts = {}
    for corpus, spec in CORPORA.items():
        stage = WORK / f"stage-{corpus}"
        stage.mkdir(parents=True, exist_ok=True)
        for leftover in stage.iterdir():
            leftover.unlink()
        for row in targets[corpus]:
            src = spec["root"] / row["file"]
            dst = stage / row["file"]
            if not dst.exists() or sha256(dst) != row["file_sha256"]:
                shutil.copyfile(src, dst)
        npz = WORK / f"candidate_{corpus}.npz"
        npz.unlink(missing_ok=True)
        rec = run([ACTINV, "build-library", str(stage), str(npz),
                   "--format", spec["format"], "--projectile", "neutron",
                   "--groups", "fispact-709", "--temperature-K", "293.6",
                   "--workers", "2", "--cache", str(WORK / "cache")],
                  DIR_BUILD_TIMEOUT_S)
        index = npz.with_name(npz.stem + "_index.json")
        if not index.is_file():
            index = Path(str(npz) + ".index.json")
        idx = json.loads(index.read_text()) if index.is_file() else None
        artifacts[corpus] = {
            "staged_files": len(list(stage.iterdir())),
            "build": rec,
            "npz": str(npz),
            "npz_sha256": sha256(npz) if npz.is_file() else None,
            "index": str(index) if index.is_file() else None,
            "index_sha256": sha256(index) if index.is_file() else None,
            "index_targets": sorted(t["za"] for t in idx["targets"])
                             if idx else None,
        }
        print(f"build {corpus}: exit {rec['exit']} "
              f"({rec['seconds']}s), {artifacts[corpus]['staged_files']} files",
              flush=True)
    record = {"schema": "actinv-p25b-g4-build-1",
              "artifacts": artifacts}
    BUILD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def percentile(values, pct):
    if not values:
        return float("nan")
    return float(np.percentile(np.asarray(values, float), pct))


def row_metrics(vals) -> dict:
    vals = [v for v in vals if math.isfinite(v)]
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
    elif not c.get("rows"):
        res["unscored_candidate"] = True
    return res


def stage_score(build: dict) -> dict:
    import p24_definitions
    import p24_scorer
    from g3_p24_diagnostic import (
        BASELINE_INDEX_SHA256, BASELINE_NPZ_SHA256, VARIANTS, p17_mapping)
    from p17_heldout import (
        catalog_record, checked_inputs as p17_checked_inputs,
        decay_by_identity, extracted_inputs as p17_extracted_inputs,
        fold_group_response, production_spectrum_response,
        selected_endf_catalog, _production_row)
    from p17_irdff import (
        ProductionLibrary, PRODUCTION_INDEX, PRODUCTION_LIBRARY)
    from p24_scorer import (
        I0_HIGH_EV, I0_LOW_EV, SIGMA0_EV, TABLE_SPECS, load_alias_index,
        parse_fresh_table, row_binding)
    from g4_p24_fresh import _histogram, fresh_spectra, pointwise_i0
    from endf_common import interp_eval

    defs = json.loads((RESULTS / "g1_p24_definitions.json").read_text())
    if sha256(PRODUCTION_LIBRARY) != BASELINE_NPZ_SHA256 \
            or sha256(PRODUCTION_INDEX) != BASELINE_INDEX_SHA256:
        raise RuntimeError("v1.0.1 baseline artifact identity mismatch")

    p17_checked_inputs()
    alias_index = load_alias_index(defs)
    parsed_tables = {t: parse_fresh_table(t) for t in sorted(TABLE_SPECS)}

    monitor_labels = {p24_scorer.MONITOR_LABEL}
    bindings = {}
    for table, parsed in parsed_tables.items():
        for row in parsed:
            if row.get("monitor_label"):
                monitor_labels.add(row["monitor_label"])
            reaction = row.get("reaction_label") or row.get("label", "")
            try:
                binding = row_binding(reaction, alias_index)
            except ValueError:
                binding = None
            if binding is not None and not binding.get("unbound"):
                bindings[reaction] = binding
    monitor_bindings = {}
    for label in sorted(monitor_labels):
        try:
            binding = row_binding(label, alias_index)
        except ValueError:
            binding = None
        if binding is not None and not binding.get("unbound"):
            monitor_bindings[label] = binding
    all_mappings = [p17_mapping(b) for b in bindings.values()]
    all_mappings += [p17_mapping(b) for b in monitor_bindings.values()]

    corpora_out = {}
    with p17_extracted_inputs() as paths:
        decays = decay_by_identity(paths["decay"])
        spectra = fresh_spectra(paths["spectrum"])
        group_mf3, group_mf10 = selected_endf_catalog(
            paths["group"], all_mappings)
        point_mf3, point_mf10 = selected_endf_catalog(
            paths["pointwise"], all_mappings)

        def fold_response(binding, spectrum):
            return fold_group_response(
                p17_mapping(binding), group_mf3, group_mf10, spectrum)

        def production_response(lib, binding, spectrum):
            value, reason, evidence = production_spectrum_response(
                lib, p17_mapping(binding), spectrum)
            return value, evidence, reason

        def thermal_response(binding, kind):
            mapping = p17_mapping(binding)
            if mapping["kind"] != "simple":
                return None, [], "variant_reaction_unavailable"
            record = catalog_record(mapping, point_mf3, point_mf10)
            if record is None:
                return None, [], "variant_reaction_unavailable"
            if mapping["product_lfs"] is None:
                key = [int(mapping["target_za"]), 3, int(mapping["mt"])]
            else:
                key = [int(mapping["target_za"]), 10, int(mapping["mt"]),
                       int(mapping["product_za"]),
                       int(mapping["raw_evaluation_lfs"])]
            if kind == "sigma0":
                value = float(interp_eval(
                    np.asarray(record[7], float),
                    np.asarray(record[8], float),
                    record[6], np.array([SIGMA0_EV]))[0])
            else:
                value = pointwise_i0(record)
            return value, [key], "scored"

        def production_thermal(lib, binding, kind):
            mapping = p17_mapping(binding)
            if mapping["kind"] != "simple":
                return None, [], "variant_reaction_unavailable"
            sigma, evidence = _production_row(lib, mapping)
            if sigma is None:
                return None, [], str(evidence["reason"])
            if kind == "sigma0":
                index = int(np.clip(
                    np.searchsorted(lib.bounds, SIGMA0_EV, side="right") - 1,
                    0, len(lib.bounds) - 2))
                value = float(sigma[index])
            else:
                lo = np.maximum(lib.bounds[:-1], I0_LOW_EV)
                hi = np.minimum(lib.bounds[1:], I0_HIGH_EV)
                weights = np.where(hi > lo, np.log(hi / lo), 0.0)
                value = float(np.dot(sigma, weights))
            return value, [evidence], "scored"

        for corpus, art in build["artifacts"].items():
            if not art.get("npz_sha256"):
                corpora_out[corpus] = {"status": "no_artifact"}
                continue
            context = {
                "alias_index": alias_index,
                "decay_by_id": decays,
                "spectra": spectra,
                "fold_response": fold_response,
                "production_response": production_response,
                "thermal_response": thermal_response,
                "production_thermal": production_thermal,
                "monitor_bindings": monitor_bindings,
                "libraries": {
                    "official": None,
                    "candidate": ProductionLibrary(
                        Path(art["npz"]), Path(art["index"])),
                    "baseline": ProductionLibrary(
                        PRODUCTION_LIBRARY, PRODUCTION_INDEX),
                },
                "input_set_ids": {
                    "official": VARIANTS["official"]["input_set_id"],
                    "candidate": f"p25b_{corpus}",
                    "baseline": VARIANTS["baseline"]["input_set_id"],
                },
                "interpretations": {
                    k: v["interpretation"] for k, v in VARIANTS.items()},
            }
            result = p24_scorer.score_fresh_partition(
                defs, context=context, parsed_tables=parsed_tables)

            # Comparable set: rows scored by BOTH candidate and baseline.
            rows = result["rows"]
            comparable = []
            reason_hist = {}
            for row in rows:
                cand = row["calculations"].get("candidate", {})
                base = row["calculations"].get("baseline", {})
                if cand.get("status") == "scored":
                    reason_hist["scored"] = reason_hist.get("scored", 0) + 1
                else:
                    reason = cand.get("reason") or "unscored"
                    reason_hist[reason] = reason_hist.get(reason, 0) + 1
                if cand.get("status") == "scored" \
                        and base.get("status") == "scored":
                    comparable.append({
                        "row_id": row["row_id"],
                        "candidate_ce": cand["ratio_C_over_E"],
                        "candidate_abs_ln": abs(cand["signed_log_C_over_E"]),
                        "baseline_ce": base["ratio_C_over_E"],
                        "baseline_abs_ln": abs(base["signed_log_C_over_E"]),
                    })
            cand_m = row_metrics(
                [r["candidate_abs_ln"] for r in comparable])
            base_m = row_metrics(
                [r["baseline_abs_ln"] for r in comparable])
            corpora_out[corpus] = {
                "status": "scored",
                "rows": len(rows),
                "candidate_reason_histogram": reason_hist,
                "candidate_scored": reason_hist.get("scored", 0),
                "baseline_scored": sum(
                    1 for r in rows
                    if (r["calculations"].get("baseline") or {})
                    .get("status") == "scored"),
                "comparable_rows": len(comparable),
                "candidate_metrics": cand_m,
                "baseline_metrics": base_m,
                "nonregression": nonreg(base_m, cand_m),
                "scored_row_ids": [
                    r["row_id"] for r in rows
                    if (r["calculations"].get("candidate") or {})
                    .get("status") == "scored"],
                "comparable_row_ids": [r["row_id"] for r in comparable],
                "family_metrics": result.get("family_metrics"),
                "inclusion_histogram": _histogram(rows),
            }
            print(f"score {corpus}: {corpora_out[corpus]['candidate_scored']}"
                  f" scored, {len(comparable)} comparable", flush=True)

    record = {
        "schema": "actinv-p25b-g4-score-1",
        "retrospective": True,
        "label": ("all scoring is retrospective: every benchmark "
                  "partition is consumed evidence"),
        "g1_census_sha256": sha256(CENSUS),
        "g4_build_sha256": sha256(BUILD),
        "corpora": corpora_out,
    }
    SCORE.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["rebind", "build", "score", "all"])
    args = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "cache").mkdir(exist_ok=True)

    if args.stage in ("rebind", "all") or not REBIND.is_file():
        rebind = stage_rebind()
    else:
        rebind = json.loads(REBIND.read_text())
    if args.stage == "rebind":
        return 0
    if args.stage in ("build", "all") or not BUILD.is_file():
        build = stage_build(rebind)
    else:
        build = json.loads(BUILD.read_text())
    if args.stage == "build":
        return 0
    if args.stage in ("score", "all"):
        stage_score(build)
    return 0


if __name__ == "__main__":
    sys.exit(main())
