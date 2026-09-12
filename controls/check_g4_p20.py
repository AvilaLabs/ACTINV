#!/usr/bin/env python3
"""P20 G4 independent checker — verifies results/g4_p20_channels.json.

Independence contract: this checker does NOT import ACTINV production code or
the G4 control module. It recomputes every derived quantity in the committed
report — finite-difference relative errors, channel variance combination,
interval widths, coverage strings versus parameter counts — and, when the
referenced real data files exist locally, re-walks the ENDF-6 MF=8/MT=454 and
MF=8/MT=457 records with its own parser to re-derive the stored parameter
values.

Checks:
  1. Report schema, protocol identity, protocol sha256.
  2. Synthetic leg: each recorded check's relative_error recomputed from
     expected/reported; worst_relative_error recomputed; combined variance is
     the exact channel sum; the normal interval is the midpoint ± z90·sigma
     band; coverage strings agree with covered/total counts; the strict
     require_complete rejection, channel-absent cleanliness, and the named
     uncovered lists are present.
  3. Real decay leg: relative error recomputed; combined sigma is the exact
     mf33+decay combination; when the recorded ENDF/B-VIII.0 decay file exists
     its sha256 is verified and Mn56's lambda and sigma_lambda are re-derived
     from the MF=8/MT=457 record (lambda = ln2/T_half, relative sigma =
     dT_half/T_half).
  4. Real yield leg: relative error recomputed; the finite-differenced
     parameter is covered; the compensation product is outside the recorded
     in-chain set; when the recorded NFY file exists its sha256 is verified
     and the U235 -> product independent yield and DY at the recorded energy
     are re-derived, along with the count of nonzero-yield products that
     produces n_yield_parameters.
  5. File-based checks degrade gracefully when data is absent (CI) — recorded
     hashes must still be well-formed.

Self-test: mutating a relative error, a channel sigma, a coverage count, the
protocol hash, the FD parameter's covered flag, or a check's reported value
must each make the checker fail.
"""

import hashlib
import json
import math
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "results" / "g4_p20_channels.json"
PROTOCOL_SHA256 = (
    "76c2ca2f646f85ea8c4a26f9ed5b2c1b3c49cfb3312a9122e546db4b53164335"
)
Z90 = statistics.NormalDist().inv_cdf(0.95)
LN2 = math.log(2.0)
# Nuclides the G4 synthetic decay fixture keeps inside the chain; every other
# fission product is leak-routed and carries exactly zero response
# sensitivity.
IN_CHAIN_ZA = {92235, 54140, 55140, 36092, 37092}


def fail(msg):
    print(f"G4-P20-CHECK-FAIL: {msg}")
    sys.exit(1)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def relative(a, b):
    """The control's convention: symmetric relative difference."""
    return abs(a - b) / max(abs(a), abs(b), 1.0e-300)


def endf_float(text):
    text = text.strip()
    if not text:
        return 0.0
    if "e" not in text.lower() and ("+" in text[1:] or "-" in text[1:]):
        for split in range(len(text) - 1, 0, -1):
            if text[split] in "+-" and text[split - 1].isdigit():
                return float(text[:split] + "e" + text[split:])
    return float(text)


def endf_fields(line):
    return [line[s : s + 11] for s in range(0, 66, 11)]


def endf_tail(line):
    tail = line[66:80]
    try:
        return int(tail[4:6]), int(tail[6:9])
    except (ValueError, IndexError):
        return None, None


def yield_table(path, energy):
    """Independent MF=8/MT=454 walker: {(za, liso): (y, dy)} at `energy`."""
    lines = Path(path).read_text().splitlines()
    i = 0
    while i < len(lines):
        mf, mt = endf_tail(lines[i])
        if mf != 8 or mt != 454:
            i += 1
            continue
        head = endf_fields(lines[i])
        n_products = int(endf_float(head[5]))
        n_lines = (n_products * 4 + 5) // 6
        if abs(endf_float(head[0]) - energy) < 1.0:
            values = []
            for k in range(i + 1, i + 1 + n_lines):
                values.extend(endf_float(f) for f in endf_fields(lines[k]))
            return {
                (
                    int(round(values[p * 4])),
                    int(round(values[p * 4 + 1])),
                ): (values[p * 4 + 2], values[p * 4 + 3])
                for p in range(n_products)
            }
        i += 1 + n_lines
    fail(f"no MT=454 table at {energy} eV in {path}")


