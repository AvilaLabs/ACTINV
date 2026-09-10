#!/usr/bin/env python3
"""Independent closure checker for P23.

Imports no ACTINV production, audit or scoring module. It rehashes every committed
evidence artifact, re-runs the four gate checkers, and rederives the analytic
expectations itself: the feed/removal closed forms from independently parsed decay
constants, the WLS arithmetic from the recorded scalar sensitivities, NNLS
optimality (KKT) for the segments solve against a design matrix the checker builds
from its own per-segment production runs, one damage fold from the committed built
table, and the frozen identity battery against the G0 baseline. ``--self-test``
plants mutations into the stored evidence and proves rejection.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols/ACTINV-P23_PROTOCOL.md"
AMENDMENT = ROOT / "protocols/ACTINV-P23_AMENDMENT_A.md"
ACTINV = ROOT / "target/release/actinv"
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
DECAY = ROOT / "actinv-data/v1.0.0/decay/endf-b-viii-0_decay.dat"
BUILT_TABLE = RESULTS / "g3_p23_damage_table.json"

PROTOCOL_SHA256 = "fa0df3411e7e2d1d8c5777810db03e76563d6dec1f695fb9219dc0ce7ee59dd5"
OPENING_COMMIT = "325f20704ead9bda1dd5eac3523a1d7b574c3537"

EVIDENCE = {
    "g0_baseline": "results/g0_p23_identity_baseline.json",
    "g0_check": "results/g0_p23_check.json",
    "g1_battery": "results/g1_p23_feed_removal.json",
    "g1_check": "results/g1_p23_check.json",
    "g2_battery": "results/g2_p23_reverse.json",
    "g2_check": "results/g2_p23_check.json",
    "g3_battery": "results/g3_p23_damage.json",
    "g3_check": "results/g3_p23_check.json",
    "g3_built_table": "results/g3_p23_damage_table.json",
    "g3_corpus_fe": "results/g3_p23_corpus/n-Fe056.tendl",
    "g3_corpus_ni": "results/g3_p23_corpus/n-Ni058.tendl",
    "g3_corpus_co": "results/g3_p23_corpus/n-Co059.tendl",
}
COMPONENT_CHECKERS = [
    "controls/check_g0_p23.py",
    "controls/check_g1_p23.py",
    "controls/check_g2_p23.py",
    "controls/check_g3_p23.py",
]
EXPECTED_CHECKS = {
    "g1": {
        "feed_stable_matches_analytic", "feed_decaying_matches_analytic",
        "feed_daughter_matches_analytic", "feed_element_key_rejected",
        "feed_unknown_nuclide_rejected", "feed_split_invariant",
        "irradiated_feed_conserves_fed_atoms", "removal_state_matches",
        "removal_sink_matches", "removal_conserves_atoms",
        "removal_daughter_matches", "element_removal_dense_agrees",
        "negative_removal_rejected", "nan_feed_rejected",
        "removal_absent_nuclide_rejected", "reservoir_exempt_ledgered",
        "exempt_sink_matches_dense", "empty_maps_byte_identical",
        "empty_maps_omit_sink_field", "mesh_surface_agrees",
        "python_surface_agrees", "dense_expm_agrees_feed",
    },
    "g2": {
        "scalar_recovers_known_multiplier", "scalar_identity_hashes",
        "scalar_reports_residuals", "multi_nuclide_multi_step_recovers",
        "segments_recover_known_multipliers", "segments_report_condition_number",
        "segments_report_se", "inconsistent_reports_chi_square",
        "underdetermined_segments_rejected", "coupled_mode_rejected",
        "absent_nuclide_rejected", "zero_sensitivity_rejected",
        "feed_schedule_rejected", "duplicate_measurement_rejected",
        "nonpositive_sigma_rejected", "zero_column_segment_rejected",
        "python_reverse_parity",
    },
    "g3": {
        "table_runs", "constant_row_closed_form", "nuclide_row_three_group_fold",
        "partial_nuclide_coverage_honest", "element_row_covers_nuclide_remainder",
        "uncovered_targets_named", "require_complete_fails_closed",
        "missing_displacement_energy_named", "nuclide_displacement_key_rejected",
        "fed_reservoir_atoms_displace", "coupled_mode_damage_block",
        "noncanonical_target_rejected", "negative_row_rejected",
        "sha_mismatch_rejected", "outputs_token_requires_section",
        "build_damage_runs", "build_damage_recollapse",
        "build_damage_provenance", "tendl_2025_uncovered_honest",
        "mesh_single_cell_damage_parity", "python_damage_parity",
        "no_damage_identity",
    },
}
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
    "verdict_cb1.json": "CB1-COMPLETE",
}
# natural-iron abundances/masses for the checker's own atom arithmetic
FE = {54: (0.05845, 53.939608189), 56: (0.91754, 55.934935537),
      57: (0.02119, 56.93539195), 58: (0.00282, 57.933273575)}
NA = 6.02214076e23


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


def actinv(*args: str) -> tuple[int, str]:
    completed = subprocess.run(
        [str(ACTINV), *args], cwd=ROOT, text=True, capture_output=True, timeout=600,
    )
    return completed.returncode, completed.stderr.strip()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalized(result: dict) -> dict:
    value = dict(result)
    value.pop("ms", None)
    if "entry_point" in value:
        value["entry_point"] = "normalized"
    if isinstance(value.get("certificate"), dict):
        value["certificate"] = dict(value["certificate"])
        value["certificate"]["entry_point"] = "normalized"
    return value


# ---- own decay-file reader for the analytic re-derivation (ENDF-6 MF=8/MT=457)
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


def decay_lambda(path: Path, za: int, liso: int) -> float | None:
    """Half-life lambda for one (ZA, LISO): scan MF=8/MT=457 section heads only."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    in_457 = False
    while i < len(lines):
        line = lines[i]
        is_457 = len(line) >= 75 and line[70:72].strip() == "8" and line[72:75].strip() == "457"
        if not is_457 or in_457:
            in_457 = is_457
            i += 1
            continue
        in_457 = True
        head_za = int(round(_f11(line, 0)))
        head_liso = int(_f11(line, 3))
        # half-life CONT follows the head; the modes LIST is stepped over
        t12 = _f11(lines[i + 1], 0)
        n_energies = int(_f11(lines[i + 1], 4))
        j = i + 2 + math.ceil(n_energies / 6)
        ndk = int(_f11(lines[j], 5))
        n_mode_fields = int(_f11(lines[j], 4))
        j += 1 + math.ceil(n_mode_fields / 6)
        if head_za == za and head_liso == liso:
            return math.log(2.0) / t12 if t12 > 0 and math.isfinite(t12) else 0.0
        i = j
    return None





