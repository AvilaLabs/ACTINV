#!/usr/bin/env python3
"""P122 gates G2-G5 (protocols/ACTINV-P122_PROTOCOL.md): the complete step summary.

  G2  The unmodified gate spec gives byte-equivalent results from the reference and candidate binaries once the
      wall-clock timing keys are dropped: the top-level `ms` (RunResult.ms) and, wherever they occur, the mesh-footer
      fields `wall_time_s` and `cells_per_s` (docs/guide/specification.md, "Mesh"). The `ACTINV_P14_PROFILE` stage
      timings go to stderr, not into the result.
  G3  Independent recomputation of every summary quantity from the emitted atoms and this script's own ENDF-6
      MF=8 MT=457 parser: per-nuclide Bq, alpha/beta/gamma W, grams; totals, Ci, heat sums, excl-H3 values, FISPACT
      alpha/beta/gamma Bq, the absolute and per-cm3 blocks; the element table; the decay-mode partition; the dominant
      lists. Tolerance 1e-9 relative (1e-30 absolute where the expected value is zero).
  G4  Consistency: summary activity/heat against the step's existing activity_Bq_per_g / heat_W_per_g (any difference
      must be explained by listed omitted states), the mode partition against total activity (1e-12), and trace
      against coupled totals (tolerance cited in the output).
  G5  Report-only comparison with the FISPACT-II output of the FNS Fe 5-minute case. No verdict.

Overall PASS iff G2, G3 and G4 pass. Only the candidate runs the summary variants (the reference predates the
`summary` section). Runs are serial, without a shell, each under a timeout.

Usage:
  check_p122.py --reference REF_BIN --candidate CAND_BIN
      [--fispact-out ~/nuclear-data/conderc-fns/fns/Fe/TENDL-2017_1996exp_5min.out]
      [--workdir target/p122-work] [--out results/p122_summary.json] [--timeout 3600] [--reuse]

Run inside the required systemd cgroup. Exit 0 on PASS, 1 on FAIL.
"""
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
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
GATE_SPEC_SHA256 = "ed55ed00f6c00fc4a2351e18332e98e7feb33d9b7ca65e7ba861e0e5e8feef39"
PROTOCOL = ROOT / "protocols" / "ACTINV-P122_PROTOCOL.md"
DENSITY_G_CM3 = 7.874

# Physical constants, restated here rather than imported.
EV_J = 1.602176634e-19
NEUTRON_MASS_AMU = 1.00866491595
AMU_G = 1.66053906660e-24
BQ_PER_CI = 3.7e10
BRANCHING_TOLERANCE = 1e-5  # crates/actinv-core/src/chain.rs, BRANCHING_TOLERANCE
CUMULATIVE_SLACK = 1e-12  # summary.rs, CUMULATIVE_SLACK

REL_TOL = 1e-9
ABS_TOL_ZERO = 1e-30
MODE_PARTITION_TOL = 1e-12
TRACE_COUPLED_REL_TOL = 1e-8  # protocols/ACTINV-P5_PROTOCOL.md, G7
MAX_LISTED = 20

# Timing keys that G2 drops (see the module docstring for where each is documented).
TIMING_TOP_LEVEL = ("ms",)
TIMING_ANYWHERE = ("wall_time_s", "cells_per_s")

SYMBOLS = [
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl",
    "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As",
    "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In",
    "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb",
    "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl",
    "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk",
    "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh",
    "Fl", "Mc", "Lv", "Ts", "Og",
]

# Decay-mode classes, in the order of totals.activity_by_mode.
MODE_KEYS = ["beta_minus_Bq", "ec_beta_plus_Bq", "isomeric_transition_Bq", "alpha_Bq", "neutron_Bq",
             "spontaneous_fission_Bq", "proton_Bq", "other_Bq"]
BETA_MINUS, EC_BETA_PLUS, ISOMERIC, ALPHA, NEUTRON, SPONT_FISSION, PROTON, OTHER = range(8)
DOMINANT_KEYS = ["activity", "heat", "alpha_heat", "beta_heat", "gamma_heat"]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True)


def rel_diff(a, b):
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0.0 else abs(a - b) / scale


def name_of(z, a, liso):
    base = f"{SYMBOLS[z - 1] if 1 <= z <= len(SYMBOLS) else '?'}{a}"
    return f"{base}m{liso}" if liso > 0 else base


# ------------------------------------------------------------------------------ ENDF-6 MF=8 MT=457 parser


def endf_float(field):
    """ENDF-6 real: blank is zero, `1.234567+3` means 1.234567e3."""
    text = field.strip()
    if not text:
        return 0.0
    for i in range(1, len(text)):
        if text[i] in "+-" and text[i - 1] not in "eE":
            text = text[:i] + "e" + text[i:]
            break
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"nonfinite ENDF number {field!r}")
    return value


def endf_int(field):
    text = field.strip()
    return int(text) if text else 0


def cont(line):
    f = [line[i * 11:(i + 1) * 11] for i in range(6)]
    return (endf_float(f[0]), endf_float(f[1]), endf_int(f[2]), endf_int(f[3]), endf_int(f[4]), endf_int(f[5]))


def read_list(lines, index):
    """LIST record at `index`: returns (head, values, next index)."""
    head = cont(lines[index])
    npl = head[4]
    values = []
    nxt = index + 1
    while len(values) < npl:
        line = lines[nxt]
        for i in range(6):
            if len(values) == npl:
                break
            values.append(endf_float(line[i * 11:(i + 1) * 11]))
        nxt += 1
    return head, values, nxt


def parse_decay_section(lines):
    """One MF=8/MT=457 section (HEAD, energy LIST, mode LIST, then spectra that this checker does not need)."""
    za, awr, _lis, liso, nst, _nsp = cont(lines[0])
    if not (math.isfinite(za) and za > 0 and math.isfinite(awr) and awr > 0):
        raise ValueError(f"invalid decay HEAD ZA/AWR {za}/{awr}")
    za_i = int(round(za))
    energy_head, energies, nxt = read_list(lines, 1)
    mode_head, mode_values, _ = read_list(lines, nxt)
    ndk = mode_head[5]
    if ndk * 6 > len(mode_values):
        raise ValueError(f"decay mode LIST has {len(mode_values)} fields for {ndk} modes")
    modes = [(mode_values[6 * k], mode_values[6 * k + 4]) for k in range(ndk)]  # (RTYP, BR)
    return {"za": za_i, "liso": liso, "awr": awr, "nst": nst, "half_life": energy_head[0], "energies": energies,
            "modes": modes}


def scan_decay_file(path, needed):
    """Stream an ENDF-6 decay sublibrary. Returns (records for the `needed` (ZA, LISO) keys, set of every key in
    the file, list of duplicate keys). Sections are delimited the way the engine delimits them: consecutive lines
    tagged MF=8 MT=457 under one MAT form a section; any other tagged line ends it."""
    records, all_keys, duplicates = {}, set(), []
    cur = None
    buf = []

    def flush():
        if cur is None or not buf:
            return
        za, liso = int(round(cont(buf[0])[0])), cont(buf[0])[3]
        key = (za, liso)
        if key in all_keys:
            duplicates.append(key)
        all_keys.add(key)
        if key in needed:
            records[key] = parse_decay_section(buf)

    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if len(line) < 75:
                continue
            tag = line[70:75]
            if tag == " 8457":
                mat = line[66:70]
                if cur != mat:
                    cur = mat
                    buf = []
                buf.append(line)
            elif cur is not None and buf:
                flush()
                cur, buf = None, []
        flush()
    return records, all_keys, duplicates


