#!/usr/bin/env python3
"""P166 checker (protocols/ACTINV-P166_PROTOCOL.md): ACTINV fission-product decay heat against the CoNDERC tables.
Reads results/p166_conderc.json written by controls/p166_harness.py. G0-G4 are evaluated here; G5 (CI on the pushed
evidence commit) is external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P166_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Inputs and environment: pinned report, table CSV, yield, decay and archive hashes; byte-identical re-extraction
      when the report is present, and the extraction report's 800 checks, no failures, 16 anomalies; the pinned binary;
      the crates tree of d6f516b; a clean tracked tree; the bounded cgroup.
  G2  Model fidelity per dataset: trace mode; one yield selection for the parent at exactly 0.0253 eV, unclamped, with
      an effective sum within 1e-6 of 2; rate closure <= 1e-9; step times to 1e-9; parent background <= 1e-6.
  G3  Coverage: every CSV row scored exactly once with finite positive C/E and positive sigma; registered counts.
  G4  The summary and allowed sentences are recomputed here and written to results/p166_verdict.json (with
      --no-write, compared with it).

Verdict: P166-PASS if G0-G4 hold (G5 external). Accuracy is reported, not gated.

Usage: check_p166.py [--record FILE] [--no-write]
       check_p166.py --selftest
"""
import argparse
import copy
import csv
import hashlib
import io
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p166_extract  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P166_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p166_conderc.json"
VERDICT = ROOT / "results" / "p166_verdict.json"
TABLES = ROOT / "results" / "p166" / "conderc_fission_tables.csv"
EXTRACTION = ROOT / "results" / "p166" / "extraction.json"

SCHEMA = "actinv-p166-conderc-1"
TABLES_SHA256 = "0160b82fedf141e71b81f7ff9ad1e26783ef77c999a22ff0b6d8db79759a0cb9"
BINARY_SHA256 = "186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194"
SOURCE_COMMIT = "d6f516b"
PINNED = {
    "decay": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "U233": "8e7e7e1d174471d457bb66c52d66e188beb6bc40eead968136fe4ab59aa588cb",
    "U235": "9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41",
    "Pu239": "a2713aa6d0283b879040a07fa29d3e99b2d660b619c6d11a2a71aa9d5c5a2605",
    "nfpy_archive": "92c5371fdb21eecf4989f48828671b904186abc6386b3d7510c8fcee2ee5ffcf",
}
PARENT_ZA = {"U233": 92233, "U235": 92235, "Pu239": 94239}
THERMAL_EV = 0.0253
GIB = 1 << 30
# dataset -> {channel: points}, as registered
COUNTS = {
    "FR76-U235-24h": {"total": 86, "gamma": 86, "beta": 86},
    "FR79-Pu239-1000s": {"total": 57, "gamma": 55, "beta": 55},
    "FR79-Pu239-24h": {"total": 74, "gamma": 74, "beta": 74},
    "FR79-U235-1000s": {"total": 72, "gamma": 70, "beta": 70},
    "FR79-U235-20000s": {"total": 77, "gamma": 73, "beta": 73},
    "FR79-U235-24h": {"total": 81, "gamma": 77, "beta": 77},
    "FR79-U235-35d": {"total": 83, "gamma": 79, "beta": 79},
    "SC78-U235-1h": {"total": 25},
    "SC78-U235-4h": {"total": 58},
    "SC78-U235-22.35h": {"total": 37},
    "FI76-Pu239-pulse": {"total": 21},
    "FI76-U233-pulse": {"total": 21},
}
SCHROCK_WINDOW_S = (600.0, 10000.0)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def g0():
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    logged = HASH_LOG.read_text()
    problems = []
    for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P166_AMENDMENT_*.md"))]:
        if f"{sha256(path)}  protocols/{path.name}" not in logged:
            problems.append(f"{path.name} hash not in protocol_hash.txt")
    return problems


