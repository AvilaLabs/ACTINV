#!/usr/bin/env python3
"""P136 checker: opt-in `study.robustness.statistics` (sampling distributions and correlation tables).

Base study: the P30 sampling study (~/nuclear-data/p30-work/g1/sampling_study.json), repointed to the v1.1.0
TENDL-2025 library (absolute path, SHA-256 computed), the v1.1.0 ENDF/B-VIII.0 and JEFF-3.3 decay files and the
v1.1.0 TENDL-2025 covariance.

G2  The default study (no `statistics`) run with the reference and the candidate gives the same files. Differing
    bytes are allowed only in timing and the hashes that cover timing, and nothing else:
      cases/*/out.json, rob_*.out.json, attr_*.out.json  top-level `ms`
      cases/*/rob_samples.ndjson                          `out_sha256` (hash of a rob_N.out.json, which carries `ms`)
      study_record.json                                   cases[].wall_s, prepare_wall_s, tool.binary_sha256,
                                                          cases[].out_sha256, cases[].robustness.sample_artifacts[]
                                                          .out_sha256
    Every stripped `out_sha256` must equal the SHA-256 of the file it names in the same output directory, and the
    two `.out.json` sets must be equal once `ms` is removed, so a hash differs only because `ms` differs. All other
    files (manifest.json, specs/*, rob_N.json, attr_N.json) must be byte-identical and neither record may carry a
    `sampling_statistics` block.
G3  The candidate is run with `statistics` on two studies (16 samples with explicit percentiles including 0 and 100,
    8 histogram bins; 40 samples with the default percentiles, 5 bins, 25 correlation rows and no bootstrap, which
    puts 2.5 and 97.5 exactly on ranks 1 and 39). Every distribution field is recomputed from
    `robustness.sample_values` with an independent implementation (math.fsum two-pass mean and std, moment
    skewness, nearest-rank percentiles on exact decimal arithmetic, equal-width histogram with exact rational bin
    indices, Pearson matrix over pairwise-complete samples): counts, order statistics, min, max and n exactly, the
    rest within 1e-12 relative. The convergence and first-order blocks are checked for internal consistency
    (the bootstrap itself is not reimplemented). Per-sample inputs are not exposed as a table: the record carries
    only the flux factor (`sample_artifacts`) and, through the per-sample specs, the renormalized Co weight percent.
    Pearson and Spearman against those two inputs are recomputed (average ranks for ties) and compared with the
    ranked table where the parameter is listed; a parameter that is not listed must not out-rank the last listed
    row. The MF33, decay and yield correlations cannot be recomputed from the record and that gap is reported.
G4  The statistics studies are run again with the same seed: `sampling_statistics` and `sample_values` must be
    identical to the first run.

Usage: check_p136.py --reference REF --candidate CAND [--workdir target/p136-work] [--base-study PATH]
"""
import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_STUDY = Path.home() / "nuclear-data" / "p30-work" / "g1" / "sampling_study.json"
DATA = ROOT / "actinv-data" / "v1.1.0"
LIBRARY = DATA / "activation" / "tendl-2025-neutron-709g.npz"
COVARIANCE = DATA / "uncertainty" / "tendl-2025-neutron-709g.cov.npz"
DECAY_PRIMARY = DATA / "decay" / "endf-b-viii-0_decay.dat"
DECAY_FALLBACK = DATA / "decay" / "jeff-3-3_decay.dat"
SPEC_REF = ROOT / "examples" / "fns_fe_5min.json"
# docs/STUDY.md: the percentiles used when `statistics.percentiles` is not declared
DEFAULT_PERCENTILES = [2.5, 5.0, 16.0, 50.0, 84.0, 95.0, 97.5]
REL_TOL = 1e-12
# Co weight percent recovered from the written per-sample spec: a few more ulps of rounding
INPUT_TOL = 1e-9
MAX_BAD = 20