def load_decay(primary, fallback, needed):
    """Merge as the engine does: the primary file wins; the fallback supplies only keys absent from the primary
    (run.rs: `Entry::Vacant`). Returns (records, provenance dict)."""
    p_records, p_keys, p_dups = scan_decay_file(primary, needed)
    records = dict(p_records)
    info = {"primary_keys": len(p_keys), "primary_duplicates": [list(k) for k in p_dups[:MAX_LISTED]],
            "from_primary": len(p_records), "from_fallback": 0, "missing": []}
    remaining = needed - set(p_records)
    # a needed key that the primary holds but did not return cannot happen; keys absent from the primary go on
    remaining -= p_keys
    if fallback and remaining:
        f_records, f_keys, f_dups = scan_decay_file(fallback, remaining)
        records.update(f_records)
        info["from_fallback"] = len(f_records)
        info["fallback_duplicates"] = [list(k) for k in f_dups[:MAX_LISTED]]
    info["missing"] = sorted([list(k) for k in needed - set(records)])[:MAX_LISTED]
    info["n_missing"] = len(needed - set(records))
    return records, info


# ------------------------------------------------------------------------------ recomputation


def lam_of(rec):
    if rec is None or rec["nst"] == 1 or rec["half_life"] <= 0.0:
        return 0.0
    return math.log(2.0) / rec["half_life"]


def e_light(rec):
    return rec["energies"][0] if len(rec["energies"]) > 0 else 0.0


def e_em(rec):
    return rec["energies"][2] if len(rec["energies"]) > 2 else 0.0


def e_heavy(rec):
    return rec["energies"][4] if len(rec["energies"]) > 4 else 0.0


def round_half_away(x):
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def mode_class(rtyp):
    leading = math.floor(round_half_away(rtyp * 1e6) / 1e6)
    if not (1.0 <= leading < 8.0):
        return OTHER
    return {1: BETA_MINUS, 2: EC_BETA_PLUS, 3: ISOMERIC, 4: ALPHA, 5: NEUTRON, 6: SPONT_FISSION, 7: PROTON}.get(
        int(leading), OTHER)


def mode_fractions(rec):
    """(fractions over the eight classes, category) following summary.rs `mode_fractions`."""
    fractions = [0.0] * 8
    assigned = 0.0
    for _rtyp, br in rec["modes"]:
        if br > 0.0:
            assigned += br
    if math.isnan(assigned) or assigned <= 0.0:
        fractions[OTHER] = 1.0
        return fractions, "no_modes"
    if assigned < 1.0 and abs(assigned - 1.0) > BRANCHING_TOLERANCE:
        scale, shortfall, category = 1.0, 1.0 - assigned, "shortfall_to_other"
    else:
        scale, shortfall = 1.0 / assigned, 0.0
        if abs(assigned - 1.0) <= 1e-12:
            category = "exact"
        elif assigned > 1.0 + BRANCHING_TOLERANCE:
            category = "excess_scaled"
        else:
            category = "within_tolerance_renormalised"
    for rtyp, br in rec["modes"]:
        if br > 0.0:
            fractions[mode_class(rtyp)] += br * scale
    fractions[OTHER] += shortfall
    return fractions, category


def recompute_rows(rows, records):
    """Independent per-nuclide values for the emitted rows (atoms are taken as given)."""
    out = []
    for row in rows:
        za, liso, atoms = row["Z"] * 1000 + row["A"], row["LISO"], row["atoms"]
        rec = records.get((za, liso))
        awr = rec["awr"] if rec else None
        if awr is not None and math.isfinite(awr) and awr > 0.0:
            atom_mass, from_a = awr * NEUTRON_MASS_AMU * AMU_G, False
        else:
            atom_mass, from_a = row["A"] * AMU_G, True
        lam = lam_of(rec)
        act = lam * atoms
        if rec is not None and lam > 0.0:
            wa, wb, wg = act * e_heavy(rec) * EV_J, act * e_light(rec) * EV_J, act * e_em(rec) * EV_J
        else:
            wa = wb = wg = 0.0
        modes, category = [0.0] * 8, None
        if rec is not None and lam > 0.0:
            fractions, category = mode_fractions(rec)
            modes = [act * f for f in fractions]
        out.append({"name": name_of(row["Z"], row["A"], liso), "Z": row["Z"], "A": row["A"], "LISO": liso,
                    "atoms": atoms, "grams": atoms * atom_mass, "mass_from_a": from_a, "act": act, "alpha": wa,
                    "beta": wb, "gamma": wg, "modes": modes, "category": category,
                    "half_life": rec["half_life"] if (rec is not None and lam > 0.0) else None,
                    "stable": rec is not None and lam == 0.0, "decay_data": rec is not None, "lam": lam,
                    "rec": rec})
    return out


def dominant_list(entries, top, slack=CUMULATIVE_SLACK):
    entries = [(n, v) for n, v in entries if v > 0.0]
    entries.sort(key=lambda e: (-e[1], e[0]))
    total = 0.0
    for _n, v in entries:
        total += v
    res = {"total": total, "top": [], "n_for_90_percent": 0, "n_for_99_percent": 0}
    if total <= 0.0:
        return res
    cumulative, n90, n99 = 0.0, None, None
    for rank, (name, value) in enumerate(entries):
        cumulative += value
        if n90 is None and cumulative >= 0.90 * total * (1.0 - slack):
            n90 = rank + 1
        if n99 is None and cumulative >= 0.99 * total * (1.0 - slack):
            n99 = rank + 1
        if rank < top:
            res["top"].append({"nuclide": name, "value": value, "percent": 100.0 * value / total,
                               "cumulative_percent": 100.0 * cumulative / total})
    res["n_for_90_percent"] = n90 if n90 is not None else len(entries)
    res["n_for_99_percent"] = n99 if n99 is not None else len(entries)
    return res


class Acc:
    """Accumulates comparisons of one quantity: count, worst error, and a capped list of mismatches."""

    def __init__(self):
        self.n = 0
        self.n_bad = 0
        self.worst_rel = 0.0
        self.worst_abs_where_zero = 0.0
        self.bad = []

    def check(self, where, got, exp):
        self.n += 1
        if got is None or exp is None:
            ok = got is None and exp is None
            err = 0.0 if ok else math.inf
        elif isinstance(exp, (bool, str, list)) or isinstance(got, (bool, str, list)):
            ok = got == exp
            err = 0.0 if ok else math.inf
        elif exp == 0.0:
            err = abs(got)
            ok = err <= ABS_TOL_ZERO
            self.worst_abs_where_zero = max(self.worst_abs_where_zero, err)
        else:
            err = abs(got - exp) / abs(exp)
            ok = err <= REL_TOL
            self.worst_rel = max(self.worst_rel, err)
        if not ok:
            self.n_bad += 1
            if len(self.bad) < MAX_LISTED:
                self.bad.append({"where": where, "got": got, "expected": exp})
        return ok

    def report(self):
        return {"n": self.n, "n_bad": self.n_bad, "worst_relative": self.worst_rel,
                "worst_absolute_where_expected_zero": self.worst_abs_where_zero, "mismatches": self.bad}


class AccSet:
    def __init__(self):
        self.accs = {}

    def check(self, quantity, where, got, exp):
        return self.accs.setdefault(quantity, Acc()).check(where, got, exp)

    def n_bad(self):
        return sum(a.n_bad for a in self.accs.values())

    def report(self):
        return {k: v.report() for k, v in sorted(self.accs.items())}


