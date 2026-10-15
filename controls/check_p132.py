#!/usr/bin/env python3
"""P132 gates G2-G4 (protocols/ACTINV-P132_PROTOCOL.md): the FISPACT-II input-deck importer.

  G2  Corpus import. controls/p132_import_all.py (driven with the candidate as ACTINV_BIN, the library, decay and
      covariance environment variables removed so the catalog references apply) imports every
      ~/nuclear-data/conderc-fns/fns/*/*.i deck. All 132 must import, every spec must pass
      `actinv validate --files`, and no `unsupported` entry may name a keyword ACTINV implements (IMPLEMENTED_KEYWORDS
      below: the protocol's list plus the keywords the importer interprets). The unsupported entries on other
      keywords are listed, and the keyword x status table is recorded.
  G3  Schedule fidelity, against this script's own parse of each deck and of each FISPACT `.out`:
      - per interval, the imported step duration equals the `.out` "TIME IS" / "COOLING TIME IS" value within 1e-9
        relative, or equals it at the printed resolution (the `.out` prints five significant digits, so a deck time
        such as 0.3156 DAYS cannot match to 1e-9); the cumulative time within the phase (the irradiation, then the
        cooling since it ended, which is what "ELAPSED TIME IS" counts) is checked against it at its printed
        resolution; the step kind (irradiation or cooling) matches; and the flux multiplier times `spectrum.total`
        matches "FLUX AMP IS" at its printed resolution;
      - each multiplier equals the deck's FLUX value divided by `spectrum.total` (1e-12), and `spectrum.total` is the
        first positive FLUX value;
      - the spec's flux_per_group equals the values in the deck's fluxes file bit for bit.
  G4  Run equivalence. Hard check: the imported Fe 1996 5-minute spec and examples/fns_fe_5min.json are run by the
      candidate and their results compared path by path. The imported spec carries `uncertainty` and `summary`
      blocks (UNCERT, MIND, NOSTABLE) that the hand-written spec lacks, so the check is made on the imported spec as
      written (every differing JSON path listed, each tagged with the spec block that explains it where it lies
      under one) and on the imported spec with exactly those two blocks removed. The hard check passes iff that
      second comparison is identical apart from `ms` and the echoed title, paths and declared hash. A third run
      (blocks removed, the hand-written `options` copied in) shows whether any remaining difference is an option
      default (`explained_by_option_defaults`). The spec-level differences are listed too. Report-only: for 10
      decks across elements the imported spec (without the uncertainty and summary blocks) is run and the total
      activity at each interval is tabulated against the FISPACT `.out` "TOTAL ACTIVITY FOR ALL MATERIALS".

G1 (fmt, clippy, tests) and G5 (CI) are not checked here. Overall PASS iff G2, G3 and the G4 hard check pass. Every
binary call is serial, without a shell and under a timeout. Imports and validation run from the repository root.

Usage:
  check_p132.py --candidate BIN [--fns ~/nuclear-data/conderc-fns/fns] [--workdir target/p132-work]
      [--out results/p132_import.json] [--timeout 3600] [--import-timeout 7200] [--reuse]

Run inside the required systemd cgroup. Exit 0 on PASS, 1 on FAIL, 2 on a missing input.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P132_PROTOCOL.md"
IMPORT_ALL = ROOT / "controls" / "p132_import_all.py"
DEFAULT_FNS = Path("~/nuclear-data/conderc-fns/fns").expanduser()
G4_DECK = "Fe/TENDL-2017_1996exp_5min.i"
EXPECTED_DECKS = 132
STATUSES = ("mapped", "approximated", "noted", "unsupported")

# Keywords the importer interprets (the protocol's list: MASS, FUEL, FLUX/FLUXES, TIME/ATOMS/ZERO, PULSE, UNCERTAINTY,
# PROJECTILE, MIND, NOSTABLE, SORTDOMINANT; plus the structural keywords and NOERROR, DENSITY, STEP, SPECTRUM, GETXS,
# GETDECAY). An `unsupported` entry for one of these fails G2.
IMPLEMENTED_KEYWORDS = {"MASS", "FUEL", "FLUX", "FLUXES", "TIME", "ATOMS", "STEP", "SPECTRUM", "ZERO", "PULSE",
                        "ENDPULSE", "UNCERTAINTY", "NOERROR", "PROJECTILE", "MIND", "NOSTABLE", "SORTDOMINANT",
                        "FISPACT", "END", "GETXS", "GETDECAY", "DENSITY"}

DURATION_REL_TOL = 1e-9
MULTIPLIER_REL_TOL = 1e-12
PRINT_SLACK = 1e-9  # relative slack on the half-unit-in-the-last-place bound, for decimal-to-binary conversion
MAX_LISTED = 25
MAX_DIFFS = 5000

# Echoes the protocol allows to differ between the imported and the hand-written spec: title and paths, plus the
# declared library hash that the hand-written spec pins and the importer does not.
G4_ALLOWED = ["/ms", "/spec_title", "/certificate/library", "/certificate/library_sha256_declared",
              "/certificate/decay_primary", "/certificate/decay_fallback",
              "/certificate/inputs/library/path", "/certificate/inputs/library_index/path",
              "/certificate/inputs/decay_primary/path", "/certificate/inputs/decay_fallback/path"]
# Result paths under these segments are attributed to the spec blocks the importer adds.
G4_BLOCK_SEGMENTS = {"uncertainty": ("/uncertainty", "/covariance"), "summary": ("/summary",)}

TIME_UNITS = {"SEC": 1.0, "MIN": 60.0, "HOU": 3600.0, "DAY": 86400.0, "YEA": 365.25 * 86400.0}
ELAPSED_UNITS = {"ps": 1e-12, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "y": 365.25 * 86400.0}
DT_UNITS = {"s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0, "y": 365.25 * 86400.0}


# ------------------------------------------------------------------------------ helpers


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def rel_diff(a, b):
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0.0 else abs(a - b) / scale


def short(value):
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def json_diffs(a, b, path="", out=None, limit=MAX_DIFFS):
    """Every differing JSON path (a key present on one side only is one difference)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append({"path": f"{path}/{key}", "imported": "<absent>" if key not in a else short(a[key]),
                            "hand_written": "<absent>" if key not in b else short(b[key])})
            else:
                json_diffs(a[key], b[key], f"{path}/{key}", out, limit)
            if len(out) >= limit:
                break
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": path, "imported": f"length {len(a)}", "hand_written": f"length {len(b)}"})
        for i, (x, y) in enumerate(zip(a, b)):
            json_diffs(x, y, f"{path}[{i}]", out, limit)
            if len(out) >= limit:
                break
    elif a != b or type(a) is not type(b):
        out.append({"path": path, "imported": short(a), "hand_written": short(b)})
    return out