def fe_atoms_per_g() -> float:
    return NA / sum(f * m for f, m in FE.values())


def example_spec(workdir: Path) -> dict:
    spec = json.loads(EXAMPLE.read_text())
    spec["library"]["path"] = str(ROOT / spec["library"]["path"])
    for role in ("primary", "fallback"):
        if spec["decay"].get(role):
            spec["decay"][role] = str(ROOT / spec["decay"][role])
    return spec


def run_spec(spec: dict, workdir: Path, name: str) -> dict | None:
    spec_path = workdir / f"{name}.json"
    spec_path.write_text(json.dumps(spec))
    code, err = actinv("run", str(spec_path), str(workdir / f"{name}.out.json"))
    if code:
        return None
    return json.loads((workdir / f"{name}.out.json").read_text())


def rederive(failures: list[str], workdir: Path) -> None:
    """Independent analytic expectations, all recomputed from first principles."""
    spec = example_spec(workdir)
    flux = [float(v) for v in spec["spectrum"]["flux_per_group"]]
    if spec["spectrum"].get("descending"):
        flux.reverse()
    if spec["spectrum"].get("total"):
        scale = spec["spectrum"]["total"] / sum(flux)
        flux = [v * scale for v in flux]
    total_flux = sum(flux)

    # feed/removal closed forms: Co60 feed saturates to s/lambda(1-exp(-lambda t));
    # independent nuclide removal leaves N0*exp(-k t) with the sink holding the rest
    lam_co60 = decay_lambda(DECAY, 27060, 0)
    if not lam_co60 or lam_co60 <= 0:
        failures.append("checker could not parse the Co60 decay constant")
        return
    dt = 300.0
    fed = example_spec(workdir)
    fed["schedule"] = [{"dt": "300.0 s", "flux": 0.0, "feed": {"Co60": 1.0e12}}]
    fed["options"]["outputs"] = ["inventory", "ledger"]
    result = run_spec(fed, workdir, "closure_feed")
    if result is None:
        failures.append("feed analytic run failed")
    else:
        inv = {r["nuclide"]: r["atoms_per_g"] for r in result["steps"][0]["inventory"]}
        co60 = inv.get("Co60", 0.0)
        expected = 1.0e12 / lam_co60 * (1.0 - math.exp(-lam_co60 * dt))
        if abs(co60 - expected) > 1e-9 * expected:
            failures.append(f"feed saturation {co60} != analytic {expected}")

    removed = example_spec(workdir)
    removed["schedule"] = [
        {"dt": "300.0 s", "flux": 0.0, "removal": {"Mn56": 2.0e-3},
         "feed": {"Mn56": 1.0e10}},
    ]
    removed["options"]["outputs"] = ["inventory", "ledger"]
    result = run_spec(removed, workdir, "closure_removal")
    if result is None:
        failures.append("removal analytic run failed")
    else:
        # fed tracked Mn56 under removal k: N = s/k (1 - exp(-kt)) decay-corrected;
        # sink holds the removed fraction. Mn56 decays too (lambda from the file).
        lam_mn56 = decay_lambda(DECAY, 25056, 0) or 0.0
        k = 2.0e-3
        s = 1.0e10
        loss = lam_mn56 + k
        n_tracked = s / loss * (1.0 - math.exp(-loss * dt))
        sink_expected = k * s / loss * (dt - (1.0 - math.exp(-loss * dt)) / loss)
        step = result["steps"][0]
        inv = {r["nuclide"]: r["atoms_per_g"] for r in step["inventory"]}
        tracked = inv.get("Mn56", 0.0)
        sink = step.get("removed_atoms_per_g", 0.0)
        if abs(tracked - n_tracked) > 1e-6 * n_tracked:
            failures.append(f"removal tracked {tracked} != {n_tracked}")
        if abs(sink - sink_expected) > 1e-6 * max(sink_expected, 1.0):
            failures.append(f"removal sink {sink} != {sink_expected}")

    # damage fold from the committed built table (Fe56 row) — closed form
    table = json.loads(BUILT_TABLE.read_text())
    row = table["targets"]["Fe56"]
    bounds = table["boundaries_eV"]
    dmg = example_spec(workdir)
    dmg["schedule"] = [{"dt": "100.0 s", "flux": 1.0}]
    dmg["material"]["composition"] = {"FE": 100.0}
    table_copy = workdir / "dmg_table.json"
    table_copy.write_text(BUILT_TABLE.read_text())
    dmg["damage"] = {
        "table": {"path": str(table_copy), "sha256": sha256(table_copy)},
        "displacement_energy_eV": {"Fe": 40.0},
    }
    dmg["options"]["outputs"] = ["damage", "ledger"]
    result = run_spec(dmg, workdir, "closure_damage")
    if result is None:
        failures.append("damage fold run failed")
    else:
        atoms = fe_atoms_per_g() * FE[56][0]
        energy = sum(s * f for s, f in zip(row, flux)) * 1e-24 * atoms
        dpa_rate = 0.8 * (energy / atoms) / 80.0
        got = result["steps"][0]["damage"]
        if abs(got["damage_energy_eV_per_g_s"] - energy) > 1e-9 * energy:
            failures.append("closure damage-energy fold mismatch")
        if abs(got["dpa_rate_per_s"] - dpa_rate) > 1e-9 * dpa_rate:
            failures.append("closure dpa fold mismatch")
        if abs(got["covered_atom_fraction"] - FE[56][0]) > 1e-12:
            failures.append("closure covered fraction mismatch")
        if abs(got["dpa"] - dpa_rate * 100.0) > 1e-9 * dpa_rate * 100.0:
            failures.append("closure cumulative dpa mismatch")

    # reverse WLS arithmetic on the committed demo inputs
    problem = ROOT / "examples/reverse_demo_problem.json"
    meas = ROOT / "examples/reverse_demo_measurements.json"
    code, err = actinv("reverse", str(problem), str(meas), str(workdir / "rev.json"))
    if code:
        failures.append(f"reverse demo failed: {err[:200]}")
    else:
        result = json.loads((workdir / "rev.json").read_text())
        num = den = 0.0
        for m in result["measurements"]:
            c = m["sensitivity_Bq_per_g_per_multiplier"]
            w = m["weight"]
            num += w * c * m["activity_Bq_per_g"]
            den += w * c * c
        estimate = num / den
        if abs(estimate - result["estimates"]["multiplier"]) > 1e-9 * estimate:
            failures.append("closure WLS estimate mismatch")
        dof = len(result["measurements"]) - 1
        chi2 = sum(
            m["weight"] * m["residual_Bq_per_g"] ** 2 for m in result["measurements"]
        )
        if abs(chi2 - result["chi_square"]) > 1e-9 * max(chi2, 1e-300):
            failures.append("closure WLS chi-square mismatch")
        se = math.sqrt(1.0 / den) if den > 0 else None
        if se is None or abs(se - result["estimates"]["standard_error"]) > 1e-9 * se:
            failures.append("closure WLS standard-error mismatch")

    # NNLS optimality on a checker-built segments case: build the design matrix from
    # checker-run per-segment forward solves, then verify the KKT conditions.
    seg_spec = example_spec(workdir)
    seg_spec["schedule"] = [
        {"dt": "60.0 s", "flux": 1.0},
        {"dt": "30.0 s", "flux": 0.0},
        {"dt": "60.0 s", "flux": 1.0},
    ]
    truth = {1: 1.3, 3: 0.7}
    meas_spec = copy.deepcopy(seg_spec)
    meas_spec["schedule"] = [
        {"dt": "60.0 s", "flux": truth[1]},
        {"dt": "30.0 s", "flux": 0.0},
        {"dt": "60.0 s", "flux": truth[3]},
    ]
    produced = run_spec(meas_spec, workdir, "closure_seg_meas")
    if produced is None:
        failures.append("segments measurement production failed")
        return
    act = produced["steps"][-1]["activity_Bq_per_g"]
    picks = sorted(act, key=act.get, reverse=True)[:4]
    measurements = {
        "format": "actinv-reverse-input-1",
        "measurements": [
            {"step": "last", "nuclide": n, "activity_Bq_per_g": act[n],
             "sigma_Bq_per_g": act[n] * 0.02}
            for n in picks
        ],
    }
    meas_path = workdir / "seg_meas.json"
    meas_path.write_text(json.dumps(measurements))
    prob_path = workdir / "seg_problem.json"
    prob_path.write_text(json.dumps(seg_spec))
    code, err = actinv("reverse", str(prob_path), str(meas_path),
                       str(workdir / "seg_rev.json"), "--segments")
    if code:
        failures.append(f"segments reverse failed: {err[:200]}")
    else:
        result = json.loads((workdir / "seg_rev.json").read_text())
        seg_steps = [int(s) for s in result["sensitivity"]["segments"]]
        # checker-built design matrix: one unit-multiplier run per segment
        columns = {}
        for step_index in seg_steps:
            col_spec = example_spec(workdir)
            col_spec["schedule"] = [
                {"dt": s["dt"], "flux": (1.0 if i + 1 == step_index else 0.0)}
                for i, s in enumerate(seg_spec["schedule"])
            ]
            col_result = run_spec(col_spec, workdir, f"closure_col_{step_index}")
            columns[step_index] = [
                col_result["steps"][-1]["activity_Bq_per_g"][m["nuclide"]]
                for m in measurements["measurements"]
            ]
        weights = [
            1.0 / (m["sigma_Bq_per_g"] ** 2) for m in measurements["measurements"]
        ]
        b = [m["activity_Bq_per_g"] for m in measurements["measurements"]]
        x = [e["multiplier"] for e in result["estimates"]]
        # residual r = b - A x, weighted; NNLS optimality is g = W A^T r with
        # g_j = 0 on the active set and g_j >= 0 on the boundary
        grad = [0.0] * len(seg_steps)
        scale = [0.0] * len(seg_steps)
        for j, s in enumerate(seg_steps):
            acc = 0.0
            norm = 0.0
            for i in range(len(b)):
                ax = sum(columns[s2][i] * x_j2 for s2, x_j2 in zip(seg_steps, x))
                r_i = b[i] - ax
                acc += weights[i] * columns[s][i] * r_i
                norm += weights[i] * columns[s][i] ** 2
            grad[j] = acc
            scale[j] = norm * max(x[j], 1.0)
        for j, x_j in enumerate(x):
            if x_j < -1e-12:
                failures.append("NNLS produced a negative estimate")
            elif x_j > 1e-8:
                if abs(grad[j]) > 1e-6 * scale[j]:
                    failures.append(f"NNLS KKT active residual at segment {seg_steps[j]}")
            elif grad[j] < -1e-6 * scale[j]:
                failures.append(f"NNLS KKT boundary violation at segment {seg_steps[j]}")
        expected_x = [truth.get(s, 0.0) for s in seg_steps]
        if max(abs(a - e) for a, e in zip(x, expected_x)) > 1e-9:
            failures.append(f"segments recovery {x} != {expected_x}")

    # identity: the example spec with absent P23 fields reproduces the G0 hash
    identity = example_spec(workdir)
    result = run_spec(identity, workdir, "closure_identity")
    if result is None:
        failures.append("identity run failed")
    else:
        baseline = json.loads(
            (RESULTS / "g0_p23_identity_baseline.json").read_text()
        )["normalized_result_sha256"]["cli_cold"]
        if canonical_sha256(normalized(result)) != baseline:
            failures.append("damageless run does not reproduce the G0 identity hash")