def half_life_record(path, za, liso):
    """Independent MF=8/MT=457 walker: (T_half, dT_half) for (za, liso)."""
    lines = Path(path).read_text().splitlines()
    i = 0
    while i < len(lines):
        mf, mt = endf_tail(lines[i])
        if mf != 8 or mt != 457:
            i += 1
            continue
        head = endf_fields(lines[i])
        if int(round(endf_float(head[0]))) == za and int(endf_float(head[3])) == liso:
            # The energy record following the head carries T1/2 in field 0 and
            # its uncertainty in field 1.
            energies = endf_fields(lines[i + 1])
            return endf_float(energies[0]), endf_float(energies[1])
        i += 1
    fail(f"no MT=457 record for ZA {za} LISO {liso} in {path}")


def coverage_ok(report):
    covered = report["covered_parameters"]
    total = report["total_parameters"]
    expected = "complete" if covered == total else "partial"
    if report["coverage"] != expected:
        fail(f"channel {report['channel']} coverage {report['coverage']} "
             f"inconsistent with {covered}/{total}")
    if not (0 <= covered <= total):
        fail(f"channel {report['channel']} counts invalid")


def check_leg_pass(leg, name):
    if leg.get("pass") is not True:
        fail(f"{name} leg did not pass")
    recomputed = relative(leg["reported_sensitivity"], leg["finite_difference"])
    if relative(recomputed, leg["relative_error"]) > 1e-9:
        fail(f"{name} relative_error field inconsistent with stored values")


def source_ok(record, must_exist=False):
    path = Path(record["path"])
    recorded = record["sha256"]
    if len(recorded) != 64 or any(c not in "0123456789abcdef" for c in recorded):
        fail(f"source file {record['path']} records a malformed sha256")
    if path.exists():
        if sha256(path) != recorded:
            fail(f"source file {record['path']} sha256 mismatch")
        return True
    if must_exist:
        fail(f"source file {record['path']} absent")
    return False


