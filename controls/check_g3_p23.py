#!/usr/bin/env python3
"""Independent checker for the P23 G3 damage-observables gate.

Imports no ACTINV production, audit or scoring module. Verifies the frozen
protocol hash, the opening-commit ancestry, the prior verdict record, and the
damage battery evidence produced by controls/p23_damage.py. It additionally
re-parses the persisted mini-corpus ENDF files, re-collapses their MF=3/MT=444
sections with its own lethargy integrator, and compares against the committed
built table -- the producer's collapse code is never trusted. With
``--self-test`` it mutates a copy of the evidence and proves rejection.
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
PROTOCOL = ROOT / "protocols/ACTINV-P23_PROTOCOL.md"
AMENDMENT = ROOT / "protocols/ACTINV-P23_AMENDMENT_A.md"
EVIDENCE = ROOT / "results/g3_p23_damage.json"
CORPUS = ROOT / "results/g3_p23_corpus"
BUILT_TABLE = ROOT / "results/g3_p23_damage_table.json"
OUTPUT = ROOT / "results/g3_p23_check.json"
G0_BASELINE = ROOT / "results/g0_p23_identity_baseline.json"

PROTOCOL_SHA256 = "fa0df3411e7e2d1d8c5777810db03e76563d6dec1f695fb9219dc0ce7ee59dd5"
OPENING_COMMIT = "325f20704ead9bda1dd5eac3523a1d7b574c3537"
TOL = 1e-9

EXPECTED_CHECKS = {
    "table_runs",
    "constant_row_closed_form",
    "nuclide_row_three_group_fold",
    "partial_nuclide_coverage_honest",
    "element_row_covers_nuclide_remainder",
    "uncovered_targets_named",
    "require_complete_fails_closed",
    "missing_displacement_energy_named",
    "nuclide_displacement_key_rejected",
    "fed_reservoir_atoms_displace",
    "coupled_mode_damage_block",
    "noncanonical_target_rejected",
    "negative_row_rejected",
    "sha_mismatch_rejected",
    "outputs_token_requires_section",
    "build_damage_runs",
    "build_damage_recollapse",
    "build_damage_provenance",
    "tendl_2025_uncovered_honest",
    "mesh_single_cell_damage_parity",
    "python_damage_parity",
    "no_damage_identity",
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


# ---- own ENDF MF=3/MT=444 reader + lethargy collapse (no shared code with the producer)

def _endf_value(field: str):
    text = field.strip()
    if not text:
        return 0.0
    text = text.replace("D", "E").replace("d", "E")
    for pos in range(len(text) - 1, 0, -1):
        if text[pos] in "+-" and text[pos - 1] not in "eE":
            text = text[:pos] + "E" + text[pos:]
            break
    return float(text)


def read_mt444(path: Path):
    """Extract the (x, y, INT) points of the single-region MF=3/MT=444 TAB1 in a file."""
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if len(line) < 75:
            i += 1
            continue
        mf, mt = int(line[70:72]), int(line[72:75])
        if mf != 3 or mt != 444:
            i += 1
            continue
        i += 1  # HEAD
        head = lines[i]
        n2 = int(head[55:66])
        i += 1
        interp = lines[i]
        law = int(interp[11:22])
        i += 1
        points = []
        while len(points) < 2 * n2:
            row = lines[i]
            for c in range(6):
                field = row[11 * c : 11 * c + 11]
                if field.strip():
                    points.append(_endf_value(field))
            i += 1
        xys = [(points[2 * j], points[2 * j + 1]) for j in range(n2)]
        return xys, law
    return None, None


def lethargy(law, x1, x2, y1, y2, a, b):
    r = (b - a) / a
    ln = math.log1p(r)
    if law == 1:
        return y1 * ln
    if law == 2:
        m = (y2 - y1) / (x2 - x1)
        return (y1 + m * (a - x1)) * ln + m * a * (r - ln)
    if law == 3:
        s = math.log1p((x2 - x1) / x1)
        ya = y1 + math.log1p((a - x1) / x1) / s * (y2 - y1)
        yb = y1 + math.log1p((b - x1) / x1) / s * (y2 - y1)
        return (ya + yb) * ln / 2.0
    if law == 5:
        p = math.log(y2 / y1) / math.log(x2 / x1)
        return y1 * (a / x1) ** p * ((b / a) ** p - 1.0) / p
    raise ValueError(f"INT={law}")


def collapse(xys, law, bounds):
    out = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        a, b = max(lo, xys[0][0]), min(hi, xys[-1][0])
        total = 0.0
        if b > a:
            for (x1, y1), (x2, y2) in zip(xys[:-1], xys[1:]):
                if x2 <= a or x1 >= b or x2 <= x1:
                    continue
                total += lethargy(law, x1, x2, y1, y2, max(a, x1), min(b, x2))
        out.append(total / math.log(hi / lo))
    return out


def check_evidence(evidence: dict, failures: list[str]) -> None:
    if evidence.get("schema") != "actinv-p23-g3-damage-1":
        failures.append("evidence schema is not actinv-p23-g3-damage-1")
    checks = evidence.get("checks")
    if not isinstance(checks, dict):
        failures.append("evidence has no checks map")
        return
    if set(checks) != EXPECTED_CHECKS:
        missing = sorted(EXPECTED_CHECKS - set(checks))
        extra = sorted(set(checks) - EXPECTED_CHECKS)
        failures.append(f"check set differs; missing={missing} extra={extra}")
        return
    for name, value in checks.items():
        if value is not True:
            failures.append(f"check {name} is {value!r}, expected true")
    if evidence.get("pass") != all(checks.values()):
        failures.append("recorded pass flag inconsistent with the check map")
    tolerance = evidence.get("tolerance")
    if not isinstance(tolerance, (int, float)) or tolerance > 1e-8:
        failures.append(f"comparison tolerance {tolerance!r} looser than 1e-8")
    details = evidence.get("details")
    if not isinstance(details, dict):
        failures.append("evidence lacks a details map")
    corpus_ev = evidence.get("evidence", {}).get("corpus", {})
    if not isinstance(corpus_ev, dict) or len(corpus_ev) < 2:
        failures.append("evidence lacks a corpus provenance map")


def check_corpus_recollapse(evidence: dict, failures: list[str]) -> None:
    """Re-parse the persisted corpus, re-collapse each MT=444, compare to the committed table."""
    if not CORPUS.is_dir() or not BUILT_TABLE.exists():
        failures.append("persisted corpus or built table missing")
        return
    corpus_ev = evidence.get("evidence", {}).get("corpus", {})
    for name, entry in corpus_ev.items():
        path = CORPUS / name
        if not path.exists():
            failures.append(f"corpus file {name} missing")
            continue
        observed = sha256(path)
        if observed != entry.get("sha256"):
            failures.append(f"corpus file {name} sha256 {observed} != recorded")
    if sha256(BUILT_TABLE) != evidence.get("evidence", {}).get("built_table_sha256"):
        failures.append("built damage table sha256 differs from the recorded evidence hash")
        return
    table = json.loads(BUILT_TABLE.read_text(encoding="utf-8"))
    bounds = table["boundaries_eV"]
    covered = set()
    for name in corpus_ev:
        xys, law = read_mt444(CORPUS / name)
        if xys is None:
            continue
        canonical = name.split("-")[1].split(".")[0]
        canonical = "".join(c for c in canonical if c.isalpha()) + str(
            int("".join(c for c in canonical if c.isdigit()))
        )
        row = table["targets"].get(canonical)
        if row is None:
            failures.append(f"built table lacks collapsed target {canonical}")
            continue
        expected = collapse(xys, law, bounds)
        diffs = [
            abs(g - e) / abs(e)
            for g, e in zip(row, expected)
            if e != 0.0
        ]
        if any(d > TOL for d in diffs) or len(diffs) != sum(1 for e in expected if e != 0.0):
            failures.append(
                f"checker re-collapse of {canonical} disagrees beyond {TOL}"
            )
        covered.add(canonical)
    if not covered:
        failures.append("checker found no MT=444 sections to re-collapse")
    if table.get("uncovered") != ["Co59"]:
        failures.append(f"built table uncovered list {table.get('uncovered')!r} != ['Co59']")
    files = {f["path"]: f["sha256"] for f in table.get("files", [])}
    if files != {n: corpus_ev[n]["sha256"] for n in corpus_ev}:
        failures.append("built table file provenance does not match the pinned corpus")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = sha256(PROTOCOL)
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    if not AMENDMENT.exists():
        failures.append("Amendment A record is missing")
    head = git("rev-parse", "HEAD")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode == 0
    if ancestor is False:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of {head}")
    protocol_diff = git("diff", f"{OPENING_COMMIT}..HEAD", "--", str(PROTOCOL.relative_to(ROOT)))
    if protocol_diff:
        failures.append("protocol changed after the opening commit")
    for name, expected in EXPECTED_VERDICTS.items():
        path = ROOT / "results" / name
        if not path.exists():
            failures.append(f"missing verdict record {name}")
            continue
        verdict = json.loads(path.read_text(encoding="utf-8")).get("verdict")
        if verdict != expected:
            failures.append(f"{name} verdict {verdict!r} != expected {expected!r}")
    baseline = json.loads(G0_BASELINE.read_text(encoding="utf-8"))
    identity = baseline.get("normalized_result_sha256", {})
    if identity.get("cli_cold") != identity.get("cli_warm"):
        failures.append("G0 baseline cold/warm hashes disagree")
    if not EVIDENCE.exists():
        failures.append("damage battery evidence is missing")
    else:
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        check_evidence(evidence, failures)
        check_corpus_recollapse(evidence, failures)
    return {
        "schema": "actinv-p23-g3-check-1",
        "protocol_sha256": observed_protocol,
        "head_commit": head,
        "opening_commit": OPENING_COMMIT,
        "verdicts_checked": len(EXPECTED_VERDICTS),
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    mutations = {
        "check_flip": lambda v: v["checks"].__setitem__("constant_row_closed_form", False),
        "check_drop": lambda v: v["checks"].pop("python_damage_parity"),
        "pass_flag_flip": lambda v: (
            v["checks"].__setitem__("build_damage_recollapse", False),
            v.__setitem__("pass", True),
        ),
        "schema_rename": lambda v: v.__setitem__("schema", "actinv-p23-g3-damage-0"),
        "tolerance_loosen": lambda v: v.__setitem__("tolerance", 1e-5),
        "corpus_sha_forge": lambda v: v["evidence"]["corpus"]["n-Fe056.tendl"].__setitem__(
            "sha256", "0" * 64
        ),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(evidence)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p23-g3-selftest-") as directory:
            planted = Path(directory) / "planted.json"
            planted.write_text(json.dumps(candidate), encoding="utf-8")
            planted_evidence = json.loads(planted.read_text(encoding="utf-8"))
            failures: list[str] = []
            check_evidence(planted_evidence, failures)
            if name == "corpus_sha_forge":
                check_corpus_recollapse(planted_evidence, failures)
            rejected.append(bool(failures))
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(mutations)} evidence mutations rejected")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
