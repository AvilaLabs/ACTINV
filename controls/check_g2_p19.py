#!/usr/bin/env python3
"""Independent checker for the P19 G2 runtime gate.

Imports no ACTINV production module. Verifies the frozen protocol hash, the
opening-commit ancestry, the G0/G1 verdict records, and the runtime evidence
produced by controls/p19_runtime.py. Independently re-derives the expected
W-187 production-rate ratio for the fixed-dilution leg by folding the
activation library's Fe-56-free W186 capture rows against the spec flux and
the artifact's own group factors — production code is never imported. With
``--self-test`` it mutates a copy of the evidence and proves rejection.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P19_PROTOCOL.md"
EVIDENCE = ROOT / "results/g2_p19_runtime.json"
OUTPUT = ROOT / "results/g2_p19_check.json"
ARTIFACT = ROOT / "results/g1_p19_shield_artifact.json"
NPZ = ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz"
INDEX = ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g_index.json"
WORK = ROOT / "target/p19_g2_runtime"

PROTOCOL_SHA256 = "8ee3d561fec513b3038fcbabfc8b57a6cce25e36c992f2af311afb6740acce16"
OPENING_COMMIT = "d463d57db4bcd87715eaf0d2082d07a71ec36424"
EXPECTED_CHECKS = {
    "absent_deterministic",
    "fixed_dilution_changes_rate",
    "ledger_records_shielding",
    "certificate_records_shielding",
    "composition_dilution_computes_sigma0",
    "uncovered_named",
    "require_complete_fails_closed",
    "uncertainty_combination_rejected",
    "sha_mismatch_rejected",
    "boundary_mismatch_rejected",
    "mesh_surface_accepts_section",
}
EXPECTED_VERDICTS = {
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


def npy_layout(blob: bytes):
    """Return (data_offset, shape, fmt) for a little-endian <f8/<i8 .npy member."""
    if blob[:6] != b"\x93NUMPY":
        raise ValueError("not an npy payload")
    major = blob[6]
    hlen = struct.unpack("<H", blob[8:10])[0] if major == 1 else struct.unpack("<I", blob[8:12])[0]
    hoff = 10 if major == 1 else 12
    header = eval(blob[hoff:hoff + hlen].decode())  # dict literal
    if header["fortran_order"]:
        raise ValueError("Fortran-ordered npy arrays are not supported")
    return hoff + hlen, header["shape"], {"<f8": "d", "<i8": "q"}[header["descr"]]


def npy_row(blob: bytes, row: int) -> list[float]:
    """Slice one row of a 2-D <f8 member without materializing the matrix."""
    offset, shape, fmt = npy_layout(blob)
    ncols = shape[1]
    start = offset + row * ncols * 8
    return list(struct.unpack(f"<{ncols}{fmt}", blob[start:start + ncols * 8]))


def rederive_w187_ratio(failures: list[str]) -> None:
    """Fold the W186 mt=102 library rows against the control spec's flux and
    the artifact's own capture factors, then compare the predicted shielded/
    unshielded production ratio to the recorded run ratio."""
    if not (NPZ.exists() and ARTIFACT.exists() and WORK.exists()):
        failures.append("missing inputs for the W187 re-derivation")
        return
    spec = json.loads((WORK / "w186_plain.json").read_text())
    flux = spec["spectrum"]["flux_per_group"]
    art = json.loads(ARTIFACT.read_text())
    w = art["nuclides"]["W186"]
    sig0 = art["sigma0_b"]
    tgrid = art["temperatures_K"]
    # sigma0 = 0.1 is the last (deepest) grid entry; temperature 293.6 is first.
    si, ti = sig0.index(0.1), tgrid.index(293.6)
    # The applied scale is the full-group Bondarenko factor when the table
    # carries it, else the flat lethargy blend of the segment factor.
    scales = {}
    for g in w["groups"]:
        gf = g.get("group_factors", {}).get("capture")
        if gf is not None:
            scales[g["group"]] = gf[si][ti]
        else:
            f = g["factors"]["capture"][si][ti]
            scales[g["group"]] = (1.0 - g["overlap_fraction"]) + g["overlap_fraction"] * f
    index = json.loads(INDEX.read_text())
    w186_target = next(
        i for i, t in enumerate(index["targets"]) if t["za"] == 74186 and t["liso"] == 0
    )
    with zipfile.ZipFile(NPZ) as z:
        rows_blob = z.read("rows.npy")
        sig_blob = z.read("sig.npy")
    rows_offset, rows_shape, rows_fmt = npy_layout(rows_blob)
    ncols = rows_shape[1]
    numerator = denominator = 0.0
    for i in range(rows_shape[0]):
        start = rows_offset + i * ncols * 8
        target, mt, zap, _, _ = struct.unpack(
            f"<{ncols}{rows_fmt}", rows_blob[start:start + ncols * 8]
        )
        if target != w186_target or mt != 102 or zap != 74187:
            continue
        sigma = npy_row(sig_blob, i)
        for g, phi in enumerate(flux):
            if phi == 0.0:
                continue
            s = scales.get(g, 1.0)
            numerator += sigma[g] * phi * s
            denominator += sigma[g] * phi
    if denominator == 0.0:
        failures.append("W186 mt=102 production row folded to zero")
        return
    expected = numerator / denominator
    recorded = json.loads(EVIDENCE.read_text())["details"]["w187_ratio_sigma0_0p1"]
    # The activity ratio approximates the production ratio: W-187 (T1/2 ~ 24 h)
    # barely decays inside the 1 h step and the chain is a single capture.
    if abs(expected - recorded) / expected > 0.05:
        failures.append(
            f"W187 activity ratio {recorded:.4f} vs folded production ratio {expected:.4f}"
        )


def rederive_composition_sigma0(failures: list[str]) -> None:
    """Recompute sigma0_eff for the W186+Fe mix from the spec composition:
    atoms from wt% via A; sigma_p from the table for covered nuclides and the
    analytic channel-radius estimate for the rest."""
    record = json.loads(EVIDENCE.read_text())
    reported = record["details"].get("composition_sigma0_eff_b", {})
    art = json.loads(ARTIFACT.read_text())
    bounds = art["group_structure"]["boundaries_eV"]
    NA = 6.02214076e23
    # wt% -> atoms/g
    comp = {"W186": 10.0, "Fe": 90.0}
    # natural iron isotopes (wt fractions of the element)
    fe_iso = {"Fe54": 0.05845, "Fe56": 0.91754, "Fe57": 0.02119, "Fe58": 0.00282}
    mass = {"W186": 186.0, "Fe54": 54.0, "Fe56": 56.0, "Fe57": 57.0, "Fe58": 58.0}
    atoms = {"W186": comp["W186"] / 100.0 * NA / mass["W186"]}
    for iso, frac in fe_iso.items():
        atoms[iso] = comp["Fe"] / 100.0 * frac * NA / mass[iso]

    def sigma_p(name: str) -> float:
        if name in art["nuclides"]:
            num = den = 0.0
            for g in art["nuclides"][name]["groups"]:
                num += g["sigma_p_b"] * g["overlap_fraction"]
                den += g["overlap_fraction"]
            return num / den
        a = mass[name]
        r = 0.123 * a ** (1.0 / 3.0) + 0.08
        lab = (a + 1.0) / a
        return 4.0 * math.pi * r * r * lab * lab * 0.01

    for target in ("W186", "Fe56"):
        ni = atoms[target]
        expected = sum(
            n * sigma_p(name) for name, n in atoms.items() if name != target
        ) / ni
        got = reported.get(target)
        if got is None or abs(got - expected) / expected > 0.02:
            failures.append(
                f"sigma0_eff {target}: reported {got} vs recomputed {expected:.4f}"
            )


def check_evidence(evidence: dict, failures: list[str]) -> None:
    if evidence.get("schema") != "actinv-p19-g2-runtime-1":
        failures.append("evidence schema is not actinv-p19-g2-runtime-1")
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
    if evidence.get("pass") != (set(checks) == EXPECTED_CHECKS and all(checks.values())):
        failures.append("recorded pass flag inconsistent with the check map")
    ratio = evidence.get("details", {}).get("w187_ratio_sigma0_0p1")
    # Under the full-group fold the deep-dilution ratio runs deeper than the
    # legacy flat blend; the band asserts a real-but-bounded suppression.
    if not isinstance(ratio, (int, float)) or not (0.2 < ratio < 0.9):
        failures.append(f"w187 ratio {ratio!r} outside the physical band (0.2, 0.9)")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = sha256(PROTOCOL)
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    head = git("rev-parse", "HEAD")
    ancestor = (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
            cwd=ROOT,
            check=False,
        ).returncode
        == 0
    )
    if not ancestor:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of {head}")
    protocol_diff = git(
        "diff", f"{OPENING_COMMIT}..HEAD", "--", str(PROTOCOL.relative_to(ROOT))
    )
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
    for gate in ("g0", "g1"):
        path = ROOT / f"results/{gate}_p19_check.json"
        if not path.exists():
            failures.append(f"missing {gate} check record")
            continue
        if json.loads(path.read_text(encoding="utf-8")).get("pass") is not True:
            failures.append(f"{gate} check did not pass")
    if not EVIDENCE.exists():
        failures.append("runtime battery evidence is missing")
    else:
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        check_evidence(evidence, failures)
        rederive_w187_ratio(failures)
        rederive_composition_sigma0(failures)
    return {
        "schema": "actinv-p19-g2-check-1",
        "protocol_sha256": observed_protocol,
        "head_commit": head,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    if not EVIDENCE.exists():
        raise SystemExit("self-test requires the g2 evidence")
    original_bytes = EVIDENCE.read_bytes()
    original = json.loads(original_bytes)
    mutations = {
        "pass_flag": lambda e: e.__setitem__("pass", False),
        "check": lambda e: e["checks"].__setitem__("fixed_dilution_changes_rate", False),
        "ratio": lambda e: e["details"].__setitem__("w187_ratio_sigma0_0p1", 1.0),
        "sigma0": lambda e: e["details"]["composition_sigma0_eff_b"].__setitem__("W186", 1e9),
        "schema": lambda e: e.__setitem__("schema", "bogus"),
    }
    rejected = []
    try:
        for name, mutate in mutations.items():
            candidate = copy.deepcopy(original)
            mutate(candidate)
            EVIDENCE.write_text(json.dumps(candidate))
            try:
                result = run_checks()
                if result["pass"]:
                    rejected.append(name)
            finally:
                EVIDENCE.write_bytes(original_bytes)
    finally:
        EVIDENCE.write_bytes(original_bytes)
    if rejected:
        raise SystemExit(f"self-test mutations not rejected: {rejected}")
    print(f"self-test: all {len(mutations)} mutations rejected")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    if not result["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