def is_allowed(path, allowed):
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "[") for p in allowed)


def run_cmd(argv, timeout, cwd=ROOT, env=None):
    """Run without a shell. Returns (exit code, stdout, stderr); the exit code is None on a timeout or OS error."""
    try:
        proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, b"", f"timed out after {timeout} s".encode()
    except OSError as error:
        return None, b"", f"{type(error).__name__}: {error}".encode()
    return proc.returncode, proc.stdout, proc.stderr


def tail(data, n=600):
    return data.decode(errors="replace")[-n:]


# ------------------------------------------------------------------------------ deck, fluxes and .out parsing


def deck_words(text):
    """Words of a FISPACT-II deck: `<< ... >>` comments (which may span lines) removed, `*` text lines skipped."""
    words, in_comment = [], False
    for line in text.splitlines():
        if not in_comment and line.lstrip().startswith("*"):
            continue
        i, kept = 0, []
        while i < len(line):
            if in_comment:
                j = line.find(">>", i)
                if j < 0:
                    i = len(line)
                else:
                    in_comment, i = False, j + 2
            else:
                j = line.find("<<", i)
                if j < 0:
                    kept.append(line[i:])
                    i = len(line)
                else:
                    kept.append(line[i:j])
                    in_comment, i = True, j + 2
        words.extend(" ".join(kept).split())
    return words


def number(word):
    return float(word.replace("D", "E").replace("d", "e"))