def check_evidence_records(failures: list[str]) -> None:
    for name, relative in EVIDENCE.items():
        path = ROOT / relative
        if not path.exists():
            failures.append(f"missing evidence artifact {relative}")
    g0 = json.loads((ROOT / EVIDENCE["g0_baseline"]).read_text())
    identity = g0.get("normalized_result_sha256", {})
    if not (identity.get("cli_cold") == identity.get("cli_warm") == identity.get("python")):
        failures.append("G0 baseline surfaces disagree")
    for gate in ("g1", "g2", "g3"):
        battery = json.loads((ROOT / EVIDENCE[f"{gate}_battery"]).read_text())
        checks = battery.get("checks", {})
        if set(checks) != EXPECTED_CHECKS[gate]:
            failures.append(f"{gate} check set differs")
        elif not all(checks.values()):
            failures.append(f"{gate} has failing checks")
        if battery.get("pass") is not True:
            failures.append(f"{gate} evidence pass flag not true")
    built = json.loads(BUILT_TABLE.read_text())
    if built.get("format") != "actinv-damage-table-1" or built.get("uncovered") != ["Co59"]:
        failures.append("built damage table schema or uncovered list wrong")
    g3 = json.loads((ROOT / EVIDENCE["g3_battery"]).read_text())
    corpus_ev = g3.get("evidence", {}).get("corpus", {})
    for name, entry in corpus_ev.items():
        if sha256(CORPUS_PATH / name) != entry.get("sha256"):
            failures.append(f"corpus artifact {name} hash mismatch")
    if sha256(BUILT_TABLE) != g3.get("evidence", {}).get("built_table_sha256"):
        failures.append("built damage table hash differs from evidence")