def check(report):
    if report.get("schema") != "actinv-p20-g4-channels-1":
        fail("schema mismatch")
    if report.get("protocol") != "ACTINV-P20":
        fail("protocol mismatch")
    if report.get("protocol_sha256") != PROTOCOL_SHA256:
        fail("protocol sha256 mismatch")
    if report.get("pass") is not True:
        fail("overall pass flag is not true")
    legs = report.get("legs", {})
    for name in ("synthetic", "real_decay", "real_yield", "performance"):
        if name not in legs:
            fail(f"{name} leg absent")

    # ---- synthetic leg
    syn = legs["synthetic"]
    if syn.get("pass") is not True:
        fail("synthetic leg did not pass")
    worst = 0.0
    for entry in syn["checks"]:
        recomputed = relative(entry["reported"], entry["expected"])
        if relative(recomputed, entry["relative_error"]) > 1e-9:
            fail(f"synthetic check {entry['name']} relative_error inconsistent")
        worst = max(worst, recomputed)
    if relative(worst, syn["worst_relative_error"]) > 1e-12:
        fail("worst_relative_error inconsistent with checks")
    if worst > 1e-12:
        fail("synthetic leg exceeded machine-precision tolerance")
    sigma_sq = (
        syn["mf33_channel_report"]["standard_uncertainty"] ** 2
        + syn["decay_channel_report"]["standard_uncertainty"] ** 2
        + syn["yield_channel_report"]["standard_uncertainty"] ** 2
    )
    if relative(math.sqrt(sigma_sq), syn["combined_standard_uncertainty"]) > 1e-12:
        fail("synthetic combined sigma is not the channel sum")
    low, high = syn["normal_interval"]
    if relative((high - low) / 2.0, Z90 * syn["combined_standard_uncertainty"]) > 1e-9:
        fail("synthetic normal interval is not midpoint ± z90·sigma")
    if syn.get("interval_consistent") is not True:
        fail("synthetic interval_consistent flag false")
    for key in ("mf33_channel_report", "decay_channel_report", "yield_channel_report"):
        coverage_ok(syn[key])
    if syn["remainder_channel_report"].get("status") != "not_evaluated":
        fail("uncovered remainder must be named, never propagated")
    if syn.get("require_complete_rejected") is not True:
        fail("strict require_complete rejection not exercised")
    if syn.get("channels_absent_clean") is not True:
        fail("channel-absent output cleanliness not verified")
    if "Kr92" not in syn.get("uncovered_decay_constants", []):
        fail("synthetic uncovered decay constants missing Kr92")
    if not any("Kr92" in name for name in syn.get("uncovered_yield_products", [])):
        fail("synthetic uncovered yield products missing the Kr92 entry")

    # ---- real decay leg
    decay = legs["real_decay"]
    check_leg_pass(decay, "real_decay")
    if decay["relative_error"] >= 5e-3:
        fail("real_decay relative error at or above the 5e-3 gate")
    mf33 = decay["mf33_standard_uncertainty"]
    combined = decay["combined_standard_uncertainty"]
    decay_sigma = decay["decay_channel_report"]["standard_uncertainty"]
    if relative(math.hypot(mf33, decay_sigma), combined) > 1e-9:
        fail("real_decay combined sigma is not hypot(mf33, decay)")
    coverage_ok(decay["decay_channel_report"])
    # When every named decay parameter is sensitivity-bearing and covered,
    # the uncovered list must be empty.
    if (
        decay["decay_channel_report"]["covered_parameters"]
        == decay["n_decay_parameters"]
        and decay["uncovered_decay_count"] != 0
    ):
        fail("real_decay names uncovered constants despite complete coverage")
    for key in ("decay", "covariance"):
        if key not in decay.get("source_files", {}):
            fail(f"real_decay source_files lacks {key}")
    if source_ok(decay["source_files"]["decay"]):
        t_half, d_half = half_life_record(
            decay["source_files"]["decay"]["path"],
            decay["decay_parameter"]["ZA"],
            decay["decay_parameter"]["LISO"],
        )
        expected_lambda = LN2 / t_half
        if relative(expected_lambda, decay["decay_parameter"]["lambda_s"]) > 1e-9:
            fail("Mn56 lambda_s inconsistent with the pinned decay file")
        expected_sigma = expected_lambda * d_half / t_half
        if relative(
            expected_sigma, decay["decay_parameter"]["standard_uncertainty_s"]
        ) > 1e-9:
            fail("Mn56 sigma_lambda inconsistent with the pinned decay file")
    source_ok(decay["source_files"]["covariance"])

    # ---- real yield leg
    yleg = legs["real_yield"]
    check_leg_pass(yleg, "real_yield")
    if yleg["relative_error"] >= 5e-3:
        fail("real_yield relative error at or above the 5e-3 gate")
    parameter = yleg["yield_parameter"]
    if parameter.get("covered") is not True:
        fail("the finite-differenced yield parameter must be covered")
    coverage_ok(yleg["yield_channel_report"])
    yield_sigma = yleg["yield_channel_report"]["standard_uncertainty"]
    if yleg["combined_standard_uncertainty"] != yield_sigma:
        fail("real_yield combined sigma must equal the yield channel sigma "
             "(empty MF=33 sidecar; the only sensitive decay parameter is "
             "uncovered)")
    compensator = yleg["compensation_product"]
    if compensator["za"] in IN_CHAIN_ZA:
        fail("compensation product is inside the chain — FD is contaminated")
    for key in ("fission_yields", "library"):
        if key not in yleg.get("source_files", {}):
            fail(f"real_yield source_files lacks {key}")
    if source_ok(yleg["source_files"]["fission_yields"]):
        table = yield_table(
            yleg["source_files"]["fission_yields"]["path"],
            yleg["source_files"]["fission_yields"]["fixed_energy_ev"],
        )
        key = (parameter["product_ZA"], parameter["product_LISO"])
        if key not in table:
            fail("FD yield parameter absent from the recorded NFY table")
        y, dy = table[key]
        if relative(y, parameter["yield_value"]) > 1e-9:
            fail("yield_value inconsistent with the pinned NFY file")
        if relative(dy, parameter["standard_uncertainty"]) > 1e-9:
            fail("yield DY inconsistent with the pinned NFY file")
        if (compensator["za"], compensator["liso"]) not in table:
            fail("compensation product absent from the recorded NFY table")
        nonzero = sum(1 for value in table.values() if value[0] != 0.0)
        if nonzero != yleg["n_yield_parameters"]:
            fail("n_yield_parameters does not match the nonzero-yield count "
                 "in the pinned NFY file")
    source_ok(yleg["source_files"]["library"])

    # ---- performance leg
    perf = legs["performance"]
    if perf.get("pass") is not True:
        fail("performance leg did not pass")
    timings = perf.get("timings_seconds", {})
    for case in (
        "fns_mf33_only",
        "fns_decay_channel",
        "u235_plain",
        "u235_yield_channel",
    ):
        values = timings.get(case, [])
        if len(values) != 2 or not all(
            isinstance(t, (int, float)) and math.isfinite(t) and t > 0.0
            for t in values
        ):
            fail(f"performance leg missing finite repeat timings for {case}")
    if perf["fns_decay_channel_added_parameters"] != decay["n_decay_parameters"]:
        fail("performance leg's decay-parameter count disagrees with the "
             "real_decay leg")
    if perf["u235_yield_channel_added_parameters"] != yleg["n_yield_parameters"]:
        fail("performance leg's yield-parameter count disagrees with the "
             "real_yield leg")