def parse_deck(text):
    """FLUX values, the intervals that ran (duration in seconds, flux at that moment), and the first positive FLUX.
    An interval runs at the first ATOMS, STEP or SPECTRUM after its TIME. PULSE loops are not handled."""
    words = deck_words(text)
    flux, pending, steps, fluxes = 0.0, None, [], []
    i = 0
    while i < len(words):
        w = words[i].upper()
        if w == "END":
            break
        if w.startswith("PULSE") or w == "ENDPULSE":
            raise ValueError("the deck uses PULSE, which this checker does not interpret")
        if w == "FLUX":
            flux = number(words[i + 1])
            fluxes.append(flux)
            i += 1
        elif w == "ZERO":
            flux = 0.0
        elif w == "TIME":
            value, unit = number(words[i + 1]), 1.0
            i += 1
            if i + 1 < len(words) and words[i + 1].upper()[:3] in TIME_UNITS:
                unit = TIME_UNITS[words[i + 1].upper()[:3]]
                i += 1
            pending = value * unit
        elif w in ("ATOMS", "STEP", "SPECTRUM"):
            if pending is not None:
                steps.append({"seconds": pending, "flux": flux})
                pending = None
        i += 1
    first_positive = next((f for f in fluxes if f > 0.0), None)
    return {"fluxes": fluxes, "steps": steps, "first_positive_flux": first_positive}


def parse_fluxes_file(path):
    """Group values (highest energy first) of a FISPACT-II fluxes file: every number before the closing title line,
    less the trailing first-wall loading."""
    lines = path.read_text().splitlines()
    last = max(i for i, line in enumerate(lines) if line.strip())
    values = [number(word) for line in lines[:last] for word in line.split()]
    return values[:-1], values[-1]


def parse_files_file(path):
    entries = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if len(words) >= 2:
            entries[words[0].lower()] = words[1]
    return entries


INTERVAL = re.compile(
    r"TIME INTERVAL\s+(\d+)\s*[* ]*?(COOLING TIME IS|TIME IS)\s+([0-9.]+E[+-]\d+)\s+SECS.*?"
    r"ELAPSED TIME IS\s+([0-9.]+)\s*([A-Za-z]+).*?FLUX AMP IS\s+([0-9.]+E[+-]\d+)")
TOTAL_ACTIVITY = re.compile(r"TOTAL ACTIVITY FOR ALL MATERIALS\s+([0-9.]+E[+-]\d+)\s+Bq")


def parse_out(path):
    """The intervals of a FISPACT `.out`: kind, printed times (as text) and the total activity of each."""
    intervals = []
    with open(path, "r", errors="replace") as handle:
        for line in handle:
            m = INTERVAL.search(line)
            if m:
                intervals.append({"index": int(m.group(1)), "kind": "cooling" if m.group(2).startswith("COOLING")
                                  else "irradiation", "seconds_text": m.group(3), "elapsed_text": m.group(4),
                                  "elapsed_unit": m.group(5), "flux_text": m.group(6), "total_activity_Bq": None})
                continue
            m = TOTAL_ACTIVITY.search(line)
            if m and intervals and intervals[-1]["total_activity_Bq"] is None:
                intervals[-1]["total_activity_Bq"] = float(m.group(1))
    return intervals


def matches_print(true_value, printed_text, scale=1.0):
    """Whether `true_value` (in the units scaled by `scale`) rounds to the printed decimal text."""
    printed = Decimal(printed_text)
    half_ulp = float(Decimal(1).scaleb(printed.as_tuple().exponent) / 2)
    return abs(true_value / scale - float(printed)) <= half_ulp * (1.0 + PRINT_SLACK)


def parse_dt(text):
    value, unit = text.split()
    return float(value) * DT_UNITS[unit]


# ------------------------------------------------------------------------------ gates