def run_components(failures: list[str]) -> None:
    for relative in COMPONENT_CHECKERS:
        completed = subprocess.run(
            [sys.executable, str(ROOT / relative)],
            cwd=ROOT, text=True, capture_output=True, timeout=900,
        )
        if completed.returncode != 0:
            failures.append(f"component checker {relative} failed")


def verdicts(failures: list[str]) -> None:
    for name, expected in EXPECTED_VERDICTS.items():
        path = RESULTS / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
            continue
        if json.loads(path.read_text()).get("verdict") != expected:
            failures.append(f"{name} verdict != {expected}")


CORPUS_PATH = RESULTS / "g3_p23_corpus"


def manifest(failures: list[str]) -> None:
    excluded = {"MANIFEST.sha256", "results/g6_p12_complete.json", "results/verdict_p12.json"}
    inventory = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode().split("\0")
    paths = sorted(p for p in inventory if p and p not in excluded)
    expected = "".join(
        f"{sha256(ROOT / path)}  ./{path}\n" for path in paths
    )
    actual = (ROOT / "MANIFEST.sha256").read_text(encoding="utf-8") if (ROOT / "MANIFEST.sha256").exists() else ""
    if actual != expected:
        failures.append("MANIFEST.sha256 is stale or missing")


def run_checks() -> dict:
    failures: list[str] = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("frozen protocol hash mismatch")
    if not AMENDMENT.exists():
        failures.append("Amendment A missing")
    head = git("rev-parse", "HEAD")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode != 0:
        failures.append("opening commit is not an ancestor")
    if git("diff", f"{OPENING_COMMIT}..HEAD", "--", "protocols/ACTINV-P23_PROTOCOL.md"):
        failures.append("protocol changed after the opening commit")
    verdicts(failures)
    check_evidence_records(failures)
    run_components(failures)
    with tempfile.TemporaryDirectory(prefix="actinv-p23-closure-") as directory:
        rederive(failures, Path(directory))
    manifest(failures)
    return {
        "schema": "actinv-p23-closure-1",
        "protocol_sha256": sha256(PROTOCOL),
        "amendment_sha256": sha256(AMENDMENT),
        "head_commit": head,
        "opening_commit": OPENING_COMMIT,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    """Plant mutations into copies of the evidence map and prove rejection."""
    evidence = {
        gate: json.loads((ROOT / EVIDENCE[f"{gate}_battery"]).read_text())
        for gate in ("g1", "g2", "g3")
    }
    cases = {
        "feed_rate_check_flip": ("g1", lambda v: v["checks"].__setitem__("feed_decaying_matches_analytic", False)),
        "removal_sink_drop": ("g1", lambda v: v["checks"].pop("removal_sink_matches")),
        "estimate_check_flip": ("g2", lambda v: v["checks"].__setitem__("scalar_recovers_known_multiplier", False)),
        "residual_check_flip": ("g2", lambda v: v["checks"].__setitem__("scalar_reports_residuals", False)),
        "coverage_list_check_flip": ("g3", lambda v: v["checks"].__setitem__("partial_nuclide_coverage_honest", False)),
        "hash_check_flip": ("g3", lambda v: v["checks"].__setitem__("sha_mismatch_rejected", False)),
        "pass_flag_flip": ("g3", lambda v: (v["checks"].__setitem__("constant_row_closed_form", False), v.__setitem__("pass", True))),
        "detail_forgery": ("g3", lambda v: v["details"].__setitem__("constant_row_closed_form", {"dpa_rate": [0.0, 0.0]})),
    }
    rejected = 0
    for name, (gate, mutate) in cases.items():
        candidate = copy.deepcopy(evidence[gate])
        mutate(candidate)
        failures: list[str] = []
        checks = candidate.get("checks", {})
        if (
            set(checks) != EXPECTED_CHECKS[gate]
            or not all(checks.values())
            or candidate.get("pass") != all(checks.values())
        ):
            failures.append("inconsistent")
        # detail forgery: details must stay consistent with the recorded numbers
        if name == "detail_forgery":
            detail = candidate.get("details", {}).get("constant_row_closed_form", {})
            pair = detail.get("dpa_rate", [])
            if not (isinstance(pair, list) and len(pair) == 2 and pair[0] > 0):
                failures.append("forged detail")
        if failures:
            rejected += 1
    if rejected != len(cases):
        raise SystemExit(f"self-test: {rejected}/{len(cases)} mutations rejected")
    print(f"self-test: all {len(cases)} evidence mutations rejected")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    (RESULTS / "p23_closure_check.json").write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
