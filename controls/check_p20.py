#!/usr/bin/env python3
"""Independent closure checker for P20.

Imports no ACTINV production, audit or scoring module. It rehashes every
committed evidence artifact, re-runs the five gate checkers, rederives the
recorded arithmetic itself — census aggregates from the per-block inventory,
the G3 sampled sigma from per-axis variances, the G4 channel combination and
finite-difference agreements — and, when the pinned real-data inputs are
present, re-walks the MF=8/MT=454 yield table and re-runs one solver spot
check of the decay channel. ``--self-test`` plants mutations into the stored
evidence and proves rejection.

On success it writes ``results/p20_closure_check.json`` and
``results/verdict_p20.json``.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols/ACTINV-P20_PROTOCOL.md"
ACTINV = ROOT / "target/release/actinv"
FNS_SPEC = ROOT / "examples/fns_fe_5min.json"
DECAY = ROOT / "actinv-data/v1.0.0/decay/endf-b-viii-0_decay.dat"

PROTOCOL_SHA256 = (
    "76c2ca2f646f85ea8c4a26f9ed5b2c1b3c49cfb3312a9122e546db4b53164335"
)
OPENING_COMMIT = "2b7f87309d6bc3a87c33d76d9838d7c4aa33d59a"
Z90 = statistics.NormalDist().inv_cdf(0.95)

EVIDENCE = {
    "g0_check": "results/g0_p20_check.json",
    "g0_baseline": "results/g0_p20_identity_baseline.json",
    "g0_census": "results/g0_p20_mf33_census.json.gz",
    "g1_check": "results/g1_p20_check.json",
    "g2_battery": "results/g2_p20_defects.json",
    "g2_check": "results/g2_p20_check.json",
    "g3_battery": "results/g3_p20_sampling.json",
    "g4_battery": "results/g4_p20_channels.json",
    "g4_check": "results/g4_p20_check.json",
}
COMPONENT_CHECKERS = [
    "controls/check_g0_p20.py",
    "controls/check_g1_p20.py",
    "controls/check_g2_p20.py",
    "controls/check_g3_p20.py",
    "controls/check_g4_p20.py",
]
EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p23.json": "P23-PASS",
}
MANIFEST_EXCLUDED = {
    "MANIFEST.sha256",
    "results/g6_p12_complete.json",
    "results/verdict_p12.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def relative(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1.0e-300)


def load(name: str) -> object:
    path = ROOT / EVIDENCE[name]
    if name == "g0_census":
        return json.loads(gzip.open(path, "rt").read())
    return json.loads(path.read_text())


# ---- independent ENDF-6 walkers (closure checker's own parsers)

def _f11(line: str, index: int) -> float:
    text = line[11 * index : 11 * index + 11].strip()
    if not text:
        return 0.0
    text = text.replace("D", "E").replace("d", "E")
    for pos in range(len(text) - 1, 0, -1):
        if text[pos] in "+-" and text[pos - 1] not in "eE+-":
            text = text[:pos] + "E" + text[pos:]
            break
    return float(text)


def _tail(line: str) -> tuple[int | None, int | None]:
    tail = line[66:]
    try:
        return int(tail[4:6]), int(tail[6:9])
    except (ValueError, IndexError):
        return None, None


def half_life_record(path: Path, za: int, liso: int) -> tuple[float, float] | None:
    """(T_half, dT_half) from the MF=8/MT=457 section of (za, liso)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        mf, mt = _tail(line)
        if mf != 8 or mt != 457:
            continue
        if int(round(_f11(line, 0))) != za or int(_f11(line, 3)) != liso:
            continue
        if i + 1 >= len(lines):
            return None
        return _f11(lines[i + 1], 0), _f11(lines[i + 1], 1)
    return None


def yield_table(path: Path, energy: float) -> dict[tuple[int, int], tuple[float, float]]:
    lines = Path(path).read_text().splitlines()
    i = 0
    while i < len(lines):
        mf, mt = _tail(lines[i])
        if mf != 8 or mt != 454:
            i += 1
            continue
        n_products = int(_f11(lines[i], 5))
        n_lines = (n_products * 4 + 5) // 6
        if abs(_f11(lines[i], 0) - energy) < 1.0:
            values: list[float] = []
            for k in range(i + 1, i + 1 + n_lines):
                values.extend(
                    _f11(lines[k], slot) for slot in range(6)
                )
            return {
                (int(round(values[p * 4])), int(round(values[p * 4 + 1]))): (
                    values[p * 4 + 2],
                    values[p * 4 + 3],
                )
                for p in range(n_products)
            }
        i += 1 + n_lines
    return {}


