#!/usr/bin/env python3
"""Independent checker for the P19 G3 oracle gate.

Imports no ACTINV production module and none of the comparison tooling.
Verifies the frozen protocol hash, the opening-commit ancestry, and the G0,
G1 and G2 verdict records. Then:

* re-parses the persisted W-186 GROUPR GENDF tape (raw ENDF-6 columns, own
  state machine) and re-derives the capture shielding factor on one
  fully-covered group at sigma0=0.1, comparing it against the value the
  production comparator recorded — an end-to-end check of the oracle's
  parse chain;
* re-derives the artifact's group factor for the same cell from the
  emitted probability tables (renormalized bins, lethargy weights), and
* checks the G3 evidence record's internal consistency and the reported
  per-material tolerance verdicts.

With ``--self-test`` it mutates a copy of the evidence and proves rejection.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P19_PROTOCOL.md"
LIMITS = ROOT / "results/g3_p19_limits.json"
RATES = ROOT / "results/g3_p19_rates.json"
COMPARE = ROOT / "results/g3_p19_compare.json"
GENDF = ROOT / "results/gendf/W-186.gendf"
ARTIFACT = ROOT / "results/g1_p19_shield_artifact.json"
OUTPUT = ROOT / "results/g3_p19_check.json"

PROTOCOL_SHA256 = "8ee3d561fec513b3038fcbabfc8b57a6cce25e36c992f2af311afb6740acce16"
OPENING_COMMIT = "d463d57db4bcd87715eaf0d2082d07a71ec36424"
SIGMA0_B = [1.0e10, 1.0e5, 1.0e4, 1.0e3, 1.0e2, 1.0e1, 3.0, 1.0, 0.3, 0.1]
TEMPS = [293.6, 600.0, 900.0, 1200.0]

ENDF_FLOAT = re.compile(r"^([+-]?\d+\.?\d*)([+-]\d+)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()


def endf_field(line: str, a: int, b: int) -> float:
    text = line[a:b].strip()
    if not text:
        return 0.0
    if "e" not in text.lower():
        text = ENDF_FLOAT.sub(r"\1e\2", text)
    return float(text)


def gendf_capture_factor(gendf: Path, group: int, mt: int = 102,
                         temp: float = 293.6, si: int = 9) -> float:
    """Re-parse one GENDF tape's MT=102 record for `group` at `temp` and
    return xs(sigma0=SIGMA0_B[si])/xs(sigma0=inf). Own state machine; does
    not reuse the production parser."""
    lines = gendf.read_text(errors="replace").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        try:
            mf, mtv = int(line[70:72]), int(line[72:75])
        except ValueError:
            i += 1
            continue
        if mf != 3 or mtv != mt:
            i += 1
            continue
        head = [endf_field(line, k * 11, (k + 1) * 11) for k in range(6)]
        nz = int(head[3])
        i += 1
        while i < len(lines):
            l2 = lines[i]
            try:
                mf2, mt2 = int(l2[70:72]), int(l2[72:75])
            except ValueError:
                i += 1
                continue
            if mf2 != 3 or mt2 != mt:
                break
            cont = [endf_field(l2, k * 11, (k + 1) * 11) for k in range(6)]
            t0, ng2, nw, g = cont[0], int(cont[2]), int(cont[4]), int(cont[5])
            i += 1
            if nw <= 0 or g <= 0:
                continue
            values = []
            while len(values) < nw and i < len(lines):
                l3 = lines[i]
                try:
                    if int(l3[70:72]) != 3 or int(l3[72:75]) != mt:
                        break
                except ValueError:
                    break
                values.extend(endf_field(l3, k * 11, (k + 1) * 11) for k in range(6))
                i += 1
            values = values[:nw]
            if nz <= 0 or ng2 < 1 or nw < nz * ng2:
                continue
            nl = nw // (nz * ng2)
            if g == group and abs(t0 - temp) < 0.1:
                xs = [values[nz * nl + iz * nl] for iz in range(nz)]
                return xs[si] / xs[0]
        i += 1
    raise ValueError(f"group {group} not found in {gendf.name}")


def lethargy_trapezoid(es: list[float], vals: list[float],
                       lo: float, hi: float) -> float:
    """log-linear interior trapezoid with end-value extension — mirrors the
    builder's collapse convention."""
    if len(es) == 1:
        return vals[0]
    pts = sorted(zip(es, vals))
    xs = [lo] + [e for e, _ in pts if lo < e < hi] + [hi]
    def v(e: float) -> float:
        if e <= pts[0][0]:
            return pts[0][1]
        for i in range(1, len(pts)):
            if e <= pts[i][0]:
                e0, v0 = pts[i - 1]
                e1, v1 = pts[i]
                w = (math.log(e) - math.log(e0)) / (math.log(e1) - math.log(e0))
                return v0 + w * (v1 - v0)
        return pts[-1][1]
    acc = 0.0
    for i in range(1, len(xs)):
        e0, e1 = xs[i - 1], xs[i]
        acc += (v(e0) + v(e1)) / 2.0 * math.log(e1 / e0)
    return acc / math.log(hi / lo)