def verify_step_summary(accs, step_label, summary, records, spec_info):
    """G3 for one step. Returns the per-step facts used by the reporting (renormalisation stats, recomputed rows)."""
    rows_in = summary["nuclides"]
    rows = recompute_rows(rows_in, records)
    tag = step_label
    # ---- nuclide table
    names = [r["name"] for r in rows]
    accs.check("nuclide.table_names_unique", tag, len(set(names)), len(names))
    accs.check("nuclide.table_count_equals_n_nuclides", tag, len(rows), summary["totals"]["n_nuclides"])
    accs.check("nuclide.table_sorted_desc_by_atoms", tag,
               all(a["atoms"] >= b["atoms"] for a, b in zip(rows_in, rows_in[1:])), True)
    accs.check("nuclide.atoms_positive", tag, all(r["atoms"] > 0.0 for r in rows), True)
    for src, r in zip(rows_in, rows):
        w = f"{tag}/{r['name']}"
        accs.check("nuclide.name", w, src["nuclide"], r["name"])
        accs.check("nuclide.activity_Bq", w, src["activity_Bq"], r["act"])
        accs.check("nuclide.alpha_W", w, src["alpha_W"], r["alpha"])
        accs.check("nuclide.beta_W", w, src["beta_W"], r["beta"])
        accs.check("nuclide.gamma_W", w, src["gamma_W"], r["gamma"])
        accs.check("nuclide.grams", w, src["grams"], r["grams"])
        accs.check("nuclide.half_life_s", w, src["half_life_s"], r["half_life"])
        accs.check("nuclide.mass_from_a", w, src["mass_from_a"], r["mass_from_a"])
        accs.check("nuclide.stable", w, src["stable"], r["stable"])
        accs.check("nuclide.decay_data", w, src["decay_data"], r["decay_data"])
    # ---- totals
    act = alpha = beta = gamma = grams = 0.0
    act_x3 = heat_x3 = gamma_bq = 0.0
    by_mode = [0.0] * 8
    for r in rows:
        act += r["act"]
        alpha += r["alpha"]
        beta += r["beta"]
        gamma += r["gamma"]
        grams += r["grams"]
        if r["gamma"] > 0.0:
            gamma_bq += r["act"]
        if not (r["Z"] == 1 and r["A"] == 3 and r["LISO"] == 0):
            act_x3 += r["act"]
            heat_x3 += r["alpha"] + r["beta"] + r["gamma"]
        for i in range(8):
            by_mode[i] += r["modes"][i]
    heat = alpha + beta + gamma
    t = summary["totals"]
    expected_totals = {
        "activity_Bq": act, "activity_Ci": act / BQ_PER_CI, "alpha_W": alpha, "beta_W": beta, "gamma_W": gamma,
        "heat_W": heat, "alpha_Bq": by_mode[ALPHA], "beta_Bq": by_mode[BETA_MINUS] + by_mode[EC_BETA_PLUS]
        + by_mode[ISOMERIC], "gamma_Bq": gamma_bq, "activity_excl_H3_Bq": act_x3, "heat_excl_H3_W": heat_x3,
        "mass_g": grams,
    }
    for key, exp in expected_totals.items():
        accs.check(f"totals.{key}", tag, t[key], exp)
    for key, exp in zip(MODE_KEYS, by_mode):
        accs.check(f"totals.activity_by_mode.{key}", tag, t["activity_by_mode"][key], exp)
    accs.check("totals.n_nuclides", tag, t["n_nuclides"], len(rows))

    def scaled_expected(mult):
        return {"multiplier": mult, "activity_Bq": act * mult, "activity_Ci": act * mult / BQ_PER_CI,
                "alpha_W": alpha * mult, "beta_W": beta * mult, "gamma_W": gamma * mult,
                "heat_W": heat * mult, "activity_excl_H3_Bq": act_x3 * mult, "heat_excl_H3_W": heat_x3 * mult}

    for block, mult in (("absolute", spec_info["mass_g"]), ("per_cm3", spec_info["density_g_cm3"])):
        got = t.get(block)
        if mult is None:
            accs.check(f"totals.{block}.absent_without_density", tag, got is None, True)
            continue
        if got is None:
            accs.check(f"totals.{block}.present", tag, None, 1.0)
            continue
        for key, exp in scaled_expected(mult).items():
            accs.check(f"totals.{block}.{key}", tag, got[key], exp)
    # ---- elements
    if summary.get("elements") is not None:
        agg = {}
        for r in rows:
            e = agg.setdefault(r["Z"], {"atoms": 0.0, "grams": 0.0, "act": 0.0, "alpha": 0.0, "beta": 0.0,
                                        "gamma": 0.0})
            e["atoms"] += r["atoms"]
            e["grams"] += r["grams"]
            e["act"] += r["act"]
            e["alpha"] += r["alpha"]
            e["beta"] += r["beta"]
            e["gamma"] += r["gamma"]
        got_elems = summary["elements"]
        accs.check("element.row_count", tag, len(got_elems), len(agg))
        accs.check("element.ascending_z", tag, [e["Z"] for e in got_elems], sorted(agg))
        for e in got_elems:
            exp = agg.get(e["Z"])
            w = f"{tag}/Z={e['Z']}"
            if exp is None:
                accs.check("element.unexpected_z", w, e["Z"], None)
                continue
            accs.check("element.symbol", w, e["symbol"], SYMBOLS[e["Z"] - 1])
            for key, ek in (("atoms", "atoms"), ("grams", "grams"), ("activity_Bq", "act"), ("alpha_W", "alpha"),
                            ("beta_W", "beta"), ("gamma_W", "gamma")):
                accs.check(f"element.{key}", w, e[key], exp[ek])
    # ---- dominant lists
    top = spec_info["dominant_top"]
    values = {
        "activity": [(r["name"], r["act"]) for r in rows],
        "heat": [(r["name"], r["alpha"] + r["beta"] + r["gamma"]) for r in rows],
        "alpha_heat": [(r["name"], r["alpha"]) for r in rows],
        "beta_heat": [(r["name"], r["beta"]) for r in rows],
        "gamma_heat": [(r["name"], r["gamma"]) for r in rows],
    }
    for key in DOMINANT_KEYS:
        exp = dominant_list(values[key], top)
        got = summary["dominant"][key]
        w = f"{tag}/{key}"
        accs.check(f"dominant.{key}.total", w, got["total"], exp["total"])
        accs.check(f"dominant.{key}.top_length", w, len(got["top"]), len(exp["top"]))
        lookup = dict(values[key])
        for rank, (g, e) in enumerate(zip(got["top"], exp["top"]), start=1):
            wr = f"{w}#{rank}"
            # an exact tie in value may legitimately swap two names; anything else is a mismatch
            name_ok = g["nuclide"] == e["nuclide"] or (
                g["nuclide"] in lookup and rel_diff(lookup[g["nuclide"]], e["value"]) <= REL_TOL)
            accs.check(f"dominant.{key}.rank_name", wr, name_ok, True)
            accs.check(f"dominant.{key}.value", wr, g["value"], e["value"])
            accs.check(f"dominant.{key}.percent", wr, g["percent"], e["percent"])
            accs.check(f"dominant.{key}.cumulative_percent", wr, g["cumulative_percent"], e["cumulative_percent"])
        for count in ("n_for_90_percent", "n_for_99_percent"):
            lo, hi = (dominant_list(values[key], top, slack)[count] for slack in (CUMULATIVE_SLACK, 1e-9))
            ok = min(lo, hi) <= got[count] <= max(lo, hi)
            accs.check(f"dominant.{key}.{count}", w, got[count] if not ok else exp[count], exp[count])
    return rows