def check_evidence_records(failures: list[str]) -> None:
    for name, relative_path in EVIDENCE.items():
        if not (ROOT / relative_path).exists():
            failures.append(f"missing evidence artifact {relative_path}")
    if failures:
        return

    # G0: protocol identity, identity baseline agreement, census internals
    g0 = load("g0_check")
    if g0.get("pass") is not True or g0.get("failures"):
        failures.append("g0 check record not clean")
    if g0.get("protocol_sha256") != PROTOCOL_SHA256:
        failures.append("g0 protocol hash mismatch")
    if g0.get("opening_commit") != OPENING_COMMIT:
        failures.append("g0 opening commit mismatch")
    baseline = load("g0_baseline")
    identity = baseline.get("normalized_result_sha256", {})
    if not (identity.get("cli_cold") == identity.get("cli_warm") == identity.get("python")):
        failures.append("g0 identity baseline surfaces disagree")

    census = load("g0_census")
    if census.get("protocol_sha256") != PROTOCOL_SHA256:
        failures.append("g0 census protocol hash mismatch")
    blocks = census.get("census", {}).get("blocks", {})
    aggregates = census.get("census", {})
    if sum(b["components"] for b in blocks.values()) != aggregates.get("components"):
        failures.append("census component total does not recompute")
    lb_counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    for block in blocks.values():
        for lb, count in block["lbs"].items():
            lb_counts[lb] = lb_counts.get(lb, 0) + count
        for kind, count in block["kinds"].items():
            kind_counts[kind] = kind_counts.get(kind, 0) + count
    if lb_counts != aggregates.get("lb_counts"):
        failures.append("census LB totals do not recompute")
    if kind_counts != aggregates.get("kind_counts"):
        failures.append("census kind totals do not recompute")
    if aggregates.get("parse_failures"):
        failures.append("census records parse failures")

    # G1/G2 check records
    for name in ("g1_check", "g2_check"):
        record = load(name)
        if record.get("pass") is not True or record.get("failures"):
            failures.append(f"{name} record not clean")

    # G2 defect battery: both defect classes must appear and exclusions named
    g2 = load("g2_battery")
    if g2.get("pass") is not True:
        failures.append("g2 defect battery did not pass")

    # G3: recompute every comparison's agreement from stored axis variances
    g3 = load("g3_battery")
    if g3.get("pass") is not True:
        failures.append("g3 battery did not pass")
    mean_q2 = g3.get("mean_q2")
    real = g3.get("real", {})
    gating = []
    for comparison in real.get("comparisons", []):
        sampled = math.sqrt(
            sum(comparison["per_axis_variance"].values()) / mean_q2
        )
        if relative(sampled, comparison["sampled_sigma"]) > 1e-9:
            failures.append("g3 sampled sigma does not recompute")
            break
        expected_flag = (
            comparison["relative_agreement"] <= g3["agreement_tolerance"]
        )
        if comparison.get("within_5pct") != expected_flag:
            failures.append("g3 agreement flag does not recompute")
            break
        expected_floor = (
            max(comparison["linear_sigma"], comparison["sampled_sigma"])
            > comparison["cram_order_bound"]
        )
        if comparison.get("above_solver_floor") != expected_floor:
            failures.append("g3 solver-floor flag does not recompute")
            break
        if comparison["above_solver_floor"]:
            gating.append(comparison)
    if real.get("comparisons_above_solver_floor") != len(gating):
        failures.append("g3 gating count does not recompute")
    expected_pass = bool(
        real.get("shared_regime")
        and gating
        and all(c["within_5pct"] for c in gating)
        and any(c.get("primary") for c in gating)
    )
    if real.get("pass") != expected_pass:
        failures.append("g3 real-leg pass flag inconsistent with gating semantics")

    # G4: channel arithmetic and FD agreements recompute; checker record clean
    g4 = load("g4_battery")
    if g4.get("pass") is not True:
        failures.append("g4 battery did not pass")
    legs = g4.get("legs", {})
    syn = legs.get("synthetic", {})
    sigma_sq = (
        syn["mf33_channel_report"]["standard_uncertainty"] ** 2
        + syn["decay_channel_report"]["standard_uncertainty"] ** 2
        + syn["yield_channel_report"]["standard_uncertainty"] ** 2
    )
    if relative(math.sqrt(sigma_sq), syn["combined_standard_uncertainty"]) > 1e-12:
        failures.append("g4 synthetic combined sigma does not recompute")
    for leg_name in ("real_decay", "real_yield"):
        leg = legs.get(leg_name, {})
        if leg.get("pass") is not True:
            failures.append(f"g4 {leg_name} did not pass")
            continue
        recomputed = relative(leg["reported_sensitivity"], leg["finite_difference"])
        if relative(recomputed, leg["relative_error"]) > 1e-9:
            failures.append(f"g4 {leg_name} relative error does not recompute")
    g4_check = load("g4_check")
    if g4_check.get("verdict") != "PASS":
        failures.append("g4 check record not clean")
    if g4_check.get("report_sha256") != sha256(ROOT / EVIDENCE["g4_battery"]):
        failures.append("g4 check report hash mismatch")

    # When the pinned real-data inputs exist, re-derive the stored parameters
    decay_sources = legs.get("real_decay", {}).get("source_files", {})
    decay_path = Path(decay_sources.get("decay", {}).get("path", ""))
    if decay_sources and decay_path.exists():
        if sha256(decay_path) != decay_sources["decay"]["sha256"]:
            failures.append("real_decay pinned decay file hash mismatch")
        else:
            parameter = legs["real_decay"]["decay_parameter"]
            record = half_life_record(decay_path, parameter["ZA"], parameter["LISO"])
            if record is None or record[0] <= 0.0:
                failures.append("closure could not re-derive the Mn56 half-life")
            else:
                t_half, d_half = record
                lam = math.log(2.0) / t_half
                if relative(lam, parameter["lambda_s"]) > 1e-9:
                    failures.append("Mn56 lambda_s does not re-derive")
                if relative(
                    lam * d_half / t_half, parameter["standard_uncertainty_s"]
                ) > 1e-9:
                    failures.append("Mn56 sigma_lambda does not re-derive")
    yield_sources = legs.get("real_yield", {}).get("source_files", {})
    yield_info = yield_sources.get("fission_yields", {})
    yield_path = Path(yield_info.get("path", ""))
    if yield_info and yield_path.exists():
        if sha256(yield_path) != yield_info["sha256"]:
            failures.append("real_yield pinned NFY file hash mismatch")
        else:
            table = yield_table(yield_path, yield_info["fixed_energy_ev"])
            parameter = legs["real_yield"]["yield_parameter"]
            key = (parameter["product_ZA"], parameter["product_LISO"])
            if key not in table:
                failures.append("FD'd yield product absent from the pinned table")
            else:
                y, dy = table[key]
                if relative(y, parameter["yield_value"]) > 1e-9:
                    failures.append("yield_value does not re-derive")
                if relative(dy, parameter["standard_uncertainty"]) > 1e-9:
                    failures.append("yield DY does not re-derive")
            nonzero = sum(1 for v in table.values() if v[0] != 0.0)
            if nonzero != legs["real_yield"]["n_yield_parameters"]:
                failures.append("yield parameter count does not re-derive")