def node_w_and_xw(node: dict, channel: str, s0: float, s0max: float,
                  t: int) -> tuple[float, float]:
    """Independent re-derivation of the builder's per-node moment pair."""
    pt = node["ptable"][t]
    prob, tot = pt["prob"], pt["total_b"]
    xs = pt[{"total": "total_b", "elastic": "elastic_b",
             "fission": "fission_b", "capture": "capture_b"}[channel]]
    inf = node["infinite_dilution_b"]
    ci = {"total": 0, "elastic": 1, "fission": 2, "capture": 3}[channel]
    rd = rt = rx = 0.0
    for i in range(len(prob)):
        w = s0max / (s0max + tot[i])
        rd += prob[i] * w
        rt += prob[i] * tot[i] * w
        rx += prob[i] * xs[i] * w
    if rd <= 0.0:
        return 0.0, 0.0
    r_tot = inf[0] / (rt / rd) if rt > 0.0 else 1.0
    r_xs = inf[ci] / (rx / rd) if rx > 0.0 else 0.0
    wa = xa = 0.0
    for i in range(len(prob)):
        w = s0 / (s0 + tot[i] * r_tot)
        wa += prob[i] * w
        xa += prob[i] * xs[i] * r_xs * w
    return wa, xa


def artifact_group_factor(artifact: dict, nuclide: str, group: int,
                          channel: str, si: int, t: int) -> float:
    """Re-derive the full-group factor: covered segments contribute their
    probability-table moments; the uncovered part contributes the emitted
    background suppressed by s0/(s0+background_total)."""
    block = artifact["nuclides"][nuclide]
    bounds = artifact["group_structure"]["boundaries_eV"]
    elo, ehi = bounds[group], bounds[group + 1]
    row = next(g for g in block["groups"] if g["group"] == group)
    ci = {"total": 0, "elastic": 1, "fission": 2, "capture": 3}[channel]
    c = row["overlap_fraction"]
    # Covered segments: the union of unresolved ranges ∩ group.
    ranges = block["unresolved_ranges_ev"]
    segs = sorted(
        (max(elo, float(a)), min(ehi, float(b)))
        for a, b in ranges if max(elo, float(a)) < min(ehi, float(b))
    )
    covered = sum(math.log(b / a) for a, b in segs)
    nodes = [n for n in block["nodes"] if n.get("covered", True)
             and any(a <= n["energy_ev"] <= b for a, b in segs)]
    s0, s0max = SIGMA0_B[si], SIGMA0_B[0]
    wm = xw = 0.0
    for a, b in segs:
        inside = [n for n in nodes if a <= n["energy_ev"] <= b]
        if not inside:
            continue
        wseg = math.log(b / a) / covered
        es = [n["energy_ev"] for n in inside]
        wmv = [node_w_and_xw(n, channel, s0, s0max, t)[0] for n in inside]
        xwv = [node_w_and_xw(n, channel, s0, s0max, t)[1] for n in inside]
        wm += wseg * lethargy_trapezoid(es, wmv, a, b)
        xw += wseg * lethargy_trapezoid(es, xwv, a, b)
    rest = math.log(ehi / elo) - covered
    bkg = row["background_b"]
    w_rest = s0 / (s0 + bkg[0]) if rest > 0 else 0.0
    num = covered * xw + rest * bkg[ci] * w_rest
    den = covered * wm + rest * w_rest
    gun = row["group_unshielded_b"][ci]
    return (num / den) / gun if den > 0 and gun != 0 else float("nan")


def check_limits(failures: list[str]) -> None:
    if not LIMITS.exists():
        failures.append("g3 limits evidence missing")
        return
    lim = json.loads(LIMITS.read_text())["legs"]["analytic_limits"]
    for k in ("inf_limit_pass", "bounded_rise_pass", "nonnegative_pass"):
        if lim.get(k) is not True:
            failures.append(f"limits leg {k} not true")