def cgroup_ok(cg):
    try:
        memory = int(cg["memory.max"])
        tasks = int(cg["pids.max"])
        quota, period = cg["cpu.max"].split()
        return (memory <= 6 * GIB and cg["memory.swap.max"] == "0" and tasks <= 128
                and quota != "max" and int(quota) <= 2 * int(period))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def g1(record, *, reextract=True, source_tree=None):
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("wrong schema")
    for name, digest in PINNED.items():
        if record["inputs"].get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not the pin")
    if record["tables"]["sha256"] != TABLES_SHA256:
        problems.append("table CSV hash in the record is not the pin")
    if TABLES.is_file() and sha256(TABLES) != TABLES_SHA256:
        problems.append("checked-out table CSV is not the pin")
    extraction = json.loads(EXTRACTION.read_text()) if EXTRACTION.is_file() else {}
    if (extraction.get("printed_ratio_checks"), extraction.get("printed_ratio_failures"),
            len(extraction.get("anomalies", [None] * 99))) != (800, 0, 16):
        problems.append("extraction report is not 800 checks, 0 failures, 16 anomalies")
    if reextract and p166_extract.PDF.is_file():
        text = subprocess.run(["pdftotext", "-layout", str(p166_extract.PDF), "-"], capture_output=True,
                              text=True, check=True, timeout=120).stdout
        rows, _, _ = p166_extract.extract(text)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=p166_extract.FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        if hashlib.sha256(buffer.getvalue().encode()).hexdigest() != TABLES_SHA256:
            problems.append("re-extraction from the report does not reproduce the pinned CSV")
    environment = record["environment"]
    if environment.get("binary_sha256") != BINARY_SHA256:
        problems.append("binary is not the pinned P163 release binary")
    expected_tree = source_tree if source_tree is not None else git("rev-parse", f"{SOURCE_COMMIT}:crates")
    if environment.get("crates_tree") != expected_tree:
        problems.append(f"crates tree is not {SOURCE_COMMIT}'s")
    if not environment.get("clean_tree"):
        problems.append("tracked tree was not clean")
    if not cgroup_ok(environment.get("cgroup", {})):
        problems.append("harness cgroup is not the bounded scope")
    return problems


def g2(record):
    problems = []
    for name, dataset in record["datasets"].items():
        model = dataset["model"]
        if model["mode"] != "trace":
            problems.append(f"{name}: mode {model['mode']}")
        selection = model["fission_yields"].get("selection", [])
        if model["fission_yields"].get("energy") != "fixed" or len(selection) != 1:
            problems.append(f"{name}: not one fixed-energy yield selection")
            continue
        chosen = selection[0]
        if (chosen.get("parent_ZA") != PARENT_ZA.get(dataset["parent"]) or chosen.get("clamped")
                or chosen.get("requested_energy_eV") != THERMAL_EV or chosen.get("lower_energy_eV") != THERMAL_EV
                or chosen.get("upper_weight") != 0.0):
            problems.append(f"{name}: yield selection is not {dataset['parent']} at exactly {THERMAL_EV} eV")
        if abs(chosen.get("effective_yield_sum", 0.0) - 2.0) > 1e-6:
            problems.append(f"{name}: effective yield sum {chosen.get('effective_yield_sum')}")
        if model["relative_rate_closure"] > 1e-9:
            problems.append(f"{name}: rate closure {model['relative_rate_closure']}")
        if model["max_relative_time_error"] > 1e-9:
            problems.append(f"{name}: step times off by {model['max_relative_time_error']}")
        if model["max_background_fraction"] > 1e-6:
            problems.append(f"{name}: parent background {model['max_background_fraction']}")
    return problems


def g3(record, csv_rows):
    problems = []
    expected = {}
    for row in csv_rows:
        key = (row["dataset"], float(row["cooling_time_s"]), row["channel"], row["run"])
        expected[key] = expected.get(key, 0) + 1
    seen = {}
    for name, dataset in record["datasets"].items():
        counts = {}
        for point in dataset["points"]:
            key = (name, point["cooling_time_s"], point["channel"], point["run"])
            seen[key] = seen.get(key, 0) + 1
            counts[point["channel"]] = counts.get(point["channel"], 0) + 1
            ce = point["C_over_E"]
            if not (isinstance(ce, float) and math.isfinite(ce) and ce > 0 and point["sigma"] > 0):
                problems.append(f"{name} t={point['cooling_time_s']} {point['channel']}: C/E {ce}")
        if counts != COUNTS.get(name):
            problems.append(f"{name}: counts {counts} are not the registered {COUNTS.get(name)}")
    if set(record["datasets"]) != set(COUNTS):
        problems.append("dataset set is not the registered one")
    if seen != expected:
        problems.append("scored points do not match the CSV rows one to one")
    return problems


def stats(points, value_key):
    calculated_key = "calculated" if value_key == "C_over_E" else "origen_calculated"
    logs = sorted(abs(math.log(point[value_key])) for point in points)
    signed = [math.log(point[value_key]) for point in points]
    n = len(logs)
    within = lambda k: sum(abs(point[calculated_key] - point["measured"]) <= k * point["sigma"]
                           for point in points) / n
    return {
        "points": n,
        "geometric_mean_C_over_E": math.exp(math.fsum(signed) / n),
        "median_abs_log_C_over_E": statistics.median(logs),
        "p90_abs_log_C_over_E": logs[min(n - 1, math.ceil(0.9 * n) - 1)],
        "max_abs_log_C_over_E": logs[-1],
        "within_1_sigma": within(1),
        "within_2_sigma": within(2),
    }