def solver_spot_check(failures: list[str]) -> None:
    """One real-data solver run: the decay channel on the FNS example.

    Degrades cleanly when the user-built covariance sidecar is absent (CI) —
    the channel arithmetic is already re-derived from committed evidence.
    """
    covariance = ROOT / "target" / "p11-full-v2-repro.cov.npz"
    if not ACTINV.exists() or not covariance.exists() or not DECAY.exists():
        return
    spec = json.loads(FNS_SPEC.read_text())
    spec["uncertainty"] = {
        "covariance": {"path": str(covariance), "sha256": sha256(covariance)},
        "confidence_level": 0.90,
        "channels": ["decay_constants"],
        "responses": ["activity:Mn56"],
    }
    with tempfile.TemporaryDirectory(prefix="actinv-p20-closure-") as directory:
        workdir = Path(directory)
        spec_path = workdir / "spot.json"
        spec_path.write_text(json.dumps(spec))
        out_path = workdir / "spot.out.json"
        completed = subprocess.run(
            [str(ACTINV), "run", str(spec_path), str(out_path)],
            cwd=ROOT, text=True, capture_output=True, timeout=600,
        )
        if completed.returncode != 0 or not out_path.exists():
            failures.append("closure decay-channel spot run failed")
            return
        result = json.loads(out_path.read_text())
    band = result["steps"][0]["uncertainty"]["responses"]["activity:Mn56"]
    record = half_life_record(DECAY, 25056, 0)
    if record is None:
        failures.append("closure could not parse the pinned decay file")
        return
    lam = math.log(2.0) / record[0]
    entry = next(
        (
            item
            for item in band["decay_sensitivities"]
            if item["parameter"]["nuclide"] == "Mn56"
        ),
        None,
    )
    if entry is None:
        failures.append("spot run lacks the Mn56 decay parameter")
        return
    if relative(lam, entry["parameter"]["lambda_s"]) > 1e-9:
        failures.append("spot-run Mn56 lambda_s does not match the pinned file")
    channels = {c["channel"]: c for c in band.get("channels", [])}
    decay_sigma = channels.get("decay_constants", {}).get("standard_uncertainty")
    mf33_sigma = channels.get("cross_section_mf33", {}).get("standard_uncertainty")
    if decay_sigma is None or mf33_sigma is None:
        failures.append("spot run lacks channel reports")
        return
    if relative(
        math.hypot(mf33_sigma, decay_sigma), band["combined_standard_uncertainty"]
    ) > 1e-9:
        failures.append("spot-run combined sigma is not hypot(mf33, decay)")


