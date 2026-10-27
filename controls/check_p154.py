#!/usr/bin/env python3
"""P154 checker: native damage energy from evaluations (`build-damage --method kinematic`, table format
`actinv-damage-table-2`), exact NRT and arc-dpa run outputs.

G0 (protocol hash), G1 (fmt, clippy, tests) and G7 (CI, baselines) are external and do not enter the verdict.

Independence: this script never reads ACTINV's implementation. Its references are (a) NJOY2016.79 RECONR + HEATR at
0 K with partials MT=444-447 (controls/p154_njoy.py) on the TENDL-2025 patched Fe-56, Cr-52, Ni-58, W-184 files,
collapsed here by its own flat-in-lethargy integrator (specification.md: the kinematic collapse "is the flat-lethargy
weighting of the other builders"), (b) its own Lindhard-Robinson, NRT and arc-dpa functions (self-checked), and (c) its
own transcription of Table 1 of Nordlund et al., Nat. Commun. 9, 1084 (2018).

G2  Defaults unchanged. examples/damage_demo.json and examples/fns_fe_5min.json are byte-identical between reference
    and candidate apart from `ms`; `build-damage` without `--method` is byte-identical on the P23 corpus
    (results/g3_p23_corpus, neutron).
G3  Elastic (Amendment A1). The table's `elastic` row against an exact elastic damage cross section: RECONR's
    pointwise MT=2 sigma(E) (from the PENDF tape) times this script's own exact damage energy per elastic event W(E),
    integrated over the scattering cosine of the TENDL MF=4 MT=2 distribution (Legendre and/or tabulated per LTT,
    laboratory cosine converted by integrating in the laboratory variable when LCT=1, cut where the recoil energy equals
    the displacement energy and at the NRT breakpoint 2 E_d / 0.8 and at every pdf panel edge, adaptive Gauss-Legendre),
    W interpolated between MF=4 energies by linear pdf mixing (INT=2) on energies refined to a 5 percent ratio, then
    W linear in E between those points; collapsed flat in lethargy onto the 709 groups: within 2 percent in every group
    with lower edge >= 1 keV where the reference exceeds 1e-6 of its maximum. Below 1 keV the distribution of the
    differences is reported. NJOY MT=445 against the table, the exact reference against NJOY MT=445, and NJOY's
    documented approximations (edis = 25 eV, 1.1 energy ladder, 64-coefficient hgetco projection; heatr.f90 line
    numbers in the output) are information.
G4  Inelastic and disappearance (Amendments B and C). `inelastic` against NJOY MT=446 with the discrete-level ladder term
    replaced (B2) and `disappearance` against NJOY MT=447 with the capture-photon term replaced (B1), within 5 percent
    in every group where the channel exceeds 1e-3 of NJOY's group total (MT=444) and the group has no uncovered cross
    section (the per-channel `channels` record is informational and not read). A group has an uncovered contribution
    only when its `uncovered_damage_upper_barn_eV` exceeds 0.5 percent (G4_UNCOVERED_FRACTION = one tenth of the G4
    tolerance) of the channel's computed value in that group, so the bound cannot move the comparison by more than a
    tenth of the tolerance; TENDL's ~3e-20 b placeholder cross sections (uncovered_xs_barn > 0 in every W-184 group)
    therefore no longer disqualify a group. The groups excluded by the rule are still reported. A channel with fewer
    than 5 evaluable groups is reported as vacuous and fails.
    B1: reference = 447 + sigma_102 (h_exact - h_tabsq6) on NJOY's PENDF grid. h_tabsq6 emulates heatr.f90 tabsq6
    (4-point rule with weights times dx, linear interpolation whatever LEP, yield, records interpolated linearly in E);
    h_exact reads the same MF=6 MT=102 photon records as the format specifies (LEP honoured, panels split where the
    recoil equals the displacement energy, discrete lines at unit weight). Precondition (Amendment C1): U = collapse of
    sum over the other disappearance MTs (103-120, 600-849 in the PENDF) of sigma T_max, T_max the largest laboratory
    energy of the residual (ejectiles one cluster of mass m_b); in every group where U <= 0.005 NJOY(447) the collapsed
    emulated capture X satisfies NJOY (1 - 0.005) - U <= X <= NJOY (1 + 0.005); fewer than 10 such groups is vacuous
    and G4 fails for the target. B2: reference at
    every PENDF grid energy = 446 + sum over MT=51-90 of sigma (W_exact - W_ladder); W_exact is the exact two-body
    damage per event of the level (MF=4 + Q, centre-of-mass kinematics, as A1), W_ladder its linear interpolation
    between the points heatr.f90 disbar evaluates (1.1 step ladder over the PENDF grid and the MF=4 energies).
    HEATR tabulates the levels' sigma W_ladder only at the grid energies, so between them the reference is the
    tabulated remainder plus sigma(E) W_exact(E) on energies subdivided to a 2 percent ratio (Amendment C2; the grid-only
    form is reported). Precondition (C1): the same test for the emulated tabulated levels term against 446, U over MT=91
    (the other 446 contributor), in groups above the lowest level threshold. HEATR's 444-447 tables carry 7-digit energies for values computed at
    the 9-digit grid energy and drop their leading zero rows; both are restored from the grid before the references and
    preconditions are formed (C3); the raw MT=446/447 comparisons use the tables as read.
G5  Total and bound (Amendment B3). In every group the reference total NJOY444 - NJOY445 + (exact elastic reference)
    + (B1 reference - NJOY447) + (B2 reference - NJOY446) lies in [computed - s, computed + upper + s], computed =
    `damage_energy_barn_eV`, upper = `uncovered_damage_upper_barn_eV`, s = 0.02 (exact elastic) + 0.05 (B2 + B1
    references) + 1e-6 max(reference total) (the G3/G4 tolerances, plus a floor for groups with essentially no damage).
    The raw NJOY444 bound and the A3 form are information. The table
    total equals elastic + inelastic + disappearance (1e-9). Totals folded with the FNS spectrum and their
    differences from NJOY are reported.
G6  Displacement quantities. FNS Fe run (spec: examples/fns_fe_5min.json plus damage and gas) with the Fe-56 table-2:
    `dpa`, `dpa_nrt`, `dpa_arc`, their rates, and the uncovered upper bounds reproduced from the table, the step flux
    multipliers and durations and the spectrum within 1e-12 relative (the uncovered energy per gram, which needs the
    atom density from this script's abundance copy, within 1e-9); the arc parameters in every table equal this
    script's transcription of Nordlund Table 1; `he_appm_per_dpa` equals independently read helium (He-3 + He-4 appm)
    over `dpa`; ledger and certificate carry the `recoil` block; a Cr-52 run (no arc constants) has no `dpa_arc`; a
    spec whose displacement energy differs from the build value is refused with a nonzero exit and writes nothing.
    Table invariants (nonnegative rows, c * nrt <= arc <= nrt) hold in every built table.

Capture mode: NJOY HEATR treats MT=102 by capdam (one-photon capture damage) only when the reaction has no MF=6
section (icon=0, heatr.f90:1741-1827, 1387). When MF=6 MT=102 exists (TENDL-2025 has it for Fe-56, Cr-52, Ni-58 and
W-184) the reaction is routed to sixbar -> tabsq6 (heatr.f90:1125-1140, 1242, 1388-1389), which partitions each
photon's recoil separately. The checker scans each pinned TENDL file for MF=6 MT=102 and builds the table with
`--capture-photons per-photon` where it is present, `one-photon` otherwise; the determination is recorded per target
in the output. Continuum recoil (Amendment A2): HEATR sends every MF=6 reaction through sixbar, which takes the damage
from the evaluation's residual-nucleus recoil subsection (LAW=4 or ZAP > 2004) where present, else from the emitted
particle (heatr.f90:2755-2860, 3157); the checker scans MF=6 MT=91 and 600-849 of each pinned file and builds with
`--continuum-recoil evaluated` where a continuum MT (91, 649, 699, 749, 799, 849) carries one, `kinematic` otherwise,
recorded per target and MT. The default build (`evaluated` capture, `kinematic` continuum) and the other combinations
are reported as information against NJOY MT=446 and 447 (relative differences, G4-style counts, FNS totals).

Build parameters: the displacement energy of each target's element is Nordlund's Table 1 value where it has one (Fe
40, Ni 39, W 70) and 40 eV for Cr (no arc-dpa constants; the ASTM E521 value), identical in `--displacement-energy`
and NJOY's HEATR `ed`. `--ed-uniform 25` sets the 25 eV default everywhere instead. Capture uses the NJOY-matching
mode chosen above for the comparisons.

Usage: check_p154.py --reference REF --candidate CAND [--workdir target/p154-work] [--out results/p154_damage.json]
       check_p154.py --self-check      (offline unit checks, no binary, no NJOY)
Run inside the required systemd cgroup. Gates run serially.
"""
import argparse
import bisect
import copy
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p154_njoy as njoy  # noqa: E402
import p126_njoy_fe56 as p126  # noqa: E402  (own ENDF-6 reader)

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P154_PROTOCOL.md"
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
DEMO_SPEC = ROOT / "examples" / "damage_demo.json"
P23_CORPUS = ROOT / "results" / "g3_p23_corpus"
GROUPS_JSON = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
TENDL_DIR = Path("~/nuclear-data/tendl-2025-patched/files/n").expanduser()
MS = re.compile(rb'"ms":\s*[-+0-9.eE]+')
NA = 6.02214076e23

TOL_G3, TOL_G4 = 0.02, 0.05
G3_EMIN_EV, G3_FLOOR = 1.0e3, 1.0e-6
G4_FRACTION, G4_MIN_GROUPS = 1.0e-3, 5
G4_UNCOVERED_FRACTION = TOL_G4 / 10.0   # an uncovered bound below this share of the channel value is negligible
G5_FLOOR = 1.0e-6
EL_TOL, EL_REFINE = 1.0e-9, 1.05   # exact elastic W: quadrature tolerance, largest energy-gap ratio
TOL_RUN = 1e-12
TOL_ATOMS = 1e-9
TOL_TABLE = 1e-9

# Nordlund et al., Nat. Commun. 9, 1084 (2018), Table 1 ("arc-dpa" columns), transcribed from the journal's HTML
# table page (https://www.nature.com/articles/s41467-018-03415-5/tables/1), fetched 2026-10-27:
#   element: (E_d in eV, b_arcdpa, c_arcdpa)
NORDLUND_TABLE_1 = {
    "Fe": (40.0, -0.568, 0.286),
    "Cu": (33.0, -0.68, 0.16),
    "Ni": (39.0, -1.01, 0.23),
    "Pd": (41.0, -0.88, 0.15),
    "Pt": (42.0, -1.12, 0.11),
    "W": (70.0, -0.56, 0.12),
}
ATOMIC_NUMBER = {"Fe": 26, "Cr": 24, "Ni": 28, "W": 74, "Cu": 29, "Pd": 46, "Pt": 78}
# Pinned TENDL-2025 (patched) evaluations: file, element, SHA-256.
TARGETS = {
    "Fe56": {"file": "n-Fe056.tendl", "element": "Fe",
             "sha256": "f33f867a4f9c4579a62954fe31dc6e70768ab2424dc8f282a122a93f156d2e1e"},
    "Cr52": {"file": "n-Cr052.tendl", "element": "Cr",
             "sha256": "796548d723c19db57930f7a3c4c81a6a53e4004346f74279946d0d70875f0a94"},
    "Ni58": {"file": "n-Ni058.tendl", "element": "Ni",
             "sha256": "26c8ae72a203187fae84f9a11c3997086cd70153386e04cf204b4122ab368916"},
    "W184": {"file": "n-W184.tendl", "element": "W",
             "sha256": "cbf1c80e1557af58fcf4c40a78b3e1a0d2d70b08719a99ffe4a55b753aaa2a20"},
}
BUILD_ED = {"Fe": 40.0, "Cr": 40.0, "Ni": 39.0, "W": 70.0}
# Natural-abundance Fe copy (atom fractions, isotopic masses), the same independent copy controls/p23_damage.py uses.
FE_ABUNDANCE = {54: (0.05845, 53.939608189), 56: (0.91754, 55.934935537), 57: (0.02119, 56.93539195),
                58: (0.00282, 57.933273575)}


class GateError(Exception):
    pass


# ------------------------------------------------------------------------------ small helpers


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rel(a, b):
    """Relative difference of a from b (inf when b is zero and a is not)."""
    if a == b:
        return 0.0
    if b == 0.0:
        return math.inf
    return abs(a - b) / abs(b)