STATISTICS_RUNS = {
    "explicit": {"samples": 16,
                 "statistics": {"percentiles": [0, 1, 2.5, 5, 10, 16, 25, 50, 75, 84, 90, 95, 97.5, 99, 100],
                                "histogram_bins": 8}},
    "default": {"samples": 40,
                "statistics": {"histogram_bins": 5, "correlation_top": 25, "bootstrap_resamples": 0}},
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --- study construction and execution -----------------------------------------------------------------------

def build_study(base, statistics=None, samples=None):
    """The base study repointed to the v1.1.0 data (absolute paths, SHA-256 computed)."""
    study = copy.deepcopy(base)
    study["library"] = {"path": str(LIBRARY), "sha256": sha256(LIBRARY)}
    study["decay"] = {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)}
    study["robustness"]["covariance"] = {"path": str(COVARIANCE), "sha256": sha256(COVARIANCE)}
    for spectrum in study["cases"]["spectra"]:
        if "spec_ref" in spectrum:
            spectrum["spec_ref"] = str(SPEC_REF)
    if samples is not None:
        study["robustness"]["samples"] = samples
    if statistics is not None:
        study["robustness"]["statistics"] = copy.deepcopy(statistics)
    return study


def fresh_dir(work, label, tag):
    """A not-yet-existing output directory (`study run` into an existing one resumes)."""
    path = work / f"{label}-{tag}"
    n = 0
    while path.exists():
        n += 1
        path = work / f"{label}-{tag}-{n}"
    return path