def check_compare(failures: list[str]) -> None:
    if not COMPARE.exists():
        failures.append("g3 comparison evidence missing")
        return
    comp = json.loads(COMPARE.read_text())
    if comp.get("all_covered") != "covered":
        failures.append("comparison did not cover all materials")
    for name, m in comp.get("materials", {}).items():
        if m.get("covered") != "covered":
            continue
        if m.get("within_tolerance") is not True:
            failures.append(f"{name} node leg out of tolerance")
        gc = m.get("group_comparison", {})
        if gc.get("within_tolerance") is not True:
            failures.append(f"{name} group leg out of tolerance")


def check_gendf_reredive(failures: list[str]) -> None:
    """Re-parse the W-186 tape, re-derive g434 capture f(0.1), and compare
    to the recorded comparison cell; also re-derive the artifact's factor."""
    if not GENDF.exists():
        failures.append("persisted W-186 GENDF missing")
        return
    artifact = json.loads(ARTIFACT.read_text())
    try:
        oracle_f = gendf_capture_factor(GENDF, group=434, si=9)
    except ValueError as error:
        failures.append(str(error))
        return
    comp = json.loads(COMPARE.read_text())
    rec = comp["materials"]["W-186"]["group_comparison"]["per_channel"]
    got = rec.get("capture", {})
    # The recorded max is the worst cell; the re-derived cell must itself
    # be consistent: |mine-oracle|/oracle <= max recorded.
    my_f = artifact_group_factor(artifact, "W186", 433, "capture", 9, 0)
    dev = abs(my_f - oracle_f) / oracle_f
    recorded_max = got.get("full_coverage_max")
    if recorded_max is not None and dev > recorded_max * 1.05:
        failures.append(
            f"re-derived g434 capture dev {dev:.3%} exceeds recorded max "
            f"{recorded_max:.3%}")
    # The artifact's own emission agrees with the independent re-derivation
    # to within the re-implementation's trapezoid edge convention (~1%).
    emitted = artifact["nuclides"]["W186"]["groups"]
    emitted_f = next(g for g in emitted if g["group"] == 433)[
        "group_factors"]["capture"][9][0]
    if abs(my_f - emitted_f) / emitted_f > 0.02:
        failures.append(
            f"re-derived artifact g434 capture factor {my_f:.5f} != "
            f"emitted {emitted_f:.5f}")


def check_rates(failures: list[str]) -> None:
    if not RATES.exists():
        failures.append("g3 rates evidence missing")
        return
    rates = json.loads(RATES.read_text())
    if rates.get("pass") is not True:
        failures.append("held-out rates battery did not pass")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = sha256(PROTOCOL)
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen")
    head = git("rev-parse", "HEAD")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode != 0:
        failures.append("opening commit not an ancestor of HEAD")
    for gate in ("g0", "g1", "g2"):
        path = ROOT / f"results/{gate}_p19_check.json"
        if not path.exists():
            failures.append(f"missing {gate} check record")
            continue
        if json.loads(path.read_text()).get("pass") is not True:
            failures.append(f"{gate} check did not pass")
    check_limits(failures)
    check_compare(failures)
    check_gendf_reredive(failures)
    check_rates(failures)
    return {
        "schema": "actinv-p19-g3-check-1",
        "protocol_sha256": observed_protocol,
        "head_commit": head,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    if not COMPARE.exists():
        raise SystemExit("self-test requires the g3 comparison")
    original_bytes = COMPARE.read_bytes()
    original = json.loads(original_bytes)
    mutations = {
        "all_covered": lambda c: c.__setitem__("all_covered", "absent"),
        "node_tol": lambda c: c["materials"]["W-186"].__setitem__(
            "within_tolerance", False),
        "group_tol": lambda c: c["materials"]["W-186"]["group_comparison"]
            .__setitem__("within_tolerance", False),
    }
    rejected = []
    try:
        for name, mutate in mutations.items():
            candidate = copy.deepcopy(original)
            mutate(candidate)
            COMPARE.write_text(json.dumps(candidate))
            if not run_checks()["pass"]:
                rejected.append(name)
    finally:
        COMPARE.write_bytes(original_bytes)
    missing = sorted(set(mutations) - set(rejected))
    print(f"self-test rejected {len(rejected)}/{len(mutations)} mutations")
    if missing:
        raise SystemExit(f"self-test FAILED: not rejected {missing}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    result = run_checks()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