def gate_g2(corpus, ctx):
    out = {"decks": len(corpus), "expected_decks": EXPECTED_DECKS}
    imp = ctx["import_all"]
    out["import_all"] = {k: v for k, v in imp.items() if k != "summary"}
    summary = imp.get("summary") or {}
    failed = {entry["deck"]: entry["message"] for entry in summary.get("failed", [])}
    out["import_failures"] = [{"deck": k, "message": v} for k, v in sorted(failed.items())][:MAX_LISTED]
    out["n_import_failures"] = len(failed)
    table = defaultdict(Counter)
    unsupported_other = defaultdict(lambda: {"count": 0, "decks": []})
    unsupported_implemented = []
    missing, bad_reports, validation_failures = [], [], []
    for deck in corpus:
        spec_path, report_path = deck["spec"], deck["report"]
        if not spec_path.is_file() or not report_path.is_file():
            missing.append(deck["id"])
            continue
        report = json.loads(report_path.read_text())
        counts = Counter(entry["status"] for entry in report["entries"])
        if {s: counts.get(s, 0) for s in STATUSES} != {s: report["counts"][s] for s in STATUSES}:
            bad_reports.append(f"{deck['id']}: counts do not match the entries")
        if report.get("deck", {}).get("sha256") != deck["sha256"]["deck"]:
            bad_reports.append(f"{deck['id']}: the report's deck hash is not the deck's SHA-256")
        for entry in report["entries"]:
            table[entry["keyword"]][entry["status"]] += 1
            if entry["status"] == "unsupported":
                if entry["keyword"] in IMPLEMENTED_KEYWORDS:
                    unsupported_implemented.append({"deck": deck["id"], "keyword": entry["keyword"],
                                                    "line": entry["line"], "detail": entry["detail"]})
                else:
                    slot = unsupported_other[entry["keyword"]]
                    slot["count"] += 1
                    slot["decks"].append(deck["id"])
        code, _, err = run_cmd([ctx["candidate"], "validate", spec_path, "--files"], ctx["timeout"])
        if code != 0:
            validation_failures.append({"deck": deck["id"], "exit_code": code, "stderr_tail": tail(err, 300)})
        deck["validate_exit_code"] = code
    out["missing_outputs"] = missing
    out["report_inconsistencies"] = bad_reports[:MAX_LISTED]
    out["validate_files"] = {"passed": len(corpus) - len(missing) - len(validation_failures),
                             "failed": validation_failures[:MAX_LISTED], "n_failed": len(validation_failures)}
    out["keyword_status_table"] = {k: {s: v.get(s, 0) for s in STATUSES} for k, v in sorted(table.items())}
    totals = Counter()
    for counter in table.values():
        totals.update(counter)
    out["status_totals"] = {s: totals.get(s, 0) for s in STATUSES}
    out["unsupported_on_implemented_keywords"] = unsupported_implemented[:MAX_LISTED]
    out["n_unsupported_on_implemented_keywords"] = len(unsupported_implemented)
    out["unsupported_on_other_keywords"] = {k: v for k, v in sorted(unsupported_other.items())}
    out["implemented_keywords"] = sorted(IMPLEMENTED_KEYWORDS)
    summary_table = summary.get("by_keyword")
    out["table_matches_import_all_summary"] = (
        summary_table is not None and
        {k: {s: v.get(s, 0) for s in STATUSES} for k, v in summary_table.items()} == out["keyword_status_table"])
    out["pass"] = bool(len(corpus) == EXPECTED_DECKS and imp.get("exit_code") == 0 and not failed and not missing
                       and not bad_reports and not validation_failures and not unsupported_implemented)
    return out


