#!/usr/bin/env python3
"""P158 checker (protocols/ACTINV-P158_PROTOCOL.md): nuclear-data uncertainty bands for `actinv impurities`.
Gates G0 and G2-G8 are evaluated here; G1 (fmt, check, clippy, tests; `--g1-log` records a log) and G9 (CI on the
pushed evidence commit) are external.

Independence: the order-statistic index and the Clopper-Pearson bounds are this repository's independent Python
implementation (controls/check_p146.py over controls/check_p145.py: 60-digit binomial tail, incomplete beta), not the
Rust code. The bands are recomputed from the kept per-sample documents (`keep_samples`), the linear endpoints from
the checker's own `actinv run` solves of the full alloy with a sample's recorded factors.

  G0  This protocol's SHA-256 (and that of any protocols/ACTINV-P158_AMENDMENT_*.md present) is in
      protocols/protocol_hash.txt.
  G1  External, recorded with --g1-log.
  G2  Existing behaviour unchanged. The reference and the candidate give identical exit codes and outputs, apart from
      the keys `ms`, `elapsed_ms`, `wall_time_s`, `cells_per_s` (any depth), for `actinv impurities` without
      `sampling` on the worked example and on P157's three other cases (tungsten, vanadium, reduced RA steel; built by
      controls/check_p157.py, limits set from pure-element runs of the candidate) and for
      `actinv waste budget uncertainty` on P146's committed specification (controls/p146_inputs/d1_steel.json,
      staged by controls/check_p146.py).
  G3  Common factors. The G4 case is run with `keep_samples`. For every sample, every element solve's recorded
      `factors` equal the sample's `factors` block on every key (flux_factor, rate_scale, decay_scale, yield_scale),
      no solve carries an error, and the blocks are non-trivial and differ between samples.
  G4  Per-sample linearity. The case is the worked example reduced to 20 samples with `confidence_level` 0.5 (the
      order-statistic minimum at 0.95 is 72; at 0.5 the minimum is 2 and k = 8 of 20) and `--no-verify`. Five samples
      are chosen: the lowest, the median (n // 2) and the highest index and two at random from the seed
      (random.Random(seed)). For each, every target and every linear endpoint (activity.total, heat.total,
      activity:<nuclide>, clearance_index) the checker builds the full-alloy spec with the sample's recorded factors
      (options.rate_scale / decay_scale / yield_scale, spectrum.total or group fluxes times flux_factor, as
      study::apply_factor_block) and runs `actinv run` at the matrix-only composition, the nominal composition and the
      sample's own worst-case corner (kept `worst_case.weights_wt_pct`, balance the remainder). The endpoint of the
      solve equals the composed per-sample value (kept screen: matrix_only, nominal, worst_case.value) to 1e-6
      relative. (The matrix-only composition is one more point than the protocol asks for; it is the same linear claim.)
  G5  Statistics. From the kept per-sample documents the checker recomputes k, the minimum sample count, every value
      band (median = nearest rank over the samples with a value; upper bound = k-th largest with a missing value as
      +infinity), every limit band (k-th smallest effective limit; statuses of LimSample::from_limit; `not_established`
      when a sample has a positive lower bound), the class frequencies with their Clopper-Pearson bounds, the
      meets-target counts, the ranking fractions and the per-impurity class limits, and compares each with the
      command's `uncertainty` blocks: every number exactly, except the Clopper-Pearson bounds (`lower`, `upper`) to
      1e-12 relative.
  G6  Refusals. Each of: samples below the minimum at 0.95, the composition channel, base options.rate_scale,
      decay_scale and yield_scale, options.gas true, a base `uncertainty` section, confidence_level without sampling:
      nonzero exit, no output file, and the message names the cause. The worked example's output (G8's) has
      verification.verified true and uncertainty.verification_pass true.
  G7  Determinism. The G4 case run twice with the same seed gives identical outputs and identical kept samples apart
      from the timing keys; with the seed plus one the per-sample values and the factor blocks differ.
  G8  The worked example at 72 samples via the handbook command (docs/guide/impurities.md, section "Add nuclear-data
      uncertainty"): exit 0, verification passes, uncertainty.failed_samples is empty, the section's JSON block is the
      example's added keys, every fenced block labelled Output after an `actinv impurities` command is a contiguous
      run of lines of the fresh output, the section states a runtime and no placeholder comment remains. The run takes
      about an hour: `--g8-output PATH` (with `--g8-runtime SECONDS`, `--g8-log PATH` for the captured stdout and
      stderr) uses a run already made by the coordinator; its sha256 is recorded.
  G9  External (CI on the pushed evidence commit).

Interpretation points, resolved here: the G4 case needs confidence 0.5 (see G4); G4 also solves the matrix-only
composition; the three non-linear endpoints of the worked example (dose.contact_proxy, waste_class) are not part of
G4 (protocol: linear endpoints); the class limits' `nominal_positive_lower_bound` is read from the output (it comes
from the nominal class setup, which the kept samples do not hold) and everything else of those bands is recomputed.

Usage:
  check_p158.py [--reference REF_BIN] [--candidate CAND_BIN] [--gates G0,G2,...] [--g1-log LOG]
      [--photon-response PATH] [--timeout 14400] [--cwd DIR] [--work target/p158-work]
      [--out results/p158_bands.json] [--g8-output PATH [--g8-runtime SECONDS] [--g8-log PATH]]
  check_p158.py --selftest
Run inside the required systemd cgroup; one process at a time. Exit 0 PASS (or PARTIAL: a subset of gates, all
passing), 1 FAIL, 2 missing input.
"""
import argparse
import copy
import importlib
import json
import math
import os
import random
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import check_p157 as p157  # noqa: E402  (cases, Solver, Clearance, run_cmd, chapter helpers)

PROTOCOL = ROOT / "protocols" / "ACTINV-P158_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
EXAMPLE_DIR = ROOT / "examples" / "impurities_ra_steel"
EXAMPLE_BASE = EXAMPLE_DIR / "base_spec.json"
EXAMPLE_PLAIN = EXAMPLE_DIR / "impurities.json"
EXAMPLE_UNC = EXAMPLE_DIR / "impurities_uncertainty.json"
CHAPTER = ROOT / "docs" / "guide" / "impurities.md"
SECTION_HEADING = "Add nuclear-data uncertainty"
RA_BASE = p157.RA_BASE
P146_SPEC = ROOT / "controls" / "p146_inputs" / "d1_steel.json"
DEFAULT_REFERENCE = Path("/home/connoravila/Documents/actinv/target/p158-ref/actinv")
DEFAULT_PHOTON = p157.DEFAULT_PHOTON

# ------------------------------------------------------------------------------ interpretation points (edit here)
G4_SAMPLES = 20
G4_CONFIDENCE = 0.5
G4_KEEP = "kept"
G4_TOL = 1.0e-6                 # protocol: the full solve agrees with the composed value to 1e-6 relative
CP_TOL = 1.0e-12                # Clopper-Pearson bounds are computed independently
CP_ABS = 1.0e-15
WORKED_SAMPLES = 72
WORKED_CONFIDENCE = 0.95
LINEAR_IDS = ("activity.total", "heat.total", "clearance_index")
CLASS_NAMES = ("A", "B", "C", "above_class_c", "unknown", "failed")
CLASS_ORDER = {"A": 0, "B": 1, "C": 2}
STATUS_NAMES = ("bound", "capped", "failed", "infeasible", "no_response", "unavailable")
IMP_KINDS = ("alone", "others_at_nominal", "others_at_worst")
REASON_WORDS = {
    "samples_below_minimum": ["72"],
    "composition_channel": ["composition"],
    "base_rate_scale": ["rate_scale"],
    "base_decay_scale": ["decay_scale"],
    "base_yield_scale": ["yield_scale"],
    "base_gas": ["gas"],
    "base_uncertainty": ["uncertainty"],
    "confidence_without_sampling": ["confidence_level", "sampling"],
}
DROP_KEYS = p157.DROP_KEYS
MAX_LISTED = p157.MAX_LISTED

GateError = p157.GateError
MissingInput = p157.MissingInput
Checks = p157.Checks
sha256_file = p157.sha256_file
sha256_text = p157.sha256_text
canonical = p157.canonical
dump = p157.dump
run_cmd = p157.run_cmd
clean_env = p157.clean_env
normalize_result = p157.normalize_result
json_diffs = p157.json_diffs


# ------------------------------------------------------------------------------ the independent statistics

_P146 = None


def p146():
    """controls/check_p146.py (which imports check_p145): the independent order statistic and beta quantile."""
    global _P146
    if _P146 is None:
        try:
            _P146 = importlib.import_module("check_p146")
        except Exception as error:   # noqa: BLE001
            raise MissingInput(f"controls/check_p146.py cannot be imported: {error}")
    return _P146


def order_index(n, confidence):
    """The largest k with the Clopper-Pearson lower end of (n - k + 1)/n at least `confidence`; None when n is too small."""
    return p146().order_index(n, confidence)


def min_samples(confidence):
    """The fewest n for which n of n successes reach `confidence`: x^n <= (1 - x)/2, from the logarithm then adjusted
    by the exact tail test."""
    tail_ok = p146().tail_ok
    n = max(1, math.ceil(math.log((1.0 - confidence) / 2.0) / math.log(confidence)))
    while n > 1 and tail_ok(n - 1, n - 1, confidence):
        n -= 1
    while not tail_ok(n, n, confidence):
        n += 1
    return n


def clopper_pearson(k, n, confidence):
    """Two-sided Clopper-Pearson interval (lower, upper) of k successes in n trials at `confidence`."""
    if n == 0:
        return 0.0, 1.0
    half = (1.0 - confidence) / 2.0
    bq = p146().beta_quantile   # (a, b, p): the Python argument order, not the Rust (p, a, b)
    if k == 0:
        lower = 0.0
    elif k == n:
        lower = half ** (1.0 / n)
    else:
        lower = bq(float(k), float(n - k + 1), half)
    if k == n:
        upper = 1.0
    elif k == 0:
        upper = 1.0 - half ** (1.0 / n)
    else:
        upper = bq(float(k + 1), float(n - k), 1.0 - half)
    return lower, upper