def summary(record):
    flat = []
    for name, dataset in record["datasets"].items():
        for point in dataset["points"]:
            entry = dict(point, dataset=name, parent=dataset["parent"], laboratory=dataset["laboratory"])
            entry["ORIGEN_C_over_E"] = point["origen_calculated"] / point["measured"] if point[
                "origen_calculated"] else None
            flat.append(entry)

    def both(points):
        result = {"ACTINV": stats(points, "C_over_E")}
        origen = [point for point in points if point["ORIGEN_C_over_E"]]
        if len(origen) == len(points):
            result["ORIGEN"] = stats(origen, "ORIGEN_C_over_E")
        return result

    total = [point for point in flat if point["channel"] == "total"]
    secondary = [point for point in total if not point["dataset"].startswith("SC78")
                 or SCHROCK_WINDOW_S[0] <= point["cooling_time_s"] <= SCHROCK_WINDOW_S[1]]
    result = {
        "primary_total": both(total),
        "secondary_total_schrock_600_10000s": both(secondary),
        "gamma": both([point for point in flat if point["channel"] == "gamma"]),
        "beta": both([point for point in flat if point["channel"] == "beta"]),
        "by_parent_total": {parent: both([p for p in total if p["parent"] == parent])
                            for parent in sorted({p["parent"] for p in total})},
        "by_laboratory_total": {lab: both([p for p in total if p["laboratory"] == lab])
                                for lab in sorted({p["laboratory"] for p in total})},
        "by_dataset": {name: {channel: both([p for p in flat if p["dataset"] == name and p["channel"] == channel])
                              for channel in sorted({p["channel"] for p in flat if p["dataset"] == name})}
                       for name in sorted(record["datasets"])},
    }
    experiments = len(record["datasets"])
    primary = result["primary_total"]
    sentences = [
        f"Against {primary['ACTINV']['points']} measured total decay-heat points from {experiments} fission decay-heat "
        f"experiments at IRT, UC Berkeley and CEA Cadarache (U-233, U-235 and Pu-239 thermal fission), ACTINV with "
        f"ENDF/B-VIII.0 yields and decay data has a median |ln C/E| of "
        f"{primary['ACTINV']['median_abs_log_C_over_E']:.3f} and a geometric-mean C/E of "
        f"{primary['ACTINV']['geometric_mean_C_over_E']:.3f}.",
        f"On the same points, ORIGEN with ENDF/B-VII.0 yields and ENDF/B-VII.1 decay data (Gauld, 2019) has a median "
        f"|ln C/E| of {primary['ORIGEN']['median_abs_log_C_over_E']:.3f} and a geometric-mean C/E of "
        f"{primary['ORIGEN']['geometric_mean_C_over_E']:.3f}.",
        *[f"For {parent}, ACTINV's median |ln C/E| is {value['ACTINV']['median_abs_log_C_over_E']:.3f} over "
          f"{value['ACTINV']['points']} points." for parent, value in result["by_parent_total"].items()],
        f"ACTINV's separately measured gamma and beta heat (Friesenhahn) have median |ln C/E| of "
        f"{result['gamma']['ACTINV']['median_abs_log_C_over_E']:.3f} and "
        f"{result['beta']['ACTINV']['median_abs_log_C_over_E']:.3f}.",
    ]
    return result, sentences


def read_csv():
    with open(TABLES, newline="") as stream:
        return list(csv.DictReader(stream))


def evaluate(record, csv_rows, *, reextract=True, source_tree=None):
    gates = {"G0": g0(), "G1": g1(record, reextract=reextract, source_tree=source_tree), "G2": g2(record),
             "G3": g3(record, csv_rows)}
    result, sentences = (summary(record) if not gates["G3"] else ({}, []))
    gates["G4"] = [] if sentences else ["summary not computed (coverage failed)"]
    return gates, result, sentences