# ------------------------------------------------------------------------------ gates


def run_binary(binary, spec_path, out_path, log_path, cwd, timeout):
    with open(log_path, "wb") as log:
        proc = subprocess.run([str(binary), "run", str(spec_path), str(out_path)], cwd=str(cwd), stdout=log,
                              stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode


def strip_timing(obj, top=True):
    """Deep copy without wall-clock timing keys. Returns (copy, list of dropped key paths)."""
    dropped = []

    def walk(node, path, is_top):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if (is_top and k in TIMING_TOP_LEVEL) or k in TIMING_ANYWHERE:
                    dropped.append(f"{path}/{k}")
                    continue
                out[k] = walk(v, f"{path}/{k}", False)
            return out
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]", False) for i, v in enumerate(node)]
        return node

    return walk(obj, "", top), dropped


def first_differences(a, b, path="", limit=10, out=None):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: present in only one result")
            else:
                first_differences(a[k], b[k], f"{path}/{k}", limit, out)
            if len(out) >= limit:
                break
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            first_differences(x, y, f"{path}[{i}]", limit, out)
            if len(out) >= limit:
                break
    elif a != b:
        out.append(f"{path}: {a!r} vs {b!r}")
    return out


def gate_g2(ref, cand, spec_sha_ok):
    ref_s, ref_dropped = strip_timing(ref)
    cand_s, cand_dropped = strip_timing(cand)
    equal = canonical(ref_s) == canonical(cand_s)
    has_summary = [bool(s.get("summary")) for r in (ref, cand) for s in r.get("steps", [])]
    out = {"timing_keys_dropped_reference": sorted(set(ref_dropped)),
           "timing_keys_dropped_candidate": sorted(set(cand_dropped)),
           "timing_keys_policy": {"top_level": list(TIMING_TOP_LEVEL), "anywhere": list(TIMING_ANYWHERE)},
           "canonical_equal_without_timing": equal, "spec_sha256_matches_protocol": spec_sha_ok,
           "no_summary_key_in_either_result": not any(has_summary), "steps": len(ref.get("steps", []))}
    if not equal:
        out["first_differences"] = first_differences(ref_s, cand_s)
    out["pass"] = bool(equal and spec_sha_ok and not any(has_summary))
    return out


