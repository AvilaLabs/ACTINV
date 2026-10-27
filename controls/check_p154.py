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
G3  Elastic. The table's `elastic` row against NJOY MT=445 collapsed onto the 709 groups: within 2 percent in every
    group with lower edge >= 1 keV where NJOY's elastic exceeds 1e-6 of its maximum. Below 1 keV the distribution of
    the differences is reported.
G4  Inelastic and disappearance. `inelastic` against MT=446 and `disappearance` against MT=447 within 5 percent in every
    group where the channel exceeds 1e-3 of NJOY's group total (MT=444) and the group has no uncovered cross section
    (`uncovered_xs_barn` is zero; the per-channel `channels` record is informational and not read). The remaining
    groups are reported. A channel with fewer than 5 evaluable groups is reported as vacuous and fails.
G5  Total and bound. In every group NJOY's MT=444 lies in [computed - s, computed + upper + s], computed =
    `damage_energy_barn_eV`, upper = `uncovered_damage_upper_barn_eV`, s = 0.02 NJOY445 + 0.05 (NJOY446 + NJOY447)
    + 1e-6 max(NJOY444) (the G3/G4 tolerances, plus a floor for groups with essentially no damage). The table
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

Build parameters: the displacement energy of each target's element is Nordlund's Table 1 value where it has one (Fe
40, Ni 39, W 70) and 40 eV for Cr (no arc-dpa constants; the ASTM E521 value), identical in `--displacement-energy`
and NJOY's HEATR `ed`. `--ed-uniform 25` sets the 25 eV default everywhere instead. Capture uses `--capture-photons
one-photon` (NJOY's treatment) for the comparisons; an `evaluated` build of each target is reported as information.

Usage: check_p154.py --reference REF --candidate CAND [--workdir target/p154-work] [--out results/p154_damage.json]
       check_p154.py --self-check      (offline unit checks, no binary, no NJOY)
Run inside the required systemd cgroup. Gates run serially.
"""
import argparse
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
G5_FLOOR = 1.0e-6
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


def channel_comparison(table, ref, ref_total, uncovered_xs, bounds, tol=TOL_G4):
    """G4 on one channel: groups where NJOY's channel exceeds 1e-3 of NJOY's total and nothing is uncovered."""
    evaluated, bad, worst = 0, [], 0.0
    skipped = []
    for g, (t, r, tot, unc) in enumerate(zip(table, ref, ref_total, uncovered_xs)):
        if not (tot > 0.0 and r > G4_FRACTION * tot):
            continue
        e = rel(t, r)
        if unc > 0.0:
            skipped.append(e)
            continue
        evaluated += 1
        worst = max(worst, e)
        if e > tol:
            bad.append({"group": g, "lo_eV": min(bounds[g], bounds[g + 1]), "table": t, "njoy": r, "rel": e})
    return {"evaluated_groups": evaluated, "n_bad": len(bad), "worst_rel": worst, "bad": bad[:20],
            "with_uncovered_reported": distribution(skipped),
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


# ------------------------------------------------------------------------------ building tables and NJOY references


def build_args(target, ed_map, capture, grid_density):
    info = TARGETS[target]
    args = ["--method", "kinematic", "--groups", "fispact-709", "--projectile", "neutron", "--capture-photons",
            capture, "--displacement-energy", f"{info['element']}={ed_map[info['element']]:g}"]
    if grid_density:
        args += ["--grid-density", str(grid_density)]
    return args


def build_table(binary, target, work, ed_map, capture, grid_density, timeout, reuse):
    out = work / f"table2_{target}_{capture}.json"
    if not (reuse and out.exists()):
        if out.exists():
            out.unlink()
        code, stdout, err = sh([binary, "build-damage", TENDL_DIR / TARGETS[target]["file"], out]
                               + build_args(target, ed_map, capture, grid_density), timeout)
        if code != 0 or not out.exists():
            raise GateError(f"build-damage {target} ({capture}) failed: {err.strip()[-400:]}")
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
    for target in TARGETS:
        entry = evidence["targets"].setdefault(target, {})
        try:
            table, path = build_table(candidate, target, work, ed_map, "one-photon", args.grid_density, args.timeout,
                                      args.reuse)
            tables[target] = (table, path)
            entry["table"] = {"path": str(path), "sha256": sha256(path)}
            violations = check_table_structure(table, target, ed_map, bounds)
            entry["structure_violations"] = violations
            record, tabs = njoy_reference(target, work, ed_map, args.njoy, args.njoy_err, args.timeout, args.reuse)
            entry["njoy"] = {k: v for k, v in record.items() if k not in ("deck",)}
            entry["njoy_deck"] = record["deck"]
            tb = [float(b) for b in table["boundaries_eV"]]
            ref = {mt: njoy.collapse(tabs[mt], tb) for mt in njoy.DAMAGE_MTS}
            row = table["targets"][target]
            entry["njoy_group_values_sha256"] = hashlib.sha256(json.dumps(ref, sort_keys=True).encode()).hexdigest()
            g3["cases"][target] = elastic_comparison(row["elastic"], ref[445], tb)
            g3["cases"][target]["structure_violations"] = violations
            g3["cases"][target]["pass"] = g3["cases"][target]["pass"] and not violations
            g4["cases"][target] = {
                "inelastic_vs_446": channel_comparison(row["inelastic"], ref[446], ref[444], row["uncovered_xs_barn"],
                                                       tb),
                "disappearance_vs_447": channel_comparison(row["disappearance"], ref[447], ref[444],
                                                           row["uncovered_xs_barn"], tb)}
            g4["cases"][target]["pass"] = all(v["pass"] for v in g4["cases"][target].values())
            case = bound_comparison(row["damage_energy_barn_eV"], row["uncovered_damage_upper_barn_eV"], ref[444],
                                    ref[445], ref[446], ref[447])
            ident = [rel(t, e + i + d) for t, e, i, d in zip(row["damage_energy_barn_eV"], row["elastic"],
                                                             row["inelastic"], row["disappearance"])]
            case["table_total_equals_partial_sum_worst_rel"] = max(ident)
            flux = spectrum_ascending(spec, tb)
            fns = {"computed": fold(row["damage_energy_barn_eV"], flux),
                   "computed_plus_upper": fold([t + u for t, u in zip(row["damage_energy_barn_eV"],
                                                                      row["uncovered_damage_upper_barn_eV"])], flux),
                   "njoy_444": fold(ref[444], flux), "njoy_445": fold(ref[445], flux),
                   "njoy_446": fold(ref[446], flux), "njoy_447": fold(ref[447], flux),
                   "table_elastic": fold(row["elastic"], flux), "table_inelastic": fold(row["inelastic"], flux),
                   "table_disappearance": fold(row["disappearance"], flux),
                   "unit": "eV per atom per second at the spec's spectrum (multiplier one)"}
            fns["relative_difference_njoy_vs_computed"] = (fns["njoy_444"] - fns["computed"]) / fns["njoy_444"] \
                if fns["njoy_444"] else None
            case["fns_collapsed"] = fns
            case["pass"] = case["pass"] and case["table_total_equals_partial_sum_worst_rel"] <= TOL_TABLE
            g5["cases"][target] = case
        except (GateError, KeyError, ValueError, OSError) as error:
            for gate in (g3, g4, g5):
                gate["cases"].setdefault(target, {"pass": False, "error": f"{type(error).__name__}: {error}"})
    # information: the default (evaluated-photon) capture treatment
    info = {}
    for target in TARGETS:
        try:
            table, _ = build_table(candidate, target, work, ed_map, "evaluated", args.grid_density, args.timeout,
                                   args.reuse)
            one = tables[target][0]["targets"][target]["disappearance"]
            ev = table["targets"][target]["disappearance"]
            diffs = [rel(a, b) for a, b in zip(ev, one) if b > 0.0]
            info[target] = {"disappearance_evaluated_vs_one_photon_rel": distribution(diffs),
                            "fns_disappearance_evaluated": fold(table["targets"][target]["disappearance"],
                                                                spectrum_ascending(spec, [float(b) for b in
                                                                                          table["boundaries_eV"]])),
                            "fns_disappearance_one_photon": fold(one, spectrum_ascending(
                                spec, [float(b) for b in tables[target][0]["boundaries_eV"]]))}
        except (GateError, KeyError, ValueError, OSError) as error:
            info[target] = {"error": f"{type(error).__name__}: {error}"}
    evidence["evaluated_capture_information"] = info
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
    ok = all(c.get("identical_apart_from_ms", True) and c.get("output_byte_identical", True) for c in cases.values())
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
    """A PENDF snippet: sections = {mt: (ranges, points)}; HEAD, TAB1 record, ranges, points, SEND, MEND, TEND."""
    out = []
    for mt, (ranges, points) in sections.items():
        n = 0

        def emit(fields):
            nonlocal n
            n += 1
            out.append(_line(fields, mat, 3, mt, n))

        emit([26056.0, 55.45, "          0", "          0", "          0", "          0"])
        emit([0.0, 0.0, "          0", "          0", f"{len(ranges):>11}", f"{len(points):>11}"])
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
              "tolerances": {"G3_elastic": TOL_G3, "G4_inelastic_disappearance": TOL_G4, "G6_run": TOL_RUN},
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