def tkey(v):
    """Rust's f64::total_cmp for finite numbers and infinity: -0.0 sorts before 0.0."""
    return v, math.copysign(1.0, v)


def median_rank_value(valid_sorted):
    """Nearest-rank 50th percentile: rank max(1, ceil(n * 50 / 100)) = (n + 1) // 2."""
    return valid_sorted[(len(valid_sorted) + 1) // 2 - 1]


def value_band(values, k):
    """The median (over the samples with a value) and the k-th largest of all samples, a missing value as +infinity."""
    n = len(values)
    order = sorted(((math.inf if v is None else v), i) for i, v in enumerate(values))
    order.sort(key=lambda e: (tkey(e[0]), e[1]))
    valid = sorted((v for v in values if v is not None), key=tkey)
    median = median_rank_value(valid) if valid else None
    at_k = order[n - k] if 0 <= n - k < n and k >= 0 and n - k >= 0 else None
    if at_k is not None and math.isfinite(at_k[0]):
        upper, sample = at_k
    else:
        upper, sample = None, None
    return {"median": median, "upper_bound": upper, "status": "bounded" if upper is not None else "unbounded",
            "order_statistic_rank": k, "valid_samples": len(valid), "upper_bound_sample": sample,
            "by_sample": list(values)}


def lim_sample(status, effective, positive=False):
    return {"status": status, "effective": effective, "positive": positive}


def lim_failed():
    return lim_sample("failed", 0.0)


def lim_from_kept(limit):
    """LimSample::from_limit of a kept scalar limit {value, status, capped_at}."""
    if limit is None:
        return lim_failed()
    if limit.get("value") is not None:
        return lim_sample("bound", limit["value"])
    if limit.get("capped_at") is not None:
        return lim_sample("capped", limit["capped_at"])
    if limit.get("status") == "infeasible":
        return lim_sample("infeasible", 0.0)
    if limit.get("status") == "unavailable":
        return lim_sample("unavailable", 0.0)
    return lim_sample("no_response", None)


def lim_from_class(entry):
    """A kept class limit {effective, status, positive_lower_bound}."""
    if entry is None:
        return lim_failed()
    return lim_sample(entry["status"], entry["effective"], bool(entry["positive_lower_bound"]))


def limit_band(samples, k, nominal_positive, unit):
    """The k-th smallest effective limit (no response sorts last as +infinity) and its status."""
    order = sorted(((math.inf if s["effective"] is None else s["effective"], i) for i, s in enumerate(samples)),
                   key=lambda e: (tkey(e[0]), e[1]))
    positive = sum(1 for s in samples if s["positive"])
    counts = {name: 0 for name in STATUS_NAMES}
    for s in samples:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    at_k = order[k - 1][0] if 1 <= k <= len(order) else math.inf
    if positive > 0 or nominal_positive:
        status, reason = "not_established", "positive_lower_bound"
    elif not math.isfinite(at_k):
        status, reason = "unbounded", "no_response"
    else:
        status, reason = "established", None
    bound = at_k if status == "established" else None
    bound_sample = order[k - 1][1] if 1 <= k <= len(order) and math.isfinite(order[k - 1][0]) else None
    return {"unit": unit, "status": status, "reason": reason, "bound": bound, "order_statistic_rank": k,
            "bound_sample": bound_sample, "positive_lower_bound_samples": positive,
            "nominal_positive_lower_bound": bool(nominal_positive), "sample_status": counts,
            "by_sample": [s["effective"] for s in samples],
            "sorted": [e[0] if math.isfinite(e[0]) else None for e in order]}


def frequency_json(count, n, confidence):
    lower, upper = clopper_pearson(count, n, confidence)
    return {"count": count, "probability": 0.0 if n == 0 else count / n, "lower": lower, "upper": upper}


def ranking_json(largest, names):
    n = len(largest)
    fractions = {}
    for e in names:
        count = sum(1 for x in largest if x == e)
        fractions[e] = {"count": count, "fraction": 0.0 if n == 0 else count / n}
    return {"samples": n, "fractions": fractions, "largest_by_sample": list(largest)}


def class_frequencies(classes, confidence):
    """`classes` per sample: a class name or None for a failed sample."""
    n = len(classes)
    counts = {c: 0 for c in CLASS_NAMES}
    for c in classes:
        name = "failed" if c is None else c
        counts[name] = counts.get(name, 0) + 1
    return {"frequency": {c: frequency_json(counts[c], n, confidence) for c in CLASS_NAMES},
            "by_sample": ["failed" if c is None else c for c in classes]}


def meets_count(classes, target):
    """Samples whose class is at most the target (A, B, C); failed, above class C and unknown meet nothing."""
    if target not in CLASS_ORDER:
        return 0
    return sum(1 for c in classes if c in CLASS_ORDER and CLASS_ORDER[c] <= CLASS_ORDER[target])


def largest_scalar(contributions):
    """The impurity with the largest contribution (ties to the first name); None when none has one."""
    have = [(-c, e) for e, c in contributions.items() if c is not None]
    return min(have)[1] if have else None


def largest_class(contributions):
    if not contributions:
        return None
    return min((-c, e) for e, c in contributions.items())[1]


# ------------------------------------------------------------------------------ recomputing the bands from kept samples


def screen_entry(sample, ep_id, step):
    """The kept screen of one endpoint at one step, or None for a failed sample."""
    screen = sample.get("screen")
    if screen is None:
        return None
    return (screen.get(ep_id) or {}).get("steps", {}).get(str(step))


def expected_scalar(kept, ep_id, step, names, k, has_limit):
    """The `uncertainty` object of a scalar endpoint at one step, from the kept samples."""
    rows = [screen_entry(s, ep_id, step) for s in kept]

    def col(fn):
        return [None if r is None else fn(r) for r in rows]

    out = {
        "matrix_only": value_band(col(lambda r: r.get("matrix_only")), k),
        "nominal": value_band(col(lambda r: r.get("nominal")), k),
        "worst_case": value_band(col(lambda r: (r.get("worst_case") or {}).get("value")), k),
    }
    imps = {}
    for e in names:
        entry = {"contribution_nominal": value_band(col(lambda r, e=e: r["impurities"][e]["contribution_nominal"]), k)}
        if has_limit:
            entry["limits"] = {}
            for kind in IMP_KINDS:
                samples = []
                for r in rows:
                    limits = None if r is None else r["impurities"][e].get("limits")
                    samples.append(lim_failed() if limits is None else lim_from_kept(limits[kind]))
                entry["limits"][kind] = limit_band(samples, k, False, "wt_pct")
        imps[e] = entry
    out["impurities"] = imps
    largest = [None if r is None else largest_scalar({e: r["impurities"][e]["contribution_nominal"] for e in names})
               for r in rows]
    out["ranking"] = ranking_json(largest, names)
    if has_limit:
        samples = [lim_failed() if r is None or r.get("joint_k") is None else lim_from_kept(r["joint_k"])
                   for r in rows]
        out["joint_k"] = limit_band(samples, k, False, "scale_factor")
    return out


def expected_class(kept, ep_id, step, names, target, confidence):
    rows = [screen_entry(s, ep_id, step) for s in kept]
    nominal = [None if r is None else r["nominal_class"] for r in rows]
    worst = [None if r is None else r["worst_class"] for r in rows]
    n = len(rows)
    largest = [None if r is None else largest_class(r["contributions"]) for r in rows]
    return {"target_class": target, "nominal_class": class_frequencies(nominal, confidence),
            "worst_class": class_frequencies(worst, confidence),
            "meets_target": {"nominal": frequency_json(meets_count(nominal, target), n, confidence),
                             "worst_case": frequency_json(meets_count(worst, target), n, confidence)},
            "ranking": ranking_json(largest, names)}


def expected_class_limits(kept, ep_id, observed, k):
    """The per-impurity class limits of one class endpoint: every band recomputed; the nominal-data flag is read from
    the observed band. Returns (expected, key problems)."""
    problems = []
    seen = set()
    for s in kept:
        screen = s.get("screen")
        if screen is not None:
            seen |= set((screen.get(ep_id) or {}).get("class_limits", {}))
    observed_keys = set()
    expected = {"impurities": {}, "joint_scale_factor": None}

    def band(key, obs):
        samples = []
        for s in kept:
            screen = s.get("screen")
            limits = None if screen is None else (screen.get(ep_id) or {}).get("class_limits")
            samples.append(lim_failed() if limits is None else lim_from_class(limits.get(key)))
        unit = "scale_factor" if key == "joint_scale_factor" else "wt_pct"
        return limit_band(samples, k, bool((obs or {}).get("nominal_positive_lower_bound")), unit)

    for e, kinds in (observed.get("impurities") or {}).items():
        expected["impurities"][e] = {}
        for which, obs in kinds.items():
            key = f"{e}/{which}"
            observed_keys.add(key)
            expected["impurities"][e][which] = band(key, obs)
    if observed.get("joint_scale_factor") is not None:
        observed_keys.add("joint_scale_factor")
        expected["joint_scale_factor"] = band("joint_scale_factor", observed["joint_scale_factor"])
    if seen != observed_keys:
        problems.append({"kept_only": sorted(seen - observed_keys), "output_only": sorted(observed_keys - seen)})
    return expected, problems


def cmp_tree(exp, obs, path="", out=None, limit=20):
    """Differences between an expected and an observed tree: exact, except `lower` and `upper` (Clopper-Pearson)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(exp, dict):
        if not isinstance(obs, dict):
            out.append({"path": path, "expected": "object", "observed": str(obs)[:80]})
            return out
        for key in sorted(set(exp) | set(obs)):
            if key not in exp or key not in obs:
                out.append({"path": f"{path}/{key}", "expected": exp.get(key, "<absent>"),
                            "observed": obs.get(key, "<absent>")})
            else:
                cmp_tree(exp[key], obs[key], f"{path}/{key}", out, limit)
    elif isinstance(exp, list):
        if not isinstance(obs, list) or len(exp) != len(obs):
            out.append({"path": path, "expected": f"list of {len(exp)}",
                        "observed": f"list of {len(obs)}" if isinstance(obs, list) else str(obs)[:80]})
            return out
        for i, (x, y) in enumerate(zip(exp, obs)):
            cmp_tree(x, y, f"{path}/{i}", out, limit)
    elif exp is None or isinstance(exp, (bool, str)):
        if exp != obs or type(exp) is not type(obs):
            out.append({"path": path, "expected": exp, "observed": obs})
    else:
        if isinstance(obs, bool) or not isinstance(obs, (int, float)):
            out.append({"path": path, "expected": exp, "observed": obs})
        elif path.endswith(("/lower", "/upper")):
            if exp != obs and abs(exp - obs) > CP_TOL * max(abs(exp), abs(obs)) + CP_ABS:
                out.append({"path": path, "expected": exp, "observed": obs})
        elif exp != obs:
            out.append({"path": path, "expected": exp, "observed": obs})
    return out


# ------------------------------------------------------------------------------ inputs, cases and runs


def localise_base(base, data_home, photon):
    """A copy of a base spec with absolute data paths under data_home and the photon response of --photon-response."""
    b = copy.deepcopy(base)

    def absolute(p):
        return str(p) if Path(p).is_absolute() else str(Path(data_home) / p)

    if isinstance(b.get("library"), dict) and b["library"].get("path"):
        b["library"]["path"] = absolute(b["library"]["path"])
    for key in ("primary", "fallback"):
        if isinstance(b.get("decay"), dict) and b["decay"].get(key):
            b["decay"][key] = absolute(b["decay"][key])
    if photon and "photon" in b:
        b["photon"]["response"] = dict(photon)
    return b


def base_resolves(base, cwd):
    """True when the library, decay and photon-response paths of a base spec exist from `cwd` (the verbatim handbook
    command can run there)."""
    paths = []
    if isinstance(base.get("library"), dict):
        paths.append(base["library"].get("path"))
    paths += [base.get("decay", {}).get(k) for k in ("primary", "fallback")]
    if isinstance(base.get("photon"), dict) and isinstance(base["photon"].get("response"), dict):
        paths.append(base["photon"]["response"].get("path"))
    return all(Path(cwd, p).is_file() for p in paths if p)


def stage_doc(directory, data_home, photon, samples=G4_SAMPLES, confidence=G4_CONFIDENCE, seed=None, keep=G4_KEEP,
              source=EXAMPLE_UNC):
    """The worked uncertainty example as a work copy: localised base_spec.json and impurities.json in `directory`."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    doc = json.loads(Path(source).read_text())
    base = localise_base(json.loads(EXAMPLE_BASE.read_text()), data_home, photon)
    dump(d / "base_spec.json", base)
    doc["base_spec"] = "base_spec.json"
    doc["confidence_level"] = confidence
    doc["sampling"]["samples"] = samples
    if seed is not None:
        doc["sampling"]["seed"] = seed
    if keep:
        doc["sampling"]["keep_samples"] = keep
    else:
        doc["sampling"].pop("keep_samples", None)
    dump(d / "impurities.json", doc)
    return d / "impurities.json", doc, base


def load_kept(directory):
    files = sorted(Path(directory).glob("sample_*.json"))
    return [json.loads(f.read_text()) for f in files]


class BandsRun:
    """One run of `actinv impurities` on a staged reduced worked example, memoised by the binary and the input."""

    def __init__(self, work, tag, binary, cwd, timeout, data_home, photon, seed=None, verify=False):
        self.dir = Path(work) / tag
        self.binary, self.cwd, self.timeout = binary, cwd, timeout
        self.data_home, self.photon, self.seed, self.verify = data_home, photon, seed, verify
        self.info = None
        self.out_doc = None
        self.kept = None
        self.doc = None
        self.base = None

    @property
    def out_path(self):
        return self.dir / "out.json"

    @property
    def kept_dir(self):
        return self.dir / G4_KEEP

    def load(self):
        if self.out_doc is not None:
            return self
        self.dir.mkdir(parents=True, exist_ok=True)
        in_path, self.doc, self.base = stage_doc(self.dir, self.data_home, self.photon, seed=self.seed)
        flags = [] if self.verify else ["--no-verify"]
        stamp = {"binary_sha256": sha256_file(self.binary), "input_sha256": sha256_file(in_path),
                 "base_sha256": sha256_file(self.dir / "base_spec.json"), "flags": flags}
        stamp_path = self.dir / "run.stamp.json"
        reuse = False
        if stamp_path.is_file() and self.out_path.is_file():
            try:
                reuse = json.loads(stamp_path.read_text()).get("stamp") == stamp \
                    and len(list(self.kept_dir.glob("sample_*.json"))) == self.doc["sampling"]["samples"]
            except ValueError:
                reuse = False
        if reuse:
            self.info = dict(json.loads(stamp_path.read_text())["info"], reused=True)
        else:
            if self.out_path.exists():
                self.out_path.unlink()
            if self.kept_dir.exists():
                shutil.rmtree(self.kept_dir)
            started = time.monotonic()
            code, so, se = run_cmd([self.binary, "impurities", in_path, self.out_path, *flags], self.timeout,
                                   self.cwd, clean_env())
            self.info = {"exit": code, "seconds": round(time.monotonic() - started, 1), "reused": False,
                         "stderr_tail": se.decode(errors="replace")[-600:], "input": str(in_path),
                         "input_sha256": stamp["input_sha256"], "flags": flags}
            if code != 0 or not self.out_path.is_file():
                raise GateError(f"actinv impurities failed ({code}) on {in_path}: {se.decode(errors='replace')[-600:]}")
            dump(stamp_path, {"stamp": stamp, "info": self.info})
        self.info["output_sha256"] = sha256_file(self.out_path)
        self.out_doc = json.loads(self.out_path.read_text())
        self.kept = load_kept(self.kept_dir)
        return self


def endpoints_of(out_doc):
    """[(step, endpoint entry)] over the targets of a result."""
    return [(t["step"], ep) for t in out_doc["targets"] for ep in t["endpoints"]]


def element_names(out_doc):
    return sorted(set([out_doc["balance"]]) | set(out_doc["matrix"]) | set(out_doc["impurities"]))


# ------------------------------------------------------------------------------ G0, G1, G9


def gate_g0():
    lines = HASH_LOG.read_text(encoding="utf-8").splitlines()
    out, ok = {}, True
    paths = [("protocol", PROTOCOL)] + [(p.stem.split("_", 1)[1].lower(), p) for p in
                                        sorted((ROOT / "protocols").glob("ACTINV-P158_AMENDMENT_*.md"))]
    for label, path in paths:
        digest = sha256_file(path)
        hit = any(ln.startswith(digest) and ln.split()[-1] == f"protocols/{path.name}" for ln in lines)
        out[label] = {"path": str(path), "sha256": digest, "in_protocol_hash_txt": hit}
        ok = ok and hit
    out["pass"] = ok
    return out


# ------------------------------------------------------------------------------ G2


def g2_compare(ident, words, make_input, ref, cand, timeout):
    """Run one command with both binaries on identical inputs; `make_input(label)` returns (argument path, cwd)."""
    row = {"id": ident, "command": " ".join(words)}
    outs = {}
    for label, binary in (("reference", ref), ("candidate", cand)):
        path, cwd, out = make_input(label)
        if out.exists():
            out.unlink()
        code, so, se = run_cmd([binary, *words, path, out], timeout, cwd, clean_env())
        doc = None
        if out.is_file():
            try:
                doc = json.loads(out.read_text())
            except ValueError:
                doc = None
        outs[label] = (code, doc, se.decode(errors="replace")[-300:], sha256_file(out) if out.is_file() else None)
    (rc, rdoc, rerr, rsha), (cc, cdoc, cerr, csha) = outs["reference"], outs["candidate"]
    diffs = json_diffs(normalize_result(rdoc), normalize_result(cdoc)) if rdoc is not None and cdoc is not None else []
    same = rc == cc and (rdoc is None) == (cdoc is None) and not diffs
    row.update({"reference_exit": rc, "candidate_exit": cc, "reference_output": rdoc is not None,
                "candidate_output": cdoc is not None, "reference_output_sha256": rsha,
                "candidate_output_sha256": csha, "diffs": diffs, "identical_apart_from_timing": same,
                "reference_stderr": rerr if not same else "", "candidate_stderr": cerr if not same else ""})
    row["pass"] = same and rc == 0 and rdoc is not None
    return row


def build_g2_cases(cand, work, data_home, timeout, photon):
    """P157's four cases (the worked example as `impurities.json`, tungsten, vanadium, reduced RA steel) with limits
    set from the candidate's pure-element runs, as P157 sets them. Returns [Case]."""
    cases = p157.build_cases(data_home, photon, lambda base: None)
    solver = p157.Solver(cand, Path(work) / "g2", data_home, timeout)
    clr = p157.Clearance()
    for c in cases:
        if c.example:
            c.base = localise_base(c.base, data_home, photon)
        else:
            p157.State(c, solver, clr, Path(work) / "g2", cand, data_home, timeout).choose_limits()
    return cases


def gate_g2(ref, cand, work, data_home, timeout, photon):
    results, ok = [], True
    cases = build_g2_cases(cand, work, data_home, timeout, photon)
    for c in cases:
        d = Path(work) / "g2" / c.name
        d.mkdir(parents=True, exist_ok=True)
        dump(d / "base_spec.json", c.base)
        doc = p157.impurities_doc(c)
        dump(d / "impurities.json", doc)

        def make(label, d=d):
            return d / "impurities.json", data_home, d / f"out.{label}.json"

        row = g2_compare(f"impurities_{c.name}", ["impurities"], make, ref, cand, timeout)
        row["input_sha256"] = sha256_file(d / "impurities.json")
        ok = ok and row["pass"]
        results.append(row)

    def stage(label):
        p = p146()
        stage_dir = p.fresh_stage_at(Path(work) / "g2" / f"p146_d1_{label}")
        p.stage_case(P146_SPEC, stage_dir)
        return stage_dir

    staged = {label: stage(label) for label in ("reference", "candidate")}

    def make_waste(label):
        return "spec.json", staged[label], staged[label] / "result.json"

    row = g2_compare("waste_budget_uncertainty_p146_d1", ["waste", "budget", "uncertainty"], make_waste, ref, cand,
                     timeout)
    row["input_sha256"] = sha256_file(staged["candidate"] / "spec.json")
    ok = ok and row["pass"]
    results.append(row)
    return {"pass": ok, "cases": results}


# ------------------------------------------------------------------------------ G3


def check_common_factors(kept, elements, ck):
    """Every element solve of every sample carries the sample's block on every key."""
    keys = ("flux_factor", "rate_scale", "decay_scale", "yield_scale")
    blocks = set()
    for i, s in enumerate(kept):
        ck.ok(s.get("sample") == i, f"sample {i}: index", i, s.get("sample"))
        ck.ok(s.get("error") is None, f"sample {i}: no error", None, s.get("error"))
        f = s["factors"]
        blocks.add(canonical(f))
        ck.ok(set(s["elements"]) == set(elements), f"sample {i}: element set", sorted(elements),
              sorted(s["elements"]))
        ck.ok(bool(f["decay_scale"]), f"sample {i}: decay_scale is not empty", ">0 entries", len(f["decay_scale"]))
        ck.ok(bool(f["rate_scale"]), f"sample {i}: rate_scale is not empty", ">0 entries", len(f["rate_scale"]))
        for e, rec in s["elements"].items():
            ck.ok(rec.get("error") is None, f"sample {i} {e}: solve error", None, rec.get("error"))
            for key in keys:
                ck.ok(rec["factors"].get(key) == f.get(key), f"sample {i} {e}: factors.{key} equal the sample's",
                      "equal", "differs")
    ck.ok(len(blocks) == len(kept), "the samples' factor blocks are distinct", len(kept), len(blocks))


def gate_g3(run):
    run.load()
    ck = Checks()
    n = run.doc["sampling"]["samples"]
    ck.ok(len(run.kept) == n, "kept sample files", n, len(run.kept))
    check_common_factors(run.kept, element_names(run.out_doc), ck)
    first = run.kept[0]["factors"]
    return ck.summary(case=run.info, samples=len(run.kept), elements=element_names(run.out_doc),
                      decay_scale_entries=len(first["decay_scale"]), rate_scale_entries=len(first["rate_scale"]),
                      yield_scale_entries=len(first["yield_scale"]))


# ------------------------------------------------------------------------------ G4


def pick_samples(n, seed):
    """The lowest, the median and the highest index and two at random from the seed."""
    fixed = [0, n // 2, n - 1]
    pool = [i for i in range(n) if i not in fixed]
    extra = random.Random(seed).sample(pool, 2)
    return sorted(set(fixed) | set(extra))


def full_comp(balance, matrix, levels):
    """{element: wt%}: the matrix, the impurity levels (zero left out), the balance element taking the rest."""
    comp = dict(matrix)
    for e, v in levels.items():
        if v > 0.0:
            comp[e] = comp.get(e, 0.0) + v
    total = 0.0
    for e in sorted(comp):
        total += comp[e]
    rest = 100.0 - total
    if not rest > 0.0:
        raise GateError(f"the fixed constituents sum to {total} wt%; no balance remains")
    comp[balance] = rest
    return {e: comp[e] for e in sorted(comp)}


def factor_spec(base, comp, block, title):
    """The full-alloy spec with a factor block applied as study::apply_factor_block does."""
    s = copy.deepcopy(base)
    s["title"] = title
    options = s.setdefault("options", {})
    options.update({"mode": "coupled", "prune": "reach"})
    for key in ("rate_scale", "decay_scale", "yield_scale"):
        if block.get(key):
            options[key] = dict(block[key])
        else:
            options.pop(key, None)
    spectrum = s["spectrum"]
    if spectrum.get("total") is not None:
        spectrum["total"] = spectrum["total"] * block["flux_factor"]
    else:
        spectrum["flux_per_group"] = [g * block["flux_factor"] for g in spectrum["flux_per_group"]]
    s.pop("photon", None)
    s["material"] = {"mass_g": 1.0, "basis": "wt_percent", "composition": comp}
    s["summary"] = {"elements": False, "dominant_top": 1}
    return s


def is_linear(ep_id):
    return ep_id in LINEAR_IDS or ep_id.startswith("activity:")


def gate_g4(run, solver, clr):
    run.load()
    out, kept = run.out_doc, run.kept
    n = len(kept)
    seed = run.doc["sampling"]["seed"]
    picks = pick_samples(n, seed)
    balance, matrix = out["balance"], out["matrix"]
    nominal = {e: v["nominal"] for e, v in out["impurities"].items()}
    ck = Checks()
    rows = []
    linear = [(step, ep["response"]) for step, ep in endpoints_of(out) if is_linear(ep["response"])]
    for i in picks:
        block = kept[i]["factors"]
        done = 0
        for step, ep_id in linear:
            r = screen_entry(kept[i], ep_id, step)
            if r is None:
                ck.ok(False, f"sample {i} {ep_id} step {step}: kept screen present", "present", None)
                continue
            ep = {"response": ep_id}
            points = [("matrix_only", {}, r["matrix_only"]), ("nominal", nominal, r["nominal"]),
                      ("worst_case", r["worst_case"]["weights_wt_pct"], r["worst_case"]["value"])]
            scale = max(abs(p[2]) for p in points if p[2] is not None) if any(p[2] is not None for p in points) else 0.0
            for kind, levels, composed in points:
                comp = full_comp(balance, matrix, levels)
                spec = factor_spec(run.base, comp, block, f"P158 checker sample {i} {kind}")
                compact, _path = solver.solve(spec)
                solved = p157.endpoint_value(compact, step, ep, clr)
                if composed is None:
                    ck.ok(False, f"sample {i} {ep_id} step {step} {kind}: composed value", "a number", None)
                    continue
                ck.ok(p157.close(composed, solved, G4_TOL, scale), f"sample {i} {ep_id} step {step} {kind}",
                      composed, solved)
                ck.max_rel = max(ck.max_rel, p157.reldiff(composed, solved))
                done += 1
        rows.append({"sample": i, "points_compared": done})
    return ck.summary(case=run.info, seed=seed, samples=n, picked=picks, per_sample=rows,
                      endpoints=sorted({e for _s, e in linear}), steps=sorted({s for s, _e in linear}),
                      solves=solver.count, memo_hits=solver.hits, tolerance_rel=G4_TOL)


# ------------------------------------------------------------------------------ G5


def recompute_all(out_doc, kept, n, confidence):
    """[(label, expected, observed)] for every band of a result, plus top-level checks as (label, expected, observed)."""
    k = order_index(n, confidence)
    minimum = min_samples(confidence)
    names = sorted(out_doc["impurities"])
    items = []
    top = out_doc["uncertainty"]
    items.append(("order_statistic", {"k": k, "n": n, "minimum_samples": minimum}, top["order_statistic"]))
    items.append(("confidence_level", confidence, top["confidence_level"]))
    failed = sorted({i for i, s in enumerate(kept) if s.get("error") is not None})
    items.append(("failed_sample_indices", failed, sorted({f["sample"] for f in top["failed_samples"]})))
    problems = []
    for step, ep in endpoints_of(out_doc):
        ep_id = ep["response"]
        label = f"{ep_id}@{step}"
        obs = ep["uncertainty"]
        if ep_id.startswith("waste_class:"):
            items.append((label, expected_class(kept, ep_id, step, names, obs["target_class"], confidence), obs))
        else:
            has_limit = ep.get("limit") is not None
            items.append((label, expected_scalar(kept, ep_id, step, names, k, has_limit), obs))
    for ep_id, observed in (top.get("impurity_class_limits") or {}).items():
        expected, probs = expected_class_limits(kept, ep_id, observed, k)
        items.append((f"{ep_id} class limits", expected, observed))
        problems += [{"endpoint": ep_id, **p} for p in probs]
    return items, k, minimum, problems


def gate_g5(run):
    run.load()
    out, kept = run.out_doc, run.kept
    n = run.doc["sampling"]["samples"]
    confidence = run.doc["confidence_level"]
    ck = Checks()
    ck.ok(len(kept) == n, "kept sample files", n, len(kept))
    items, k, minimum, problems = recompute_all(out, kept, n, confidence)
    ck.ok(not problems, "class limit keys match the kept samples", [], problems)
    bands = 0
    for label, expected, observed in items:
        diffs = cmp_tree(expected, observed)
        ck.ok(not diffs, f"{label}: bands equal the recomputation", "no differences", diffs[:3])
        bands += 1
    return ck.summary(case=run.info, n=n, confidence_level=confidence, k=k, minimum_samples=minimum,
                      band_groups_compared=bands, ci_tolerance_rel=CP_TOL)


# ------------------------------------------------------------------------------ G6


def refusal_variants(doc, base, ra_uncertainty):
    """{name: (document, base spec)}: the baseline with one edit each. Pure; the baseline is not modified."""

    def variant(edit_doc=None, edit_base=None):
        d, b = copy.deepcopy(doc), copy.deepcopy(base)
        if edit_doc:
            edit_doc(d)
        if edit_base:
            edit_base(b)
        return d, b

    def few(d):
        d["sampling"]["samples"] = 71
        d["confidence_level"] = 0.95

    def composition(d):
        d["sampling"]["channels"]["composition_rel_std"] = {"Fe": 0.01}

    def no_sampling(d):
        d.pop("sampling", None)
        d["confidence_level"] = 0.95

    def option(key, value):
        return lambda b: b.setdefault("options", {}).__setitem__(key, value)

    return {
        "samples_below_minimum": variant(edit_doc=few),
        "composition_channel": variant(edit_doc=composition),
        "base_rate_scale": variant(edit_base=option("rate_scale", {"0": 1.0})),
        "base_decay_scale": variant(edit_base=option("decay_scale", {"Mn54": 1.0})),
        "base_yield_scale": variant(edit_base=option("yield_scale", {"Cs137": 1.0})),
        "base_gas": variant(edit_base=option("gas", True)),
        "base_uncertainty": variant(edit_base=lambda b: b.__setitem__("uncertainty", copy.deepcopy(ra_uncertainty))),
        "confidence_without_sampling": variant(edit_doc=no_sampling),
    }


def g8_verification_checks(out_doc):
    """The worked example's output: verification.verified and uncertainty.verification_pass."""
    verification = out_doc.get("verification") or {}
    return {"verification_verified": verification.get("verified") is True,
            "uncertainty_verification_pass": (out_doc.get("uncertainty") or {}).get("verification_pass") is True,
            "verification_uncertainty_verified": (verification.get("uncertainty") or {}).get("verified") is True}


def gate_g6(binary, work, cwd, timeout, data_home, photon, g8):
    d = Path(work) / "g6"
    in_path, doc, base = stage_doc(d / "baseline", data_home, photon, samples=WORKED_SAMPLES,
                                   confidence=WORKED_CONFIDENCE, keep=None)
    ra = json.loads(RA_BASE.read_text())
    results, ok = [], True
    for name, (vdoc, vbase) in refusal_variants(doc, base, ra["uncertainty"]).items():
        dd = d / name
        dump(dd / "base_spec.json", vbase)
        vdoc["base_spec"] = "base_spec.json"
        dump(dd / "impurities.json", vdoc)
        out = dd / "out.json"
        if out.exists():
            out.unlink()
        code, so, se = run_cmd([binary, "impurities", dd / "impurities.json", out], timeout, cwd, clean_env())
        text = (se + so).decode(errors="replace").lower()
        named = any(w in text for w in REASON_WORDS[name])
        row = {"id": name, "exit": code, "output_written": out.is_file(), "reason_names_cause": named,
               "message": text[-300:],
               "pass": code not in (0, None) and not out.is_file() and named}
        ok = ok and row["pass"]
        results.append(row)
    verification = {"pass": False}
    try:
        got = g8()
        verification = {**g8_verification_checks(got["out_doc"]), "source": got["source"]}
        verification["pass"] = all(v is True for k, v in verification.items() if k != "source")
    except GateError as error:
        verification["error"] = str(error)
    ok = ok and verification["pass"]
    return {"pass": ok, "cases": results, "worked_example_verification": verification}


# ------------------------------------------------------------------------------ G7


def strip_timing(doc):
    return normalize_result(doc)


def gate_g7(run, repeat, other):
    run.load()
    repeat.load()
    other.load()
    ck = Checks()
    diffs = json_diffs(strip_timing(run.out_doc), strip_timing(repeat.out_doc))
    ck.ok(not diffs, "same seed: identical outputs apart from timing keys", [], diffs[:3])
    ck.ok(len(run.kept) == len(repeat.kept), "same seed: kept sample count", len(run.kept), len(repeat.kept))
    kept_diffs = []
    for a, b in zip(run.kept, repeat.kept):
        kept_diffs += json_diffs(strip_timing(a), strip_timing(b), limit=3)
    ck.ok(not kept_diffs, "same seed: identical kept samples", [], kept_diffs[:3])
    ck.ok(run.doc["sampling"]["seed"] != other.doc["sampling"]["seed"], "the other run has another seed",
          "different", "same")
    changed = 0
    for (step, ep), (_s2, ep2) in zip(endpoints_of(run.out_doc), endpoints_of(other.out_doc)):
        if ep["response"].startswith("waste_class:"):
            continue
        a, b = ep["uncertainty"]["nominal"]["by_sample"], ep2["uncertainty"]["nominal"]["by_sample"]
        if a != b:
            changed += 1
    ck.ok(changed > 0, "another seed: per-sample values differ", ">0 endpoints", changed)
    same_blocks = sum(1 for a, b in zip(run.kept, other.kept) if canonical(a["factors"]) == canonical(b["factors"]))
    ck.ok(same_blocks == 0, "another seed: no sample has the same factor block", 0, same_blocks)
    return ck.summary(case=run.info, repeat=repeat.info, other_seed=other.info,
                      seeds=[run.doc["sampling"]["seed"], other.doc["sampling"]["seed"]],
                      endpoints_with_different_values=changed)


# ------------------------------------------------------------------------------ G8


def section_text(text, heading=SECTION_HEADING):
    """The body of the `## heading` section of a markdown chapter, up to the next `## `."""
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def section_commands(section):
    """Every `actinv impurities` command line of the section's fenced blocks."""
    out = []
    for _info, body, _label in p157.fenced_blocks(section):
        out += [ln.strip().lstrip("$ ").strip() for ln in body.splitlines() if "actinv impurities" in ln]
    return out


def json_block_keys(section):
    """The first fenced json block of the section parsed as the added keys of the example, or None."""
    for info, body, _label in p157.fenced_blocks(section):
        if info == "json":
            try:
                return json.loads("{" + body.strip().rstrip(",") + "}")
            except ValueError:
                return None
    return None


def runtime_stated(section):
    return bool(re.search(r"\d+(\.\d+)?\s*(minutes?|hours?|min|h|seconds?|s)\b|\b(minutes?|hours?)\b", section, re.I))


def obtain_g8(binary, cwd, timeout, work, data_home, photon, g8_output, g8_runtime, g8_log):
    """The worked example's output: a precomputed run (--g8-output) or a fresh run of the handbook command."""
    section = section_text(CHAPTER.read_text()) if CHAPTER.is_file() else ""
    commands = section_commands(section)
    if g8_output:
        path = Path(g8_output)
        if not path.is_file():
            raise MissingInput(f"--g8-output {path} is absent")
        text = path.read_text()
        fresh = text
        if g8_log:
            fresh = Path(g8_log).read_text() + "\n" + text
        return {"out_doc": json.loads(text), "fresh": fresh, "source": "precomputed", "exit": 0,
                "runtime_s": g8_runtime, "output": str(path), "output_sha256": sha256_file(path),
                "command": commands[0] if commands else None}
    if not commands:
        raise GateError("the handbook section has no `actinv impurities` command")
    words = commands[0].split()
    k = words.index("impurities")
    ins = [w for w in words[k + 1:] if not w.startswith("--")]
    flags = [w for w in words[k + 1:] if w.startswith("--")]
    d = Path(work) / "g8"
    d.mkdir(parents=True, exist_ok=True)
    out = d / "out.json"
    if out.exists():
        out.unlink()
    source = (ROOT / ins[0]) if ins else None
    localised = False
    doc = json.loads(source.read_text())
    base = json.loads((source.parent / doc["base_spec"]).read_text())
    if not base_resolves(base, cwd):
        in_path, _doc, _base = stage_doc(d / "staged", data_home, photon, samples=doc["sampling"]["samples"],
                                         confidence=doc.get("confidence_level", WORKED_CONFIDENCE),
                                         seed=doc["sampling"]["seed"], keep=doc["sampling"].get("keep_samples"),
                                         source=source)
        source, localised = in_path, True
    started = time.monotonic()
    code, so, se = run_cmd([binary, "impurities", source, out, *flags], timeout, cwd, clean_env())
    runtime = round(time.monotonic() - started, 1)
    if not out.is_file():
        raise GateError(f"the handbook command wrote no output (exit {code}): {se.decode(errors='replace')[-400:]}")
    fresh = (so + b"\n" + se).decode(errors="replace") + "\n" + out.read_text()
    return {"out_doc": json.loads(out.read_text()), "fresh": fresh, "source": "fresh run", "exit": code,
            "runtime_s": runtime, "output": str(out), "output_sha256": sha256_file(out), "localised_input": localised,
            "command": commands[0]}


def gate_g8(got):
    ck = Checks()
    out = got["out_doc"]
    unc = out.get("uncertainty") or {}
    example = json.loads(EXAMPLE_UNC.read_text()) if EXAMPLE_UNC.is_file() else {}
    ck.ok(got["exit"] == 0, "the command exits 0", 0, got["exit"])
    ck.ok((out.get("verification") or {}).get("verified") is True, "verification.verified", True,
          (out.get("verification") or {}).get("verified"))
    ck.ok(unc.get("verification_pass") is True, "uncertainty.verification_pass", True, unc.get("verification_pass"))
    ck.ok(unc.get("failed_samples") == [], "uncertainty.failed_samples is empty", [], unc.get("failed_samples"))
    ck.ok((unc.get("order_statistic") or {}).get("n") == WORKED_SAMPLES, "72 samples", WORKED_SAMPLES,
          (unc.get("order_statistic") or {}).get("n"))
    ck.ok(unc.get("confidence_level") == WORKED_CONFIDENCE, "confidence 0.95", WORKED_CONFIDENCE,
          unc.get("confidence_level"))
    ck.ok((unc.get("sampling") or {}).get("seed") == (example.get("sampling") or {}).get("seed"),
          "the example's seed", (example.get("sampling") or {}).get("seed"), (unc.get("sampling") or {}).get("seed"))
    pts = ((out.get("verification") or {}).get("uncertainty") or {}).get("points")
    ck.ok(bool(pts), "the uncertainty verification has points", ">0", len(pts or []))
    text = CHAPTER.read_text() if CHAPTER.is_file() else ""
    section = section_text(text)
    ck.ok(bool(section), f"the chapter has the section '{SECTION_HEADING}'", "present", "absent")
    added = json_block_keys(section)
    want = {k: example.get(k) for k in ("confidence_level", "sampling")}
    ck.ok(added == want, "the section's JSON block is the example's added keys", want, added)
    pairs, rows = p157.chapter_pairs(section), []
    primary = got.get("command")
    for cmd, block in pairs:
        matches = p157.line_run_in(block, got["fresh"])
        row = {"command": cmd, "same_command_as_run": cmd == primary, "excerpt_matches": matches,
               "pass": matches and cmd == primary}
        ck.ok(row["pass"], f"output excerpt after `{cmd}`", "a contiguous run of the fresh output", "no match")
        rows.append(row)
    ck.ok("<!-- P158" not in section, "no P158 placeholder comment remains in the section", "none", "present")
    ck.ok(runtime_stated(re.sub(r"<!--.*?-->", "", section, flags=re.S)), "the section states a runtime",
          "a duration", "none found")
    notes = [] if rows else ["the section has no block labelled Output after an `actinv impurities` command"]
    return ck.summary(source=got["source"], exit=got["exit"], runtime_s=got["runtime_s"], output=got["output"],
                      output_sha256=got["output_sha256"], command=primary, excerpts=rows, excerpt_notes=notes,
                      localised_input=got.get("localised_input", False))


# ------------------------------------------------------------------------------ the self-test


def synthetic_kept(n=10):
    """Kept-sample documents for one linear endpoint (heat.total, limit, impurities Co and Nb) and one class endpoint
    at step 2. nominal_i = 10 + i; sample 6 failed (screen null); Co limit: 1 + i, sample 3 capped at 90, sample 4 no
    response, sample 5 infeasible."""
    kept = []
    for i in range(n):
        if i == 6:
            kept.append({"sample": i, "factors": {"flux_factor": 1.0}, "elements": {}, "screen": None,
                         "error": "solve failed"})
            continue

        def lim(v, status="limit", cap=None):
            return {"value": v, "status": status, "capped_at": cap}

        if i == 3:
            co = lim(None, "no response", 90.0)
        elif i == 4:
            co = lim(None, "no response")
        elif i == 5:
            co = lim(None, "infeasible")
        else:
            co = lim(1.0 + i)
        kept.append({"sample": i, "factors": {"flux_factor": 1.0}, "elements": {}, "error": None, "screen": {
            "heat.total": {"steps": {"2": {
                "matrix_only": 9.0 + i, "nominal": 10.0 + i, "worst_case": {"value": 11.0 + i, "weights_wt_pct": {}},
                "impurities": {
                    "Co": {"contribution_nominal": 0.5 if i % 2 == 0 else 0.25, "gradient_per_wt_pct": 1.0,
                           "limits": {"alone": co, "others_at_nominal": co, "others_at_worst": co}},
                    "Nb": {"contribution_nominal": 0.5 if i % 4 == 0 else 0.75, "gradient_per_wt_pct": 1.0,
                           "limits": {"alone": lim(2.0 + i), "others_at_nominal": lim(2.0 + i),
                                      "others_at_worst": lim(2.0 + i)}}},
                "joint_k": lim(5.0 + i)}}},
            "waste_class:C": {
                "steps": {"2": {"nominal_class": "A" if i < 4 else "C", "worst_class": "B" if i < 8 else "above_class_c",
                                "contributions": {"Co": 1.0, "Nb": 1.0 if i != 9 else 2.0}}},
                "class_limits": {"Co/alone": {"effective": 91.0, "positive_lower_bound": False, "status": "capped"},
                                 "joint_scale_factor": {"effective": 60.0 + i, "positive_lower_bound": i == 9,
                                                        "status": "bound"}}}}})
    return kept


def self_test():
    fails = []

    def expect(cond, what):
        if not cond:
            fails.append(what)

    def close(a, b, tol=1e-9):
        return a is not None and b is not None and abs(a - b) <= tol * max(1.0, abs(b))

    # --- order statistic. n = 72 at 0.95: 0.95^72 = 0.0249 <= 0.025 (all 72 pass), 71 of 72 has tail 0.094 > 0.025,
    # so only k = 1 exists; 71 samples: 0.95^71 = 0.0262 > 0.025, no index.
    # n = 20 at 0.5: P(Bin(20, 0.5) >= m) is 0.1316 at m = 13 and 0.2517 at m = 12 against 0.25, so the smallest passing
    # m is 13 and k = 20 + 1 - 13 = 8 (the smoke run's k). The minimum at 0.5: 0.5^2 = 0.25 <= 0.25, so 2.
    expect(order_index(72, 0.95) == 1, "order_index(72, 0.95) == 1")
    expect(order_index(71, 0.95) is None, "order_index(71, 0.95) is None")
    expect(order_index(20, 0.5) == 8, f"order_index(20, 0.5) == 8, got {order_index(20, 0.5)}")
    expect(min_samples(0.95) == 72 and min_samples(0.5) == 2 and min_samples(0.99) == 528,
           f"min_samples 72 / 2 / 528: {min_samples(0.95)} {min_samples(0.5)} {min_samples(0.99)}")
    # --- Clopper-Pearson: 10 of 20 at 0.95 is (0.2719578, 0.7280422), R's binom.test; the closed forms at the ends
    lo, hi = clopper_pearson(10, 20, 0.95)
    expect(abs(lo - 0.2719578) < 1e-7 and abs(hi - 0.7280422) < 1e-7, f"CP 10/20: {lo} {hi}")
    lo, hi = clopper_pearson(72, 72, 0.95)
    expect(close(lo, 0.025 ** (1 / 72), 1e-12) and hi == 1.0, "CP 72/72 closed form")
    lo, hi = clopper_pearson(0, 72, 0.95)
    expect(lo == 0.0 and close(hi, 1.0 - 0.025 ** (1 / 72), 1e-12), "CP 0/72 closed form")
    expect(clopper_pearson(0, 0, 0.95) == (0.0, 1.0), "CP n = 0")

    # --- value bands: sample 6 has no value
    nominal = [None if i == 6 else 10.0 + i for i in range(10)]
    b2 = value_band(nominal, 2)
    # 9 valid values 10..19 without 16; median = 5th smallest = 14; ascending with inf last, order[8] = (19, 9)
    expect(b2["median"] == 14.0 and b2["upper_bound"] == 19.0 and b2["upper_bound_sample"] == 9, f"value band k=2 {b2}")
    expect(b2["status"] == "bounded" and b2["valid_samples"] == 9 and b2["by_sample"][6] is None, "value band meta")
    b1 = value_band(nominal, 1)
    expect(b1["upper_bound"] is None and b1["status"] == "unbounded" and b1["upper_bound_sample"] is None,
           "a missing value makes the k=1 bound unbounded")
    expect(value_band([None, None], 1)["median"] is None, "no valid value: no median")
    expect(median_rank_value([1.0, 2.0, 3.0, 4.0]) == 2.0 and median_rank_value([1.0, 2.0, 3.0, 4.0, 5.0]) == 3.0
           and median_rank_value([7.0]) == 7.0, "nearest-rank median")

    # --- limit bands: Co alone = [1, 2, 3, cap 90, none, infeasible 0, failed 0, 8, 9, 10]
    kept = synthetic_kept()
    names = ["Co", "Nb"]
    exp = expected_scalar(kept, "heat.total", 2, names, 2, True)
    co = exp["impurities"]["Co"]["limits"]["alone"]
    expect(co["sorted"] == [0.0, 0.0, 1.0, 2.0, 3.0, 8.0, 9.0, 10.0, 90.0, None], f"Co sorted {co['sorted']}")
    expect(co["bound"] == 0.0 and co["bound_sample"] == 6 and co["status"] == "established", f"Co k=2 {co}")
    expect(co["sample_status"] == {"bound": 6, "capped": 1, "failed": 1, "infeasible": 1, "no_response": 1,
                                   "unavailable": 0}, f"Co statuses {co['sample_status']}")
    expect(co["by_sample"][3] == 90.0 and co["by_sample"][4] is None and co["by_sample"][5] == 0.0
           and co["by_sample"][6] == 0.0, "capped enters at the cap, failed and infeasible at 0, no response none")
    expect(limit_band([lim_sample("bound", 1.0 + i) for i in range(5)], 3, False, "wt_pct")["bound"] == 3.0,
           "k-th smallest limit")
    exp3 = expected_scalar(kept, "heat.total", 2, names, 3, True)
    expect(exp3["impurities"]["Co"]["limits"]["alone"]["bound"] == 1.0
           and exp3["impurities"]["Co"]["limits"]["alone"]["bound_sample"] == 0, "Co k=3")
    # joint = 5 + i with the failed sample at 0: k = 2 is the smallest real one, sample 0
    expect(exp["joint_k"]["bound"] == 5.0 and exp["joint_k"]["bound_sample"] == 0
           and exp["joint_k"]["sample_status"]["failed"] == 1, "joint k band")
    allopen = limit_band([lim_sample("no_response", None) for _ in range(4)], 1, False, "wt_pct")
    expect(allopen["status"] == "unbounded" and allopen["bound"] is None and allopen["bound_sample"] is None,
           "no response everywhere: unbounded")
    positive = limit_band([lim_sample("bound", 5.0), lim_sample("bound", 6.0, True)], 1, False, "wt_pct")
    expect(positive["status"] == "not_established" and positive["bound"] is None
           and positive["positive_lower_bound_samples"] == 1, "a positive lower bound withdraws the bound")
    expect(limit_band([lim_sample("bound", 5.0)], 1, True, "wt_pct")["status"] == "not_established",
           "a positive nominal lower bound withdraws the bound")
    expect(lim_from_kept({"value": 2.0, "status": "limit", "capped_at": None})["status"] == "bound"
           and lim_from_kept({"value": None, "status": "no response", "capped_at": 3.0})["status"] == "capped"
           and lim_from_kept({"value": None, "status": "unavailable", "capped_at": None})["effective"] == 0.0
           and lim_from_kept({"value": None, "status": "no response", "capped_at": None})["effective"] is None,
           "LimSample::from_limit")

    # --- ranking: largest contributor per sample, ties to the first name, a failed sample counts in n only
    # Co is 0.5 at even i else 0.25; Nb is 0.5 at i % 4 == 0 else 0.75: the ties at i = 0, 4, 8 go to Co, every
    # other sample to Nb (0.75), the failed sample 6 to no one.
    rk = exp["ranking"]
    expect(rk["largest_by_sample"] == ["Co", "Nb", "Nb", "Nb", "Co", "Nb", None, "Nb", "Co", "Nb"],
           f"largest {rk['largest_by_sample']}")
    expect(rk["fractions"]["Co"] == {"count": 3, "fraction": 0.3} and rk["fractions"]["Nb"]["count"] == 6
           and rk["samples"] == 10, "ranking fractions")
    expect(largest_class({"Co": 1.0, "Nb": 1.0}) == "Co" and largest_class({"Co": 1.0, "Nb": 2.0}) == "Nb"
           and largest_class({}) is None, "largest class contributor")

    # --- class frequencies with a failed sample; the 6 samples with i < 4 are A, the rest C; sample 6 failed
    cls = expected_class(kept, "waste_class:C", 2, names, "C", 0.95)
    freq = cls["nominal_class"]["frequency"]
    expect(freq["A"]["count"] == 4 and freq["C"]["count"] == 5 and freq["failed"]["count"] == 1, f"class counts {freq}")
    expect(cls["nominal_class"]["by_sample"][6] == "failed", "failed sample named in by_sample")
    expect(cls["worst_class"]["frequency"]["above_class_c"]["count"] == 2, "worst classes")
    expect(cls["meets_target"]["nominal"]["count"] == 9 and cls["meets_target"]["worst_case"]["count"] == 7,
           f"meets target {cls['meets_target']}")
    expect(cls["meets_target"]["nominal"]["probability"] == 0.9, "meets probability")
    lower, upper = clopper_pearson(9, 10, 0.95)
    expect(cls["meets_target"]["nominal"]["lower"] == lower and lower < 0.9 < upper, "meets lower bound")
    expect(meets_count(["A", "B", "C", "above_class_c", "unknown", None], "B") == 2, "meets_count orders A < B < C")
    expect(meets_count(["A", "C"], "above_class_c") == 0, "an unknown target meets nothing")
    expect(freq["unknown"]["count"] == 0 and set(freq) == set(CLASS_NAMES), "all class names reported")

    # --- class limits: Co/alone capped at 91 everywhere, the joint factor 60 + i with sample 9 positive-lower
    obs = {"impurities": {"Co": {"alone": {"nominal_positive_lower_bound": False}}},
           "joint_scale_factor": {"nominal_positive_lower_bound": False}}
    cl, probs = expected_class_limits(kept, "waste_class:C", obs, 2)
    expect(not probs, f"class limit keys {probs}")
    # sorted: the failed sample at 0, then 91 (capped) in index order: k = 2 is sample 0 at 91, k = 1 the failed one
    expect(cl["impurities"]["Co"]["alone"]["sample_status"]["capped"] == 9
           and cl["impurities"]["Co"]["alone"]["sample_status"]["failed"] == 1
           and cl["impurities"]["Co"]["alone"]["bound"] == 91.0
           and cl["impurities"]["Co"]["alone"]["bound_sample"] == 0, "class limit Co/alone")
    expect(cl["joint_scale_factor"]["status"] == "not_established"
           and cl["joint_scale_factor"]["positive_lower_bound_samples"] == 1, "class joint limit positive lower")
    _cl2, probs2 = expected_class_limits(kept, "waste_class:C", {"impurities": {}, "joint_scale_factor": None}, 2)
    expect(bool(probs2), "class limit key mismatch is reported")

    # --- the whole comparison: an observed tree equal to the recomputation passes, a changed number does not
    observed = json.loads(json.dumps(exp))
    expect(cmp_tree(exp, observed) == [], "equal trees")
    observed["impurities"]["Co"]["limits"]["alone"]["bound"] = 0.5
    expect(len(cmp_tree(exp, observed)) == 1, "a changed bound is found")
    observed = json.loads(json.dumps(cls))
    observed["meets_target"]["nominal"]["lower"] = lower * (1 + 1e-13)
    expect(cmp_tree(cls, observed) == [], "Clopper-Pearson bounds are compared to 1e-12 relative")
    observed["meets_target"]["nominal"]["lower"] = lower * (1 + 1e-9)
    expect(len(cmp_tree(cls, observed)) == 1, "a Clopper-Pearson bound off by 1e-9 is found")
    observed = json.loads(json.dumps(cls))
    observed["meets_target"]["nominal"]["probability"] = 0.9 + 1e-15
    expect(len(cmp_tree(cls, observed)) == 1, "other numbers are compared exactly")
    expect(len(cmp_tree({"a": True}, {"a": 1})) == 1, "a bool is not a number")
    expect(len(cmp_tree({"a": [1, 2]}, {"a": [1]})) == 1, "list lengths")

    # --- timing keys
    noisy = {"ms": 5, "elapsed_ms": 9, "x": [{"wall_time_s": 1.5, "cells_per_s": 2.0, "keep": 3, "d": {"ms": 1}}]}
    expect(strip_timing(noisy) == {"x": [{"keep": 3, "d": {}}]}, f"timing keys stripped: {strip_timing(noisy)}")
    expect(json_diffs(strip_timing({"ms": 1, "a": 2}), strip_timing({"ms": 7, "a": 2})) == [], "timing-only change")
    expect(len(json_diffs(strip_timing({"ms": 1, "a": 2}), strip_timing({"ms": 1, "a": 3}))) == 1, "real change")

    # --- factors: the recorded elements carry the sample's block on every key
    block = {"flux_factor": 1.1, "rate_scale": {"3": 1.2}, "decay_scale": {"Co60": 0.9}, "yield_scale": {}}
    good = {"sample": 0, "factors": block, "error": None,
            "elements": {"Fe": {"error": None, "factors": dict(block)}, "Co": {"error": None, "factors": dict(block)}}}
    ck = Checks()
    check_common_factors([good], ["Fe", "Co"], ck)
    expect(ck.nfail == 0, f"common factors accept equal blocks: {ck.failures}")
    bad = copy.deepcopy(good)
    bad["elements"]["Co"]["factors"]["decay_scale"] = {"Co60": 0.91}
    ck = Checks()
    check_common_factors([bad], ["Fe", "Co"], ck)
    expect(ck.nfail == 1, f"common factors find a differing element: {ck.nfail}")
    flux = copy.deepcopy(good)
    flux["elements"]["Fe"]["factors"]["flux_factor"] = 1.0
    ck = Checks()
    check_common_factors([flux], ["Fe", "Co"], ck)
    expect(ck.nfail == 1, "common factors find a differing flux factor")

    # --- G4 helpers: spec with factors, composition, sample choice
    base = {"spectrum": {"total": 100.0, "flux_per_group": [1.0, 2.0]}, "photon": {"x": 1}, "options": {"mode": "auto"},
            "title": "t"}
    comp = full_comp("Fe", {"Cr": 9.0}, {"Co": 0.005, "Nb": 0.0})
    expect(set(comp) == {"Co", "Cr", "Fe"} and close(comp["Fe"], 90.995) and "Nb" not in comp
           and comp["Cr"] == 9.0, f"full_comp {comp}")
    try:
        full_comp("Fe", {"Cr": 100.0}, {})
        expect(False, "no balance left is refused")
    except GateError:
        pass
    s = factor_spec(base, comp, block, "x")
    expect(s["spectrum"]["total"] == 110.00000000000001 or close(s["spectrum"]["total"], 110.0), "total scaled")
    expect(s["spectrum"]["flux_per_group"] == [1.0, 2.0], "group fluxes untouched when a total exists")
    expect(s["options"]["rate_scale"] == {"3": 1.2} and s["options"]["decay_scale"] == {"Co60": 0.9}
           and "yield_scale" not in s["options"] and s["options"]["mode"] == "coupled"
           and s["options"]["prune"] == "reach", "options from the block, empty maps absent")
    expect("photon" not in s and s["material"]["composition"] == comp and base["spectrum"]["total"] == 100.0,
           "photon dropped, the base is not modified")
    s2 = factor_spec({"spectrum": {"flux_per_group": [1.0, 2.0]}}, comp, block, "x")
    expect(s2["spectrum"]["flux_per_group"] == [1.1, 2.2], f"group fluxes scaled without a total: {s2['spectrum']}")
    picks = pick_samples(20, 158001)
    expect(len(picks) == 5 and {0, 10, 19} <= set(picks) and picks == pick_samples(20, 158001)
           and all(0 <= p < 20 for p in picks), f"pick_samples {picks}")
    expect(is_linear("activity:Nb94") and is_linear("clearance_index") and not is_linear("dose.contact_proxy")
           and not is_linear("waste_class:C"), "linear endpoints")

    # --- refusal builders: each variant differs from the baseline by its one edit
    doc = {"schema": "actinv-impurities-1", "base_spec": "base_spec.json", "confidence_level": 0.5,
           "sampling": {"channels": {"cross_section_mf33": True}, "samples": 72, "seed": 1}}
    base_spec = {"options": {"mode": "auto"}}
    variants = refusal_variants(doc, base_spec, {"first_order": True})
    expect(set(variants) == set(REASON_WORDS), f"refusal names {sorted(variants)}")
    expect(variants["samples_below_minimum"][0]["sampling"]["samples"] == 71
           and variants["samples_below_minimum"][0]["confidence_level"] == 0.95, "few samples at 0.95")
    expect(variants["composition_channel"][0]["sampling"]["channels"]["composition_rel_std"] == {"Fe": 0.01},
           "composition channel")
    expect("sampling" not in variants["confidence_without_sampling"][0]
           and variants["confidence_without_sampling"][0]["confidence_level"] == 0.95, "confidence without sampling")
    for name, key in (("base_rate_scale", "rate_scale"), ("base_decay_scale", "decay_scale"),
                      ("base_yield_scale", "yield_scale"), ("base_gas", "gas")):
        expect(key in variants[name][1]["options"] and variants[name][0] == doc, f"{name} edits the base only")
    expect(variants["base_gas"][1]["options"]["gas"] is True, "gas true")
    expect(variants["base_uncertainty"][1]["uncertainty"] == {"first_order": True}, "base uncertainty")
    expect(doc["sampling"]["samples"] == 72 and base_spec == {"options": {"mode": "auto"}},
           "the baseline is not modified")

    # --- the worked example's verification fields
    good_out = {"verification": {"verified": True, "uncertainty": {"verified": True}},
                "uncertainty": {"verification_pass": True}}
    expect(all(g8_verification_checks(good_out).values()), "verification fields read")
    bad_out = {"verification": {"verified": True}, "uncertainty": {"verification_pass": False}}
    expect(not all(g8_verification_checks(bad_out).values()), "a failed verification is seen")

    # --- the handbook section helpers
    chapter = ("## Before\n\n```sh\nactinv impurities a.json b.json\n```\n\n## Add nuclear-data uncertainty\n\n"
               "```json\n \"confidence_level\": 0.95,\n \"sampling\": {\"samples\": 72}\n```\n\n"
               "```sh\n$ actinv impurities examples/x.json out.json --no-verify\n```\n\nOutput:\n\n```json\n  \"k\": 1,\n"
               "  \"n\": 72\n```\n\nIt takes about 50 minutes.\n\n## After\n\nmore\n")
    sec = section_text(chapter)
    expect("Before" not in sec and "After" not in sec and "50 minutes" in sec, "section bounds")
    expect(section_commands(sec) == ["actinv impurities examples/x.json out.json --no-verify"], "section commands")
    expect(json_block_keys(sec) == {"confidence_level": 0.95, "sampling": {"samples": 72}}, "section json block")
    pairs = p157.chapter_pairs(sec)
    expect(len(pairs) == 1 and p157.line_run_in(pairs[0][1], '{\n  "z": 0,\n  "k": 1,\n  "n": 72\n}'),
           f"output excerpt pairs: {pairs}")
    expect(runtime_stated(sec) and not runtime_stated("no number here"), "runtime statement found")
    expect(section_text("## Other\n") == "", "an absent section is empty")

    if fails:
        for f in fails:
            print(f"P158 selftest FAIL: {f}")
        return 1
    print("P158 selftest: all checks passed")
    return 0


# ------------------------------------------------------------------------------ main


ALL_GATES = ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9")
JUDGED = ("G0", "G2", "G3", "G4", "G5", "G6", "G7", "G8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", default=str(DEFAULT_REFERENCE))
    ap.add_argument("--candidate", default=os.environ.get("ACTINV_BIN", str(ROOT / "target" / "release" / "actinv")))
    ap.add_argument("--gates", default=",".join(ALL_GATES))
    ap.add_argument("--g1-log", help="record-only log of fmt, check, clippy and tests (G1 is external)")
    ap.add_argument("--photon-response", default=str(DEFAULT_PHOTON), help="actinv-photon-response-1 file, all elements")
    ap.add_argument("--timeout", type=int, default=14400, help="seconds allowed per command")
    ap.add_argument("--cwd", default=str(ROOT), help="directory holding actinv-data (relative spec paths resolve there)")
    ap.add_argument("--work", default="target/p158-work")
    ap.add_argument("--out", default="results/p158_bands.json")
    ap.add_argument("--g8-output", help="the worked example's output from a run already made (G6 and G8)")
    ap.add_argument("--g8-runtime", type=float, help="that run's wall time in seconds")
    ap.add_argument("--g8-log", help="that run's captured stdout and stderr (for output excerpts that show them)")
    ap.add_argument("--selftest", "--self-check", action="store_true", dest="selftest")
    args = ap.parse_args(argv)
    if args.selftest:
        return self_test()
    gates = [g.strip().upper() for g in args.gates.split(",") if g.strip()]
    unknown = [g for g in gates if g not in ALL_GATES]
    if unknown:
        ap.error(f"unknown gates {unknown}")
    ref, cand = Path(args.reference).resolve(), Path(args.candidate).resolve()
    work = Path(args.work)
    work = (work if work.is_absolute() else ROOT / work).resolve()
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else ROOT / out_path
    needs_bin = [g for g in gates if g in ("G2", "G3", "G4", "G5", "G6", "G7", "G8")]
    try:
        if "G2" in gates and not ref.is_file():
            raise MissingInput(f"reference binary {ref} is absent")
        if needs_bin and not cand.is_file() and not (needs_bin == ["G8"] and args.g8_output):
            raise MissingInput(f"candidate binary {cand} is absent")
        data_home = p157.find_data_home(args.cwd) if needs_bin else Path(args.cwd)
        photon = None
        if needs_bin:
            photon_file = Path(args.photon_response).expanduser()
            if not photon_file.is_file():
                raise MissingInput(f"photon response {photon_file} is absent (needed for dose.contact_proxy)")
            photon = {"path": str(photon_file), "sha256": sha256_file(photon_file)}
        if args.g8_output and not Path(args.g8_output).is_file():
            raise MissingInput(f"--g8-output {args.g8_output} is absent")
    except MissingInput as error:
        print(f"P158: missing input: {error}", file=sys.stderr)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    cwd = Path(args.cwd)
    record = {"protocol": "protocols/ACTINV-P158_PROTOCOL.md", "protocol_sha256": sha256_file(PROTOCOL),
              "checker_sha256": sha256_file(__file__),
              "reference": str(ref), "reference_sha256": sha256_file(ref) if ref.is_file() else None,
              "candidate": str(cand), "candidate_sha256": sha256_file(cand) if cand.is_file() else None,
              "data_home": str(data_home), "photon_response": photon, "gates_requested": gates,
              "external_gates": {"G1": "fmt, check --tests, clippy -D warnings, workspace tests: coordinator",
                                 "G9": "CI on the pushed evidence commit: coordinator"},
              "gates": {}}
    if args.g1_log:
        p = Path(args.g1_log)
        record["g1_log"] = {"path": str(p), "sha256": sha256_file(p) if p.is_file() else None}
    g = record["gates"]

    def guarded(name, fn):
        try:
            g[name] = fn()
        except (GateError, p157.SchemaError, KeyError, ValueError, IndexError, TypeError, OSError, AttributeError,
                MissingInput, ZeroDivisionError) as error:
            g[name] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        print(f"P158 {name}: {'PASS' if g[name].get('pass') else 'FAIL'}"
              + (f" ERROR {g[name]['error']}" if "error" in g[name] else ""))
        sys.stdout.flush()

    solver = p157.Solver(cand, work / "g4", data_home, args.timeout)
    clr = p157.Clearance()
    main_run = BandsRun(work, "g4_case", cand, cwd, args.timeout, data_home, photon)
    repeat_run = BandsRun(work, "g7_repeat", cand, cwd, args.timeout, data_home, photon)
    other_run = BandsRun(work, "g7_other_seed", cand, cwd, args.timeout, data_home, photon,
                         seed=json.loads(EXAMPLE_UNC.read_text())["sampling"]["seed"] + 1 if EXAMPLE_UNC.is_file() else None)
    shared = {}

    def g8():
        if "got" not in shared:
            if "G8" not in gates and not args.g8_output:
                raise GateError("the worked example's output is needed: include G8 or pass --g8-output")
            shared["got"] = obtain_g8(cand, cwd, args.timeout, work, data_home, photon, args.g8_output,
                                      args.g8_runtime, args.g8_log)
        return shared["got"]

    if "G0" in gates:
        guarded("G0", gate_g0)
    if "G2" in gates:
        guarded("G2", lambda: gate_g2(ref, cand, work, data_home, args.timeout, photon))
    if "G3" in gates:
        guarded("G3", lambda: gate_g3(main_run))
    if "G4" in gates:
        guarded("G4", lambda: gate_g4(main_run, solver, clr))
    if "G5" in gates:
        guarded("G5", lambda: gate_g5(main_run))
    if "G6" in gates:
        guarded("G6", lambda: gate_g6(cand, work, cwd, args.timeout, data_home, photon, g8))
    if "G7" in gates:
        guarded("G7", lambda: gate_g7(main_run, repeat_run, other_run))
    if "G8" in gates:
        guarded("G8", lambda: gate_g8(g8()))
    record["g4_solves"] = {"run": solver.count, "memo_hits": solver.hits}
    judged = [x for x in JUDGED if x in gates]
    passed = all(g.get(x, {}).get("pass") is True for x in judged)
    if not passed:
        record["verdict"] = "FAIL"
    elif len(judged) == len(JUDGED):
        record["verdict"] = "PASS"
    else:
        record["verdict"] = "PARTIAL"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n")
    for x in judged:
        res = g.get(x, {})
        for f in (res.get("failure_list") or [])[:5]:
            print(f"  {x}: {f['check']}: expected {str(f['expected'])[:100]!r}, observed {str(f['observed'])[:120]}")
        for row in res.get("cases") or []:
            if isinstance(row, dict) and not row.get("pass"):
                print(f"  {x}/{row.get('id')}: exit {row.get('exit', row.get('candidate_exit'))} "
                      f"{(row.get('message') or row.get('candidate_stderr') or '')[:160]}")
    print("P158 G1, G9: external (not evaluated here)")
    print(f"P158: {record['verdict']} ({out_path})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