def selftest():
    """Synthetic records: one passing, then one defect per gate."""
    csv_rows = []
    record = {"schema": SCHEMA, "inputs": {k: {"sha256": v} for k, v in PINNED.items()},
              "tables": {"sha256": TABLES_SHA256},
              "environment": {"binary_sha256": BINARY_SHA256, "crates_tree": "tree", "clean_tree": True,
                              "cgroup": {"memory.max": str(6 * GIB), "memory.swap.max": "0", "pids.max": "128",
                                         "cpu.max": "200000 100000"}},
              "datasets": {}}
    for name, channels in COUNTS.items():
        parent = name.split("-")[1]
        points = []
        for channel, count in channels.items():
            for index in range(count):
                time = 10.0 * (index + 1)
                csv_rows.append({"dataset": name, "cooling_time_s": repr(time), "channel": channel, "run": ""})
                points.append({"cooling_time_s": time, "channel": channel, "run": "", "measured": 2.0, "sigma": 0.15,
                               "calculated": 2.0 * 1.05, "C_over_E": 1.05, "log_C_over_E": math.log(1.05),
                               "residual_over_sigma": 1.0, "origen_calculated": 1.9, "origen_c_over_m": 0.95})
        record["datasets"][name] = {
            "parent": parent, "laboratory": "lab", "points": points,
            "model": {"mode": "trace", "relative_rate_closure": 0.0, "max_relative_time_error": 0.0,
                      "max_background_fraction": 1e-12,
                      "fission_yields": {"energy": "fixed", "selection": [{
                          "parent_ZA": PARENT_ZA[parent], "clamped": False, "requested_energy_eV": THERMAL_EV,
                          "lower_energy_eV": THERMAL_EV, "upper_weight": 0.0, "effective_yield_sum": 1.9999998}]}}}
    failures = []

    def run(label, mutate, gate):
        trial = copy.deepcopy(record)
        rows = copy.deepcopy(csv_rows)
        mutate(trial, rows)
        gates, result, _ = evaluate(trial, rows, reextract=False, source_tree="tree")
        bad = [name for name in ("G1", "G2", "G3") if gates[name]]
        if gate is None and bad:
            failures.append(f"{label}: expected pass, failed {bad}: {[gates[b] for b in bad]}")
        if gate is not None and gate not in bad:
            failures.append(f"{label}: expected {gate} to fail")
        return result

    result = run("clean", lambda r, c: None, None)
    primary = result["primary_total"]["ACTINV"]
    if (primary["points"] != 692 or abs(primary["geometric_mean_C_over_E"] - 1.05) > 1e-12
            or abs(primary["median_abs_log_C_over_E"] - math.log(1.05)) > 1e-12 or primary["within_1_sigma"] != 1.0):
        failures.append(f"clean summary wrong: {primary}")
    if abs(result["primary_total"]["ORIGEN"]["geometric_mean_C_over_E"] - 0.95) > 1e-12:
        failures.append("ORIGEN summary wrong")
    run("binary", lambda r, c: r["environment"].update(binary_sha256="0" * 64), "G1")
    run("yields hash", lambda r, c: r["inputs"]["Pu239"].update(sha256="0" * 64), "G1")
    run("cgroup", lambda r, c: r["environment"]["cgroup"].update({"memory.max": "max"}), "G1")
    run("dirty", lambda r, c: r["environment"].update(clean_tree=False), "G1")
    run("tree", lambda r, c: r["environment"].update(crates_tree="other"), "G1")
    run("energy", lambda r, c: r["datasets"]["FI76-U233-pulse"]["model"]["fission_yields"]["selection"][0].update(
        requested_energy_eV=0.5), "G2")
    run("wrong parent", lambda r, c: r["datasets"]["FR79-Pu239-24h"]["model"]["fission_yields"]["selection"][0].update(
        parent_ZA=92235), "G2")
    run("background", lambda r, c: r["datasets"]["FR76-U235-24h"]["model"].update(max_background_fraction=1e-3), "G2")
    run("closure", lambda r, c: r["datasets"]["SC78-U235-1h"]["model"].update(relative_rate_closure=1e-6), "G2")
    run("dropped point", lambda r, c: r["datasets"]["SC78-U235-4h"]["points"].pop(), "G3")
    run("bad C/E", lambda r, c: r["datasets"]["SC78-U235-4h"]["points"][0].update(C_over_E=float("nan")), "G3")
    run("unscored row", lambda r, c: c.append({"dataset": "SC78-U235-4h", "cooling_time_s": "1e9", "channel": "total",
                                               "run": ""}), "G3")
    # within-sigma counting: a point 3 sigma away counts in neither band
    trial = copy.deepcopy(record)
    point = trial["datasets"]["FI76-U233-pulse"]["points"][0]
    point.update(C_over_E=1.25, calculated=2.5)
    one = stats([dict(point, ORIGEN_C_over_E=None)], "C_over_E")
    if one["within_1_sigma"] != 0.0 or one["within_2_sigma"] != 0.0:
        failures.append(f"sigma bands wrong: {one}")
    print("selftest:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    record = json.loads(args.record.read_text())
    gates, result, sentences = evaluate(record, read_csv())
    passed = all(not problems for problems in gates.values())
    verdict = {
        "protocol": "ACTINV-P166",
        "verdict": "P166-PASS (pending G5, CI on the evidence commit)" if passed else "P166-FAIL",
        "gates": {name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
        "summary": result,
        "allowed_sentences": sentences,
    }
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems[:20]:
            print(f"  {problem}")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if recorded.get("summary") != json.loads(json.dumps(result)) or recorded.get(
                    "allowed_sentences") != sentences:
                print("G4: FAIL (verdict file differs from the recomputed summary)")
                passed = False
    else:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    for sentence in sentences:
        print("  " + sentence)
    print(verdict["verdict"] if passed else "P166-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