def sh(command, timeout, cwd=ROOT):
    """One subprocess, no shell, bounded; the child is killed and reaped on timeout by subprocess.run."""
    try:
        proc = subprocess.run([str(c) for c in command], cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GateError(f"timed out after {timeout} s: {' '.join(str(c) for c in command)[:200]}")
    return proc.returncode, proc.stdout, proc.stderr


def run_spec(binary, spec_path, out_path, timeout):
    code, _out, err = sh([binary, "run", spec_path, out_path], timeout)
    if code != 0:
        raise GateError(f"{binary} run failed on {spec_path}: {err.strip()[-400:]}")
    return json.loads(Path(out_path).read_text())


def absolute(spec):
    spec = copy.deepcopy(spec)
    spec["library"]["path"] = str(ROOT / spec["library"]["path"])
    for key in ("primary", "fallback"):
        if spec.get("decay", {}).get(key):
            spec["decay"][key] = str(ROOT / spec["decay"][key])
    return spec


def vendored_boundaries():
    data = json.loads(GROUPS_JSON.read_text())
    return sorted(float(b) for b in data["boundaries_eV"])


def parse_duration(text):
    match = re.fullmatch(r"\s*([-+0-9.eE]+)\s*([A-Za-z]*)\s*", str(text))
    if not match:
        raise ValueError(f"unparseable duration {text!r}")
    scale = {"s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0}.get(match.group(2) or "s")
    if scale is None:
        raise ValueError(f"unsupported duration unit in {text!r}")
    return float(match.group(1)) * scale


def spectrum_ascending(spec, bounds):
    """Per-group flux of the spec's spectrum in the order of `bounds` (as controls/p23_damage.py mirrors the run)."""
    sp = spec["spectrum"]
    flux = [float(v) for v in sp["flux_per_group"]]
    if sp.get("descending"):
        flux = flux[::-1]
    if sp.get("total"):
        scale = sp["total"] / sum(flux)
        flux = [v * scale for v in flux]
    if bounds[0] > bounds[-1]:
        flux = flux[::-1]
    return flux


def fold(row, flux):
    """Sum_g row_g flux_g in barn per atom times 1e-24: reactions per atom per second at multiplier one."""
    return sum(r * f for r, f in zip(row, flux)) * 1e-24


# ------------------------------------------------------------------------------ physics (own implementations)


def lindhard_energy(zr, ar, zl, al):
    """The Lindhard energy E_L in eV as in Robinson's fit: 30.724 Zr Zl sqrt(Zr^(2/3)+Zl^(2/3)) (Ar+Al)/Al."""
    return 30.724 * zr * zl * math.sqrt(zr ** (2.0 / 3.0) + zl ** (2.0 / 3.0)) * (ar + al) / al


def lindhard_k(zr, ar, zl, al):
    """Lindhard's k for the (recoil, lattice) pair."""
    denom = (zr ** (2.0 / 3.0) + zl ** (2.0 / 3.0)) ** 0.75 * ar ** 1.5 * math.sqrt(al)
    return 0.0793 * zr ** (2.0 / 3.0) * math.sqrt(zl) * (ar + al) ** 1.5 / denom


def df(energy, zr, ar, zl, al, ed):
    """Damage energy of a recoil of `energy` eV (Z, A: zr, ar) in a lattice (zl, al): E / (1 + k g(eps)),
    g = 3.4008 eps^(1/6) + 0.40244 eps^(3/4) + eps, eps = E / E_L; zero below the displacement energy `ed`
    (NJOY HEATR: `break` applies to the recoil energy) and for a recoil without charge."""
    if zr == 0.0 or energy <= 0.0 or energy < ed:
        return 0.0
    eps = energy / lindhard_energy(zr, ar, zl, al)
    g = 3.4008 * eps ** (1.0 / 6.0) + 0.40244 * eps ** 0.75 + eps
    return energy / (1.0 + lindhard_k(zr, ar, zl, al) * g)


def nrt(td, ed):
    """Exact NRT displacements: 0 below E_d, 1 up to 2 E_d / 0.8, 0.8 T_d / (2 E_d) above."""
    if td < ed:
        return 0.0
    if td < 2.0 * ed / 0.8:
        return 1.0
    return 0.8 * td / (2.0 * ed)


def arc_dpa(td, ed, b, c):
    """Nordlund arc-dpa: NRT below 2 E_d / 0.8, NRT times xi(T_d) above, xi = (1-c)(T_d/(2 E_d/0.8))^b + c."""
    n = nrt(td, ed)
    t0 = 2.0 * ed / 0.8
    if td < t0:
        return n
    return n * ((1.0 - c) * (td / t0) ** b + c)


# ------------------------------------------------------------------------------ comparison logic (gates G3-G5)


def distribution(errors):
    if not errors:
        return {"n": 0}
    s = sorted(errors)
    return {"n": len(s), "median": statistics.median(s), "p90": s[min(len(s) - 1, int(0.9 * len(s)))],
            "max": s[-1]}


def elastic_comparison(table, ref, bounds, tol=TOL_G3):
    """G3 on one target. `ref` is NJOY's MT=445 collapsed; bounds ascending or descending as the rows are."""
    top = max(ref) if ref else 0.0
    evaluated, bad, below = 0, [], []
    worst = 0.0
    for g, (t, r) in enumerate(zip(table, ref)):
        lo = min(bounds[g], bounds[g + 1])
        if lo >= G3_EMIN_EV:
            if r > G3_FLOOR * top:
                evaluated += 1
                e = rel(t, r)
                worst = max(worst, e)
                if e > tol:
                    bad.append({"group": g, "lo_eV": lo, "table": t, "njoy": r, "rel": e})
        elif r > 0.0:
            below.append(rel(t, r))
    return {"evaluated_groups": evaluated, "n_bad": len(bad), "worst_rel": worst, "bad": bad[:20],
            "below_1keV": distribution(below),
            "below_1keV_table_positive_njoy_zero": sum(
                1 for g, (t, r) in enumerate(zip(table, ref)) if min(bounds[g], bounds[g + 1]) < G3_EMIN_EV
                and r == 0.0 and t > 0.0),
            "pass": evaluated > 0 and not bad}


def uncovered_present(upper, computed):
    """A group has an uncovered contribution only when its uncovered damage upper bound exceeds
    G4_UNCOVERED_FRACTION (0.5 percent) of the channel's computed value in that group."""
    return upper > G4_UNCOVERED_FRACTION * computed


def channel_comparison(table, ref, ref_total, uncovered_upper, bounds, tol=TOL_G4):
    """G4 on one channel: groups where NJOY's channel exceeds 1e-3 of NJOY's total and the uncovered damage upper
    bound is at most 0.5 percent of the channel's computed value (uncovered_present)."""
    evaluated, bad, worst = 0, [], 0.0
    skipped, negligible = [], 0
    for g, (t, r, tot, unc) in enumerate(zip(table, ref, ref_total, uncovered_upper)):
        if not (tot > 0.0 and r > G4_FRACTION * tot):
            continue
        e = rel(t, r)
        if uncovered_present(unc, t):
            skipped.append(e)
            continue
        if unc > 0.0:
            negligible += 1
        evaluated += 1
        worst = max(worst, e)
        if e > tol:
            bad.append({"group": g, "lo_eV": min(bounds[g], bounds[g + 1]), "table": t, "njoy": r, "rel": e})
    return {"evaluated_groups": evaluated, "n_bad": len(bad), "worst_rel": worst, "bad": bad[:20],
            "with_uncovered_reported": distribution(skipped),
            "uncovered_negligible_groups_evaluated": negligible,
            "vacuous": evaluated < G4_MIN_GROUPS and any(v > 0.0 for v in ref),
            "pass": not bad and not (evaluated < G4_MIN_GROUPS and any(v > 0.0 for v in ref))}


def bound_comparison(total, upper, n444, n445, n446, n447):
    """G5 on one target: NJOY 444 in [total - s, total + upper + s] per group."""
    floor = G5_FLOOR * (max(n444) if n444 else 0.0)
    bad, below, above = [], 0, 0
    for g, (t, u, a, b, c, d) in enumerate(zip(total, upper, n444, n445, n446, n447)):
        s = TOL_G3 * b + TOL_G4 * (c + d) + floor
        if a < t - s:
            below += 1
            bad.append({"group": g, "side": "below_computed", "njoy444": a, "computed": t, "slack": s})
        elif a > t + u + s:
            above += 1
            bad.append({"group": g, "side": "above_upper", "njoy444": a, "computed_plus_upper": t + u, "slack": s})
    return {"n_below_computed": below, "n_above_upper": above, "bad": bad[:20], "pass": not bad}


# ------------------------------------------------------------------------------ exact elastic damage (gate G3 reference)


def gauss_legendre(n):
    """Nodes and weights of the n-point Gauss-Legendre rule on [-1, 1] (Newton on P_n)."""
    nodes, weights = [], []
    for i in range(1, n + 1):
        x = math.cos(math.pi * (i - 0.25) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, x
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
            dp = n * (x * p1 - p0) / (x * x - 1.0)
            step = p1 / dp
            x -= step
            if abs(step) < 1e-15:
                break
        p0, p1 = 1.0, x
        for k in range(2, n + 1):
            p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
        dp = n * (x * p1 - p0) / (x * x - 1.0)
        nodes.append(x)
        weights.append(2.0 / ((1.0 - x * x) * dp * dp))
    return nodes, weights


_GL_X, _GL_W = gauss_legendre(8)


def _gl(fun, a, b):
    half, mid = 0.5 * (b - a), 0.5 * (a + b)
    return half * sum(w * fun(mid + half * x) for x, w in zip(_GL_X, _GL_W))


def adaptive_gl(fun, a, b, tol=EL_TOL, depth=0):
    """Adaptive 8-point Gauss-Legendre: a panel is accepted when its two halves agree with it to `tol` (relative)."""
    whole = _gl(fun, a, b)
    mid = 0.5 * (a + b)
    halves = _gl(fun, a, mid) + _gl(fun, mid, b)
    if depth >= 30 or abs(halves - whole) <= tol * abs(halves) + 1e-300:
        return halves
    return adaptive_gl(fun, a, mid, tol, depth + 1) + adaptive_gl(fun, mid, b, tol, depth + 1)


def interp_value(tab, e):
    """Value of a TAB1-like table at `e` under its own interpolation laws; zero outside its support."""
    x, y = tab["x"], tab["y"]
    if e < x[0] or e > x[-1]:
        return 0.0
    i = min(max(bisect.bisect_right(x, e) - 1, 0), len(x) - 2)
    x1, x2, y1, y2 = x[i], x[i + 1], y[i], y[i + 1]
    law = njoy.law_of_segment(tab["ranges"], i)
    if law == 1 or not x2 > x1:
        return y1
    if law == 2:
        return y1 + (y2 - y1) * (e - x1) / (x2 - x1)
    if law == 3:
        return y1 + (y2 - y1) * math.log(e / x1) / math.log(x2 / x1)
    if law == 4:
        return y1 + (y2 - y1) * (e - x1) / (x2 - x1) if y1 <= 0.0 or y2 <= 0.0 else \
            math.exp(math.log(y1) + (e - x1) / (x2 - x1) * (math.log(y2) - math.log(y1)))
    if law == 5:
        if y1 <= 0.0 or y2 <= 0.0:
            return y1 + (y2 - y1) * (e - x1) / (x2 - x1)
        return y1 * (e / x1) ** (math.log(y2 / y1) / math.log(x2 / x1))
    raise ValueError(f"unsupported interpolation law INT={law}")


def legendre_pdf(coeffs):
    """Angular pdf f(mu) = 1/2 + sum_l (2l+1)/2 a_l P_l(mu) from the ENDF coefficients a_1..a_L: (breaks, f)."""
    coeffs = list(coeffs)

    def fn(mu):
        total = 0.5
        p0, p1 = 1.0, mu
        for ell, a in enumerate(coeffs, start=1):
            if ell > 1:
                p0, p1 = p1, ((2 * ell - 1) * mu * p1 - (ell - 1) * p0) / ell
            total += 0.5 * (2 * ell + 1) * a * p1
        return total

    return [-1.0, 1.0], fn


def tabulated_pdf(tab):
    """Tabulated pdf renormalized to unit integral where its laws allow it: (breaks, f, deviation of the norm)."""
    norm, exact = 0.0, True
    for i in range(len(tab["x"]) - 1):
        law = njoy.law_of_segment(tab["ranges"], i)
        width = tab["x"][i + 1] - tab["x"][i]
        if law == 2:
            norm += 0.5 * (tab["y"][i] + tab["y"][i + 1]) * width
        elif law == 1:
            norm += tab["y"][i] * width
        else:
            exact = False
    scale = 1.0 / norm if exact and norm > 0.0 else 1.0
    breaks = sorted({min(1.0, max(-1.0, v)) for v in tab["x"]})
    return breaks, (lambda mu: scale * interp_value(tab, mu)), (abs(norm - 1.0) if exact else None)


def mixed_pdf(a, b, alpha):
    """(1 - alpha) a + alpha b on the union of the panel edges (linear interpolation in incident energy)."""
    breaks = sorted(set(a[0]) | set(b[0]))
    return breaks, (lambda mu: (1.0 - alpha) * a[1](mu) + alpha * b[1](mu))


def elastic_w(pdf, e, awr, z, ed, lab=False, damage=None):
    """Exact mean damage energy per elastic event at incident energy `e` (eV): the integral over the scattering cosine
    of pdf(mu) R(T(mu)), R the Lindhard damage function (0 below `ed`, as HEATR's `break`), T the recoil energy of a
    target of mass ratio `awr`. Centre-of-mass cosine: T = 2 A E (1 - mu) / (A + 1)^2. Laboratory cosine (LCT=1):
    T = E (1 - s^2) with s = (mu + sqrt(A^2 - 1 + mu^2)) / (A + 1). The integral is cut where T = ed (no damage on the
    other side) and at the NRT breakpoint T = 2 ed / 0.8 and at every pdf panel edge, then integrated adaptively."""
    breaks, fn = pdf[0], pdf[1]
    if damage is None:
        def damage(t):
            return df(t, z, awr, z, awr, ed)
    if lab:
        def recoil(mu):
            s = (mu + math.sqrt(awr * awr - 1.0 + mu * mu)) / (awr + 1.0)
            return e * (1.0 - s * s)

        def cosine_at(t):                         # inverse of `recoil`: the cosine where the recoil energy is t
            if t >= e:
                return -math.inf
            q = (awr + 1.0) * math.sqrt(1.0 - t / e)
            return (q * q - awr * awr + 1.0) / (2.0 * q)
    else:
        c = 2.0 * awr * e / (awr + 1.0) ** 2

        def recoil(mu):
            return c * (1.0 - mu)

        def cosine_at(t):
            return 1.0 - t / c
    top = cosine_at(ed)
    if not top > -1.0:
        return 0.0
    top = min(1.0, top)
    cuts = [cosine_at(2.0 * ed / 0.8)] + list(breaks)
    edges = [-1.0] + sorted({v for v in cuts if -1.0 < v < top}) + [top]

    def integrand(mu):
        return fn(mu) * damage(recoil(mu))

    return sum(adaptive_gl(integrand, lo, hi) for lo, hi in zip(edges, edges[1:]) if hi > lo)


def parse_mf4(lines):
    """One MF=4 section: {"za", "awr", "lct", "li", "ltt", "parts": [{"kind", "energies", "data", "ranges"}]}.
    LTT=0 (isotropic, LI=1) has no parts; LTT=1/2/3 give a Legendre part, a tabulated part, or both in that order."""
    za, awr, _lvt, ltt, _a, _b = p126.record(lines, 0)
    _c1, awr2, li, lct, _a, _b = p126.record(lines, 1)
    out = {"za": int(round(za)), "awr": awr, "lct": lct, "li": li, "ltt": ltt, "parts": []}
    index = 2
    for kind in ([] if ltt == 0 else {1: ["legendre"], 2: ["tabulated"], 3: ["legendre", "tabulated"]}[ltt]):
        _t, _u, _l1, _l2, nr, ne = p126.record(lines, index)
        words, index = p126.data_words(lines, index + 1, 2 * nr)
        ranges = [(p126.endf_integer(words[2 * k]), p126.endf_integer(words[2 * k + 1])) for k in range(nr)]
        energies, data = [], []
        for _ in range(ne):
            _t, energy, _l1, _l2, n1, n2 = p126.record(lines, index)
            energies.append(energy)
            if kind == "legendre":
                words, index = p126.data_words(lines, index + 1, n1)
                data.append([p126.endf_number(w) for w in words])
            else:
                words, index = p126.data_words(lines, index + 1, 2 * n1)
                rng = [(p126.endf_integer(words[2 * k]), p126.endf_integer(words[2 * k + 1])) for k in range(n1)]
                words, index = p126.data_words(lines, index, 2 * n2)
                data.append({"x": [p126.endf_number(words[2 * k]) for k in range(n2)],
                             "y": [p126.endf_number(words[2 * k + 1]) for k in range(n2)], "ranges": rng})
        out["parts"].append({"kind": kind, "energies": energies, "data": data, "ranges": ranges})
    if ltt == 0 and li != 1:
        raise ValueError("MF=4 with LTT=0 but LI != 1")
    return out


def read_mf4_many(path, mts):
    """{mt: parse_mf4(...)} for the MF=4 sections of the requested MTs found in the file (one pass)."""
    found = {}
    wanted = set(mts)
    for _mat, mf, mt, lines in p126.sections(path):
        if mf == 4 and mt in wanted:
            found[mt] = parse_mf4(lines)
    return found


def read_mf4_elastic(path):
    """MF=4 MT=2 of an ENDF-6 file (see parse_mf4)."""
    got = read_mf4_many(path, (2,))
    if 2 not in got:
        raise ValueError(f"{path}: no MF=4/MT=2 section")
    return got[2]


def w_table(mf4, ed, refine=EL_REFINE):
    """W(E) per elastic event on the MF=4 energies refined so no gap exceeds `refine` in ratio, the pdf interpolated
    linearly in energy between MF=4 energies (INT=2, the interpolation law of the pinned evaluations; INT=1 holds the
    lower pdf; any other law is refused), plus the threshold energy ed (A+1)^2 / (4 A) where W is zero. W is then taken
    linear in E between these points and constant above the last MF=4 energy. Returns {"x", "y", "ranges", "info"}."""
    awr, z = mf4["awr"], mf4["za"] // 1000
    lab = mf4["lct"] == 1
    if mf4["lct"] not in (1, 2):
        raise ValueError(f"MF=4 LCT={mf4['lct']} is neither laboratory nor centre of mass")
    points, deviation = {}, 0.0
    threshold = ed * (awr + 1.0) ** 2 / (4.0 * awr)

    def make_pdf(part, i):
        nonlocal deviation
        if part["kind"] == "legendre":
            return legendre_pdf(part["data"][i])
        breaks, fn, dev = tabulated_pdf(part["data"][i])
        if dev is not None:
            deviation = max(deviation, dev)
        return breaks, fn

    def put(e, pdf):
        points[e] = elastic_w(pdf, e, awr, z, ed, lab=lab) if e > threshold * (1.0 - 1e-12) else 0.0

    if not mf4["parts"]:
        energies = [1e-5, 2.0e7]
        iso = legendre_pdf([])
        for e in sorted(set(energies + [threshold, threshold * 1.0001])):
            put(e, iso)
    for part in mf4["parts"]:
        energies = part["energies"]
        for j in range(len(energies) - 1):
            e0, e1 = energies[j], energies[j + 1]
            if not e1 > e0:
                continue
            law = njoy.law_of_segment(part["ranges"], j)
            if law not in (1, 2):
                raise ValueError(f"MF=4 energy interpolation law INT={law} is not handled")
            pdf0 = make_pdf(part, j)
            pdf1 = make_pdf(part, j + 1) if law == 2 else None
            if e1 < threshold * (1.0 - 1e-12):
                points.setdefault(e0, 0.0)
                continue
            pieces = max(1, int(math.ceil(math.log(e1 / e0) / math.log(refine)))) if e0 > 0.0 else 1
            for k in range(pieces):
                e = e0 * (e1 / e0) ** (k / pieces) if e0 > 0.0 else e0
                pdf = pdf0 if law == 1 or k == 0 else mixed_pdf(pdf0, pdf1, (e - e0) / (e1 - e0))
                put(e, pdf)
        put(energies[-1], make_pdf(part, len(energies) - 1))
    if mf4["parts"] and threshold < max(points):
        points[threshold] = 0.0
    xs = sorted(points)
    return {"x": xs, "y": [points[x] for x in xs], "ranges": [(len(xs), 2)],
            "info": {"n_points": len(xs), "threshold_eV": threshold, "frame": "laboratory" if lab else "centre of mass",
                     "ltt": mf4["ltt"], "tabulated_pdf_norm_worst_deviation": deviation, "refine_ratio": refine,
                     "energy_interpolation": "linear pdf mixing between MF=4 energies (INT=2); W linear between "
                                             "the refined points; constant above the last MF=4 energy"}}


def damage_product(sigma, w):
    """sigma(E) W(E) on the union of both energy grids (lin-lin), W held at its last value above its grid."""
    xs = sorted(set(sigma["x"]) | set(w["x"]))
    last = w["x"][-1]
    ys = []
    for x in xs:
        wv = w["y"][-1] if x > last else interp_value(w, x)
        ys.append(interp_value(sigma, x) * wv)
    return {"x": xs, "y": ys, "ranges": [(len(xs), 2)]}


def exact_elastic_reference(endf_path, pendf_path, boundaries, ed):
    """The G3 reference: RECONR's pointwise MT=2 times the exact elastic damage per event, collapsed flat in lethargy.
    Returns {"groups": [...], "info": {...}}."""
    mf4 = read_mf4_elastic(endf_path)
    sigma = njoy.read_pendf(pendf_path, mts=(2,)).get(2)
    if sigma is None:
        raise GateError("the PENDF carries no MF=3 MT=2")
    w = w_table(mf4, ed)
    product = damage_product(sigma, w)
    return {"groups": njoy.collapse(product, boundaries), "info": w["info"]}


def njoy_documented_approximations():
    """NJOY2016.79 HEATR approximations relevant to the elastic damage (reported, not emulated)."""
    return [
        {"where": "heatr.f90:1891, 1941", "what": "elastic onset uses a fixed edis = 25 eV: below the incident energy "
                                                  "where the maximum recoil is 25 eV, W is taken as zero whatever `ed`"},
        {"where": "heatr.f90:1892, 1950-1960", "what": "elastic W is evaluated on a 1.1-ratio energy ladder and "
                                                        "interpolated linearly in between"},
        {"where": "heatr.f90:4404-4551 (hgetco), 4484", "what": "tabulated MF=4 pdfs are projected onto at most 64 "
                                                                 "Legendre coefficients by 64-point Gauss quadrature, "
                                                                 "rounded to 6 digits (4538-4546)"},
        {"where": "heatr.f90:4531", "what": "laboratory-to-centre-of-mass conversion of coefficients is not coded"},
        {"where": "heatr.f90:1943-1970", "what": "the angle integral of the damage function is a fixed 64-point "
                                                   "Gauss-Legendre rule over the Legendre series"},
        {"where": "heatr.f90:4147-4153, 4182-4198", "what": "capture photons (tabsq6): the continuum panels use "
                                                            "4-point Gauss-Legendre weights (sum 2) times dx instead "
                                                            "of dx/2, so the continuum counts twice against the "
                                                            "unit-weight discrete lines, and the spectrum is "
                                                            "interpolated linearly whatever LEP (Amendment B1)"},
        {"where": "heatr.f90:1892, 1949-2010 (disbar, step = 1.1)", "what": "discrete-level damage is evaluated on a "
                                                                      "1.1-ratio ladder over the PENDF grid and the "
                                                                      "MF=4 energies and interpolated linearly; "
                                                                      "HEATR tabulates sigma W only at the grid "
                                                                      "energies (Amendment B2)"},
    ]


# ------------------------------------------------------------------------------ Amendment B references (G4, G5)

B_VERSION = 1                       # bump when the B1/B2 reference computation changes (keys the cached results)
EMC2 = 1.00866491595 * 931.49410242e6   # eV: amassn * amu c^2 (NJOY phys.f90), the photon-recoil mass unit of tabsq6
TABSQ6_X = (-0.86114, -0.33998, 0.33998, 0.86114)    # heatr.f90:4147-4150: the 5-digit 4-point Gauss-Legendre rule
TABSQ6_W = (0.34785, 0.65215, 0.65215, 0.34785)      # (the weights sum to 2)
B_PRECONDITION_TOL, C1_BOUND_FRACTION, C1_MIN_GROUPS = 0.005, 5.0e-3, 10     # Amendment C1
# MTs HEATR adds to MT=447 other than capture (heatr.f90 indx: MT=102-120 and 600-849 for ENDF-6) and the 446 contributors
# other than the levels (MT=51-91): the terms the emulations do not cover
C1_OTHER_447 = tuple(range(103, 121)) + tuple(range(600, 850))
C1_OTHER_446 = (91,)
LADDER_STEP, LADDER_SMALL, LADDER_ETOP = 1.1, 1.0e-10, 2.0e7   # heatr.f90 disbar `step`, `small`; etop (line 485)
LEVEL_MTS = tuple(range(51, 91))
LEVEL_REFINE, LEVEL_TOL, LEVEL_FINE_RATIO = 1.05, 1.0e-8, 1.02


def read_mf6_photons(path, mt=102):
    """The photon subsection (ZAP = 0, LAW = 1) of MF=6 `mt`: {"awr", "yield", "lang", "lep", "records"}, records being
    {"energy", "nd", "x", "y"} (the first nd pairs are discrete lines (photon energy, probability), the rest the
    continuum (photon energy, spectrum value)). None when the section or a photon subsection is absent."""
    for _mat, mf, mt_, lines in p126.sections(path):
        if (mf, mt_) != (6, mt):
            continue
        _za, awr, _jp, _lct, nk, _ = p126.record(lines, 0)
        index = 1
        for _ in range(nk):
            zap, awp, _lip, law, nr, npts = p126.record(lines, index)
            words, index = p126.data_words(lines, index + 1, 2 * nr)
            ranges = [(p126.endf_integer(words[2 * k]), p126.endf_integer(words[2 * k + 1])) for k in range(nr)]
            words, index = p126.data_words(lines, index, 2 * npts)
            yld = {"x": [p126.endf_number(words[2 * k]) for k in range(npts)],
                   "y": [p126.endf_number(words[2 * k + 1]) for k in range(npts)], "ranges": ranges}
            if zap == 0.0 and law == 1:
                if awp != 0.0:
                    raise ValueError("MF=6 photon subsection with AWP > 0 (HEATR's relativistic hgam102 path)")
                _a, _b, lang, lep, nr2, ne = p126.record(lines, index)
                _, index = p126.data_words(lines, index + 1, 2 * nr2)
                records = []
                for _ in range(ne):
                    _c, energy, nd, _na, nw, nep = p126.record(lines, index)
                    words, index = p126.data_words(lines, index + 1, nw)
                    values = [p126.endf_number(w) for w in words]
                    stride = nw // nep
                    records.append({"energy": energy, "nd": nd, "x": values[0::stride], "y": values[1::stride]})
                return {"awr": awr, "yield": yld, "lang": lang, "lep": lep, "records": records}
            index = _skip_law(lines, index, law)
            if index is None:
                raise ValueError(f"MF=6 MT={mt}: a subsection with LAW={law} could not be skipped")
        return None
    return None


def tabsq6_record(record, tm, damage):
    """What heatr.f90 tabsq6 (4135-4212) makes of one MF=6 photon record: (damage per photon, normalisation s).
    Discrete lines carry their probability; each continuum panel is integrated by the 4-point rule with weights times
    dx (the weights sum to 2, so the continuum counts twice) on the spectrum interpolated linearly (inn = 2) whatever
    LEP says; the sums are divided by s."""
    x, y, nd = record["x"], record["y"], record["nd"]
    h = s = 0.0
    for i in range(nd):
        h += damage(x[i] * x[i] / (2.0 * tm)) * y[i]
        s += y[i]
    for i in range(nd, len(x) - 1):
        xl, xh, yl, yh = x[i], x[i + 1], y[i], y[i + 1]
        if xl == xh:
            continue
        dx = xh - xl
        for q, w in zip(TABSQ6_X, TABSQ6_W):
            xx = xl + (1.0 + q) * dx / 2.0
            yy = yl + (xx - xl) / dx * (yh - yl)
            h += w * damage(xx * xx / (2.0 * tm)) * dx * yy
            s += w * dx * yy
    return (h / s if s > 0.0 else 0.0), s


def exact_photon_record(record, tm, damage, lep, cuts=()):
    """The same record read as ENDF-6 specifies: discrete lines at unit weight, the continuum a histogram (LEP = 1:
    the value at the lower point holds over the panel) or linear (LEP = 2), each panel split at the photon energies
    in `cuts` (where the recoil energy equals the displacement energy) and integrated adaptively. Returns (damage per
    photon, probability normalisation)."""
    if lep not in (1, 2):
        raise ValueError(f"MF=6 LEP={lep} is neither histogram nor linear")
    x, y, nd = record["x"], record["y"], record["nd"]
    h = s = 0.0
    for i in range(nd):
        h += damage(x[i] * x[i] / (2.0 * tm)) * y[i]
        s += y[i]
    for i in range(nd, len(x) - 1):
        xl, xh, yl, yh = x[i], x[i + 1], y[i], y[i + 1]
        if not xh > xl:
            continue
        if lep == 1:
            def f(v, yl=yl):
                return yl
        else:
            def f(v, xl=xl, xh=xh, yl=yl, yh=yh):
                return yl + (v - xl) / (xh - xl) * (yh - yl)
        edges = [xl] + sorted(c for c in cuts if xl < c < xh) + [xh]
        for a, b in zip(edges, edges[1:]):
            h += adaptive_gl(lambda v: f(v) * damage(v * v / (2.0 * tm)), a, b, LEVEL_TOL)
            s += adaptive_gl(f, a, b, LEVEL_TOL)
    return (h / s if s > 0.0 else 0.0), s


def record_interpolant(energies, values, e):
    """sixbar's value between MF=6 records: linear in E between the two records around `e` (extrapolated below the first,
    as terp1 does), zero above the last record (heatr.f90:305-307, 450)."""
    if e > energies[-1] * (1.0 + 1e-10):
        return 0.0
    k = min(max(bisect.bisect_right(energies, e) - 1, 0), len(energies) - 2)
    e0, e1 = energies[k], energies[k + 1]
    return values[k] + (values[k + 1] - values[k]) * (e - e0) / (e1 - e0)


def capture_records(mf6, z, awr, ed):
    """Per MF=6 record of the capture photons: the yield-weighted damage per capture as tabsq6 computes it and as the
    format specifies, with the normalisations."""
    tm = EMC2 * (awr + 1.0)

    def damage(t):
        return df(t, z, awr + 1.0, z, awr, ed)

    cuts = [math.sqrt(2.0 * tm * ed)]
    out = {"energies": [], "tabsq6": [], "exact": [], "s_tabsq6": [], "s_exact": [], "yield": []}
    for rec in mf6["records"]:
        y = interp_value(mf6["yield"], rec["energy"])
        h_n, s_n = tabsq6_record(rec, tm, damage)
        h_x, s_x = exact_photon_record(rec, tm, damage, mf6["lep"], cuts)
        out["energies"].append(rec["energy"])
        out["tabsq6"].append(y * h_n)
        out["exact"].append(y * h_x)
        out["s_tabsq6"].append(s_n)
        out["s_exact"].append(s_x)
        out["yield"].append(y)
    return out


# ejectile cluster masses m_b (neutron masses, mass numbers) of the non-emulated MTs, as ENDF defines the reactions
C1_CLUSTER_MASS = {91: 1, 103: 1, 104: 2, 105: 3, 106: 3, 107: 4, 108: 8, 109: 12, 111: 2, 112: 5, 113: 11, 114: 10,
                   115: 3, 116: 4, 117: 6}


def cluster_mass(mt):
    """m_b of MT `mt`: 600-649 proton, 650-699 deuteron, 700-749 triton, 750-799 He-3, 800-849 alpha (the parent channel's
    ejectile) and the table above for MT=91 and 103-117."""
    for lo, mass in ((600, 1), (650, 2), (700, 3), (750, 3), (800, 4)):
        if lo <= mt < lo + 50:
            return mass
    if mt in C1_CLUSTER_MASS:
        return C1_CLUSTER_MASS[mt]
    raise ValueError(f"no ejectile cluster mass known for MT={mt}")


def recoil_tmax(e, awr, m_b, qi):
    """Largest laboratory kinetic energy of the residual nucleus (Amendment C1), all ejectiles one cluster of mass m_b
    (neutron masses), m_R = A + 1 - m_b: (sqrt(m_b/(m_b+m_R) (E A/(A+1) + QI)) + sqrt(E m_R/(A+1)^2))^2, zero where
    E A/(A+1) + QI <= 0."""
    available = e * awr / (awr + 1.0) + qi
    if available <= 0.0:
        return 0.0
    m_r = awr + 1.0 - m_b
    return (math.sqrt(m_b / (m_b + m_r) * available) + math.sqrt(e * m_r / (awr + 1.0) ** 2)) ** 2


def unemulated_bound(tabs, mts, boundaries, awr):
    """Amendment C1: U, the collapse of sum over `mts` (those present in `tabs`) of sigma(E) T_max(E). Returns
    (groups, {MT: m_b} of the MTs used)."""
    used = {mt: cluster_mass(mt) for mt in mts if mt in tabs}
    total = [0.0] * (len(boundaries) - 1)
    for mt, m_b in used.items():
        t = tabs[mt]
        weighted = {"x": t["x"], "y": [y * recoil_tmax(x, awr, m_b, t["qi"]) for x, y in zip(t["x"], t["y"])],
                    "ranges": t["ranges"]}
        total = [a + b for a, b in zip(total, njoy.collapse(weighted, boundaries))]
    return total, used


def b_precondition(emulated, njoy_values, bound, tol=B_PRECONDITION_TOL, bounds=None, mts=None,
                   min_groups=C1_MIN_GROUPS, above=None):
    """Amendment C1: the set is the groups where NJOY's value is positive, the independent bound U on the terms not
    emulated is at most C1_BOUND_FRACTION of it, and (B2) the group's upper edge exceeds `above`; in every group of the
    set NJOY (1 - tol) - U <= emulated <= NJOY (1 + tol). Fewer than `min_groups` groups is vacuous and fails.
    Returns the values reported; `deviation` is signed (emulated - NJOY)/NJOY."""
    rows, bad, ratios = [], [], []
    for g, (c, n, u) in enumerate(zip(emulated, njoy_values, bound)):
        if not (n > 0.0 and u <= C1_BOUND_FRACTION * n):
            continue
        if above is not None and max(bounds[g], bounds[g + 1]) <= above:
            continue
        rows.append((c - n) / n)
        ratios.append(u / n)
        if not (n * (1.0 - tol) - u <= c <= n * (1.0 + tol)):
            bad.append({"group": g, "lo_eV": min(bounds[g], bounds[g + 1]) if bounds else None, "emulated": c,
                        "njoy": n, "bound": u, "deviation": (c - n) / n})
    vacuous = len(rows) < min_groups
    worst = max(rows, key=abs, default=0.0)
    return {"set_size": len(rows), "worst_deviation": worst, "worst_rel": abs(worst), "n_bad": len(bad),
            "bad": bad[:20], "distribution": distribution([abs(r) for r in rows]), "tolerance": tol,
            "bound_fraction": C1_BOUND_FRACTION, "bound_over_njoy_max": max(ratios, default=None),
            "bound_mts_cluster_mass": dict(mts or {}), "min_groups": min_groups, "above_eV": above,
            "pass": not vacuous and not bad, "vacuous": vacuous}


def true_grid(tab, grid):
    """HEATR's MT=444-447 tables carry energies rounded to 7 digits while their values were computed at the 9-digit
    RECONR grid energy (a resonance peak sampled at 149829.35 eV is written at 149829.4), and a table starts at its
    first nonzero row (MT=445 of Fe-56 at 575 eV, the zero row at 550 eV, from which 444 ramps up, is dropped). The
    tables run from some grid index to the end of the grid, so when the tail and the rounded energies agree to 1e-6
    the energies are restored from `grid` and the dropped grid row is restored as zero; otherwise the table is
    returned as read. Returns (table, restored)."""
    n = len(tab["x"])
    if n > len(grid) or n < 2:
        return tab, False
    tail = grid[len(grid) - n:]
    if any(abs(a - b) > 1e-6 * abs(b) for a, b in zip(tail, tab["x"])):
        return tab, False
    x, y = list(tail), list(tab["y"])
    if len(grid) > n:
        x.insert(0, grid[len(grid) - n - 1])
        y.insert(0, 0.0)
    return dict(tab, x=x, y=y, ranges=[(len(x), 2)]), True


def capture_reference(endf_path, pendf_path, boundaries, ed, min_groups=C1_MIN_GROUPS):
    """B1. The disappearance reference: NJOY MT=447 + sigma_102 (h_exact - h_tabsq6) pointwise on NJOY's PENDF grid
    (and at the MF=6 record energies), collapsed flat in lethargy. Without MF=6 MT=102 photons the reference is raw
    MT=447. Returns {"applicable", "groups", "emulated_groups", "precondition", "info"}."""
    mf6 = read_mf6_photons(endf_path)
    tabs = njoy.read_pendf(pendf_path, mts=(1, 102, 447) + C1_OTHER_447)
    if 447 not in tabs or 1 not in tabs:
        raise GateError("the PENDF carries no MF=3 MT=447 or MT=1")
    tabs[447], restored = true_grid(tabs[447], tabs[1]["x"])
    raw = njoy.collapse(tabs[447], boundaries)
    if mf6 is None:
        return {"applicable": False, "groups": raw, "raw_groups": raw,
                "info": {"note": "no MF=6 MT=102 photon subsection: raw MT=447"}}
    if 102 not in tabs:
        raise GateError("MF=6 MT=102 photons present but the PENDF carries no MF=3 MT=102")
    _mat, za, awr = p126.endf_mat_and_za(endf_path)
    recs = capture_records(mf6, za // 1000, awr, ed)
    sig = tabs[102]
    xs = sorted(set(sig["x"]) | set(tabs[447]["x"]) | {e for e in recs["energies"] if sig["x"][0] <= e <= sig["x"][-1]})
    nj, ex, adj, emu = [], [], [], []
    for x in xs:
        s = interp_value(sig, x)
        h_n = record_interpolant(recs["energies"], recs["tabsq6"], x)
        h_x = record_interpolant(recs["energies"], recs["exact"], x)
        nj.append(s * h_n)
        ex.append(s * h_x)
        adj.append(interp_value(tabs[447], x) + s * (h_x - h_n))
    rng = [(len(xs), 2)]
    emulated = njoy.collapse({"x": xs, "y": nj, "ranges": rng}, boundaries)
    exact = njoy.collapse({"x": xs, "y": ex, "ranges": rng}, boundaries)
    groups = njoy.collapse({"x": xs, "y": adj, "ranges": rng}, boundaries)
    bound, used = unemulated_bound(tabs, C1_OTHER_447, boundaries, awr)
    pre = b_precondition(emulated, raw, bound, bounds=boundaries, mts=used, min_groups=min_groups)
    first = 0                          # the 1e-5 eV record
    info = {"lep": mf6["lep"], "lang": mf6["lang"], "n_records": len(recs["energies"]),
            "record_energies_eV": [recs["energies"][0], recs["energies"][-1]],
            "per_capture_damage_eV_first_record": {"tabsq6_emulation": recs["tabsq6"][first],
                                                   "exact_format": recs["exact"][first],
                                                   "yield": recs["yield"][first]},
            "normalisation_first_record": {"tabsq6_s": recs["s_tabsq6"][first], "exact_s": recs["s_exact"][first]},
            "worst_exact_normalisation_deviation": max(abs(v - 1.0) for v in recs["s_exact"]),
            "grid_points": len(xs), "mt447_energies_restored_from_grid": restored}
    return {"applicable": True, "groups": groups, "raw_groups": raw, "emulated_groups": emulated, "exact_groups": exact,
            "precondition": pre, "info": info}


def inelastic_w(pdf, e, awr, z, ed, thresh, damage=None):
    """Exact mean damage energy per inelastic event of a discrete level at incident energy `e` (eV), the integral over
    the centre-of-mass cosine of pdf(mu) R(T(mu)) with R the damage function (0 below `ed`). The residual nucleus of a
    two-body reaction with reaction threshold `thresh` = -Q (A+1)/A has T = A E (1 + r^2 - 2 r mu)/(A+1)^2,
    r = sqrt(1 - thresh/E) (r = 0 at and below the threshold, as disbar sets it); r = 1 is elastic scattering. The
    integral is cut where T = ed and at the NRT breakpoint T = 2 ed / 0.8 and at every pdf panel edge."""
    breaks, fn = pdf
    if damage is None:
        def damage(t):
            return df(t, z, awr, z, awr, ed)
    r = 0.0 if thresh >= e * (1.0 - LADDER_SMALL) else math.sqrt(1.0 - thresh / e)
    c = awr * e / (awr + 1.0) ** 2
    if r == 0.0:
        return damage(c)

    def cosine_at(t):                 # T = t at this cosine (T falls as mu rises)
        return (1.0 + r * r - t / c) / (2.0 * r)

    top = cosine_at(ed)
    if not top > -1.0:
        return 0.0
    top = min(1.0, top)
    cuts = [cosine_at(2.0 * ed / 0.8)] + list(breaks)
    edges = [-1.0] + sorted({v for v in cuts if -1.0 < v < top}) + [top]

    def integrand(mu):
        return fn(mu) * damage(c * (1.0 + r * r - 2.0 * r * mu))

    return sum(adaptive_gl(integrand, lo, hi, LEVEL_TOL) for lo, hi in zip(edges, edges[1:]) if hi > lo)


def part_pdf(part, i):
    """The pdf of record i of an MF=4 part as (breaks, f)."""
    if part["kind"] == "legendre":
        return legendre_pdf(part["data"][i])
    breaks, fn, _dev = tabulated_pdf(part["data"][i])
    return breaks, fn


def mf4_pdf_at(mf4, e):
    """The MF=4 pdf at incident energy `e`: isotropic below the first energy (as hgtfle returns), the last record's
    above the last, linear pdf mixing between records (INT=2; INT=1 holds the lower pdf; other laws are refused)."""
    parts = mf4["parts"]
    if not parts:
        return legendre_pdf([])
    part = parts[0] if len(parts) == 1 or e <= parts[0]["energies"][-1] else parts[1]
    es = part["energies"]
    if e < es[0] * (1.0 - LADDER_SMALL):
        return legendre_pdf([])
    j = bisect.bisect_right(es, e) - 1
    if j >= len(es) - 1:
        return part_pdf(part, len(es) - 1)
    law = njoy.law_of_segment(part["ranges"], j)
    if law not in (1, 2):
        raise ValueError(f"MF=4 energy interpolation law INT={law} is not handled")
    if law == 1 or e == es[j]:
        return part_pdf(part, j)
    return mixed_pdf(part_pdf(part, j), part_pdf(part, j + 1), (e - es[j]) / (es[j + 1] - es[j]))


def mf4_ladder_energies(mf4):
    """(sorted MF=4 incident energies of the first part, isotropic): hgtfle returns etop for the next energy when the
    level is isotropic (no parts, or two energies both without coefficients)."""
    parts = mf4["parts"]
    if not parts:
        return [], True
    part = parts[0]
    es = part["energies"]
    iso = part["kind"] == "legendre" and len(es) == 2 and all(len(c) == 0 for c in part["data"])
    return list(es), iso


def disbar_ladder(grid, mf4_energies, iso, step=LADDER_STEP, small=LADDER_SMALL, etop=LADDER_ETOP):
    """The incident energies at which disbar (heatr.f90:1829-2013, normal entry from label `if (ee.le.en*(1+small))`)
    evaluates the damage, for the ascending energies `grid` that HEATR loops over (from the reaction threshold up):
    a point lies at the grid energy when it is beyond the step, otherwise at step times the previous point or the next
    MF=4 incident energy (hgtfle's `enext`), whichever is lower."""
    def enext_after(e):
        if iso or not mf4_energies:
            return etop
        j = 1
        while j < len(mf4_energies) - 1 and mf4_energies[j] <= e * (1.0 + small):
            j += 1
        return mf4_energies[min(j, len(mf4_energies) - 1)]

    points, en = [], 0.0
    enext = mf4_energies[0] if (mf4_energies and not iso) else etop
    for ee in grid:
        if ee <= en * (1.0 + small):
            continue
        e = step * en
        if enext < e * (1.0 - small):
            e = enext
        if ee > e * (1.0 + small):
            e = ee
        points.append(e)
        en = e
        enext = enext_after(e)
    return points


def level_w_tables(mf4, awr, z, ed, thresh, grid):
    """For one discrete level: (W_exact table, W_ladder table, ladder points). W_exact is the exact damage per event on
    the MF=4 energies refined to a 5 percent ratio, the reaction threshold and the ladder points; W_ladder is W_exact
    at the ladder points only, linear in E between them (disbar's label 130)."""
    mf4_energies, iso = mf4_ladder_energies(mf4)
    ladder = disbar_ladder(grid, mf4_energies, iso)
    energies = set(ladder)
    for part in mf4["parts"][:1]:
        es = part["energies"]
        for e0, e1 in zip(es, es[1:]):
            if e1 <= thresh or not e1 > e0:
                continue
            pieces = max(1, int(math.ceil(math.log(e1 / e0) / math.log(LEVEL_REFINE)))) if e0 > 0.0 else 1
            energies.update(e0 * (e1 / e0) ** (k / pieces) if e0 > 0.0 else e0 for k in range(pieces + 1))
    energies.add(thresh)
    energies = sorted(e for e in energies if e >= thresh * (1.0 - LADDER_SMALL))
    values = {e: inelastic_w(mf4_pdf_at(mf4, e), e, awr, z, ed, thresh) for e in energies}
    exact = {"x": energies, "y": [values[e] for e in energies], "ranges": [(len(energies), 2)]}
    lad = {"x": ladder, "y": [values[e] for e in ladder], "ranges": [(max(len(ladder), 2), 2)]}
    return exact, lad, ladder


def fine_points(grid, ratio):
    """Energies subdividing every gap of the ascending `grid` wider than `ratio` into geometric steps of at most it."""
    extra = []
    for a, b in zip(grid, grid[1:]):
        if a > 0.0 and b > a * ratio:
            n = int(math.ceil(math.log(b / a) / math.log(ratio)))
            extra.extend(a * (b / a) ** (k / n) for k in range(1, n))
    return extra


def inelastic_reference(endf_path, pendf_path, boundaries, ed, fine=LEVEL_FINE_RATIO, min_groups=C1_MIN_GROUPS):
    """B2. The inelastic reference for G4: NJOY MT=446 with the discrete levels' ladder term replaced by the exact
    one, collapsed flat in lethargy. At every PENDF grid energy it is 446 + sum over the levels MT=51-90 of
    sigma (W_exact - W_ladder). HEATR tabulates the levels' sigma W_ladder only at the PENDF grid energies (linear in E
    between them, a coarse interpolation of a product near a threshold, where sigma and W both rise), so between grid
    energies the reference is the tabulated remainder (446 minus the levels' tabulated term, interpolated) plus
    sigma(E) W_exact(E) on energies subdivided to a ratio of `fine`. `fine = None` gives the pointwise formula on the
    grid and ladder energies only. Returns {"groups", "groups_grid_only", "ladder_groups", "exact_groups",
    "precondition", "info"}; the precondition applies in the groups where the levels carry at least 99 percent of
    MT=446 and some level is within a factor 2 of its threshold; `ladder_groups` is the collapse of what HEATR's
    table holds for the levels: sigma W_ladder at the PENDF grid energies."""
    _mat, za, awr = p126.endf_mat_and_za(endf_path)
    z = za // 1000
    tabs = njoy.read_pendf(pendf_path, mts=(1, 446) + LEVEL_MTS + C1_OTHER_446)
    if 446 not in tabs or 1 not in tabs:
        raise GateError("the PENDF carries no MF=3 MT=446 or MT=1")
    tabs[446], restored = true_grid(tabs[446], tabs[1]["x"])
    levels = [mt for mt in LEVEL_MTS if mt in tabs]
    raw = njoy.collapse(tabs[446], boundaries)
    mf4s = read_mf4_many(endf_path, levels)
    iso_default = {"za": za, "awr": awr, "lct": 2, "li": 1, "ltt": 0, "parts": []}
    grid446 = list(tabs[446]["x"])
    per_level = {}
    pts_all, extra = set(grid446), set()
    for mt in levels:
        sig = tabs[mt]
        mf4 = mf4s.get(mt, iso_default)
        if mf4["lct"] not in (1, 2):
            raise ValueError(f"MT={mt}: MF=4 LCT={mf4['lct']} is neither laboratory nor centre of mass")
        thresh = (awr + 1.0) / awr * (-sig["qi"])
        grid = sig["x"]                 # HEATR loops over the PENDF grid from the reaction's first energy
        exact, lad, ladder = level_w_tables(mf4, awr, z, ed, thresh, grid)
        pts = sorted(set(grid) | set(ladder))
        pts_all.update(pts)
        if fine:
            extra.update(fine_points(grid, fine))
        per_level[mt] = {"sigma": sig, "exact": exact, "ladder": lad, "first": pts[0], "thresh": thresh,
                         "n_ladder": len(ladder)}

    def level_terms(x):
        """(sum sigma W_ladder, sum sigma W_exact) over the levels at `x`."""
        lad_sum = ex_sum = 0.0
        for d in per_level.values():
            if x < d["first"]:
                continue
            sg = interp_value(d["sigma"], x)
            if sg != 0.0:
                lad_sum += sg * interp_value(d["ladder"], x)
                ex_sum += sg * interp_value(d["exact"], x)
        return lad_sum, ex_sum

    def tab(xs, ys):
        return {"x": xs, "y": ys, "ranges": [(len(xs), 2)]}

    at_grid = [level_terms(x) for x in grid446]                      # HEATR's own tabulated levels term
    ladder_groups = njoy.collapse(tab(grid446, [t[0] for t in at_grid]), boundaries)
    exact_groups = njoy.collapse(tab(grid446, [t[1] for t in at_grid]), boundaries)
    xs_lit = sorted(pts_all)
    y_lit = []
    for x in xs_lit:
        lad_sum, ex_sum = level_terms(x)
        y_lit.append(interp_value(tabs[446], x) + ex_sum - lad_sum)
    groups_lit = njoy.collapse(tab(xs_lit, y_lit), boundaries)
    groups = groups_lit
    if fine:
        other = tab(grid446, [interp_value(tabs[446], x) - t[0] for x, t in zip(grid446, at_grid)])
        xs_fine = sorted(pts_all | extra)
        y_fine = [interp_value(other, x) + level_terms(x)[1] for x in xs_fine]
        groups = njoy.collapse(tab(xs_fine, y_fine), boundaries)
    bound, used = unemulated_bound(tabs, C1_OTHER_446, boundaries, awr)
    lowest = min(per_level[mt]["thresh"] for mt in per_level) if per_level else None
    pre = b_precondition(ladder_groups, raw, bound, bounds=boundaries, mts=used, min_groups=min_groups, above=lowest)
    info = {"levels": levels, "ladder_points": {str(mt): d["n_ladder"] for mt, d in per_level.items()},
            "thresholds_eV": {str(mt): d["thresh"] for mt, d in per_level.items()},
            "fine_ratio": fine,
            "grid_points": len(grid446), "mt446_energies_restored_from_grid": restored}
    return {"groups": groups, "raw_groups": raw, "groups_grid_only": groups_lit, "ladder_groups": ladder_groups,
            "exact_groups": exact_groups, "precondition": pre, "info": info}


# ------------------------------------------------------------------------------ continuum recoil determination


def _skip_law(lines, index, law):
    """Index after the angular-energy data of an MF=6 subsection, or None for a law this reader does not skip."""
    if law in (0, 3, 4):
        return index
    if law in (1, 2):
        _a, _b, _l1, _l2, nr, ne = p126.record(lines, index)
        _, index = p126.data_words(lines, index + 1, 2 * nr)
        for _ in range(ne):
            nw = p126.record(lines, index)[4]
            _, index = p126.data_words(lines, index + 1, nw)
        return index
    if law == 6:
        return index + 1
    if law == 7:
        _a, _b, _l1, _l2, nr, ne = p126.record(lines, index)
        _, index = p126.data_words(lines, index + 1, 2 * nr)
        for _ in range(ne):
            _a, _b, _l1, _l2, nr2, nmu = p126.record(lines, index)
            _, index = p126.data_words(lines, index + 1, 2 * nr2)
            for _ in range(nmu):
                nw = p126.record(lines, index)[4]
                _, index = p126.data_words(lines, index + 1, nw)
        return index
    return None


def mf6_subsections(lines):
    """[(ZAP, LAW)] of an MF=6 section and whether every subsection could be walked."""
    _za, _awr, _jp, _lct, nk, _ = p126.record(lines, 0)
    index, subs = 1, []
    for _ in range(nk):
        zap, _awp, _lip, law, nr, npts = p126.record(lines, index)
        _, index = p126.data_words(lines, index + 1, 2 * nr)
        _, index = p126.data_words(lines, index, 2 * npts)
        subs.append((int(round(zap)), law))
        index = _skip_law(lines, index, law)
        if index is None:
            return subs, False
    return subs, True


CONTINUUM_MTS = (91, 649, 699, 749, 799, 849)


def njoy_continuum_recoil(path):
    """Which continuum reactions of the evaluation carry a recoil subsection in MF=6, read from the ENDF file. NJOY
    HEATR routes every MF=6 reaction through sixbar, which takes the damage from the residual-nucleus recoil
    subsection (LAW=4, or ZAP above 2004 for a residual nucleus) where there is one and computes it from the emitted
    particle otherwise (heatr.f90:2755-2860, 3157). Returns the record kept in the output; `mode` is the
    `--continuum-recoil` value to build with: `evaluated` when any continuum MT (91, 649, 699, 749, 799, 849) has a
    recoil subsection, `kinematic` otherwise."""
    per_mt = {}
    for _mat, mf, mt, lines in p126.sections(path):
        if mf != 6 or not (mt == 91 or 600 <= mt <= 849):
            continue
        try:
            subs, complete = mf6_subsections(lines)
        except (IndexError, ValueError) as error:
            per_mt[str(mt)] = {"error": f"{type(error).__name__}: {error}", "recoil_subsection": False}
            continue
        per_mt[str(mt)] = {"subsections_zap_law": subs, "walked_completely": complete,
                           "recoil_subsection": any(law == 4 or zap > 2004 for zap, law in subs)}
    evaluated = any(per_mt.get(str(mt), {}).get("recoil_subsection") for mt in CONTINUUM_MTS)
    return {"per_mt": per_mt, "continuum_mts_with_recoil": sorted(int(k) for k, v in per_mt.items()
                                                                  if int(k) in CONTINUUM_MTS
                                                                  and v.get("recoil_subsection")),
            "njoy_treatment": "evaluated recoil subsection" if evaluated else "emitted-particle kinematics",
            "mode": "evaluated" if evaluated else "kinematic"}


# ------------------------------------------------------------------------------ building tables and NJOY references


def njoy_capture_treatment(path):
    """What NJOY HEATR does with MT=102 of this evaluation, read from the ENDF file itself: an MF=6 MT=102 section
    routes the reaction to sixbar -> tabsq6 (each photon's recoil partitioned separately: `per-photon`), otherwise to
    capdam (one recoil from the incident momentum and one photon: `one-photon`). Returns the record kept in the
    output; `mode` is the `--capture-photons` value to build with."""
    sections = 0
    head = None
    with open(path, "r", errors="replace") as handle:
        for line in handle:
            if len(line) >= 75 and line[70:72] == " 6" and line[72:75] == "102":
                sections += 1
                if head is None and line[75:80].strip() == "1":
                    head = line
    has = sections > 0
    record = {"has_mf6_mt102": has, "mf6_mt102_records": sections,
              "njoy_treatment": "per-photon" if has else "one-photon",
              "njoy_routine": "sixbar/tabsq6 (heatr.f90 j6>0, icon=-1)" if has else "capdam (heatr.f90 icon=0)",
              "mode": "per-photon" if has else "one-photon"}
    if head is not None:
        record["head_NK"] = int(head[44:55].strip() or 0)
    return record


def build_args(target, ed_map, capture, grid_density, continuum="kinematic"):
    info = TARGETS[target]
    args = ["--method", "kinematic", "--groups", "fispact-709", "--projectile", "neutron", "--capture-photons",
            capture, "--displacement-energy", f"{info['element']}={ed_map[info['element']]:g}"]
    if continuum != "kinematic":     # `kinematic` is the build-damage default and is not passed
        args += ["--continuum-recoil", continuum]
    if grid_density:
        args += ["--grid-density", str(grid_density)]
    return args


def build_table(binary, target, work, ed_map, capture, grid_density, timeout, reuse, continuum="kinematic"):
    out = work / f"table2_{target}_{capture}_{continuum}.json"
    if not (reuse and out.exists()):
        if out.exists():
            out.unlink()
        code, stdout, err = sh([binary, "build-damage", TENDL_DIR / TARGETS[target]["file"], out]
                               + build_args(target, ed_map, capture, grid_density, continuum), timeout)
        if code != 0 or not out.exists():
            raise GateError(f"build-damage {target} ({capture}, continuum {continuum}) failed: {err.strip()[-400:]}")
    return json.loads(out.read_text()), out


def check_table_structure(table, target, ed_map, bounds):
    """Format-level facts every built table must satisfy; returns a list of violations."""
    info = TARGETS[target]
    el = info["element"]
    bad = []
    if table.get("format") != "actinv-damage-table-2":
        bad.append(f"format {table.get('format')!r}")
    got = [float(b) for b in table.get("boundaries_eV", [])]
    if sorted(got) != bounds or len(got) != 710:
        bad.append("boundaries_eV differ from the 709-group structure")
    if table.get("projectile") != "neutron":
        bad.append(f"projectile {table.get('projectile')!r}")
    if table.get("method") not in (None, "kinematic"):
        bad.append(f"method {table.get('method')!r}")
    disp = table.get("displacement", {})
    if disp.get("displacement_energy_eV", {}).get(el) != ed_map[el]:
        bad.append(f"displacement_energy_eV[{el}] = {disp.get('displacement_energy_eV')}")
    if target not in table.get("targets", {}):
        bad.append(f"no target row {target}")
        return bad
    row = table["targets"][target]
    for key in ("damage_energy_barn_eV", "elastic", "inelastic", "disappearance", "uncovered_xs_barn",
                "uncovered_damage_upper_barn_eV", "displacement_nrt_barn"):
        values = row.get(key)
        if not isinstance(values, list) or len(values) != 709:
            bad.append(f"row key {key} missing or not 709 long")
        elif any(not math.isfinite(v) or v < 0.0 for v in values):
            bad.append(f"row key {key} has a negative or non-finite value")
    if el in NORDLUND_TABLE_1 and "displacement_arc_barn" not in row:
        bad.append("displacement_arc_barn missing for an element with arc-dpa constants")
    if el not in NORDLUND_TABLE_1 and "displacement_arc_barn" in row:
        bad.append("displacement_arc_barn present for an element without arc-dpa constants")
    return bad


def arc_parameter_check(table):
    """Every arc-dpa constant in a table against this script's Nordlund transcription (exact JSON equality)."""
    params = table.get("displacement", {}).get("arc_dpa", {}).get("parameters", {})
    problems = []
    for el, entry in params.items():
        if el not in NORDLUND_TABLE_1:
            problems.append(f"{el}: not an element of Nordlund Table 1")
            continue
        ed, b, c = NORDLUND_TABLE_1[el]
        if entry.get("b") != b or entry.get("c") != c or entry.get("reference_displacement_energy_eV") != ed:
            problems.append(f"{el}: table {entry} vs Nordlund (E_d {ed}, b {b}, c {c})")
    citation = table.get("displacement", {}).get("arc_dpa", {}).get("citation", "")
    if "Nordlund" not in citation:
        problems.append(f"citation does not name Nordlund: {citation!r}")
    return {"elements": sorted(params), "problems": problems}


def exact_elastic_cached(target, work, record, ed, boundaries, reuse):
    """exact_elastic_reference for one target; a cached result is reused with --reuse when the tape and ed match."""
    cache = work / f"exact_elastic_{target}.json"
    key = {"pendf_sha256": record.get("pendf_sha256"), "endf_sha256": record["endf"]["sha256"], "ed_eV": ed}
    if reuse and cache.exists():
        saved = json.loads(cache.read_text())
        if saved.get("key") == key:
            return saved["result"]
    result = exact_elastic_reference(record["endf"]["path"], record["pendf"], boundaries, ed)
    cache.write_text(json.dumps({"key": key, "result": result}))
    return result


def b_reference_cached(kind, target, work, record, ed, boundaries, reuse):
    """capture_reference ("b1") or inelastic_reference ("b2") for one target, cached like exact_elastic_cached."""
    cache = work / f"{kind}_{target}.json"
    key = {"pendf_sha256": record.get("pendf_sha256"), "endf_sha256": record["endf"]["sha256"], "ed_eV": ed,
           "version": B_VERSION}
    if reuse and cache.exists():
        saved = json.loads(cache.read_text())
        if saved.get("key") == key:
            return saved["result"]
    fn = capture_reference if kind == "b1" else inelastic_reference
    result = fn(record["endf"]["path"], record["pendf"], boundaries, ed)
    cache.write_text(json.dumps({"key": key, "result": result}))
    return result


def njoy_reference(target, work, ed_map, njoy_exe, err, timeout, reuse):
    info = TARGETS[target]
    record = njoy.run_njoy(TENDL_DIR / info["file"], work / f"njoy_{target}", ed_map[info["element"]], njoy_exe, err,
                           timeout, reuse)
    if "pendf" not in record:
        raise GateError(f"NJOY {target}: {record.get('error')}")
    tabs = njoy.read_pendf(record["pendf"])
    if set(tabs) != set(njoy.DAMAGE_MTS):
        raise GateError(f"NJOY {target}: PENDF carries MT {sorted(tabs)}")
    return record, tabs


def gates_g3_g4_g5(candidate, work, args, bounds, spec):
    """Builds, NJOY runs and the three comparisons, per target. Returns (g3, g4, g5, g6_tables, evidence)."""
    ed_map = dict(BUILD_ED)
    if args.ed_uniform:
        ed_map = {k: args.ed_uniform for k in ed_map}
    g3, g4, g5, evidence, tables = {"cases": {}}, {"cases": {}}, {"cases": {}}, {"targets": {}}, {}
    g4["uncovered_rule"] = (f"a group's uncovered contribution is present only when uncovered_damage_upper_barn_eV "
                            f"> {G4_UNCOVERED_FRACTION:g} x the channel's computed value in that group (one tenth of "
                            f"the G4 tolerance); excluded groups are reported")
    g4["note"] = ("tables compared with NJOY are built in NJOY's treatments: capture per-photon where MF=6 MT=102 "
                  "exists, continuum recoil `evaluated` where an MF=6 continuum MT carries a recoil subsection; the "
                  "determinations are in evidence.targets.<T>.capture_determination / continuum_determination")
    g4["note_b"] = ("Amendment B: the references are NJOY MT=447 with the capture-photon term replaced (B1) and NJOY "
                    "MT=446 with the discrete-level ladder term replaced (B2, fine-grid form per C2), each with the "
                    "Amendment C1 precondition (groups chosen by the recoil bound U <= 0.5 percent of NJOY, "
                    "NJOY(1-0.005)-U <= emulated <= NJOY(1+0.005), at least 10 groups) that the checker's emulation of the HEATR routine "
                    "reproduces NJOY; both are part of the gate, the raw comparisons are in raw_njoy_information")
    g5["note"] = ("B3: NJOY MT=444 with MT=445 (leading zero row restored) replaced by the exact elastic reference "
                  "(A1) and the B1 and B2 differences added; the 2 percent term of the slack uses the exact elastic "
                  "reference and the 5 percent term the B2 and B1 references; the raw NJOY444 bound and the A3 form "
                  "(444 - 445 + exact elastic) are information")
    evidence["njoy_documented_approximations"] = njoy_documented_approximations()
    refs, modes = {}, {}
    for target in TARGETS:
        entry = evidence["targets"].setdefault(target, {})
        try:
            endf = TENDL_DIR / TARGETS[target]["file"]
            capture = njoy_capture_treatment(endf)
            continuum = njoy_continuum_recoil(endf)
            entry["capture_determination"] = capture
            entry["continuum_determination"] = continuum
            modes[target] = (capture["mode"], continuum["mode"])
            record, tabs = njoy_reference(target, work, ed_map, args.njoy, args.njoy_err, args.timeout, args.reuse)
            entry["njoy"] = {k: v for k, v in record.items() if k not in ("deck",)}
            entry["njoy_deck"] = record["deck"]
            asc = vendored_boundaries()
            ref = {mt: njoy.collapse(tabs[mt], asc) for mt in njoy.DAMAGE_MTS}
            entry["njoy_group_values_sha256"] = hashlib.sha256(json.dumps(ref, sort_keys=True).encode()).hexdigest()
            exact = exact_elastic_cached(target, work, record, ed_map[TARGETS[target]["element"]], asc, args.reuse)
            entry["exact_elastic"] = exact["info"]
            ed_t = ed_map[TARGETS[target]["element"]]
            b1 = b2 = b_error = None
            try:
                b1 = b_reference_cached("b1", target, work, record, ed_t, asc, args.reuse)
                b2 = b_reference_cached("b2", target, work, record, ed_t, asc, args.reuse)
                grid_1 = njoy.read_pendf(record["pendf"], mts=(1,))[1]["x"]
                full_445 = njoy.collapse(true_grid(tabs[445], grid_1)[0], asc)
            except (GateError, KeyError, ValueError, OSError, IndexError) as error:
                b_error = f"{type(error).__name__}: {error}"
            entry["amendment_b"] = {"error": b_error} if b_error else {
                "b1_capture": {k: v for k, v in b1.items() if not k.endswith("groups")},
                "b2_levels": {k: v for k, v in b2.items() if not k.endswith("groups")}}
            table, path = build_table(candidate, target, work, ed_map, modes[target][0], args.grid_density,
                                      args.timeout, args.reuse, modes[target][1])
            tables[target] = (table, path)
            entry["table"] = {"path": str(path), "sha256": sha256(path)}
            violations = check_table_structure(table, target, ed_map, bounds)
            entry["structure_violations"] = violations
            tb = [float(b) for b in table["boundaries_eV"]]
            if tb != asc:                      # the references were collapsed in ascending order; follow the table
                if tb != asc[::-1]:
                    raise GateError("table boundaries are neither the vendored ascending nor descending order")
                ref = {mt: v[::-1] for mt, v in ref.items()}
                exact = dict(exact, groups=exact["groups"][::-1])
                if not b_error:
                    full_445 = full_445[::-1]
                    b1 = {k: (v[::-1] if k.endswith("groups") else v) for k, v in b1.items()}
                    b2 = {k: (v[::-1] if k.endswith("groups") else v) for k, v in b2.items()}
            refs[target] = ref
            row = table["targets"][target]
            ref_el = exact["groups"]
            g3["cases"][target] = elastic_comparison(row["elastic"], ref_el, tb)
            g3["cases"][target]["structure_violations"] = violations
            g3["cases"][target]["pass"] = g3["cases"][target]["pass"] and not violations
            above = [rel(a, b) for g, (a, b) in enumerate(zip(ref_el, ref[445])) if b > 0.0
                     and min(tb[g], tb[g + 1]) >= G3_EMIN_EV]
            g3["cases"][target]["information"] = {
                "table_vs_njoy_445": {k: v for k, v in elastic_comparison(row["elastic"], ref[445], tb).items()
                                      if k != "bad"},
                "exact_reference_vs_njoy_445_above_1keV": distribution(above),
                "njoy_documented_approximations": "see evidence.njoy_documented_approximations"}
            uncovered = row["uncovered_damage_upper_barn_eV"]
            raw_446 = channel_comparison(row["inelastic"], ref[446], ref[444], uncovered, tb)
            raw_447 = channel_comparison(row["disappearance"], ref[447], ref[444], uncovered, tb)
            adjusted = [a - b + c for a, b, c in zip(ref[444], ref[445], ref_el)]
            raw = bound_comparison(row["damage_energy_barn_eV"], uncovered, ref[444], ref[445], ref[446], ref[447])
            a3 = bound_comparison(row["damage_energy_barn_eV"], uncovered, adjusted, ref_el, ref[446], ref[447])
            info_keys = ("n_below_computed", "n_above_upper", "pass")
            if b_error:
                g4["cases"][target] = {"error": f"Amendment B reference: {b_error}", "pass": False,
                                       "raw_njoy_information": {"inelastic_vs_446": raw_446,
                                                                "disappearance_vs_447": raw_447}}
                case = {"error": f"Amendment B reference: {b_error}", "pass": False,
                        "raw_njoy444_information": {k: raw[k] for k in info_keys},
                        "a3_information": {k: a3[k] for k in info_keys}}
                g5["cases"][target] = case
                continue
            ref_446, ref_447 = b2["groups"], b1["groups"]
            g4["cases"][target] = {
                "inelastic_vs_446": channel_comparison(row["inelastic"], ref_446, ref[444], uncovered, tb),
                "disappearance_vs_447": channel_comparison(row["disappearance"], ref_447, ref[444], uncovered, tb),
                "b2_precondition": b2["precondition"], "b1_precondition": b1["precondition"]}
            g4["cases"][target]["pass"] = all(v["pass"] for v in g4["cases"][target].values())
            g4["cases"][target]["raw_njoy_information"] = {"inelastic_vs_446": raw_446,
                                                           "disappearance_vs_447": raw_447}
            if not b2["info"].get("fine_ratio"):
                g4["cases"][target]["b2_grid_only"] = True
            # B3: NJOY 444 with 445 replaced by the exact elastic reference and the B1 and B2 differences added
            b3 = [a - f + c + (d1 - r1) + (d2 - r2) for a, f, c, d1, r1, d2, r2 in
                  zip(ref[444], full_445, ref_el, ref_447, b1["raw_groups"], ref_446, b2["raw_groups"])]
            case = bound_comparison(row["damage_energy_barn_eV"], uncovered, b3, ref_el, ref_446, ref_447)
            case["raw_njoy444_information"] = {k: raw[k] for k in info_keys}
            case["a3_information"] = {k: a3[k] for k in info_keys}
            ident = [rel(t, e + i + d) for t, e, i, d in zip(row["damage_energy_barn_eV"], row["elastic"],
                                                             row["inelastic"], row["disappearance"])]
            case["table_total_equals_partial_sum_worst_rel"] = max(ident)
            flux = spectrum_ascending(spec, tb)
            fns = {"computed": fold(row["damage_energy_barn_eV"], flux),
                   "computed_plus_upper": fold([t + u for t, u in zip(row["damage_energy_barn_eV"], uncovered)],
                                               flux),
                   "njoy_444": fold(ref[444], flux), "njoy_444_adjusted": fold(b3, flux),
                   "njoy_444_a3": fold(adjusted, flux), "reference_446_b2": fold(ref_446, flux),
                   "reference_447_b1": fold(ref_447, flux),
                   "exact_elastic_reference": fold(ref_el, flux), "njoy_445": fold(ref[445], flux),
                   "njoy_446": fold(ref[446], flux), "njoy_447": fold(ref[447], flux),
                   "table_elastic": fold(row["elastic"], flux), "table_inelastic": fold(row["inelastic"], flux),
                   "table_disappearance": fold(row["disappearance"], flux),
                   "unit": "eV per atom per second at the spec's spectrum (multiplier one)"}
            fns["relative_difference_njoy_vs_computed"] = (fns["njoy_444"] - fns["computed"]) / fns["njoy_444"] \
                if fns["njoy_444"] else None
            fns["relative_difference_adjusted_vs_computed"] = (fns["njoy_444_adjusted"] - fns["computed"]) \
                / fns["njoy_444_adjusted"] if fns["njoy_444_adjusted"] else None
            case["fns_collapsed"] = fns
            case["pass"] = case["pass"] and case["table_total_equals_partial_sum_worst_rel"] <= TOL_TABLE
            g5["cases"][target] = case
        except (GateError, KeyError, ValueError, OSError, IndexError) as error:
            for gate in (g3, g4, g5):
                gate["cases"].setdefault(target, {"pass": False, "error": f"{type(error).__name__}: {error}"})
    # information: every capture / continuum combination of interest against NJOY 446 and 447 (relative difference over
    # the groups where NJOY is positive, the G4-style comparison, and the FNS-collapsed totals); the default build
    # (`evaluated` capture, `kinematic` continuum) is always among them
    info = {}
    for target in TARGETS:
        info[target] = {"gate_mode": {"capture": modes[target][0], "continuum": modes[target][1]}
                        if target in modes else None}
        if target not in refs or target not in tables:
            info[target]["error"] = "no NJOY reference or no gate-mode table"
            continue
        tb = [float(b) for b in tables[target][0]["boundaries_eV"]]
        flux = spectrum_ascending(spec, tb)
        combos = [modes[target], ("evaluated", "kinematic"), ("one-photon", "kinematic"), ("per-photon", "kinematic")]
        for capture_mode, continuum_mode in dict.fromkeys(combos):
            label = f"capture={capture_mode},continuum={continuum_mode}"
            try:
                if (capture_mode, continuum_mode) == modes[target]:
                    tab = tables[target][0]
                else:
                    tab, _ = build_table(candidate, target, work, ed_map, capture_mode, args.grid_density,
                                         args.timeout, args.reuse, continuum_mode)
                row = tab["targets"][target]
                result = {"is_gate_mode": (capture_mode, continuum_mode) == modes[target]}
                for name, mt in (("inelastic", 446), ("disappearance", 447)):
                    values, njoy_values = row[name], refs[target][mt]
                    total = fold(values, flux)
                    g4_style = channel_comparison(values, njoy_values, refs[target][444],
                                                  row["uncovered_damage_upper_barn_eV"], tb)
                    result[name] = {
                        f"rel_vs_njoy_{mt}": distribution([rel(a, b) for a, b in zip(values, njoy_values) if b > 0.0]),
                        "fns_table": total, f"fns_njoy_{mt}": fold(njoy_values, flux),
                        "fns_relative_difference": rel(total, fold(njoy_values, flux)),
                        "g4_style": {k: g4_style[k] for k in ("evaluated_groups", "n_bad", "worst_rel", "vacuous")}}
                info[target][label] = result
            except (GateError, KeyError, ValueError, OSError) as error:
                info[target][label] = {"error": f"{type(error).__name__}: {error}"}
    evidence["mode_information"] = info
    for gate in (g3, g4, g5):
        gate["pass"] = all(c.get("pass") is True for c in gate["cases"].values()) and len(gate["cases"]) == len(TARGETS)
    g3["note"] = ("pass requires every target to have evaluated groups and none outside 2 percent; the <1 keV "
                  "distribution is reported only")
    return g3, g4, g5, tables, evidence, ed_map


# ------------------------------------------------------------------------------ G2


def gate_g2(ref, cand, work, timeout):
    cases = {}
    for label, spec in (("damage_demo", DEMO_SPEC), ("fns_fe_5min", GATE_SPEC)):
        a, b = work / f"{label}.ref.json", work / f"{label}.cand.json"
        for binary, out in ((ref, a), (cand, b)):
            code, _o, err = sh([binary, "run", spec, out], timeout)
            if code != 0:
                raise GateError(f"{binary} failed on {spec}: {err.strip()[-300:]}")
        cases[label] = {"identical_apart_from_ms": MS.sub(b'"ms":0', a.read_bytes()) == MS.sub(b'"ms":0', b.read_bytes()),
                        "spec_sha256": sha256(spec)}
    outs = {}
    for label, binary in (("ref", ref), ("cand", cand)):
        out = work / f"p23_corpus.{label}.json"
        if out.exists():
            out.unlink()
        code, stdout, err = sh([binary, "build-damage", P23_CORPUS, out, "--projectile", "neutron"], timeout)
        if code != 0:
            raise GateError(f"{label} build-damage on the P23 corpus failed: {err.strip()[-300:]}")
        outs[label] = (out.read_bytes(), stdout.replace(str(out), "<OUT>"))
    corpus_files = {p.name: sha256(p) for p in sorted(P23_CORPUS.iterdir()) if p.is_file()}
    cases["build_damage_default_p23_corpus"] = {
        "output_byte_identical": outs["ref"][0] == outs["cand"][0],
        "stdout_identical": outs["ref"][1] == outs["cand"][1], "corpus_files": corpus_files,
        "output_sha256": hashlib.sha256(outs["cand"][0]).hexdigest()}
    for case in cases.values():
        case["pass"] = bool(case.get("identical_apart_from_ms", True) and case.get("output_byte_identical", True))
    ok = all(c["pass"] for c in cases.values())
    return {"pass": bool(ok), "cases": cases}


# ------------------------------------------------------------------------------ G6


def close(found, name, a, b, tol, where):
    e = rel(a, b)
    if not (e <= tol):
        found.append({"what": name, "where": where, "run": a, "independent": b, "rel": e})
    return e


def find_key(step, key):
    """A per-step key in the damage block or at step level."""
    block = step.get("damage", {})
    if key in block:
        return block[key]
    return step.get(key)


def check_run_with_bounds(result, spec, bounds, row, el, ed, has_arc, atom_density=None):
    found, compared, worst = [], 0, 0.0
    flux = spectrum_ascending(spec, bounds)
    base = {
        "dpa_rate_per_s": 0.8 * fold(row["damage_energy_barn_eV"], flux) / (2.0 * ed),
        "dpa_nrt_rate_per_s": fold(row["displacement_nrt_barn"], flux),
        "damage_energy_uncovered_upper_eV_per_g_s": None,
        "dpa_uncovered_upper_bound_rate_per_s": fold(row["uncovered_damage_upper_barn_eV"], flux) / ed,
    }
    if has_arc:
        base["dpa_arc_rate_per_s"] = fold(row["displacement_arc_barn"], flux)
    if atom_density is not None:
        base["damage_energy_uncovered_upper_eV_per_g_s"] = atom_density * fold(row["uncovered_damage_upper_barn_eV"],
                                                                               flux)
    cumulative_of = {"dpa_rate_per_s": "dpa", "dpa_nrt_rate_per_s": "dpa_nrt", "dpa_arc_rate_per_s": "dpa_arc",
                     "dpa_uncovered_upper_bound_rate_per_s": "dpa_uncovered_upper_bound"}
    cumulative = {k: 0.0 for k in cumulative_of.values()}
    missing = []
    he_ratios = []
    t_run = 0.0
    for k, (step, sched) in enumerate(zip(result["steps"], spec["schedule"])):
        mult = float(sched.get("flux", 1.0))
        dt = parse_duration(sched["dt"])
        t_run += dt
        block = step.get("damage")
        if block is None:
            missing.append(f"step {k}: no damage block")
            continue
        if rel(step.get("t_s", t_run), t_run) > 1e-12:
            found.append({"what": "t_s", "where": f"step {k}", "run": step.get("t_s"), "independent": t_run})
        for rate_key, base_value in base.items():
            if base_value is None:
                continue
            expected_rate = mult * base_value
            if rate_key in cumulative_of:
                cumulative[cumulative_of[rate_key]] += expected_rate * dt
            for level, container in (("material", block), (el, block.get("elements", {}).get(el, {}))):
                if level == "material" and rate_key == "damage_energy_uncovered_upper_eV_per_g_s":
                    key_tol = TOL_ATOMS
                elif rate_key == "damage_energy_uncovered_upper_eV_per_g_s":
                    key_tol = TOL_ATOMS
                else:
                    key_tol = TOL_RUN
                if rate_key not in container:
                    missing.append(f"step {k} {level}: {rate_key}")
                    continue
                compared += 1
                worst = max(worst, close(found, rate_key, container[rate_key], expected_rate, key_tol,
                                         f"step {k} {level}"))
                if rate_key in cumulative_of:
                    ck = cumulative_of[rate_key]
                    if ck not in container:
                        missing.append(f"step {k} {level}: {ck}")
                    else:
                        compared += 1
                        worst = max(worst, close(found, ck, container[ck], cumulative[ck], TOL_RUN,
                                                 f"step {k} {level}"))
        if not has_arc:
            for container in (block, block.get("elements", {}).get(el, {})):
                if "dpa_arc" in container:
                    found.append({"what": "dpa_arc present without arc constants", "where": f"step {k}"})
        # helium per dpa (independent reading of the gas block)
        gas = step.get("gas")
        ratio = find_key(step, "he_appm_per_dpa")
        if gas is not None:
            he = gas["He3"]["appm"] + gas["He4"]["appm"]
            if rel(gas.get("He_appm", he), he) > 1e-12:
                found.append({"what": "gas He_appm vs He3+He4", "where": f"step {k}", "run": gas.get("He_appm"),
                              "independent": he})
            dpa = block.get("dpa", 0.0)
            if dpa > 0.0:
                if ratio is None:
                    missing.append(f"step {k}: he_appm_per_dpa")
                else:
                    compared += 1
                    worst = max(worst, close(found, "he_appm_per_dpa", ratio, he / dpa, TOL_RUN, f"step {k}"))
                    he_ratios.append(he / dpa)
            elif ratio not in (None, 0.0):
                found.append({"what": "he_appm_per_dpa with zero dpa", "where": f"step {k}", "run": ratio})
        else:
            missing.append(f"step {k}: no gas block")
    return {"compared": compared, "worst_rel": worst, "failures": found[:30], "n_failures": len(found),
            "missing": missing[:30], "n_missing": len(missing), "he_appm_per_dpa_last": he_ratios[-1:] or None,
            "pass": compared > 0 and not found and not missing}


def fe_atoms_per_g(isotope_mass_number):
    molar = sum(f * m for f, m in FE_ABUNDANCE.values())
    return FE_ABUNDANCE[isotope_mass_number][0] / molar * NA


def table_invariants(table, target):
    """Row-level sanity of one built table: nonnegativity and, with constants, c * nrt <= arc <= nrt."""
    problems = []
    row = table["targets"][target]
    el = TARGETS[target]["element"]
    if "displacement_arc_barn" in row and el in NORDLUND_TABLE_1:
        c = NORDLUND_TABLE_1[el][2]
        for g, (n, a) in enumerate(zip(row["displacement_nrt_barn"], row["displacement_arc_barn"])):
            if a > n * (1 + 1e-9) + 1e-300 or a < c * n * (1 - 1e-9) - 1e-300:
                problems.append({"group": g, "nrt": n, "arc": a})
    return problems[:10]


def make_run_spec(base_spec, table_path, table_sha, el, ed, composition):
    spec = absolute(base_spec)
    spec["title"] = f"P154 G6 {el}"
    spec["material"] = {"mass_g": 1.0, "basis": "wt_percent", "composition": composition}
    spec["damage"] = {"table": {"path": str(table_path), "sha256": table_sha}, "displacement_energy_eV": {el: ed}}
    spec["options"] = dict(spec["options"], gas=True, outputs=["damage", "ledger", "certificate"])
    return spec


def gate_g6(cand, work, tables, ed_map, bounds, timeout):
    base = json.loads(GATE_SPEC.read_text())
    cases = {}
    # arc constants of every built table against the own transcription
    arc = {}
    for target, (table, _path) in tables.items():
        arc[target] = arc_parameter_check(table)
        el = TARGETS[target]["element"]
        arc[target]["invariant_problems"] = table_invariants(table, target)
        arc[target]["expected_elements_present"] = (el in NORDLUND_TABLE_1) == (el in arc[target]["elements"])
        arc[target]["pass"] = (not arc[target]["problems"] and not arc[target]["invariant_problems"]
                               and arc[target]["expected_elements_present"])
    cases["arc_parameters_and_invariants"] = {"pass": all(v["pass"] for v in arc.values()), "tables": arc,
                                              "nordlund_transcription": {k: list(v) for k, v in
                                                                         NORDLUND_TABLE_1.items()}}
    plans = (("Fe56", "Fe", {"FE": 100.0}, True), ("Cr52", "Cr", {"CR": 100.0}, False))
    for target, el, composition, has_arc in plans:
        label = f"run_{target}"
        try:
            table, path = tables[target]
            tsha = sha256(path)
            spec = make_run_spec(base, path, tsha, el, ed_map[el], composition)
            spec_path = work / f"g6_{target}.spec.json"
            spec_path.write_text(json.dumps(spec))
            out = work / f"g6_{target}.out.json"
            result = run_spec(cand, spec_path, out, timeout)
            tb = [float(b) for b in table["boundaries_eV"]]
            atoms = fe_atoms_per_g(56) if target == "Fe56" else None
            case = check_run_with_bounds(result, spec, tb, table["targets"][target], el, ed_map[el], has_arc, atoms)
            led = result.get("ledger", {}).get("damage", {}).get("recoil")
            cert = result.get("certificate", {}).get("damage", {}).get("recoil")
            led_ok = (isinstance(led, dict) and led.get("table_format") == "actinv-damage-table-2"
                      and led.get("built_displacement_energy_eV", {}).get(el) == ed_map[el]
                      and "recoil_integrated" in led and "Nordlund" in json.dumps(led.get("arc_dpa", {}))
                      and (el in NORDLUND_TABLE_1) == (el in led.get("arc_dpa", {}).get("elements", [])
                                                       or el in led.get("arc_dpa", {}).get("elements", {})))
            case["ledger_recoil_ok"] = bool(led_ok)
            case["certificate_recoil_present"] = isinstance(cert, dict)
            step_keys = sorted(result["steps"][0].get("damage", {}))
            case["step0_damage_keys"] = step_keys
            case["pass"] = bool(case["pass"] and led_ok and isinstance(cert, dict))
            case["output_sha256"] = sha256(out)
            cases[label] = case
        except (GateError, KeyError, ValueError, OSError, TypeError) as error:
            cases[label] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    # mismatch refusal writes nothing
    try:
        table, path = tables["Fe56"]
        spec = make_run_spec(base, path, sha256(path), "Fe", ed_map["Fe"] + 1.0, {"FE": 100.0})
        spec_path = work / "g6_mismatch.spec.json"
        spec_path.write_text(json.dumps(spec))
        out = work / "g6_mismatch.out.json"
        if out.exists():
            out.unlink()
        code, _o, err = sh([cand, "run", spec_path, out], timeout)
        cases["displacement_energy_mismatch_refused"] = {
            "exit_code": code, "output_written": out.exists(), "stderr_tail": err.strip()[-300:],
            "message_names_rebuild": "rebuild" in err.lower() or "--displacement-energy" in err,
            "pass": code != 0 and not out.exists()}
    except (GateError, KeyError, OSError) as error:
        cases["displacement_energy_mismatch_refused"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    return {"pass": all(c.get("pass") is True for c in cases.values()), "cases": cases}


# ------------------------------------------------------------------------------ self-check


def _endf_float(x):
    mant, exp = f"{x:.6e}".split("e")
    e = int(exp)
    return f"{mant}{'+' if e >= 0 else '-'}{abs(e)}".rjust(11)


def _line(fields, mat, mf, mt, seq):
    body = "".join(f.rjust(11) if isinstance(f, str) else _endf_float(f) for f in fields)
    return body.ljust(66) + f"{mat:>4}{mf:>2}{mt:>3}{seq:>5}"


def synthetic_pendf(path, mat, sections):
    """A PENDF snippet: sections = {mt: (ranges, points[, qi])}; HEAD, TAB1 record (QM = QI = qi), ranges, points, SEND,
    MEND, TEND."""
    out = []
    for mt, (ranges, points, *qi) in sections.items():
        n = 0

        def emit(fields):
            nonlocal n
            n += 1
            out.append(_line(fields, mat, 3, mt, n))

        emit([26056.0, 55.45, "          0", "          0", "          0", "          0"])
        emit([qi[0] if qi else 0.0, qi[0] if qi else 0.0, "          0", "          0", f"{len(ranges):>11}",
              f"{len(points):>11}"])
        words = []
        for nbt, law in ranges:
            words += [f"{nbt:>11}", f"{law:>11}"]
        for i in range(0, len(words), 6):
            emit(words[i:i + 6] + ["          "] * (6 - len(words[i:i + 6])))
        words = []
        for x, y in points:
            words += [x, y]
        for i in range(0, len(words), 6):
            chunk = words[i:i + 6]
            emit(chunk + ["           "] * (6 - len(chunk)))
        out.append(_line(["           "] * 6, mat, 3, 0, 99999))
    out.append(_line(["           "] * 6, mat, 0, 0, 0))
    out.append(_line(["           "] * 6, -1, 0, 0, 0))
    Path(path).write_text("\n".join(out) + "\n")


def self_check():
    # Lindhard energy against E_L = 86.931 Z^(7/3) eV and k against 0.1337 Z^(2/3) A^(-1/2) for like atoms
    for z, a in ((26, 55.85), (74, 183.84), (28, 58.69)):
        assert abs(lindhard_energy(z, a, z, a) / (86.931 * z ** (7 / 3)) - 1) < 1e-3, z
        assert abs(lindhard_k(z, a, z, a) / (0.1337 * z ** (2 / 3) / math.sqrt(a)) - 1) < 5e-3, z
    # df: zero below the threshold, at most the recoil energy, monotone, continuous at the threshold side, no charge
    z, a = 26, 55.45
    assert df(39.9, z, a, z, a, 40.0) == 0.0 and df(40.0, z, a, z, a, 40.0) > 0.0 and df(1e3, 0, a, z, a, 40.0) == 0.0
    last = 0.0
    for e in (40.0, 100.0, 1e3, 1e4, 1e5, 1e6, 1e7):
        d = df(e, z, a, z, a, 40.0)
        assert last < d < e, (e, d)
        last = d
    assert df(40.0, z, a, z, a, 40.0) > 0.7 * 40.0      # the electronic loss is small at threshold
    assert 0.2 < df(1e6, z, a, z, a, 40.0) / 1e6 < 0.5  # and about a third at 1 MeV
    # an unlike pair is evaluated with its own constants (recoil Mn-56 in Fe-56 differs from Fe in Fe)
    assert df(1e5, 25, 55.4, 26, 55.45, 40.0) != df(1e5, 26, 55.45, 26, 55.45, 40.0)
    # NRT and arc-dpa
    ed = 40.0
    assert nrt(39.0, ed) == 0.0 and nrt(40.0, ed) == 1.0 and nrt(99.9, ed) == 1.0 and nrt(100.0, ed) == 1.0
    assert abs(nrt(1e4, ed) - 0.8 * 1e4 / 80.0) < 1e-12
    _, b, c = NORDLUND_TABLE_1["Fe"]
    assert abs(arc_dpa(100.0, ed, b, c) - 1.0) < 1e-12                # continuity at 2 E_d / 0.8
    hand = 100.0 * ((1 - c) * (1e4 / 100.0) ** b + c)                  # 0.8*1e4/80 times xi(1e4)
    assert abs(arc_dpa(1e4, ed, b, c) - hand) < 1e-12 and abs(hand - 33.82) < 0.05
    assert arc_dpa(1e6, ed, b, c) < nrt(1e6, ed) and arc_dpa(1e6, ed, b, c) > c * nrt(1e6, ed)
    # transcription equals the list the implementer reported
    reported = {"Fe": (40, -0.568, 0.286), "Cu": (33, -0.68, 0.16), "Ni": (39, -1.01, 0.23),
                "Pd": (41, -0.88, 0.15), "Pt": (42, -1.12, 0.11), "W": (70, -0.56, 0.12)}
    assert {k: tuple(v) for k, v in NORDLUND_TABLE_1.items()} == {k: tuple(map(float, v)) for k, v in reported.items()}
    # collapse: constant, linear, histogram, log-log against fine numerical integration, steps, support edges
    tab = {"x": [1.0, 100.0], "y": [5.0, 5.0], "ranges": [(2, 2)]}
    got = njoy.collapse(tab, [1.0, 10.0, 100.0, 1000.0])
    assert abs(got[0] - 5.0) < 1e-12 and abs(got[1] - 5.0) < 1e-12 and got[2] == 0.0
    tab = {"x": [1.0, 2.0], "y": [1.0, 2.0], "ranges": [(2, 2)]}  # y = E: integral y dE/E = E, mean over ln
    got = njoy.collapse(tab, [1.0, 2.0])
    assert abs(got[0] - 1.0 / math.log(2.0)) < 1e-12
    got = njoy.collapse(tab, [0.5, 1.5, 4.0])                      # partial overlap of both groups; descending too
    assert abs(got[0] - 0.5 / math.log(3.0)) < 1e-12 and abs(got[1] - 0.5 / math.log(4.0 / 1.5)) < 1e-12
    assert njoy.collapse(tab, [4.0, 1.5, 0.5]) == [got[1], got[0]] or all(
        abs(p - q) < 1e-15 for p, q in zip(njoy.collapse(tab, [4.0, 1.5, 0.5]), [got[1], got[0]]))
    for law in (1, 2, 3, 5):
        tab = {"x": [1.0, 3.0, 9.0, 50.0], "y": [2.0, 3.0, 1.0, 4.0], "ranges": [(4, law)]}
        bounds = [0.5, 2.0, 7.0, 20.0, 80.0]
        exact = njoy.collapse(tab, bounds)
        for g in range(4):
            lo, hi, total, n = bounds[g], bounds[g + 1], 0.0, 20000
            h = math.log(hi / lo) / n
            for i in range(n):
                e = lo * math.exp((i + 0.5) * h)
                total += _interp(tab, e, law) * h
            assert abs(total / math.log(hi / lo) - exact[g]) < 5e-4 * (1 + exact[g]), (law, g)
    step = {"x": [1.0, 2.0, 2.0, 4.0], "y": [1.0, 1.0, 3.0, 3.0], "ranges": [(4, 2)]}  # zero-width discontinuity
    got = njoy.collapse(step, [1.0, 2.0, 4.0])
    assert abs(got[0] - 1.0) < 1e-12 and abs(got[1] - 3.0) < 1e-12
    mixed = {"x": [1.0, 2.0, 4.0], "y": [1.0, 1.0, 3.0], "ranges": [(2, 1), (3, 2)]}  # histogram then linear
    got = njoy.collapse(mixed, [1.0, 2.0])
    assert abs(got[0] - 1.0) < 1e-12
    # PENDF reader on a synthetic snippet with two ranges and a partial last line
    with tempfile.TemporaryDirectory(prefix="p154-selfcheck-") as tmp:
        path = Path(tmp) / "pendf"
        sections = {444: ([(3, 2), (5, 1)], [(1e-5, 0.0), (40.0, 0.0), (1e3, 12.5), (1e6, 2.5e3), (2e7, 4.0e3)]),
                    445: ([(2, 2)], [(1e-5, 0.0), (2e7, 7.0)]),
                    301: ([(2, 2)], [(1e-5, 1.0), (2e7, 2.0)])}
        synthetic_pendf(path, 2631, sections)
        got = njoy.read_pendf(path)
        assert sorted(got) == [444, 445], sorted(got)
        assert got[444]["ranges"] == [(3, 2), (5, 1)] and got[444]["x"][2] == 1e3 and got[444]["y"][4] == 4.0e3
        assert abs(got[445]["y"][1] - 7.0) < 1e-12 and len(got[444]["x"]) == 5
        assert njoy.law_of_segment(got[444]["ranges"], 0) == 2 and njoy.law_of_segment(got[444]["ranges"], 1) == 2
        assert njoy.law_of_segment(got[444]["ranges"], 2) == 1 and njoy.law_of_segment(got[444]["ranges"], 3) == 1
    deck = njoy.njoy_deck(2631, 40.0)
    assert "reconr" in deck and "heatr" in deck and " 2631 4 0 0 0 0 40.0/" in deck and " 444 445 446 447/" in deck
    assert "broadr" not in deck and deck.rstrip().endswith("stop")
    assert njoy.njoy_version.__doc__
    # gate logic on synthetic arrays
    bounds = [1.0, 10.0, 1e3, 1e4, 1e5]
    ok = elastic_comparison([0.0, 5.0, 10.1, 20.0], [0.0, 5.5, 10.0, 20.0], bounds)  # sub-keV group 2 off by 10 percent
    assert ok["pass"] and ok["evaluated_groups"] == 2 and ok["below_1keV"]["n"] == 1
    bad = elastic_comparison([0.0, 5.0, 11.0, 20.0], [0.0, 5.0, 10.0, 20.0], bounds)
    assert not bad["pass"] and bad["n_bad"] == 1
    tiny = elastic_comparison([0.0, 0.0, 1.0, 20.0], [0.0, 0.0, 1e-9, 20.0], bounds)  # below 1e-6 of the maximum
    assert tiny["pass"] and tiny["evaluated_groups"] == 1
    ch = channel_comparison([1.0] * 4 + [9.0], [1.0] * 4 + [1.0], [10.0] * 5, [0.0] * 5, [1, 2, 3, 4, 5, 6])
    assert not ch["pass"] and ch["n_bad"] == 1 and ch["evaluated_groups"] == 5
    ch = channel_comparison([1.0] * 4 + [9.0], [1.0] * 4 + [1.0], [10.0] * 5, [0.0] * 4 + [1.0], [1, 2, 3, 4, 5, 6])
    assert not ch["pass"] and ch["vacuous"] and ch["with_uncovered_reported"]["n"] == 1  # 4 evaluable groups: vacuous
    ch = channel_comparison([1.0] * 5, [1.0] * 5, [10.0] * 5, [0.0] * 5, [1, 2, 3, 4, 5, 6])
    assert ch["pass"]
    # uncovered rule: a bound below 0.5 percent of the channel value is negligible, above it excludes the group
    assert abs(G4_UNCOVERED_FRACTION - 0.005) < 1e-15
    assert not uncovered_present(0.04, 9.0) and uncovered_present(0.05, 9.0) and uncovered_present(1e-30, 0.0)
    ch = channel_comparison([1.0] * 4 + [9.0], [1.0] * 4 + [1.0], [10.0] * 5, [3e-20] * 4 + [0.04], [1, 2, 3, 4, 5, 6])
    assert not ch["pass"] and ch["n_bad"] == 1 and ch["evaluated_groups"] == 5 and not ch["vacuous"], ch
    assert ch["uncovered_negligible_groups_evaluated"] == 5 and ch["with_uncovered_reported"]["n"] == 0
    ch = channel_comparison([1.0] * 4 + [9.0], [1.0] * 4 + [1.0], [10.0] * 5, [0.0] * 4 + [0.05], [1, 2, 3, 4, 5, 6])
    assert ch["vacuous"] and ch["with_uncovered_reported"]["n"] == 1 and not ch["pass"]
    # capture-mode determination from the ENDF record layout (MF=6 MT=102 -> per-photon, none -> one-photon)
    with tempfile.TemporaryDirectory(prefix="p154-selfcheck-") as tmp:
        for name, mf, mt, expect in (("with", 6, 102, "per-photon"), ("none", 6, 16, "one-photon"),
                                     ("mf3", 3, 102, "one-photon")):
            path = Path(tmp) / name
            head = _line([26056.0, 55.45, "          0", "          3", "          1", "          0"], 2631, mf, mt, 1)
            path.write_text(head + "\n" + _line(["           "] * 6, 2631, mf, mt, 2) + "\n")
            got = njoy_capture_treatment(path)
            assert got["mode"] == expect and got["has_mf6_mt102"] == (expect == "per-photon"), (name, got)
        assert got["njoy_treatment"] == "one-photon"
        assert njoy_capture_treatment(Path(tmp) / "with")["head_NK"] == 1
    bc = bound_comparison([10.0, 10.0], [5.0, 5.0], [12.0, 20.0], [1.0, 1.0], [0.0, 0.0], [0.0, 0.0])
    assert bc["n_above_upper"] == 1 and not bc["pass"]
    bc = bound_comparison([10.0, 10.0], [5.0, 5.0], [9.99, 15.0], [1.0, 1.0], [0.0, 0.0], [0.0, 0.0])
    assert bc["pass"], bc
    bc = bound_comparison([10.0], [0.0], [9.0], [1.0], [0.0], [0.0])
    assert bc["n_below_computed"] == 1
    # run-reproduction arithmetic on a synthetic table and result
    bounds_asc = [1.0, 10.0, 100.0]
    spec = {"spectrum": {"flux_per_group": [3.0, 1.0], "descending": True, "total": 8.0},
            "schedule": [{"dt": "10 s", "flux": 2.0}, {"dt": "5 s", "flux": 0.0}]}
    flux = spectrum_ascending(spec, bounds_asc)
    assert flux == [2.0, 6.0] and spectrum_ascending(spec, bounds_asc[::-1]) == [6.0, 2.0]
    row = {"damage_energy_barn_eV": [100.0, 400.0], "displacement_nrt_barn": [2.0, 3.0],
           "displacement_arc_barn": [1.5, 2.0], "uncovered_damage_upper_barn_eV": [10.0, 0.0]}
    rate = 2.0 * 0.8 * fold(row["damage_energy_barn_eV"], flux) / 80.0
    nrt_rate = 2.0 * fold(row["displacement_nrt_barn"], flux)
    arc_rate = 2.0 * fold(row["displacement_arc_barn"], flux)
    unc_rate = 2.0 * fold(row["uncovered_damage_upper_barn_eV"], flux) / 40.0
    res = {"steps": [
        {"t_s": 10.0, "damage": {"dpa_rate_per_s": rate, "dpa": rate * 10, "dpa_nrt_rate_per_s": nrt_rate,
                                 "dpa_nrt": nrt_rate * 10, "dpa_arc_rate_per_s": arc_rate, "dpa_arc": arc_rate * 10,
                                 "dpa_uncovered_upper_bound_rate_per_s": unc_rate,
                                 "dpa_uncovered_upper_bound": unc_rate * 10, "he_appm_per_dpa": 3.0 / (rate * 10),
                                 "elements": {"Fe": {}}}, "gas": {"He3": {"appm": 1.0}, "He4": {"appm": 2.0},
                                                                 "He_appm": 3.0}},
        {"t_s": 15.0, "damage": {"dpa_rate_per_s": 0.0, "dpa": rate * 10, "dpa_nrt_rate_per_s": 0.0,
                                 "dpa_nrt": nrt_rate * 10, "dpa_arc_rate_per_s": 0.0, "dpa_arc": arc_rate * 10,
                                 "dpa_uncovered_upper_bound_rate_per_s": 0.0,
                                 "dpa_uncovered_upper_bound": unc_rate * 10, "he_appm_per_dpa": 3.0 / (rate * 10),
                                 "elements": {"Fe": {}}}, "gas": {"He3": {"appm": 1.0}, "He4": {"appm": 2.0},
                                                                 "He_appm": 3.0}}]}
    for s in res["steps"]:
        s["damage"]["elements"]["Fe"] = {k: v for k, v in s["damage"].items() if k != "elements"
                                          and k != "he_appm_per_dpa"}
    out = check_run_with_bounds(res, spec, bounds_asc, row, "Fe", 40.0, True, None)
    assert out["pass"] and out["compared"] > 0, out
    res["steps"][1]["damage"]["dpa_nrt"] *= 1 + 1e-9
    assert not check_run_with_bounds(res, spec, bounds_asc, row, "Fe", 40.0, True, None)["pass"]
    # arc-parameter and structure checks
    good = {"displacement": {"arc_dpa": {"citation": "Nordlund et al., Nat. Commun. 9, 1084 (2018)",
                                         "parameters": {"Fe": {"b": -0.568, "c": 0.286,
                                                               "reference_displacement_energy_eV": 40.0}}}}}
    assert arc_parameter_check(good)["problems"] == []
    good["displacement"]["arc_dpa"]["parameters"]["Fe"]["c"] = 0.28
    assert arc_parameter_check(good)["problems"]
    assert parse_duration("300.0 s") == 300.0 and parse_duration("2 min") == 120.0 and parse_duration("1e2 s") == 100.0
    assert abs(sum(fe_atoms_per_g(m) * 1.0 for m in FE_ABUNDANCE) - NA / sum(f * m for f, m in
                                                                          FE_ABUNDANCE.values())) < 1e11
    # exact elastic damage: quadrature, analytic isotropic and P1 centre-of-mass cases, tabulated, laboratory frame
    gx, gw = gauss_legendre(8)
    assert abs(sum(gw) - 2.0) < 1e-14 and abs(sum(w * x ** 14 for x, w in zip(gx, gw)) - 2.0 / 15.0) < 1e-14
    assert abs(adaptive_gl(lambda t: math.sqrt(t), 0.0, 1.0, 1e-8) - 2.0 / 3.0) < 1e-6
    awr, zz, edp, lin = 55.45, 26, 40.0, (lambda t: t)
    for e in (1e3, 5e3, 1e5, 2e7):                    # W = c - ed^2 / (4 c) for an isotropic pdf and R = T above ed
        c = 2.0 * awr * e / (awr + 1.0) ** 2
        assert rel(elastic_w(legendre_pdf([]), e, awr, zz, edp, damage=lin), c - edp ** 2 / (4.0 * c)) < 1e-9, e
    assert elastic_w(legendre_pdf([]), 500.0, awr, zz, edp, damage=lin) == 0.0       # 2 c < ed: no damage possible
    e = 1e5
    c = 2.0 * awr * e / (awr + 1.0) ** 2
    mu_ed = 1.0 - edp / c
    f_int = mu_ed ** 2 / 2.0 - mu_ed ** 3 / 3.0 - 5.0 / 6.0
    want = c - edp ** 2 / (4.0 * c) + 1.5 * 0.3 * c * f_int                            # P1: a_1 = 0.3
    assert rel(elastic_w(legendre_pdf([0.3]), e, awr, zz, edp, damage=lin), want) < 1e-9
    p1_tab = {"x": [-1.0, 0.0, 1.0], "y": [-0.25, 0.5, 1.25], "ranges": [(3, 2)]}     # = 1/2 + 3/4 mu (a_1 = 0.5)
    b, fn, dev = tabulated_pdf(p1_tab)
    assert dev < 1e-12 and b == [-1.0, 0.0, 1.0]
    assert rel(elastic_w((b, fn), e, awr, zz, edp, damage=lin), elastic_w(legendre_pdf([0.5]), e, awr, zz, edp,
                                                                           damage=lin)) < 1e-9
    b, fn, dev = tabulated_pdf({"x": [-1.0, 1.0], "y": [1.0, 1.0], "ranges": [(2, 2)]})   # norm 2: renormalized
    assert abs(dev - 1.0) < 1e-12 and rel(elastic_w((b, fn), e, awr, zz, edp, damage=lin),
                                          elastic_w(legendre_pdf([]), e, awr, zz, edp, damage=lin)) < 1e-9
    ratio = 10.0                                      # laboratory pdf of an isotropic centre-of-mass distribution

    def lab_fn(mu):
        root = math.sqrt(ratio * ratio - 1.0 + mu * mu)
        s_ = (mu + root) / (ratio + 1.0)
        return 0.5 * (ratio + 1.0) * s_ * (1.0 + mu / root) / ratio

    c = 2.0 * ratio * e / (ratio + 1.0) ** 2
    assert rel(elastic_w(([-1.0, 1.0], lab_fn), e, ratio, 5, edp, lab=True, damage=lin), c - edp ** 2 / (4.0 * c)) < 1e-8
    real = elastic_w(legendre_pdf([]), 1e6, awr, zz, edp)                                # with the Lindhard function
    assert 0.0 < real < 2.0 * awr * 1e6 / (awr + 1.0) ** 2
    # interpolation helper against the self-check's own formulas
    for law in (1, 2, 3, 5):
        tab = {"x": [1.0, 3.0, 9.0], "y": [2.0, 3.0, 1.0], "ranges": [(3, law)]}
        for q in (1.5, 3.0, 5.0, 8.9):
            assert abs(interp_value(tab, q) - _interp(tab, q, law)) < 1e-12, (law, q)
    # W(E) table: refinement, threshold point, pdf mixing in energy (Legendre a_1 linear in E gives a_1(E))
    mf4 = {"za": 26056, "awr": awr, "lct": 2, "li": 0, "ltt": 1,
           "parts": [{"kind": "legendre", "energies": [1e3, 1e4, 2e4], "data": [[0.0], [0.0], [0.4]],
                      "ranges": [(3, 2)]}]}
    wt = w_table(mf4, edp)
    thr = edp * (awr + 1.0) ** 2 / (4.0 * awr)
    assert thr in wt["x"] and wt["y"][wt["x"].index(thr)] == 0.0 and all(
        y == 0.0 for x_, y in zip(wt["x"], wt["y"]) if x_ <= thr)
    gaps = [b_ / a_ for a_, b_ in zip(wt["x"], wt["x"][1:]) if a_ >= 1e4]
    assert gaps and max(gaps) <= EL_REFINE * (1 + 1e-9)
    mid = next(x_ for x_ in wt["x"] if 1.2e4 < x_ < 1.8e4)
    a1 = 0.4 * (mid - 1e4) / 1e4
    assert rel(wt["y"][wt["x"].index(mid)], elastic_w(legendre_pdf([a1]), mid, awr, 26, edp)) < 1e-8
    prod = damage_product({"x": [1e3, 3e4], "y": [2.0, 2.0], "ranges": [(2, 2)]}, wt)
    assert rel(prod["y"][prod["x"].index(mid)], 2.0 * wt["y"][wt["x"].index(mid)]) < 1e-12
    bad_law = dict(mf4, parts=[dict(mf4["parts"][0], ranges=[(3, 3)])])
    try:
        w_table(bad_law, edp)
        raise AssertionError("an unsupported energy interpolation law must be refused")
    except ValueError:
        pass
    # MF=4 and MF=6 readers on synthetic ENDF text
    def ival(n):
        return f"{n:>11}"

    def endf_file(path, mat, mf, mt, rows):
        out = []
        for fields in rows:
            out.append(_line(fields, mat, mf, mt, len(out) + 1))
        out.append(_line([], mat, mf, 0, 99999))
        out.append(_line([], mat, 0, 0, 0))
        out.append(_line([], -1, 0, 0, 0))
        Path(path).write_text("\n".join(out) + "\n")

    def chunks(words):
        return [words[i:i + 6] for i in range(0, len(words), 6)]

    with tempfile.TemporaryDirectory(prefix="p154-selfcheck-") as tmp:
        rows = [[26056.0, 55.45, ival(0), ival(3), ival(0), ival(0)], [0.0, 55.45, ival(0), ival(2), ival(0), ival(0)],
                [0.0, 0.0, ival(0), ival(0), ival(1), ival(2)], [ival(2), ival(2)],
                [0.0, 1.0e5, ival(0), ival(0), ival(1), ival(0)], [0.2], [0.0, 2.0e5, ival(0), ival(0), ival(1),
                                                                         ival(0)], [0.4],
                [0.0, 0.0, ival(0), ival(0), ival(1), ival(1)], [ival(1), ival(2)],
                [0.0, 3.0e5, ival(0), ival(0), ival(1), ival(2)], [ival(2), ival(2)], [-1.0, 0.5, 1.0, 0.5]]
        endf_file(Path(tmp) / "mf4", 2631, 4, 2, rows)
        got = read_mf4_elastic(Path(tmp) / "mf4")
        assert got["ltt"] == 3 and got["lct"] == 2 and got["za"] == 26056 and abs(got["awr"] - 55.45) < 1e-12
        assert [p_["kind"] for p_ in got["parts"]] == ["legendre", "tabulated"]
        assert got["parts"][0]["energies"] == [1e5, 2e5] and got["parts"][0]["data"] == [[0.2], [0.4]]
        assert got["parts"][1]["energies"] == [3e5] and got["parts"][1]["data"][0]["x"] == [-1.0, 1.0]
        assert got["parts"][0]["ranges"] == [(2, 2)]
        wsyn = w_table(got, edp)
        assert wsyn["x"][-1] == 3e5 and all(y >= 0.0 for y in wsyn["y"]) and wsyn["y"][-1] > 0.0

        def mf6_file(path, mt, recoil):
            sub1 = [[1.0, 1.0, ival(0), ival(1), ival(1), ival(2)], [ival(2), ival(2)], [1e6, 1.0, 2e8, 1.0],
                    [0.0, 0.0, ival(1), ival(2), ival(1), ival(1)], [ival(1), ival(2)],
                    [0.0, 1e6, ival(0), ival(1), ival(3), ival(1)], [0.0, 1.0, 2.0]]
            sub2 = [[26055.0, 54.4, ival(0), ival(4), ival(1), ival(2)], [ival(2), ival(2)], [1e6, 1.0, 2e8, 1.0]]
            head = [[26056.0, 55.45, ival(0), ival(2), ival(2 if recoil else 1), ival(0)]]
            endf_file(path, 2631, 6, mt, head + sub1 + (sub2 if recoil else []))

        mf6_file(Path(tmp) / "with", 91, True)
        mf6_file(Path(tmp) / "without", 91, False)
        mf6_file(Path(tmp) / "other_mt", 16, True)
        mf6_file(Path(tmp) / "continuum649", 649, True)
        ok = njoy_continuum_recoil(Path(tmp) / "with")
        assert ok["mode"] == "evaluated" and ok["per_mt"]["91"]["subsections_zap_law"] == [(1, 1), (26055, 4)], ok
        assert ok["per_mt"]["91"]["walked_completely"] and ok["continuum_mts_with_recoil"] == [91]
        no = njoy_continuum_recoil(Path(tmp) / "without")
        assert no["mode"] == "kinematic" and no["per_mt"]["91"]["subsections_zap_law"] == [(1, 1)], no
        assert njoy_continuum_recoil(Path(tmp) / "other_mt")["mode"] == "kinematic"          # MT=16 is not a continuum
        assert njoy_continuum_recoil(Path(tmp) / "continuum649")["mode"] == "evaluated"
        assert njoy_continuum_recoil(Path(tmp) / "mf4")["mode"] == "kinematic"                # no MF=6 at all
    # build arguments: `kinematic` continuum is the default and is not passed
    assert "--continuum-recoil" not in build_args("Fe56", dict(BUILD_ED), "per-photon", None)
    assert build_args("Fe56", dict(BUILD_ED), "per-photon", None, "evaluated")[-2:] == ["--continuum-recoil",
                                                                                      "evaluated"]
    # G5 reference total: NJOY444 - NJOY445 + exact elastic
    adjusted = 12.0 - 6.0 + 3.0                                   # NJOY444 12, NJOY445 6, exact elastic 3
    assert bound_comparison([9.0], [0.0], [adjusted], [3.0], [0.0], [0.0])["pass"]
    assert not bound_comparison([9.0], [0.0], [12.0], [6.0], [0.0], [0.0])["pass"]
    # ---- Amendment B: capture photons (B1) -----------------------------------------------------------------------
    assert abs(EMC2 / 939565420.52 - 1.0) < 1e-9 and abs(sum(TABSQ6_W) - 2.0) < 1e-12 and abs(sum(TABSQ6_X)) < 1e-12
    tm_s, ident = 1.0e9, (lambda t: t)
    rec_s = {"energy": 1.0e-5, "nd": 2, "x": [1.0e6, 2.0e6, 3.0e6, 4.0e6, 6.0e6], "y": [0.1, 0.2, 0.4, 0.4, 0.4]}
    disc = sum(y_ * x_ * x_ / (2.0 * tm_s) for x_, y_ in ((1.0e6, 0.1), (2.0e6, 0.2)))
    cont = 0.4 / (2.0 * tm_s) * (6.0e6 ** 3 - 3.0e6 ** 3) / 3.0          # integral of f x^2 / (2 tm) dx
    h_n, s_n = tabsq6_record(rec_s, tm_s, ident)                         # tabsq6: the continuum counts twice
    assert abs(s_n / (0.3 + 2.0 * 0.4 * 3.0e6) - 1.0) < 1e-4 and abs(h_n / ((disc + 2.0 * cont) / s_n) - 1.0) < 1e-4
    h_x, s_x = exact_photon_record(rec_s, tm_s, ident, 1)
    assert abs(s_x / (0.3 + 0.4 * 3.0e6) - 1.0) < 1e-9 and abs(h_x / ((disc + cont) / s_x) - 1.0) < 1e-9, (h_x, s_x)
    assert h_x != h_n
    rec_l = {"energy": 1.0e-5, "nd": 0, "x": [3.0e6, 5.0e6], "y": [0.0, 1.0]}           # f = (x - 3e6)/2e6, linear
    cont_l = (5.0e6 ** 4 / 4.0 - 3.0e6 * 5.0e6 ** 3 / 3.0 - (3.0e6 ** 4 / 4.0 - 3.0e6 ** 4 / 3.0)) / (2.0e6 * 2.0 * tm_s)
    h_l, s_l = exact_photon_record(rec_l, tm_s, ident, 2)
    assert abs(s_l - 1.0e6) < 1e-3 and abs(h_l * s_l / cont_l - 1.0) < 1e-9
    h_t, s_t = tabsq6_record(rec_l, tm_s, ident)           # a linear spectrum is read exactly by tabsq6, then doubled
    assert abs(s_t / (2.0 * s_l) - 1.0) < 1e-4 and abs(h_t / h_l - 1.0) < 1e-4
    t0 = 12.0                                              # damage = T for T >= t0: a panel split at the cut
    step = (lambda t: t if t >= t0 else 0.0)
    x0 = math.sqrt(2.0 * tm_s * t0)
    rec_c = {"energy": 1.0e-5, "nd": 0, "x": [0.5 * x0, 2.0 * x0], "y": [1.0, 1.0]}
    h_c, s_c = exact_photon_record(rec_c, tm_s, step, 1, cuts=[x0])
    want = (2.0 * x0) ** 3 / (6.0 * tm_s) - x0 ** 3 / (6.0 * tm_s)
    assert abs(h_c * s_c / want - 1.0) < 1e-9 and abs(s_c - 1.5 * x0) < 1e-6
    try:
        exact_photon_record(rec_c, tm_s, step, 3)
        raise AssertionError("an unsupported LEP must be refused")
    except ValueError:
        pass
    assert record_interpolant([1.0, 2.0, 4.0], [10.0, 20.0, 60.0], 3.0) == 40.0
    assert record_interpolant([1.0, 2.0, 4.0], [10.0, 20.0, 60.0], 0.5) == 5.0
    assert record_interpolant([1.0, 2.0, 4.0], [10.0, 20.0, 60.0], 4.0 * (1.0 + 1e-9)) == 0.0
    assert record_interpolant([1.0, 2.0, 4.0], [10.0, 20.0, 60.0], 4.0) == 60.0
    # the yield multiplies the record values; an MF=6 photon subsection read from synthetic ENDF text
    with tempfile.TemporaryDirectory(prefix="p154-selfcheck-") as tmp:
        def mf6_capture(path, yld):
            recs_ = []
            for energy in (1.0e-5, 2.0e7):
                recs_ += [[0.0, energy, ival(1), ival(0), ival(6), ival(3)], [9.0e6, 0.25, 3.0e6, 5.0e-7, 6.0e6, 5.0e-7]]
            rows_ = [[26056.0, 55.45, ival(0), ival(3), ival(1), ival(0)],
                     [0.0, 0.0, ival(0), ival(1), ival(1), ival(2)], [ival(2), ival(2)], [1.0e-5, yld, 3.0e7, yld],
                     [0.0, 0.0, ival(1), ival(1), ival(1), ival(2)], [ival(2), ival(2)]] + recs_
            endf_file(path, 2631, 6, 102, rows_)

        mf6_capture(Path(tmp) / "cap", 2.0)
        ph = read_mf6_photons(Path(tmp) / "cap")
        assert ph["lep"] == 1 and ph["lang"] == 1 and len(ph["records"]) == 2 and ph["yield"]["y"] == [2.0, 2.0]
        assert ph["records"][0]["nd"] == 1 and ph["records"][1]["x"] == [9.0e6, 3.0e6, 6.0e6]
        assert ph["records"][0]["y"] == [0.25, 5.0e-7, 5.0e-7] and abs(ph["awr"] - 55.45) < 1e-12
        assert read_mf6_photons(Path(tmp) / "cap", 16) is None
        recs_c = capture_records(ph, 26, 55.45, 40.0)
        ph1 = {**ph, "yield": {"x": [1.0e-5, 3.0e7], "y": [1.0, 1.0], "ranges": [(2, 2)]}}
        recs_1 = capture_records(ph1, 26, 55.45, 40.0)
        assert abs(recs_c["exact"][0] / recs_1["exact"][0] - 2.0) < 1e-12 and recs_c["exact"][0] > 0.0
        assert abs(recs_c["tabsq6"][1] / recs_1["tabsq6"][1] - 2.0) < 1e-12 and recs_c["energies"] == [1.0e-5, 2.0e7]
        # capture_reference on a synthetic PENDF whose MT=447 is exactly sigma_102 times the tabsq6 emulation
        mf6_capture(Path(tmp) / "endf", 1.0)
        head = _line([26056.0, 55.45, "          0", "          0", "          0", "          0"], 2631, 1, 451, 1)
        body = (Path(tmp) / "endf").read_text()
        (Path(tmp) / "endf").write_text(head + "\n" + _line(["           "] * 6, 2631, 1, 0, 99999) + "\n" + body)
        grid_ = [1.0e-5, 1.0, 1.0e3, 1.0e5, 2.0e7]
        sig_ = [10.0, 8.0, 3.0, 1.0, 0.1]
        hn_ = recs_1["tabsq6"][0]                                          # both records are equal: h is constant in E
        he_ = recs_1["exact"][0]
        assert abs(hn_ / he_ - 1.0) > 1e-3 and he_ > 0.0
        synthetic_pendf(Path(tmp) / "pendf", 2631, {
            1: ([(5, 2)], list(zip(grid_, [20.0] * 5))), 102: ([(5, 2)], list(zip(grid_, sig_))),
            447: ([(5, 2)], [(g_, s_ * hn_) for g_, s_ in zip(grid_, sig_)])})
        bnd = [1.0e-5, 1.0, 1.0e3, 1.0e5, 2.0e7]
        got = capture_reference(Path(tmp) / "endf", Path(tmp) / "pendf", bnd, 40.0, min_groups=1)
        want_ = njoy.collapse({"x": grid_, "y": [s_ * he_ for s_ in sig_], "ranges": [(5, 2)]}, bnd)
        assert got["applicable"] and got["precondition"]["pass"] and got["precondition"]["set_size"] == 4
        assert all(abs(a_ / b_ - 1.0) < 1e-5 for a_, b_ in zip(got["groups"], want_)), (got["groups"], want_)   # 7-digit tape
        assert all(abs(a_ / b_ - 1.0) < 1e-5 for a_, b_ in zip(got["emulated_groups"], got["raw_groups"]))
        # a 447 that is 3 percent above the emulation fails the precondition
        synthetic_pendf(Path(tmp) / "pendf3", 2631, {
            1: ([(5, 2)], list(zip(grid_, [20.0] * 5))), 102: ([(5, 2)], list(zip(grid_, sig_))),
            447: ([(5, 2)], [(g_, 1.03 * s_ * hn_) for g_, s_ in zip(grid_, sig_)])})
        assert not capture_reference(Path(tmp) / "endf", Path(tmp) / "pendf3", bnd, 40.0, min_groups=1)["precondition"]["pass"]
        assert capture_reference(Path(tmp) / "endf", Path(tmp) / "pendf", bnd, 40.0)["precondition"]["vacuous"]   # 4 < 10
        # without MF=6 photons the reference is the raw MT=447
        (Path(tmp) / "plain").write_text(head + "\n" + _line(["           "] * 6, 2631, 1, 0, 99999) + "\n")
        none_ = capture_reference(Path(tmp) / "plain", Path(tmp) / "pendf", bnd, 40.0)
        assert not none_["applicable"] and none_["groups"] == none_["raw_groups"] and all(
            abs(a_ / b_ - 1.0) < 1e-5 for a_, b_ in zip(none_["groups"], njoy.collapse(
                {"x": grid_, "y": [s_ * hn_ for s_ in sig_], "ranges": [(5, 2)]}, bnd)))
    # C1: the set is chosen by the independent bound U (not by the emulated value); window NJOY (1-tol) - U <= X <= NJOY (1+tol)
    one, zero_u = [1.0] * 12, [0.0] * 12
    assert b_precondition(one, one, zero_u)["set_size"] == 12 and b_precondition(one, one, zero_u)["pass"]
    u_in = [0.0] * 11 + [0.004]                  # in the set (0.4 percent <= 0.5): low side widened by U
    assert b_precondition([1.0] * 11 + [0.9951], one, u_in)["pass"]          # low by 0.49 percent < U + 0.5 percent
    pc = b_precondition([1.0] * 11 + [0.990], one, u_in)                      # low by 1 percent > U + 0.5 percent
    assert not pc["pass"] and pc["n_bad"] == 1 and pc["bad"][0]["group"] == 11 and abs(pc["worst_deviation"] + 0.01) < 1e-12
    assert not b_precondition([1.0] * 11 + [1.006], one, u_in)["pass"]       # high by more than 0.5 percent
    assert b_precondition([1.0] * 11 + [1.004], one, u_in)["pass"]
    assert not b_precondition([1.0] * 11 + [0.98], one, zero_u)["pass"]       # low with no bound: fails (not selected out)
    big_u = [0.0] * 11 + [0.006]                 # a bound above 0.5 percent of NJOY excludes the group
    ex_ = b_precondition([1.0] * 11 + [0.5], one, big_u)
    assert ex_["set_size"] == 11 and ex_["pass"] and ex_["bound_over_njoy_max"] == 0.0
    assert b_precondition([1.0] * 9, [1.0] * 9, [0.0] * 9)["vacuous"] and b_precondition([1.0] * 10, [1.0] * 10,
                                                                                         [0.0] * 10)["pass"]
    assert b_precondition([0.0] * 12, [0.0] * 12, zero_u)["vacuous"]
    bnd_s = [float(k) for k in range(13)]        # B2: groups whose upper edge is at or below the lowest threshold drop
    ab = b_precondition([0.0] * 2 + [1.0] * 10, [1.0] * 12, zero_u, bounds=bnd_s, above=2.0)
    assert ab["set_size"] == 10 and ab["pass"] and not b_precondition([0.0] * 12, [1.0] * 12, zero_u, bounds=bnd_s,
                                                                      above=2.0)["pass"]
    for a_, m_b in ((55.45, 1), (182.37, 1)):    # elastic limit: T_max = 4 A E / (A+1)^2
        assert abs(recoil_tmax(1.0e6, a_, m_b, 0.0) / (4.0 * a_ / (a_ + 1.0) ** 2 * 1.0e6) - 1.0) < 1e-12
    assert recoil_tmax(1.0e6, 55.45, 4, -2.0e6) == 0.0 and recoil_tmax(1.0e6, 55.45, 4, 3.0e6) > 0.0
    assert recoil_tmax(10.0, 55.45, 4, 2.0e6) < 2.0e6 * 4.0 / 55.45 * 1.01     # (n,alpha)-like: recoil ~ Q m_b/(A+1)
    assert [cluster_mass(m) for m in (91, 103, 107, 112, 600, 650, 700, 750, 849)] == [1, 1, 4, 5, 1, 2, 3, 3, 4]
    try:
        cluster_mass(118)
        raise AssertionError("an MT with no known cluster must be refused")
    except ValueError:
        pass
    tb_ = {107: {"x": [1.0, 100.0], "y": [2.0, 2.0], "ranges": [(2, 2)], "qi": 5.0}}
    got_b, used_b = unemulated_bound(tb_, (107, 108), [1.0, 100.0], 10.0)
    n_ = 20000                                   # midpoint rule in ln E of sigma T_max (T_max is smooth on the tape's chord)
    ref_ = sum(2.0 * recoil_tmax(math.exp((k + 0.5) / n_ * math.log(100.0)), 10.0, 4, 5.0) for k in range(n_)) / n_
    assert used_b == {107: 4} and abs(got_b[0] / ref_ - 1.0) < 2e-2    # the tape interpolates sigma T_max linearly
    # HEATR table energies and leading zero rows restored from the grid
    grid_t = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    restored, flag = true_grid({"x": [4.0, 5.0, 6.000001], "y": [1.0, 2.0, 3.0], "ranges": [(3, 2)]}, grid_t)
    assert flag and restored["x"] == [3.0, 4.0, 5.0, 6.0] and restored["y"] == [0.0, 1.0, 2.0, 3.0]
    full, flag = true_grid({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "y": [0.0] * 6, "ranges": [(6, 2)]}, grid_t)
    assert flag and full["x"] == grid_t and len(full["y"]) == 6
    shifted, flag = true_grid({"x": [4.0, 5.1, 6.0], "y": [1.0, 2.0, 3.0], "ranges": [(3, 2)]}, grid_t)
    assert not flag and shifted["x"] == [4.0, 5.1, 6.0]
    # ---- Amendment B: discrete levels (B2) -----------------------------------------------------------------------
    awr_s, thr_s = 55.45, 20000.0
    for e in (30000.0, 1.0e5, 1.0e6):                          # isotropic, damage = T: W = c (1 + r^2), r^2 = 1 - thr/E
        c_ = awr_s * e / (awr_s + 1.0) ** 2
        got_w = inelastic_w(legendre_pdf([]), e, awr_s, 26, 0.0, thr_s, damage=lin)
        assert rel(got_w, c_ * (2.0 - thr_s / e)) < 1e-9, e
    e = 1.0e5                                                  # with a cut at T = ed: closed form
    c_ = awr_s * e / (awr_s + 1.0) ** 2
    r_ = math.sqrt(1.0 - thr_s / e)
    mu_ed = (1.0 + r_ * r_ - 40.0 / c_) / (2.0 * r_)
    want_w = c_ * ((1.0 + r_ * r_) * (mu_ed + 1.0) - r_ * (mu_ed ** 2 - 1.0)) / 2.0
    assert rel(inelastic_w(legendre_pdf([]), e, awr_s, 26, 40.0, thr_s, damage=lin), want_w) < 1e-9
    assert rel(inelastic_w(legendre_pdf([0.3]), e, awr_s, 26, 40.0, 0.0, damage=lin),
               elastic_w(legendre_pdf([0.3]), e, awr_s, 26, 40.0, damage=lin)) < 1e-9     # thresh = 0 is elastic
    assert abs(inelastic_w(legendre_pdf([]), thr_s, awr_s, 26, 40.0, thr_s, damage=lin)
               - awr_s * thr_s / (awr_s + 1.0) ** 2) < 1e-9                                 # r = 0 at the threshold
    assert inelastic_w(legendre_pdf([]), 100.0, awr_s, 26, 40.0, 0.0, damage=lin) == 0.0       # 2 c < ed: no damage
    assert 0.0 < inelastic_w(legendre_pdf([]), 2.0e6, awr_s, 26, 40.0, thr_s) < awr_s * 2.0e6 / (awr_s + 1.0) ** 2 * 2.0
    # the ladder: isotropic level, grid [100, 105, 130, 131, 200, 400] -> 100, 110, 130, 143, 200, 400
    def same(a_, b_):
        return len(a_) == len(b_) and all(abs(x_ - y_) <= 1e-12 * y_ for x_, y_ in zip(a_, b_))

    assert same(disbar_ladder([100.0, 105.0, 130.0, 131.0, 200.0, 400.0], [], True),
                [100.0, 110.0, 130.0, 143.0, 200.0, 400.0])
    # MF=4 energies [50, 120, 1000] bound the step: 100, 110, then 120 (enext < 1.1 * 110) although 115 <= 120
    assert same(disbar_ladder([100.0, 105.0, 115.0, 200.0], [50.0, 120.0, 1000.0], False),
                [100.0, 110.0, 120.0, 200.0])
    assert disbar_ladder([100.0, 105.0, 115.0], [50.0, 120.0, 1000.0], False)[2] == 120.0
    assert disbar_ladder([], [], True) == []
    pts = disbar_ladder([1.0e5 * 1.05 ** k for k in range(60)], [], True)      # 5 percent grid: a point every other energy
    assert all(b_ / a_ > 1.0999 for a_, b_ in zip(pts, pts[1:])) and pts[0] == 1.0e5 and len(pts) < 35
    assert mf4_ladder_energies({"parts": []}) == ([], True)
    assert mf4_ladder_energies({"parts": [{"kind": "legendre", "energies": [1.0, 2.0], "data": [[], []]}]})[1]
    assert not mf4_ladder_energies({"parts": [{"kind": "legendre", "energies": [1.0, 2.0], "data": [[], [0.1]]}]})[1]
    assert fine_points([1.0, 1.01, 1.5], 1.02)[0] > 1.01 and len(fine_points([1.0, 1.01, 1.5], 1.02)) == 19
    # MF=4 pdf at an energy: isotropic below the first record, mixing between, INT=1 holds the lower
    m4 = {"parts": [{"kind": "legendre", "energies": [10.0, 20.0, 40.0], "data": [[0.0], [0.4], [0.4]],
                     "ranges": [(2, 2), (3, 1)]}]}
    assert abs(mf4_pdf_at(m4, 5.0)[1](0.5) - 0.5) < 1e-15
    assert abs(mf4_pdf_at(m4, 15.0)[1](1.0) - (0.5 + 1.5 * 0.2)) < 1e-12
    assert abs(mf4_pdf_at(m4, 30.0)[1](1.0) - (0.5 + 1.5 * 0.4)) < 1e-12 and abs(mf4_pdf_at(m4, 99.0)[1](1.0) - 1.1) < 1e-12
    # inelastic_reference on a synthetic isotropic level: the emulated tabulated term reproduces a 446 built from it
    with tempfile.TemporaryDirectory(prefix="p154-selfcheck-") as tmp:
        head = _line([26056.0, 55.45, "          0", "          0", "          0", "          0"], 2631, 1, 451, 1)
        (Path(tmp) / "endf").write_text(head + "\n" + _line(["           "] * 6, 2631, 1, 0, 99999) + "\n")
        q_s = -20000.0 * 55.45 / 56.45                                       # threshold 20 keV
        thr_q = (55.45 + 1.0) / 55.45 * (-q_s)
        grid_i = [1.0e-5, 1.0e3, thr_q] + [thr_q * 1.4 ** k for k in range(1, 12)]
        sig_i = [0.0, 0.0, 0.0] + [0.1 * k for k in range(1, 12)]
        lvl = [(g_, s_) for g_, s_ in zip(grid_i, sig_i) if g_ >= thr_q]
        ex_s, lad_s, lad_pts = level_w_tables({"parts": []}, 55.45, 26, 40.0, thr_q, [g_ for g_, _ in lvl])
        assert lad_pts[0] == thr_q and abs(lad_s["y"][0] - inelastic_w(legendre_pdf([]), thr_q, 55.45, 26, 40.0, thr_q)) < 1e-12
        t446 = [(g_, interp_value(lad_s, g_) * s_ if g_ >= thr_q else 0.0) for g_, s_ in zip(grid_i, sig_i)]
        synthetic_pendf(Path(tmp) / "pendf", 2631, {
            1: ([(len(grid_i), 2)], [(g_, 9.0) for g_ in grid_i]), 51: ([(len(lvl), 2)], lvl, q_s),
            446: ([(len(grid_i), 2)], t446)})
        bnd = [1.0e-5, 1.0e3, 1.0e4, thr_q, thr_q * 2.0, thr_q * 8.0, thr_q * 100.0]
        got = inelastic_reference(Path(tmp) / "endf", Path(tmp) / "pendf", bnd, 40.0, min_groups=1)
        assert got["precondition"]["pass"] and got["precondition"]["set_size"] >= 1, got["precondition"]
        raw_ = njoy.collapse({"x": grid_i, "y": [t[1] for t in t446], "ranges": [(len(grid_i), 2)]}, bnd)
        assert all(abs(a_ - b_) <= 1e-5 * (1.0 + abs(b_)) for a_, b_ in zip(got["ladder_groups"], raw_))   # 7-digit tape
        assert all(a_ >= 0.0 for a_ in got["groups"]) and got["info"]["levels"] == [51]
        grid_only = inelastic_reference(Path(tmp) / "endf", Path(tmp) / "pendf", bnd, 40.0, fine=None, min_groups=1)
        assert grid_only["info"]["fine_ratio"] is None and len(grid_only["groups"]) == 6
        # the exact reference of a convex-product group: sigma W_exact on the fine energies, not the coarse chord
        fine_ = got["groups"][3]
        exact_ = grid_only["exact_groups"][3]
        assert abs(fine_ / exact_ - 1.0) < 0.2
    print("P154 self-check: PASS")
    return 0


def _interp(tab, e, law):
    x, y = tab["x"], tab["y"]
    if e < x[0] or e >= x[-1]:
        return 0.0
    i = max(j for j in range(len(x) - 1) if x[j] <= e)
    x1, x2, y1, y2 = x[i], x[i + 1], y[i], y[i + 1]
    if law == 1:
        return y1
    if law == 2:
        return y1 + (y2 - y1) * (e - x1) / (x2 - x1)
    if law == 3:
        return y1 + (y2 - y1) * math.log(e / x1) / math.log(x2 / x1)
    return y1 * (e / x1) ** (math.log(y2 / y1) / math.log(x2 / x1))


# ------------------------------------------------------------------------------ main


def guarded(fn):
    try:
        return fn()
    except (GateError, KeyError, ValueError, OSError, TypeError) as error:
        return {"pass": False, "error": f"{type(error).__name__}: {error}"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--workdir", default="target/p154-work")
    ap.add_argument("--out", default="results/p154_damage.json")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--njoy", default=njoy.DEFAULT_NJOY)
    ap.add_argument("--njoy-err", type=float, default=njoy.RECONR_ERR)
    ap.add_argument("--ed-uniform", type=float, default=0.0, help="one displacement energy for every element")
    ap.add_argument("--grid-density", default=None, help="passed to build-damage --grid-density")
    ap.add_argument("--reuse", action="store_true", help="reuse built tables and NJOY tapes found in the workdir")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.reference or not args.candidate:
        ap.error("--reference and --candidate are required")
    ref = str(Path(args.reference).resolve())
    cand = str(Path(args.candidate).resolve())
    work = (ROOT / args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    bounds = vendored_boundaries()
    spec = json.loads(GATE_SPEC.read_text())

    inputs = {}
    for target, info in TARGETS.items():
        path = TENDL_DIR / info["file"]
        found = sha256(path) if path.is_file() else None
        inputs[target] = {"path": str(path), "sha256": found, "pinned": info["sha256"], "match": found == info["sha256"]}
    inputs_ok = all(v["match"] for v in inputs.values())

    gates = {g: {"pass": None, "external": True} for g in ("G0", "G1", "G7")}
    gates["G0"]["note"] = "protocol SHA-256 in protocols/protocol_hash.txt (external)"
    gates["G1"]["note"] = "fmt, clippy -D warnings, workspace tests (external)"
    gates["G7"]["note"] = "CI green on the pushed commit; committed baselines unchanged (external)"
    gates["G2"] = guarded(lambda: gate_g2(ref, cand, work, args.timeout))
    evidence, tables, ed_map = {}, {}, dict(BUILD_ED)
    if inputs_ok:
        g3, g4, g5, tables, evidence, ed_map = gates_g3_g4_g5(cand, work, args, bounds, spec)
        gates["G3"], gates["G4"], gates["G5"] = g3, g4, g5
        gates["G6"] = guarded(lambda: gate_g6(cand, work, tables, ed_map, bounds, args.timeout))
    else:
        for g in ("G3", "G4", "G5", "G6"):
            gates[g] = {"pass": False, "error": "TENDL-2025 evaluation SHA-256 does not match the pinned value"}

    judged = ("G2", "G3", "G4", "G5", "G6")
    verdict = all(gates[g].get("pass") is True for g in judged)
    record = {"protocol": "protocols/ACTINV-P154_PROTOCOL.md", "protocol_sha256": sha256(PROTOCOL),
              "reference_sha256": sha256(ref), "candidate_sha256": sha256(cand),
              "checker_sha256": sha256(__file__), "njoy_driver_sha256": sha256(Path(njoy.__file__)),
              "inputs": inputs, "groups_sha256": sha256(GROUPS_JSON), "displacement_energy_eV": ed_map,
              "flux_weight": "flat in lethargy: sum of the integral of y dE/E over the group divided by ln(hi/lo)",
              "tolerances": {"G3_elastic": TOL_G3, "G4_inelastic_disappearance": TOL_G4,
                             "G4_uncovered_fraction": G4_UNCOVERED_FRACTION,
                             "elastic_exact_quadrature": EL_TOL, "elastic_exact_energy_refinement": EL_REFINE, "G6_run": TOL_RUN,
                             "C1_precondition_tolerance": B_PRECONDITION_TOL,
                             "C1_bound_fraction": C1_BOUND_FRACTION, "C1_min_groups": C1_MIN_GROUPS,
                             "B2_fine_energy_ratio": LEVEL_FINE_RATIO, "B_reference_version": B_VERSION},
              "evidence": evidence, "gates": gates, "verdict": "PASS" if verdict else "FAIL"}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n")
    for g in judged:
        print(f"P154 {g}: {'PASS' if gates[g].get('pass') else 'FAIL'}")
        if "error" in gates[g]:
            print(f"  {g}: ERROR {gates[g]['error']}")
        for label, case in (gates[g].get("cases") or {}).items():
            state = "PASS" if case.get("pass") else "FAIL"
            print(f"  {g}/{label}: {state}" + (f" ERROR {case['error']}" if "error" in case else ""))
    print("P154 G0, G1, G7: external (not evaluated here)")
    print(f"P154: {record['verdict']} ({out})")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