def absolutize_paths(node):
    """Resolve relative data paths against the repo root, in place."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("path", "primary", "fallback", "overrides", "file") and isinstance(v, str) and v \
                    and not v.startswith("catalog:") and not Path(v).is_absolute() and (ROOT / v).exists():
                node[k] = str((ROOT / v).resolve())
            else:
                absolutize_paths(v)
    elif isinstance(node, list):
        for v in node:
            absolutize_paths(v)


def make_variant(base_spec, mode, title_suffix):
    spec = copy.deepcopy(base_spec)
    absolutize_paths(spec)
    spec["summary"] = {}
    spec["material"]["density_g_cm3"] = DENSITY_G_CM3
    if mode is not None:
        spec.setdefault("options", {})["mode"] = mode
    spec["title"] = f"{spec.get('title', 'P122')} [{title_suffix}]"
    return spec


def decay_files_from_spec(spec):
    decay = spec.get("decay", {})
    extra = sorted(set(decay) - {"primary", "fallback"})
    return decay.get("primary"), decay.get("fallback") or None, extra


def gate_g3(results, spec):
    """G3 over every step of every summary variant result. `results` is {label: result}. Returns the gate record;
    a nuclide with no record in either decay file is legitimate (the per-row `decay_data` flag is checked against
    this parse), so missing records are reported but do not fail the gate on their own."""
    primary, fallback, extra = decay_files_from_spec(spec)
    out = {"decay_primary": primary, "decay_fallback": fallback, "unsupported_decay_keys": extra,
           "options_gas": bool((spec.get("options") or {}).get("gas", False))}
    needed = set()
    for res in results.values():
        for step in res["steps"]:
            for row in step["summary"].get("nuclides") or []:
                needed.add((row["Z"] * 1000 + row["A"], row["LISO"]))
    out["distinct_nuclides_needed"] = len(needed)
    if extra:
        out["error"] = f"decay section carries {extra}; this checker parses only primary and fallback"
        out["pass"] = False
        return out
    records, info = load_decay(primary, fallback, needed)
    out["decay"] = info
    out["decay_sha256"] = {"primary": sha256_file(primary), "fallback": sha256_file(fallback) if fallback else None}
    accs = AccSet()
    spec_info = {"mass_g": spec["material"]["mass_g"], "density_g_cm3": spec["material"].get("density_g_cm3"),
                 "dominant_top": (spec.get("summary") or {}).get("dominant_top", 10)}
    # the variant summary is `{}`, so the defaults apply: unfiltered table, top 10
    categories = {}
    seen_categories = {}
    for label, res in results.items():
        for i, step in enumerate(res["steps"]):
            rows = verify_step_summary(accs, f"{label}/step{i}", step["summary"], records, spec_info)
            for r in rows:
                if r["category"] is not None:
                    seen_categories[(r["Z"], r["A"], r["LISO"])] = r["category"]
    for key, cat in seen_categories.items():
        categories.setdefault(cat, []).append(name_of(*key))
    out["mode_partition_normalisation"] = {
        "distinct_decaying_nuclides": len(seen_categories),
        "counts": {k: len(v) for k, v in sorted(categories.items())},
        "needed_renormalisation": sum(len(v) for k, v in categories.items() if k != "exact"),
        "examples_not_exact": {k: sorted(v)[:MAX_LISTED] for k, v in sorted(categories.items()) if k != "exact"},
        "rule": "summary.rs mode_fractions: positive branching ratios normalised to unit sum; a sum short of one "
                "by more than 1e-5 books the shortfall to `other`; no modes books everything to `other`",
    }
    out["tolerance"] = {"relative": REL_TOL, "absolute_where_expected_zero": ABS_TOL_ZERO}
    out["quantities"] = accs.report()
    out["n_comparisons"] = sum(a.n for a in accs.accs.values())
    out["n_mismatches"] = accs.n_bad()
    out["pass"] = accs.n_bad() == 0
    return out


def gate_g4(results, mode_info):
    out = {}
    declared, other = results["declared"], results["other"]
    # ---- (a) consistency with the existing fields
    a = {"tolerance_relative": REL_TOL, "steps": [], "worst_relative": {}, "unexplained": []}
    worst = {}
    for label, res in results.items():
        for i, step in enumerate(res["steps"]):
            s = step["summary"]["totals"]
            existing_act = math.fsum(step["activity_Bq_per_g"].values())
            h = step["heat_W_per_g"]
            pairs = {"activity_Bq": (s["activity_Bq"], existing_act), "alpha_W": (s["alpha_W"], h["alpha"]),
                     "beta_W": (s["beta_W"], h["beta"]), "gamma_W": (s["gamma_W"], h["gamma"]),
                     "heat_W": (s["heat_W"], h["total"])}
            rows = step["summary"].get("nuclides") or []
            absent = [r for r in rows if r["activity_Bq"] > 0.0 and r["nuclide"] not in step["activity_Bq_per_g"]]
            absent_act = math.fsum(r["activity_Bq"] for r in absent)
            absent_heat = {"alpha_W": math.fsum(r["alpha_W"] for r in absent),
                           "beta_W": math.fsum(r["beta_W"] for r in absent),
                           "gamma_W": math.fsum(r["gamma_W"] for r in absent)}
            absent_heat["heat_W"] = sum(absent_heat.values())
            absent_heat["activity_Bq"] = absent_act
            entry = {"run": label, "step": i, "t_s": step["t_s"]}
            per_row = [r["nuclide"] for r in rows
                       if r["activity_Bq"] > 0.0 and r["nuclide"] in step["activity_Bq_per_g"]
                       and rel_diff(r["activity_Bq"], step["activity_Bq_per_g"][r["nuclide"]]) > REL_TOL]
            if per_row:
                entry["rows_differing_from_existing_activity"] = per_row[:MAX_LISTED]
            for key, (got, ref) in pairs.items():
                d = rel_diff(got, ref)
                worst[key] = max(worst.get(key, 0.0), d)
                entry[key] = {"summary": got, "existing": ref, "relative": d}
                if d > REL_TOL:
                    explained = abs((got - ref) - absent_heat[key]) <= REL_TOL * max(abs(got), abs(ref))
                    entry[key]["explained_by_omitted_states"] = bool(explained)
                    entry[key]["omitted_contribution"] = absent_heat[key]
                    if not explained:
                        a["unexplained"].append(f"{label}/step{i}/{key}")
            if absent:
                entry["states_in_summary_not_in_activity_Bq_per_g"] = [
                    {"nuclide": r["nuclide"], "activity_Bq": r["activity_Bq"]}
                    for r in sorted(absent, key=lambda r: -r["activity_Bq"])[:MAX_LISTED]]
            if per_row:
                a["unexplained"].append(f"{label}/step{i}/per-nuclide activity differs from the existing map")
            # only steps with a finding are kept, the rest are summarised by the worst values
            if any(isinstance(entry.get(k), dict) and entry[k]["relative"] > REL_TOL for k in pairs) or absent \
                    or per_row:
                a["steps"].append(entry)
    a["worst_relative"] = worst
    a["pass"] = not a["unexplained"]
    out["a_existing_fields"] = a
    # ---- (b) mode partition
    b = {"tolerance_relative": MODE_PARTITION_TOL, "worst_relative": 0.0, "bad": []}
    for label, res in results.items():
        for i, step in enumerate(res["steps"]):
            t = step["summary"]["totals"]
            parts = math.fsum(t["activity_by_mode"][k] for k in MODE_KEYS)
            d = rel_diff(parts, t["activity_Bq"])
            b["worst_relative"] = max(b["worst_relative"], d)
            if d > MODE_PARTITION_TOL:
                b["bad"].append({"run": label, "step": i, "sum": parts, "total": t["activity_Bq"]})
    b["pass"] = not b["bad"]
    out["b_mode_partition"] = b
    # ---- (c) trace against coupled
    c = trace_vs_coupled(declared, other, mode_info)
    out["c_trace_vs_coupled"] = c
    out["pass"] = bool(a["pass"] and b["pass"] and c["pass"])
    return out


def row_quantities(row):
    """The per-row values that feed each total, for the trace/coupled floor bound."""
    heat = row["alpha_W"] + row["beta_W"] + row["gamma_W"]
    is_h3 = (row["Z"], row["A"], row["LISO"]) == (1, 3, 0)
    return {"activity_Bq": row["activity_Bq"], "alpha_W": row["alpha_W"], "beta_W": row["beta_W"],
            "gamma_W": row["gamma_W"], "heat_W": heat, "activity_excl_H3_Bq": 0.0 if is_h3 else row["activity_Bq"],
            "heat_excl_H3_W": 0.0 if is_h3 else heat, "mass_g": row["grams"]}


def trace_vs_coupled(declared, other, mode_info):
    modes = {mode_info["declared_resolved"]: declared, mode_info["other"]: other}
    c = {
        "declared_mode_resolved": mode_info["declared_resolved"], "other_mode": mode_info["other"],
        "tolerance_source": [
            "protocols/ACTINV-P5_PROTOCOL.md G7: trace and coupled agree to 1e-8 on the FNS Fe spec "
            f"(relative {TRACE_COUPLED_REL_TOL:g}, applied here to every total)",
            "protocols/ACTINV-P9_PROTOCOL.md G3, controls/g3_p9_coupled_auto.py and controls/g7_mode_selection.py: "
            "where the modes differ they agree within the coupled CRAM floor (numerical_floor_atoms_per_g); "
            "g7_mode_selection.py applies it, per nuclide, to the nuclides carrying at least 1 % of the activity",
        ],
        "rule": "A total passes if it agrees to 1e-8 relative, OR differs by no more than the coupled floor "
                "(atoms/g) times the per-atom coefficient summed over the states that carry at least 1 % of that "
                "total in the trace run. Separately, every nuclide with at least 1 % of the trace activity must "
                "have |N_coupled - N_trace| <= the coupled floor (the g7_mode_selection.py rule).",
        "note": "results/g7_mode_selection.json records a trace/coupled heat difference of 2.5e-4 on this spec "
                "(its pass criterion is the floor rule), so the 1e-8 text of P5 G7 is not what that control "
                "enforces. Both measures are reported per step.",
        "significant_share": 0.01, "steps": [], "bad": [],
    }
    if set(modes) != {"trace", "coupled"}:
        c["error"] = f"expected one trace and one coupled run, got {sorted(modes)}"
        c["pass"] = False
        return c
    trace, coupled = modes["trace"], modes["coupled"]
    if len(trace["steps"]) != len(coupled["steps"]):
        c["error"] = "step counts differ between the modes"
        c["pass"] = False
        return c
    keys = ["activity_Bq", "alpha_W", "beta_W", "gamma_W", "heat_W", "activity_excl_H3_Bq", "heat_excl_H3_W",
            "mass_g"]
    worst_rel = {k: 0.0 for k in keys}
    n_strict = n_floor_only = 0
    sig_worst = 0.0
    for i, (st, sc) in enumerate(zip(trace["steps"], coupled["steps"])):
        if rel_diff(st["t_s"], sc["t_s"]) > 1e-12:
            c["bad"].append({"step": i, "note": "t_s differs", "trace": st["t_s"], "coupled": sc["t_s"]})
            continue
        tt, tc = st["summary"]["totals"], sc["summary"]["totals"]
        floor = sc["numerical_floor_atoms_per_g"]
        rows_t, rows_c = st["summary"]["nuclides"], sc["summary"]["nuclides"]
        quantities_t = [(r, row_quantities(r)) for r in rows_t]
        entry = {"step": i, "t_s": st["t_s"], "coupled_floor_atoms_per_g": floor}
        step_ok = True
        for k in keys:
            d = rel_diff(tt[k], tc[k])
            worst_rel[k] = max(worst_rel[k], d)
            bound = floor * sum(q[k] / r["atoms"] for r, q in quantities_t
                                if tt[k] > 0.0 and q[k] >= c["significant_share"] * tt[k])
            strict = d <= TRACE_COUPLED_REL_TOL
            within_floor = abs(tt[k] - tc[k]) <= bound
            entry[k] = {"trace": tt[k], "coupled": tc[k], "relative": d, "floor_bound_abs": bound,
                        "within_1e-8": strict, "within_floor": within_floor}
            if strict:
                n_strict += 1
            elif within_floor:
                n_floor_only += 1
            else:
                step_ok = False
                c["bad"].append({"step": i, "quantity": k, "relative": d, "abs_diff": abs(tt[k] - tc[k]),
                                 "floor_bound_abs": bound})
        atoms_c = {r["nuclide"]: r["atoms"] for r in rows_c}
        total_t = tt["activity_Bq"]
        sig = [r for r in rows_t if total_t > 0.0 and r["activity_Bq"] / total_t >= c["significant_share"]]
        worst_abs = max((abs(atoms_c.get(r["nuclide"], 0.0) - r["atoms"]) for r in sig), default=0.0)
        entry["significant_nuclides"] = sorted(r["nuclide"] for r in sig)
        entry["significant_worst_abs_atoms_difference"] = worst_abs
        entry["significant_within_floor"] = worst_abs <= floor
        sig_worst = max(sig_worst, worst_abs / floor if floor > 0 else (math.inf if worst_abs > 0 else 0.0))
        if worst_abs > floor:
            step_ok = False
            c["bad"].append({"step": i, "note": "significant nuclide differs by more than the coupled floor",
                             "worst_abs_atoms": worst_abs, "floor": floor})
        entry["ok"] = step_ok
        c["steps"].append(entry)
    c["worst_relative_difference"] = worst_rel
    c["comparisons_within_1e-8"] = n_strict
    c["comparisons_within_floor_only"] = n_floor_only
    c["significant_worst_atoms_difference_over_floor"] = sig_worst
    c["pass"] = not c["bad"]
    return c


# ------------------------------------------------------------------------------ G5: FISPACT-II comparison

NUM = r"[-+]?\d+\.?\d*(?:[Ee][-+]?\d+)?"


def fnum(text):
    return float(text)


def fispact_nuclide_name(sym, a, suffix):
    base = f"{sym}{a}"
    if suffix == "m":
        return base + "m1"
    if suffix == "n":
        return base + "m2"
    return base


def parse_dominant_block(lines, start):
    """Parse one dominant-nuclide table whose first header line is lines[start]. Returns (columns, next index);
    each column is {"title", "total", "rows": [{"rank", "nuclide", "value", "percent"}]}."""
    header = lines[start]
    pos = [m.start() for m in re.finditer("NUCLIDE", header)]
    bounds = pos + [10 ** 6]
    titles = []
    for i in range(len(pos)):
        titles.append(re.sub(r"\s+", " ", header[pos[i]:bounds[i + 1]].replace("NUCLIDE", "").strip()))
    columns = [{"title": t, "total": None, "rows": []} for t in titles]
    i = start + 2  # the unit line follows the header
    seg_re = re.compile(r"^\s*([A-Z][a-z]?)\s+(\d+)([a-z])?\s+(" + NUM + r")\s+(" + NUM + r")\s*$")
    while i < len(lines) and lines[i].strip():
        line = lines[i]
        rank_m = re.match(r"^\s*(\d+)\s", line)
        total_row = line.strip().startswith("Total")
        for ci in range(len(pos)):
            seg = line[pos[ci]:bounds[ci + 1]]
            if total_row:
                m = re.match(r"^\s*Total\s+(" + NUM + ")", seg)
                if m:
                    columns[ci]["total"] = fnum(m.group(1))
                continue
            m = seg_re.match(seg)
            if m and rank_m:
                columns[ci]["rows"].append({"rank": int(rank_m.group(1)),
                                            "nuclide": fispact_nuclide_name(m.group(1), m.group(2), m.group(3)),
                                            "value": fnum(m.group(4)), "percent": fnum(m.group(5))})
        i += 1
    return columns, i


def parse_fispact_out(path):
    """Parse the per-interval inventory blocks of a FISPACT-II output."""
    lines = Path(path).read_text(errors="replace").splitlines()
    starts = [i for i, ln in enumerate(lines) if re.search(r"TIME INTERVAL\s+\d+\s+\*", ln)]
    intervals = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = lines[s:e]
        head = block[0]
        iv = {"interval": int(re.search(r"TIME INTERVAL\s+(\d+)", head).group(1)), "line": s + 1}
        m = re.search(r"COOLING TIME IS\s+(" + NUM + r")\s+SECS", head)
        if m:
            iv["kind"], iv["time_s"] = "cooling", fnum(m.group(1))
        else:
            m = re.search(r"TIME IS\s+(" + NUM + r")\s+SECS", head)
            iv["kind"], iv["time_s"] = "irradiation", fnum(m.group(1)) if m else None
        m = re.search(r"ELAPSED TIME IS\s+(\S+\s*\S*)\s+\*", head)
        iv["elapsed_text"] = m.group(1).strip() if m else None
        for j, ln in enumerate(block):
            m = re.search(r"ALPHA BECQUERELS =\s*(" + NUM + r")\s+BETA BECQUERELS =\s*(" + NUM
                          + r")\s+GAMMA BECQUERELS =\s*(" + NUM + ")", ln)
            if m:
                iv["alpha_Bq"], iv["beta_Bq"], iv["gamma_Bq"] = (fnum(m.group(k)) for k in (1, 2, 3))
            m = re.search(r"TOTAL ACTIVITY FOR ALL MATERIALS\s+(" + NUM + r")\s+Bq", ln)
            if m:
                iv["activity_Bq"] = fnum(m.group(1))
                m2 = re.search(r"(" + NUM + r")\s+Ci/cc\s+DENSITY\s+(" + NUM + ")", block[j + 1])
                if m2:
                    iv["activity_Ci_per_cm3"], iv["density_g_cm3"] = fnum(m2.group(1)), fnum(m2.group(2))
            m = re.search(r"TOTAL ACTIVITY EXCLUDING TRITIUM\s+(" + NUM + r")\s+Bq", ln)
            if m:
                iv["activity_excl_H3_Bq"] = fnum(m.group(1))
                m2 = re.search(r"(" + NUM + r")\s+Ci/cc", block[j + 1])
                if m2:
                    iv["activity_excl_H3_Ci_per_cm3"] = fnum(m2.group(1))
            if "TOTAL CURIES" in ln and j + 2 < len(block):
                m2 = re.match(r"\s*(" + NUM + ")", block[j + 2])
                if m2:
                    iv["activity_Ci"] = fnum(m2.group(1))
            m = re.search(r"TOTAL ALPHA HEAT PRODUCTION\s+(" + NUM + r")\s+kW", ln)
            if m:
                iv["alpha_heat_kW"] = fnum(m.group(1))
            m = re.search(r"TOTAL BETA\s+HEAT PRODUCTION\s+(" + NUM + r")\s+kW", ln)
            if m:
                iv["beta_heat_kW"] = fnum(m.group(1))
            m = re.search(r"TOTAL GAMMA HEAT PRODUCTION\s+(" + NUM + r")\s+kW\s+TOTAL HEAT PRODUCTION\s+(" + NUM
                          + r")\s+kW", ln)
            if m:
                iv["gamma_heat_kW"], iv["heat_kW"] = fnum(m.group(1)), fnum(m.group(2))
            m = re.search(r"INITIAL TOTAL MASS OF MATERIAL\s+(" + NUM + r")\s+kg\s+TOTAL HEAT EX TRITIUM\s+("
                          + NUM + r")\s+kW", ln)
            if m:
                iv["initial_mass_kg"], iv["heat_excl_H3_kW"] = fnum(m.group(1)), fnum(m.group(2))
            m = re.match(r"^\S?\s*TOTAL MASS OF MATERIAL\s+(" + NUM + r")\s+kg", ln)
            if m:
                iv["mass_kg"] = fnum(m.group(1))
            if "COMPOSITION  OF  MATERIAL  BY  ELEMENT" in ln:
                elems = []
                for k in range(j + 1, len(block)):
                    m2 = re.match(r"^\s*(\d+)\s+([A-Z][a-z]?)\s+((?:" + NUM + r"\s*){9,10})$", block[k])
                    if m2:
                        v = [fnum(x) for x in m2.group(3).split()]
                        elems.append({"Z": int(m2.group(1)), "symbol": m2.group(2), "atoms": v[0], "grams": v[2],
                                      "beta_kW": v[4], "gamma_kW": v[6], "alpha_kW": v[8]})
                    elif elems and not block[k].strip():
                        break
                iv["elements"] = elems
            if "DOMINANT NUCLIDES" in ln:
                k = j + 1
                while k < len(block) and "NUCLIDE" not in block[k]:
                    k += 1
                cols1, k = parse_dominant_block(block, k)
                while k < len(block) and "NUCLIDE" not in block[k]:
                    k += 1
                cols2, _ = parse_dominant_block(block, k) if k < len(block) else ([], k)
                iv["dominant"] = {"activity": cols1[0] if cols1 else None, "heat": cols1[1] if len(cols1) > 1
                                  else None, "gamma_heat": cols2[0] if cols2 else None,
                                  "beta_heat": cols2[1] if len(cols2) > 1 else None}
        intervals.append(iv)
    return intervals


def ratio(actinv, fispact):
    if actinv is None or fispact is None or fispact == 0.0:
        return None
    return actinv / fispact


def g5_compare(result, spec, fispact_path):
    out = {"report_only": True, "fispact_out": str(fispact_path), "fispact_sha256": sha256_file(fispact_path),
           "note": "Different libraries (TENDL-2017 vs TENDL-2025): differences are expected. No pass threshold.",
           "intervals": []}
    intervals = parse_fispact_out(fispact_path)
    out["fispact_intervals_parsed"] = len(intervals)
    steps = result["steps"]
    irr_end = max((s["t_s"] for s in steps if s["flux"] > 0.0), default=0.0)
    out["time_mapping"] = ("FISPACT irradiation interval: ACTINV t_s = 'TIME IS' seconds; cooling interval: "
                           "ACTINV t_s = end of the last irradiating step (%g s) + the running sum of the 'COOLING "
                           "TIME IS' seconds" % irr_end)
    mass_g = spec["material"]["mass_g"]
    cum_cool = 0.0
    for iv in intervals:
        if iv["kind"] == "cooling":
            cum_cool += iv["time_s"]
            t = irr_end + cum_cool
        else:
            t = iv["time_s"]
        entry = {"interval": iv["interval"], "kind": iv["kind"], "fispact_time_s": iv["time_s"], "mapped_t_s": t}
        match = None
        for i, s in enumerate(steps):
            if abs(s["t_s"] - t) <= 1e-6 * max(1.0, abs(t)):
                match = i
                break
        if match is None:
            entry["actinv_step"] = None
            entry["note"] = "no ACTINV step at this time"
            out["intervals"].append(entry)
            continue
        step = steps[match]
        sm = step["summary"]
        tot = sm["totals"]
        absolute = tot["absolute"]
        per_cm3 = tot.get("per_cm3")
        entry["actinv_step"] = match
        entry["actinv_t_s"] = step["t_s"]
        entry["initial_mass_kg_fispact"] = iv.get("initial_mass_kg")
        entry["mass_g_spec"] = mass_g
        actinv = {
            "activity_Ci": absolute["activity_Ci"], "activity_Bq": absolute["activity_Bq"],
            "alpha_Bq": tot["alpha_Bq"] * mass_g, "beta_Bq": tot["beta_Bq"] * mass_g,
            "gamma_Bq": tot["gamma_Bq"] * mass_g, "activity_excl_H3_Bq": absolute["activity_excl_H3_Bq"],
            "activity_Ci_per_cm3": per_cm3["activity_Ci"] if per_cm3 else None,
            "activity_excl_H3_Ci_per_cm3": (per_cm3["activity_excl_H3_Bq"] / BQ_PER_CI) if per_cm3 else None,
            "alpha_heat_kW": absolute["alpha_W"] / 1e3, "beta_heat_kW": absolute["beta_W"] / 1e3,
            "gamma_heat_kW": absolute["gamma_W"] / 1e3, "heat_kW": absolute["heat_W"] / 1e3,
            "heat_excl_H3_kW": absolute["heat_excl_H3_W"] / 1e3,
        }
        quantities = {}
        for key, a_val in actinv.items():
            f_val = iv.get(key)
            quantities[key] = {"actinv": a_val, "fispact": f_val, "ratio_actinv_over_fispact": ratio(a_val, f_val)}
        entry["quantities"] = quantities
        # element composition
        elems = {}
        for f in iv.get("elements", []):
            a = next((e for e in sm.get("elements") or [] if e["Z"] == f["Z"]), None)
            if a is None:
                elems[f["symbol"]] = {"actinv": None, "fispact": f}
                continue
            row = {}
            for key, a_val in (("atoms", a["atoms"] * mass_g), ("grams", a["grams"] * mass_g),
                               ("beta_kW", a["beta_W"] * mass_g / 1e3), ("gamma_kW", a["gamma_W"] * mass_g / 1e3),
                               ("alpha_kW", a["alpha_W"] * mass_g / 1e3)):
                row[key] = {"actinv": a_val, "fispact": f[key], "ratio_actinv_over_fispact": ratio(a_val, f[key])}
            elems[f["symbol"]] = row
        entry["elements"] = elems
        entry["elements_only_in_actinv"] = sorted(e["symbol"] for e in sm.get("elements") or []
                                                  if e["Z"] not in {f["Z"] for f in iv.get("elements", [])})
        # dominant lists
        dom = {}
        for key, scale in (("activity", mass_g), ("heat", mass_g / 1e3), ("gamma_heat", mass_g / 1e3),
                           ("beta_heat", mass_g / 1e3)):
            f = (iv.get("dominant") or {}).get(key)
            a = sm["dominant"][key]
            if not f:
                dom[key] = {"fispact": None}
                continue
            f_rows = f["rows"]
            a_top = a["top"]
            cumulative = 0.0
            n90 = n99 = None
            for r in f_rows:
                cumulative += r["percent"]
                if n90 is None and cumulative >= 90.0:
                    n90 = r["rank"]
                if n99 is None and cumulative >= 99.0:
                    n99 = r["rank"]
            ranks = []
            for rank in range(1, min(len(a_top), len(f_rows)) + 1):
                ar, fr = a_top[rank - 1], f_rows[rank - 1]
                ranks.append({"rank": rank, "actinv": ar["nuclide"], "fispact": fr["nuclide"],
                              "same_nuclide": ar["nuclide"] == fr["nuclide"],
                              "actinv_value": ar["value"] * scale, "fispact_value": fr["value"],
                              "ratio_actinv_over_fispact": ratio(ar["value"] * scale, fr["value"]),
                              "actinv_percent": ar["percent"], "fispact_percent": fr["percent"]})
            a_names, f_names = [r["nuclide"] for r in a_top], [r["nuclide"] for r in f_rows[:len(a_top)]]
            dom[key] = {
                "actinv_total": a["total"] * scale, "fispact_total": f["total"],
                "total_ratio_actinv_over_fispact": ratio(a["total"] * scale, f["total"]),
                "top_ranked": ranks, "top_overlap": len(set(a_names) & set(f_names)),
                "top_compared": len(f_names),
                "n_for_90_percent": {"actinv": a["n_for_90_percent"], "fispact_from_printed_rows": n90},
                "n_for_99_percent": {"actinv": a["n_for_99_percent"], "fispact_from_printed_rows": n99},
                "fispact_rows_printed": len(f_rows),
            }
        entry["dominant"] = dom
        out["intervals"].append(entry)
    return out


def print_g5_table(g5):
    cols = ["activity_Ci", "activity_excl_H3_Bq", "beta_Bq", "heat_kW", "heat_excl_H3_kW", "gamma_heat_kW"]
    print("G5 (report only): ACTINV / FISPACT-II ratios by FISPACT interval")
    print("  int  mapped_t_s    step  " + "  ".join(f"{c[:15]:>15}" for c in cols) + "  top1 act/heat")
    for e in g5["intervals"]:
        if e.get("actinv_step") is None:
            print(f"  {e['interval']:>3}  {e['mapped_t_s']:>10.4g}  (no ACTINV step)")
            continue
        cells = []
        for c in cols:
            r = e["quantities"][c]["ratio_actinv_over_fispact"]
            cells.append(f"{r:>15.4g}" if r is not None else f"{'n/a':>15}")
        tops = []
        for k in ("activity", "heat"):
            ranks = e["dominant"].get(k, {}).get("top_ranked") or []
            tops.append("=" if ranks and ranks[0]["same_nuclide"] else ("x" if ranks else "-"))
        print(f"  {e['interval']:>3}  {e['mapped_t_s']:>10.4g}  {e['actinv_step']:>4}  " + "  ".join(cells)
              + "  " + "/".join(tops))


# ------------------------------------------------------------------------------ driver


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", required=True, help="binary built from the P122 base commit")
    ap.add_argument("--candidate", required=True, help="binary built from the P122 implementation commit")
    ap.add_argument("--fispact-out", default=str(Path("~/nuclear-data/conderc-fns/fns/Fe/"
                                                     "TENDL-2017_1996exp_5min.out").expanduser()))
    ap.add_argument("--workdir", default="target/p122-work")
    ap.add_argument("--out", default="results/p122_summary.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run")
    ap.add_argument("--reuse", action="store_true", help="reuse existing outputs in the workdir")
    args = ap.parse_args(argv)
    # Runs execute from the workdir as well as the repo root, so binaries must be absolute.
    args.reference = str(Path(args.reference).resolve())
    args.candidate = str(Path(args.candidate).resolve())

    workdir = Path(args.workdir)
    if not workdir.is_absolute():
        workdir = ROOT / workdir
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    record = {"protocol": "ACTINV-P122", "checker": record_file(Path(__file__).resolve()),
              "protocol_file": record_file(PROTOCOL),
              "reference_binary": record_file(args.reference), "candidate_binary": record_file(args.candidate),
              "gate_spec": record_file(GATE_SPEC), "runs": {}, "gates": {}}
    spec_ok = record["gate_spec"]["sha256"] == GATE_SPEC_SHA256
    record["gate_spec"]["matches_protocol_sha256"] = spec_ok
    base_spec = json.loads(GATE_SPEC.read_text())

    def execute(label, binary, spec_path, cwd):
        out_file = workdir / f"{label}.json"
        log_file = workdir / f"{label}.log"
        info = {"binary": str(binary), "spec": record_file(spec_path), "cwd": str(cwd)}
        record["runs"][label] = info
        if not (args.reuse and out_file.exists()):
            try:
                code = run_binary(binary, spec_path, out_file, log_file, cwd, args.timeout)
            except subprocess.TimeoutExpired:
                info["error"] = f"timed out after {args.timeout} s"
                return None
            except OSError as error:
                info["error"] = f"{type(error).__name__}: {error}"
                return None
            info["exit_code"] = code
            if code != 0:
                info["error"] = f"exit code {code}; see {log_file}"
                return None
        info["output"] = record_file(out_file)
        return json.loads(out_file.read_text())

    # ---- G2
    g2 = {"pass": False}
    ref = execute("g2_reference", args.reference, GATE_SPEC, ROOT)
    cand = execute("g2_candidate", args.candidate, GATE_SPEC, ROOT)
    if ref is not None and cand is not None:
        g2 = gate_g2(ref, cand, spec_ok)
    else:
        g2["error"] = "a gate run failed; see runs"
    record["gates"]["G2"] = g2

    # ---- summary variants (candidate only)
    declared_mode = (base_spec.get("options") or {}).get("mode", "auto")
    mode_info, results = {"declared": declared_mode}, {}
    if cand is not None:
        resolved = cand.get("mode")
        mode_info["declared_resolved"] = resolved
        if resolved in ("trace", "coupled"):
            mode_info["other"] = "coupled" if resolved == "trace" else "trace"
            specs = {"declared": make_variant(base_spec, None, f"summary, declared mode {declared_mode}"),
                     "other": make_variant(base_spec, mode_info["other"], f"summary, {mode_info['other']} mode")}
            for label, spec in specs.items():
                path = workdir / f"{label}.spec.json"
                path.write_text(json.dumps(spec, indent=1) + "\n")
                res = execute(f"summary_{label}", args.candidate, path, workdir)
                if res is not None:
                    results[label] = res
        else:
            mode_info["error"] = f"the gate run reports mode {resolved!r}, expected trace or coupled"
    record["mode_selection"] = mode_info

    # ---- G3, G4
    if len(results) == 2 and all(s.get("summary") for r in results.values() for s in r["steps"]):
        spec_for_data = json.loads((workdir / "declared.spec.json").read_text())
        try:
            record["gates"]["G3"] = gate_g3(results, spec_for_data)
        except (OSError, ValueError, KeyError, IndexError) as error:
            record["gates"]["G3"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        try:
            record["gates"]["G4"] = gate_g4(results, mode_info)
        except (ValueError, KeyError, IndexError, ZeroDivisionError) as error:
            record["gates"]["G4"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    else:
        reason = "summary variant runs missing or without `summary` blocks; see runs"
        record["gates"]["G3"] = {"pass": False, "error": reason}
        record["gates"]["G4"] = {"pass": False, "error": reason}

    # ---- G5 (report only)
    g5_path = Path(args.fispact_out)
    if "declared" in results and g5_path.exists():
        try:
            record["gates"]["G5"] = g5_compare(results["declared"], json.loads(
                (workdir / "declared.spec.json").read_text()), g5_path)
        except (OSError, ValueError, KeyError, IndexError, AttributeError) as error:
            record["gates"]["G5"] = {"report_only": True, "error": f"{type(error).__name__}: {error}"}
    else:
        record["gates"]["G5"] = {"report_only": True, "skipped": "no summary run or FISPACT output not found"}

    overall = all(bool(record["gates"].get(g, {}).get("pass")) for g in ("G2", "G3", "G4"))
    record["verdict"] = "PASS" if overall else "FAIL"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for gate in ("G2", "G3", "G4"):
        print(f"P122 {gate}: {'PASS' if record['gates'][gate].get('pass') else 'FAIL'}")
    g3 = record["gates"]["G3"]
    if "n_comparisons" in g3:
        print(f"  G3: {g3['n_comparisons']} comparisons, {g3['n_mismatches']} mismatches; mode-partition "
              f"normalisation {g3['mode_partition_normalisation']['counts']}")
    if "error" not in record["gates"]["G5"] and "intervals" in record["gates"]["G5"]:
        print_g5_table(record["gates"]["G5"])
    print(f"P122 verdict: {record['verdict']} ({out_path})")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