def mutate(report, *path, value):
    clone = json.loads(json.dumps(report))
    node = clone
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return clone


def self_test(report):
    mutations = [
        ("synthetic check reported value",
         mutate(report, "legs", "synthetic", "checks", value=[])),
        ("synthetic combined sigma",
         mutate(report, "legs", "synthetic", "combined_standard_uncertainty",
                value=1.0)),
        ("synthetic coverage string",
         mutate(report, "legs", "synthetic", "decay_channel_report",
                value={"channel": "decay_constants", "coverage": "partial",
                       "covered_parameters": 1, "total_parameters": 1,
                       "standard_uncertainty": 1.0, "status": "propagated"})),
        ("protocol sha256",
         mutate(report, "protocol_sha256", value="0" * 64)),
        ("real_yield relative error",
         mutate(report, "legs", "real_yield", "relative_error", value=1.0)),
        ("real_yield FD parameter covered flag",
         mutate(report, "legs", "real_yield", "yield_parameter",
                value={"covered": False, "product_ZA": 36092,
                       "product_LISO": 0, "yield_value": 0.0135368,
                       "standard_uncertainty": 0.00216589,
                       "parent_ZA": 92235, "parent_LISO": 0,
                       "parent_nuclide": "U235", "product_nuclide": "Kr92"})),
        ("real_decay combined sigma",
         mutate(report, "legs", "real_decay", "combined_standard_uncertainty",
                value=1.0)),
        ("synthetic interval",
         mutate(report, "legs", "synthetic", "normal_interval",
                value=[0.0, 1.0])),
    ]
    for name, mutated in mutations:
        try:
            check(mutated)
        except SystemExit as exit_:
            if exit_.code == 0:
                fail(f"self-test mutation accepted: {name}")
            continue
        fail(f"self-test mutation accepted: {name}")
    print(f"self-test: {len(mutations)} mutations rejected")


def main():
    no_write = "--no-write" in sys.argv
    if not REPORT.exists():
        fail(f"report absent: {REPORT}")
    report = json.loads(REPORT.read_text())
    check(report)
    self_test(report)
    if not no_write:
        out = ROOT / "results" / "g4_p20_check.json"
        out.write_text(json.dumps({
            "checker": "check_g4_p20",
            "report": str(REPORT),
            "report_sha256": sha256(REPORT),
            "verdict": "PASS",
        }, indent=2) + "\n")
    print("G4-P20-CHECK-PASS")


if __name__ == "__main__":
    main()