def check_deck_schedule(deck, ctx):
    """G3 for one deck. Returns (record, list of failure strings)."""
    spec = json.loads(deck["spec"].read_text())
    parsed = parse_deck(deck["deck"].read_text())
    intervals = parse_out(deck["out"])
    failures = []
    total = spec["spectrum"].get("total")
    schedule = spec["schedule"]
    rec = {"id": deck["id"], "steps": len(schedule), "deck_steps": len(parsed["steps"]),
           "out_intervals": len(intervals), "spectrum_total": total, "strict": 0, "print_resolution_only": 0}
    if len(schedule) != len(parsed["steps"]) or len(intervals) != len(schedule) + 1:
        failures.append(f"step counts differ: spec {len(schedule)}, deck {len(parsed['steps'])}, "
                        f".out intervals {len(intervals)} (expected spec + 1)")
        return rec, failures
    first = parsed["first_positive_flux"]
    if first is not None and (total is None or rel_diff(total, first) > MULTIPLIER_REL_TOL):
        failures.append(f"spectrum.total {total} is not the first positive FLUX {first}")
    if intervals[0]["kind"] != "irradiation" or float(intervals[0]["seconds_text"]) != 0.0:
        failures.append("the first .out interval is not the zero-time initial state")
    # "ELAPSED TIME IS" counts from the start of the current phase: the irradiation, then the cooling since it ended
    elapsed_expected, phase = 0.0, None
    for k, (step, want, interval) in enumerate(zip(schedule, parsed["steps"], intervals[1:]), start=1):
        dt = parse_dt(step["dt"])
        kind_now = "irradiation" if step["flux"] > 0.0 else "cooling"
        if kind_now != phase:
            elapsed_expected, phase = 0.0, kind_now
        elapsed_expected += dt
        where = f"step {k}"
        # duration against "TIME IS" / "COOLING TIME IS"
        printed = float(interval["seconds_text"])
        if rel_diff(dt, want["seconds"]) > DURATION_REL_TOL:
            failures.append(f"{where}: dt {dt} s differs from the deck's TIME {want['seconds']} s")
        if rel_diff(dt, printed) <= DURATION_REL_TOL:
            rec["strict"] += 1
        elif matches_print(dt, interval["seconds_text"]):
            rec["print_resolution_only"] += 1
        else:
            failures.append(f"{where}: dt {dt} s does not match the .out interval time {interval['seconds_text']} s")
        # cumulative against "ELAPSED TIME IS"
        scale = ELAPSED_UNITS.get(interval["elapsed_unit"])
        if scale is None or not matches_print(elapsed_expected, interval["elapsed_text"], scale):
            failures.append(f"{where}: elapsed-in-phase {elapsed_expected} s does not match ELAPSED TIME IS "
                            f"{interval['elapsed_text']} {interval['elapsed_unit']}")
        # multiplier against FLUX / spectrum.total, kind and FLUX AMP
        multiplier = step["flux"]
        wanted = 0.0 if want["flux"] == 0.0 else want["flux"] / total
        if (multiplier == 0.0) != (wanted == 0.0) or (wanted and rel_diff(multiplier, wanted) > MULTIPLIER_REL_TOL):
            failures.append(f"{where}: multiplier {multiplier} differs from FLUX/total {wanted}")
        kind = "irradiation" if multiplier > 0.0 else "cooling"
        if kind != interval["kind"]:
            failures.append(f"{where}: step is {kind} but the .out interval is {interval['kind']}")
        flux_amp = multiplier * (total or 0.0)
        if not (matches_print(flux_amp, interval["flux_text"]) or flux_amp == float(interval["flux_text"])):
            failures.append(f"{where}: multiplier x total {flux_amp} differs from FLUX AMP IS {interval['flux_text']}")
    # the spectrum
    flux_file = deck["fluxes"]
    if flux_file is None:
        failures.append("no fluxes file found for the deck")
    else:
        values, wall = parse_fluxes_file(flux_file)
        got = spec["spectrum"].get("flux_per_group")
        rec["fluxes_groups"] = len(values)
        rec["fluxes_wall_loading"] = wall
        if got != values or not spec["spectrum"].get("descending", False):
            failures.append("spectrum.flux_per_group is not the fluxes file (highest energy first, descending true)")
    return rec, failures


def gate_g3(corpus, ctx):
    out, failures, records = {"decks": len(corpus)}, {}, []
    for deck in corpus:
        if not deck["spec"].is_file():
            failures[deck["id"]] = ["no imported spec"]
            continue
        try:
            rec, bad = check_deck_schedule(deck, ctx)
        except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as error:
            failures[deck["id"]] = [f"{type(error).__name__}: {error}"]
            continue
        records.append(rec)
        if bad:
            failures[deck["id"]] = bad
    out["decks_checked"] = len(records)
    out["steps_compared"] = sum(r["steps"] for r in records)
    out["durations_equal_within_1e-9"] = sum(r["strict"] for r in records)
    out["durations_equal_at_printed_resolution_only"] = sum(r["print_resolution_only"] for r in records)
    out["decks_with_failures"] = len(failures)
    out["failures"] = {k: v[:5] for k, v in list(sorted(failures.items()))[:MAX_LISTED]}
    out["tolerances"] = {"duration_relative": DURATION_REL_TOL, "multiplier_relative": MULTIPLIER_REL_TOL,
                         "print_resolution": "half a unit in the last printed digit"}
    out["per_deck"] = records
    out["pass"] = bool(not failures and len(records) == len(corpus) == EXPECTED_DECKS)
    return out