def run_study(binary, study_path, outdir, timeout):
    proc = subprocess.run([binary, "study", "run", str(study_path), str(outdir)], cwd=ROOT, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{binary} study run failed on {study_path}: {proc.stderr.strip()[-400:]}")
    return json.loads((outdir / "study_record.json").read_text())


# --- G2: default case files equal apart from timing ---------------------------------------------------------

def canon(value):
    return json.dumps(value, sort_keys=True)


def relative_files(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file())


def is_out_file(rel):
    return rel.endswith(".out.json") or rel.endswith("/out.json")


def strip_record(record):
    """study_record.json without its timing fields and the hashes that cover them; returns the stripped copy."""
    r = copy.deepcopy(record)
    r.pop("prepare_wall_s", None)
    r.get("tool", {}).pop("binary_sha256", None)
    for case in r.get("cases", []):
        case.pop("wall_s", None)
        case.pop("out_sha256", None)
        for art in (case.get("robustness") or {}).get("sample_artifacts", []):
            art.pop("out_sha256", None)
    return r


def self_hashes_ok(outdir, record, ndjson):
    """Every out_sha256 the record and the sample ledgers carry must be the SHA-256 of the named file."""
    bad = []
    for case in record["cases"]:
        cdir = Path(outdir) / "cases" / case["case_id"]
        if "out_sha256" in case and case["out_sha256"] != sha256(cdir / "out.json"):
            bad.append(f"{case['case_id']}/out.json")
        for art in (case.get("robustness") or {}).get("sample_artifacts", []):
            if "out_sha256" in art and art["out_sha256"] != sha256(cdir / f"rob_{art['sample']}.out.json"):
                bad.append(f"{case['case_id']}/rob_{art['sample']}.out.json (record)")
        for line in ndjson.get(case["case_id"], []):
            if "out_sha256" in line and line["out_sha256"] != sha256(cdir / f"rob_{line['sample']}.out.json"):
                bad.append(f"{case['case_id']}/rob_{line['sample']}.out.json (ndjson)")
    return bad


def read_ndjson(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def compare_default(ref_dir, cand_dir, ref_bin, cand_bin):
    ref_files, cand_files = relative_files(ref_dir), relative_files(cand_dir)
    result = {"files": len(ref_files), "same_file_set": ref_files == cand_files, "byte_identical": 0,
              "stripped": {"ms": 0, "ndjson_out_sha256": 0, "record_fields": 0}, "differences": [],
              "self_hash_mismatches": []}
    if ref_files != cand_files:
        result["only_reference"] = sorted(set(ref_files) - set(cand_files))[:MAX_BAD]
        result["only_candidate"] = sorted(set(cand_files) - set(ref_files))[:MAX_BAD]
        result["pass"] = False
        return result
    records, ndjsons = {}, {}
    for rel in ref_files:
        a, b = Path(ref_dir) / rel, Path(cand_dir) / rel
        if a.read_bytes() == b.read_bytes():
            result["byte_identical"] += 1
            continue
        if is_out_file(rel):
            ja, jb = json.loads(a.read_text()), json.loads(b.read_text())
            if "ms" not in ja or "ms" not in jb:
                result["differences"].append(f"{rel}: differs without a top-level ms")
                continue
            ja.pop("ms")
            jb.pop("ms")
            result["stripped"]["ms"] += 1
            if canon(ja) != canon(jb):
                result["differences"].append(f"{rel}: differs beyond ms")
        elif rel.endswith("rob_samples.ndjson"):
            la, lb = read_ndjson(a), read_ndjson(b)
            if len(la) != len(lb):
                result["differences"].append(f"{rel}: line count")
                continue
            for x, y in zip(la, lb):
                x, y = dict(x), dict(y)
                if x.pop("out_sha256", None) != y.pop("out_sha256", None):
                    result["stripped"]["ndjson_out_sha256"] += 1
                if canon(x) != canon(y):
                    result["differences"].append(f"{rel}: sample {x.get('sample')} differs beyond out_sha256")
        elif rel == "study_record.json":
            ja, jb = json.loads(a.read_text()), json.loads(b.read_text())
            if canon(strip_record(ja)) != canon(strip_record(jb)):
                result["differences"].append("study_record.json: differs beyond timing and out_sha256")
            result["stripped"]["record_fields"] += 1
        else:
            result["differences"].append(f"{rel}: bytes differ")
    for label, outdir, binary in (("reference", ref_dir, ref_bin), ("candidate", cand_dir, cand_bin)):
        record = json.loads((Path(outdir) / "study_record.json").read_text())
        records[label] = record
        ndjsons[label] = {c["case_id"]: read_ndjson(Path(outdir) / "cases" / c["case_id"] / "rob_samples.ndjson")
                          for c in record["cases"]
                          if (Path(outdir) / "cases" / c["case_id"] / "rob_samples.ndjson").exists()}
        bad = self_hashes_ok(outdir, record, ndjsons[label])
        result["self_hash_mismatches"] += [f"{label}: {b}" for b in bad]
        result[f"{label}_binary_sha256_matches"] = record.get("tool", {}).get("binary_sha256") == sha256(binary)
        result[f"{label}_has_sampling_statistics"] = any(
            "sampling_statistics" in (c.get("robustness") or {}) for c in record["cases"])
    result["pass"] = (not result["differences"] and not result["self_hash_mismatches"]
                      and result["stripped"]["ms"] > 0
                      and not result["reference_has_sampling_statistics"]
                      and not result["candidate_has_sampling_statistics"])
    result["differences"] = result["differences"][:MAX_BAD]
    return result


# --- independent statistics ----------------------------------------------------------------------------------

def close(a, b, tol=REL_TOL):
    """Equal as JSON values: both missing, or numbers within `tol` relative to the expected value."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    scale = abs(b) if b != 0 else 1.0
    return abs(a - b) <= tol * scale


def mean_of(x):
    return math.fsum(x) / len(x)


def std_of(x):
    """Unbiased standard deviation about the exactly rounded mean."""
    m = mean_of(x)
    return math.sqrt(math.fsum((v - m) * (v - m) for v in x) / (len(x) - 1))


def skewness_of(x):
    """m3 / m2^1.5 with population central moments; None below three values or for a constant sample."""
    if len(x) < 3:
        return None
    n, m = len(x), mean_of(x)
    m2 = math.fsum((v - m) ** 2 for v in x) / n
    m3 = math.fsum((v - m) ** 3 for v in x) / n
    return m3 / (m2 * math.sqrt(m2)) if m2 > 0 else None


def nearest_rank(sorted_x, p):
    """x_(k), k = max(1, ceil(n p / 100)), with p taken as the decimal it was written as."""
    n = len(sorted_x)
    k = max(1, math.ceil(Fraction(n) * Fraction(str(p)) / 100))
    return sorted_x[min(k, n) - 1]


def percentile_key(p):
    """The JSON object key of a percentile (`2.5`, `16`, `97.5`)."""
    return str(int(p)) if float(p).is_integer() else repr(float(p))


def histogram_of(x, bins):
    """Equal-width bins over [min, max], last bin closed on the right; bin index from exact rational arithmetic."""
    lo, hi = min(x), max(x)
    width = hi - lo
    counts_exact, counts_float = [0] * bins, [0] * bins
    for v in x:
        if width > 0:
            idx = min(math.floor(Fraction(v - lo) * bins / Fraction(width)), bins - 1)
            idx_f = min(math.floor((v - lo) / width * bins), bins - 1)
        else:
            idx = idx_f = 0
        counts_exact[idx] += 1
        counts_float[idx_f] += 1
    edges = [hi if i == bins else lo + width * i / bins for i in range(bins + 1)]
    return edges, counts_exact, counts_float


def pearson_of(x, y):
    """Two-pass Pearson over equal-length lists; None below two pairs or for a constant side."""
    if len(x) != len(y) or len(x) < 2:
        return None
    mx, my = mean_of(x), mean_of(y)
    dx, dy = [a - mx for a in x], [b - my for b in y]
    sxx, syy = math.fsum(a * a for a in dx), math.fsum(b * b for b in dy)
    if not (sxx > 0 and syy > 0):
        return None
    r = math.fsum(a * b for a, b in zip(dx, dy)) / (math.sqrt(sxx) * math.sqrt(syy))
    return max(-1.0, min(1.0, r))


def average_ranks(x):
    """1-based ranks, ties sharing the mean of the positions they span."""
    order = sorted(range(len(x)), key=lambda i: x[i])
    ranks = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return ranks


def spearman_of(x, y):
    return pearson_of(average_ranks(x), average_ranks(y))


def defined(sample, tkey, response):
    """The response value of one sample, or None for a failed sample or a nonfinite value."""
    if sample is None:
        return None
    v = (sample.get(tkey) or {}).get(response)
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


# --- G3: recomputation ---------------------------------------------------------------------------------------

def recovered_inputs(outdir, case, nominal_spec, rb):
    """Sampled inputs the written files let one recompute, as {name: [ln factor or None per sample]}.

    flux: ln of `sample_artifacts[i].flux_factor`.
    composition:Co: the draw f behind the renormalized weight percent w' = T c f / (T - c + c f), recovered from the
    per-sample spec as f = w' (T - c) / (c (T - w')), when Co is in the nominal material and is the only varied
    element."""
    n = len(rb["sample_values"])
    out = {}
    flux = [None] * n
    for art in rb["sample_artifacts"]:
        if "flux_factor" in art and art["flux_factor"] > 0:
            flux[art["sample"]] = math.log(art["flux_factor"])
    out["flux"] = flux
    varied = {el for el, s in rb["channels"]["composition_rel_std"].items() if s > 0}
    comp = nominal_spec["material"]["composition"]
    if varied == {"Co"} and comp.get("Co", 0) > 0:
        total = math.fsum(comp.values())
        c = comp["Co"]
        col = [None] * n
        for i in range(n):
            path = Path(outdir) / "cases" / case["case_id"] / f"rob_{i}.json"
            if path.exists():
                w = json.loads(path.read_text())["material"]["composition"].get("Co")
                if w is not None and 0 < w < total:
                    col[i] = math.log(w * (total - c) / (c * (total - w)))
        out["composition:Co"] = col
    return out


def check_input_correlations(entry, y, columns, top_n, tolerances):
    """Compare the exposed inputs against the ranked table. Returns (checked entries, failures)."""
    rows = entry["input_correlations"]["top"]
    bad, checked = [], {}
    for name, col in columns.items():
        idx = [i for i, (c, v) in enumerate(zip(col, y)) if c is not None and v is not None]
        if len(idx) < 2:
            continue
        xs, ys = [col[i] for i in idx], [y[i] for i in idx]
        mine = (pearson_of(xs, ys), spearman_of(xs, ys))
        if mine[0] is None or mine[1] is None:
            continue
        listed = [r for r in rows if r["parameter"].get("name") == name]
        if listed:
            r = listed[0]
            ok = close(r["pearson"], mine[0], INPUT_TOL) and close(r["spearman"], mine[1], INPUT_TOL)
            checked[name] = {"listed": True, "pearson": mine[0], "spearman": mine[1], "match": ok}
            if not ok:
                bad.append({"parameter": name, "recorded": [r["pearson"], r["spearman"]], "recomputed": mine})
        else:
            # not listed: it may not out-rank the last listed row (rows are cut at correlation_top)
            full = len(rows) >= top_n
            floor_abs = abs(rows[-1]["spearman"]) if rows else 0.0
            exceeds = full and abs(mine[1]) > floor_abs * (1 + tolerances) + tolerances
            checked[name] = {"listed": False, "pearson": mine[0], "spearman": mine[1],
                             "abs_spearman_exceeds_last_listed": exceeds}
            if exceeds:
                bad.append({"parameter": name, "reason": "absent from the table but out-ranks its last row",
                            "recomputed": mine, "last_listed_abs_spearman": floor_abs})
    return checked, bad


def verify_case(outdir, case, study, statistics):
    """Recompute every sampling_statistics field of one case record; returns the evidence dict."""
    rb = case["robustness"]
    ss = rb["sampling_statistics"]
    values = rb["sample_values"]
    n_total = len(values)
    bad, edge_cases = [], []
    counts = {"distribution_fields": 0, "percentiles": 0, "histogram_bins": 0, "matrix_entries": 0,
              "consistency_checks": 0, "input_correlation_rows": 0}

    def expect(label, got, want, tol=REL_TOL):
        if not close(got, want, tol):
            bad.append({"field": label, "recorded": got, "recomputed": want})

    def expect_eq(label, got, want):
        if got != want:
            bad.append({"field": label, "recorded": got, "recomputed": want})

    expect_eq("samples", ss["samples"], n_total)
    expect_eq("samples_succeeded", ss["samples_succeeded"], sum(1 for v in values if v is not None))
    expect_eq("samples_succeeded_vs_failed", ss["samples_succeeded"], n_total - rb["n_failed_samples"])
    declared = study["robustness"]["responses"]
    pcts = statistics.get("percentiles", DEFAULT_PERCENTILES)
    bins = statistics.get("histogram_bins", 0)
    top_n = statistics.get("correlation_top", 10)
    boot = statistics.get("bootstrap_resamples", 200)
    times = sorted({t for v in values if v is not None for t in v}, key=float)
    expect_eq("responses", sorted(ss["responses"]), sorted(declared))
    nominal_spec = json.loads((Path(outdir) / "specs" / f"{case['case_id']}.json").read_text())
    columns = recovered_inputs(outdir, case, nominal_spec, rb)
    input_report = {}

    series_labels, series = [], []
    # series are labelled in the declared response order, then ascending time
    for response in declared:
        if response not in ss["responses"]:
            continue
        expect_eq(f"{response}: time keys", sorted(ss["responses"][response], key=float), times)
        for tkey in times:
            entry = ss["responses"][response][tkey]
            tag = f"{response}@{tkey}"
            y = [defined(v, tkey, response) for v in values]
            x = [v for v in y if v is not None]
            series_labels.append(tag)
            series.append(y)
            d = entry["distribution"]
            n = len(x)
            expect_eq(f"{tag} n", d["n"], n)
            if n == 0:
                continue
            sx = sorted(x)
            counts["distribution_fields"] += 6
            expect(f"{tag} mean", d["mean"], mean_of(x))
            expect(f"{tag} std", d["std"], std_of(x) if n > 1 else None)
            expect(f"{tag} skewness", d["skewness"], skewness_of(x))
            expect_eq(f"{tag} min", d["min"], sx[0])
            expect_eq(f"{tag} max", d["max"], sx[-1])
            expect_eq(f"{tag} percentile keys", sorted(float(k) for k in d["percentiles"]),
                      sorted({float(p) for p in pcts}))
            for p in pcts:
                counts["percentiles"] += 1
                expect_eq(f"{tag} p{p}", d["percentiles"].get(percentile_key(p)), nearest_rank(sx, p))
            if bins > 0:
                edges, exact, flt = histogram_of(x, bins)
                h = d.get("histogram")
                if h is None:
                    bad.append({"field": f"{tag} histogram", "recorded": None})
                else:
                    counts["histogram_bins"] += bins
                    # the float formula and exact rational indices can only differ for a value on a bin edge
                    if h["counts"] not in (exact, flt):
                        bad.append({"field": f"{tag} histogram counts", "recorded": h["counts"], "recomputed": exact,
                                    "float_formula": flt})
                    elif exact != flt:
                        edge_cases.append(tag)
                    expect_eq(f"{tag} histogram edge count", len(h["bin_edges"]), bins + 1)
                    for i, (got, want) in enumerate(zip(h["bin_edges"], edges)):
                        expect(f"{tag} histogram edge {i}", got, want)
                    expect_eq(f"{tag} histogram total", sum(h["counts"]), n)
            else:
                expect_eq(f"{tag} no histogram", "histogram" in d, False)

            # convergence and first-order blocks: relations the record must satisfy (bootstrap not reimplemented)
            sd = d["std"]
            if boot > 0:
                conv = entry.get("convergence")
                if conv is None or "std_standard_error" not in conv:
                    bad.append({"field": f"{tag} convergence", "recorded": conv})
                    conv = {}
                else:
                    counts["consistency_checks"] += 3
                    expect_eq(f"{tag} resamples", conv["resamples"], boot)
                    if sd and sd > 0:
                        expect(f"{tag} relative std se", conv["std_relative_standard_error"],
                               conv["std_standard_error"] / sd)
                    expect_eq(f"{tag} percentile se keys", sorted(conv["percentile_standard_error"]),
                              sorted(d["percentiles"]))
                    if not all(v >= 0 and math.isfinite(v) for v in
                               [conv["std_standard_error"], *conv["percentile_standard_error"].values()]):
                        bad.append({"field": f"{tag} bootstrap standard errors", "note": "negative or nonfinite"})
            else:
                expect_eq(f"{tag} no convergence", "convergence" in entry, False)
                conv = {}
            lin = (rb["local_vs_nonlinear"].get("comparisons", {}).get(response, {}).get(tkey) or {}) \
                if isinstance(rb.get("local_vs_nonlinear"), dict) else {}
            fo = entry.get("first_order")
            if "first_order_std" in lin:
                counts["consistency_checks"] += 3
                if fo is None:
                    bad.append({"field": f"{tag} first_order", "recorded": None, "expected": "block"})
                else:
                    expect(f"{tag} first_order.std", fo["std"], lin["first_order_std"])
                    expect(f"{tag} first_order.sampled_std", fo["sampled_std"], sd)
                    if lin["first_order_std"] > 0:
                        expect(f"{tag} sampled_over_first_order", fo["sampled_over_first_order"],
                               sd / lin["first_order_std"])
                    se = conv.get("std_standard_error")
                    dev_want = (sd - lin["first_order_std"]) / se if se and se > 0 else None
                    expect(f"{tag} deviation_in_std_standard_errors", fo["deviation_in_std_standard_errors"],
                           dev_want)
            else:
                expect_eq(f"{tag} first_order absent", fo, None)

            # input-output table: shape, ordering, and the inputs the record exposes
            if top_n > 0:
                ic = entry["input_correlations"]
                expect_eq(f"{tag} n_pairs", ic["n_pairs"], n)
                expect_eq(f"{tag} n_parameters", ic["n_parameters"], ss["input_parameters"])
                rows = ic["top"]
                expect_eq(f"{tag} top rows", len(rows),
                          min(top_n, ic["n_parameters"] - ic["n_constant_parameters"]))
                keys = [(abs(r["spearman"]), abs(r["pearson"])) for r in rows]
                if any(keys[i][0] < keys[i + 1][0] or (keys[i][0] == keys[i + 1][0] and keys[i][1] < keys[i + 1][1])
                       for i in range(len(keys) - 1)):
                    bad.append({"field": f"{tag} input_correlations order", "note": "not by |spearman| then |pearson|"})
                if any(abs(r["spearman"]) > 1 or abs(r["pearson"]) > 1 for r in rows):
                    bad.append({"field": f"{tag} input_correlations range"})
                if len({canon(r["parameter"]) for r in rows}) != len(rows):
                    bad.append({"field": f"{tag} input_correlations", "note": "duplicate parameter identity"})
                counts["input_correlation_rows"] += len(rows)
                checked, input_bad = check_input_correlations(entry, y, columns, top_n, 1e-9)
                input_report[tag] = checked
                bad += [dict(b, field=f"{tag} input_correlations") for b in input_bad]
            else:
                expect_eq(f"{tag} no input_correlations", "input_correlations" in entry, False)

    rc = ss["response_correlation"]
    expect_eq("response_correlation labels", rc["labels"], series_labels)
    matrix = rc["pearson"]
    expect_eq("matrix shape", [len(r) for r in matrix], [len(series)] * len(series))
    if [len(r) for r in matrix] == [len(series)] * len(series):
        for i in range(len(series)):
            for j in range(len(series)):
                pairs = [(a, b) for a, b in zip(series[i], series[j]) if a is not None and b is not None]
                want = pearson_of([a for a, _ in pairs], [b for _, b in pairs])
                counts["matrix_entries"] += 1
                expect(f"pearson[{series_labels[i]}][{series_labels[j]}]", matrix[i][j], want)
                if matrix[i][j] != matrix[j][i]:
                    bad.append({"field": f"pearson[{i}][{j}]", "note": "not symmetric"})
    return {"case_id": case["case_id"], "samples": n_total, "series": series_labels, "counts": counts,
            "recomputed_inputs": {k: sum(v is not None for v in col) for k, col in columns.items()},
            "input_correlation_checks": input_report, "histogram_edge_ambiguous": edge_cases,
            "bad": bad[:MAX_BAD], "n_bad": len(bad),
            "pass": not bad and counts["distribution_fields"] > 0 and counts["matrix_entries"] > 0}


# --- main ----------------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--workdir", default="target/p136-work")
    ap.add_argument("--base-study", default=str(BASE_STUDY))
    ap.add_argument("--out", default="results/p136_sampling_statistics.json")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)
    ref = str(Path(args.reference).resolve())
    cand = str(Path(args.candidate).resolve())
    work = (ROOT / args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    base_path = Path(args.base_study).resolve()
    base = json.loads(base_path.read_text())
    tag = f"{int(time.time())}-{os.getpid()}"
    gates = {}

    # G2
    default = build_study(base)
    default_path = work / f"default-{tag}.json"
    default_path.write_text(json.dumps(default, indent=1) + "\n")
    ref_dir, cand_dir = fresh_dir(work, "default-ref", tag), fresh_dir(work, "default-cand", tag)
    run_study(ref, default_path, ref_dir, args.timeout)
    run_study(cand, default_path, cand_dir, args.timeout)
    g2 = compare_default(ref_dir, cand_dir, ref, cand)
    g2["study_sha256"] = sha256(default_path)
    g2["outdirs"] = [str(ref_dir), str(cand_dir)]
    gates["G2"] = g2

    # G3 / G4
    g3_cases, g4_cases = {}, {}
    for label, variant in STATISTICS_RUNS.items():
        study = build_study(base, statistics=variant["statistics"], samples=variant["samples"])
        path = work / f"stats-{label}-{tag}.json"
        path.write_text(json.dumps(study, indent=1) + "\n")
        first, second = fresh_dir(work, f"stats-{label}-a", tag), fresh_dir(work, f"stats-{label}-b", tag)
        rec1 = run_study(cand, path, first, args.timeout)
        rec2 = run_study(cand, path, second, args.timeout)
        cases = [verify_case(first, c, study, variant["statistics"]) for c in rec1["cases"]]
        g3_cases[label] = {"study_sha256": sha256(path), "outdir": str(first), "statistics": variant["statistics"],
                           "samples": variant["samples"], "cases": cases,
                           "pass": bool(cases) and all(c["pass"] for c in cases)}
        pairs, diffs = 0, []
        for c1, c2 in zip(rec1["cases"], rec2["cases"]):
            pairs += 1
            same_stats = canon(c1["robustness"]["sampling_statistics"]) == canon(c2["robustness"]["sampling_statistics"])
            same_values = canon(c1["robustness"]["sample_values"]) == canon(c2["robustness"]["sample_values"])
            if not (same_stats and same_values) or c1["case_id"] != c2["case_id"]:
                diffs.append({"case_id": c1["case_id"], "statistics_equal": same_stats,
                              "sample_values_equal": same_values})
        g4_cases[label] = {"outdirs": [str(first), str(second)], "cases_compared": pairs,
                           "count_matches": len(rec1["cases"]) == len(rec2["cases"]), "differences": diffs,
                           "pass": pairs > 0 and not diffs and len(rec1["cases"]) == len(rec2["cases"])}
    gap = ("MF33 cross-section, decay-constant and fission-yield sampled factors are not exposed as a per-sample "
           "input table in the record (sample_inputs is internal to the run): their Pearson/Spearman rows are not "
           "recomputed. Only flux (sample_artifacts.flux_factor) and, when Co is a varied nominal element, the "
           "renormalized Co weight percent in rob_N.json are recoverable, and those are recomputed.")
    gates["G3"] = {"pass": all(v["pass"] for v in g3_cases.values()), "runs": g3_cases,
                   "input_correlation_gap": gap}
    gates["G4"] = {"pass": all(v["pass"] for v in g4_cases.values()), "runs": g4_cases,
                   "note": "a different bootstrap seed is not exposed, so only the repeat is gated"}

    verdict = all(gates[g]["pass"] for g in ("G2", "G3", "G4"))
    record = {"protocol": "protocols/ACTINV-P136_PROTOCOL.md",
              "protocol_sha256": sha256(ROOT / "protocols/ACTINV-P136_PROTOCOL.md"),
              "reference_sha256": sha256(ref), "candidate_sha256": sha256(cand),
              "checker_sha256": sha256(__file__), "base_study_sha256": sha256(base_path), "gates": gates,
              "verdict": "PASS" if verdict else "FAIL"}
    out = ROOT / args.out
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for g in ("G2", "G3", "G4"):
        print(f"P136 {g}: {'PASS' if gates[g]['pass'] else 'FAIL'}")
    print(f"  G2: {g2['files']} files, {g2['byte_identical']} byte-identical, {g2['stripped']['ms']} out files "
          f"equal apart from ms, {len(g2['differences'])} other differences")
    for label, run in g3_cases.items():
        for c in run["cases"]:
            k = c["counts"]
            print(f"  G3/{label}/{c['case_id']}: {k['distribution_fields']} distribution fields, {k['percentiles']} "
                  f"percentiles, {k['histogram_bins']} bins, {k['matrix_entries']} matrix entries, "
                  f"{k['consistency_checks']} consistency checks, {c['n_bad']} mismatches")
    print("  G3 gap: input correlations against MF33/decay/yield parameters are not recomputable from the record")
    for label, run in g4_cases.items():
        print(f"  G4/{label}: {run['cases_compared']} cases repeated, {len(run['differences'])} differences")
    print(f"P136: {record['verdict']} ({out})")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