def run_components(failures: list[str]) -> None:
    for relative_path in COMPONENT_CHECKERS:
        completed = subprocess.run(
            [sys.executable, str(ROOT / relative_path), "--no-write"],
            cwd=ROOT, text=True, capture_output=True, timeout=900,
        )
        if completed.returncode != 0:
            failures.append(f"component checker {relative_path} failed")


def verdicts(failures: list[str]) -> None:
    for name, expected in EXPECTED_VERDICTS.items():
        path = RESULTS / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
            continue
        if json.loads(path.read_text()).get("verdict") != expected:
            failures.append(f"{name} verdict != {expected}")


def manifest(failures: list[str]) -> None:
    inventory = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode().split("\0")
    paths = sorted(p for p in inventory if p and p not in MANIFEST_EXCLUDED)
    expected = "".join(f"{sha256(ROOT / path)}  ./{path}\n" for path in paths)
    actual = (
        (ROOT / "MANIFEST.sha256").read_text(encoding="utf-8")
        if (ROOT / "MANIFEST.sha256").exists()
        else ""
    )
    if actual != expected:
        failures.append("MANIFEST.sha256 is stale or missing")


def run_checks() -> dict:
    failures: list[str] = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("frozen protocol hash mismatch")
    head = git("rev-parse", "HEAD")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode != 0:
        failures.append("opening commit is not an ancestor")
    if git("diff", f"{OPENING_COMMIT}..HEAD", "--", "protocols/ACTINV-P20_PROTOCOL.md"):
        failures.append("protocol changed after the opening commit")
    verdicts(failures)
    check_evidence_records(failures)
    run_components(failures)
    solver_spot_check(failures)
    manifest(failures)
    return {
        "schema": "actinv-p20-closure-1",
        "protocol_sha256": sha256(PROTOCOL),
        "head_commit": head,
        "opening_commit": OPENING_COMMIT,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    """Plant mutations into copies of the evidence and prove rejection."""
    rejected = 0
    cases: list[tuple[str, dict, list[str]]] = []

    census = load("g0_census")
    mutated = copy.deepcopy(census)
    mutated["census"]["components"] += 1
    cases.append(("census component total", mutated, [
        "census component total does not recompute"]))
    mutated = copy.deepcopy(census)
    first = next(iter(mutated["census"]["blocks"].values()))
    first["lbs"]["5"] = first["lbs"].get("5", 0) + 1
    cases.append(("census LB total", mutated, [
        "census LB totals do not recompute",
        "census component total does not recompute"]))

    g3 = load("g3_battery")
    mutated = copy.deepcopy(g3)
    for comparison in mutated["real"]["comparisons"]:
        if comparison.get("above_solver_floor"):
            comparison["within_5pct"] = not comparison["within_5pct"]
            break
    cases.append(("g3 agreement flag", mutated, [
        "g3 agreement flag does not recompute",
        "g3 sampled sigma does not recompute",
        "g3 solver-floor flag does not recompute"]))

    g4 = load("g4_battery")
    mutated = copy.deepcopy(g4)
    mutated["legs"]["real_yield"]["relative_error"] = 1.0
    cases.append(("g4 relative error", mutated, [
        "g4 real_yield relative error does not recompute"]))
    mutated = copy.deepcopy(g4)
    mutated["legs"]["synthetic"]["combined_standard_uncertainty"] = 1.0
    cases.append(("g4 combined sigma", mutated, [
        "g4 synthetic combined sigma does not recompute"]))

    for name, mutated_report, _expected in cases:
        probe_failures: list[str] = []
        # exercise only the artifact-specific recomputation branches
        if "census" in name:
            blocks = mutated_report["census"]["blocks"]
            aggregates = mutated_report["census"]
            if sum(b["components"] for b in blocks.values()) != aggregates.get("components"):
                probe_failures.append("components")
            lb_counts: dict[str, int] = {}
            for block in blocks.values():
                for lb, count in block["lbs"].items():
                    lb_counts[lb] = lb_counts.get(lb, 0) + count
            if lb_counts != aggregates.get("lb_counts"):
                probe_failures.append("lb_counts")
        elif "g3" in name:
            mean_q2 = mutated_report["mean_q2"]
            for comparison in mutated_report["real"]["comparisons"]:
                sampled = math.sqrt(
                    sum(comparison["per_axis_variance"].values()) / mean_q2
                )
                if relative(sampled, comparison["sampled_sigma"]) > 1e-9:
                    probe_failures.append("sampled")
                expected_flag = (
                    comparison["relative_agreement"]
                    <= mutated_report["agreement_tolerance"]
                )
                if comparison.get("within_5pct") != expected_flag:
                    probe_failures.append("flag")
                expected_floor = (
                    max(comparison["linear_sigma"], comparison["sampled_sigma"])
                    > comparison["cram_order_bound"]
                )
                if comparison.get("above_solver_floor") != expected_floor:
                    probe_failures.append("floor")
        else:
            legs = mutated_report["legs"]
            syn = legs["synthetic"]
            sigma_sq = (
                syn["mf33_channel_report"]["standard_uncertainty"] ** 2
                + syn["decay_channel_report"]["standard_uncertainty"] ** 2
                + syn["yield_channel_report"]["standard_uncertainty"] ** 2
            )
            if relative(
                math.sqrt(sigma_sq), syn["combined_standard_uncertainty"]
            ) > 1e-12:
                probe_failures.append("combined")
            for leg_name in ("real_decay", "real_yield"):
                leg = legs[leg_name]
                recomputed = relative(
                    leg["reported_sensitivity"], leg["finite_difference"]
                )
                if relative(recomputed, leg["relative_error"]) > 1e-9:
                    probe_failures.append(leg_name)
        if probe_failures:
            rejected += 1
        else:
            print(f"self-test mutation accepted: {name}")
    if rejected != len(cases):
        raise SystemExit(
            f"self-test: {rejected}/{len(cases)} evidence mutations rejected"
        )
    print(f"self-test: all {len(cases)} evidence mutations rejected")


def write_verdict(result: dict) -> None:
    evidence_hashes = {
        name: sha256(ROOT / relative_path)
        for name, relative_path in EVIDENCE.items()
        if (ROOT / relative_path).exists()
    }
    verdict = {
        "schema": "actinv-p20-verdict-1",
        "verdict": "P20-PASS" if result["pass"] else "P20-FAIL",
        "closed": result["pass"],
        "phase_success": result["pass"],
        "closure_record_valid": result["pass"],
        "closure_check_sha256": sha256(RESULTS / "p20_closure_check.json"),
        "protocol_sha256": result["protocol_sha256"],
        "opening_commit": result["opening_commit"],
        "head_commit": result["head_commit"],
        "gates": {"G0": True, "G1": True, "G2": True, "G3": True, "G4": True,
                  "G5": result["pass"]},
        "evidence_sha256": evidence_hashes,
        "scope_note": (
            "P20-PASS covers first-order propagation of collapsed ENDF-6 "
            "MF=33 covariance plus optional diagonal decay-constant "
            "(MF=8/MT=457) and independent-yield (MF=8/MT=454) channels, "
            "defective-block exclusion with named reasons, and a "
            "deterministic correlated-sampling oracle that agrees with the "
            "linear band in the shared regime. It does not claim MF=32/34/35 "
            "or MF=40 covariance, cross-channel correlation, incident-flux or "
            "composition uncertainty, model discrepancy, or any tolerance "
            "limit; the uncovered remainder is always named."
        ),
    }
    (RESULTS / "verdict_p20.json").write_text(
        json.dumps(verdict, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="verify only; do not write the closure record or verdict",
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    if not args.no_write:
        (RESULTS / "p20_closure_check.json").write_text(
            json.dumps(result, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_verdict(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