def explained_by(path):
    for block, segments in G4_BLOCK_SEGMENTS.items():
        if any(seg in path for seg in segments):
            return block
    return None


def gate_g4(corpus, ctx):
    runner = ctx["runner"]
    deck = next((d for d in corpus if d["rel"] == G4_DECK), None)
    out = {"deck": G4_DECK}
    if deck is None or not deck["spec"].is_file():
        return {**out, "pass": False, "error": "the imported Fe 1996 5-minute spec is missing"}
    imported = json.loads(deck["spec"].read_text())
    hand = json.loads(GATE_SPEC.read_text())
    out["imported_spec"] = record_file(deck["spec"])
    out["hand_written_spec"] = record_file(GATE_SPEC)
    spec_diffs = json_diffs(imported, hand, limit=MAX_DIFFS)
    # the spectrum arrays are summarised, not listed element by element
    out["spec_differences"] = [d for d in spec_diffs if not d["path"].startswith("/spectrum/flux_per_group")][:200]
    out["spec_flux_per_group_differences"] = sum(1 for d in spec_diffs if d["path"].startswith("/spectrum/flux_per_group"))
    stripped = copy.deepcopy(imported)
    removed = [k for k in ("uncertainty", "summary") if stripped.pop(k, None) is not None]
    out["blocks_removed_for_the_second_comparison"] = removed
    with_options = copy.deepcopy(stripped)
    if "options" in hand and "options" not in with_options:
        with_options["options"] = hand["options"]
    res_hand = runner.run("g4_hand_written", GATE_SPEC)
    res_as = runner.run("g4_imported_as_written", imported)
    res_stripped = runner.run("g4_imported_without_blocks", stripped)
    res_options = runner.run("g4_imported_without_blocks_hand_options", with_options)
    if res_hand is None:
        return {**out, "pass": False, "error": "the hand-written run failed; see runs"}
    for label, res in (("as_written", res_as), ("without_blocks", res_stripped),
                       ("without_blocks_with_hand_written_options", res_options)):
        if res is None:
            out[label] = {"error": "the run failed; see runs"}
            continue
        diffs = json_diffs(res, {k: v for k, v in res_hand.items()}, limit=MAX_DIFFS)
        rows = []
        for d in diffs:
            allowed = is_allowed(d["path"], G4_ALLOWED)
            rows.append({**d, "echo": allowed, "explained_by_spec_block": None if allowed else explained_by(d["path"])})
        other = [r for r in rows if not r["echo"]]
        out[label] = {"n_differences": len(rows), "n_echo": len(rows) - len(other),
                      "n_other": len(other),
                      "n_other_explained_by_added_blocks": sum(1 for r in other if r["explained_by_spec_block"]),
                      "n_other_unexplained": sum(1 for r in other if not r["explained_by_spec_block"]),
                      "identical_apart_from_echoes": not other,
                      "truncated": len(diffs) >= MAX_DIFFS,
                      "differences": rows}
    out["pass"] = bool(out.get("without_blocks", {}).get("identical_apart_from_echoes"))
    out["explained_by_option_defaults"] = bool(
        not out["pass"] and out.get("without_blocks_with_hand_written_options", {}).get("identical_apart_from_echoes"))
    return out


def select_report_decks(corpus):
    """Ten decks, one per element, spread evenly over the sorted element directories."""
    by_element = {}
    for deck in corpus:
        by_element.setdefault(deck["element"], deck)
    elements = sorted(by_element)
    picks = sorted({round(i * (len(elements) - 1) / 9) for i in range(10)})
    return [by_element[elements[i]] for i in picks]


