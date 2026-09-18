#!/usr/bin/env python3
"""Independent checker for the P18b-G3 conservative-builder gate.

Imports no production or control module. Verifies the frozen protocol hash and
the committed G2 evidence, then exercises the released build path through
generated ENDF fixtures:

* a within-envelope state sum must be emitted scaled by the common factor T/S
  with the reconciled sum at or below the runtime total and the loss row
  unchanged — verified against an independent lethargy collapse of the source;
* a conformant vector must be emitted byte-for-byte;
* an outside-envelope sum must fail construction with target, MT, ZAP, group,
  total, sum, relative excess and source hash in the diagnostic;
* the strict option must reject every emitted sum above the total;
* a zero runtime total uses the frozen 0.001 barn absolute bound;
* MF=9 production rows follow the same rule;
* an MF=10/MT=18 IZAP=-1 sentinel supplies the permitted comparator when MF=3
  is absent; under P38, production-only sections use a ledgered self-comparator
  (see protocols/ACTINV-P18b_AMENDMENT_P38_RUNTIME.md).

When the TENDL corpus is present it additionally builds the real fixtures
recorded in the report: the At198 outside-envelope failure, the Ag112
reconciled build (whose emitted NPZ rows are re-checked for closure
independently), and the p-Ac223 sentinel path. With ``--self-test`` it mutates
a copy of the committed report and proves rejection.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
G2_REPORT = ROOT / "results/g2_p18b_corpus_classification.json"
G2_CHECK = ROOT / "results/g2_p18b_check.json"
OUTPUT = ROOT / "results/g3_p18b_check.json"
DATA_ROOT = Path(
    os.environ.get("ACTINV_P18B_CORPUS", "/home/connoravila/nuclear-data/tendl-2025/files")
)

PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
G2_REPORT_SHA256 = "cfeb4fa2a1b3a5fb465f61299285d0633c43948fd5142e22ba1649dcaa8b38d6"
G2_CHECK_SHA256 = "fb05923e1ab9535ffef61da4029cd07ddbfc0fecd3211f2ae98f8d2fffe9735d"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def actinv_binary() -> Path:
    configured = os.environ.get("ACTINV_BIN")
    candidates = ([Path(configured)] if configured else []) + [
        ROOT / "target/release/actinv",
        ROOT / "target/debug/actinv",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise AssertionError("no actinv binary found; build the workspace first")


def record(values: list[str], mat: int, mf: int, mt: int, sequence: int) -> str:
    data = "".join(f"{value:>11}" for value in values)
    return f"{data}{mat:>4}{mf:>2}{mt:>3}{sequence:>5}"


def send(mat: int, mf: int) -> str:
    return record(["", "", "", "", "", ""], mat, mf, 0, 99_999)


def header(za: int, mat: int) -> list[str]:
    return [
        record([str(za), "55.45", "0", "0", "0", "0"], mat, 1, 451, 1),
        record(["0", "0", "0", "0", "0", "0"], mat, 1, 451, 2),
        record(["1", "4", "1", "0", "10", "2025"], mat, 1, 451, 3),
        record(["0", "0", "0", "0", "0", "0"], mat, 1, 451, 4),
        send(mat, 1),
    ]


def tab1(mat: int, mf: int, mt: int, sequence: int, head: list[str], value: str) -> list[str]:
    return [
        record([head[0], head[1], head[2], head[3], "1", "2"], mat, mf, mt, sequence),
        record(["2", "2", "", "", "", ""], mat, mf, mt, sequence + 1),
        record(["1", value, "4", value, "", ""], mat, mf, mt, sequence + 2),
    ]


def mf3_section(mat: int, mt: int, za: int, value: str) -> list[str]:
    lines = [record([str(za), "55.45", "0", "0", "0", "0"], mat, 3, mt, 1)]
    lines.extend(tab1(mat, 3, mt, 2, ["0", "0", "0", "0"], value))
    lines.append(send(mat, 3))
    return lines


def state_section(
    mat: int, mf: int, mt: int, za: int, products: list[tuple[int, int, str]]
) -> list[str]:
    lines = [
        record([str(za), "55.45", "0", "0", str(len(products)), "0"], mat, mf, mt, 1)
    ]
    sequence = 2
    for zap, lfs, value in products:
        lines.extend(
            tab1(mat, mf, mt, sequence, ["1000000", "1000000", str(zap), str(lfs)], value)
        )
        sequence += 3
    lines.append(send(mat, mf))
    return lines


def fixture_tape(sections: list[list[str]], mat: int = 2631, za: int = 26056) -> str:
    lines = header(za, mat)
    for section in sections:
        lines.extend(section)
    return "\n".join(lines) + "\n"


def read_npy(blob: bytes):
    assert blob[:6] == b"\x93NUMPY", "not an npy payload"
    if blob[6] == 1:
        (length,) = struct.unpack("<H", blob[8:10])
        offset = 10 + length
        header_text = blob[10:offset].decode()
    else:
        (length,) = struct.unpack("<I", blob[8:12])
        offset = 12 + length
        header_text = blob[12:offset].decode()
    descr = header_text.split("'descr':")[1].split("'")[1]
    shape_text = header_text.split("'shape':")[1].split("(")[1].split(")")[0]
    shape = tuple(int(part) for part in shape_text.split(",") if part.strip())
    count = 1
    for extent in shape:
        count *= extent
    if descr == "<f8":
        values = list(struct.unpack(f"<{count}d", blob[offset : offset + 8 * count]))
    elif descr == "<i8":
        values = list(struct.unpack(f"<{count}q", blob[offset : offset + 8 * count]))
    else:
        raise AssertionError(f"unsupported npy dtype {descr}")
    return shape, values


def load_npz(path: Path):
    with zipfile.ZipFile(path) as archive:
        row_shape, rows = read_npy(archive.read("rows.npy"))
        sig_shape, sig = read_npy(archive.read("sig.npy"))
        (_,), bounds = read_npy(archive.read("bounds.npy"))
    n_rows, _ = row_shape
    _, n_groups = sig_shape
    row_meta = [rows[i * 5 : (i + 1) * 5] for i in range(n_rows)]
    sigma = [sig[i * n_groups : (i + 1) * n_groups] for i in range(n_rows)]
    return row_meta, sigma, bounds


def lethargy_coverage(bounds: list[float], low: float = 1.0, high: float = 4.0) -> list[float]:
    """Independent flat-lethargy coverage of the fixture tables' [1,4] eV
    domain inside each group — the collapse of a constant table equals its
    value times this fraction."""
    coverage = []
    for index in range(len(bounds) - 1):
        group_low, group_high = bounds[index], bounds[index + 1]
        width = math.log(group_high / group_low)
        overlap = math.log(min(group_high, high)) - math.log(max(group_low, low))
        coverage.append(max(0.0, overlap) / width)
    return coverage


def build(
    binary: Path,
    input_dir: Path,
    output: Path,
    projectile: str = "neutron",
    groups: str | None = None,
    temperature: str | None = None,
    extra: list[str] | None = None,
):
    if groups is None:
        groups = "fispact-709" if projectile == "neutron" else "fispact-162"
    command = [
        str(binary),
        "build-library",
        str(input_dir),
        str(output),
        "--format",
        "tendl",
        "--projectile",
        projectile,
        "--groups",
        groups,
    ]
    if temperature is not None:
        command.extend(["--temperature-K", temperature])
    command.extend(extra or [])
    return subprocess.run(command, capture_output=True, text=True)


def vector_closure_failures(row_meta, sigma, n_groups: int) -> list[str]:
    """Every emitted production vector must sum at or below its reaction's
    loss row (zap=-1, lmf=0) within binary rounding.

    ZAP-identified rows (lmf in {9,10,-1,-2}) are summed per (mt, zap) — the
    frozen per-vector invariant. Leakage-routed rows (lmf=-3) lose their
    original ZAP, but each was a member of a reconciled vector whose scaling
    keeps every member at or below the total, so each is checked
    individually."""
    failures = []
    loss_rows = {}
    for index, (target, mt, zap, lfs, lmf) in enumerate(row_meta):
        if lmf == 0 and zap == -1:
            loss_rows[(target, mt)] = index
    vectors: dict[tuple[int, int, int], list[int]] = {}
    leakage_rows = []
    for index, (target, mt, zap, lfs, lmf) in enumerate(row_meta):
        if lmf == -3:
            leakage_rows.append((index, target, mt))
        elif lmf in (9, 10, -1, -2):
            vectors.setdefault((target, mt, zap), []).append(index)
    checks = [
        (f"mt{mt} zap{zap}", members, loss_rows.get((target, mt)))
        for (target, mt, zap), members in vectors.items()
    ]
    checks += [
        (f"mt{mt} leakage row {index}", [index], loss_rows.get((target, mt)))
        for index, target, mt in leakage_rows
    ]
    for label, members, total_index in checks:
        if total_index is None:
            failures.append(f"{label}: no loss row")
            continue
        for group in range(n_groups):
            emitted = sum(sigma[member][group] for member in members)
            total = sigma[total_index][group]
            if emitted > total * (1.0 + 1e-12) + 1e-30:
                failures.append(
                    f"{label} group {group}: sum {emitted:.17e} > total {total:.17e}"
                )
    return failures


def check_scaled_vector(sigma, loss_index, product_indices, source_values, bounds, total):
    """Verify emitted product rows equal the independent lethargy collapse of
    each source table scaled by T/S, group by group; the loss row must equal
    the unscaled collapsed total; the emitted sum never exceeds it. The
    1e-12 relative bound absorbs integrator rounding differences while
    remaining nine orders of magnitude inside the frozen 0.001 envelope."""
    coverage = lethargy_coverage(bounds)
    source_sum = sum(source_values)
    failures = []
    for group, cov in enumerate(coverage):
        if cov == 0.0:
            continue
        runtime_total = total * cov
        if abs(sigma[loss_index][group] - runtime_total) > 1e-12 * runtime_total:
            failures.append(
                f"group {group}: loss row {sigma[loss_index][group]:.17e} != collapsed total {runtime_total:.17e}"
            )
            continue
        raw = [value * cov for value in source_values]
        raw_sum = source_sum * cov
        scale = min(1.0, runtime_total / raw_sum)
        emitted = [sigma[index][group] for index in product_indices]
        expected = [value * scale for value in raw]
        for got, want in zip(emitted, expected):
            if abs(got - want) > 1e-12 * max(1.0, abs(want)):
                failures.append(
                    f"group {group}: emitted {got:.17e} != expected {want:.17e}"
                )
        if sum(emitted) > runtime_total * (1.0 + 1e-12):
            failures.append(
                f"group {group}: emitted sum {sum(emitted):.17e} exceeds total {runtime_total:.17e}"
            )
    return failures


def run_generated_legs(binary: Path) -> dict:
    """Fixture builds through the released path; each leg records pass/fail."""
    legs = {}
    work = Path(tempfile.mkdtemp(prefix="actinv-g3-"))
    try:
        # 1. Within-envelope MF=10 vector reconciles by T/S.
        case = work / "envelope"
        source = case / "in"
        source.mkdir(parents=True)
        (source / "n-fixture.tendl").write_text(
            fixture_tape(
                [
                    mf3_section(2631, 102, 26056, "2"),
                    state_section(
                        2631, 10, 102, 26056, [(26057, 0, "1.2"), (26057, 1, "0.801")]
                    ),
                ]
            )
        )
        out = case / "out.npz"
        result = build(binary, source, out, temperature="0")
        ok = result.returncode == 0
        detail = {}
        if ok:
            rows, sigma, bounds = load_npz(out)
            loss = next(i for i, r in enumerate(rows) if r[4] == 0 and r[2] == -1)
            # LFS>0 products without an auditable isomer route to lmf=-3.
            products = [i for i, r in enumerate(rows) if r[4] in (10, -3)]
            failures = check_scaled_vector(
                sigma, loss, products, [1.2, 0.801], bounds, 2.0
            )
            index = json.loads(Path(str(out).replace(".npz", "_index.json")).read_text())
            ledger = "\n".join(index["targets"][0]["ledger"])
            ok = (
                not failures
                and len(products) == 2
                and "scaled by the common factor" in ledger
                and "audit" in ledger
            )
            detail = {"closure_failures": failures[:5]}
        legs["envelope_reconcile"] = {"pass": ok, **detail}

        # 2. Conformant vectors are emitted unchanged.
        case = work / "conformant"
        source = case / "in"
        source.mkdir(parents=True)
        (source / "n-fixture.tendl").write_text(
            fixture_tape(
                [
                    mf3_section(2631, 102, 26056, "2"),
                    state_section(
                        2631, 10, 102, 26056, [(26057, 0, "0.5"), (26057, 1, "0.25")]
                    ),
                ]
            )
        )
        out = case / "out.npz"
        result = build(binary, source, out, temperature="0")
        ok = result.returncode == 0
        if ok:
            rows, sigma, bounds = load_npz(out)
            coverage = lethargy_coverage(bounds)
            products = sorted(
                (sigma[i] for i, r in enumerate(rows) if r[4] in (10, -3)),
                key=lambda column: max(column),
            )
            ok = len(products) == 2 and all(
                abs(sigma_row[group] - value * coverage[group])
                <= 1e-12 * max(1.0, value * coverage[group])
                for sigma_row, value in zip(products, [0.25, 0.5])
                for group in range(len(coverage))
            )
        legs["conformant_byte_identical"] = {"pass": ok}

        # 3. Outside-envelope fails closed with the full diagnostic context.
        case = work / "outside"
        source = case / "in"
        source.mkdir(parents=True)
        (source / "n-fixture.tendl").write_text(
            fixture_tape(
                [
                    mf3_section(2631, 102, 26056, "2"),
                    state_section(
                        2631, 10, 102, 26056, [(26057, 0, "1.2"), (26057, 1, "0.81")]
                    ),
                ]
            )
        )
        result = build(binary, source, case / "out.npz", temperature="0")
        message = result.stdout + result.stderr
        ok = result.returncode != 0 and all(
            required in message
            for required in [
                "MT102/MF=10 ZAP=26057 group",
                "runtime total",
                "emitted state sum",
                "relative excess",
                "source_sha256=",
                "MAT=2631",
                "ZA=26056",
                "fails closed",
            ]
        )
        legs["outside_envelope_fail_closed"] = {"pass": ok}

        # 4. The strict option rejects every excess, even inside the envelope.
        case = work / "strict"
        source = case / "in"
        source.mkdir(parents=True)
        (source / "n-fixture.tendl").write_text(
            fixture_tape(
                [
                    mf3_section(2631, 102, 26056, "2"),
                    state_section(2631, 10, 102, 26056, [(26057, 0, "2.001")]),
                ]
            )
        )
        result = build(binary, source, case / "out.npz", temperature="0", extra=["--strict-states", "true"])
        ok = result.returncode != 0 and "strict state-conservation" in (
            result.stdout + result.stderr
        )
        legs["strict_rejects_excess"] = {"pass": ok}

        # 5. Zero total: the 0.001 barn absolute bound applies.
        case = work / "zero"
        source = case / "in"
        source.mkdir(parents=True)
        (source / "n-fixture.tendl").write_text(
            fixture_tape(
                [
                    mf3_section(2631, 102, 26056, "0"),
                    state_section(
                        2631, 10, 102, 26056, [(26057, 0, "0.0006"), (26057, 1, "0.0003")]
                    ),
                ]
            )
        )
        out = case / "out.npz"
        result = build(binary, source, out, temperature="0")
        ok = result.returncode == 0
        if ok:
            rows, sigma, _ = load_npz(out)
            ok = all(
                value == 0.0
                for i, r in enumerate(rows)
                if r[4] in (10, -3)
                for value in sigma[i]
            )
        legs["zero_total_absolute_bound"] = {"pass": ok}

        # 6. MF=9 production rows follow the same rule.
        case = work / "mf9"
        source = case / "in"
        source.mkdir(parents=True)
        (source / "n-fixture.tendl").write_text(
            fixture_tape(
                [
                    mf3_section(2631, 102, 26056, "2"),
                    state_section(
                        2631, 9, 102, 26056, [(26057, 0, "0.6"), (26057, 1, "0.4005")]
                    ),
                ]
            )
        )
        out = case / "out.npz"
        result = build(binary, source, out, temperature="0")
        ok = result.returncode == 0
        if ok:
            rows, sigma, bounds = load_npz(out)
            loss = next(i for i, r in enumerate(rows) if r[4] == 0 and r[2] == -1)
            products = [i for i, r in enumerate(rows) if r[4] in (9, -3)]
            failures = check_scaled_vector(
                sigma, loss, products, [1.2, 0.801], bounds, 2.0
            )
            ok = not failures and len(products) == 2
        legs["mf9_reconcile"] = {"pass": ok}

        # 7. Sentinel comparator: MF=10/MT=18 IZAP=-1 with no MF=3 builds.
        case = work / "sentinel"
        source = case / "in"
        source.mkdir(parents=True)
        (source / "n-fixture.tendl").write_text(
            fixture_tape([state_section(2631, 10, 18, 26056, [(-1, 0, "1.5")])])
        )
        out = case / "out.npz"
        result = build(binary, source, out, temperature="0")
        ok = result.returncode == 0
        if ok:
            rows, sigma, bounds = load_npz(out)
            coverage = lethargy_coverage(bounds)
            ok = all(
                abs(sigma[i][group] - 1.5 * coverage[group])
                <= 1e-12 * max(1.0, 1.5 * coverage[group])
                for i in range(len(rows))
                for group in range(len(coverage))
            )
            index = json.loads(Path(str(out).replace(".npz", "_index.json")).read_text())
            ledger = "\n".join(index["targets"][0]["ledger"])
            ok = ok and "sentinel supplies the permitted runtime comparator" in ledger
        legs["fission_sentinel_comparator"] = {"pass": ok}

        # 8. P38 supersedes the historical missing-total rejection. Require
        # the actual emitted values and the unanchored-comparator caveat.
        case = work / "missing"
        source = case / "in"
        source.mkdir(parents=True)
        (source / "n-fixture.tendl").write_text(
            fixture_tape([state_section(2631, 10, 102, 26056, [(26057, 0, "0.5")])])
        )
        out = case / "out.npz"
        result = build(binary, source, out, temperature="0")
        ok = result.returncode == 0
        if ok:
            rows, sigma, bounds = load_npz(out)
            coverage = lethargy_coverage(bounds)
            losses = [i for i, r in enumerate(rows) if r[1] == 102 and r[2] == -1 and r[4] == 0]
            products = [i for i, r in enumerate(rows) if r[1] == 102 and r[4] in (10, -3)]
            ok = len(losses) == len(products) == 1 and len(rows) == 2
            ok = ok and all(
                math.isfinite(sigma[i][group])
                and abs(sigma[i][group] - 0.5 * fraction) <= 1e-12
                for i in losses + products
                for group, fraction in enumerate(coverage)
            )
            ok = ok and not vector_closure_failures(rows, sigma, len(coverage))
            index = json.loads(out.with_name(out.stem + "_index.json").read_text())
            ledger = "\n".join(index["targets"][0]["ledger"])
            ok = ok and all(text in ledger for text in (
                "missing_total_self_comparator",
                "not anchored to an independent total",
            ))
        legs["missing_total_self_comparator"] = {"pass": ok}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return legs


def run_corpus_legs(binary: Path) -> dict:
    """Real-file legs; skipped where the corpus is absent."""
    neutron = DATA_ROOT / "n-working"
    proton = DATA_ROOT / "p"
    if not neutron.is_dir() or not proton.is_dir():
        return {"skipped": True}
    legs = {}
    work = Path(tempfile.mkdtemp(prefix="actinv-g3-corpus-"))
    try:
        # At198 carries a genuine outside-envelope MF10/MT16 defect.
        source = work / "at198"
        source.mkdir()
        shutil.copy(neutron / "n-At198.tendl", source)
        result = build(binary, source, work / "at198.npz")
        message = result.stdout + result.stderr
        legs["at198_fail_closed"] = {
            "pass": result.returncode != 0
            and "MT16/MF=10 ZAP=85197" in message
            and "outside the frozen 0.001" in message
            and "source_sha256=" in message
        }

        # Ag112 reconciles within the envelope; emitted rows must close.
        source = work / "ag112"
        source.mkdir()
        shutil.copy(neutron / "n-Ag112.tendl", source)
        out = work / "ag112.npz"
        result = build(binary, source, out)
        ok = result.returncode == 0
        detail = {}
        if ok:
            rows, sigma, bounds = load_npz(out)
            failures = vector_closure_failures(rows, sigma, len(bounds) - 1)
            index = json.loads(Path(str(out).replace(".npz", "_index.json")).read_text())
            ledger = "\n".join(index["targets"][0]["ledger"])
            ok = not failures and "scaled by the common factor" in ledger
            detail = {"closure_failures": failures[:5], "rows": len(rows)}
        legs["ag112_reconciled_closure"] = {"pass": ok, **detail}

        # p-Ac223 supplies the MT=18 sentinel comparator on a charged corpus.
        source = work / "ac223p"
        source.mkdir()
        shutil.copy(proton / "p-Ac223.tendl", source)
        result = build(binary, source, work / "ac223p.npz", projectile="proton")
        legs["ac223_sentinel"] = {"pass": result.returncode == 0}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return legs


def evaluate(report: dict) -> list[str]:
    failures = []
    if report.get("protocol_sha256") != PROTOCOL_SHA256:
        failures.append("protocol hash")
    if report.get("g2_report_sha256") != G2_REPORT_SHA256:
        failures.append("g2 report hash")
    if report.get("g2_check_sha256") != G2_CHECK_SHA256:
        failures.append("g2 check hash")
    if report.get("g2_audit_complete") is not True:
        failures.append("g2 audit completeness")
    if report.get("g2_check_pass") is not True:
        failures.append("g2 check verdict")
    for name, leg in report.get("generated", {}).items():
        if not leg.get("pass"):
            failures.append(f"generated leg {name}")
    corpus = report.get("corpus", {})
    if not corpus.get("skipped"):
        for name, leg in corpus.items():
            if not leg.get("pass"):
                failures.append(f"corpus leg {name}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()

    if arguments.self_test:
        baseline = {
            "protocol_sha256": PROTOCOL_SHA256,
            "g2_report_sha256": G2_REPORT_SHA256,
            "g2_check_sha256": G2_CHECK_SHA256,
            "g2_audit_complete": True,
            "g2_check_pass": True,
            "generated": {"envelope_reconcile": {"pass": True}},
            "corpus": {"skipped": True},
        }
        if evaluate(baseline):
            print("self-test baseline unexpectedly fails")
            return 1
        rejected = 0
        mutations = (
            lambda r: r.update({"protocol_sha256": "0" * 64}),
            lambda r: r.update({"g2_report_sha256": "0" * 64}),
            lambda r: r["generated"]["envelope_reconcile"].update({"pass": False}),
        )
        for mutation in mutations:
            mutated = copy.deepcopy(baseline)
            mutation(mutated)
            if not evaluate(mutated):
                print("self-test accepted a mutation")
            else:
                rejected += 1
        print(f"self-test rejected {rejected}/3 mutations")
        return 0 if rejected == 3 else 1

    report = {
        "schema": "actinv-p18b-g3-check-1",
        "protocol_sha256": sha256(PROTOCOL),
        "g2_report_sha256": sha256(G2_REPORT),
        "g2_check_sha256": sha256(G2_CHECK),
    }
    g2 = json.loads(G2_REPORT.read_text())
    g2_check = json.loads(G2_CHECK.read_text())
    report["g2_audit_complete"] = g2.get("audit_complete") is True and g2.get("pass") is True
    report["g2_check_pass"] = g2_check.get("pass") is True

    binary = actinv_binary()
    report["binary"] = str(binary)
    report["binary_sha256"] = sha256(binary)
    report["generated"] = run_generated_legs(binary)
    report["corpus"] = run_corpus_legs(binary)
    report["failures"] = evaluate(report)
    report["pass"] = not report["failures"]

    if not arguments.no_write:
        OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"failures": report["failures"], "pass": report["pass"]}, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