def report_activity_table(corpus, ctx):
    runner = ctx["runner"]
    rows = []
    for deck in select_report_decks(corpus):
        entry = {"id": deck["id"], "element": deck["element"]}
        try:
            spec = json.loads(deck["spec"].read_text())
            for key in ("uncertainty", "summary"):
                spec.pop(key, None)
            res = runner.run(f"report_{deck['element']}__{deck['stem']}", spec)
            intervals = parse_out(deck["out"])
            if res is None:
                entry["error"] = "the run failed; see runs"
                rows.append(entry)
                continue
            mass = spec["material"]["mass_g"]
            steps = []
            for k, step in enumerate(res["steps"], start=1):
                fispact = intervals[k]["total_activity_Bq"]
                actinv = mass * math.fsum(step["activity_Bq_per_g"].values())
                steps.append({"interval": k + 1, "t_s": step["t_s"], "fispact_Bq": fispact, "actinv_Bq": actinv,
                              "ratio": actinv / fispact if fispact else None})
            ratios = sorted(s["ratio"] for s in steps if s["ratio"] is not None)
            entry.update({"steps": steps, "ratio_min": ratios[0] if ratios else None,
                          "ratio_median": ratios[len(ratios) // 2] if ratios else None,
                          "ratio_max": ratios[-1] if ratios else None,
                          "ratio_end_of_irradiation": steps[0]["ratio"], "mass_g": mass,
                          "run": "imported spec without the uncertainty and summary blocks"})
        except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as error:
            entry["error"] = f"{type(error).__name__}: {error}"
        rows.append(entry)
    return {"report_only": True, "decks": rows}


# ------------------------------------------------------------------------------ runner for `actinv run`


class Runner:
    def __init__(self, workdir, timeout, reuse, record, candidate):
        self.workdir, self.timeout, self.reuse, self.record, self.candidate = workdir, timeout, reuse, record, candidate

    def run(self, label, spec):
        """`candidate run SPEC OUT` from the repository root. `spec` is a dict (written to the workdir) or a path."""
        if isinstance(spec, dict):
            spec_path = self.workdir / f"{label}.spec.json"
            spec_path.write_text(json.dumps(spec, indent=1) + "\n")
        else:
            spec_path = Path(spec)
        out_file = self.workdir / f"{label}.json"
        info = {"binary": str(self.candidate), "spec": record_file(spec_path), "cwd": str(ROOT)}
        self.record["runs"][label] = info
        if self.reuse and out_file.exists():
            info["reused"] = True
        else:
            started = time.monotonic()
            code, _, err = run_cmd([self.candidate, "run", spec_path, out_file], self.timeout)
            info["exit_code"] = code
            info["wall_s"] = time.monotonic() - started
            if code != 0:
                info["error"] = f"exit code {code}"
                info["stderr_tail"] = tail(err)
                return None
        info["output"] = record_file(out_file)
        return json.loads(out_file.read_text())


# ------------------------------------------------------------------------------ main


def collect_corpus(fns_root, import_dir):
    decks = []
    for path in sorted(fns_root.glob("*/*.i")):
        stem, element = path.stem, path.parent.name
        files_path = path.with_name(f"{stem}_files")
        fluxes = None
        if files_path.is_file():
            name = parse_files_file(files_path).get("fluxes")
            if name and (path.parent / name).is_file():
                fluxes = path.parent / name
        out_path = path.with_suffix(".out")
        entry = {"id": f"{element}__{stem}", "rel": f"{element}/{path.name}", "element": element, "stem": stem,
                 "deck": path, "out": out_path, "files": files_path if files_path.is_file() else None,
                 "fluxes": fluxes, "spec": import_dir / f"{element}__{stem}.json",
                 "report": import_dir / f"{element}__{stem}.import.json"}
        entry["sha256"] = {"deck": sha256_file(path),
                           "out": sha256_file(out_path) if out_path.is_file() else None,
                           "files": sha256_file(files_path) if files_path.is_file() else None,
                           "fluxes": sha256_file(fluxes) if fluxes else None}
        decks.append(entry)
    return decks


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", required=True, help="release binary of the integrated P132 implementation")
    ap.add_argument("--fns", default=str(DEFAULT_FNS), help="root of the FNS deck corpus")
    ap.add_argument("--workdir", default="target/p132-work")
    ap.add_argument("--out", default="results/p132_import.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run or validation")
    ap.add_argument("--import-timeout", type=int, default=7200, help="seconds allowed for the whole corpus import")
    ap.add_argument("--reuse", action="store_true", help="reuse the corpus import and run outputs in the workdir")
    args = ap.parse_args(argv)
    candidate = Path(args.candidate).resolve()
    fns_root = Path(os.path.expanduser(args.fns)).resolve()
    workdir = Path(args.workdir)
    workdir = workdir if workdir.is_absolute() else ROOT / workdir
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else ROOT / out_path
    missing = [f"{label}: {path}" for label, path in (("candidate binary", candidate), ("FNS corpus", fns_root),
                                                      ("importer driver", IMPORT_ALL), ("gate spec", GATE_SPEC),
                                                      ("protocol", PROTOCOL)) if not Path(path).exists()]
    if missing:
        print("missing inputs:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2
    workdir.mkdir(parents=True, exist_ok=True)
    import_dir = workdir / "imports"

    record = {"protocol": "ACTINV-P132", "checker": record_file(Path(__file__).resolve()),
              "protocol_file": record_file(PROTOCOL), "candidate_binary": record_file(candidate),
              "import_driver": record_file(IMPORT_ALL), "gate_spec": record_file(GATE_SPEC),
              "fns_root": str(fns_root), "runs": {},
              "gates": {"G1": {"external": "fmt, clippy -D warnings and workspace tests are not run here"},
                        "G5": {"external": "CI on the pushed commit is not run here"}}}
    corpus = collect_corpus(fns_root, import_dir)
    record["inputs"] = {deck["id"]: deck["sha256"] for deck in corpus}

    # ---- corpus import, through the driver
    summary_path = import_dir / "summary.json"
    imp = {"argv": [sys.executable, str(IMPORT_ALL), str(fns_root), "--out", str(import_dir)],
           "env": {"ACTINV_BIN": str(candidate),
                   "removed": ["ACTINV_LIBRARY", "ACTINV_ENDF_DECAY", "ACTINV_COVARIANCE"]}}
    if args.reuse and summary_path.exists():
        imp["reused"] = True
        imp["exit_code"] = 0 if not json.loads(summary_path.read_text()).get("failed") else 1
    else:
        env = {k: v for k, v in os.environ.items()
               if k not in ("ACTINV_LIBRARY", "ACTINV_ENDF_DECAY", "ACTINV_COVARIANCE")}
        env["ACTINV_BIN"] = str(candidate)
        started = time.monotonic()
        code, stdout, stderr = run_cmd(imp["argv"], args.import_timeout, env=env)
        imp.update({"exit_code": code, "wall_s": time.monotonic() - started, "stdout_tail": tail(stdout, 2000),
                    "stderr_tail": tail(stderr, 2000)})
    if summary_path.exists():
        imp["summary"] = json.loads(summary_path.read_text())
        imp["summary_sha256"] = sha256_file(summary_path)
    record["import_all"] = {k: v for k, v in imp.items() if k != "summary"}

    ctx = {"candidate": candidate, "timeout": args.timeout, "import_all": imp,
           "runner": Runner(workdir, args.timeout, args.reuse, record, candidate)}
    for name, fn in (("G2", gate_g2), ("G3", gate_g3), ("G4", gate_g4)):
        try:
            record["gates"][name] = fn(corpus, ctx)
        except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as error:
            record["gates"][name] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    try:
        record["gates"]["G4"]["activity_table_report_only"] = report_activity_table(corpus, ctx)
    except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as error:
        record["gates"]["G4"]["activity_table_report_only"] = {"report_only": True,
                                                              "error": f"{type(error).__name__}: {error}"}
    overall = all(bool(record["gates"][g].get("pass")) for g in ("G2", "G3", "G4"))
    record["verdict"] = "P132-PASS" if overall else "P132-FAIL"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for gate in ("G2", "G3", "G4"):
        g = record["gates"][gate]
        extra = f" ({g['error']})" if "error" in g else ""
        print(f"P132 {gate}: {'PASS' if g.get('pass') else 'FAIL'}{extra}")
    print(f"P132 verdict: {record['verdict']} ({out_path})")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
